from __future__ import annotations

import csv
import json
import logging
import math
import re
from copy import deepcopy
from collections import Counter
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

import yaml
from sqlalchemy.orm import Session, joinedload, selectinload

from app.adapters.text_search.base import TextSearchClient
from app.adapters.vector_db.base import VectorSearchClient
from app.core.config import REPO_ROOT, get_settings
from app.core.telemetry import stage, timed
from app.db.models import Dataset, Frame, QueryRun, RetrievalResult, Video, new_id
from app.modules.media.urls import gcs_public_url
from app.modules.models.service import ModelRegistryService
from app.modules.retrieval.query_planning import AgentQueryPlanner
from app.modules.retrieval.schemas import ResultItem, SearchOptions, SearchRequest, SearchResponse
from app.modules.retrieval.temporal_query import TemporalEventParse, parse_temporal_events
from app.modules.temporal.ats import Candidate, adaptive_temporal_search
from app.modules.temporal.diversification import diversify_temporal_sequences, nms_event_candidates
from app.modules.temporal.dev_first import (
    DevFirstCandidate,
    build_dev_first_vortex_ats_sequences,
    calibrate_candidates,
    event_probe,
    score_candidate_videos,
    score_candidate_videos_across_events,
    select_diagnostic_event,
    temporal_nms,
)
from app.modules.temporal.vortex import vortex_k_context_rerank


logger = logging.getLogger(__name__)


TOKEN_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)
FRAME_VIDEO_ID_RE = re.compile(r"^(?P<video_id>.+)_F\d+$")
VECTOR_KEYFRAME_ID_RE = re.compile(r"^(?P<video_id>L\d{2}_V\d{3})_(?P<n>\d+)$")
VIETNAMESE_CHAR_RE = re.compile(r"[\u00c0-\u1ef9\u0110\u0111]")
VIETNAMESE_SIGNAL_TOKENS = {
    "ao",
    "ban",
    "cau",
    "cua",
    "dang",
    "den",
    "do",
    "duoc",
    "gi",
    "la",
    "nguoi",
    "nhung",
    "noi",
    "o",
    "trong",
    "voi",
}

AGENT_MODEL_PROFILES = {
    "gpt-4o": "openai_gpt4o",
    "gpt-5-nano": "openai_gpt5_nano",
    "gpt-5.6-luna": "openai_gpt56_luna",
}


def normalize_tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "") if len(token) > 1]


def cosine_like_overlap(query: str, document: str) -> float:
    q = Counter(normalize_tokens(query))
    d = Counter(normalize_tokens(document))
    if not q or not d:
        return 0.0
    intersection = sum(min(q[token], d[token]) for token in q)
    q_norm = math.sqrt(sum(value * value for value in q.values())) or 1.0
    d_norm = math.sqrt(sum(value * value for value in d.values())) or 1.0
    return intersection / (q_norm * d_norm)


@dataclass(frozen=True)
class FrameScore:
    frame: Frame
    semantic_score: float
    text_score: float
    quality_score: float
    weighted_score: float
    rrf_score: float
    final_score: float
    rerank_score: float = 0.0
    rerank_detail: dict[str, Any] = field(default_factory=dict)
    filter_debug: dict[str, Any] = field(default_factory=dict)
    source_hit: dict[str, Any] = field(default_factory=dict)
    text_hit: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SemanticCollection:
    collection: str
    weight: float = 1.0
    model_key: str | None = None


class RetrievalService:
    def __init__(
        self,
        db: Session,
        model_registry: ModelRegistryService,
        vector_client: VectorSearchClient | None = None,
        text_client: TextSearchClient | None = None,
    ) -> None:
        self.db = db
        self.model_registry = model_registry
        self.vector_client = vector_client
        self.text_client = text_client
        self.settings = get_settings()
        self.profiles = self._load_profiles()
        self.query_planner = AgentQueryPlanner.from_config(self.settings.agent_config_path)
        self._map_keyframes_cache: dict[str, dict[int, dict[str, Any]] | None] = {}
        self._semantic_hit_sources: dict[str, dict[str, Any]] = {}
        self._text_hit_sources: dict[str, dict[str, Any]] = {}
        self._source_status: dict[str, str] = {}

    @timed("search")
    def search(self, request: SearchRequest) -> SearchResponse:
        if not request.query_text.strip():
            raise ValueError("query_text must not be empty")

        self._source_status = {}
        started_at = perf_counter()
        dataset = self._resolve_dataset(request.dataset_id)
        normalized = self._normalize_query(request)
        run_id = new_id()

        # Init run data
        run = QueryRun(
            id=run_id,
            dataset_id=dataset.id,
            query_name=request.query_name,
            query_type=request.query_type,
            query_text=request.query_text,
            normalized_query=normalized,
            options=request.model_dump(mode="json"),
            status="RUNNING",
        )
        self.db.add(run)

        # search 
        try:
            if request.query_type == "TRAKE":
                results = self._search_trake(run, dataset, request, normalized)
            elif request.query_type == "KIS" and request.options.temporal_mode:
                results = self._search_temporal_kis(run, dataset, request, normalized)
            else:
                results = self._search_frame_level(run, dataset, request, normalized)
            run.status = "DONE"
            latency_ms = int((perf_counter() - started_at) * 1000)
            degraded = any(value in {"degraded", "unavailable"} for value in self._source_status.values())
            normalized = {**normalized, "latency_ms": latency_ms,
                          "source_status": dict(self._source_status),
                          "retrieval_mode": "degraded" if degraded else "indexed"}
            # Re-assign JSON fields so SQLAlchemy persists updated values reliably.
            run.normalized_query = normalized
            run.options = {**(run.options or {}), "latency_ms": latency_ms}
            with stage("commit"):
                self.db.commit()
        except Exception:
            self.db.rollback()
            try:
                failed_run = QueryRun(
                    id=run_id,
                    dataset_id=dataset.id,
                    query_name=request.query_name,
                    query_type=request.query_type,
                    query_text=request.query_text,
                    normalized_query=normalized,
                    options=request.model_dump(mode="json"),
                    status="FAILED",
                )
                self.db.merge(failed_run)
                self.db.commit()
            except Exception:
                self.db.rollback()
            raise

        response = SearchResponse(
            query_run_id=run_id,
            query_type=request.query_type,
            query_name=request.query_name,
            normalized_query=normalized,
            results=results,
        )
        self._cache_search_history(response)
        return response

    def get_run(self, run_id: str) -> SearchResponse:
        run = self.db.query(QueryRun).filter(QueryRun.id == run_id).one()
        results = [self._result_to_item(result) for result in sorted(run.results, key=lambda item: item.rank)]
        return SearchResponse(
            query_run_id=run.id,
            query_type=run.query_type,
            query_name=run.query_name,
            normalized_query=run.normalized_query,
            results=results,
        )

    def select_results(self, result_ids: list[str], selected: bool) -> int:
        updated = 0
        for result in self.db.query(RetrievalResult).filter(RetrievalResult.id.in_(result_ids)).all():
            result.selected = selected
            updated += 1
        self.db.commit()
        return updated

    @timed("history_cache")
    def _cache_search_history(self, response: SearchResponse) -> None:
        redis_url = (self.settings.redis_url or "").strip()
        if not redis_url:
            return
        try:
            import redis  # type: ignore

            client = redis.Redis.from_url(
                redis_url,
                socket_connect_timeout=0.75,
                socket_timeout=1.5,
                decode_responses=True,
            )
            payload = json.dumps(response.model_dump(mode="json"), ensure_ascii=False)
            key = f"search_history:{response.query_run_id}"
            client.setex(key, 60 * 60 * 24 * 7, payload)
            client.lpush("search_history:recent", response.query_run_id)
            client.ltrim("search_history:recent", 0, 99)
        except Exception:  # noqa: BLE001 - Redis history is an auxiliary cache.
            logger.warning("Failed to cache search history in Redis.", exc_info=True)

    def plan_query(self, request: SearchRequest) -> dict[str, Any]:
        if not request.query_text.strip():
            raise ValueError("query_text must not be empty")
        return self._normalize_query(request)

    def _load_profiles(self) -> dict[str, Any]:
        path = self.settings.retrieval_profiles_path
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}

    @timed("dataset")
    def _resolve_dataset(self, dataset_id: str | None) -> Dataset:
        if dataset_id:
            dataset = self.db.query(Dataset).filter(Dataset.id == dataset_id).first()
        else:
            dataset = self.db.query(Dataset).filter(Dataset.status == "READY").first()
        if not dataset:
            raise ValueError("No READY dataset found. Create or ingest a dataset first.")
        return dataset

    # Get request and return a normalized query dict with multi-view variants, temporal events, and retrieval weights.
    @timed("planning")
    def _normalize_query(self, request: SearchRequest) -> dict[str, Any]:
        profile = self.profiles.get(request.profile, self.profiles.get("competition_default", {}))
        expansion_profile = profile.get("query_expansion", {})
        max_variants = int(expansion_profile.get("max_variants", 5))
        expansion_default_enabled = bool(expansion_profile.get("enabled_default", True))
        query_text = request.query_text.strip()
        semantic_variants = [query_text]
        text_variants = [query_text]
        temporal_kis = request.query_type == "KIS" and request.options.temporal_mode
        query_planner = self._planner_for_request(request)
        agent_plan = None
        if request.options.use_agent_query_planning or temporal_kis:
            if temporal_kis:
                try:
                    agent_plan = query_planner.plan(
                        query=query_text,
                        query_type=request.query_type,
                        max_variants=max_variants,
                        temporal_kis=True,
                    )
                except TypeError as exc:
                    if "temporal_kis" not in str(exc):
                        raise
                    # Keeps third-party planners implementing the prior contract usable.
                    agent_plan = query_planner.plan(
                        query=query_text,
                        query_type=request.query_type,
                        max_variants=max_variants,
                    )
            else:
                agent_plan = query_planner.plan(
                    query=query_text,
                    query_type=request.query_type,
                    max_variants=max_variants,
                )
        if agent_plan is not None:
            agent_multi_views = getattr(agent_plan, "multi_views", None) or getattr(agent_plan, "variants", None)
            if agent_multi_views:
                semantic_variants = list(agent_multi_views)
        if agent_plan is not None and agent_plan.text_variants:
            text_variants = self._dedupe_query_variants(
                [query_text, *agent_plan.text_variants],
                max_variants=max_variants,
            )
        text_variants = self._dedupe_query_variants(
            [*text_variants, *self._metadata_query_cues(query_text)],
            max_variants=max_variants,
        )
        if request.options.use_query_expansion and expansion_default_enabled and not temporal_kis:
            semantic_variants = self._dedupe_query_variants(
                [*semantic_variants, *self._expand_query_variants(query_text, max_variants=max_variants)],
                max_variants=max_variants,
            )
        semantic_variants = self._english_semantic_variants(
            query_text,
            semantic_variants,
            max_variants=max_variants,
        )
        temporal_parse = self._parse_temporal_events(query_text)
        temporal_events, temporal_source = self._resolve_temporal_events(request, agent_plan, temporal_parse)
        text_temporal_events = self._text_temporal_events(request, temporal_parse, temporal_events, agent_plan)
        retrieval_weights, retrieval_weight_source = self._resolve_retrieval_weights(
            profile,
            agent_plan,
            query_text,
            request.query_type,
        )
        text_source_weights, text_source_weight_source = self._resolve_text_source_weights(
            profile,
            agent_plan,
            query_text,
            request.query_type,
        )
        temporal_event_plans = (
            self._resolve_temporal_event_plans(
                temporal_events,
                text_temporal_events,
                agent_plan,
                request.query_type,
                text_source_weights,
                max_variants,
            )
            if request.query_type == "TRAKE" or temporal_kis
            else []
        )
        if temporal_kis and temporal_event_plans:
            event_multi_views = self._dedupe_query_variants(
                [
                    str(view)
                    for plan in temporal_event_plans
                    if isinstance(plan, dict)
                    for view in (plan.get("multi_views") or [plan.get("query")])
                ],
                max_variants=max(1, max_variants * len(temporal_event_plans)),
            )
            if event_multi_views:
                semantic_variants = event_multi_views
        target_scope = str(getattr(agent_plan, "target_scope", "frame") or "frame") if temporal_source == "agent" else "frame"
        if target_scope not in {"frame", "video_sequence"}:
            target_scope = "frame"
        temporal_anchor_index = self._resolve_temporal_anchor_index(
            request.options,
            agent_plan if temporal_source == "agent" else None,
            len(temporal_events),
        )
        if temporal_kis and request.options.temporal_strategy == "dev_first_search" and target_scope == "video_sequence":
            temporal_anchor_index = None
        normalized = {
            "language": agent_plan.language if agent_plan else "auto",
            "multi_views": semantic_variants,
            "variants": semantic_variants,
            "semantic_variants": semantic_variants,
            "text_variants": text_variants,
            "tokens": normalize_tokens(" ".join([*semantic_variants, *text_variants])),
            "temporal_events": temporal_events,
            "text_temporal_events": text_temporal_events,
            "temporal_event_count": len(temporal_events),
            "temporal_event_source": temporal_source,
            "temporal_event_plans": temporal_event_plans,
            "temporal_mode": temporal_kis,
            "temporal_strategy": request.options.temporal_strategy if temporal_kis else None,
            "temporal_anchor_index": temporal_anchor_index,
            "temporal_intent": getattr(agent_plan, "temporal_intent", "single_event") if agent_plan else "single_event",
            "target_scope": target_scope,
            "anchor_policy": getattr(agent_plan, "anchor_policy", "inferred") if agent_plan else "inferred",
            "temporal_edges": getattr(agent_plan, "temporal_edges", []) if agent_plan else [],
            "temporal_retrieval": {
                "enabled": temporal_kis,
                "independent_event_searches": len(temporal_events) if temporal_kis and request.options.temporal_strategy != "dev_first_search" else 0,
                "multi_view_event_searches": sum(
                    len(plan.get("multi_views", []) or [plan.get("query")])
                    for plan in temporal_event_plans
                    if isinstance(plan, dict)
                ) if temporal_kis else 0,
                "multiperspective_fusion": "aithena_independent_view_merge" if temporal_kis else None,
                "reranker": request.options.temporal_strategy if temporal_kis else None,
            },
            "retrieval_weights": retrieval_weights,
            "retrieval_weight_source": retrieval_weight_source,
            "text_source_weights": text_source_weights,
            "text_source_weight_source": text_source_weight_source,
            "visual_search": self._visual_search_summary(profile, request.options.visual_search_mode),
            "raw_temporal_events": temporal_parse.events,
            "profile": request.profile,
            "filters": self._normalized_filter_options(request.options),
        }
        if agent_plan is not None:
            normalized["agent_query_plan"] = agent_plan.as_normalized_query()
        return normalized

    def _planner_for_request(self, request: SearchRequest) -> AgentQueryPlanner:
        """Bind a UI-selected, allow-listed model without mutating shared planner state."""
        selected_model = request.options.agent_model
        profile_name = AGENT_MODEL_PROFILES.get(selected_model or "")
        if not profile_name:
            return self.query_planner
        config = deepcopy(self.query_planner.config)
        planner_config = config.setdefault("llm_query_planning", {})
        if not isinstance(planner_config, dict):
            return self.query_planner
        profiles = planner_config.get("profiles")
        if not isinstance(profiles, dict) or profile_name not in profiles:
            logger.warning("Selected agent model profile is not configured: %s", selected_model)
            return self.query_planner
        # A request-scoped preference must take precedence over AGENT_LLM_PROFILE.
        # The latter is a deployment default; otherwise a running container pinned
        # to GPT-4o silently ignores the model selected in the web UI.
        planner_config["request_profile_override"] = profile_name
        planner_config["active_profile"] = profile_name
        return AgentQueryPlanner(config=config, config_path=self.query_planner.config_path)

    @staticmethod
    def _resolve_temporal_anchor_index(options: SearchOptions, agent_plan: Any, event_count: int) -> int:
        if event_count <= 0:
            return 1
        raw_index = options.temporal_anchor_index
        if raw_index is None and agent_plan is not None:
            raw_index = getattr(agent_plan, "temporal_anchor_index", None)
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            index = (event_count + 1) // 2
        return min(max(1, index), event_count)

    def _resolve_retrieval_weights(
        self,
        profile: dict[str, Any],
        agent_plan: Any,
        query_text: str,
        query_type: str,
    ) -> tuple[dict[str, float], str]:
        agent_weights = getattr(agent_plan, "retrieval_weights", {}) if agent_plan else {}
        normalized_agent_weights = self._normalize_retrieval_weights(agent_weights)
        if normalized_agent_weights:
            source = str(getattr(agent_plan, "retrieval_weight_source", "agent") or "agent")
            return normalized_agent_weights, source

        heuristic = self._infer_retrieval_strategy(query_text, query_type)
        heuristic_weights = self._normalize_retrieval_weights(heuristic.get("weights"))
        if heuristic_weights:
            return heuristic_weights, "heuristic"

        profile_weights = {
            "visual": float(profile.get("semantic_weight", 0.6)),
            "text": float(profile.get("metadata_weight", profile.get("text_weight", 0.25))),
        }
        return self._normalize_retrieval_weights(profile_weights) or {"visual": 0.5, "text": 0.5}, "profile"

    def _resolve_text_source_weights(
        self,
        profile: dict[str, Any],
        agent_plan: Any,
        query_text: str,
        query_type: str,
    ) -> tuple[dict[str, float], str]:
        agent_weights = getattr(agent_plan, "text_source_weights", {}) if agent_plan else {}
        normalized_agent_weights = self._normalize_text_source_weights(agent_weights)
        if normalized_agent_weights:
            source = str(getattr(agent_plan, "text_source_weight_source", "agent") or "agent")
            return normalized_agent_weights, source

        heuristic = self._infer_retrieval_strategy(query_text, query_type)
        heuristic_weights = self._normalize_text_source_weights(heuristic.get("text_source_weights"))
        if heuristic_weights:
            return heuristic_weights, "heuristic"

        metadata = profile.get("metadata", {}) if isinstance(profile.get("metadata"), dict) else {}
        profile_weights = metadata.get("text_source_weights")
        normalized_profile_weights = self._normalize_text_source_weights(profile_weights)
        if normalized_profile_weights:
            return normalized_profile_weights, "profile"
        return {"asr": 0.35, "caption": 0.5, "ocr": 0.15}, "default"

    def _resolve_temporal_event_plans(
        self,
        events: list[str],
        text_events: list[str],
        agent_plan: Any,
        query_type: str,
        default_text_source_weights: dict[str, float],
        max_views: int,
    ) -> list[dict[str, Any]]:
        raw_plans = getattr(agent_plan, "temporal_event_plans", []) if agent_plan else []
        raw_plans = raw_plans if isinstance(raw_plans, list) else []
        plans: list[dict[str, Any]] = []
        for index, event_query in enumerate(events, start=1):
            raw_plan = raw_plans[index - 1] if index - 1 < len(raw_plans) else {}
            raw_plan = raw_plan if isinstance(raw_plan, dict) else {}
            weights = self._normalize_retrieval_weights(raw_plan.get("retrieval_weights"))
            if not weights:
                heuristic = self._infer_retrieval_strategy(event_query, query_type)
                weights = self._normalize_retrieval_weights(heuristic.get("weights")) or {"visual": 0.5, "text": 0.5}
            text_source_weights = self._normalize_text_source_weights(raw_plan.get("text_source_weights"))
            if not text_source_weights:
                heuristic = self._infer_retrieval_strategy(event_query, query_type)
                text_source_weights = self._normalize_text_source_weights(heuristic.get("text_source_weights"))
            if not text_source_weights:
                text_source_weights = default_text_source_weights
            try:
                importance = float(raw_plan.get("importance", 1.0))
            except (TypeError, ValueError):
                importance = 1.0
            text_query = str(
                raw_plan.get("text_query")
                or (text_events[index - 1] if index - 1 < len(text_events) else event_query)
            )
            semantic_views = self._event_semantic_views(raw_plan, event_query, max_views)
            text_views = self._event_text_views(raw_plan, text_query, max_views)
            plans.append(
                {
                    "event_index": index,
                    "query": event_query,
                    "text_query": text_query,
                    "multi_views": semantic_views,
                    "semantic_views": semantic_views,
                    "text_views": text_views,
                    "importance": max(0.0, importance),
                    "retrieval_weights": weights,
                    "retrieval_weight_source": str(raw_plan.get("retrieval_weight_source") or "heuristic"),
                    "text_source_weights": text_source_weights,
                    "text_source_weight_source": str(raw_plan.get("text_source_weight_source") or "heuristic"),
                }
            )
        return plans

    def _event_semantic_views(self, event_plan: dict[str, Any], event_query: str, max_views: int) -> list[str]:
        views = self._extract_plan_text_values(
            event_plan,
            ("multi_views", "semantic_views", "perspectives", "views", "variants"),
        )
        views = self._dedupe_query_variants([event_query, *views], max_variants=max_views)
        return self._english_semantic_variants(event_query, views, max_variants=max_views)

    def _event_text_views(self, event_plan: dict[str, Any], text_query: str, max_views: int) -> list[str]:
        views = self._extract_plan_text_values(
            event_plan,
            ("text_views", "text_multi_views", "lexical_views", "text_variants", "asr_variants"),
        )
        return self._dedupe_query_variants([text_query, *views], max_variants=max_views)

    def _extract_plan_text_values(self, payload: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
        values: list[str] = []
        for key in keys:
            raw_values = payload.get(key)
            if isinstance(raw_values, str):
                values.append(raw_values)
                continue
            if not isinstance(raw_values, list):
                continue
            for item in raw_values:
                if isinstance(item, dict):
                    for item_key in ("text", "query", "view", "perspective"):
                        value = str(item.get(item_key) or "").strip()
                        if value:
                            values.append(value)
                            break
                else:
                    values.append(str(item))
        return self._dedupe_query_variants(values, max_variants=8)

    def _rank_frames_multiperspective(
        self,
        dataset: Dataset,
        semantic_views: list[str],
        text_views: list[str],
        query_text: str,
        profile_name: str,
        options: SearchOptions,
        top_k: int,
        retrieval_weights: dict[str, float] | None = None,
        use_agent_retrieval_weights: bool = False,
        text_source_weights: dict[str, float] | None = None,
    ) -> list[FrameScore]:
        semantic_views = self._dedupe_query_variants(semantic_views or [query_text], max_variants=8)
        text_views = self._dedupe_query_variants(text_views or [query_text], max_variants=8)
        if len(semantic_views) <= 1 and len(text_views) <= 1:
            return self._rank_frames(
                dataset=dataset,
                semantic_variants=semantic_views,
                text_variants=text_views,
                query_text=text_views[0] if text_views else query_text,
                profile_name=profile_name,
                options=options,
                top_k=top_k,
                retrieval_weights=retrieval_weights,
                use_agent_retrieval_weights=use_agent_retrieval_weights,
                text_source_weights=text_source_weights,
            )

        view_queries = [
            {
                "view_index": index + 1,
                "semantic_query": semantic_view,
                "text_query": text_views[index] if index < len(text_views) else text_views[0],
            }
            for index, semantic_view in enumerate(semantic_views)
        ]
        merged: dict[str, dict[str, Any]] = {}
        for view in view_queries:
            ranked = self._rank_frames(
                dataset=dataset,
                semantic_variants=[view["semantic_query"]],
                text_variants=[view["text_query"]],
                query_text=view["text_query"] or view["semantic_query"],
                profile_name=profile_name,
                options=options,
                top_k=top_k,
                retrieval_weights=retrieval_weights,
                use_agent_retrieval_weights=use_agent_retrieval_weights,
                text_source_weights=text_source_weights,
            )
            for rank, item in enumerate(ranked, start=1):
                frame_id = item.frame.keyframe_id
                entry = merged.setdefault(
                    frame_id,
                    {
                        "frame": item.frame,
                        "best": item,
                        "score_sum": 0.0,
                        "semantic_score_sum": 0.0,
                        "text_score_sum": 0.0,
                        "weighted_score_sum": 0.0,
                        "rrf_raw": 0.0,
                        "views": [],
                    },
                )
                if item.final_score > entry["best"].final_score:
                    entry["best"] = item
                entry["score_sum"] += item.final_score
                entry["semantic_score_sum"] += item.semantic_score
                entry["text_score_sum"] += item.text_score
                entry["weighted_score_sum"] += item.weighted_score
                entry["rrf_raw"] += 1.0 / (60.0 + rank)
                entry["views"].append(
                    {
                        "view_index": view["view_index"],
                        "semantic_query": view["semantic_query"],
                        "text_query": view["text_query"],
                        "rank": rank,
                        "score": round(item.final_score, 6),
                    }
                )

        if not merged:
            return []

        max_rrf_raw = max(float(entry["rrf_raw"]) for entry in merged.values()) or 1.0
        scored: list[FrameScore] = []
        total_views = max(1, len(view_queries))
        for entry in merged.values():
            best: FrameScore = entry["best"]
            matched_views = len(entry["views"])
            coverage = matched_views / total_views
            avg_score = entry["score_sum"] / matched_views
            view_rrf = entry["rrf_raw"] / max_rrf_raw
            final_score = 0.58 * best.final_score + 0.24 * avg_score + 0.13 * view_rrf + 0.05 * coverage
            source_hit = dict(best.source_hit)
            source_hit["multiperspective_fusion"] = {
                "strategy": "aithena_independent_view_merge",
                "total_views": total_views,
                "matched_views": matched_views,
                "coverage": round(coverage, 6),
                "view_rrf": round(view_rrf, 6),
                "matched_view_details": entry["views"][:8],
            }
            scored.append(
                FrameScore(
                    frame=best.frame,
                    semantic_score=entry["semantic_score_sum"] / matched_views,
                    text_score=entry["text_score_sum"] / matched_views,
                    quality_score=best.quality_score,
                    weighted_score=entry["weighted_score_sum"] / matched_views,
                    rrf_score=view_rrf,
                    final_score=final_score,
                    rerank_score=best.rerank_score,
                    rerank_detail=best.rerank_detail,
                    filter_debug=best.filter_debug,
                    source_hit=source_hit,
                    text_hit=best.text_hit,
                )
            )
        scored.sort(key=lambda item: (item.final_score, item.frame.frame_idx), reverse=True)
        return scored[:top_k]

    def _text_temporal_events(
        self,
        request: SearchRequest,
        temporal_parse: TemporalEventParse,
        semantic_events: list[str],
        agent_plan: Any,
    ) -> list[str]:
        option_events = self._dedupe_query_variants(request.options.temporal_events, max_variants=8)
        if option_events:
            return option_events
        agent_event_plans = getattr(agent_plan, "temporal_event_plans", []) if agent_plan else []
        if isinstance(agent_event_plans, list):
            agent_text_events = self._dedupe_query_variants(
                [str(plan.get("text_query") or "") for plan in agent_event_plans if isinstance(plan, dict)],
                max_variants=8,
            )
            if len(agent_text_events) == len(semantic_events):
                return agent_text_events
        raw_events = self._dedupe_query_variants(temporal_parse.events, max_variants=8)
        if len(raw_events) == len(semantic_events):
            return raw_events
        if len(semantic_events) <= 1:
            return [request.query_text.strip()]
        if self._looks_vietnamese(request.query_text):
            return [request.query_text.strip()] * len(semantic_events)
        return semantic_events

    @staticmethod
    def _normalize_retrieval_weights(raw_weights: Any) -> dict[str, float]:
        if not isinstance(raw_weights, dict):
            return {}
        try:
            visual = max(0.0, float(raw_weights.get("visual")))
            text = max(0.0, float(raw_weights.get("text")))
        except (TypeError, ValueError):
            return {}
        total = visual + text
        if total <= 0:
            return {}
        return {"visual": visual / total, "text": text / total}

    @staticmethod
    def _normalize_text_source_weights(raw_weights: Any) -> dict[str, float]:
        if not isinstance(raw_weights, dict):
            return {}
        try:
            weights = {
                source: max(0.0, float(raw_weights.get(source, 0.0)))
                for source in ("asr", "caption", "ocr")
            }
        except (TypeError, ValueError):
            return {}
        total = sum(weights.values())
        if total <= 0:
            return {}
        return {source: value / total for source, value in weights.items()}

    def _infer_retrieval_strategy(self, query_text: str, query_type: str) -> dict[str, Any]:
        infer = getattr(self.query_planner, "infer_retrieval_strategy", None)
        if callable(infer):
            strategy = infer(query_text, query_type)
            if isinstance(strategy, dict):
                return strategy
        return AgentQueryPlanner(config={}).infer_retrieval_strategy(query_text, query_type)

    def _dedupe_query_variants(self, values: list[str], max_variants: int) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for item in values:
            value = " ".join(str(item).split())
            key = value.lower()
            if value and key not in seen:
                deduped.append(value)
                seen.add(key)
        return deduped[: max(1, max_variants)]

    @staticmethod
    def _metadata_query_cues(query_text: str) -> list[str]:
        """Keep exact lexical cues searchable when the LLM planner is unavailable."""
        cues: list[str] = []
        cues.extend(match.strip() for match in re.findall(r"[\"'\u201c\u201d]([^\"'\u201c\u201d]{1,80})[\"'\u201c\u201d]", query_text))
        cues.extend(re.findall(r"\b[A-Z0-9][A-Z0-9._:/-]{1,}\b", query_text))
        return list(dict.fromkeys(cue for cue in cues if cue))

    def _english_semantic_variants(
        self,
        query_text: str,
        values: list[str],
        max_variants: int,
    ) -> list[str]:
        variants = self._dedupe_query_variants(values, max_variants=max_variants)
        if not self._looks_vietnamese(query_text):
            return variants
        english = [value for value in variants if not self._looks_vietnamese(value)]
        # Do not turn an offline/degraded translation into an empty visual search.
        return english[:max_variants] or variants[:1]

    @staticmethod
    def _looks_vietnamese(value: str) -> bool:
        if VIETNAMESE_CHAR_RE.search(value):
            return True
        tokens = {token.lower() for token in re.findall(r"[a-z]+", value)}
        return len(tokens.intersection(VIETNAMESE_SIGNAL_TOKENS)) >= 2

    def _expand_query_variants(self, query: str, max_variants: int) -> list[str]:
        try:
            variants = self.model_registry.query_expander.expand(query, max_variants=max_variants)
        except Exception:
            variants = []
        deduped: list[str] = []
        for item in [query, *variants]:
            value = str(item).strip()
            if value and value not in deduped:
                deduped.append(value)
        return deduped[: max(1, max_variants)]

    def _parse_temporal_events(self, query: str) -> TemporalEventParse:
        return parse_temporal_events(query, max_events=8)

    def _resolve_temporal_events(
        self,
        request: SearchRequest,
        agent_plan: Any,
        temporal_parse: TemporalEventParse,
    ) -> tuple[list[str], str]:
        option_events = self._dedupe_query_variants(request.options.temporal_events, max_variants=8)
        if option_events:
            return option_events, "request_options"

        agent_events = self._dedupe_query_variants(
            agent_plan.temporal_events if agent_plan and agent_plan.temporal_events else [],
            max_variants=8,
        )
        parsed_events = temporal_parse.events

        if request.query_type == "TRAKE":
            if agent_events and (len(parsed_events) <= 1 or len(agent_events) == len(parsed_events)):
                return agent_events, "agent"
            if len(parsed_events) > 1:
                return parsed_events, temporal_parse.source
            if agent_events:
                return agent_events, "agent"
            return parsed_events or [request.query_text.strip()], temporal_parse.source

        if request.options.temporal_mode and len(parsed_events) > 1 and len(agent_events) < len(parsed_events):
            if request.options.temporal_strategy == "dev_first_search" and agent_events:
                return agent_events, "agent"
            return parsed_events, temporal_parse.source
        if agent_events:
            return agent_events, "agent"
        return parsed_events or [request.query_text.strip()], temporal_parse.source

    def _split_temporal_events(self, query: str) -> list[str]:
        return self._parse_temporal_events(query).events or [query]
        separators = [r"\bthen\b", r"\bafter that\b", r"\bsau đó\b", r"\btiếp theo\b", r";", r"\(e\d+\)\s*:"]
        pattern = "|".join(separators)
        parts = [part.strip(" .:-") for part in re.split(pattern, query, flags=re.IGNORECASE) if part.strip(" .:-")]
        return parts[:8] if len(parts) > 1 else [query]



    # Search functions

    # 1. Frame-level search (default)
    def _search_frame_level(
        self,
        run: QueryRun,
        dataset: Dataset,
        request: SearchRequest,
        normalized: dict[str, Any],
    ) -> list[ResultItem]:
        
        candidates = self._rank_frames(
            dataset=dataset,
            semantic_variants=normalized["semantic_variants"],
            text_variants=normalized["text_variants"],
            query_text=request.query_text,
            profile_name=request.profile,
            options=request.options,
            top_k=request.top_k,
            retrieval_weights=normalized.get("retrieval_weights"),
            use_agent_retrieval_weights=normalized.get("retrieval_weight_source") == "agent",
            text_source_weights=normalized.get("text_source_weights"),
        )
        qa_frames_by_id: dict[str, Frame] = {}
        if request.query_type == "QA" and candidates:
            top_frame_ids = [candidate.frame.id for candidate in candidates[: request.top_k]]
            qa_frames_by_id = {
                frame.id: frame
                for frame in self.db.query(Frame)
                .options(joinedload(Frame.video), selectinload(Frame.annotations))
                .filter(Frame.id.in_(top_frame_ids))
                .all()
            }
        items: list[ResultItem] = []
        for rank, candidate in enumerate(candidates[: request.top_k], start=1):
            frame = qa_frames_by_id.get(candidate.frame.id, candidate.frame)
            answer = None
            if request.query_type == "QA":
                evidence = self._frame_text(frame)
                answer_hint = self._answer_hint(frame)
                try:
                    raw_answer = self.model_registry.visual_qa.answer(request.query_text, evidence, answer_hint)
                except Exception:
                    raw_answer = answer_hint
                answer = self._postprocess_qa_answer(raw_answer)
            result = RetrievalResult(
                id=new_id(),
                query_run_id=run.id,
                rank=rank,
                video_id=frame.video_id,
                frame_id=frame.id,
                answer=answer,
                score=candidate.final_score,
                score_breakdown={
                    "semantic_score": round(candidate.semantic_score, 6),
                    "text_score": round(candidate.text_score, 6),
                    "quality_score": round(candidate.quality_score, 6),
                    "weighted_score": round(candidate.weighted_score, 6),
                    "rrf_score": round(candidate.rrf_score, 6),
                    "rerank_score": round(candidate.rerank_score, 6),
                    "rerank_detail": candidate.rerank_detail,
                    "semantic_hit": candidate.source_hit,
                    "text_hit": candidate.text_hit,
                    "retrieval_weights": normalized.get("retrieval_weights", {}),
                    "retrieval_weight_source": normalized.get("retrieval_weight_source", "profile"),
                    "text_source_weights": normalized.get("text_source_weights", {}),
                    "text_source_weight_source": normalized.get("text_source_weight_source", "profile"),
                    "final_score": round(candidate.final_score, 6),
                    "filter_debug": candidate.filter_debug,
                },
                sequence_frames=[],
            )
            result.frame = frame
            result.video = frame.video
            self.db.add(result)
            items.append(self._result_to_item(result))
        return items

    # 2. Search temporal
    def _search_dev_first_kis(
        self,
        run: QueryRun,
        dataset: Dataset,
        request: SearchRequest,
        normalized: dict[str, Any],
    ) -> list[ResultItem]:
        """DEV: probe globally, choose a diagnostic event, then sequence by time."""
        events = self._dedupe_query_variants(normalized["temporal_events"] or [request.query_text], max_variants=8)
        plans = normalized.get("temporal_event_plans") if isinstance(normalized.get("temporal_event_plans"), list) else []
        plans = [plan if isinstance(plan, dict) else {} for plan in plans]
        while len(plans) < len(events):
            plans.append({"query": events[len(plans)], "importance": 1.0, "diagnostic_prior": 0.5})
        profile = self.profiles.get(request.profile, self.profiles.get("competition_default", {}))
        config = profile.get("dev_first", {}) if isinstance(profile.get("dev_first"), dict) else {}
        probe_cfg = config.get("diagnostic_probe", {}) if isinstance(config.get("diagnostic_probe"), dict) else {}
        probe_top_k = max(1, int(probe_cfg.get("top_k_per_event", 40)))
        calibration = config.get("score_calibration", {}) if isinstance(config.get("score_calibration"), dict) else {}

        def retrieve(event_index: int, top_k: int) -> list[DevFirstCandidate]:
            plan = plans[event_index - 1]
            query = events[event_index - 1]
            semantic_views = self._event_semantic_views(plan, query, max_views=8)
            # The final event often describes the sought product/state while
            # the root plan carries a more precise semantic rewrite of the
            # whole Vietnamese narrative.  Inject it as an auxiliary view so
            # event retrieval retains that information instead of embedding a
            # vague phrase such as "stick-like product" by itself.
            if event_index == len(events):
                semantic_views = self._dedupe_query_variants(
                    [*semantic_views, *list(normalized.get("semantic_variants") or [])],
                    max_variants=8,
                )
            text_views = self._event_text_views(plan, str(plan.get("text_query") or query), max_views=8)
            weights = self._normalize_retrieval_weights(plan.get("retrieval_weights")) or normalized.get("retrieval_weights")
            text_weights = self._normalize_text_source_weights(plan.get("text_source_weights")) or self._normalize_text_source_weights(normalized.get("text_source_weights"))
            ranked = self._rank_frames_multiperspective(
                dataset=dataset, semantic_views=semantic_views, text_views=text_views,
                query_text=text_views[0] if text_views else query, profile_name=request.profile,
                options=request.options, top_k=top_k, retrieval_weights=weights,
                use_agent_retrieval_weights=str(plan.get("retrieval_weight_source") or "") == "agent",
                text_source_weights=text_weights,
            )
            raw = [
                DevFirstCandidate(
                    frame_id=item.frame.id, video_id=item.frame.video_id, video_code=item.frame.video.video_code,
                    frame_idx=item.frame.frame_idx, event_index=event_index, event_query=query,
                    timestamp_ms=item.frame.timestamp_ms if item.frame.timestamp_ms is not None else None,
                    final_retrieval_score=item.final_score, semantic_raw_score=None,
                    text_raw_score=None, rrf_score=item.rrf_score,
                )
                for item in ranked
            ]
            return calibrate_candidates(raw, calibration)

        probes = [retrieve(index, probe_top_k) for index in range(1, len(events) + 1)]
        diagnostic_index, diagnostics = select_diagnostic_event(
            plans,
            [event_probe(candidates, probe_top_k) for candidates in probes],
            config,
        )
        diagnostic = temporal_nms(
            retrieve(diagnostic_index, max(probe_top_k, int(config.get("diagnostic_candidate_frames", 500)))),
            int(config.get("event_nms_window_ms", 2500)),
        )
        # Preserve the diagnostic event's role, but seed candidate videos with
        # every event probe so a broad diagnostic event cannot remove the true
        # video before the later frame-level sequence search starts.
        seed_candidate_sets = list(probes)
        # The chosen diagnostic event is intentionally retrieved to a deeper
        # pool. Keep that recall in video seeding; the lightweight probes from
        # other events only add corroborating evidence.
        seed_candidate_sets[diagnostic_index - 1] = diagnostic
        video_ranking = score_candidate_videos_across_events(seed_candidate_sets, diagnostic_index, config)
        if not video_ranking:
            video_ranking = score_candidate_videos(diagnostic, config)
        candidate_limit = max(1, int(config.get("candidate_video_limit", 40)))
        candidate_video_ids = {video_id for video_id, _score in video_ranking[:candidate_limit]}
        narrative_cfg = config.get("narrative_probe", {}) if isinstance(config.get("narrative_probe"), dict) else {}
        if len(events) > 1 and bool(narrative_cfg.get("enabled", True)):
            narrative_views = self._dedupe_query_variants(
                list(normalized.get("semantic_variants") or []) + [request.query_text],
                max_variants=8,
            )
            narrative_text = self._dedupe_query_variants(
                list(normalized.get("text_variants") or []) + [request.query_text],
                max_variants=8,
            )
            narrative_ranked = self._rank_frames_multiperspective(
                dataset=dataset,
                semantic_views=narrative_views,
                text_views=narrative_text,
                query_text=request.query_text,
                profile_name=request.profile,
                options=request.options,
                top_k=max(1, int(narrative_cfg.get("top_k", 200))),
                retrieval_weights=normalized.get("retrieval_weights"),
                use_agent_retrieval_weights=normalized.get("retrieval_weight_source") == "agent",
                text_source_weights=self._normalize_text_source_weights(normalized.get("text_source_weights")),
            )
            narrative_candidates = calibrate_candidates(
                [
                    DevFirstCandidate(
                        frame_id=item.frame.id,
                        video_id=item.frame.video_id,
                        video_code=item.frame.video.video_code,
                        frame_idx=item.frame.frame_idx,
                        event_index=0,
                        event_query=request.query_text,
                        timestamp_ms=item.frame.timestamp_ms if item.frame.timestamp_ms is not None else None,
                        final_retrieval_score=item.final_score,
                        rrf_score=item.rrf_score,
                    )
                    for item in narrative_ranked
                ],
                calibration,
            )
            narrative_videos = score_candidate_videos(narrative_candidates, config)
            narrative_limit = max(0, int(narrative_cfg.get("candidate_video_limit", 20)))
            candidate_video_ids.update(video_id for video_id, _score in narrative_videos[:narrative_limit])
        fallback_stages: list[str] = []
        if len(candidate_video_ids) < int(config.get("min_candidate_videos", 8)):
            fallback_stages.append("global_event_retrieval")

        event_cfg = config.get("event_retrieval", {}) if isinstance(config.get("event_retrieval"), dict) else {}
        initial_top_k = max(1, int(event_cfg.get("initial_top_k", 200)))
        max_top_k = max(initial_top_k, int(event_cfg.get("max_top_k", 1000)))
        widening = max(2, int(event_cfg.get("widening_factor", 2)))
        minimum = max(1, int(event_cfg.get("min_candidates_per_event", 20)))
        candidate_sets: list[list[DevFirstCandidate]] = []
        event_summaries: list[dict[str, Any]] = []
        for event_index, query in enumerate(events, start=1):
            if event_index == diagnostic_index:
                candidates = diagnostic
            else:
                current_top_k = initial_top_k
                candidates = []
                while True:
                    found = retrieve(event_index, current_top_k)
                    filtered = [item for item in found if not candidate_video_ids or item.video_id in candidate_video_ids]
                    candidates = filtered if filtered or candidate_video_ids else found
                    if len(candidates) >= minimum or current_top_k >= max_top_k:
                        break
                    current_top_k = min(max_top_k, current_top_k * widening)
                if not candidates and candidate_video_ids:
                    # Recover the event globally.  The later Vortex--ATS stage
                    # still performs all temporal construction and DEV scoring.
                    candidates = retrieve(event_index, max_top_k)
                    fallback_stages.append(f"global_recovery_event_{event_index}")
            candidates = temporal_nms(
                candidates,
                int(config.get("per_video_candidate_min_gap_ms", 1500)),
                int(config.get("per_event_per_video_limit", 12)),
            )
            candidate_sets.append(candidates)
            plan = plans[event_index - 1]
            event_summaries.append({
                "event_index": event_index, "query": query,
                "multi_views": self._event_semantic_views(plan, query, max_views=8),
                "candidate_count": len(candidates), "importance": round(float(plan.get("importance", 1.0)), 4),
                "diagnostic_prior": round(float(plan.get("diagnostic_prior", 0.5)), 4),
            })

        weights = [max(0.0, float(plan.get("importance", 1.0))) for plan in plans[:len(events)]]
        if not weights or sum(weights) <= 0:
            weights = [1.0] * len(events)
        ratio = max(0.0, min(1.0, float(config.get("min_match_ratio", 0.5))))
        min_match = request.options.min_match or max(1, math.ceil(len(events) * ratio))
        if len(events) > 1:
            min_match = max(2, min_match)
        sequences = build_dev_first_vortex_ats_sequences(
            candidate_sets, weights, diagnostic_index,
            normalized.get("temporal_edges") if isinstance(normalized.get("temporal_edges"), list) else [],
            config, min_match, max(request.top_k * 4, request.top_k),
            request.options.delta_t_max_ms,
        )
        target_scope = str(normalized.get("target_scope") or "frame")
        anchor_index = normalized.get("temporal_anchor_index")
        if target_scope == "frame" and anchor_index:
            sequences = [sequence for sequence in sequences if any(item.event_index == anchor_index for item in sequence.candidates)]
        sequences = sequences[:request.top_k]
        ids = {candidate.frame_id for sequence in sequences for candidate in sequence.candidates}
        frames = {frame.id: frame for frame in self.db.query(Frame).options(joinedload(Frame.video)).filter(Frame.id.in_(ids)).all()} if ids else {}
        coordinate = "timestamp_ms" if all(candidate.timestamp_ms is not None for candidates in candidate_sets for candidate in candidates) else "frame_idx_fallback"
        items: list[ResultItem] = []
        for rank, sequence in enumerate(sequences, start=1):
            anchor_candidate = next(
                (item for item in sequence.candidates if item.event_index == anchor_index),
                None,
            ) if target_scope == "frame" and anchor_index else None
            representative = anchor_candidate or max(
                sequence.candidates,
                key=lambda item: item.calibrated_event_score,
            )
            representative_policy = (
                "requested_anchor_event"
                if anchor_candidate is not None
                else config.get("representative_frame_policy", "highest_confidence_matched_event")
            )
            frame = frames.get(representative.frame_id)
            if frame is None:
                continue
            sequence_frames = [{
                "frame_id": item.frame_id, "frame_idx": item.frame_idx, "video_code": item.video_code,
                "timestamp_ms": item.timestamp_ms, "score": round(item.calibrated_event_score, 4),
                "event_index": item.event_index, "event_query": item.event_query,
                "thumbnail_url": f"/api/media/frames/{item.frame_id}/thumbnail",
                "image_url": self._browser_image_url(frames.get(item.frame_id)),
            } for item in sequence.candidates]
            result = RetrievalResult(
                id=new_id(), query_run_id=run.id, rank=rank, video_id=sequence.video_id,
                frame_id=representative.frame_id, score=sequence.score,
                score_breakdown={
                    "temporal_score": round(sequence.score, 6), "final_score": round(sequence.score, 6),
                    "temporal_reranker": "dev_first_vortex_ats", "temporal_strategy": "dev_first_search",
                    "temporal_components": ["vortex_hard_anchor", "aithena_ats_recovery", "dev_sequence_score"],
                    "target_scope": target_scope, "representative_frame_policy": representative_policy,
                    "representative_event_index": representative.event_index, "diagnostic_event_index": diagnostic_index,
                    "diagnostic_events": diagnostics, "candidate_video_count": len(candidate_video_ids),
                    "fallback_stages": fallback_stages, "matched_events": len(sequence.candidates),
                    "expected_events": len(events), "min_match": min_match, "event_queries": event_summaries,
                    "event_weights": [round(weight, 6) for weight in weights], "temporal_coordinate": coordinate,
                    "sequence_evidence": sequence.details,
                }, sequence_frames=sequence_frames,
            )
            result.frame, result.video = frame, frame.video
            self.db.add(result)
            items.append(self._result_to_item(result))
        return items

    def _search_temporal_kis(
        self,
        run: QueryRun,
        dataset: Dataset,
        request: SearchRequest,
        normalized: dict[str, Any],
    ) -> list[ResultItem]:
        if request.options.temporal_strategy == "dev_first_search":
            return self._search_dev_first_kis(run, dataset, request, normalized)
        events = self._dedupe_query_variants(normalized["temporal_events"] or [request.query_text], max_variants=8)
        event_plans = normalized.get("temporal_event_plans") if isinstance(normalized.get("temporal_event_plans"), list) else []
        profile = self.profiles.get(request.profile, self.profiles.get("competition_default", {}))
        temporal_cfg = profile.get("temporal", {}) if isinstance(profile.get("temporal"), dict) else {}

        candidate_sets: list[list[Candidate]] = []
        event_summaries: list[dict[str, Any]] = []
        per_event_top_k = max(80, min(500, request.top_k * 20))

        for event_index, event_query in enumerate(events, start=1):
            event_plan = event_plans[event_index - 1] if event_index - 1 < len(event_plans) else {}
            event_plan = event_plan if isinstance(event_plan, dict) else {}
            event_weights = self._normalize_retrieval_weights(event_plan.get("retrieval_weights"))
            if not event_weights:
                event_weights = normalized.get("retrieval_weights")
            event_weight_source = str(event_plan.get("retrieval_weight_source") or normalized.get("retrieval_weight_source") or "profile")
            event_text_source_weights = self._normalize_text_source_weights(event_plan.get("text_source_weights"))
            if not event_text_source_weights:
                event_text_source_weights = self._normalize_text_source_weights(normalized.get("text_source_weights"))
            event_semantic_views = self._event_semantic_views(event_plan, event_query, max_views=8)
            event_text_views = self._event_text_views(event_plan, str(event_plan.get("text_query") or event_query), max_views=8)
            ranked = self._rank_frames_multiperspective(
                dataset=dataset,
                semantic_views=event_semantic_views,
                text_views=event_text_views,
                query_text=event_text_views[0] if event_text_views else event_query,
                profile_name=request.profile,
                options=request.options,
                top_k=per_event_top_k,
                retrieval_weights=event_weights,
                use_agent_retrieval_weights=event_weight_source == "agent",
                text_source_weights=event_text_source_weights,
            )
            candidates = [
                Candidate(
                    frame_id=item.frame.id,
                    video_id=item.frame.video_id,
                    video_code=item.frame.video.video_code,
                    frame_idx=item.frame.frame_idx,
                    score=item.final_score,
                    text="",
                    event_index=event_index,
                    event_query=event_query,
                    visual_score=item.semantic_score,
                    text_score=item.text_score,
                    rrf_score=item.rrf_score,
                    timestamp_ms=item.frame.timestamp_ms if item.frame.timestamp_ms is not None else None,
                )
                for item in ranked
            ]
            # _rank_frames_multiperspective fuses independent views after its
            # frame-level diversification, so run temporal NMS here on the
            # fused pool. The pool is intentionally still deep for recall.
            candidates = nms_event_candidates(
                candidates,
                min_gap_ms=max(0, int(temporal_cfg.get("event_nms_window_ms", 1500))),
                per_video_limit=max(1, int(temporal_cfg.get("event_nms_per_video_limit", temporal_cfg.get("per_query_video_limit", 24)))),
                min_gap_frames=max(1, int(temporal_cfg.get("event_nms_window_frames", 1))),
            )
            candidate_sets.append(candidates)
            event_summaries.append(
                {
                    "event_index": event_index,
                    "query": event_query,
                    "multi_views": event_semantic_views,
                    "text_views": event_text_views,
                    "view_count": len(event_semantic_views),
                    "search_mode": "aithena_independent_view_merge",
                    "candidate_count": len(candidates),
                    "importance": round(float(event_plan.get("importance", 1.0)), 4),
                    "retrieval_weights": event_weights,
                    "retrieval_weight_source": event_weight_source,
                    "text_source_weights": event_text_source_weights,
                }
            )

        anchor_index = int(normalized.get("temporal_anchor_index") or 1)
        # frame_idx remains only as a compatibility fallback for legacy frames
        # with no timestamp. Timestamp-aware temporal methods receive the
        # request's millisecond window directly.
        fallback_delta_frames = max(1, int(request.options.delta_t_max_ms / 1000 * 30))
        event_weights = [max(0.0, float(plan.get("importance", 1.0))) for plan in event_plans[: len(events)]]
        if len(event_weights) != len(events) or sum(event_weights) <= 0:
            event_weights = [1.0 for _ in events]
        else:
            scale = len(events) / sum(event_weights)
            event_weights = [value * scale for value in event_weights]
        compactness_weight = max(0.0, min(1.0, float(temporal_cfg.get("compactness_weight", 0.0))))
        gap_penalty = max(0.0, min(1.0, float(temporal_cfg.get("gap_penalty", 0.0))))
        strategy = request.options.temporal_strategy
        if strategy == "aithena_weighted_ats":
            # AIThena ATS can rank a valid partial chain when one event has no
            # strong candidate, while still preferring complete sequences when available.
            min_match = request.options.min_match or min(
                len(events),
                max(2, math.ceil(len(events) * 0.6)),
            )
            sequences = adaptive_temporal_search(
                candidate_sets=candidate_sets,
                weights=event_weights,
                delta_frame_max=fallback_delta_frames,
                delta_t_max_ms=request.options.delta_t_max_ms,
                min_match=min_match,
                limit=max(request.top_k * 4, request.top_k),
                per_query_video_limit=max(1, int(temporal_cfg.get("per_query_video_limit", 24))),
                prefer_full_sequences=bool(temporal_cfg.get("prefer_full_sequences", True)),
                compactness_weight=compactness_weight,
                per_video_sequence_limit=max(0, int(temporal_cfg.get("per_video_sequence_limit", 4))),
                sequence_nms_window_ms=max(0, int(temporal_cfg.get("sequence_nms_window_ms", 3000))),
                sequence_nms_window_frames=max(1, int(temporal_cfg.get("sequence_nms_window_frames", 1))),
            )
            reranker = "aithena_weighted_ats"
        else:
            sequences = vortex_k_context_rerank(
                candidate_sets=candidate_sets,
                weights=event_weights,
                anchor_index=anchor_index,
                delta_frame_max=fallback_delta_frames,
                limit=max(request.top_k * 4, request.top_k),
                per_query_video_limit=max(1, int(temporal_cfg.get("per_query_video_limit", 24))),
                compactness_weight=compactness_weight,
                gap_penalty=gap_penalty,
                delta_t_max_ms=request.options.delta_t_max_ms,
                anchor_nms_window_ms=max(0, int(temporal_cfg.get("anchor_nms_window_ms", temporal_cfg.get("event_nms_window_ms", 1500)))),
                anchor_nms_window_frames=max(1, int(temporal_cfg.get("anchor_nms_window_frames", temporal_cfg.get("event_nms_window_frames", 1)))),
            )
            min_match = 1
            reranker = "vortex_k_context"

        anchored_sequences = [
            sequence
            for sequence in sequences
            if any(candidate.event_index == anchor_index for candidate in sequence.candidates)
        ]
        anchored_sequences = diversify_temporal_sequences(
            anchored_sequences,
            max_sequences_per_video=max(1, int(temporal_cfg.get("max_sequences_per_video", 1))),
            representative_event_index=anchor_index,
            min_representative_gap_ms=max(0, int(temporal_cfg.get("min_representative_gap_ms", 10000))),
            min_representative_gap_frames=max(1, int(temporal_cfg.get("min_representative_gap_frames", 1))),
            sequence_nms_window_ms=max(0, int(temporal_cfg.get("sequence_nms_window_ms", 3000))),
            sequence_nms_window_frames=max(1, int(temporal_cfg.get("sequence_nms_window_frames", 1))),
        )[: request.top_k]
        frame_ids = {
            candidate.frame_id
            for sequence in anchored_sequences
            for candidate in sequence.candidates
            if candidate.frame_id
        }
        frames_by_id = {
            frame.id: frame
            for frame in self.db.query(Frame)
            .options(joinedload(Frame.video))
            .filter(Frame.id.in_(frame_ids))
            .all()
        } if frame_ids else {}
        items: list[ResultItem] = []
        for rank, sequence in enumerate(anchored_sequences, start=1):
            anchor = next((item for item in sequence.candidates if item.event_index == anchor_index), None)
            if anchor is None:
                continue
            anchor_frame = frames_by_id.get(anchor.frame_id)
            if anchor_frame is None:
                continue
            frame_indices = [item.frame_idx for item in sequence.candidates]
            timestamps = [item.timestamp_ms for item in sequence.candidates]
            temporal_coordinates = [timestamp if timestamp is not None else frame_idx for timestamp, frame_idx in zip(timestamps, frame_indices)]
            temporal_deltas = [temporal_coordinates[index] - temporal_coordinates[index - 1] for index in range(1, len(temporal_coordinates))]
            sequence_frames = [
                {
                    "frame_id": item.frame_id,
                    "frame_idx": item.frame_idx,
                    "video_code": item.video_code,
                    "timestamp_ms": frames_by_id.get(item.frame_id).timestamp_ms if frames_by_id.get(item.frame_id) else None,
                    "score": round(item.score, 4),
                    "visual_score": round(item.visual_score, 4),
                    "text_score": round(item.text_score, 4),
                    "rrf_score": round(item.rrf_score, 4),
                    "order_index": index + 1,
                    "event_index": item.event_index,
                    "event_query": item.event_query,
                    "delta_from_previous": None if index == 0 else temporal_coordinates[index] - temporal_coordinates[index - 1],
                    "thumbnail_url": f"/api/media/frames/{item.frame_id}/thumbnail" if item.frame_id else None,
                    "image_url": self._browser_image_url(frames_by_id.get(item.frame_id)),
                    "image_uri": frames_by_id.get(item.frame_id).image_uri if frames_by_id.get(item.frame_id) else None,
                    "image_storage_key": frames_by_id.get(item.frame_id).image_storage_key if frames_by_id.get(item.frame_id) else None,
                }
                for index, item in enumerate(sequence.candidates)
            ]
            result = RetrievalResult(
                id=new_id(),
                query_run_id=run.id,
                rank=rank,
                video_id=anchor.video_id,
                frame_id=anchor.frame_id,
                score=sequence.score,
                score_breakdown={
                    "temporal_score": round(sequence.score, 6),
                    "semantic_score": round(anchor.visual_score, 6),
                    "text_score": round(anchor.text_score, 6),
                    "rrf_score": round(anchor.rrf_score, 6),
                    "final_score": round(sequence.score, 6),
                    "temporal_reranker": reranker,
                    "temporal_strategy": strategy,
                    "temporal_anchor_index": anchor_index,
                    "temporal_anchor_frame_idx": anchor.frame_idx,
                    "matched_events": len(sequence.candidates),
                    "expected_events": len(events),
                    "min_match": min_match,
                    "event_queries": event_summaries,
                    "multiperspective_fusion": "aithena_independent_view_merge",
                    "event_weights": [round(weight, 6) for weight in event_weights],
                    "compactness_weight": compactness_weight,
                    "gap_penalty": gap_penalty,
                    "ordering": {
                        "coordinate": "timestamp_ms" if all(timestamp is not None for timestamp in timestamps) else "frame_idx_fallback",
                        "is_strictly_increasing": all(delta > 0 for delta in temporal_deltas) if temporal_deltas else True,
                        "frame_indices": frame_indices,
                        "timestamps_ms": timestamps,
                        "delta_t": temporal_deltas,
                    },
                },
                sequence_frames=sequence_frames,
            )
            result.frame = anchor_frame
            result.video = anchor_frame.video
            self.db.add(result)
            items.append(self._result_to_item(result))
        return items

    def _search_trake(
        self,
        run: QueryRun,
        dataset: Dataset,
        request: SearchRequest,
        normalized: dict[str, Any],
    ) -> list[ResultItem]:
        events = self._dedupe_query_variants(normalized["temporal_events"] or [request.query_text], max_variants=8)
        event_plans = normalized.get("temporal_event_plans") if isinstance(normalized.get("temporal_event_plans"), list) else []
        candidate_sets: list[list[Candidate]] = []
        event_summaries: list[dict[str, Any]] = []
        per_event_top_k = max(80, min(500, request.top_k * 20))
        for event_index, event_query in enumerate(events, start=1):
            event_plan = event_plans[event_index - 1] if event_index - 1 < len(event_plans) else {}
            event_plan = event_plan if isinstance(event_plan, dict) else {}
            event_weights = self._normalize_retrieval_weights(event_plan.get("retrieval_weights"))
            if not event_weights:
                event_weights = normalized.get("retrieval_weights")
            event_weight_source = str(event_plan.get("retrieval_weight_source") or normalized.get("retrieval_weight_source") or "profile")
            event_text_source_weights = self._normalize_text_source_weights(event_plan.get("text_source_weights"))
            if not event_text_source_weights:
                event_text_source_weights = self._normalize_text_source_weights(normalized.get("text_source_weights"))
            event_semantic_views = self._event_semantic_views(event_plan, event_query, max_views=8)
            event_text_views = self._event_text_views(event_plan, str(event_plan.get("text_query") or event_query), max_views=8)
            ranked = self._rank_frames(
                dataset=dataset,
                semantic_variants=event_semantic_views,
                text_variants=event_text_views,
                query_text=event_text_views[0] if event_text_views else event_query,
                profile_name=request.profile,
                options=request.options,
                top_k=per_event_top_k,
                retrieval_weights=event_weights,
                use_agent_retrieval_weights=event_weight_source == "agent",
                text_source_weights=event_text_source_weights,
            )
            event_candidates = [
                Candidate(
                    frame_id=candidate.frame.id,
                    video_id=candidate.frame.video_id,
                    video_code=candidate.frame.video.video_code,
                    frame_idx=candidate.frame.frame_idx,
                    score=candidate.final_score,
                    text="",
                    event_index=event_index,
                    event_query=event_query,
                    visual_score=candidate.semantic_score,
                    text_score=candidate.text_score,
                    rrf_score=candidate.rrf_score,
                )
                for candidate in ranked
            ]
            candidate_sets.append(event_candidates)
            event_summaries.append(
                {
                    "event_index": event_index,
                    "query": event_query,
                    "multi_views": event_semantic_views,
                    "text_views": event_text_views,
                    "view_count": len(event_semantic_views),
                    "candidate_count": len(event_candidates),
                    "importance": round(float(event_plan.get("importance", 1.0)), 4),
                    "retrieval_weights": event_weights,
                    "retrieval_weight_source": event_weight_source,
                    "text_source_weights": event_text_source_weights,
                }
            )
        delta_frames = max(1, int(request.options.delta_t_max_ms / 1000 * 30))
        min_match = request.options.min_match or len(events)
        event_importances = [max(0.0, float(plan.get("importance", 1.0))) for plan in event_plans[: len(events)]]
        if len(event_importances) != len(events) or sum(event_importances) <= 0:
            event_importances = [1.0 for _ in events]
        else:
            scale = len(events) / sum(event_importances)
            event_importances = [value * scale for value in event_importances]
        profile = self.profiles.get(request.profile, self.profiles.get("competition_default", {}))
        temporal_cfg = profile.get("temporal", {}) if isinstance(profile.get("temporal"), dict) else {}
        sequences = adaptive_temporal_search(
            candidate_sets=candidate_sets,
            weights=event_importances,
            delta_frame_max=delta_frames,
            min_match=min_match,
            limit=request.top_k,
            per_query_video_limit=max(1, int(temporal_cfg.get("per_query_video_limit", 24))),
            prefer_full_sequences=bool(temporal_cfg.get("prefer_full_sequences", True)),
        )
        sequences = sorted(
            sequences,
            key=lambda item: (
                -round(item.score, 8),
                -len(item.candidates),
                item.video_code,
                item.candidates[0].frame_idx if item.candidates else 10**9,
                tuple(candidate.frame_idx for candidate in item.candidates),
            ),
        )
        sequence_frame_ids = {
            candidate.frame_id
            for sequence in sequences
            for candidate in sequence.candidates
            if candidate.frame_id
        }
        sequence_frames_by_id = {
            frame.id: frame
            for frame in self.db.query(Frame)
            .options(joinedload(Frame.video))
            .filter(Frame.id.in_(sequence_frame_ids))
            .all()
        } if sequence_frame_ids else {}
        items: list[ResultItem] = []
        for rank, sequence in enumerate(sequences, start=1):
            representative = sequence.candidates[len(sequence.candidates) // 2]
            frame_indices = [candidate.frame_idx for candidate in sequence.candidates]
            delta_frames_seq = [frame_indices[idx] - frame_indices[idx - 1] for idx in range(1, len(frame_indices))]
            sequence_frames = [
                {
                    "frame_id": candidate.frame_id,
                    "frame_idx": candidate.frame_idx,
                    "video_code": candidate.video_code,
                    "timestamp_ms": sequence_frames_by_id.get(candidate.frame_id).timestamp_ms
                    if sequence_frames_by_id.get(candidate.frame_id)
                    else None,
                    "score": round(candidate.score, 4),
                    "visual_score": round(candidate.visual_score, 4),
                    "text_score": round(candidate.text_score, 4),
                    "rrf_score": round(candidate.rrf_score, 4),
                    "order_index": idx + 1,
                    "event_index": candidate.event_index or idx + 1,
                    "event_query": candidate.event_query,
                    "delta_from_previous": None if idx == 0 else candidate.frame_idx - sequence.candidates[idx - 1].frame_idx,
                    "thumbnail_url": f"/api/media/frames/{candidate.frame_id}/thumbnail" if candidate.frame_id else None,
                    "image_url": self._browser_image_url(sequence_frames_by_id.get(candidate.frame_id)),
                    "image_uri": sequence_frames_by_id.get(candidate.frame_id).image_uri
                    if sequence_frames_by_id.get(candidate.frame_id)
                    else None,
                    "image_storage_key": sequence_frames_by_id.get(candidate.frame_id).image_storage_key
                    if sequence_frames_by_id.get(candidate.frame_id)
                    else None,
                }
                for idx, candidate in enumerate(sequence.candidates)
            ]
            representative_frame = sequence_frames_by_id.get(representative.frame_id)
            if representative_frame is None:
                continue
            sequence_visual_score = sum(candidate.visual_score for candidate in sequence.candidates) / len(sequence.candidates)
            sequence_text_score = sum(candidate.text_score for candidate in sequence.candidates) / len(sequence.candidates)
            sequence_rrf_score = sum(candidate.rrf_score for candidate in sequence.candidates) / len(sequence.candidates)
            result = RetrievalResult(
                id=new_id(),
                query_run_id=run.id,
                rank=rank,
                video_id=representative.video_id,
                frame_id=representative.frame_id,
                score=sequence.score,
                score_breakdown={
                    "temporal_score": round(sequence.score, 4),
                    "semantic_score": round(sequence_visual_score, 6),
                    "text_score": round(sequence_text_score, 6),
                    "rrf_score": round(sequence_rrf_score, 6),
                    "final_score": round(sequence.score, 6),
                    "matched_events": len(sequence.candidates),
                    "expected_events": len(events),
                    "min_match": min_match,
                    "requires_full_sequence": request.options.min_match is None,
                    "event_queries": event_summaries,
                    "event_weights": [round(weight, 6) for weight in event_importances],
                    "temporal_reranker": "aithena_weighted_ats",
                    "partial_match_enabled": min_match < len(events),
                    "ordering": {
                        "is_strictly_increasing": all(delta > 0 for delta in delta_frames_seq) if delta_frames_seq else True,
                        "frame_indices": frame_indices,
                        "delta_frames": delta_frames_seq,
                        "stable_sort_key": [
                            round(sequence.score, 8),
                            len(sequence.candidates),
                            sequence.video_code,
                            frame_indices[0] if frame_indices else None,
                        ],
                    },
                },
                sequence_frames=sequence_frames,
            )
            result.frame = representative_frame
            result.video = representative_frame.video
            self.db.add(result)
            items.append(self._result_to_item(result))
        return items

    @timed("ranking")
    def _rank_frames(
        self,
        dataset: Dataset,
        semantic_variants: list[str],
        text_variants: list[str],
        query_text: str,
        profile_name: str,
        options: SearchOptions,
        top_k: int,
        retrieval_weights: dict[str, float] | None = None,
        use_agent_retrieval_weights: bool = False,
        text_source_weights: dict[str, float] | None = None,
    ) -> list[FrameScore]:
        profile = self.profiles.get(profile_name, self.profiles.get("competition_default", {}))
        semantic_weight = float(profile.get("semantic_weight", 0.6))
        text_weight = float(profile.get("metadata_weight", profile.get("text_weight", 0.25)))
        quality_weight = float(profile.get("quality_weight", profile.get("user_boost_weight", 0.05)))
        modality_weights = self._normalize_retrieval_weights(retrieval_weights)
        if options.source_mode != "auto":
            text_source_weights = {"ocr": 0.0, "asr": 0.0, "caption": 0.0}
            text_source_weights["caption" if options.source_mode == "scene" else options.source_mode] = 1.0
            if options.source_mode in {"ocr", "asr"}:
                modality_weights = {"visual": 0.0, "text": 1.0}
        if modality_weights:
            modality_total = semantic_weight + text_weight
            semantic_weight = modality_total * modality_weights["visual"]
            text_weight = modality_total * modality_weights["text"]
        rrf_config = profile.get("rrf", {})
        rrf_enabled = bool(rrf_config.get("enabled", False))
        rrf_k = max(1.0, float(rrf_config.get("k", 60)))
        rrf_blend = min(1.0, max(0.0, float(rrf_config.get("blend", 0.35))))
        rrf_semantic_weight = float(rrf_config.get("semantic_weight", semantic_weight))
        rrf_text_weight = float(rrf_config.get("text_weight", text_weight))
        if use_agent_retrieval_weights and modality_weights:
            rrf_total = rrf_semantic_weight + rrf_text_weight
            rrf_semantic_weight = rrf_total * modality_weights["visual"]
            rrf_text_weight = rrf_total * modality_weights["text"]
        configured_ann_top_k = int(profile.get("milvus", {}).get("top_k_per_model", 0))
        diversification_config = profile.get("result_diversification", {})
        diversification_config = diversification_config if isinstance(diversification_config, dict) else {}
        candidate_pool_multiplier = max(1, int(diversification_config.get("candidate_pool_multiplier", 20)))
        min_candidate_pool = max(1, int(diversification_config.get("min_candidate_pool", 500)))
        max_candidate_pool = max(top_k, int(diversification_config.get("max_candidate_pool", 1000)))
        # Diversification can only surface other videos if the retrieval pool has
        # candidates beyond the first cluster from a single video.
        ann_top_k = min(
            max_candidate_pool,
            max(configured_ann_top_k, top_k * candidate_pool_multiplier, min_candidate_pool, 1),
        )

        dataset_video_ids = self._dataset_video_ids(dataset)
        semantic_scores, semantic_backend_error = ({}, False) if options.source_mode in {"ocr", "asr"} else self._semantic_scores(
            semantic_variants,
            ann_top_k,
            dataset_video_ids,
            profile,
            request_visual_search_mode=options.visual_search_mode,
        )
        if options.use_metadata or options.source_mode != "auto":
            text_scores, text_backend_error = self._text_scores(
                text_variants,
                semantic_variants,
                ann_top_k,
                dataset_video_ids,
                profile,
                text_source_weights,
            )
        else:
            text_scores, text_backend_error = {}, False

        # Aggregate across perspectives/events; never erase an earlier failure.
        for name, error, scores, enabled in (
            ("semantic", semantic_backend_error, semantic_scores, options.source_mode not in {"ocr", "asr"}),
            ("text", text_backend_error, text_scores, options.use_metadata or options.source_mode != "auto"),
        ):
            state = "disabled" if not enabled else ("degraded" if scores else "unavailable") if error else "ok"
            if self._source_status.get(name) not in {"degraded", "unavailable"}:
                self._source_status[name] = state

        if options.strict_hybrid and (semantic_backend_error or text_backend_error):
            failed_backends: list[str] = []
            if semantic_backend_error:
                failed_backends.append("semantic (embedder/Milvus)")
            if text_backend_error:
                failed_backends.append("metadata (Elasticsearch)")
            raise ValueError(
                "strict_hybrid is enabled and backend retrieval failed for: "
                + ", ".join(failed_backends)
                + "."
            )

        candidate_ids = set(semantic_scores).union(text_scores)
        if not candidate_ids:
            if options.strict_hybrid:
                raise ValueError(
                    "strict_hybrid is enabled and hybrid retrieval returned no candidates; fallback ranking is disabled."
                )
            # Empty indexed retrieval must remain empty. Scanning metadata here
            # invents visual scores and makes no-match latency scale with dataset.
            return []

        load_options = [joinedload(Frame.video)]
        if self._needs_frame_annotations(options):
            load_options.append(selectinload(Frame.annotations))
        frames = (
            self.db.query(Frame)
            .options(*load_options)
            .filter(Frame.keyframe_id.in_(candidate_ids))
            .all()
        )
        if not frames:
            return []

        filtered_frames, filter_debug = self._apply_filters(frames, options)
        if not filtered_frames:
            return []

        filtered_ids = {frame.keyframe_id for frame in filtered_frames}
        semantic_rank_map = self._rank_map(semantic_scores, filtered_ids)
        text_rank_map = self._rank_map(text_scores, filtered_ids)
        semantic_max = max((semantic_scores.get(frame_id, 0.0) for frame_id in filtered_ids), default=1.0) or 1.0
        text_max = max((text_scores.get(frame_id, 0.0) for frame_id in filtered_ids), default=1.0) or 1.0

        intermediate: list[dict[str, Any]] = []
        max_rrf_raw = 0.0
        for frame in filtered_frames:
            frame_id = frame.keyframe_id
            semantic_score = (semantic_scores.get(frame_id, 0.0) / semantic_max) if semantic_max > 0 else 0.0
            text_score = (text_scores.get(frame_id, 0.0) / text_max) if text_max > 0 else 0.0
            quality_score = max(0.0, min(1.0, float(frame.quality_score or 0.0)))
            weighted_score = (
                semantic_weight * semantic_score
                + text_weight * text_score
                + quality_weight * quality_score
            )
            rrf_raw = 0.0
            if rrf_enabled:
                semantic_rank = semantic_rank_map.get(frame_id)
                text_rank = text_rank_map.get(frame_id)
                semantic_rrf = (1.0 / (rrf_k + semantic_rank)) if semantic_rank else 0.0
                text_rrf = (1.0 / (rrf_k + text_rank)) if text_rank else 0.0
                rrf_raw = rrf_semantic_weight * semantic_rrf + rrf_text_weight * text_rrf
                max_rrf_raw = max(max_rrf_raw, rrf_raw)
            intermediate.append(
                {
                    "frame": frame,
                    "semantic_score": semantic_score,
                    "text_score": text_score,
                    "quality_score": quality_score,
                    "weighted_score": weighted_score,
                    "rrf_raw": rrf_raw,
                    "source_hit": self._semantic_hit_sources.get(frame_id, {}),
                    "text_hit": self._text_hit_sources.get(frame_id, {}),
                }
            )

        scored: list[FrameScore] = []
        for item in intermediate:
            frame = item["frame"]
            rrf_score = (item["rrf_raw"] / max_rrf_raw) if rrf_enabled and max_rrf_raw > 0 else 0.0
            final_score = (
                (1.0 - rrf_blend) * item["weighted_score"] + rrf_blend * rrf_score
                if rrf_enabled
                else item["weighted_score"]
            )
            scored.append(
                FrameScore(
                    frame=frame,
                    semantic_score=item["semantic_score"],
                    text_score=item["text_score"],
                    quality_score=item["quality_score"],
                    weighted_score=item["weighted_score"],
                    rrf_score=rrf_score,
                    final_score=final_score,
                    filter_debug=filter_debug.get(frame.keyframe_id, {}),
                    source_hit=item["source_hit"],
                    text_hit=item["text_hit"],
                )
            )
        scored.sort(key=lambda item: (item.final_score, item.frame.frame_idx), reverse=True)
        reranked = self._apply_reranking(query_text=query_text, scored=scored, profile=profile, options=options)
        return self._diversify_ranked_frames(reranked, profile)

    @timed("semantic")
    def _semantic_scores(
        self,
        variants: list[str],
        top_k: int,
        dataset_video_ids: set[str],
        profile: dict[str, Any],
        request_visual_search_mode: str = "profile",
    ) -> tuple[dict[str, float], bool]:
        self._semantic_hit_sources = {}
        if self.vector_client is None:
            return {}, True
        backend_error = False
        collections = self._semantic_collections(profile, request_visual_search_mode)
        if not collections:
            return {}, True
        visual_rrf_config = profile.get("visual_rrf", {})
        visual_rrf_enabled = len(collections) > 1 and bool(visual_rrf_config.get("enabled", True))
        visual_rrf_k = max(
            1.0,
            float(visual_rrf_config.get("k", profile.get("rrf", {}).get("k", 60))),
        )
        query_vectors_by_model: dict[str, list[list[float]]] = {}
        per_model_scores: dict[str, dict[str, float]] = {}
        per_model_weights: dict[str, float] = {}
        per_frame_sources: dict[str, dict[str, dict[str, Any]]] = {}
        for target in collections:
            model_id = target.model_key or target.collection
            per_model_weights.setdefault(model_id, target.weight)
            if model_id not in query_vectors_by_model:
                try:
                    embedder = self.model_registry.embedder_for(target.model_key)
                    batch_embed = getattr(embedder, "embed_texts", None)
                    vectors = batch_embed(variants) if callable(batch_embed) else [embedder.embed_text(value) for value in variants]
                    if len(vectors) != len(variants):
                        raise ValueError(f"Embedding batch returned {len(vectors)} vectors for {len(variants)} queries.")
                    query_vectors_by_model[model_id] = vectors
                except Exception:
                    # Keep retrieval available even when one embedder runtime is misconfigured.
                    backend_error = True
                    query_vectors_by_model[model_id] = [[] for _ in variants]
            for variant, query_vector in zip(variants, query_vectors_by_model[model_id], strict=True):
                if not query_vector:
                    continue
                try:
                    hits = self.vector_client.search(target.collection, query_vector, top_k=top_k)
                except Exception:
                    backend_error = True
                    continue
                for hit in hits:
                    frame_id = self._resolve_keyframe_id(hit.id, hit.metadata)
                    if not frame_id:
                        continue
                    if dataset_video_ids and self._resolve_video_id(frame_id, hit.metadata) not in dataset_video_ids:
                        continue
                    score = max(0.0, float(hit.score))
                    model_scores = per_model_scores.setdefault(model_id, {})
                    existing = model_scores.get(frame_id, 0.0)
                    if score > existing:
                        model_scores[frame_id] = score
                        source_hit = self._semantic_source_hit(
                            item_id=hit.id,
                            metadata=hit.metadata,
                            collection=target.collection,
                            model_key=target.model_key,
                            score=score,
                            resolved_frame_id=frame_id,
                        )
                        source_hit["variant"] = variant
                        source_hit["visual_model_family"] = self._visual_model_family(
                            target.model_key or target.collection,
                            {"model_key": target.model_key, "collection": target.collection},
                        )
                        source_hit["model_weight"] = target.weight
                        source_hit["weighted_similarity"] = round(score * target.weight, 6)
                        per_frame_sources.setdefault(frame_id, {})[model_id] = source_hit

        if visual_rrf_enabled:
            scores: dict[str, float] = {}
            rrf_sources: dict[str, list[dict[str, Any]]] = {}
            for model_id, model_scores in per_model_scores.items():
                rank_map = self._rank_map(model_scores, set(model_scores))
                weight = per_model_weights.get(model_id, 1.0)
                for frame_id, rank in rank_map.items():
                    contribution = weight / (visual_rrf_k + rank)
                    scores[frame_id] = scores.get(frame_id, 0.0) + contribution
                    source = dict(per_frame_sources.get(frame_id, {}).get(model_id, {}))
                    source.update(
                        {
                            "visual_rank": rank,
                            "visual_rrf_contribution": round(contribution, 8),
                        }
                    )
                    rrf_sources.setdefault(frame_id, []).append(source)
            for frame_id, sources in rrf_sources.items():
                sources.sort(key=lambda item: (item.get("visual_rank") or 10**9, item.get("model_key") or ""))
                self._semantic_hit_sources[frame_id] = {
                    "fusion": "visual_rrf",
                    "rrf_k": visual_rrf_k,
                    "formula": "sum(weight / (k + rank))",
                    "score": round(scores.get(frame_id, 0.0), 8),
                    "sources": sources,
                }
            return scores, backend_error

        scores: dict[str, float] = {}
        for model_id, model_scores in per_model_scores.items():
            weight = per_model_weights.get(model_id, 1.0)
            for frame_id, score in model_scores.items():
                weighted_score = score * weight
                if weighted_score > scores.get(frame_id, 0.0):
                    scores[frame_id] = weighted_score
                    self._semantic_hit_sources[frame_id] = per_frame_sources.get(frame_id, {}).get(model_id, {})
        return scores, backend_error

    def _semantic_collections(
        self,
        profile: dict[str, Any],
        visual_search_mode: str = "profile",
    ) -> list[SemanticCollection]:
        mode = self._normalize_visual_search_mode(visual_search_mode)
        requested_families = self._requested_visual_families(mode)
        explicit_model_choice = requested_families is not None
        collections: list[SemanticCollection] = []

        def append_from_config(
            name: str,
            config: dict[str, Any],
            *,
            honor_enabled: bool,
        ) -> None:
            if honor_enabled and config.get("enabled") is False:
                return
            if requested_families is not None and self._visual_model_family(name, config) not in requested_families:
                return
            collection = str(config.get("collection") or "").strip()
            if not collection:
                return
            model_key = str(config.get("model_key") or config.get("embedder") or "").strip() or None
            collections.append(
                SemanticCollection(
                    collection=collection,
                    weight=float(config.get("weight", 1.0)),
                    model_key=model_key,
                )
            )

        visual_models = profile.get("visual_models")
        if isinstance(visual_models, dict):
            for name, config in visual_models.items():
                if not isinstance(config, dict):
                    continue
                append_from_config(str(name), config, honor_enabled=not explicit_model_choice)

        milvus_profile = profile.get("milvus") if isinstance(profile.get("milvus"), dict) else {}
        if milvus_profile:
            append_from_config("milvus", milvus_profile, honor_enabled=False)

        if not collections and explicit_model_choice:
            collections.extend(self._registry_semantic_collections(requested_families))

        if not collections and not explicit_model_choice:
            embedder_entry = self.model_registry.first_enabled("embedders")
            if embedder_entry and isinstance(embedder_entry[1], dict):
                provider = str(embedder_entry[1].get("provider") or "").lower()
                collection = str(embedder_entry[1].get("collection") or "").strip()
                if provider in {"openai_compatible", "siglip2", "transformers_siglip2", "huggingface_siglip2"} and collection:
                    collections.append(SemanticCollection(collection=collection, model_key=embedder_entry[0]))

        if not collections and mode in {"profile", "openclip", "both"}:
            collections.append(SemanticCollection(collection="keyframe_embeddings"))

        deduped: list[SemanticCollection] = []
        seen: set[tuple[str, str | None]] = set()
        for item in collections:
            key = (item.collection, item.model_key)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _registry_semantic_collections(self, requested_families: set[str] | None) -> list[SemanticCollection]:
        collections: list[SemanticCollection] = []
        entries = self.model_registry.registry.get("embedders")
        if not isinstance(entries, dict):
            return collections
        supported_providers = {"openai_compatible", "siglip2", "transformers_siglip2", "huggingface_siglip2"}
        for name, config in entries.items():
            if not isinstance(config, dict):
                continue
            provider = str(config.get("provider") or "").lower()
            collection = str(config.get("collection") or "").strip()
            if provider not in supported_providers or not collection:
                continue
            if requested_families is not None and self._visual_model_family(str(name), config) not in requested_families:
                continue
            collections.append(
                SemanticCollection(
                    collection=collection,
                    weight=float(config.get("weight", 1.0)),
                    model_key=str(name),
                )
            )
        return collections

    def _visual_search_summary(self, profile: dict[str, Any], visual_search_mode: str) -> dict[str, Any]:
        mode = self._normalize_visual_search_mode(visual_search_mode)
        collections = self._semantic_collections(profile, mode)
        visual_rrf_config = profile.get("visual_rrf", {}) if isinstance(profile.get("visual_rrf"), dict) else {}
        rrf_enabled = len(collections) > 1 and bool(visual_rrf_config.get("enabled", True))
        rrf_k = max(1.0, float(visual_rrf_config.get("k", profile.get("rrf", {}).get("k", 60))))
        return {
            "mode": mode,
            "fusion": "visual_rrf" if rrf_enabled else "single_vector",
            "formula": "sum(weight / (k + rank))" if rrf_enabled else "max(weight * similarity)",
            "rrf_k": rrf_k if rrf_enabled else None,
            "models": [
                {
                    "model_key": item.model_key,
                    "collection": item.collection,
                    "weight": item.weight,
                    "family": self._visual_model_family(
                        item.model_key or item.collection,
                        {"model_key": item.model_key, "collection": item.collection},
                    ),
                }
                for item in collections
            ],
        }

    @staticmethod
    def _normalize_visual_search_mode(raw_mode: Any) -> str:
        mode = str(raw_mode or "profile").strip().lower()
        if mode in {"rrf", "rff", "ensemble", "all"}:
            return "both"
        if mode in {"clip", "open_clip"}:
            return "openclip"
        if mode in {"siglip", "siglip_2"}:
            return "siglip2"
        if mode in {"profile", "openclip", "siglip2", "both"}:
            return mode
        return "profile"

    @staticmethod
    def _requested_visual_families(mode: str) -> set[str] | None:
        if mode == "openclip":
            return {"openclip"}
        if mode == "siglip2":
            return {"siglip2"}
        if mode == "both":
            return {"openclip", "siglip2"}
        return None

    def _visual_model_family(self, name: str, config: dict[str, Any]) -> str:
        parts = [name]
        keys = (
            "model_key",
            "embedder",
            "collection",
            "provider",
            "model",
            "openclip_model",
            "description",
            "extractor_version",
        )
        parts.extend(str(config.get(key) or "") for key in keys)
        model_key = str(config.get("model_key") or config.get("embedder") or name or "").strip()
        registry_config = {}
        entries = self.model_registry.registry.get("embedders")
        if isinstance(entries, dict) and isinstance(entries.get(model_key), dict):
            registry_config = entries[model_key]
        parts.extend(str(registry_config.get(key) or "") for key in keys)
        haystack = " ".join(parts).lower()
        if "siglip2" in haystack or "siglip-2" in haystack:
            return "siglip2"
        return "openclip"

    @timed("text")
    def _text_scores(
        self,
        lexical_variants: list[str],
        caption_variants: list[str],
        top_k: int,
        dataset_video_ids: set[str],
        profile: dict[str, Any],
        text_source_weights: dict[str, float] | None,
    ) -> tuple[dict[str, float], bool]:
        self._text_hit_sources = {}
        if self.text_client is None:
            return {}, True
        metadata_profile = profile.get("metadata", {})
        source_weights = self._normalize_text_source_weights(text_source_weights) or {"asr": 0.35, "caption": 0.5, "ocr": 0.15}
        asr_boost = float(metadata_profile.get("asr_boost", 2.5)) * source_weights["asr"]
        caption_boost = float(metadata_profile.get("caption_boost", 1.5)) * source_weights["caption"]
        ocr_boost = float(metadata_profile.get("ocr_boost", 3.0)) * source_weights["ocr"]
        source_searches = (
            ("asr", lexical_variants, {"asr_text": asr_boost, "normalized_asr_text": asr_boost}),
            ("ocr", lexical_variants, {"ocr_texts": ocr_boost}),
            ("caption", caption_variants, {"caption": caption_boost}),
        )
        scores: dict[str, float] = {}
        backend_error = False
        for expected_source, variants, boosts in source_searches:
            if not variants or not any(boost > 0 for boost in boosts.values()):
                continue
            for variant in variants:
                try:
                    hits = self.text_client.search(
                        "keyframe_annotations",
                        query=variant,
                        top_k=top_k,
                        boosts=boosts,
                        source_types=[expected_source],
                    )
                except Exception:
                    backend_error = True
                    continue
                for hit in hits:
                    frame_id = self._resolve_keyframe_id(hit.id, hit.metadata)
                    if not frame_id:
                        continue
                    if dataset_video_ids and self._resolve_video_id(frame_id, hit.metadata) not in dataset_video_ids:
                        continue
                    score = max(0.0, float(hit.score))
                    existing = scores.get(frame_id, 0.0)
                    if score > existing:
                        scores[frame_id] = score
                        self._text_hit_sources[frame_id] = self._text_source_hit(
                            metadata=hit.metadata,
                            score=score,
                            resolved_frame_id=frame_id,
                            variant=variant,
                            expected_source=expected_source,
                            source_weights=source_weights,
                        )
        return scores, backend_error

    def _text_source_hit(
        self,
        metadata: dict[str, Any],
        score: float,
        resolved_frame_id: str,
        variant: str,
        expected_source: str,
        source_weights: dict[str, float],
    ) -> dict[str, Any]:
        return {
            "source_type": metadata.get("source_type") or metadata.get("kind") or expected_source,
            "score": round(float(score), 6),
            "variant": variant,
            "keyframe_id": metadata.get("keyframe_id") or metadata.get("frame_id") or resolved_frame_id,
            "video_id": metadata.get("video_id"),
            "segment_id": metadata.get("segment_id"),
            "start_seconds": metadata.get("start_seconds"),
            "end_seconds": metadata.get("end_seconds"),
            "field": {"asr": "asr_text", "caption": "caption", "ocr": "ocr_texts"}.get(expected_source, "metadata"),
            "text_source_weights": source_weights,
            "snippet": self._text_hit_snippet(metadata),
        }

    def _text_hit_snippet(self, metadata: dict[str, Any]) -> str:
        for key in ("asr_text", "caption", "normalized_asr_text", "raw_asr_text", "text_value"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value[:240]
        ocr_texts = metadata.get("ocr_texts")
        if isinstance(ocr_texts, list):
            value = " ".join(str(item).strip() for item in ocr_texts if str(item).strip())
            if value:
                return value[:240]
        value = str(ocr_texts or "").strip()
        if value:
            return value[:240]
        return ""

    def _apply_reranking(
        self,
        query_text: str,
        scored: list[FrameScore],
        profile: dict[str, Any],
        options: SearchOptions,
    ) -> list[FrameScore]:
        raw_rerank_profile = profile.get("reranking", {})
        rerank_profile = raw_rerank_profile if isinstance(raw_rerank_profile, dict) else {}
        if not options.use_reranker or not bool(rerank_profile.get("enabled", False)) or not scored:
            return scored

        reranker = getattr(self.model_registry, "reranker", None)
        if reranker is None:
            return scored

        top_k = min(len(scored), max(1, int(rerank_profile.get("top_k", min(50, len(scored))))))
        blend = min(1.0, max(0.0, float(rerank_profile.get("blend", 0.35))))
        cross_weight = float(rerank_profile.get("cross_encoder_weight", 0.8))
        mllm_weight = float(rerank_profile.get("mllm_weight", 0.2))
        raw_mllm_cfg = rerank_profile.get("mllm", {})
        mllm_cfg = raw_mllm_cfg if isinstance(raw_mllm_cfg, dict) else {}
        mllm_enabled = bool(mllm_cfg.get("enabled", False))
        mllm_top_k = min(top_k, max(1, int(mllm_cfg.get("top_k", min(10, top_k)))))

        top_items = scored[:top_k]
        passages = [self._frame_text(item.frame) or item.frame.video.video_code for item in top_items]
        try:
            cross_scores = reranker.rerank(query_text, passages)
        except Exception:
            cross_scores = [0.0 for _ in top_items]
        cross_scores = self._normalize_signal(cross_scores)

        mllm_scores = [0.0 for _ in top_items]
        if mllm_enabled:
            for index, item in enumerate(top_items[:mllm_top_k]):
                evidence = passages[index]
                answer_hint = self._answer_hint(item.frame)
                try:
                    answer = self.model_registry.visual_qa.answer(query_text, evidence, answer_hint)
                except Exception:
                    answer = ""
                mllm_scores[index] = self._alignment_score(query_text, answer or evidence, evidence)
        mllm_scores = self._normalize_signal(mllm_scores)

        reranked_top: list[FrameScore] = []
        for index, item in enumerate(top_items):
            rerank_signal = (cross_weight * cross_scores[index]) + (mllm_weight * mllm_scores[index])
            final_score = (1.0 - blend) * item.final_score + blend * rerank_signal
            reranked_top.append(
                FrameScore(
                    frame=item.frame,
                    semantic_score=item.semantic_score,
                    text_score=item.text_score,
                    quality_score=item.quality_score,
                    weighted_score=item.weighted_score,
                    rrf_score=item.rrf_score,
                    final_score=final_score,
                    rerank_score=rerank_signal,
                    rerank_detail={
                        "cross_encoder": round(cross_scores[index], 6),
                        "cross_encoder_weight": cross_weight,
                        "mllm": round(mllm_scores[index], 6),
                        "mllm_weight": mllm_weight,
                        "blend": blend,
                        "backend": getattr(reranker, "backend", reranker.__class__.__name__),
                    },
                    filter_debug=item.filter_debug,
                    source_hit=item.source_hit,
                    text_hit=item.text_hit,
                )
            )

        reranked = reranked_top + scored[top_k:]
        reranked.sort(key=lambda item: (item.final_score, item.frame.frame_idx), reverse=True)
        return reranked

    @staticmethod
    def _diversify_ranked_frames(scored: list[FrameScore], profile: dict[str, Any]) -> list[FrameScore]:
        """Front-load distinct videos and well-separated moments without dropping recall.

        The full candidate list is retained. Only its order changes, so the interactive
        top-k shows broad video coverage first while callers that need a deeper pool can
        still access every close-by frame later in the list.
        """
        raw_config = profile.get("result_diversification", {})
        config = raw_config if isinstance(raw_config, dict) else {}
        if len(scored) < 2 or not bool(config.get("enabled", True)):
            return scored

        max_per_video = max(1, int(config.get("max_frames_per_video", 1)))
        min_gap_ms = max(0, int(config.get("min_frame_gap_ms", 10000)))
        min_gap_frames = max(0, int(config.get("min_frame_gap", 300)))
        by_video: dict[str, list[FrameScore]] = {}
        for item in scored:
            by_video.setdefault(item.frame.video_id, []).append(item)

        selected: list[FrameScore] = []
        selected_ids: set[str] = set()
        selected_by_video: dict[str, list[FrameScore]] = {}

        while True:
            round_candidates: list[FrameScore] = []
            for video_id, candidates in by_video.items():
                already_selected = selected_by_video.get(video_id, [])
                if len(already_selected) >= max_per_video:
                    continue
                candidate = next(
                    (
                        item
                        for item in candidates
                        if item.frame.keyframe_id not in selected_ids
                        and RetrievalService._is_frame_sufficiently_separated(
                            item,
                            already_selected,
                            min_gap_ms=min_gap_ms,
                            min_gap_frames=min_gap_frames,
                        )
                    ),
                    None,
                )
                if candidate is not None:
                    round_candidates.append(candidate)

            if not round_candidates:
                break
            round_candidates.sort(key=lambda item: (item.final_score, item.frame.frame_idx), reverse=True)
            for candidate in round_candidates:
                selected.append(candidate)
                selected_ids.add(candidate.frame.keyframe_id)
                selected_by_video.setdefault(candidate.frame.video_id, []).append(candidate)

        deferred = [item for item in scored if item.frame.keyframe_id not in selected_ids]
        return [*selected, *deferred]

    @staticmethod
    def _is_frame_sufficiently_separated(
        candidate: FrameScore,
        selected: list[FrameScore],
        *,
        min_gap_ms: int,
        min_gap_frames: int,
    ) -> bool:
        for previous in selected:
            candidate_timestamp = candidate.frame.timestamp_ms
            previous_timestamp = previous.frame.timestamp_ms
            if candidate_timestamp is not None and previous_timestamp is not None:
                if abs(int(candidate_timestamp) - int(previous_timestamp)) < min_gap_ms:
                    return False
            if abs(int(candidate.frame.frame_idx) - int(previous.frame.frame_idx)) < min_gap_frames:
                return False
        return True

    def _needs_frame_annotations(self, options: SearchOptions) -> bool:
        return bool(options.objects or (options.scene or "").strip() or options.use_reranker)

    def _normalize_signal(self, scores: list[float]) -> list[float]:
        if not scores:
            return []
        high = max(scores)
        low = min(scores)
        if math.isclose(high, low):
            return [1.0 if high > 0 else 0.0 for _ in scores]
        span = high - low
        return [(score - low) / span for score in scores]

    def _alignment_score(self, query_text: str, answer_text: str, evidence_text: str) -> float:
        answer_score = cosine_like_overlap(query_text, answer_text)
        evidence_score = cosine_like_overlap(query_text, evidence_text)
        return max(answer_score, evidence_score)

    def _normalized_filter_options(self, options: SearchOptions) -> dict[str, Any]:
        video_codes = sorted({code.strip() for code in options.video_codes if code.strip()})
        objects = sorted({obj.strip().lower() for obj in options.objects if obj.strip()})
        scene = (options.scene or "").strip()
        start = options.time_range_start_seconds
        end = options.time_range_end_seconds
        if start is not None and end is not None and start > end:
            raise ValueError("options.time_range_start_seconds must be <= options.time_range_end_seconds")
        return {
            "video_codes": video_codes,
            "time_range_start_seconds": start,
            "time_range_end_seconds": end,
            "objects": objects,
            "scene": scene or None,
        }

    def _apply_filters(self, frames: list[Frame], options: SearchOptions) -> tuple[list[Frame], dict[str, dict[str, Any]]]:
        filters = self._normalized_filter_options(options)
        video_codes = set(filters["video_codes"])
        objects = set(filters["objects"])
        scene = (filters.get("scene") or "").lower()
        start = filters.get("time_range_start_seconds")
        end = filters.get("time_range_end_seconds")

        selected: list[Frame] = []
        debug: dict[str, dict[str, Any]] = {}
        for frame in frames:
            reasons: list[str] = []
            matched: dict[str, Any] = {}

            if video_codes:
                if frame.video.video_code not in video_codes:
                    reasons.append("video_code_mismatch")
                else:
                    matched["video_code"] = frame.video.video_code

            if start is not None and frame.frame_seconds < float(start):
                reasons.append("time_range_before_start")
            if end is not None and frame.frame_seconds > float(end):
                reasons.append("time_range_after_end")
            if start is not None or end is not None:
                matched["frame_seconds"] = round(float(frame.frame_seconds or 0.0), 3)

            if objects:
                frame_objects = self._frame_objects(frame)
                matched_objects = sorted(objects.intersection(frame_objects))
                if not matched_objects:
                    reasons.append("objects_mismatch")
                else:
                    matched["objects"] = matched_objects

            if scene:
                has_scene = scene in self._frame_text(frame).lower()
                if not has_scene:
                    reasons.append("scene_mismatch")
                else:
                    matched["scene"] = scene

            if reasons:
                continue

            selected.append(frame)
            debug[frame.keyframe_id] = {
                "applied_filters": filters,
                "matched": matched,
                "debug_enabled": options.debug_filters,
            }
        return selected, debug

    def _rank_map(self, scores: dict[str, float], candidate_ids: set[str]) -> dict[str, int]:
        ranked = sorted(
            ((frame_id, score) for frame_id, score in scores.items() if frame_id in candidate_ids and score > 0),
            key=lambda item: item[1],
            reverse=True,
        )
        return {frame_id: rank for rank, (frame_id, _) in enumerate(ranked, start=1)}

    def _frame_objects(self, frame: Frame) -> set[str]:
        values: set[str] = set()
        for annotation in frame.annotations:
            if isinstance(annotation.detected_objects, list):
                values.update(str(item).strip().lower() for item in annotation.detected_objects if str(item).strip())
            if isinstance(annotation.object_counts, dict):
                values.update(str(key).strip().lower() for key in annotation.object_counts if str(key).strip())
        return values

    def _dataset_video_ids(self, dataset: Dataset) -> set[str]:
        return {
            video_id
            for (video_id,) in self.db.query(Video.video_id).filter(Video.dataset_id == dataset.id).all()
        }

    def _resolve_keyframe_id(self, item_id: str, metadata: dict[str, Any]) -> str | None:
        if metadata.get("canonical_keyframe_id"):
            return str(metadata["canonical_keyframe_id"])
        if metadata.get("mapped_keyframe_id"):
            return str(metadata["mapped_keyframe_id"])
        for candidate in (metadata.get("keyframe_id"), metadata.get("frame_id"), item_id):
            value = str(candidate or "").strip()
            if value and FRAME_VIDEO_ID_RE.match(value):
                return value
        for candidate in (
            metadata.get("original_keyframe_id"),
            item_id,
            metadata.get("keyframe_id"),
            metadata.get("frame_id"),
        ):
            map_info = self._map_keyframe_info(str(candidate or ""), metadata)
            if map_info:
                return str(map_info["resolved_keyframe_id"])

        for candidate in (metadata.get("keyframe_id"), metadata.get("frame_id"), item_id):
            value = str(candidate or "").strip()
            if value:
                return value
        return None

    def _resolve_video_id(self, frame_id: str, metadata: dict[str, Any]) -> str:
        if metadata.get("video_id"):
            return str(metadata["video_id"])
        match = FRAME_VIDEO_ID_RE.match(frame_id or "")
        if match:
            return match.group("video_id")
        match = VECTOR_KEYFRAME_ID_RE.match(frame_id or "")
        if match:
            return match.group("video_id")
        return ""

    def _semantic_source_hit(
        self,
        item_id: str,
        metadata: dict[str, Any],
        collection: str,
        model_key: str | None,
        score: float,
        resolved_frame_id: str,
    ) -> dict[str, Any]:
        raw_keyframe_id = str(metadata.get("original_keyframe_id") or item_id or metadata.get("keyframe_id") or "")
        map_info = None
        for candidate in (raw_keyframe_id, item_id, metadata.get("keyframe_id"), metadata.get("frame_id")):
            candidate_map_info = self._map_keyframe_info(str(candidate or ""), metadata)
            if not candidate_map_info:
                continue
            if map_info is None:
                map_info = candidate_map_info
            if candidate_map_info.get("resolved_keyframe_id") == resolved_frame_id:
                map_info = candidate_map_info
                break
        map_matches_resolved = bool(map_info and map_info.get("resolved_keyframe_id") == resolved_frame_id)
        return {
            "collection": collection,
            "model_key": model_key,
            "model_name": metadata.get("model_name") or None,
            "model_version": metadata.get("model_version") or None,
            "embedding_dim": metadata.get("embedding_dim") or None,
            "score": round(float(score), 6),
            "source_keyframe_id": raw_keyframe_id or None,
            "milvus_keyframe_id": metadata.get("keyframe_id") or None,
            "resolved_keyframe_id": resolved_frame_id,
            "map_matches_resolved": map_matches_resolved,
            "map_keyframe": map_info,
        }

    def _map_keyframe_info(self, candidate: str, metadata: dict[str, Any]) -> dict[str, Any] | None:
        candidate = str(candidate or "").strip()
        video_id = ""
        map_n: int | None = None
        match = VECTOR_KEYFRAME_ID_RE.match(candidate)
        if match:
            video_id = match.group("video_id")
            map_n = self._int_or_none(match.group("n"))
        elif metadata.get("video_id"):
            video_id = str(metadata["video_id"])
            map_n = self._first_int(
                metadata.get("keyframe_number"),
                metadata.get("map_n"),
                metadata.get("n"),
            )
            if map_n is None and metadata.get("embedding_index_0") is not None:
                index = self._int_or_none(metadata.get("embedding_index_0"))
                map_n = index + 1 if index is not None else None

        if not video_id or map_n is None:
            return None

        rows = self._load_map_keyframes(video_id)
        if not rows:
            return None
        row = rows.get(map_n)
        if not row:
            return None
        frame_idx = self._int_or_none(row.get("frame_idx"))
        if frame_idx is None:
            return None
        return {
            "source": "data/map-keyframes",
            "map_path": f"data/map-keyframes/{video_id}.csv",
            "video_id": video_id,
            "n": map_n,
            "pts_time": self._float_or_none(row.get("pts_time")),
            "fps": self._float_or_none(row.get("fps")),
            "frame_idx": frame_idx,
            "source_keyframe_id": candidate or f"{video_id}_{map_n:03d}",
            "resolved_keyframe_id": f"{video_id}_F{frame_idx:06d}",
        }

    def _load_map_keyframes(self, video_id: str) -> dict[int, dict[str, Any]] | None:
        if video_id in self._map_keyframes_cache:
            return self._map_keyframes_cache[video_id]
        candidate_paths = (
            self.settings.data_root / "map-keyframes" / f"{video_id}.csv",
            REPO_ROOT / "data" / "map-keyframes" / f"{video_id}.csv",
        )
        path = next((candidate for candidate in candidate_paths if candidate.is_file()), None)
        if path is None:
            self._map_keyframes_cache[video_id] = None
            return None
        rows: dict[int, dict[str, Any]] = {}
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                map_n = self._int_or_none(row.get("n"))
                if map_n is not None:
                    rows[map_n] = dict(row)
        self._map_keyframes_cache[video_id] = rows
        return rows

    def _first_int(self, *values: Any) -> int | None:
        for value in values:
            parsed = self._int_or_none(value)
            if parsed is not None:
                return parsed
        return None

    def _int_or_none(self, value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(float(str(value)))
        except (TypeError, ValueError):
            return None

    def _float_or_none(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(str(value))
        except (TypeError, ValueError):
            return None

    def _metadata_score(self, variants: list[str], frame: Frame) -> float:
        annotation_text = self._frame_text(frame)
        scores = []
        for variant in variants:
            score = cosine_like_overlap(variant, annotation_text)
            if frame.video.video_code.lower() in variant.lower():
                score += 0.25
            scores.append(min(1.0, score))
        return max(scores) if scores else 0.0

    def _frame_text(self, frame: Frame) -> str:
        chunks: list[str] = []
        for annotation in frame.annotations:
            if annotation.text_value:
                chunks.append(annotation.text_value)
            if annotation.caption:
                chunks.append(annotation.caption)
            if isinstance(annotation.ocr_texts, list):
                chunks.extend(str(item) for item in annotation.ocr_texts)
            if isinstance(annotation.detected_objects, list):
                chunks.extend(str(item) for item in annotation.detected_objects)
        return " ".join(chunks).strip()

    def _answer_hint(self, frame: Frame) -> str | None:
        for annotation in frame.annotations:
            hint = (annotation.json_value or {}).get("answer_hint")
            if hint:
                return str(hint)
        return None

    def _postprocess_qa_answer(self, answer: str | None) -> str | None:
        if answer is None:
            return None
        normalized = " ".join(str(answer).split())
        if not normalized:
            return None
        if len(normalized) <= 100:
            return normalized
        return normalized[:100].rstrip()

    def _browser_image_url(self, frame: Frame | None) -> str | None:
        if frame is None:
            return None
        candidates = [
            frame.thumbnail_uri,
            frame.image_url,
            gcs_public_url(
                frame.image_uri or "",
                default_bucket=self.settings.gcs_bucket,
                public_base_url=self.settings.gcs_public_url,
            ),
            gcs_public_url(
                frame.image_storage_key or "",
                default_bucket=self.settings.gcs_bucket,
                public_base_url=self.settings.gcs_public_url,
            ),
        ]
        return next((url for url in candidates if url), None)

    def _browser_video_url(self, video: Video | None) -> str | None:
        if video is None:
            return None
        return f"/api/media/videos/{video.video_id}/preview"

    def _result_to_item(self, result: RetrievalResult) -> ResultItem:
        frame = result.frame
        return ResultItem(
            id=result.id,
            rank=result.rank,
            video_id=result.video_id,
            video_code=result.video.video_code,
            frame_id=result.frame_id,
            frame_idx=frame.frame_idx if frame else None,
            timestamp_ms=frame.timestamp_ms if frame else None,
            answer=result.answer,
            score=round(result.score, 6),
            score_breakdown=result.score_breakdown or {},
            sequence_frames=result.sequence_frames or [],
            thumbnail_url=f"/api/media/frames/{result.frame_id}/thumbnail" if result.frame_id else None,
            image_url=self._browser_image_url(frame),
            image_uri=frame.image_uri if frame else None,
            image_storage_key=frame.image_storage_key if frame else None,
            video_url=self._browser_video_url(result.video),
            video_uri=result.video.uri if result.video else None,
        )

from __future__ import annotations

import csv
import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

import yaml
from sqlalchemy.orm import Session, joinedload, selectinload

from app.adapters.text_search.base import TextSearchClient
from app.adapters.vector_db.base import VectorSearchClient
from app.core.config import REPO_ROOT, get_settings
from app.db.models import Dataset, Frame, QueryRun, RetrievalResult, Video, new_id
from app.modules.media.urls import gcs_public_url
from app.modules.models.service import ModelRegistryService
from app.modules.retrieval.query_planning import AgentQueryPlanner
from app.modules.retrieval.schemas import ResultItem, SearchOptions, SearchRequest, SearchResponse
from app.modules.retrieval.temporal_query import TemporalEventParse, parse_temporal_events
from app.modules.temporal.ats import Candidate, adaptive_temporal_search


logger = logging.getLogger(__name__)


TOKEN_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)
FRAME_VIDEO_ID_RE = re.compile(r"^(?P<video_id>.+)_F\d+$")
VECTOR_KEYFRAME_ID_RE = re.compile(r"^(?P<video_id>L\d{2}_V\d{3})_(?P<n>\d+)$")


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

    def search(self, request: SearchRequest) -> SearchResponse:
        if not request.query_text.strip():
            raise ValueError("query_text must not be empty")

        started_at = perf_counter()
        dataset = self._resolve_dataset(request.dataset_id)
        normalized = self._normalize_query(request)
        run_id = new_id()
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

        try:
            if request.query_type == "TRAKE":
                results = self._search_trake(run, dataset, request, normalized)
            else:
                results = self._search_frame_level(run, dataset, request, normalized)
            run.status = "DONE"
            latency_ms = int((perf_counter() - started_at) * 1000)
            normalized = {**normalized, "latency_ms": latency_ms}
            # Re-assign JSON fields so SQLAlchemy persists updated values reliably.
            run.normalized_query = normalized
            run.options = {**(run.options or {}), "latency_ms": latency_ms}
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

    def _resolve_dataset(self, dataset_id: str | None) -> Dataset:
        if dataset_id:
            dataset = self.db.query(Dataset).filter(Dataset.id == dataset_id).first()
        else:
            dataset = self.db.query(Dataset).filter(Dataset.status == "READY").first()
        if not dataset:
            raise ValueError("No READY dataset found. Create or ingest a dataset first.")
        return dataset

    def _normalize_query(self, request: SearchRequest) -> dict[str, Any]:
        profile = self.profiles.get(request.profile, self.profiles.get("competition_default", {}))
        expansion_profile = profile.get("query_expansion", {})
        max_variants = int(expansion_profile.get("max_variants", 5))
        expansion_default_enabled = bool(expansion_profile.get("enabled_default", True))
        query_text = request.query_text.strip()
        variants = [query_text]
        agent_plan = None
        if request.options.use_agent_query_planning:
            agent_plan = self.query_planner.plan(
                query=query_text,
                query_type=request.query_type,
                max_variants=max_variants,
            )
        if request.options.use_query_expansion and expansion_default_enabled:
            if agent_plan is not None and agent_plan.variants:
                variants = self._dedupe_query_variants(
                    [*agent_plan.variants, *self._expand_query_variants(query_text, max_variants=max_variants)],
                    max_variants=max_variants,
                )
            else:
                variants = self._expand_query_variants(query_text, max_variants=max_variants)
        temporal_parse = self._parse_temporal_events(query_text)
        temporal_events, temporal_source = self._resolve_temporal_events(request, agent_plan, temporal_parse)
        normalized = {
            "language": agent_plan.language if agent_plan else "auto",
            "variants": variants,
            "tokens": normalize_tokens(" ".join(variants)),
            "temporal_events": temporal_events,
            "temporal_event_count": len(temporal_events),
            "temporal_event_source": temporal_source,
            "raw_temporal_events": temporal_parse.events,
            "profile": request.profile,
            "filters": self._normalized_filter_options(request.options),
        }
        if agent_plan is not None:
            normalized["agent_query_plan"] = agent_plan.as_normalized_query()
        return normalized

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

        if agent_events:
            return agent_events, "agent"
        return parsed_events or [request.query_text.strip()], temporal_parse.source

    def _split_temporal_events(self, query: str) -> list[str]:
        return self._parse_temporal_events(query).events or [query]
        separators = [r"\bthen\b", r"\bafter that\b", r"\bsau đó\b", r"\btiếp theo\b", r";", r"\(e\d+\)\s*:"]
        pattern = "|".join(separators)
        parts = [part.strip(" .:-") for part in re.split(pattern, query, flags=re.IGNORECASE) if part.strip(" .:-")]
        return parts[:8] if len(parts) > 1 else [query]

    def _search_frame_level(
        self,
        run: QueryRun,
        dataset: Dataset,
        request: SearchRequest,
        normalized: dict[str, Any],
    ) -> list[ResultItem]:
        candidates = self._rank_frames(
            dataset=dataset,
            variants=normalized["variants"],
            query_text=request.query_text,
            profile_name=request.profile,
            options=request.options,
            top_k=request.top_k,
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

    def _search_trake(
        self,
        run: QueryRun,
        dataset: Dataset,
        request: SearchRequest,
        normalized: dict[str, Any],
    ) -> list[ResultItem]:
        events = self._dedupe_query_variants(normalized["temporal_events"] or [request.query_text], max_variants=8)
        candidate_sets: list[list[Candidate]] = []
        event_summaries: list[dict[str, Any]] = []
        per_event_top_k = max(80, min(500, request.top_k * 20))
        for event_index, event_query in enumerate(events, start=1):
            ranked = self._rank_frames(
                dataset=dataset,
                variants=[event_query],
                query_text=event_query,
                profile_name=request.profile,
                options=request.options,
                top_k=per_event_top_k,
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
                )
                for candidate in ranked
            ]
            candidate_sets.append(event_candidates)
            event_summaries.append(
                {
                    "event_index": event_index,
                    "query": event_query,
                    "candidate_count": len(event_candidates),
                }
            )
        delta_frames = max(1, int(request.options.delta_t_max_ms / 1000 * 30))
        min_match = request.options.min_match or len(events)
        sequences = adaptive_temporal_search(
            candidate_sets=candidate_sets,
            weights=[1.0 for _ in events],
            delta_frame_max=delta_frames,
            min_match=min_match,
            limit=request.top_k,
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
            result = RetrievalResult(
                id=new_id(),
                query_run_id=run.id,
                rank=rank,
                video_id=representative.video_id,
                frame_id=representative.frame_id,
                score=sequence.score,
                score_breakdown={
                    "temporal_score": round(sequence.score, 4),
                    "matched_events": len(sequence.candidates),
                    "expected_events": len(events),
                    "min_match": min_match,
                    "requires_full_sequence": request.options.min_match is None,
                    "event_queries": event_summaries,
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

    def _rank_frames(
        self,
        dataset: Dataset,
        variants: list[str],
        query_text: str,
        profile_name: str,
        options: SearchOptions,
        top_k: int,
    ) -> list[FrameScore]:
        profile = self.profiles.get(profile_name, self.profiles.get("competition_default", {}))
        semantic_weight = float(profile.get("semantic_weight", 0.6))
        text_weight = float(profile.get("metadata_weight", profile.get("text_weight", 0.25)))
        quality_weight = float(profile.get("quality_weight", profile.get("user_boost_weight", 0.05)))
        rrf_config = profile.get("rrf", {})
        rrf_enabled = bool(rrf_config.get("enabled", False))
        rrf_k = max(1.0, float(rrf_config.get("k", 60)))
        rrf_blend = min(1.0, max(0.0, float(rrf_config.get("blend", 0.35))))
        rrf_semantic_weight = float(rrf_config.get("semantic_weight", semantic_weight))
        rrf_text_weight = float(rrf_config.get("text_weight", text_weight))
        ann_top_k = int(profile.get("milvus", {}).get("top_k_per_model", max(200, top_k * 4)))

        dataset_video_ids = self._dataset_video_ids(dataset)
        semantic_scores, semantic_backend_error = self._semantic_scores(variants, ann_top_k, dataset_video_ids, profile)
        if options.use_metadata:
            text_scores, text_backend_error = self._text_scores(variants, ann_top_k, dataset_video_ids, profile)
        else:
            text_scores, text_backend_error = {}, False

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
            return self._fallback_rank_frames(
                dataset=dataset,
                variants=variants,
                query_text=query_text,
                profile=profile,
                semantic_weight=semantic_weight,
                text_weight=text_weight,
                quality_weight=quality_weight,
                rrf_enabled=rrf_enabled,
                rrf_k=rrf_k,
                rrf_blend=rrf_blend,
                rrf_semantic_weight=rrf_semantic_weight,
                rrf_text_weight=rrf_text_weight,
                options=options,
            )

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
        return self._apply_reranking(query_text=query_text, scored=scored, profile=profile, options=options)

    def _semantic_scores(
        self,
        variants: list[str],
        top_k: int,
        dataset_video_ids: set[str],
        profile: dict[str, Any],
    ) -> tuple[dict[str, float], bool]:
        self._semantic_hit_sources = {}
        if self.vector_client is None:
            return {}, True
        backend_error = False
        collections = self._semantic_collections(profile)
        visual_rrf_config = profile.get("visual_rrf", {})
        visual_rrf_enabled = len(collections) > 1 and bool(visual_rrf_config.get("enabled", True))
        visual_rrf_k = max(
            1.0,
            float(visual_rrf_config.get("k", profile.get("rrf", {}).get("k", 60))),
        )
        query_vectors: dict[tuple[str, str], list[float]] = {}
        per_model_scores: dict[str, dict[str, float]] = {}
        per_model_weights: dict[str, float] = {}
        per_frame_sources: dict[str, dict[str, dict[str, Any]]] = {}
        for variant in variants:
            for target in collections:
                model_id = target.model_key or target.collection
                per_model_weights.setdefault(model_id, target.weight)
                vector_key = (target.model_key or "__default__", variant)
                if vector_key not in query_vectors:
                    try:
                        query_vectors[vector_key] = self.model_registry.embedder_for(target.model_key).embed_text(variant)
                    except Exception:
                        # Keep retrieval available even when one embedder runtime is misconfigured.
                        backend_error = True
                        query_vectors[vector_key] = []
                query_vector = query_vectors[vector_key]
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

    def _semantic_collections(self, profile: dict[str, Any]) -> list[SemanticCollection]:
        collections: list[SemanticCollection] = []

        visual_models = profile.get("visual_models")
        if isinstance(visual_models, dict):
            for config in visual_models.values():
                if not isinstance(config, dict):
                    continue
                if config.get("enabled") is False:
                    continue
                collection = str(config.get("collection") or "").strip()
                if collection:
                    model_key = str(config.get("model_key") or config.get("embedder") or "").strip() or None
                    collections.append(
                        SemanticCollection(
                            collection=collection,
                            weight=float(config.get("weight", 1.0)),
                            model_key=model_key,
                        )
                    )

        milvus_profile = profile.get("milvus") if isinstance(profile.get("milvus"), dict) else {}
        collection = str(milvus_profile.get("collection") or "").strip()
        if collection:
            model_key = str(milvus_profile.get("model_key") or milvus_profile.get("embedder") or "").strip() or None
            collections.append(
                SemanticCollection(
                    collection=collection,
                    weight=float(milvus_profile.get("weight", 1.0)),
                    model_key=model_key,
                )
            )

        if not collections:
            embedder_entry = self.model_registry.first_enabled("embedders")
            if embedder_entry and isinstance(embedder_entry[1], dict):
                provider = str(embedder_entry[1].get("provider") or "").lower()
                collection = str(embedder_entry[1].get("collection") or "").strip()
                if provider in {"openai_compatible", "siglip2", "transformers_siglip2", "huggingface_siglip2"} and collection:
                    collections.append(SemanticCollection(collection=collection, model_key=embedder_entry[0]))

        if not collections:
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

    def _text_scores(
        self,
        variants: list[str],
        top_k: int,
        dataset_video_ids: set[str],
        profile: dict[str, Any],
    ) -> tuple[dict[str, float], bool]:
        self._text_hit_sources = {}
        if self.text_client is None:
            return {}, True
        metadata_profile = profile.get("metadata", {})
        boosts = {
            "asr_text": float(metadata_profile.get("asr_boost", 2.5)),
            "normalized_asr_text": float(metadata_profile.get("asr_boost", 2.5)),
            "text_value": float(metadata_profile.get("text_boost", metadata_profile.get("asr_boost", 2.5))),
            "ocr_texts": float(metadata_profile.get("ocr_boost", 3.0)),
            "caption": float(metadata_profile.get("caption_boost", 1.5)),
            "detected_objects": float(metadata_profile.get("object_boost", 1.0)),
        }
        scores: dict[str, float] = {}
        backend_error = False
        for variant in variants:
            try:
                hits = self.text_client.search(
                    "keyframe_annotations",
                    query=variant,
                    top_k=top_k,
                    boosts=boosts,
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
                    )
        return scores, backend_error

    def _text_source_hit(
        self,
        metadata: dict[str, Any],
        score: float,
        resolved_frame_id: str,
        variant: str,
    ) -> dict[str, Any]:
        return {
            "source_type": metadata.get("source_type") or metadata.get("kind") or "metadata",
            "score": round(float(score), 6),
            "variant": variant,
            "keyframe_id": metadata.get("keyframe_id") or metadata.get("frame_id") or resolved_frame_id,
            "video_id": metadata.get("video_id"),
            "segment_id": metadata.get("segment_id"),
            "start_seconds": metadata.get("start_seconds"),
            "end_seconds": metadata.get("end_seconds"),
            "field": "asr_text" if metadata.get("source_type") == "asr" else "metadata",
            "snippet": self._text_hit_snippet(metadata),
        }

    def _text_hit_snippet(self, metadata: dict[str, Any]) -> str:
        for key in ("asr_text", "text_value", "caption", "normalized_asr_text", "raw_asr_text"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value[:240]
        return ""

    def _fallback_rank_frames(
        self,
        dataset: Dataset,
        variants: list[str],
        query_text: str,
        profile: dict[str, Any],
        semantic_weight: float,
        text_weight: float,
        quality_weight: float,
        rrf_enabled: bool,
        rrf_k: float,
        rrf_blend: float,
        rrf_semantic_weight: float,
        rrf_text_weight: float,
        options: SearchOptions,
    ) -> list[FrameScore]:
        frames = (
            self.db.query(Frame)
            .options(joinedload(Frame.video), selectinload(Frame.annotations))
            .join(Frame.video)
            .filter(Video.dataset_id == dataset.id)
            .all()
        )
        filtered_frames, filter_debug = self._apply_filters(frames, options)
        if not filtered_frames:
            return []

        overlap_scores = {
            frame.keyframe_id: max(cosine_like_overlap(variant, self._frame_text(frame)) for variant in variants)
            for frame in filtered_frames
        }
        overlap_rank_map = self._rank_map(overlap_scores, set(overlap_scores))

        intermediate: list[dict[str, Any]] = []
        max_rrf_raw = 0.0
        for frame in filtered_frames:
            overlap_score = overlap_scores.get(frame.keyframe_id, 0.0)
            quality_score = max(0.0, min(1.0, float(frame.quality_score or 0.0)))
            weighted_score = (
                semantic_weight * overlap_score
                + text_weight * overlap_score
                + quality_weight * quality_score
            )
            rrf_raw = 0.0
            if rrf_enabled:
                overlap_rank = overlap_rank_map.get(frame.keyframe_id)
                overlap_rrf = (1.0 / (rrf_k + overlap_rank)) if overlap_rank else 0.0
                rrf_raw = (rrf_semantic_weight + rrf_text_weight) * overlap_rrf
                max_rrf_raw = max(max_rrf_raw, rrf_raw)
            intermediate.append(
                {
                    "frame": frame,
                    "overlap_score": overlap_score,
                    "quality_score": quality_score,
                    "weighted_score": weighted_score,
                    "rrf_raw": rrf_raw,
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
                    semantic_score=item["overlap_score"],
                    text_score=item["overlap_score"],
                    quality_score=item["quality_score"],
                    weighted_score=item["weighted_score"],
                    rrf_score=rrf_score,
                    final_score=final_score,
                    filter_debug=filter_debug.get(frame.keyframe_id, {}),
                    source_hit={},
                    text_hit={},
                )
            )
        scored.sort(key=lambda item: (item.final_score, item.frame.frame_idx), reverse=True)
        return self._apply_reranking(query_text=query_text, scored=scored, profile=profile, options=options)

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

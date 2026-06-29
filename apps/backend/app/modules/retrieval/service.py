from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.adapters.text_search.base import TextSearchClient
from app.adapters.vector_db.base import VectorSearchClient
from app.core.config import get_settings
from app.db.models import Dataset, Frame, QueryRun, RetrievalResult, Video
from app.modules.models.service import ModelRegistryService
from app.modules.retrieval.schemas import ResultItem, SearchRequest, SearchResponse
from app.modules.temporal.ats import Candidate, adaptive_temporal_search


TOKEN_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)
FRAME_VIDEO_ID_RE = re.compile(r"^(?P<video_id>.+)_F\d+$")


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
    final_score: float


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

    def search(self, request: SearchRequest) -> SearchResponse:
        if not request.query_text.strip():
            raise ValueError("query_text must not be empty")

        started_at = perf_counter()
        dataset = self._resolve_dataset(request.dataset_id)
        normalized = self._normalize_query(request)
        run = QueryRun(
            dataset_id=dataset.id,
            query_name=request.query_name,
            query_type=request.query_type,
            query_text=request.query_text,
            normalized_query=normalized,
            options=request.model_dump(mode="json"),
            status="RUNNING",
        )
        self.db.add(run)
        self.db.flush()

        try:
            if request.query_type == "TRAKE":
                results = self._search_trake(run, dataset, request, normalized)
            else:
                results = self._search_frame_level(run, dataset, request, normalized)
            run.status = "DONE"
            latency_ms = int((perf_counter() - started_at) * 1000)
            run.options = {**(run.options or {}), "latency_ms": latency_ms}
            normalized["latency_ms"] = latency_ms
            self.db.commit()
        except Exception:
            run.status = "FAILED"
            self.db.commit()
            raise

        return SearchResponse(
            query_run_id=run.id,
            query_type=request.query_type,
            query_name=request.query_name,
            normalized_query=normalized,
            results=results,
        )

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
        variants = [request.query_text]
        if request.options.use_query_expansion and expansion_default_enabled:
            variants = self.model_registry.query_expander.expand(request.query_text, max_variants=max_variants)
        temporal_events = request.options.temporal_events or self._split_temporal_events(request.query_text)
        return {
            "language": "auto",
            "variants": variants,
            "tokens": normalize_tokens(" ".join(variants)),
            "temporal_events": temporal_events,
            "profile": request.profile,
        }

    def _split_temporal_events(self, query: str) -> list[str]:
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
        candidates = self._rank_frames(dataset, normalized["variants"], request.profile, request.top_k)
        items: list[ResultItem] = []
        for rank, candidate in enumerate(candidates[: request.top_k], start=1):
            frame = candidate.frame
            answer = None
            if request.query_type == "QA":
                evidence = self._frame_text(frame)
                answer_hint = self._answer_hint(frame)
                answer = self.model_registry.visual_qa.answer(request.query_text, evidence, answer_hint)
            result = RetrievalResult(
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
                    "final_score": round(candidate.final_score, 6),
                },
                sequence_frames=[],
            )
            self.db.add(result)
            self.db.flush()
            items.append(self._result_to_item(result))
        return items

    def _search_trake(
        self,
        run: QueryRun,
        dataset: Dataset,
        request: SearchRequest,
        normalized: dict[str, Any],
    ) -> list[ResultItem]:
        events = normalized["temporal_events"] or [request.query_text]
        candidate_sets: list[list[Candidate]] = []
        for event_query in events:
            ranked = self._rank_frames(dataset, [event_query], request.profile, top_k=80)
            candidate_sets.append(
                [
                    Candidate(
                        frame_id=candidate.frame.id,
                        video_id=candidate.frame.video_id,
                        video_code=candidate.frame.video.video_code,
                        frame_idx=candidate.frame.frame_idx,
                        score=candidate.final_score,
                        text=self._frame_text(candidate.frame),
                    )
                    for candidate in ranked
                ]
            )
        delta_frames = max(1, int(request.options.delta_t_max_ms / 1000 * 30))
        min_match = request.options.min_match or max(1, math.ceil(len(events) * 0.67))
        sequences = adaptive_temporal_search(
            candidate_sets=candidate_sets,
            weights=[1.0 for _ in events],
            delta_frame_max=delta_frames,
            min_match=min_match,
            limit=request.top_k,
        )
        items: list[ResultItem] = []
        for rank, sequence in enumerate(sequences, start=1):
            representative = sequence.candidates[len(sequence.candidates) // 2]
            sequence_frames = [
                {
                    "frame_id": candidate.frame_id,
                    "frame_idx": candidate.frame_idx,
                    "video_code": candidate.video_code,
                    "score": round(candidate.score, 4),
                }
                for candidate in sequence.candidates
            ]
            result = RetrievalResult(
                query_run_id=run.id,
                rank=rank,
                video_id=representative.video_id,
                frame_id=representative.frame_id,
                score=sequence.score,
                score_breakdown={
                    "temporal_score": round(sequence.score, 4),
                    "matched_events": len(sequence.candidates),
                    "expected_events": len(events),
                },
                sequence_frames=sequence_frames,
            )
            self.db.add(result)
            self.db.flush()
            items.append(self._result_to_item(result))
        return items

    def _rank_frames(
        self,
        dataset: Dataset,
        variants: list[str],
        profile_name: str,
        top_k: int,
    ) -> list[FrameScore]:
        profile = self.profiles.get(profile_name, self.profiles.get("competition_default", {}))
        semantic_weight = float(profile.get("semantic_weight", 0.6))
        text_weight = float(profile.get("metadata_weight", profile.get("text_weight", 0.25)))
        quality_weight = float(profile.get("quality_weight", profile.get("user_boost_weight", 0.05)))
        ann_top_k = int(profile.get("milvus", {}).get("top_k_per_model", max(200, top_k * 4)))

        dataset_video_ids = self._dataset_video_ids(dataset)
        semantic_scores = self._semantic_scores(variants, ann_top_k, dataset_video_ids)
        text_scores = self._text_scores(variants, ann_top_k, dataset_video_ids, profile)

        candidate_ids = set(semantic_scores).union(text_scores)
        if not candidate_ids:
            return self._fallback_rank_frames(
                dataset=dataset,
                variants=variants,
                semantic_weight=semantic_weight,
                text_weight=text_weight,
                quality_weight=quality_weight,
            )

        frames = (
            self.db.query(Frame)
            .join(Frame.video)
            .filter(Video.dataset_id == dataset.id, Frame.keyframe_id.in_(candidate_ids))
            .all()
        )
        if not frames:
            return []

        semantic_max = max(semantic_scores.values(), default=1.0) or 1.0
        text_max = max(text_scores.values(), default=1.0) or 1.0
        scored: list[FrameScore] = []
        for frame in frames:
            semantic_score = (semantic_scores.get(frame.keyframe_id, 0.0) / semantic_max) if semantic_max > 0 else 0.0
            text_score = (text_scores.get(frame.keyframe_id, 0.0) / text_max) if text_max > 0 else 0.0
            quality_score = max(0.0, min(1.0, float(frame.quality_score or 0.0)))
            final_score = (
                semantic_weight * semantic_score
                + text_weight * text_score
                + quality_weight * quality_score
            )
            scored.append(
                FrameScore(
                    frame=frame,
                    semantic_score=semantic_score,
                    text_score=text_score,
                    quality_score=quality_score,
                    final_score=final_score,
                )
            )
        scored.sort(key=lambda item: (item.final_score, item.frame.frame_idx), reverse=True)
        return scored

    def _semantic_scores(self, variants: list[str], top_k: int, dataset_video_ids: set[str]) -> dict[str, float]:
        if self.vector_client is None:
            return {}
        scores: dict[str, float] = {}
        for variant in variants:
            query_vector = self.model_registry.embedder.embed_text(variant)
            if not query_vector:
                continue
            try:
                hits = self.vector_client.search("keyframe_embeddings", query_vector, top_k=top_k)
            except Exception:
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
        return scores

    def _text_scores(
        self,
        variants: list[str],
        top_k: int,
        dataset_video_ids: set[str],
        profile: dict[str, Any],
    ) -> dict[str, float]:
        if self.text_client is None:
            return {}
        metadata_profile = profile.get("metadata", {})
        boosts = {
            "ocr_texts": float(metadata_profile.get("ocr_boost", 3.0)),
            "caption": float(metadata_profile.get("caption_boost", 1.5)),
            "detected_objects": float(metadata_profile.get("object_boost", 1.0)),
        }
        scores: dict[str, float] = {}
        for variant in variants:
            try:
                hits = self.text_client.search(
                    "keyframe_annotations",
                    query=variant,
                    top_k=top_k,
                    boosts=boosts,
                )
            except Exception:
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
        return scores

    def _fallback_rank_frames(
        self,
        dataset: Dataset,
        variants: list[str],
        semantic_weight: float,
        text_weight: float,
        quality_weight: float,
    ) -> list[FrameScore]:
        frames = (
            self.db.query(Frame)
            .join(Frame.video)
            .filter(Video.dataset_id == dataset.id)
            .all()
        )
        scored: list[FrameScore] = []
        for frame in frames:
            doc = self._frame_text(frame)
            overlap_score = max(cosine_like_overlap(variant, doc) for variant in variants)
            quality_score = max(0.0, min(1.0, float(frame.quality_score or 0.0)))
            final_score = (
                semantic_weight * overlap_score
                + text_weight * overlap_score
                + quality_weight * quality_score
            )
            scored.append(
                FrameScore(
                    frame=frame,
                    semantic_score=overlap_score,
                    text_score=overlap_score,
                    quality_score=quality_score,
                    final_score=final_score,
                )
            )
        scored.sort(key=lambda item: (item.final_score, item.frame.frame_idx), reverse=True)
        return scored

    def _dataset_video_ids(self, dataset: Dataset) -> set[str]:
        return {
            video_id
            for (video_id,) in self.db.query(Video.video_id).filter(Video.dataset_id == dataset.id).all()
        }

    def _resolve_keyframe_id(self, item_id: str, metadata: dict[str, Any]) -> str | None:
        if metadata.get("keyframe_id"):
            return str(metadata["keyframe_id"])
        if metadata.get("frame_id"):
            return str(metadata["frame_id"])
        if item_id:
            return str(item_id)
        return None

    def _resolve_video_id(self, frame_id: str, metadata: dict[str, Any]) -> str:
        if metadata.get("video_id"):
            return str(metadata["video_id"])
        match = FRAME_VIDEO_ID_RE.match(frame_id or "")
        if match:
            return match.group("video_id")
        return ""

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
        )

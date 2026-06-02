from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Dataset, Frame, QueryRun, RetrievalResult
from app.modules.models.service import model_registry_service
from app.modules.retrieval.schemas import ResultItem, SearchRequest, SearchResponse
from app.modules.temporal.ats import Candidate, adaptive_temporal_search


TOKEN_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)


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


class RetrievalService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.profiles = self._load_profiles()

    def search(self, request: SearchRequest) -> SearchResponse:
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
        max_variants = int(profile.get("query_expansion", {}).get("max_variants", 5))
        variants = [request.query_text]
        if request.options.use_query_expansion:
            variants = model_registry_service.query_expander.expand(request.query_text, max_variants=max_variants)
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
        candidates = self._rank_frames(dataset, normalized["variants"], request.profile)
        items: list[ResultItem] = []
        for rank, (frame, score, breakdown) in enumerate(candidates[: request.top_k], start=1):
            answer = None
            if request.query_type == "QA":
                evidence = self._frame_text(frame)
                answer_hint = self._answer_hint(frame)
                answer = model_registry_service.visual_qa.answer(request.query_text, evidence, answer_hint)
            result = RetrievalResult(
                query_run_id=run.id,
                rank=rank,
                video_id=frame.video_id,
                frame_id=frame.id,
                answer=answer,
                score=score,
                score_breakdown=breakdown,
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
            ranked = self._rank_frames(dataset, [event_query], request.profile)[:80]
            candidate_sets.append(
                [
                    Candidate(
                        frame_id=frame.id,
                        video_id=frame.video_id,
                        video_code=frame.video.video_code,
                        frame_idx=frame.frame_idx,
                        score=score,
                        text=self._frame_text(frame),
                    )
                    for frame, score, _ in ranked
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

    def _rank_frames(self, dataset: Dataset, variants: list[str], profile_name: str) -> list[tuple[Frame, float, dict[str, float]]]:
        profile = self.profiles.get(profile_name, self.profiles.get("competition_default", {}))
        semantic_weight = float(profile.get("semantic_weight", 0.6))
        metadata_weight = float(profile.get("metadata_weight", 0.25))
        frames = (
            self.db.query(Frame)
            .join(Frame.video)
            .filter_by(dataset_id=dataset.id)
            .all()
        )
        scored: list[tuple[Frame, float, dict[str, float]]] = []
        for frame in frames:
            doc = self._frame_text(frame)
            semantic_score = max(cosine_like_overlap(variant, doc) for variant in variants)
            metadata_score = self._metadata_score(variants, frame)
            quality = frame.quality_score or 1.0
            score = semantic_weight * semantic_score + metadata_weight * metadata_score + 0.03 * quality
            scored.append(
                (
                    frame,
                    score,
                    {
                        "semantic_score": round(semantic_score, 4),
                        "metadata_score": round(metadata_score, 4),
                        "quality_score": round(quality, 4),
                    },
                )
            )
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored

    def _metadata_score(self, variants: list[str], frame: Frame) -> float:
        annotation_text = " ".join(annotation.text_value or "" for annotation in frame.annotations)
        scores = []
        for variant in variants:
            score = cosine_like_overlap(variant, annotation_text)
            if frame.video.video_code.lower() in variant.lower():
                score += 0.25
            scores.append(min(1.0, score))
        return max(scores) if scores else 0.0

    def _frame_text(self, frame: Frame) -> str:
        return " ".join(annotation.text_value or "" for annotation in frame.annotations)

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

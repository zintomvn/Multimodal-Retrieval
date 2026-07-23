from __future__ import annotations

import math
from typing import Any

from app.adapters.model_runtime.base import TextReranker


class CrossEncoderTextReranker(TextReranker):
    """Optional sentence-transformers cross-encoder with heuristic fallback."""

    def __init__(
        self,
        model: str,
        device: str | None = None,
        max_length: int | None = None,
        batch_size: int | None = None,
        fallback_to_overlap: bool = True,
    ) -> None:
        self.model_name = model
        normalized_device = (device or "").strip().lower()
        self.device = None if normalized_device in {"", "auto"} else device.strip()
        self.max_length = max_length if max_length and max_length > 0 else None
        self.batch_size = batch_size if batch_size and batch_size > 0 else None
        self.fallback_to_overlap = fallback_to_overlap
        self._model: Any | None = None
        self._load_attempted = False
        self.backend = "heuristic"

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        model = self._load_model()
        if model is None:
            return [self._heuristic_score(query, passage) for passage in passages]
        try:
            pairs = [(query, passage) for passage in passages]
            kwargs: dict[str, Any] = {"convert_to_numpy": True}
            if self.batch_size is not None:
                kwargs["batch_size"] = self.batch_size
            scores = model.predict(pairs, **kwargs)
            if hasattr(scores, "tolist"):
                scores = scores.tolist()
            if isinstance(scores, (int, float)):
                return [float(scores)]
            return [float(value) for value in scores]
        except Exception:
            if not self.fallback_to_overlap:
                raise
            self.backend = "heuristic"
            return [self._heuristic_score(query, passage) for passage in passages]

    def _load_model(self) -> Any | None:
        if self._load_attempted:
            return self._model
        self._load_attempted = True
        try:
            from sentence_transformers import CrossEncoder

            kwargs: dict[str, Any] = {}
            if self.device:
                kwargs["device"] = self.device
            if self.max_length is not None:
                kwargs["max_length"] = self.max_length
            self._model = CrossEncoder(self.model_name, **kwargs)
            self.backend = "sentence_transformers"
        except Exception:
            self._model = None
            self.backend = "heuristic"
        return self._model

    def _heuristic_score(self, query: str, passage: str) -> float:
        q = self._token_counts(query)
        p = self._token_counts(passage)
        if not q or not p:
            return 0.0
        overlap = sum(min(q[token], p[token]) for token in q)
        q_norm = math.sqrt(sum(value * value for value in q.values())) or 1.0
        p_norm = math.sqrt(sum(value * value for value in p.values())) or 1.0
        return overlap / (q_norm * p_norm)

    def _token_counts(self, text: str) -> dict[str, int]:
        tokens: dict[str, int] = {}
        for raw in text.lower().split():
            token = raw.strip(".,;:!?()[]{}\"'`")
            if len(token) <= 1:
                continue
            tokens[token] = tokens.get(token, 0) + 1
        return tokens

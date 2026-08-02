from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .extractors.runtime import ModelRuntime


@dataclass(frozen=True)
class TextEmbeddingRecord:
    """One text embedding source record."""

    id: str
    text: str
    metadata: dict[str, Any]


class VietnameseTextEmbedder:
    """SentenceTransformer embedder for Vietnamese text."""

    def __init__(self, runtime: ModelRuntime, config: dict[str, Any]) -> None:
        self.runtime = runtime
        self.config = config
        self.model = None

    def warmup(self) -> None:
        """Download and initialize the text embedding model."""
        self._ensure_model()

    def encode(self, texts: list[str]) -> np.ndarray:
        """Encode text into normalized embeddings."""
        self._ensure_model()
        if not texts:
            return np.empty((0, 0), dtype="float32")
        embeddings = self.model.encode(
            texts,
            batch_size=int(self.config.get("batch_size", 32)),
            convert_to_numpy=True,
            normalize_embeddings=bool(self.config.get("normalize", True)),
            show_progress_bar=bool(self.config.get("show_progress_bar", False)),
        )
        return np.asarray(embeddings, dtype="float32")

    def _ensure_model(self) -> None:
        if self.model is not None:
            return
        from sentence_transformers import SentenceTransformer

        model_name = str(self.config.get("model_name", "dangvantuan/vietnamese-embedding"))
        cache_dir = Path(self.runtime.cache_dir) / "sentence-transformers"
        cache_dir.mkdir(parents=True, exist_ok=True)
        device = self.runtime.device
        self.model = SentenceTransformer(model_name, device=device, cache_folder=str(cache_dir))

from __future__ import annotations

import math
from threading import Lock
from typing import Any

from app.adapters.model_runtime.base import TextImageEmbedder


class Siglip2TextEmbedder(TextImageEmbedder):
    """Local SigLIP2 text encoder for querying SigLIP2 image vectors."""

    def __init__(
        self,
        model_name: str,
        device: str | None = None,
        cache_dir: str | None = None,
        local_files_only: bool = True,
        expected_dim: int | None = None,
        l2_normalize: bool = True,
    ) -> None:
        self.model_name = model_name
        self.configured_device = device or "auto"
        self.cache_dir = cache_dir or None
        self.local_files_only = local_files_only
        self.expected_dim = expected_dim
        self.l2_normalize = l2_normalize
        self.processor: Any | None = None
        self.model: Any | None = None
        self.device = "cpu"
        self._lock = Lock()

    def embed_text(self, text: str) -> list[float]:
        if not text.strip():
            return []
        vectors = self.embed_texts([text])
        return vectors[0] if vectors else []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        values_to_embed = [text.strip() for text in texts]
        if not values_to_embed:
            return []
        self._ensure_loaded()

        import torch

        inputs = self.processor(text=values_to_embed, padding="max_length", truncation=True, return_tensors="pt")
        inputs = {
            key: item.to(self.device) if hasattr(item, "to") else item
            for key, item in inputs.items()
        }
        with torch.inference_mode():
            if hasattr(self.model, "get_text_features"):
                output = self.model.get_text_features(**inputs)
            else:
                output = self.model(**inputs)
        tensor = output if isinstance(output, torch.Tensor) else output.pooler_output
        matrix = tensor.detach().cpu().float().numpy()
        vectors: list[list[float]] = []
        for row in matrix:
            values = row.astype("float32").tolist()
            if self.expected_dim and len(values) != self.expected_dim:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {self.expected_dim}, got {len(values)} "
                    f"for model '{self.model_name}'."
                )
            vectors.append(self._normalize(values) if self.l2_normalize else values)
        return vectors

    def embed_image_uri(self, image_uri: str) -> list[float]:
        raise RuntimeError("SigLIP2 image embedding from URI is not implemented in the backend adapter.")

    def _ensure_loaded(self) -> None:
        if self.model is not None and self.processor is not None:
            return
        with self._lock:
            if self.model is not None and self.processor is not None:
                return

            import torch
            from transformers import AutoModel, AutoProcessor

            kwargs: dict[str, Any] = {"local_files_only": self.local_files_only}
            if self.cache_dir:
                kwargs["cache_dir"] = self.cache_dir

            self.processor = AutoProcessor.from_pretrained(self.model_name, **kwargs)
            self.model = AutoModel.from_pretrained(self.model_name, **kwargs)
            self.device = self._resolve_device(torch)
            self.model.to(self.device)
            self.model.eval()

    def _resolve_device(self, torch_module: Any) -> str:
        requested = str(self.configured_device or "auto").strip().lower()
        if requested in {"", "auto"}:
            return "cuda" if torch_module.cuda.is_available() else "cpu"
        return str(self.configured_device)

    def _normalize(self, vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(value * value for value in vector))
        if norm <= 0:
            return vector
        return [value / norm for value in vector]

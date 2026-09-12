from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    name: str
    task: str
    provider: str
    enabled: bool
    checkpoint_uri: str | None = None
    config: dict | None = None


class TextImageEmbedder(ABC):
    @abstractmethod
    def embed_text(self, text: str) -> list[float]:
        raise NotImplementedError

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a query batch; adapters may override this for one network/model call."""
        return [self.embed_text(text) for text in texts]

    @abstractmethod
    def embed_image_uri(self, image_uri: str) -> list[float]:
        raise NotImplementedError


class QueryExpander(ABC):
    @abstractmethod
    def expand(self, query: str, max_variants: int = 5) -> list[str]:
        raise NotImplementedError


class VisualQaModel(ABC):
    @abstractmethod
    def answer(self, question: str, evidence_text: str, answer_hint: str | None = None) -> str:
        raise NotImplementedError


class TextReranker(ABC):
    @abstractmethod
    def rerank(self, query: str, passages: list[str]) -> list[float]:
        raise NotImplementedError

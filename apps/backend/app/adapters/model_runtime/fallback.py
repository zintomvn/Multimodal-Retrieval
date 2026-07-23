from __future__ import annotations

from app.adapters.model_runtime.base import QueryExpander, TextImageEmbedder, TextReranker, VisualQaModel


class PassthroughQueryExpander(QueryExpander):
    """Return the original query when no LLM query-expansion service is configured."""

    def expand(self, query: str, max_variants: int = 5) -> list[str]:
        value = query.strip()
        return [value] if value else []


class UnavailableTextImageEmbedder(TextImageEmbedder):
    """Fail fast when semantic retrieval is requested without an embedding service."""

    def embed_text(self, text: str) -> list[float]:
        raise RuntimeError("No text embedding model is configured.")

    def embed_image_uri(self, image_uri: str) -> list[float]:
        raise RuntimeError("No image embedding model is configured.")


class UnavailableVisualQaModel(VisualQaModel):
    """Fail fast when QA retrieval is requested without a VLM service."""

    def answer(self, question: str, evidence_text: str, answer_hint: str | None = None) -> str:
        raise RuntimeError("No visual QA model is configured.")


class UnavailableTextReranker(TextReranker):
    """Return neutral rerank scores when no reranker service is configured."""

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        return [0.0 for _ in passages]

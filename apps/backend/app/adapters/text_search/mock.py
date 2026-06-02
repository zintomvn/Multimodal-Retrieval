from __future__ import annotations

from app.adapters.text_search.base import TextHit


class InMemoryTextSearchClient:
    def __init__(self) -> None:
        self._indices: dict[str, dict[str, dict]] = {}

    def search(self, index: str, query: str, top_k: int, boosts: dict[str, float] | None = None) -> list[TextHit]:
        boosts = boosts or {}
        tokens = {token.lower() for token in query.split() if token}
        hits: list[TextHit] = []
        for item_id, document in self._indices.get(index, {}).items():
            score = 0.0
            for field, value in document.items():
                if not isinstance(value, str):
                    continue
                overlap = tokens.intersection(value.lower().split())
                score += len(overlap) * boosts.get(field, 1.0)
            if score > 0:
                hits.append(TextHit(id=item_id, score=score, metadata=document))
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:top_k]

    def upsert(self, index: str, documents: list[tuple[str, dict]]) -> int:
        bucket = self._indices.setdefault(index, {})
        for item_id, document in documents:
            bucket[item_id] = document
        return len(documents)

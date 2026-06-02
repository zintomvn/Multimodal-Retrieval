from __future__ import annotations

from app.adapters.vector_db.base import VectorHit


class InMemoryVectorSearchClient:
    def __init__(self) -> None:
        self._collections: dict[str, dict[str, tuple[list[float], dict]]] = {}

    def search(self, collection: str, vector: list[float], top_k: int, filters: dict | None = None) -> list[VectorHit]:
        filters = filters or {}
        items = self._collections.get(collection, {})
        hits: list[VectorHit] = []
        for item_id, (stored_vector, metadata) in items.items():
            if any(metadata.get(key) != value for key, value in filters.items()):
                continue
            score = sum(a * b for a, b in zip(vector, stored_vector))
            hits.append(VectorHit(id=item_id, score=score, metadata=metadata))
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:top_k]

    def upsert(self, collection: str, vectors: list[tuple[str, list[float], dict]]) -> int:
        bucket = self._collections.setdefault(collection, {})
        for item_id, vector, metadata in vectors:
            bucket[item_id] = (vector, metadata)
        return len(vectors)

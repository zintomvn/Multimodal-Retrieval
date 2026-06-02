from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VectorHit:
    id: str
    score: float
    metadata: dict


class VectorSearchClient(Protocol):
    def search(self, collection: str, vector: list[float], top_k: int, filters: dict | None = None) -> list[VectorHit]:
        ...

    def upsert(self, collection: str, vectors: list[tuple[str, list[float], dict]]) -> int:
        ...

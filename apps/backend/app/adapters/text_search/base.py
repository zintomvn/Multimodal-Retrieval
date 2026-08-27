from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TextHit:
    id: str
    score: float
    metadata: dict


class TextSearchClient(Protocol):
    def search(
        self,
        index: str,
        query: str,
        top_k: int,
        boosts: dict[str, float] | None = None,
        source_types: list[str] | None = None,
    ) -> list[TextHit]:
        ...

    def upsert(self, index: str, documents: list[tuple[str, dict]]) -> int:
        ...

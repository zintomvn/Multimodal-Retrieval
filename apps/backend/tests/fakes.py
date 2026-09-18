from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from app.adapters.model_runtime.base import QueryExpander, TextImageEmbedder, VisualQaModel
from app.adapters.text_search.base import TextHit
from app.adapters.vector_db.base import VectorHit


def _stable_float(seed: str, offset: int) -> float:
    digest = hashlib.sha256(f"{seed}:{offset}".encode("utf-8")).digest()
    value = int.from_bytes(digest[:4], "big") / 2**32
    return value * 2 - 1


class DeterministicEmbedder(TextImageEmbedder):
    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed_text(self, text: str) -> list[float]:
        return self._embed(text)

    def embed_image_uri(self, image_uri: str) -> list[float]:
        return self._embed(image_uri)

    def _embed(self, seed: str) -> list[float]:
        vector = [_stable_float(seed.lower(), i) for i in range(self.dim)]
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


class ExpandingQueryExpander(QueryExpander):
    def expand(self, query: str, max_variants: int = 5) -> list[str]:
        variants = [query.strip()]
        lower = query.lower()
        if "castle" in lower or "bavaria" in lower:
            variants.extend(
                [
                    "Neuschwanstein castle Bavaria Disney logo inspiration",
                    "famous company logo inspired by a German castle",
                ]
            )
        if "helmet" in lower or "cyclist" in lower:
            variants.extend(
                [
                    "cyclists crossing finish line in order",
                    "pink helmet blue helmet red helmet bicycle race finish line",
                ]
            )
        if "exhibition" in lower or "dragon" in lower:
            variants.extend(
                [
                    "royal decorative panel dragon cloud motifs exhibition",
                    "PHU XUAN GIA DINH historical exhibition gate",
                ]
            )
        deduped: list[str] = []
        for item in variants:
            if item and item not in deduped:
                deduped.append(item)
        return deduped[:max_variants]


class HintVisualQaModel(VisualQaModel):
    def answer(self, question: str, evidence_text: str, answer_hint: str | None = None) -> str:
        if answer_hint:
            return answer_hint[:100]
        combined = f"{question} {evidence_text}".lower()
        if "company" in combined and ("castle" in combined or "logo" in combined):
            return "Disney"
        if re.search(r"\bhow many\b|bao nhieu|may", combined):
            numbers = re.findall(r"\b\d+\b", combined)
            return numbers[0] if numbers else "3"
        if "color" in combined or "mau" in combined:
            for color in ["red", "blue", "pink", "green", "yellow", "do", "xanh", "hong", "vang"]:
                if color in combined:
                    return color.capitalize()
        return "Unknown"


class InMemoryVectorSearchClient:
    def __init__(self) -> None:
        self._collections: dict[str, dict[str, tuple[list[float], dict]]] = {}

    def search(self, collection: str, vector: list[float], top_k: int, filters: dict | None = None) -> list[VectorHit]:
        filters = filters or {}
        items = self._collections.get(collection, {})
        hits: list[VectorHit] = []
        for item_id, (stored_vector, metadata) in items.items():
            if any((metadata.get(key) not in value if isinstance(value, list) else metadata.get(key) != value)
                   for key, value in filters.items()):
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


class InMemoryTextSearchClient:
    def __init__(self) -> None:
        self._indices: dict[str, dict[str, dict]] = {}

    def search(
        self,
        index: str,
        query: str,
        top_k: int,
        boosts: dict[str, float] | None = None,
        source_types: list[str] | None = None,
    ) -> list[TextHit]:
        boosts = boosts or {}
        tokens = {token.lower() for token in query.split() if token}
        hits: list[TextHit] = []
        for item_id, document in self._indices.get(index, {}).items():
            if source_types and document.get("source_type") not in {*source_types, None}:
                continue
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


class InMemoryObjectStorageClient:
    def __init__(self, public_base_url: str = "http://localhost:9000/test") -> None:
        self._store: dict[str, bytes] = {}
        self._public_base_url = public_base_url.rstrip("/")

    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self._store[key] = data
        return key

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return f"{self._public_base_url}/{key}?expires_in={expires_in}"

    def delete_object(self, key: str) -> None:
        self._store.pop(key, None)

    def public_url(self, key: str) -> str:
        return f"{self._public_base_url}/{key}"

    def list_objects(self, prefix: str) -> list[str]:
        return [key for key in self._store if key.startswith(prefix)]

    def download_to_file(self, key: str, local_path: Path | str) -> None:
        dest = Path(local_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = self._store.get(key)
        if data is None:
            raise FileNotFoundError(f"Object not found: {key}")
        dest.write_bytes(data)

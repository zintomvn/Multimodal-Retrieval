from __future__ import annotations

from app.adapters.vector_db.base import VectorHit


class MilvusVectorSearchClient:
    """Thin optional wrapper. Import pymilvus only when this adapter is enabled."""

    def __init__(self, host: str, port: int) -> None:
        from pymilvus import MilvusClient

        self.client = MilvusClient(uri=f"http://{host}:{port}")

    def search(self, collection: str, vector: list[float], top_k: int, filters: dict | None = None) -> list[VectorHit]:
        filter_expr = self._to_filter_expr(filters or {})
        raw_hits = self.client.search(
            collection_name=collection,
            data=[vector],
            limit=top_k,
            filter=filter_expr,
            output_fields=["frame_id", "video_id", "frame_idx", "event_id", "model_version"],
        )
        hits: list[VectorHit] = []
        for hit in raw_hits[0] if raw_hits else []:
            hits.append(VectorHit(id=str(hit["id"]), score=float(hit["distance"]), metadata=hit.get("entity", {})))
        return hits

    def upsert(self, collection: str, vectors: list[tuple[str, list[float], dict]]) -> int:
        data = [{"id": item_id, "vector": vector, **metadata} for item_id, vector, metadata in vectors]
        if data:
            self.client.upsert(collection_name=collection, data=data)
        return len(data)

    def _to_filter_expr(self, filters: dict) -> str:
        parts = []
        for key, value in filters.items():
            if isinstance(value, str):
                parts.append(f'{key} == "{value}"')
            else:
                parts.append(f"{key} == {value}")
        return " and ".join(parts)

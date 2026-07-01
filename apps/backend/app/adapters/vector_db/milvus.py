from __future__ import annotations

from app.adapters.vector_db.base import VectorHit


class MilvusVectorSearchClient:
    """Thin optional wrapper. Import pymilvus only when this adapter is enabled."""

    def __init__(self, uri: str, token: str = "") -> None:
        from pymilvus import MilvusClient

        kwargs: dict = {"uri": uri}
        if token:
            kwargs["token"] = token
        self.client = MilvusClient(**kwargs)

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
        if not vectors:
            return 0
        self._ensure_collection(collection=collection, dimension=len(vectors[0][1]))
        data = [{"id": item_id, "vector": vector, **metadata} for item_id, vector, metadata in vectors]
        self.client.upsert(collection_name=collection, data=data)
        return len(data)

    def ensure_collection(self, name: str, dim: int) -> None:
        """Create the collection with HNSW/COSINE index if it does not exist."""
        from pymilvus import DataType, MilvusClient

        if self.client.has_collection(name):
            return

        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=True)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)

        index_params = MilvusClient.prepare_index_params()
        index_params.add_index(
            field_name="vector",
            metric_type="COSINE",
            index_type="HNSW",
            params={"M": 16, "efConstruction": 200},
        )

        self.client.create_collection(name, schema=schema, index_params=index_params)

    def _to_filter_expr(self, filters: dict) -> str:
        parts = []
        for key, value in filters.items():
            if isinstance(value, str):
                parts.append(f'{key} == "{value}"')
            else:
                parts.append(f"{key} == {value}")
        return " and ".join(parts)

    def _ensure_collection(self, collection: str, dimension: int) -> None:
        if self.client.has_collection(collection_name=collection):
            return
        from pymilvus import DataType

        schema = self.client.create_schema(enable_dynamic_field=True)
        schema.add_field(field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dimension)
        index_params = self.client.prepare_index_params()
        index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
        self.client.create_collection(
            collection_name=collection,
            schema=schema,
            index_params=index_params,
        )

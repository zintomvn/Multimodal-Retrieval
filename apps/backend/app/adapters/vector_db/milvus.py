from __future__ import annotations

import logging
import os
import time
import json
import re

from app.adapters.vector_db.base import VectorHit


logger = logging.getLogger(__name__)

# These fields are sufficient to resolve a vector hit back to the canonical
# keyframe stored in the relational database. Returning every dynamic ingest
# field is expensive on Zilliz Cloud and can exceed the request deadline.
_SEARCH_OUTPUT_FIELDS = [
    "frame_id",
    "video_id",
    "keyframe_id",
    "original_keyframe_id",
    "map_n",
    "keyframe_number",
    "embedding_index_0",
]


class MilvusVectorSearchClient:
    """Thin optional wrapper. Import pymilvus only when this adapter is enabled."""

    def search_many(self, collection, vectors, top_k, filters=None):
        if not vectors:
            return []
        self._ensure_client()
        raw = self.client.search(collection_name=collection, data=vectors, limit=top_k,
            filter=self._to_filter_expr(filters or {}), output_fields=_SEARCH_OUTPUT_FIELDS,
            timeout=self.search_timeout_s)
        return [[VectorHit(id=str(hit['id']),score=float(hit['distance']),metadata=hit.get('entity',{})) for hit in group] for group in raw]

    def __init__(self, uri: str, token: str = "") -> None:
        self.uri = uri
        self.token = token
        self.client = None
        self.connect_timeout_s = float(os.getenv("MILVUS_CONNECT_TIMEOUT", "3.0"))
        self.search_timeout_s = float(os.getenv("MILVUS_SEARCH_TIMEOUT", "12.0"))
        self.slow_search_warning_s = float(os.getenv("MILVUS_SLOW_SEARCH_WARNING", "2.0"))

    def search(self, collection: str, vector: list[float], top_k: int, filters: dict | None = None) -> list[VectorHit]:
        self._ensure_client()
        filter_expr = self._to_filter_expr(filters or {})
        started_at = time.monotonic()
        raw_hits = self.client.search(
            collection_name=collection,
            data=[vector],
            limit=top_k,
            filter=filter_expr,
            output_fields=_SEARCH_OUTPUT_FIELDS,
            timeout=self.search_timeout_s,
        )
        elapsed_s = time.monotonic() - started_at
        if elapsed_s >= self.slow_search_warning_s:
            logger.warning(
                "Milvus search took %.2fs for collection '%s' (top_k=%s, output_fields=%s).",
                elapsed_s,
                collection,
                top_k,
                len(_SEARCH_OUTPUT_FIELDS),
            )
        hits: list[VectorHit] = []
        for hit in raw_hits[0] if raw_hits else []:
            hits.append(VectorHit(id=str(hit["id"]), score=float(hit["distance"]), metadata=hit.get("entity", {})))
        return hits

    def upsert(self, collection: str, vectors: list[tuple[str, list[float], dict]]) -> int:
        if not vectors:
            return 0
        self._ensure_client()
        self._ensure_collection(collection=collection, dimension=len(vectors[0][1]))
        data = [{"id": item_id, "vector": vector, **metadata} for item_id, vector, metadata in vectors]
        self.client.upsert(collection_name=collection, data=data)
        return len(data)

    def ensure_collection(self, name: str, dim: int) -> None:
        """Create the collection with HNSW/COSINE index if it does not exist."""
        from pymilvus import DataType, MilvusClient

        self._ensure_client()
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

    def _ensure_client(self) -> None:
        if self.client is not None:
            return
        from pymilvus import MilvusClient

        kwargs: dict = {"uri": self.uri}
        if self.token:
            kwargs["token"] = self.token
        kwargs["timeout"] = self.connect_timeout_s
        self.client = MilvusClient(**kwargs)

    def _to_filter_expr(self, filters: dict) -> str:
        parts = []
        for key, value in filters.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError("Invalid vector filter field")
            operator = "in" if isinstance(value, list) else "=="
            parts.append(f"{key} {operator} {json.dumps(value)}")
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

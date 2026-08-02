from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import numpy as np

from .config import SinkConfig


@dataclass(frozen=True)
class TextMilvusPayload:
    """One record for text embedding storage."""

    id: str
    vector: list[float]
    keyframe_id: str | None
    video_id: str | None
    text: str
    doc_type: str
    model_version: str
    metadata: dict[str, Any]


class MilvusTextEmbeddingSink:
    """Upsert Vietnamese text embeddings into Milvus."""

    def __init__(self, config: SinkConfig, collection_name: str) -> None:
        self.config = config
        self.collection_name = collection_name
        self.client = None

    def upsert(self, payloads: list[TextMilvusPayload]) -> int:
        """Upsert one batch of text embeddings."""
        if not payloads:
            return 0
        vectors = np.asarray([item.vector for item in payloads], dtype="float32")
        self._ensure_client()
        self._ensure_collection(int(vectors.shape[1]))
        data = [
            {
                "id": item.id,
                "vector": item.vector,
                "keyframe_id": item.keyframe_id,
                "video_id": item.video_id,
                "text": item.text,
                "doc_type": item.doc_type,
                "model_version": item.model_version,
                "metadata_json": json.dumps(item.metadata, ensure_ascii=False, sort_keys=True),
            }
            for item in payloads
        ]
        self.client.upsert(collection_name=self.collection_name, data=data)
        return len(data)

    def _ensure_client(self) -> None:
        if self.client is not None:
            return
        from pymilvus import MilvusClient

        kwargs = {"uri": self.config.milvus_uri}
        if self.config.milvus_token:
            kwargs["token"] = self.config.milvus_token
        self.client = MilvusClient(**kwargs)

    def _ensure_collection(self, dim: int) -> None:
        if self.client.has_collection(collection_name=self.collection_name):
            return
        from pymilvus import DataType

        schema = self.client.create_schema(enable_dynamic_field=True)
        schema.add_field(field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dim)
        schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=8192)
        schema.add_field(field_name="doc_type", datatype=DataType.VARCHAR, max_length=32)
        schema.add_field(field_name="model_version", datatype=DataType.VARCHAR, max_length=128)
        index_params = self.client.prepare_index_params()
        index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
        self.client.create_collection(
            collection_name=self.collection_name,
            schema=schema,
            index_params=index_params,
        )

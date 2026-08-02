from __future__ import annotations

import numpy as np

from ..gcs_source import FrameItem
from .config import SinkConfig


class MilvusEmbeddingSink:
    """Upsert image embeddings into Zilliz/Milvus."""

    def __init__(self, config: SinkConfig) -> None:
        self.config = config
        self.client = None

    def upsert(self, frames: list[FrameItem], embeddings: np.ndarray | None) -> int:
        """Upsert one embedding batch."""
        if embeddings is None or not frames:
            return 0
        self._ensure_client()
        self._ensure_collection(int(embeddings.shape[1]))
        data = [
            {
                "id": item.keyframe_id,
                "vector": vector.astype(float).tolist(),
                "keyframe_id": item.keyframe_id,
                "video_id": item.video_id,
                "frame_idx": item.frame_idx,
                "model_version": self.config.model_version,
            }
            for item, vector in zip(frames, embeddings)
        ]
        self.client.upsert(collection_name=self.config.milvus_collection, data=data)
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
        if self.client.has_collection(collection_name=self.config.milvus_collection):
            return
        from pymilvus import DataType

        schema = self.client.create_schema(enable_dynamic_field=True)
        schema.add_field(field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dim)
        index_params = self.client.prepare_index_params()
        index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
        self.client.create_collection(
            collection_name=self.config.milvus_collection,
            schema=schema,
            index_params=index_params,
        )

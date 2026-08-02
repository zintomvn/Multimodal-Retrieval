from __future__ import annotations

import logging
from typing import Any

import numpy as np

from ..gcs_source import FrameItem
from .config import SinkConfig
from .elasticsearch import ElasticsearchAnnotationSink
from .milvus import MilvusEmbeddingSink
from .postgres import PostgresAnnotationSink


LOGGER = logging.getLogger(__name__)


class CloudAnnotationSink:
    """Fan out processed batches to enabled cloud sinks."""

    def __init__(self, config: SinkConfig) -> None:
        self.config = config
        self.pg = PostgresAnnotationSink(config) if config.write_pg else None
        self.milvus = MilvusEmbeddingSink(config) if config.write_milvus else None
        self.elasticsearch = ElasticsearchAnnotationSink(config) if config.write_elasticsearch else None

    def upsert_batch(
        self,
        frames: list[FrameItem],
        annotations: list[dict[str, Any]],
        embeddings: np.ndarray | None,
    ) -> dict[str, int]:
        """Write one processed batch to all enabled sinks."""
        if self.config.dry_run:
            return {"pg": 0, "milvus": 0, "es": 0, "sink_errors": 0}
        results = {"pg": 0, "milvus": 0, "es": 0, "sink_errors": 0}
        sink_calls = (
            ("pg", self.pg, (frames, annotations)),
            ("milvus", self.milvus, (frames, embeddings)),
            ("es", self.elasticsearch, (frames, annotations)),
        )
        for name, sink, payload in sink_calls:
            if sink is None:
                continue
            try:
                results[name] = sink.upsert(*payload)
            except Exception as exc:
                results["sink_errors"] += 1
                if self.config.fail_on_sink_error:
                    LOGGER.exception("%s sink failed: %s", name, exc)
                    raise
                LOGGER.error("%s sink failed: %s", name, exc)
        return results

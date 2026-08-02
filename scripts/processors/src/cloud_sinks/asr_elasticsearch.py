from __future__ import annotations

import socket
from typing import Any
from urllib.parse import urlparse, urlunparse

from .config import SinkConfig


class ElasticsearchAsrSink:
    """Index faster-whisper ASR segments into Elasticsearch."""

    def __init__(self, config: SinkConfig, index_name: str) -> None:
        self.config = config
        self.index_name = index_name
        self.client = None
        self.bulk = None
        self.disabled = False

    def upsert(self, records: list[dict[str, Any]]) -> int:
        """Index ASR segment records."""
        if self.disabled or not records:
            return 0
        try:
            resolved_url = self._resolve_url(self.config.elasticsearch_url)
            self._probe_endpoint(resolved_url)
            self._ensure_client()
            self._ensure_index()
            actions = [self._build_action(record) for record in records]
            success, _ = self.bulk(self.client, actions, raise_on_error=True)
            return int(success)
        except Exception:
            if self.config.elasticsearch_disable_after_error:
                self.disabled = True
            raise

    def _build_action(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "_op_type": "index",
            "_index": self.index_name,
            "_id": record["segment_id"],
            "_source": record,
        }

    def _ensure_client(self) -> None:
        if self.client is not None:
            return
        from elasticsearch import Elasticsearch
        from elasticsearch.helpers import bulk

        self.client = Elasticsearch(
            self._resolve_url(self.config.elasticsearch_url),
            request_timeout=self.config.elasticsearch_request_timeout,
            max_retries=self.config.elasticsearch_max_retries,
            retry_on_timeout=True,
        )
        self.bulk = bulk

    def _ensure_index(self) -> None:
        if self.client.indices.exists(index=self.index_name):
            return
        self.client.indices.create(
            index=self.index_name,
            mappings={
                "properties": {
                    "segment_id": {"type": "keyword"},
                    "video_id": {"type": "keyword"},
                    "run_id": {"type": "keyword"},
                    "stage": {"type": "keyword"},
                    "start_seconds": {"type": "float"},
                    "end_seconds": {"type": "float"},
                    "text": {"type": "text"},
                    "language": {"type": "keyword"},
                    "model_name": {"type": "keyword"},
                    "source_gcs_uri": {"type": "keyword"},
                }
            },
        )

    def _resolve_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.hostname != "elasticsearch":
            return url
        netloc = "localhost"
        if parsed.port:
            netloc = f"localhost:{parsed.port}"
        return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))

    def _probe_endpoint(self, url: str) -> None:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            return
        try:
            with socket.create_connection((host, port), timeout=self.config.elasticsearch_probe_timeout):
                return
        except OSError as exc:
            raise ConnectionError(f"Elasticsearch unavailable at {url}") from exc

from __future__ import annotations

import socket
from typing import Any
from urllib.parse import urlparse, urlunparse

from ..gcs_source import FrameItem
from .config import SinkConfig


class ElasticsearchAnnotationSink:
    """Index annotation text into Elasticsearch."""

    def __init__(self, config: SinkConfig) -> None:
        self.config = config
        self.client = None
        self.bulk = None
        self.disabled = False

    def upsert(self, frames: list[FrameItem], annotations: list[dict[str, Any]]) -> int:
        """Index one annotation batch."""
        if self.disabled:
            return 0
        try:
            resolved_url = self._resolve_url(self.config.elasticsearch_url)
            self._probe_endpoint(resolved_url)
            self._ensure_client()
            self._ensure_index()
            actions = [self._build_action(item, record) for item, record in zip(frames, annotations)]
            if not actions:
                return 0
            success, _ = self.bulk(self.client, actions, raise_on_error=True)
            return int(success)
        except Exception:
            if self.config.elasticsearch_disable_after_error:
                self.disabled = True
            raise

    def _build_action(self, item: FrameItem, record: dict[str, Any]) -> dict[str, Any]:
        texts = record.get("texts") if isinstance(record.get("texts"), list) else []
        return {
            "_op_type": "index",
            "_index": self.config.elasticsearch_index,
            "_id": item.keyframe_id,
            "_source": {
                "keyframe_id": item.keyframe_id,
                "video_id": item.video_id,
                "shot_id": item.shot_id,
                "frame_seconds": item.frame_seconds,
                "caption": str(record.get("caption") or ""),
                "ocr_texts": " ".join(texts),
                "detected_objects": record.get("objects") if isinstance(record.get("objects"), list) else [],
                "object_counts": record.get("object_counts") if isinstance(record.get("object_counts"), dict) else {},
                "image_uri": item.gcs_uri,
                "image_url": self._public_url(item.blob_name),
            },
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

    def _ensure_index(self) -> None:
        if self.client.indices.exists(index=self.config.elasticsearch_index):
            return
        self.client.indices.create(
            index=self.config.elasticsearch_index,
            mappings={
                "properties": {
                    "keyframe_id": {"type": "keyword"},
                    "video_id": {"type": "keyword"},
                    "shot_id": {"type": "keyword"},
                    "frame_seconds": {"type": "float"},
                    "caption": {"type": "text"},
                    "ocr_texts": {"type": "text"},
                    "detected_objects": {"type": "keyword"},
                    "object_counts": {"type": "object"},
                    "image_uri": {"type": "keyword"},
                    "image_url": {"type": "keyword"},
                }
            },
        )

    def _public_url(self, blob_name: str) -> str:
        if self.config.gcs_public_url:
            return f"{self.config.gcs_public_url.rstrip('/')}/{blob_name}"
        bucket = self.config.dataset_root_uri.removeprefix("gs://").split("/", 1)[0]
        return f"https://storage.googleapis.com/{bucket}/{blob_name}"

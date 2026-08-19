from __future__ import annotations

import json
import socket
from typing import Any
from urllib import error, request
from urllib.parse import urlparse, urlunparse

from ..gcs_source import FrameItem
from .config import SinkConfig


class ElasticsearchAnnotationSink:
    """Index annotation text into Elasticsearch."""

    def __init__(self, config: SinkConfig) -> None:
        self.config = config
        self.url = self._resolve_url(self.config.elasticsearch_url).rstrip("/")
        self.disabled = False

    def upsert(self, frames: list[FrameItem], annotations: list[dict[str, Any]]) -> int:
        """Index one annotation batch."""
        if self.disabled:
            return 0
        try:
            self._probe_endpoint(self.url)
            self._ensure_index()
            actions = [self._build_action(item, record) for item, record in zip(frames, annotations)]
            if not actions:
                return 0
            return self._bulk(actions)
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
        index_url = f"{self.url}/{self.config.elasticsearch_index}"
        if self._head(index_url):
            return
        self._request_json(
            "PUT",
            index_url,
            {
                "mappings": {
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
                }
            },
        )

    def _bulk(self, actions: list[dict[str, Any]]) -> int:
        lines: list[str] = []
        for action in actions:
            op = action.get("_op_type", "index")
            index = action["_index"]
            item_id = action["_id"]
            source = action["_source"]
            lines.append(json.dumps({op: {"_index": index, "_id": item_id}}, ensure_ascii=False))
            lines.append(json.dumps(source, ensure_ascii=False))
        body = ("\n".join(lines) + "\n").encode("utf-8")
        response = self._request_bytes(
            "POST",
            f"{self.url}/_bulk",
            body,
            content_type="application/x-ndjson",
        )
        if response.get("errors"):
            failures = [item for item in response.get("items", []) if item.get("index", {}).get("error")]
            raise RuntimeError(f"Elasticsearch bulk import failed for {len(failures)} documents")
        return len(actions)

    def _head(self, url: str) -> bool:
        req = request.Request(url, method="HEAD")
        try:
            with request.urlopen(req, timeout=self.config.elasticsearch_request_timeout):
                return True
        except error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise

    def _request_json(self, method: str, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_bytes(
            method,
            url,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
        )

    def _request_bytes(self, method: str, url: str, body: bytes, *, content_type: str) -> dict[str, Any]:
        req = request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": content_type,
            },
        )
        with request.urlopen(req, timeout=self.config.elasticsearch_request_timeout) as response:
            raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _public_url(self, blob_name: str) -> str:
        if self.config.gcs_public_url:
            return f"{self.config.gcs_public_url.rstrip('/')}/{blob_name}"
        bucket = self.config.dataset_root_uri.removeprefix("gs://").split("/", 1)[0]
        return f"https://storage.googleapis.com/{bucket}/{blob_name}"

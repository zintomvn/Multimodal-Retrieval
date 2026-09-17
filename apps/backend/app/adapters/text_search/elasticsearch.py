from __future__ import annotations

import logging
from urllib.parse import urlparse, urlunparse

from app.adapters.text_search.base import TextHit

logger = logging.getLogger(__name__)


class ElasticsearchTextSearchClient:
    """Optional adapter. Import elasticsearch only when this adapter is enabled."""

    def __init__(self, url: str) -> None:
        from elasticsearch import Elasticsearch

        self.client = self._connect_with_fallback(Elasticsearch=Elasticsearch, url=url)

    def search(
        self,
        index: str,
        query: str,
        top_k: int,
        boosts: dict[str, float] | None = None,
        source_types: list[str] | None = None,
    ) -> list[TextHit]:
        boosts = boosts or {}
        query = " ".join((query or "").split())
        if not query:
            return []
        fields = [f"{field}^{boost}" for field, boost in boosts.items()] or [
            "text_value",
            "caption",
            "ocr_texts",
            "detected_objects",
            "asr_text",
            "normalized_asr_text",
        ]
        try:
            match_query = {
                "multi_match": {
                    "query": query,
                    "fields": fields,
                    "type": "best_fields",
                    "operator": "or",
                    "minimum_should_match": self._minimum_should_match(query),
                }
            }
            query_body = match_query
            if source_types:
                query_body = {
                    "bool": {
                        "filter": [{"terms": {"source_type": source_types}}],
                        "must": [match_query],
                    }
                }
            response = self.client.search(
                index=index,
                size=top_k,
                ignore_unavailable=False,
                request_timeout=3,
                query=query_body,
            )
        except Exception as exc:
            # The retrieval service decides whether partial results are allowed.
            # An unavailable index is not a successful query with zero matches.
            logger.warning("Elasticsearch search unavailable for index '%s'.", index)
            raise RuntimeError("Text search is unavailable") from exc
        return [
            TextHit(id=hit["_id"], score=float(hit["_score"]), metadata=hit.get("_source", {}))
            for hit in response.get("hits", {}).get("hits", [])
        ]

    @staticmethod
    def _minimum_should_match(query: str) -> str:
        token_count = len(query.split())
        if token_count <= 2:
            return "100%"
        if token_count <= 4:
            return "75%"
        return "50%"

    def upsert(self, index: str, documents: list[tuple[str, dict]]) -> int:
        self._ensure_index(index)
        for item_id, document in documents:
            self.client.index(index=index, id=item_id, document=document)
        return len(documents)

    def _ensure_index(self, index: str) -> None:
        properties = {
            "caption": {"type": "text"},
            "text_value": {"type": "text"},
            "ocr_texts": {"type": "text"},
            "detected_objects": {"type": "text"},
            "asr_text": {"type": "text"},
            "normalized_asr_text": {"type": "text"},
            "raw_asr_text": {"type": "text"},
            "context_before": {"type": "text"},
            "context_after": {"type": "text"},
            "source_type": {"type": "keyword"},
            "kind": {"type": "keyword"},
            "video_id": {"type": "keyword"},
            "video_code": {"type": "keyword"},
            "keyframe_id": {"type": "keyword"},
            "frame_id": {"type": "keyword"},
            "segment_id": {"type": "keyword"},
            "batch_id": {"type": "keyword"},
            "dataset_code": {"type": "keyword"},
            "frame_idx": {"type": "integer"},
            "frame_seconds": {"type": "float"},
            "timestamp_ms": {"type": "long"},
            "start_seconds": {"type": "float"},
            "end_seconds": {"type": "float"},
        }
        try:
            if self.client.indices.exists(index=index):
                mapping = self.client.indices.get_mapping(index=index)
                existing = mapping.get(index, {}).get("mappings", {}).get("properties", {})
                missing = {name: config for name, config in properties.items() if name not in existing}
                if missing:
                    self.client.indices.put_mapping(index=index, properties=missing)
                return
            self.client.indices.create(
                index=index,
                settings={"number_of_shards": 1, "number_of_replicas": 0},
                mappings={
                    "dynamic": True,
                    "properties": properties,
                },
            )
        except Exception:  # noqa: BLE001 - index creation should not break ingest compatibility.
            logger.warning("Elasticsearch index ensure failed for '%s'.", index, exc_info=True)

    def _connect_with_fallback(self, Elasticsearch, url: str):  # noqa: ANN001 - external client type.
        client_options = {"request_timeout": 3, "max_retries": 0, "retry_on_timeout": False}
        primary = Elasticsearch(url, **client_options)
        if self._can_ping(primary):
            return primary
        fallback = self._fallback_url(url)
        if fallback and fallback != url:
            secondary = Elasticsearch(fallback, **client_options)
            if self._can_ping(secondary):
                return secondary
        return primary

    def _can_ping(self, client) -> bool:  # noqa: ANN001 - external client type.
        try:
            return bool(client.ping())
        except Exception:  # noqa: BLE001 - connectivity probes should not fail initialization.
            return False

    def _fallback_url(self, url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.hostname != "elasticsearch":
            return None
        netloc = "localhost"
        if parsed.port:
            netloc = f"localhost:{parsed.port}"
        return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))

from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from app.adapters.text_search.base import TextHit


class ElasticsearchTextSearchClient:
    """Optional adapter. Import elasticsearch only when this adapter is enabled."""

    def __init__(self, url: str) -> None:
        from elasticsearch import Elasticsearch

        self.client = self._connect_with_fallback(Elasticsearch=Elasticsearch, url=url)

    def search(self, index: str, query: str, top_k: int, boosts: dict[str, float] | None = None) -> list[TextHit]:
        boosts = boosts or {}
        fields = [f"{field}^{boost}" for field, boost in boosts.items()] or ["*"]
        response = self.client.search(
            index=index,
            size=top_k,
            query={
                "multi_match": {
                    "query": query,
                    "fields": fields,
                    "fuzziness": "AUTO",
                }
            },
        )
        return [
            TextHit(id=hit["_id"], score=float(hit["_score"]), metadata=hit.get("_source", {}))
            for hit in response.get("hits", {}).get("hits", [])
        ]

    def upsert(self, index: str, documents: list[tuple[str, dict]]) -> int:
        for item_id, document in documents:
            self.client.index(index=index, id=item_id, document=document)
        return len(documents)

    def _connect_with_fallback(self, Elasticsearch, url: str):  # noqa: ANN001 - external client type.
        primary = Elasticsearch(url)
        if self._can_ping(primary):
            return primary
        fallback = self._fallback_url(url)
        if fallback and fallback != url:
            secondary = Elasticsearch(fallback)
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

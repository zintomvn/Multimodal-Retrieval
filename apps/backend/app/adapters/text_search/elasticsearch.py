from __future__ import annotations

from app.adapters.text_search.base import TextHit


class ElasticsearchTextSearchClient:
    """Optional adapter. Import elasticsearch only when this adapter is enabled."""

    def __init__(self, url: str) -> None:
        from elasticsearch import Elasticsearch

        self.client = Elasticsearch(url)

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

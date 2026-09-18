from __future__ import annotations

import sys
from pathlib import Path
import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.adapters.text_search.elasticsearch import ElasticsearchTextSearchClient


class CapturingElasticsearchClient:
    def __init__(self) -> None:
        self.request: dict = {}

    def search(self, **kwargs):  # noqa: ANN003 - mirrors the external client.
        self.request = kwargs
        return {"hits": {"hits": []}}


def test_long_vietnamese_query_does_not_enable_fuzzy_clause_expansion() -> None:
    capturing_client = CapturingElasticsearchClient()
    text_client = ElasticsearchTextSearchClient.__new__(ElasticsearchTextSearchClient)
    text_client.client = capturing_client

    text_client.search(
        "keyframe_annotations",
        query="tim nguoi mac ao do dang di bo tren duong pho vao buoi sang",
        top_k=50,
        boosts={"asr_text": 2.5, "normalized_asr_text": 2.5},
    )

    multi_match = capturing_client.request["query"]["multi_match"]
    assert multi_match["type"] == "best_fields"
    assert multi_match["minimum_should_match"] == "50%"
    assert "fuzziness" not in multi_match


def test_search_outage_is_not_reported_as_empty_hits():
    class UnavailableClient:
        def search(self, **kwargs):
            raise ConnectionError('offline')
    adapter = ElasticsearchTextSearchClient.__new__(ElasticsearchTextSearchClient)
    adapter.client = UnavailableClient()
    with pytest.raises(RuntimeError, match='Text search is unavailable'):
        adapter.search('keyframe_annotations', query='test', top_k=5, boosts={})


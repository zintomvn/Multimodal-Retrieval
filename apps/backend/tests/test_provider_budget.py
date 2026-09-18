from threading import Event
from time import monotonic

import pytest

from app.core.budget import bounded_call, deadline
from app.adapters.vector_db.milvus import MilvusVectorSearchClient
from app.adapters.text_search.elasticsearch import ElasticsearchTextSearchClient


def test_deadline_returns_before_blocked_provider_and_recovers():
    release = Event()
    token = deadline.set(monotonic() + .05)
    try:
        with pytest.raises(TimeoutError):
            bounded_call(release.wait, 2)
    finally:
        release.set()
        deadline.reset(token)
    assert bounded_call(lambda: 42) == 42


def test_exhausted_budget_does_not_start_provider():
    called = []
    token = deadline.set(monotonic() - 1)
    try:
        with pytest.raises(TimeoutError):
            bounded_call(lambda: called.append(True))
    finally:
        deadline.reset(token)
    assert not called


def test_vector_filter_list_and_escaping():
    adapter = MilvusVectorSearchClient('unused')
    assert adapter._to_filter_expr({'video_id': ['a', 'b"c']}) == 'video_id in ["a", "b\\"c"]'
    with pytest.raises(ValueError):
        adapter._to_filter_expr({'x or true': 'a'})


def test_text_batch_filters_before_top_k_and_preserves_partial_failure():
    class Client:
        def msearch(self, **kwargs):
            self.request = kwargs
            return {'responses': [{'hits': {'hits': [{'_id': 'a', '_score': 1, '_source': {}}]}}, {'error': 'failed'}]}
    adapter = object.__new__(ElasticsearchTextSearchClient)
    adapter.client = Client()
    request = {'query': 'caption', 'top_k': 5, 'source_types': ['CAPTION'], 'boosts': {'text_value': 1}}
    results = adapter.search_many('annotations', [request, request], ['allowed'])
    assert results[0][0].id == 'a'
    assert isinstance(results[1], RuntimeError)
    body = adapter.client.request['searches'][1]
    assert body['size'] == 5
    assert {'terms': {'video_id': ['allowed']}} in body['query']['bool']['filter']

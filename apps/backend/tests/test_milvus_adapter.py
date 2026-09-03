from app.adapters.vector_db.milvus import MilvusVectorSearchClient, _SEARCH_OUTPUT_FIELDS


class _StubMilvusClient:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    def search(self, **kwargs):  # noqa: ANN003
        self.kwargs = kwargs
        return [[{"id": "L21_V001_shot_0001_first_f000001", "distance": 0.9, "entity": {"keyframe_id": "L21_V001_F000001"}}]]


def test_search_requests_only_keyframe_resolution_metadata() -> None:
    stub = _StubMilvusClient()
    adapter = MilvusVectorSearchClient(uri="http://milvus.test")
    adapter.client = stub

    hits = adapter.search("keyframes", vector=[0.1, 0.2], top_k=50)

    assert stub.kwargs["output_fields"] == _SEARCH_OUTPUT_FIELDS
    assert stub.kwargs["timeout"] == adapter.search_timeout_s
    assert hits[0].id == "L21_V001_shot_0001_first_f000001"
    assert hits[0].metadata["keyframe_id"] == "L21_V001_F000001"

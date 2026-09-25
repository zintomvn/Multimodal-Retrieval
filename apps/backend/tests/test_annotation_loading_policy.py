from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.modules.retrieval.schemas import SearchOptions
from app.modules.retrieval.service import RetrievalService


def test_disabled_profile_does_not_load_annotations_for_reranking():
    service = RetrievalService.__new__(RetrievalService)
    assert not service._needs_frame_annotations(SearchOptions(use_reranker=True), {"reranking": {"enabled": False}})
    assert not service._needs_frame_annotations(SearchOptions(use_reranker=True), {})
    assert service._needs_frame_annotations(SearchOptions(use_reranker=True), {"reranking": {"enabled": True}})
    assert not service._needs_frame_annotations(SearchOptions(use_reranker=False), {"reranking": {"enabled": True}})


def test_annotation_filters_still_load_annotations():
    service = RetrievalService.__new__(RetrievalService)
    assert service._needs_frame_annotations(SearchOptions(objects=["car"], use_reranker=False), {})
    assert service._needs_frame_annotations(SearchOptions(scene="street", use_reranker=False), {})

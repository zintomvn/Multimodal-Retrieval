from __future__ import annotations

from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.adapters.text_search.base import TextHit
from app.adapters.vector_db.base import VectorHit
from app.db.models import Base, Dataset, Frame, FrameAnnotation, Shot, Video
from app.modules.models.service import ModelRegistryService
from app.modules.retrieval.query_planning import QueryPlanningResult
from app.modules.retrieval.schemas import SearchOptions, SearchRequest
from app.modules.retrieval.service import FrameScore, RetrievalService
from tests.fakes import DeterministicEmbedder, ExpandingQueryExpander, HintVisualQaModel


class StubVectorClient:
    def search(self, collection: str, vector: list[float], top_k: int, filters: dict | None = None) -> list[VectorHit]:
        _ = (collection, vector, filters)
        hits = [
            VectorHit(id="L30_V001_F000020", score=0.95, metadata={"keyframe_id": "L30_V001_F000020", "video_id": "L30_V001"}),
            VectorHit(id="L30_V001_F000005", score=0.50, metadata={"keyframe_id": "L30_V001_F000005", "video_id": "L30_V001"}),
            VectorHit(id="L30_V002_F000012", score=0.40, metadata={"keyframe_id": "L30_V002_F000012", "video_id": "L30_V002"}),
        ]
        return hits[:top_k]

    def upsert(self, collection: str, vectors: list[tuple[str, list[float], dict]]) -> int:
        _ = (collection, vectors)
        return len(vectors)


class StubTextClient:
    def __init__(self) -> None:
        self.search_calls = 0

    def search(self, index: str, query: str, top_k: int, boosts: dict[str, float] | None = None, source_types: list[str] | None = None) -> list[TextHit]:
        _ = (index, query, boosts, source_types)
        self.search_calls += 1
        hits = [
            TextHit(id="L30_V001_F000005", score=10.0, metadata={"keyframe_id": "L30_V001_F000005", "video_id": "L30_V001"}),
            TextHit(id="L30_V002_F000012", score=8.0, metadata={"keyframe_id": "L30_V002_F000012", "video_id": "L30_V002"}),
            TextHit(id="L30_V001_F000020", score=1.0, metadata={"keyframe_id": "L30_V001_F000020", "video_id": "L30_V001"}),
        ]
        return hits[:top_k]

    def upsert(self, index: str, documents: list[tuple[str, dict]]) -> int:
        _ = (index, documents)
        return len(documents)


class StaticEmbedder:
    def embed_text(self, text: str) -> list[float]:
        _ = text
        return [1.0]

    def embed_image_uri(self, image_uri: str) -> list[float]:
        _ = image_uri
        return [1.0]


class VisualModeVectorClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def search(self, collection: str, vector: list[float], top_k: int, filters: dict | None = None) -> list[VectorHit]:
        _ = (vector, filters)
        self.calls.append(collection)
        by_collection = {
            "clip_vectors": [
                VectorHit(id="L30_V001_F000005", score=0.95, metadata={"keyframe_id": "L30_V001_F000005", "video_id": "L30_V001"}),
                VectorHit(id="L30_V001_F000020", score=0.50, metadata={"keyframe_id": "L30_V001_F000020", "video_id": "L30_V001"}),
            ],
            "siglip2_vectors": [
                VectorHit(id="L30_V001_F000020", score=0.99, metadata={"keyframe_id": "L30_V001_F000020", "video_id": "L30_V001"}),
            ],
        }
        return by_collection.get(collection, [])[:top_k]

    def upsert(self, collection: str, vectors: list[tuple[str, list[float], dict]]) -> int:
        _ = (collection, vectors)
        return len(vectors)


def _build_fixture(tmp_path: Path) -> tuple[Session, RetrievalService, Dataset]:
    db_path = tmp_path / "fusion.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()

    dataset = Dataset(
        dataset_code="fusion-demo",
        name="fusion-demo",
        version="v1",
        root_uri="file:///demo",
        status="READY",
    )
    session.add(dataset)
    session.flush()

    video_1 = Video(
        video_id="L30_V001",
        dataset_id=dataset.dataset_id,
        video_code="L30_V001",
        video_name="L30_V001.mp4",
        uri="file:///demo/L30_V001.mp4",
        fps=25.0,
        duration_seconds=30.0,
        duration_ms=30000,
    )
    video_2 = Video(
        video_id="L30_V002",
        dataset_id=dataset.dataset_id,
        video_code="L30_V002",
        video_name="L30_V002.mp4",
        uri="file:///demo/L30_V002.mp4",
        fps=25.0,
        duration_seconds=30.0,
        duration_ms=30000,
    )
    session.add_all([video_1, video_2])
    session.flush()

    session.add_all(
        [
            Shot(
                shot_id="L30_V001_S0000",
                video_id=video_1.video_id,
                shot_index=0,
                start_frame=0,
                end_frame=1000,
                start_seconds=0.0,
                end_seconds=30.0,
                boundary_threshold=0.4,
            ),
            Shot(
                shot_id="L30_V002_S0000",
                video_id=video_2.video_id,
                shot_index=0,
                start_frame=0,
                end_frame=1000,
                start_seconds=0.0,
                end_seconds=30.0,
                boundary_threshold=0.4,
            ),
        ]
    )
    session.flush()

    frame_person = Frame(
        keyframe_id="L30_V001_F000005",
        video_id=video_1.video_id,
        shot_id="L30_V001_S0000",
        frame_idx=5,
        frame_seconds=5.0,
        timestamp_ms=5000,
        image_uri="/tmp/L30_V001_F000005.jpg",
        quality_score=1.0,
    )
    frame_semantic = Frame(
        keyframe_id="L30_V001_F000020",
        video_id=video_1.video_id,
        shot_id="L30_V001_S0000",
        frame_idx=20,
        frame_seconds=20.0,
        timestamp_ms=20000,
        image_uri="/tmp/L30_V001_F000020.jpg",
        quality_score=1.0,
    )
    frame_other_video = Frame(
        keyframe_id="L30_V002_F000012",
        video_id=video_2.video_id,
        shot_id="L30_V002_S0000",
        frame_idx=12,
        frame_seconds=12.0,
        timestamp_ms=12000,
        image_uri="/tmp/L30_V002_F000012.jpg",
        quality_score=1.0,
    )
    session.add_all([frame_person, frame_semantic, frame_other_video])
    session.flush()

    session.add_all(
        [
            FrameAnnotation(
                frame_id=frame_person.keyframe_id,
                kind="MULTIMODAL",
                text_value="nguoi ao do di bo",
                caption="nguoi ao do",
                detected_objects=["person"],
                object_counts={"person": 1},
                json_value={},
            ),
            FrameAnnotation(
                frame_id=frame_semantic.keyframe_id,
                kind="MULTIMODAL",
                text_value="canh quan duong pho",
                caption="canh duong pho",
                detected_objects=["building"],
                object_counts={"building": 1},
                json_value={},
            ),
            FrameAnnotation(
                frame_id=frame_other_video.keyframe_id,
                kind="MULTIMODAL",
                text_value="oto tren duong",
                caption="xe hoi",
                detected_objects=["car"],
                object_counts={"car": 1},
                json_value={},
            ),
        ]
    )
    session.commit()

    model_registry = ModelRegistryService(
        embedder=DeterministicEmbedder(dim=64),
        query_expander=ExpandingQueryExpander(),
        visual_qa=HintVisualQaModel(),
    )
    service = RetrievalService(
        db=session,
        model_registry=model_registry,
        vector_client=StubVectorClient(),
        text_client=StubTextClient(),
    )
    service.profiles["m4_weighted"] = {
        "semantic_weight": 0.9,
        "metadata_weight": 0.05,
        "quality_weight": 0.05,
        "rrf": {"enabled": False, "k": 60},
        "query_expansion": {"enabled_default": False, "max_variants": 1},
    }
    service.profiles["m4_rrf"] = {
        "semantic_weight": 0.9,
        "metadata_weight": 0.05,
        "quality_weight": 0.05,
        "rrf": {"enabled": True, "k": 1, "blend": 1.0, "semantic_weight": 0.1, "text_weight": 0.9},
        "query_expansion": {"enabled_default": False, "max_variants": 1},
    }
    return session, service, dataset


def test_visual_search_mode_selects_siglip2_and_fuses_both_with_rrf(tmp_path: Path) -> None:
    db, service, dataset = _build_fixture(tmp_path)
    vector_client = VisualModeVectorClient()
    service.vector_client = vector_client
    service.model_registry.embedder = StaticEmbedder()  # type: ignore[assignment]
    service.model_registry.embedders = {
        "clip_vith14_quickgelu_dfn5b_v2": StaticEmbedder(),  # type: ignore[dict-item]
        "siglip2_so400m16_384_webli_openclip_1152_v1": StaticEmbedder(),  # type: ignore[dict-item]
    }
    service.profiles["visual_mode"] = {
        "semantic_weight": 1.0,
        "metadata_weight": 0.0,
        "quality_weight": 0.0,
        "rrf": {"enabled": False, "k": 60},
        "visual_rrf": {"enabled": True, "k": 1},
        "query_expansion": {"enabled_default": False, "max_variants": 1},
        "visual_models": {
            "clip_global": {
                "model_key": "clip_vith14_quickgelu_dfn5b_v2",
                "collection": "clip_vectors",
                "weight": 1.0,
                "enabled": True,
            },
            "siglip2_fine_grained": {
                "model_key": "siglip2_so400m16_384_webli_openclip_1152_v1",
                "collection": "siglip2_vectors",
                "weight": 1.0,
                "enabled": False,
            },
        },
    }

    common_options = {
        "use_query_expansion": False,
        "use_agent_query_planning": False,
        "use_metadata": False,
        "use_reranker": False,
    }
    openclip_response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="KIS",
            query_name="visual-openclip",
            query_text="person in red shirt",
            profile="visual_mode",
            top_k=2,
            options=SearchOptions(**common_options, visual_search_mode="openclip"),
        )
    )
    siglip2_response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="KIS",
            query_name="visual-siglip2",
            query_text="person in red shirt",
            profile="visual_mode",
            top_k=2,
            options=SearchOptions(**common_options, visual_search_mode="siglip2"),
        )
    )
    both_response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="KIS",
            query_name="visual-both",
            query_text="person in red shirt",
            profile="visual_mode",
            top_k=2,
            options=SearchOptions(**common_options, visual_search_mode="both"),
        )
    )

    assert openclip_response.results[0].frame_id == "L30_V001_F000005"
    assert siglip2_response.results[0].frame_id == "L30_V001_F000020"
    assert both_response.results[0].frame_id == "L30_V001_F000020"
    assert both_response.normalized_query["visual_search"]["mode"] == "both"
    assert both_response.normalized_query["visual_search"]["fusion"] == "visual_rrf"
    semantic_hit = both_response.results[0].score_breakdown["semantic_hit"]
    assert semantic_hit["fusion"] == "visual_rrf"
    assert {source["collection"] for source in semantic_hit["sources"]} == {"clip_vectors", "siglip2_vectors"}
    assert "clip_vectors" in vector_client.calls
    assert "siglip2_vectors" in vector_client.calls

    db.close()


def test_explicit_semantic_views_use_independent_multiperspective_fusion(tmp_path: Path) -> None:
    db, service, dataset = _build_fixture(tmp_path)
    views = ["chef pours egg into soup", "chef cuts tofu above the pot"]
    response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="KIS",
            query_name="frozen-perspectives",
            query_text="long original KIS query",
            profile="m4_weighted",
            top_k=3,
            options=SearchOptions(
                use_query_expansion=False,
                use_agent_query_planning=False,
                use_metadata=False,
                use_reranker=False,
                semantic_views=views,
                semantic_fusion="multiperspective",
            ),
        )
    )

    assert response.normalized_query["semantic_variants"] == views
    fusion = response.results[0].score_breakdown["semantic_hit"]["multiperspective_fusion"]
    assert fusion["strategy"] == "aithena_independent_view_merge"
    assert fusion["total_views"] == 2
    assert {item["semantic_query"] for item in fusion["matched_view_details"]} == set(views)
    db.close()


def test_qwen3_vl_visual_mode_selects_only_qwen_collection(tmp_path: Path) -> None:
    db, service, _dataset = _build_fixture(tmp_path)
    service.model_registry.registry = {
        "embedders": {
            "qwen3_vl_embedding_2b_2048_v1": {
                "provider": "openai_compatible",
                "model": "Qwen/Qwen3-VL-Embedding-2B",
                "collection": "qwen3_vectors",
            },
            "clip_vith14": {
                "provider": "openai_compatible",
                "model": "ViT-H-14",
                "collection": "clip_vectors",
            },
        }
    }

    collections = service._semantic_collections({}, "qwen3_vl")

    assert [(item.model_key, item.collection) for item in collections] == [
        ("qwen3_vl_embedding_2b_2048_v1", "qwen3_vectors")
    ]
    db.close()


def test_result_diversification_prioritizes_videos_and_separated_frames(tmp_path: Path) -> None:
    db, service, _dataset = _build_fixture(tmp_path)
    frames = {frame.keyframe_id: frame for frame in db.query(Frame).all()}
    near_frame = Frame(
        keyframe_id="L30_V001_F000007",
        video_id=frames["L30_V001_F000005"].video_id,
        frame_idx=7,
        timestamp_ms=7000,
        quality_score=1.0,
    )

    def scored(frame: Frame, score: float) -> FrameScore:
        return FrameScore(
            frame=frame,
            semantic_score=score,
            text_score=0.0,
            quality_score=1.0,
            weighted_score=score,
            rrf_score=0.0,
            final_score=score,
        )

    ranked = [
        scored(frames["L30_V001_F000005"], 0.99),
        scored(near_frame, 0.98),
        scored(frames["L30_V001_F000020"], 0.97),
        scored(frames["L30_V002_F000012"], 0.96),
    ]
    diversified = service._diversify_ranked_frames(  # noqa: SLF001 - validates final presentation ordering.
        ranked,
        {
            "result_diversification": {
                "enabled": True,
                "max_frames_per_video": 2,
                "min_frame_gap_ms": 5000,
                "min_frame_gap": 1,
            }
        },
    )

    assert [item.frame.keyframe_id for item in diversified[:3]] == [
        "L30_V001_F000005",
        "L30_V002_F000012",
        "L30_V001_F000020",
    ]
    assert diversified[-1].frame.keyframe_id == "L30_V001_F000007"

    db.close()


def test_semantic_search_batches_variants_once_per_embedding_model(tmp_path: Path) -> None:
    db, service, _dataset = _build_fixture(tmp_path)

    class BatchOnlyEmbedder:
        def __init__(self) -> None:
            self.batches: list[list[str]] = []

        def embed_text(self, text: str) -> list[float]:
            raise AssertionError(f"Expected batch embedding, received scalar call for {text!r}")

        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            self.batches.append(texts)
            return [[1.0] for _ in texts]

    embedder = BatchOnlyEmbedder()
    service.model_registry.embedders = {"batch_model": embedder}  # type: ignore[dict-item]
    profile = {
        "visual_models": {
            "batch_model": {
                "model_key": "batch_model",
                "collection": "clip_vectors",
                "weight": 1.0,
                "enabled": True,
            }
        },
        "rrf": {"enabled": False, "k": 60},
    }

    scores, backend_error = service._semantic_scores(  # noqa: SLF001 - verifies batching boundary.
        variants=["presenter in studio", "television news set", "woman speaking"],
        top_k=3,
        dataset_video_ids=set(),
        profile=profile,
    )

    assert backend_error is False
    assert embedder.batches == [["presenter in studio", "television news set", "woman speaking"]]
    assert scores

    db.close()


def test_m4_weighted_and_rrf_profiles_change_expected_order(tmp_path: Path) -> None:
    db, service, dataset = _build_fixture(tmp_path)

    weighted_response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="KIS",
            query_name="m4-weighted",
            query_text="nguoi ao do",
            profile="m4_weighted",
            top_k=3,
            options=SearchOptions(use_query_expansion=False, use_agent_query_planning=False),
        )
    )
    rrf_response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="KIS",
            query_name="m4-rrf",
            query_text="nguoi ao do",
            profile="m4_rrf",
            top_k=3,
            options=SearchOptions(use_query_expansion=False, use_agent_query_planning=False),
        )
    )

    assert weighted_response.results[0].frame_id == "L30_V001_F000020"
    assert rrf_response.results[0].frame_id == "L30_V001_F000005"
    assert weighted_response.results[0].score_breakdown["rrf_score"] == 0
    assert rrf_response.results[0].score_breakdown["rrf_score"] > 0

    db.close()


def test_agent_text_weight_can_override_visual_profile_for_mixed_evidence(tmp_path: Path) -> None:
    db, service, dataset = _build_fixture(tmp_path)

    class TextFirstPlanner:
        def plan(self, query: str, query_type: str, max_variants: int) -> QueryPlanningResult:
            return QueryPlanningResult(
                language="en",
                intent=query_type,
                summary=query,
                variants=[query],
                temporal_events=[query],
                retrieval_weights={"visual": 0.1, "text": 0.9},
                retrieval_weight_source="agent",
                decomposition={"search_factors": {}, "retrieval_strategy": {"clauses": []}},
                source="langchain_deep_agent",
            )

    service.query_planner = TextFirstPlanner()  # type: ignore[assignment]
    response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="KIS",
            query_name="agent-text-first",
            query_text="person says red shirt",
            profile="m4_weighted",
            top_k=3,
            options=SearchOptions(use_query_expansion=False, use_agent_query_planning=True),
        )
    )

    assert response.results[0].frame_id == "L30_V001_F000005"
    assert response.normalized_query["retrieval_weights"] == {"visual": 0.1, "text": 0.9}
    assert response.normalized_query["retrieval_weight_source"] == "agent"
    assert response.results[0].score_breakdown["retrieval_weight_source"] == "agent"

    db.close()


def test_m4_filters_remove_out_of_scope_results_and_include_debug_metadata(tmp_path: Path) -> None:
    db, service, dataset = _build_fixture(tmp_path)

    response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="KIS",
            query_name="m4-filtered",
            query_text="nguoi ao do",
            profile="m4_weighted",
            top_k=10,
            options=SearchOptions(
                use_query_expansion=False,
                video_codes=["L30_V001"],
                time_range_start_seconds=0.0,
                time_range_end_seconds=10.0,
                objects=["person"],
                scene="ao do",
                debug_filters=True,
            ),
        )
    )

    assert len(response.results) == 1
    assert response.results[0].frame_id == "L30_V001_F000005"
    filter_debug = response.results[0].score_breakdown["filter_debug"]
    assert filter_debug["matched"]["video_code"] == "L30_V001"
    assert "person" in filter_debug["matched"]["objects"]
    assert filter_debug["matched"]["scene"] == "ao do"

    db.close()


def test_m4_use_metadata_false_skips_text_backend(tmp_path: Path) -> None:
    db, service, dataset = _build_fixture(tmp_path)
    counting_text_client = StubTextClient()
    service.text_client = counting_text_client

    response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="KIS",
            query_name="m4-no-metadata",
            query_text="nguoi ao do",
            profile="m4_rrf",
            top_k=3,
            options=SearchOptions(use_query_expansion=False, use_metadata=False),
        )
    )

    assert counting_text_client.search_calls == 0
    assert response.results[0].frame_id == "L30_V001_F000020"
    assert response.results[0].score_breakdown["text_score"] == 0

    db.close()


def test_m4_strict_hybrid_returns_error_when_semantic_backend_fails(tmp_path: Path) -> None:
    db, service, dataset = _build_fixture(tmp_path)

    class BrokenVectorClient(StubVectorClient):
        def search(self, collection: str, vector: list[float], top_k: int, filters: dict | None = None) -> list[VectorHit]:
            _ = (collection, vector, top_k, filters)
            raise RuntimeError("milvus unavailable")

    service.vector_client = BrokenVectorClient()

    with pytest.raises(ValueError, match="strict_hybrid is enabled and backend retrieval failed"):
        service.search(
            SearchRequest(
                dataset_id=dataset.dataset_id,
                query_type="KIS",
                query_name="m4-strict-hybrid",
                query_text="nguoi ao do",
                profile="m4_weighted",
                top_k=3,
                options=SearchOptions(use_query_expansion=False, strict_hybrid=True),
            )
        )

    db.close()


def test_m4_strict_hybrid_disables_fallback_when_no_candidates(tmp_path: Path) -> None:
    db, service, dataset = _build_fixture(tmp_path)

    class EmptyVectorClient(StubVectorClient):
        def search(self, collection: str, vector: list[float], top_k: int, filters: dict | None = None) -> list[VectorHit]:
            _ = (collection, vector, top_k, filters)
            return []

    class EmptyTextClient(StubTextClient):
        def search(self, index: str, query: str, top_k: int, boosts: dict[str, float] | None = None, source_types: list[str] | None = None) -> list[TextHit]:
            _ = (index, query, top_k, boosts, source_types)
            self.search_calls += 1
            return []

    service.vector_client = EmptyVectorClient()
    service.text_client = EmptyTextClient()

    with pytest.raises(ValueError, match="fallback ranking is disabled"):
        service.search(
            SearchRequest(
                dataset_id=dataset.dataset_id,
                query_type="KIS",
                query_name="m4-strict-empty",
                query_text="nguoi ao do",
                profile="m4_weighted",
                top_k=3,
                options=SearchOptions(use_query_expansion=False, strict_hybrid=True),
            )
        )

    db.close()

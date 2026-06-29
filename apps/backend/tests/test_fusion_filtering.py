from __future__ import annotations

from pathlib import Path
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.adapters.model_runtime.mock import MockEmbedder, MockQueryExpander, MockVisualQaModel
from app.adapters.text_search.base import TextHit
from app.adapters.vector_db.base import VectorHit
from app.db.models import Base, Dataset, Frame, FrameAnnotation, Shot, Video
from app.modules.models.service import ModelRegistryService
from app.modules.retrieval.schemas import SearchOptions, SearchRequest
from app.modules.retrieval.service import RetrievalService


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
    def search(self, index: str, query: str, top_k: int, boosts: dict[str, float] | None = None) -> list[TextHit]:
        _ = (index, query, boosts)
        hits = [
            TextHit(id="L30_V001_F000005", score=10.0, metadata={"keyframe_id": "L30_V001_F000005", "video_id": "L30_V001"}),
            TextHit(id="L30_V002_F000012", score=8.0, metadata={"keyframe_id": "L30_V002_F000012", "video_id": "L30_V002"}),
            TextHit(id="L30_V001_F000020", score=1.0, metadata={"keyframe_id": "L30_V001_F000020", "video_id": "L30_V001"}),
        ]
        return hits[:top_k]

    def upsert(self, index: str, documents: list[tuple[str, dict]]) -> int:
        _ = (index, documents)
        return len(documents)


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
        embedder=MockEmbedder(dim=64),
        query_expander=MockQueryExpander(),
        visual_qa=MockVisualQaModel(),
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
            options=SearchOptions(use_query_expansion=False),
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
            options=SearchOptions(use_query_expansion=False),
        )
    )

    assert weighted_response.results[0].frame_id == "L30_V001_F000020"
    assert rrf_response.results[0].frame_id == "L30_V001_F000005"
    assert weighted_response.results[0].score_breakdown["rrf_score"] == 0
    assert rrf_response.results[0].score_breakdown["rrf_score"] > 0

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

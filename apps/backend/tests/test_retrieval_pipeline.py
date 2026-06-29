from __future__ import annotations

from pathlib import Path
import sys

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.adapters.model_runtime.mock import MockEmbedder, MockQueryExpander, MockVisualQaModel
from app.adapters.text_search.mock import InMemoryTextSearchClient
from app.adapters.vector_db.mock import InMemoryVectorSearchClient
from app.db.models import Base, Dataset, Frame, FrameAnnotation, QueryRun, RetrievalResult, Shot, Video
from app.modules.models.service import ModelRegistryService
from app.modules.retrieval.router import search as search_endpoint
from app.modules.retrieval.schemas import SearchOptions, SearchRequest
from app.modules.retrieval.service import RetrievalService


def _build_retrieval_fixture(tmp_path: Path) -> tuple[Session, RetrievalService, Dataset, Frame, Frame]:
    db_path = tmp_path / "retrieval.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()

    dataset = Dataset(
        dataset_code="demo-m3",
        name="demo-m3",
        version="v1",
        root_uri="file:///demo",
        status="READY",
    )
    session.add(dataset)
    session.flush()

    video = Video(
        video_id="L30_V001",
        dataset_id=dataset.dataset_id,
        video_code="L30_V001",
        video_name="L30_V001.mp4",
        uri="file:///demo/L30_V001.mp4",
        fps=25.0,
        duration_seconds=10.0,
        duration_ms=10000,
    )
    session.add(video)
    session.flush()

    shot = Shot(
        shot_id="L30_V001_S0000",
        video_id=video.video_id,
        shot_index=0,
        start_frame=0,
        end_frame=250,
        start_seconds=0.0,
        end_seconds=10.0,
        boundary_threshold=0.5,
    )
    session.add(shot)
    session.flush()

    frame_a = Frame(
        keyframe_id="L30_V001_F000010",
        video_id=video.video_id,
        shot_id=shot.shot_id,
        frame_idx=10,
        frame_seconds=0.4,
        timestamp_ms=400,
        image_uri="/tmp/L30_V001_F000010.jpg",
        quality_score=0.95,
    )
    frame_b = Frame(
        keyframe_id="L30_V001_F000025",
        video_id=video.video_id,
        shot_id=shot.shot_id,
        frame_idx=25,
        frame_seconds=1.0,
        timestamp_ms=1000,
        image_uri="/tmp/L30_V001_F000025.jpg",
        quality_score=0.4,
    )
    session.add_all([frame_a, frame_b])
    session.flush()

    session.add_all(
        [
            FrameAnnotation(
                frame_id=frame_a.keyframe_id,
                kind="MULTIMODAL",
                text_value="nguoi mac ao do dang di bo tren duong pho",
                caption="nguoi ao do",
                ocr_texts=["duong pho"],
                detected_objects=["person"],
                json_value={"answer_hint": "Ao do"},
            ),
            FrameAnnotation(
                frame_id=frame_b.keyframe_id,
                kind="MULTIMODAL",
                text_value="xe may chay tren duong",
                caption="xe may",
                ocr_texts=["ban tin"],
                detected_objects=["motorbike"],
                json_value={},
            ),
        ]
    )
    session.commit()

    embedder = MockEmbedder(dim=64)
    model_registry = ModelRegistryService(
        embedder=embedder,
        query_expander=MockQueryExpander(),
        visual_qa=MockVisualQaModel(),
    )
    vector_client = InMemoryVectorSearchClient()
    text_client = InMemoryTextSearchClient()

    vector_client.upsert(
        "keyframe_embeddings",
        [
            (
                frame_a.keyframe_id,
                embedder.embed_text("nguoi ao do"),
                {"keyframe_id": frame_a.keyframe_id, "video_id": video.video_id, "frame_idx": frame_a.frame_idx},
            ),
            (
                frame_b.keyframe_id,
                embedder.embed_text("xe may"),
                {"keyframe_id": frame_b.keyframe_id, "video_id": video.video_id, "frame_idx": frame_b.frame_idx},
            ),
        ],
    )
    text_client.upsert(
        "keyframe_annotations",
        [
            (
                frame_a.keyframe_id,
                {
                    "keyframe_id": frame_a.keyframe_id,
                    "video_id": video.video_id,
                    "caption": "nguoi mac ao do dang di bo",
                    "ocr_texts": "duong pho",
                    "detected_objects": "person",
                },
            ),
            (
                frame_b.keyframe_id,
                {
                    "keyframe_id": frame_b.keyframe_id,
                    "video_id": video.video_id,
                    "caption": "xe may tren duong",
                    "ocr_texts": "ban tin",
                    "detected_objects": "motorbike",
                },
            ),
        ],
    )

    service = RetrievalService(
        db=session,
        model_registry=model_registry,
        vector_client=vector_client,
        text_client=text_client,
    )
    return session, service, dataset, frame_a, frame_b


def test_m3_search_returns_hybrid_scores_and_persists_run(tmp_path: Path) -> None:
    db, service, dataset, frame_a, _ = _build_retrieval_fixture(tmp_path)

    response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="KIS",
            query_name="m3-kis-smoke",
            query_text="nguoi ao do",
            top_k=2,
            options=SearchOptions(use_query_expansion=False),
        )
    )

    assert len(response.results) == 2
    assert response.results[0].frame_id == frame_a.keyframe_id
    assert response.results[0].score_breakdown.keys() >= {
        "semantic_score",
        "text_score",
        "quality_score",
        "final_score",
    }
    assert response.results[0].score_breakdown["final_score"] == pytest.approx(response.results[0].score)
    assert isinstance(response.normalized_query.get("latency_ms"), int)

    run = db.query(QueryRun).one()
    stored_results = db.query(RetrievalResult).filter(RetrievalResult.query_run_id == run.id).all()
    ranks = [result.rank for result in stored_results]

    assert run.status == "DONE"
    assert isinstance(run.options.get("latency_ms"), int)
    assert isinstance((run.normalized_query or {}).get("latency_ms"), int)
    assert len(stored_results) == 2
    assert len(ranks) == len(set(ranks))

    loaded_run = service.get_run(response.query_run_id)
    assert loaded_run.query_run_id == response.query_run_id
    assert isinstance(loaded_run.normalized_query.get("latency_ms"), int)

    db.close()


def test_m3_qa_smoke_returns_answer(tmp_path: Path) -> None:
    db, service, dataset, frame_a, _ = _build_retrieval_fixture(tmp_path)

    response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="QA",
            query_name="m3-qa-smoke",
            query_text="nguoi nay mac ao mau gi",
            top_k=1,
            options=SearchOptions(use_query_expansion=False),
        )
    )

    assert len(response.results) == 1
    assert response.results[0].frame_id == frame_a.keyframe_id
    assert response.results[0].answer == "Ao do"
    assert len(response.results[0].answer or "") <= 100

    db.close()


def test_m3_blank_query_returns_http_400(tmp_path: Path) -> None:
    db, service, dataset, _, _ = _build_retrieval_fixture(tmp_path)

    request = SearchRequest(
        dataset_id=dataset.dataset_id,
        query_type="KIS",
        query_name="m3-invalid",
        query_text="   ",
        top_k=1,
        options=SearchOptions(use_query_expansion=False),
    )

    with pytest.raises(HTTPException) as excinfo:
        search_endpoint(
            request=request,
            db=db,
            model_registry=service.model_registry,
            vector_client=service.vector_client,
            text_client=service.text_client,
        )

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "query_text must not be empty"
    db.close()


def test_m3_invalid_time_range_returns_http_400(tmp_path: Path) -> None:
    db, service, dataset, _, _ = _build_retrieval_fixture(tmp_path)

    request = SearchRequest(
        dataset_id=dataset.dataset_id,
        query_type="KIS",
        query_name="m3-invalid-time-range",
        query_text="nguoi ao do",
        top_k=1,
        options=SearchOptions(
            use_query_expansion=False,
            time_range_start_seconds=2.0,
            time_range_end_seconds=1.0,
        ),
    )

    with pytest.raises(HTTPException) as excinfo:
        search_endpoint(
            request=request,
            db=db,
            model_registry=service.model_registry,
            vector_client=service.vector_client,
            text_client=service.text_client,
        )

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "options.time_range_start_seconds must be <= options.time_range_end_seconds"
    db.close()

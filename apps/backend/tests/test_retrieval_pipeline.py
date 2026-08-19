from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.models import Base, Dataset, Frame, FrameAnnotation, QueryRun, RetrievalResult, Shot, Video
from app.modules.models.service import ModelRegistryService
from app.modules.retrieval.query_planning import AgentQueryPlanner
from app.modules.retrieval.router import search as search_endpoint
from app.modules.retrieval.schemas import SearchOptions, SearchRequest
from app.modules.retrieval.service import RetrievalService
from tests.fakes import DeterministicEmbedder, ExpandingQueryExpander, HintVisualQaModel, InMemoryTextSearchClient, InMemoryVectorSearchClient


class PreferenceReranker:
    def __init__(self, preferred_token: str) -> None:
        self.preferred_token = preferred_token.lower()
        self.backend = "fake"

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        scores: list[float] = []
        for passage in passages:
            score = 0.1
            if self.preferred_token in passage.lower():
                score = 0.95
            elif query.lower().split()[0] in passage.lower():
                score = 0.5
            scores.append(score)
        return scores


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

    embedder = DeterministicEmbedder(dim=64)
    model_registry = ModelRegistryService(
        embedder=embedder,
        query_expander=ExpandingQueryExpander(),
        visual_qa=HintVisualQaModel(),
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


def test_semantic_numeric_keyframe_id_uses_local_map_keyframes(tmp_path: Path) -> None:
    db, service, dataset, _frame_a, frame_b = _build_retrieval_fixture(tmp_path)
    map_dir = tmp_path / "map-keyframes"
    map_dir.mkdir()
    (map_dir / "L30_V001.csv").write_text(
        "n,pts_time,fps,frame_idx\n"
        "1,0.4,25,10\n"
        "2,1.0,25,25\n",
        encoding="utf-8",
    )
    service.settings = replace(service.settings, data_root=tmp_path)
    embedder = service.model_registry.embedder
    service.vector_client.upsert(
        "keyframe_embeddings_siglip2_base_patch16_256",
        [
            (
                "L30_V001_002",
                embedder.embed_text("rocket astronauts"),
                {"video_id": "L30_V001", "keyframe_number": 2},
            )
        ],
    )

    response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="KIS",
            query_name="numeric-map",
            query_text="rocket astronauts",
            top_k=1,
            options=SearchOptions(use_query_expansion=False, use_metadata=False, use_reranker=False),
        )
    )

    assert response.results[0].frame_id == frame_b.keyframe_id
    semantic_hit = response.results[0].score_breakdown["semantic_hit"]
    assert semantic_hit["source_keyframe_id"] == "L30_V001_002"
    assert semantic_hit["map_keyframe"]["n"] == 2
    assert semantic_hit["map_keyframe"]["frame_idx"] == 25

    db.close()


def test_m3_cross_encoder_reranker_can_reorder_results(tmp_path: Path) -> None:
    db, service, dataset, _frame_a, frame_b = _build_retrieval_fixture(tmp_path)
    service.model_registry.reranker = PreferenceReranker("xe may")
    service.profiles["rerank_smoke"] = {
        "semantic_weight": 0.1,
        "metadata_weight": 0.9,
        "temporal_weight": 0.0,
        "user_boost_weight": 0.0,
        "rrf": {"enabled": False, "k": 60},
        "query_expansion": {"enabled_default": False, "max_variants": 1},
        "reranking": {
            "enabled": True,
            "top_k": 2,
            "blend": 1.0,
            "cross_encoder_weight": 1.0,
            "mllm_weight": 0.0,
            "mllm": {"enabled": False, "top_k": 1},
        },
    }

    response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="KIS",
            query_name="m3-rerank-smoke",
            query_text="nguoi ao do",
            top_k=2,
            profile="rerank_smoke",
            options=SearchOptions(use_query_expansion=False, use_reranker=True),
        )
    )

    assert response.results[0].frame_id == frame_b.keyframe_id
    assert response.results[0].score_breakdown["rerank_score"] > response.results[1].score_breakdown["rerank_score"]

    db.close()


def test_m4_agent_query_planning_falls_back_and_simple_query_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_LLM_PROFILE", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    db, service, dataset, frame_a, _ = _build_retrieval_fixture(tmp_path)

    response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="KIS",
            query_name="m4-agent-fallback-smoke",
            query_text="nguoi ao do",
            top_k=1,
        )
    )

    assert response.results
    assert response.results[0].frame_id == frame_a.keyframe_id
    assert response.normalized_query["variants"][0] == "nguoi ao do"
    agent_plan = response.normalized_query["agent_query_plan"]
    assert agent_plan["source"] == "fallback"
    assert "missing GROQ_API_KEY" in agent_plan["error"]

    db.close()


def test_m4_agent_query_planning_openai_profile_falls_back_when_key_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_LLM_PROFILE", "openai_gpt4o")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    db, service, dataset, frame_a, _ = _build_retrieval_fixture(tmp_path)

    response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="KIS",
            query_name="m4-agent-openai-fallback-smoke",
            query_text="nguoi ao do",
            top_k=1,
        )
    )

    assert response.results
    assert response.results[0].frame_id == frame_a.keyframe_id
    agent_plan = response.normalized_query["agent_query_plan"]
    assert agent_plan["source"] == "fallback"
    assert agent_plan["error"] == "missing OPENAI_API_KEY"
    assert agent_plan["agent_metadata"]["active_profile"] == "openai_gpt4o"
    assert agent_plan["agent_metadata"]["provider"] == "openai"
    assert agent_plan["agent_metadata"]["model"] == "gpt-4o"

    db.close()


def test_m4_agent_profile_env_override_resolves_provider_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LLM_PROFILE", "openai_gpt4o")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config_path = Path(__file__).resolve().parents[3] / "configs" / "agent.yaml"
    planner = AgentQueryPlanner.from_config(config_path)

    result = planner.plan("nguoi ao do", "KIS", 5)

    assert result.source == "fallback"
    assert result.error == "missing OPENAI_API_KEY"
    assert result.agent_metadata["active_profile"] == "openai_gpt4o"
    assert result.agent_metadata["provider"] == "openai"
    assert result.agent_metadata["model"] == "gpt-4o"


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

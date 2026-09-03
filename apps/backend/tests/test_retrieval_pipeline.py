from __future__ import annotations

from dataclasses import replace
import json
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
from app.modules.retrieval.query_planning import AgentQueryPlanner, QueryPlanningResult
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


class StaticEmbedder:
    def __init__(self, vector: list[float]) -> None:
        self.vector = vector

    def embed_text(self, text: str) -> list[float]:
        return self.vector

    def embed_image_uri(self, image_uri: str) -> list[float]:
        return self.vector


class NoopPlanner:
    def plan(self, query: str, query_type: str, max_variants: int) -> QueryPlanningResult:
        return QueryPlanningResult(
            language="auto",
            intent=query_type,
            summary=query,
            variants=[query],
            temporal_events=[query],
            decomposition={"search_factors": {"tokens": query.split()}},
            agent_metadata={
                "active_profile": "test",
                "provider": "noop",
                "model": "noop-planner",
                "api_key_env": "NOOP_API_KEY",
                "api_key_configured": False,
            },
            source="fallback",
            error="test planner disabled",
        )


def _build_retrieval_fixture(
    tmp_path: Path,
    *,
    use_real_planner: bool = False,
) -> tuple[Session, RetrievalService, Dataset, Frame, Frame]:
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
                text_value="nguoi di xe may tren duong",
                caption="nguoi di xe may",
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
                    "caption": "nguoi di xe may tren duong",
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
    if not use_real_planner:
        service.query_planner = NoopPlanner()  # type: ignore[assignment]
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


@pytest.mark.parametrize("strategy", ["vortex_k_context", "aithena_weighted_ats"])
def test_kis_temporal_search_returns_the_agent_anchor_frame_with_context(
    tmp_path: Path,
    strategy: str,
) -> None:
    db, service, dataset, frame_a, frame_b = _build_retrieval_fixture(tmp_path)

    response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="KIS",
            query_name="temporal-kis",
            query_text="nguoi ao do then xe may",
            top_k=5,
            options=SearchOptions(
                use_query_expansion=False,
                temporal_mode=True,
                temporal_strategy=strategy,  # type: ignore[arg-type]
                temporal_events=["nguoi ao do", "xe may"],
                temporal_anchor_index=1,
                min_match=2,
            ),
        )
    )

    assert response.normalized_query["temporal_mode"] is True
    assert response.normalized_query["temporal_strategy"] == strategy
    assert response.normalized_query["temporal_anchor_index"] == 1
    assert response.results
    assert response.results[0].frame_id == frame_a.keyframe_id
    assert [item["frame_idx"] for item in response.results[0].sequence_frames] == [
        frame_a.frame_idx,
        frame_b.frame_idx,
    ]
    assert response.results[0].score_breakdown["temporal_reranker"] == strategy
    db.close()


def test_kis_temporal_parser_overrides_an_incomplete_agent_event_plan(tmp_path: Path) -> None:
    db, service, dataset, _frame_a, _frame_b = _build_retrieval_fixture(tmp_path)

    response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="KIS",
            query_name="temporal-kis-parse",
            query_text="Find the oil-contact moment after batter is added and before asparagus is removed.",
            top_k=3,
            options=SearchOptions(
                use_query_expansion=False,
                temporal_mode=True,
                temporal_strategy="vortex_k_context",
            ),
        )
    )

    assert response.normalized_query["temporal_event_source"] == "context_relation"
    assert response.normalized_query["temporal_events"] == [
        "batter is added",
        "Find the oil-contact moment",
        "asparagus is removed",
    ]
    assert response.normalized_query["temporal_anchor_index"] == 2
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
        "keyframe_embeddings_clip_vith14_quickgelu_dfn5b_v2",
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


def test_visual_rrf_fuses_clip_and_siglip_ranked_lists(tmp_path: Path) -> None:
    db, service, dataset, frame_a, frame_b = _build_retrieval_fixture(tmp_path)
    clip_key = "clip_vith14_quickgelu_dfn5b_v2"
    siglip_key = "siglip2_so400m16_384_webli_openclip_1152_v1"
    clip_collection = "keyframe_embeddings_clip_vith14_quickgelu_dfn5b_v2"
    siglip_collection = "keyframe_embeddings_siglip2_so400m16_384_webli_openclip_1152_v1"
    service.model_registry.embedder = StaticEmbedder([1.0, 0.0])
    service.model_registry.embedders = {
        clip_key: StaticEmbedder([1.0, 0.0]),
        siglip_key: StaticEmbedder([0.0, 1.0]),
    }
    service.vector_client.upsert(
        clip_collection,
        [
            (frame_a.keyframe_id, [1.0, 0.0], {"keyframe_id": frame_a.keyframe_id, "video_id": frame_a.video_id}),
            (frame_b.keyframe_id, [0.8, 0.2], {"keyframe_id": frame_b.keyframe_id, "video_id": frame_b.video_id}),
        ],
    )
    service.vector_client.upsert(
        siglip_collection,
        [
            (frame_a.keyframe_id, [0.2, 0.8], {"keyframe_id": frame_a.keyframe_id, "video_id": frame_a.video_id}),
            (frame_b.keyframe_id, [0.0, 1.0], {"keyframe_id": frame_b.keyframe_id, "video_id": frame_b.video_id}),
        ],
    )
    service.profiles["visual_rrf_smoke"] = {
        "semantic_weight": 1.0,
        "metadata_weight": 0.0,
        "user_boost_weight": 0.0,
        "rrf": {"enabled": False, "k": 60},
        "visual_rrf": {"enabled": True, "k": 1},
        "query_expansion": {"enabled_default": False, "max_variants": 1},
        "visual_models": {
            "clip": {
                "collection": clip_collection,
                "model_key": clip_key,
                "weight": 1.0,
                "enabled": True,
            },
            "siglip2": {
                "collection": siglip_collection,
                "model_key": siglip_key,
                "weight": 2.0,
                "enabled": True,
            },
        },
    }

    response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="KIS",
            query_name="visual-rrf",
            query_text="a visual query",
            top_k=2,
            profile="visual_rrf_smoke",
            options=SearchOptions(use_query_expansion=False, use_metadata=False, use_reranker=False),
        )
    )

    assert response.results[0].frame_id == frame_b.keyframe_id
    semantic_hit = response.results[0].score_breakdown["semantic_hit"]
    assert semantic_hit["fusion"] == "visual_rrf"
    assert {source["model_key"] for source in semantic_hit["sources"]} == {clip_key, siglip_key}
    assert response.results[0].score_breakdown["semantic_score"] > 0

    db.close()


def test_m4_agent_query_planning_falls_back_and_simple_query_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_LLM_PROFILE", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    db, service, dataset, frame_a, _ = _build_retrieval_fixture(tmp_path, use_real_planner=True)

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
    assert response.normalized_query["semantic_variants"] == ["person wearing a red shirt"]
    assert response.normalized_query["text_variants"] == ["nguoi ao do"]
    agent_plan = response.normalized_query["agent_query_plan"]
    assert agent_plan["source"] == "fallback"
    assert "missing OPENAI_API_KEY" in agent_plan["error"]

    db.close()


def test_m4_agent_query_planning_runs_even_when_expansion_is_disabled(tmp_path: Path) -> None:
    db, service, dataset, frame_a, _ = _build_retrieval_fixture(tmp_path)

    class FakePlanner:
        def plan(self, query: str, query_type: str, max_variants: int) -> QueryPlanningResult:
            return QueryPlanningResult(
                language="vi",
                intent=query_type,
                summary="person in red shirt walking",
                variants=[query, "red shirt person walking"],
                text_variants=["nguoi ao do", "ao do"],
                temporal_events=["person in red shirt walking"],
                decomposition={"search_factors": {"objects": ["person"], "actions": ["walking"]}},
                agent_metadata={
                    "active_profile": "test",
                    "provider": "fake",
                    "model": "fake-planner",
                    "api_key_env": "FAKE_API_KEY",
                    "api_key_configured": True,
                },
                source="langchain_deep_agent",
            )

    service.query_planner = FakePlanner()  # type: ignore[assignment]
    response = service.search(
        SearchRequest(
            dataset_id=dataset.dataset_id,
            query_type="KIS",
            query_name="m4-agent-no-expansion",
            query_text="nguoi ao do",
            top_k=1,
            options=SearchOptions(use_query_expansion=False, use_agent_query_planning=True),
        )
    )

    assert response.results
    assert response.results[0].frame_id == frame_a.keyframe_id
    assert response.normalized_query["semantic_variants"] == ["red shirt person walking"]
    assert response.normalized_query["text_variants"] == ["nguoi ao do", "ao do"]
    assert response.normalized_query["temporal_events"] == ["person in red shirt walking"]
    agent_plan = response.normalized_query["agent_query_plan"]
    assert agent_plan["source"] == "langchain_deep_agent"
    assert agent_plan["variants"] == ["nguoi ao do", "red shirt person walking"]
    assert agent_plan["agent_metadata"]["api_key_configured"] is True

    db.close()


def test_m4_agent_query_planning_openai_profile_falls_back_when_key_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_LLM_PROFILE", "openai_gpt4o")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    db, service, dataset, frame_a, _ = _build_retrieval_fixture(tmp_path, use_real_planner=True)

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
    assert result.variants[:2] == ["person wearing a red shirt", "nguoi ao do"]
    assert result.error == "missing OPENAI_API_KEY"
    assert result.agent_metadata["active_profile"] == "openai_gpt4o"
    assert result.agent_metadata["provider"] == "openai"
    assert result.agent_metadata["model"] == "gpt-4o"


def test_agent_planner_promotes_english_rewrite_when_agent_returns_raw_vietnamese() -> None:
    planner = AgentQueryPlanner(config={"llm_query_planning": {"enabled": False}})
    query = "dan ho o mien Nam co them vai con ho con moi sinh"

    result = planner._result_from_raw_plan(  # noqa: SLF001 - validates runtime hardening.
        raw_plan={
            "language": "vi",
            "intent": "KIS",
            "summary": query,
            "temporal_events": [{"query": query}],
            "variants": [{"text": query}],
        },
        query=query,
        query_type="KIS",
        max_variants=3,
    )

    assert result.summary == "news segment about a tiger family in southern Vietnam with newborn tiger cubs, rare tiger species"
    assert result.variants[:2] == [
        "news segment about a tiger family in southern Vietnam with newborn tiger cubs, rare tiger species",
        query,
    ]
    assert result.temporal_events[:2] == [
        "news segment about a tiger family in southern Vietnam with newborn tiger cubs, rare tiger species",
        query,
    ]


def test_agent_planner_repairs_vietnamese_temporal_events_for_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    planner = AgentQueryPlanner(config={"llm_query_planning": {"enabled": False}})
    vietnamese_events = [
        "Kho\u1ea3nh kh\u1eafc b\u1ed9t \u0111\u01b0\u1ee3c b\u1ecf v\u00e0o t\u00f4 m\u0103ng t\u00e2y",
        "Kho\u1ea3nh kh\u1eafc mi\u1ebfng m\u0103ng t\u00e2y r\u1eddi kh\u1ecfi ch\u1ea3o",
    ]
    english_events = [
        "batter is added to a bowl of asparagus",
        "a piece of asparagus is removed from the pan",
    ]

    def translate(values: list[str]) -> list[str]:
        return english_events if len(values) == len(vietnamese_events) else ["asparagus cooking"]

    monkeypatch.setattr(planner, "_translate_vietnamese_values", translate)
    result = planner._result_from_raw_plan(  # noqa: SLF001 - verifies routing after an LLM repair.
        raw_plan={
            "language": "vi",
            "intent": "TRAKE",
            "temporal_events": [{"query": event} for event in vietnamese_events],
            "variants": [{"text": vietnamese_events[0]}],
        },
        query="\n".join(f"E{index}: {event}" for index, event in enumerate(vietnamese_events, start=1)),
        query_type="TRAKE",
        max_variants=3,
    )

    assert result.temporal_events == english_events
    assert result.variants
    assert all(not planner._looks_vietnamese(variant) for variant in result.variants)  # noqa: SLF001


def test_agent_planner_extracts_evidence_driven_visual_and_text_weights() -> None:
    planner = AgentQueryPlanner(config={"llm_query_planning": {"enabled": False}})

    result = planner._result_from_raw_plan(  # noqa: SLF001 - validates plan normalization.
        raw_plan={
            "summary": "find a person speaking the phrase red bicycle",
            "variants": [{"text": "person speaking the phrase red bicycle"}],
            "text_variants": ["nguoi noi xe dap do", "xe dap do"],
            "retrieval_strategy": {
                "clauses": [
                    {"text": "a person is visible", "evidence": "visual", "importance": 0.3},
                    {"text": "the spoken phrase red bicycle", "evidence": "text", "importance": 0.9},
                ],
                "weights": {"visual": 2, "text": 8},
                "rationale": "The exact spoken phrase needs ASR evidence.",
            },
        },
        query="find the person saying red bicycle",
        query_type="KIS",
        max_variants=3,
    )

    assert result.retrieval_weights == {"visual": 0.2, "text": 0.8}
    assert result.text_variants == ["find the person saying red bicycle", "nguoi noi xe dap do", "xe dap do"]
    strategy = result.decomposition["retrieval_strategy"]
    assert strategy["clauses"][1]["evidence"] == "text"
    assert strategy["rationale"] == "The exact spoken phrase needs ASR evidence."


def test_agent_planner_extracts_text_source_weights_for_asr_caption_and_ocr() -> None:
    planner = AgentQueryPlanner(config={"llm_query_planning": {"enabled": False}})

    result = planner._result_from_raw_plan(  # noqa: SLF001 - validates source-aware plan normalization.
        raw_plan={
            "summary": "a cook adds asparagus to a bowl beside the number 15",
            "variants": [{"text": "cook adds asparagus to a bowl"}],
            "text_variants": ["dau bep cho mang tay vao to", "so 15"],
            "retrieval_strategy": {
                "weights": {"visual": 0.4, "text": 0.6},
                "text_source_weights": {"asr": 1, "caption": 6, "ocr": 3},
            },
            "temporal_events": [
                {
                    "query": "cook adds asparagus to a bowl",
                    "text_query": "dau bep cho mang tay vao to",
                    "retrieval_weights": {"visual": 0.6, "text": 0.4},
                    "text_source_weights": {"asr": 0.1, "caption": 0.8, "ocr": 0.1},
                }
            ],
        },
        query="tim dau bep cho mang tay vao to co so 15",
        query_type="TRAKE",
        max_variants=3,
    )

    assert result.text_source_weights == {"asr": 0.1, "caption": 0.6, "ocr": 0.3}
    assert result.temporal_event_plans[0]["text_source_weights"] == {"asr": 0.1, "caption": 0.8, "ocr": 0.1}
    assert result.temporal_event_plans[0]["text_query"] == "dau bep cho mang tay vao to"
    assert result.decomposition["retrieval_strategy"]["text_source_weight_source"] == "agent"


def test_metadata_query_cues_preserve_exact_ocr_terms() -> None:
    assert RetrievalService._metadata_query_cues('tim chu "HOME" va ma AIC-2025') == ["HOME", "AIC-2025"]


def test_planner_fallback_routes_benchmark_style_fact_queries_to_text_and_trake_actions_to_visual() -> None:
    planner = AgentQueryPlanner(config={"llm_query_planning": {"enabled": False}})

    fact_plan = planner.plan(
        "Hỏi tên xã của chương trình từ thiện tại Khánh Hòa vào năm 2024 là gì?",
        "QA",
        5,
    )
    trake_plan = planner.plan(
        "E1: The first moment a mushroom is cut. E2: The first moment a pan is placed on the stove.",
        "TRAKE",
        5,
    )

    assert fact_plan.retrieval_weight_source == "heuristic"
    assert fact_plan.retrieval_weights["text"] > fact_plan.retrieval_weights["visual"]
    assert trake_plan.retrieval_weights["visual"] > trake_plan.retrieval_weights["text"]
    assert all(
        event["retrieval_weights"]["visual"] > event["retrieval_weights"]["text"]
        for event in trake_plan.temporal_event_plans
    )


def test_agent_fallback_splits_labeled_trake_events() -> None:
    planner = AgentQueryPlanner(config={"llm_query_planning": {"enabled": False, "max_temporal_events": 8}})
    events = [
        "The moment the batter is added to the bowl of asparagus",
        "The moment the first piece of asparagus makes contact with the oil in the pan",
        "The moment the first piece of asparagus is removed from the pan",
        "The moment the last piece of asparagus leaves the pan and rests completely on the plate",
    ]
    query = "\n".join(f"E{index}: {event}." for index, event in enumerate(events, start=1))

    result = planner.plan(query, "TRAKE", 5)

    assert result.temporal_events == events
    assert query not in result.temporal_events


def test_agent_fallback_splits_vietnamese_temporal_connectors() -> None:
    planner = AgentQueryPlanner(config={"llm_query_planning": {"enabled": False, "max_temporal_events": 8}})
    query = (
        "Đoạn phim bắt đầu bằng một bản đồ, trên đó một loại công trình thủy lợi xuất hiện bốn lần. "
        "Sau đó chuyển sang cảnh một con đập được quay từ trên cao, tiếp đến là cảnh cận con đập dưới trời mưa."
    )

    result = planner.plan(query, "KIS", 3, temporal_kis=True)

    assert len(result.temporal_events) == 3
    assert "bản đồ" in result.temporal_events[0]
    assert "con đập được quay từ trên cao" in result.temporal_events[1]
    assert "cận con đập dưới trời mưa" in result.temporal_events[2]


def test_temporal_kis_repairs_an_agent_plan_that_collapses_ordered_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    planner = AgentQueryPlanner(
        config={
            "llm_query_planning": {
                "enabled": True,
                "active_profile": "test",
                "execution_mode": "direct",
                "max_temporal_events": 8,
                "profiles": {"test": {"provider": "openai", "model": "test-model"}},
            }
        }
    )
    query = "The video starts with a map, then shows a dam from above, then a close-up dam in rain."
    initial = json.dumps(
        {
            "summary": query,
            "variants": [{"text": query}],
            "temporal_events": [{"query": query, "text_query": ""}],
        }
    )
    repaired = json.dumps(
        {
            "summary": "map, aerial dam, close-up dam in rain",
            "variants": [{"text": "map waterworks"}],
            "temporal_events": [
                {"query": "map with waterworks", "text_query": "map waterworks"},
                {"query": "aerial view of a dam", "text_query": "aerial dam"},
                {"query": "close-up dam in rain", "text_query": "dam rain"},
            ],
            "temporal_anchor_index": 3,
        }
    )

    class RepairModel:
        def invoke(self, *_args: object, **_kwargs: object) -> str:
            return repaired

    monkeypatch.setattr(planner, "_invoke_agent", lambda **_kwargs: initial)
    monkeypatch.setattr(planner, "_get_model", lambda: RepairModel())
    monkeypatch.setattr(planner, "_unavailable_reason", lambda: None)

    result = planner.plan(query, "KIS", 3, temporal_kis=True)

    assert result.temporal_events == ["map with waterworks", "aerial view of a dam", "close-up dam in rain"]
    assert result.temporal_anchor_index == 3
    assert result.decomposition["temporal_repair_applied"] is True


def test_ocr_routing_requires_an_explicit_displayed_text_cue() -> None:
    planner = AgentQueryPlanner(config={"llm_query_planning": {"enabled": False}})

    visual = planner.infer_retrieval_strategy("bản đồ có kênh thủy lợi xuất hiện", "KIS")
    displayed_text = planner.infer_retrieval_strategy("tìm chữ và ký hiệu hiển thị trên bản đồ", "KIS")

    assert visual["text_source_weights"]["ocr"] == 0.0
    assert displayed_text["text_source_weights"]["ocr"] > 0.0


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

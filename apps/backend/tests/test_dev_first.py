from __future__ import annotations

from app.modules.temporal.dev_first import (
    DevFirstCandidate,
    build_dev_first_sequences,
    build_dev_first_vortex_ats_sequences,
    score_candidate_videos,
    score_candidate_videos_across_events,
    temporal_nms,
)
from app.modules.retrieval.schemas import SearchOptions, SearchRequest
from app.modules.retrieval.service import FrameScore
from app.modules.retrieval.temporal_query import parse_temporal_events
from tests.test_retrieval_pipeline import _build_retrieval_fixture


def candidate(video: str, event: int, timestamp: int, score: float, suffix: str = "") -> DevFirstCandidate:
    return DevFirstCandidate(
        frame_id=f"{video}-{event}-{timestamp}{suffix}", video_id=video, video_code=video.upper(),
        frame_idx=timestamp // 10, event_index=event, event_query=f"event {event}", timestamp_ms=timestamp,
        final_retrieval_score=score, calibrated_event_score=score,
    )


def config() -> dict:
    return {
        "sequence_beam_width": 40, "max_sequences_per_video": 2, "sequence_nms_window_ms": 1,
        "strong_match_threshold": 0.55,
        "temporal_gap_classes": {"short": {"max_gap_ms": 100}, "unknown": {"max_gap_ms": None}},
        "sequence_scoring": {
            "evidence_weight": .55, "coverage_weight": .20, "strong_coverage_weight": .10,
            "diagnostic_weight": .05, "missing_penalty_weight": .10, "temporal_gap_penalty_weight": 0,
        },
    }


def test_temporal_nms_is_per_event_per_video_and_uses_timestamp() -> None:
    kept = temporal_nms([candidate("a", 1, 1000, .9), candidate("a", 1, 1100, .8), candidate("a", 2, 1100, .8), candidate("b", 1, 1100, .8)], 500)
    assert {(item.video_id, item.event_index) for item in kept} == {("a", 1), ("a", 2), ("b", 1)}


def test_temporal_parser_splits_vietnamese_ordinal_event_cues() -> None:
    parsed = parse_temporal_events(
        "Đầu tiên người đầu bếp cho rau vào nồi. Sau đó đổ nấm. Cuối cùng thêm thịt.",
    )

    assert parsed.source == "soft_separators"
    assert parsed.events == [
        "người đầu bếp cho rau vào nồi",
        "đổ nấm",
        "thêm thịt",
    ]


def test_dev_sequence_prefers_strong_partial_over_weak_full_but_not_single_event() -> None:
    sequences = build_dev_first_sequences(
        [
            [candidate("partial", 1, 100, .95), candidate("full", 1, 100, .40), candidate("single", 1, 100, .99)],
            [candidate("partial", 2, 200, .95), candidate("full", 2, 200, .40)],
            [candidate("full", 3, 300, .40)],
        ], [1, 1, 1], 1,
        [{"from_event": 1, "to_event": 2, "gap_class": "short"}, {"from_event": 2, "to_event": 3, "gap_class": "short"}],
        config(), min_match=1, limit=10,
    )
    assert sequences[0].video_id == "partial"
    best = {video: max(item.score for item in sequences if item.video_id == video) for video in {item.video_id for item in sequences}}
    assert best["partial"] > best["full"] > best["single"]


def test_timestamp_constraint_does_not_use_frame_rate_assumption() -> None:
    sequences = build_dev_first_sequences(
        [[candidate("good", 1, 1000, .8), candidate("bad", 1, 1000, .9)], [candidate("good", 2, 1050, .8), candidate("bad", 2, 1200, .9)]],
        [1, 1], 1, [{"from_event": 1, "to_event": 2, "gap_class": "short"}], config(), min_match=2, limit=10,
    )
    assert [sequence.video_id for sequence in sequences] == ["good"]


def test_dev_vortex_ats_recovers_a_sequence_with_a_missing_event() -> None:
    sequences = build_dev_first_vortex_ats_sequences(
        [
            [candidate("target", 1, 1000, .90)],
            [],
            [candidate("target", 3, 3000, .90)],
        ],
        [1, 1, 1], 1,
        [], config(), min_match=2, limit=10, default_max_gap_ms=5000,
    )

    assert sequences
    assert [item.event_index for item in sequences[0].candidates] == [1, 3]
    assert sequences[0].details["sequence_constructor"] == "vortex_hard_anchor_then_ats"


def test_dev_vortex_ats_applies_timestamp_edges_after_ats() -> None:
    sequences = build_dev_first_vortex_ats_sequences(
        [
            [candidate("good", 1, 1000, .80), candidate("bad", 1, 1000, .95)],
            [candidate("good", 2, 1050, .80), candidate("bad", 2, 1200, .95)],
        ],
        [1, 1], 1,
        [{"from_event": 1, "to_event": 2, "gap_class": "short"}],
        config(), min_match=2, limit=10, default_max_gap_ms=100,
    )

    assert [sequence.video_id for sequence in sequences] == ["good"]


def test_video_scoring_does_not_reward_duplicate_count() -> None:
    ranked = score_candidate_videos([candidate("a", 1, 1000, .9), candidate("a", 1, 2000, .1), candidate("b", 1, 1000, .8)], {"video_scoring": {"best_weight": 1, "top_m_mean_weight": 0, "view_agreement_weight": 0, "top_m": 3}})
    assert ranked[0][0] == "a"


def test_cross_event_video_scoring_recovers_evidence_missed_by_generic_diagnostic() -> None:
    ranked = score_candidate_videos_across_events(
        [
            [candidate("generic", 1, 1000, .95), candidate("target", 1, 1000, .80)],
            [candidate("target", 2, 2000, .80)],
        ],
        diagnostic_index=1,
        config={"video_scoring": {"diagnostic_weight": .55, "cross_event_weight": .35, "coverage_weight": .10}},
    )

    assert ranked[0][0] == "target"


def test_dev_strategy_is_explicitly_dispatched_by_retrieval_service(tmp_path, monkeypatch) -> None:
    db, service, dataset, first, second = _build_retrieval_fixture(tmp_path)

    def fake_rank(**kwargs):
        semantic_views = kwargs["semantic_views"]
        frame = first if "first" in semantic_views[0] else second
        return [FrameScore(frame=frame, semantic_score=.8, text_score=.1, quality_score=.0, weighted_score=.8, rrf_score=.1, final_score=.8)]

    monkeypatch.setattr(service, "_rank_frames_multiperspective", fake_rank)
    response = service.search(SearchRequest(
        dataset_id=dataset.dataset_id, query_type="KIS", query_text="first then second", top_k=3,
        options=SearchOptions(use_query_expansion=False, temporal_mode=True, temporal_strategy="dev_first_search", temporal_events=["first", "second"], min_match=2),
    ))
    assert response.results
    assert response.results[0].score_breakdown["temporal_reranker"] == "dev_first_vortex_ats"
    assert response.results[0].score_breakdown["temporal_components"] == ["vortex_hard_anchor", "aithena_ats_recovery", "dev_sequence_score"]
    assert response.results[0].score_breakdown["temporal_coordinate"] == "timestamp_ms"
    assert [item["event_index"] for item in response.results[0].sequence_frames] == [1, 2]
    db.close()


def test_dev_frame_scope_returns_the_requested_anchor_not_the_best_event(tmp_path, monkeypatch) -> None:
    db, service, dataset, first, second = _build_retrieval_fixture(tmp_path)

    def fake_rank(**kwargs):
        semantic_views = kwargs["semantic_views"]
        frame = first if "first" in semantic_views[0] else second
        score = .95 if frame is first else .60
        return [FrameScore(frame=frame, semantic_score=score, text_score=.0, quality_score=.0, weighted_score=score, rrf_score=.0, final_score=score)]

    monkeypatch.setattr(service, "_rank_frames_multiperspective", fake_rank)
    response = service.search(SearchRequest(
        dataset_id=dataset.dataset_id, query_type="KIS", query_text="first then second", top_k=3,
        options=SearchOptions(
            use_query_expansion=False,
            temporal_mode=True,
            temporal_strategy="dev_first_search",
            temporal_events=["first", "second"],
            temporal_anchor_index=2,
            min_match=2,
        ),
    ))

    assert response.results[0].frame_id == second.keyframe_id
    assert response.results[0].score_breakdown["representative_frame_policy"] == "requested_anchor_event"
    db.close()

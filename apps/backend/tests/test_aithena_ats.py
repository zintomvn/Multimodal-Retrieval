from __future__ import annotations

import pytest

from app.modules.retrieval.temporal.ats import adaptive_temporal_search
from app.modules.retrieval.temporal.diversification import diversify_temporal_sequences
from app.modules.retrieval.temporal.types import Candidate, TemporalSequence
from app.modules.retrieval.temporal.vortex import vortex_k_context_rerank


def test_aithena_ats_reranks_temporally_valid_sequences_by_event_importance() -> None:
    candidate_sets = [
        [
            Candidate("v1-e1", "v1", "V1", 10, 0.2, "", event_index=1),
            Candidate("v2-e1", "v2", "V2", 10, 0.9, "", event_index=1),
        ],
        [
            Candidate("v1-e2", "v1", "V1", 20, 0.9, "", event_index=2),
            Candidate("v2-e2", "v2", "V2", 20, 0.2, "", event_index=2),
        ],
    ]

    sequences = adaptive_temporal_search(
        candidate_sets=candidate_sets,
        weights=[0.1, 0.9],
        delta_frame_max=30,
        min_match=2,
    )

    assert sequences[0].video_id == "v1"
    assert abs(sequences[0].score - 0.415) < 1e-9
    assert [candidate.frame_idx for candidate in sequences[0].candidates] == [10, 20]


def test_vortex_k_context_extends_previous_current_next_to_a_full_event_chain() -> None:
    candidate_sets = [
        [Candidate("v1-e1", "v1", "V1", 10, 0.6, "", event_index=1)],
        [Candidate("v1-e2", "v1", "V1", 30, 0.7, "", event_index=2)],
        [Candidate("v1-e3", "v1", "V1", 50, 0.8, "", event_index=3)],
        [Candidate("v1-e4", "v1", "V1", 70, 0.9, "", event_index=4)],
    ]

    sequences = vortex_k_context_rerank(
        candidate_sets=candidate_sets,
        weights=[1.0, 1.0, 1.0, 1.0],
        anchor_index=2,
        delta_frame_max=30,
    )

    assert [candidate.frame_idx for candidate in sequences[0].candidates] == [10, 30, 50, 70]
    assert sequences[0].score == pytest.approx(3.0)


def test_temporal_compactness_prefers_a_tighter_equally_valid_sequence() -> None:
    candidate_sets = [
        [
            Candidate("v1-e1", "v1", "V1", 10, 0.92, "", event_index=1),
            Candidate("v2-e1", "v2", "V2", 40, 0.88, "", event_index=1),
        ],
        [
            Candidate("v1-e2", "v1", "V1", 100, 0.92, "", event_index=2),
            Candidate("v2-e2", "v2", "V2", 50, 0.88, "", event_index=2),
        ],
    ]

    sequences = adaptive_temporal_search(
        candidate_sets=candidate_sets,
        weights=[1.0, 1.0],
        delta_frame_max=100,
        min_match=2,
        compactness_weight=0.7,
    )

    assert sequences[0].video_id == "v2"


def test_aithena_limits_near_duplicate_sequences_from_one_video() -> None:
    candidate_sets = [
        [
            Candidate("v1-e1-a", "v1", "V1", 10, 0.99, "", event_index=1),
            Candidate("v1-e1-b", "v1", "V1", 11, 0.98, "", event_index=1),
            Candidate("v2-e1", "v2", "V2", 10, 0.90, "", event_index=1),
        ],
        [
            Candidate("v1-e2-a", "v1", "V1", 20, 0.99, "", event_index=2),
            Candidate("v1-e2-b", "v1", "V1", 21, 0.98, "", event_index=2),
            Candidate("v2-e2", "v2", "V2", 20, 0.90, "", event_index=2),
        ],
    ]

    sequences = adaptive_temporal_search(
        candidate_sets=candidate_sets,
        weights=[1.0, 1.0],
        delta_frame_max=30,
        min_match=2,
        per_video_sequence_limit=1,
    )

    assert [sequence.video_id for sequence in sequences] == ["v1", "v2"]


def test_vortex_nmses_nearby_timestamp_anchors_before_building_duplicate_chains() -> None:
    candidate_sets = [
        [Candidate("before", "v1", "V1", 1, 0.8, "", event_index=1, timestamp_ms=1_000)],
        [
            Candidate("anchor-best", "v1", "V1", 10, 0.99, "", event_index=2, timestamp_ms=2_000),
            # The frame indexes look separate, but the media frames are only
            # 100 ms apart and should not each create the same Vortex context.
            Candidate("anchor-near", "v1", "V1", 900, 0.98, "", event_index=2, timestamp_ms=2_100),
        ],
        [Candidate("after", "v1", "V1", 2_000, 0.8, "", event_index=3, timestamp_ms=3_000)],
    ]

    sequences = vortex_k_context_rerank(
        candidate_sets, [1.0, 1.0, 1.0], anchor_index=2, delta_frame_max=30,
        delta_t_max_ms=2_000, anchor_nms_window_ms=500,
    )

    assert len(sequences) == 1
    assert sequences[0].candidates[1].frame_id == "anchor-best"


def test_ats_nmses_duplicate_beam_sequences_by_corresponding_timestamps() -> None:
    candidate_sets = [
        [
            Candidate("e1-best", "v1", "V1", 1, 0.99, "", event_index=1, timestamp_ms=1_000),
            Candidate("e1-near", "v1", "V1", 100, 0.98, "", event_index=1, timestamp_ms=1_100),
        ],
        [
            Candidate("e2-best", "v1", "V1", 2, 0.99, "", event_index=2, timestamp_ms=5_000),
            Candidate("e2-near", "v1", "V1", 200, 0.98, "", event_index=2, timestamp_ms=5_100),
        ],
    ]

    sequences = adaptive_temporal_search(
        candidate_sets, [1.0, 1.0], delta_frame_max=30, delta_t_max_ms=10_000,
        min_match=2, sequence_nms_window_ms=250,
    )

    assert len(sequences) == 1
    assert [item.frame_id for item in sequences[0].candidates] == ["e1-best", "e2-best"]


def test_ats_uses_timestamps_not_frame_index_for_temporal_gaps() -> None:
    sequences = adaptive_temporal_search(
        [[Candidate("e1", "v1", "V1", 10, 0.9, "", event_index=1, timestamp_ms=1_000)],
         [Candidate("e2", "v1", "V1", 10_000, 0.9, "", event_index=2, timestamp_ms=2_000)]],
        [1.0, 1.0], delta_frame_max=30, delta_t_max_ms=1_500, min_match=2,
    )

    assert len(sequences) == 1
    assert [item.frame_idx for item in sequences[0].candidates] == [10, 10_000]


def test_final_temporal_diversification_keeps_one_highest_scoring_sequence_per_video() -> None:
    def sequence(video_id: str, score: float, timestamp_ms: int) -> TemporalSequence:
        candidate = Candidate(
            f"{video_id}-{timestamp_ms}", video_id, video_id.upper(), timestamp_ms // 10,
            score, "", event_index=1, timestamp_ms=timestamp_ms,
        )
        return TemporalSequence(video_id, video_id.upper(), [candidate], score)

    diversified = diversify_temporal_sequences(
        [sequence("v1", 0.99, 1_000), sequence("v1", 0.98, 20_000), sequence("v2", 0.97, 1_000)],
        max_sequences_per_video=1, representative_event_index=1, min_representative_gap_ms=1_000,
    )

    assert [(item.video_id, item.score) for item in diversified] == [("v1", 0.99), ("v2", 0.97)]

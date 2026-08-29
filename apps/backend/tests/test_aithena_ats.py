from __future__ import annotations

import pytest

from app.modules.temporal.ats import Candidate, adaptive_temporal_search
from app.modules.temporal.vortex import vortex_k_context_rerank


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

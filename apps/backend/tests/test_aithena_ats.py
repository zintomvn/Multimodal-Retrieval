from __future__ import annotations

from app.modules.temporal.ats import Candidate, adaptive_temporal_search


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

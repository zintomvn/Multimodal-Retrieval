from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    frame_id: str
    video_id: str
    video_code: str
    frame_idx: int
    score: float
    text: str


@dataclass(frozen=True)
class TemporalSequence:
    video_id: str
    video_code: str
    candidates: list[Candidate]
    score: float


def adaptive_temporal_search(
    candidate_sets: list[list[Candidate]],
    weights: list[float],
    delta_frame_max: int,
    min_match: int,
    limit: int = 100,
) -> list[TemporalSequence]:
    grouped: dict[str, list[list[Candidate]]] = {}
    for query_idx, candidates in enumerate(candidate_sets):
        for candidate in candidates:
            grouped.setdefault(candidate.video_id, [[] for _ in candidate_sets])
            grouped[candidate.video_id][query_idx].append(candidate)

    final_sequences: list[TemporalSequence] = []
    for per_query in grouped.values():
        for candidates in per_query:
            candidates.sort(key=lambda c: c.frame_idx)
        video_sequences: list[list[Candidate]] = []
        _extend_sequences(per_query, 0, [], delta_frame_max, min_match, video_sequences)
        for sequence in video_sequences:
            if len(sequence) < min_match:
                continue
            score_sum = 0.0
            for idx, candidate in enumerate(sequence):
                weight = weights[min(idx, len(weights) - 1)] if weights else 1.0
                score_sum += weight * candidate.score
            gap_penalty = _gap_penalty(sequence, delta_frame_max)
            score = score_sum / len(sequence) - gap_penalty
            first = sequence[0]
            final_sequences.append(
                TemporalSequence(
                    video_id=first.video_id,
                    video_code=first.video_code,
                    candidates=sequence,
                    score=score,
                )
            )

    final_sequences.sort(key=lambda item: item.score, reverse=True)
    return final_sequences[:limit]


def _extend_sequences(
    per_query: list[list[Candidate]],
    query_idx: int,
    current: list[Candidate],
    delta_frame_max: int,
    min_match: int,
    output: list[list[Candidate]],
) -> None:
    if query_idx >= len(per_query):
        if len(current) >= min_match:
            output.append(list(current))
        return

    remaining = len(per_query) - query_idx
    if len(current) + remaining < min_match:
        return

    # Adaptive skip: allow missing ambiguous sub-events.
    _extend_sequences(per_query, query_idx + 1, current, delta_frame_max, min_match, output)

    last_frame = current[-1].frame_idx if current else None
    for candidate in per_query[query_idx][:30]:
        if last_frame is not None:
            if candidate.frame_idx <= last_frame:
                continue
            if candidate.frame_idx - last_frame > delta_frame_max:
                continue
        current.append(candidate)
        _extend_sequences(per_query, query_idx + 1, current, delta_frame_max, min_match, output)
        current.pop()


def _gap_penalty(sequence: list[Candidate], delta_frame_max: int) -> float:
    if len(sequence) < 2 or delta_frame_max <= 0:
        return 0.0
    gaps = [sequence[i + 1].frame_idx - sequence[i].frame_idx for i in range(len(sequence) - 1)]
    avg_gap = sum(gaps) / len(gaps)
    return min(0.15, avg_gap / delta_frame_max * 0.08)

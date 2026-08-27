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
    event_index: int = 0
    event_query: str = ""
    visual_score: float = 0.0
    text_score: float = 0.0
    rrf_score: float = 0.0


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
    per_query_video_limit: int = 12,
    beam_width: int = 400,
    prefer_full_sequences: bool = True,
    compactness_weight: float = 0.0,
    per_video_sequence_limit: int = 0,
) -> list[TemporalSequence]:
    grouped: dict[str, list[list[Candidate]]] = {}
    for query_idx, candidates in enumerate(candidate_sets):
        for candidate in candidates:
            grouped.setdefault(candidate.video_id, [[] for _ in candidate_sets])
            grouped[candidate.video_id][query_idx].append(candidate)

    final_sequences: list[TemporalSequence] = []
    for per_query in grouped.values():
        pruned_per_query = [_prune_video_candidates(candidates, per_query_video_limit) for candidates in per_query]
        for sequence in _beam_sequences(
            pruned_per_query,
            weights,
            delta_frame_max,
            min_match,
            beam_width,
            compactness_weight,
        ):
            if len(sequence) < min_match:
                continue
            first = sequence[0]
            final_sequences.append(
                TemporalSequence(
                    video_id=first.video_id,
                    video_code=first.video_code,
                    candidates=sequence,
                    score=_sequence_score(sequence, weights, delta_frame_max, compactness_weight),
                )
            )

    if prefer_full_sequences:
        full_sequences = [item for item in final_sequences if len(item.candidates) == len(candidate_sets)]
        if full_sequences:
            final_sequences = full_sequences

    final_sequences.sort(key=lambda item: item.score, reverse=True)
    if per_video_sequence_limit > 0:
        diversified: list[TemporalSequence] = []
        per_video_count: dict[str, int] = {}
        for sequence in final_sequences:
            count = per_video_count.get(sequence.video_id, 0)
            if count >= per_video_sequence_limit:
                continue
            diversified.append(sequence)
            per_video_count[sequence.video_id] = count + 1
            if len(diversified) >= limit:
                break
        return diversified
    return final_sequences[:limit]


def _prune_video_candidates(candidates: list[Candidate], limit: int) -> list[Candidate]:
    if not candidates:
        return []
    limit = max(1, limit)
    top_by_score = sorted(candidates, key=lambda c: (c.score, -c.frame_idx), reverse=True)[:limit]
    return sorted(top_by_score, key=lambda c: c.frame_idx)


def _beam_sequences(
    per_query: list[list[Candidate]],
    weights: list[float],
    delta_frame_max: int,
    min_match: int,
    beam_width: int,
    compactness_weight: float,
) -> list[list[Candidate]]:
    beam: list[list[Candidate]] = [[]]
    total_queries = len(per_query)
    beam_width = max(1, beam_width)
    for query_idx, candidates in enumerate(per_query):
        remaining_after = total_queries - query_idx - 1
        next_beam: list[list[Candidate]] = []
        for sequence in beam:
            if len(sequence) + remaining_after >= min_match:
                next_beam.append(sequence)

            last_frame = sequence[-1].frame_idx if sequence else None
            used_frame_ids = {candidate.frame_id for candidate in sequence}
            for candidate in candidates:
                if candidate.frame_id in used_frame_ids:
                    continue
                if last_frame is not None:
                    if candidate.frame_idx <= last_frame:
                        continue
                    if candidate.frame_idx - last_frame > delta_frame_max:
                        continue
                next_beam.append([*sequence, candidate])

        beam = sorted(
            next_beam,
            key=lambda sequence: (
                _sequence_score(sequence, weights, delta_frame_max, compactness_weight),
                len(sequence),
                -(sequence[-1].frame_idx if sequence else 10**9),
            ),
            reverse=True,
        )[:beam_width]
        if not beam:
            break

    return [sequence for sequence in beam if len(sequence) >= min_match]


def _sequence_score(
    sequence: list[Candidate],
    weights: list[float],
    delta_frame_max: int,
    compactness_weight: float = 0.0,
) -> float:
    if not sequence:
        return 0.0
    score_sum = 0.0
    for idx, candidate in enumerate(sequence):
        weight_idx = candidate.event_index - 1 if candidate.event_index > 0 else idx
        weight = weights[min(weight_idx, len(weights) - 1)] if weights else 1.0
        score_sum += weight * candidate.score
    # AIThena ATS Eq. (3): weighted average over temporally valid matched events.
    base_score = score_sum / len(sequence)
    return base_score * _compactness_multiplier(sequence, delta_frame_max, compactness_weight)


def _compactness_multiplier(
    sequence: list[Candidate],
    delta_frame_max: int,
    compactness_weight: float,
) -> float:
    if len(sequence) < 2 or compactness_weight <= 0 or delta_frame_max <= 0:
        return 1.0
    gaps = [current.frame_idx - previous.frame_idx for previous, current in zip(sequence, sequence[1:])]
    mean_gap_ratio = sum(gaps) / (len(gaps) * delta_frame_max)
    return max(0.0, 1.0 - min(1.0, compactness_weight) * min(1.0, mean_gap_ratio))

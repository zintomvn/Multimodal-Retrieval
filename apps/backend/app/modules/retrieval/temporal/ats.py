from __future__ import annotations

from .types import Candidate, TemporalSequence


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
    delta_t_max_ms: int | None = None,
    sequence_nms_window_ms: int = 1500,
    sequence_nms_window_frames: int = 1,
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
            delta_t_max_ms,
        ):
            if len(sequence) < min_match:
                continue
            first = sequence[0]
            final_sequences.append(
                TemporalSequence(
                    video_id=first.video_id,
                    video_code=first.video_code,
                    candidates=sequence,
                    score=_sequence_score(sequence, weights, delta_frame_max, compactness_weight, delta_t_max_ms),
                )
            )

    if prefer_full_sequences:
        full_sequences = [item for item in final_sequences if len(item.candidates) == len(candidate_sets)]
        if full_sequences:
            final_sequences = full_sequences

    final_sequences.sort(key=_sequence_sort_key)
    if sequence_nms_window_ms > 0 or sequence_nms_window_frames > 0:
        final_sequences = _sequence_nms(
            final_sequences,
            sequence_nms_window_ms,
            sequence_nms_window_frames,
        )
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
    top_by_score = sorted(candidates, key=lambda c: (c.score, -_coordinate(c)), reverse=True)[:limit]
    return sorted(top_by_score, key=_coordinate)


def _beam_sequences(
    per_query: list[list[Candidate]],
    weights: list[float],
    delta_frame_max: int,
    min_match: int,
    beam_width: int,
    compactness_weight: float,
    delta_t_max_ms: int | None,
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

            previous = sequence[-1] if sequence else None
            used_frame_ids = {candidate.frame_id for candidate in sequence}
            for candidate in candidates:
                if candidate.frame_id in used_frame_ids:
                    continue
                if previous is not None:
                    if not _is_after(candidate, previous):
                        continue
                    if not _within_gap(candidate, previous, delta_frame_max, delta_t_max_ms):
                        continue
                next_beam.append([*sequence, candidate])

        beam = sorted(
            next_beam,
            key=lambda sequence: (
                _sequence_score(sequence, weights, delta_frame_max, compactness_weight, delta_t_max_ms),
                len(sequence),
                -_coordinate(sequence[-1]) if sequence else 0,
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
    delta_t_max_ms: int | None = None,
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
    return base_score * _compactness_multiplier(sequence, delta_frame_max, compactness_weight, delta_t_max_ms)


def _compactness_multiplier(
    sequence: list[Candidate],
    delta_frame_max: int,
    compactness_weight: float,
    delta_t_max_ms: int | None = None,
) -> float:
    if len(sequence) < 2 or compactness_weight <= 0 or delta_frame_max <= 0:
        return 1.0
    gaps = [_distance(current, previous) for previous, current in zip(sequence, sequence[1:])]
    max_gap = delta_t_max_ms if all(
        previous.timestamp_ms is not None and current.timestamp_ms is not None
        for previous, current in zip(sequence, sequence[1:])
    ) and delta_t_max_ms is not None else delta_frame_max
    if max_gap <= 0:
        return 1.0
    mean_gap_ratio = sum(gaps) / (len(gaps) * max_gap)
    return max(0.0, 1.0 - min(1.0, compactness_weight) * min(1.0, mean_gap_ratio))


def _coordinate(candidate: Candidate) -> int:
    return candidate.timestamp_ms if candidate.timestamp_ms is not None else candidate.frame_idx


def _distance(left: Candidate, right: Candidate) -> int:
    if left.timestamp_ms is not None and right.timestamp_ms is not None:
        return abs(left.timestamp_ms - right.timestamp_ms)
    return abs(left.frame_idx - right.frame_idx)


def _is_after(current: Candidate, previous: Candidate) -> bool:
    if current.timestamp_ms is not None and previous.timestamp_ms is not None:
        return current.timestamp_ms > previous.timestamp_ms
    return current.frame_idx > previous.frame_idx


def _within_gap(current: Candidate, previous: Candidate, delta_frame_max: int, delta_t_max_ms: int | None) -> bool:
    max_gap = delta_t_max_ms if current.timestamp_ms is not None and previous.timestamp_ms is not None and delta_t_max_ms is not None else delta_frame_max
    return _distance(current, previous) <= max_gap


def _sequence_sort_key(sequence: TemporalSequence) -> tuple[float, int, str, tuple[int, ...]]:
    return (-round(sequence.score, 8), -len(sequence.candidates), sequence.video_code, tuple(_coordinate(item) for item in sequence.candidates))


def _sequence_nms(
    sequences: list[TemporalSequence], window_ms: int, window_frames: int,
) -> list[TemporalSequence]:
    """Keep the best sequence for each same-video, same-event temporal neighborhood."""
    selected: list[TemporalSequence] = []
    for sequence in sequences:
        if any(_strongly_overlaps(sequence, kept, window_ms, window_frames) for kept in selected):
            continue
        selected.append(sequence)
    return selected


def _strongly_overlaps(
    left: TemporalSequence, right: TemporalSequence, window_ms: int, window_frames: int,
) -> bool:
    if left.video_id != right.video_id:
        return False
    left_events = {item.event_index: item for item in left.candidates}
    right_events = {item.event_index: item for item in right.candidates}
    if not left_events or left_events.keys() != right_events.keys():
        return False
    for event_index, item in left_events.items():
        other = right_events[event_index]
        window = window_ms if item.timestamp_ms is not None and other.timestamp_ms is not None else window_frames
        if window <= 0 or _distance(item, other) > window:
            return False
    return True

from __future__ import annotations

from .types import Candidate, TemporalSequence


def vortex_k_context_rerank(
    candidate_sets: list[list[Candidate]],
    weights: list[float],
    anchor_index: int,
    delta_frame_max: int,
    limit: int = 100,
    per_query_video_limit: int = 24,
    compactness_weight: float = 0.0,
    gap_penalty: float = 0.0,
    delta_t_max_ms: int | None = None,
    anchor_nms_window_ms: int = 1500,
    anchor_nms_window_frames: int = 1,
) -> list[TemporalSequence]:
    """Generalize Vortex's previous/current/next video-level reranker to k events.

    Each anchor candidate is boosted by the strongest chronologically compatible
    candidate for every other LLM-planned event from the same video. Missing
    context contributes zero, matching the original paper's lightweight rule.
    """
    if not candidate_sets:
        return []
    anchor_position = min(max(anchor_index - 1, 0), len(candidate_sets) - 1)
    grouped: dict[str, list[list[Candidate]]] = {}
    for event_position, candidates in enumerate(candidate_sets):
        for candidate in candidates:
            grouped.setdefault(candidate.video_id, [[] for _ in candidate_sets])
            grouped[candidate.video_id][event_position].append(candidate)

    sequences: list[TemporalSequence] = []
    for per_event in grouped.values():
        pruned = [_top_candidates(candidates, per_query_video_limit) for candidates in per_event]
        anchors = _nms_anchors(
            pruned[anchor_position], anchor_nms_window_ms, anchor_nms_window_frames,
        )
        for anchor in anchors:
            sequence = _context_for_anchor(
                pruned,
                anchor,
                anchor_position,
                delta_frame_max,
                gap_penalty,
                delta_t_max_ms,
            )
            score = anchor.score
            for candidate in sequence:
                if candidate.event_index == anchor.event_index and candidate.frame_id == anchor.frame_id:
                    continue
                weight_index = max(0, candidate.event_index - 1)
                weight = weights[weight_index] if weight_index < len(weights) else 1.0
                score += weight * candidate.score
            score *= _compactness_multiplier(sequence, delta_frame_max, compactness_weight, delta_t_max_ms)
            sequences.append(
                TemporalSequence(
                    video_id=anchor.video_id,
                    video_code=anchor.video_code,
                    candidates=sequence,
                    score=score,
                )
            )

    sequences.sort(
        key=lambda item: (
            -round(item.score, 8),
            -len(item.candidates),
            item.video_code,
            tuple(_coordinate(candidate) for candidate in item.candidates),
        )
    )
    return sequences[:limit]


def _top_candidates(candidates: list[Candidate], limit: int) -> list[Candidate]:
    return sorted(
        sorted(candidates, key=lambda item: (item.score, -_coordinate(item)), reverse=True)[: max(1, limit)],
        key=_coordinate,
    )


def _context_for_anchor(
    per_event: list[list[Candidate]],
    anchor: Candidate,
    anchor_position: int,
    delta_frame_max: int,
    gap_penalty: float,
    delta_t_max_ms: int | None,
) -> list[Candidate]:
    before: list[Candidate] = []
    boundary = anchor
    for position in range(anchor_position - 1, -1, -1):
        candidate = _best_before(per_event[position], boundary, delta_frame_max, gap_penalty, delta_t_max_ms)
        if candidate is not None:
            before.append(candidate)
            boundary = candidate

    after: list[Candidate] = []
    boundary = anchor
    for position in range(anchor_position + 1, len(per_event)):
        candidate = _best_after(per_event[position], boundary, delta_frame_max, gap_penalty, delta_t_max_ms)
        if candidate is not None:
            after.append(candidate)
            boundary = candidate
    return [*reversed(before), anchor, *after]


def _best_before(
    candidates: list[Candidate],
    boundary: Candidate,
    delta_frame_max: int,
    gap_penalty: float,
    delta_t_max_ms: int | None,
) -> Candidate | None:
    eligible = [item for item in candidates if _is_before(item, boundary) and _within_gap(item, boundary, delta_frame_max, delta_t_max_ms)]
    return max(
        eligible,
        key=lambda item: (item.score - gap_penalty * _distance(item, boundary) / _max_gap(item, boundary, delta_frame_max, delta_t_max_ms), _coordinate(item)),
        default=None,
    )


def _best_after(
    candidates: list[Candidate],
    boundary: Candidate,
    delta_frame_max: int,
    gap_penalty: float,
    delta_t_max_ms: int | None,
) -> Candidate | None:
    eligible = [item for item in candidates if _is_after(item, boundary) and _within_gap(item, boundary, delta_frame_max, delta_t_max_ms)]
    return max(
        eligible,
        key=lambda item: (item.score - gap_penalty * _distance(item, boundary) / _max_gap(item, boundary, delta_frame_max, delta_t_max_ms), -_coordinate(item)),
        default=None,
    )


def _compactness_multiplier(
    sequence: list[Candidate],
    delta_frame_max: int,
    compactness_weight: float,
    delta_t_max_ms: int | None = None,
) -> float:
    if len(sequence) < 2 or compactness_weight <= 0 or delta_frame_max <= 0:
        return 1.0
    gaps = [_distance(current, previous) for previous, current in zip(sequence, sequence[1:])]
    max_gap = delta_t_max_ms if delta_t_max_ms is not None and all(
        current.timestamp_ms is not None and previous.timestamp_ms is not None
        for previous, current in zip(sequence, sequence[1:])
    ) else delta_frame_max
    if max_gap <= 0:
        return 1.0
    mean_gap_ratio = sum(gaps) / (len(gaps) * max_gap)
    return max(0.0, 1.0 - min(1.0, compactness_weight) * min(1.0, mean_gap_ratio))


def _nms_anchors(candidates: list[Candidate], window_ms: int, window_frames: int) -> list[Candidate]:
    """Avoid producing the same Vortex chain once per near-identical anchor."""
    selected: list[Candidate] = []
    for candidate in sorted(candidates, key=lambda item: (-item.score, _coordinate(item), item.frame_id)):
        if any(_distance(candidate, prior) < _nms_gap(candidate, prior, window_ms, window_frames) for prior in selected):
            continue
        selected.append(candidate)
    return sorted(selected, key=_coordinate)


def _coordinate(candidate: Candidate) -> int:
    return candidate.timestamp_ms if candidate.timestamp_ms is not None else candidate.frame_idx


def _distance(left: Candidate, right: Candidate) -> int:
    if left.timestamp_ms is not None and right.timestamp_ms is not None:
        return abs(left.timestamp_ms - right.timestamp_ms)
    return abs(left.frame_idx - right.frame_idx)


def _is_before(left: Candidate, right: Candidate) -> bool:
    if left.timestamp_ms is not None and right.timestamp_ms is not None:
        return left.timestamp_ms < right.timestamp_ms
    return left.frame_idx < right.frame_idx


def _is_after(left: Candidate, right: Candidate) -> bool:
    if left.timestamp_ms is not None and right.timestamp_ms is not None:
        return left.timestamp_ms > right.timestamp_ms
    return left.frame_idx > right.frame_idx


def _max_gap(left: Candidate, right: Candidate, delta_frame_max: int, delta_t_max_ms: int | None) -> int:
    if left.timestamp_ms is not None and right.timestamp_ms is not None and delta_t_max_ms is not None:
        return max(1, delta_t_max_ms)
    return max(1, delta_frame_max)


def _within_gap(left: Candidate, right: Candidate, delta_frame_max: int, delta_t_max_ms: int | None) -> bool:
    return _distance(left, right) <= _max_gap(left, right, delta_frame_max, delta_t_max_ms)


def _nms_gap(left: Candidate, right: Candidate, window_ms: int, window_frames: int) -> int:
    return max(0, window_ms if left.timestamp_ms is not None and right.timestamp_ms is not None else window_frames)

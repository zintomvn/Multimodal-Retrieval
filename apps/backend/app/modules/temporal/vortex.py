from __future__ import annotations

from app.modules.temporal.ats import Candidate, TemporalSequence


def vortex_k_context_rerank(
    candidate_sets: list[list[Candidate]],
    weights: list[float],
    anchor_index: int,
    delta_frame_max: int,
    limit: int = 100,
    per_query_video_limit: int = 24,
    compactness_weight: float = 0.0,
    gap_penalty: float = 0.0,
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
        for anchor in pruned[anchor_position]:
            sequence = _context_for_anchor(
                pruned,
                anchor,
                anchor_position,
                delta_frame_max,
                gap_penalty,
            )
            score = anchor.score
            for candidate in sequence:
                if candidate.event_index == anchor.event_index and candidate.frame_id == anchor.frame_id:
                    continue
                weight_index = max(0, candidate.event_index - 1)
                weight = weights[weight_index] if weight_index < len(weights) else 1.0
                score += weight * candidate.score
            score *= _compactness_multiplier(sequence, delta_frame_max, compactness_weight)
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
            tuple(candidate.frame_idx for candidate in item.candidates),
        )
    )
    return sequences[:limit]


def _top_candidates(candidates: list[Candidate], limit: int) -> list[Candidate]:
    return sorted(
        sorted(candidates, key=lambda item: (item.score, -item.frame_idx), reverse=True)[: max(1, limit)],
        key=lambda item: item.frame_idx,
    )


def _context_for_anchor(
    per_event: list[list[Candidate]],
    anchor: Candidate,
    anchor_position: int,
    delta_frame_max: int,
    gap_penalty: float,
) -> list[Candidate]:
    before: list[Candidate] = []
    boundary = anchor.frame_idx
    for position in range(anchor_position - 1, -1, -1):
        candidate = _best_before(per_event[position], boundary, delta_frame_max, gap_penalty)
        if candidate is not None:
            before.append(candidate)
            boundary = candidate.frame_idx

    after: list[Candidate] = []
    boundary = anchor.frame_idx
    for position in range(anchor_position + 1, len(per_event)):
        candidate = _best_after(per_event[position], boundary, delta_frame_max, gap_penalty)
        if candidate is not None:
            after.append(candidate)
            boundary = candidate.frame_idx
    return [*reversed(before), anchor, *after]


def _best_before(
    candidates: list[Candidate],
    boundary: int,
    delta_frame_max: int,
    gap_penalty: float,
) -> Candidate | None:
    eligible = [item for item in candidates if 0 < boundary - item.frame_idx <= delta_frame_max]
    return max(
        eligible,
        key=lambda item: (item.score - gap_penalty * (boundary - item.frame_idx) / delta_frame_max, item.frame_idx),
        default=None,
    )


def _best_after(
    candidates: list[Candidate],
    boundary: int,
    delta_frame_max: int,
    gap_penalty: float,
) -> Candidate | None:
    eligible = [item for item in candidates if 0 < item.frame_idx - boundary <= delta_frame_max]
    return max(
        eligible,
        key=lambda item: (item.score - gap_penalty * (item.frame_idx - boundary) / delta_frame_max, -item.frame_idx),
        default=None,
    )


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

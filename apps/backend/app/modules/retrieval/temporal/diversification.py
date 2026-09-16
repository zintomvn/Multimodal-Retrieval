from __future__ import annotations

from .types import Candidate, TemporalSequence


def nms_event_candidates(
    candidates: list[Candidate],
    min_gap_ms: int,
    per_video_limit: int,
    min_gap_frames: int = 1,
) -> list[Candidate]:
    """Score-first NMS per video after multi-view fusion, retaining a deep pool."""
    selected: list[Candidate] = []
    selected_by_video: dict[str, list[Candidate]] = {}
    for candidate in sorted(candidates, key=lambda item: (-item.score, item.video_code, _coordinate(item), item.frame_id)):
        kept = selected_by_video.setdefault(candidate.video_id, [])
        if len(kept) >= max(1, per_video_limit):
            continue
        if any(_nearby(candidate, prior, min_gap_ms, min_gap_frames) for prior in kept):
            continue
        kept.append(candidate)
        selected.append(candidate)
    return selected


def diversify_temporal_sequences(
    sequences: list[TemporalSequence],
    *,
    max_sequences_per_video: int = 1,
    representative_event_index: int | None = None,
    min_representative_gap_ms: int = 0,
    min_representative_gap_frames: int = 1,
    sequence_nms_window_ms: int = 1500,
    sequence_nms_window_frames: int = 0,
) -> list[TemporalSequence]:
    """Apply sequence NMS, then present temporal evidence from diverse videos."""
    selected: list[TemporalSequence] = []
    per_video: dict[str, list[TemporalSequence]] = {}
    for sequence in sorted(sequences, key=_sequence_sort_key):
        kept = per_video.setdefault(sequence.video_id, [])
        if any(_strongly_overlaps(sequence, prior, sequence_nms_window_ms, sequence_nms_window_frames) for prior in kept):
            continue
        if len(kept) >= max(1, max_sequences_per_video):
            continue
        representative = _representative(sequence, representative_event_index)
        if any(
            _nearby(representative, _representative(prior, representative_event_index), min_representative_gap_ms, min_representative_gap_frames)
            for prior in kept
        ):
            continue
        kept.append(sequence)
        selected.append(sequence)
    return selected


def _representative(sequence: TemporalSequence, event_index: int | None) -> Candidate:
    return next((item for item in sequence.candidates if item.event_index == event_index), max(sequence.candidates, key=lambda item: item.score))


def _strongly_overlaps(left: TemporalSequence, right: TemporalSequence, window_ms: int, window_frames: int) -> bool:
    if left.video_id != right.video_id:
        return False
    left_events = {item.event_index: item for item in left.candidates}
    right_events = {item.event_index: item for item in right.candidates}
    return bool(left_events) and left_events.keys() == right_events.keys() and all(
        _nearby(item, right_events[index], window_ms, window_frames) for index, item in left_events.items()
    )


def _nearby(left: Candidate, right: Candidate, gap_ms: int, gap_frames: int) -> bool:
    if left.timestamp_ms is not None and right.timestamp_ms is not None:
        return abs(left.timestamp_ms - right.timestamp_ms) < max(0, gap_ms)
    return abs(left.frame_idx - right.frame_idx) < max(0, gap_frames)


def _coordinate(candidate: Candidate) -> int:
    return candidate.timestamp_ms if candidate.timestamp_ms is not None else candidate.frame_idx


def _sequence_sort_key(sequence: TemporalSequence) -> tuple[float, int, str, tuple[int, ...]]:
    return (-round(sequence.score, 8), -len(sequence.candidates), sequence.video_code, tuple(_coordinate(item) for item in sequence.candidates))

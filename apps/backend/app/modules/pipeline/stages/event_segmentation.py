"""Event segmentation via greedy-merge algorithm.

Ports the event-embeddings.ipynb logic:
  - time-gap 6.0s / cosine-similarity 0.72 / max-duration 45.0s
  - mean-pool + L2-renormalize event vectors

Event ID convention: "{video_id}_E{event_order:06d}" (6-digit zero-padded)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

# --- Greedy-merge parameters ---
TIME_GAP_SEC = 6.0
COSINE_SIM_THRESHOLD = 0.72
MAX_EVENT_DURATION_SEC = 45.0


@dataclass
class EventInfo:
    event_id: str
    event_order: int
    video_id: str
    start_frame: int
    end_frame: int
    start_seconds: float
    end_seconds: float
    keyframe_indices: list[int] = field(default_factory=list)  # embedding row indices (0-based)
    keyframe_frame_indices: list[int] = field(default_factory=list)  # actual frame numbers
    keyframe_ids: list[str] = field(default_factory=list)
    representative_keyframe_id: str = ""
    embedding: list[float] = field(default_factory=list)


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def segment_events(
    video_id: str,
    keyframe_ids: list[str],
    keyframe_embeddings: np.ndarray,
    keyframe_frame_indices: list[int],
    keyframe_seconds: list[float],
) -> list[EventInfo]:
    """Segment keyframes into events using greedy-merge.

    Args:
        keyframe_frame_indices: actual frame numbers from the video (used for Event.start_frame/end_frame).
        keyframe_indices in EventInfo will be 0-based embedding row indices.

    Returns list of EventInfo with embeddings (mean-pooled + L2-renormalized).
    """
    n = len(keyframe_ids)
    if n == 0:
        return []

    # 0-based embedding row indices
    embedding_indices = list(range(n))

    events: list[EventInfo] = []
    current_start = 0

    def _flush_event(start: int, end: int, event_order: int) -> EventInfo:
        kf_ids = keyframe_ids[start : end + 1]
        kf_emb_indices = embedding_indices[start : end + 1]
        kf_frame_indices = keyframe_frame_indices[start : end + 1]
        kf_secs = keyframe_seconds[start : end + 1]
        vecs = keyframe_embeddings[start : end + 1]
        mean_vec = vecs.mean(axis=0)
        norm = np.linalg.norm(mean_vec)
        if norm > 0:
            mean_vec = mean_vec / norm
        rep_idx = 0  # first keyframe as representative
        return EventInfo(
            event_id=f"{video_id}_E{event_order:06d}",
            event_order=event_order,
            video_id=video_id,
            start_frame=kf_frame_indices[0],
            end_frame=kf_frame_indices[-1],
            start_seconds=kf_secs[0],
            end_seconds=kf_secs[-1],
            keyframe_indices=kf_emb_indices,
            keyframe_frame_indices=kf_frame_indices,
            keyframe_ids=kf_ids,
            representative_keyframe_id=kf_ids[rep_idx],
            embedding=mean_vec.tolist(),
        )

    event_order = 0
    i = current_start
    while i < n:
        if i == current_start:
            i += 1
            continue

        time_gap = keyframe_seconds[i] - keyframe_seconds[i - 1]
        cos_sim = _cosine_similarity(
            keyframe_embeddings[i], keyframe_embeddings[i - 1]
        )
        event_duration = keyframe_seconds[i] - keyframe_seconds[current_start]

        # Check merge conditions
        merge = False
        if time_gap <= TIME_GAP_SEC and cos_sim >= COSINE_SIM_THRESHOLD:
            if event_duration <= MAX_EVENT_DURATION_SEC:
                merge = True

        if merge:
            i += 1
            continue

        # Flush current event
        events.append(_flush_event(current_start, i - 1, event_order))
        event_order += 1
        current_start = i
        i = current_start + 1

    # Flush last event
    if current_start < n:
        events.append(_flush_event(current_start, n - 1, event_order))

    logger.info("Segmented %d keyframes into %d events for %s", n, len(events), video_id)
    return events

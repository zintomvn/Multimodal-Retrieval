"""Stable data contracts shared by temporal retrieval algorithms.

Algorithms accept and return only these immutable value objects.  They do not
know about SQLAlchemy, API schemas, vector stores, or ``RetrievalService``.
That keeps an experiment reproducible and makes it safe to add a new temporal
strategy without growing the search service.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    """One event-level frame candidate supplied by the retrieval layer."""

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
    # Normal callers should provide the media timestamp. ``frame_idx`` remains
    # a compatibility fallback for legacy data without timestamps.
    timestamp_ms: int | None = None


@dataclass(frozen=True)
class TemporalSequence:
    """A time-ordered sequence produced by ATS or Vortex."""

    video_id: str
    video_code: str
    candidates: list[Candidate]
    score: float

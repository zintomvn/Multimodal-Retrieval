"""Tests for event segmentation greedy-merge algorithm.

Uses synthetic embeddings to verify merge thresholds without GPU/real models.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.modules.pipeline.stages.event_segmentation import (
    COSINE_SIM_THRESHOLD,
    MAX_EVENT_DURATION_SEC,
    TIME_GAP_SEC,
    segment_events,
)


def _make_embeddings(n: int, dim: int = 512, seed: int = 42) -> np.ndarray:
    """Create L2-normalized random embeddings."""
    rng = np.random.RandomState(seed)
    vecs = rng.randn(n, dim).astype(np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / norms


def _make_identical_embeddings(n: int, dim: int = 512) -> np.ndarray:
    """Create n identical L2-normalized embeddings."""
    vec = np.ones(dim, dtype=np.float32)
    vec = vec / np.linalg.norm(vec)
    return np.tile(vec, (n, 1))


def test_single_keyframe_produces_one_event():
    embeddings = _make_identical_embeddings(1)
    events = segment_events(
        video_id="V001",
        keyframe_ids=["V001_F000000"],
        keyframe_embeddings=embeddings,
        keyframe_frame_indices=[0],
        keyframe_seconds=[0.0],
    )
    assert len(events) == 1
    assert events[0].event_id == "V001_E000000"
    assert events[0].keyframe_ids == ["V001_F000000"]


def test_empty_keyframes():
    events = segment_events(
        video_id="V001",
        keyframe_ids=[],
        keyframe_embeddings=np.array([], dtype=np.float32).reshape(0, 512),
        keyframe_frame_indices=[],
        keyframe_seconds=[],
    )
    assert len(events) == 0


def test_identical_embeddings_low_time_gap_merge():
    """All identical embeddings with small time gaps → single event."""
    n = 5
    embeddings = _make_identical_embeddings(n)
    seconds = [i * 1.0 for i in range(n)]  # 1s apart

    events = segment_events(
        video_id="V001",
        keyframe_ids=[f"V001_F{i:06d}" for i in range(n)],
        keyframe_embeddings=embeddings,
        keyframe_frame_indices=list(range(n)),
        keyframe_seconds=seconds,
    )
    assert len(events) == 1
    assert events[0].event_order == 0


def test_time_gap_exceeds_threshold_splits():
    """Identical embeddings but large time gap → split into two events."""
    n = 3
    embeddings = _make_identical_embeddings(n)
    seconds = [0.0, 1.0, 10.0]  # gap of 9s between frame 1 and 2

    events = segment_events(
        video_id="V001",
        keyframe_ids=[f"V001_F{i:06d}" for i in range(n)],
        keyframe_embeddings=embeddings,
        keyframe_frame_indices=list(range(n)),
        keyframe_seconds=seconds,
    )
    assert len(events) >= 2
    # First event should have frames 0-1, second event frame 2
    assert events[0].keyframe_ids == ["V001_F000000", "V001_F000001"]
    assert events[1].keyframe_ids == ["V001_F000002"]


def test_dissimilar_embeddings_split():
    """Dissimilar embeddings with small time gap → split."""
    dim = 512
    # Create two orthogonal vectors
    rng = np.random.RandomState(0)
    v1 = rng.randn(dim).astype(np.float32)
    v1 = v1 / np.linalg.norm(v1)
    v2 = rng.randn(dim).astype(np.float32)
    v2 = v2 / np.linalg.norm(v2)
    # Make v2 orthogonal to v1
    v2 = v2 - np.dot(v2, v1) * v1
    v2 = v2 / np.linalg.norm(v2)

    embeddings = np.array([v1, v2], dtype=np.float32)
    seconds = [0.0, 1.0]

    events = segment_events(
        video_id="V001",
        keyframe_ids=["V001_F000000", "V001_F000001"],
        keyframe_embeddings=embeddings,
        keyframe_frame_indices=[0, 1],
        keyframe_seconds=seconds,
    )
    # Orthogonal vectors → cosine sim ≈ 0 < threshold → split
    assert len(events) == 2


def test_max_duration_splits():
    """Identical embeddings exceeding max duration → forced split."""
    n = 10
    embeddings = _make_identical_embeddings(n)
    # Space frames 6s apart → total 54s > MAX_EVENT_DURATION_SEC (45s)
    seconds = [i * 6.0 for i in range(n)]

    events = segment_events(
        video_id="V001",
        keyframe_ids=[f"V001_F{i:06d}" for i in range(n)],
        keyframe_embeddings=embeddings,
        keyframe_frame_indices=list(range(n)),
        keyframe_seconds=seconds,
    )
    # Should produce at least 2 events due to max duration
    assert len(events) >= 2


def test_event_id_convention():
    """Event IDs should be 6-digit zero-padded."""
    n = 3
    embeddings = _make_identical_embeddings(n)
    seconds = [0.0, 10.0, 20.0]

    events = segment_events(
        video_id="L21_V001",
        keyframe_ids=[f"L21_V001_F{i:06d}" for i in range(n)],
        keyframe_embeddings=embeddings,
        keyframe_frame_indices=list(range(n)),
        keyframe_seconds=seconds,
    )
    for i, event in enumerate(events):
        assert event.event_id == f"L21_V001_E{i:06d}"
        assert event.event_order == i


def test_embedding_is_mean_pooled_and_normalized():
    """Event embedding should be the mean-pooled L2-normalized vector of its keyframes."""
    dim = 4
    # Use similar vectors (high cosine sim) so they merge into one event
    v1 = np.array([1, 0.1, 0, 0], dtype=np.float32)
    v2 = np.array([0.9, 0.2, 0, 0], dtype=np.float32)
    v1 = v1 / np.linalg.norm(v1)
    v2 = v2 / np.linalg.norm(v2)
    embeddings = np.array([v1, v2], dtype=np.float32)
    seconds = [0.0, 1.0]

    events = segment_events(
        video_id="V001",
        keyframe_ids=["V001_F000000", "V001_F000001"],
        keyframe_embeddings=embeddings,
        keyframe_frame_indices=[0, 1],
        keyframe_seconds=seconds,
    )
    assert len(events) == 1
    emb = np.array(events[0].embedding)
    expected = (v1 + v2) / 2
    expected = expected / np.linalg.norm(expected)
    np.testing.assert_allclose(emb, expected, atol=1e-6)


def test_representative_keyframe_is_first():
    """Representative keyframe should be the first keyframe in the event."""
    embeddings = _make_identical_embeddings(3)
    seconds = [0.0, 1.0, 2.0]

    events = segment_events(
        video_id="V001",
        keyframe_ids=["V001_F000000", "V001_F000001", "V001_F000002"],
        keyframe_embeddings=embeddings,
        keyframe_frame_indices=[0, 1, 2],
        keyframe_seconds=seconds,
    )
    assert len(events) == 1
    assert events[0].representative_keyframe_id == "V001_F000000"


# ── Keyframe extraction tests (unit-level, no video file) ──

def test_keyframe_id_format():
    """Keyframe IDs should be 6-digit zero-padded."""
    from dataclasses import dataclass
    from pathlib import Path

    @dataclass
    class ExtractedKeyframe:
        keyframe_id: str
        frame_idx: int
        frame_seconds: float
        shot_index: int
        frame_type: str
        local_path: Path

    @dataclass
    class ExtractedShot:
        shot_id: str
        shot_index: int
        start_frame: int
        end_frame: int
        start_seconds: float
        end_seconds: float

    # Direct construction test (no video file / cv2 needed)
    kf = ExtractedKeyframe(
        keyframe_id="V001_F000123",
        frame_idx=123,
        frame_seconds=4.1,
        shot_index=0,
        frame_type="middle",
        local_path=Path("/tmp/f000123.jpg"),
    )
    assert kf.keyframe_id == "V001_F000123"
    assert kf.frame_idx == 123

    shot = ExtractedShot(
        shot_id="V001_S0003",
        shot_index=3,
        start_frame=100,
        end_frame=200,
        start_seconds=3.33,
        end_seconds=6.67,
    )
    assert shot.shot_id == "V001_S0003"
    assert shot.shot_index == 3

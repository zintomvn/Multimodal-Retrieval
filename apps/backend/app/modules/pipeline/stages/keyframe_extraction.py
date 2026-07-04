"""Shot detection + keyframe extraction.

Ports AutoShot logic from notebooks/data processing/get-keyframe-autoshot.ipynb:
shot-boundary detection + first/middle/last frame per shot.

ID conventions:
  shot_id   = "{video_id}_S{shot_index:04d}"
  keyframe_id = "{video_id}_F{frame_idx:06d}"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# --- Shot boundary detection parameters ---
HIST_BINS = 64
HIST_NORM = cv2.NORM_HAMMING
COSINE_SIM_THRESHOLD = 0.40
MIN_SHOT_FRAMES = 3
SHOT_MERGE_GAP = 2


@dataclass
class ExtractedKeyframe:
    keyframe_id: str
    frame_idx: int
    frame_seconds: float
    shot_index: int
    frame_type: str  # "first" | "middle" | "last"
    local_path: Path


@dataclass
class ExtractedShot:
    shot_id: str
    shot_index: int
    start_frame: int
    end_frame: int
    start_seconds: float
    end_seconds: float


@dataclass
class ExtractionResult:
    shots: list[ExtractedShot]
    keyframes: list[ExtractedKeyframe]
    fps: float
    width: int
    height: int
    duration_seconds: float


def _video_id_from_path(video_path: Path) -> str:
    return video_path.stem


def _compute_hist(frame: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [HIST_BINS, HIST_BINS], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten()


def _hist_distance(h1: np.ndarray, h2: np.ndarray) -> float:
    return 1.0 - cv2.compareHist(
        h1.astype(np.float32).reshape(-1, 1),
        h2.astype(np.float32).reshape(-1, 1),
        cv2.HISTCMP_CORREL,
    )


def detect_shots(video_path: Path) -> list[tuple[int, int]]:
    """Return list of (start_frame, end_frame) tuples for each shot."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames < MIN_SHOT_FRAMES:
        cap.release()
        return [(0, max(0, total_frames - 1))]

    prev_hist: np.ndarray | None = None
    boundaries: list[int] = []
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        hist = _compute_hist(frame)
        if prev_hist is not None:
            dist = _hist_distance(prev_hist, hist)
            if dist > COSINE_SIM_THRESHOLD:
                boundaries.append(frame_idx)
        prev_hist = hist
        frame_idx += 1

    cap.release()

    # Build shot list from boundaries
    boundaries = [0] + boundaries + [total_frames]
    shots: list[tuple[int, int]] = []
    for i in range(len(boundaries) - 1):
        start = boundaries[i]
        end = boundaries[i + 1] - 1
        if end - start >= MIN_SHOT_FRAMES:
            shots.append((start, end))

    # Merge very short gaps
    if len(shots) > 1:
        merged = [shots[0]]
        for start, end in shots[1:]:
            prev_start, prev_end = merged[-1]
            if start - prev_end <= SHOT_MERGE_GAP:
                merged[-1] = (prev_start, end)
            else:
                merged.append((start, end))
        shots = merged

    if not shots:
        shots = [(0, total_frames - 1)]

    return shots


def extract_keyframes(
    video_path: Path,
    video_id: str,
    output_dir: Path,
    max_keyframes_per_shot: int = 3,
) -> ExtractionResult:
    """Extract shots and keyframes from a video file.

    For each shot, extracts up to max_keyframes_per_shot frames:
    first, middle, last (or just first/middle if only 2 frames).
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_seconds = total_frames / fps if fps > 0 else 0.0

    shots_bounds = detect_shots(video_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    extracted_shots: list[ExtractedShot] = []
    extracted_keyframes: list[ExtractedKeyframe] = []

    for shot_index, (start_f, end_f) in enumerate(shots_bounds):
        shot_id = f"{video_id}_S{shot_index:04d}"
        shot_start_sec = start_f / fps
        shot_end_sec = end_f / fps

        extracted_shots.append(
            ExtractedShot(
                shot_id=shot_id,
                shot_index=shot_index,
                start_frame=start_f,
                end_frame=end_f,
                start_seconds=shot_start_sec,
                end_seconds=shot_end_sec,
            )
        )

        # Select keyframe positions within shot
        shot_len = end_f - start_f + 1
        if shot_len <= 1:
            kf_positions = [(start_f, "first")]
        elif shot_len <= 2:
            kf_positions = [(start_f, "first"), (end_f, "last")]
        elif max_keyframes_per_shot >= 3:
            mid_f = (start_f + end_f) // 2
            kf_positions = [(start_f, "first"), (mid_f, "middle"), (end_f, "last")]
        else:
            mid_f = (start_f + end_f) // 2
            kf_positions = [(start_f, "first"), (mid_f, "middle")]

        for frame_idx, frame_type in kf_positions:
            keyframe_id = f"{video_id}_F{frame_idx:06d}"
            filename = f"f{frame_idx:06d}.jpg"
            local_path = output_dir / filename

            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                logger.warning("Failed to read frame %d from %s", frame_idx, video_path)
                continue

            cv2.imwrite(str(local_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])

            extracted_keyframes.append(
                ExtractedKeyframe(
                    keyframe_id=keyframe_id,
                    frame_idx=frame_idx,
                    frame_seconds=frame_idx / fps,
                    shot_index=shot_index,
                    frame_type=frame_type,
                    local_path=local_path,
                )
            )

    cap.release()

    return ExtractionResult(
        shots=extracted_shots,
        keyframes=extracted_keyframes,
        fps=fps,
        width=width,
        height=height,
        duration_seconds=duration_seconds,
    )

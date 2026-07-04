"""Tests for video pipeline ID generation and collision rules.

Covers:
  - video_id / shot_id / keyframe_id / event_id generation
  - Collision rules: same-dataset-same-uri idempotent/reprocess,
    same-dataset-different-uri always-fail, cross-dataset always-fail
  - Pipeline request schema validation
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.modules.pipeline.schemas import PipelineJobRequest


# ── ID generation ──

def test_shot_id_format():
    from app.modules.ingest.service import DemoIngestService

    svc = DemoIngestService.__new__(DemoIngestService)
    assert svc._shot_id("L21_V001", 0) == "L21_V001_S0000"
    assert svc._shot_id("L21_V001", 5) == "L21_V001_S0005"
    assert svc._shot_id("L21_V001", 99) == "L21_V001_S0099"


def test_keyframe_id_format():
    from app.modules.ingest.service import DemoIngestService

    svc = DemoIngestService.__new__(DemoIngestService)
    assert svc._keyframe_id("L21_V001", 0) == "L21_V001_F000000"
    assert svc._keyframe_id("L21_V001", 12345) == "L21_V001_F012345"


def test_event_id_normalization():
    from app.modules.ingest.service import _normalize_event_id

    assert _normalize_event_id("L21_V001_E0001") == "L21_V001_E000001"
    assert _normalize_event_id("L21_V001_E000042") == "L21_V001_E000042"
    assert _normalize_event_id("L21_V001_E1") == "L21_V001_E000001"
    assert _normalize_event_id("") == ""
    assert _normalize_event_id("no_event_suffix") == "no_event_suffix"


def test_video_id_from_path():
    from pathlib import Path

    from app.modules.ingest.service import _video_id_from_row

    assert _video_id_from_row("L21_V001.mp4") == "L21_V001"
    assert _video_id_from_row("/path/to/L21_V001.mp4") == "L21_V001"
    assert _video_id_from_row("") == ""
    assert _video_id_from_row("no_extension") == "no_extension"


# ── Pipeline request schema ──

def test_pipeline_request_requires_source_id():
    with pytest.raises(ValidationError):
        PipelineJobRequest()


def test_pipeline_request_minimal():
    req = PipelineJobRequest(source_id="l21_l30_ai_challenge_2025")
    assert req.source_id == "l21_l30_ai_challenge_2025"
    assert req.source_dataset_id is None
    assert req.batch_ids is None
    assert req.video_keys is None
    assert req.force_reprocess is False


def test_pipeline_request_with_video_keys_bypass():
    req = PipelineJobRequest(
        source_id="test_source",
        video_keys=["raw/batch=L21/original/video.mp4"],
    )
    assert req.video_keys == ["raw/batch=L21/original/video.mp4"]
    assert req.batch_ids is None


def test_pipeline_request_empty_listing():
    """An empty video_keys list should be accepted by the schema (worker-side failure)."""
    req = PipelineJobRequest(
        source_id="test_source",
        video_keys=[],
    )
    assert req.video_keys == []


def test_pipeline_request_force_reprocess():
    req = PipelineJobRequest(
        source_id="test_source",
        force_reprocess=True,
    )
    assert req.force_reprocess is True


# ── Collision rules (unit-level, no DB) ──

def test_collision_cross_dataset_always_fail():
    """Video exists in dataset A, pipeline targets dataset B → always fail."""
    from unittest.mock import MagicMock

    from app.modules.pipeline.service import PipelineService

    existing_video = MagicMock()
    existing_video.dataset_id = "dataset-A"
    existing_video.source_video_path = "gs://bucket/video.mp4"
    existing_video.num_keyframes = 10
    existing_video.extra_metadata = {}

    target_dataset = MagicMock()
    target_dataset.dataset_id = "dataset-B"

    svc = PipelineService.__new__(PipelineService)
    with pytest.raises(ValueError, match="video_id collision"):
        svc._check_collision(
            existing_video=existing_video,
            dataset=target_dataset,
            video_key="gs://bucket/video.mp4",
            video_id="L21_V001",
            force_reprocess=False,
        )


def test_collision_same_dataset_different_uri_always_fail():
    """Video exists with same dataset but different source URI → always fail."""
    from unittest.mock import MagicMock

    from app.modules.pipeline.service import PipelineService

    existing_video = MagicMock()
    existing_video.dataset_id = "dataset-A"
    existing_video.source_video_path = "gs://bucket/old_video.mp4"
    existing_video.num_keyframes = 10
    existing_video.extra_metadata = {}

    target_dataset = MagicMock()
    target_dataset.dataset_id = "dataset-A"

    svc = PipelineService.__new__(PipelineService)
    with pytest.raises(ValueError, match="different source URI"):
        svc._check_collision(
            existing_video=existing_video,
            dataset=target_dataset,
            video_key="gs://bucket/new_video.mp4",
            video_id="L21_V001",
            force_reprocess=False,
        )


def test_collision_same_dataset_different_uri_force_reprocess_always_fail():
    """Even with force_reprocess=True, different URI → always fail."""
    from unittest.mock import MagicMock

    from app.modules.pipeline.service import PipelineService

    existing_video = MagicMock()
    existing_video.dataset_id = "dataset-A"
    existing_video.source_video_path = "gs://bucket/old_video.mp4"
    existing_video.num_keyframes = 10
    existing_video.extra_metadata = {}

    target_dataset = MagicMock()
    target_dataset.dataset_id = "dataset-A"

    svc = PipelineService.__new__(PipelineService)
    with pytest.raises(ValueError, match="different source URI"):
        svc._check_collision(
            existing_video=existing_video,
            dataset=target_dataset,
            video_key="gs://bucket/new_video.mp4",
            video_id="L21_V001",
            force_reprocess=True,
        )


def test_collision_same_dataset_same_uri_idempotent_skip():
    """Video exists with same dataset, same URI, already has keyframes → skip."""
    from unittest.mock import MagicMock

    from app.modules.pipeline.service import PipelineService

    existing_video = MagicMock()
    existing_video.dataset_id = "dataset-A"
    existing_video.source_video_path = "gs://bucket/video.mp4"
    existing_video.num_keyframes = 10
    existing_video.extra_metadata = {}

    target_dataset = MagicMock()
    target_dataset.dataset_id = "dataset-A"

    svc = PipelineService.__new__(PipelineService)
    result = svc._check_collision(
        existing_video=existing_video,
        dataset=target_dataset,
        video_key="gs://bucket/video.mp4",
        video_id="L21_V001",
        force_reprocess=False,
    )
    assert result is not None
    assert result.status == "SKIPPED"


def test_collision_same_dataset_same_uri_force_reprocess_version_mismatch():
    """force_reprocess=True but pipeline_version mismatch → reject."""
    from unittest.mock import MagicMock

    from app.modules.pipeline.service import PipelineService

    existing_video = MagicMock()
    existing_video.dataset_id = "dataset-A"
    existing_video.source_video_path = "gs://bucket/video.mp4"
    existing_video.num_keyframes = 10
    existing_video.extra_metadata = {"pipeline_version": "old-version"}

    target_dataset = MagicMock()
    target_dataset.dataset_id = "dataset-A"

    svc = PipelineService.__new__(PipelineService)
    with pytest.raises(ValueError, match="pipeline_version mismatch"):
        svc._check_collision(
            existing_video=existing_video,
            dataset=target_dataset,
            video_key="gs://bucket/video.mp4",
            video_id="L21_V001",
            force_reprocess=True,
        )


# ── IngestJobRequest validation ──

def test_ingest_job_request_demo_mode():
    from app.modules.ingest.schemas import IngestJobRequest

    req = IngestJobRequest(mode="demo", dataset_code="test", dataset_name="Test", dataset_version="v1")
    assert req.mode == "demo"
    assert req.dataset_code == "test"


def test_ingest_job_request_mock_mode():
    from app.modules.ingest.schemas import IngestJobRequest

    req = IngestJobRequest(mode="mock")
    assert req.mode == "mock"
    assert req.dry_run is False


def test_ingest_job_request_no_real_mode():
    """'real' mode should still be accepted by schema but not by router."""
    from app.modules.ingest.schemas import IngestJobRequest

    req = IngestJobRequest(mode="real")
    assert req.mode == "real"

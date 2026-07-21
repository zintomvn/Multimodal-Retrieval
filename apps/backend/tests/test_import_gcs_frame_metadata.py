from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT / "scripts"
for path in (BACKEND_ROOT, SCRIPTS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.db.models import Base, Dataset, Frame, Shot, Video
from app.modules.media import router as media_router
from import_gcs_frame_metadata import (
    BatchSource,
    DatasetImportConfig,
    LoadedBatch,
    build_payloads,
    limit_rows_by_videos,
    normalize_loaded_batches,
    upsert_payloads,
    verify_object_presence,
)


def _config() -> DatasetImportConfig:
    return DatasetImportConfig(
        dataset_id="ai_challenge_2025",
        dataset_code="aic-2026",
        dataset_name="aic-ai-challenge-2025",
        dataset_version="v1",
        bucket_name="aic_ai_2026",
        keyframes_prefix="processed/keyframes",
        manifests_prefix="processed/keyframes_manifests",
        profile_version="autoshot_v1",
        source_version="kaggle_current",
        raw_prefix="raw/source=kaggle",
        public_urls=False,
        metadata_import_run_id="test_run",
    )


def _source() -> BatchSource:
    return BatchSource(
        batch_id="L21",
        run_id="full_autoshot_l21",
        shot_segments_uri=(
            "gs://aic_ai_2026/processed/keyframes_manifests/"
            "dataset=ai_challenge_2025/batch=L21/profile=autoshot_v1/"
            "run_id=full_autoshot_l21/shot_segments.csv"
        ),
        success_uri=(
            "gs://aic_ai_2026/processed/keyframes_manifests/"
            "dataset=ai_challenge_2025/batch=L21/profile=autoshot_v1/"
            "run_id=full_autoshot_l21/_SUCCESS"
        ),
    )


def _rows() -> list[dict[str, str]]:
    image_prefix = "processed/keyframes/dataset=ai_challenge_2025/batch=L21/profile=autoshot_v1/video_id=L21_V001"
    return [
        {
            "dataset_id": "ai_challenge_2025",
            "batch_id": "L21",
            "video_id": "L21_V001",
            "video_name": "L21_V001.mp4",
            "video_gcs_uri": "gs://aic_ai_2026/raw/source=kaggle/dataset=ai_challenge_2025/source_version=kaggle_current/batch=L21/video/L21_V001.mp4",
            "shot_id": "L21_V001_S0000",
            "shot_id_local": "0",
            "shot_start_frame": "0",
            "shot_end_frame": "74",
            "shot_start_sec": "0",
            "shot_end_sec": "2.96",
            "frame_type": "first",
            "frame_idx": "0",
            "frame_sec": "0.0",
            "keyframe_id": "L21_V001_F000000",
            "image_rel_path": "L21_V001/shot_0000_first_f000000.jpg",
            "image_gcs_uri": f"gs://aic_ai_2026/{image_prefix}/shot_0000_first_f000000.jpg",
            "image_storage_key": f"{image_prefix}/shot_0000_first_f000000.jpg",
            "boundary_threshold": "0.296",
            "saved": "true",
            "fps": "25",
            "total_frames_opencv": "100",
        },
        {
            "dataset_id": "ai_challenge_2025",
            "batch_id": "L21",
            "video_id": "L21_V001",
            "video_name": "L21_V001.mp4",
            "video_gcs_uri": "gs://aic_ai_2026/raw/source=kaggle/dataset=ai_challenge_2025/source_version=kaggle_current/batch=L21/video/L21_V001.mp4",
            "shot_id": "L21_V001_S0000",
            "shot_id_local": "0",
            "shot_start_frame": "0",
            "shot_end_frame": "74",
            "shot_start_sec": "0",
            "shot_end_sec": "2.96",
            "frame_type": "middle",
            "frame_idx": "37",
            "frame_sec": "1.48",
            "keyframe_id": "L21_V001_F000037",
            "image_rel_path": "L21_V001/shot_0000_middle_f000037.jpg",
            "image_gcs_uri": f"gs://aic_ai_2026/{image_prefix}/shot_0000_middle_f000037.jpg",
            "image_storage_key": f"{image_prefix}/shot_0000_middle_f000037.jpg",
            "boundary_threshold": "0.296",
            "saved": "true",
            "fps": "25",
            "total_frames_opencv": "100",
        },
    ]


def _session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_build_payloads_from_gcs_shot_segments() -> None:
    config = _config()
    rows = normalize_loaded_batches([LoadedBatch(source=_source(), rows=_rows())], config)
    payloads = build_payloads(rows, config)

    assert payloads["dataset"]["root_uri"] == "gs://aic_ai_2026/processed/keyframes/dataset=ai_challenge_2025"
    assert list(payloads["videos"]) == ["L21_V001"]
    assert list(payloads["shots"]) == ["L21_V001_S0000"]
    assert set(payloads["frames"]) == {"L21_V001_F000000", "L21_V001_F000037"}

    frame = payloads["frames"]["L21_V001_F000037"]
    assert frame["image_storage_key"].startswith("processed/keyframes/")
    assert frame["image_uri"].startswith("gs://aic_ai_2026/processed/keyframes/")
    assert frame["is_media_present"] is True
    assert frame["image_url"] is None


def test_upsert_payloads_is_idempotent() -> None:
    config = _config()
    rows = normalize_loaded_batches([LoadedBatch(source=_source(), rows=_rows())], config)
    payloads = build_payloads(rows, config)
    Session = _session_factory()

    session = Session()
    try:
        first = upsert_payloads(session, payloads)
        second = upsert_payloads(session, payloads)

        assert first["datasets_inserted"] == 1
        assert first["videos_inserted"] == 1
        assert first["shots_inserted"] == 1
        assert first["keyframes_inserted"] == 2
        assert second["datasets_updated"] == 1
        assert second["videos_updated"] == 1
        assert second["shots_updated"] == 1
        assert second["keyframes_updated"] == 2
        assert session.query(Dataset).count() == 1
        assert session.query(Video).count() == 1
        assert session.query(Shot).count() == 1
        assert session.query(Frame).count() == 2
        assert session.query(Frame).filter_by(keyframe_id="L21_V001_F000037").one().image_uri.startswith("gs://")
    finally:
        session.close()


def test_upsert_payloads_keeps_existing_dataset_primary_key() -> None:
    config = _config()
    rows = normalize_loaded_batches([LoadedBatch(source=_source(), rows=_rows())], config)
    payloads = build_payloads(rows, config)
    Session = _session_factory()

    session = Session()
    try:
        session.add(
            Dataset(
                dataset_id="55434b53-9fae-49ed-995a-59dc1e0e7282",
                dataset_code="aic-2026",
                name="old-name",
                version="v1",
                root_uri="gs://old-root",
                status="READY",
            )
        )
        session.commit()

        counts = upsert_payloads(session, payloads)

        assert counts["datasets_updated"] == 1
        assert session.query(Dataset).filter_by(dataset_code="aic-2026").one().dataset_id == "55434b53-9fae-49ed-995a-59dc1e0e7282"
        assert session.query(Video).filter_by(video_id="L21_V001").one().dataset_id == "55434b53-9fae-49ed-995a-59dc1e0e7282"
    finally:
        session.close()


def test_verify_missing_object_marks_media_absent() -> None:
    config = _config()
    rows = normalize_loaded_batches([LoadedBatch(source=_source(), rows=_rows())], config)
    missing_key = rows[0]["image_storage_key"]
    presence, failures = verify_object_presence(
        rows,
        object_exists=lambda key: key != missing_key,
        workers=2,
    )
    payloads = build_payloads(rows, config, object_presence=presence)

    assert any(item["image_storage_key"] == missing_key for item in failures)
    assert payloads["frames"][rows[0]["keyframe_id"]]["is_media_present"] is False
    assert payloads["frames"][rows[1]["keyframe_id"]]["is_media_present"] is True


def test_unsaved_rows_are_imported_as_media_absent() -> None:
    config = _config()
    csv_rows = _rows()
    csv_rows[0]["saved"] = "false"
    rows = normalize_loaded_batches([LoadedBatch(source=_source(), rows=csv_rows)], config)
    payloads = build_payloads(rows, config)

    assert len(payloads["frames"]) == 2
    assert payloads["frames"][csv_rows[0]["keyframe_id"]]["is_media_present"] is False
    assert payloads["frames"][csv_rows[1]["keyframe_id"]]["is_media_present"] is True


def test_limit_rows_by_videos_keeps_whole_video_groups() -> None:
    config = _config()
    csv_rows = _rows()
    extra = {**csv_rows[0]}
    extra["video_id"] = "L21_V002"
    extra["video_name"] = "L21_V002.mp4"
    extra["shot_id"] = "L21_V002_S0000"
    extra["keyframe_id"] = "L21_V002_F000000"
    extra["image_rel_path"] = "L21_V002/shot_0000_first_f000000.jpg"
    extra["image_storage_key"] = extra["image_storage_key"].replace("L21_V001", "L21_V002")
    extra["image_gcs_uri"] = extra["image_gcs_uri"].replace("L21_V001", "L21_V002")
    rows = normalize_loaded_batches([LoadedBatch(source=_source(), rows=[*csv_rows, extra])], config)

    limited = limit_rows_by_videos(rows, max_videos=1)

    assert {row["video_id"] for row in limited} == {"L21_V001"}
    assert len(limited) == 2


def test_media_router_can_presign_from_image_uri_when_storage_key_missing(monkeypatch) -> None:
    monkeypatch.setattr(media_router, "settings", SimpleNamespace(gcs_bucket="aic_ai_2026"))
    frame = Frame(
        keyframe_id="L21_V001_F000037",
        video_id="L21_V001",
        frame_idx=37,
        frame_seconds=1.48,
        timestamp_ms=1480,
        image_uri="gs://aic_ai_2026/processed/keyframes/dataset=ai_challenge_2025/batch=L21/profile=autoshot_v1/video_id=L21_V001/shot_0000_middle_f000037.jpg",
        image_storage_key=None,
    )

    assert media_router._gcs_object_key_for_frame(frame).endswith("shot_0000_middle_f000037.jpg")


def test_media_router_lists_imported_frames_for_gallery() -> None:
    config = _config()
    rows = normalize_loaded_batches([LoadedBatch(source=_source(), rows=_rows())], config)
    payloads = build_payloads(rows, config)
    Session = _session_factory()

    session = Session()
    try:
        upsert_payloads(session, payloads)

        response = media_router.list_frames(
            dataset_id=session.query(Dataset).filter_by(dataset_code="aic-2026").one().dataset_id,
            limit=1,
            offset=0,
            db=session,
        )

        assert response["total"] == 2
        assert response["frames"][0]["id"] == "L21_V001_F000000"
        assert response["frames"][0]["thumbnail_url"] == "/api/media/frames/L21_V001_F000000/thumbnail"
        assert response["frames"][0]["image_storage_key"].startswith("processed/keyframes/")
    finally:
        session.close()

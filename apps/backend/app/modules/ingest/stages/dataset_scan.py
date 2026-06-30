from __future__ import annotations

import logging
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from app.db.models import Dataset, Frame, Video, new_id
from app.db.session import SessionLocal
from app.modules.ingest.pipeline import PipelineContext

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


class DatasetScanStage:
    name = "dataset_scan"

    def run(self, context: PipelineContext) -> PipelineContext:
        manifest_path = context.artifacts.get("manifest_path", "")
        if not manifest_path:
            raise ValueError("manifest_path missing from context.artifacts")

        manifest_file = Path(manifest_path)
        if not manifest_file.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")

        with open(manifest_file) as f:
            manifest = yaml.safe_load(f)

        dataset_root = Path(context.dataset_root)

        with SessionLocal() as db:
            dataset = _upsert_dataset(db, context.dataset_id, manifest.get("dataset", {}), dataset_root)
            context.dataset_id = dataset.id

            total_frames = 0
            for video_conf in manifest.get("videos", []):
                video = _upsert_video(db, dataset.id, video_conf, dataset_root)
                frames = _scan_frames(db, video, video_conf, dataset_root)
                total_frames += len(frames)
                logger.info("  %s → %d frames registered", video.video_code, len(frames))

            dataset.status = "INGESTING"
            db.commit()

        context.stats["frames_scanned"] = total_frames
        logger.info("dataset_scan: %d total frames", total_frames)
        return context


def _upsert_dataset(db: Session, dataset_id: str, conf: dict, root: Path) -> Dataset:
    if dataset_id:
        existing = db.get(Dataset, dataset_id)
        if existing:
            return existing

    name = conf.get("name", "aic2026")
    version = str(conf.get("version", "1.0"))
    existing = db.query(Dataset).filter(Dataset.name == name, Dataset.version == version).first()
    if existing:
        return existing

    dataset = Dataset(
        id=dataset_id or new_id(),
        name=name,
        version=version,
        root_uri=str(root),
        status="INGESTING",
    )
    db.add(dataset)
    db.flush()
    return dataset


def _upsert_video(db: Session, dataset_id: str, conf: dict, root: Path) -> Video:
    video_code = conf["video_code"]
    existing = (
        db.query(Video)
        .filter(Video.dataset_id == dataset_id, Video.video_code == video_code)
        .first()
    )
    if existing:
        return existing

    keyframes_dir = conf.get("keyframes_dir", video_code)
    video = Video(
        dataset_id=dataset_id,
        video_code=video_code,
        uri=str(root / keyframes_dir),
        fps=conf.get("fps", 25.0),
        duration_ms=conf.get("duration_ms", 0),
        width=conf.get("width"),
        height=conf.get("height"),
        extra_metadata={
            "keyframes_dir": keyframes_dir,
            "features_file": conf.get("features_file", ""),
        },
    )
    db.add(video)
    db.flush()
    return video


def _scan_frames(db: Session, video: Video, conf: dict, root: Path) -> list[Frame]:
    keyframes_dir = root / conf.get("keyframes_dir", video.video_code)
    if not keyframes_dir.exists():
        logger.warning("keyframes_dir not found: %s", keyframes_dir)
        return []

    image_files = sorted(
        p for p in keyframes_dir.iterdir() if p.suffix.lower() in _IMAGE_EXTS
    )
    fps = video.fps or 25.0
    frames: list[Frame] = []

    for img_path in image_files:
        frame_idx = _parse_frame_idx(img_path.name)
        existing = (
            db.query(Frame)
            .filter(Frame.video_id == video.id, Frame.frame_idx == frame_idx)
            .first()
        )
        if existing:
            frames.append(existing)
            continue

        frame = Frame(
            video_id=video.id,
            frame_idx=frame_idx,
            timestamp_ms=int(frame_idx / fps * 1000),
            image_uri=str(img_path),
        )
        db.add(frame)
        db.flush()
        frames.append(frame)

    return frames


def _parse_frame_idx(filename: str) -> int:
    stem = Path(filename).stem
    digits = "".join(c for c in stem if c.isdigit())
    return int(digits) if digits else 0

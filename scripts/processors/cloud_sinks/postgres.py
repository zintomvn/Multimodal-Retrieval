from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cloud_sinks.config import SinkConfig
from gcs_source import FrameItem


class PostgresAnnotationSink:
    """Upsert dataset, video, frame and annotation metadata into PostgreSQL."""

    def __init__(self, config: SinkConfig) -> None:
        self.config = config
        self._session_factory = None

    def upsert(self, frames: list[FrameItem], annotations: list[dict[str, Any]]) -> int:
        """Upsert one processed batch into PostgreSQL."""
        self._ensure_backend_path()
        from app.db.models import Dataset, Frame, FrameAnnotation, Shot, Video

        session = self._get_session_factory()()
        try:
            dataset = self._upsert_dataset(session, Dataset)
            self._upsert_videos(session, Video, dataset.dataset_id, frames)
            self._upsert_shots(session, Shot, frames)
            self._upsert_frames(session, Frame, frames)
            self._upsert_annotations(session, FrameAnnotation, frames, annotations)
            session.commit()
            return len(frames)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _upsert_dataset(self, session, Dataset):  # noqa: ANN001 - SQLAlchemy model/session types.
        dataset = session.query(Dataset).filter(Dataset.dataset_code == self.config.dataset_code).first()
        if dataset is None:
            dataset = Dataset(
                dataset_code=self.config.dataset_code,
                name=self.config.dataset_name,
                version=self.config.dataset_version,
                root_uri=self.config.dataset_root_uri,
                status="READY",
            )
            session.add(dataset)
            session.flush()
            return dataset
        dataset.name = self.config.dataset_name
        dataset.version = self.config.dataset_version
        dataset.root_uri = self.config.dataset_root_uri
        dataset.status = "READY"
        session.flush()
        return dataset

    def _upsert_videos(self, session, Video, dataset_id: str, frames: list[FrameItem]) -> None:  # noqa: ANN001
        video_ids = sorted({item.video_id for item in frames})
        existing = {item.video_id: item for item in session.query(Video).filter(Video.video_id.in_(video_ids)).all()}
        for video_id in video_ids:
            payload = {
                "dataset_id": dataset_id,
                "video_code": video_id,
                "video_name": f"{video_id}.mp4",
                "uri": f"{self.config.dataset_root_uri.rstrip('/')}/{video_id}",
                "extra_metadata": {"source": "gcs_frame_processor"},
            }
            if video_id in existing:
                for key, value in payload.items():
                    setattr(existing[video_id], key, value)
            else:
                session.add(Video(video_id=video_id, **payload))
        session.flush()

    def _upsert_shots(self, session, Shot, frames: list[FrameItem]) -> None:  # noqa: ANN001
        shot_items = [item for item in frames if item.shot_id is not None]
        shot_ids = [item.shot_id for item in shot_items if item.shot_id]
        if not shot_ids:
            return
        existing = {item.shot_id: item for item in session.query(Shot).filter(Shot.shot_id.in_(shot_ids)).all()}
        for item in shot_items:
            if item.shot_id is None or item.shot_index is None:
                continue
            payload = {
                "video_id": item.video_id,
                "shot_index": item.shot_index,
                "start_frame": item.frame_idx,
                "end_frame": item.frame_idx,
                "start_seconds": item.frame_seconds,
                "end_seconds": item.frame_seconds,
            }
            if item.shot_id in existing:
                for key, value in payload.items():
                    setattr(existing[item.shot_id], key, value)
            else:
                session.add(Shot(shot_id=item.shot_id, **payload))
        session.flush()

    def _upsert_frames(self, session, Frame, frames: list[FrameItem]) -> None:  # noqa: ANN001
        keyframe_ids = [item.keyframe_id for item in frames]
        existing = {item.keyframe_id: item for item in session.query(Frame).filter(Frame.keyframe_id.in_(keyframe_ids)).all()}
        for item in frames:
            image_url = self._public_url(item.blob_name)
            payload = {
                "video_id": item.video_id,
                "shot_id": item.shot_id,
                "frame_idx": item.frame_idx,
                "frame_seconds": item.frame_seconds,
                "timestamp_ms": int(item.frame_seconds * 1000),
                "frame_type": item.frame_type or "key",
                "image_rel_path": f"{item.video_id}/{item.image_name}",
                "image_storage_key": item.blob_name,
                "image_url": image_url,
                "image_uri": item.gcs_uri,
                "thumbnail_uri": image_url,
                "quality_score": 1.0,
                "is_media_present": True,
            }
            if item.keyframe_id in existing:
                for key, value in payload.items():
                    setattr(existing[item.keyframe_id], key, value)
            else:
                session.add(Frame(keyframe_id=item.keyframe_id, **payload))
        session.flush()

    def _upsert_annotations(self, session, FrameAnnotation, frames: list[FrameItem], annotations: list[dict[str, Any]]) -> None:  # noqa: ANN001
        annotation_ids = [
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"frame-annotation:{item.keyframe_id}:multimodal"))
            for item in frames
        ]
        existing = {item.id: item for item in session.query(FrameAnnotation).filter(FrameAnnotation.id.in_(annotation_ids)).all()}
        for item, record, annotation_id in zip(frames, annotations, annotation_ids):
            caption = str(record.get("caption") or "")
            texts = record.get("texts") if isinstance(record.get("texts"), list) else []
            objects = record.get("objects") if isinstance(record.get("objects"), list) else []
            text_value = " ".join([caption, " ".join(texts), " ".join(objects)]).strip()
            payload = {
                "frame_id": item.keyframe_id,
                "kind": "MULTIMODAL",
                "text_value": text_value,
                "json_value": {"gcs_uri": item.gcs_uri, "image_name": item.image_name},
                "confidence": 1.0,
                "model_version": self.config.model_version,
                "caption": caption,
                "ocr_texts": texts,
                "detected_objects": objects,
                "object_counts": record.get("object_counts") if isinstance(record.get("object_counts"), dict) else {},
                "detections": record.get("detections") if isinstance(record.get("detections"), list) else [],
                "annotation_version": self.config.model_version,
            }
            if annotation_id in existing:
                for key, value in payload.items():
                    setattr(existing[annotation_id], key, value)
            else:
                session.add(FrameAnnotation(id=annotation_id, **payload))
        session.flush()

    def _get_session_factory(self):
        if self._session_factory is None:
            connect_args = {}
            if self.config.postgres_disable_prepared_statements and "postgresql+psycopg" in self.config.database_url:
                connect_args["prepare_threshold"] = None
            engine = create_engine(self.config.database_url, pool_pre_ping=True, connect_args=connect_args)
            self._session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        return self._session_factory

    def _public_url(self, blob_name: str) -> str:
        if self.config.gcs_public_url:
            return f"{self.config.gcs_public_url.rstrip('/')}/{blob_name}"
        bucket = self.config.dataset_root_uri.removeprefix("gs://").split("/", 1)[0]
        return f"https://storage.googleapis.com/{bucket}/{blob_name}"

    def _ensure_backend_path(self) -> None:
        backend_root = Path(__file__).resolve().parents[3] / "apps" / "backend"
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

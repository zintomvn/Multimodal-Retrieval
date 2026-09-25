from __future__ import annotations

import csv
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from app.adapters.object_storage.base import ObjectStorageClient
from app.adapters.object_storage.key_format import build_keyframe_object_key_from_rel_path
from app.adapters.text_search.base import TextSearchClient
from app.adapters.vector_db.base import VectorSearchClient
from app.db.models import Dataset, Event, EventKeyframe, Frame, FrameAnnotation, Shot, Video
from app.modules.ingest.schemas import IngestJobRequest


FRAME_IDX_RE = re.compile(r"f(\d+)", re.IGNORECASE)
EVENT_ID_RE = re.compile(r"^(.*_E)(\d+)$")


@dataclass
class DemoFiles:
    root: Path
    per_video_summary_csv: Path
    shot_segments_csv: Path
    annotations_dir: Path
    frames_dir: Path
    features_map_keyframes_dir: Path
    features_map_event_dir: Path
    features_events_dir: Path
    keyframe_feature_dir: Path


def _to_bool(raw: str | bool | None) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return False
    return str(raw).strip().lower() in {"1", "true", "yes", "y"}


def _to_int(raw: str | int | None, default: int = 0) -> int:
    if raw is None or raw == "":
        return default
    return int(raw)


def _to_float(raw: str | float | int | None, default: float = 0.0) -> float:
    if raw is None or raw == "":
        return default
    return float(raw)


def _video_id_from_row(video_name: str, fallback: str = "") -> str:
    stem = Path(video_name or "").stem
    return stem or fallback


def _extract_frame_idx_from_name(image_name: str) -> int | None:
    match = FRAME_IDX_RE.search(image_name or "")
    if not match:
        return None
    return int(match.group(1))


def _normalize_event_id(raw_event_id: str) -> str:
    value = (raw_event_id or "").strip()
    match = EVENT_ID_RE.match(value)
    if not match:
        return value
    prefix, ordinal = match.groups()
    return f"{prefix}{ordinal.zfill(6)}"


def _image_rel_path(video_id: str, image_path: str, image_name: str | None = None) -> str:
    if image_name:
        return f"{video_id}/{Path(image_name).name}"
    return f"{video_id}/{Path(image_path).name}"


def _chunks(items: list[Any], size: int) -> list[list[Any]]:
    if size <= 0:
        raise ValueError("size must be > 0")
    return [items[index : index + size] for index in range(0, len(items), size)]


class DemoIngestService:
    def __init__(
        self,
        db: Session,
        vector_client: VectorSearchClient | None,
        text_client: TextSearchClient | None,
        object_storage: ObjectStorageClient | None,
    ) -> None:
        self.db = db
        self.vector_client = vector_client
        self.text_client = text_client
        self.object_storage = object_storage

    def run(self, request: IngestJobRequest) -> dict[str, Any]:
        files = self._resolve_demo_files(request.dataset_root)
        report: dict[str, Any] = {
            "mode": request.mode,
            "dataset_root": str(files.root),
            "targets": sorted(set(request.targets)),
            "dry_run": request.dry_run,
            "reconcile": self.preflight_reconcile(files),
            "pg": {},
            "media": {},
            "milvus": {},
            "es": {},
            "failed_rows": [],
        }

        if request.dry_run:
            return report

        dataset = self._upsert_dataset(
            dataset_code=request.dataset_code,
            dataset_name=request.dataset_name,
            dataset_version=request.dataset_version,
            dataset_root=files.root,
        )

        selected_targets = set(request.targets)
        if "pg" in selected_targets:
            report["pg"] = self.import_pg(dataset, files, report["failed_rows"])
        if "media" in selected_targets:
            report["media"] = self.import_media(dataset, files, report["failed_rows"])
        if "milvus" in selected_targets:
            report["milvus"] = self.import_milvus(dataset, files, report["failed_rows"])
        if "es" in selected_targets:
            report["es"] = self.import_es(dataset, files, report["failed_rows"])

        return report

    def preflight_reconcile(self, files: DemoFiles) -> dict[str, Any]:
        missing_media: list[str] = []
        total_shot_rows = 0
        total_saved_rows = 0
        unique_keyframes: set[str] = set()
        unique_shots: set[str] = set()

        with files.shot_segments_csv.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                total_shot_rows += 1
                video_id = _video_id_from_row(row.get("video_name", ""))
                if not video_id:
                    continue
                frame_idx = _to_int(row.get("frame_idx"))
                shot_index = _to_int(row.get("shot_id"))
                keyframe_id = self._keyframe_id(video_id, frame_idx)
                shot_id = self._shot_id(video_id, shot_index)
                unique_keyframes.add(keyframe_id)
                unique_shots.add(shot_id)
                if not _to_bool(row.get("saved")):
                    continue
                total_saved_rows += 1
                rel_path = _image_rel_path(video_id=video_id, image_path=row.get("image_path", ""))
                media_path = files.frames_dir / rel_path
                if not media_path.exists():
                    missing_media.append(rel_path)

        frames_total = sum(1 for _ in files.frames_dir.rglob("*.jpg"))
        annotation_files = self._annotation_files(files.annotations_dir)
        annotations_rows = 0
        for path in annotation_files:
            with path.open("r", encoding="utf-8") as handle:
                annotations_rows += sum(1 for line in handle if line.strip())

        per_video_rows = list(csv.DictReader(files.per_video_summary_csv.open("r", encoding="utf-8")))
        map_rows_total = 0
        keyframe_npy_rows_total = 0
        missing_map_files: list[str] = []
        missing_feature_files: list[str] = []
        n_mismatch_rows = 0
        for video_row in per_video_rows:
            video_id = (video_row.get("video_id") or "").strip()
            if not video_id:
                continue
            map_file = files.features_map_keyframes_dir / f"{video_id}.csv"
            npy_file = files.keyframe_feature_dir / f"{video_id}.npy"
            if not map_file.exists():
                missing_map_files.append(str(map_file))
                continue
            if not npy_file.exists():
                missing_feature_files.append(str(npy_file))
                continue
            with map_file.open("r", encoding="utf-8") as handle:
                for idx, row in enumerate(csv.DictReader(handle), start=1):
                    map_rows_total += 1
                    if _to_int(row.get("n")) != idx:
                        n_mismatch_rows += 1
            keyframe_npy_rows_total += int(np.load(npy_file).shape[0])

        event_mapping_rows = 0
        event_embeddings_rows = 0
        missing_event_feature_files: list[str] = []
        for map_file in sorted(files.features_map_event_dir.glob("*.csv")):
            with map_file.open("r", encoding="utf-8") as handle:
                event_mapping_rows += sum(1 for _ in csv.DictReader(handle))
            npy_file = files.features_events_dir / f"{map_file.stem}.npy"
            if not npy_file.exists():
                missing_event_feature_files.append(str(npy_file))
                continue
            event_embeddings_rows += int(np.load(npy_file).shape[0])

        return {
            "per_video_rows": len(per_video_rows),
            "shot_rows": total_shot_rows,
            "saved_keyframe_rows": total_saved_rows,
            "unique_shots": len(unique_shots),
            "unique_keyframes": len(unique_keyframes),
            "media_files_in_frames_dir": frames_total,
            "missing_media_count": len(missing_media),
            "missing_media_samples": missing_media[:20],
            "annotations_rows": annotations_rows,
            "annotations_files_count": len(annotation_files),
            "map_rows_total": map_rows_total,
            "keyframe_npy_rows_total": keyframe_npy_rows_total,
            "map_n_sequential_mismatch_rows": n_mismatch_rows,
            "missing_map_files_count": len(missing_map_files),
            "missing_map_files_samples": missing_map_files[:20],
            "missing_feature_files_count": len(missing_feature_files),
            "missing_feature_files_samples": missing_feature_files[:20],
            "event_mapping_rows": event_mapping_rows,
            "event_embeddings_rows": event_embeddings_rows,
            "event_embedding_parity": event_mapping_rows == event_embeddings_rows,
            "missing_event_feature_files_count": len(missing_event_feature_files),
            "missing_event_feature_files_samples": missing_event_feature_files[:20],
        }

    def import_pg(self, dataset: Dataset, files: DemoFiles, failed_rows: list[dict[str, Any]]) -> dict[str, Any]:
        if self.object_storage is None:
            raise ValueError("object_storage client is required for PG import")
        map_frame_to_n = self._load_frame_to_n_map(files.features_map_keyframes_dir, failed_rows)
        video_rows = list(csv.DictReader(files.per_video_summary_csv.open("r", encoding="utf-8")))
        video_ids = sorted({(row.get("video_id") or "").strip() for row in video_rows if row.get("video_id")})

        existing_videos = {
            item.video_id: item
            for item in self.db.query(Video).filter(Video.video_id.in_(video_ids)).all()
        }
        videos_inserted = 0
        videos_updated = 0
        for row in video_rows:
            video_id = (row.get("video_id") or "").strip()
            if not video_id:
                failed_rows.append({"stage": "pg.videos", "reason": "missing_video_id", "row": row})
                continue
            payload = {
                "dataset_id": dataset.dataset_id,
                "video_code": video_id,
                "video_name": f"{video_id}.mp4",
                "uri": f"{dataset.root_uri}/video/{video_id}.mp4",
                "source_video_path": "",
                "fps": self._fps_from_map(files.features_map_keyframes_dir / f"{video_id}.csv"),
                "duration_seconds": _to_float(row.get("seconds")),
                "duration_ms": int(_to_float(row.get("seconds")) * 1000),
                "num_keyframes": _to_int(row.get("num_keyframes")),
                "embedding_shape": row.get("embedding_shape") or "",
                "source_feature_path": str(files.keyframe_feature_dir / f"{video_id}.npy"),
                "source_map_path": str(files.features_map_keyframes_dir / f"{video_id}.csv"),
                "extra_metadata": {
                    "source_feature_path_raw": row.get("feature_path"),
                    "source_map_path_raw": row.get("map_path"),
                },
            }
            existing = existing_videos.get(video_id)
            if existing:
                for key, value in payload.items():
                    setattr(existing, key, value)
                videos_updated += 1
            else:
                created = Video(video_id=video_id, **payload)
                self.db.add(created)
                existing_videos[video_id] = created
                videos_inserted += 1
        self.db.flush()

        # Shots + keyframes come from shot_segments.csv.
        existing_shots = {
            item.shot_id: item
            for item in self.db.query(Shot).filter(Shot.video_id.in_(video_ids)).all()
        }
        existing_frames = {
            item.keyframe_id: item
            for item in self.db.query(Frame).filter(Frame.video_id.in_(video_ids)).all()
        }
        shots_inserted = 0
        shots_updated = 0
        keyframes_inserted = 0
        keyframes_updated = 0
        with files.shot_segments_csv.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                video_id = _video_id_from_row(row.get("video_name", ""))
                if not video_id:
                    failed_rows.append({"stage": "pg.shot_segments", "reason": "missing_video_id", "row": row})
                    continue
                if video_id not in existing_videos:
                    failed_rows.append(
                        {"stage": "pg.shot_segments", "reason": "video_not_in_summary", "video_id": video_id, "row": row}
                    )
                    continue
                shot_index = _to_int(row.get("shot_id"))
                frame_idx = _to_int(row.get("frame_idx"))
                shot_id = self._shot_id(video_id, shot_index)
                keyframe_id = self._keyframe_id(video_id, frame_idx)

                shot_payload = {
                    "video_id": video_id,
                    "shot_index": shot_index,
                    "start_frame": _to_int(row.get("shot_start_frame")),
                    "end_frame": _to_int(row.get("shot_end_frame")),
                    "start_seconds": _to_float(row.get("shot_start_sec")),
                    "end_seconds": _to_float(row.get("shot_end_sec")),
                    "boundary_threshold": _to_float(row.get("boundary_threshold"), default=0.0),
                }
                existing_shot = existing_shots.get(shot_id)
                if existing_shot:
                    for key, value in shot_payload.items():
                        setattr(existing_shot, key, value)
                    shots_updated += 1
                else:
                    existing_shot = Shot(shot_id=shot_id, **shot_payload)
                    self.db.add(existing_shot)
                    existing_shots[shot_id] = existing_shot
                    shots_inserted += 1

                image_name = Path(row.get("image_path", "")).name
                image_rel_path = _image_rel_path(video_id=video_id, image_path=row.get("image_path", ""), image_name=image_name)
                image_storage_key = build_keyframe_object_key_from_rel_path(image_rel_path)
                image_url = self.object_storage.public_url(image_storage_key)
                media_path = files.frames_dir / image_rel_path
                map_n = map_frame_to_n.get(video_id, {}).get(frame_idx)

                frame_payload = {
                    "video_id": video_id,
                    "shot_id": shot_id,
                    "frame_idx": frame_idx,
                    "frame_seconds": _to_float(row.get("frame_sec")),
                    "timestamp_ms": int(_to_float(row.get("frame_sec")) * 1000),
                    "frame_type": (row.get("frame_type") or "").strip() or "middle",
                    "map_n": map_n,
                    "embedding_index_0": (map_n - 1) if map_n else None,
                    "image_rel_path": image_rel_path,
                    "image_storage_key": image_storage_key,
                    "image_url": image_url,
                    "image_uri": str(media_path),
                    "thumbnail_uri": image_url,
                    "quality_score": 1.0,
                    "is_media_present": media_path.exists(),
                }
                existing_frame = existing_frames.get(keyframe_id)
                if existing_frame:
                    for key, value in frame_payload.items():
                        setattr(existing_frame, key, value)
                    keyframes_updated += 1
                else:
                    existing_frame = Frame(keyframe_id=keyframe_id, **frame_payload)
                    self.db.add(existing_frame)
                    existing_frames[keyframe_id] = existing_frame
                    keyframes_inserted += 1
        self.db.flush()

        # frame_annotations from annotations/<video_id>/annotations.jsonl
        annotation_rows = list(self._iter_annotation_rows(files.annotations_dir))
        frame_lookup = {
            frame.keyframe_id: frame
            for frame in self.db.query(Frame).filter(Frame.video_id.in_(video_ids)).all()
        }
        annotation_ids: list[str] = []
        parsed_annotations: list[tuple[str, dict[str, Any]]] = []
        for row in annotation_rows:
            video_id = (row.get("video_id") or "").strip()
            frame_idx = _extract_frame_idx_from_name(row.get("image_name") or row.get("image_path") or "")
            if not video_id or frame_idx is None:
                failed_rows.append({"stage": "pg.annotations", "reason": "cannot_parse_frame", "row": row})
                continue
            keyframe_id = self._keyframe_id(video_id, frame_idx)
            if keyframe_id not in frame_lookup:
                failed_rows.append(
                    {"stage": "pg.annotations", "reason": "keyframe_missing", "keyframe_id": keyframe_id, "row": row}
                )
                continue
            annotation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"frame-annotation:{keyframe_id}:multimodal"))
            annotation_ids.append(annotation_id)
            parsed_annotations.append((annotation_id, row))

        existing_annotations = {
            item.id: item
            for item in self.db.query(FrameAnnotation).filter(FrameAnnotation.id.in_(annotation_ids)).all()
        }
        annotations_inserted = 0
        annotations_updated = 0
        for annotation_id, row in parsed_annotations:
            video_id = (row.get("video_id") or "").strip()
            frame_idx = _extract_frame_idx_from_name(row.get("image_name") or row.get("image_path") or "")
            assert frame_idx is not None
            keyframe_id = self._keyframe_id(video_id, frame_idx)
            caption = str(row.get("caption") or "")
            ocr_texts = row.get("texts") if isinstance(row.get("texts"), list) else []
            detected_objects = row.get("objects") if isinstance(row.get("objects"), list) else []
            object_counts = row.get("object_counts") if isinstance(row.get("object_counts"), dict) else {}
            detections = row.get("detections") if isinstance(row.get("detections"), list) else []
            text_value = " ".join(filter(None, [caption, " ".join(ocr_texts), " ".join(detected_objects)])).strip()
            payload = {
                "frame_id": keyframe_id,
                "kind": "MULTIMODAL",
                "text_value": text_value,
                "json_value": {
                    "source_image_name": row.get("image_name"),
                    "source_image_path": row.get("image_path"),
                    "elasticsearch": {
                        "index": "keyframe_annotations",
                        "document_id": keyframe_id,
                    },
                    "zilliz": {
                        "collection": "keyframe_embeddings",
                        "vector_id": keyframe_id,
                    },
                },
                "confidence": 1.0,
                "model_version": "demo-v1",
                "caption": caption,
                "ocr_texts": ocr_texts,
                "detected_objects": detected_objects,
                "object_counts": object_counts,
                "detections": detections,
                "annotation_version": "demo-v1",
            }
            existing = existing_annotations.get(annotation_id)
            if existing:
                for key, value in payload.items():
                    setattr(existing, key, value)
                annotations_updated += 1
            else:
                self.db.add(FrameAnnotation(id=annotation_id, **payload))
                annotations_inserted += 1
        self.db.flush()

        # events from per-video features/map-event/*.csv
        event_rows = self._load_event_rows(files.features_map_event_dir)
        event_ids = [row.get("event_id", "") for row in event_rows if row.get("event_id")]
        existing_events = {
            item.event_id: item
            for item in self.db.query(Event).filter(Event.event_id.in_(event_ids)).all()
        }
        events_inserted = 0
        events_updated = 0
        for row in event_rows:
            event_id = (row.get("event_id") or "").strip()
            if not event_id:
                failed_rows.append({"stage": "pg.events", "reason": "missing_event_id", "row": row})
                continue
            video_id = (row.get("video_id") or "").strip()
            if video_id not in existing_videos:
                failed_rows.append(
                    {"stage": "pg.events", "reason": "video_not_found", "video_id": video_id, "event_id": event_id}
                )
                continue
            representative = self._representative_keyframe_id_from_map(
                video_id=video_id,
                start_n=_to_int(row.get("start_n"), default=-1),
                frame_to_n=map_frame_to_n,
            )
            if representative and representative not in frame_lookup:
                failed_rows.append(
                    {
                        "stage": "pg.events",
                        "reason": "representative_keyframe_missing",
                        "event_id": event_id,
                        "representative_keyframe_id": representative,
                    }
                )
                representative = None
            event_order = self._event_order(event_id)
            payload = {
                "video_id": video_id,
                "embedding_index_0": _to_int(row.get("event_embedding_index")),
                "start_seconds": _to_float(row.get("start_sec")),
                "end_seconds": _to_float(row.get("end_sec")),
                "start_frame": _to_int(row.get("start_frame")),
                "end_frame": _to_int(row.get("end_frame")),
                "representative_keyframe_id": representative,
                "n_shots": None,
                "n_keyframes": _to_int(row.get("n_keyframes")),
                "shot_ids_raw": "",
                "keyframe_embedding_indices_raw": row.get("keyframe_ns") or "",
                "start_frame_idx": _to_int(row.get("start_frame")),
                "end_frame_idx": _to_int(row.get("end_frame")),
                "representative_frame_id": representative,
                "title": event_id,
                "description": "",
                "event_order": event_order,
                "segmentation_version": "demo-v1",
            }
            existing = existing_events.get(event_id)
            if existing:
                for key, value in payload.items():
                    setattr(existing, key, value)
                events_updated += 1
            else:
                self.db.add(Event(event_id=event_id, **payload))
                events_inserted += 1
        self.db.flush()

        # event_keyframes from per-video map-event/*.csv
        event_keyframe_rows = self._build_event_keyframe_rows(
            files=files,
            frame_to_n=map_frame_to_n,
            failed_rows=failed_rows,
        )
        existing_event_ids = {
            event_id
            for (event_id,) in self.db.query(Event.event_id).filter(Event.video_id.in_(video_ids)).all()
        }
        existing_keyframe_ids = {
            keyframe_id
            for (keyframe_id,) in self.db.query(Frame.keyframe_id).filter(Frame.video_id.in_(video_ids)).all()
        }
        filtered_event_keyframe_rows: list[dict[str, Any]] = []
        for row in event_keyframe_rows:
            if row["event_id"] not in existing_event_ids:
                failed_rows.append(
                    {
                        "stage": "pg.event_keyframes",
                        "reason": "event_not_in_events_table",
                        "event_id": row["event_id"],
                        "keyframe_id": row["keyframe_id"],
                    }
                )
                continue
            if row["keyframe_id"] not in existing_keyframe_ids:
                failed_rows.append(
                    {
                        "stage": "pg.event_keyframes",
                        "reason": "keyframe_not_in_keyframes_table",
                        "event_id": row["event_id"],
                        "keyframe_id": row["keyframe_id"],
                    }
                )
                continue
            filtered_event_keyframe_rows.append(row)

        imported_event_ids = sorted({row["event_id"] for row in filtered_event_keyframe_rows})
        for chunk in _chunks(imported_event_ids, 500):
            self.db.query(EventKeyframe).filter(EventKeyframe.event_id.in_(chunk)).delete(synchronize_session=False)
        self.db.flush()
        for row in filtered_event_keyframe_rows:
            self.db.add(
                EventKeyframe(
                    event_id=row["event_id"],
                    seq_no=row["seq_no"],
                    keyframe_id=row["keyframe_id"],
                    keyframe_embedding_index_0=row["keyframe_embedding_index_0"],
                )
            )
        self.db.commit()

        return {
            "dataset_id": dataset.dataset_id,
            "videos_inserted": videos_inserted,
            "videos_updated": videos_updated,
            "shots_inserted": shots_inserted,
            "shots_updated": shots_updated,
            "keyframes_inserted": keyframes_inserted,
            "keyframes_updated": keyframes_updated,
            "annotations_inserted": annotations_inserted,
            "annotations_updated": annotations_updated,
            "events_inserted": events_inserted,
            "events_updated": events_updated,
            "event_keyframes_upserted": len(filtered_event_keyframe_rows),
        }

    def import_media(self, dataset: Dataset, files: DemoFiles, failed_rows: list[dict[str, Any]]) -> dict[str, Any]:
        if self.object_storage is None:
            raise ValueError("object_storage client is required for media import")
        video_ids = [
            item.video_id
            for item in self.db.query(Video.video_id).filter(Video.dataset_id == dataset.dataset_id).all()
        ]
        frames = (
            self.db.query(Frame)
            .filter(Frame.video_id.in_(video_ids))
            .order_by(Frame.video_id.asc(), Frame.frame_idx.asc())
            .all()
        )
        uploaded = 0
        missing = 0
        for frame in frames:
            if not frame.image_rel_path:
                missing += 1
                failed_rows.append(
                    {"stage": "media.upload", "reason": "missing_image_rel_path", "keyframe_id": frame.keyframe_id}
                )
                continue
            source = files.frames_dir / frame.image_rel_path
            if not source.exists():
                missing += 1
                frame.is_media_present = False
                failed_rows.append(
                    {
                        "stage": "media.upload",
                        "reason": "source_image_missing",
                        "keyframe_id": frame.keyframe_id,
                        "source": str(source),
                    }
                )
                continue
            data = source.read_bytes()
            key = frame.image_storage_key or build_keyframe_object_key_from_rel_path(frame.image_rel_path)
            saved_key = self.object_storage.put_object(key=key, data=data, content_type="image/jpeg")
            frame.image_storage_key = saved_key
            frame.image_url = self.object_storage.public_url(saved_key)
            frame.thumbnail_uri = frame.image_url
            frame.is_media_present = True
            uploaded += 1
        self.db.commit()
        return {"uploaded": uploaded, "missing": missing}

    def import_milvus(self, dataset: Dataset, files: DemoFiles, failed_rows: list[dict[str, Any]]) -> dict[str, Any]:
        if self.vector_client is None:
            raise ValueError("vector_client is required for Milvus import")
        video_ids = [
            item.video_id
            for item in self.db.query(Video.video_id).filter(Video.dataset_id == dataset.dataset_id).all()
        ]
        keyframe_ids = {
            item.keyframe_id
            for item in self.db.query(Frame.keyframe_id).filter(Frame.video_id.in_(video_ids)).all()
        }
        event_ids = {
            item.event_id
            for item in self.db.query(Event.event_id).filter(Event.video_id.in_(video_ids)).all()
        }
        frame_to_n = self._load_frame_to_n_map(files.features_map_keyframes_dir, failed_rows)
        n_to_frame = {
            video_id: {n_value: frame_idx for frame_idx, n_value in frame_map.items()}
            for video_id, frame_map in frame_to_n.items()
        }

        keyframe_vectors_upserted = 0
        for video_id in video_ids:
            npy_file = files.keyframe_feature_dir / f"{video_id}.npy"
            if not npy_file.exists():
                failed_rows.append(
                    {
                        "stage": "milvus.keyframe",
                        "reason": "missing_feature_file",
                        "video_id": video_id,
                        "path": str(npy_file),
                    }
                )
                continue
            vectors = np.load(npy_file)
            payload: list[tuple[str, list[float], dict[str, Any]]] = []
            for idx, vector in enumerate(vectors):
                n_value = idx + 1
                frame_idx = n_to_frame.get(video_id, {}).get(n_value)
                if frame_idx is None:
                    failed_rows.append(
                        {
                            "stage": "milvus.keyframe",
                            "reason": "missing_map_n",
                            "video_id": video_id,
                            "n": n_value,
                        }
                    )
                    continue
                keyframe_id = self._keyframe_id(video_id, frame_idx)
                if keyframe_id not in keyframe_ids:
                    failed_rows.append(
                        {
                            "stage": "milvus.keyframe",
                            "reason": "keyframe_not_in_db",
                            "video_id": video_id,
                            "keyframe_id": keyframe_id,
                        }
                    )
                    continue
                payload.append(
                    (
                        keyframe_id,
                        vector.astype(float).tolist(),
                        {
                            "keyframe_id": keyframe_id,
                            "video_id": video_id,
                            "frame_idx": frame_idx,
                            "model_version": files.keyframe_feature_dir.name,
                        },
                    )
                )
            keyframe_vectors_upserted += self.vector_client.upsert("keyframe_embeddings", payload)

        event_payload: list[tuple[str, list[float], dict[str, Any]]] = []
        for video_id in video_ids:
            map_file = files.features_map_event_dir / f"{video_id}.csv"
            npy_file = files.features_events_dir / f"{video_id}.npy"
            if not map_file.exists():
                failed_rows.append(
                    {
                        "stage": "milvus.event",
                        "reason": "missing_map_event_file",
                        "video_id": video_id,
                        "path": str(map_file),
                    }
                )
                continue
            if not npy_file.exists():
                failed_rows.append(
                    {
                        "stage": "milvus.event",
                        "reason": "missing_event_feature_file",
                        "video_id": video_id,
                        "path": str(npy_file),
                    }
                )
                continue

            with map_file.open("r", encoding="utf-8") as handle:
                event_rows = {
                    _to_int(row.get("event_embedding_index"), default=-1): row
                    for row in csv.DictReader(handle)
                }
            vectors = np.load(npy_file)
            for idx, vector in enumerate(vectors):
                row = event_rows.get(idx)
                if not row:
                    failed_rows.append(
                        {
                            "stage": "milvus.event",
                            "reason": "missing_event_mapping_index",
                            "video_id": video_id,
                            "index": idx,
                        }
                    )
                    continue
                event_id = _normalize_event_id(row.get("event_id") or "")
                if event_id not in event_ids:
                    failed_rows.append(
                        {
                            "stage": "milvus.event",
                            "reason": "event_not_in_db",
                            "event_id": event_id,
                            "video_id": video_id,
                            "index": idx,
                        }
                    )
                    continue
                event_payload.append(
                    (
                        event_id,
                        vector.astype(float).tolist(),
                        {
                            "event_id": event_id,
                            "video_id": video_id,
                            "model_version": files.features_events_dir.name,
                        },
                    )
                )
        event_vectors_upserted = self.vector_client.upsert("event_embeddings", event_payload)

        return {
            "keyframe_vectors_upserted": keyframe_vectors_upserted,
            "event_vectors_upserted": event_vectors_upserted,
        }

    def import_es(self, dataset: Dataset, files: DemoFiles, failed_rows: list[dict[str, Any]]) -> dict[str, Any]:
        if self.text_client is None:
            raise ValueError("text_client is required for Elasticsearch import")
        self._ensure_es_index(index_name="keyframe_annotations")

        video_ids = [
            item.video_id
            for item in self.db.query(Video.video_id).filter(Video.dataset_id == dataset.dataset_id).all()
        ]
        frame_lookup = {
            frame.keyframe_id: frame
            for frame in self.db.query(Frame).filter(Frame.video_id.in_(video_ids)).all()
        }

        docs: list[tuple[str, dict[str, Any]]] = []
        total_rows = 0
        for row in self._iter_annotation_rows(files.annotations_dir):
            total_rows += 1
            video_id = (row.get("video_id") or "").strip()
            frame_idx = _extract_frame_idx_from_name(row.get("image_name") or row.get("image_path") or "")
            if not video_id or frame_idx is None:
                failed_rows.append({"stage": "es.import", "reason": "cannot_parse_frame", "row": row})
                continue
            keyframe_id = self._keyframe_id(video_id, frame_idx)
            frame = frame_lookup.get(keyframe_id)
            if not frame:
                failed_rows.append({"stage": "es.import", "reason": "keyframe_not_found", "keyframe_id": keyframe_id})
                continue
            caption = str(row.get("caption") or "")
            ocr_texts = row.get("texts") if isinstance(row.get("texts"), list) else []
            detected_objects = row.get("objects") if isinstance(row.get("objects"), list) else []
            docs.append(
                (
                    keyframe_id,
                    {
                        "keyframe_id": keyframe_id,
                        "video_id": video_id,
                        "shot_id": frame.shot_id,
                        "frame_seconds": frame.frame_seconds,
                        "caption": caption,
                        "ocr_texts": " ".join(ocr_texts),
                        "detected_objects": detected_objects,
                        "object_counts": row.get("object_counts") if isinstance(row.get("object_counts"), dict) else {},
                    },
                )
            )
        indexed = self.text_client.upsert("keyframe_annotations", docs)
        return {"annotations_rows": total_rows, "docs_indexed": indexed}

    def _resolve_demo_files(self, dataset_root: str | None) -> DemoFiles:
        root = Path(dataset_root or "demo").expanduser().resolve()
        features_root = root / "features"
        keyframe_dirs = sorted(
            item for item in features_root.iterdir() if item.is_dir() and item.name.startswith("vit-")
        )
        if not keyframe_dirs:
            raise FileNotFoundError(f"No keyframe embedding directory (vit-*) found under {features_root}")
        keyframe_feature_dir = keyframe_dirs[0]

        required = {
            "per_video_summary_csv": root / "per_video_summary.csv",
            "shot_segments_csv": root / "shot_segments.csv",
            "annotations_dir": root / "annotations",
            "frames_dir": root / "frames",
            "features_map_keyframes_dir": root / "features" / "map-keyframes",
            "features_map_event_dir": root / "features" / "map-event",
            "features_events_dir": root / "features" / "events",
        }
        for name, path in required.items():
            if not path.exists():
                raise FileNotFoundError(f"Required demo file missing: {name} => {path}")
        return DemoFiles(root=root, keyframe_feature_dir=keyframe_feature_dir, **required)

    def _upsert_dataset(self, dataset_code: str, dataset_name: str, dataset_version: str, dataset_root: Path) -> Dataset:
        dataset = self.db.query(Dataset).filter(Dataset.dataset_code == dataset_code).first()
        if dataset is None:
            dataset = Dataset(
                dataset_code=dataset_code,
                name=dataset_name,
                version=dataset_version,
                root_uri=str(dataset_root),
                status="READY",
            )
            self.db.add(dataset)
            self.db.flush()
            return dataset
        dataset.name = dataset_name
        dataset.version = dataset_version
        dataset.root_uri = str(dataset_root)
        dataset.status = "READY"
        self.db.flush()
        return dataset

    def _fps_from_map(self, map_file: Path) -> float | None:
        if not map_file.exists():
            return None
        with map_file.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            first = next(reader, None)
            if not first:
                return None
            value = first.get("fps")
            if value in {None, ""}:
                return None
            return float(value)

    def _load_frame_to_n_map(
        self, map_keyframes_dir: Path, failed_rows: list[dict[str, Any]]
    ) -> dict[str, dict[int, int]]:
        frame_to_n: dict[str, dict[int, int]] = {}
        for map_file in sorted(map_keyframes_dir.glob("*.csv")):
            video_id = map_file.stem
            mapping: dict[int, int] = {}
            with map_file.open("r", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    frame_idx = _to_int(row.get("frame_idx"), default=-1)
                    n_value = _to_int(row.get("n"), default=-1)
                    if frame_idx < 0 or n_value <= 0:
                        failed_rows.append(
                            {
                                "stage": "map-keyframes",
                                "reason": "invalid_frame_idx_or_n",
                                "video_id": video_id,
                                "row": row,
                            }
                        )
                        continue
                    mapping[frame_idx] = n_value
            frame_to_n[video_id] = mapping
        return frame_to_n

    def _build_event_keyframe_rows(
        self,
        files: DemoFiles,
        frame_to_n: dict[str, dict[int, int]],
        failed_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # Reverse map per video: n -> frame_idx so we can map sequence from map-event.
        n_to_frame_idx: dict[str, dict[int, int]] = {
            video_id: {n_value: frame_idx for frame_idx, n_value in mapping.items()}
            for video_id, mapping in frame_to_n.items()
        }
        event_keyframes: list[dict[str, Any]] = []
        for map_file in sorted(files.features_map_event_dir.glob("*.csv")):
            video_id = map_file.stem
            with map_file.open("r", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    event_id = _normalize_event_id(row.get("event_id") or "")
                    if not event_id:
                        failed_rows.append({"stage": "map-event", "reason": "missing_event_id", "row": row})
                        continue
                    ns = [int(value) for value in re.findall(r"\d+", row.get("keyframe_ns") or "")]
                    for seq_no, n_value in enumerate(ns):
                        frame_idx = n_to_frame_idx.get(video_id, {}).get(n_value)
                        if frame_idx is None:
                            failed_rows.append(
                                {
                                    "stage": "map-event",
                                    "reason": "missing_n_mapping",
                                    "video_id": video_id,
                                    "event_id": event_id,
                                    "n": n_value,
                                }
                            )
                            continue
                        event_keyframes.append(
                            {
                                "event_id": event_id,
                                "seq_no": seq_no,
                                "keyframe_id": self._keyframe_id(video_id, frame_idx),
                                "keyframe_embedding_index_0": n_value - 1,
                            }
                        )
        return event_keyframes

    def _representative_keyframe_id_from_map(
        self,
        video_id: str,
        start_n: int,
        frame_to_n: dict[str, dict[int, int]],
    ) -> str | None:
        if start_n <= 0:
            return None
        n_to_frame_idx = {n_value: frame_idx for frame_idx, n_value in frame_to_n.get(video_id, {}).items()}
        frame_idx = n_to_frame_idx.get(start_n)
        if frame_idx is None:
            return None
        return self._keyframe_id(video_id, frame_idx)

    def _event_order(self, event_id: str) -> int:
        match = EVENT_ID_RE.match(event_id)
        if not match:
            return 0
        return int(match.group(2))

    def _shot_id(self, video_id: str, shot_index: int) -> str:
        return f"{video_id}_S{shot_index:04d}"

    def _keyframe_id(self, video_id: str, frame_idx: int) -> str:
        return f"{video_id}_F{frame_idx:06d}"

    def _ensure_es_index(self, index_name: str) -> None:
        raw_client = getattr(self.text_client, "client", None)
        indices = getattr(raw_client, "indices", None)
        if indices is None:
            return
        exists = indices.exists(index=index_name)
        if exists:
            return
        indices.create(
            index=index_name,
            mappings={
                "properties": {
                    "keyframe_id": {"type": "keyword"},
                    "video_id": {"type": "keyword"},
                    "shot_id": {"type": "keyword"},
                    "frame_seconds": {"type": "float"},
                    "caption": {"type": "text"},
                    "ocr_texts": {"type": "text"},
                    "detected_objects": {"type": "keyword"},
                    "object_counts": {"type": "object"},
                }
            },
        )

    def _annotation_files(self, annotations_dir: Path) -> list[Path]:
        return sorted(path for path in annotations_dir.glob("*/annotations.jsonl") if path.is_file())

    def _iter_annotation_rows(self, annotations_dir: Path):  # noqa: ANN201 - generator yielding dict rows.
        for path in self._annotation_files(annotations_dir):
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    yield json.loads(line)

    def _load_event_rows(self, map_event_dir: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for map_file in sorted(map_event_dir.glob("*.csv")):
            with map_file.open("r", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    row_copy = dict(row)
                    row_copy["event_id"] = _normalize_event_id(row_copy.get("event_id") or "")
                    if not row_copy.get("video_id"):
                        row_copy["video_id"] = map_file.stem
                    rows.append(row_copy)
        return rows

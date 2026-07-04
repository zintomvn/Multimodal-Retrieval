"""Pipeline service orchestrator.

Runs the full video processing pipeline sequentially per video:
  GCS raw video -> download -> shot/keyframe extraction -> upload keyframes to GCS
  -> upsert Dataset/Video/Shot/Frame (Postgres)
  -> embed keyframes (OpenCLIP) -> upsert keyframe_embeddings (Milvus)
  -> event segmentation -> upsert Event/EventKeyframe + event_embeddings (Milvus)
  -> annotations (Phase 2) -> upsert FrameAnnotation (Postgres) -> upsert keyframe_annotations (ES)
"""

from __future__ import annotations

import logging
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
import yaml
from sqlalchemy.orm import Session

from app.adapters.object_storage.base import ObjectStorageClient
from app.adapters.object_storage.key_format import build_keyframe_object_key_from_rel_path
from app.adapters.text_search.base import TextSearchClient
from app.adapters.vector_db.base import VectorSearchClient
from app.db.models import Dataset, Event, EventKeyframe, Frame, FrameAnnotation, Job, Shot, Video
from app.modules.pipeline.schemas import PipelineJobRequest, VideoPipelineResult

logger = logging.getLogger(__name__)

PIPELINE_VERSION = "pipeline-v1"
SEGMENTATION_VERSION = "segmentation-v1"
VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}

FRAME_IDX_RE = re.compile(r"f(\d+)", re.IGNORECASE)
EVENT_ID_RE = re.compile(r"^(.*_E)(\d+)$")


@dataclass
class SourceConfig:
    source_id: str
    source_dataset_id: str
    display_name: str
    source_version: str
    raw_prefix: str
    expected_batches: list[str]


def _load_source_config(source_id: str) -> SourceConfig:
    """Load source configuration from data_ingestion_sources.yaml."""
    config_path = Path(__file__).resolve().parents[5] / "configs" / "data_ingestion_sources.yaml"
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    defaults = config.get("defaults", {})
    gcs_defaults = defaults.get("gcs", {})
    raw_prefix_base = gcs_defaults.get("raw_prefix", "raw/source=kaggle")

    for ds in config.get("datasets", []):
        if ds.get("source_id") == source_id:
            dataset_id = ds.get("dataset_id", source_id)
            return SourceConfig(
                source_id=source_id,
                source_dataset_id=dataset_id,
                display_name=ds.get("display_name", source_id),
                source_version=defaults.get("source_version", "kaggle_current"),
                raw_prefix=f"{raw_prefix_base}/dataset={dataset_id}",
                expected_batches=ds.get("expected_batches", []),
            )

    raise ValueError(f"source_id '{source_id}' not found in {config_path}")


def _normalize_event_id(raw_event_id: str) -> str:
    value = (raw_event_id or "").strip()
    match = EVENT_ID_RE.match(value)
    if not match:
        return value
    prefix, ordinal = match.groups()
    return f"{prefix}{ordinal.zfill(6)}"


class PipelineService:
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

    def run(self, job: Job, request: PipelineJobRequest) -> dict[str, Any]:
        """Execute the full pipeline for the given request."""
        source_config = _load_source_config(request.source_id)
        source_dataset_id = request.source_dataset_id or source_config.source_dataset_id

        # Resolve video keys
        if request.video_keys:
            video_keys = request.video_keys
        else:
            video_keys = self._list_video_keys(
                source_config, source_dataset_id, request.batch_ids
            )

        if not video_keys:
            job.status = "FAILED"
            job.progress = 0.0
            job.message = "No video files found in the specified source/prefix."
            job.payload = {**job.payload, "error": "empty_listing", "video_keys": []}
            self.db.commit()
            return {"error": "empty_listing", "video_keys": []}

        job.message = f"Found {len(video_keys)} video(s). Processing..."
        job.payload = {**job.payload, "video_keys": video_keys}
        self.db.commit()

        # Resolve dataset
        dataset_code = request.dataset_code or source_config.source_id
        dataset_name = request.dataset_name or source_config.display_name
        dataset_version = request.dataset_version or source_config.source_version

        dataset = self._upsert_dataset(
            dataset_code=dataset_code,
            dataset_name=dataset_name,
            dataset_version=dataset_version,
            source_dataset_id=source_dataset_id,
        )

        results: list[VideoPipelineResult] = []
        total = len(video_keys)

        for idx, video_key in enumerate(video_keys):
            progress = idx / total
            job.progress = progress
            job.message = f"Processing video {idx + 1}/{total}: {video_key}"
            self.db.commit()

            try:
                result = self._process_video(
                    video_key=video_key,
                    dataset=dataset,
                    source_dataset_id=source_dataset_id,
                    force_reprocess=request.force_reprocess,
                    job=job,
                    progress_base=progress,
                    progress_scale=1.0 / total,
                )
                results.append(result)
            except Exception as exc:
                logger.error("Failed to process %s: %s", video_key, exc, exc_info=True)
                results.append(VideoPipelineResult(
                    video_id=Path(video_key).stem,
                    status="FAILED",
                    error=str(exc),
                ))

        n_failed = sum(1 for r in results if r.status == "FAILED")
        n_completed = sum(1 for r in results if r.status == "COMPLETED")
        n_skipped = sum(1 for r in results if r.status == "SKIPPED")

        job.progress = 1.0
        if n_failed == total:
            job.status = "FAILED"
            job.message = f"Pipeline failed: all {total} video(s) failed."
        elif n_failed > 0:
            job.status = "COMPLETED"
            job.message = (
                f"Pipeline complete with errors: {n_completed} completed, "
                f"{n_failed} failed, {n_skipped} skipped out of {total}."
            )
        else:
            job.status = "COMPLETED"
            job.message = f"Pipeline complete. {n_completed} completed, {n_skipped} skipped."
        job.payload = {
            **job.payload,
            "results": [r.model_dump() for r in results],
        }
        self.db.commit()

        return {
            "results": [r.model_dump() for r in results],
            "dataset_id": dataset.dataset_id,
        }

    def _list_video_keys(
        self,
        source_config: SourceConfig,
        source_dataset_id: str,
        batch_ids: list[str] | None,
    ) -> list[str]:
        """List video object keys from GCS/local storage."""
        if self.object_storage is None:
            raise ValueError("object_storage is required for video listing")

        batches = batch_ids or source_config.expected_batches
        all_keys: list[str] = []

        # Rebuild prefix with effective source_dataset_id (may differ from YAML default)
        raw_prefix_base = source_config.raw_prefix.rsplit("/dataset=", 1)[0]
        effective_prefix = f"{raw_prefix_base}/dataset={source_dataset_id}"

        for batch in batches:
            candidate_prefixes = [
                # Original planned upload layout.
                f"{effective_prefix}/source_version={source_config.source_version}/batch={batch}/original/",
                # Current Kaggle upload layout observed in GCS.
                f"{effective_prefix}/batch={batch}/source_version={source_config.source_version}/",
            ]
            for prefix in candidate_prefixes:
                keys = self.object_storage.list_objects(prefix)
                for key in keys:
                    suffix = Path(key).suffix.lower()
                    if suffix in VIDEO_EXTS and key not in all_keys:
                        all_keys.append(key)

        return all_keys

    def _upsert_dataset(
        self,
        dataset_code: str,
        dataset_name: str,
        dataset_version: str,
        source_dataset_id: str,
    ) -> Dataset:
        dataset = self.db.query(Dataset).filter(Dataset.dataset_code == dataset_code).first()
        if dataset is None:
            dataset = Dataset(
                dataset_code=dataset_code,
                name=dataset_name,
                version=dataset_version,
                root_uri=f"gcs://source={source_dataset_id}",
                status="READY",
            )
            self.db.add(dataset)
            self.db.flush()
            return dataset
        dataset.name = dataset_name
        dataset.version = dataset_version
        dataset.root_uri = f"gcs://source={source_dataset_id}"
        dataset.status = "READY"
        self.db.flush()
        return dataset

    def _process_video(
        self,
        video_key: str,
        dataset: Dataset,
        source_dataset_id: str,
        force_reprocess: bool,
        job: Job,
        progress_base: float,
        progress_scale: float,
    ) -> VideoPipelineResult:
        video_id = Path(video_key).stem

        # --- Step 1: Collision/idempotency check ---
        existing_video = self.db.query(Video).filter(Video.video_id == video_id).first()
        if existing_video:
            collision_result = self._check_collision(
                existing_video=existing_video,
                dataset=dataset,
                video_key=video_key,
                video_id=video_id,
                force_reprocess=force_reprocess,
            )
            if collision_result:
                return collision_result

        # --- Step 2: Download raw video ---
        job.message = f"Downloading {video_key}..."
        job.progress = progress_base + 0.05 * progress_scale
        self.db.commit()

        with tempfile.TemporaryDirectory() as tmp_dir:
            local_video_path = Path(tmp_dir) / Path(video_key).name
            self.object_storage.download_to_file(video_key, local_video_path)

            # --- Step 3: Shot detection + keyframe extraction ---
            job.message = f"Extracting keyframes from {video_id}..."
            job.progress = progress_base + 0.15 * progress_scale
            self.db.commit()

            from app.modules.pipeline.stages.keyframe_extraction import extract_keyframes

            kf_output_dir = Path(tmp_dir) / "keyframes"
            extraction = extract_keyframes(
                video_path=local_video_path,
                video_id=video_id,
                output_dir=kf_output_dir,
            )

            # --- Step 3b: ID-parity guard (keyframe IDs) ---
            if force_reprocess and existing_video:
                self._verify_keyframe_id_parity(video_id, extraction.keyframes)

            # --- Step 4: Upload keyframes to GCS ---
            job.message = f"Uploading {len(extraction.keyframes)} keyframes..."
            job.progress = progress_base + 0.25 * progress_scale
            self.db.commit()

            self._upload_keyframes(extraction.keyframes, video_id)

            # --- Step 5: Upsert Dataset/Video/Shot/Frame ---
            job.message = f"Upserting database records..."
            job.progress = progress_base + 0.35 * progress_scale
            self.db.commit()

            video = self._upsert_video(
                video_id=video_id,
                dataset=dataset,
                video_key=video_key,
                extraction=extraction,
                source_dataset_id=source_dataset_id,
            )
            self._upsert_shots(extraction.shots, video_id)
            frames = self._upsert_frames(extraction.keyframes, video_id)

            # --- Step 6: Embed keyframes ---
            job.message = f"Embedding {len(extraction.keyframes)} keyframes..."
            job.progress = progress_base + 0.50 * progress_scale
            self.db.commit()

            embeddings = self._embed_keyframes(extraction.keyframes)
            self._upsert_keyframe_embeddings(
                extraction=extraction,
                embeddings=embeddings,
                video_id=video_id,
            )

            # --- Step 7: Event segmentation ---
            job.message = f"Segmenting events..."
            job.progress = progress_base + 0.65 * progress_scale
            self.db.commit()

            events = self._segment_events(
                video_id=video_id,
                extraction=extraction,
                embeddings=embeddings,
            )

            # --- Step 7b: ID-parity guard (event IDs) ---
            if force_reprocess and existing_video and events:
                self._verify_event_id_parity(video_id, events)

            # --- Step 7c: Upsert events to DB + Milvus ---
            self._upsert_events(
                video_id=video_id,
                events=events,
            )

            # --- Step 8: Annotations (Phase 2 placeholder) ---
            job.message = f"Annotations (Phase 2)..."
            job.progress = progress_base + 0.80 * progress_scale
            self.db.commit()

            # Clean up temp keyframe files
            for kf in extraction.keyframes:
                if kf.local_path.exists():
                    kf.local_path.unlink(missing_ok=True)

        # --- Step 9: Finalize ---
        video.num_keyframes = len(extraction.keyframes)
        job.progress = progress_base + 1.0 * progress_scale
        job.message = f"Complete: {video_id} ({len(extraction.keyframes)} keyframes, {len(events)} events)"
        self.db.commit()

        return VideoPipelineResult(
            video_id=video_id,
            status="COMPLETED",
            num_keyframes=len(extraction.keyframes),
            num_events=len(events),
        )

    def _check_collision(
        self,
        existing_video: Video,
        dataset: Dataset,
        video_key: str,
        video_id: str,
        force_reprocess: bool,
    ) -> VideoPipelineResult | None:
        """Check for video_id collisions. Returns VideoPipelineResult if should skip/fail."""
        # Different dataset → always fail (global PK collision)
        if existing_video.dataset_id != dataset.dataset_id:
            raise ValueError(
                f"video_id collision: '{video_id}' exists in dataset '{existing_video.dataset_id}' "
                f"but pipeline targets dataset '{dataset.dataset_id}'. "
                f"Use a different video_id or dataset."
            )

        # Same dataset, different source URI → always fail
        if existing_video.source_video_path and existing_video.source_video_path != video_key:
            raise ValueError(
                f"video_id '{video_id}' exists with different source URI "
                f"('{existing_video.source_video_path}' vs '{video_key}'). "
                f"Destructive reprocessing not supported in MVP."
            )

        # Same dataset, same URI → idempotent skip or force_reprocess
        if existing_video.num_keyframes and not force_reprocess:
            return VideoPipelineResult(
                video_id=video_id,
                status="SKIPPED",
                num_keyframes=existing_video.num_keyframes or 0,
            )

        if force_reprocess:
            # Version check
            extra = existing_video.extra_metadata or {}
            stored_pv = extra.get("pipeline_version", "")
            stored_sv = extra.get("segmentation_version", "")
            if stored_pv and stored_pv != PIPELINE_VERSION:
                raise ValueError(
                    f"force_reprocess rejected: pipeline_version mismatch "
                    f"('{stored_pv}' vs '{PIPELINE_VERSION}'). "
                    f"Bump version intentionally before reprocessing."
                )
            if stored_sv and stored_sv != SEGMENTATION_VERSION:
                raise ValueError(
                    f"force_reprocess rejected: segmentation_version mismatch "
                    f"('{stored_sv}' vs '{SEGMENTATION_VERSION}')."
                )

        return None

    def _verify_keyframe_id_parity(self, video_id: str, extracted_keyframes) -> None:
        """For force_reprocess: verify extracted keyframe IDs match stored ones."""
        stored_ids = {
            kf_id
            for (kf_id,) in self.db.query(Frame.keyframe_id).filter(Frame.video_id == video_id).all()
        }
        if not stored_ids:
            return  # no existing data, nothing to compare
        extracted_ids = {kf.keyframe_id for kf in extracted_keyframes}
        if extracted_ids != stored_ids:
            missing_in_extracted = stored_ids - extracted_ids
            extra_in_extracted = extracted_ids - stored_ids
            msg_parts = ["ID-parity guard: keyframe_id mismatch."]
            if missing_in_extracted:
                msg_parts.append(f"Missing in extraction: {sorted(missing_in_extracted)[:10]}")
            if extra_in_extracted:
                msg_parts.append(f"Extra in extraction: {sorted(extra_in_extracted)[:10]}")
            raise ValueError(" ".join(msg_parts))

    def _verify_event_id_parity(self, video_id: str, extracted_events) -> None:
        """For force_reprocess: verify extracted event IDs match stored ones."""
        stored_ids = {
            eid
            for (eid,) in self.db.query(Event.event_id).filter(Event.video_id == video_id).all()
        }
        if not stored_ids:
            return  # no existing data
        extracted_ids = {e.event_id for e in extracted_events}
        if extracted_ids != stored_ids:
            missing = stored_ids - extracted_ids
            extra = extracted_ids - stored_ids
            msg_parts = ["ID-parity guard: event_id mismatch."]
            if missing:
                msg_parts.append(f"Missing in extraction: {sorted(missing)[:10]}")
            if extra:
                msg_parts.append(f"Extra in extraction: {sorted(extra)[:10]}")
            raise ValueError(" ".join(msg_parts))

    def _upload_keyframes(self, keyframes, video_id: str) -> None:
        """Upload extracted keyframe images to object storage."""
        if self.object_storage is None:
            return

        for kf in keyframes:
            if not kf.local_path.exists():
                continue
            rel_path = f"{video_id}/{kf.local_path.name}"
            storage_key = build_keyframe_object_key_from_rel_path(rel_path)
            data = kf.local_path.read_bytes()
            self.object_storage.put_object(
                key=storage_key, data=data, content_type="image/jpeg"
            )

    def _upsert_video(
        self,
        video_id: str,
        dataset: Dataset,
        video_key: str,
        extraction,
        source_dataset_id: str,
    ) -> Video:
        video = self.db.query(Video).filter(Video.video_id == video_id).first()
        payload = {
            "dataset_id": dataset.dataset_id,
            "video_code": video_id,
            "video_name": Path(video_key).name,
            "uri": f"gs://{video_key}",
            "source_video_path": video_key,
            "fps": extraction.fps,
            "duration_seconds": extraction.duration_seconds,
            "duration_ms": int(extraction.duration_seconds * 1000),
            "width": extraction.width,
            "height": extraction.height,
            "num_keyframes": len(extraction.keyframes),
            "embedding_shape": "512",
            "extra_metadata": {
                "source_dataset_id": source_dataset_id,
                "pipeline_version": PIPELINE_VERSION,
                "segmentation_version": SEGMENTATION_VERSION,
            },
        }
        if video:
            for key, value in payload.items():
                setattr(video, key, value)
        else:
            video = Video(video_id=video_id, **payload)
            self.db.add(video)
        self.db.flush()
        return video

    def _upsert_shots(self, shots, video_id: str) -> None:
        existing = {
            s.shot_id: s
            for s in self.db.query(Shot).filter(Shot.video_id == video_id).all()
        }
        for shot in shots:
            shot_payload = {
                "video_id": video_id,
                "shot_index": shot.shot_index,
                "start_frame": shot.start_frame,
                "end_frame": shot.end_frame,
                "start_seconds": shot.start_seconds,
                "end_seconds": shot.end_seconds,
            }
            if shot.shot_id in existing:
                for key, value in shot_payload.items():
                    setattr(existing[shot.shot_id], key, value)
            else:
                self.db.add(Shot(shot_id=shot.shot_id, **shot_payload))
        self.db.flush()

    def _upsert_frames(self, keyframes, video_id: str) -> list[Frame]:
        existing = {
            f.keyframe_id: f
            for f in self.db.query(Frame).filter(Frame.video_id == video_id).all()
        }
        frames: list[Frame] = []
        for kf in keyframes:
            rel_path = f"{video_id}/{kf.local_path.name}"
            storage_key = build_keyframe_object_key_from_rel_path(rel_path)
            image_url = self.object_storage.public_url(storage_key) if self.object_storage else ""
            image_uri = f"gs://{storage_key}" if self.object_storage else image_url

            frame_payload = {
                "video_id": video_id,
                "shot_id": f"{video_id}_S{kf.shot_index:04d}",
                "frame_idx": kf.frame_idx,
                "frame_seconds": kf.frame_seconds,
                "timestamp_ms": int(kf.frame_seconds * 1000),
                "frame_type": kf.frame_type,
                "image_rel_path": rel_path,
                "image_storage_key": storage_key,
                "image_url": image_url,
                "image_uri": image_uri,
                "thumbnail_uri": image_url,
                "quality_score": 1.0,
                "is_media_present": True,
            }

            if kf.keyframe_id in existing:
                for key, value in frame_payload.items():
                    setattr(existing[kf.keyframe_id], key, value)
                frames.append(existing[kf.keyframe_id])
            else:
                frame = Frame(keyframe_id=kf.keyframe_id, **frame_payload)
                self.db.add(frame)
                frames.append(frame)
        self.db.flush()
        return frames

    def _embed_keyframes(self, keyframes) -> np.ndarray:
        """Embed all keyframes using OpenCLIP."""
        if not keyframes:
            return np.array([], dtype=np.float32)

        from app.modules.pipeline.stages.embed_keyframes import embed_keyframe_images, load_model_config

        config = load_model_config()
        image_paths = [kf.local_path for kf in keyframes]

        return embed_keyframe_images(
            image_paths=image_paths,
            batch_size=config["batch_size"],
            model_name=config["model_name"],
            pretrained=config["pretrained"],
            device=config["device"],
        )

    def _upsert_keyframe_embeddings(
        self,
        extraction,
        embeddings: np.ndarray,
        video_id: str,
    ) -> int:
        """Upsert keyframe embeddings into Milvus."""
        if self.vector_client is None or embeddings.size == 0:
            return 0

        from app.modules.pipeline.stages.embed_keyframes import load_model_config

        config = load_model_config()
        model_version = f"openclip-{config['model_name']}-{config['pretrained']}"

        payload: list[tuple[str, list[float], dict[str, Any]]] = []
        for i, kf in enumerate(extraction.keyframes):
            if i >= len(embeddings):
                break
            payload.append((
                kf.keyframe_id,
                embeddings[i].astype(float).tolist(),
                {
                    "keyframe_id": kf.keyframe_id,
                    "video_id": video_id,
                    "frame_idx": kf.frame_idx,
                    "model_version": model_version,
                },
            ))

        return self.vector_client.upsert("keyframe_embeddings", payload)

    def _segment_events(
        self,
        video_id: str,
        extraction,
        embeddings: np.ndarray,
    ) -> list:
        """Segment events from keyframes + embeddings. No DB/Milvus writes."""
        if embeddings.size == 0 or not extraction.keyframes:
            return []

        from app.modules.pipeline.stages.event_segmentation import segment_events

        keyframe_ids = [kf.keyframe_id for kf in extraction.keyframes]
        keyframe_frame_indices = [kf.frame_idx for kf in extraction.keyframes]
        keyframe_seconds = [kf.frame_seconds for kf in extraction.keyframes]

        return segment_events(
            video_id=video_id,
            keyframe_ids=keyframe_ids,
            keyframe_embeddings=embeddings,
            keyframe_frame_indices=keyframe_frame_indices,
            keyframe_seconds=keyframe_seconds,
        )

    def _upsert_events(
        self,
        video_id: str,
        events: list,
    ) -> None:
        """Upsert segmented events into Postgres + Milvus."""
        if not events:
            return

        # Upsert Event rows
        existing_events = {
            e.event_id: e
            for e in self.db.query(Event).filter(Event.video_id == video_id).all()
        }
        for event in events:
            event_payload = {
                "video_id": video_id,
                "embedding_index_0": event.event_order,
                "start_seconds": event.start_seconds,
                "end_seconds": event.end_seconds,
                "start_frame": event.start_frame,
                "end_frame": event.end_frame,
                "representative_keyframe_id": event.representative_keyframe_id,
                "n_keyframes": len(event.keyframe_ids),
                "shot_ids_raw": "",
                "keyframe_embedding_indices_raw": ",".join(str(idx) for idx in event.keyframe_indices),
                # Legacy fields — use actual frame numbers
                "start_frame_idx": event.start_frame,
                "end_frame_idx": event.end_frame,
                "representative_frame_id": event.representative_keyframe_id,
                "title": event.event_id,
                "description": "",
                "event_order": event.event_order,
                "segmentation_version": SEGMENTATION_VERSION,
            }
            if event.event_id in existing_events:
                for key, value in event_payload.items():
                    setattr(existing_events[event.event_id], key, value)
            else:
                self.db.add(Event(event_id=event.event_id, **event_payload))
        self.db.flush()

        # Upsert EventKeyframe rows
        existing_ek = {
            (ek.event_id, ek.seq_no): ek
            for ek in self.db.query(EventKeyframe).filter(EventKeyframe.event_id.in_([e.event_id for e in events])).all()
        }
        for event in events:
            for seq_no, kf_id in enumerate(event.keyframe_ids):
                ek_key = (event.event_id, seq_no)
                ek_payload = {
                    "keyframe_id": kf_id,
                    "keyframe_embedding_index_0": event.keyframe_indices[seq_no] if seq_no < len(event.keyframe_indices) else 0,
                }
                if ek_key in existing_ek:
                    for key, value in ek_payload.items():
                        setattr(existing_ek[ek_key], key, value)
                else:
                    self.db.add(EventKeyframe(
                        event_id=event.event_id,
                        seq_no=seq_no,
                        **ek_payload,
                    ))
        self.db.flush()

        # Upsert event embeddings into Milvus
        if self.vector_client is None:
            return

        event_payload: list[tuple[str, list[float], dict[str, Any]]] = []
        for event in events:
            if event.embedding:
                event_payload.append((
                    event.event_id,
                    event.embedding,
                    {
                        "event_id": event.event_id,
                        "video_id": video_id,
                        "model_version": f"event-seg-{SEGMENTATION_VERSION}",
                    },
                ))
        if event_payload:
            self.vector_client.upsert("event_embeddings", event_payload)

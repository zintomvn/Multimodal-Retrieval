from __future__ import annotations

import argparse
import csv
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from sqlalchemy.orm import Session

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.db.models import Dataset, Frame, Shot, Video


DEFAULT_KEYFRAMES_PREFIX = "processed/keyframes"
DEFAULT_MANIFESTS_PREFIX = "processed/keyframes_manifests"
DEFAULT_RAW_PREFIX = "raw/source=kaggle"
DEFAULT_SOURCE_VERSION = "kaggle_current"


@dataclass(frozen=True)
class DatasetImportConfig:
    dataset_id: str
    dataset_code: str
    dataset_name: str
    dataset_version: str
    bucket_name: str
    keyframes_prefix: str
    manifests_prefix: str
    profile_version: str
    source_version: str
    raw_prefix: str
    public_urls: bool
    metadata_import_run_id: str


@dataclass(frozen=True)
class BatchSource:
    batch_id: str
    run_id: str
    shot_segments_uri: str
    success_uri: str


@dataclass(frozen=True)
class LoadedBatch:
    source: BatchSource
    rows: list[dict[str, str]]


def utc_now() -> str:
    """Return the current UTC timestamp in ISO-8601 form."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_prefix(value: str) -> str:
    """Normalize a GCS object prefix without leading or trailing slash."""
    return str(value or "").replace("\\", "/").strip("/")


def split_batches(raw: str) -> list[str]:
    """Parse a comma-separated batch list."""
    return [item.strip().upper() for item in str(raw or "").split(",") if item.strip()]


def bool_value(raw: Any, default: bool = False) -> bool:
    """Convert common CSV truthy values into a bool."""
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "t", "yes", "y"}


def int_value(raw: Any, default: int = 0) -> int:
    """Convert a nullable CSV value into an int."""
    if raw is None or raw == "":
        return default
    return int(float(raw))


def float_value(raw: Any, default: float = 0.0) -> float:
    """Convert a nullable CSV value into a float."""
    if raw is None or raw == "":
        return default
    return float(raw)


def parse_shot_index(row: dict[str, Any]) -> int:
    """Return a shot index from shot_id_local or a formatted shot_id."""
    if row.get("shot_id_local") not in (None, ""):
        return int_value(row.get("shot_id_local"))
    raw = str(row.get("shot_id") or "")
    marker = "_S"
    if marker in raw:
        return int_value(raw.rsplit(marker, 1)[1])
    return int_value(raw, 0)


def parse_gcs_uri(uri: str) -> tuple[str, str] | None:
    """Return (bucket, object_key) for a gs:// URI."""
    raw = (uri or "").strip()
    if not raw.startswith("gs://"):
        return None
    tail = raw[len("gs://") :].strip("/")
    if "/" not in tail:
        return None
    bucket, key = tail.split("/", 1)
    return (bucket, key.strip("/")) if bucket and key else None


def object_key_from_uri(uri: str) -> str:
    """Extract the object key from a GCS URI."""
    parsed = parse_gcs_uri(uri)
    return parsed[1] if parsed else ""


def make_gcs_uri(bucket_name: str, object_key: str) -> str:
    """Build a gs:// URI from a bucket name and object key."""
    return f"gs://{bucket_name}/{normalize_prefix(object_key)}"


def make_public_url(bucket_name: str, object_key: str) -> str:
    """Build a public storage.googleapis.com URL."""
    encoded = "/".join(part for part in PurePosixPath(normalize_prefix(object_key)).parts)
    return f"https://storage.googleapis.com/{bucket_name}/{encoded}"


def deterministic_dataset_id(dataset_code: str, dataset_version: str) -> str:
    """Create a stable UUID for the dataset row when no DB id is provided."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"dataset:{dataset_code}:{dataset_version}"))


class GCSManifestSource:
    """Read frame metadata manifests from Google Cloud Storage."""

    def __init__(self, bucket_name: str, credentials_file: str = "", timeout: float = 60.0) -> None:
        from google.cloud import storage

        self.bucket_name = bucket_name
        self.timeout = timeout
        self.client = (
            storage.Client.from_service_account_json(credentials_file)
            if credentials_file
            else storage.Client()
        )
        self.bucket = self.client.bucket(bucket_name)

    def explicit_source(
        self,
        batch_id: str,
        run_id: str,
        config: DatasetImportConfig,
    ) -> BatchSource:
        """Build a batch source from an explicit run id."""
        prefix = manifest_run_prefix(config, batch_id=batch_id, run_id=run_id)
        success_key = f"{prefix}_SUCCESS"
        if not self.bucket.blob(success_key).exists(timeout=self.timeout):
            raise FileNotFoundError(f"Missing _SUCCESS for batch={batch_id} run_id={run_id}: gs://{self.bucket_name}/{success_key}")
        shot_key = f"{prefix}shot_segments.csv"
        if not self.bucket.blob(shot_key).exists(timeout=self.timeout):
            raise FileNotFoundError(f"Missing shot_segments.csv for batch={batch_id} run_id={run_id}: gs://{self.bucket_name}/{shot_key}")
        return BatchSource(
            batch_id=batch_id,
            run_id=run_id,
            shot_segments_uri=make_gcs_uri(self.bucket_name, shot_key),
            success_uri=make_gcs_uri(self.bucket_name, success_key),
        )

    def discover_latest_successful_source(
        self,
        batch_id: str,
        config: DatasetImportConfig,
    ) -> BatchSource | None:
        """Find the latest manifest run with both _SUCCESS and shot_segments.csv."""
        root_prefix = manifest_batch_prefix(config, batch_id)
        blobs = list(self.client.list_blobs(self.bucket_name, prefix=root_prefix, timeout=self.timeout))
        success_prefixes: dict[str, Any] = {}
        shot_blobs: dict[str, Any] = {}
        for blob in blobs:
            name = blob.name
            if name.endswith("/_SUCCESS"):
                success_prefixes[name[: -len("_SUCCESS")]] = blob
            elif name.endswith("/shot_segments.csv"):
                shot_blobs[name[: -len("shot_segments.csv")]] = blob

        candidates = [
            (prefix, shot_blobs[prefix], success_prefixes[prefix])
            for prefix in sorted(set(shot_blobs) & set(success_prefixes))
        ]
        if not candidates:
            return None

        prefix, shot_blob, success_blob = max(
            candidates,
            key=lambda item: item[1].updated or item[2].updated or datetime.min.replace(tzinfo=timezone.utc),
        )
        run_part = [part for part in PurePosixPath(prefix.rstrip("/")).parts if part.startswith("run_id=")]
        run_id = run_part[-1].split("=", 1)[1] if run_part else ""
        return BatchSource(
            batch_id=batch_id,
            run_id=run_id,
            shot_segments_uri=make_gcs_uri(self.bucket_name, shot_blob.name),
            success_uri=make_gcs_uri(self.bucket_name, success_blob.name),
        )

    def read_csv_rows(self, source: BatchSource) -> list[dict[str, str]]:
        """Read rows from a batch shot_segments.csv manifest."""
        parsed = parse_gcs_uri(source.shot_segments_uri)
        if parsed is None:
            raise ValueError(f"Expected GCS URI: {source.shot_segments_uri}")
        bucket, key = parsed
        text = self.client.bucket(bucket).blob(key).download_as_text(timeout=self.timeout)
        return [dict(row) for row in csv.DictReader(StringIO(text))]

    def object_exists(self, object_key: str) -> bool:
        """Return whether a frame object exists in the configured bucket."""
        return self.bucket.blob(normalize_prefix(object_key)).exists(timeout=self.timeout)


def keyframes_dataset_root(config: DatasetImportConfig) -> str:
    """Return the dataset root URI for keyframes in GCS."""
    return make_gcs_uri(
        config.bucket_name,
        f"{normalize_prefix(config.keyframes_prefix)}/dataset={config.dataset_id}",
    )


def manifest_batch_prefix(config: DatasetImportConfig, batch_id: str) -> str:
    """Return the GCS prefix containing manifest runs for one batch."""
    return (
        f"{normalize_prefix(config.manifests_prefix)}/"
        f"dataset={config.dataset_id}/"
        f"batch={batch_id}/"
        f"profile={config.profile_version}/"
    )


def manifest_run_prefix(config: DatasetImportConfig, batch_id: str, run_id: str) -> str:
    """Return the GCS prefix for one manifest run."""
    return f"{manifest_batch_prefix(config, batch_id)}run_id={run_id.strip('/')}/"


def fallback_video_uri(row: dict[str, Any], config: DatasetImportConfig, batch_id: str, video_id: str) -> str:
    """Build a best-effort raw video URI when the manifest row lacks one."""
    video_name = str(row.get("video_name") or f"{video_id}.mp4").strip()
    if video_name.startswith("gs://"):
        return video_name
    raw_prefix = normalize_prefix(config.raw_prefix)
    return make_gcs_uri(
        config.bucket_name,
        f"{raw_prefix}/dataset={config.dataset_id}/source_version={config.source_version}/batch={batch_id}/{video_name}",
    )


def normalize_shot_row(
    row: dict[str, Any],
    config: DatasetImportConfig,
    source: BatchSource,
) -> dict[str, Any] | None:
    """Normalize one shot_segments.csv row into backend metadata fields."""
    video_id = str(row.get("video_id") or Path(str(row.get("video_name") or "")).stem).strip()
    if not video_id:
        return None

    frame_idx = int_value(row.get("frame_idx"), default=-1)
    if frame_idx < 0:
        return None

    image_storage_key = normalize_prefix(row.get("image_storage_key") or object_key_from_uri(str(row.get("image_gcs_uri") or "")))
    image_gcs_uri = str(row.get("image_gcs_uri") or "").strip()
    if not image_gcs_uri and image_storage_key:
        image_gcs_uri = make_gcs_uri(config.bucket_name, image_storage_key)

    image_rel_path = str(row.get("image_rel_path") or "").strip()
    if not image_rel_path:
        image_rel_path = f"{video_id}/{PurePosixPath(image_storage_key).name}"

    shot_index = parse_shot_index(row)
    shot_id = str(row.get("shot_id") or "").strip()
    if not shot_id or not shot_id.startswith(f"{video_id}_S"):
        shot_id = f"{video_id}_S{shot_index:04d}"

    frame_sec = float_value(row.get("frame_sec"), default=0.0)
    video_gcs_uri = str(row.get("video_gcs_uri") or "").strip() or fallback_video_uri(row, config, source.batch_id, video_id)
    keyframe_id = str(row.get("keyframe_id") or f"{video_id}_F{frame_idx:06d}").strip()

    return {
        "batch_id": str(row.get("batch_id") or source.batch_id),
        "source_run_id": source.run_id,
        "source_shot_segments_uri": source.shot_segments_uri,
        "video_id": video_id,
        "video_name": str(row.get("video_name") or f"{video_id}.mp4").strip(),
        "video_gcs_uri": video_gcs_uri,
        "fps": float_value(row.get("fps"), default=0.0),
        "total_frames_opencv": int_value(row.get("total_frames_opencv"), default=0),
        "shot_id": shot_id,
        "shot_index": shot_index,
        "shot_start_frame": int_value(row.get("shot_start_frame"), default=frame_idx),
        "shot_end_frame": int_value(row.get("shot_end_frame"), default=frame_idx),
        "shot_start_sec": float_value(row.get("shot_start_sec"), default=frame_sec),
        "shot_end_sec": float_value(row.get("shot_end_sec"), default=frame_sec),
        "boundary_threshold": float_value(row.get("boundary_threshold"), default=0.0),
        "frame_type": str(row.get("frame_type") or "middle").strip() or "middle",
        "frame_idx": frame_idx,
        "frame_seconds": frame_sec,
        "timestamp_ms": int(frame_sec * 1000),
        "keyframe_id": keyframe_id,
        "image_rel_path": image_rel_path,
        "image_storage_key": image_storage_key,
        "image_uri": image_gcs_uri,
        "saved": bool_value(row.get("saved"), default=True),
    }


def normalize_loaded_batches(loaded_batches: Iterable[LoadedBatch], config: DatasetImportConfig) -> list[dict[str, Any]]:
    """Normalize all loaded CSV rows and drop only invalid frame rows."""
    normalized: list[dict[str, Any]] = []
    for loaded in loaded_batches:
        for row in loaded.rows:
            item = normalize_shot_row(row, config, loaded.source)
            if item is not None:
                normalized.append(item)
    return sorted(normalized, key=lambda item: (item["batch_id"], item["video_id"], item["frame_idx"], item["frame_type"]))


def limit_rows_by_videos(rows: list[dict[str, Any]], max_videos: int) -> list[dict[str, Any]]:
    """Keep rows for the first N videos in stable batch/video order."""
    if max_videos <= 0:
        return rows
    selected: set[str] = set()
    for row in rows:
        if row["video_id"] in selected:
            continue
        selected.add(row["video_id"])
        if len(selected) >= max_videos:
            break
    return [row for row in rows if row["video_id"] in selected]


def verify_object_presence(
    rows: list[dict[str, Any]],
    object_exists: Callable[[str], bool],
    workers: int,
) -> tuple[dict[str, bool], list[dict[str, Any]]]:
    """Verify frame objects on GCS without downloading bytes."""
    keys = sorted({row["image_storage_key"] for row in rows if row.get("image_storage_key")})
    present: dict[str, bool] = {}
    failures: list[dict[str, Any]] = []

    def check(key: str) -> tuple[str, bool]:
        return key, object_exists(key)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(check, key): key for key in keys}
        for future in as_completed(futures):
            key = futures[future]
            try:
                checked_key, exists = future.result()
                present[checked_key] = exists
                if not exists:
                    failures.append({"stage": "verify_objects", "reason": "missing_gcs_object", "image_storage_key": checked_key})
            except Exception as exc:  # noqa: BLE001 - keep scanning remaining objects.
                present[key] = False
                failures.append(
                    {
                        "stage": "verify_objects",
                        "reason": "object_check_failed",
                        "image_storage_key": key,
                        "error": str(exc),
                    }
                )
    return present, failures


def build_payloads(
    rows: list[dict[str, Any]],
    config: DatasetImportConfig,
    object_presence: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Build DB payload dictionaries from normalized frame rows."""
    dataset_id = deterministic_dataset_id(config.dataset_code, config.dataset_version)
    dataset_payload = {
        "dataset_id": dataset_id,
        "dataset_code": config.dataset_code,
        "name": config.dataset_name,
        "version": config.dataset_version,
        "root_uri": keyframes_dataset_root(config),
        "status": "READY",
    }

    videos: dict[str, dict[str, Any]] = {}
    shots: dict[str, dict[str, Any]] = {}
    frames: dict[str, dict[str, Any]] = {}
    rows_by_video: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_video.setdefault(row["video_id"], []).append(row)

    for video_id, video_rows in rows_by_video.items():
        first = video_rows[0]
        duration_seconds = max(float(item["shot_end_sec"]) for item in video_rows)
        fps_values = [float(item["fps"]) for item in video_rows if float(item.get("fps") or 0) > 0]
        total_frames_values = [int(item["total_frames_opencv"]) for item in video_rows if int(item.get("total_frames_opencv") or 0) > 0]
        videos[video_id] = {
            "dataset_id": dataset_id,
            "video_code": video_id,
            "video_name": first["video_name"],
            "uri": first["video_gcs_uri"],
            "source_video_path": first["video_gcs_uri"],
            "fps": fps_values[0] if fps_values else None,
            "duration_seconds": duration_seconds,
            "duration_ms": int(duration_seconds * 1000),
            "num_keyframes": len(video_rows),
            "extra_metadata": {
                "batch_id": first["batch_id"],
                "profile_version": config.profile_version,
                "frame_metadata_run_id": config.metadata_import_run_id,
                "source_frame_extraction_run_id": first["source_run_id"],
                "source_shot_segments_uri": first["source_shot_segments_uri"],
                "total_frames_opencv": max(total_frames_values) if total_frames_values else None,
                "imported_at": utc_now(),
            },
        }

    for row in rows:
        shots[row["shot_id"]] = {
            "video_id": row["video_id"],
            "shot_index": row["shot_index"],
            "start_frame": row["shot_start_frame"],
            "end_frame": row["shot_end_frame"],
            "start_seconds": row["shot_start_sec"],
            "end_seconds": row["shot_end_sec"],
            "boundary_threshold": row["boundary_threshold"],
        }
        key = row["image_storage_key"]
        if object_presence is None:
            is_media_present = bool(row["saved"])
        else:
            is_media_present = bool(object_presence.get(key, False))
        frames[row["keyframe_id"]] = {
            "video_id": row["video_id"],
            "shot_id": row["shot_id"],
            "frame_idx": row["frame_idx"],
            "frame_seconds": row["frame_seconds"],
            "timestamp_ms": row["timestamp_ms"],
            "frame_type": row["frame_type"],
            "image_rel_path": row["image_rel_path"],
            "image_storage_key": key,
            "image_url": make_public_url(config.bucket_name, key) if config.public_urls and key else None,
            "image_uri": row["image_uri"] or make_gcs_uri(config.bucket_name, key),
            "thumbnail_uri": None,
            "quality_score": 1.0,
            "is_media_present": is_media_present,
        }

    return {
        "dataset": dataset_payload,
        "videos": videos,
        "shots": shots,
        "frames": frames,
    }


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    """Yield list chunks."""
    for index in range(0, len(items), size):
        yield items[index : index + size]


def upsert_payloads(db: Session, payloads: dict[str, Any]) -> dict[str, int]:
    """Upsert dataset, video, shot, and keyframe metadata into PostgreSQL."""
    counts = {
        "datasets_inserted": 0,
        "datasets_updated": 0,
        "videos_inserted": 0,
        "videos_updated": 0,
        "shots_inserted": 0,
        "shots_updated": 0,
        "keyframes_inserted": 0,
        "keyframes_updated": 0,
    }

    dataset_payload = payloads["dataset"]
    dataset = db.query(Dataset).filter(Dataset.dataset_code == dataset_payload["dataset_code"]).first()
    if dataset is None:
        dataset = Dataset(**dataset_payload)
        db.add(dataset)
        active_dataset_id = dataset_payload["dataset_id"]
        counts["datasets_inserted"] += 1
    else:
        for key, value in dataset_payload.items():
            if key == "dataset_id":
                continue
            setattr(dataset, key, value)
        active_dataset_id = dataset.dataset_id
        counts["datasets_updated"] += 1
    db.flush()

    videos: dict[str, dict[str, Any]] = payloads["videos"]
    video_ids = list(videos)
    existing_videos = {
        item.video_id: item
        for chunk in _chunks(video_ids, 500)
        for item in db.query(Video).filter(Video.video_id.in_(chunk)).all()
    }
    for video_id, payload in videos.items():
        payload = {**payload, "dataset_id": active_dataset_id}
        existing = existing_videos.get(video_id)
        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
            counts["videos_updated"] += 1
        else:
            db.add(Video(video_id=video_id, **payload))
            counts["videos_inserted"] += 1
    db.flush()

    shots: dict[str, dict[str, Any]] = payloads["shots"]
    shot_ids = list(shots)
    existing_shots = {
        item.shot_id: item
        for chunk in _chunks(shot_ids, 500)
        for item in db.query(Shot).filter(Shot.shot_id.in_(chunk)).all()
    }
    for shot_id, payload in shots.items():
        existing = existing_shots.get(shot_id)
        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
            counts["shots_updated"] += 1
        else:
            db.add(Shot(shot_id=shot_id, **payload))
            counts["shots_inserted"] += 1
    db.flush()

    frames: dict[str, dict[str, Any]] = payloads["frames"]
    frame_ids = list(frames)
    existing_frames = {
        item.keyframe_id: item
        for chunk in _chunks(frame_ids, 500)
        for item in db.query(Frame).filter(Frame.keyframe_id.in_(chunk)).all()
    }
    for keyframe_id, payload in frames.items():
        existing = existing_frames.get(keyframe_id)
        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
            counts["keyframes_updated"] += 1
        else:
            db.add(Frame(keyframe_id=keyframe_id, **payload))
            counts["keyframes_inserted"] += 1
    db.commit()
    return counts


def summarize_payloads(payloads: dict[str, Any]) -> dict[str, int]:
    """Return row counts for the prepared import payload."""
    return {
        "datasets": 1,
        "videos": len(payloads["videos"]),
        "shots": len(payloads["shots"]),
        "keyframes": len(payloads["frames"]),
    }


def load_batches_from_gcs(
    source: GCSManifestSource,
    config: DatasetImportConfig,
    batch_ids: list[str],
    run_id: str,
) -> tuple[list[LoadedBatch], list[dict[str, Any]]]:
    """Load shot segment rows from GCS for all requested batches."""
    loaded: list[LoadedBatch] = []
    failures: list[dict[str, Any]] = []
    for batch_id in batch_ids:
        try:
            batch_source = (
                source.explicit_source(batch_id, run_id, config)
                if run_id
                else source.discover_latest_successful_source(batch_id, config)
            )
            if batch_source is None:
                failures.append(
                    {
                        "stage": "discover",
                        "reason": "missing_successful_run",
                        "batch_id": batch_id,
                        "prefix": make_gcs_uri(config.bucket_name, manifest_batch_prefix(config, batch_id)),
                    }
                )
                continue
            rows = source.read_csv_rows(batch_source)
            loaded.append(LoadedBatch(source=batch_source, rows=rows))
        except Exception as exc:  # noqa: BLE001 - report every failed batch.
            failures.append(
                {
                    "stage": "load_batch",
                    "reason": "batch_load_failed",
                    "batch_id": batch_id,
                    "error": str(exc),
                }
            )
    return loaded, failures


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Import GCS keyframe metadata into PostgreSQL.")
    parser.add_argument("--dataset-id", default="ai_challenge_2025")
    parser.add_argument("--dataset-code", default="aic-2026")
    parser.add_argument("--dataset-name", default="aic-ai-challenge-2025")
    parser.add_argument("--dataset-version", default="v1")
    parser.add_argument("--batches", default="L21")
    parser.add_argument("--profile-version", default="autoshot_v1")
    parser.add_argument("--run-id", default="", help="Optional frame extraction run id to use for every batch.")
    parser.add_argument("--auto-discover-run", action="store_true", help="Compatibility flag; discovery is default when --run-id is empty.")
    parser.add_argument("--gcs-bucket", default="", help="GCS bucket. Defaults to GCS_BUCKET from .env.")
    parser.add_argument("--gcs-credentials-file", default="", help="Service account JSON file. Defaults to GCS_CREDENTIALS_FILE.")
    parser.add_argument("--keyframes-prefix", default=DEFAULT_KEYFRAMES_PREFIX)
    parser.add_argument("--manifests-prefix", default=DEFAULT_MANIFESTS_PREFIX)
    parser.add_argument("--raw-prefix", default=DEFAULT_RAW_PREFIX)
    parser.add_argument("--source-version", default=DEFAULT_SOURCE_VERSION)
    parser.add_argument("--gcs-timeout", type=float, default=60.0)
    parser.add_argument("--max-videos", type=int, default=0, help="Limit import to the first N videos after loading manifests; useful for smoke tests.")
    parser.add_argument("--verify-objects", action="store_true", help="Check that every image_storage_key exists on GCS.")
    parser.add_argument("--verify-workers", type=int, default=16)
    parser.add_argument("--public-urls", action="store_true", help="Populate keyframes.image_url with public GCS URLs.")
    parser.add_argument("--dry-run", action="store_true", help="Prepare and report metadata without writing PostgreSQL.")
    return parser.parse_args()


def main() -> None:
    """Run the GCS frame metadata import."""

    # Config and parameters
    args = parse_args()
    settings = get_settings()
    bucket_name = args.gcs_bucket or settings.gcs_bucket
    if not bucket_name:
        raise RuntimeError("Set --gcs-bucket or GCS_BUCKET.")
    batch_ids = split_batches(args.batches)
    if not batch_ids:
        raise RuntimeError("Set at least one batch id via --batches.")

    config = DatasetImportConfig(
        dataset_id=args.dataset_id,
        dataset_code=args.dataset_code,
        dataset_name=args.dataset_name,
        dataset_version=args.dataset_version,
        bucket_name=bucket_name,
        keyframes_prefix=args.keyframes_prefix,
        manifests_prefix=args.manifests_prefix,
        profile_version=args.profile_version,
        source_version=args.source_version,
        raw_prefix=args.raw_prefix,
        public_urls=args.public_urls,
        metadata_import_run_id=f"gcs_frame_metadata_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
    )
    source = GCSManifestSource(
        bucket_name=bucket_name,
        credentials_file=args.gcs_credentials_file or settings.gcs_credentials_file,
        timeout=args.gcs_timeout,
    )
    # Load batches
    loaded_batches, failures = load_batches_from_gcs(source, config, batch_ids=batch_ids, run_id=args.run_id)
    rows = normalize_loaded_batches(loaded_batches, config)
    loaded_frame_rows = len(rows)
    rows = limit_rows_by_videos(rows, args.max_videos)
    object_presence = None

    # Satisfy 2 conditions: verify_objects and rows
    if args.verify_objects and rows:
        object_presence, verify_failures = verify_object_presence(
            rows,
            object_exists=source.object_exists,
            workers=args.verify_workers,
        )
        failures.extend(verify_failures)

    # Payloads
    payloads = build_payloads(rows, config, object_presence=object_presence)


    summary = {
        "dry_run": args.dry_run,
        "dataset": payloads["dataset"],
        "batches_requested": batch_ids,
        "batches_loaded": [
            {
                "batch_id": item.source.batch_id,
                "run_id": item.source.run_id,
                "shot_segments_uri": item.source.shot_segments_uri,
                "rows": len(item.rows),
            }
            for item in loaded_batches
        ],
        "loaded_frame_rows": loaded_frame_rows,
        "max_videos": args.max_videos,
        "prepared_rows": summarize_payloads(payloads),
        "verify_objects": bool(args.verify_objects),
        "failures": failures,
        "created_at": utc_now(),
    }

    if args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    # Database
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        summary["upsert"] = upsert_payloads(db, payloads)
    finally:
        db.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

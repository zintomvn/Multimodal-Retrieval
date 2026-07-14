"""Extract AutoShot keyframes from raw videos stored in Google Cloud Storage.

This module implements the cloud-oriented video-to-frame plan documented in
``docs/data_processing/feature_extraction/1_video_to_frame_cloud_plan.md``.

The CLI is intentionally split into small pipeline stages:

* ``doctor`` validates GCS access and prints the observed raw-object format.
* ``discover`` lists raw videos and writes ``processing_manifest.jsonl`` plus
  shard files.
* ``extract`` reads one manifest shard, downloads each video to local scratch,
  runs AutoShot, uploads first/middle/last keyframes, and writes shard results.
* ``merge`` merges shard outputs, runs quality gates, writes final artifacts,
  and creates ``_SUCCESS`` only when the batch is valid.

AutoShot, OpenCV, and PyTorch are imported lazily so that manifest discovery and
small GCS smoke tests can run on lightweight machines.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import logging
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


# Configs

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
DEFAULT_RAW_PREFIX = "raw/source=kaggle"
DEFAULT_KEYFRAMES_PREFIX = "processed/keyframes"
DEFAULT_MANIFESTS_PREFIX = "processed/keyframes_manifests"
DEFAULT_PROFILE_VERSION = "autoshot_v1"
DEFAULT_SOURCE_VERSION = "kaggle_current"
DEFAULT_THRESHOLD = 0.296
DEFAULT_MIN_SHOT_LEN = 5
DEFAULT_VIDEOS_PER_SHARD = 16
LOADERS_DIR = Path(__file__).resolve().parent
DEFAULT_RUN_DIR = LOADERS_DIR / "frame_extraction_runs"
DEFAULT_AUTOSHOT_REPO_DIR = LOADERS_DIR / "AutoShot"

SHOT_SEGMENTS_COLUMNS = [
    "dataset_id",
    "batch_id",
    "video_id",
    "video_name",
    "video_gcs_uri",
    "video_gcs_generation",
    "shot_id",
    "shot_id_local",
    "shot_start_frame",
    "shot_end_frame",
    "shot_start_sec",
    "shot_end_sec",
    "frame_type",
    "frame_idx",
    "frame_sec",
    "keyframe_id",
    "image_rel_path",
    "image_gcs_uri",
    "image_storage_key",
    "boundary_threshold",
    "min_shot_len",
    "saved",
    "fps",
    "total_frames_opencv",
    "profile_version",
    "run_id",
]


@dataclass(frozen=True)
class GcsUri:
    """Parsed representation of a ``gs://bucket/object`` URI."""

    bucket: str
    blob_name: str

    @property
    def uri(self) -> str:
        """Return the canonical GCS URI."""

        return f"gs://{self.bucket}/{self.blob_name}"


@dataclass(frozen=True)
class RunLayout:
    """Local and GCS artifact layout for one extraction run."""

    run_dir: Path
    manifest_path: Path
    shards_dir: Path
    results_dir: Path
    summary_path: Path
    errors_path: Path
    gcs_artifact_prefix: str


# Logs format 

def utc_now_iso() -> str:
    """Return a compact UTC timestamp suitable for manifests and logs."""

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_run_id() -> str:
    """Create a run id that is sortable by time and unique enough for retries."""

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_{uuid.uuid4().hex[:8]}"


def normalize_prefix(prefix: str) -> str:
    """Normalize a GCS prefix by removing leading and trailing slashes."""

    return prefix.strip().strip("/")


def parse_gcs_uri(value: str) -> GcsUri:
    """Parse a GCS URI and raise ``ValueError`` when the format is invalid."""

    if not value.startswith("gs://"):
        raise ValueError(f"Expected a GCS URI starting with gs://, got: {value}")
    without_scheme = value[len("gs://") :]
    bucket, sep, blob_name = without_scheme.partition("/")
    if not bucket or not sep or not blob_name:
        raise ValueError(f"Invalid GCS URI: {value}")
    return GcsUri(bucket=bucket, blob_name=blob_name)


def load_env_file(path: Path) -> dict[str, str]:
    """Load key-value pairs from a dotenv-style file without overwriting env."""

    loaded: dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"Env file not found: {path}")

    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                raise ValueError(f"Invalid env line {line_number} in {path}: missing '='")
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                loaded[key] = value

    for key, value in loaded.items():
        os.environ.setdefault(key, value)
    return loaded


def resolve_path(value: str, base_dir: Path | None = None) -> str:
    """Resolve a potentially relative filesystem path."""

    if not value:
        return ""
    path = Path(value)
    if path.is_absolute():
        return str(path)
    if base_dir is not None:
        return str((base_dir / path).resolve())
    return str(path.resolve())


def configure_logging(verbose: bool) -> None:
    """Configure human-readable logs for CLI execution."""

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


# Connect GCS and format folder in GCS

def make_storage_client(args: argparse.Namespace):
    """Create a Google Cloud Storage client from args, env vars, or ADC."""

    try:
        from google.cloud import storage
    except ImportError as exc:  # pragma: no cover - environment dependent.
        raise RuntimeError("Missing dependency. Install project dependencies with: pip install -r requirements.txt") from exc

    credentials_file = args.gcs_credentials_file or os.environ.get("GCS_CREDENTIALS_FILE", "")
    credentials_file = resolve_path(credentials_file, getattr(args, "env_file_dir", None))
    credentials_json = os.environ.get("GCS_CREDENTIALS_JSON", "")

    if credentials_json:
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_info(json.loads(credentials_json))
        return storage.Client(project=credentials.project_id, credentials=credentials)
    if credentials_file:
        return storage.Client.from_service_account_json(credentials_file)
    return storage.Client()


def resolve_bucket_name(args: argparse.Namespace) -> str:
    """Resolve the target bucket from CLI or ``GCS_BUCKET``."""

    bucket = args.gcs_bucket or os.environ.get("GCS_BUCKET", "")
    if bucket.startswith("gs://"):
        return parse_gcs_uri(bucket.rstrip("/") + "/_").bucket
    if not bucket:
        raise RuntimeError("Set --gcs-bucket or GCS_BUCKET.")
    return bucket.strip()


def build_raw_batch_prefix(raw_prefix: str, dataset_id: str, source_version: str, batch_id: str) -> str:
    """Build the raw GCS prefix used by the Kaggle upload pipeline."""

    return (
        f"{normalize_prefix(raw_prefix)}/"
        f"dataset={dataset_id}/"
        f"source_version={source_version}/"
        f"batch={batch_id}/"
    )


def build_keyframe_prefix(keyframes_prefix: str, dataset_id: str, batch_id: str, profile_version: str, video_id: str) -> str:
    """Build the final GCS prefix for one video's extracted keyframes."""

    return (
        f"{normalize_prefix(keyframes_prefix)}/"
        f"dataset={dataset_id}/"
        f"batch={batch_id}/"
        f"profile={profile_version}/"
        f"video_id={video_id}/"
    )


def build_artifact_prefix(manifests_prefix: str, dataset_id: str, batch_id: str, profile_version: str, run_id: str) -> str:
    """Build the GCS prefix for run-level manifests and result artifacts."""

    return (
        f"{normalize_prefix(manifests_prefix)}/"
        f"dataset={dataset_id}/"
        f"batch={batch_id}/"
        f"profile={profile_version}/"
        f"run_id={run_id}/"
    )


def make_run_layout(args: argparse.Namespace, run_id: str) -> RunLayout:
    """Create local directories and return paths for one pipeline run."""

    run_dir = Path(args.run_dir) / run_id
    shards_dir = run_dir / "shards"
    results_dir = run_dir / "results"
    shards_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    artifact_prefix = build_artifact_prefix(
        args.manifests_prefix,
        args.dataset_id,
        args.batch_id,
        args.profile_version,
        run_id,
    )
    return RunLayout(
        run_dir=run_dir,
        manifest_path=run_dir / "processing_manifest.jsonl",
        shards_dir=shards_dir,
        results_dir=results_dir,
        summary_path=run_dir / "summary.json",
        errors_path=run_dir / "errors.jsonl",
        gcs_artifact_prefix=artifact_prefix,
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a JSON object with stable formatting."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Write newline-delimited JSON rows."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl_text(text: str) -> list[dict[str, Any]]:
    """Parse newline-delimited JSON text."""

    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at line {line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"JSONL line {line_number} must be an object")
        rows.append(row)
    return rows


def read_jsonl_uri(client: Any, uri_or_path: str) -> list[dict[str, Any]]:
    """Read JSONL rows from a local path or a GCS URI."""

    if uri_or_path.startswith("gs://"):
        gcs_uri = parse_gcs_uri(uri_or_path)
        bucket = client.bucket(gcs_uri.bucket)
        text = bucket.blob(gcs_uri.blob_name).download_as_text(encoding="utf-8")
        return read_jsonl_text(text)
    return read_jsonl_text(Path(uri_or_path).read_text(encoding="utf-8"))


def write_csv_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    """Write CSV rows using the provided field order."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    """Read all CSV rows as dictionaries."""

    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def upload_file(bucket: Any, local_path: Path, object_key: str, content_type: str, overwrite: bool = True) -> None:
    """Upload one local file to GCS with an optional create-only precondition."""

    blob = bucket.blob(object_key)
    kwargs: dict[str, Any] = {"content_type": content_type, "timeout": 600}
    if not overwrite:
        kwargs["if_generation_match"] = 0
    blob.upload_from_filename(str(local_path), **kwargs)


def upload_text(bucket: Any, text: str, object_key: str, content_type: str = "text/plain") -> None:
    """Upload a UTF-8 text object to GCS."""

    bucket.blob(object_key).upload_from_string(text, content_type=content_type, timeout=600)


def discover_raw_videos(
    client: Any,
    bucket_name: str,
    run_id: str,
    dataset_id: str,
    batch_id: str,
    source_version: str,
    raw_prefix: str,
    keyframes_prefix: str,
    profile_version: str,
    threshold: float,
    min_shot_len: int,
    max_videos: int | None,
) -> list[dict[str, Any]]:
    """List raw videos in GCS and convert them into processing records."""

    bucket = client.bucket(bucket_name)
    batch_prefix = build_raw_batch_prefix(raw_prefix, dataset_id, source_version, batch_id)
    logging.info("listing raw videos gs://%s/%s", bucket_name, batch_prefix)

    records: list[dict[str, Any]] = []
    seen_video_ids: set[str] = set()

    for blob in bucket.list_blobs(prefix=batch_prefix):
        suffix = PurePosixPath(blob.name).suffix.lower()
        if suffix not in VIDEO_EXTENSIONS:
            continue
        video_id = PurePosixPath(blob.name).stem
        if video_id in seen_video_ids:
            raise RuntimeError(f"Duplicate video_id in raw prefix: {video_id}")
        seen_video_ids.add(video_id)

        output_prefix = build_keyframe_prefix(
            keyframes_prefix=keyframes_prefix,
            dataset_id=dataset_id,
            batch_id=batch_id,
            profile_version=profile_version,
            video_id=video_id,
        )
        record = {
            "manifest_schema_version": 1,
            "run_id": run_id,
            "dataset_id": dataset_id,
            "batch_id": batch_id,
            "source_version": source_version,
            "video_id": video_id,
            "video_name": PurePosixPath(blob.name).name,
            "relative_path": blob.name[len(batch_prefix) :],
            "input_gcs_uri": f"gs://{bucket_name}/{blob.name}",
            "input_generation": str(blob.generation or ""),
            "input_size_bytes": int(blob.size or 0),
            "input_crc32c": blob.crc32c or "",
            "output_bucket": bucket_name,
            "output_prefix": output_prefix,
            "profile_version": profile_version,
            "threshold": threshold,
            "min_shot_len": min_shot_len,
            "status": "planned",
            "planned_at": utc_now_iso(),
        }
        records.append(record)
        if max_videos is not None and len(records) >= max_videos:
            break

    return sorted(records, key=lambda row: row["input_gcs_uri"])


def split_shards(records: list[dict[str, Any]], videos_per_shard: int) -> list[list[dict[str, Any]]]:
    """Split manifest records into fixed-size shards."""

    if videos_per_shard < 1:
        raise ValueError("--videos-per-shard must be >= 1")
    return [records[index : index + videos_per_shard] for index in range(0, len(records), videos_per_shard)]


def write_discovery_artifacts(layout: RunLayout, records: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    """Write local processing manifest, shard files, and discovery summary."""

    write_jsonl(layout.manifest_path, records)
    write_jsonl(layout.errors_path, [])

    shards = split_shards(records, args.videos_per_shard)
    shard_paths = []
    for shard_index, shard_records in enumerate(shards):
        shard_path = layout.shards_dir / f"shard-{shard_index:05d}.jsonl"
        write_jsonl(shard_path, shard_records)
        shard_paths.append(shard_path)

    summary = {
        "run_id": args.run_id,
        "created_at": utc_now_iso(),
        "stage": "discover",
        "dataset_id": args.dataset_id,
        "batch_id": args.batch_id,
        "source_version": args.source_version,
        "profile_version": args.profile_version,
        "raw_prefix": args.raw_prefix,
        "planned_videos": len(records),
        "videos_per_shard": args.videos_per_shard,
        "num_shards": len(shard_paths),
        "local_run_dir": str(layout.run_dir),
        "gcs_artifact_prefix": layout.gcs_artifact_prefix,
        "sample_input_gcs_uri": records[0]["input_gcs_uri"] if records else "",
        "sample_output_prefix": records[0]["output_prefix"] if records else "",
    }
    write_json(layout.summary_path, summary)
    return summary


def upload_discovery_artifacts(client: Any, bucket_name: str, layout: RunLayout) -> None:
    """Upload discovery artifacts to the run-level GCS manifest prefix."""

    bucket = client.bucket(bucket_name)
    upload_file(bucket, layout.manifest_path, layout.gcs_artifact_prefix + "processing_manifest.jsonl", "application/jsonl")
    upload_file(bucket, layout.errors_path, layout.gcs_artifact_prefix + "errors.jsonl", "application/jsonl")
    upload_file(bucket, layout.summary_path, layout.gcs_artifact_prefix + "summary.json", "application/json")

    for shard_path in sorted(layout.shards_dir.glob("*.jsonl")):
        upload_file(
            bucket,
            shard_path,
            layout.gcs_artifact_prefix + f"shards/{shard_path.name}",
            "application/jsonl",
        )


def require_module(module_name: str, install_hint: str) -> None:
    """Raise a clear error when a runtime dependency is missing."""

    if importlib.util.find_spec(module_name) is None:
        raise RuntimeError(f"Missing dependency '{module_name}'. Install with: {install_hint}")


def add_repo_to_path(repo_dir: str) -> None:
    """Make the baked AutoShot repository importable."""

    repo_path = Path(repo_dir).expanduser().resolve()
    required_files = [
        repo_path / "supernet_flattransf_3_8_8_8_13_12_0_16_60.py",
        repo_path / "utils.py",
    ]
    missing_files = [str(path) for path in required_files if not path.exists()]
    if missing_files:
        missing = ", ".join(missing_files)
        raise FileNotFoundError(f"AutoShot repo is not ready at {repo_path}. Missing files: {missing}")

    resolved = str(repo_path)
    if resolved not in sys.path:
        sys.path.insert(0, resolved)


# Autoshot model

def load_autoshot_model(repo_dir: str, checkpoint_path: str, device: str):
    """Load AutoShot model architecture and checkpoint.

    This follows the notebook logic: import ``TransNetV2Supernet``, support both
    checkpoints with a top-level ``net`` key and plain state dict checkpoints,
    then load only matching parameter names and shapes.
    """

    require_module("torch", "pip install -r requirements.txt")
    import torch

    add_repo_to_path(repo_dir)
    from supernet_flattransf_3_8_8_8_13_12_0_16_60 import TransNetV2Supernet

    model = TransNetV2Supernet().eval()
    checkpoint = torch.load(checkpoint_path, map_location=device)
    pretrained_state = checkpoint["net"] if isinstance(checkpoint, dict) and "net" in checkpoint else checkpoint

    model_state = model.state_dict()
    matched_state = {
        key: value
        for key, value in pretrained_state.items()
        if key in model_state and tuple(value.shape) == tuple(model_state[key].shape)
    }
    if not matched_state:
        raise RuntimeError("Checkpoint did not match any AutoShot model parameters.")

    model_state.update(matched_state)
    model.load_state_dict(model_state)
    model = model.to(device)
    model.eval()
    logging.info("loaded AutoShot params matched=%d total=%d", len(matched_state), len(model_state))
    return model


def resolve_ffmpeg_executable() -> str:
    """Return an FFmpeg executable path usable by ``ffmpeg-python``.

    AutoShot's original ``utils.get_frames`` calls the executable name
    ``ffmpeg`` directly. On Windows that fails when FFmpeg is not on PATH, so
    the loader also supports ``FFMPEG_BINARY`` and the bundled
    ``imageio-ffmpeg`` binary declared in ``requirements.txt``.
    """

    env_binary = os.getenv("FFMPEG_BINARY", "").strip()
    candidates = [env_binary] if env_binary else []

    path_binary = shutil.which("ffmpeg")
    if path_binary:
        candidates.append(path_binary)

    if importlib.util.find_spec("imageio_ffmpeg"):
        import imageio_ffmpeg

        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())

    for candidate in candidates:
        if not candidate:
            continue
        candidate_path = Path(candidate).expanduser()
        if candidate_path.exists():
            return str(candidate_path.resolve())
        resolved = shutil.which(candidate)
        if resolved:
            return resolved

    raise RuntimeError(
        "FFmpeg binary not found. Install project dependencies with "
        "`python -m pip install -r requirements.txt`, or install FFmpeg system-wide "
        "and make sure `ffmpeg` is on PATH."
    )


def read_autoshot_frames(video_path: str, ffmpeg_executable: str, width: int = 48, height: int = 27):
    """Read video frames with the same FFmpeg settings as AutoShot ``get_frames``."""

    require_module("ffmpeg", "pip install -r requirements.txt")
    import ffmpeg
    import numpy as np

    try:
        video_stream, _ = (
            ffmpeg.input(video_path)
            .output("pipe:", format="rawvideo", pix_fmt="rgb24", s=f"{width}x{height}")
            .run(cmd=ffmpeg_executable, capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else str(exc)
        raise RuntimeError(f"FFmpeg failed to decode video: {video_path}. stderr: {stderr[-1200:]}") from exc

    return np.frombuffer(video_stream, np.uint8).reshape([-1, height, width, 3])


def predict_boundary_scores(model: Any, video_path: str, repo_dir: str, device: str):
    """Predict AutoShot boundary scores for every frame in a video.

    The implementation mirrors ``get-keyframe-autoshot.ipynb``:
    ``get_frames`` resizes to 48x27 RGB, ``get_batches`` produces 100-frame
    windows, and only ``prob[25:75]`` is kept from each window to avoid unstable
    padded edges.
    """

    require_module("numpy", "pip install -r requirements.txt")
    require_module("torch", "pip install -r requirements.txt")
    require_module("ffmpeg", "pip install -r requirements.txt")
    require_module("imageio_ffmpeg", "pip install -r requirements.txt")
    require_module("matplotlib", "pip install -r requirements.txt")
    import numpy as np
    import torch

    add_repo_to_path(repo_dir)
    from utils import get_batches

    frames = read_autoshot_frames(video_path, resolve_ffmpeg_executable())
    if len(frames) == 0:
        raise RuntimeError(f"AutoShot could not read frames from video: {video_path}")

    all_scores = []
    with torch.no_grad():
        for batch in get_batches(frames):
            x = batch.transpose((3, 0, 1, 2))
            x = x[np.newaxis, ...]
            x = torch.from_numpy(x).float().to(device)

            output = model(x)
            one_hot_logits = output[0] if isinstance(output, tuple) else output
            prob = torch.sigmoid(one_hot_logits[0]).detach().cpu().numpy()
            prob = np.squeeze(prob)
            all_scores.append(prob[25:75])

    scores = np.concatenate(all_scores, axis=0)[: len(frames)]
    return scores


def boundaries_to_shots(boundary_frames: Iterable[int], num_frames: int, min_shot_len: int = DEFAULT_MIN_SHOT_LEN) -> list[tuple[int, int]]:
    """Convert boundary frame indices into inclusive shot segments."""

    boundaries = sorted(set(int(value) for value in boundary_frames))
    shots: list[tuple[int, int]] = []
    start = 0

    for boundary in boundaries:
        boundary = max(0, min(boundary, num_frames - 1))
        end = boundary
        if end - start + 1 >= min_shot_len:
            shots.append((start, end))
        start = boundary + 1

    if start <= num_frames - 1:
        end = num_frames - 1
        if end - start + 1 >= min_shot_len:
            shots.append((start, end))

    return shots or [(0, num_frames - 1)]


def save_frame_at(cap: Any, frame_idx: int, out_path: Path) -> bool:
    """Save one original-resolution frame from an OpenCV capture object."""

    import cv2

    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame_bgr = cap.read()
    if not ok or frame_bgr is None:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(out_path), frame_bgr))


def extract_representative_frames(video_path: str, video_id: str, shots: list[tuple[int, int]], output_dir: Path) -> list[dict[str, Any]]:
    """Extract first, middle, and last original frames for every shot."""

    require_module("cv2", "pip install -r requirements.txt")
    import cv2

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV cannot open video: {video_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    rows: list[dict[str, Any]] = []

    for shot_index, (start, end) in enumerate(shots):
        middle = (start + end) // 2
        frame_items = [
            ("first", start),
            ("middle", middle),
            ("last", end),
        ]

        for frame_type, frame_idx in frame_items:
            filename = f"shot_{shot_index:04d}_{frame_type}_f{frame_idx:06d}.jpg"
            local_path = output_dir / video_id / filename
            saved = save_frame_at(cap, frame_idx, local_path)
            rows.append(
                {
                    "shot_id_local": shot_index,
                    "shot_start_frame": start,
                    "shot_end_frame": end,
                    "shot_start_sec": start / fps if fps > 0 else "",
                    "shot_end_sec": end / fps if fps > 0 else "",
                    "frame_type": frame_type,
                    "frame_idx": frame_idx,
                    "frame_sec": frame_idx / fps if fps > 0 else "",
                    "local_image_path": str(local_path),
                    "saved": saved,
                    "fps": fps,
                    "total_frames_opencv": total_frames,
                }
            )

    cap.release()
    return rows


def ensure_local_blob(
    client: Any,
    gcs_uri: str,
    local_path: Path,
    generation: str = "",
    expected_size_bytes: int | None = None,
) -> Path:
    """Download a GCS object to a local path, optionally pinned by generation.

    ``download_to_filename`` truncates the destination before writing. If a
    credential/network failure leaves a zero-byte file behind, validate it here
    so later video decoding errors are not misleading.
    """

    parsed = parse_gcs_uri(gcs_uri)
    bucket = client.bucket(parsed.bucket)
    blob = bucket.blob(parsed.blob_name, generation=int(generation) if generation else None)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(str(local_path), timeout=900)
    downloaded_size = local_path.stat().st_size
    if expected_size_bytes is not None and downloaded_size != expected_size_bytes:
        raise RuntimeError(
            f"Downloaded GCS object has unexpected size: {gcs_uri}. "
            f"expected={expected_size_bytes} bytes actual={downloaded_size} bytes"
        )
    if downloaded_size == 0:
        raise RuntimeError(f"Downloaded GCS object is empty: {gcs_uri}")
    return local_path


def ensure_local_checkpoint(client: Any, checkpoint_uri: str, local_path: Path) -> Path:
    """Resolve a checkpoint URI or local path into a local checkpoint file."""

    if checkpoint_uri.startswith("gs://"):
        return ensure_local_blob(client, checkpoint_uri, local_path)
    checkpoint_path = Path(checkpoint_uri)
    if not checkpoint_path.exists():
        raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")
    return checkpoint_path


def select_device(raw_device: str) -> str:
    """Resolve ``auto`` to CUDA when available, otherwise CPU."""

    if raw_device != "auto":
        return raw_device
    require_module("torch", "pip install -r requirements.txt")
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def upload_keyframe_if_needed(
    bucket: Any,
    local_path: Path,
    object_key: str,
    skip_existing: bool,
    overwrite: bool,
) -> bool:
    """Upload one keyframe and return whether the object is present."""

    blob = bucket.blob(object_key)
    if skip_existing and blob.exists():
        return True
    kwargs: dict[str, Any] = {"content_type": "image/jpeg", "timeout": 600}
    if not overwrite:
        kwargs["if_generation_match"] = 0
    blob.upload_from_filename(str(local_path), **kwargs)
    return True


def process_video_record(
    client: Any,
    record: dict[str, Any],
    model: Any,
    repo_dir: str,
    checkpoint_path: Path,
    device: str,
    scratch_dir: Path,
    skip_existing: bool,
    overwrite: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Process one manifest record and upload its keyframes to GCS."""

    start_time = time.perf_counter()
    require_module("numpy", "pip install -r requirements.txt")
    import numpy as np

    video_id = str(record["video_id"])
    input_uri = parse_gcs_uri(str(record["input_gcs_uri"]))
    local_video = scratch_dir / "input" / f"{video_id}{PurePosixPath(input_uri.blob_name).suffix.lower()}"
    local_frames_dir = scratch_dir / "frames"

    ensure_local_blob(
        client=client,
        gcs_uri=input_uri.uri,
        local_path=local_video,
        generation=str(record.get("input_generation") or ""),
        expected_size_bytes=int(record["input_size_bytes"]) if record.get("input_size_bytes") else None,
    )
    scores = predict_boundary_scores(model, str(local_video), repo_dir, device)
    boundary_frames = np.where(scores > float(record.get("threshold", DEFAULT_THRESHOLD)))[0]
    shots = boundaries_to_shots(
        boundary_frames=boundary_frames,
        num_frames=len(scores),
        min_shot_len=int(record.get("min_shot_len", DEFAULT_MIN_SHOT_LEN)),
    )

    frame_rows = extract_representative_frames(str(local_video), video_id, shots, local_frames_dir)
    output_bucket_name = str(record.get("output_bucket") or input_uri.bucket)
    output_bucket = client.bucket(output_bucket_name)
    output_prefix = str(record["output_prefix"]).rstrip("/") + "/"
    final_rows: list[dict[str, Any]] = []

    for row in frame_rows:
        filename = Path(str(row["local_image_path"])).name
        image_storage_key = output_prefix + filename
        image_gcs_uri = f"gs://{output_bucket_name}/{image_storage_key}"
        local_image_path = Path(str(row["local_image_path"]))

        uploaded = False
        if bool(row["saved"]):
            uploaded = upload_keyframe_if_needed(
                bucket=output_bucket,
                local_path=local_image_path,
                object_key=image_storage_key,
                skip_existing=skip_existing,
                overwrite=overwrite,
            )

        shot_index = int(row["shot_id_local"])
        frame_idx = int(row["frame_idx"])
        final_row = {
            "dataset_id": record["dataset_id"],
            "batch_id": record["batch_id"],
            "video_id": video_id,
            "video_name": record["video_name"],
            "video_gcs_uri": record["input_gcs_uri"],
            "video_gcs_generation": record.get("input_generation", ""),
            "shot_id": f"{video_id}_S{shot_index:04d}",
            "shot_id_local": shot_index,
            "shot_start_frame": row["shot_start_frame"],
            "shot_end_frame": row["shot_end_frame"],
            "shot_start_sec": row["shot_start_sec"],
            "shot_end_sec": row["shot_end_sec"],
            "frame_type": row["frame_type"],
            "frame_idx": frame_idx,
            "frame_sec": row["frame_sec"],
            "keyframe_id": f"{video_id}_F{frame_idx:06d}",
            "image_rel_path": f"{video_id}/{filename}",
            "image_gcs_uri": image_gcs_uri,
            "image_storage_key": image_storage_key,
            "boundary_threshold": record.get("threshold", DEFAULT_THRESHOLD),
            "min_shot_len": record.get("min_shot_len", DEFAULT_MIN_SHOT_LEN),
            "saved": bool(row["saved"]) and uploaded,
            "fps": row["fps"],
            "total_frames_opencv": row["total_frames_opencv"],
            "profile_version": record["profile_version"],
            "run_id": record["run_id"],
        }
        final_rows.append(final_row)

    frames_manifest_path = scratch_dir / "frames_manifest" / f"{video_id}.jsonl"
    write_jsonl(frames_manifest_path, final_rows)
    upload_file(output_bucket, frames_manifest_path, output_prefix + "frames_manifest.jsonl", "application/jsonl")

    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
    summary = {
        "run_id": record["run_id"],
        "dataset_id": record["dataset_id"],
        "batch_id": record["batch_id"],
        "video_id": video_id,
        "status": "success",
        "input_gcs_uri": record["input_gcs_uri"],
        "input_generation": record.get("input_generation", ""),
        "checkpoint_path": str(checkpoint_path),
        "device": device,
        "num_frames": int(len(scores)),
        "num_boundaries": int(len(boundary_frames)),
        "num_shots": int(len(shots)),
        "num_keyframes": int(len(final_rows)),
        "duration_ms": elapsed_ms,
        "finished_at": utc_now_iso(),
    }
    return final_rows, summary


def shard_id_from_uri_or_path(value: str) -> str:
    """Infer a stable shard id from a local shard path or GCS URI."""

    name = PurePosixPath(parse_gcs_uri(value).blob_name).name if value.startswith("gs://") else Path(value).name
    return Path(name).stem


def write_extract_results(
    layout: RunLayout,
    shard_id: str,
    shot_rows: list[dict[str, Any]],
    error_rows: list[dict[str, Any]],
    result_summary: dict[str, Any],
) -> tuple[Path, Path, Path]:
    """Write local shard result CSV, errors JSONL, and summary JSON."""

    csv_path = layout.results_dir / f"{shard_id}.shot_segments.csv"
    errors_path = layout.results_dir / f"{shard_id}.errors.jsonl"
    result_path = layout.results_dir / f"{shard_id}.result.json"
    write_csv_rows(csv_path, shot_rows, SHOT_SEGMENTS_COLUMNS)
    write_jsonl(errors_path, error_rows)
    write_json(result_path, result_summary)
    return csv_path, errors_path, result_path


def upload_extract_results(client: Any, bucket_name: str, layout: RunLayout, result_paths: tuple[Path, Path, Path]) -> None:
    """Upload shard result artifacts under ``results/`` in the run prefix."""

    bucket = client.bucket(bucket_name)
    for path in result_paths:
        if path.suffix == ".csv":
            content_type = "text/csv"
        elif path.suffix == ".json":
            content_type = "application/json"
        else:
            content_type = "application/jsonl"
        upload_file(bucket, path, layout.gcs_artifact_prefix + f"results/{path.name}", content_type)


def list_prefixes(client: Any, bucket_name: str, prefix: str, max_results: int = 50) -> tuple[list[str], list[str]]:
    """List immediate child prefixes and blobs under a GCS prefix."""

    iterator = client.list_blobs(bucket_name, prefix=prefix, delimiter="/", max_results=max_results)
    blobs = list(iterator)
    return sorted(iterator.prefixes), [blob.name for blob in blobs]


def collect_gcs_result_files(client: Any, bucket_name: str, prefix: str, local_results_dir: Path) -> None:
    """Download result files from a GCS results prefix into a local directory."""

    bucket = client.bucket(bucket_name)
    local_results_dir.mkdir(parents=True, exist_ok=True)
    for blob in bucket.list_blobs(prefix=prefix):
        if not (
            blob.name.endswith(".shot_segments.csv")
            or blob.name.endswith(".errors.jsonl")
            or blob.name.endswith(".result.json")
        ):
            continue
        destination = local_results_dir / PurePosixPath(blob.name).name
        blob.download_to_filename(str(destination), timeout=600)


def validate_quality_gate(
    client: Any,
    planned_records: list[dict[str, Any]],
    shot_rows: list[dict[str, Any]],
    error_rows: list[dict[str, Any]],
    profile_version: str,
    verify_gcs_images: bool,
) -> dict[str, Any]:
    """Run the quality checks required before final ``_SUCCESS``."""

    failures: list[str] = []
    planned_videos = {str(row["video_id"]) for row in planned_records}
    succeeded_videos = {str(row.get("video_id", "")) for row in shot_rows if row.get("video_id")}
    failed_videos = {str(row.get("video_id", "")) for row in error_rows if row.get("video_id")}

    missing_videos = planned_videos - succeeded_videos - failed_videos
    if missing_videos:
        failures.append(f"missing result for video(s): {', '.join(sorted(missing_videos))}")
    if error_rows:
        failures.append(f"error rows present: {len(error_rows)}")
    if not shot_rows:
        failures.append("shot_segments rows are empty")

    seen_keyframes: set[str] = set()
    seen_video_frames: set[tuple[str, int]] = set()

    for index, row in enumerate(shot_rows, 1):
        if str(row.get("profile_version", "")) != profile_version:
            failures.append(f"row {index} has unexpected profile_version={row.get('profile_version')}")
        if str(row.get("saved", "")).lower() not in {"true", "1"}:
            failures.append(f"row {index} saved is not true")

        keyframe_id = str(row.get("keyframe_id", ""))
        if keyframe_id in seen_keyframes:
            failures.append(f"duplicate keyframe_id={keyframe_id}")
        seen_keyframes.add(keyframe_id)

        try:
            frame_idx = int(row.get("frame_idx", ""))
            shot_start = int(row.get("shot_start_frame", ""))
            shot_end = int(row.get("shot_end_frame", ""))
        except ValueError:
            failures.append(f"row {index} has invalid frame integer fields")
            continue

        video_frame = (str(row.get("video_id", "")), frame_idx)
        if video_frame in seen_video_frames:
            failures.append(f"duplicate (video_id, frame_idx)={video_frame}")
        seen_video_frames.add(video_frame)

        if not (shot_start <= frame_idx <= shot_end):
            failures.append(f"row {index} frame_idx is outside shot range")

    if verify_gcs_images:
        for index, row in enumerate(shot_rows, 1):
            image_uri = str(row.get("image_gcs_uri", ""))
            try:
                parsed = parse_gcs_uri(image_uri)
            except ValueError:
                failures.append(f"row {index} has invalid image_gcs_uri")
                continue
            if not client.bucket(parsed.bucket).blob(parsed.blob_name).exists():
                failures.append(f"row {index} image object is missing: {image_uri}")

    return {
        "passed": not failures,
        "failures": failures,
        "planned_videos": len(planned_videos),
        "succeeded_videos": len(succeeded_videos),
        "failed_videos": len(failed_videos),
        "shot_rows": len(shot_rows),
        "unique_keyframes": len(seen_keyframes),
    }


def merge_local_results(results_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Load shard CSV, error JSONL, and result JSON files from a results dir."""

    shot_rows: list[dict[str, Any]] = []
    error_rows: list[dict[str, Any]] = []
    result_summaries: list[dict[str, Any]] = []

    for csv_path in sorted(results_dir.glob("*.shot_segments.csv")):
        shot_rows.extend(read_csv_rows(csv_path))
    for errors_path in sorted(results_dir.glob("*.errors.jsonl")):
        error_rows.extend(read_jsonl_text(errors_path.read_text(encoding="utf-8")))
    for result_path in sorted(results_dir.glob("*.result.json")):
        result_summaries.append(json.loads(result_path.read_text(encoding="utf-8")))

    return shot_rows, error_rows, result_summaries


def apply_env_file(args: argparse.Namespace) -> None:
    """Load ``--env-file`` and remember its directory for relative paths."""

    args.env_file_dir = None
    if getattr(args, "env_file", None):
        env_file = Path(args.env_file).resolve()
        load_env_file(env_file)
        args.env_file_dir = env_file.parent


def cmd_doctor(args: argparse.Namespace) -> int:
    """Validate dependencies and show a small sample of the raw GCS layout."""

    apply_env_file(args)
    configure_logging(args.verbose)
    client = make_storage_client(args)
    bucket_name = resolve_bucket_name(args)
    batch_prefix = build_raw_batch_prefix(args.raw_prefix, args.dataset_id, args.source_version, args.batch_id)

    prefixes, blobs = list_prefixes(client, bucket_name, normalize_prefix(args.raw_prefix) + "/")
    _, sample_blobs = list_prefixes(client, bucket_name, batch_prefix, max_results=100)
    sample_video = ""
    for blob in client.list_blobs(bucket_name, prefix=batch_prefix, max_results=500):
        if PurePosixPath(blob.name).suffix.lower() in VIDEO_EXTENSIONS:
            sample_video = f"gs://{bucket_name}/{blob.name}"
            break

    dependency_status = {
        "google.cloud.storage": importlib.util.find_spec("google.cloud.storage") is not None,
        "numpy": importlib.util.find_spec("numpy") is not None,
        "torch": importlib.util.find_spec("torch") is not None,
        "cv2": importlib.util.find_spec("cv2") is not None,
    }

    report = {
        "bucket": bucket_name,
        "raw_root_prefix": normalize_prefix(args.raw_prefix) + "/",
        "raw_root_child_prefixes": prefixes,
        "batch_prefix": batch_prefix,
        "batch_immediate_blobs": sample_blobs[:10],
        "sample_video": sample_video,
        "dependencies": dependency_status,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if sample_video else 1


def cmd_discover(args: argparse.Namespace) -> int:
    """Create processing manifest and shard files from raw GCS videos."""

    apply_env_file(args)
    configure_logging(args.verbose)
    args.run_id = args.run_id or new_run_id()

    client = make_storage_client(args)
    bucket_name = resolve_bucket_name(args)
    layout = make_run_layout(args, args.run_id)
    records = discover_raw_videos(
        client=client,
        bucket_name=bucket_name,
        run_id=args.run_id,
        dataset_id=args.dataset_id,
        batch_id=args.batch_id,
        source_version=args.source_version,
        raw_prefix=args.raw_prefix,
        keyframes_prefix=args.keyframes_prefix,
        profile_version=args.profile_version,
        threshold=args.threshold,
        min_shot_len=args.min_shot_len,
        max_videos=args.max_videos,
    )
    if not records:
        raise RuntimeError("No raw videos found. Check bucket, dataset_id, batch_id, and raw prefix.")

    summary = write_discovery_artifacts(layout, records, args)
    if args.upload_artifacts:
        upload_discovery_artifacts(client, bucket_name, layout)
        summary["uploaded_to_gcs"] = True
        summary["gcs_processing_manifest"] = f"gs://{bucket_name}/{layout.gcs_artifact_prefix}processing_manifest.jsonl"
        write_json(layout.summary_path, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    """Process a manifest shard and write shard-level extraction results."""

    apply_env_file(args)
    configure_logging(args.verbose)
    client = make_storage_client(args)
    bucket_name = resolve_bucket_name(args)
    records = read_jsonl_uri(client, args.manifest_shard)
    if not records:
        raise RuntimeError("Manifest shard is empty.")

    first_record = records[0]
    args.dataset_id = args.dataset_id or first_record["dataset_id"]
    args.batch_id = args.batch_id or first_record["batch_id"]
    args.profile_version = args.profile_version or first_record["profile_version"]
    args.run_id = args.run_id or first_record["run_id"]
    layout = make_run_layout(args, args.run_id)
    shard_id = args.shard_id or shard_id_from_uri_or_path(args.manifest_shard)

    shot_rows: list[dict[str, Any]] = []
    error_rows: list[dict[str, Any]] = []
    video_summaries: list[dict[str, Any]] = []
    started_at = time.perf_counter()

    if args.dry_run:
        result_summary = {
            "run_id": args.run_id,
            "shard_id": shard_id,
            "stage": "extract",
            "dry_run": True,
            "planned_videos": len(records),
            "finished_at": utc_now_iso(),
        }
        result_paths = write_extract_results(layout, shard_id, shot_rows, error_rows, result_summary)
        print(json.dumps(result_summary, indent=2, ensure_ascii=False))
        return 0

    require_module("cv2", "pip install -r requirements.txt")
    require_module("numpy", "pip install -r requirements.txt")
    require_module("torch", "pip install -r requirements.txt")

    device = select_device(args.device)
    scratch_dir = Path(args.scratch_dir) / args.run_id / shard_id
    checkpoint_path = ensure_local_checkpoint(client, args.checkpoint_uri, scratch_dir / "models" / "ckpt_0_200_0.pth")
    model = load_autoshot_model(args.autoshot_repo_dir, str(checkpoint_path), device)

    for record in records:
        try:
            rows, summary = process_video_record(
                client=client,
                record=record,
                model=model,
                repo_dir=args.autoshot_repo_dir,
                checkpoint_path=checkpoint_path,
                device=device,
                scratch_dir=scratch_dir,
                skip_existing=args.skip_existing,
                overwrite=args.overwrite,
            )
            shot_rows.extend(rows)
            video_summaries.append(summary)
            logging.info("processed video_id=%s keyframes=%d", record.get("video_id"), len(rows))
        except Exception as exc:  # noqa: BLE001 - every video failure must be recorded.
            error_row = {
                "run_id": record.get("run_id", args.run_id),
                "dataset_id": record.get("dataset_id", args.dataset_id),
                "batch_id": record.get("batch_id", args.batch_id),
                "video_id": record.get("video_id", ""),
                "input_gcs_uri": record.get("input_gcs_uri", ""),
                "input_generation": record.get("input_generation", ""),
                "stage": "extract",
                "error_code": exc.__class__.__name__,
                "error_message": str(exc),
                "failed_at": utc_now_iso(),
                "recommended_action": "Inspect video, checkpoint, AutoShot repo, and worker dependencies.",
            }
            error_rows.append(error_row)
            logging.exception("failed video_id=%s", record.get("video_id", ""))

    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    result_summary = {
        "run_id": args.run_id,
        "shard_id": shard_id,
        "stage": "extract",
        "dry_run": False,
        "planned_videos": len(records),
        "succeeded_videos": len(video_summaries),
        "failed_videos": len(error_rows),
        "shot_rows": len(shot_rows),
        "video_summaries": video_summaries,
        "duration_ms": elapsed_ms,
        "finished_at": utc_now_iso(),
    }
    result_paths = write_extract_results(layout, shard_id, shot_rows, error_rows, result_summary)
    if args.upload_results:
        upload_extract_results(client, bucket_name, layout, result_paths)
    print(json.dumps(result_summary, indent=2, ensure_ascii=False))
    return 1 if error_rows else 0


def cmd_merge(args: argparse.Namespace) -> int:
    """Merge shard results, run quality gates, and write final artifacts."""

    apply_env_file(args)
    configure_logging(args.verbose)
    client = make_storage_client(args)
    bucket_name = resolve_bucket_name(args)
    layout = make_run_layout(args, args.run_id)

    if args.download_results:
        result_prefix = layout.gcs_artifact_prefix + "results/"
        collect_gcs_result_files(client, bucket_name, result_prefix, layout.results_dir)

    manifest_uri = args.manifest_uri or str(layout.manifest_path)
    planned_records = read_jsonl_uri(client, manifest_uri)
    shot_rows, error_rows, result_summaries = merge_local_results(layout.results_dir)
    quality = validate_quality_gate(
        client=client,
        planned_records=planned_records,
        shot_rows=shot_rows,
        error_rows=error_rows,
        profile_version=args.profile_version,
        verify_gcs_images=args.verify_gcs_images,
    )

    final_csv_path = layout.run_dir / "shot_segments.csv"
    final_errors_path = layout.run_dir / "errors.jsonl"
    final_summary_path = layout.run_dir / "summary.json"
    success_path = layout.run_dir / "_SUCCESS"

    write_csv_rows(final_csv_path, shot_rows, SHOT_SEGMENTS_COLUMNS)
    write_jsonl(final_errors_path, error_rows)
    summary = {
        "run_id": args.run_id,
        "stage": "merge",
        "dataset_id": args.dataset_id,
        "batch_id": args.batch_id,
        "profile_version": args.profile_version,
        "status": "success" if quality["passed"] else "failed",
        "quality_gate": quality,
        "result_summaries": result_summaries,
        "finished_at": utc_now_iso(),
    }
    write_json(final_summary_path, summary)

    if quality["passed"]:
        success_path.write_text("", encoding="utf-8")

    if args.upload_final:
        bucket = client.bucket(bucket_name)
        upload_file(bucket, final_csv_path, layout.gcs_artifact_prefix + "shot_segments.csv", "text/csv")
        upload_file(bucket, final_errors_path, layout.gcs_artifact_prefix + "errors.jsonl", "application/jsonl")
        upload_file(bucket, final_summary_path, layout.gcs_artifact_prefix + "summary.json", "application/json")
        if quality["passed"]:
            upload_text(bucket, "", layout.gcs_artifact_prefix + "_SUCCESS")

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if quality["passed"] else 1


def add_common_gcs_args(parser: argparse.ArgumentParser) -> None:
    """Add shared GCS and logging arguments to a subparser."""

    parser.add_argument("--env-file", type=Path, default=None, help="Optional .env file with GCS settings.")
    parser.add_argument("--gcs-bucket", default="", help="GCS bucket. Fallback: GCS_BUCKET.")
    parser.add_argument("--gcs-credentials-file", default="", help="Service account JSON file. Fallback: GCS_CREDENTIALS_FILE.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")


def add_plan_args(parser: argparse.ArgumentParser) -> None:
    """Add arguments that define the plan's dataset/profile layout."""

    parser.add_argument("--dataset-id", default="ai_challenge_2025")
    parser.add_argument("--batch-id", default="L21")
    parser.add_argument("--source-version", default=DEFAULT_SOURCE_VERSION)
    parser.add_argument("--profile-version", default=DEFAULT_PROFILE_VERSION)
    parser.add_argument("--raw-prefix", default=DEFAULT_RAW_PREFIX)
    parser.add_argument("--keyframes-prefix", default=DEFAULT_KEYFRAMES_PREFIX)
    parser.add_argument("--manifests-prefix", default=DEFAULT_MANIFESTS_PREFIX)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--min-shot-len", type=int, default=DEFAULT_MIN_SHOT_LEN)


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI parser."""

    parser = argparse.ArgumentParser(description="AutoShot video-to-frame pipeline for GCS raw videos.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check GCS raw format and runtime dependencies.")
    add_common_gcs_args(doctor)
    add_plan_args(doctor)
    doctor.set_defaults(func=cmd_doctor)

    discover = subparsers.add_parser("discover", help="Build processing manifest and shard files from GCS raw videos.")
    add_common_gcs_args(discover)
    add_plan_args(discover)
    discover.add_argument("--run-id", default="", help="Optional run id. Generated when omitted.")
    discover.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    discover.add_argument("--max-videos", type=int, default=None, help="Limit videos for smoke tests.")
    discover.add_argument("--videos-per-shard", type=int, default=DEFAULT_VIDEOS_PER_SHARD)
    discover.add_argument("--upload-artifacts", action="store_true", help="Upload manifest and shards to GCS.")
    discover.set_defaults(func=cmd_discover)

    extract = subparsers.add_parser("extract", help="Run AutoShot extraction for one manifest shard.")
    add_common_gcs_args(extract)
    extract.add_argument("--manifest-shard", required=True, help="Local path or GCS URI to shard JSONL.")
    extract.add_argument("--checkpoint-uri", default="", help="GCS URI or local path to ckpt_0_200_0.pth.")
    extract.add_argument(
        "--autoshot-repo-dir",
        default=str(DEFAULT_AUTOSHOT_REPO_DIR),
        help=f"Local AutoShot repository path. Defaults to {DEFAULT_AUTOSHOT_REPO_DIR}.",
    )
    extract.add_argument("--run-id", default="", help="Optional run id. Defaults to manifest run_id.")
    extract.add_argument("--dataset-id", default="", help="Optional override. Defaults to manifest dataset_id.")
    extract.add_argument("--batch-id", default="", help="Optional override. Defaults to manifest batch_id.")
    extract.add_argument("--profile-version", default="", help="Optional override. Defaults to manifest profile_version.")
    extract.add_argument("--manifests-prefix", default=DEFAULT_MANIFESTS_PREFIX)
    extract.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    extract.add_argument("--scratch-dir", type=Path, default=Path(".tmp") / "autoshot")
    extract.add_argument("--shard-id", default="")
    extract.add_argument("--device", default="auto", help="auto, cpu, cuda, or cuda:N.")
    extract.add_argument("--dry-run", action="store_true", help="Read shard and write empty result without running AutoShot.")
    extract.add_argument("--skip-existing", action=argparse.BooleanOptionalAction, default=True)
    extract.add_argument("--overwrite", action="store_true", help="Allow overwriting existing keyframe objects.")
    extract.add_argument("--upload-results", action="store_true", help="Upload shard result artifacts to GCS.")
    extract.set_defaults(func=cmd_extract)

    merge = subparsers.add_parser("merge", help="Merge shard results and write final shot_segments.csv.")
    add_common_gcs_args(merge)
    add_plan_args(merge)
    merge.add_argument("--run-id", required=True)
    merge.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    merge.add_argument("--manifest-uri", default="", help="Local or GCS URI to processing_manifest.jsonl.")
    merge.add_argument("--download-results", action="store_true", help="Download shard results from GCS before merging.")
    merge.add_argument("--verify-gcs-images", action=argparse.BooleanOptionalAction, default=True)
    merge.add_argument("--upload-final", action="store_true", help="Upload final CSV/errors/summary/_SUCCESS to GCS.")
    merge.set_defaults(func=cmd_merge)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "command", "") == "extract" and not args.dry_run:
        if not args.checkpoint_uri:
            parser.error("extract requires --checkpoint-uri unless --dry-run is set.")
        if not args.autoshot_repo_dir:
            parser.error("extract requires --autoshot-repo-dir unless --dry-run is set.")

    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001 - CLI should report actionable errors.
        logging.exception("pipeline command failed")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

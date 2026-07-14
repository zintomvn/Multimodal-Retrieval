"""Kaggle AutoShot keyframe extraction and GCS upload helper.

This module is designed for ``video_to_frame_gcs.ipynb``. It extracts
first/middle/last representative frames per AutoShot shot from Kaggle-mounted
videos, uploads images to GCS, and writes manifest artifacts compatible with
``scripts/loaders/video_to_frame_gcs.py`` and the backend data model.
"""

from __future__ import annotations

import csv
import importlib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


DEFAULT_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}

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
    bucket: str
    blob_name: str

    @property
    def uri(self) -> str:
        return f"gs://{self.bucket}/{self.blob_name}"


@dataclass(frozen=True)
class RunLayout:
    run_id: str
    run_dir: Path
    frames_dir: Path
    artifacts_dir: Path
    manifest_path: Path
    shot_segments_path: Path
    errors_path: Path
    video_summaries_path: Path
    summary_path: Path
    log_path: Path
    gcs_artifact_prefix: str


def cfg_value(cfg: Any, name: str, default: Any = None) -> Any:
    return getattr(cfg, name, default)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_run_id(prefix: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", prefix).strip("_").lower()
    return f"{safe_prefix}_{stamp}_{uuid.uuid4().hex[:8]}"


def normalize_prefix(prefix: str) -> str:
    return str(prefix or "").strip().strip("/")


def parse_gcs_uri(value: str) -> GcsUri:
    if not value.startswith("gs://"):
        raise ValueError(f"Expected gs:// URI, got: {value}")
    bucket, sep, blob_name = value[len("gs://") :].partition("/")
    if not bucket or not sep or not blob_name:
        raise ValueError(f"Invalid GCS URI: {value}")
    return GcsUri(bucket=bucket, blob_name=blob_name)


def read_kaggle_secret(name: str) -> str:
    if not name:
        return ""
    try:
        from kaggle_secrets import UserSecretsClient

        return UserSecretsClient().get_secret(name) or ""
    except Exception:
        return ""


def resolve_bucket_name(cfg: Any, require: bool) -> str:
    configured = str(cfg_value(cfg, "GCS_BUCKET", "") or "").strip()
    env_value = os.environ.get("GCS_BUCKET", "").strip()
    secret_name = str(cfg_value(cfg, "GCS_BUCKET_SECRET_NAME", "GCS_BUCKET") or "").strip()
    secret_value = read_kaggle_secret(secret_name).strip()
    raw_value = configured or env_value or secret_value
    if not raw_value:
        if require:
            raise RuntimeError("Set GCS_BUCKET in params, env vars, or Kaggle Secrets.")
        return ""
    if raw_value.startswith("gs://"):
        return parse_gcs_uri(raw_value.rstrip("/") + "/_").bucket
    return raw_value.strip().strip("/")


def make_storage_client(cfg: Any):
    try:
        from google.cloud import storage
    except ImportError as exc:
        raise RuntimeError("Install google-cloud-storage first.") from exc

    credentials_file = str(cfg_value(cfg, "GCS_CREDENTIALS_FILE", "") or os.environ.get("GCS_CREDENTIALS_FILE", "")).strip()
    credentials_json = os.environ.get("GCS_CREDENTIALS_JSON", "").strip()
    secret_name = str(cfg_value(cfg, "GCS_CREDENTIALS_JSON_SECRET_NAME", "GCS_CREDENTIALS_JSON") or "").strip()
    credentials_json = credentials_json or read_kaggle_secret(secret_name).strip()

    if credentials_json:
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_info(json.loads(credentials_json))
        return storage.Client(project=credentials.project_id, credentials=credentials)
    if credentials_file:
        return storage.Client.from_service_account_json(credentials_file)
    return storage.Client()


def build_raw_video_uri(cfg: Any, bucket_name: str, batch_id: str, relative_path: str) -> str:
    mode = str(cfg_value(cfg, "RAW_VIDEO_URI_MODE", "gcs_expected")).strip().lower()
    if mode == "kaggle" or not bucket_name:
        return f"kaggle://{relative_path.lstrip('/')}"
    raw_prefix = normalize_prefix(cfg_value(cfg, "RAW_PREFIX", "raw/source=kaggle"))
    raw_relative_prefix = normalize_prefix(cfg_value(cfg, "RAW_RELATIVE_PATH_PREFIX", "ai-challenge-2025"))
    raw_relative_path = relative_path.strip("/")
    if raw_relative_prefix and not raw_relative_path.lower().startswith(raw_relative_prefix.lower() + "/"):
        raw_relative_path = f"{raw_relative_prefix}/{raw_relative_path}"
    dataset_id = str(cfg_value(cfg, "DATASET_ID", "ai_challenge_2025"))
    source_version = str(cfg_value(cfg, "SOURCE_VERSION", "kaggle_current"))
    object_key = "/".join(
        [
            raw_prefix,
            f"dataset={dataset_id}",
            f"source_version={source_version}",
            f"batch={batch_id}",
            raw_relative_path,
        ]
    )
    return f"gs://{bucket_name}/{object_key}"


def build_keyframe_prefix(cfg: Any, dataset_id: str, batch_id: str, profile_version: str, video_id: str) -> str:
    return (
        f"{normalize_prefix(cfg_value(cfg, 'KEYFRAMES_PREFIX', 'processed/keyframes'))}/"
        f"dataset={dataset_id}/"
        f"batch={batch_id}/"
        f"profile={profile_version}/"
        f"video_id={video_id}/"
    )


def build_artifact_prefix(cfg: Any, dataset_id: str, batch_id: str, profile_version: str, run_id: str) -> str:
    return (
        f"{normalize_prefix(cfg_value(cfg, 'MANIFESTS_PREFIX', 'processed/keyframes_manifests'))}/"
        f"dataset={dataset_id}/"
        f"batch={batch_id}/"
        f"profile={profile_version}/"
        f"run_id={run_id}/"
    )


def make_run_layout(cfg: Any, run_id: str, artifact_batch_id: str) -> RunLayout:
    run_dir = Path(str(cfg_value(cfg, "RUN_DIR", "/kaggle/working/frame_extraction_runs"))) / run_id
    artifacts_dir = run_dir / "artifacts"
    frames_dir = run_dir / "frames"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    dataset_id = str(cfg_value(cfg, "DATASET_ID", "ai_challenge_2025"))
    profile_version = str(cfg_value(cfg, "PROFILE_VERSION", "autoshot_v1"))
    gcs_artifact_prefix = build_artifact_prefix(cfg, dataset_id, artifact_batch_id, profile_version, run_id)

    return RunLayout(
        run_id=run_id,
        run_dir=run_dir,
        frames_dir=frames_dir,
        artifacts_dir=artifacts_dir,
        manifest_path=artifacts_dir / "processing_manifest.jsonl",
        shot_segments_path=artifacts_dir / "shot_segments.csv",
        errors_path=artifacts_dir / "errors.jsonl",
        video_summaries_path=artifacts_dir / "video_summaries.jsonl",
        summary_path=artifacts_dir / "summary.json",
        log_path=run_dir / "run.log",
        gcs_artifact_prefix=gcs_artifact_prefix,
    )


def setup_logging(layout: RunLayout, verbose: bool) -> logging.Logger:
    logger = logging.getLogger("kaggle_autoshot_gcs")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(layout.log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.addHandler(console_handler)
    return logger


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def init_csv(path: Path, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()


def append_csv_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    if not rows:
        return
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def parse_selected_batches(raw: Any, expected_batches: list[str]) -> list[str]:
    expected = [batch.upper() for batch in expected_batches]
    if isinstance(raw, str):
        value = raw.strip()
        if value.lower() in {"", "all", "*"}:
            return expected
        selected = [part.strip().upper() for part in value.split(",") if part.strip()]
    else:
        selected = [str(part).strip().upper() for part in raw if str(part).strip()]
    unknown = sorted(set(selected) - set(expected))
    if unknown:
        raise ValueError(f"Unknown batch(es): {', '.join(unknown)}. Expected: {', '.join(expected)}")
    return selected


def resolve_input_root(cfg: Any) -> Path:
    configured = str(cfg_value(cfg, "INPUT_ROOT", "") or "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            Path("/kaggle/input/ai-challenge-2025"),
            Path("/kaggle/input/datasets/aresusayhi/ai-challenge-2025"),
        ]
    )
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate

    kaggle_input = Path("/kaggle/input")
    if kaggle_input.exists():
        for child in sorted(kaggle_input.iterdir()):
            if child.is_dir() and any(child.rglob("*.mp4")):
                return child

    searched = ", ".join(str(item) for item in candidates)
    raise FileNotFoundError(f"Cannot find Kaggle input root. Checked: {searched}")


def detect_batch(relative_path: str, cfg: Any) -> str | None:
    pattern = str(cfg_value(cfg, "BATCH_REGEX", r"(?i)(?:^|[/_\\-])(?:videos?_)?([A-Z]\d{2})(?:[_/\\-]|$)"))
    match = re.search(pattern, relative_path)
    if match:
        return match.group(1).upper()
    stem_match = re.match(r"(?i)^([A-Z]\d{2})[_-]", PurePosixPath(relative_path).name)
    if stem_match:
        return stem_match.group(1).upper()
    return None


def discover_videos(cfg: Any, batches: Any, max_videos: int | None, bucket_name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Path]:
    input_root = resolve_input_root(cfg)
    expected = [str(item).upper() for item in cfg_value(cfg, "EXPECTED_BATCHES", [])]
    selected_batches = set(parse_selected_batches(batches, expected))
    extensions = {str(item).lower() for item in cfg_value(cfg, "VIDEO_EXTENSIONS", DEFAULT_VIDEO_EXTENSIONS)}
    dataset_id = str(cfg_value(cfg, "DATASET_ID", "ai_challenge_2025"))
    profile_version = str(cfg_value(cfg, "PROFILE_VERSION", "autoshot_v1"))

    records: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []

    for path in sorted(input_root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        rel = path.relative_to(input_root).as_posix()
        batch_id = detect_batch(rel, cfg)
        if not batch_id or batch_id not in set(expected):
            unmapped.append(
                {
                    "relative_path": rel,
                    "local_path": str(path),
                    "reason": "batch_not_detected_or_unexpected",
                    "size_bytes": path.stat().st_size,
                }
            )
            continue
        if batch_id not in selected_batches:
            continue

        video_id = path.stem
        output_prefix = build_keyframe_prefix(cfg, dataset_id, batch_id, profile_version, video_id)
        records.append(
            {
                "manifest_schema_version": 1,
                "dataset_id": dataset_id,
                "batch_id": batch_id,
                "source_version": str(cfg_value(cfg, "SOURCE_VERSION", "kaggle_current")),
                "profile_version": profile_version,
                "video_id": video_id,
                "video_name": path.name,
                "relative_path": rel,
                "local_video_path": str(path),
                "input_gcs_uri": build_raw_video_uri(cfg, bucket_name, batch_id, rel),
                "input_generation": "",
                "input_size_bytes": path.stat().st_size,
                "output_bucket": bucket_name,
                "output_prefix": output_prefix,
                "threshold": float(cfg_value(cfg, "THRESHOLD", 0.296)),
                "min_shot_len": int(cfg_value(cfg, "MIN_SHOT_LEN", 5)),
                "planned_at": utc_now_iso(),
            }
        )
        if max_videos is not None and len(records) >= max_videos:
            break

    return records, unmapped, input_root


def require_module(module_name: str, install_hint: str) -> None:
    if importlib.util.find_spec(module_name) is None:
        raise RuntimeError(f"Missing dependency '{module_name}'. Install with: {install_hint}")


def ensure_autoshot_repo(cfg: Any, logger: logging.Logger) -> Path:
    repo_dir = Path(str(cfg_value(cfg, "AUTOSHOT_REPO_DIR", "/kaggle/working/AutoShot"))).expanduser()
    required = [
        repo_dir / "supernet_flattransf_3_8_8_8_13_12_0_16_60.py",
        repo_dir / "utils.py",
    ]
    if all(path.exists() for path in required):
        return repo_dir

    if not bool(cfg_value(cfg, "AUTO_CLONE_AUTOSHOT", True)):
        missing = ", ".join(str(path) for path in required if not path.exists())
        raise FileNotFoundError(f"AutoShot repo is missing required file(s): {missing}")

    repo_url = str(cfg_value(cfg, "AUTOSHOT_REPO_URL", "https://github.com/wentaozhu/AutoShot.git"))
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    if repo_dir.exists() and not any(repo_dir.iterdir()):
        repo_dir.rmdir()
    logger.info("cloning AutoShot repo to %s", repo_dir)
    subprocess.run(["git", "clone", repo_url, str(repo_dir)], check=True)

    missing_after_clone = [str(path) for path in required if not path.exists()]
    if missing_after_clone:
        raise FileNotFoundError(f"AutoShot clone is incomplete: {', '.join(missing_after_clone)}")
    return repo_dir


def add_repo_to_path(repo_dir: Path) -> None:
    resolved = str(repo_dir.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)


def select_device(raw_device: str) -> str:
    if raw_device != "auto":
        return raw_device
    require_module("torch", "Kaggle GPU notebooks normally include torch.")
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def load_autoshot_model(repo_dir: Path, checkpoint_path: Path, device: str, logger: logging.Logger):
    require_module("torch", "Kaggle GPU notebooks normally include torch.")
    import torch

    add_repo_to_path(repo_dir)
    from supernet_flattransf_3_8_8_8_13_12_0_16_60 import TransNetV2Supernet

    model = TransNetV2Supernet().eval()
    checkpoint = torch.load(str(checkpoint_path), map_location=device)
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
    model = model.to(device).eval()
    logger.info("loaded AutoShot checkpoint=%s matched_params=%d/%d device=%s", checkpoint_path, len(matched_state), len(model_state), device)
    return model


def resolve_ffmpeg_executable() -> str:
    env_binary = os.getenv("FFMPEG_BINARY", "").strip()
    candidates = [env_binary] if env_binary else []
    path_binary = shutil.which("ffmpeg")
    if path_binary:
        candidates.append(path_binary)
    if importlib.util.find_spec("imageio_ffmpeg"):
        import imageio_ffmpeg

        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return "ffmpeg"


def read_autoshot_frames(video_path: str, ffmpeg_executable: str):
    require_module("ffmpeg", "pip install ffmpeg-python imageio-ffmpeg")
    require_module("numpy", "pip install numpy")
    import ffmpeg
    import numpy as np

    width, height = 48, 27
    try:
        video_stream, _ = (
            ffmpeg.input(video_path)
            .output("pipe:", format="rawvideo", pix_fmt="rgb24", s=f"{width}x{height}")
            .run(cmd=ffmpeg_executable, capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else str(exc)
        raise RuntimeError(f"FFmpeg failed for {video_path}. stderr: {stderr[-1200:]}") from exc
    return np.frombuffer(video_stream, np.uint8).reshape([-1, height, width, 3])


def predict_boundary_scores(model: Any, video_path: str, repo_dir: Path, device: str):
    require_module("numpy", "pip install numpy")
    require_module("torch", "Kaggle GPU notebooks normally include torch.")
    import numpy as np
    import torch

    add_repo_to_path(repo_dir)
    from utils import get_batches

    frames = read_autoshot_frames(video_path, resolve_ffmpeg_executable())
    if len(frames) == 0:
        raise RuntimeError(f"AutoShot could not read frames from video: {video_path}")

    scores = []
    with torch.no_grad():
        for batch in get_batches(frames):
            x = batch.transpose((3, 0, 1, 2))
            x = x[np.newaxis, ...]
            x = torch.from_numpy(x).float().to(device)
            output = model(x)
            logits = output[0] if isinstance(output, tuple) else output
            prob = torch.sigmoid(logits[0]).detach().cpu().numpy()
            scores.append(np.squeeze(prob)[25:75])
    return np.concatenate(scores, axis=0)[: len(frames)]


def boundaries_to_shots(boundary_frames: Iterable[int], num_frames: int, min_shot_len: int) -> list[tuple[int, int]]:
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


def save_frame_at(cap: Any, frame_idx: int, out_path: Path, jpeg_quality: int) -> bool:
    import cv2

    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame_bgr = cap.read()
    if not ok or frame_bgr is None:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(out_path), frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)]))


def extract_representative_frames(
    video_path: Path,
    video_id: str,
    shots: list[tuple[int, int]],
    frames_dir: Path,
    jpeg_quality: int,
) -> list[dict[str, Any]]:
    require_module("cv2", "pip install opencv-python-headless")
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV cannot open video: {video_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    rows: list[dict[str, Any]] = []

    for shot_index, (start, end) in enumerate(shots):
        frame_items = [("first", start), ("middle", (start + end) // 2), ("last", end)]
        for frame_type, frame_idx in frame_items:
            filename = f"shot_{shot_index:04d}_{frame_type}_f{frame_idx:06d}.jpg"
            local_path = frames_dir / video_id / filename
            saved = save_frame_at(cap, frame_idx, local_path, jpeg_quality)
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


def resolve_checkpoint(cfg: Any, client: Any | None, layout: RunLayout) -> Path:
    checkpoint = str(cfg_value(cfg, "CHECKPOINT_PATH", "") or "").strip()
    if not checkpoint:
        raise RuntimeError("Set CHECKPOINT_PATH to a local Kaggle path or gs:// checkpoint.")
    if checkpoint.startswith("gs://"):
        if client is None:
            raise RuntimeError("A GCS client is required to download gs:// checkpoint.")
        parsed = parse_gcs_uri(checkpoint)
        local_path = layout.run_dir / "models" / PurePosixPath(parsed.blob_name).name
        local_path.parent.mkdir(parents=True, exist_ok=True)
        client.bucket(parsed.bucket).blob(parsed.blob_name).download_to_filename(str(local_path), timeout=900)
        return local_path
    path = Path(checkpoint)
    if not path.exists():
        raise FileNotFoundError(f"Checkpoint not found: {path}")
    return path


def upload_file(bucket: Any, local_path: Path, object_key: str, content_type: str, overwrite: bool = True) -> None:
    blob = bucket.blob(object_key)
    blob.upload_from_filename(str(local_path), content_type=content_type, timeout=600)


def upload_text(bucket: Any, text: str, object_key: str, content_type: str = "text/plain") -> None:
    bucket.blob(object_key).upload_from_string(text, content_type=content_type, timeout=600)


def upload_keyframe(bucket: Any, local_path: Path, object_key: str, skip_existing: bool, overwrite: bool) -> bool:
    blob = bucket.blob(object_key)
    if skip_existing and blob.exists():
        return True
    kwargs: dict[str, Any] = {"content_type": "image/jpeg", "timeout": 600}
    if not overwrite:
        kwargs["if_generation_match"] = 0
    blob.upload_from_filename(str(local_path), **kwargs)
    return True


def process_video_record(
    cfg: Any,
    record: dict[str, Any],
    model: Any,
    repo_dir: Path,
    device: str,
    layout: RunLayout,
    output_bucket: Any | None,
    upload_to_gcs: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import numpy as np

    started = time.perf_counter()
    video_id = str(record["video_id"])
    local_video = Path(str(record["local_video_path"]))
    output_bucket_name = str(record.get("output_bucket") or "")
    output_prefix = str(record["output_prefix"]).rstrip("/") + "/"
    jpeg_quality = int(cfg_value(cfg, "JPEG_QUALITY", 95))
    skip_existing = bool(cfg_value(cfg, "SKIP_EXISTING", True))
    overwrite = bool(cfg_value(cfg, "OVERWRITE", False))

    score_started = time.perf_counter()
    scores = predict_boundary_scores(model, str(local_video), repo_dir, device)
    score_ms = int((time.perf_counter() - score_started) * 1000)

    boundary_frames = np.where(scores > float(record.get("threshold", cfg_value(cfg, "THRESHOLD", 0.296))))[0]
    shots = boundaries_to_shots(boundary_frames, len(scores), int(record.get("min_shot_len", cfg_value(cfg, "MIN_SHOT_LEN", 5))))

    frame_started = time.perf_counter()
    frame_rows = extract_representative_frames(local_video, video_id, shots, layout.frames_dir, jpeg_quality)
    frame_extract_ms = int((time.perf_counter() - frame_started) * 1000)

    final_rows: list[dict[str, Any]] = []
    upload_ms = 0
    uploaded_count = 0
    skipped_or_present_count = 0

    for row in frame_rows:
        filename = Path(str(row["local_image_path"])).name
        image_storage_key = output_prefix + filename
        image_gcs_uri = f"gs://{output_bucket_name}/{image_storage_key}" if output_bucket_name else ""
        local_image_path = Path(str(row["local_image_path"]))

        uploaded = False
        if bool(row["saved"]):
            if upload_to_gcs:
                if output_bucket is None:
                    raise RuntimeError("output_bucket is required when upload_to_gcs=True")
                single_upload_started = time.perf_counter()
                uploaded = upload_keyframe(output_bucket, local_image_path, image_storage_key, skip_existing, overwrite)
                upload_ms += int((time.perf_counter() - single_upload_started) * 1000)
                if uploaded:
                    uploaded_count += 1
            else:
                uploaded = True
                skipped_or_present_count += 1

        shot_index = int(row["shot_id_local"])
        frame_idx = int(row["frame_idx"])
        final_rows.append(
            {
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
                "boundary_threshold": record.get("threshold", cfg_value(cfg, "THRESHOLD", 0.296)),
                "min_shot_len": record.get("min_shot_len", cfg_value(cfg, "MIN_SHOT_LEN", 5)),
                "saved": bool(row["saved"]) and uploaded,
                "fps": row["fps"],
                "total_frames_opencv": row["total_frames_opencv"],
                "profile_version": record["profile_version"],
                "run_id": record["run_id"],
            }
        )

    frames_manifest_path = layout.artifacts_dir / "frames_manifest" / f"{video_id}.jsonl"
    write_jsonl(frames_manifest_path, final_rows)
    if upload_to_gcs and output_bucket is not None:
        upload_file(output_bucket, frames_manifest_path, output_prefix + "frames_manifest.jsonl", "application/jsonl")

    duration_ms = int((time.perf_counter() - started) * 1000)
    summary = {
        "run_id": record["run_id"],
        "dataset_id": record["dataset_id"],
        "batch_id": record["batch_id"],
        "video_id": video_id,
        "status": "success",
        "input_path": str(local_video),
        "input_gcs_uri": record["input_gcs_uri"],
        "num_frames": int(len(scores)),
        "num_boundaries": int(len(boundary_frames)),
        "num_shots": int(len(shots)),
        "num_keyframes": int(len(final_rows)),
        "uploaded_keyframes": uploaded_count,
        "local_or_skipped_keyframes": skipped_or_present_count,
        "score_ms": score_ms,
        "frame_extract_ms": frame_extract_ms,
        "upload_ms": upload_ms,
        "duration_ms": duration_ms,
        "finished_at": utc_now_iso(),
    }

    if upload_to_gcs and bool(cfg_value(cfg, "CLEANUP_LOCAL_IMAGES_AFTER_UPLOAD", True)):
        shutil.rmtree(layout.frames_dir / video_id, ignore_errors=True)

    return final_rows, summary


def upload_run_artifacts(bucket: Any, layout: RunLayout, success: bool) -> None:
    upload_file(bucket, layout.manifest_path, layout.gcs_artifact_prefix + "processing_manifest.jsonl", "application/jsonl")
    upload_file(bucket, layout.shot_segments_path, layout.gcs_artifact_prefix + "shot_segments.csv", "text/csv")
    upload_file(bucket, layout.errors_path, layout.gcs_artifact_prefix + "errors.jsonl", "application/jsonl")
    upload_file(bucket, layout.video_summaries_path, layout.gcs_artifact_prefix + "video_summaries.jsonl", "application/jsonl")
    upload_file(bucket, layout.summary_path, layout.gcs_artifact_prefix + "summary.json", "application/json")
    upload_file(bucket, layout.log_path, layout.gcs_artifact_prefix + "run.log", "text/plain")
    if success:
        upload_text(bucket, "", layout.gcs_artifact_prefix + "_SUCCESS")


def run_pipeline(
    cfg: Any,
    run_kind: str,
    batches: Any,
    max_videos: int | None,
    dry_run: bool,
    upload_to_gcs: bool | None = None,
) -> dict[str, Any]:
    started_at = utc_now_iso()
    started = time.perf_counter()
    expected = [str(item).upper() for item in cfg_value(cfg, "EXPECTED_BATCHES", [])]
    selected_batches = parse_selected_batches(batches, expected)
    artifact_batch_id = selected_batches[0] if len(selected_batches) == 1 else "all"
    run_id = new_run_id(run_kind)
    layout = make_run_layout(cfg, run_id, artifact_batch_id)
    logger = setup_logging(layout, bool(cfg_value(cfg, "VERBOSE", False)))

    upload_enabled = bool(cfg_value(cfg, "UPLOAD_TO_GCS", True)) if upload_to_gcs is None else bool(upload_to_gcs)
    bucket_name = resolve_bucket_name(cfg, require=upload_enabled and not dry_run)
    client = make_storage_client(cfg) if upload_enabled and not dry_run else None
    bucket = client.bucket(bucket_name) if client is not None and bucket_name else None

    records, unmapped, input_root = discover_videos(cfg, selected_batches, max_videos, bucket_name)
    for record in records:
        record["run_id"] = run_id

    write_jsonl(layout.manifest_path, records)
    write_jsonl(layout.errors_path, [])
    write_jsonl(layout.video_summaries_path, [])
    init_csv(layout.shot_segments_path, SHOT_SEGMENTS_COLUMNS)

    logger.info(
        "run_id=%s kind=%s input_root=%s batches=%s dry_run=%s upload=%s planned_videos=%d unmapped=%d",
        run_id,
        run_kind,
        input_root,
        ",".join(selected_batches),
        dry_run,
        upload_enabled and not dry_run,
        len(records),
        len(unmapped),
    )

    if unmapped:
        write_jsonl(layout.artifacts_dir / "unmapped.jsonl", unmapped)
        logger.warning("wrote unmapped report with %d file(s)", len(unmapped))

    if dry_run:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        summary = {
            "run_id": run_id,
            "run_kind": run_kind,
            "stage": "dry_run",
            "status": "success",
            "dataset_id": str(cfg_value(cfg, "DATASET_ID", "ai_challenge_2025")),
            "batches": selected_batches,
            "input_root": str(input_root),
            "planned_videos": len(records),
            "unmapped_videos": len(unmapped),
            "max_videos": max_videos,
            "duration_ms": elapsed_ms,
            "started_at": started_at,
            "finished_at": utc_now_iso(),
            "local_run_dir": str(layout.run_dir),
            "gcs_artifact_prefix": layout.gcs_artifact_prefix,
            "sample_record": records[0] if records else {},
        }
        write_json(layout.summary_path, summary)
        logger.info("dry run finished planned_videos=%d duration_ms=%d", len(records), elapsed_ms)
        return summary

    if not records:
        raise RuntimeError("No videos found. Check INPUT_ROOT, BATCHES, EXPECTED_BATCHES, and BATCH_REGEX.")

    require_module("cv2", "pip install opencv-python-headless")
    require_module("numpy", "pip install numpy")
    require_module("torch", "Kaggle GPU notebooks normally include torch.")
    require_module("ffmpeg", "pip install ffmpeg-python")
    require_module("imageio_ffmpeg", "pip install imageio-ffmpeg")

    repo_dir = ensure_autoshot_repo(cfg, logger)
    checkpoint_path = resolve_checkpoint(cfg, client, layout)
    device = select_device(str(cfg_value(cfg, "DEVICE", "auto")))
    model = load_autoshot_model(repo_dir, checkpoint_path, device, logger)

    total = len(records)
    succeeded = 0
    failed = 0
    shot_rows_count = 0
    keyframes_count = 0

    progress = None
    if bool(cfg_value(cfg, "USE_TQDM", True)):
        try:
            from tqdm.auto import tqdm

            progress = tqdm(total=total, unit="video")
        except Exception:
            progress = None

    for index, record in enumerate(records, 1):
        pct_start = ((index - 1) / total) * 100
        if bool(cfg_value(cfg, "LOG_EVERY_VIDEO", True)):
            logger.info(
                "[%d/%d %.1f%%] start video_id=%s batch=%s path=%s",
                index,
                total,
                pct_start,
                record["video_id"],
                record["batch_id"],
                record["local_video_path"],
            )

        try:
            rows, video_summary = process_video_record(
                cfg=cfg,
                record=record,
                model=model,
                repo_dir=repo_dir,
                device=device,
                layout=layout,
                output_bucket=bucket,
                upload_to_gcs=upload_enabled,
            )
            append_csv_rows(layout.shot_segments_path, rows, SHOT_SEGMENTS_COLUMNS)
            append_jsonl(layout.video_summaries_path, video_summary)
            succeeded += 1
            shot_rows_count += len(rows)
            keyframes_count += int(video_summary.get("num_keyframes", 0))
            pct_done = (index / total) * 100
            logger.info(
                "[%d/%d %.1f%%] done video_id=%s shots=%d keyframes=%d upload_ms=%d total_ms=%d",
                index,
                total,
                pct_done,
                record["video_id"],
                video_summary.get("num_shots", 0),
                video_summary.get("num_keyframes", 0),
                video_summary.get("upload_ms", 0),
                video_summary.get("duration_ms", 0),
            )
        except Exception as exc:  # noqa: BLE001 - keep processing remaining videos.
            failed += 1
            error_row = {
                "run_id": run_id,
                "dataset_id": record.get("dataset_id", ""),
                "batch_id": record.get("batch_id", ""),
                "video_id": record.get("video_id", ""),
                "input_path": record.get("local_video_path", ""),
                "input_gcs_uri": record.get("input_gcs_uri", ""),
                "stage": "extract_upload",
                "error_code": exc.__class__.__name__,
                "error_message": str(exc),
                "failed_at": utc_now_iso(),
            }
            append_jsonl(layout.errors_path, error_row)
            logger.exception("[%d/%d] failed video_id=%s", index, total, record.get("video_id", ""))
        finally:
            if progress:
                progress.set_postfix(succeeded=succeeded, failed=failed, refresh=False)
                progress.update(1)

    if progress:
        progress.close()

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    elapsed_sec = elapsed_ms / 1000 if elapsed_ms else 0
    videos_per_hour = (succeeded / elapsed_sec * 3600) if elapsed_sec else 0
    keyframes_per_min = (keyframes_count / elapsed_sec * 60) if elapsed_sec else 0
    success = failed == 0 and succeeded == total and shot_rows_count > 0

    summary = {
        "run_id": run_id,
        "run_kind": run_kind,
        "stage": "extract_upload",
        "status": "success" if success else "failed",
        "dataset_id": str(cfg_value(cfg, "DATASET_ID", "ai_challenge_2025")),
        "profile_version": str(cfg_value(cfg, "PROFILE_VERSION", "autoshot_v1")),
        "batches": selected_batches,
        "input_root": str(input_root),
        "planned_videos": total,
        "succeeded_videos": succeeded,
        "failed_videos": failed,
        "shot_rows": shot_rows_count,
        "keyframes": keyframes_count,
        "duration_ms": elapsed_ms,
        "videos_per_hour": videos_per_hour,
        "keyframes_per_min": keyframes_per_min,
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "local_run_dir": str(layout.run_dir),
        "gcs_artifact_prefix": layout.gcs_artifact_prefix,
    }
    write_json(layout.summary_path, summary)

    if upload_enabled and bool(cfg_value(cfg, "UPLOAD_RUN_ARTIFACTS", True)) and bucket is not None:
        summary["uploaded_run_artifacts"] = True
        write_json(layout.summary_path, summary)
        upload_run_artifacts(bucket, layout, success)
        logger.info("uploaded run artifacts to gs://%s/%s", bucket_name, layout.gcs_artifact_prefix)

    logger.info(
        "run finished status=%s videos=%d/%d keyframes=%d duration_ms=%d videos_per_hour=%.2f",
        summary["status"],
        succeeded,
        total,
        keyframes_count,
        elapsed_ms,
        videos_per_hour,
    )
    return summary


def run_dry_run(cfg: Any) -> dict[str, Any]:
    return run_pipeline(
        cfg=cfg,
        run_kind="dry_run",
        batches=cfg_value(cfg, "DRY_RUN_BATCHES", "all"),
        max_videos=cfg_value(cfg, "DRY_RUN_MAX_VIDEOS", 20),
        dry_run=True,
        upload_to_gcs=False,
    )


def run_demo_one_batch(cfg: Any) -> dict[str, Any]:
    return run_pipeline(
        cfg=cfg,
        run_kind="demo",
        batches=cfg_value(cfg, "DEMO_BATCHES", "L21"),
        max_videos=cfg_value(cfg, "DEMO_MAX_VIDEOS", 2),
        dry_run=False,
        upload_to_gcs=cfg_value(cfg, "UPLOAD_TO_GCS", True),
    )


def run_full_dataset(cfg: Any) -> list[dict[str, Any]]:
    expected = [str(item).upper() for item in cfg_value(cfg, "EXPECTED_BATCHES", [])]
    batches = parse_selected_batches(cfg_value(cfg, "FULL_BATCHES", "all"), expected)
    max_videos = cfg_value(cfg, "FULL_MAX_VIDEOS", None)
    summaries: list[dict[str, Any]] = []
    for batch_id in batches:
        summaries.append(
            run_pipeline(
                cfg=cfg,
                run_kind=f"full_{batch_id.lower()}",
                batches=[batch_id],
                max_videos=max_videos,
                dry_run=False,
                upload_to_gcs=cfg_value(cfg, "UPLOAD_TO_GCS", True),
            )
        )
    return summaries


def preview_plan(cfg: Any, batches: Any | None = None, max_videos: int | None = 5) -> dict[str, Any]:
    bucket_name = resolve_bucket_name(cfg, require=False)
    selected = batches if batches is not None else cfg_value(cfg, "DRY_RUN_BATCHES", "all")
    records, unmapped, input_root = discover_videos(cfg, selected, max_videos, bucket_name)
    return {
        "input_root": str(input_root),
        "bucket": bucket_name,
        "selected_batches": parse_selected_batches(selected, [str(item).upper() for item in cfg_value(cfg, "EXPECTED_BATCHES", [])]),
        "sample_count": len(records),
        "unmapped_sample_count": len(unmapped),
        "sample_records": records[: min(len(records), max_videos or len(records))],
    }

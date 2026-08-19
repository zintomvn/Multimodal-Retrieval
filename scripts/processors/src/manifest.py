from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .artifact_io import ArtifactStore, gcs_client, join_uri, parse_gcs_uri
from .gcs_source import FrameItem
from .gcs_source import GCSFrameSource


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
BATCH_RE = re.compile(r"(?:^|/)batch=([^/]+)(?:/|$)")
VIDEO_RE = re.compile(r"(?:^|/)video_id=([^/]+)(?:/|$)")
FRAME_RE = re.compile(r"f(\d+)", re.IGNORECASE)
SHOT_RE = re.compile(r"shot[_-]?(\d+)", re.IGNORECASE)
FRAME_TYPE_RE = re.compile(r"(?:^|[_-])(first|middle|last|key)(?:[_-]|$)", re.IGNORECASE)


@dataclass(frozen=True)
class ManifestRecord:
    """Normalized keyframe manifest row."""

    dataset_id: str
    batch: str
    video_id: str
    keyframe_id: str
    frame_idx: int
    frame_seconds: float
    frame_type: str
    gcs_uri: str
    bucket: str
    blob_name: str
    image_name: str
    shot_id: str | None = None
    shot_index: int | None = None
    fps: float | None = None
    shard_index: int | None = None
    global_index: int | None = None


def build_gcs_keyframe_manifest(
    bucket_name: str,
    prefixes: list[str],
    dataset_id: str,
    credentials_file: str = "",
    timeout_seconds: float = 60.0,
    max_frames: int = 0,
    shot_segments_path: str = "",
) -> list[dict[str, Any]]:
    """List GCS keyframes and return normalized manifest rows."""
    if shot_segments_path:
        source = GCSFrameSource(bucket_name=bucket_name, credentials_file=credentials_file, timeout_seconds=timeout_seconds)
        items = []
        for prefix in prefixes:
            remaining = None if max_frames <= 0 else max_frames - len(items)
            if remaining is not None and remaining <= 0:
                break
            items.extend(source.list_frames(prefix=prefix, shot_segments_path=shot_segments_path, limit=remaining))
        rows = [frame_item_to_manifest_row(item, dataset_id=dataset_id) for item in items]
        return _with_global_indices(rows)

    client = gcs_client(credentials_file)
    rows: list[dict[str, Any]] = []
    for prefix in prefixes:
        clean_prefix = prefix.strip("/")
        for blob in client.list_blobs(bucket_name, prefix=clean_prefix, timeout=timeout_seconds):
            if Path(blob.name).suffix.lower() not in IMAGE_SUFFIXES:
                continue
            row = record_from_gcs_blob(bucket_name=bucket_name, blob_name=blob.name, dataset_id=dataset_id)
            row_dict = asdict(row)
            row_dict["size_bytes"] = int(getattr(blob, "size", 0) or 0)
            row_dict["generation"] = str(getattr(blob, "generation", "") or "")
            rows.append(row_dict)
            if max_frames and len(rows) >= max_frames:
                return _with_global_indices(rows)
    return _with_global_indices(rows)


def record_from_gcs_blob(bucket_name: str, blob_name: str, dataset_id: str) -> ManifestRecord:
    """Build one manifest record from a GCS object key."""
    image_name = Path(blob_name).name
    frame_idx = _extract_frame_idx(image_name)
    video_id = _extract_video_id(blob_name)
    batch = _extract_batch(blob_name)
    shot_index = _extract_shot_index(image_name)
    frame_type = _extract_frame_type(image_name)
    keyframe_id = f"{video_id}_F{frame_idx:06d}"
    shot_id = f"{video_id}_S{shot_index:04d}" if shot_index is not None else None
    return ManifestRecord(
        dataset_id=dataset_id,
        batch=batch,
        video_id=video_id,
        keyframe_id=keyframe_id,
        frame_idx=frame_idx,
        frame_seconds=0.0,
        frame_type=frame_type,
        gcs_uri=f"gs://{bucket_name}/{blob_name}",
        bucket=bucket_name,
        blob_name=blob_name,
        image_name=image_name,
        shot_id=shot_id,
        shot_index=shot_index,
    )


def write_manifest_and_shards(
    rows: list[dict[str, Any]],
    output_root: str,
    run_id: str,
    frames_per_shard: int,
    credentials_file: str = "",
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Write a full manifest, shard JSONL files, and a summary."""
    if frames_per_shard < 1:
        raise ValueError("frames_per_shard must be >= 1")
    store = ArtifactStore(credentials_file=credentials_file, timeout_seconds=timeout_seconds)
    run_root = join_uri(output_root.rstrip("/"), f"run_id={run_id}")
    manifest_uri = join_uri(run_root, "keyframes.jsonl")
    summary_uri = join_uri(run_root, "manifest_summary.json")
    shard_root = join_uri(run_root, "shards")

    all_rows = _with_global_indices(rows)
    store.write_jsonl(manifest_uri, all_rows)
    shard_uris: list[str] = []
    for shard_index, start in enumerate(range(0, len(all_rows), frames_per_shard)):
        shard_rows = [
            {**row, "shard_index": shard_index, "shard_local_index": local_index}
            for local_index, row in enumerate(all_rows[start : start + frames_per_shard])
        ]
        shard_uri = join_uri(shard_root, f"shard-{shard_index:05d}.jsonl")
        store.write_jsonl(shard_uri, shard_rows)
        shard_uris.append(shard_uri)
    summary = {
        "run_id": run_id,
        "manifest_uri": manifest_uri,
        "summary_uri": summary_uri,
        "shard_root": shard_root,
        "frames": len(all_rows),
        "frames_per_shard": frames_per_shard,
        "num_shards": len(shard_uris),
        "shards": shard_uris,
    }
    store.write_json(summary_uri, summary)
    return summary


def load_manifest_rows(uri: str, credentials_file: str = "", timeout_seconds: float = 60.0) -> list[dict[str, Any]]:
    """Load manifest rows from JSONL."""
    return ArtifactStore(credentials_file=credentials_file, timeout_seconds=timeout_seconds).read_jsonl(uri)


def frame_item_from_record(record: dict[str, Any]) -> FrameItem:
    """Convert a manifest or artifact frame record into FrameItem."""
    gcs_uri = str(record.get("gcs_uri") or record.get("image_gcs_uri") or record.get("image_uri") or "")
    if gcs_uri:
        parsed = parse_gcs_uri(gcs_uri)
        bucket = parsed.bucket
        blob_name = parsed.blob
    else:
        bucket = str(record.get("bucket") or "")
        blob_name = str(record.get("blob_name") or record.get("image_storage_key") or "")
    if not bucket or not blob_name:
        raise ValueError(f"Frame record does not contain a GCS location: {record}")
    video_id = str(record.get("video_id") or _extract_video_id(blob_name))
    image_name = str(record.get("image_name") or Path(blob_name).name)
    frame_idx = int(record.get("frame_idx") if record.get("frame_idx") is not None else _extract_frame_idx(image_name))
    return FrameItem(
        bucket=bucket,
        blob_name=blob_name,
        video_id=video_id,
        image_name=image_name,
        frame_idx=frame_idx,
        frame_seconds=float(record.get("frame_seconds") or record.get("frame_sec") or 0.0),
        fps=_optional_float(record.get("fps")),
        shot_index=_optional_int(record.get("shot_index")) or _shot_index_from_id(str(record.get("shot_id") or "")),
        frame_type=str(record.get("frame_type") or "key"),
    )


def _shot_index_from_id(raw: str) -> int | None:
    match = re.search(r"(?:^|[_-])S(\d+)", raw, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"shot[_-]?(\d+)", raw, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def frame_item_to_manifest_row(item: FrameItem, dataset_id: str) -> dict[str, Any]:
    """Convert FrameItem into a manifest row with timing metadata."""
    return {
        "dataset_id": dataset_id,
        "batch": item.video_id[:3] if item.video_id.startswith(("L", "K")) else "UNKNOWN",
        "video_id": item.video_id,
        "keyframe_id": item.keyframe_id,
        "shot_id": item.shot_id,
        "frame_idx": item.frame_idx,
        "frame_seconds": item.frame_seconds,
        "frame_type": item.frame_type or "key",
        "gcs_uri": item.gcs_uri,
        "bucket": item.bucket,
        "blob_name": item.blob_name,
        "image_name": item.image_name,
        "shot_index": item.shot_index,
        "fps": item.fps,
    }


def frame_record_from_item(item: FrameItem, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convert FrameItem to a JSON-serializable artifact frame record."""
    payload = {
        "bucket": item.bucket,
        "blob_name": item.blob_name,
        "gcs_uri": item.gcs_uri,
        "video_id": item.video_id,
        "keyframe_id": item.keyframe_id,
        "shot_id": item.shot_id,
        "shot_index": item.shot_index,
        "image_name": item.image_name,
        "frame_idx": item.frame_idx,
        "frame_seconds": item.frame_seconds,
        "fps": item.fps,
        "frame_type": item.frame_type,
    }
    if extra:
        payload.update(extra)
    return payload


def split_batches(raw: str) -> list[str]:
    """Parse a comma-separated batch list."""
    return [item.strip().upper() for item in str(raw or "").split(",") if item.strip()]


def gcs_prefixes_for_batches(keyframes_prefix: str, dataset_id: str, frame_profile: str, batches: Iterable[str]) -> list[str]:
    """Build current keyframe GCS prefixes for logical batches."""
    return [
        f"{keyframes_prefix.strip('/')}/dataset={dataset_id}/batch={batch}/profile={frame_profile}"
        for batch in batches
    ]


def _with_global_indices(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**row, "global_index": index} for index, row in enumerate(sorted(rows, key=_sort_key))]


def _sort_key(row: dict[str, Any]) -> tuple[str, str, int, str]:
    return (str(row.get("batch") or ""), str(row.get("video_id") or ""), int(row.get("frame_idx") or 0), str(row.get("image_name") or ""))


def _extract_batch(blob_name: str) -> str:
    match = BATCH_RE.search("/" + blob_name)
    if match:
        return match.group(1)
    parts = Path(blob_name).parts
    for part in parts:
        if re.fullmatch(r"[LK]\d{2}", part.upper()):
            return part.upper()
    return "UNKNOWN"


def _extract_video_id(blob_name: str) -> str:
    match = VIDEO_RE.search("/" + blob_name)
    if match:
        return match.group(1)
    parent = Path(blob_name).parent.name
    if parent.startswith("video_id="):
        return parent.split("=", 1)[1]
    return parent


def _extract_frame_idx(image_name: str) -> int:
    match = FRAME_RE.search(image_name)
    if not match:
        raise ValueError(f"Could not parse frame index from: {image_name}")
    return int(match.group(1))


def _extract_shot_index(image_name: str) -> int | None:
    match = SHOT_RE.search(image_name)
    return int(match.group(1)) if match else None


def _extract_frame_type(image_name: str) -> str:
    match = FRAME_TYPE_RE.search(image_name)
    return match.group(1).lower() if match else "key"


def _optional_float(raw: Any) -> float | None:
    if raw in (None, ""):
        return None
    return float(raw)


def _optional_int(raw: Any) -> int | None:
    if raw in (None, ""):
        return None
    return int(raw)

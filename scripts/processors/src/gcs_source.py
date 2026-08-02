from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Iterable

from .artifact_io import gcs_client


FRAME_IDX_RE = re.compile(r"f(\d+)", re.IGNORECASE)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass(frozen=True)
class FrameItem:
    """One frame stored in Google Cloud Storage."""

    bucket: str
    blob_name: str
    video_id: str
    image_name: str
    frame_idx: int
    frame_seconds: float
    fps: float | None = None
    shot_index: int | None = None
    frame_type: str | None = None

    @property
    def keyframe_id(self) -> str:
        return f"{self.video_id}_F{self.frame_idx:06d}"

    @property
    def shot_id(self) -> str | None:
        if self.shot_index is None:
            return None
        return f"{self.video_id}_S{self.shot_index:04d}"

    @property
    def gcs_uri(self) -> str:
        return f"gs://{self.bucket}/{self.blob_name}"


class GCSFrameSource:
    """List and download frame images from Google Cloud Storage."""

    def __init__(self, bucket_name: str, credentials_file: str = "", timeout_seconds: float = 20.0) -> None:
        self.client = gcs_client(credentials_file)
        self.bucket = self.client.bucket(bucket_name)
        self.bucket_name = bucket_name
        self.timeout_seconds = timeout_seconds

    def list_frames(
        self,
        prefix: str,
        shot_segments_path: str = "",
        video_ids: set[str] | None = None,
        limit: int | None = None,
    ) -> list[FrameItem]:
        """Return frame items under a GCS prefix, enriched by shot_segments.csv when available."""
        metadata = self._load_shot_metadata(shot_segments_path)
        frames: list[FrameItem] = []
        retry = self._retry()
        for blob in self.client.list_blobs(
            self.bucket_name,
            prefix=prefix.strip("/"),
            timeout=self.timeout_seconds,
            retry=retry,
        ):
            suffix = Path(blob.name).suffix.lower()
            if suffix not in IMAGE_SUFFIXES:
                continue
            parts = Path(blob.name).parts
            if len(parts) < 2:
                continue
            video_id = _normalize_partition_value(parts[-2], expected_key="video_id")
            if video_ids and video_id not in video_ids:
                continue
            image_name = parts[-1]
            frame_idx = _extract_frame_idx(image_name)
            if frame_idx is None:
                continue
            row = metadata.get((video_id, image_name), {})
            frames.append(
                FrameItem(
                    bucket=self.bucket_name,
                    blob_name=blob.name,
                    video_id=video_id,
                    image_name=image_name,
                    frame_idx=frame_idx,
                    frame_seconds=_optional_float_from_row(row, ("frame_sec", "frame_seconds", "timestamp_sec"), default=0.0),
                    fps=_optional_float_from_row(row, ("fps", "frames_per_second"), default=None),
                    shot_index=_shot_index_from_row(row),
                    frame_type=_optional_text_from_row(row, ("frame_type", "frame_kind", "frame_label")),
                )
            )
            if limit and len(frames) >= limit:
                break
        return sorted(frames, key=lambda item: (item.video_id, item.frame_idx, item.image_name))

    def download_to(self, item: FrameItem, destination: Path) -> Path:
        """Download a frame item into destination and return the local file path."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.bucket.blob(item.blob_name).download_to_filename(
            str(destination),
            timeout=self.timeout_seconds,
            retry=self._retry(),
        )
        return destination

    def public_url(self, blob_name: str, public_base_url: str = "") -> str:
        """Return a public URL for a GCS blob."""
        if public_base_url:
            return f"{public_base_url.rstrip('/')}/{blob_name}"
        return f"https://storage.googleapis.com/{self.bucket_name}/{blob_name}"

    def _load_shot_metadata(self, shot_segments_path: str) -> dict[tuple[str, str], dict[str, str]]:
        if not shot_segments_path:
            return {}
        text = self._read_text_path(shot_segments_path)
        rows: dict[tuple[str, str], dict[str, str]] = {}
        for row in csv.DictReader(StringIO(text)):
            image_path = str(
                row.get("image_path")
                or row.get("image_rel_path")
                or row.get("image_name")
                or row.get("image_storage_key")
                or row.get("image_uri")
                or ""
            )
            image_name = Path(image_path).name
            video_id = str(
                row.get("video_id")
                or Path(str(row.get("video_name") or "")).stem
                or Path(image_path).parent.name
                or ""
            ).strip()
            if video_id and image_name:
                rows[(video_id, image_name)] = dict(row)
        return rows

    def _read_text_path(self, path: str) -> str:
        if path.startswith("gs://"):
            without_scheme = path[len("gs://") :]
            bucket_name, blob_name = without_scheme.split("/", 1)
            return self.client.bucket(bucket_name).blob(blob_name).download_as_text(
                encoding="utf-8",
                timeout=self.timeout_seconds,
                retry=self._retry(),
            )
        local_path = Path(path).expanduser()
        return local_path.read_text(encoding="utf-8-sig")

    def _retry(self):
        from google.api_core.retry import Retry

        return Retry(initial=1.0, maximum=3.0, multiplier=2.0, deadline=self.timeout_seconds)


def chunked(items: Iterable[FrameItem], size: int) -> Iterable[list[FrameItem]]:
    """Yield fixed-size lists from an iterable."""
    batch: list[FrameItem] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _extract_frame_idx(image_name: str) -> int | None:
    match = FRAME_IDX_RE.search(image_name)
    if not match:
        return None
    return int(match.group(1))


def _normalize_partition_value(raw: str, expected_key: str = "") -> str:
    if "=" not in raw:
        return raw
    key, value = raw.split("=", 1)
    if expected_key and key != expected_key:
        return raw
    return value


def _to_float(raw: object, default: float) -> float:
    if raw in (None, ""):
        return default
    return float(raw)


def _optional_text_from_row(row: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        raw = str(row.get(key) or "").strip()
        if raw:
            return raw
    return None


def _optional_float_from_row(row: dict[str, str], keys: tuple[str, ...], default: float | None) -> float | None:
    for key in keys:
        raw = row.get(key)
        if raw in (None, ""):
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return default


def _shot_index_from_row(row: dict[str, str]) -> int | None:
    for key in ("shot_id_local", "shot_index", "shot_idx"):
        raw = row.get(key)
        if raw in (None, ""):
            continue
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            continue

    raw = str(row.get("shot_id") or "").strip()
    if not raw:
        return None

    match = re.search(r"(?:^|[_-])S(\d+)", raw, re.IGNORECASE)
    if match:
        return int(match.group(1))

    match = re.search(r"shot[_-]?(\d+)", raw, re.IGNORECASE)
    if match:
        return int(match.group(1))

    if raw.isdigit():
        return int(raw)
    return None

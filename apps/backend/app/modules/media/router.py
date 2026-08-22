from __future__ import annotations

from mimetypes import guess_type
from pathlib import Path
from collections.abc import Iterator
import re
from statistics import median
from typing import Literal
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, or_
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.config import get_settings
from app.db.models import Frame, Video
from app.db.session import get_db
from app.modules.media.urls import gcs_public_url, split_gcs_uri

router = APIRouter(prefix="/api/media", tags=["media"])
settings = get_settings()
CACHE_HEADERS = {"Cache-Control": "public, max-age=3600"}
VIDEO_CACHE_HEADERS = {
    "Accept-Ranges": "bytes",
    "Cache-Control": "public, max-age=86400",
}
VIDEO_CHUNK_SIZE = 1024 * 1024
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}


def _local_media_candidates(raw_value: str) -> list[Path]:
    raw = (raw_value or "").strip()
    if not raw or raw.startswith("http://") or raw.startswith("https://") or raw.startswith("gs://"):
        return []
    if raw.startswith("file://"):
        raw = unquote(urlparse(raw).path)

    path = Path(raw)
    if path.is_absolute():
        return [path]
    return [
        settings.data_root / raw,
        settings.data_root / "videos" / raw,
        settings.data_root / "raw_videos" / raw,
    ]


def _resolve_video_path(video: Video) -> Path | None:
    root = settings.data_root.resolve()
    seen: set[str] = set()
    for raw in (video.uri, video.source_video_path, video.video_name or ""):
        for candidate in _local_media_candidates(raw or ""):
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if not resolved.is_file():
                continue
            if root not in resolved.parents and resolved != root:
                continue
            return resolved
    return None


def _iter_file_range(path: Path, start: int, end: int) -> Iterator[bytes]:
    remaining = end - start + 1
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(VIDEO_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _local_video_response(path: Path, media_type: str, range_header: str | None) -> Response:
    file_size = path.stat().st_size
    if file_size <= 0:
        return FileResponse(path=str(path), media_type=media_type, headers=VIDEO_CACHE_HEADERS)

    if not range_header or not range_header.startswith("bytes="):
        return FileResponse(path=str(path), media_type=media_type, headers=VIDEO_CACHE_HEADERS)

    spec = range_header.removeprefix("bytes=").split(",", 1)[0].strip()
    start_raw, separator, end_raw = spec.partition("-")
    if separator != "-":
        raise HTTPException(status_code=416, detail="Invalid range", headers={"Content-Range": f"bytes */{file_size}"})

    try:
        if start_raw == "":
            suffix_length = int(end_raw)
            if suffix_length <= 0:
                raise ValueError
            start = max(0, file_size - suffix_length)
            end = file_size - 1
        else:
            start = int(start_raw)
            end = int(end_raw) if end_raw else file_size - 1
    except ValueError as exc:
        raise HTTPException(
            status_code=416,
            detail="Invalid range",
            headers={"Content-Range": f"bytes */{file_size}"},
        ) from exc

    if start < 0 or start >= file_size or end < start:
        raise HTTPException(status_code=416, detail="Range not satisfiable", headers={"Content-Range": f"bytes */{file_size}"})

    end = min(end, file_size - 1)
    content_length = end - start + 1
    headers = {
        **VIDEO_CACHE_HEADERS,
        "Content-Length": str(content_length),
        "Content-Range": f"bytes {start}-{end}/{file_size}",
    }
    return StreamingResponse(
        _iter_file_range(path, start, end),
        status_code=206,
        media_type=media_type,
        headers=headers,
    )


def _is_video_reference(raw: str) -> bool:
    value = (raw or "").strip()
    if not value:
        return False
    if value.startswith("http://") or value.startswith("https://"):
        suffix = Path(urlparse(value).path).suffix.lower()
    elif value.startswith("gs://"):
        parsed = split_gcs_uri(value, default_bucket=settings.gcs_bucket)
        suffix = Path(parsed[1]).suffix.lower() if parsed else ""
    else:
        suffix = Path(value).suffix.lower()
    return suffix in VIDEO_EXTENSIONS


def _dataset_key_from_video(video: Video) -> str:
    for raw in (video.source_video_path, video.uri):
        match = re.search(r"(?:^|/)dataset=([^/]+)", raw or "")
        if match:
            return match.group(1)
    return "ai_challenge_2025"


def _batch_id_from_video(video: Video) -> str:
    metadata = video.extra_metadata if isinstance(video.extra_metadata, dict) else {}
    for raw in (
        metadata.get("batch_id"),
        video.video_code,
        video.video_id,
        video.video_name,
        video.uri,
        video.source_video_path,
    ):
        match = re.search(r"(?:^|[/_.-])(L\d{2}|K\d{2})(?:[/_.-]|$)", str(raw or ""), flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return ""


def _video_file_names(video: Video) -> list[str]:
    code = (video.video_code or video.video_id or "").strip()
    raw_names = [video.video_name, f"{code}.mp4" if code else ""]
    names: list[str] = []
    seen: set[str] = set()
    for raw in raw_names:
        name = Path(str(raw or "").strip()).name
        if not name:
            continue
        candidates = [name] if Path(name).suffix else [f"{name}.mp4"]
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                names.append(candidate)
    return names


def _video_number_from_video(video: Video) -> int | None:
    for raw in (video.video_code, video.video_id, video.video_name):
        match = re.search(r"_V(\d+)", str(raw or ""), flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _preferred_kaggle_video_folders(batch_id: str, video_number: int | None) -> list[str]:
    suffixes = ["a"]
    if batch_id == "L26" and video_number is not None:
        if video_number >= 400:
            suffixes = ["e"]
        elif video_number >= 300:
            suffixes = ["d"]
        elif video_number >= 200:
            suffixes = ["c"]
        elif video_number >= 100:
            suffixes = ["b"]
    elif batch_id == "L30":
        suffixes = ["a"]

    fallback_suffixes = ["a", "a1", "b", "c", "d", "e"]
    suffixes.extend(suffix for suffix in fallback_suffixes if suffix not in suffixes)

    folders: list[str] = []
    for suffix in suffixes:
        if batch_id == "L30":
            folders.append(f"Video_{batch_id}_{suffix}")
        folders.append(f"Videos_{batch_id}_{suffix}")
    return folders


def _raw_video_candidate_keys(video: Video) -> list[str]:
    bucket = settings.gcs_bucket.strip()
    if not bucket:
        return []

    dataset_key = _dataset_key_from_video(video)
    batch_id = _batch_id_from_video(video)
    if not batch_id:
        return []

    keys: list[str] = []
    video_number = _video_number_from_video(video)
    kaggle_folders = _preferred_kaggle_video_folders(batch_id, video_number)
    for name in _video_file_names(video):
        kaggle_subpaths = []
        for folder in kaggle_folders:
            kaggle_subpaths.extend(
                [
                    f"ai-challenge-2025/Videos/Videos/{folder}/video/{name}",
                    f"Videos/Videos/{folder}/video/{name}",
                    f"{folder}/video/{name}",
                ]
            )
        kaggle_subpaths.append(name)
        for subpath in kaggle_subpaths:
            keys.append(
                f"raw/source=kaggle/dataset={dataset_key}/source_version=kaggle_current/batch={batch_id}/{subpath}"
            )
        for source, version in (("drive", "drive_current"), ("kaggle", "2025"), ("drive", "2025")):
            keys.append(f"raw/source={source}/dataset={dataset_key}/source_version={version}/batch={batch_id}/{name}")
            keys.append(f"raw/source={source}/dataset={dataset_key}/batch={batch_id}/{name}")
        keys.extend(
            [
                f"videos/{batch_id}/{name}",
                f"raw_videos/{batch_id}/{name}",
                f"processed/videos/dataset={dataset_key}/batch={batch_id}/{name}",
            ]
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for key in keys:
        cleaned = key.strip("/")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            deduped.append(cleaned)
    return deduped


def _video_cloud_references(video: Video) -> list[str]:
    references: list[str] = []
    for raw in (video.source_video_path, video.uri):
        value = (raw or "").strip()
        if value and _is_video_reference(value):
            references.append(value)
    if settings.gcs_bucket:
        references.extend(f"gs://{settings.gcs_bucket}/{key}" for key in _raw_video_candidate_keys(video))

    deduped: list[str] = []
    seen: set[str] = set()
    for value in references:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _gcs_object_exists(key: str) -> bool | None:
    try:
        from app.core.deps import get_object_storage

        object_storage = get_object_storage()
        exists = getattr(object_storage, "object_exists", None)
        if not callable(exists):
            return None
        return bool(exists(key))
    except Exception:
        return None


def _video_redirect_url(video: Video) -> str | None:
    for raw in _video_cloud_references(video):
        value = (raw or "").strip()
        if not value:
            continue
        if value.startswith("http://") or value.startswith("https://"):
            return value

        parsed = split_gcs_uri(value, default_bucket=settings.gcs_bucket)
        if parsed is None:
            continue
        bucket, key = parsed
        if settings.storage_provider == "gcs" and bucket == settings.gcs_bucket:
            if settings.gcs_public_url:
                public = gcs_public_url(
                    value,
                    default_bucket=settings.gcs_bucket,
                    public_base_url=settings.gcs_public_url,
                )
                if public:
                    return public
            exists = _gcs_object_exists(key)
            if exists is False:
                continue
            try:
                from app.core.deps import get_object_storage

                return get_object_storage().get_presigned_url(key, expires_in=3600)
            except Exception:
                pass
        public = gcs_public_url(
            value,
            default_bucket=settings.gcs_bucket,
            public_base_url=settings.gcs_public_url,
        )
        if public:
            return public
    return None


def _gcs_object_key_for_frame(frame: Frame) -> str | None:
    for raw in (frame.image_storage_key, frame.image_uri):
        parsed = split_gcs_uri(raw or "", default_bucket=settings.gcs_bucket)
        if parsed is None:
            continue
        bucket, key = parsed
        if bucket == settings.gcs_bucket:
            return key
    return None


def _frame_media_payload(frame: Frame) -> dict:
    return {
        "id": frame.id,
        "keyframe_id": frame.id,
        "video_id": frame.video_id,
        "video_code": frame.video.video_code if frame.video else frame.video_id,
        "frame_idx": frame.frame_idx,
        "timestamp_ms": frame.timestamp_ms,
        "frame_type": frame.frame_type,
        "thumbnail_url": f"/api/media/frames/{frame.id}/thumbnail",
        "image_url": frame.thumbnail_uri
        or frame.image_url
        or gcs_public_url(
            frame.image_uri or "",
            default_bucket=settings.gcs_bucket,
            public_base_url=settings.gcs_public_url,
        )
        or gcs_public_url(
            frame.image_storage_key or "",
            default_bucket=settings.gcs_bucket,
            public_base_url=settings.gcs_public_url,
        ),
        "image_uri": frame.image_uri,
        "image_storage_key": frame.image_storage_key,
        "is_media_present": frame.is_media_present,
    }


def _context_frame_payload(frame: Frame) -> dict:
    return {
        "id": frame.id,
        "frame_idx": frame.frame_idx,
        "timestamp_ms": frame.timestamp_ms,
        "thumbnail_url": f"/api/media/frames/{frame.id}/thumbnail",
        "image_url": frame.thumbnail_uri
        or frame.image_url
        or gcs_public_url(
            frame.image_uri or "",
            default_bucket=settings.gcs_bucket,
            public_base_url=settings.gcs_public_url,
        )
        or gcs_public_url(
            frame.image_storage_key or "",
            default_bucket=settings.gcs_bucket,
            public_base_url=settings.gcs_public_url,
        ),
        "image_uri": frame.image_uri,
        "image_storage_key": frame.image_storage_key,
        "text": " ".join(annotation.text_value or "" for annotation in frame.annotations),
    }


def _video_fps(video: Video, db: Session) -> float | None:
    if video.fps is not None and 1 <= video.fps <= 240:
        return float(video.fps)

    # Older imported videos may not have fps on the video record. Their indexed
    # keyframes still preserve source frame indices and presentation timestamps.
    samples = (
        db.query(Frame.frame_idx, Frame.frame_seconds)
        .filter(
            Frame.video_id == video.video_id,
            Frame.frame_idx > 0,
            Frame.frame_seconds > 0,
        )
        .order_by(Frame.frame_idx.asc())
        .limit(24)
        .all()
    )
    candidates = [frame_idx / frame_seconds for frame_idx, frame_seconds in samples]
    candidates = [value for value in candidates if 1 <= value <= 240]
    return float(median(candidates)) if candidates else None


@router.get("/frames")
def list_frames(
    dataset_id: str | None = None,
    video_id: str | None = None,
    video_code: str | None = None,
    frame_idx: int | None = None,
    limit: int = 60,
    offset: int = 0,
    present_only: bool = True,
    db: Session = Depends(get_db),
) -> dict:
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    query = db.query(Frame).join(Frame.video)
    if dataset_id:
        query = query.filter(Video.dataset_id == dataset_id)
    if video_id:
        query = query.filter(Frame.video_id == video_id)
    if video_code and video_code.strip():
        pattern = f"%{video_code.strip()}%"
        query = query.filter(or_(Video.video_code.ilike(pattern), Video.video_name.ilike(pattern)))
    if present_only:
        query = query.filter(Frame.is_media_present.is_(True))

    total = query.count()
    order_by = (
        (func.abs(Frame.frame_idx - frame_idx), Video.video_code.asc(), Frame.frame_idx.asc(), Frame.keyframe_id.asc())
        if frame_idx is not None
        else (Video.video_code.asc(), Frame.frame_idx.asc(), Frame.keyframe_id.asc())
    )
    frames = (
        query.options(joinedload(Frame.video))
        .order_by(*order_by)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "frames": [_frame_media_payload(frame) for frame in frames],
    }


@router.get("/frames/{frame_id}/context")
def frame_context(frame_id: str, window: int = 4, db: Session = Depends(get_db)) -> dict:
    window = min(max(window, 1), 24)
    frame = (
        db.query(Frame)
        .options(joinedload(Frame.video), selectinload(Frame.annotations))
        .filter(Frame.id == frame_id)
        .first()
    )
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")

    previous_frames = (
        db.query(Frame)
        .options(joinedload(Frame.video), selectinload(Frame.annotations))
        .filter(Frame.video_id == frame.video_id)
        .filter(Frame.frame_idx < frame.frame_idx)
        .order_by(Frame.frame_idx.desc())
        .limit(window)
        .all()
    )
    next_frames = (
        db.query(Frame)
        .options(joinedload(Frame.video), selectinload(Frame.annotations))
        .filter(Frame.video_id == frame.video_id)
        .filter(Frame.frame_idx > frame.frame_idx)
        .order_by(Frame.frame_idx.asc())
        .limit(window)
        .all()
    )
    frames = [*reversed(previous_frames), frame, *next_frames]
    return {
        "target_frame_id": frame.id,
        "video_code": frame.video.video_code,
        "frames": [_context_frame_payload(item) for item in frames],
    }


@router.get("/frames/{frame_id}/thumbnail")
def frame_thumbnail(frame_id: str, db: Session = Depends(get_db)) -> Response:
    frame = db.query(Frame).filter(Frame.id == frame_id).first()
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")

    # Frame images are cloud-first: never serve a local frame file from DATA_ROOT here.
    gcs_object_key = _gcs_object_key_for_frame(frame)
    if settings.storage_provider == "gcs" and gcs_object_key:
        try:
            from app.core.deps import get_object_storage
            object_storage = get_object_storage()
            presigned = object_storage.get_presigned_url(gcs_object_key)
            return RedirectResponse(url=presigned, status_code=307, headers=CACHE_HEADERS)
        except Exception:
            pass

    for raw_url in (
        frame.image_url,
        gcs_public_url(
            frame.image_uri or "",
            default_bucket=settings.gcs_bucket,
            public_base_url=settings.gcs_public_url,
        ),
        gcs_public_url(
            frame.image_storage_key or "",
            default_bucket=settings.gcs_bucket,
            public_base_url=settings.gcs_public_url,
        ),
    ):
        if raw_url:
            return RedirectResponse(url=raw_url, status_code=307, headers=CACHE_HEADERS)

    raise HTTPException(status_code=404, detail="Frame cloud media is not available")


@router.get("/videos/{video_id}/frames/seek")
def seek_video_frame(
    video_id: str,
    seconds: float | None = None,
    frame_idx: int | None = None,
    direction: Literal["nearest", "next", "previous"] = "nearest",
    db: Session = Depends(get_db),
) -> dict:
    """Resolve an indexed frame by video time or step to its adjacent indexed frame."""
    if seconds is not None and seconds < 0:
        raise HTTPException(status_code=422, detail="seconds must be zero or greater")
    if seconds is None and frame_idx is None:
        raise HTTPException(status_code=422, detail="Provide seconds or frame_idx")

    video = db.query(Video).filter(Video.video_id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    query = (
        db.query(Frame)
        .options(joinedload(Frame.video), selectinload(Frame.annotations))
        .filter(Frame.video_id == video_id, Frame.is_media_present.is_(True))
    )

    if direction == "next":
        if frame_idx is None:
            raise HTTPException(status_code=422, detail="frame_idx is required for next frame")
        frame = query.filter(Frame.frame_idx > frame_idx).order_by(Frame.frame_idx.asc()).first()
    elif direction == "previous":
        if frame_idx is None:
            raise HTTPException(status_code=422, detail="frame_idx is required for previous frame")
        frame = query.filter(Frame.frame_idx < frame_idx).order_by(Frame.frame_idx.desc()).first()
    elif seconds is not None:
        target_ms = round(seconds * 1000)
        frame = query.order_by(func.abs(Frame.timestamp_ms - target_ms).asc(), Frame.frame_idx.asc()).first()
    else:
        frame = query.order_by(func.abs(Frame.frame_idx - frame_idx).asc(), Frame.frame_idx.asc()).first()

    if not frame:
        label = "next" if direction == "next" else "previous" if direction == "previous" else "indexed"
        raise HTTPException(status_code=404, detail=f"No {label} frame is available")

    fps = _video_fps(video, db)
    if direction == "nearest" and seconds is not None:
        selected_frame_idx = round(seconds * fps) if fps is not None else frame.frame_idx
        selected_timestamp_ms = round(seconds * 1000)
    else:
        selected_frame_idx = frame.frame_idx
        selected_timestamp_ms = frame.timestamp_ms

    return {
        "video_id": video.video_id,
        "video_code": video.video_code,
        "frame": _context_frame_payload(frame),
        "selection": {
            "frame_idx": selected_frame_idx,
            "timestamp_ms": selected_timestamp_ms,
            "fps": fps,
        },
    }


@router.get("/videos/{video_id}/preview")
@router.head("/videos/{video_id}/preview")
def video_preview(video_id: str, request: Request, db: Session = Depends(get_db)) -> Response:
    video = db.query(Video).filter(Video.video_id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    redirect_url = _video_redirect_url(video)
    if redirect_url:
        return RedirectResponse(url=redirect_url, status_code=307, headers=VIDEO_CACHE_HEADERS)

    local_video = _resolve_video_path(video)
    if local_video is not None:
        media_type = guess_type(str(local_video))[0] or "video/mp4"
        return _local_video_response(local_video, media_type, request.headers.get("range"))

    raise HTTPException(status_code=404, detail="Video media is not available")


@router.get("/videos/{video_id}/preview-url")
def video_preview_url(video_id: str, db: Session = Depends(get_db)) -> dict:
    video = db.query(Video).filter(Video.video_id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    redirect_url = _video_redirect_url(video)
    if redirect_url:
        return {
            "video_id": video.video_id,
            "url": redirect_url,
            "direct": True,
            "provider": "gcs" if "storage.googleapis.com" in redirect_url or "X-Goog-" in redirect_url else "http",
        }

    local_video = _resolve_video_path(video)
    if local_video is not None:
        return {
            "video_id": video.video_id,
            "url": f"/api/media/videos/{video.video_id}/preview",
            "direct": False,
            "provider": "local",
        }

    raise HTTPException(status_code=404, detail="Video media is not available")

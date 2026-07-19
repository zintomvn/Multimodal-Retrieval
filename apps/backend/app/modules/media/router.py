from __future__ import annotations

from mimetypes import guess_type
from pathlib import Path
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.db.models import Frame, Video
from app.db.session import get_db
from app.modules.media.urls import gcs_public_url, split_gcs_uri

router = APIRouter(prefix="/api/media", tags=["media"])
settings = get_settings()
CACHE_HEADERS = {"Cache-Control": "public, max-age=3600"}


def _thumbnail_candidates(frame: Frame) -> list[Path]:
    candidates: list[Path] = []
    rel_path_raw = (frame.image_rel_path or "").lstrip("/\\")
    if rel_path_raw:
        rel_path = Path(rel_path_raw)
        candidates.extend(
            [
                settings.data_root / "frames" / rel_path,
                settings.data_root / rel_path,
                settings.data_root / "keyframes" / rel_path,
            ]
        )

    if frame.video and frame.frame_idx is not None:
        suffix = f"f{int(frame.frame_idx):06d}"
        for base in (settings.data_root / "frames", settings.data_root / "keyframes"):
            video_dir = base / frame.video.video_code
            if not video_dir.is_dir():
                continue
            matches = sorted(video_dir.glob(f"*{suffix}.*"))
            if matches:
                candidates.extend(matches)

    return candidates


def _resolve_thumbnail_path(frame: Frame) -> Path | None:
    root = settings.data_root.resolve()
    seen: set[str] = set()
    for candidate in _thumbnail_candidates(frame):
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


def _video_redirect_url(video: Video) -> str | None:
    for raw in (video.uri, video.source_video_path):
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


@router.get("/frames")
def list_frames(
    dataset_id: str | None = None,
    video_id: str | None = None,
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
    if present_only:
        query = query.filter(Frame.is_media_present.is_(True))

    total = query.count()
    frames = (
        query.options(joinedload(Frame.video))
        .order_by(Video.video_code.asc(), Frame.frame_idx.asc(), Frame.keyframe_id.asc())
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
    frame = db.query(Frame).filter(Frame.id == frame_id).first()
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")
    frames = (
        db.query(Frame)
        .filter(Frame.video_id == frame.video_id)
        .filter(Frame.frame_idx >= frame.frame_idx - window * 500)
        .filter(Frame.frame_idx <= frame.frame_idx + window * 500)
        .order_by(Frame.frame_idx.asc())
        .all()
    )
    return {
        "target_frame_id": frame.id,
        "video_code": frame.video.video_code,
        "frames": [
            {
                "id": item.id,
                "frame_idx": item.frame_idx,
                "timestamp_ms": item.timestamp_ms,
                "thumbnail_url": f"/api/media/frames/{item.id}/thumbnail",
                "image_url": item.thumbnail_uri
                or item.image_url
                or gcs_public_url(
                    item.image_uri or "",
                    default_bucket=settings.gcs_bucket,
                    public_base_url=settings.gcs_public_url,
                )
                or gcs_public_url(
                    item.image_storage_key or "",
                    default_bucket=settings.gcs_bucket,
                    public_base_url=settings.gcs_public_url,
                ),
                "image_uri": item.image_uri,
                "image_storage_key": item.image_storage_key,
                "text": " ".join(annotation.text_value or "" for annotation in item.annotations),
            }
            for item in frames
        ],
    }


@router.get("/frames/{frame_id}/thumbnail")
def mock_thumbnail(frame_id: str, db: Session = Depends(get_db)) -> Response:
    frame = db.query(Frame).filter(Frame.id == frame_id).first()
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")

    thumbnail = _resolve_thumbnail_path(frame)
    if thumbnail is not None:
        return FileResponse(path=str(thumbnail), headers=CACHE_HEADERS)

    # Fallback 1: presigned URL via GCS object key (reliable, works with private buckets)
    gcs_object_key = _gcs_object_key_for_frame(frame)
    if settings.storage_provider == "gcs" and gcs_object_key:
        try:
            from app.core.deps import get_object_storage
            object_storage = get_object_storage()
            presigned = object_storage.get_presigned_url(gcs_object_key)
            return RedirectResponse(url=presigned, status_code=307, headers=CACHE_HEADERS)
        except Exception:
            pass

    # Fallback 2: public_url (best-effort, only works if bucket is public)
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

    # Fallback 3: mock SVG
    title = f"{frame.video.video_code} / {frame.frame_idx}"
    caption = " ".join((annotation.text_value or "")[:80] for annotation in frame.annotations[:1])
    color_seed = abs(hash(frame.video.video_code)) % 360
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="480" height="270" viewBox="0 0 480 270">
<rect width="480" height="270" fill="hsl({color_seed},55%,20%)"/>
<rect x="16" y="16" width="448" height="238" rx="8" fill="hsl({(color_seed + 40) % 360},45%,32%)"/>
<text x="28" y="56" fill="white" font-family="Arial" font-size="26" font-weight="700">{title}</text>
<text x="28" y="96" fill="white" font-family="Arial" font-size="15">{caption}</text>
<text x="28" y="230" fill="rgba(255,255,255,0.72)" font-family="Arial" font-size="14">mock thumbnail</text>
</svg>"""
    return Response(content=svg, media_type="image/svg+xml", headers=CACHE_HEADERS)


@router.get("/videos/{video_id}/preview")
def video_preview(video_id: str, db: Session = Depends(get_db)) -> Response:
    video = db.query(Video).filter(Video.video_id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    local_video = _resolve_video_path(video)
    if local_video is not None:
        media_type = guess_type(str(local_video))[0] or "video/mp4"
        return FileResponse(path=str(local_video), media_type=media_type, headers=CACHE_HEADERS)

    redirect_url = _video_redirect_url(video)
    if redirect_url:
        return RedirectResponse(url=redirect_url, status_code=307, headers=CACHE_HEADERS)

    raise HTTPException(status_code=404, detail="Video media is not available")

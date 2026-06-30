from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Frame
from app.db.session import get_db

router = APIRouter(prefix="/api/media", tags=["media"])
settings = get_settings()


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
        return FileResponse(path=str(thumbnail))

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
    return Response(content=svg, media_type="image/svg+xml")

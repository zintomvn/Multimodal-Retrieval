from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.db.models import Frame
from app.db.session import get_db

router = APIRouter(prefix="/api/media", tags=["media"])


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

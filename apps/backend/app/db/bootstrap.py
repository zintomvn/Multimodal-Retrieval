from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import (
    Base,
    Dataset,
    Event,
    Frame,
    FrameAnnotation,
    IndexBuild,
    Video,
)
from app.db.session import engine


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def seed_mock_data(db: Session) -> None:
    existing = db.query(Dataset).filter(Dataset.name == "mock-aic-2026").first()
    if existing:
        return

    dataset = Dataset(name="mock-aic-2026", version="v0", root_uri="data/mock", status="READY")
    db.add(dataset)
    db.flush()

    video_specs = [
        ("L00_V000", 25, 120000, "exhibition"),
        ("L01_V028", 25, 180000, "castle"),
        ("L10_V001", 30, 240000, "bicycle_race"),
    ]
    videos: dict[str, Video] = {}
    for code, fps, duration_ms, theme in video_specs:
        video = Video(
            dataset_id=dataset.id,
            video_code=code,
            uri=f"data/mock/videos/{code}.mp4",
            fps=fps,
            duration_ms=duration_ms,
            width=1920,
            height=1080,
            extra_metadata={"theme": theme},
        )
        db.add(video)
        db.flush()
        videos[code] = video

    def add_frame(video_code: str, frame_idx: int, text: str, kind: str = "CAPTION", answer_hint: str | None = None) -> Frame:
        video = videos[video_code]
        timestamp_ms = int(frame_idx / (video.fps or 25) * 1000)
        frame = Frame(
            video_id=video.id,
            frame_idx=frame_idx,
            timestamp_ms=timestamp_ms,
            image_uri=f"mock://{video_code}/{frame_idx}.jpg",
            thumbnail_uri=f"mock://{video_code}/{frame_idx}.thumb.jpg",
            quality_score=1.0,
        )
        db.add(frame)
        db.flush()
        db.add(
            FrameAnnotation(
                frame_id=frame.id,
                kind=kind,
                text_value=text,
                json_value={"answer_hint": answer_hint} if answer_hint else {},
                confidence=0.99,
                model_version="mock-v0",
            )
        )
        return frame

    # KIS-focused mock frames.
    add_frame(
        "L00_V000",
        1234,
        "royal style decorative panel exhibition program PHU XUAN GIA DINH NHUNG DAU AN LICH SU dragon cloud motifs traditional festival gate pillars vibrant patterns",
    )
    add_frame("L00_V000", 5555, "people walking inside an exhibition hall with posters and warm lights")
    add_frame("L00_V000", 8000, "outdoor street scene with banners and motorbikes")

    # QA-focused mock frames.
    add_frame(
        "L01_V028",
        3450,
        "Neuschwanstein castle in Bavaria Germany UNESCO world heritage inspiration for famous company logo Disney majestic lake forest hills",
        answer_hint="Disney",
    )
    add_frame("L01_V028", 7100, "large lake and mountain landscape near a castle in Europe")
    add_frame("L01_V028", 9200, "tourists standing near a stone bridge taking photos")

    # TRAKE-focused mock frames.
    add_frame("L10_V001", 1200, "first cyclist pink helmet pink jersey wheel crosses finish line bicycle race")
    add_frame("L10_V001", 1850, "second cyclist blue helmet crosses finish line shortly after first racer")
    add_frame("L10_V001", 2100, "third cyclist red helmet crosses finish line in succession")
    add_frame("L10_V001", 3900, "crowd cheering beside cycling race barrier")

    # Background frames so ranking has realistic noise.
    for code in videos:
        for frame_idx in range(250, 5000, 500):
            add_frame(code, frame_idx, f"generic keyframe {frame_idx} from video {code} with people scene object background")

    for video in videos.values():
        sorted_frames = sorted(video.frames, key=lambda f: f.frame_idx)
        for order, frame in enumerate(sorted_frames):
            db.add(
                Event(
                    video_id=video.id,
                    start_frame_idx=max(0, frame.frame_idx - 100),
                    end_frame_idx=frame.frame_idx + 100,
                    representative_frame_id=frame.id,
                    title=f"{video.video_code} event {order + 1}",
                    description=" ".join(a.text_value or "" for a in frame.annotations),
                    event_order=order + 1,
                    segmentation_version="mock-v0",
                )
            )

    db.add(
        IndexBuild(
            dataset_id=dataset.id,
            index_type="MILVUS",
            collection_name="frame_embeddings_mock_aic_2026_clip_mock",
            model_name="clip_mock",
            model_version="mock-v0",
            status="READY",
            stats={"frames": db.query(Frame).count(), "mode": "mock"},
        )
    )
    db.commit()

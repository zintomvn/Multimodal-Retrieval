from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    Dataset,
    Event,
    EventKeyframe,
    Frame,
    FrameAnnotation,
    IndexBuild,
    Shot,
    Video,
)


def init_db() -> None:
    settings = get_settings()
    backend_root = Path(__file__).resolve().parents[2]
    alembic_ini = backend_root / "alembic.ini"
    if alembic_ini.exists():
        try:
            from alembic import command
            from alembic.config import Config

            cfg = Config(str(alembic_ini))
            cfg.set_main_option("sqlalchemy.url", settings.database_url)
            command.upgrade(cfg, "head")
            return
        except Exception:
            # Fall back to metadata create in environments where Alembic is unavailable.
            pass
    # Fallback for environments where Alembic is not present yet.
    from app.db.models import Base
    from app.db.session import engine

    Base.metadata.create_all(bind=engine)


def seed_mock_data(db: Session) -> None:
    existing = db.query(Dataset).filter(Dataset.name == "mock-aic-2026").first()
    if existing:
        return

    dataset = Dataset(
        dataset_code="mock-aic-2026",
        name="mock-aic-2026",
        version="v0",
        root_uri="data/mock",
        status="READY",
    )
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
            video_id=code,
            dataset_id=dataset.id,
            video_code=code,
            video_name=f"{code}.mp4",
            uri=f"data/mock/videos/{code}.mp4",
            source_video_path=f"data/mock/videos/{code}.mp4",
            fps=fps,
            duration_seconds=duration_ms / 1000.0,
            duration_ms=duration_ms,
            width=1920,
            height=1080,
            extra_metadata={"theme": theme},
        )
        db.add(video)
        db.flush()
        videos[code] = video

    shot_cache: dict[tuple[str, int], Shot] = {}

    def add_frame(video_code: str, frame_idx: int, text: str, kind: str = "CAPTION", answer_hint: str | None = None) -> Frame:
        video = videos[video_code]
        shot_index = max(0, frame_idx // 500)
        shot_key = (video_code, shot_index)
        shot = shot_cache.get(shot_key)
        if shot is None:
            start_frame = shot_index * 500
            end_frame = start_frame + 499
            shot = Shot(
                shot_id=f"{video_code}_S{shot_index:04d}",
                video_id=video.id,
                shot_index=shot_index,
                start_frame=start_frame,
                end_frame=end_frame,
                start_seconds=start_frame / (video.fps or 25),
                end_seconds=end_frame / (video.fps or 25),
                boundary_threshold=0.296,
            )
            db.add(shot)
            db.flush()
            shot_cache[shot_key] = shot

        timestamp_ms = int(frame_idx / (video.fps or 25) * 1000)
        frame = Frame(
            keyframe_id=f"{video_code}_F{frame_idx:06d}",
            video_id=video.id,
            shot_id=shot.id,
            frame_idx=frame_idx,
            frame_seconds=timestamp_ms / 1000.0,
            timestamp_ms=timestamp_ms,
            frame_type="middle",
            map_n=frame_idx + 1,
            embedding_index_0=frame_idx,
            image_rel_path=f"{video_code}/shot_auto_middle_f{frame_idx:06d}.jpg",
            image_storage_key=f"keyframes/{video_code}/shot_auto_middle_f{frame_idx:06d}.jpg",
            image_url=f"/api/media/static/keyframes/{video_code}/shot_auto_middle_f{frame_idx:06d}.jpg",
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
                    event_id=f"{video.video_code}_E{order:06d}",
                    video_id=video.id,
                    embedding_index_0=order,
                    start_seconds=max(0, frame.frame_seconds - 4.0),
                    end_seconds=frame.frame_seconds + 4.0,
                    start_frame=max(0, frame.frame_idx - 100),
                    end_frame=frame.frame_idx + 100,
                    representative_keyframe_id=frame.id,
                    n_shots=1,
                    n_keyframes=1,
                    start_frame_idx=max(0, frame.frame_idx - 100),
                    end_frame_idx=frame.frame_idx + 100,
                    representative_frame_id=frame.id,
                    title=f"{video.video_code} event {order + 1}",
                    description=" ".join(a.text_value or "" for a in frame.annotations),
                    event_order=order + 1,
                    segmentation_version="mock-v0",
                )
            )
            db.flush()
            db.add(
                EventKeyframe(
                    event_id=f"{video.video_code}_E{order:06d}",
                    seq_no=0,
                    keyframe_id=frame.id,
                    keyframe_embedding_index_0=frame.embedding_index_0,
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

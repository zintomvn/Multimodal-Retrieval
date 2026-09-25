from __future__ import annotations

import uuid

from sqlalchemy import (
    CheckConstraint,
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import declarative_base, relationship, synonym


Base = declarative_base()


def new_id() -> str:
    return str(uuid.uuid4())


class Dataset(Base):
    __tablename__ = "datasets"

    dataset_id = Column(String(36), primary_key=True, default=new_id)
    id = synonym("dataset_id")
    dataset_code = Column(String(128), nullable=False, default="aic-2026", unique=True)
    name = Column(String(255), nullable=False)
    version = Column(String(64), nullable=False)
    root_uri = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="READY")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    videos = relationship("Video", back_populates="dataset", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("name", "version", name="uq_dataset_name_version"),)


class Video(Base):
    __tablename__ = "videos"

    video_id = Column(String(64), primary_key=True)
    id = synonym("video_id")
    dataset_id = Column(String(36), ForeignKey("datasets.dataset_id"), nullable=False)
    video_code = Column(String(64), nullable=False)
    video_name = Column(String(255))
    uri = Column(Text, nullable=False)
    source_video_path = Column(Text)
    fps = Column(Float)
    duration_seconds = Column(Float)
    duration_ms = Column(Integer)
    width = Column(Integer)
    height = Column(Integer)
    num_keyframes = Column(Integer)
    embedding_shape = Column(String(64))
    source_feature_path = Column(Text)
    source_map_path = Column(Text)
    extra_metadata = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    dataset = relationship("Dataset", back_populates="videos")
    frames = relationship("Frame", back_populates="video", cascade="all, delete-orphan")
    events = relationship("Event", back_populates="video", cascade="all, delete-orphan")
    shots = relationship("Shot", back_populates="video", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("dataset_id", "video_code", name="uq_video_dataset_code"),)


class Shot(Base):
    __tablename__ = "shots"

    shot_id = Column(String(100), primary_key=True)
    id = synonym("shot_id")
    video_id = Column(String(64), ForeignKey("videos.video_id"), nullable=False)
    shot_index = Column(Integer, nullable=False)
    start_frame = Column(Integer, nullable=False)
    end_frame = Column(Integer, nullable=False)
    start_seconds = Column(Float, nullable=False)
    end_seconds = Column(Float, nullable=False)
    boundary_threshold = Column(Float)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    video = relationship("Video", back_populates="shots")
    frames = relationship("Frame", back_populates="shot")

    __table_args__ = (
        UniqueConstraint("video_id", "shot_index", name="uq_shot_video_index"),
        CheckConstraint("shot_index >= 0", name="ck_shot_index_non_negative"),
        CheckConstraint("start_frame >= 0", name="ck_shot_start_frame_non_negative"),
        CheckConstraint("end_frame >= start_frame", name="ck_shot_frame_order"),
        CheckConstraint("start_seconds >= 0", name="ck_shot_start_seconds_non_negative"),
        CheckConstraint("end_seconds >= start_seconds", name="ck_shot_seconds_order"),
    )


class Frame(Base):
    __tablename__ = "keyframes"

    keyframe_id = Column(String(100), primary_key=True)
    id = synonym("keyframe_id")
    video_id = Column(String(64), ForeignKey("videos.video_id"), nullable=False)
    shot_id = Column(String(100), ForeignKey("shots.shot_id"))
    frame_idx = Column(Integer, nullable=False)
    frame_seconds = Column(Float, nullable=False, default=0.0)
    timestamp_ms = Column(Integer, nullable=False, default=0)
    frame_type = Column(String(10))
    map_n = Column(Integer)
    embedding_index_0 = Column(Integer)
    image_rel_path = Column(Text)
    image_storage_key = Column(Text)
    image_url = Column(Text)
    image_uri = Column(Text, nullable=False)
    thumbnail_uri = Column(Text)
    dedup_group_id = Column(String(36))
    quality_score = Column(Float, default=1.0)
    is_media_present = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    video = relationship("Video", back_populates="frames")
    shot = relationship("Shot", back_populates="frames")
    annotations = relationship("FrameAnnotation", back_populates="frame", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("video_id", "frame_idx", name="uq_frame_video_idx"),)


class Event(Base):
    __tablename__ = "events"

    event_id = Column(String(100), primary_key=True)
    id = synonym("event_id")
    video_id = Column(String(64), ForeignKey("videos.video_id"), nullable=False)
    embedding_index_0 = Column(Integer, nullable=False, default=0)
    start_seconds = Column(Float, nullable=False, default=0.0)
    end_seconds = Column(Float, nullable=False, default=0.0)
    start_frame = Column(Integer, nullable=False, default=0)
    end_frame = Column(Integer, nullable=False, default=0)
    representative_keyframe_id = Column(String(100), ForeignKey("keyframes.keyframe_id"))
    n_shots = Column(Integer)
    n_keyframes = Column(Integer)
    shot_ids_raw = Column(Text)
    keyframe_embedding_indices_raw = Column(Text)

    # Legacy compatibility fields used by existing retrieval flow.
    start_frame_idx = Column(Integer, nullable=False)
    end_frame_idx = Column(Integer, nullable=False)
    representative_frame_id = Column(String(100), ForeignKey("keyframes.keyframe_id"))
    title = Column(Text)
    description = Column(Text)
    event_order = Column(Integer, nullable=False)
    segmentation_version = Column(String(64), nullable=False, default="unknown")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    video = relationship("Video", back_populates="events")
    event_keyframes = relationship("EventKeyframe", back_populates="event", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("video_id", "embedding_index_0", name="uq_event_video_embedding_idx"),
        CheckConstraint("start_seconds >= 0", name="ck_event_start_seconds_non_negative"),
        CheckConstraint("end_seconds >= start_seconds", name="ck_event_seconds_order"),
    )


class EventKeyframe(Base):
    __tablename__ = "event_keyframes"

    event_id = Column(String(100), ForeignKey("events.event_id"), primary_key=True)
    seq_no = Column(Integer, primary_key=True)
    keyframe_id = Column(String(100), ForeignKey("keyframes.keyframe_id"), nullable=False)
    keyframe_embedding_index_0 = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    event = relationship("Event", back_populates="event_keyframes")
    frame = relationship("Frame")

    __table_args__ = (
        UniqueConstraint("event_id", "keyframe_id", name="uq_event_keyframe_pair"),
        CheckConstraint("seq_no >= 0", name="ck_event_keyframe_seq_non_negative"),
    )


class FrameAnnotation(Base):
    __tablename__ = "frame_annotations"

    id = Column(String(36), primary_key=True, default=new_id)
    frame_id = Column(String(100), ForeignKey("keyframes.keyframe_id"), nullable=False)
    keyframe_id = synonym("frame_id")
    kind = Column(String(32), nullable=False)
    text_value = Column(Text)
    json_value = Column(JSON, nullable=False, default=dict)
    confidence = Column(Float, default=1.0)
    model_version = Column(String(128), nullable=False, default="unknown")
    caption = Column(Text)
    ocr_texts = Column(JSON, nullable=False, default=list)
    detected_objects = Column(JSON, nullable=False, default=list)
    object_counts = Column(JSON, nullable=False, default=dict)
    detections = Column(JSON, nullable=False, default=list)
    annotation_version = Column(String(128))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    frame = relationship("Frame", back_populates="annotations")


class ModelRegistryRecord(Base):
    __tablename__ = "model_registry"

    id = Column(String(36), primary_key=True, default=new_id)
    name = Column(String(255), nullable=False)
    task = Column(String(128), nullable=False)
    provider = Column(String(128), nullable=False)
    checkpoint_uri = Column(Text)
    config = Column(JSON, nullable=False, default=dict)
    status = Column(String(32), nullable=False, default="READY")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("name", "task", name="uq_model_name_task"),)


class IndexBuild(Base):
    __tablename__ = "index_builds"

    id = Column(String(36), primary_key=True, default=new_id)
    dataset_id = Column(String(36), ForeignKey("datasets.dataset_id"), nullable=False)
    index_type = Column(String(32), nullable=False)
    collection_name = Column(String(255))
    model_name = Column(String(255))
    model_version = Column(String(128))
    status = Column(String(32), nullable=False, default="READY")
    stats = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True))


class QueryRun(Base):
    __tablename__ = "query_runs"

    id = Column(String(36), primary_key=True, default=new_id)
    dataset_id = Column(String(36), ForeignKey("datasets.dataset_id"), nullable=False)
    query_name = Column(String(255))
    query_type = Column(String(32), nullable=False)
    query_text = Column(Text, nullable=False)
    normalized_query = Column(JSON, nullable=False, default=dict)
    options = Column(JSON, nullable=False, default=dict)
    status = Column(String(32), nullable=False, default="RUNNING")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    results = relationship("RetrievalResult", back_populates="query_run", cascade="all, delete-orphan")


class RetrievalResult(Base):
    __tablename__ = "retrieval_results"

    id = Column(String(36), primary_key=True, default=new_id)
    query_run_id = Column(String(36), ForeignKey("query_runs.id"), nullable=False)
    rank = Column(Integer, nullable=False)
    video_id = Column(String(64), ForeignKey("videos.video_id"), nullable=False)
    frame_id = Column(String(100), ForeignKey("keyframes.keyframe_id"))
    event_id = Column(String(100), ForeignKey("events.event_id"))
    answer = Column(Text)
    score = Column(Float, nullable=False)
    score_breakdown = Column(JSON, nullable=False, default=dict)
    sequence_frames = Column(JSON, nullable=False, default=list)
    selected = Column(Boolean, nullable=False, default=False)

    query_run = relationship("QueryRun", back_populates="results")
    video = relationship("Video")
    frame = relationship("Frame")

    __table_args__ = (UniqueConstraint("query_run_id", "rank", name="uq_query_run_rank"),)


class Submission(Base):
    __tablename__ = "submissions"

    id = Column(String(36), primary_key=True, default=new_id)
    dataset_id = Column(String(36), ForeignKey("datasets.dataset_id"), nullable=False)
    name = Column(String(255), nullable=False)
    zip_uri = Column(Text)
    status = Column(String(32), nullable=False, default="DRAFT")
    validation_report = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    items = relationship("SubmissionItem", back_populates="submission", cascade="all, delete-orphan")


class SubmissionItem(Base):
    __tablename__ = "submission_items"

    id = Column(String(36), primary_key=True, default=new_id)
    submission_id = Column(String(36), ForeignKey("submissions.id"), nullable=False)
    query_name = Column(String(255), nullable=False)
    query_type = Column(String(32), nullable=False)
    rank = Column(Integer, nullable=False)
    video_code = Column(String(64), nullable=False)
    frame_indices = Column(JSON, nullable=False, default=list)
    answer = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    submission = relationship("Submission", back_populates="items")

    __table_args__ = (UniqueConstraint("submission_id", "query_name", "rank", name="uq_submission_query_rank"),)


class DresSubmission(Base):
    """A durable idempotency record for answers sent to DRES."""

    __tablename__ = "dres_submissions"

    id = Column(String(36), primary_key=True, default=new_id)
    evaluation_id = Column(String(128), nullable=False)
    evaluation_name = Column(String(255), nullable=False)
    media_item_name = Column(String(64), nullable=False)
    timestamp_ms = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="PENDING")
    response = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "evaluation_id",
            "media_item_name",
            "timestamp_ms",
            name="uq_dres_submission_answer",
        ),
    )


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=new_id)
    kind = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="PENDING")
    progress = Column(Float, nullable=False, default=0.0)
    message = Column(Text)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

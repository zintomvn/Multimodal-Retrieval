from __future__ import annotations

import uuid

from sqlalchemy import (
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
from sqlalchemy.orm import declarative_base, relationship


Base = declarative_base()


def new_id() -> str:
    return str(uuid.uuid4())


class Dataset(Base):
    __tablename__ = "datasets"

    id = Column(String(36), primary_key=True, default=new_id)
    name = Column(String(255), nullable=False)
    version = Column(String(64), nullable=False)
    root_uri = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="READY")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    videos = relationship("Video", back_populates="dataset", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("name", "version", name="uq_dataset_name_version"),)


class Video(Base):
    __tablename__ = "videos"

    id = Column(String(36), primary_key=True, default=new_id)
    dataset_id = Column(String(36), ForeignKey("datasets.id"), nullable=False)
    video_code = Column(String(64), nullable=False)
    uri = Column(Text, nullable=False)
    fps = Column(Float)
    duration_ms = Column(Integer)
    width = Column(Integer)
    height = Column(Integer)
    extra_metadata = Column(JSON, nullable=False, default=dict)

    dataset = relationship("Dataset", back_populates="videos")
    frames = relationship("Frame", back_populates="video", cascade="all, delete-orphan")
    events = relationship("Event", back_populates="video", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("dataset_id", "video_code", name="uq_video_dataset_code"),)


class Frame(Base):
    __tablename__ = "frames"

    id = Column(String(36), primary_key=True, default=new_id)
    video_id = Column(String(36), ForeignKey("videos.id"), nullable=False)
    frame_idx = Column(Integer, nullable=False)
    timestamp_ms = Column(Integer, nullable=False)
    image_uri = Column(Text, nullable=False)
    thumbnail_uri = Column(Text)
    shot_id = Column(String(36))
    dedup_group_id = Column(String(36))
    quality_score = Column(Float, default=1.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    video = relationship("Video", back_populates="frames")
    annotations = relationship("FrameAnnotation", back_populates="frame", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("video_id", "frame_idx", name="uq_frame_video_idx"),)


class Event(Base):
    __tablename__ = "events"

    id = Column(String(36), primary_key=True, default=new_id)
    video_id = Column(String(36), ForeignKey("videos.id"), nullable=False)
    start_frame_idx = Column(Integer, nullable=False)
    end_frame_idx = Column(Integer, nullable=False)
    representative_frame_id = Column(String(36), ForeignKey("frames.id"))
    title = Column(Text)
    description = Column(Text)
    event_order = Column(Integer, nullable=False)
    segmentation_version = Column(String(64), nullable=False, default="mock-v0")

    video = relationship("Video", back_populates="events")


class FrameAnnotation(Base):
    __tablename__ = "frame_annotations"

    id = Column(String(36), primary_key=True, default=new_id)
    frame_id = Column(String(36), ForeignKey("frames.id"), nullable=False)
    kind = Column(String(32), nullable=False)
    text_value = Column(Text)
    json_value = Column(JSON, nullable=False, default=dict)
    confidence = Column(Float, default=1.0)
    model_version = Column(String(128), nullable=False, default="mock-v0")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    frame = relationship("Frame", back_populates="annotations")


class ModelRegistryRecord(Base):
    __tablename__ = "model_registry"

    id = Column(String(36), primary_key=True, default=new_id)
    name = Column(String(255), nullable=False)
    task = Column(String(128), nullable=False)
    provider = Column(String(128), nullable=False)
    checkpoint_uri = Column(Text)
    config = Column(JSON, nullable=False, default=dict)
    status = Column(String(32), nullable=False, default="MOCK")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("name", "task", name="uq_model_name_task"),)


class IndexBuild(Base):
    __tablename__ = "index_builds"

    id = Column(String(36), primary_key=True, default=new_id)
    dataset_id = Column(String(36), ForeignKey("datasets.id"), nullable=False)
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
    dataset_id = Column(String(36), ForeignKey("datasets.id"), nullable=False)
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
    video_id = Column(String(36), ForeignKey("videos.id"), nullable=False)
    frame_id = Column(String(36), ForeignKey("frames.id"))
    event_id = Column(String(36), ForeignKey("events.id"))
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
    dataset_id = Column(String(36), ForeignKey("datasets.id"), nullable=False)
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

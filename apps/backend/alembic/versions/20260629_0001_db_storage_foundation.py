"""M1 db-storage foundation with demo-aligned core schema.

Revision ID: 20260629_0001
Revises:
Create Date: 2026-06-29 19:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260629_0001"
down_revision = None
branch_labels = None
depends_on = None


def _reset_legacy_schema_if_needed() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    dataset_cols = {col["name"] for col in inspector.get_columns("datasets")} if "datasets" in existing else set()
    needs_reset = "datasets" in existing and "dataset_id" not in dataset_cols

    if not needs_reset:
        return

    drop_order = [
        "retrieval_results",
        "query_runs",
        "submission_items",
        "submissions",
        "event_keyframes",
        "frame_annotations",
        "keyframes",
        "shots",
        "events",
        "index_builds",
        "jobs",
        "model_registry",
        "videos",
        "datasets",
    ]
    for table_name in drop_order:
        if table_name in existing:
            op.drop_table(table_name)


def upgrade() -> None:
    _reset_legacy_schema_if_needed()

    op.create_table(
        "datasets",
        sa.Column("dataset_id", sa.String(length=36), primary_key=True),
        sa.Column("dataset_code", sa.String(length=128), nullable=False, unique=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("root_uri", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="READY"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("name", "version", name="uq_dataset_name_version"),
    )

    op.create_table(
        "videos",
        sa.Column("video_id", sa.String(length=64), primary_key=True),
        sa.Column("dataset_id", sa.String(length=36), sa.ForeignKey("datasets.dataset_id"), nullable=False),
        sa.Column("video_code", sa.String(length=64), nullable=False),
        sa.Column("video_name", sa.String(length=255)),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("source_video_path", sa.Text()),
        sa.Column("fps", sa.Float()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("num_keyframes", sa.Integer()),
        sa.Column("embedding_shape", sa.String(length=64)),
        sa.Column("source_feature_path", sa.Text()),
        sa.Column("source_map_path", sa.Text()),
        sa.Column("extra_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("dataset_id", "video_code", name="uq_video_dataset_code"),
    )
    op.create_index("idx_videos_dataset", "videos", ["dataset_id"])

    op.create_table(
        "shots",
        sa.Column("shot_id", sa.String(length=100), primary_key=True),
        sa.Column("video_id", sa.String(length=64), sa.ForeignKey("videos.video_id"), nullable=False),
        sa.Column("shot_index", sa.Integer(), nullable=False),
        sa.Column("start_frame", sa.Integer(), nullable=False),
        sa.Column("end_frame", sa.Integer(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("boundary_threshold", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("video_id", "shot_index", name="uq_shot_video_index"),
        sa.CheckConstraint("shot_index >= 0", name="ck_shot_index_non_negative"),
        sa.CheckConstraint("start_frame >= 0", name="ck_shot_start_frame_non_negative"),
        sa.CheckConstraint("end_frame >= start_frame", name="ck_shot_frame_order"),
        sa.CheckConstraint("start_seconds >= 0", name="ck_shot_start_seconds_non_negative"),
        sa.CheckConstraint("end_seconds >= start_seconds", name="ck_shot_seconds_order"),
    )
    op.create_index("idx_shots_video_time", "shots", ["video_id", "start_seconds", "end_seconds"])

    op.create_table(
        "keyframes",
        sa.Column("keyframe_id", sa.String(length=100), primary_key=True),
        sa.Column("video_id", sa.String(length=64), sa.ForeignKey("videos.video_id"), nullable=False),
        sa.Column("shot_id", sa.String(length=100), sa.ForeignKey("shots.shot_id")),
        sa.Column("frame_idx", sa.Integer(), nullable=False),
        sa.Column("frame_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("timestamp_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("frame_type", sa.String(length=10)),
        sa.Column("map_n", sa.Integer()),
        sa.Column("embedding_index_0", sa.Integer()),
        sa.Column("image_rel_path", sa.Text()),
        sa.Column("image_storage_key", sa.Text()),
        sa.Column("image_url", sa.Text()),
        sa.Column("image_uri", sa.Text(), nullable=False),
        sa.Column("thumbnail_uri", sa.Text()),
        sa.Column("dedup_group_id", sa.String(length=36)),
        sa.Column("quality_score", sa.Float(), server_default="1"),
        sa.Column("is_media_present", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("video_id", "frame_idx", name="uq_frame_video_idx"),
    )
    op.create_index("idx_keyframes_video_time", "keyframes", ["video_id", "frame_seconds"])
    op.create_index("idx_keyframes_shot", "keyframes", ["shot_id", "frame_idx"])

    op.create_table(
        "events",
        sa.Column("event_id", sa.String(length=100), primary_key=True),
        sa.Column("video_id", sa.String(length=64), sa.ForeignKey("videos.video_id"), nullable=False),
        sa.Column("embedding_index_0", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("start_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("end_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("start_frame", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("end_frame", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("representative_keyframe_id", sa.String(length=100), sa.ForeignKey("keyframes.keyframe_id")),
        sa.Column("n_shots", sa.Integer()),
        sa.Column("n_keyframes", sa.Integer()),
        sa.Column("shot_ids_raw", sa.Text()),
        sa.Column("keyframe_embedding_indices_raw", sa.Text()),
        sa.Column("start_frame_idx", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("end_frame_idx", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("representative_frame_id", sa.String(length=100), sa.ForeignKey("keyframes.keyframe_id")),
        sa.Column("title", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("event_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("segmentation_version", sa.String(length=64), nullable=False, server_default="mock-v0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("video_id", "embedding_index_0", name="uq_event_video_embedding_idx"),
        sa.CheckConstraint("start_seconds >= 0", name="ck_event_start_seconds_non_negative"),
        sa.CheckConstraint("end_seconds >= start_seconds", name="ck_event_seconds_order"),
    )
    op.create_index("idx_events_video_time", "events", ["video_id", "start_seconds", "end_seconds"])

    op.create_table(
        "event_keyframes",
        sa.Column("event_id", sa.String(length=100), sa.ForeignKey("events.event_id"), primary_key=True),
        sa.Column("seq_no", sa.Integer(), primary_key=True),
        sa.Column("keyframe_id", sa.String(length=100), sa.ForeignKey("keyframes.keyframe_id"), nullable=False),
        sa.Column("keyframe_embedding_index_0", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("event_id", "keyframe_id", name="uq_event_keyframe_pair"),
        sa.CheckConstraint("seq_no >= 0", name="ck_event_keyframe_seq_non_negative"),
    )
    op.create_index("idx_event_keyframes_keyframe", "event_keyframes", ["keyframe_id"])

    op.create_table(
        "frame_annotations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("frame_id", sa.String(length=100), sa.ForeignKey("keyframes.keyframe_id"), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("text_value", sa.Text()),
        sa.Column("json_value", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("confidence", sa.Float(), server_default="1"),
        sa.Column("model_version", sa.String(length=128), nullable=False, server_default="mock-v0"),
        sa.Column("caption", sa.Text()),
        sa.Column("ocr_texts", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("detected_objects", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("object_counts", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("detections", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("annotation_version", sa.String(length=128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    op.create_table(
        "model_registry",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("task", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("checkpoint_uri", sa.Text()),
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="MOCK"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("name", "task", name="uq_model_name_task"),
    )

    op.create_table(
        "index_builds",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("dataset_id", sa.String(length=36), sa.ForeignKey("datasets.dataset_id"), nullable=False),
        sa.Column("index_type", sa.String(length=32), nullable=False),
        sa.Column("collection_name", sa.String(length=255)),
        sa.Column("model_name", sa.String(length=255)),
        sa.Column("model_version", sa.String(length=128)),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="READY"),
        sa.Column("stats", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "query_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("dataset_id", sa.String(length=36), sa.ForeignKey("datasets.dataset_id"), nullable=False),
        sa.Column("query_name", sa.String(length=255)),
        sa.Column("query_type", sa.String(length=32), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("normalized_query", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("options", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="RUNNING"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_table(
        "retrieval_results",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("query_run_id", sa.String(length=36), sa.ForeignKey("query_runs.id"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.String(length=64), sa.ForeignKey("videos.video_id"), nullable=False),
        sa.Column("frame_id", sa.String(length=100), sa.ForeignKey("keyframes.keyframe_id")),
        sa.Column("event_id", sa.String(length=100), sa.ForeignKey("events.event_id")),
        sa.Column("answer", sa.Text()),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("score_breakdown", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("sequence_frames", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("query_run_id", "rank", name="uq_query_run_rank"),
    )

    op.create_table(
        "submissions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("dataset_id", sa.String(length=36), sa.ForeignKey("datasets.dataset_id"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("zip_uri", sa.Text()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DRAFT"),
        sa.Column("validation_report", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_table(
        "submission_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("submission_id", sa.String(length=36), sa.ForeignKey("submissions.id"), nullable=False),
        sa.Column("query_name", sa.String(length=255), nullable=False),
        sa.Column("query_type", sa.String(length=32), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("video_code", sa.String(length=64), nullable=False),
        sa.Column("frame_indices", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("answer", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("submission_id", "query_name", "rank", name="uq_submission_query_rank"),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text()),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("submission_items")
    op.drop_table("submissions")
    op.drop_table("retrieval_results")
    op.drop_table("query_runs")
    op.drop_table("index_builds")
    op.drop_table("model_registry")
    op.drop_table("frame_annotations")
    op.drop_index("idx_event_keyframes_keyframe", table_name="event_keyframes")
    op.drop_table("event_keyframes")
    op.drop_index("idx_events_video_time", table_name="events")
    op.drop_table("events")
    op.drop_index("idx_keyframes_shot", table_name="keyframes")
    op.drop_index("idx_keyframes_video_time", table_name="keyframes")
    op.drop_table("keyframes")
    op.drop_index("idx_shots_video_time", table_name="shots")
    op.drop_table("shots")
    op.drop_index("idx_videos_dataset", table_name="videos")
    op.drop_table("videos")
    op.drop_table("datasets")

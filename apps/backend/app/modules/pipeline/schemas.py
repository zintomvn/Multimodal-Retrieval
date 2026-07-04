from __future__ import annotations

from pydantic import BaseModel, Field


class PipelineJobRequest(BaseModel):
    source_id: str = Field(description="Resolves source config from data_ingestion_sources.yaml")
    source_dataset_id: str | None = Field(default=None, description="Override dataset_id from source config")
    dataset_code: str | None = Field(default=None, description="Override dataset_code (default: source_id)")
    dataset_name: str | None = Field(default=None, description="Override display_name")
    dataset_version: str | None = Field(default=None, description="Override source_version")
    batch_ids: list[str] | None = Field(default=None, description="Filter to specific batches (default: all expected_batches)")
    video_keys: list[str] | None = Field(default=None, description="Explicit GCS keys (bypasses listing)")
    force_reprocess: bool = Field(default=False, description="Reprocess even if video already exists (internal escape hatch)")


class PipelineJobResponse(BaseModel):
    job_id: str
    status: str
    message: str


class VideoPipelineResult(BaseModel):
    video_id: str
    status: str
    num_keyframes: int = 0
    num_events: int = 0
    num_annotations: int = 0
    error: str | None = None

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class IngestJobRequest(BaseModel):
    mode: Literal["demo"] = Field(default="demo", description="demo")
    dataset_code: str = Field(default="l30-demo")
    dataset_name: str = Field(default="aic-2026-l30-demo")
    dataset_version: str = Field(default="v1")
    dataset_root: str | None = Field(default=None, description="Path to demo/ directory.")
    targets: list[str] = Field(default_factory=lambda: ["pg", "media", "milvus", "es"])
    dry_run: bool = False


class IngestJobResponse(BaseModel):
    job_id: str
    status: str
    message: str
    report: dict

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SubmissionCreate(BaseModel):
    dataset_id: str
    name: str


class SubmissionRow(BaseModel):
    query_name: str
    query_type: Literal["KIS", "QA", "TRAKE"]
    rank: int
    video_code: str
    frame_indices: list[int] = Field(default_factory=list)
    answer: str | None = None


class SubmissionItemsRequest(BaseModel):
    rows: list[SubmissionRow]


class SubmissionExportResponse(BaseModel):
    submission_id: str
    status: str
    csv_uri: str | None = None
    zip_uri: str | None
    validation_report: dict

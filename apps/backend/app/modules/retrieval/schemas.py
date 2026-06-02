from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


QueryType = Literal["KIS", "QA", "TRAKE", "IMAGE", "FREEFORM"]


class SearchOptions(BaseModel):
    use_query_expansion: bool = True
    use_metadata: bool = True
    delta_t_max_ms: int = 180000
    min_match: int | None = None
    temporal_events: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    dataset_id: str | None = None
    query_name: str | None = None
    query_type: QueryType = "KIS"
    query_text: str
    top_k: int = Field(default=100, ge=1, le=1000)
    profile: str = "competition_default"
    options: SearchOptions = Field(default_factory=SearchOptions)


class ResultItem(BaseModel):
    id: str
    rank: int
    video_id: str
    video_code: str
    frame_id: str | None = None
    frame_idx: int | None = None
    timestamp_ms: int | None = None
    answer: str | None = None
    score: float
    score_breakdown: dict
    sequence_frames: list[dict] = Field(default_factory=list)
    thumbnail_url: str | None = None


class SearchResponse(BaseModel):
    query_run_id: str
    query_type: QueryType
    query_name: str | None = None
    normalized_query: dict
    results: list[ResultItem]


class SelectResultsRequest(BaseModel):
    result_ids: list[str]
    selected: bool = True

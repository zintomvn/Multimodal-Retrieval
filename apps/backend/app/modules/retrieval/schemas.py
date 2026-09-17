from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


QueryType = Literal["KIS", "QA", "TRAKE", "IMAGE", "FREEFORM"]
VisualSearchMode = Literal["profile", "openclip", "siglip2", "both"]
AgentModel = Literal["gpt-4o", "gpt-5-nano", "gpt-5.6-luna"]


class SearchOptions(BaseModel):
    deadline_ms: int = Field(default=30000, ge=100, le=120000)
    source_mode: Literal["auto", "ocr", "asr", "scene"] = "auto"
    use_query_expansion: bool = True
    use_agent_query_planning: bool = True
    agent_model: AgentModel | None = None
    use_metadata: bool = True
    use_reranker: bool = True
    strict_hybrid: bool = False
    visual_search_mode: VisualSearchMode = "profile"
    delta_t_max_ms: int = 180000
    min_match: int | None = None
    temporal_events: list[str] = Field(default_factory=list)
    temporal_mode: bool = False
    temporal_strategy: Literal["vortex_k_context", "aithena_weighted_ats", "dev_first_search"] = "vortex_k_context"
    temporal_anchor_index: int | None = Field(default=None, ge=1, le=8)
    video_codes: list[str] = Field(default_factory=list)
    time_range_start_seconds: float | None = None
    time_range_end_seconds: float | None = None
    objects: list[str] = Field(default_factory=list)
    scene: str | None = None
    debug_filters: bool = False


class SearchRequest(BaseModel):
    dataset_id: str | None = None
    query_name: str | None = None
    query_type: QueryType = "KIS"
    query_text: str = Field(min_length=1)
    top_k: int = Field(default=50, ge=1, le=1000)
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
    image_url: str | None = None
    image_uri: str | None = None
    image_storage_key: str | None = None
    video_url: str | None = None
    video_uri: str | None = None


class SearchResponse(BaseModel):
    query_run_id: str
    query_type: QueryType
    query_name: str | None = None
    normalized_query: dict
    results: list[ResultItem]


class QueryPlanResponse(BaseModel):
    normalized_query: dict


class SelectResultsRequest(BaseModel):
    result_ids: list[str]
    selected: bool = True

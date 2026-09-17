export type QueryType = "KIS" | "QA" | "TRAKE";
export type SubmissionFormat = "csv" | "zip";
// Additive HTTP diagnostics; durations are milliseconds and stages may overlap.
export interface ApiDiagnostics {
  requestId: string | null;
  serverTiming: string | null;
  clientDurationMs: number;
}
export type VisualSearchMode = "openclip" | "siglip2" | "both";

export interface Dataset {
  id: string;
  name: string;
  version: string;
  root_uri: string;
  status: string;
  video_count: number;
}

export interface SearchResult {
  source_result_id?: string;
  id: string;
  rank: number;
  video_id: string;
  video_code: string;
  frame_id: string | null;
  frame_idx: number | null;
  timestamp_ms: number | null;
  answer: string | null;
  score: number;
  score_breakdown: Record<string, unknown>;
  sequence_frames: Array<{
    frame_id: string;
    frame_idx: number;
    video_code: string;
    timestamp_ms?: number | null;
    score: number;
    visual_score?: number;
    text_score?: number;
    rrf_score?: number;
    order_index?: number;
    event_index?: number;
    event_query?: string;
    thumbnail_url?: string | null;
    image_url?: string | null;
    image_uri?: string | null;
    image_storage_key?: string | null;
  }>;
  thumbnail_url: string | null;
  image_url: string | null;
  image_uri: string | null;
  image_storage_key: string | null;
  video_url: string | null;
  video_uri: string | null;
}

export interface SearchResponse {
  query_run_id: string;
  query_type: QueryType;
  query_name: string | null;
  normalized_query: {
    language?: string;
    multi_views?: string[];
    variants?: string[];
    semantic_variants?: string[];
    text_variants?: string[];
    temporal_events?: string[];
    text_temporal_events?: string[];
    temporal_event_count?: number;
    temporal_event_source?: string;
    temporal_mode?: boolean;
    temporal_strategy?: "vortex_k_context" | "aithena_weighted_ats" | "dev_first_search" | null;
    temporal_anchor_index?: number;
    temporal_intent?: "single_event" | "ordered_sequence" | "narrative_sequence" | string;
    target_scope?: "frame" | "video_sequence" | string;
    anchor_policy?: "explicit" | "inferred" | "none" | string;
    temporal_edges?: Array<{
      from_event?: number;
      to_event?: number;
      relation?: "after" | "before" | "unknown" | string;
      gap_class?: "short" | "medium" | "loose" | "unknown" | string;
    }>;
    retrieval_weights?: { visual?: number; text?: number };
    retrieval_weight_source?: string;
    text_source_weights?: { asr?: number; caption?: number; ocr?: number };
    text_source_weight_source?: string;
    visual_search?: {
      mode?: VisualSearchMode | "profile";
      fusion?: string;
      formula?: string;
      rrf_k?: number | null;
      models?: Array<{
        model_key?: string | null;
        collection?: string;
        weight?: number;
        family?: "openclip" | "siglip2" | string;
      }>;
    };
    temporal_event_plans?: Array<{
      event_index?: number;
      query?: string;
      text_query?: string;
      multi_views?: string[];
      semantic_views?: string[];
      text_views?: string[];
      importance?: number;
      diagnostic_prior?: number;
      retrieval_weights?: { visual?: number; text?: number };
      retrieval_weight_source?: string;
      text_source_weights?: { asr?: number; caption?: number; ocr?: number };
      text_source_weight_source?: string;
    }>;
    raw_temporal_events?: string[];
    profile?: string;
    latency_ms?: number;
    retrieval_mode?: "indexed" | "degraded";
    source_status?: Record<string, "ok" | "disabled" | "degraded" | "unavailable">;
    agent_query_plan?: {
      source?: string;
      language?: string;
      intent?: string;
      summary?: string;
      decomposition?: {
        search_factors?: Record<string, unknown>;
        retrieval_strategy?: Record<string, unknown>;
        temporal_event_plans?: Array<Record<string, unknown>>;
        raw_temporal_events?: unknown[];
      };
      temporal_events?: string[];
      temporal_anchor_index?: number | null;
      multi_views?: string[];
      variants?: string[];
      retrieval_weights?: { visual?: number; text?: number };
      retrieval_weight_source?: string;
      text_source_weights?: { asr?: number; caption?: number; ocr?: number };
      text_source_weight_source?: string;
      temporal_event_plans?: Array<Record<string, unknown>>;
      agent_metadata?: {
        enabled?: boolean;
        active_profile?: string;
        provider?: string;
        model?: string;
        api_key_env?: string;
        api_key_configured?: boolean;
        langsmith_api_key_env?: string;
        langsmith_api_key_configured?: boolean;
        langsmith_trace_enabled?: boolean;
        config_path?: string | null;
      };
      error?: string;
    };
  };
  results: SearchResult[];
}

export interface QueryPlanResponse {
  normalized_query: SearchResponse["normalized_query"];
}

export interface ContextFrame {
  id: string;
  frame_idx: number;
  timestamp_ms: number;
  thumbnail_url: string;
  image_url?: string | null;
  image_uri?: string | null;
  image_storage_key?: string | null;
  text: string;
}

export interface FrameContext {
  target_frame_id: string;
  video_code: string;
  frames: ContextFrame[];
}

export interface VideoEvidenceItem {
  text: string;
  start_seconds: number | null;
  end_seconds: number | null;
  frame_id: string | null;
  segment_id: string | null;
  model_version: string | null;
  matches_selected_frame: boolean;
}

export interface VideoEvidence {
  video_id: string;
  video_code: string;
  evidence: {
    asr: VideoEvidenceItem[];
    ocr: VideoEvidenceItem[];
    captions: VideoEvidenceItem[];
  };
}

export interface VideoFrameSeekResponse {
  video_id: string;
  video_code: string;
  frame: ContextFrame;
  selection: {
    frame_idx: number;
    timestamp_ms: number;
    fps: number | null;
  };
}

export interface VideoPreviewUrl {
  video_id: string;
  url: string;
  direct: boolean;
  provider: string;
}

export interface MediaFrame {
  id: string;
  keyframe_id: string;
  video_id: string;
  video_code: string;
  frame_idx: number;
  timestamp_ms: number;
  frame_type: string | null;
  thumbnail_url: string;
  image_url: string | null;
  image_uri: string | null;
  image_storage_key: string | null;
  is_media_present: boolean;
}

export interface FrameListResponse {
  total: number;
  limit: number;
  offset: number;
  frames: MediaFrame[];
}

export interface SubmissionRow {
  query_name: string;
  query_type: QueryType;
  rank: number;
  video_code: string;
  frame_indices: number[];
  answer?: string | null;
}

// ─── Ingest job ───

export type IngestJobStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";

export interface IngestJobStartRequest {
  mode: "demo";
  dataset_code?: string;
  dataset_name?: string;
  dataset_version?: string;
  dataset_root?: string;
  targets?: string[];
  dry_run?: boolean;
}

export interface IngestJobStartResponse {
  job_id: string;
  status: string;
  message: string;
}

export interface IngestJobPollResponse {
  id: string;
  status: IngestJobStatus;
  progress: number;
  message: string | null;
}

// ─── Video pipeline job ───

export interface PipelineJobStartRequest {
  source_id: string;
  source_dataset_id?: string;
  dataset_code?: string;
  dataset_name?: string;
  dataset_version?: string;
  batch_ids?: string[];
  video_keys?: string[];
  force_reprocess?: boolean;
}

export interface PipelineJobStartResponse {
  job_id: string;
  status: string;
  message: string;
}

export interface PipelineJobPollResponse {
  id: string;
  status: IngestJobStatus;
  progress: number;
  message: string | null;
}

// ─── Cloud upload jobs ───

export interface GCSUploadJobRequest {
  source_path: string;
  source_type: "folder" | "zip";
  dataset_id?: string;
}

export interface MilvusUploadJobRequest {
  features_file: string;
  collection?: string;
  dataset_id?: string;
}

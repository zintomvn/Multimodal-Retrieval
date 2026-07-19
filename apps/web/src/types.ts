export type QueryType = "KIS" | "QA" | "TRAKE";

export interface Dataset {
  id: string;
  name: string;
  version: string;
  root_uri: string;
  status: string;
  video_count: number;
}

export interface SearchResult {
  id: string;
  rank: number;
  video_id: string;
  video_code: string;
  frame_id: string | null;
  frame_idx: number | null;
  timestamp_ms: number | null;
  answer: string | null;
  score: number;
  score_breakdown: Record<string, number | string>;
  sequence_frames: Array<{
    frame_id: string;
    frame_idx: number;
    video_code: string;
    score: number;
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
    variants?: string[];
    temporal_events?: string[];
    profile?: string;
  };
  results: SearchResult[];
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
  mode: "demo" | "mock";
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

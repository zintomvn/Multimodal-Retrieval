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
  text: string;
}

export interface FrameContext {
  target_frame_id: string;
  video_code: string;
  frames: ContextFrame[];
}

export interface SubmissionRow {
  query_name: string;
  query_type: QueryType;
  rank: number;
  video_code: string;
  frame_indices: number[];
  answer?: string | null;
}

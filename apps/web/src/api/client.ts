import { requireValidExport } from "./submissionValidation";
import { consumeCached, mediaCache } from "./mediaCache";
import type {
  Dataset,
  FrameListResponse,
  FrameContext,
  GCSUploadJobRequest,
  IngestJobPollResponse,
  IngestJobStartRequest,
  IngestJobStartResponse,
  MilvusUploadJobRequest,
  PipelineJobPollResponse,
  PipelineJobStartRequest,
  PipelineJobStartResponse,
  QueryPlanResponse,
  QueryType,
  SearchResponse,
  SubmissionRow,
  VisualSearchMode,
  SubmissionFormat,
  VideoFrameSeekResponse,
  VideoEvidence,
  VideoPreviewUrl,
} from "../types";

// Leave the base empty when the frontend is publicly proxied through Vite/ngrok.
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
const GCS_BUCKET = import.meta.env.VITE_GCS_BUCKET ?? "";
const GCS_PUBLIC_BASE_URL = (
  import.meta.env.VITE_GCS_PUBLIC_BASE_URL ?? ""
).replace(/\/$/, "");

// Convert a GCS path (gs://bucket/key) to a public URL
function gcsMediaUrl(path: string): string | null {
  const raw = path.trim();
  if (!raw) return null;

  let bucket = GCS_BUCKET;
  let key = raw.replace(/^\/+/, "");
  if (raw.startsWith("gs://")) {
    const [, tail = ""] = raw.split("gs://");
    const slashIndex = tail.indexOf("/");
    if (slashIndex < 0) return null;
    bucket = tail.slice(0, slashIndex);
    key = tail.slice(slashIndex + 1);
  }

  if (!bucket || !key) return null;
  const encodedKey = key.split("/").map(encodeURIComponent).join("/");
  if (GCS_PUBLIC_BASE_URL) return `${GCS_PUBLIC_BASE_URL}/${encodedKey}`;
  return `https://storage.googleapis.com/${bucket}/${encodedKey}`;
}

// JSON request
async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  // Prevent ngrok's browser warning page from being returned to API fetches.
  headers.set("ngrok-skip-browser-warning", "true");
  const timeout = AbortSignal.timeout(120_000);
  const signal = init?.signal ? AbortSignal.any([init.signal, timeout]) : timeout;
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    signal,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function listDatasets(): Promise<Dataset[]> {
  const payload = await requestJson<{ datasets: Dataset[] }>("/api/datasets");
  return payload.datasets;
}

export interface RetrievalSearchInput {
  datasetId: string;
  queryType: QueryType;
  queryName: string;
  queryText: string;
  topK: number;
  useExpansion: boolean;
  useAgentPlanning: boolean;
  agentModel?: "gpt-4o" | "gpt-5-nano" | "gpt-5.6-luna";
  useMetadata: boolean;
  temporalMode: boolean;
  temporalStrategy:
    | "vortex_k_context"
    | "aithena_weighted_ats"
    | "dev_first_search";
  visualSearchMode: VisualSearchMode;
  sourceMode?: "auto" | "ocr" | "asr" | "scene";
  temporalEvents?: string[];
  videoFilter?:string; timeStart?:string; timeEnd?:string;
}

function retrievalPayload(input: RetrievalSearchInput): Record<string, unknown> {
  return {
    dataset_id: input.datasetId,
    query_name: input.queryName,
    query_type: input.queryType,
    query_text: input.queryText,
    top_k: input.topK,
    profile: "competition_default",
    options: {
      use_query_expansion: input.useExpansion,
      use_agent_query_planning: input.useAgentPlanning,
      agent_model: input.agentModel,
      use_metadata: input.useMetadata,
      use_reranker: true,
      strict_hybrid: false,
      visual_search_mode: input.visualSearchMode,
      source_mode: input.sourceMode ?? "auto",
      // The original UI displays answers inline and has no deferred-answer action.
      defer_qa: false,
      video_codes: input.videoFilter?.split(',').map(v=>v.trim()).filter(Boolean) ?? [],
      time_range_start_seconds: input.timeStart?.trim() ? Number(input.timeStart) : null,
      time_range_end_seconds: input.timeEnd?.trim() ? Number(input.timeEnd) : null,
      temporal_events: input.temporalEvents?.filter(value => value.trim()) ?? [],
      delta_t_max_ms: 180000,
      temporal_mode: input.temporalMode,
      temporal_strategy: input.temporalStrategy,
    },
  };
}

export async function planSearch(input: RetrievalSearchInput, signal?: AbortSignal): Promise<QueryPlanResponse> {
  return requestJson<QueryPlanResponse>("/api/retrieval/plan", {
    method: "POST",
    signal,
    body: JSON.stringify(retrievalPayload(input)),
  });
}

export async function runSearch(input: RetrievalSearchInput, signal?: AbortSignal): Promise<SearchResponse> {
  const path =
    input.queryType === "QA"
      ? "/api/retrieval/qa"
      : input.queryType === "TRAKE"
        ? "/api/retrieval/trake"
        : "/api/retrieval/search";
  return requestJson<SearchResponse>(path, {
    signal,
    method: "POST",
    body: JSON.stringify(retrievalPayload(input)),
  });
}

export async function getFrameContext(frameId: string, signal?: AbortSignal): Promise<FrameContext> {
  // Aborting one consumer must not abort another consumer's cached promise.
  signal?.throwIfAborted();
  return consumeCached(mediaCache.get(`context:${frameId}`, () => requestJson<FrameContext>(`/api/media/frames/${frameId}/context`)), signal);
}

export async function getVideoPreviewUrl(
  videoId: string,
): Promise<VideoPreviewUrl> {
  return requestJson<VideoPreviewUrl>(
    `/api/media/videos/${encodeURIComponent(videoId)}/preview-url`,
  );
}

export async function getVideoEvidence(
  videoId: string,
  focus?: {
    seconds?: number;
    frameId?: string | null;
    resultId?: string;
  },
): Promise<VideoEvidence> {
  const params = new URLSearchParams();
  if (focus?.seconds !== undefined && Number.isFinite(focus.seconds)) params.set("seconds", String(focus.seconds));
  if (focus?.frameId) params.set("frame_id", focus.frameId);
  if (focus?.resultId) params.set("result_id", focus.resultId);
  const suffix = params.size > 0 ? `?${params.toString()}` : "";
  return mediaCache.get(`evidence:${videoId}${suffix}`, () => requestJson<VideoEvidence>(
    `/api/media/videos/${encodeURIComponent(videoId)}/evidence${suffix}`,
  ));
}

export async function seekVideoFrame(input: {
  videoId: string;
  seconds?: number;
  frameIdx?: number;
  direction?: "nearest" | "next" | "previous";
}): Promise<VideoFrameSeekResponse> {
  const params = new URLSearchParams();
  if (input.seconds !== undefined) params.set("seconds", String(input.seconds));
  if (input.frameIdx !== undefined) params.set("frame_idx", String(input.frameIdx));
  params.set("direction", input.direction ?? "nearest");
  return requestJson<VideoFrameSeekResponse>(
    `/api/media/videos/${encodeURIComponent(input.videoId)}/frames/seek?${params.toString()}`,
  );
}

export async function listFrames(input: {
  datasetId?: string;
  videoId?: string;
  videoCode?: string;
  frameIdx?: number;
  limit?: number;
  offset?: number;
  presentOnly?: boolean;
}, signal?: AbortSignal): Promise<FrameListResponse> {
  const params = new URLSearchParams();
  if (input.datasetId) params.set("dataset_id", input.datasetId);
  if (input.videoId) params.set("video_id", input.videoId);
  if (input.videoCode) params.set("video_code", input.videoCode);
  if (input.frameIdx !== undefined) params.set("frame_idx", String(input.frameIdx));
  params.set("limit", String(input.limit ?? 60));
  params.set("offset", String(input.offset ?? 0));
  params.set("present_only", String(input.presentOnly ?? true));
  return requestJson<FrameListResponse>(
    `/api/media/frames?${params.toString()}`,
    { signal },
  );
}

export async function createAndExportSubmission(
  datasetId: string,
  name: string,
  rows: SubmissionRow[],
  format: SubmissionFormat = "csv",
) {
  const submission = await requestJson<{ id: string; status: string }>(
    "/api/submissions",
    {
      method: "POST",
      body: JSON.stringify({ dataset_id: datasetId, name }),
    },
  );
  await requestJson(`/api/submissions/${submission.id}/items`, {
    method: "POST",
    body: JSON.stringify({ rows }),
  });
  const exported = await requestJson<{
    submission_id: string;
    status: string;
    csv_uri: string | null;
    zip_uri: string | null;
    validation_report: { valid: boolean; errors: string[]; warnings: string[] };
  }>(`/api/submissions/${submission.id}/export${format === "zip" ? "?format=zip" : ""}`, {
    method: "POST",
  });
  requireValidExport(exported.validation_report, format === "zip" ? exported.zip_uri : exported.csv_uri);
  return {
    ...exported,
    downloadUrl: `${API_BASE}/api/submissions/${submission.id}/download${format === "zip" ? "?format=zip" : ""}`,
  };
}

export function mediaUrl(path: string | null): string | null {
  if (!path) return null;
  if (
    path.startsWith("http://") ||
    path.startsWith("https://") ||
    path.startsWith("blob:") ||
    path.startsWith("data:")
  ) {
    return path;
  }
  if (path.startsWith("/")) return `${API_BASE}${path}`;
  if (path.startsWith("gs://")) return gcsMediaUrl(path);
  return gcsMediaUrl(path) ?? path;
}

export function firstMediaUrl(
  ...paths: Array<string | null | undefined>
): string | null {
  for (const path of paths) {
    const resolved = mediaUrl(path ?? null);
    if (resolved) return resolved;
  }
  return null;
}

export async function startIngestJob(
  input: IngestJobStartRequest,
): Promise<IngestJobStartResponse> {
  return requestJson<IngestJobStartResponse>("/api/ingest/jobs", {
    method: "POST",
    body: JSON.stringify({
      mode: input.mode,
      dataset_code: input.dataset_code ?? undefined,
      dataset_name: input.dataset_name ?? undefined,
      dataset_version: input.dataset_version ?? undefined,
      dataset_root: input.dataset_root ?? undefined,
      targets: input.targets ?? undefined,
      dry_run: input.dry_run ?? false,
    }),
  });
}

export async function getIngestJob(
  jobId: string,
): Promise<IngestJobPollResponse> {
  return requestJson<IngestJobPollResponse>(`/api/jobs/${jobId}`);
}

export async function startPipelineJob(
  input: PipelineJobStartRequest,
): Promise<PipelineJobStartResponse> {
  return requestJson<PipelineJobStartResponse>("/api/pipeline/jobs", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function getPipelineJob(
  jobId: string,
): Promise<PipelineJobPollResponse> {
  return requestJson<PipelineJobPollResponse>(`/api/jobs/${jobId}`);
}

export async function startGCSUpload(
  input: GCSUploadJobRequest,
): Promise<IngestJobStartResponse> {
  return requestJson<IngestJobStartResponse>("/api/ingest/upload/gcs", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function startMilvusUpload(
  input: MilvusUploadJobRequest,
): Promise<IngestJobStartResponse> {
  return requestJson<IngestJobStartResponse>("/api/ingest/upload/milvus", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function uploadFileToGCS(
  file: File,
  datasetId?: string,
): Promise<IngestJobStartResponse> {
  const form = new FormData();
  form.append("file", file);
  if (datasetId) form.append("dataset_id", datasetId);
  const res = await fetch(`${API_BASE}/api/ingest/upload/file/gcs`, {
    method: "POST",
    body: form,
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<IngestJobStartResponse>;
}

export async function uploadFileToMilvus(
  file: File,
  collection?: string,
  datasetId?: string,
): Promise<IngestJobStartResponse> {
  const form = new FormData();
  form.append("file", file);
  if (collection) form.append("collection", collection);
  if (datasetId) form.append("dataset_id", datasetId);
  const res = await fetch(`${API_BASE}/api/ingest/upload/file/milvus`, {
    method: "POST",
    body: form,
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<IngestJobStartResponse>;
}

export function getReadiness(signal?: AbortSignal) {
  return requestJson<{status:string; checks:Record<string,{status:string;reason?:string}>}>('/api/readyz',{signal});
}
export function generateAnswer(resultId:string,signal?:AbortSignal) {
  return requestJson<{answer:string;evidence:unknown[];mode:string}>(`/api/retrieval/results/${encodeURIComponent(resultId)}/answer`,{method:'POST',signal});
}

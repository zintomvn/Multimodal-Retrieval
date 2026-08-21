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
  QueryType,
  SearchResponse,
  SubmissionRow,
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
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
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

export async function runSearch(input: {
  datasetId: string;
  queryType: QueryType;
  queryName: string;
  queryText: string;
  topK: number;
  useExpansion: boolean;
  useAgentPlanning: boolean;
  useMetadata: boolean;
}): Promise<SearchResponse> {
  const path =
    input.queryType === "QA"
      ? "/api/retrieval/qa"
      : input.queryType === "TRAKE"
        ? "/api/retrieval/trake"
        : "/api/retrieval/search";
  return requestJson<SearchResponse>(path, {
    method: "POST",
    body: JSON.stringify({
      dataset_id: input.datasetId,
      query_name: input.queryName,
      query_type: input.queryType,
      query_text: input.queryText,
      top_k: input.topK,
      profile: "competition_default",
      options: {
        use_query_expansion: input.useExpansion,
        use_agent_query_planning: input.useAgentPlanning,
        use_metadata: input.useMetadata,
        use_reranker: false,
        strict_hybrid: false,
        delta_t_max_ms: 180000,
      },
    }),
  });
}

export async function getFrameContext(frameId: string): Promise<FrameContext> {
  return requestJson<FrameContext>(`/api/media/frames/${frameId}/context`);
}

export async function getVideoPreviewUrl(
  videoId: string,
): Promise<VideoPreviewUrl> {
  return requestJson<VideoPreviewUrl>(
    `/api/media/videos/${encodeURIComponent(videoId)}/preview-url`,
  );
}

export async function listFrames(input: {
  datasetId?: string;
  videoId?: string;
  limit?: number;
  offset?: number;
  presentOnly?: boolean;
}): Promise<FrameListResponse> {
  const params = new URLSearchParams();
  if (input.datasetId) params.set("dataset_id", input.datasetId);
  if (input.videoId) params.set("video_id", input.videoId);
  params.set("limit", String(input.limit ?? 60));
  params.set("offset", String(input.offset ?? 0));
  params.set("present_only", String(input.presentOnly ?? true));
  return requestJson<FrameListResponse>(
    `/api/media/frames?${params.toString()}`,
  );
}

export async function createAndExportSubmission(
  datasetId: string,
  name: string,
  rows: SubmissionRow[],
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
  }>(`/api/submissions/${submission.id}/export`, {
    method: "POST",
  });
  return {
    ...exported,
    downloadUrl: `${API_BASE}/api/submissions/${submission.id}/download`,
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

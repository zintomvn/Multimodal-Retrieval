import type {
  Dataset,
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
  SubmissionRow
} from "../types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    ...init
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
  useMetadata: boolean;
}): Promise<SearchResponse> {
  const path = input.queryType === "QA" ? "/api/retrieval/qa" : input.queryType === "TRAKE" ? "/api/retrieval/trake" : "/api/retrieval/search";
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
        use_metadata: input.useMetadata,
        delta_t_max_ms: 180000
      }
    })
  });
}

export async function getFrameContext(frameId: string): Promise<FrameContext> {
  return requestJson<FrameContext>(`/api/media/frames/${frameId}/context`);
}

export async function createAndExportSubmission(datasetId: string, name: string, rows: SubmissionRow[]) {
  const submission = await requestJson<{ id: string; status: string }>("/api/submissions", {
    method: "POST",
    body: JSON.stringify({ dataset_id: datasetId, name })
  });
  await requestJson(`/api/submissions/${submission.id}/items`, {
    method: "POST",
    body: JSON.stringify({ rows })
  });
  const exported = await requestJson<{
    submission_id: string;
    status: string;
    zip_uri: string | null;
    validation_report: { valid: boolean; errors: string[]; warnings: string[] };
  }>(`/api/submissions/${submission.id}/export`, {
    method: "POST"
  });
  return {
    ...exported,
    downloadUrl: `${API_BASE}/api/submissions/${submission.id}/download`
  };
}

export function mediaUrl(path: string | null): string | null {
  if (!path) return null;
  return path.startsWith("http") ? path : `${API_BASE}${path}`;
}

export async function startIngestJob(input: IngestJobStartRequest): Promise<IngestJobStartResponse> {
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
    })
  });
}

export async function getIngestJob(jobId: string): Promise<IngestJobPollResponse> {
  return requestJson<IngestJobPollResponse>(`/api/jobs/${jobId}`);
}

export async function startPipelineJob(input: PipelineJobStartRequest): Promise<PipelineJobStartResponse> {
  return requestJson<PipelineJobStartResponse>("/api/pipeline/jobs", {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export async function getPipelineJob(jobId: string): Promise<PipelineJobPollResponse> {
  return requestJson<PipelineJobPollResponse>(`/api/jobs/${jobId}`);
}

export async function startGCSUpload(input: GCSUploadJobRequest): Promise<IngestJobStartResponse> {
  return requestJson<IngestJobStartResponse>("/api/ingest/upload/gcs", {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export async function startMilvusUpload(input: MilvusUploadJobRequest): Promise<IngestJobStartResponse> {
  return requestJson<IngestJobStartResponse>("/api/ingest/upload/milvus", {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export async function uploadFileToGCS(file: File, datasetId?: string): Promise<IngestJobStartResponse> {
  const form = new FormData();
  form.append("file", file);
  if (datasetId) form.append("dataset_id", datasetId);
  const res = await fetch(`${API_BASE}/api/ingest/upload/file/gcs`, { method: "POST", body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<IngestJobStartResponse>;
}

export async function uploadFileToMilvus(file: File, collection?: string, datasetId?: string): Promise<IngestJobStartResponse> {
  const form = new FormData();
  form.append("file", file);
  if (collection) form.append("collection", collection);
  if (datasetId) form.append("dataset_id", datasetId);
  const res = await fetch(`${API_BASE}/api/ingest/upload/file/milvus`, { method: "POST", body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<IngestJobStartResponse>;
}

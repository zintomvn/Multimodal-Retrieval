import type { Dataset, FrameContext, QueryType, SearchResponse, SubmissionRow } from "../types";

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

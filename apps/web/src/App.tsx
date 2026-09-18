import { LatestRequest } from "./api/latestRequest";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { readWorkspace, saveWorkspace, newQueryName, type SearchDraft } from "./workspace";
import { useStableEvent } from "./useStableEvent";
import { useDialogFocus } from "./useDialogFocus";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ImageOff,
  Loader2,
  LocateFixed,
  PanelLeft,
  PanelRight,
  Paperclip,
  Plus,
  Search,
  Send,
  SlidersHorizontal,
  X,
} from "lucide-react";
import {
  createAndExportSubmission,
  firstMediaUrl,
  getFrameContext,
  getVideoEvidence,
  getVideoPreviewUrl,
  listDatasets,
  listFrames,
  mediaUrl,
  planSearch,
  runSearch,
  seekVideoFrame,
} from "./api/client";
import type {
  ContextFrame,
  Dataset,
  FrameContext,
  MediaFrame,
  QueryType,
  SearchResult,
  SearchResponse,
  SubmissionRow,
  VisualSearchMode,
  VideoEvidence,
  VideoEvidenceItem,
} from "./types";

type AppMode = "Search" | "Auto" | "Chat";
type ThemeMode = "light" | "dark" | "system";
type SearchTask = QueryType | "VIDEO";
type ReasoningModel = "gpt-4o" | "gpt-5-nano" | "gpt-5.6-luna";

const reasoningModelLabels: Record<ReasoningModel, string> = {
  "gpt-4o": "GPT-4o",
  "gpt-5-nano": "GPT-5 nano",
  "gpt-5.6-luna": "GPT-5.6 Luna",
};

// Classes for the main app container based on sidebar visibility
interface SearchHistoryItem {
  datasetId?: string;
  draft?: SearchDraft;
  id: string;
  mode: AppMode;
  queryType: SearchTask;
  queryName: string;
  queryText: string;
  resultCount: number;
  createdAt: string;
  results: SearchResult[];
  trace: AgentStep[];
}

interface AgentStep {
  title: string;
  detail: string;
  status: "done" | "running" | "queued" | "warning";
  raw?: unknown;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  files?: string[];
  trace?: AgentStep[];
}

interface VideoPreview {
  title: string;
  subtitle: string;
  url: string;
  posterUrl: string | null;
  result: SearchResult;
  frames: ContextFrame[];
  frameIndex: number;
  loadingFrames: boolean;
  targetSeconds: number;
  selectedFrameIdx: number | null;
  selectedTimestampMs: number;
  trakeEventIndex: number | null;
  evidence: VideoEvidence | null;
  evidenceLoading: boolean;
}

type TrakeSequenceFrame = SearchResult["sequence_frames"][number];

interface TrakeFrameChoice {
  eventIndex: number;
  result: SearchResult;
  frame: TrakeSequenceFrame;
}

// Mock data
const sampleQueries: Record<SearchTask, string> = {
  KIS: "Tìm cảnh chương trình triển lãm có bảng trang trí phong cách cung đình, họa tiết rồng mây và dòng chữ PHU XUAN GIA DINH.",
  QA: "Tên công ty nổi tiếng thế giới nào có logo lấy cảm hứng từ một lâu đài ở Bavaria, Đức?",
  TRAKE:
    "Trong cuộc đua xe đạp, đầu tiên người đội mũ bảo hiểm màu hồng qua vạch đích, sau đó người đội mũ xanh, rồi đến người đội mũ đỏ.",
  VIDEO: "L26_V194",
};

const queryNameByType: Record<SearchTask, string> = {
  KIS: "query-1-kis",
  QA: "query-2-qa",
  TRAKE: "query-3-trake",
  VIDEO: "video-lookup-kis",
};

const queryLabels: Record<SearchTask, { title: string; caption: string }> = {
  KIS: {
    title: "KIS",
    caption: "Find exact scene",
  },
  QA: {
    title: "QA",
    caption: "Answer from video",
  },
  TRAKE: {
    title: "TRAKE",
    caption: "Order key events",
  },
  VIDEO: {
    title: "Video",
    caption: "Find video and frame",
  },
};

const mockDatasets: Dataset[] = [
  {
    id: "mock-aic-dataset",
    name: "AIC mock media",
    version: "local",
    root_uri: "mock://aic",
    status: "READY",
    video_count: 96,
  },
];

// URL helpers
function uniqueMediaUrls(paths: Array<string | null | undefined>): string[] {
  const seen = new Set<string>();
  return paths
    .map((path) => mediaUrl(path ?? null))
    .filter((url): url is string => Boolean(url))
    .filter((url) => {
      if (seen.has(url)) return false;
      seen.add(url);
      return true;
    });
}

function resultImageCandidates(result: SearchResult): string[] {
  return uniqueMediaUrls([
    result.image_url,
    result.image_uri,
    result.image_storage_key,
    result.thumbnail_url,
  ]);
}

function contextImageCandidates(frame: ContextFrame): string[] {
  return uniqueMediaUrls([
    frame.image_url,
    frame.image_uri,
    frame.image_storage_key,
    frame.thumbnail_url,
  ]);
}

function galleryImageCandidates(frame: MediaFrame): string[] {
  return uniqueMediaUrls([
    frame.image_url,
    frame.image_uri,
    frame.image_storage_key,
    frame.thumbnail_url,
  ]);
}

function sequenceImageCandidates(
  frame: SearchResult["sequence_frames"][number],
  fallback: SearchResult,
): string[] {
  return uniqueMediaUrls([
    frame.image_url,
    frame.image_uri,
    frame.image_storage_key,
    frame.thumbnail_url,
    fallback.image_url,
    fallback.image_uri,
    fallback.image_storage_key,
    fallback.thumbnail_url,
  ]);
}

function resultForTrakeFrame(
  result: SearchResult,
  frame: TrakeSequenceFrame,
): SearchResult {
  return {
    ...result,
    id: `${result.id}:event:${frame.event_index ?? frame.order_index ?? 0}:${frame.frame_id}`,
    frame_id: frame.frame_id,
    frame_idx: frame.frame_idx,
    timestamp_ms: frame.timestamp_ms ?? result.timestamp_ms,
    score: frame.score,
    score_breakdown: {
      ...result.score_breakdown,
      visual_score: frame.visual_score ?? result.score_breakdown.visual_score,
      text_score: frame.text_score ?? result.score_breakdown.text_score,
      rrf_score: frame.rrf_score ?? result.score_breakdown.rrf_score,
      final_score: frame.score,
    },
    sequence_frames: [],
    thumbnail_url: frame.thumbnail_url ?? result.thumbnail_url,
    image_url: frame.image_url ?? result.image_url,
    image_uri: frame.image_uri ?? result.image_uri,
    image_storage_key: frame.image_storage_key ?? result.image_storage_key,
  };
}

function diversifyResultsForDisplay(
  results: SearchResult[],
  queryType: string,
): SearchResult[] {
  if (queryType !== "KIS" && queryType !== "QA") return results;

  const firstByVideo: SearchResult[] = [];
  const deferred: SearchResult[] = [];
  const seenVideos = new Set<string>();
  for (const result of results) {
    if (seenVideos.has(result.video_id)) {
      deferred.push(result);
      continue;
    }
    seenVideos.add(result.video_id);
    firstByVideo.push(result);
  }
  return [...firstByVideo, ...deferred].map((result, index) => ({
    ...result,
    rank: index + 1,
  }));
}

function timestampLabel(timestampMs: number | null): string {
  if (timestampMs === null) return "00:00";
  const total = Math.max(0, Math.floor(timestampMs / 1000));
  const minutes = Math.floor(total / 60)
    .toString()
    .padStart(2, "0");
  const seconds = (total % 60).toString().padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function formatScore(score: number): string {
  return score.toFixed(3);
}

function scoreNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number.parseFloat(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function scoreFromBreakdown(
  result: SearchResult,
  keys: string[],
): number | null {
  for (const key of keys) {
    const value = scoreNumber(result.score_breakdown[key]);
    if (value !== null) return value;
  }
  return null;
}

function scoreComponents(result: SearchResult): Array<{
  label: string;
  value: number;
  kind: "visual" | "text" | "rrf" | "final";
}> {
  const visual =
    scoreFromBreakdown(result, [
      "semantic_score",
      "visual_score",
      "semantic",
      "visual",
    ]) ?? 0;
  const text =
    scoreFromBreakdown(result, ["text_score", "metadata_score", "text"]) ?? 0;
  const rrf = scoreFromBreakdown(result, ["rrf_score", "rrf"]);
  const items: Array<{
    label: string;
    value: number;
    kind: "visual" | "text" | "rrf" | "final";
  }> = [
    { label: "Visual", value: visual, kind: "visual" },
    { label: "Text", value: text, kind: "text" },
  ];
  if (rrf !== null) items.push({ label: "RRF", value: rrf, kind: "rrf" });
  items.push({
    label: "Final",
    value: scoreFromBreakdown(result, ["final_score"]) ?? result.score,
    kind: "final",
  });
  return items;
}

function csvDownloadName(raw: string, fallback = "submission.csv"): string {
  const cleaned = (raw || fallback)
    .trim()
    .replace(/[\\/:*?"<>|]+/g, "_")
    .replace(/\s+/g, "_");
  const name = cleaned || fallback;
  return name.toLowerCase().endsWith(".csv") ? name : `${name}.csv`;
}

function uniqueFrameIndices(
  values: Array<number | null | undefined>,
): number[] {
  const seen = new Set<number>();
  const indices: number[] = [];
  values.forEach((value) => {
    if (value === null || value === undefined || seen.has(value)) return;
    seen.add(value);
    indices.push(value);
  });
  return indices;
}

function contextFrameFromResult(result: SearchResult): ContextFrame | null {
  if (!result.frame_id || result.frame_idx === null) return null;
  return {
    id: result.frame_id,
    frame_idx: result.frame_idx,
    timestamp_ms: result.timestamp_ms ?? 0,
    thumbnail_url: result.thumbnail_url ?? "",
    image_url: result.image_url,
    image_uri: result.image_uri,
    image_storage_key: result.image_storage_key,
    text: "",
  };
}

function clampNumber(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

function rowKey(row: SubmissionRow): string {
  return [
    row.query_name,
    row.query_type,
    row.video_code,
    row.frame_indices.join("-"),
    row.answer ?? "",
  ].join(":");
}

function normalizeRanks(rows: SubmissionRow[]): SubmissionRow[] {
  const counts = new Map<string, number>();
  return rows.map((row) => {
    const count = (counts.get(row.query_name) ?? 0) + 1;
    counts.set(row.query_name, count);
    return { ...row, rank: count };
  });
}

function csvCell(value: string | number): string {
  const raw = String(value);
  if (/[",\r\n]/.test(raw) || /^\s|\s$/.test(raw)) {
    return `"${raw.replace(/"/g, '""')}"`;
  }
  return raw;
}

function submissionCsvLine(row: SubmissionRow): string {
  const cells: Array<string | number> = [row.video_code, ...row.frame_indices];
  if (row.query_type === "QA") cells.push(row.answer ?? "");
  return cells.map(csvCell).join(",");
}

function buildSubmissionCsv(rows: SubmissionRow[]): string {
  const queryNames = new Set(rows.map((row) => row.query_name));
  if (queryNames.size <= 1) {
    return `${rows.map(submissionCsvLine).join("\r\n")}\r\n`;
  }
  const lines = [
    ["query_name", "video_code", "frame_indices", "answer"]
      .map(csvCell)
      .join(","),
    ...rows.map((row) =>
      [
        csvCell(row.query_name),
        csvCell(row.video_code),
        csvCell(row.frame_indices.join(" ")),
        csvCell(row.answer ?? ""),
      ].join(","),
    ),
  ];
  return `${lines.join("\r\n")}\r\n`;
}

function triggerCsvDownload(url: string, fileName: string): void {
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function downloadCsvBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  triggerCsvDownload(url, fileName);
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

async function downloadCsvFromUrl(
  url: string,
  fileName: string,
): Promise<void> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`CSV download failed: ${response.status}`);
  downloadCsvBlob(await response.blob(), fileName);
}

// Mock results generator for testing without API
function makeMockResults(type: QueryType, queryName: string): SearchResult[] {
  const videos = [
    "L21_V003",
    "L22_V017",
    "L24_V006",
    "L27_V042",
    "L30_V011",
    "L23_V028",
  ];
  return Array.from({ length: 36 }, (_, index) => {
    const videoCode = videos[index % videos.length];
    const frameIdx = 840 + index * 137 + (index % 5) * 17;
    const rank = index + 1;
    const score = Math.max(0.36, 0.94 - index * 0.013);
    const seed = `${type}-${videoCode}-${frameIdx}`;
    const sequenceFrames =
      type === "TRAKE"
        ? [0, 1, 2].map((offset) => ({
            frame_id: `mock-${videoCode}-${frameIdx + offset * 280}`,
            frame_idx: frameIdx + offset * 280,
            video_code: videoCode,
            score: Math.max(0.24, score - offset * 0.045),
          }))
        : [];

    return {
      id: `mock-${type}-${rank}`,
      rank,
      video_id: videoCode.toLowerCase(),
      video_code: videoCode,
      frame_id: `mock-${videoCode}-${frameIdx}`,
      frame_idx: frameIdx,
      timestamp_ms: frameIdx * 40,
      answer: type === "QA" ? "The Walt Disney Company" : null,
      score,
      score_breakdown: {
        visual: Math.max(0.21, score - 0.05).toFixed(2),
        text: Math.max(0.18, score - 0.12).toFixed(2),
        ocr: Math.max(0.08, score - 0.22).toFixed(2),
      },
      sequence_frames: sequenceFrames,
      thumbnail_url: `https://picsum.photos/seed/${seed}/420/240`,
      image_url: `https://picsum.photos/seed/${seed}/900/520`,
      image_uri: null,
      image_storage_key: null,
      video_url: null,
      video_uri: null,
    };
  });
}

function makeMockFrames(): MediaFrame[] {
  return makeMockResults("KIS", "query-1-kis").map((result) => ({
    id: result.frame_id ?? result.id,
    keyframe_id: result.frame_id ?? result.id,
    video_id: result.video_id,
    video_code: result.video_code,
    frame_idx: result.frame_idx ?? 0,
    timestamp_ms: result.timestamp_ms ?? 0,
    frame_type: "mock",
    thumbnail_url: result.thumbnail_url ?? "",
    image_url: result.image_url,
    image_uri: result.image_uri,
    image_storage_key: result.image_storage_key,
    is_media_present: true,
  }));
}

function frameToResult(frame: MediaFrame, rank: number): SearchResult {
  return {
    id: `gallery-${frame.id}`,
    rank,
    video_id: frame.video_id,
    video_code: frame.video_code,
    frame_id: frame.id,
    frame_idx: frame.frame_idx,
    timestamp_ms: frame.timestamp_ms,
    answer: null,
    score: Math.max(0.3, 0.72 - rank * 0.002),
    score_breakdown: {
      gallery: "manual",
      frame_type: frame.frame_type ?? "keyframe",
    },
    sequence_frames: [],
    thumbnail_url: frame.thumbnail_url,
    image_url: frame.image_url,
    image_uri: frame.image_uri,
    image_storage_key: frame.image_storage_key,
    video_url: null,
    video_uri: null,
  };
}

function mockContextForResult(result: SearchResult): FrameContext | null {
  if (!result.frame_id || result.frame_idx === null) return null;
  const frames: ContextFrame[] = [-2, -1, 0, 1, 2].map((offset) => {
    const frameIdx = Math.max(0, (result.frame_idx ?? 0) + offset * 40);
    const seed = `context-${result.video_code}-${frameIdx}`;
    return {
      id: `context-${result.video_code}-${frameIdx}`,
      frame_idx: frameIdx,
      timestamp_ms: frameIdx * 40,
      thumbnail_url: `https://picsum.photos/seed/${seed}/320/180`,
      image_url: `https://picsum.photos/seed/${seed}/640/360`,
      image_uri: null,
      image_storage_key: null,
      text:
        offset === 0
          ? "Target frame selected for submission."
          : "Nearby frame for temporal context.",
    };
  });
  return {
    target_frame_id: `context-${result.video_code}-${result.frame_idx}`,
    video_code: result.video_code,
    frames,
  };
}

function makeAgentTrace(
  mode: AppMode,
  queryType: SearchTask,
  resultCount: number,
  reasoningModel?: ReasoningModel,
): AgentStep[] {
  const action = mode === "Auto" ? "Shortlist" : "Answer trace";
  const align =
    queryType === "TRAKE"
      ? "Temporal order checked across event frames."
      : "Frame window checked around top matches.";
  return [
    {
      title: "Parse query",
      detail: reasoningModel
        ? `${reasoningModelLabels[reasoningModel]} selected for reasoning.`
        : `${queryType} format detected and mapped to Codabench output.`,
      status: "done",
    },
    {
      title: "Retrieve candidates",
      detail: `Visual, OCR and metadata signals produced ${resultCount} candidates.`,
      status: "done",
    },
    {
      title: action,
      detail: align,
      status: resultCount > 0 ? "done" : "running",
    },
    {
      title: "Submission check",
      detail: "CSV rows are checked before export.",
      status: "queued",
    },
  ];
}

function makeVideoLookupTrace(
  videoCode: string,
  frameIdx: number | null,
  resultCount: number,
): AgentStep[] {
  const target =
    frameIdx === null
      ? "all indexed keyframes"
      : `nearest keyframes to F${frameIdx}`;
  return [
    {
      title: "Resolve video",
      detail: `Matched video code ${videoCode}.`,
      status: resultCount > 0 ? "done" : "warning",
    },
    {
      title: "Locate frames",
      detail: `Loaded ${target}.`,
      status: resultCount > 0 ? "done" : "queued",
    },
    {
      title: "Submission check",
      detail: "Picked frames export as KIS-compatible CSV rows.",
      status: "queued",
    },
  ];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

type TemporalEventMatch = {
  eventIndex: number;
  query: string;
  viewCount: number;
  frameIdx: number | null;
  score: number | null;
  visualScore: number | null;
  textScore: number | null;
  rrfScore: number | null;
};

function temporalEventMatches(result: SearchResult): TemporalEventMatch[] {
  const eventQueries = result.score_breakdown.event_queries;
  if (!Array.isArray(eventQueries)) return [];

  return eventQueries.flatMap((rawEvent, arrayIndex) => {
    if (!isRecord(rawEvent)) return [];
    const eventIndex = scoreNumber(rawEvent.event_index) ?? arrayIndex + 1;
    const matchedFrame = result.sequence_frames.find(
      (frame) => (frame.event_index ?? frame.order_index) === eventIndex,
    );
    const query = String(
      rawEvent.query ?? matchedFrame?.event_query ?? "",
    ).trim();
    if (!query) return [];
    return [
      {
        eventIndex,
        query,
        viewCount:
          stringList(rawEvent.multi_views).length ||
          scoreNumber(rawEvent.view_count) ||
          1,
        frameIdx: matchedFrame?.frame_idx ?? null,
        score: matchedFrame?.score ?? null,
        visualScore: matchedFrame?.visual_score ?? null,
        textScore: matchedFrame?.text_score ?? null,
        rrfScore: matchedFrame?.rrf_score ?? null,
      },
    ];
  });
}

function hasTemporalEventMatches(result: SearchResult): boolean {
  return temporalEventMatches(result).length > 0;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => String(item ?? "").trim())
    .filter((item) => item.length > 0);
}

function summarizeList(values: string[], empty: string): string {
  if (values.length === 0) return empty;
  const shown = values.slice(0, 3);
  const suffix =
    values.length > shown.length ? ` +${values.length - shown.length}` : "";
  return `${shown.join(" | ")}${suffix}`;
}

function summarizeFactors(value: unknown): string {
  if (!isRecord(value)) return "No structured factors returned.";
  const labels: Record<string, string> = {
    subjects: "subjects",
    actions: "actions",
    objects: "objects",
    attributes: "attributes",
    scene: "scene",
    text_cues: "OCR",
    time_cues: "time",
    negative_constraints: "negative",
  };
  const parts = Object.entries(labels)
    .map(([key, label]) => {
      const values = stringList(value[key]);
      if (values.length === 0) return "";
      return `${label}: ${values.slice(0, 3).join(", ")}`;
    })
    .filter(Boolean);
  return parts.length > 0
    ? parts.join(" / ")
    : "No structured factors returned.";
}

function visualSearchLabel(
  visualSearch: SearchResponse["normalized_query"]["visual_search"],
): string {
  const mode = visualSearch?.mode;
  if (mode === "siglip2") return "SigLIP2";
  if (mode === "both") return "OpenCLIP + SigLIP2";
  if (mode === "profile") return "Profile visual";
  return "OpenCLIP";
}

function visualModelSummary(
  visualSearch: SearchResponse["normalized_query"]["visual_search"],
): string {
  const models = visualSearch?.models ?? [];
  if (models.length === 0) return visualSearchLabel(visualSearch);
  return models
    .map((model) => (model.family === "siglip2" ? "SigLIP2" : "OpenCLIP"))
    .filter((label, index, labels) => labels.indexOf(label) === index)
    .join(" + ");
}

function makeTraceFromResponse(
  mode: AppMode,
  queryType: QueryType,
  response: SearchResponse,
  resultCount: number,
): AgentStep[] {
  const normalized = response.normalized_query;
  const plan = normalized.agent_query_plan;
  const metadata = plan?.agent_metadata;
  const source = plan?.source ?? "baseline";
  const profile = metadata?.active_profile ?? normalized.profile ?? "default";
  const provider = metadata?.provider ?? "local";
  const model = metadata?.model ?? "retrieval";
  const keyEnv = metadata?.api_key_env ?? "API key";
  const keyStatus =
    metadata?.api_key_configured === false
      ? `missing ${keyEnv}`
      : metadata?.api_key_configured
        ? `${keyEnv} configured`
        : "key status unavailable";
  const traceStatus: AgentStep["status"] = plan?.error ? "warning" : "done";
  // The backend may repair a collapsed agent plan before independent event retrieval.
  const temporalEvents =
    normalized.temporal_events ?? plan?.temporal_events ?? [];
  const textTemporalEvents = normalized.text_temporal_events ?? [];
  const semanticVariants =
    normalized.multi_views ??
    normalized.semantic_variants ??
    normalized.variants ??
    [];
  const textVariants = normalized.text_variants ?? [];
  const summary = plan?.summary || semanticVariants[0] || "Query parsed.";
  const traceLabel =
    metadata?.langsmith_trace_enabled && metadata.langsmith_api_key_configured
      ? "LangSmith on"
      : "LangSmith off";
  const retrievalWeights =
    plan?.retrieval_weights ?? normalized.retrieval_weights;
  const weightSource =
    plan?.retrieval_weight_source ??
    normalized.retrieval_weight_source ??
    "profile";
  const textSourceWeights =
    plan?.text_source_weights ?? normalized.text_source_weights;
  const visualSearch = normalized.visual_search;
  const visualSummary = visualModelSummary(visualSearch);
  return [
    {
      title: "Agent profile",
      // detail: `${profile} | ${provider}/${model} | ${keyStatus} | ${traceLabel}`,
      detail: `${profile}`,
      status: traceStatus,
      raw: metadata ?? null,
    },
    {
      title: "Decompose query",
      detail: `${plan?.intent ?? queryType} / ${plan?.language ?? normalized.language ?? "auto"}: ${summary}`,
      status: traceStatus,
      raw: {
        summary: plan?.summary ?? summary,
        decomposition: plan?.decomposition ?? null,
        multi_views: semanticVariants,
        semantic_variants: semanticVariants,
        text_variants: textVariants,
      },
    },
    {
      title: "Search factors",
      detail: summarizeFactors(plan?.decomposition?.search_factors),
      status: traceStatus,
      raw: plan?.decomposition?.search_factors ?? null,
    },
    {
      title: "Route modalities",
      detail: retrievalWeights
        ? `Visual ${formatScore(retrievalWeights.visual ?? 0)} (${visualSummary}) | Text ${formatScore(retrievalWeights.text ?? 0)} | ${weightSource}`
        : "Using retrieval profile weights.",
      status: traceStatus,
      raw: {
        ...(plan?.decomposition?.retrieval_strategy ?? {
          weights: retrievalWeights,
          source: weightSource,
        }),
        visual_search: visualSearch,
      },
    },
    {
      title: "Multi-view search",
      detail: `English: ${summarizeList(semanticVariants, "No semantic view returned.")}`,
      status: traceStatus,
      raw: {
        language: "en",
        multi_views: semanticVariants,
        visual_search: visualSearch,
      },
    },
    {
      title: "Captioning query",
      detail: `English: ${summarizeList(semanticVariants, "No English caption query returned.")}`,
      status: traceStatus,
      raw: {
        source: "caption",
        language: "en",
        multi_views: semanticVariants,
      },
    },
    {
      title: "ASR query",
      detail: `Vietnamese: ${summarizeList(textVariants, "No Vietnamese text query returned.")}`,
      status: traceStatus,
      raw: {
        language: "vi",
        text_variants: textVariants,
      },
    },
    {
      title: "OCR query",
      detail: `Exact text / Vietnamese: ${summarizeList(textVariants, "No OCR query returned.")}`,
      status: traceStatus,
      raw: {
        source: "ocr",
        language: "vi",
        text_variants: textVariants,
      },
    },
    {
      title: "Temporal reasoning",
      detail:
        temporalEvents.length > 1
          ? `${normalized.temporal_strategy === "dev_first_search" ? `DEV-first / ${normalized.target_scope ?? "frame"}: diagnostic-event video-first. ` : ""}The plan separates this request into ordered moments. Embedding/captioning (English): ${summarizeList(temporalEvents, "-")} | ASR/OCR (Vietnamese): ${summarizeList(textTemporalEvents, "-")}`
          : normalized.temporal_mode
            ? "The plan uses the available temporal evidence for this request."
            : "No temporal split needed.",
      status: traceStatus,
      raw: {
        semantic_events: temporalEvents,
        text_events: textTemporalEvents,
        event_plans:
          normalized.temporal_event_plans ?? plan?.temporal_event_plans ?? [],
      },
    },
    {
      title: "Retrieve candidates",
      detail: `${mode === "Auto" ? "Shortlisted" : "Returned"} ${resultCount} results using ${visualSummary}, English captioning, Vietnamese ASR, and OCR. Text weights: ASR ${formatScore(textSourceWeights?.asr ?? 0)} | Caption ${formatScore(textSourceWeights?.caption ?? 0)} | OCR ${formatScore(textSourceWeights?.ocr ?? 0)}. Source: ${source}${plan?.error ? ` | ${plan.error}` : ""}`,
      status: resultCount > 0 ? "done" : "warning",
      raw: {
        query_run_id: response.query_run_id,
        latency_ms: normalized.latency_ms,
        top_results: response.results.slice(0, 5).map((result) => ({
          rank: result.rank,
          video_code: result.video_code,
          frame_idx: result.frame_idx,
          score: result.score,
          score_breakdown: result.score_breakdown,
        })),
      },
    },
  ];
}

function CloudFrameImage({
  candidates,
  alt,
  eager = false,
}: {
  candidates: string[];
  alt: string;
  eager?: boolean;
}) {
  const [candidateIndex, setCandidateIndex] = useState(0);
  const signature = candidates.join("|");

  useEffect(() => {
    setCandidateIndex(0);
  }, [signature]);

  const src = candidates[candidateIndex];
  if (!src) {
    return (
      <div
        className="cloud-image-placeholder"
        aria-label="No frame media available"
      >
        <ImageOff size={20} />
      </div>
    );
  }

  return (
    <img
      src={src}
      alt={alt}
      loading={eager ? "eager" : "lazy"}
      decoding="async"
      fetchPriority={eager ? "high" : "auto"}
      referrerPolicy="no-referrer"
      onError={() =>
        setCandidateIndex((index) => Math.min(index + 1, candidates.length))
      }
    />
  );
}

function ScoreBreakdown({
  result,
  compact = false,
}: {
  result: SearchResult;
  compact?: boolean;
}) {
  return (
    <div className={`score-breakdown ${compact ? "compact" : ""}`}>
      {scoreComponents(result).map((item) => (
        <span className={`score-pill ${item.kind}`} key={item.label}>
          <small>{item.label}</small>
          <strong>{formatScore(item.value)}</strong>
        </span>
      ))}
    </div>
  );
}

function evidenceTimeLabel(item: VideoEvidenceItem): string {
  if (item.start_seconds === null) return "Unmapped time";
  const start = timestampLabel(item.start_seconds * 1000);
  if (item.end_seconds === null || item.end_seconds <= item.start_seconds)
    return start;
  return `${start} - ${timestampLabel(item.end_seconds * 1000)}`;
}

function VideoEvidenceSection({
  title,
  items,
  emptyLabel,
}: {
  title: string;
  items: VideoEvidenceItem[];
  emptyLabel: string;
}) {
  return (
    <section className="video-evidence-section">
      <div className="video-evidence-heading">
        <strong>{title}</strong>
        <small>{items.length}</small>
      </div>
      {items.length === 0 ? (
        <p className="video-evidence-empty">{emptyLabel}</p>
      ) : (
        <div className="video-evidence-list">
          {items.map((item, index) => (
            <article
              className="video-evidence-item active"
              key={`${item.segment_id ?? title}-${item.frame_id ?? ""}-${index}`}
            >
              <time>{evidenceTimeLabel(item)}</time>
              <p>{item.text}</p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function TemporalEventEvidence({ result }: { result: SearchResult }) {
  const events = temporalEventMatches(result);
  if (events.length === 0) return null;

  return (
    <section
      className="temporal-event-evidence"
      aria-label="Temporal event search scores"
    >
      <div className="video-evidence-heading">
        <strong>Temporal event</strong>
        <small>{events.length} events</small>
      </div>
      <p className="temporal-event-caption">
        Scores show the frame selected for each event in this video sequence.
      </p>
      <div className="temporal-event-list">
        {events.map((event) => (
          <article className="temporal-event-card" key={event.eventIndex}>
            <div className="temporal-event-card-header">
              <span>E{event.eventIndex}</span>
              {event.frameIdx === null ? (
                <small>Not matched</small>
              ) : (
                <small>Frame {event.frameIdx}</small>
              )}
            </div>
            <p>{event.query}</p>
            <div className="temporal-event-meta">
              <small>
                {event.viewCount} view{event.viewCount === 1 ? "" : "s"}
              </small>
              <strong>
                {event.score === null ? "—" : formatScore(event.score)}
              </strong>
            </div>
            {event.score !== null && (
              <div className="temporal-event-score-grid">
                <span>
                  <small>Visual</small>
                  {formatScore(event.visualScore ?? 0)}
                </span>
                <span>
                  <small>Text</small>
                  {formatScore(event.textScore ?? 0)}
                </span>
                <span>
                  <small>RRF</small>
                  {formatScore(event.rrfScore ?? 0)}
                </span>
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

function VideoEvidenceSidebar({
  evidence,
  loading,
  frameIdx,
  result,
}: {
  evidence: VideoEvidence | null;
  loading: boolean;
  frameIdx: number | null;
  result: SearchResult;
}) {
  const values = evidence?.evidence;
  return (
    <aside className="video-evidence-sidebar" aria-label="Video text evidence">
      <div className="video-evidence-sidebar-header">
        <span>
          <strong>Evidence</strong>
          <small>
            {frameIdx === null ? "Selected frame" : `Frame ${frameIdx}`}
          </small>
        </span>
        {loading && (
          <Loader2
            className="spin-icon"
            size={15}
            aria-label="Loading evidence"
          />
        )}
      </div>
      <div className="video-evidence-scroll">
        <TemporalEventEvidence result={result} />
        <VideoEvidenceSection
          title="ASR"
          items={values?.asr ?? []}
          emptyLabel={
            loading ? "Loading ASR transcript" : "No ASR aligned to this frame"
          }
        />
        <VideoEvidenceSection
          title="OCR"
          items={values?.ocr ?? []}
          emptyLabel={loading ? "Loading OCR" : "No OCR aligned to this frame"}
        />
        <VideoEvidenceSection
          title="Captioning (English)"
          items={values?.captions ?? []}
          emptyLabel={
            loading
              ? "Loading captions"
              : "No English caption aligned to this frame"
          }
        />
      </div>
    </aside>
  );
}

function TrakeFrameScores({
  frame,
}: {
  frame: SearchResult["sequence_frames"][number];
}) {
  return (
    <div className="score-breakdown compact trake-frame-scores">
      <span className="score-pill visual">
        <small>Visual</small>
        <strong>{formatScore(frame.visual_score ?? 0)}</strong>
      </span>
      <span className="score-pill text">
        <small>Text</small>
        <strong>{formatScore(frame.text_score ?? 0)}</strong>
      </span>
      <span className="score-pill rrf">
        <small>RRF</small>
        <strong>{formatScore(frame.rrf_score ?? 0)}</strong>
      </span>
      <span className="score-pill final">
        <small>Event</small>
        <strong>{formatScore(frame.score)}</strong>
      </span>
    </div>
  );
}

function FrameCard({
  result,
  selected,
  eager,
  onOpen,
  onSelect,
  onPreview,
}: {
  result: SearchResult;
  selected: boolean;
  eager?: boolean;
  onOpen: () => void;
  onSelect: () => void;
  onPreview: () => void;
}) {
  const candidates = resultImageCandidates(result);
  const frameText = result.frame_idx ?? "N/A";
  return (
    <article
      className={`frame-card ${selected ? "selected" : ""}`}
      onClick={onOpen}
    >
      <div className="frame-thumb">
        <CloudFrameImage
          candidates={candidates}
          alt={`${result.video_code} frame ${frameText}`}
          eager={eager}
        />
        <span className="rank-chip">#{result.rank}</span>
      </div>
      <div className="frame-card-body">
        <div className="frame-title-row">
          <strong>{result.video_code}</strong>
          <span>{formatScore(result.score)}</span>
        </div>
        <p>Frame {frameText}</p>
        <ScoreBreakdown result={result} compact />
        <div className="frame-actions">
          <button
            type="button"
            className="ghost-button"
            onClick={(event) => {
              event.stopPropagation();
              onPreview();
            }}
          >
            Video
          </button>
          <button
            type="button"
            className="select-button"
            onClick={(event) => {
              event.stopPropagation();
              onSelect();
            }}
          >
            {selected ? "Added" : "Pick"}
          </button>
        </div>
      </div>
    </article>
  );
}

const EMPTY_SELECTION = new Set<string>();
const ResultGrid = memo(function ResultGrid({results, columns, selectedKeys, keyFor, onOpen, onPick, onPreview, className = "frame-grid"}: {
  results: SearchResult[]; columns: number; selectedKeys: Set<string>; keyFor: (result: SearchResult) => string;
  onOpen: (result: SearchResult) => void; onPick: (result: SearchResult) => void; onPreview: (result: SearchResult) => void;
  className?: string;
}) {
  if (import.meta.env.DEV) {
    if (performance.getEntriesByName("result-grid-render").length >= 100) performance.clearMarks("result-grid-render");
    performance.mark("result-grid-render");
  }
  return <div className={className} style={{gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`}}>
    {results.map((result, index) => <FrameCard key={result.id} result={result} eager={index < columns}
      selected={selectedKeys.has(keyFor(result))} onOpen={() => onOpen(result)}
      onSelect={() => onPick(result)} onPreview={() => onPreview(result)} />)}
  </div>;
});

function TrakeRows({
  results,
  eventCount,
  isSelected,
  onOpen,
  onSelect,
  onPreview,
  selectedFrames,
  selectedEventCount,
  selectedVideoCode,
  canAddSequence,
  onAddSequence,
  onClearSelection,
}: {
  results: SearchResult[];
  eventCount: number;
  isSelected: (
    result: SearchResult,
    frame: TrakeSequenceFrame,
    eventIndex: number,
  ) => boolean;
  onOpen: (result: SearchResult) => void;
  onSelect: (
    result: SearchResult,
    frame: TrakeSequenceFrame,
    eventIndex: number,
  ) => void;
  onPreview: (result: SearchResult, eventIndex?: number) => void;
  selectedFrames: TrakeFrameChoice[];
  selectedEventCount: number;
  selectedVideoCode: string | null;
  canAddSequence: boolean;
  onAddSequence: () => void;
  onClearSelection: () => void;
}) {
  const inferredEventCount = Math.max(
    1,
    eventCount,
    ...results.flatMap((result) =>
      result.sequence_frames.map(
        (frame) => frame.event_index ?? frame.order_index ?? 0,
      ),
    ),
  );
  const lanes = Array.from({ length: inferredEventCount }, (_, index) => index);

  return (
    <div className="trake-board" aria-label="TRAKE ordered frame lanes">
      {lanes.map((lane) => {
        const laneCells = results.flatMap((result, resultIndex) => {
          const sequenceFrame =
            result.sequence_frames.find(
              (frame) => (frame.event_index ?? frame.order_index) === lane + 1,
            ) ?? result.sequence_frames[lane];
          if (!sequenceFrame && lane > 0) return [];
          const frameIdx = sequenceFrame?.frame_idx ?? result.frame_idx;
          const timestampMs =
            sequenceFrame?.timestamp_ms ?? result.timestamp_ms ?? null;
          const candidates = sequenceFrame
            ? sequenceImageCandidates(sequenceFrame, result)
            : resultImageCandidates(result);
          return [
            {
              result,
              resultIndex,
              sequenceFrame,
              frameIdx,
              timestampMs,
              candidates,
              selected:
                sequenceFrame !== undefined
                  ? isSelected(result, sequenceFrame, lane + 1)
                  : false,
            },
          ];
        });
        const seenFrameKeys = new Set<string>();
        const uniqueLaneCells = laneCells.filter((cell) => {
          const frameKey = [
            cell.result.video_code,
            cell.frameIdx ??
              cell.sequenceFrame?.frame_id ??
              cell.result.frame_id ??
              cell.result.id,
          ].join(":");
          if (seenFrameKeys.has(frameKey)) return false;
          seenFrameKeys.add(frameKey);
          return true;
        });

        return (
          <section className="trake-lane" key={lane}>
            <div className="trake-lane-label">
              <strong>E{lane + 1}</strong>
              <span>{uniqueLaneCells.length} candidates</span>
            </div>
            <div className="trake-strip">
              {uniqueLaneCells.map(
                ({
                  result,
                  resultIndex,
                  sequenceFrame,
                  frameIdx,
                  timestampMs,
                  candidates,
                  selected,
                }) => {
                  const frameResult = sequenceFrame
                    ? resultForTrakeFrame(result, sequenceFrame)
                    : result;
                  return (
                    <article
                      className={`trake-cell ${selected ? "selected" : ""}`}
                      key={`${result.video_code}-${frameIdx ?? sequenceFrame?.frame_id ?? result.id}`}
                      onClick={() => onOpen(frameResult)}
                    >
                      <div className="trake-thumb">
                        <CloudFrameImage
                          candidates={candidates}
                          alt={`${result.video_code} event ${lane + 1} frame ${frameIdx ?? "N/A"}`}
                          eager={lane === 0 && resultIndex < 8}
                        />
                        <span className="rank-chip">#{result.rank}</span>
                      </div>
                      <div className="trake-cell-body">
                        <strong>{result.video_code}</strong>
                        <small>
                          F{frameIdx ?? "N/A"} | {timestampLabel(timestampMs)}
                        </small>
                        {sequenceFrame ? (
                          <TrakeFrameScores frame={sequenceFrame} />
                        ) : (
                          <ScoreBreakdown result={result} compact />
                        )}
                        <div className="trake-cell-actions">
                          <button
                            type="button"
                            className="ghost-button"
                            onClick={(event) => {
                              event.stopPropagation();
                              onPreview(frameResult, lane + 1);
                            }}
                          >
                            Video
                          </button>
                          <button
                            type="button"
                            className="select-button"
                            onClick={(event) => {
                              event.stopPropagation();
                              if (sequenceFrame)
                                onSelect(result, sequenceFrame, lane + 1);
                            }}
                            disabled={!sequenceFrame}
                          >
                            {selected ? "Picked" : "Pick"}
                          </button>
                        </div>
                      </div>
                    </article>
                  );
                },
              )}
            </div>
          </section>
        );
      })}
      <div className="trake-selection-toolbar" aria-live="polite">
        <div>
          <strong>
            {selectedEventCount} frame{selectedEventCount === 1 ? "" : "s"}{" "}
            selected
          </strong>
          <small>
            {selectedVideoCode
              ? `Video ${selectedVideoCode}`
              : "Pick one frame for each event"}
          </small>
        </div>
        <div className="trake-selection-actions">
          <button
            type="button"
            className="ghost-button"
            onClick={onClearSelection}
            disabled={selectedEventCount === 0}
          >
            Clear
          </button>
          <button
            type="button"
            className="select-button"
            onClick={onAddSequence}
            disabled={!canAddSequence}
          >
            Add sequence
          </button>
        </div>
      </div>
      <section
        className="trake-picked-sequence"
        aria-label="Selected TRAKE sequence"
      >
        <div className="trake-picked-heading">
          <strong>Sequence draft</strong>
          <small>Click a chosen frame to review it in the video.</small>
        </div>
        <div className="trake-picked-strip">
          {lanes.map((lane) => {
            const eventIndex = lane + 1;
            const choice = selectedFrames.find(
              (item) => item.eventIndex === eventIndex,
            );
            const frameResult = choice
              ? resultForTrakeFrame(choice.result, choice.frame)
              : null;
            return (
              <button
                type="button"
                className={`trake-picked-frame ${choice ? "picked" : "empty"}`}
                key={eventIndex}
                disabled={!choice || !frameResult}
                onClick={() => {
                  if (frameResult) onPreview(frameResult, eventIndex);
                }}
              >
                {choice ? (
                  <CloudFrameImage
                    candidates={sequenceImageCandidates(
                      choice.frame,
                      choice.result,
                    )}
                    alt={`Selected event ${eventIndex}, frame ${choice.frame.frame_idx}`}
                  />
                ) : (
                  <span className="trake-picked-placeholder">No frame</span>
                )}
                <span>
                  <strong>E{eventIndex}</strong>
                  {choice ? (
                    <small>
                      {choice.result.video_code} | F{choice.frame.frame_idx} |{" "}
                      {timestampLabel(choice.frame.timestamp_ms ?? null)}
                    </small>
                  ) : (
                    <small>Not selected</small>
                  )}
                </span>
              </button>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function ReasoningDisclosure({
  steps,
  compact = false,
}: {
  steps: AgentStep[];
  compact?: boolean;
}) {
  const running = steps.some((step) => step.status === "running");
  const doneCount = steps.filter(
    (step) => step.status === "done" || step.status === "warning",
  ).length;
  return (
    <details
      className={`reasoning-disclosure ${compact ? "compact" : ""}`}
      open={running}
    >
      <summary>
        <span>{running ? "Thinking" : "Reasoning"}</span>
        <small>
          {doneCount}/{steps.length} checks
        </small>
      </summary>
      <div className="reasoning-steps">
        {steps.map((step) => (
          <div className={`reasoning-step ${step.status}`} key={step.title}>
            <small className="reasoning-status">
              {step.status === "done"
                ? "Done"
                : step.status === "running"
                  ? "Running"
                  : step.status === "warning"
                    ? "Check"
                    : "Waiting"}
            </small>
            <span>
              <strong>{step.title}</strong>
              <small>{step.detail}</small>
              {step.raw !== undefined && step.raw !== null && (
                <pre className="trace-json">
                  {JSON.stringify(step.raw, null, 2)}
                </pre>
              )}
            </span>
          </div>
        ))}
      </div>
    </details>
  );
}

function SearchLoadingStage({ frameColumns }: { frameColumns: number }) {
  const columns = Math.round(clampNumber(frameColumns, 1, 8));
  const ghostCount = Math.max(9, columns * 3);

  return (
    <div className="search-loading-stage" aria-label="Searching">
      <div
        className="search-loading-grid"
        style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
      >
        {Array.from({ length: ghostCount }).map((_, index) => (
          <span
            key={index}
            className="search-loading-frame"
            style={{ animationDelay: `${index * 80}ms` }}
          />
        ))}
      </div>
    </div>
  );
}

// Functions in App

export function App() {
  const [initialWorkspace] = useState(() => readWorkspace());
  const saved = initialWorkspace.value;
  const drafts = useRef(saved?.drafts ?? {});
  const exportInFlight = useRef(false);
  // Attibutes
  const [mode, setMode] = useState<AppMode>(saved?.mode ?? "Search");
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [datasetId, setDatasetId] = useState(saved?.datasetId ?? "");
  const [queryType, setQueryType] = useState<SearchTask>(saved?.active.queryType ?? "KIS");
  const [queryName, setQueryName] = useState(saved?.active.queryName ?? queryNameByType.KIS);
  const [exportFileName, setExportFileName] = useState(saved?.active.exportFileName ?? queryNameByType.KIS);
  const [queryText, setQueryText] = useState(saved?.active.queryText ?? sampleQueries.KIS);
  const [videoCodeQuery, setVideoCodeQuery] = useState(saved?.active.videoCodeQuery ?? sampleQueries.VIDEO);
  const [videoFrameQuery, setVideoFrameQuery] = useState(saved?.active.videoFrameQuery ?? "");
  const [topK, setTopK] = useState(saved?.active.topK ?? 50);
  const [useExpansion, setUseExpansion] = useState(saved?.active.useExpansion ?? true);
  const [useAgentPlanning, setUseAgentPlanning] = useState(saved?.active.useAgentPlanning ?? true);
  const [useMetadata, setUseMetadata] = useState(saved?.active.useMetadata ?? true);
  const [kisTemporalMode, setKisTemporalMode] = useState(saved?.active.kisTemporalMode ?? false);
  const [temporalStrategy, setTemporalStrategy] = useState<
    "vortex_k_context" | "aithena_weighted_ats" | "dev_first_search"
  >(saved?.active.temporalStrategy ?? "vortex_k_context");
  const [visualSearchMode, setVisualSearchMode] =
    useState<VisualSearchMode>(saved?.active.visualSearchMode ?? "openclip");
  const [weights, setWeights] = useState({
    visual: 0.42,
    text: 0.32,
    ocr: 0.16,
    temporal: 0.1,
  });
  const [frameColumns, setFrameColumns] = useState(5);
  const [resultFilter, setResultFilter] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [trakeEventCount, setTrakeEventCount] = useState(4);
  const [trakeFrameChoices, setTrakeFrameChoices] = useState<
    TrakeFrameChoice[]
  >([]);
  const [galleryFrames, setGalleryFrames] = useState<MediaFrame[]>([]);
  const [galleryLoading, setGalleryLoading] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("Ready");
  const searchRequests = useRef(new LatestRequest());
  const contextRequests = useRef(new LatestRequest());
  const previewRequests = useRef(new LatestRequest());

  function cancelSearch() {
    searchRequests.current.cancel();
    contextRequests.current.cancel();
    previewRequests.current.cancel();
    setLoading(false);
  }

  useEffect(() => {
    cancelSearch();
    return () => {
      searchRequests.current.cancel();
      contextRequests.current.cancel();
      previewRequests.current.cancel();
    };
  }, [datasetId, queryType, mode]);
  const [selected, setSelected] = useState<SubmissionRow[]>(saved?.selected ?? []);
  const [context, setContext] = useState<FrameContext | null>(null);
  const [activeResultId, setActiveResultId] = useState<string | null>(null);
  const [history, setHistory] = useState<SearchHistoryItem[]>(saved?.history ?? []);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [intelligenceOpen, setIntelligenceOpen] = useState(false);
  const [autoEnabled, setAutoEnabled] = useState(true);
  const [autoTrace, setAutoTrace] = useState<AgentStep[]>(
    makeAgentTrace("Auto", "KIS", 0),
  );
  const [leftSidebarOpen, setLeftSidebarOpen] = useState(() =>
    typeof window === "undefined" ? true : window.innerWidth > 900,
  );
  const [rightSidebarOpen, setRightSidebarOpen] = useState(() =>
    typeof window === "undefined" ? true : window.innerWidth > 1180,
  );
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const [reasoningModel, setReasoningModel] =
    useState<ReasoningModel>(saved?.active.reasoningModel ?? "gpt-4o");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([
    {
      id: "assistant-welcome",
      role: "assistant",
      text: "Upload a video or ask a QA query. I will keep the trace visible and prepare CSV rows.",
      trace: makeAgentTrace("Chat", "QA", 0),
    },
  ]);
  const [videoPreview, setVideoPreview] = useState<VideoPreview | null>(null);
  const [videoEvidenceOpen, setVideoEvidenceOpen] = useState(false);
  const [videoPlaybackSeconds, setVideoPlaybackSeconds] = useState<
    number | null
  >(null);
  const [theme, setTheme] = useState<ThemeMode>(() => {
    try {
      return (
        (localStorage.getItem("chatshasimi-theme") as ThemeMode) ?? "system"
      );
    } catch {
      return "system";
    }
  });

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const videoPreviewRef = useRef<HTMLVideoElement | null>(null);

  const videoDialogRef = useRef<HTMLDivElement>(null);
  const settingsDialogRef = useRef<HTMLDivElement>(null);
  function closeVideoPreview() {
    previewRequests.current.cancel();
    setVideoPreview(null);
  }
  useDialogFocus(videoDialogRef, Boolean(videoPreview), closeVideoPreview);
  useDialogFocus(settingsDialogRef, settingsOpen, () => setSettingsOpen(false));

  function currentDraft(): SearchDraft {
    return {queryType, queryName, queryText, exportFileName, videoCodeQuery, videoFrameQuery,
      topK, useExpansion, useAgentPlanning, useMetadata, kisTemporalMode, temporalStrategy,
      visualSearchMode, reasoningModel, sourceMode: "auto", temporalEvents: []};
  }
  function applyDraft(draft: SearchDraft) {
    setQueryType(draft.queryType); setQueryName(draft.queryName); setQueryText(draft.queryText);
    setExportFileName(draft.exportFileName); setVideoCodeQuery(draft.videoCodeQuery); setVideoFrameQuery(draft.videoFrameQuery);
    setTopK(draft.topK); setUseExpansion(draft.useExpansion); setUseAgentPlanning(draft.useAgentPlanning);
    setUseMetadata(draft.useMetadata); setKisTemporalMode(draft.kisTemporalMode); setTemporalStrategy(draft.temporalStrategy);
    setVisualSearchMode(draft.visualSearchMode); setReasoningModel(draft.reasoningModel);
  }
  const workspace = useMemo(() => ({version: 1 as const, mode, datasetId, active: currentDraft(),
    drafts: {...drafts.current, [queryType]: currentDraft()}, selected, history}),
    [mode,datasetId,queryType,queryName,queryText,exportFileName,videoCodeQuery,videoFrameQuery,
      topK,useExpansion,useAgentPlanning,useMetadata,kisTemporalMode,temporalStrategy,visualSearchMode,reasoningModel,selected,history]);
  useEffect(() => {
    if (initialWorkspace.error) return; // Preserve incompatible storage for recovery.
    const save = () => { const error = saveWorkspace(workspace); if (error) console.warn(error); };
    const timer = window.setTimeout(save, 250);
    window.addEventListener("pagehide", save);
    return () => { window.clearTimeout(timer); window.removeEventListener("pagehide", save); };
  }, [workspace, initialWorkspace.error]);

  // Effects
  useEffect(() => {
    const apply = () => {
      const prefersDark = window.matchMedia(
        "(prefers-color-scheme: dark)",
      ).matches;
      document.documentElement.classList.toggle(
        "theme-dark",
        theme === "dark" || (theme === "system" && prefersDark),
      );
    };
    apply();
    try {
      localStorage.setItem("chatshasimi-theme", theme);
    } catch {
      // ignore local storage failures
    }
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [theme]);

  useEffect(() => {
    videoPreviewRef.current?.pause();
    setVideoPlaybackSeconds(null);
  }, [videoPreview?.url]);

  useEffect(() => {
    const player = videoPreviewRef.current;
    const targetSeconds = videoPreview?.targetSeconds;
    if (!player || targetSeconds === undefined) return;

    const seek = () => {
      player.pause();
      const upperBound = Number.isFinite(player.duration)
        ? Math.max(0, player.duration)
        : targetSeconds;
      player.currentTime = Math.min(Math.max(0, targetSeconds), upperBound);
      setVideoPlaybackSeconds(targetSeconds);
    };
    if (player.readyState >= HTMLMediaElement.HAVE_METADATA) {
      seek();
      return;
    }
    player.addEventListener("loadedmetadata", seek, { once: true });
    return () => player.removeEventListener("loadedmetadata", seek);
  }, [videoPreview?.targetSeconds, videoPreview?.url]);

  useEffect(() => {
    const preview = videoPreview;
    const frame = preview?.frames[preview.frameIndex];
    if (!preview || !frame) return;
    let cancelled = false;
    setVideoPreview((current) =>
      current && current.result.id === preview.result.id
        ? { ...current, evidence: null, evidenceLoading: true }
        : current,
    );
    void getVideoEvidence(preview.result.video_id, {
      frameId: frame.id,
      seconds: frame.timestamp_ms / 1000,
    })
      .then((evidence) => {
        if (cancelled) return;
        setVideoPreview((current) =>
          current &&
          current.result.id === preview.result.id &&
          current.selectedFrameIdx === frame.frame_idx
            ? { ...current, evidence, evidenceLoading: false }
            : current,
        );
      })
      .catch(() => {
        if (cancelled) return;
        setVideoPreview((current) =>
          current &&
          current.result.id === preview.result.id &&
          current.selectedFrameIdx === frame.frame_idx
            ? { ...current, evidenceLoading: false }
            : current,
        );
      });
    return () => {
      cancelled = true;
    };
  }, [
    videoPreview?.result.id,
    videoPreview?.frameIndex,
    videoPreview?.selectedFrameIdx,
    videoPreview?.selectedTimestampMs,
  ]);

  useEffect(() => {
    let cancelled = false;
    listDatasets()
      .then((items) => {
        if (cancelled) return;
        const next = items;
        setDatasets(next);
        setDatasetId((current) => current || next[0]?.id || "");
        if (items.length === 0) setStatus("No datasets available");
      })
      .catch(() => {
        if (cancelled) return;
        setDatasets([]);
        setDatasetId("");
        setGalleryFrames([]);
        setStatus("Cannot load datasets. Check the API connection and retry.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!datasetId) return;
    let cancelled = false;
    setGalleryLoading(true);
    listFrames({ datasetId, limit: 48, offset: 0, presentOnly: true })
      .then((payload) => {
        if (cancelled) return;
        setGalleryFrames(payload.frames);
      })
      .catch(() => {
        if (cancelled) return;
        setGalleryFrames([]);
        setStatus("Cannot load frames. Check the API connection and retry.");
      })
      .finally(() => {
        if (!cancelled) setGalleryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [datasetId]);

  useEffect(() => {
    const element = composerRef.current;
    if (!element) return;
    element.style.height = "0px";
    const nextHeight = Math.min(Math.max(element.scrollHeight, 44), 220);
    element.style.height = `${nextHeight}px`;
  }, [attachedFiles.length, mode, queryText]);

  const activeDataset = useMemo(
    () =>
      datasets.find((item) => item.id === datasetId) ??
      datasets[0] ??
      mockDatasets[0],
    [datasets, datasetId],
  );

  const galleryResults = useMemo(
    () => galleryFrames.map((frame, index) => frameToResult(frame, index + 1)),
    [galleryFrames],
  );

  const visibleResults = useMemo(() => {
    const source = hasSearched
      ? results
      : queryType === "VIDEO"
        ? []
        : galleryResults;
    const needle = resultFilter.trim().toLowerCase();
    if (!needle) return source;
    return source.filter((result) => {
      const frameText = [
        result.video_code,
        result.frame_idx,
        result.answer,
        ...result.sequence_frames.map((frame) => frame.frame_idx),
      ]
        .join(" ")
        .toLowerCase();
      return frameText.includes(needle);
    });
  }, [galleryResults, hasSearched, queryType, resultFilter, results]);

  const selectedKeys = useMemo(() => new Set(selected.map(rowKey)), [selected]);
  const selectedTrakeVideoCode =
    trakeFrameChoices[0]?.result.video_code ?? null;
  const sortedTrakeFrameChoices = useMemo(
    () =>
      [...trakeFrameChoices].sort(
        (left, right) => left.eventIndex - right.eventIndex,
      ),
    [trakeFrameChoices],
  );
  const canAddTrakeSequence =
    sortedTrakeFrameChoices.length === trakeEventCount &&
    new Set(sortedTrakeFrameChoices.map((choice) => choice.result.video_code))
      .size === 1 &&
    sortedTrakeFrameChoices.every(
      (choice, index, choices) =>
        index === 0 ||
        choices[index - 1].frame.frame_idx < choice.frame.frame_idx,
    );

  // Functions
  function selectionKeyForResult(result: SearchResult): string {
    return rowKey({
      query_name: queryName,
      query_type: queryType === "VIDEO" ? "KIS" : queryType,
      rank: 0,
      video_code: result.video_code,
      frame_indices:
        queryType === "TRAKE" && result.sequence_frames.length > 0
          ? uniqueFrameIndices(
              result.sequence_frames.map((item) => item.frame_idx),
            )
          : result.frame_idx === null
            ? []
            : [result.frame_idx],
      answer: queryType === "QA" ? (result.answer ?? "") : null,
    });
  }

  function changeType(type: SearchTask) {
    cancelSearch();
    drafts.current[queryType] = currentDraft();
    const draft = drafts.current[type];
    if (draft) applyDraft(draft);
    else applyDraft({...currentDraft(), queryType: type, queryName: queryNameByType[type],
      exportFileName: queryNameByType[type], queryText: sampleQueries[type]});
    setResults([]);
    setHasSearched(false);
    setContext(null);
    setTrakeFrameChoices([]);
    setActiveResultId(null);
    setAutoTrace(makeAgentTrace(mode, type, 0));
  }

  function switchMode(nextMode: AppMode) {
    cancelSearch();
    setMode(nextMode);
    setIntelligenceOpen(false);
    if (
      nextMode === "Chat" &&
      Object.values(sampleQueries).includes(queryText)
    ) {
      setQueryText("");
      return;
    }
    if (nextMode !== "Chat" && !queryText.trim()) {
      setQueryText(sampleQueries[queryType]);
    }
  }

  function rememberSearch(
    nextResults: SearchResult[],
    trace: AgentStep[],
    searchText = queryText,
  ) {
    const createdAt = new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    const item: SearchHistoryItem = {
      id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      mode,
      queryType,
      queryName,
      queryText: searchText,
      resultCount: nextResults.length,
      createdAt,
      results: nextResults,
      trace,
      datasetId, draft: currentDraft(),
    };
    setHistory((current) => [item, ...current].slice(0, 12));
  }

  async function openFrameContext(result: SearchResult) {
    if (!result.frame_id) return;
    contextRequests.current.cancel();
    const request = contextRequests.current.begin()!;
    setActiveResultId(result.id);
    setContext(null);
    try {
      const next = await getFrameContext(result.frame_id, request.signal);
      if (contextRequests.current.isCurrent(request)) setContext(next);
    } catch {
      if (contextRequests.current.isCurrent(request)) setStatus("Cannot load frame context. Try opening the frame again.");
    } finally {
      contextRequests.current.finish(request);
    }
  }

  function addResult(result: SearchResult) {
    const frameIndices =
      queryType === "TRAKE" && result.sequence_frames.length > 0
        ? uniqueFrameIndices(
            result.sequence_frames.map((item) => item.frame_idx),
          )
        : result.frame_idx === null
          ? []
          : [result.frame_idx];

    if (frameIndices.length === 0) {
      setStatus("Frame index missing");
      return;
    }

    const row: SubmissionRow = {
      query_name: queryName,
      query_type: queryType === "VIDEO" ? "KIS" : queryType,
      rank: selected.filter((item) => item.query_name === queryName).length + 1,
      video_code: result.video_code,
      frame_indices: frameIndices,
      answer: queryType === "QA" ? (result.answer ?? "") : null,
    };

    setSelected((current) => {
      if (current.some((item) => rowKey(item) === rowKey(row))) return current;
      return normalizeRanks([...current, row]).slice(0, 100);
    });
  }

  function isTrakeFramePicked(
    result: SearchResult,
    frame: TrakeSequenceFrame,
    eventIndex: number,
  ) {
    return trakeFrameChoices.some(
      (choice) =>
        choice.eventIndex === eventIndex &&
        choice.result.video_code === result.video_code &&
        choice.frame.frame_idx === frame.frame_idx,
    );
  }

  function pickTrakeFrame(
    result: SearchResult,
    frame: TrakeSequenceFrame,
    eventIndex: number,
  ) {
    setTrakeFrameChoices((current) => {
      const sameChoice = current.some(
        (choice) =>
          choice.eventIndex === eventIndex &&
          choice.result.video_code === result.video_code &&
          choice.frame.frame_idx === frame.frame_idx,
      );
      if (sameChoice) {
        setStatus(`E${eventIndex} already uses frame ${frame.frame_idx}`);
        return current;
      }

      const remainingChoices = current.filter(
        (choice) => choice.eventIndex !== eventIndex,
      );
      const selectedVideoCode = remainingChoices[0]?.result.video_code;
      if (selectedVideoCode && selectedVideoCode !== result.video_code) {
        setStatus(`TRAKE frames must belong to ${selectedVideoCode}`);
        return current;
      }

      setStatus(`Set E${eventIndex} to frame ${frame.frame_idx}`);
      return [...remainingChoices, { eventIndex, result, frame }];
    });
  }

  function addTrakeSequence() {
    if (trakeFrameChoices.length !== trakeEventCount) {
      setStatus(`Pick a frame for all ${trakeEventCount} TRAKE events`);
      return;
    }
    if (!canAddTrakeSequence) {
      setStatus("TRAKE frames must be from one video and ordered by time");
      return;
    }

    const firstChoice = sortedTrakeFrameChoices[0];
    if (!firstChoice) return;
    const row: SubmissionRow = {
      query_name: queryName,
      query_type: "TRAKE",
      rank: selected.filter((item) => item.query_name === queryName).length + 1,
      video_code: firstChoice.result.video_code,
      frame_indices: sortedTrakeFrameChoices.map(
        (choice) => choice.frame.frame_idx,
      ),
      answer: null,
    };
    setSelected((current) => {
      if (current.some((item) => rowKey(item) === rowKey(row))) return current;
      return normalizeRanks([...current, row]).slice(0, 100);
    });
    setTrakeFrameChoices([]);
    setStatus(`Added TRAKE sequence for ${row.video_code}`);
  }

  function removeSelected(key: string) {
    setSelected((current) =>
      normalizeRanks(current.filter((row) => rowKey(row) !== key)),
    );
  }

  async function submitSearch() {
    if (mode === "Chat") {
      submitChat();
      return;
    }
    if (queryType === "VIDEO") {
      await submitVideoLookup();
      return;
    }
    if (!datasetId || !queryText.trim()) return;
    const request = searchRequests.current.begin();
    if (!request) return;
    contextRequests.current.cancel();
    setContext(null);
    setLoading(true);
    setHasSearched(true);
    if (queryType === "TRAKE") setTrakeFrameChoices([]);
    setStatus(mode === "Auto" && autoEnabled ? "Auto running" : "Searching");
    const runningTrace = makeAgentTrace(mode, queryType, 0).map(
      (step, index) => ({
        ...step,
        status: index <= 1 ? ("running" as const) : ("queued" as const),
      }),
    );
    setAutoTrace(runningTrace);

    const searchInput = {
      datasetId,
      queryType,
      queryName,
      queryText,
      topK,
      useExpansion,
      useAgentPlanning,
      agentModel: reasoningModel,
      useMetadata,
      temporalMode: queryType === "KIS" && kisTemporalMode,
      temporalStrategy,
      visualSearchMode,
    };

    try {
      const shouldShowPlanEarly =
        useAgentPlanning ||
        queryType === "TRAKE" ||
        (queryType === "KIS" && kisTemporalMode);
      if (shouldShowPlanEarly) {
        try {
          const planned = await planSearch(searchInput, request.signal);
          if (!searchRequests.current.isCurrent(request)) return;
          const planningTrace = makeTraceFromResponse(
            mode,
            queryType,
            {
              query_run_id: "planning",
              query_type: queryType,
              query_name: queryName,
              normalized_query: planned.normalized_query,
              results: [],
            },
            0,
          ).map((step) =>
            step.title === "Retrieve candidates"
              ? {
                  ...step,
                  detail: "LLM reasoning is ready. Retrieving matching frames.",
                  status: "running" as const,
                }
              : step,
          );
          setAutoTrace(planningTrace);
        } catch {
          if (!searchRequests.current.isCurrent(request)) return;
          setAutoTrace((current) =>
            current.map((step) =>
              step.title === "Parse query"
                ? {
                    ...step,
                    detail:
                      "Planning is unavailable. Continuing with retrieval.",
                    status: "warning" as const,
                  }
                : step,
            ),
          );
        }
      }

      const response = await runSearch(searchInput, request.signal);
      if (!searchRequests.current.isCurrent(request)) return;
      const nextResults = diversifyResultsForDisplay(
        response.results,
        queryType,
      );
      if (queryType === "TRAKE") {
        setTrakeEventCount(response.normalized_query.temporal_event_count ?? 4);
      }
      setResults(nextResults);
      setStatus(
        nextResults.length > 0
          ? `${nextResults.length} results`
          : queryType === "TRAKE"
            ? "No complete ordered sequence found"
            : "No results found",
      );
      const trace = makeTraceFromResponse(
        mode,
        queryType,
        response,
        nextResults.length,
      );
      setAutoTrace(trace);
      rememberSearch(nextResults, trace);
      if (nextResults[0]) void openFrameContext(nextResults[0]);
    } catch (error) {
      if (!searchRequests.current.isCurrent(request)) return;
      const nextResults: SearchResult[] = [];
      const trace = makeAgentTrace(mode, queryType, 0).map((step, index) =>
        index === 0
          ? {
              ...step,
              detail:
                error instanceof Error
                  ? `Search API fallback: ${error.message.slice(0, 96)}`
                  : "Search API fallback.",
              status: "warning" as const,
            }
          : step,
      );
      setResults(nextResults);
      setStatus(
        error instanceof Error
          ? `Search failed: ${error.message.slice(0, 64)}`
          : "Search failed",
      );
      setAutoTrace(trace);
      rememberSearch(nextResults, trace);
      setContext(null);
    } finally {
      if (searchRequests.current.finish(request)) setLoading(false);
    }
  }

  async function submitVideoLookup() {
    const videoCode = videoCodeQuery.trim();
    const parsedFrame = Number(videoFrameQuery);
    const frameIdx =
      videoFrameQuery.trim() &&
      Number.isInteger(parsedFrame) &&
      parsedFrame >= 0
        ? parsedFrame
        : null;
    if (!datasetId || !videoCode) {
      setStatus("Enter a video code");
      return;
    }

    const request = searchRequests.current.begin();
    if (!request) return;
    contextRequests.current.cancel();
    setLoading(true);
    setHasSearched(true);
    setResults([]);
    setContext(null);
    setActiveResultId(null);
    setStatus("Finding indexed frames");
    setAutoTrace(
      makeVideoLookupTrace(videoCode, frameIdx, 0).map((step, index) => ({
        ...step,
        status: index < 2 ? ("running" as const) : step.status,
      })),
    );

    try {
      const response = await listFrames({
        datasetId,
        videoCode,
        frameIdx: frameIdx ?? undefined,
        limit:
          frameIdx === null
            ? Math.min(topK, 200)
            : Math.min(Math.max(topK, 12), 200),
        offset: 0,
        presentOnly: true,
      }, request.signal);
      if (!searchRequests.current.isCurrent(request)) return;
      const nextResults = response.frames.map((frame, index) =>
        frameToResult(frame, index + 1),
      );
      const trace = makeVideoLookupTrace(
        videoCode,
        frameIdx,
        nextResults.length,
      );
      setResults(nextResults);
      setQueryText(videoCode);
      setAutoTrace(trace);
      setStatus(
        nextResults.length > 0
          ? `${nextResults.length} indexed frames`
          : "No indexed frames match this video",
      );
      rememberSearch(nextResults, trace, videoCode);
      if (nextResults[0]) void openFrameContext(nextResults[0]);
    } catch (error) {
      if (!searchRequests.current.isCurrent(request)) return;
      const detail =
        error instanceof Error
          ? error.message.slice(0, 64)
          : "Video lookup failed";
      const trace = makeVideoLookupTrace(videoCode, frameIdx, 0).map(
        (step, index) =>
          index === 0 ? { ...step, detail, status: "warning" as const } : step,
      );
      setAutoTrace(trace);
      setStatus(`Video lookup failed: ${detail}`);
      rememberSearch([], trace, videoCode);
    } finally {
      if (searchRequests.current.finish(request)) setLoading(false);
    }
  }

  async function submitChat() {
    const prompt = queryText.trim();
    if (!prompt && attachedFiles.length === 0) return;
    const request = searchRequests.current.begin();
    if (!request) return;
    contextRequests.current.cancel();
    const fileNames = attachedFiles.map((file) => file.name);
    const fallbackTrace = makeAgentTrace(
      "Chat",
      "QA",
      fileNames.length > 0 ? fileNames.length : 4,
      reasoningModel,
    );
    const messageId = Date.now();
    setChatMessages((current) => [
      ...current,
      {
        id: `user-${messageId}`,
        role: "user",
        text: prompt || "Uploaded video for QA",
        files: fileNames,
      },
    ]);
    setQueryText("");
    setAttachedFiles([]);
    if (!datasetId || !prompt) {
      setChatMessages((current) => [
        ...current,
        {
          id: `assistant-${messageId}`,
          role: "assistant",
          text: `${reasoningModelLabels[reasoningModel]} is selected. Enter a message and select a dataset to run QA retrieval.`,
          trace: fallbackTrace,
        },
      ]);
      searchRequests.current.finish(request);
      setStatus("Chat model selected");
      return;
    }

    setLoading(true);
    setStatus(`Reasoning with ${reasoningModelLabels[reasoningModel]}`);
    try {
      const response = await runSearch({
        datasetId,
        queryType: "QA",
        queryName,
        queryText: prompt,
        topK,
        useExpansion,
        useAgentPlanning: true,
        agentModel: reasoningModel,
        useMetadata,
        temporalMode: false,
        temporalStrategy,
        visualSearchMode,
      }, request.signal);
      if (!searchRequests.current.isCurrent(request)) return;
      const nextResults = diversifyResultsForDisplay(response.results, "QA");
      setResults(nextResults);
      setHasSearched(true);
      const trace = makeTraceFromResponse(
        "Chat",
        "QA",
        response,
        nextResults.length,
      );
      const answer = nextResults[0]?.answer?.trim();
      setChatMessages((current) => [
        ...current,
        {
          id: `assistant-${messageId}`,
          role: "assistant",
          text:
            answer ||
            `Found ${nextResults.length} evidence frame${nextResults.length === 1 ? "" : "s"}.`,
          trace,
        },
      ]);
      if (nextResults[0]) void openFrameContext(nextResults[0]);
      setStatus(
        answer ? "QA answer ready" : `${nextResults.length} evidence frames`,
      );
    } catch (error) {
      if (!searchRequests.current.isCurrent(request)) return;
      const detail =
        error instanceof Error
          ? error.message.slice(0, 96)
          : "QA request failed";
      setChatMessages((current) => [
        ...current,
        {
          id: `assistant-${messageId}`,
          role: "assistant",
          text: `${reasoningModelLabels[reasoningModel]} could not complete the QA request: ${detail}`,
          trace: fallbackTrace.map((step, index) =>
            index === 0 ? { ...step, detail, status: "warning" } : step,
          ),
        },
      ]);
      setStatus("Chat request failed");
    } finally {
      if (searchRequests.current.finish(request)) setLoading(false);
    }
  }

  function restoreHistory(item: SearchHistoryItem) {
    cancelSearch();
    drafts.current[queryType] = currentDraft();
    if (item.draft) applyDraft(item.draft);
    if (item.datasetId) setDatasetId(item.datasetId);
    setMode(item.mode);
    setQueryType(item.queryType);
    setQueryName(item.queryName);
    setQueryText(item.queryText);
    if (item.queryType === "VIDEO") setVideoCodeQuery(item.queryText);
    setResults(item.results);
    setHasSearched(true);
    setAutoTrace(item.trace);
    if (item.results[0]) void openFrameContext(item.results[0]);
  }

  async function openVideoPreview(
    result: SearchResult,
    trakeEventIndex: number | null = null,
  ) {
    previewRequests.current.cancel();
    const request = previewRequests.current.begin()!;
    let videoUrl = firstMediaUrl(
      result.video_url,
      `/api/media/videos/${result.video_id}/preview`,
    );
    setStatus("Opening video");
    try {
      const preview = await getVideoPreviewUrl(result.video_id);
      videoUrl = mediaUrl(preview.url) ?? preview.url;
    } catch {
      // Fall back to the legacy preview redirect below.
    }
    if (!previewRequests.current.finish(request)) return;
    if (!videoUrl) {
      setStatus("Video preview unavailable");
      return;
    }
    const fallbackFrame = contextFrameFromResult(result);
    const initialFrames = fallbackFrame ? [fallbackFrame] : [];
    const initialTimestamp = fallbackFrame?.timestamp_ms ?? result.timestamp_ms;
    const frameLabel = fallbackFrame
      ? `frame ${fallbackFrame.frame_idx}`
      : "sequence";
    const initialSeconds = Math.max(0, (initialTimestamp ?? 0) / 1000);
    setVideoEvidenceOpen(hasTemporalEventMatches(result));
    setVideoPreview({
      title: result.video_code,
      subtitle: `${frameLabel} | ${timestampLabel(initialTimestamp)}`,
      url: videoUrl,
      posterUrl: resultImageCandidates(result)[0] ?? null,
      result,
      frames: initialFrames,
      frameIndex: 0,
      loadingFrames: Boolean(result.frame_id),
      targetSeconds: initialSeconds,
      selectedFrameIdx: fallbackFrame?.frame_idx ?? result.frame_idx,
      selectedTimestampMs: initialTimestamp ?? 0,
      trakeEventIndex,
      evidence: null,
      evidenceLoading: true,
    });
    setStatus("Video ready");
    if (!result.frame_id) return;
    try {
      const nextContext = await getFrameContext(result.frame_id);
      const targetIndex = Math.max(
        0,
        nextContext.frames.findIndex(
          (frame) => frame.id === nextContext.target_frame_id,
        ),
      );
      const activeFrame = nextContext.frames[targetIndex] ?? fallbackFrame;
      setVideoPreview((current) => {
        if (!current || current.result.id !== result.id) return current;
        return {
          ...current,
          frames: nextContext.frames,
          frameIndex: targetIndex,
          loadingFrames: false,
          subtitle: activeFrame
            ? `frame ${activeFrame.frame_idx} | ${timestampLabel(activeFrame.timestamp_ms)}`
            : current.subtitle,
          targetSeconds: Math.max(
            0,
            (activeFrame?.timestamp_ms ?? initialTimestamp ?? 0) / 1000,
          ),
          posterUrl:
            (activeFrame ? contextImageCandidates(activeFrame)[0] : null) ??
            current.posterUrl,
        };
      });
    } catch {
      setVideoPreview((current) =>
        current && current.result.id === result.id
          ? { ...current, loadingFrames: false }
          : current,
      );
    }
  }

  function applyVideoPreviewFrame(
    resultId: string,
    frame: ContextFrame,
    selection: { frame_idx: number; timestamp_ms: number },
    targetSeconds = selection.timestamp_ms / 1000,
  ) {
    setVideoPlaybackSeconds(targetSeconds);
    setVideoPreview((current) => {
      if (!current || current.result.id !== resultId) return current;
      return {
        ...current,
        frames: [frame],
        frameIndex: 0,
        loadingFrames: false,
        subtitle: `frame ${frame.frame_idx} | ${timestampLabel(frame.timestamp_ms)}`,
        targetSeconds: Math.max(0, targetSeconds),
        selectedFrameIdx: selection.frame_idx,
        selectedTimestampMs: selection.timestamp_ms,
        posterUrl: contextImageCandidates(frame)[0] ?? current.posterUrl,
      };
    });
  }

  async function selectCurrentVideoFrame() {
    const current = videoPreview;
    const seconds = videoPreviewRef.current?.currentTime;
    if (
      !current ||
      seconds === undefined ||
      !Number.isFinite(seconds) ||
      seconds < 0
    ) {
      setStatus("Video time is not ready yet");
      return;
    }
    setVideoPreview((preview) =>
      preview && preview.result.id === current.result.id
        ? { ...preview, loadingFrames: true }
        : preview,
    );
    setStatus("Resolving the current video frame");
    try {
      const resolved = await seekVideoFrame({
        videoId: current.result.video_id,
        seconds,
      });
      applyVideoPreviewFrame(
        current.result.id,
        resolved.frame,
        resolved.selection,
        seconds,
      );
      setStatus(`Frame ${resolved.selection.frame_idx} selected`);
    } catch {
      setVideoPreview((preview) =>
        preview && preview.result.id === current.result.id
          ? { ...preview, loadingFrames: false }
          : preview,
      );
      setStatus("No indexed frame is available at the current video time");
    }
  }

  async function shiftVideoPreview(delta: number) {
    const current = videoPreview;
    const frame = videoPreviewFrame;
    if (!current || !frame || current.loadingFrames) return;
    const direction = delta < 0 ? "previous" : "next";
    setVideoPreview((preview) =>
      preview && preview.result.id === current.result.id
        ? { ...preview, loadingFrames: true }
        : preview,
    );
    setStatus(`Loading ${direction} indexed frame`);
    try {
      const resolved = await seekVideoFrame({
        videoId: current.result.video_id,
        frameIdx: frame.frame_idx,
        direction,
      });
      applyVideoPreviewFrame(
        current.result.id,
        resolved.frame,
        resolved.selection,
      );
      setStatus(`Frame ${resolved.selection.frame_idx} selected`);
    } catch {
      setVideoPreview((preview) =>
        preview && preview.result.id === current.result.id
          ? { ...preview, loadingFrames: false }
          : preview,
      );
      setStatus(`No ${direction} indexed frame is available`);
    }
  }

  function addVideoPreviewFrame() {
    if (!videoPreview || videoPreview.selectedFrameIdx === null) return;
    const frame = videoPreview.frames[videoPreview.frameIndex];
    if (!frame) {
      setStatus("Frame index missing");
      return;
    }
    if (videoPreview.trakeEventIndex !== null) {
      const trakeFrame: TrakeSequenceFrame = {
        frame_id: frame.id,
        frame_idx: videoPreview.selectedFrameIdx,
        video_code: videoPreview.result.video_code,
        timestamp_ms: videoPreview.selectedTimestampMs,
        score: videoPreview.result.score,
        visual_score:
          scoreFromBreakdown(videoPreview.result, [
            "semantic_score",
            "visual_score",
            "semantic",
            "visual",
          ]) ?? undefined,
        text_score:
          scoreFromBreakdown(videoPreview.result, [
            "text_score",
            "metadata_score",
            "text",
          ]) ?? undefined,
        rrf_score:
          scoreFromBreakdown(videoPreview.result, ["rrf_score", "rrf"]) ??
          undefined,
        event_index: videoPreview.trakeEventIndex,
        thumbnail_url: frame.thumbnail_url,
        image_url: frame.image_url ?? null,
        image_uri: frame.image_uri ?? null,
        image_storage_key: frame.image_storage_key ?? null,
      };
      pickTrakeFrame(
        videoPreview.result,
        trakeFrame,
        videoPreview.trakeEventIndex,
      );
      return;
    }
    const row: SubmissionRow = {
      query_name: queryName,
      query_type: queryType === "VIDEO" ? "KIS" : queryType,
      rank: selected.filter((item) => item.query_name === queryName).length + 1,
      video_code: videoPreview.result.video_code,
      frame_indices: [videoPreview.selectedFrameIdx],
      answer: queryType === "QA" ? (videoPreview.result.answer ?? "") : null,
    };
    setSelected((current) => {
      if (current.some((item) => rowKey(item) === rowKey(row))) return current;
      return normalizeRanks([...current, row]).slice(0, 100);
    });
    setStatus(
      `Added ${videoPreview.result.video_code} frame ${videoPreview.selectedFrameIdx}`,
    );
  }

  async function exportSubmission() {
    if (!datasetId || selected.length === 0 || exportInFlight.current) return;
    exportInFlight.current = true;
    setStatus("Exporting");
    try {
      const csvName = csvDownloadName(exportFileName || "submission");
      const exported = await createAndExportSubmission(datasetId, csvName.replace(/\.csv$/i, ""), selected);
      await downloadCsvFromUrl(exported.downloadUrl, csvName);
      setStatus("CSV downloaded");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Export failed");
      console.warn("Export failed; no unvalidated CSV was downloaded.", error);
    } finally {
      exportInFlight.current = false;
    }
  }

  function newSession() {
    const name = newQueryName(queryType);
    setQueryName(name); setExportFileName(name);
    cancelSearch();
    setQueryText(sampleQueries[queryType]);
    setResults([]);
    setHasSearched(false);
    setContext(null);
    setActiveResultId(null);
    setAttachedFiles([]);
    setStatus("Ready");
    setAutoTrace(makeAgentTrace(mode, queryType, 0));
  }

  function updateWeight(key: keyof typeof weights, value: number) {
    setWeights((current) => ({ ...current, [key]: clampNumber(value, 0, 1) }));
  }

  function updateFrameColumns(value: number) {
    setFrameColumns(Math.round(clampNumber(value, 1, 8)));
  }

  function updateTopK(value: number) {
    setTopK(Math.round(clampNumber(value, 1, 100)));
  }

  const stableOpen = useStableEvent((result: SearchResult) => void openFrameContext(result));
  const stablePick = useStableEvent((result: SearchResult) => addResult(result));
  const stablePreview = useStableEvent((result: SearchResult) => void openVideoPreview(result));
  const resultKey = useCallback((result: SearchResult) => selectionKeyForResult(result), [queryName, queryType]);

  const videoPreviewFrame =
    videoPreview?.frames[videoPreview.frameIndex] ?? null;
  const videoPreviewCanStepBack = Boolean(
    videoPreview && videoPreviewFrame && !videoPreview.loadingFrames,
  );
  const videoPreviewCanStepForward = Boolean(
    videoPreview && videoPreviewFrame && !videoPreview.loadingFrames,
  );

  const modeCaption =
    mode === "Search" && hasSearched && !loading
      ? `${visibleResults.length} frames`
      : "";

  return (
    <main
      className={`chat-shell ${leftSidebarOpen ? "" : "left-collapsed"} ${rightSidebarOpen ? "" : "right-collapsed"}`}
    >
      <aside className="left-sidebar">
        <div className="brand-row">
          <div className="brand-mark">CS</div>
          <strong>ChatShasimi</strong>
          {/* {Chat} */}
        </div>

        <button type="button" className="new-button" onClick={newSession}>
          New search
        </button>

        <nav className="task-nav" aria-label="Query type">
          {(Object.keys(queryLabels) as SearchTask[]).map((type) => (
            <button
              type="button"
              key={type}
              className={`task-item ${queryType === type ? "active" : ""}`}
              onClick={() => changeType(type)}
            >
              <span>{queryLabels[type].title}</span>
              <small>{queryLabels[type].caption}</small>
            </button>
          ))}
        </nav>

        <section className="history-section">
          <div className="sidebar-label">History</div>
          <div className="history-list">
            {history.length === 0 ? (
              <p className="empty-note">No searches yet</p>
            ) : (
              history.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className="history-item"
                  onClick={() => restoreHistory(item)}
                >
                  <span>{item.queryName}</span>
                  <small>
                    {item.createdAt} | {item.resultCount} results
                  </small>
                </button>
              ))
            )}
          </div>
        </section>

        <div className="sidebar-footer">
          <button
            type="button"
            className="settings-entry"
            onClick={() => setSettingsOpen((open) => !open)}
          >
            Settings
          </button>
        </div>
      </aside>

      <section className="main-pane">
        <header className="topbar">
          <button
            type="button"
            className="icon-button sidebar-toggle"
            onClick={() => {
              setLeftSidebarOpen((open) => !open);
              setIntelligenceOpen(false);
              setSettingsOpen(false);
            }}
            aria-label={
              leftSidebarOpen ? "Close left sidebar" : "Open left sidebar"
            }
          >
            <PanelLeft size={18} />
          </button>
          <div className="mode-switch" role="tablist" aria-label="Mode">
            {(["Search", "Auto", "Chat"] as AppMode[]).map((item) => (
              <button
                type="button"
                role="tab"
                aria-selected={mode === item}
                className={mode === item ? "active" : ""}
                key={item}
                onClick={() => switchMode(item)}
              >
                {item}
              </button>
            ))}
          </div>
          <div className="topbar-actions">
            <button
              type="button"
              className="icon-button sidebar-toggle"
              onClick={() => {
                setRightSidebarOpen((open) => !open);
                setIntelligenceOpen(false);
                setSettingsOpen(false);
              }}
              aria-label={
                rightSidebarOpen
                  ? "Close selected frames sidebar"
                  : "Open selected frames sidebar"
              }
            >
              <PanelRight size={18} />
            </button>
          </div>
        </header>

        <div className="content-scroll">
          <div className="workspace-toolbar">
            <div className="workspace-context">
              <strong>{queryLabels[queryType].title}</strong>
              {modeCaption && <span>{modeCaption}</span>}
            </div>
            <div className="workspace-controls">
              {/* <label className="compact-field">
                Dataset
                <select
                  value={datasetId}
                  onChange={(event) => setDatasetId(event.target.value)}
                >
                  {datasets.map((dataset) => (
                    <option key={dataset.id} value={dataset.id}>
                      {dataset.name}
                    </option>
                  ))}
                </select>
              </label> */}
              {queryType === "VIDEO" && mode !== "Chat" && (
                <>
                  <label className="compact-field video-code-field">
                    Video name / code
                    <input
                      value={videoCodeQuery}
                      onChange={(event) =>
                        setVideoCodeQuery(event.target.value)
                      }
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          void submitVideoLookup();
                        }
                      }}
                      placeholder="L26_V194"
                    />
                  </label>
                  <label className="compact-field video-frame-field">
                    Frame index
                    <input
                      type="number"
                      min={0}
                      step={1}
                      value={videoFrameQuery}
                      onChange={(event) =>
                        setVideoFrameQuery(event.target.value)
                      }
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          void submitVideoLookup();
                        }
                      }}
                      placeholder="Optional"
                    />
                  </label>
                </>
              )}
            </div>
          </div>

          {/* {mode !== "Chat" && (
            <div className="filter-row">
              <div className="filter-input">
                <Search size={15} />
                <input
                  value={resultFilter}
                  onChange={(event) => setResultFilter(event.target.value)}
                  placeholder="Filter video, frame, answer"
                />
              </div>
              {!loading && <span>{visibleResults.length} shown</span>}
            </div>
          )} */}

          {mode === "Search" && (
            <section className="frame-section">
              {(loading || hasSearched) && (
                <ReasoningDisclosure steps={autoTrace} />
              )}
              {loading ? (
                <SearchLoadingStage frameColumns={frameColumns} />
              ) : galleryLoading && !hasSearched ? (
                <div className="skeleton-grid">
                  {Array.from({ length: 12 }).map((_, index) => (
                    <div className="skeleton-card" key={index} />
                  ))}
                </div>
              ) : hasSearched && visibleResults.length === 0 ? (
                <p className="empty-note search-empty-note">
                  {queryType === "TRAKE"
                    ? "No complete ordered sequence matches this query."
                    : "No frames match this query."}
                </p>
              ) : queryType === "TRAKE" && hasSearched ? (
                <TrakeRows
                  results={visibleResults}
                  eventCount={trakeEventCount}
                  isSelected={isTrakeFramePicked}
                  onOpen={(result) => void openFrameContext(result)}
                  onSelect={pickTrakeFrame}
                  onPreview={(result, eventIndex) =>
                    void openVideoPreview(result, eventIndex ?? null)
                  }
                  selectedFrames={sortedTrakeFrameChoices}
                  selectedEventCount={trakeFrameChoices.length}
                  selectedVideoCode={selectedTrakeVideoCode}
                  canAddSequence={canAddTrakeSequence}
                  onAddSequence={addTrakeSequence}
                  onClearSelection={() => setTrakeFrameChoices([])}
                />
              ) : (
                <ResultGrid results={visibleResults} columns={frameColumns} selectedKeys={selectedKeys}
                  keyFor={resultKey} onOpen={stableOpen} onPick={stablePick} onPreview={stablePreview} />
              )}
            </section>
          )}

          {mode === "Auto" && (
            <section className="auto-section">
              {autoEnabled ? (
                <>
                  <ReasoningDisclosure steps={autoTrace} />
                  {loading ? (
                    <SearchLoadingStage frameColumns={frameColumns} />
                  ) : hasSearched && visibleResults.length === 0 ? (
                    <p className="empty-note search-empty-note">
                      {queryType === "TRAKE"
                        ? "No complete ordered sequence matches this query."
                        : "No frames match this query."}
                    </p>
                  ) : queryType === "TRAKE" && hasSearched ? (
                    <TrakeRows
                      results={visibleResults}
                      eventCount={trakeEventCount}
                      isSelected={isTrakeFramePicked}
                      onOpen={(result) => void openFrameContext(result)}
                      onSelect={pickTrakeFrame}
                      onPreview={(result, eventIndex) =>
                        void openVideoPreview(result, eventIndex ?? null)
                      }
                      selectedFrames={sortedTrakeFrameChoices}
                      selectedEventCount={trakeFrameChoices.length}
                      selectedVideoCode={selectedTrakeVideoCode}
                      canAddSequence={canAddTrakeSequence}
                      onAddSequence={addTrakeSequence}
                      onClearSelection={() => setTrakeFrameChoices([])}
                    />
                  ) : (
                    <ResultGrid className="frame-grid auto-grid" results={visibleResults} columns={frameColumns}
                      selectedKeys={EMPTY_SELECTION} keyFor={resultKey} onOpen={stableOpen}
                      onPick={stablePick} onPreview={stablePreview} />
                  )}
                </>
              ) : loading ? (
                <SearchLoadingStage frameColumns={frameColumns} />
              ) : hasSearched && visibleResults.length === 0 ? (
                <p className="empty-note search-empty-note">
                  {queryType === "TRAKE"
                    ? "No complete ordered sequence matches this query."
                    : "No frames match this query."}
                </p>
              ) : queryType === "TRAKE" && hasSearched ? (
                <TrakeRows
                  results={visibleResults}
                  eventCount={trakeEventCount}
                  isSelected={isTrakeFramePicked}
                  onOpen={(result) => void openFrameContext(result)}
                  onSelect={pickTrakeFrame}
                  onPreview={(result, eventIndex) =>
                    void openVideoPreview(result, eventIndex ?? null)
                  }
                  selectedFrames={sortedTrakeFrameChoices}
                  selectedEventCount={trakeFrameChoices.length}
                  selectedVideoCode={selectedTrakeVideoCode}
                  canAddSequence={canAddTrakeSequence}
                  onAddSequence={addTrakeSequence}
                  onClearSelection={() => setTrakeFrameChoices([])}
                />
              ) : (
                <ResultGrid results={visibleResults} columns={frameColumns} selectedKeys={EMPTY_SELECTION}
                  keyFor={resultKey} onOpen={stableOpen} onPick={stablePick} onPreview={stablePreview} />
              )}
            </section>
          )}

          {mode === "Chat" && (
            <section className="chat-thread" aria-label="Chat messages">
              {chatMessages.map((message) => (
                <article
                  className={`chat-message ${message.role}`}
                  key={message.id}
                >
                  <div className="avatar">
                    {message.role === "assistant" ? "CS" : "You"}
                  </div>
                  <div className="message-body">
                    <p>{message.text}</p>
                    {message.files && message.files.length > 0 && (
                      <div className="file-list">
                        {message.files.map((file) => (
                          <span key={file}>{file}</span>
                        ))}
                      </div>
                    )}
                    {message.trace && (
                      <ReasoningDisclosure steps={message.trace} compact />
                    )}
                  </div>
                </article>
              ))}
            </section>
          )}
        </div>

        <footer className="composer-wrap">
          {attachedFiles.length > 0 && (
            <div className="attachment-row">
              {attachedFiles.map((file) => (
                <span key={`${file.name}-${file.size}`}>
                  {file.name}
                  <button
                    type="button"
                    aria-label={`Remove ${file.name}`}
                    onClick={() =>
                      setAttachedFiles((current) =>
                        current.filter((item) => item !== file),
                      )
                    }
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}
          <div className="composer">
            {queryType === "VIDEO" && mode !== "Chat" ? (
              <div className="video-lookup-composer" aria-live="polite">
                <strong>
                  {videoCodeQuery.trim() || "Video code required"}
                </strong>
                <span>
                  {videoFrameQuery.trim()
                    ? `nearest to F${videoFrameQuery}`
                    : "indexed keyframes"}
                </span>
              </div>
            ) : (
              <textarea
                ref={composerRef}
                rows={1}
                aria-label={mode === "Chat" ? "Chat message" : "Search query"}
                value={queryText}
                onChange={(event) => setQueryText(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void submitSearch();
                  }
                }}
                placeholder={
                  mode === "Chat"
                    ? "Ask about a video or upload one for QA"
                    : "Search frames, events, OCR text, or visual details"
                }
              />
            )}
            <div className="composer-actions">
              <div className="composer-left-actions">
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  className="hidden-file"
                  accept=".txt,.csv,.json,.jpg,.jpeg,.png,.mp4,.mov,.mkv,.zip"
                  onChange={(event) => {
                    setAttachedFiles(Array.from(event.target.files ?? []));
                    event.currentTarget.value = "";
                  }}
                />
                <button
                  type="button"
                  className="icon-button"
                  onClick={() => fileInputRef.current?.click()}
                  aria-label="Attach file"
                >
                  <Paperclip size={17} />
                </button>
                <div className="intelligence-wrap">
                  <button
                    type="button"
                    className="intelligence-button"
                    onClick={() => setIntelligenceOpen((open) => !open)}
                  >
                    <SlidersHorizontal size={16} />
                    Intelligence
                    <ChevronDown size={14} />
                  </button>
                  {intelligenceOpen && (
                    <div className="intelligence-popover">
                      <label>
                        Frames per row
                        <input
                          type="number"
                          min={1}
                          max={8}
                          step={1}
                          value={frameColumns}
                          onChange={(event) =>
                            updateFrameColumns(Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        Top K
                        <input
                          type="number"
                          min={1}
                          max={100}
                          step={1}
                          value={topK}
                          onChange={(event) =>
                            updateTopK(Number(event.target.value))
                          }
                        />
                      </label>
                      <div
                        className="visual-strategy"
                        aria-label="Visual embedding model"
                      >
                        <label htmlFor="visual-search-mode">Visual</label>
                        <select
                          id="visual-search-mode"
                          className="strategy-select"
                          value={visualSearchMode}
                          onChange={(event) =>
                            setVisualSearchMode(
                              event.target.value as VisualSearchMode,
                            )
                          }
                        >
                          <option value="openclip">OpenCLIP</option>
                          <option value="siglip2">SigLIP2</option>
                          <option value="both">OpenCLIP + SigLIP2</option>
                        </select>
                      </div>
                      {queryType === "KIS" && (
                        <>
                          <label className="temporal-toggle">
                            <span>Temporal</span>
                            <input
                              type="checkbox"
                              checked={kisTemporalMode}
                              onChange={(event) =>
                                setKisTemporalMode(event.target.checked)
                              }
                            />
                          </label>
                          {kisTemporalMode && (
                            <div
                              className="temporal-strategy"
                              aria-label="Temporal strategy"
                            >
                              <label htmlFor="temporal-strategy">
                                Strategy
                              </label>
                              <select
                                id="temporal-strategy"
                                className="strategy-select"
                                value={temporalStrategy}
                                onChange={(event) =>
                                  setTemporalStrategy(
                                    event.target
                                      .value as typeof temporalStrategy,
                                  )
                                }
                              >
                                <option value="vortex_k_context">
                                  Baseline 1
                                </option>
                                <option value="aithena_weighted_ats">
                                  Baseline 2
                                </option>
                                <option value="dev_first_search">
                                  DEV-first
                                </option>
                              </select>
                            </div>
                          )}
                        </>
                      )}
                      {/* <button
                        type="button"
                        className="reserved-slot"
                        disabled
                        aria-label="Future filter slot"
                      />
                      <button
                        type="button"
                        className="reserved-slot"
                        disabled
                        aria-label="Future reranker slot"
                      /> */}
                    </div>
                  )}
                </div>
                {mode === "Auto" && (
                  <button
                    type="button"
                    className={`auto-toggle ${autoEnabled ? "on" : ""}`}
                    onClick={() => setAutoEnabled((enabled) => !enabled)}
                  >
                    {autoEnabled ? "Turn off" : "Turn on"}
                  </button>
                )}
              </div>
              <div className="composer-submit-actions">
                <label className="reasoning-model-select">
                  <span className="sr-only">Reasoning model</span>
                  <select
                    value={reasoningModel}
                    onChange={(event) =>
                      setReasoningModel(event.target.value as ReasoningModel)
                    }
                    aria-label="Reasoning model"
                  >
                    {(
                      Object.keys(reasoningModelLabels) as ReasoningModel[]
                    ).map((model) => (
                      <option key={model} value={model}>
                        {reasoningModelLabels[model]}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  className="send-button"
                  onClick={() => void submitSearch()}
                  disabled={loading}
                  aria-label={mode === "Chat" ? "Send message" : "Run search"}
                >
                  {loading ? (
                    <Loader2 className="spin-icon" size={18} />
                  ) : mode === "Chat" ? (
                    <Send size={18} />
                  ) : (
                    <Search size={18} />
                  )}
                </button>
              </div>
            </div>
          </div>
        </footer>
      </section>

      <aside className="right-sidebar">
        <section className="selected-panel">
          <div className="panel-heading">
            <strong>Selected frames</strong>
            <span>{selected.length}/100</span>
          </div>
          <div className="selected-list">
            {selected.length === 0 ? (
              <p className="empty-note">Pick frames for CSV rows</p>
            ) : (
              selected.map((row) => (
                <div className="selected-row" key={rowKey(row)}>
                  <span className="row-rank">{row.rank}</span>
                  <span>
                    <strong>{row.video_code}</strong>
                    <small>
                      {row.query_type} | {row.query_name}
                    </small>
                    <code>
                      {row.frame_indices.join(", ")}
                      {row.answer ? `, ${row.answer}` : ""}
                    </code>
                  </span>
                  <button
                    type="button"
                    className="selected-remove"
                    aria-label="Remove row"
                    onClick={() => removeSelected(rowKey(row))}
                  >
                    <span aria-hidden="true" />
                  </button>
                </div>
              ))
            )}
          </div>
        </section>

        <section className="context-panel">
          <div className="panel-heading">
            <strong>Frame context</strong>
            {/* <span>{context?.video_code ?? activeDataset.name}</span> */}
          </div>
          <div className="context-list">
            {context?.frames.map((frame) => (
              <div
                className={`context-frame ${frame.id === context.target_frame_id ? "target" : ""}`}
                key={frame.id}
              >
                <CloudFrameImage
                  candidates={contextImageCandidates(frame)}
                  alt={`Context frame ${frame.frame_idx}`}
                />
                <span>
                  <strong>Frame {frame.frame_idx}</strong>
                  <small>{timestampLabel(frame.timestamp_ms)}</small>
                </span>
              </div>
            )) ?? <p className="empty-note">Open a frame to see neighbors</p>}
          </div>
        </section>

        <section className="export-panel">
          <label className="export-file-field">
            Query file
            <input
              value={exportFileName}
              onChange={(event) => setExportFileName(event.target.value)}
              placeholder="submission"
              aria-label="Export CSV file name"
            />
          </label>
          <button
            type="button"
            className="export-button"
            disabled={selected.length === 0}
            onClick={() => void exportSubmission()}
          >
            Export CSV
          </button>
        </section>
      </aside>

      {settingsOpen && (
        <div ref={settingsDialogRef} className="settings-popover" role="dialog" aria-label="Settings">
          <div className="panel-heading">
            <strong>Settings</strong>
            <button
              type="button"
              className="icon-button small"
              onClick={() => setSettingsOpen(false)}
              aria-label="Close settings"
            >
              <X size={14} />
            </button>
          </div>
          {/* <div className="settings-block">
            <span>Search coefficients</span>
            {(Object.keys(weights) as Array<keyof typeof weights>).map(
              (key) => (
                <label key={key}>
                  {key}
                  <input
                    type="number"
                    min={0}
                    max={1}
                    step={0.01}
                    value={weights[key]}
                    onChange={(event) =>
                      updateWeight(key, Number(event.target.value))
                    }
                  />
                </label>
              ),
            )}
          </div> */}
          <div className="settings-block toggles">
            <button
              type="button"
              className={useExpansion ? "active" : ""}
              onClick={() => setUseExpansion((value) => !value)}
            >
              Expansion
            </button>
            <button
              type="button"
              className={useMetadata ? "active" : ""}
              onClick={() => setUseMetadata((value) => !value)}
            >
              Metadata
            </button>
            <button
              type="button"
              className={useAgentPlanning ? "active" : ""}
              onClick={() => setUseAgentPlanning((value) => !value)}
            >
              Agent plan
            </button>
          </div>
          <div className="settings-block theme-row">
            <button
              type="button"
              className={theme === "light" ? "active" : ""}
              onClick={() => setTheme("light")}
            >
              Light
            </button>
            <button
              type="button"
              className={theme === "dark" ? "active" : ""}
              onClick={() => setTheme("dark")}
            >
              Dark
            </button>
            <button
              type="button"
              className={theme === "system" ? "active" : ""}
              onClick={() => setTheme("system")}
            >
              System
            </button>
          </div>
        </div>
      )}

      {videoPreview && (
        <div
          className="video-backdrop"
          onClick={(event) => {
            if (event.target === event.currentTarget) closeVideoPreview();
          }}
        >
          <div
            ref={videoDialogRef}
            className={`video-modal ${videoEvidenceOpen ? "evidence-open" : ""}`}
            role="dialog"
            aria-modal="true"
            aria-label="Video preview"
          >
            <div className="video-modal-header">
              <span>
                <strong>{videoPreview.title}</strong>
                <small>{videoPreview.subtitle}</small>
              </span>
              <div className="video-modal-header-actions">
                <button
                  type="button"
                  className={`icon-button ${videoEvidenceOpen ? "active" : ""}`}
                  onClick={() => setVideoEvidenceOpen((open) => !open)}
                  aria-label={
                    videoEvidenceOpen
                      ? "Hide text evidence"
                      : "Show text evidence"
                  }
                  aria-pressed={videoEvidenceOpen}
                  title={
                    videoEvidenceOpen
                      ? "Hide text evidence"
                      : "Show text evidence"
                  }
                >
                  <PanelLeft size={16} />
                </button>
                <button
                  type="button"
                  className="icon-button"
                  onClick={closeVideoPreview}
                  aria-label="Close video"
                >
                  <X size={16} />
                </button>
              </div>
            </div>
            <div className="video-modal-layout">
              {videoEvidenceOpen && (
                <VideoEvidenceSidebar
                  evidence={videoPreview.evidence}
                  loading={videoPreview.evidenceLoading}
                  frameIdx={videoPreview.selectedFrameIdx}
                  result={videoPreview.result}
                />
              )}
              <div className="video-modal-main">
                <video
                  ref={videoPreviewRef}
                  className="video-preview-player"
                  src={videoPreview.url}
                  poster={videoPreview.posterUrl ?? undefined}
                  controls
                  preload="metadata"
                  playsInline
                  onLoadedMetadata={(event) => event.currentTarget.pause()}
                  onTimeUpdate={(event) =>
                    setVideoPlaybackSeconds(event.currentTarget.currentTime)
                  }
                />
                <div className="video-current-frame-actions">
                  {videoPreview.trakeEventIndex !== null && (
                    <label className="trake-video-slot">
                      <span>Sequence slot</span>
                      <select
                        value={videoPreview.trakeEventIndex}
                        onChange={(event) => {
                          const nextIndex = Number.parseInt(
                            event.target.value,
                            10,
                          );
                          if (!Number.isInteger(nextIndex)) return;
                          setVideoPreview((current) =>
                            current
                              ? { ...current, trakeEventIndex: nextIndex }
                              : current,
                          );
                        }}
                      >
                        {Array.from(
                          { length: trakeEventCount },
                          (_, index) => index + 1,
                        ).map((eventIndex) => (
                          <option key={eventIndex} value={eventIndex}>
                            E{eventIndex}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  <button
                    type="button"
                    className="ghost-button"
                    disabled={videoPreview.loadingFrames}
                    onClick={() => void selectCurrentVideoFrame()}
                    title="Select the frame currently shown in the video"
                  >
                    <LocateFixed size={16} />
                    Select current frame
                  </button>
                </div>
                <div className="video-frame-toolbar">
                  <button
                    type="button"
                    className="ghost-button"
                    disabled={!videoPreviewCanStepBack}
                    onClick={() => shiftVideoPreview(-1)}
                  >
                    <ChevronLeft size={16} />
                    Prev
                  </button>
                  <div className="video-frame-current">
                    {videoPreviewFrame ? (
                      <>
                        <strong>
                          Frame{" "}
                          {videoPreview.selectedFrameIdx ??
                            videoPreviewFrame.frame_idx}
                        </strong>
                        <small>
                          {timestampLabel(videoPreview.selectedTimestampMs)}
                          {videoPreview.loadingFrames
                            ? " | loading context"
                            : ""}
                        </small>
                      </>
                    ) : (
                      <>
                        <strong>Sequence</strong>
                        <small>
                          {videoPreview.loadingFrames
                            ? "loading context"
                            : "no frame context"}
                        </small>
                      </>
                    )}
                  </div>
                  <button
                    type="button"
                    className="ghost-button"
                    disabled={!videoPreviewCanStepForward}
                    onClick={() => shiftVideoPreview(1)}
                  >
                    Next
                    <ChevronRight size={16} />
                  </button>
                  <button
                    type="button"
                    className="select-button"
                    disabled={!videoPreviewFrame}
                    onClick={addVideoPreviewFrame}
                  >
                    <Plus size={15} />
                    {videoPreview.trakeEventIndex !== null
                      ? `Add to E${videoPreview.trakeEventIndex} sequence`
                      : "Pick frame"}
                  </button>
                </div>
                <div className="video-score-panel">
                  <ScoreBreakdown result={videoPreview.result} />
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

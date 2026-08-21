import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ImageOff,
  Loader2,
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
  getVideoPreviewUrl,
  listDatasets,
  listFrames,
  mediaUrl,
  runSearch,
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
} from "./types";

type AppMode = "Search" | "Auto" | "Chat";
type ThemeMode = "light" | "dark" | "system";

// Classes for the main app container based on sidebar visibility
interface SearchHistoryItem {
  id: string;
  mode: AppMode;
  queryType: QueryType;
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
  baseUrl: string;
  url: string;
  posterUrl: string | null;
  result: SearchResult;
  frames: ContextFrame[];
  frameIndex: number;
  loadingFrames: boolean;
}

interface MapKeyframeInfo {
  n?: number;
  pts_time?: number | null;
  fps?: number | null;
  frame_idx?: number;
  source_keyframe_id?: string | null;
  resolved_keyframe_id?: string;
  map_path?: string;
}

// Mock data
const sampleQueries: Record<QueryType, string> = {
  KIS: "The clip shows an exhibition program with a royal-style decorative panel, dragon and cloud motifs, and the text PHU XUAN GIA DINH.",
  QA: "Identify the name of the world-famous company whose logo was inspired by a castle in Bavaria, Germany.",
  TRAKE:
    "In a bicycle race, first a cyclist with a pink helmet crosses the finish line, then a cyclist with a blue helmet, then a cyclist with a red helmet.",
};

const queryNameByType: Record<QueryType, string> = {
  KIS: "query-1-kis",
  QA: "query-2-qa",
  TRAKE: "query-3-trake",
};

const queryLabels: Record<QueryType, { title: string; caption: string }> = {
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

function withTimeFragment(url: string, timestampMs: number | null): string {
  if (timestampMs === null) return url;
  const seconds = Math.max(0, timestampMs / 1000);
  const [base] = url.split("#", 1);
  return `${base}#t=${seconds.toFixed(2)}`;
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
    scoreFromBreakdown(result, ["semantic_score", "visual_score", "semantic", "visual"]) ?? 0;
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

function textHitInfo(result: SearchResult): {
  source: string;
  score: number | null;
  time: string | null;
  snippet: string;
} | null {
  const raw = result.score_breakdown.text_hit;
  if (!isRecord(raw)) return null;
  const snippet = String(raw.snippet ?? "").trim();
  const source = String(raw.source_type ?? raw.field ?? "text").trim();
  const score = scoreNumber(raw.score);
  const start = scoreNumber(raw.start_seconds);
  const end = scoreNumber(raw.end_seconds);
  const time =
    start !== null
      ? end !== null && end > start
        ? `${timestampLabel(start * 1000)}-${timestampLabel(end * 1000)}`
        : timestampLabel(start * 1000)
      : null;
  if (!snippet && score === null && !source) return null;
  return { source: source || "text", score, time, snippet };
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

function mapKeyframeInfo(result: SearchResult): MapKeyframeInfo | null {
  const semanticHit = result.score_breakdown.semantic_hit;
  if (!semanticHit || typeof semanticHit !== "object") return null;
  const mapInfo = (semanticHit as { map_keyframe?: unknown }).map_keyframe;
  if (!mapInfo || typeof mapInfo !== "object") return null;
  return mapInfo as MapKeyframeInfo;
}

function semanticHitInfo(result: SearchResult): {
  map_matches_resolved?: boolean;
  resolved_keyframe_id?: string | null;
} | null {
  const semanticHit = result.score_breakdown.semantic_hit;
  if (!semanticHit || typeof semanticHit !== "object") return null;
  return semanticHit as {
    map_matches_resolved?: boolean;
    resolved_keyframe_id?: string | null;
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
    ["query_name", "video_code", "frame_indices", "answer"].map(csvCell).join(","),
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

async function downloadCsvFromUrl(url: string, fileName: string): Promise<void> {
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
  queryType: QueryType,
  resultCount: number,
): AgentStep[] {
  const action = mode === "Auto" ? "Shortlist" : "Answer trace";
  const align =
    queryType === "TRAKE"
      ? "Temporal order checked across event frames."
      : "Frame window checked around top matches.";
  return [
    {
      title: "Parse query",
      detail: `${queryType} format detected and mapped to Codabench output.`,
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
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
  const suffix = values.length > shown.length ? ` +${values.length - shown.length}` : "";
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
  return parts.length > 0 ? parts.join(" / ") : "No structured factors returned.";
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
  const temporalEvents = plan?.temporal_events ?? normalized.temporal_events ?? [];
  const plannedVariants = plan?.variants ?? [];
  const retrievalVariants = normalized.variants ?? [];
  const summary = plan?.summary || response.normalized_query.variants?.[0] || "Query parsed.";
  const traceLabel =
    metadata?.langsmith_trace_enabled && metadata.langsmith_api_key_configured
      ? "LangSmith on"
      : "LangSmith off";

  return [
    {
      title: "Agent profile",
      detail: `${profile} | ${provider}/${model} | ${keyStatus} | ${traceLabel}`,
      status: traceStatus,
      raw: metadata ?? null,
    },
    {
      title: "Decompose query",
      detail: `${plan?.intent ?? queryType} / ${plan?.language ?? normalized.language ?? "auto"}: ${summary}`,
      status: traceStatus,
      raw: plan ?? normalized,
    },
    {
      title: "Search factors",
      detail: summarizeFactors(plan?.decomposition?.search_factors),
      status: traceStatus,
      raw: plan?.decomposition?.search_factors ?? null,
    },
    {
      title: "Split events",
      detail: summarizeList(temporalEvents, "No temporal split needed."),
      status: traceStatus,
      raw: temporalEvents,
    },
    {
      title: "Generate variants",
      detail: summarizeList(
        plannedVariants.length > 0 ? plannedVariants : retrievalVariants,
        "Using original query only.",
      ),
      status: traceStatus,
      raw: {
        planned_variants: plannedVariants,
        retrieval_variants: retrievalVariants,
      },
    },
    {
      title: "Retrieve candidates",
      detail: `${mode === "Auto" ? "Shortlisted" : "Returned"} ${resultCount} results using ${retrievalVariants.length || 1} retrieval variant(s). Source: ${source}${plan?.error ? ` | ${plan.error}` : ""}`,
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
  const hit = textHitInfo(result);
  return (
    <div className={`score-breakdown ${compact ? "compact" : ""}`}>
      {scoreComponents(result).map((item) => (
        <span className={`score-pill ${item.kind}`} key={item.label}>
          <small>{item.label}</small>
          <strong>{formatScore(item.value)}</strong>
        </span>
      ))}
      {hit && (
        <span className="text-hit-pill" title={hit.snippet}>
          <small>
            {hit.source.toUpperCase()}
            {hit.time ? ` ${hit.time}` : ""}
          </small>
          <strong>{hit.score !== null ? formatScore(hit.score) : "hit"}</strong>
          {hit.snippet && <em>{hit.snippet}</em>}
        </span>
      )}
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
  const frameText =
    result.sequence_frames.length > 0
      ? result.sequence_frames.map((frame) => frame.frame_idx).join(", ")
      : (result.frame_idx ?? "N/A");
  const mapInfo = mapKeyframeInfo(result);
  const semanticInfo = semanticHitInfo(result);
  const mapMismatch = Boolean(mapInfo && semanticInfo?.map_matches_resolved === false);

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
        {mapInfo && (
          <div
            className={`map-keyframe-meta${mapMismatch ? " is-mismatch" : ""}`}
            title={mapInfo.map_path}
          >
            <span>Map n {mapInfo.n ?? "?"}</span>
            <span>F{String(mapInfo.frame_idx ?? result.frame_idx ?? "").padStart(6, "0")}</span>
            {typeof mapInfo.pts_time === "number" && <span>{mapInfo.pts_time.toFixed(2)}s</span>}
            <small>
              {mapInfo.source_keyframe_id ?? "vector"} -&gt; {mapInfo.resolved_keyframe_id ?? result.frame_id}
              {mapMismatch && result.frame_id ? ` | UI ${result.frame_id}` : ""}
            </small>
          </div>
        )}
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

function TrakeRows({
  results,
  eventCount,
  isSelected,
  onOpen,
  onSelect,
  onPreview,
}: {
  results: SearchResult[];
  eventCount: number;
  isSelected: (result: SearchResult) => boolean;
  onOpen: (result: SearchResult) => void;
  onSelect: (result: SearchResult) => void;
  onPreview: (result: SearchResult) => void;
}) {
  const inferredEventCount = Math.max(
    1,
    eventCount,
    ...results.flatMap((result) =>
      result.sequence_frames.map((frame) => frame.event_index ?? frame.order_index ?? 0),
    ),
  );
  const lanes = Array.from({ length: inferredEventCount }, (_, index) => index);

  return (
    <div className="trake-board" aria-label="TRAKE ordered frame lanes">
      {lanes.map((lane) => {
        const seenLaneFrames = new Set<string>();
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
          const dedupeKey =
            frameIdx === null
              ? ""
              : `${sequenceFrame?.video_code ?? result.video_code}:${frameIdx}`;
          if (dedupeKey && seenLaneFrames.has(dedupeKey)) return [];
          if (dedupeKey) seenLaneFrames.add(dedupeKey);
          return [
            {
              result,
              resultIndex,
              frameIdx,
              timestampMs,
              candidates,
              selected: isSelected(result),
            },
          ];
        });

        return (
          <section className="trake-lane" key={lane}>
          <div className="trake-lane-label">
            <strong>E{lane + 1}</strong>
            <span>{laneCells.length} candidates</span>
          </div>
          <div className="trake-strip">
            {laneCells.map(
              ({
                result,
                resultIndex,
                frameIdx,
                timestampMs,
                candidates,
                selected,
              }) => {
              return (
                <article
                  className={`trake-cell ${selected ? "selected" : ""}`}
                  key={`${result.id}-${lane}`}
                  onClick={() => onOpen(result)}
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
                    <ScoreBreakdown result={result} compact />
                    <div className="trake-cell-actions">
                      <button
                        type="button"
                        className="ghost-button"
                        onClick={(event) => {
                          event.stopPropagation();
                          onPreview(result);
                        }}
                      >
                        Video
                      </button>
                      <button
                        type="button"
                        className="select-button"
                        onClick={(event) => {
                          event.stopPropagation();
                          onSelect(result);
                        }}
                      >
                        {selected ? "Added" : "Pick"}
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        </section>
        );
      })}
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
  const doneCount = steps.filter((step) => step.status === "done" || step.status === "warning").length;
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
  // Attibutes
  const [mode, setMode] = useState<AppMode>("Search");
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [queryType, setQueryType] = useState<QueryType>("KIS");
  const [queryName, setQueryName] = useState(queryNameByType.KIS);
  const [queryText, setQueryText] = useState(sampleQueries.KIS);
  const [topK, setTopK] = useState(50);
  const [useExpansion, setUseExpansion] = useState(false);
  const [useAgentPlanning, setUseAgentPlanning] = useState(true);
  const [useMetadata, setUseMetadata] = useState(true);
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
  const [galleryFrames, setGalleryFrames] = useState<MediaFrame[]>([]);
  const [galleryLoading, setGalleryLoading] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("Ready");
  const [selected, setSelected] = useState<SubmissionRow[]>([]);
  const [context, setContext] = useState<FrameContext | null>(null);
  const [activeResultId, setActiveResultId] = useState<string | null>(null);
  const [history, setHistory] = useState<SearchHistoryItem[]>([]);
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
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([
    {
      id: "assistant-welcome",
      role: "assistant",
      text: "Upload a video or ask a QA query. I will keep the trace visible and prepare CSV rows.",
      trace: makeAgentTrace("Chat", "QA", 0),
    },
  ]);
  const [videoPreview, setVideoPreview] = useState<VideoPreview | null>(null);
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
    let cancelled = false;
    listDatasets()
      .then((items) => {
        if (cancelled) return;
        const next = items.length > 0 ? items : mockDatasets;
        setDatasets(next);
        setDatasetId((current) => current || next[0]?.id || "");
        if (items.length === 0) setStatus("Mock dataset ready");
      })
      .catch(() => {
        if (cancelled) return;
        setDatasets(mockDatasets);
        setDatasetId(mockDatasets[0].id);
        setStatus("Mock mode");
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
        setGalleryFrames(
          payload.frames.length > 0 ? payload.frames : makeMockFrames(),
        );
      })
      .catch(() => {
        if (cancelled) return;
        const mock = makeMockFrames();
        setGalleryFrames(mock);
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
    const source = hasSearched ? results : galleryResults;
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
  }, [galleryResults, hasSearched, resultFilter, results]);

  const selectedKeys = useMemo(() => new Set(selected.map(rowKey)), [selected]);

  // Functions
  function selectionKeyForResult(result: SearchResult): string {
    return rowKey({
      query_name: queryName,
      query_type: queryType,
      rank: 0,
      video_code: result.video_code,
      frame_indices:
        queryType === "TRAKE" && result.sequence_frames.length > 0
          ? uniqueFrameIndices(result.sequence_frames.map((item) => item.frame_idx))
          : result.frame_idx === null
            ? []
            : [result.frame_idx],
      answer: queryType === "QA" ? (result.answer ?? "") : null,
    });
  }

  function changeType(type: QueryType) {
    setQueryType(type);
    setQueryName(queryNameByType[type]);
    setQueryText(sampleQueries[type]);
    setResults([]);
    setHasSearched(false);
    setContext(null);
    setActiveResultId(null);
    setAutoTrace(makeAgentTrace(mode, type, 0));
  }

  function switchMode(nextMode: AppMode) {
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

  function rememberSearch(nextResults: SearchResult[], trace: AgentStep[]) {
    const createdAt = new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    const item: SearchHistoryItem = {
      id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      mode,
      queryType,
      queryName,
      queryText,
      resultCount: nextResults.length,
      createdAt,
      results: nextResults,
      trace,
    };
    setHistory((current) => [item, ...current].slice(0, 12));
  }

  async function openFrameContext(result: SearchResult) {
    if (!result.frame_id) return;
    setActiveResultId(result.id);
    try {
      setContext(await getFrameContext(result.frame_id));
    } catch {
      setContext(mockContextForResult(result));
    }
  }

  function addResult(result: SearchResult) {
    const frameIndices =
      queryType === "TRAKE" && result.sequence_frames.length > 0
        ? uniqueFrameIndices(result.sequence_frames.map((item) => item.frame_idx))
        : result.frame_idx === null
          ? []
          : [result.frame_idx];

    if (frameIndices.length === 0) {
      setStatus("Frame index missing");
      return;
    }

    const row: SubmissionRow = {
      query_name: queryName,
      query_type: queryType,
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
    if (!datasetId || !queryText.trim()) return;

    setLoading(true);
    setHasSearched(true);
    setStatus(mode === "Auto" && autoEnabled ? "Auto running" : "Searching");
    const runningTrace = makeAgentTrace(mode, queryType, 0).map(
      (step, index) => ({
        ...step,
        status: index <= 1 ? ("running" as const) : ("queued" as const),
      }),
    );
    setAutoTrace(runningTrace);

    try {
      const response = await runSearch({
        datasetId,
        queryType,
        queryName,
        queryText,
        topK,
        useExpansion,
        useAgentPlanning,
        useMetadata,
      });
      const nextResults =
        response.results.length > 0
          ? response.results
          : makeMockResults(queryType, queryName);
      if (queryType === "TRAKE") {
        setTrakeEventCount(response.normalized_query.temporal_event_count ?? 4);
      }
      setResults(nextResults);
      setStatus(`${nextResults.length} results`);
      const trace = makeTraceFromResponse(mode, queryType, response, nextResults.length);
      setAutoTrace(trace);
      rememberSearch(nextResults, trace);
      if (nextResults[0]) void openFrameContext(nextResults[0]);
    } catch (error) {
      const nextResults = makeMockResults(queryType, queryName);
      const trace = makeAgentTrace(mode, queryType, nextResults.length).map(
        (step, index) =>
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
          ? `Mock results: ${error.message.slice(0, 64)}`
          : "Mock results",
      );
      setAutoTrace(trace);
      rememberSearch(nextResults, trace);
      if (nextResults[0]) void openFrameContext(nextResults[0]);
    } finally {
      setLoading(false);
    }
  }

  function submitChat() {
    const prompt = queryText.trim();
    if (!prompt && attachedFiles.length === 0) return;
    const fileNames = attachedFiles.map((file) => file.name);
    const trace = makeAgentTrace(
      "Chat",
      "QA",
      fileNames.length > 0 ? fileNames.length : 4,
    );
    const answerText =
      fileNames.length > 0
        ? "Video received. I prepared a QA trace and can turn a confirmed answer into a Codabench row."
        : "I will answer in QA mode and keep evidence frames ready for export.";
    setChatMessages((current) => [
      ...current,
      {
        id: `user-${Date.now()}`,
        role: "user",
        text: prompt || "Uploaded video for QA",
        files: fileNames,
      },
      {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        text: answerText,
        trace,
      },
    ]);
    setQueryText("");
    setAttachedFiles([]);
    setStatus("Chat trace ready");
  }

  function restoreHistory(item: SearchHistoryItem) {
    setMode(item.mode);
    setQueryType(item.queryType);
    setQueryName(item.queryName);
    setQueryText(item.queryText);
    setResults(item.results);
    setHasSearched(true);
    setAutoTrace(item.trace);
    if (item.results[0]) void openFrameContext(item.results[0]);
  }

  async function openVideoPreview(result: SearchResult) {
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
    if (!videoUrl) {
      setStatus("Video preview unavailable");
      return;
    }
    const fallbackFrame = contextFrameFromResult(result);
    const initialFrames = fallbackFrame ? [fallbackFrame] : [];
    const initialTimestamp = fallbackFrame?.timestamp_ms ?? result.timestamp_ms;
    const frameLabel = fallbackFrame ? `frame ${fallbackFrame.frame_idx}` : "sequence";
    setVideoPreview({
      title: result.video_code,
      subtitle: `${frameLabel} | ${timestampLabel(initialTimestamp)}`,
      baseUrl: videoUrl,
      url: withTimeFragment(videoUrl, initialTimestamp),
      posterUrl: resultImageCandidates(result)[0] ?? null,
      result,
      frames: initialFrames,
      frameIndex: 0,
      loadingFrames: Boolean(result.frame_id),
    });
    setStatus("Video ready");
    if (!result.frame_id) return;
    try {
      const nextContext = await getFrameContext(result.frame_id);
      const targetIndex = Math.max(
        0,
        nextContext.frames.findIndex((frame) => frame.id === nextContext.target_frame_id),
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
          url: withTimeFragment(videoUrl, activeFrame?.timestamp_ms ?? initialTimestamp),
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

  function shiftVideoPreview(delta: number) {
    setVideoPreview((current) => {
      if (!current || current.frames.length === 0) return current;
      const frameIndex = Math.round(
        clampNumber(current.frameIndex + delta, 0, current.frames.length - 1),
      );
      const frame = current.frames[frameIndex];
      if (!frame) return current;
      return {
        ...current,
        frameIndex,
        subtitle: `frame ${frame.frame_idx} | ${timestampLabel(frame.timestamp_ms)}`,
        url: withTimeFragment(current.baseUrl, frame.timestamp_ms),
        posterUrl: contextImageCandidates(frame)[0] ?? current.posterUrl,
      };
    });
  }

  function addVideoPreviewFrame() {
    if (!videoPreview) return;
    const frame = videoPreview.frames[videoPreview.frameIndex];
    if (!frame) {
      setStatus("Frame index missing");
      return;
    }
    const row: SubmissionRow = {
      query_name: queryName,
      query_type: queryType,
      rank: selected.filter((item) => item.query_name === queryName).length + 1,
      video_code: videoPreview.result.video_code,
      frame_indices: [frame.frame_idx],
      answer: queryType === "QA" ? (videoPreview.result.answer ?? "") : null,
    };
    setSelected((current) => {
      if (current.some((item) => rowKey(item) === rowKey(row))) return current;
      return normalizeRanks([...current, row]).slice(0, 100);
    });
    setStatus(`Added ${videoPreview.result.video_code} frame ${frame.frame_idx}`);
  }

  function exportLocalCsv() {
    const blob = new Blob([buildSubmissionCsv(selected)], {
      type: "text/csv;charset=utf-8",
    });
    const name = csvDownloadName(queryName || "submission");
    downloadCsvBlob(blob, name);
    setStatus("CSV downloaded");
  }

  async function exportSubmission() {
    if (selected.length === 0) return;
    setStatus("Exporting");
    try {
      if (datasetId.startsWith("mock"))
        throw new Error("Using local mock dataset");
      const csvName = csvDownloadName(queryName || "submission");
      const name = csvName.replace(/\.csv$/i, "");
      const exported = await createAndExportSubmission(
        datasetId,
        name,
        selected,
      );
      await downloadCsvFromUrl(exported.downloadUrl, csvName);
      const report = exported.validation_report;
      setStatus(
        report.valid
          ? "CSV downloaded"
          : `Invalid: ${report.errors.join(", ")}`,
      );
    } catch {
      exportLocalCsv();
    }
  }

  function newSession() {
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

  const videoPreviewFrame =
    videoPreview?.frames[videoPreview.frameIndex] ?? null;
  const videoPreviewCanStepBack = Boolean(
    videoPreview && videoPreview.frameIndex > 0,
  );
  const videoPreviewCanStepForward = Boolean(
    videoPreview && videoPreview.frameIndex < videoPreview.frames.length - 1,
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
        </div>

        <button type="button" className="new-button" onClick={newSession}>
          New search
        </button>

        <nav className="task-nav" aria-label="Query type">
          {(Object.keys(queryLabels) as QueryType[]).map((type) => (
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
              <label className="compact-field">
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
              </label>
              <label className="compact-field query-file">
                Query file
                <input
                  value={queryName}
                  onChange={(event) => setQueryName(event.target.value)}
                />
              </label>
            </div>
          </div>

          {mode !== "Chat" && (
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
          )}

          {mode === "Search" && (
            <section className="frame-section">
              {(loading || hasSearched) && <ReasoningDisclosure steps={autoTrace} />}
              {loading ? (
                <SearchLoadingStage frameColumns={frameColumns} />
              ) : galleryLoading && !hasSearched ? (
                <div className="skeleton-grid">
                  {Array.from({ length: 12 }).map((_, index) => (
                    <div className="skeleton-card" key={index} />
                  ))}
                </div>
              ) : queryType === "TRAKE" && hasSearched ? (
                <TrakeRows
                  results={visibleResults}
                  eventCount={trakeEventCount}
                  isSelected={(result) =>
                    selectedKeys.has(selectionKeyForResult(result))
                  }
                  onOpen={(result) => void openFrameContext(result)}
                  onSelect={addResult}
                  onPreview={(result) => void openVideoPreview(result)}
                />
              ) : (
                <div
                  className="frame-grid"
                  style={{
                    gridTemplateColumns: `repeat(${frameColumns}, minmax(0, 1fr))`,
                  }}
                >
                  {visibleResults.map((result, index) => (
                    <FrameCard
                      key={result.id}
                      result={result}
                      eager={index < frameColumns}
                      selected={selectedKeys.has(selectionKeyForResult(result))}
                      onOpen={() => void openFrameContext(result)}
                      onSelect={() => addResult(result)}
                      onPreview={() => void openVideoPreview(result)}
                    />
                  ))}
                </div>
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
                  ) : queryType === "TRAKE" && hasSearched ? (
                    <TrakeRows
                      results={visibleResults}
                      eventCount={trakeEventCount}
                      isSelected={(result) =>
                        selectedKeys.has(selectionKeyForResult(result))
                      }
                      onOpen={(result) => void openFrameContext(result)}
                      onSelect={addResult}
                      onPreview={(result) => void openVideoPreview(result)}
                    />
                  ) : (
                    <div
                      className="frame-grid auto-grid"
                      style={{
                        gridTemplateColumns: `repeat(${frameColumns}, minmax(0, 1fr))`,
                      }}
                    >
                      {visibleResults.map((result, index) => (
                        <FrameCard
                          key={result.id}
                          result={result}
                          eager={index < frameColumns}
                          selected={false}
                          onOpen={() => void openFrameContext(result)}
                          onSelect={() => addResult(result)}
                          onPreview={() => void openVideoPreview(result)}
                        />
                      ))}
                    </div>
                  )}
                </>
              ) : loading ? (
                <SearchLoadingStage frameColumns={frameColumns} />
              ) : queryType === "TRAKE" && hasSearched ? (
                <TrakeRows
                  results={visibleResults}
                  eventCount={trakeEventCount}
                  isSelected={(result) =>
                    selectedKeys.has(selectionKeyForResult(result))
                  }
                  onOpen={(result) => void openFrameContext(result)}
                  onSelect={addResult}
                  onPreview={(result) => void openVideoPreview(result)}
                />
              ) : (
                <div
                  className="frame-grid"
                  style={{
                    gridTemplateColumns: `repeat(${frameColumns}, minmax(0, 1fr))`,
                  }}
                >
                  {visibleResults.map((result, index) => (
                    <FrameCard
                      key={result.id}
                      result={result}
                      eager={index < frameColumns}
                      selected={false}
                      onOpen={() => void openFrameContext(result)}
                      onSelect={() => addResult(result)}
                      onPreview={() => void openVideoPreview(result)}
                    />
                  ))}
                </div>
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
                      <button
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
                      />
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
            <span>{context?.video_code ?? activeDataset.name}</span>
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
        <div className="settings-popover" role="dialog" aria-label="Settings">
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
          <div className="settings-block">
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
          </div>
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
            if (event.target === event.currentTarget) setVideoPreview(null);
          }}
        >
          <div
            className="video-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Video preview"
          >
            <div className="video-modal-header">
              <span>
                <strong>{videoPreview.title}</strong>
                <small>{videoPreview.subtitle}</small>
              </span>
              <button
                type="button"
                className="icon-button"
                onClick={() => setVideoPreview(null)}
                aria-label="Close video"
              >
                <X size={16} />
              </button>
            </div>
            <video
              className="video-preview-player"
              src={videoPreview.url}
              poster={videoPreview.posterUrl ?? undefined}
              controls
              autoPlay
              preload="metadata"
              playsInline
            />
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
                    <strong>Frame {videoPreviewFrame.frame_idx}</strong>
                    <small>
                      {timestampLabel(videoPreviewFrame.timestamp_ms)}
                      {videoPreview.loadingFrames ? " | loading context" : ""}
                    </small>
                  </>
                ) : (
                  <>
                    <strong>Sequence</strong>
                    <small>{videoPreview.loadingFrames ? "loading context" : "no frame context"}</small>
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
                Pick frame
              </button>
            </div>
            <div className="video-score-panel">
              <ScoreBreakdown result={videoPreview.result} />
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

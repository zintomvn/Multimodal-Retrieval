import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import {
  Check,
  CheckCircle2,
  Clock,
  CloudUpload,
  Database,
  Download,
  Film,
  FileArchive,
  ImageOff,
  Images,
  Layers,
  Loader2,
  Monitor,
  Moon,
  PlayCircle,
  Search,
  Sparkles,
  Sun,
  Trash2,
  Upload,
  X,
  XCircle
} from "lucide-react";
import { createAndExportSubmission, firstMediaUrl, getFrameContext, getIngestJob, getPipelineJob, listDatasets, listFrames, mediaUrl, runSearch, startGCSUpload, startIngestJob, startMilvusUpload, startPipelineJob, uploadFileToGCS, uploadFileToMilvus } from "./api/client";
import type { ContextFrame, Dataset, FrameContext, IngestJobStatus, MediaFrame, QueryType, SearchResult, SubmissionRow } from "./types";

const sampleQueries: Record<QueryType, string> = {
  KIS: "The clip shows an exhibition program with a royal-style decorative panel, dragon and cloud motifs, and the text PHU XUAN GIA DINH.",
  QA: "Identify the name of the world-famous company whose logo was inspired by a castle in Bavaria, Germany.",
  TRAKE: "In a bicycle race, first a cyclist with a pink helmet crosses the finish line, then a cyclist with a blue helmet, then a cyclist with a red helmet."
};
const FRAME_GALLERY_LIMIT = 60;

interface VideoPreview {
  title: string;
  subtitle: string;
  url: string;
  posterUrl: string | null;
}

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
    result.thumbnail_url,
    result.image_url,
    result.image_uri,
    result.image_storage_key
  ]);
}

function contextImageCandidates(frame: ContextFrame): string[] {
  return uniqueMediaUrls([
    frame.thumbnail_url,
    frame.image_url,
    frame.image_uri,
    frame.image_storage_key
  ]);
}

function galleryImageCandidates(frame: MediaFrame): string[] {
  return uniqueMediaUrls([
    frame.thumbnail_url,
    frame.image_url,
    frame.image_uri,
    frame.image_storage_key
  ]);
}

function withTimeFragment(url: string, timestampMs: number | null): string {
  if (timestampMs === null) return url;
  const seconds = Math.max(0, timestampMs / 1000);
  const [base] = url.split("#", 1);
  return `${base}#t=${seconds.toFixed(2)}`;
}

function CloudFrameImage({
  candidates,
  alt,
  eager = false
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
      <div className="cloud-image-placeholder" aria-label="No cloud frame available">
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
      onError={() => setCandidateIndex((index) => Math.min(index + 1, candidates.length))}
    />
  );
}

export function App() {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [queryType, setQueryType] = useState<QueryType>("KIS");
  const [queryName, setQueryName] = useState("query-1-kis");
  const [queryText, setQueryText] = useState(sampleQueries.KIS);
  const [topK, setTopK] = useState(100);
  const [useExpansion, setUseExpansion] = useState(true);
  const [useMetadata, setUseMetadata] = useState(true);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [viewMode, setViewMode] = useState<"frames" | "results">("frames");
  const [galleryFrames, setGalleryFrames] = useState<MediaFrame[]>([]);
  const [galleryTotal, setGalleryTotal] = useState(0);
  const [galleryLoading, setGalleryLoading] = useState(false);
  const [galleryError, setGalleryError] = useState<string | null>(null);
  const [hasSearched, setHasSearched] = useState(false);
  const [selected, setSelected] = useState<SubmissionRow[]>([]);
  const [context, setContext] = useState<FrameContext | null>(null);
  const [activeResultId, setActiveResultId] = useState<string | null>(null);
  const [status, setStatus] = useState("Ready");
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [videoPreview, setVideoPreview] = useState<VideoPreview | null>(null);

  const [theme, setTheme] = useState<"dark" | "light" | "system">(() => {
    try { return (localStorage.getItem("theme") as "dark" | "light" | "system") ?? "system"; } catch { return "system"; }
  });

  // ─── Upload modal state ───
  const [uploadOpen, setUploadOpen] = useState(false);
  const [gcsSourcePath, setGcsSourcePath] = useState("");
  const [gcsSourceType, setGcsSourceType] = useState<"folder" | "zip">("folder");
  const [gcsJobId, setGcsJobId] = useState<string | null>(null);
  const [gcsStatus, setGcsStatus] = useState<IngestJobStatus | null>(null);
  const [gcsProgress, setGcsProgress] = useState(0);
  const [gcsMessage, setGcsMessage] = useState("");
  const [gcsError, setGcsError] = useState<string | null>(null);
  const [milvusFile, setMilvusFile] = useState("");
  const [milvusCollection, setMilvusCollection] = useState("frames");
  const [milvusJobId, setMilvusJobId] = useState<string | null>(null);
  const [milvusStatus, setMilvusStatus] = useState<IngestJobStatus | null>(null);
  const [milvusProgress, setMilvusProgress] = useState(0);
  const [milvusMessage, setMilvusMessage] = useState("");
  const [milvusError, setMilvusError] = useState<string | null>(null);
  const [gcsBrowserFile, setGcsBrowserFile] = useState<File | null>(null);
  const [milvusBrowserFile, setMilvusBrowserFile] = useState<File | null>(null);

  // ─── Ingest modal state ───
  const [ingestOpen, setIngestOpen] = useState(false);
  const [ingestMode, setIngestMode] = useState<"demo" | "mock">("demo");
  const [datasetRoot, setDatasetRoot] = useState("");
  const [targetDatasetCode, setTargetDatasetCode] = useState("");
  const [ingestJobId, setIngestJobId] = useState<string | null>(null);
  const [ingestJobStatus, setIngestJobStatus] = useState<IngestJobStatus | null>(null);
  const [ingestJobProgress, setIngestJobProgress] = useState(0);
  const [ingestJobMessage, setIngestJobMessage] = useState("");
  const [ingestError, setIngestError] = useState<string | null>(null);

  // ─── Pipeline modal state ───
  const [pipelineOpen, setPipelineOpen] = useState(false);
  const [pipelineSourceId, setPipelineSourceId] = useState("l21_l30_ai_challenge_2025");
  const [pipelineSourceDatasetId, setPipelineSourceDatasetId] = useState("");
  const [pipelineBatchIds, setPipelineBatchIds] = useState("");
  const [pipelineVideoKeys, setPipelineVideoKeys] = useState("");
  const [pipelineJobId, setPipelineJobId] = useState<string | null>(null);
  const [pipelineJobStatus, setPipelineJobStatus] = useState<IngestJobStatus | null>(null);
  const [pipelineJobProgress, setPipelineJobProgress] = useState(0);
  const [pipelineJobMessage, setPipelineJobMessage] = useState("");
  const [pipelineError, setPipelineError] = useState<string | null>(null);

  useEffect(() => {
    const apply = () => {
      const prefersLight = window.matchMedia("(prefers-color-scheme: light)").matches;
      document.documentElement.classList.toggle(
        "theme-light",
        theme === "light" || (theme === "system" && prefersLight)
      );
    };
    apply();
    try { localStorage.setItem("theme", theme); } catch { /* ignore */ }
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [theme]);

  useEffect(() => {
    listDatasets()
      .then((items) => {
        setDatasets(items);
        if (items[0]) setDatasetId(items[0].id);
      })
      .catch((error) => setStatus(error.message));
  }, []);

  useEffect(() => {
    if (!datasetId) {
      setGalleryFrames([]);
      setGalleryTotal(0);
      return;
    }
    let cancelled = false;
    setGalleryLoading(true);
    setGalleryError(null);
    listFrames({ datasetId, limit: FRAME_GALLERY_LIMIT, offset: 0, presentOnly: true })
      .then((payload) => {
        if (cancelled) return;
        setGalleryFrames(payload.frames);
        setGalleryTotal(payload.total);
      })
      .catch((error) => {
        if (cancelled) return;
        setGalleryFrames([]);
        setGalleryTotal(0);
        setGalleryError(error instanceof Error ? error.message : "Frame gallery failed");
      })
      .finally(() => {
        if (!cancelled) setGalleryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [datasetId]);

  const isGcsRunning = gcsStatus === "PENDING" || gcsStatus === "RUNNING";
  const isGcsSettled = gcsStatus === "COMPLETED" || gcsStatus === "FAILED";
  const isMilvusRunning = milvusStatus === "PENDING" || milvusStatus === "RUNNING";
  const isMilvusSettled = milvusStatus === "COMPLETED" || milvusStatus === "FAILED";
  const isUploadRunning = isGcsRunning || isMilvusRunning;

  const isIngestRunning = ingestJobStatus === "PENDING" || ingestJobStatus === "RUNNING";
  const isIngestSettled = ingestJobStatus === "COMPLETED" || ingestJobStatus === "FAILED";

  useEffect(() => {
    if (!ingestJobId || isIngestSettled) return;
    const tick = async () => {
      try {
        const job = await getIngestJob(ingestJobId);
        setIngestJobStatus(job.status);
        setIngestJobProgress(job.progress);
        setIngestJobMessage(job.message ?? "");
      } catch (err) {
        setIngestError(err instanceof Error ? err.message : "Polling failed");
      }
    };
    void tick();
    const id = setInterval(() => void tick(), 1500);
    return () => clearInterval(id);
  }, [ingestJobId, isIngestSettled]);

  useEffect(() => {
    if (!ingestOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isIngestRunning) handleIngestClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [ingestOpen, isIngestRunning]);

  const isPipelineRunning = pipelineJobStatus === "PENDING" || pipelineJobStatus === "RUNNING";
  const isPipelineSettled = pipelineJobStatus === "COMPLETED" || pipelineJobStatus === "FAILED";

  useEffect(() => {
    if (!pipelineJobId || isPipelineSettled) return;
    const tick = async () => {
      try {
        const job = await getPipelineJob(pipelineJobId);
        setPipelineJobStatus(job.status);
        setPipelineJobProgress(job.progress);
        setPipelineJobMessage(job.message ?? "");
      } catch (err) {
        setPipelineError(err instanceof Error ? err.message : "Polling failed");
      }
    };
    void tick();
    const id = setInterval(() => void tick(), 1500);
    return () => clearInterval(id);
  }, [pipelineJobId, isPipelineSettled]);

  useEffect(() => {
    if (!pipelineOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isPipelineRunning) handlePipelineClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [pipelineOpen, isPipelineRunning]);

  useEffect(() => {
    if (!gcsJobId || isGcsSettled) return;
    const tick = async () => {
      try {
        const job = await getIngestJob(gcsJobId);
        setGcsStatus(job.status);
        setGcsProgress(job.progress);
        setGcsMessage(job.message ?? "");
      } catch (err) {
        setGcsError(err instanceof Error ? err.message : "Polling failed");
      }
    };
    void tick();
    const id = setInterval(() => void tick(), 1500);
    return () => clearInterval(id);
  }, [gcsJobId, isGcsSettled]);

  useEffect(() => {
    if (!milvusJobId || isMilvusSettled) return;
    const tick = async () => {
      try {
        const job = await getIngestJob(milvusJobId);
        setMilvusStatus(job.status);
        setMilvusProgress(job.progress);
        setMilvusMessage(job.message ?? "");
      } catch (err) {
        setMilvusError(err instanceof Error ? err.message : "Polling failed");
      }
    };
    void tick();
    const id = setInterval(() => void tick(), 1500);
    return () => clearInterval(id);
  }, [milvusJobId, isMilvusSettled]);

  const activeDataset = useMemo(() => datasets.find((item) => item.id === datasetId), [datasets, datasetId]);
  const firstResultImageUrls = useMemo(
    () => results.slice(0, 12).map((result) => resultImageCandidates(result)[0]).filter((url): url is string => Boolean(url)),
    [results]
  );

  useEffect(() => {
    const warmups = firstResultImageUrls.map((url) => {
      const image = new Image();
      image.decoding = "async";
      image.src = url;
      return image;
    });
    return () => {
      warmups.forEach((image) => {
        image.src = "";
      });
    };
  }, [firstResultImageUrls]);

  useEffect(() => {
    if (!videoPreview) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") setVideoPreview(null);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [videoPreview]);

  function changeType(type: QueryType) {
    setQueryType(type);
    setQueryText(sampleQueries[type]);
    setQueryName(type === "KIS" ? "query-1-kis" : type === "QA" ? "query-2-qa" : "query-3-trake");
    setResults([]);
    setViewMode("results");
    setHasSearched(false);
    setContext(null);
  }

  async function submitSearch() {
    if (!datasetId) return;
    setStatus("Searching");
    setDownloadUrl(null);
    setViewMode("results");
    try {
      const response = await runSearch({
        datasetId,
        queryType,
        queryName,
        queryText,
        topK,
        useExpansion,
        useMetadata
      });
      setResults(response.results);
      setHasSearched(true);
      setStatus(
        response.results.length > 0
          ? `${response.results.length} results from ${response.query_run_id.slice(0, 8)}`
          : `No results from ${response.query_run_id.slice(0, 8)}`
      );
      const firstFrame = response.results.find((item) => item.frame_id);
      if (firstFrame?.frame_id) void openContext(firstFrame);
    } catch (error) {
      setHasSearched(true);
      setStatus(error instanceof Error ? error.message : "Search failed");
    }
  }

  async function openContext(result: SearchResult) {
    if (!result.frame_id) return;
    await openFrameContext(result.frame_id, result.id);
  }

  async function openFrameContext(frameId: string, activeId = `frame:${frameId}`) {
    setActiveResultId(activeId);
    try {
      setContext(await getFrameContext(frameId));
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Context failed");
    }
  }

  async function loadMoreGalleryFrames() {
    if (!datasetId || galleryLoading || galleryFrames.length >= galleryTotal) return;
    setGalleryLoading(true);
    setGalleryError(null);
    try {
      const payload = await listFrames({
        datasetId,
        limit: FRAME_GALLERY_LIMIT,
        offset: galleryFrames.length,
        presentOnly: true,
      });
      setGalleryFrames((current) => [...current, ...payload.frames]);
      setGalleryTotal(payload.total);
    } catch (error) {
      setGalleryError(error instanceof Error ? error.message : "Frame gallery failed");
    } finally {
      setGalleryLoading(false);
    }
  }

  function openVideoPreview(result: SearchResult) {
    const fallbackVideoEndpoint = `/api/media/videos/${result.video_id}/preview`;
    const videoUrl = firstMediaUrl(result.video_url, fallbackVideoEndpoint);
    if (!videoUrl) {
      setStatus("Video media is not available");
      return;
    }
    const frameLabel = result.frame_idx === null ? "sequence" : `frame ${result.frame_idx}`;
    setVideoPreview({
      title: result.video_code,
      subtitle: `${frameLabel} · score ${result.score.toFixed(3)}`,
      url: withTimeFragment(videoUrl, result.timestamp_ms),
      posterUrl: resultImageCandidates(result)[0] ?? null
    });
  }

  function addResult(result: SearchResult) {
    const frameIndices =
      queryType === "TRAKE" && result.sequence_frames.length > 0
        ? result.sequence_frames.map((item) => item.frame_idx)
        : result.frame_idx === null
          ? []
          : [result.frame_idx];
    const row: SubmissionRow = {
      query_name: queryName,
      query_type: queryType,
      rank: selected.filter((item) => item.query_name === queryName).length + 1,
      video_code: result.video_code,
      frame_indices: frameIndices,
      answer: result.answer
    };
    setSelected((current) => [...current, row]);
  }

  async function exportSubmission() {
    if (!datasetId || selected.length === 0) return;
    setStatus("Exporting");
    try {
      const name = `submission_${new Date().toISOString().replace(/[:.]/g, "-")}`;
      const exported = await createAndExportSubmission(datasetId, name, selected);
      setDownloadUrl(exported.downloadUrl);
      const report = exported.validation_report;
      setStatus(report.valid ? "Submission exported" : `Invalid: ${report.errors.join(", ")}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Export failed");
    }
  }

  async function handleGCSUpload() {
    setGcsError(null);
    setGcsJobId(null);
    setGcsStatus("PENDING");
    setGcsProgress(0);
    setGcsMessage("Starting GCS upload...");
    try {
      const job = gcsBrowserFile
        ? await uploadFileToGCS(gcsBrowserFile)
        : await startGCSUpload({ source_path: gcsSourcePath, source_type: gcsSourceType });
      setGcsJobId(job.job_id);
      setGcsStatus(job.status as IngestJobStatus);
      setGcsMessage(job.message ?? "");
    } catch (err) {
      setGcsStatus("FAILED");
      setGcsError(err instanceof Error ? err.message : "Failed to start GCS upload");
    }
  }

  async function handleMilvusUpload() {
    setMilvusError(null);
    setMilvusJobId(null);
    setMilvusStatus("PENDING");
    setMilvusProgress(0);
    setMilvusMessage("Starting Milvus index...");
    try {
      const job = milvusBrowserFile
        ? await uploadFileToMilvus(milvusBrowserFile, milvusCollection || "frames")
        : await startMilvusUpload({ features_file: milvusFile, collection: milvusCollection || "frames" });
      setMilvusJobId(job.job_id);
      setMilvusStatus(job.status as IngestJobStatus);
      setMilvusMessage(job.message ?? "");
    } catch (err) {
      setMilvusStatus("FAILED");
      setMilvusError(err instanceof Error ? err.message : "Failed to start Milvus index");
    }
  }

  function handleUploadClose() {
    if (isUploadRunning) return;
    setUploadOpen(false);
    setGcsJobId(null); setGcsStatus(null); setGcsProgress(0); setGcsMessage(""); setGcsError(null); setGcsBrowserFile(null);
    setMilvusJobId(null); setMilvusStatus(null); setMilvusProgress(0); setMilvusMessage(""); setMilvusError(null); setMilvusBrowserFile(null);
  }

  async function handleIngestStart() {
    setIngestError(null);
    setIngestJobId(null);
    setIngestJobStatus("PENDING");
    setIngestJobProgress(0);
    setIngestJobMessage("Starting demo ingest...");
    try {
      const job = await startIngestJob({
        mode: ingestMode,
        dataset_code: targetDatasetCode.trim() || undefined,
        dataset_root: datasetRoot.trim() || undefined,
      });
      setIngestJobId(job.job_id);
      setIngestJobStatus(job.status as IngestJobStatus);
      setIngestJobMessage(job.message ?? "");
    } catch (err) {
      setIngestJobStatus("FAILED");
      setIngestError(err instanceof Error ? err.message : "Failed to start ingest");
    }
  }

  function handleIngestClose() {
    if (isIngestRunning) return;
    setIngestOpen(false);
    setIngestJobId(null);
    setIngestJobStatus(null);
    setIngestJobProgress(0);
    setIngestJobMessage("");
    setIngestError(null);
  }

  async function handlePipelineStart() {
    setPipelineError(null);
    setPipelineJobId(null);
    setPipelineJobStatus("PENDING");
    setPipelineJobProgress(0);
    setPipelineJobMessage("Starting video pipeline...");
    try {
      const batchIds = pipelineBatchIds.trim()
        ? pipelineBatchIds.split(",").map((s) => s.trim()).filter(Boolean)
        : undefined;
      const videoKeys = pipelineVideoKeys.trim()
        ? pipelineVideoKeys.split("\n").map((s) => s.trim()).filter(Boolean)
        : undefined;
      const job = await startPipelineJob({
        source_id: pipelineSourceId.trim(),
        source_dataset_id: pipelineSourceDatasetId.trim() || undefined,
        batch_ids: batchIds,
        video_keys: videoKeys,
      });
      setPipelineJobId(job.job_id);
      setPipelineJobStatus(job.status as IngestJobStatus);
      setPipelineJobMessage(job.message ?? "");
    } catch (err) {
      setPipelineJobStatus("FAILED");
      setPipelineError(err instanceof Error ? err.message : "Failed to start pipeline");
    }
  }

  function handlePipelineClose() {
    if (isPipelineRunning) return;
    setPipelineOpen(false);
    setPipelineJobId(null);
    setPipelineJobStatus(null);
    setPipelineJobProgress(0);
    setPipelineJobMessage("");
    setPipelineError(null);
  }

  const isSearching = status === "Searching";
  const isError = /failed|invalid|error/i.test(status);
  const pillClass = isSearching ? "status-pill searching" : isError ? "status-pill error" : "status-pill";
  const ThemeIcon = theme === "dark" ? Moon : theme === "light" ? Sun : Monitor;

  return (
    <main className="grid min-h-dvh grid-rows-[auto_1fr] bg-surface-base">
      <div className="ambient-glow" aria-hidden="true" />
      <header className="topbar sticky top-0 z-[200] flex min-h-[54px] items-center justify-between gap-4 border-b border-border bg-[var(--topbar-bg)] px-[18px] backdrop-blur-[22px] backdrop-saturate-[180%]">
        <div className="flex min-w-0 items-center gap-[11px]">
          <div className="brand-mark">
            <Search size={13} />
          </div>
          <div>
            <h1 className="text-[13.5px] font-bold leading-none tracking-[-0.015em] text-txt-1">Multimodal Retrieval</h1>
            <p className="mt-[3px] font-mono text-[10.5px] tracking-[0.01em] text-txt-3">{activeDataset ? `${activeDataset.name}:${activeDataset.version} · ${activeDataset.video_count} videos` : "No dataset"}</p>
          </div>
        </div>
        <div className="topbar-actions flex items-center gap-2">
          <select
            value={datasetId}
            onChange={(event) => {
              setDatasetId(event.target.value);
              setResults([]);
              setViewMode("frames");
              setHasSearched(false);
              setContext(null);
            }}
            aria-label="Dataset"
            className="min-w-0 rounded-[7px] border border-[var(--border-hi)] bg-surface-raised p-[7px_10px] text-[13px] font-medium text-txt-1 transition-[border-color,box-shadow] duration-[140ms] ease-[cubic-bezier(0.16,1,0.3,1)] focus:border-accent focus:shadow-[0_0_0_3px_var(--accent-dim)] focus:outline-none"
          >
            {datasets.map((dataset) => (
              <option key={dataset.id} value={dataset.id}>
                {dataset.name}:{dataset.version}
              </option>
            ))}
          </select>
          <span className={pillClass}>
            {isSearching ? <Loader2 size={14} className="animate-spin-slow" /> : <Database size={14} />}
            {status}
          </span>
          <button
            className="inline-flex items-center gap-1.5 h-8 px-3 rounded-[7px] border border-[var(--border-hi)] bg-[var(--bg-raised)] text-[var(--text-2)] text-xs font-semibold shrink-0 transition-[background,color,border-color,box-shadow] duration-[140ms] hover:bg-[var(--accent-dim)] hover:border-[var(--border-acc)] hover:text-[var(--accent-hi)] hover:shadow-[0_0_0_3px_var(--accent-dim)]"
            onClick={() => { setTargetDatasetCode(datasetId); setIngestOpen(true); }}
            aria-label="Open demo ingest modal"
          >
            <Upload size={13} />
            Demo ingest
          </button>
          <button
            className="inline-flex items-center gap-1.5 h-8 px-3 rounded-[7px] border border-[var(--border-hi)] bg-[var(--bg-raised)] text-[var(--text-2)] text-xs font-semibold shrink-0 transition-[background,color,border-color,box-shadow] duration-[140ms] hover:bg-[var(--accent-dim)] hover:border-[var(--border-acc)] hover:text-[var(--accent-hi)] hover:shadow-[0_0_0_3px_var(--accent-dim)]"
            onClick={() => setPipelineOpen(true)}
            aria-label="Open video pipeline modal"
          >
            <Layers size={13} />
            Run pipeline
          </button>
          <button
            className="inline-flex items-center gap-1.5 h-8 px-3 rounded-[7px] border border-[var(--border-hi)] bg-[var(--bg-raised)] text-[var(--text-2)] text-xs font-semibold shrink-0 transition-[background,color,border-color,box-shadow] duration-[140ms] hover:bg-[var(--accent-dim)] hover:border-[var(--border-acc)] hover:text-[var(--accent-hi)] hover:shadow-[0_0_0_3px_var(--accent-dim)]"
            onClick={() => setUploadOpen(true)}
            aria-label="Open cloud upload modal"
          >
            <CloudUpload size={13} />
            Upload
          </button>
          <button
            className="theme-toggle"
            onClick={() => setTheme((t) => (t === "system" ? "dark" : t === "dark" ? "light" : "system"))}
            title={`Theme: ${theme}`}
            aria-label={`Switch theme (current: ${theme})`}
          >
            <ThemeIcon size={14} />
          </button>
        </div>
      </header>

      <section className="workspace grid min-h-0 gap-[1px] bg-border" style={{ gridTemplateColumns: "minmax(248px,286px) minmax(400px,1fr) minmax(262px,315px)" }}>
        <aside className="flex flex-col gap-[13px] overflow-auto bg-surface-panel p-[14px]">
          <div className="grid grid-cols-3 gap-[2px] overflow-hidden rounded-[9px] border border-[var(--border-hi)] bg-surface-raised p-[3px]" aria-label="Query type">
            {(["KIS", "QA", "TRAKE"] as QueryType[]).map((type) => (
              <button key={type} className={["segmented-btn", queryType === type ? "active" : ""].filter(Boolean).join(" ")} onClick={() => changeType(type)}>
                {type}
              </button>
            ))}
          </div>

          <label className="grid gap-[6px] font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-txt-3">
            Query file
            <input value={queryName} onChange={(event) => setQueryName(event.target.value)} />
          </label>

          <label className="grid gap-[6px] font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-txt-3">
            Query
            <textarea value={queryText} onChange={(event) => setQueryText(event.target.value)} className="min-h-[150px] resize-y rounded-[7px] border border-[var(--border-hi)] bg-surface-raised p-[7px_10px] text-[12.5px] font-normal leading-[1.6] normal-case text-txt-1 transition-[border-color,box-shadow] duration-[140ms] ease-[cubic-bezier(0.16,1,0.3,1)] focus:border-accent focus:shadow-[0_0_0_3px_var(--accent-dim)] focus:outline-none" />
          </label>

          <div className="grid grid-cols-[1fr_auto_auto] items-end gap-[7px]">
            <label className="grid gap-[6px] font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-txt-3">
              Top-K
              <input type="number" min={1} max={1000} value={topK} onChange={(event) => setTopK(Number(event.target.value))} />
            </label>
            <button className={useExpansion ? "toggle active" : "toggle"} onClick={() => setUseExpansion((value) => !value)} title="LLM query expansion">
              <Sparkles size={17} />
              MV
            </button>
            <button className={useMetadata ? "toggle active" : "toggle"} onClick={() => setUseMetadata((value) => !value)} title="OCR/ASR/object metadata">
              <Layers size={17} />
              Meta
            </button>
          </div>

          <button className="primary-action" onClick={submitSearch}>
            <Search size={18} />
            Run
          </button>

          <div className="grid gap-[10px] border-t border-border pt-[12px]">
            <div className="flex items-center justify-between gap-2 font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-txt-3">
              <FileArchive size={17} />
              Selected
              <span className="inline-grid min-w-[20px] place-items-center border border-[var(--border-acc)] bg-accent-dim font-mono text-[10px] font-bold tabular-nums text-accent-hi" style={{ height: 18, borderRadius: 4 }}>{selected.length}</span>
            </div>
            <div className="grid gap-[5px] overflow-auto" style={{ maxHeight: 200 }}>
              {selected.map((row, index) => (
                <div className="selected-row" key={`${row.query_name}-${index}`}>
                  <button title="Remove" onClick={() => setSelected((items) => items.filter((_, idx) => idx !== index))}>
                    <Trash2 size={15} />
                  </button>
                  <span>{row.query_name}</span>
                  <strong>{row.video_code}</strong>
                  <code>{row.frame_indices.join(" · ")}</code>
                </div>
              ))}
            </div>
            <button className="export-action" onClick={exportSubmission} disabled={selected.length === 0}>
              <Download size={18} />
              Export ZIP
            </button>
            {downloadUrl && (
              <a className="text-center font-mono text-[12px] font-medium text-accent no-underline transition-colors duration-[140ms] ease-[cubic-bezier(0.16,1,0.3,1)] hover:text-accent-hi hover:underline" href={downloadUrl}>
                Download
              </a>
            )}
          </div>
        </aside>

        <section className="grid grid-rows-[auto_1fr] min-h-0 gap-3 overflow-hidden bg-surface-panel p-[14px]">
          <div className="flex items-center justify-between gap-2">
            <div>
              <h2 className="text-[13px] font-bold tracking-[-0.01em] text-txt-1">{viewMode === "frames" ? "Frames" : "Results"}</h2>
              <p className="mt-[2px] font-mono text-[10.5px] text-txt-3">
                {viewMode === "frames"
                  ? `${galleryFrames.length}/${galleryTotal} cloud frames`
                  : `${results.length} ranked candidates`}
              </p>
            </div>
            <div className="flex items-center gap-[6px]">
              <button className={viewMode === "results" ? "toggle active" : "toggle"} style={{ height: 30 }} onClick={() => setViewMode("results")}>
                <Search size={13} />
                Results
              </button>
              <button className={viewMode === "frames" ? "toggle active" : "toggle"} style={{ height: 30 }} onClick={() => setViewMode("frames")}>
                <Images size={13} />
                Frames
              </button>
            </div>
          </div>
          {viewMode === "results" && results.length > 0 ? (
            <div className="result-grid min-h-0 grid content-start gap-[9px] overflow-auto" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(220px,1fr))" }}>
              {results.map((result, index) => {
                const imageCandidates = resultImageCandidates(result);
                const hasImage = imageCandidates.length > 0;
                return (
                  <article
                    key={result.id}
                    className={[
                      "result-card",
                      !hasImage ? "no-img" : "",
                      activeResultId === result.id ? "active" : ""
                    ].filter(Boolean).join(" ")}
                    style={{ animationDelay: `${index * 35}ms` }}
                    onClick={() => openContext(result)}
                  >
                    {hasImage && (
                      <div className="result-img">
                        <CloudFrameImage
                          candidates={imageCandidates}
                          alt={`${result.video_code} frame ${result.frame_idx}`}
                          eager={index < 8}
                        />
                        <span className="result-rank">#{result.rank}</span>
                      </div>
                    )}
                    <div className="grid gap-[7px] p-[10px] content-start">
                      <div className="rank-line">
                        {!hasImage && <span>#{result.rank}</span>}
                        <strong>{result.video_code}</strong>
                        <code>{result.frame_idx ?? result.sequence_frames.map((item) => item.frame_idx).join(" · ")}</code>
                      </div>
                      <div className="score-line">
                        <Clock size={13} />
                        {result.score.toFixed(3)}
                        {result.answer && <em>{result.answer}</em>}
                      </div>
                      {result.sequence_frames.length > 0 && (
                        <div className="sequence-strip">
                          {result.sequence_frames.map((item) => (
                            <span key={`${result.id}-${item.frame_idx}`}>{item.frame_idx}</span>
                          ))}
                        </div>
                      )}
                      <div className="score-breakdown">
                        {Object.entries(result.score_breakdown).map(([key, value]) => (
                          <span key={key}>
                            {key.replace("_score", "")}: {String(value)}
                          </span>
                        ))}
                      </div>
                      <div className="card-actions">
                        <button
                          className="select-button"
                          onClick={(event) => {
                            event.stopPropagation();
                            addResult(result);
                          }}
                        >
                          <Check size={14} />
                          Select
                        </button>
                        <button
                          className="select-button video-button"
                          onClick={(event) => {
                            event.stopPropagation();
                            openVideoPreview(result);
                          }}
                        >
                          <PlayCircle size={14} />
                          Video
                        </button>
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : viewMode === "results" ? (
            <div className="result-empty">
              <Search size={38} />
              <p>
                {isSearching
                  ? "Searching..."
                  : hasSearched
                    ? "No candidates returned for this dataset. Check that keyframes, annotations, and indexes were imported."
                    : "Run a query to see ranked candidates"}
              </p>
            </div>
          ) : galleryFrames.length > 0 ? (
            <div className="result-grid min-h-0 grid content-start gap-[9px] overflow-auto" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(220px,1fr))" }}>
              {galleryFrames.map((frame, index) => {
                const imageCandidates = galleryImageCandidates(frame);
                const activeId = `frame:${frame.id}`;
                const seconds = Math.max(0, frame.timestamp_ms / 1000);
                return (
                  <article
                    key={frame.id}
                    className={[
                      "result-card",
                      imageCandidates.length === 0 ? "no-img" : "",
                      activeResultId === activeId ? "active" : ""
                    ].filter(Boolean).join(" ")}
                    style={{ animationDelay: `${Math.min(index, 12) * 25}ms` }}
                    onClick={() => openFrameContext(frame.id, activeId)}
                  >
                    {imageCandidates.length > 0 && (
                      <div className="result-img">
                        <CloudFrameImage
                          candidates={imageCandidates}
                          alt={`${frame.video_code} frame ${frame.frame_idx}`}
                          eager={index < 12}
                        />
                        <span className="result-rank">{frame.video_code}</span>
                      </div>
                    )}
                    <div className="grid gap-[7px] p-[10px] content-start">
                      <div className="rank-line">
                        <strong>{frame.video_code}</strong>
                        <code>{frame.frame_idx}</code>
                      </div>
                      <div className="score-line">
                        <Clock size={13} />
                        {seconds.toFixed(2)}s
                        {frame.frame_type && <em>{frame.frame_type}</em>}
                      </div>
                    </div>
                  </article>
                );
              })}
              {galleryFrames.length < galleryTotal && (
                <button
                  className="toggle"
                  style={{ gridColumn: "1 / -1", justifySelf: "center", minWidth: 150 }}
                  onClick={loadMoreGalleryFrames}
                  disabled={galleryLoading}
                >
                  {galleryLoading ? <Loader2 size={14} className="animate-spin-slow" /> : <Images size={14} />}
                  {galleryLoading ? "Loading..." : "Load more"}
                </button>
              )}
            </div>
          ) : (
            <div className="result-empty">
              {galleryLoading ? <Loader2 size={38} className="animate-spin-slow" /> : <Images size={38} />}
              <p>{galleryLoading ? "Loading frames..." : galleryError ?? "No media-present frames found for this dataset"}</p>
            </div>
          )}
        </section>

        <aside className="context-panel grid grid-rows-[auto_1fr] min-h-0 gap-3 overflow-hidden bg-surface-panel p-[14px]">
          <div className="flex items-center justify-between gap-2">
            <div>
              <h2 className="text-[13px] font-bold tracking-[-0.01em] text-txt-1">Context</h2>
              <p className="mt-[2px] font-mono text-[10.5px] text-txt-3">{context?.video_code ?? "No frame"}</p>
            </div>
          </div>
          <div className="min-h-0 grid content-start gap-[7px] overflow-auto">
            {context?.frames.map((frame) => {
              const imageCandidates = contextImageCandidates(frame);
              return (
                <div key={frame.id} className={frame.id === context.target_frame_id ? "context-frame target" : "context-frame"}>
                  <CloudFrameImage
                    candidates={imageCandidates}
                    alt={`Frame ${frame.frame_idx}`}
                    eager={frame.id === context.target_frame_id}
                  />
                  <div>
                    <strong>{frame.frame_idx}</strong>
                    <p>{frame.text}</p>
                  </div>
                </div>
              );
            })}
          </div>
        </aside>
      </section>

      {videoPreview && createPortal(
        <div
          className="fixed inset-0 z-[500] bg-black/70 backdrop-blur-md grid place-items-center p-5 animate-fade-up"
          onClick={(event) => { if (event.target === event.currentTarget) setVideoPreview(null); }}
          role="dialog"
          aria-modal="true"
          aria-label="Video preview"
        >
          <div className="video-modal">
            <div className="video-modal-header">
              <div className="min-w-0">
                <h2>
                  <Film size={15} />
                  {videoPreview.title}
                </h2>
                <p>{videoPreview.subtitle}</p>
              </div>
              <button
                className="theme-toggle"
                onClick={() => setVideoPreview(null)}
                aria-label="Close video preview"
              >
                <X size={15} />
              </button>
            </div>
            <video
              className="video-preview-player"
              src={videoPreview.url}
              poster={videoPreview.posterUrl ?? undefined}
              controls
              preload="metadata"
            />
          </div>
        </div>,
        document.body
      )}

      {uploadOpen && createPortal(
        <div
          className="fixed inset-0 z-[500] bg-black/70 backdrop-blur-md grid place-items-center p-5 animate-fade-up"
          onClick={(e) => { if (e.target === e.currentTarget) handleUploadClose(); }}
          role="dialog"
          aria-modal="true"
          aria-label="Upload to cloud"
        >
          <div className="w-full max-w-[580px] max-h-[calc(100dvh-40px)] overflow-hidden flex flex-col rounded-[14px] border border-[var(--border-hi)] bg-[var(--bg-panel)] shadow-[0_24px_80px_rgba(0,0,0,0.55),0_0_60px_var(--accent-glow)] animate-fade-up">

            {/* Header */}
            <div className="flex items-center justify-between gap-3 px-5 py-4 border-b border-[var(--border)] shrink-0">
              <div>
                <h2 className="m-0 text-[15px] font-bold tracking-tight text-[var(--text-1)]">Upload to cloud</h2>
                <p className="m-0 mt-0.5 font-mono text-[11px] text-[var(--text-3)]">Push images to GCS or index vectors in Milvus</p>
              </div>
              <button
                className="w-7 h-7 grid place-items-center rounded-[6px] border border-transparent text-[var(--text-3)] hover:bg-[var(--red-dim)] hover:border-[var(--red-bdr)] hover:text-[var(--red)] transition-[background,color,border-color] duration-[140ms]"
                onClick={handleUploadClose}
                disabled={isUploadRunning}
                aria-label="Close modal"
              >
                <X size={15} />
              </button>
            </div>

            {/* Body */}
            <div className="flex-1 overflow-y-auto px-5 py-[18px] grid gap-[13px]">

              {/* GCS card */}
              <div className="rounded-[10px] border border-[var(--border-hi)] bg-[var(--bg-raised)] p-4 grid gap-[11px]">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="inline-grid place-items-center h-5 px-2 rounded bg-blue-500/10 border border-blue-500/30 font-mono text-[9.5px] font-bold tracking-[0.04em] text-blue-400">GCS</span>
                    <span className="text-[13px] font-semibold text-[var(--text-1)]">Images → Google Cloud Storage</span>
                  </div>
                  {gcsStatus === "COMPLETED" && <CheckCircle2 size={15} className="text-[var(--green)] shrink-0" />}
                  {gcsStatus === "FAILED"    && <XCircle size={15} className="text-[var(--red)] shrink-0" />}
                  {isGcsRunning              && <Loader2 size={15} className="animate-spin-slow text-[var(--accent)] shrink-0" />}
                </div>

                <div className="grid grid-cols-2 gap-[6px]">
                  <button
                    className={`h-7 rounded-[6px] border text-xs font-semibold transition-[background,color,border-color] duration-[140ms] ${gcsSourceType === "folder" ? "border-[var(--border-acc)] bg-[var(--accent-dim)] text-[var(--accent-hi)]" : "border-[var(--border)] bg-transparent text-[var(--text-3)] hover:text-[var(--text-2)]"}`}
                    onClick={() => setGcsSourceType("folder")}
                    disabled={isGcsRunning}
                  >Folder</button>
                  <button
                    className={`h-7 rounded-[6px] border text-xs font-semibold transition-[background,color,border-color] duration-[140ms] ${gcsSourceType === "zip" ? "border-[var(--border-acc)] bg-[var(--accent-dim)] text-[var(--accent-hi)]" : "border-[var(--border)] bg-transparent text-[var(--text-3)] hover:text-[var(--text-2)]"}`}
                    onClick={() => setGcsSourceType("zip")}
                    disabled={isGcsRunning}
                  >Zip file</button>
                </div>

                <label className="grid gap-[5px] font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--text-3)]">
                  {gcsSourceType === "folder" ? "Folder path" : "Zip file path"}
                  <input
                    type="text"
                    value={gcsSourcePath}
                    onChange={(e) => setGcsSourcePath(e.target.value)}
                    placeholder={gcsSourceType === "folder" ? "/data/datasets/v3/keyframes" : "/data/datasets/v3/keyframes.zip"}
                    disabled={isGcsRunning}
                  />
                </label>

                <div className="flex items-center gap-2">
                  <div className="flex-1 h-px bg-[var(--border)]" />
                  <span className="font-mono text-[9.5px] text-[var(--text-3)] uppercase tracking-[0.1em] shrink-0">or from machine</span>
                  <div className="flex-1 h-px bg-[var(--border)]" />
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="file"
                    accept=".jpg,.jpeg,.png,.mp4,.avi,.mov,.mkv,.zip"
                    id="gcs-file-input"
                    className="hidden"
                    disabled={isGcsRunning}
                    onChange={(e) => setGcsBrowserFile(e.target.files?.[0] ?? null)}
                  />
                  <label
                    htmlFor="gcs-file-input"
                    className="toggle cursor-pointer shrink-0"
                    style={{ pointerEvents: isGcsRunning ? "none" : "auto", opacity: isGcsRunning ? 0.5 : 1 }}
                  >
                    Choose file
                  </label>
                  <span className="font-mono text-[11px] text-[var(--text-3)] truncate">
                    {gcsBrowserFile ? gcsBrowserFile.name : "No file chosen"}
                  </span>
                </div>

                {(isGcsRunning || gcsStatus === "COMPLETED" || gcsStatus === "FAILED") && (
                  <div className="grid gap-[6px]">
                    <div className="h-[3px] rounded bg-[var(--border)] overflow-hidden">
                      <div className="stage-progress-fill" style={{ width: `${gcsProgress * 100}%` }} />
                    </div>
                    <p className={`m-0 font-mono text-[11px] leading-[1.55] ${gcsStatus === "FAILED" ? "text-[var(--red)]" : gcsStatus === "COMPLETED" ? "text-[var(--green)]" : "text-[var(--text-3)]"}`}>
                      {gcsError ?? gcsMessage}
                    </p>
                  </div>
                )}

                <button
                  className="primary-action"
                  style={{ width: "auto", alignSelf: "end" }}
                  onClick={handleGCSUpload}
                  disabled={isGcsRunning || (!gcsSourcePath.trim() && !gcsBrowserFile)}
                >
                  {isGcsRunning ? <Loader2 size={14} className="animate-spin-slow" /> : <CloudUpload size={14} />}
                  {isGcsRunning ? "Uploading…" : "Upload to GCS"}
                </button>
              </div>

              {/* Milvus card */}
              <div className="rounded-[10px] border border-[var(--border-hi)] bg-[var(--bg-raised)] p-4 grid gap-[11px]">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="inline-grid place-items-center h-5 px-2 rounded bg-purple-500/10 border border-purple-500/30 font-mono text-[9.5px] font-bold tracking-[0.04em] text-purple-400">Milvus</span>
                    <span className="text-[13px] font-semibold text-[var(--text-1)]">Vectors → Milvus</span>
                  </div>
                  {milvusStatus === "COMPLETED" && <CheckCircle2 size={15} className="text-[var(--green)] shrink-0" />}
                  {milvusStatus === "FAILED"    && <XCircle size={15} className="text-[var(--red)] shrink-0" />}
                  {isMilvusRunning              && <Loader2 size={15} className="animate-spin-slow text-[var(--accent)] shrink-0" />}
                </div>

                <label className="grid gap-[5px] font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--text-3)]">
                  Features file (.npy / .npz)
                  <input
                    type="text"
                    value={milvusFile}
                    onChange={(e) => setMilvusFile(e.target.value)}
                    placeholder="/data/datasets/v3/features/clip_embeddings.npy"
                    disabled={isMilvusRunning}
                  />
                </label>

                <div className="flex items-center gap-2">
                  <div className="flex-1 h-px bg-[var(--border)]" />
                  <span className="font-mono text-[9.5px] text-[var(--text-3)] uppercase tracking-[0.1em] shrink-0">or from machine</span>
                  <div className="flex-1 h-px bg-[var(--border)]" />
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="file"
                    accept=".npy,.npz,.zip"
                    id="milvus-file-input"
                    className="hidden"
                    disabled={isMilvusRunning}
                    onChange={(e) => setMilvusBrowserFile(e.target.files?.[0] ?? null)}
                  />
                  <label
                    htmlFor="milvus-file-input"
                    className="toggle cursor-pointer shrink-0"
                    style={{ pointerEvents: isMilvusRunning ? "none" : "auto", opacity: isMilvusRunning ? 0.5 : 1 }}
                  >
                    Choose file
                  </label>
                  <span className="font-mono text-[11px] text-[var(--text-3)] truncate">
                    {milvusBrowserFile ? milvusBrowserFile.name : "No file chosen"}
                  </span>
                </div>

                <label className="grid gap-[5px] font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--text-3)]">
                  Collection
                  <input
                    type="text"
                    value={milvusCollection}
                    onChange={(e) => setMilvusCollection(e.target.value)}
                    placeholder="frames"
                    disabled={isMilvusRunning}
                  />
                </label>

                {(isMilvusRunning || milvusStatus === "COMPLETED" || milvusStatus === "FAILED") && (
                  <div className="grid gap-[6px]">
                    <div className="h-[3px] rounded bg-[var(--border)] overflow-hidden">
                      <div className="stage-progress-fill" style={{ width: `${milvusProgress * 100}%` }} />
                    </div>
                    <p className={`m-0 font-mono text-[11px] leading-[1.55] ${milvusStatus === "FAILED" ? "text-[var(--red)]" : milvusStatus === "COMPLETED" ? "text-[var(--green)]" : "text-[var(--text-3)]"}`}>
                      {milvusError ?? milvusMessage}
                    </p>
                  </div>
                )}

                <button
                  className="primary-action"
                  style={{ width: "auto", alignSelf: "end" }}
                  onClick={handleMilvusUpload}
                  disabled={isMilvusRunning || (!milvusFile.trim() && !milvusBrowserFile)}
                >
                  {isMilvusRunning ? <Loader2 size={14} className="animate-spin-slow" /> : <Database size={14} />}
                  {isMilvusRunning ? "Indexing…" : "Index in Milvus"}
                </button>
              </div>
            </div>

            {/* Footer */}
            <div className="flex items-center justify-end border-t border-[var(--border)] px-5 py-[14px] shrink-0">
              <button className="toggle" onClick={handleUploadClose} disabled={isUploadRunning}>
                Close
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}

      {ingestOpen && createPortal(
        <div
          className="fixed inset-0 z-[500] bg-black/70 backdrop-blur-md grid place-items-center p-5 animate-fade-up"
          onClick={(e) => { if (e.target === e.currentTarget) handleIngestClose(); }}
          role="dialog"
          aria-modal="true"
          aria-label="Demo ingest"
        >
          <div className="w-full max-w-[560px] max-h-[calc(100dvh-40px)] overflow-hidden flex flex-col rounded-[14px] border border-[var(--border-hi)] bg-[var(--bg-panel)] shadow-[0_24px_80px_rgba(0,0,0,0.55),0_0_60px_var(--accent-glow)] animate-fade-up">

            {/* Header */}
            <div className="flex items-center justify-between gap-3 px-5 py-4 border-b border-[var(--border)] shrink-0">
              <div>
                <h2 className="m-0 text-[15px] font-bold tracking-tight text-[var(--text-1)]">Demo ingest</h2>
                <p className="m-0 mt-0.5 font-mono text-[11px] text-[var(--text-3)]">Ingest pre-computed demo data into the database</p>
              </div>
              <button
                className="w-7 h-7 grid place-items-center rounded-[6px] border border-transparent text-[var(--text-3)] hover:bg-[var(--red-dim)] hover:border-[var(--red-bdr)] hover:text-[var(--red)] transition-[background,color,border-color] duration-[140ms]"
                onClick={handleIngestClose}
                disabled={isIngestRunning}
                aria-label="Close modal"
              >
                <X size={15} />
              </button>
            </div>

            {/* Body */}
            <div className="flex-1 overflow-y-auto px-5 py-[18px]">
              <div className="grid gap-[13px]">
                <label className="grid gap-[6px] font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--text-3)]">
                  Dataset root path
                  <input
                    type="text"
                    value={datasetRoot}
                    onChange={(e) => setDatasetRoot(e.target.value)}
                    placeholder="/data/demo"
                    disabled={isIngestRunning}
                  />
                </label>
                <div className="grid grid-cols-2 gap-[7px]">
                  <label className="grid gap-[6px] font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--text-3)]">
                    Mode
                    <select
                      value={ingestMode}
                      onChange={(e) => setIngestMode(e.target.value as "demo" | "mock")}
                      disabled={isIngestRunning}
                      className="rounded-[7px] border border-[var(--border-hi)] bg-[var(--bg-raised)] p-[7px_10px] text-[13px] font-medium text-[var(--text-1)] normal-case tracking-normal transition-[border-color,box-shadow] duration-[140ms] ease-[cubic-bezier(0.16,1,0.3,1)] focus:border-[var(--accent)] focus:shadow-[0_0_0_3px_var(--accent-dim)] focus:outline-none"
                    >
                      <option value="demo">Demo pipeline</option>
                      <option value="mock">Mock / dry-run</option>
                    </select>
                  </label>
                  <label className="grid gap-[6px] font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--text-3)]">
                    Dataset code
                    <input
                      type="text"
                      value={targetDatasetCode}
                      onChange={(e) => setTargetDatasetCode(e.target.value)}
                      placeholder="l30-demo"
                      disabled={isIngestRunning}
                    />
                  </label>
                </div>
              </div>

              {(ingestJobMessage || ingestError) && (
                <div className={`flex items-start gap-2 px-3 py-2 rounded-[7px] border mt-3 ${
                  isIngestSettled && ingestJobStatus === "COMPLETED"
                    ? "border-[var(--green-bdr)] bg-[var(--green-dim)]"
                    : isIngestSettled
                    ? "border-[var(--red-bdr)] bg-[var(--red-dim)]"
                    : "border-[var(--border)] bg-[var(--bg-raised)]"
                }`}>
                  <span className={`w-1.5 h-1.5 rounded-full mt-1 shrink-0 ${
                    isIngestSettled && ingestJobStatus === "COMPLETED"
                      ? "bg-[var(--green)]"
                      : isIngestSettled
                      ? "bg-[var(--red)]"
                      : "bg-[var(--accent)]"
                  }`} />
                  <p className="m-0 font-mono text-[11px] text-[var(--text-2)] leading-[1.55]">
                    {ingestError ?? ingestJobMessage}
                  </p>
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="flex items-center justify-end gap-2 border-t border-[var(--border)] px-5 py-[14px] shrink-0">
              <button className="toggle" onClick={handleIngestClose} disabled={isIngestRunning && !isIngestSettled}>
                {isIngestSettled ? "Close" : "Cancel"}
              </button>
              <button
                className="primary-action"
                style={{ width: "auto", paddingInline: "20px" }}
                onClick={handleIngestStart}
                disabled={isIngestRunning}
              >
                {isIngestRunning
                  ? <Loader2 size={15} className="animate-spin-slow" />
                  : <Upload size={15} />}
                {isIngestRunning ? "Running…" : "Start demo ingest"}
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}

      {pipelineOpen && createPortal(
        <div
          className="fixed inset-0 z-[500] bg-black/70 backdrop-blur-md grid place-items-center p-5 animate-fade-up"
          onClick={(e) => { if (e.target === e.currentTarget) handlePipelineClose(); }}
          role="dialog"
          aria-modal="true"
          aria-label="Run video pipeline"
        >
          <div className="w-full max-w-[560px] max-h-[calc(100dvh-40px)] overflow-hidden flex flex-col rounded-[14px] border border-[var(--border-hi)] bg-[var(--bg-panel)] shadow-[0_24px_80px_rgba(0,0,0,0.55),0_0_60px_var(--accent-glow)] animate-fade-up">

            {/* Header */}
            <div className="flex items-center justify-between gap-3 px-5 py-4 border-b border-[var(--border)] shrink-0">
              <div>
                <h2 className="m-0 text-[15px] font-bold tracking-tight text-[var(--text-1)]">Run video pipeline</h2>
                <p className="m-0 mt-0.5 font-mono text-[11px] text-[var(--text-3)]">
                  {"GCS raw video -> frames -> embeddings -> events"}
                </p>
              </div>
              <button
                className="w-7 h-7 grid place-items-center rounded-[6px] border border-transparent text-[var(--text-3)] hover:bg-[var(--red-dim)] hover:border-[var(--red-bdr)] hover:text-[var(--red)] transition-[background,color,border-color] duration-[140ms]"
                onClick={handlePipelineClose}
                disabled={isPipelineRunning}
                aria-label="Close modal"
              >
                <X size={15} />
              </button>
            </div>

            {/* Body */}
            <div className="flex-1 overflow-y-auto px-5 py-[18px]">
              <div className="grid gap-[13px]">
                <label className="grid gap-[6px] font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--text-3)]">
                  Source
                  <select
                    value={pipelineSourceId}
                    onChange={(e) => setPipelineSourceId(e.target.value)}
                    className="rounded-[7px] border border-[var(--border-hi)] bg-[var(--bg-raised)] p-[7px_10px] text-[13px] font-medium text-[var(--text-1)] normal-case tracking-normal transition-[border-color,box-shadow] duration-[140ms] ease-[cubic-bezier(0.16,1,0.3,1)] focus:border-[var(--accent)] focus:shadow-[0_0_0_3px_var(--accent-dim)] focus:outline-none"
                    disabled={isPipelineRunning}
                  >
                    <option value="l21_l30_ai_challenge_2025">AI Challenge 2025 - L21 to L30</option>
                    <option value="k01_k10_data_video_batch_2_1">Data Video Batch 2.1 - K01 to K10</option>
                    <option value="k11_k20_data_video_batch_2_2">Data Video Batch 2.2 - K11 to K20</option>
                  </select>
                </label>
                <details className="rounded-[7px] border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-2">
                  <summary className="cursor-pointer font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--text-3)]">
                    Advanced
                  </summary>
                  <div className="grid gap-[13px] mt-3">
                    <label className="grid gap-[6px] font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--text-3)]">
                      Batch IDs (optional)
                      <input
                        type="text"
                        value={pipelineBatchIds}
                        onChange={(e) => setPipelineBatchIds(e.target.value)}
                        placeholder="L21,L22 or leave empty for all"
                        disabled={isPipelineRunning}
                      />
                    </label>
                    <label className="grid gap-[6px] font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--text-3)]">
                      Source dataset ID override
                      <input
                        type="text"
                        value={pipelineSourceDatasetId}
                        onChange={(e) => setPipelineSourceDatasetId(e.target.value)}
                        placeholder="auto from source config"
                        disabled={isPipelineRunning}
                      />
                    </label>
                    <label className="grid gap-[6px] font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--text-3)]">
                      Video keys override
                      <textarea
                        value={pipelineVideoKeys}
                        onChange={(e) => setPipelineVideoKeys(e.target.value)}
                        placeholder="one GCS object key per line"
                        className="min-h-[80px] resize-y rounded-[7px] border border-[var(--border-hi)] bg-[var(--bg-panel)] p-[7px_10px] text-[12.5px] font-normal leading-[1.6] normal-case text-[var(--text-1)] transition-[border-color,box-shadow] duration-[140ms] ease-[cubic-bezier(0.16,1,0.3,1)] focus:border-[var(--accent)] focus:shadow-[0_0_0_3px_var(--accent-dim)] focus:outline-none"
                        disabled={isPipelineRunning}
                      />
                    </label>
                  </div>
                </details>
              </div>

              {(pipelineJobMessage || pipelineError) && (
                <div className={`flex items-start gap-2 px-3 py-2 rounded-[7px] border mt-3 ${
                  isPipelineSettled && pipelineJobStatus === "COMPLETED"
                    ? "border-[var(--green-bdr)] bg-[var(--green-dim)]"
                    : isPipelineSettled
                    ? "border-[var(--red-bdr)] bg-[var(--red-dim)]"
                    : "border-[var(--border)] bg-[var(--bg-raised)]"
                }`}>
                  <span className={`w-1.5 h-1.5 rounded-full mt-1 shrink-0 ${
                    isPipelineSettled && pipelineJobStatus === "COMPLETED"
                      ? "bg-[var(--green)]"
                      : isPipelineSettled
                      ? "bg-[var(--red)]"
                      : "bg-[var(--accent)]"
                  }`} />
                  <p className="m-0 font-mono text-[11px] text-[var(--text-2)] leading-[1.55]">
                    {pipelineError ?? pipelineJobMessage}
                  </p>
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="flex items-center justify-end gap-2 border-t border-[var(--border)] px-5 py-[14px] shrink-0">
              <button className="toggle" onClick={handlePipelineClose} disabled={isPipelineRunning && !isPipelineSettled}>
                {isPipelineSettled ? "Close" : "Cancel"}
              </button>
              <button
                className="primary-action"
                style={{ width: "auto", paddingInline: "20px" }}
                onClick={handlePipelineStart}
                disabled={isPipelineRunning || !pipelineSourceId.trim()}
              >
                {isPipelineRunning
                  ? <Loader2 size={15} className="animate-spin-slow" />
                  : <Layers size={15} />}
                {isPipelineRunning ? "Running…" : "Start pipeline"}
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </main>
  );
}

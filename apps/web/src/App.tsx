import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import {
  Check,
  CheckCircle2,
  Circle,
  Clock,
  CloudUpload,
  Database,
  Download,
  FileArchive,
  Layers,
  Loader2,
  Monitor,
  Moon,
  Search,
  Settings,
  Sparkles,
  Sun,
  Trash2,
  Upload,
  X,
  XCircle
} from "lucide-react";
import { createAndExportSubmission, getFrameContext, getIngestJob, listDatasets, mediaUrl, runSearch, startGCSUpload, startIngestJob, startMilvusUpload, uploadFileToGCS, uploadFileToMilvus } from "./api/client";
import type { Dataset, FrameContext, IngestJobStatus, QueryType, SearchResult, SubmissionRow } from "./types";

type StageDisplayStatus = "pending" | "running" | "done" | "failed";

const PIPELINE_STAGES = [
  { id: "dataset_scan", label: "Dataset scan",  badge: null,      badgeLabel: null,    description: "Walks keyframes dir, registers Video/Frame rows in the database." },
  { id: "embedding",    label: "Embedding",     badge: null,      badgeLabel: null,    description: "Loads pre-computed CLIP .npy vectors or runs CLIP model on frames." },
  { id: "gcs_upload",  label: "GCS upload",    badge: "gcs",     badgeLabel: "GCS",   description: "Uploads frame JPG thumbnails to Google Cloud Storage." },
  { id: "milvus_index",label: "Milvus index",  badge: "milvus",  badgeLabel: "Milvus",description: "Upserts CLIP embedding vectors into the Milvus collection for search." }
] as const;

function resolveStageStatuses(
  status: IngestJobStatus | null,
  progress: number
): Record<string, StageDisplayStatus> {
  const ids = ["dataset_scan", "embedding", "gcs_upload", "milvus_index"];
  if (!status) return Object.fromEntries(ids.map((id) => [id, "pending" as const]));
  if (status === "COMPLETED") return Object.fromEntries(ids.map((id) => [id, "done" as const]));
  const runningIdx = Math.min(Math.floor(progress * 4), 3);
  return Object.fromEntries(
    ids.map((id, i) => [
      id,
      i < runningIdx ? "done" : i === runningIdx ? (status === "FAILED" ? "failed" : "running") : "pending"
    ])
  );
}

const sampleQueries: Record<QueryType, string> = {
  KIS: "The clip shows an exhibition program with a royal-style decorative panel, dragon and cloud motifs, and the text PHU XUAN GIA DINH.",
  QA: "Identify the name of the world-famous company whose logo was inspired by a castle in Bavaria, Germany.",
  TRAKE: "In a bicycle race, first a cyclist with a pink helmet crosses the finish line, then a cyclist with a blue helmet, then a cyclist with a red helmet."
};

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
  const [selected, setSelected] = useState<SubmissionRow[]>([]);
  const [context, setContext] = useState<FrameContext | null>(null);
  const [activeResultId, setActiveResultId] = useState<string | null>(null);
  const [status, setStatus] = useState("Ready");
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);

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
  const [manifestPath, setManifestPath] = useState("");
  const [ingestMode, setIngestMode] = useState<"real" | "mock">("real");
  const [targetDatasetId, setTargetDatasetId] = useState("");
  const [ingestJobId, setIngestJobId] = useState<string | null>(null);
  const [ingestJobStatus, setIngestJobStatus] = useState<IngestJobStatus | null>(null);
  const [ingestJobProgress, setIngestJobProgress] = useState(0);
  const [ingestJobMessage, setIngestJobMessage] = useState("");
  const [ingestError, setIngestError] = useState<string | null>(null);

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

  const isGcsRunning = gcsStatus === "PENDING" || gcsStatus === "RUNNING";
  const isGcsSettled = gcsStatus === "COMPLETED" || gcsStatus === "FAILED";
  const isMilvusRunning = milvusStatus === "PENDING" || milvusStatus === "RUNNING";
  const isMilvusSettled = milvusStatus === "COMPLETED" || milvusStatus === "FAILED";
  const isUploadRunning = isGcsRunning || isMilvusRunning;

  const isIngestRunning = ingestJobStatus === "PENDING" || ingestJobStatus === "RUNNING";
  const isIngestSettled = ingestJobStatus === "COMPLETED" || ingestJobStatus === "FAILED";
  const stageStatuses = resolveStageStatuses(ingestJobStatus, ingestJobProgress);

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

  function changeType(type: QueryType) {
    setQueryType(type);
    setQueryText(sampleQueries[type]);
    setQueryName(type === "KIS" ? "query-1-kis" : type === "QA" ? "query-2-qa" : "query-3-trake");
    setResults([]);
    setContext(null);
  }

  async function submitSearch() {
    if (!datasetId) return;
    setStatus("Searching");
    setDownloadUrl(null);
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
      setStatus(`${response.results.length} results from ${response.query_run_id.slice(0, 8)}`);
      const firstFrame = response.results.find((item) => item.frame_id);
      if (firstFrame?.frame_id) void openContext(firstFrame);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Search failed");
    }
  }

  async function openContext(result: SearchResult) {
    if (!result.frame_id) return;
    setActiveResultId(result.id);
    try {
      setContext(await getFrameContext(result.frame_id));
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Context failed");
    }
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
    setIngestJobMessage("Starting pipeline...");
    try {
      const job = await startIngestJob({
        dataset_id: targetDatasetId || undefined,
        manifest_path: manifestPath.trim() || undefined,
        mode: ingestMode
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
          <select value={datasetId} onChange={(event) => setDatasetId(event.target.value)} aria-label="Dataset" className="min-w-0 rounded-[7px] border border-[var(--border-hi)] bg-surface-raised p-[7px_10px] text-[13px] font-medium text-txt-1 transition-[border-color,box-shadow] duration-[140ms] ease-[cubic-bezier(0.16,1,0.3,1)] focus:border-accent focus:shadow-[0_0_0_3px_var(--accent-dim)] focus:outline-none">
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
            onClick={() => { setTargetDatasetId(datasetId); setIngestOpen(true); }}
            aria-label="Open ingest data modal"
          >
            <Upload size={13} />
            Ingest
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
              <h2 className="text-[13px] font-bold tracking-[-0.01em] text-txt-1">Results</h2>
              <p className="mt-[2px] font-mono text-[10.5px] text-txt-3">{results.length} ranked candidates</p>
            </div>
            <Settings size={16} />
          </div>
          {results.length > 0 ? (
            <div className="result-grid min-h-0 grid content-start gap-[9px] overflow-auto" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(220px,1fr))" }}>
              {results.map((result, index) => (
                <article
                  key={result.id}
                  className={[
                    "result-card",
                    !result.thumbnail_url ? "no-img" : "",
                    activeResultId === result.id ? "active" : ""
                  ].filter(Boolean).join(" ")}
                  style={{ animationDelay: `${index * 35}ms` }}
                  onClick={() => openContext(result)}
                >
                  {result.thumbnail_url && (
                    <div className="result-img">
                      <img src={mediaUrl(result.thumbnail_url) ?? ""} alt={`${result.video_code} frame ${result.frame_idx}`} />
                      <span className="result-rank">#{result.rank}</span>
                    </div>
                  )}
                  <div className="grid gap-[7px] p-[10px] content-start">
                    <div className="rank-line">
                      {!result.thumbnail_url && <span>#{result.rank}</span>}
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
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <div className="result-empty">
              <Search size={38} />
              <p>{isSearching ? "Searching…" : "Run a query to see ranked candidates"}</p>
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
            {context?.frames.map((frame) => (
              <div key={frame.id} className={frame.id === context.target_frame_id ? "context-frame target" : "context-frame"}>
                <img src={mediaUrl(frame.thumbnail_url) ?? ""} alt={`Frame ${frame.frame_idx}`} />
                <div>
                  <strong>{frame.frame_idx}</strong>
                  <p>{frame.text}</p>
                </div>
              </div>
            ))}
          </div>
        </aside>
      </section>

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
          aria-label="Ingest data"
        >
          <div className="w-full max-w-[560px] max-h-[calc(100dvh-40px)] overflow-hidden flex flex-col rounded-[14px] border border-[var(--border-hi)] bg-[var(--bg-panel)] shadow-[0_24px_80px_rgba(0,0,0,0.55),0_0_60px_var(--accent-glow)] animate-fade-up">

            {/* Header */}
            <div className="flex items-center justify-between gap-3 px-5 py-4 border-b border-[var(--border)] shrink-0">
              <div>
                <h2 className="m-0 text-[15px] font-bold tracking-tight text-[var(--text-1)]">Ingest data</h2>
                <p className="m-0 mt-0.5 font-mono text-[11px] text-[var(--text-3)]">Run the 4-stage ingestion pipeline for a dataset</p>
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

              {/* Form */}
              <div className="grid gap-[13px]">
                <label className="grid gap-[6px] font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--text-3)]">
                  Manifest YAML path
                  <input
                    type="text"
                    value={manifestPath}
                    onChange={(e) => setManifestPath(e.target.value)}
                    placeholder="/data/datasets/v3/manifest.yaml"
                    disabled={isIngestRunning}
                  />
                </label>
                <div className="grid grid-cols-2 gap-[7px]">
                  <label className="grid gap-[6px] font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--text-3)]">
                    Mode
                    <select
                      value={ingestMode}
                      onChange={(e) => setIngestMode(e.target.value as "real" | "mock")}
                      disabled={isIngestRunning}
                      className="rounded-[7px] border border-[var(--border-hi)] bg-[var(--bg-raised)] p-[7px_10px] text-[13px] font-medium text-[var(--text-1)] normal-case tracking-normal transition-[border-color,box-shadow] duration-[140ms] ease-[cubic-bezier(0.16,1,0.3,1)] focus:border-[var(--accent)] focus:shadow-[0_0_0_3px_var(--accent-dim)] focus:outline-none"
                    >
                      <option value="real">Real pipeline</option>
                      <option value="mock">Mock / dry-run</option>
                    </select>
                  </label>
                  <label className="grid gap-[6px] font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--text-3)]">
                    Dataset ID
                    <input
                      type="text"
                      value={targetDatasetId}
                      onChange={(e) => setTargetDatasetId(e.target.value)}
                      placeholder="auto-detect"
                      disabled={isIngestRunning}
                    />
                  </label>
                </div>
              </div>

              {/* Stage rail */}
              <h3 className="m-0 mt-5 mb-2 font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--text-3)]">
                Pipeline stages
              </h3>
              <div className="grid gap-0">
                {PIPELINE_STAGES.map((stage, i) => {
                  const stStatus = stageStatuses[stage.id] ?? "pending";
                  const isLast = i === PIPELINE_STAGES.length - 1;
                  const dotColors: Record<StageDisplayStatus, string> = {
                    pending: "bg-[var(--border-hi)]",
                    running: "bg-[var(--accent)] stage-dot-running",
                    done: "bg-[var(--green)]",
                    failed: "bg-[var(--red)]"
                  };
                  const subProgress = Math.min(Math.max(ingestJobProgress * 4 - i, 0), 1);
                  return (
                    <div key={stage.id} className="grid grid-cols-[28px_1fr] gap-x-3">
                      {/* Rail */}
                      <div className="flex flex-col items-center pt-[3px]">
                        <div className={`w-[10px] h-[10px] rounded-full border-2 border-[var(--bg-panel)] shrink-0 ${dotColors[stStatus]}`} />
                        {!isLast && <div className="w-px flex-1 min-h-3 bg-[var(--border)] my-[3px]" />}
                      </div>
                      {/* Body */}
                      <div className={isLast ? "pb-1" : "pb-4"}>
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center min-w-0">
                            <strong className="text-[12.5px] font-semibold text-[var(--text-1)]">{stage.label}</strong>
                            {stage.badge === "gcs" && (
                              <span className="inline-grid place-items-center h-4 px-1.5 ml-[7px] rounded bg-blue-500/10 border border-blue-500/30 font-mono text-[9.5px] font-bold tracking-[0.04em] text-blue-400">
                                {stage.badgeLabel}
                              </span>
                            )}
                            {stage.badge === "milvus" && (
                              <span className="inline-grid place-items-center h-4 px-1.5 ml-[7px] rounded bg-purple-500/10 border border-purple-500/30 font-mono text-[9.5px] font-bold tracking-[0.04em] text-purple-400">
                                {stage.badgeLabel}
                              </span>
                            )}
                          </div>
                          <span className="shrink-0">
                            {stStatus === "done"    && <CheckCircle2 size={14} className="text-[var(--green)]" />}
                            {stStatus === "failed"  && <XCircle size={14} className="text-[var(--red)]" />}
                            {stStatus === "running" && <Loader2 size={14} className="animate-spin-slow text-[var(--accent)]" />}
                            {stStatus === "pending" && <Circle size={14} className="text-[var(--text-3)]" />}
                          </span>
                        </div>
                        <p className="m-0 mt-[3px] font-mono text-[11px] text-[var(--text-3)] leading-[1.55]">{stage.description}</p>
                        {stStatus === "running" && (
                          <div className="h-[3px] rounded bg-[var(--border)] overflow-hidden mt-2 max-w-[320px]">
                            <div className="stage-progress-fill" style={{ width: `${subProgress * 100}%` }} />
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Log line */}
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
                disabled={isIngestRunning || !manifestPath.trim()}
              >
                {isIngestRunning
                  ? <Loader2 size={15} className="animate-spin-slow" />
                  : <Upload size={15} />}
                {isIngestRunning ? "Running…" : "Start ingest"}
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </main>
  );
}

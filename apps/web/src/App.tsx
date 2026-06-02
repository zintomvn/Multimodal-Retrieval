import { useEffect, useMemo, useState } from "react";
import {
  Check,
  Clock,
  Database,
  Download,
  FileArchive,
  Layers,
  Search,
  Settings,
  Sparkles,
  Trash2
} from "lucide-react";
import { createAndExportSubmission, getFrameContext, listDatasets, mediaUrl, runSearch } from "./api/client";
import type { Dataset, FrameContext, QueryType, SearchResult, SubmissionRow } from "./types";

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

  useEffect(() => {
    listDatasets()
      .then((items) => {
        setDatasets(items);
        if (items[0]) setDatasetId(items[0].id);
      })
      .catch((error) => setStatus(error.message));
  }, []);

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

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <h1>Multimodal Retrieval Assistant</h1>
          <p>{activeDataset ? `${activeDataset.name}:${activeDataset.version} · ${activeDataset.video_count} videos` : "No dataset"}</p>
        </div>
        <div className="topbar-actions">
          <select value={datasetId} onChange={(event) => setDatasetId(event.target.value)} aria-label="Dataset">
            {datasets.map((dataset) => (
              <option key={dataset.id} value={dataset.id}>
                {dataset.name}:{dataset.version}
              </option>
            ))}
          </select>
          <span className="status-pill">
            <Database size={16} />
            {status}
          </span>
        </div>
      </header>

      <section className="workspace">
        <aside className="query-panel">
          <div className="segmented" aria-label="Query type">
            {(["KIS", "QA", "TRAKE"] as QueryType[]).map((type) => (
              <button key={type} className={queryType === type ? "active" : ""} onClick={() => changeType(type)}>
                {type}
              </button>
            ))}
          </div>

          <label>
            Query file
            <input value={queryName} onChange={(event) => setQueryName(event.target.value)} />
          </label>

          <label className="query-text">
            Query
            <textarea value={queryText} onChange={(event) => setQueryText(event.target.value)} />
          </label>

          <div className="settings-row">
            <label>
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

          <div className="selection-panel">
            <div className="panel-heading">
              <FileArchive size={17} />
              Selected
              <span>{selected.length}</span>
            </div>
            <div className="selected-list">
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
              <a className="download-link" href={downloadUrl}>
                Download
              </a>
            )}
          </div>
        </aside>

        <section className="results-panel">
          <div className="panel-toolbar">
            <div>
              <h2>Results</h2>
              <p>{results.length} ranked candidates</p>
            </div>
            <Settings size={18} />
          </div>
          <div className="result-grid">
            {results.map((result) => (
              <article key={result.id} className={activeResultId === result.id ? "result-card active" : "result-card"} onClick={() => openContext(result)}>
                {result.thumbnail_url && <img src={mediaUrl(result.thumbnail_url) ?? ""} alt={`${result.video_code} frame ${result.frame_idx}`} />}
                <div className="result-body">
                  <div className="rank-line">
                    <span>#{result.rank}</span>
                    <strong>{result.video_code}</strong>
                    <code>{result.frame_idx ?? result.sequence_frames.map((item) => item.frame_idx).join(" · ")}</code>
                  </div>
                  <div className="score-line">
                    <Clock size={14} />
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
                    <Check size={16} />
                    Select
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>

        <aside className="context-panel">
          <div className="panel-toolbar">
            <div>
              <h2>Context</h2>
              <p>{context?.video_code ?? "No frame"}</p>
            </div>
          </div>
          <div className="context-list">
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
    </main>
  );
}

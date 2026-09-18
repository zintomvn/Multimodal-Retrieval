from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BENCHMARK = Path("data/experiments/aic_2026_groundtruth")
MODEL_LABELS = {
    "openclip": "OpenCLIP",
    "siglip2": "SigLIP2",
    "qwen3_vl": "Qwen3-VL-Embedding-2B",
}


@dataclass(frozen=True)
class Query:
    query_id: str
    text: str
    video_id: str
    frame_ids: tuple[int, ...]


def post_json(url: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail[:1000]}") from exc


def parse_frame_ids(raw: str) -> tuple[int, ...]:
    values: list[int] = []
    for value in str(raw or "").replace(";", ",").split(","):
        value = value.strip()
        if value:
            values.append(int(value))
    return tuple(values)


def parse_raw_answer(raw: str) -> tuple[str, tuple[int, ...]]:
    value = str(raw or "").strip()
    video_match = re.search(r"L\d+_(?:V)?\d+", value, flags=re.IGNORECASE)
    if not video_match:
        return "", ()
    video_id = video_match.group(0).upper()
    if "_V" not in video_id:
        video_id = video_id.replace("_", "_V", 1)
    suffix = value[video_match.end() :]
    frame_ids = tuple(int(item) for item in re.findall(r"\d+", suffix))
    return video_id, frame_ids


def load_queries(path: Path, include_uncertain: bool = False) -> list[Query]:
    input_files = sorted(path.glob("*.csv")) if path.is_dir() else [path]
    queries: list[Query] = []
    query_pattern = re.compile(r"(query-p\d+-\d+-(kis|qa|trake))", re.IGNORECASE)
    namespace_ids = len(input_files) > 1
    for input_file in input_files:
        with input_file.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if "GT Video ID" in row:
                    if row.get("Task Type", "").strip().upper() != "KIS":
                        continue
                    if row.get("Status", "").strip().upper() != "OK":
                        continue
                    query_id = row.get("Query ID", "").strip()
                    query_text = row.get("Query Text", "").strip()
                    video_id = row.get("GT Video ID", "").strip()
                    frame_ids = parse_frame_ids(row.get("GT Frame ID(s)", ""))
                else:
                    status = row.get("Trạng thái", "").strip()
                    if not include_uncertain and status.casefold() != "chắn chắn".casefold():
                        continue
                    filename_hint = row.get("Tên file (optional)", "") or ""
                    raw_query = row.get("Query", "") or ""
                    match = query_pattern.search(f"{filename_hint} {raw_query}")
                    if not match or match.group(2).upper() != "KIS":
                        continue
                    query_id = match.group(1).lower()
                    query_match = query_pattern.search(raw_query)
                    query_text = raw_query[query_match.end() :].strip(" \t\r\n:-") if query_match else raw_query.strip()
                    video_id, frame_ids = parse_raw_answer(row.get("Đáp án", ""))
                    if namespace_ids:
                        query_id = f"{input_file.stem}::{query_id}"
                if query_id and query_text and video_id and frame_ids:
                    queries.append(Query(query_id=query_id, text=query_text, video_id=video_id, frame_ids=frame_ids))
    if not queries:
        raise ValueError(f"No usable KIS queries with ground truth found in {path}")
    return queries


def filter_queries_by_batch(queries: list[Query], batch_min: int = 0, batch_max: int = 0) -> list[Query]:
    if batch_min <= 0 and batch_max <= 0:
        return queries
    output: list[Query] = []
    for query in queries:
        match = re.match(r"L(\d+)_", query.video_id, flags=re.IGNORECASE)
        if not match:
            continue
        batch_number = int(match.group(1))
        if batch_min > 0 and batch_number < batch_min:
            continue
        if batch_max > 0 and batch_number > batch_max:
            continue
        output.append(query)
    if not output:
        raise ValueError(f"No queries remain after batch filter {batch_min}..{batch_max}")
    return output


def dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        key = clean.casefold()
        if clean and key not in seen:
            output.append(clean)
            seen.add(key)
    return output


def plan_perspectives(args: argparse.Namespace, queries: list[Query], required: int) -> dict[str, list[str]]:
    if args.perspectives_file.exists():
        raw = json.loads(args.perspectives_file.read_text(encoding="utf-8"))
        perspectives = {str(key): dedupe(list(value)) for key, value in raw.items()}
    else:
        perspectives = {}
        endpoint = f"{args.api_base.rstrip('/')}/api/retrieval/plan"
        for index, query in enumerate(queries, start=1):
            payload = {
                "dataset_id": args.dataset_id,
                "query_name": query.query_id,
                "query_type": "KIS",
                "query_text": query.text,
                "top_k": args.top_k,
                "profile": args.profile,
                "options": {
                    "use_query_expansion": False,
                    "use_agent_query_planning": True,
                    "use_metadata": False,
                    "use_reranker": False,
                },
            }
            response = post_json(endpoint, payload, args.timeout_s)
            normalized = response.get("normalized_query") or {}
            views = normalized.get("semantic_variants") or normalized.get("multi_views") or []
            perspectives[query.query_id] = dedupe([str(item) for item in views])
            print(json.dumps({"stage": "plan", "index": index, "query_id": query.query_id}, ensure_ascii=False))
        args.perspectives_file.parent.mkdir(parents=True, exist_ok=True)
        args.perspectives_file.write_text(
            json.dumps(perspectives, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    missing = {
        query.query_id: len(perspectives.get(query.query_id, []))
        for query in queries
        if len(perspectives.get(query.query_id, [])) < required
    }
    if missing:
        raise ValueError(
            f"Perspective file must contain at least {required} frozen views for every query; insufficient: {missing}"
        )
    return perspectives


def find_rank(results: list[dict[str, Any]], query: Query, tolerance: int) -> int:
    for rank, result in enumerate(results, start=1):
        video_id = str(result.get("video_code") or result.get("video_id") or "")
        frame_idx = result.get("frame_idx")
        if video_id != query.video_id or frame_idx is None:
            continue
        if any(abs(int(frame_idx) - expected) <= tolerance for expected in query.frame_ids):
            return rank
    return -1


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def summarize(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault((record["model"], record["query_mode"], str(record["n"])), []).append(record)
    rows: list[dict[str, Any]] = []
    for (model, query_mode, n), group in grouped.items():
        ranks = [int(item["rank"]) for item in group]
        latencies = [float(item["latency_ms"]) for item in group]
        found = [rank for rank in ranks if rank > 0]
        row: dict[str, Any] = {
            "model": model,
            "query_mode": query_mode,
            "n": n,
            "queries": len(group),
            "MRR": sum((1.0 / rank) if rank > 0 else 0.0 for rank in ranks) / len(ranks),
            "mean_latency_ms": statistics.fmean(latencies),
            "p50_latency_ms": percentile(latencies, 0.50),
            "p95_latency_ms": percentile(latencies, 0.95),
            "misses_at_100": len(ranks) - len(found),
        }
        for k in (1, 5, 10, 50, 100):
            row[f"Recall@{k}"] = sum(1 for rank in ranks if 0 < rank <= k) / len(ranks)
        rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_rank_tables(output_dir: Path, queries: list[Query], records: list[dict[str, Any]]) -> None:
    query_aliases = {query.query_id: f"q{index}" for index, query in enumerate(queries, start=1)}
    grouped: dict[tuple[str, str, str], dict[str, int]] = {}
    for record in records:
        key = (record["model"], record["query_mode"], str(record["n"]))
        grouped.setdefault(key, {})[record["query_id"]] = int(record["rank"])

    header = ["Model", "Mode", "n", *query_aliases.values()]
    markdown = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    latex = ["\\begin{tabular}{lll" + "r" * len(queries) + "}", "\\toprule", " & ".join(header) + r" \\", "\\midrule"]
    for (model, mode, n), ranks in grouped.items():
        values = [MODEL_LABELS.get(model, model), mode, n, *[str(ranks[query.query_id]) for query in queries]]
        markdown.append("| " + " | ".join(values) + " |")
        latex.append(" & ".join(value.replace("_", r"\_") for value in values) + r" \\")
    latex.extend(["\\bottomrule", "\\end{tabular}"])
    mapping = ["", "Query mapping:", *[f"- {alias}: `{query_id}`" for query_id, alias in query_aliases.items()]]
    (output_dir / "rank_table.md").write_text("\n".join(markdown + mapping) + "\n", encoding="utf-8")
    (output_dir / "rank_table.tex").write_text("\n".join(latex) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Controlled KIS embedding ablation: full query versus frozen multi-perspective queries."
    )
    parser.add_argument("--benchmark-csv", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument(
        "--include-uncertain",
        action="store_true",
        help="Also include answered rows marked Cân nhắc/Đang làm/Không tra ra in the raw ground-truth files.",
    )
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--dataset-id", default=None)
    parser.add_argument("--profile", default="embedding_ablation")
    parser.add_argument("--models", nargs="+", choices=tuple(MODEL_LABELS), default=list(MODEL_LABELS))
    parser.add_argument("--perspective-counts", nargs="+", type=int, default=[3, 5, 7])
    parser.add_argument("--top-k", type=int, default=100)
    parser.add_argument("--frame-tolerance", type=int, default=0)
    parser.add_argument("--timeout-s", type=float, default=180.0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-min", type=int, default=0)
    parser.add_argument("--batch-max", type=int, default=0)
    parser.add_argument("--validate-only", action="store_true", help="Validate and summarize ground truth without calling the backend.")
    parser.add_argument("--plan-only", action="store_true", help="Generate/validate frozen perspectives, then stop before retrieval.")
    parser.add_argument("--sleep-s", type=float, default=0.0)
    parser.add_argument("--output-dir", type=Path, default=Path("data/experiments/embedding_ablation"))
    parser.add_argument("--perspectives-file", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.batch_min < 0 or args.batch_max < 0 or (args.batch_max and args.batch_min > args.batch_max):
        raise ValueError("invalid batch range")
    if args.top_k < 100:
        raise ValueError("top_k must be at least 100 to report Recall@100 and -1 misses consistently")
    if not args.perspective_counts or min(args.perspective_counts) < 2 or max(args.perspective_counts) > 8:
        raise ValueError("perspective counts must be between 2 and 8")
    queries = load_queries(args.benchmark_csv, include_uncertain=args.include_uncertain)
    queries = filter_queries_by_batch(queries, args.batch_min, args.batch_max)
    if args.limit > 0:
        queries = queries[: args.limit]
    if args.validate_only:
        by_source: dict[str, int] = {}
        for query in queries:
            source = query.query_id.split("::", 1)[0] if "::" in query.query_id else str(args.benchmark_csv)
            by_source[source] = by_source.get(source, 0) + 1
        print(
            json.dumps(
                {
                    "ok": True,
                    "queries": len(queries),
                    "by_source": by_source,
                    "sample": [
                        {"query_id": item.query_id, "gt_video_id": item.video_id, "gt_frame_ids": item.frame_ids}
                        for item in queries[:3]
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.perspectives_file is None:
        args.perspectives_file = args.output_dir / "perspectives.json"
    perspectives = plan_perspectives(args, queries, max(args.perspective_counts))
    if args.plan_only:
        print(json.dumps({"ok": True, "queries": len(queries), "perspectives_file": str(args.perspectives_file)}, ensure_ascii=False))
        return

    records: list[dict[str, Any]] = []
    raw_path = args.output_dir / "raw_responses.jsonl"
    endpoint = f"{args.api_base.rstrip('/')}/api/retrieval/search"
    configurations = [("full_query", 1), *[("perspective", n) for n in args.perspective_counts]]
    with raw_path.open("w", encoding="utf-8") as raw_handle:
        for model in args.models:
            for mode, n in configurations:
                for index, query in enumerate(queries, start=1):
                    views = [query.text] if mode == "full_query" else perspectives[query.query_id][:n]
                    payload = {
                        "dataset_id": args.dataset_id,
                        "query_name": f"ablation::{model}::{mode}-{n}::{query.query_id}",
                        "query_type": "KIS",
                        "query_text": query.text,
                        "top_k": args.top_k,
                        "profile": args.profile,
                        "options": {
                            "use_query_expansion": False,
                            "use_agent_query_planning": False,
                            "use_metadata": False,
                            "use_reranker": False,
                            "strict_hybrid": True,
                            "visual_search_mode": model,
                            "semantic_views": views,
                            "semantic_fusion": "multiperspective" if len(views) > 1 else "max_similarity",
                            "temporal_mode": False,
                        },
                    }
                    started = time.perf_counter()
                    response = post_json(endpoint, payload, args.timeout_s)
                    latency_ms = (time.perf_counter() - started) * 1000.0
                    results = list(response.get("results") or [])
                    record = {
                        "model": model,
                        "query_mode": mode,
                        "n": n,
                        "query_id": query.query_id,
                        "gt_video_id": query.video_id,
                        "gt_frame_ids": ";".join(str(value) for value in query.frame_ids),
                        "rank": find_rank(results, query, args.frame_tolerance),
                        "latency_ms": round(latency_ms, 3),
                        "returned": len(results),
                    }
                    records.append(record)
                    raw_handle.write(
                        json.dumps({"record": record, "views": views, "response": response}, ensure_ascii=False) + "\n"
                    )
                    raw_handle.flush()
                    print(
                        json.dumps(
                            {"model": model, "mode": mode, "n": n, "index": index, "query_id": query.query_id, "rank": record["rank"]},
                            ensure_ascii=False,
                        )
                    )
                    if args.sleep_s > 0:
                        time.sleep(args.sleep_s)

    summary = summarize(records)
    write_csv(args.output_dir / "per_query.csv", records)
    write_csv(args.output_dir / "summary.csv", summary)
    write_rank_tables(args.output_dir, queries, records)
    run_config = {
        "benchmark_csv": str(args.benchmark_csv),
        "models": args.models,
        "perspective_counts": args.perspective_counts,
        "top_k": args.top_k,
        "frame_tolerance": args.frame_tolerance,
        "profile": args.profile,
        "dataset_id": args.dataset_id,
        "perspectives_file": str(args.perspectives_file),
        "query_count": len(queries),
        "batch_min": args.batch_min,
        "batch_max": args.batch_max,
    }
    (args.output_dir / "run_config.json").write_text(
        json.dumps(run_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"queries": len(queries), "experiments": len(records), "output_dir": str(args.output_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

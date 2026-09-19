"""Reproducible Exp-1: ATS, Vortex, and DEV temporal-search comparison.

The only experimental factor is ``temporal_strategy``.  GPT-4o and the
SigLIP2 semantic-search branch are fixed for every request.  Results are
checkpointed after every query, so an interrupted run can be resumed safely.
"""
from __future__ import annotations

import argparse
import csv
import http.client
import json
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
BENCHMARK = ROOT / "data/experiments/aic_2026_groundtruth/aic_2026_GT_dataset.csv"
OUTPUT_DIR = ROOT / "data/experiments/results/exp1_temporal_search_v2"
API_URL = "http://127.0.0.1:8000/api/retrieval/search"
PLAN_URL = "http://127.0.0.1:8000/api/retrieval/plan"
DATASET_ID = "917efbbe-44b8-4476-98ef-3e7ec7ca7958"
PROFILE = "competition_default"
TOP_K = 100
DEADLINE_MS = 120_000
HTTP_TIMEOUT_S = 180

METHODS = {
    "ATS": "aithena_weighted_ats",
    "Vortex": "vortex_k_context",
    "DEV": "dev_first_search",
}
RECALL_KS = (1, 5, 10, 20, 50, 100)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_benchmark(path: Path) -> list[dict[str, Any]]:
    """Load the supplied UTF-8 benchmark without changing query text."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"Original Query ID", "Query Text", "Task Type", "GT Video ID", "GT Frame ID"}
    missing = required - set(rows[0] if rows else {})
    if missing:
        raise ValueError(f"Benchmark is missing columns: {sorted(missing)}")
    return [
        {
            "query_id": row["Original Query ID"].strip(),
            "query_text": row["Query Text"].strip(),
            "query_type": row["Task Type"].strip().upper(),
            "gt_video": row["GT Video ID"].strip(),
            "gt_frame": int(row["GT Frame ID"]),
        }
        for row in rows
    ]


def request_search(payload: dict[str, Any]) -> dict[str, Any]:
    return request_json(API_URL, payload)


def request_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


def build_payload(row: dict[str, Any], strategy: str) -> dict[str, Any]:
    return {
        "dataset_id": DATASET_ID,
        "query_name": f"exp1-{strategy}-{row['query_id']}",
        "query_type": row["query_type"],
        "query_text": row["query_text"],
        "top_k": TOP_K,
        "profile": PROFILE,
        "options": {
            "temporal_mode": True,
            "temporal_strategy": strategy,
            "use_query_expansion": False,
            "use_agent_query_planning": True,
            "agent_model": "gpt-4o",
            "visual_search_mode": "siglip2",
            "use_metadata": True,
            "use_reranker": False,
            "delta_t_max_ms": 180_000,
            "deadline_ms": DEADLINE_MS,
            "debug_filters": True,
        },
    }


def build_plan_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Create one GPT-4o plan before the three strategy calls.

    ``AgentQueryPlanner`` has a process-wide cache keyed by profile, prompt,
    query type, query text and temporal mode (not strategy).  Calling /plan
    first therefore makes the exact same LLM plan available to ATS, Vortex,
    and DEV without each search request waiting on GPT-4o's 15-second search
    planning budget.
    """
    payload = build_payload(row, "aithena_weighted_ats")
    payload["query_name"] = f"exp1-plan-{row['query_id']}"
    return payload


def first_rank(results: list[dict[str, Any]], predicate: Any) -> int | None:
    return next((i for i, item in enumerate(results, 1) if predicate(item)), None)


def evaluate(row: dict[str, Any], response: dict[str, Any], latency_ms: int) -> dict[str, Any]:
    results = response.get("results", [])
    normalized = response.get("normalized_query") or {}
    agent_plan = normalized.get("agent_query_plan") or {}
    agent_metadata = agent_plan.get("agent_metadata") or {}
    video_rank = first_rank(results, lambda item: item.get("video_code") == row["gt_video"])
    exact_frame_rank = first_rank(
        results,
        lambda item: item.get("video_code") == row["gt_video"] and item.get("frame_idx") == row["gt_frame"],
    )
    sequence_frame_rank = first_rank(
        results,
        lambda item: item.get("video_code") == row["gt_video"]
        and any(frame.get("frame_idx") == row["gt_frame"] for frame in item.get("sequence_frames", [])),
    )
    top = results[0] if results else {}
    return {
        **row,
        "latency_ms": latency_ms,
        "result_count": len(results),
        "rank": video_rank,
        "exact_frame_rank": exact_frame_rank,
        "sequence_frame_rank": sequence_frame_rank,
        "top_video": top.get("video_code"),
        "top_frame": top.get("frame_idx"),
        "planner_source": agent_plan.get("source"),
        "planner_model": agent_metadata.get("model"),
        "error": "",
    }


def read_checkpoint(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    records: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                previous = records.get(record["query_id"])
                # A later accidental retry must never replace a valid answer
                # with a transient 504 result.
                if previous is None or (previous.get("error") and not record.get("error")) or bool(record.get("error")) == bool(previous.get("error")):
                    records[record["query_id"]] = record
    return records


def metrics_for_records(method: str, strategy: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(records)
    return {
        "method": method,
        "temporal_strategy": strategy,
        "queries": n,
        **{f"R@{k}": round(100 * sum((r.get("rank") or TOP_K + 1) <= k for r in records) / n, 2) for k in RECALL_KS},
        "exact_frame_hits": sum(r.get("exact_frame_rank") is not None for r in records),
        "sequence_frame_hits": sum(r.get("sequence_frame_rank") is not None for r in records),
        "errors": sum(bool(r.get("error")) for r in records),
        "median_latency_ms": round(statistics.median(r["latency_ms"] for r in records), 2),
    }


def write_method_outputs(method: str, strategy: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    method_dir = OUTPUT_DIR / method.lower()
    method_dir.mkdir(parents=True, exist_ok=True)
    answer_path = method_dir / f"{method.lower()}_answers_top100.csv"
    fields = [
        "query_id", "query_type", "gt_video", "gt_frame", "rank", "exact_frame_rank",
        "sequence_frame_rank", "result_count", "top_video", "top_frame", "latency_ms",
        "planner_source", "planner_model", "error",
    ]
    with answer_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: record.get(field) for field in fields} for record in records)
    metrics = metrics_for_records(method, strategy, records)
    (method_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics


def run_method(method: str, strategy: str, benchmark: list[dict[str, Any]]) -> dict[str, Any]:
    method_dir = OUTPUT_DIR / method.lower()
    raw_dir = method_dir / "raw_responses"
    raw_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = method_dir / "checkpoint.jsonl"
    completed = read_checkpoint(checkpoint_path)
    with checkpoint_path.open("a", encoding="utf-8") as checkpoint:
        for index, row in enumerate(benchmark, 1):
            if row["query_id"] in completed:
                continue
            payload = build_payload(row, strategy)
            started = time.perf_counter()
            try:
                response = request_search(payload)
                latency_ms = round((time.perf_counter() - started) * 1000)
                (raw_dir / f"{row['query_id']}.json").write_text(
                    json.dumps({"payload": payload, "response": response}, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                record = evaluate(row, response, latency_ms)
            except (urllib.error.HTTPError, urllib.error.URLError, http.client.HTTPException, TimeoutError, ValueError) as exc:
                record = {**row, "latency_ms": round((time.perf_counter() - started) * 1000), "result_count": 0,
                          "rank": None, "exact_frame_rank": None, "sequence_frame_rank": None,
                          "top_video": None, "top_frame": None, "planner_source": None, "planner_model": None,
                          "error": str(exc)}
            checkpoint.write(json.dumps(record, ensure_ascii=False) + "\n")
            checkpoint.flush()
            completed[row["query_id"]] = record
            print(f"{method} {index}/{len(benchmark)} {row['query_id']}: rank={record.get('rank')} error={record.get('error')}", flush=True)
    records = [completed[row["query_id"]] for row in benchmark]
    return write_method_outputs(method, strategy, records)


def run_one(
    method: str,
    strategy: str,
    row: dict[str, Any],
    completed: dict[str, dict[str, Any]],
    retry_rank_none: bool = False,
    force: bool = False,
) -> None:
    """Run a selected strategy/query pair without overwriting valid work by default.

    ``retry_rank_none`` performs exactly one diagnostic retry for a prior miss;
    ``force`` is reserved for an explicitly requested benchmark slice rerun.
    """
    existing = completed.get(row["query_id"])
    should_retry_none = retry_rank_none and existing is not None and existing.get("rank") is None
    if not force and existing is not None and not existing.get("error") and not should_retry_none:
        return
    method_dir = OUTPUT_DIR / method.lower()
    raw_dir = method_dir / "raw_responses"
    raw_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = method_dir / "checkpoint.jsonl"
    payload = build_payload(row, strategy)
    started = time.perf_counter()
    try:
        response = request_search(payload)
        latency_ms = round((time.perf_counter() - started) * 1000)
        (raw_dir / f"{row['query_id']}.json").write_text(
            json.dumps({"payload": payload, "response": response}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        record = evaluate(row, response, latency_ms)
    except (urllib.error.HTTPError, urllib.error.URLError, http.client.HTTPException, TimeoutError, ValueError) as exc:
        record = {**row, "latency_ms": round((time.perf_counter() - started) * 1000), "result_count": 0,
                  "rank": None, "exact_frame_rank": None, "sequence_frame_rank": None,
                  "top_video": None, "top_frame": None, "planner_source": None, "planner_model": None,
                  "error": str(exc)}
    with checkpoint_path.open("a", encoding="utf-8") as checkpoint:
        checkpoint.write(json.dumps(record, ensure_ascii=False) + "\n")
    if not record.get("error") or existing is None or existing.get("error"):
        completed[row["query_id"]] = record
    print(f"{method} {row['query_id']}: rank={record.get('rank')} error={record.get('error')}", flush=True)


def main() -> None:
    global OUTPUT_DIR, METHODS
    parser = argparse.ArgumentParser(description="Exp-1 temporal search with one shared GPT plan per query.")
    parser.add_argument("--limit", type=int, default=0, help="Run only the first N benchmark rows; 0 runs all rows.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Checkpoint/output directory; use a new directory for an independent smoke test.",
    )
    parser.add_argument(
        "--methods",
        default=",".join(METHODS),
        help="Comma-separated methods to run (ATS,Vortex,DEV); defaults to all three.",
    )
    parser.add_argument("--query-ids", default="", help="Comma-separated benchmark query IDs to run.")
    parser.add_argument("--retry-rank-none", action="store_true", help="Retry each existing rank=None record once.")
    parser.add_argument("--force", action="store_true", help="Rerun selected query IDs even when a valid record exists.")
    args = parser.parse_args()
    # The module default remains the versioned experiment folder.  A runtime
    # override keeps smoke-test checkpoints separate from accepted results.
    OUTPUT_DIR = args.output_dir.resolve()
    requested_methods = [method.strip() for method in args.methods.split(",") if method.strip()]
    unknown_methods = [method for method in requested_methods if method not in METHODS]
    if unknown_methods:
        raise ValueError(f"Unknown method(s): {unknown_methods}; choose from {sorted(METHODS)}")
    # A method-scoped rerun is useful after changing one algorithm: it keeps
    # the previous ATS/Vortex controls immutable and avoids extra LLM calls.
    METHODS = {method: METHODS[method] for method in requested_methods}
    benchmark = load_benchmark(BENCHMARK)
    requested_ids = {query_id.strip() for query_id in args.query_ids.split(",") if query_id.strip()}
    if requested_ids:
        benchmark = [row for row in benchmark if row["query_id"] in requested_ids]
        found_ids = {row["query_id"] for row in benchmark}
        if missing_ids := requested_ids - found_ids:
            raise ValueError(f"Query IDs not in benchmark: {sorted(missing_ids)}")
    if args.limit:
        benchmark = benchmark[:args.limit]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "created_at_utc": utcnow(), "benchmark": str(BENCHMARK), "queries": len(benchmark),
        "fixed_configuration": {"agent_model": "gpt-4o", "visual_search_mode": "siglip2", "top_k": TOP_K,
                                "temporal_mode": True, "query_expansion": False, "reranker": False,
                                "dataset_id": DATASET_ID, "profile": PROFILE},
        "methods": METHODS,
    }
    (OUTPUT_DIR / "experiment_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    completed_by_method = {
        method: read_checkpoint(OUTPUT_DIR / method.lower() / "checkpoint.jsonl")
        for method in METHODS
    }
    plans_dir = OUTPUT_DIR / "plans"
    plans_dir.mkdir(exist_ok=True)
    for index, row in enumerate(benchmark, start=1):
        pending = [
            method for method in METHODS
            if args.force
            or not (completed_by_method[method].get(row["query_id"]) and not completed_by_method[method][row["query_id"]].get("error"))
            or (args.retry_rank_none and completed_by_method[method].get(row["query_id"], {}).get("rank") is None)
        ]
        if not pending:
            continue
        # /plan does not use the search route's 15-second bounded_call.  It
        # seeds the backend's shared GPT cache once, then the three immediate
        # searches consume an identical cached plan.
        started = time.perf_counter()
        try:
            plan_response = request_json(PLAN_URL, build_plan_payload(row))
            plan_path = plans_dir / f"{row['query_id']}.json"
            plan_path.write_text(json.dumps({"payload": build_plan_payload(row), "response": plan_response}, ensure_ascii=False, indent=2), encoding="utf-8")
            plan = plan_response.get("normalized_query", {}).get("agent_query_plan", {})
            if plan.get("source") != "langchain_direct_llm" or plan.get("agent_metadata", {}).get("model") != "gpt-4o":
                raise RuntimeError(f"GPT-4o planner was not confirmed: {plan.get('source')!r}, {plan.get('agent_metadata', {}).get('model')!r}")
        except (urllib.error.HTTPError, urllib.error.URLError, http.client.HTTPException, TimeoutError, ValueError, RuntimeError) as exc:
            # Do not run any strategy with an unverified/fallback plan.
            print(f"PLAN {index}/{len(benchmark)} {row['query_id']}: error={exc}", flush=True)
            continue
        print(f"PLAN {index}/{len(benchmark)} {row['query_id']}: GPT-4o cached in {round((time.perf_counter()-started)*1000)} ms", flush=True)
        for method, strategy in METHODS.items():
            run_one(method, strategy, row, completed_by_method[method], retry_rank_none=args.retry_rank_none, force=args.force)

    if args.limit:
        summary = []
        for method, strategy in METHODS.items():
            records_by_id = read_checkpoint(OUTPUT_DIR / method.lower() / "checkpoint.jsonl")
            records = [records_by_id[row["query_id"]] for row in benchmark if row["query_id"] in records_by_id]
            summary.append(metrics_for_records(method, strategy, records))
        # Method-scoped reruns must not overwrite the prior control summary.
        summary_suffix = "" if len(METHODS) == len({"ATS", "Vortex", "DEV"}) else f"_{'_'.join(method.lower() for method in METHODS)}"
        summary_json = OUTPUT_DIR / f"smoke_test_{args.limit}{summary_suffix}_summary.json"
        summary_csv = OUTPUT_DIR / f"smoke_test_{args.limit}{summary_suffix}_summary.csv"
    else:
        summary = []
        full_benchmark = load_benchmark(BENCHMARK)
        for method, strategy in METHODS.items():
            records_by_id = read_checkpoint(OUTPUT_DIR / method.lower() / "checkpoint.jsonl")
            records = [records_by_id[row["query_id"]] for row in full_benchmark if row["query_id"] in records_by_id]
            if records:
                summary.append(write_method_outputs(method, strategy, records))
        summary_json = OUTPUT_DIR / "temporal_search_summary.json"
        summary_csv = OUTPUT_DIR / "temporal_search_summary.csv"
    with summary_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["method", "temporal_strategy", "queries", *[f"R@{k}" for k in RECALL_KS], "exact_frame_hits", "sequence_frame_hits", "errors", "median_latency_ms"])
        writer.writeheader(); writer.writerows(summary)
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

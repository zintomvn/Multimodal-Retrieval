from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BENCHMARK = Path("data/benchmarks/AIC - ST1 25.csv")


@dataclass(frozen=True)
class BenchmarkQuery:
    query_name: str
    query_text: str
    query_type: str
    expected_raw: str


def fix_text(value: str) -> str:
    try:
        import ftfy  # type: ignore
    except Exception:
        return value
    return ftfy.fix_text(value)


def parse_expected(raw: str) -> list[str]:
    value = fix_text(raw or "").strip()
    if not value:
        return []
    try:
        return next(csv.reader([value], skipinitialspace=True))
    except csv.Error:
        return [part.strip() for part in value.split(",") if part.strip()]


def infer_query_type(query_text: str, expected_parts: list[str]) -> str:
    query_lower = query_text.lower()
    if len(expected_parts) >= 3 and not all(part.strip().isdigit() for part in expected_parts[2:]):
        return "QA"
    if "e1:" in query_lower or len(expected_parts) > 2:
        return "TRAKE"
    return "KIS"


def load_benchmark(path: Path) -> list[BenchmarkQuery]:
    queries: list[BenchmarkQuery] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        for row_number, row in enumerate(reader, start=1):
            if row_number == 1:
                continue
            if len(row) < 2:
                continue
            query_text = fix_text(row[0]).strip()
            expected_raw = fix_text(row[1]).strip()
            if not query_text:
                continue
            expected_parts = parse_expected(expected_raw)
            queries.append(
                BenchmarkQuery(
                    query_name=f"st1_{len(queries) + 1:02d}",
                    query_text=query_text,
                    query_type=infer_query_type(query_text, expected_parts),
                    expected_raw=expected_raw,
                )
            )
    return queries


def post_json(url: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))


def search_one(args: argparse.Namespace, item: BenchmarkQuery) -> dict[str, Any]:
    endpoint = {
        "QA": "/api/retrieval/qa",
        "TRAKE": "/api/retrieval/trake",
    }.get(item.query_type, "/api/retrieval/search")
    payload = {
        "dataset_id": args.dataset_id,
        "query_name": item.query_name,
        "query_type": item.query_type,
        "query_text": item.query_text,
        "top_k": args.top_k,
        "profile": args.profile,
        "options": {
            "use_query_expansion": not args.no_query_expansion,
            "use_agent_query_planning": not args.no_agent_query_planning,
            "use_metadata": not args.no_metadata,
            "use_reranker": args.use_reranker,
            "strict_hybrid": args.strict_hybrid,
            "delta_t_max_ms": args.delta_t_max_ms,
        },
    }
    return post_json(f"{args.api_base.rstrip('/')}{endpoint}", payload, timeout_s=args.timeout_s)


def result_submission_row(query_type: str, result: dict[str, Any]) -> list[Any]:
    video_code = result.get("video_code") or result.get("video_id") or ""
    if query_type == "TRAKE":
        sequence_frames = result.get("sequence_frames") or []
        frame_indices = [item.get("frame_idx") for item in sequence_frames if item.get("frame_idx") is not None]
        if not frame_indices and result.get("frame_idx") is not None:
            frame_indices = [result["frame_idx"]]
        return [video_code, *frame_indices]
    frame_idx = result.get("frame_idx")
    if query_type == "QA":
        return [video_code, frame_idx, result.get("answer") or ""]
    return [video_code, frame_idx]


def write_outputs(output_dir: Path, query: BenchmarkQuery, response: dict[str, Any]) -> None:
    submission_dir = output_dir / "submission"
    submission_dir.mkdir(parents=True, exist_ok=True)
    csv_path = submission_dir / f"{query.query_name}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for result in response.get("results", [])[:100]:
            writer.writerow(result_submission_row(query.query_type, result))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run backend retrieval over the AIC ST1 benchmark CSV.")
    parser.add_argument("--benchmark-csv", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--api-base", default="http://127.0.0.1:8010")
    parser.add_argument("--dataset-id", default="ai_challenge_2025")
    parser.add_argument("--profile", default="competition_default")
    parser.add_argument("--top-k", type=int, default=100)
    parser.add_argument("--output-dir", type=Path, default=Path("data/benchmarks/aic_st1_25_backend_search"))
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument("--sleep-s", type=float, default=0.0)
    parser.add_argument("--delta-t-max-ms", type=int, default=180000)
    parser.add_argument("--use-reranker", action="store_true")
    parser.add_argument("--strict-hybrid", action="store_true")
    parser.add_argument("--no-query-expansion", action="store_true")
    parser.add_argument("--no-agent-query-planning", action="store_true")
    parser.add_argument("--no-metadata", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    queries = load_benchmark(args.benchmark_csv)
    if args.limit > 0:
        queries = queries[: args.limit]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = args.output_dir / "results.jsonl"

    with jsonl_path.open("w", encoding="utf-8") as handle:
        for index, query in enumerate(queries, start=1):
            started = time.perf_counter()
            record: dict[str, Any] = {
                "query_name": query.query_name,
                "query_type": query.query_type,
                "query_text": query.query_text,
                "expected_raw": query.expected_raw,
            }
            try:
                response = search_one(args, query)
                write_outputs(args.output_dir, query, response)
                record["ok"] = True
                record["latency_ms"] = int((time.perf_counter() - started) * 1000)
                record["normalized_query"] = response.get("normalized_query")
                record["top_results"] = response.get("results", [])[:5]
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, RuntimeError) as exc:
                record["ok"] = False
                record["latency_ms"] = int((time.perf_counter() - started) * 1000)
                record["error"] = str(exc)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            print(json.dumps({"index": index, "query_name": query.query_name, "ok": record["ok"]}, ensure_ascii=False))
            if args.sleep_s > 0:
                time.sleep(args.sleep_s)

    print(json.dumps({"queries": len(queries), "results_jsonl": str(jsonl_path), "submission_dir": str(args.output_dir / "submission")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

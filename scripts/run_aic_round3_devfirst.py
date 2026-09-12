"""Evaluate temporal retrieval strategies on the AIC 2026 round-3 benchmark.

The runner selects rows with ground truth by default and reports video/frame
rank separately.  A KIS result is a top-10 success when the ground-truth video
is among the first ten returned items; exact-frame rank is diagnostic only.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import urllib.request
from pathlib import Path
from typing import Any


def request_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, default=Path("data/benchmarks/aic_2026_round3.csv"))
    parser.add_argument("--api", default="http://127.0.0.1:8000/api/retrieval/search")
    parser.add_argument("--dataset-id", default=None)
    parser.add_argument("--limit", type=int, default=8, help="Number of evaluable benchmark rows.")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=["dev_first_search"],
        choices=["vortex_k_context", "aithena_weighted_ats", "dev_first_search"],
    )
    parser.add_argument("--agent-model", choices=["gpt-4o", "gpt-5-nano", "gpt-5.6-luna"], default="gpt-4o")
    parser.add_argument("--visual-search-mode", choices=["openclip", "siglip2", "both", "profile"], default="both")
    parser.add_argument("--include-missing-ground-truth", action="store_true")
    parser.add_argument("--query-ids", nargs="*", default=[], help="Optional Query ID subset for diagnosis.")
    parser.add_argument("--print-records", action="store_true", help="Print all per-query records to stdout.")
    parser.add_argument("--output", type=Path, default=Path("data/benchmarks/aic_2026_round3_devfirst.json"))
    args = parser.parse_args()
    with args.benchmark.open(encoding="utf-8-sig", newline="") as handle:
        all_rows = list(csv.DictReader(handle))
    rows = all_rows if args.include_missing_ground_truth else [
        row for row in all_rows if str(row.get("GT Video ID", "")).strip()
    ]
    requested_ids = {value.strip() for value in args.query_ids if value.strip()}
    if requested_ids:
        rows = [row for row in rows if row.get("Query ID") in requested_ids]
    rows = rows[: args.limit]
    report: dict[str, Any] = {"benchmark": str(args.benchmark), "query_count": len(rows), "strategies": {}}
    for strategy in args.strategies:
        records: list[dict[str, Any]] = []
        for row in rows:
            payload = {
                "dataset_id": args.dataset_id,
                "query_name": row["Query ID"], "query_type": row["Task Type"], "query_text": row["Query Text"],
                "top_k": args.top_k, "profile": "competition_default",
                "options": {
                    "temporal_mode": row["Task Type"] == "KIS",
                    "temporal_strategy": strategy,
                    "use_query_expansion": False,
                    "use_agent_query_planning": True,
                    "agent_model": args.agent_model,
                    "visual_search_mode": args.visual_search_mode,
                },
            }
            response = request_json(args.api, payload)
            results = response.get("results", [])
            gt_video, gt_frame = row.get("GT Video ID", ""), row.get("GT Frame ID(s)", "")
            video_rank = next((index + 1 for index, result in enumerate(results) if result.get("video_code") == gt_video), None)
            gt_frames = {value for value in re.split(r"[;,|\\s]+", str(gt_frame)) if value}
            frame_rank = next(
                (
                    index + 1
                    for index, result in enumerate(results)
                    if result.get("video_code") == gt_video and str(result.get("frame_idx")) in gt_frames
                ),
                None,
            )
            normalized = response.get("normalized_query", {})
            records.append({
                "query_id": row["Query ID"], "gt_video": gt_video, "gt_frame": gt_frame,
                "video_rank": video_rank, "frame_rank": frame_rank,
                "top10_video_hit": video_rank is not None and video_rank <= 10,
                "top10_frame_hit": frame_rank is not None and frame_rank <= 10,
                "result_count": len(results), "latency_ms": normalized.get("latency_ms"),
                "planner": normalized.get("agent_query_plan", {}).get("agent_metadata", {}),
                "temporal_events": normalized.get("temporal_events", []),
                "top_result": results[0] if results else None,
            })
        report["strategies"][strategy] = records
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for strategy, records in report["strategies"].items():
        summary = {
            "strategy": strategy,
            "queries": len(records),
            "top10_video_hits": sum(item["top10_video_hit"] for item in records),
            "top10_frame_hits": sum(item["top10_frame_hit"] for item in records),
        }
        if args.print_records:
            summary["records"] = records
        print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

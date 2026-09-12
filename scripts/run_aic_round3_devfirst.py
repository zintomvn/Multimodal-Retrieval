"""Compare temporal strategies on the AIC 2026 round-3 benchmark.

The CSV's first five rows contain video/frame ground truth, so the default
five-query run reports a transparent top-k video and frame hit comparison.
"""
from __future__ import annotations

import argparse
import csv
import json
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
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--output", type=Path, default=Path("data/benchmarks/aic_2026_round3_devfirst_5.json"))
    args = parser.parse_args()
    with args.benchmark.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))[: args.limit]
    report: dict[str, Any] = {"benchmark": str(args.benchmark), "query_count": len(rows), "strategies": {}}
    for strategy in ("aithena_weighted_ats", "dev_first_search"):
        records: list[dict[str, Any]] = []
        for row in rows:
            payload = {
                "dataset_id": args.dataset_id,
                "query_name": row["Query ID"], "query_type": row["Task Type"], "query_text": row["Query Text"],
                "top_k": args.top_k, "profile": "competition_default",
                "options": {"temporal_mode": row["Task Type"] == "KIS", "temporal_strategy": strategy, "use_query_expansion": False},
            }
            response = request_json(args.api, payload)
            results = response.get("results", [])
            gt_video, gt_frame = row.get("GT Video ID", ""), row.get("GT Frame ID(s)", "")
            video_rank = next((index + 1 for index, result in enumerate(results) if result.get("video_code") == gt_video), None)
            frame_rank = next((index + 1 for index, result in enumerate(results) if result.get("video_code") == gt_video and str(result.get("frame_idx")) == gt_frame), None)
            records.append({"query_id": row["Query ID"], "gt_video": gt_video, "gt_frame": gt_frame, "video_rank": video_rank, "frame_rank": frame_rank, "result_count": len(results), "latency_ms": response.get("normalized_query", {}).get("latency_ms"), "top_result": results[0] if results else None})
        report["strategies"][strategy] = records
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for strategy, records in report["strategies"].items():
        print(json.dumps({"strategy": strategy, "video_hits": sum(item["video_rank"] is not None for item in records), "frame_hits": sum(item["frame_rank"] is not None for item in records), "records": records}, ensure_ascii=False))


if __name__ == "__main__":
    main()

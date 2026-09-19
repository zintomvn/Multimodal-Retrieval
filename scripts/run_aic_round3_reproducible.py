"""Reproducible, evidence-preserving AIC 2026 round-3 KIS runner.

This intentionally evaluates the *returned ranking* separately from frame
localisation.  A correct video must never be counted as an exact-frame hit.
Every HTTP response is retained before metrics are computed, so a later report
can be audited without querying the service again.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_IDS = [
    "query-p2-1-kis", "query-p2-2-kis", "query-p2-3-kis",
    "query-p2-4-kis", "query-p2-10-kis", "query-p2-11-kis",
]
ORIGINAL_FIVE_IDS = [item for item in DEFAULT_IDS if item != "query-p2-10-kis"]


def request_json(url: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))


def query_id(raw_query: str) -> str | None:
    match = re.search(r"query-p2-\d+-kis", raw_query, flags=re.IGNORECASE)
    return match.group(0).lower() if match else None


def load_ground_truth(path: Path, ids: list[str]) -> list[dict[str, Any]]:
    """Parse the supplied three-column CSV, whose Query cell is multiline.

    The source is UTF-8 but its Vietnamese prose may be mojibake.  IDs and
    answer labels are ASCII and are extracted without changing query text.
    """
    desired = set(ids)
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            raw_query = str(row.get("Query", ""))
            item_id = query_id(raw_query)
            if item_id not in desired:
                continue
            answer = str(row.get("Đáp án", row.get("\ufffd\x90áp án", ""))).strip()
            pieces = [piece.strip() for piece in re.split(r"[,;|]", answer) if piece.strip()]
            if len(pieces) < 2:
                raise ValueError(f"Malformed ground truth for {item_id!r}: {answer!r}")
            # The identifier label is a header line, not semantic query text.
            text = re.sub(r"^.*?query-p2-\d+-kis\s*", "", raw_query, count=1, flags=re.IGNORECASE | re.DOTALL).strip()
            rows.append({"query_id": item_id, "query_text": text, "gt_video": pieces[0], "gt_frames": [int(v) for v in pieces[1:] if v.isdigit()], "gt_answer_raw": answer, "ground_truth_status": str(row.get("Trạng thái", "")).strip(), "query_raw": raw_query})
    found = {row["query_id"] for row in rows}
    missing = desired - found
    if missing:
        raise ValueError(f"Ground truth IDs not found: {sorted(missing)}")
    return sorted(rows, key=lambda row: ids.index(row["query_id"]))


def rank_of(predicate: Any, values: list[dict[str, Any]]) -> int | None:
    return next((index for index, value in enumerate(values, start=1) if predicate(value)), None)


def evaluate(row: dict[str, Any], response: dict[str, Any], latency_ms: int) -> dict[str, Any]:
    results = response.get("results", [])
    gt_video, gt_frames = row["gt_video"], set(row["gt_frames"])
    video_rank = rank_of(lambda item: item.get("video_code") == gt_video, results)
    exact_returned_frame_rank = rank_of(lambda item: item.get("video_code") == gt_video and item.get("frame_idx") in gt_frames, results)
    sequence_frame_rank = rank_of(lambda item: item.get("video_code") == gt_video and any(frame.get("frame_idx") in gt_frames for frame in item.get("sequence_frames", [])), results)
    gt_results = [item for item in results if item.get("video_code") == gt_video and isinstance(item.get("frame_idx"), int)]
    nearest_delta = min((min(abs(item["frame_idx"] - frame) for frame in gt_frames) for item in gt_results), default=None)
    # These score_breakdown/normalized fields are the only stage trace exported
    # by the API.  Do not infer unreturned global Milvus candidates from them.
    stage_trace = [{"rank": item.get("rank"), "video_code": item.get("video_code"), "frame_idx": item.get("frame_idx"), "score_breakdown": item.get("score_breakdown", {}), "sequence_frames": item.get("sequence_frames", [])} for item in results]
    return {
        **row, "latency_ms": latency_ms, "result_count": len(results),
        "video_rank": video_rank,
        "video_recall_at_5": bool(video_rank and video_rank <= 5),
        "video_recall_at_10": bool(video_rank and video_rank <= 10),
        "exact_returned_frame_rank": exact_returned_frame_rank,
        "sequence_contains_exact_frame_rank": sequence_frame_rank,
        "nearest_returned_frame_delta": nearest_delta,
        "top_result": results[0] if results else None,
        "normalized_query": response.get("normalized_query", {}),
        "api_stage_trace": stage_trace,
    }


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(records)
    return {
        "queries": count,
        "video_recall_at_5": sum(item["video_recall_at_5"] for item in records),
        "video_recall_at_10": sum(item["video_recall_at_10"] for item in records),
        "exact_returned_frame_hits": sum(item["exact_returned_frame_rank"] is not None for item in records),
        "sequence_contains_exact_frame_hits": sum(item["sequence_contains_exact_frame_rank"] is not None for item in records),
        "median_latency_ms": statistics.median(item["latency_ms"] for item in records) if records else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ground-truth", type=Path, default=Path("data/experiments/aic_2026_groundtruth/aic2026_round_3.csv"))
    parser.add_argument("--api", default="http://127.0.0.1:8000/api/retrieval/search")
    parser.add_argument("--dataset-id", default="917efbbe-44b8-4476-98ef-3e7ec7ca7958")
    parser.add_argument("--profile", default="competition_default")
    parser.add_argument("--strategy", default="dev_first_search", choices=["vortex_k_context", "aithena_weighted_ats", "dev_first_search"])
    parser.add_argument("--agent-model", default="gpt-5-nano", choices=["gpt-4o", "gpt-5-nano", "gpt-5.6-luna"])
    parser.add_argument("--visual-search-mode", default="both", choices=["openclip", "siglip2", "both", "profile"])
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--deadline-ms", type=int, default=120000, help="Backend deadline; saved with every request.")
    parser.add_argument("--timeout-s", type=float, default=180)
    parser.add_argument("--query-ids", nargs="*", default=DEFAULT_IDS)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    records: list[dict[str, Any]] = []
    args.output_dir.mkdir(parents=True, exist_ok=False)
    raw_dir = args.output_dir / "raw_responses"
    raw_dir.mkdir()
    for row in load_ground_truth(args.ground_truth, args.query_ids):
        payload = {"dataset_id": args.dataset_id, "query_name": row["query_id"], "query_type": "KIS", "query_text": row["query_text"], "top_k": args.top_k, "profile": args.profile, "options": {"temporal_mode": True, "temporal_strategy": args.strategy, "use_query_expansion": False, "use_agent_query_planning": True, "agent_model": args.agent_model, "visual_search_mode": args.visual_search_mode, "use_reranker": False, "delta_t_max_ms": 180000, "deadline_ms": args.deadline_ms, "debug_filters": True}}
        started = time.perf_counter()
        try:
            response = request_json(args.api, payload, args.timeout_s)
            latency_ms = round((time.perf_counter() - started) * 1000)
            (raw_dir / f"{row['query_id']}.json").write_text(json.dumps({"payload": payload, "response": response}, ensure_ascii=False, indent=2), encoding="utf-8")
            record = evaluate(row, response, latency_ms)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            record = {**row, "latency_ms": round((time.perf_counter() - started) * 1000), "error": str(exc), "video_recall_at_5": False, "video_recall_at_10": False, "exact_returned_frame_rank": None, "sequence_contains_exact_frame_rank": None}
        records.append(record)
        print(json.dumps({"query_id": row["query_id"], "video_rank": record.get("video_rank"), "latency_ms": record["latency_ms"], "error": record.get("error")}, ensure_ascii=False))
    summary = {"created_at": datetime.now(timezone.utc).isoformat(), "ids": args.query_ids, "runtime": {"api": args.api, "dataset_id": args.dataset_id, "profile": args.profile, "strategy": args.strategy, "agent_model": args.agent_model, "visual_search_mode": args.visual_search_mode, "top_k": args.top_k, "deadline_ms": args.deadline_ms}, "metrics_all_six": aggregate(records), "metrics_original_five_excluding_p2_10": aggregate([item for item in records if item["query_id"] in ORIGINAL_FIVE_IDS]), "metric_definitions": {"video_recall_at_k": "GT video occurs in final returned ranks <= k.", "exact_returned_frame_rank": "GT video AND final representative frame_idx exactly equals a GT frame index; never implied by video recall.", "sequence_contains_exact_frame_rank": "Diagnostic only: a returned KIS sequence contains a GT frame index, even if its representative differs.", "nearest_returned_frame_delta": "Minimum absolute frame-index delta among returned results for the GT video; no tolerance is applied."}, "records": records}
    (args.output_dir / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (args.output_dir / "per_query.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["query_id", "gt_video", "gt_frames", "latency_ms", "result_count", "video_rank", "video_recall_at_5", "video_recall_at_10", "exact_returned_frame_rank", "sequence_contains_exact_frame_rank", "nearest_returned_frame_delta", "error"])
        writer.writeheader()
        for item in records:
            writer.writerow({key: item.get(key) for key in writer.fieldnames} | {"gt_frames": ";".join(map(str, item["gt_frames"]))})
    print(json.dumps(summary["metrics_all_six"], ensure_ascii=False))


if __name__ == "__main__":
    main()

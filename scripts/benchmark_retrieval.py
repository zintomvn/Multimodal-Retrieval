from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


TOP_KS = [1, 5, 20, 50, 100]


def score_query(results: list[dict], ground_truth: dict) -> dict:
    r_scores: list[float] = []
    for row in results[:100]:
        if row["video_code"] != ground_truth["video_code"]:
            r_scores.append(0.0)
            continue
        if ground_truth["type"] == "TRAKE":
            expected = ground_truth["frames"]
            predicted = row.get("frame_indices", [])
            matched = 0
            for idx, frame_idx in enumerate(predicted[: len(expected)]):
                start, end = expected[idx]
                matched += int(start <= frame_idx <= end)
            r_scores.append(matched / max(1, len(expected)))
        else:
            frame_idx = row.get("frame_indices", [None])[0]
            start, end = ground_truth["frame_range"]
            answer_ok = ground_truth["type"] != "QA" or str(row.get("answer", "")).lower() == str(ground_truth["answer"]).lower()
            r_scores.append(float(start <= frame_idx <= end and answer_ok))
    final = sum(max(r_scores[:k] or [0.0]) for k in TOP_KS) / len(TOP_KS)
    return {"final_score": final, "r_at": {f"R@{k}": max(r_scores[:k] or [0.0]) for k in TOP_KS}}


def load_csv(path: Path, query_type: str) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for rank, row in enumerate(csv.reader(handle), start=1):
            if not row:
                continue
            if query_type == "QA":
                rows.append({"rank": rank, "video_code": row[0], "frame_indices": [int(row[1])], "answer": row[2] if len(row) > 2 else ""})
            else:
                rows.append({"rank": rank, "video_code": row[0], "frame_indices": [int(value) for value in row[1:]]})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute Codabench-style top-k score for local ground truth.")
    parser.add_argument("--submission-dir", required=True)
    parser.add_argument("--ground-truth", required=True, help="JSON mapping query csv name to GT object.")
    args = parser.parse_args()

    submission_dir = Path(args.submission_dir)
    with open(args.ground_truth, "r", encoding="utf-8") as handle:
        ground_truth = json.load(handle)

    total = 0.0
    report = {}
    for csv_name, gt in ground_truth.items():
        rows = load_csv(submission_dir / csv_name, gt["type"])
        query_score = score_query(rows, gt)
        report[csv_name] = query_score
        total += query_score["final_score"]
    report["total"] = total
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

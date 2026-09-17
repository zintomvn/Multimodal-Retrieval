from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


QUERY_PATTERN = re.compile(r"(query-p\d+-\d+-(kis|qa|trake))", re.IGNORECASE)
VIDEO_PATTERN = re.compile(r"L\d+_(?:V)?\d+", re.IGNORECASE)
CONFIRMED_STATUS = "chắn chắn"
FIELDS = [
    "Round",
    "No.",
    "Query ID",
    "Original Query ID",
    "Query Text",
    "Task Type",
    "GT Video ID",
    "GT Frame ID(s)",
    "GT Answer",
    "Status",
    "Source File",
    "Raw Ground Truth",
    "Notes",
]


def normalize_video_id(value: str) -> str:
    video_id = value.upper()
    return video_id if "_V" in video_id else video_id.replace("_", "_V", 1)


def choose_answer_line(raw: str) -> tuple[str, list[str]]:
    lines = [line.strip() for line in str(raw or "").splitlines() if VIDEO_PATTERN.search(line)]
    non_rejected = [line for line in lines if "-> sai" not in line.casefold()]
    candidates = non_rejected or lines
    return (candidates[0] if candidates else ""), lines


def parse_ground_truth(raw: str, task_type: str) -> tuple[str, str, str, list[str]]:
    line, all_lines = choose_answer_line(raw)
    video_match = VIDEO_PATTERN.search(line)
    if not video_match:
        return "", "", "", ["unparseable_ground_truth"]

    video_id = normalize_video_id(video_match.group(0))
    suffix = line[video_match.end() :]
    numbers = re.findall(r"\d+", suffix)
    notes: list[str] = []
    if video_match.group(0).upper() != video_id:
        notes.append("normalized_missing_V_in_video_id")
    if len(all_lines) > 1:
        notes.append("multiple_ground_truth_lines_primary_selected")

    if task_type == "KIS":
        frame_ids = []
        for candidate in all_lines:
            if "-> sai" in candidate.casefold():
                continue
            candidate_video = VIDEO_PATTERN.search(candidate)
            if not candidate_video or normalize_video_id(candidate_video.group(0)) != video_id:
                continue
            candidate_frames = re.findall(r"\d+", candidate[candidate_video.end() :])
            if candidate_frames and candidate_frames[0] not in frame_ids:
                frame_ids.append(candidate_frames[0])
        answer = ""
    elif task_type == "TRAKE":
        frame_ids = numbers
        answer = ""
    else:
        frame_ids = numbers[:1]
        answer = ""
        if task_type == "QA":
            try:
                parts = next(csv.reader([line], skipinitialspace=True))
            except csv.Error:
                parts = []
            if len(parts) >= 3:
                answer = ",".join(parts[2:]).strip()

    if not frame_ids:
        notes.append("missing_frame_id")
    return video_id, ";".join(frame_ids), answer, notes


def merge(input_dir: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    output: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for source in sorted(input_dir.glob("aic2026_round_[123].csv")):
        round_match = re.search(r"round_(\d+)", source.stem)
        round_number = round_match.group(1) if round_match else ""
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            for source_row, row in enumerate(csv.DictReader(handle), start=2):
                status = str(row.get("Trạng thái") or "").strip()
                if status.casefold() != CONFIRMED_STATUS.casefold():
                    continue

                filename_hint = str(row.get("Tên file (optional)") or "")
                raw_query = str(row.get("Query") or "")
                query_match = QUERY_PATTERN.search(f"{filename_hint} {raw_query}")
                if not query_match:
                    skipped.append({"source": source.name, "row": str(source_row), "reason": "missing_query_id"})
                    continue

                original_query_id = query_match.group(1).lower()
                task_type = query_match.group(2).upper()
                inline_match = QUERY_PATTERN.search(raw_query)
                query_text = raw_query[inline_match.end() :].strip(" \t\r\n:-") if inline_match else raw_query.strip()
                raw_ground_truth = str(row.get("Đáp án") or "").strip()
                video_id, frame_ids, answer, notes = parse_ground_truth(raw_ground_truth, task_type)
                if not query_text or not video_id or not frame_ids:
                    skipped.append(
                        {
                            "source": source.name,
                            "row": str(source_row),
                            "query_id": original_query_id,
                            "reason": ";".join(notes) or "missing_required_value",
                        }
                    )
                    continue

                output.append(
                    {
                        "Round": round_number,
                        "No.": str(len(output) + 1),
                        "Query ID": f"r{round_number}::{original_query_id}",
                        "Original Query ID": original_query_id,
                        "Query Text": query_text,
                        "Task Type": task_type,
                        "GT Video ID": video_id,
                        "GT Frame ID(s)": frame_ids,
                        "GT Answer": answer,
                        "Status": "OK",
                        "Source File": source.name,
                        "Raw Ground Truth": raw_ground_truth,
                        "Notes": ";".join(notes),
                    }
                )
    return output, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge confirmed AIC 2026 ground-truth CSV files into one canonical CSV.")
    parser.add_argument("--input-dir", type=Path, default=Path("data/experiments/aic_2026_groundtruth"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/experiments/aic_2026_groundtruth/aic2026_all_confirmed.csv"),
    )
    args = parser.parse_args()

    rows, skipped = merge(args.input_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    by_type: dict[str, int] = {}
    by_round: dict[str, int] = {}
    for row in rows:
        by_type[row["Task Type"]] = by_type.get(row["Task Type"], 0) + 1
        by_round[row["Round"]] = by_round.get(row["Round"], 0) + 1
    print(
        {
            "output": str(args.output),
            "rows": len(rows),
            "by_round": by_round,
            "by_type": by_type,
            "skipped": skipped,
        }
    )


if __name__ == "__main__":
    main()

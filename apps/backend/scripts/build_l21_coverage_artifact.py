from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parents[1]
PROCESSORS_ROOT = REPO_ROOT / "scripts" / "processors"

for path in (BACKEND_ROOT, PROCESSORS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from src.artifact_io import ArtifactStore, join_uri  # noqa: E402
from src.config import get_processor_settings  # noqa: E402
from src.manifest import (  # noqa: E402
    build_gcs_keyframe_manifest,
    frame_item_from_record,
    frame_item_to_manifest_row,
)


DEFAULT_DATASET_ID = "ai_challenge_2025"
DEFAULT_BATCH = "L21"
DEFAULT_FRAME_PROFILE = "autoshot_v1"
DEFAULT_BUCKET = "aic_ai_2026"
DEFAULT_KEYFRAMES_PREFIX = "processed/keyframes"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "patched"
DEFAULT_GCS_OUTPUT_ROOT = (
    "gs://aic_ai_2026/features/patches/"
    "dataset=ai_challenge_2025/batch=L21/frame_profile=autoshot_v1"
)

DEFAULT_FEATURE_ARTIFACTS = [
    (
        "captioning",
        "gs://aic_ai_2026/features/extractors/dataset=ai_challenge_2025/batch=L21/"
        "frame_profile=autoshot_v1/extractor=captioning/extractor_version=fe-captioning-v2.2/"
        "model=qwen3_vl_4b/data/annotations.jsonl",
    ),
    (
        "object_detection",
        "gs://aic_ai_2026/features/extractors/dataset=ai_challenge_2025/batch=L21/"
        "frame_profile=autoshot_v1/extractor=object_detection/extractor_version=fe-object-detection-v1/"
        "run_id=full_l21_20260810T053426Z_2fd88279/annotations.jsonl",
    ),
    (
        "ocr",
        "gs://aic_ai_2026/features/extractors/dataset=ai_challenge_2025/batch=L21/"
        "frame_profile=autoshot_v1/extractor=ocr/extractor_version=fe-ocr-v1/"
        "run_id=full_l21_20260719T064450Z_6876c339/annotations.jsonl",
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a full-coverage L21 annotation artifact from current GCS keyframes."
    )
    parser.add_argument("--dataset-id", default=DEFAULT_DATASET_ID)
    parser.add_argument("--batch", default=DEFAULT_BATCH)
    parser.add_argument("--frame-profile", default=DEFAULT_FRAME_PROFILE)
    parser.add_argument("--bucket", default="")
    parser.add_argument("--keyframes-prefix", default=DEFAULT_KEYFRAMES_PREFIX)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--gcs-output-root", default=DEFAULT_GCS_OUTPUT_ROOT)
    parser.add_argument("--upload-gcs", action="store_true")
    parser.add_argument(
        "--feature-artifact",
        action="append",
        default=[],
        help="Optional feature JSONL URI. Repeatable. Defaults to the known L21 caption/object/OCR artifacts.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--no-manifest-fallback", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_processor_settings()
    bucket = args.bucket or settings.gcs_bucket or DEFAULT_BUCKET
    credentials_file = settings.gcs_credentials_file
    run_id = args.run_id or f"coverage_patch_{args.batch.lower()}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    store = ArtifactStore(credentials_file=credentials_file, timeout_seconds=args.timeout_seconds)

    frames = load_current_frames(
        store=store,
        bucket=bucket,
        dataset_id=args.dataset_id,
        batch=args.batch,
        frame_profile=args.frame_profile,
        keyframes_prefix=args.keyframes_prefix,
        timeout_seconds=args.timeout_seconds,
        credentials_file=credentials_file,
        allow_image_fallback=not args.no_manifest_fallback,
    )
    if not frames:
        raise SystemExit(f"No current frames found for batch {args.batch}.")

    annotations = {row["keyframe_id"]: blank_annotation() for row in frames}
    source_summaries: list[dict[str, Any]] = []
    feature_uris = args.feature_artifact or [uri for _, uri in DEFAULT_FEATURE_ARTIFACTS]
    for uri in feature_uris:
        summary = merge_feature_artifact(store, uri, annotations)
        source_summaries.append(summary)

    rows = build_rows(
        frames=frames,
        annotations=annotations,
        dataset_id=args.dataset_id,
        batch=args.batch,
        frame_profile=args.frame_profile,
        run_id=run_id,
        source_artifacts=feature_uris,
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    local_jsonl = output_dir / f"{args.batch.lower()}_full_coverage_annotations.jsonl"
    local_summary = output_dir / f"{args.batch.lower()}_full_coverage_summary.json"

    write_jsonl(local_jsonl, rows)
    summary = build_summary(
        rows=rows,
        frames=frames,
        run_id=run_id,
        dataset_id=args.dataset_id,
        batch=args.batch,
        frame_profile=args.frame_profile,
        local_jsonl=local_jsonl,
        local_summary=local_summary,
        source_summaries=source_summaries,
        gcs_jsonl="",
        gcs_summary="",
    )
    write_json(local_summary, summary)

    if args.upload_gcs:
        gcs_root = join_uri(args.gcs_output_root, f"run_id={run_id}")
        gcs_jsonl = join_uri(gcs_root, "annotations.jsonl")
        gcs_summary = join_uri(gcs_root, "summary.json")
        store.write_jsonl(gcs_jsonl, rows)
        summary["outputs"]["gcs_annotations"] = gcs_jsonl
        summary["outputs"]["gcs_summary"] = gcs_summary
        store.write_json(gcs_summary, summary)
        write_json(local_summary, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


def load_current_frames(
    *,
    store: ArtifactStore,
    bucket: str,
    dataset_id: str,
    batch: str,
    frame_profile: str,
    keyframes_prefix: str,
    timeout_seconds: float,
    credentials_file: str,
    allow_image_fallback: bool,
) -> list[dict[str, Any]]:
    prefix = (
        f"{keyframes_prefix.strip('/')}/dataset={dataset_id}/batch={batch}/"
        f"profile={frame_profile}"
    )
    manifest_uris = [
        uri
        for uri in store.list_jsonl(f"gs://{bucket}/{prefix}")
        if uri.endswith("/frames_manifest.jsonl")
    ]
    frames_by_id: dict[str, dict[str, Any]] = {}
    for uri in sorted(manifest_uris):
        for raw in store.read_jsonl(uri):
            row = normalize_frame(raw, bucket=bucket, dataset_id=dataset_id, batch=batch)
            if row:
                frames_by_id[row["keyframe_id"]] = row

    if frames_by_id or not allow_image_fallback:
        return sorted(frames_by_id.values(), key=frame_sort_key)

    rows = build_gcs_keyframe_manifest(
        bucket_name=bucket,
        prefixes=[prefix],
        dataset_id=dataset_id,
        credentials_file=credentials_file,
        timeout_seconds=timeout_seconds,
    )
    normalized = [normalize_frame(row, bucket=bucket, dataset_id=dataset_id, batch=batch) for row in rows]
    return sorted((row for row in normalized if row), key=frame_sort_key)


def normalize_frame(raw: dict[str, Any], *, bucket: str, dataset_id: str, batch: str) -> dict[str, Any]:
    source = raw.get("frame") if isinstance(raw.get("frame"), dict) else raw
    try:
        item = frame_item_from_record(source)
    except Exception:
        return {}
    row = frame_item_to_manifest_row(item, dataset_id=dataset_id)
    row["dataset_id"] = str(source.get("dataset_id") or source.get("dataset_code") or dataset_id)
    row["batch"] = str(source.get("batch") or source.get("batch_id") or batch)
    row["timestamp_ms"] = int(float(row.get("frame_seconds") or 0.0) * 1000)
    row["global_index"] = source.get("global_index")
    row["shard_index"] = source.get("shard_index")
    row["image_uri"] = row["gcs_uri"]
    row["image_gcs_uri"] = row["gcs_uri"]
    row["image_storage_key"] = row["blob_name"]
    if not row.get("bucket"):
        row["bucket"] = bucket
    return row


def merge_feature_artifact(
    store: ArtifactStore,
    uri: str,
    annotations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    files = store.list_jsonl(uri)
    summary = {
        "uri": uri,
        "files": files,
        "rows": 0,
        "matched_rows": 0,
        "unmatched_rows": 0,
        "videos": {},
    }
    video_counts: Counter[str] = Counter()
    for file_uri in files:
        for raw in store.read_jsonl(file_uri):
            summary["rows"] += 1
            keyframe_id = keyframe_id_from_record(raw)
            if not keyframe_id or keyframe_id not in annotations:
                summary["unmatched_rows"] += 1
                continue
            merge_annotation(annotations[keyframe_id], raw)
            summary["matched_rows"] += 1
            video_counts[str(raw.get("video_id") or keyframe_id.rsplit("_F", 1)[0])] += 1
    summary["videos"] = dict(sorted(video_counts.items()))
    return summary


def keyframe_id_from_record(raw: dict[str, Any]) -> str:
    frame = raw.get("frame") if isinstance(raw.get("frame"), dict) else raw
    keyframe_id = str(frame.get("keyframe_id") or frame.get("frame_id") or "").strip()
    if keyframe_id:
        return keyframe_id
    try:
        return frame_item_from_record(frame).keyframe_id
    except Exception:
        return ""


def merge_annotation(left: dict[str, Any], raw: dict[str, Any]) -> None:
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    annotation = raw.get("annotation") if isinstance(raw.get("annotation"), dict) else {}
    merged_source = {**raw, **annotation}
    kind = str(merged_source.get("kind") or merged_source.get("extractor_name") or "").lower()

    caption = first_text(
        merged_source.get("caption"),
        payload.get("caption"),
        merged_source.get("text_value") if "caption" in kind else "",
    )
    if caption:
        left["caption"] = caption

    texts = first_value(
        merged_source.get("texts"),
        merged_source.get("ocr_texts"),
        payload.get("texts"),
        payload.get("ocr_texts"),
        merged_source.get("text_value") if "ocr" in kind else None,
    )
    left["texts"] = merge_list(left.get("texts"), texts)

    objects = first_value(
        merged_source.get("objects"),
        merged_source.get("detected_objects"),
        payload.get("objects"),
        payload.get("detected_objects"),
    )
    left["objects"] = merge_list(left.get("objects"), objects)

    object_counts = first_dict(merged_source.get("object_counts"), payload.get("object_counts"))
    for label, count in object_counts.items():
        left["object_counts"][str(label)] = max(int(left["object_counts"].get(str(label), 0)), int(count or 0))

    detections = first_value(merged_source.get("detections"), payload.get("detections"))
    left["detections"] = merge_list(left.get("detections"), detections)


def build_rows(
    *,
    frames: list[dict[str, Any]],
    annotations: dict[str, dict[str, Any]],
    dataset_id: str,
    batch: str,
    frame_profile: str,
    run_id: str,
    source_artifacts: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for global_index, frame in enumerate(sorted(frames, key=frame_sort_key)):
        annotation = annotations.get(frame["keyframe_id"], blank_annotation())
        texts = clean_list(annotation.get("texts"))
        objects = clean_list(annotation.get("objects"))
        caption = str(annotation.get("caption") or "").strip()
        text_value = " ".join([caption, " ".join(map(str, texts)), " ".join(map(str, objects))]).strip()
        rows.append(
            {
                "schema_version": "frame-annotation-v1",
                "dataset_id": dataset_id,
                "dataset_code": dataset_id,
                "batch": batch,
                "batch_id": batch,
                "frame_profile": frame_profile,
                "video_id": frame["video_id"],
                "keyframe_id": frame["keyframe_id"],
                "frame_id": frame["keyframe_id"],
                "shot_id": frame.get("shot_id"),
                "shot_index": frame.get("shot_index"),
                "frame_idx": int(frame.get("frame_idx") or 0),
                "frame_seconds": float(frame.get("frame_seconds") or 0.0),
                "timestamp_ms": int(float(frame.get("frame_seconds") or 0.0) * 1000),
                "frame_type": frame.get("frame_type") or "key",
                "bucket": frame.get("bucket"),
                "blob_name": frame.get("blob_name"),
                "gcs_uri": frame.get("gcs_uri"),
                "image_uri": frame.get("gcs_uri"),
                "image_gcs_uri": frame.get("gcs_uri"),
                "image_storage_key": frame.get("blob_name"),
                "image_name": frame.get("image_name"),
                "fps": frame.get("fps"),
                "global_index": global_index,
                "kind": "coverage_patch",
                "extractor_name": "coverage_patch",
                "extractor_version": "feature-coverage-patch-v1",
                "model_name": "existing_l21_features_plus_empty_missing",
                "model_version": run_id,
                "run_id": run_id,
                "caption": caption,
                "texts": texts,
                "ocr_texts": texts,
                "objects": objects,
                "detected_objects": objects,
                "object_counts": annotation.get("object_counts") or {},
                "detections": annotation.get("detections") or [],
                "text_value": text_value,
                "payload": {
                    "coverage_patch": True,
                    "source_artifacts": source_artifacts,
                },
            }
        )
    return rows


def build_summary(
    *,
    rows: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    run_id: str,
    dataset_id: str,
    batch: str,
    frame_profile: str,
    local_jsonl: Path,
    local_summary: Path,
    source_summaries: list[dict[str, Any]],
    gcs_jsonl: str,
    gcs_summary: str,
) -> dict[str, Any]:
    video_counts: dict[str, int] = defaultdict(int)
    caption_counts: dict[str, int] = defaultdict(int)
    ocr_counts: dict[str, int] = defaultdict(int)
    object_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        video_id = row["video_id"]
        video_counts[video_id] += 1
        if row.get("caption"):
            caption_counts[video_id] += 1
        if row.get("texts"):
            ocr_counts[video_id] += 1
        if row.get("objects"):
            object_counts[video_id] += 1
    return {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_id": dataset_id,
        "batch": batch,
        "frame_profile": frame_profile,
        "rows": len(rows),
        "frames": len(frames),
        "videos": dict(sorted(video_counts.items())),
        "feature_coverage": {
            "caption_frames": sum(1 for row in rows if row.get("caption")),
            "ocr_frames": sum(1 for row in rows if row.get("texts")),
            "object_frames": sum(1 for row in rows if row.get("objects")),
            "caption_by_video": dict(sorted(caption_counts.items())),
            "ocr_by_video": dict(sorted(ocr_counts.items())),
            "object_by_video": dict(sorted(object_counts.items())),
        },
        "source_artifacts": source_summaries,
        "outputs": {
            "local_annotations": str(local_jsonl),
            "local_summary": str(local_summary),
            "gcs_annotations": gcs_jsonl,
            "gcs_summary": gcs_summary,
        },
    }


def blank_annotation() -> dict[str, Any]:
    return {
        "caption": "",
        "texts": [],
        "objects": [],
        "object_counts": {},
        "detections": [],
    }


def frame_sort_key(row: dict[str, Any]) -> tuple[str, int, str]:
    return (str(row.get("video_id") or ""), int(row.get("frame_idx") or 0), str(row.get("image_name") or ""))


def first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def first_value(*values: Any) -> Any:
    for value in values:
        if value:
            return value
    return None


def first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict) and value:
            return value
    return {}


def clean_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    if value:
        return [value]
    return []


def merge_list(left: Any, right: Any) -> list[Any]:
    values = clean_list(left) + clean_list(right)
    merged: list[Any] = []
    seen: set[str] = set()
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, dict) else str(value)
        if key in seen:
            continue
        seen.add(key)
        merged.append(value)
    return merged


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

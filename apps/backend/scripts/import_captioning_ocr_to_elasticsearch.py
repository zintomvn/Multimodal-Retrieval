from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings, normalize_database_url  # noqa: E402
from import_asr_to_elasticsearch import (  # noqa: E402
    DEFAULT_INDEX,
    FrameRef,
    as_float,
    as_int,
    batched,
    ensure_index,
    load_frame_index,
    split_batches,
)


DEFAULT_CAPTION_ROOT = REPO_ROOT / "data" / "extracted" / "captioning_annotations"
DEFAULT_OCR_ROOT = REPO_ROOT / "data" / "extracted" / "ocr" / "ocr_annotations"


@dataclass(frozen=True)
class AnnotationRecord:
    source_type: str
    batch_id: str
    dataset_code: str
    video_id: str
    keyframe_id: str
    text_value: str
    frame_idx: int
    frame_seconds: float
    timestamp_ms: int
    language: str
    extractor_name: str
    extractor_version: str
    model_name: str
    model_version: str
    run_id: str
    ocr_texts: list[str]


@dataclass
class ImportStats:
    rows_read: int = 0
    actions: int = 0
    skipped_blank: int = 0
    skipped_missing_keyframe: int = 0
    skipped_duplicate: int = 0
    parse_errors: int = 0
    files: int = 0
    seen_document_ids: set[str] = field(default_factory=set)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import frame-level captioning and OCR annotations into Elasticsearch for hybrid text retrieval."
    )
    parser.add_argument("--caption-root", default=str(DEFAULT_CAPTION_ROOT))
    parser.add_argument("--ocr-root", default=str(DEFAULT_OCR_ROOT))
    parser.add_argument("--database-url", default="", help="Defaults to DATABASE_URL from .env.")
    parser.add_argument("--dataset-code", default="aic-2026", help="Backend dataset_code used to validate keyframes.")
    parser.add_argument("--no-dataset-filter", action="store_true")
    parser.add_argument("--elasticsearch-url", default="", help="Defaults to ELASTICSEARCH_URL from .env.")
    parser.add_argument("--index", default=DEFAULT_INDEX)
    parser.add_argument("--batches", default="", help="Optional comma-separated batches, for example L21,L22.")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--limit-videos", type=int, default=0, help="Optional smoke-test cap per source.")
    parser.add_argument("--skip-captioning", action="store_true")
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replace-captioning", action="store_true", help="Delete source_type=caption docs before importing.")
    parser.add_argument("--replace-ocr", action="store_true", help="Delete source_type=ocr docs before importing.")
    parser.add_argument("--refresh", action="store_true", help="Refresh the index after import.")
    return parser.parse_args()


def _annotation_files(root: Path, batches: set[str]) -> list[Path]:
    files = sorted(root.rglob("annotations.jsonl"))
    if not batches:
        return files
    return [
        path
        for path in files
        if any(part.upper() in batches or part.upper().endswith(f"-{batch}") for part in path.parts for batch in batches)
    ]


def _ocr_texts(payload: Any, text_value: str) -> list[str]:
    payload = payload if isinstance(payload, dict) else {}
    values = payload.get("ocr_texts", [])
    if isinstance(values, str):
        values = [values]
    texts = [" ".join(str(value).split()) for value in values if str(value).strip()] if isinstance(values, list) else []
    return texts or ([text_value] if text_value else [])


def iter_records(
    root: Path,
    source_type: str,
    batches: set[str],
    limit_videos: int,
    stats: ImportStats,
) -> Iterator[AnnotationRecord]:
    seen_videos: set[str] = set()
    for path in _annotation_files(root, batches):
        stats.files += 1
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    stats.parse_errors += 1
                    print(f"Skipping invalid JSON: {path}:{line_number}", file=sys.stderr)
                    continue
                if not isinstance(row, dict):
                    stats.parse_errors += 1
                    continue
                kind = str(row.get("kind") or "").strip().lower()
                if kind and kind != source_type:
                    continue
                batch_id = str(row.get("batch_id") or "").strip().upper()
                if batches and batch_id not in batches:
                    continue
                video_id = str(row.get("video_id") or "").strip()
                if limit_videos and video_id not in seen_videos and len(seen_videos) >= limit_videos:
                    continue
                if video_id:
                    seen_videos.add(video_id)
                stats.rows_read += 1
                text_value = " ".join(str(row.get("text_value") or "").split())
                keyframe_id = str(row.get("keyframe_id") or row.get("frame_id") or "").strip()
                if not video_id or not keyframe_id or not text_value:
                    stats.skipped_blank += 1
                    continue
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                yield AnnotationRecord(
                    source_type=source_type,
                    batch_id=batch_id,
                    dataset_code=str(row.get("dataset_code") or "").strip(),
                    video_id=video_id,
                    keyframe_id=keyframe_id,
                    text_value=text_value,
                    frame_idx=as_int(row.get("frame_idx")),
                    frame_seconds=as_float(row.get("frame_seconds")),
                    timestamp_ms=as_int(row.get("timestamp_ms")),
                    language=str(payload.get("language") or row.get("language") or "").strip(),
                    extractor_name=str(row.get("extractor_name") or "").strip(),
                    extractor_version=str(row.get("extractor_version") or "").strip(),
                    model_name=str(row.get("model_name") or "").strip(),
                    model_version=str(row.get("model_version") or "").strip(),
                    run_id=str(row.get("run_id") or "").strip(),
                    ocr_texts=_ocr_texts(payload, text_value) if source_type == "ocr" else [],
                )


def build_actions(
    records: Iterable[AnnotationRecord],
    frames_by_id: dict[str, FrameRef],
    index: str,
    stats: ImportStats,
) -> Iterator[dict[str, Any]]:
    for record in records:
        frame = frames_by_id.get(record.keyframe_id)
        if frame is None:
            stats.skipped_missing_keyframe += 1
            continue
        document_id = f"{record.source_type}::{frame.keyframe_id}"
        if document_id in stats.seen_document_ids:
            stats.skipped_duplicate += 1
            continue
        stats.seen_document_ids.add(document_id)
        source = {
            "source_type": record.source_type,
            "kind": record.source_type,
            "dataset_code": record.dataset_code,
            "batch_id": record.batch_id,
            "video_id": frame.video_id,
            "video_code": frame.video_code,
            "keyframe_id": frame.keyframe_id,
            "frame_id": frame.keyframe_id,
            "frame_idx": frame.frame_idx,
            "frame_seconds": frame.frame_seconds,
            "timestamp_ms": frame.timestamp_ms,
            "segment_id": document_id,
            "start_seconds": frame.frame_seconds,
            "end_seconds": frame.frame_seconds,
            "timestamp_ms_start": frame.timestamp_ms,
            "timestamp_ms_end": frame.timestamp_ms,
            "text_value": record.text_value,
            "asr_text": "",
            "normalized_asr_text": "",
            "raw_asr_text": "",
            "caption": record.text_value if record.source_type == "caption" else "",
            "ocr_texts": record.ocr_texts if record.source_type == "ocr" else [],
            "detected_objects": [],
            "language": record.language,
            "extractor_name": record.extractor_name,
            "extractor_version": record.extractor_version,
            "model_name": record.model_name,
            "model_version": record.model_version,
            "run_id": record.run_id,
        }
        stats.actions += 1
        yield {"_op_type": "index", "_index": index, "_id": document_id, "_source": source}


def _delete_source(client: Any, index: str, source_type: str) -> None:
    client.delete_by_query(
        index=index,
        query={"term": {"source_type": source_type}},
        conflicts="proceed",
        refresh=True,
        wait_for_completion=True,
    )


def _summary(stats_by_source: dict[str, ImportStats], indexed: int, errors: int, dry_run: bool) -> dict[str, Any]:
    return {
        "dry_run": dry_run,
        "documents_indexed": indexed,
        "bulk_errors": errors,
        "sources": {
            source: {
                "files": stats.files,
                "rows_read": stats.rows_read,
                "documents": stats.actions,
                "skipped_blank": stats.skipped_blank,
                "skipped_missing_keyframe": stats.skipped_missing_keyframe,
                "skipped_duplicate": stats.skipped_duplicate,
                "parse_errors": stats.parse_errors,
            }
            for source, stats in stats_by_source.items()
        },
    }


def main() -> None:
    args = parse_args()
    settings = get_settings()
    batches = split_batches(args.batches)
    inputs: list[tuple[str, Path]] = []
    if not args.skip_captioning:
        inputs.append(("caption", Path(args.caption_root).resolve()))
    if not args.skip_ocr:
        inputs.append(("ocr", Path(args.ocr_root).resolve()))
    for _, root in inputs:
        if not root.is_dir():
            raise SystemExit(f"Annotation root not found: {root}")

    print("Loading keyframe metadata from database...", flush=True)
    frames_by_video = load_frame_index(
        database_url=normalize_database_url(args.database_url or settings.database_url),
        dataset_code=args.dataset_code,
        no_dataset_filter=bool(args.no_dataset_filter),
    )
    frames_by_id = {frame.keyframe_id: frame for frames in frames_by_video.values() for frame in frames}
    print(f"Loaded {len(frames_by_id)} keyframes across {len(frames_by_video)} videos.", flush=True)

    stats_by_source = {source: ImportStats() for source, _ in inputs}

    def actions() -> Iterator[dict[str, Any]]:
        for source, root in inputs:
            records = iter_records(
                root=root,
                source_type=source,
                batches=batches,
                limit_videos=max(0, args.limit_videos),
                stats=stats_by_source[source],
            )
            yield from build_actions(records, frames_by_id, args.index, stats_by_source[source])

    if args.dry_run:
        for _ in actions():
            pass
        print(json.dumps(_summary(stats_by_source, indexed=0, errors=0, dry_run=True), ensure_ascii=False, indent=2))
        return

    from elasticsearch import Elasticsearch, helpers

    client = Elasticsearch(args.elasticsearch_url or settings.elasticsearch_url, request_timeout=60, max_retries=1, retry_on_timeout=True)
    ensure_index(client, args.index)
    if args.replace_captioning and not args.skip_captioning:
        _delete_source(client, args.index, "caption")
    if args.replace_ocr and not args.skip_ocr:
        _delete_source(client, args.index, "ocr")

    indexed = 0
    errors = 0
    for batch in batched(actions(), max(1, args.batch_size)):
        ok, failed = helpers.bulk(client.options(request_timeout=90), batch, raise_on_error=False)
        indexed += int(ok)
        errors += len(failed)
        print(f"Indexed {indexed} documents; errors={errors}", flush=True)
    if args.refresh:
        client.indices.refresh(index=args.index)
    print(json.dumps(_summary(stats_by_source, indexed=indexed, errors=errors, dry_run=False), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

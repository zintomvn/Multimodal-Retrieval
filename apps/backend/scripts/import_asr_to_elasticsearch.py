from __future__ import annotations

import argparse
import bisect
import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import database_connect_args, get_settings, normalize_database_url  # noqa: E402


DEFAULT_ASR_ROOT = REPO_ROOT / "data" / "extracted" / "asr" / "asr_output_corrected_split"
DEFAULT_INDEX = "keyframe_annotations"


@dataclass(frozen=True)
class FrameRef:
    keyframe_id: str
    video_id: str
    video_code: str
    frame_idx: int
    frame_seconds: float
    timestamp_ms: int


@dataclass(frozen=True)
class AsrSegment:
    batch_id: str
    dataset_code: str
    video_id: str
    segment_id: str
    start_seconds: float
    end_seconds: float
    text: str
    normalized_text: str
    raw_text: str
    language: str
    correction_status: str
    model_name: str
    run_id: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import ASR segments into local Elasticsearch as frame-level text docs.")
    parser.add_argument("--asr-root", default=str(DEFAULT_ASR_ROOT))
    parser.add_argument("--database-url", default="", help="Defaults to DATABASE_URL from .env.")
    parser.add_argument("--dataset-code", default="aic-2026", help="Backend dataset_code to load keyframes from.")
    parser.add_argument("--no-dataset-filter", action="store_true")
    parser.add_argument("--elasticsearch-url", default="", help="Defaults to ELASTICSEARCH_URL from .env.")
    parser.add_argument("--index", default=DEFAULT_INDEX)
    parser.add_argument("--batches", default="", help="Optional comma-separated batches, e.g. L21,L22.")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--pad-seconds", type=float, default=1.0)
    parser.add_argument("--max-frames-per-segment", type=int, default=8)
    parser.add_argument("--limit-videos", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replace-asr", action="store_true", help="Delete old source_type=asr docs in the index before importing.")
    parser.add_argument("--refresh", action="store_true", help="Refresh index after import.")
    return parser.parse_args()


def split_batches(raw: str) -> set[str]:
    return {item.strip().upper() for item in raw.split(",") if item.strip()}


def as_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def load_frame_index(database_url: str, dataset_code: str, no_dataset_filter: bool) -> dict[str, list[FrameRef]]:
    engine = create_engine(
        normalize_database_url(database_url),
        pool_pre_ping=True,
        connect_args=database_connect_args(normalize_database_url(database_url)),
    )
    where_clause = "" if no_dataset_filter else "WHERE d.dataset_code = :dataset_code"
    sql = text(
        f"""
        SELECT
            k.keyframe_id,
            k.video_id,
            v.video_code,
            k.frame_idx,
            k.frame_seconds,
            k.timestamp_ms
        FROM keyframes k
        JOIN videos v ON v.video_id = k.video_id
        JOIN datasets d ON d.dataset_id = v.dataset_id
        {where_clause}
        ORDER BY k.video_id ASC, k.frame_seconds ASC, k.frame_idx ASC
        """
    )
    frames_by_video: dict[str, list[FrameRef]] = defaultdict(list)
    with engine.connect() as connection:
        for row in connection.execute(sql, {"dataset_code": dataset_code}).mappings():
            frames_by_video[str(row["video_id"])].append(
                FrameRef(
                    keyframe_id=str(row["keyframe_id"]),
                    video_id=str(row["video_id"]),
                    video_code=str(row["video_code"]),
                    frame_idx=as_int(row["frame_idx"]),
                    frame_seconds=as_float(row["frame_seconds"]),
                    timestamp_ms=as_int(row["timestamp_ms"]),
                )
            )
    engine.dispose()
    return dict(frames_by_video)


def iter_asr_segments(asr_root: Path, batches: set[str], limit_videos: int) -> Iterator[AsrSegment]:
    video_count = 0
    manifests = sorted(asr_root.glob("L*/asr_segments.jsonl"))
    for manifest in manifests:
        batch_id = manifest.parent.name.upper()
        if batches and batch_id not in batches:
            continue
        with manifest.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                video = json.loads(line)
                video_count += 1
                if limit_videos and video_count > limit_videos:
                    return
                source = video.get("source") if isinstance(video.get("source"), dict) else {}
                model = video.get("model") if isinstance(video.get("model"), dict) else {}
                video_id = str(video.get("video_id") or source.get("video_id") or "").strip()
                for segment in video.get("segments") or []:
                    if not isinstance(segment, dict):
                        continue
                    text_value = str(segment.get("text") or segment.get("corrected_text") or "").strip()
                    normalized_text = str(segment.get("normalized_text") or "").strip()
                    raw_text = str(segment.get("raw_text") or "").strip()
                    if not text_value and not normalized_text and not raw_text:
                        continue
                    start_seconds = as_float(segment.get("start_seconds"), 0.0)
                    end_seconds = as_float(segment.get("end_seconds"), start_seconds)
                    if end_seconds < start_seconds:
                        end_seconds = start_seconds
                    yield AsrSegment(
                        batch_id=str(video.get("batch_id") or batch_id),
                        dataset_code=str(video.get("dataset_code") or ""),
                        video_id=video_id,
                        segment_id=str(segment.get("segment_id") or f"{video_id}_ASR_{start_seconds:.3f}"),
                        start_seconds=start_seconds,
                        end_seconds=end_seconds,
                        text=text_value or raw_text or normalized_text,
                        normalized_text=normalized_text,
                        raw_text=raw_text,
                        language=str(segment.get("language") or model.get("language") or ""),
                        correction_status=str(segment.get("correction_status") or ""),
                        model_name=str(model.get("name") or ""),
                        run_id=str(video.get("run_id") or ""),
                    )


def choose_frames(
    frames: list[FrameRef],
    segment: AsrSegment,
    pad_seconds: float,
    max_frames: int,
) -> list[FrameRef]:
    if not frames:
        return []
    times = [frame.frame_seconds for frame in frames]
    start = max(0.0, segment.start_seconds - pad_seconds)
    end = max(start, segment.end_seconds + pad_seconds)
    lo = bisect.bisect_left(times, start)
    hi = bisect.bisect_right(times, end)
    candidates = frames[lo:hi]
    midpoint = (segment.start_seconds + segment.end_seconds) / 2.0
    if not candidates:
        nearest = min(frames, key=lambda frame: abs(frame.frame_seconds - midpoint))
        return [nearest]
    if max_frames > 0 and len(candidates) > max_frames:
        candidates = sorted(candidates, key=lambda frame: abs(frame.frame_seconds - midpoint))[:max_frames]
        candidates.sort(key=lambda frame: frame.frame_idx)
    return candidates


def asr_doc(segment: AsrSegment, frame: FrameRef) -> dict[str, Any]:
    text_value = segment.text
    return {
        "source_type": "asr",
        "kind": "asr",
        "dataset_code": segment.dataset_code,
        "batch_id": segment.batch_id,
        "video_id": frame.video_id,
        "video_code": frame.video_code,
        "keyframe_id": frame.keyframe_id,
        "frame_id": frame.keyframe_id,
        "frame_idx": frame.frame_idx,
        "frame_seconds": frame.frame_seconds,
        "timestamp_ms": frame.timestamp_ms,
        "segment_id": segment.segment_id,
        "start_seconds": segment.start_seconds,
        "end_seconds": segment.end_seconds,
        "timestamp_ms_start": int(segment.start_seconds * 1000),
        "timestamp_ms_end": int(segment.end_seconds * 1000),
        "text_value": text_value,
        "asr_text": text_value,
        "normalized_asr_text": segment.normalized_text,
        "raw_asr_text": segment.raw_text,
        "caption": "",
        "ocr_texts": [],
        "detected_objects": [],
        "language": segment.language,
        "correction_status": segment.correction_status,
        "model_name": segment.model_name,
        "run_id": segment.run_id,
    }


def ensure_index(client: Any, index: str) -> None:
    properties = {
        "source_type": {"type": "keyword"},
        "kind": {"type": "keyword"},
        "dataset_code": {"type": "keyword"},
        "batch_id": {"type": "keyword"},
        "video_id": {"type": "keyword"},
        "video_code": {"type": "keyword"},
        "keyframe_id": {"type": "keyword"},
        "frame_id": {"type": "keyword"},
        "frame_idx": {"type": "integer"},
        "frame_seconds": {"type": "float"},
        "timestamp_ms": {"type": "long"},
        "segment_id": {"type": "keyword"},
        "start_seconds": {"type": "float"},
        "end_seconds": {"type": "float"},
        "timestamp_ms_start": {"type": "long"},
        "timestamp_ms_end": {"type": "long"},
        "text_value": {"type": "text"},
        "asr_text": {"type": "text"},
        "normalized_asr_text": {"type": "text"},
        "raw_asr_text": {"type": "text"},
        "caption": {"type": "text"},
        "ocr_texts": {"type": "text"},
        "detected_objects": {"type": "text"},
        "language": {"type": "keyword"},
        "correction_status": {"type": "keyword"},
        "model_name": {"type": "keyword"},
        "run_id": {"type": "keyword"},
    }
    if client.indices.exists(index=index):
        mapping = client.indices.get_mapping(index=index)
        existing = mapping.get(index, {}).get("mappings", {}).get("properties", {})
        missing = {name: config for name, config in properties.items() if name not in existing}
        if missing:
            client.indices.put_mapping(index=index, properties=missing)
        return
    client.indices.create(
        index=index,
        settings={"number_of_shards": 1, "number_of_replicas": 0},
        mappings={"dynamic": True, "properties": properties},
    )


def batched(items: Iterable[dict[str, Any]], batch_size: int) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for item in items:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def build_actions(
    segments: Iterable[AsrSegment],
    frames_by_video: dict[str, list[FrameRef]],
    index: str,
    pad_seconds: float,
    max_frames_per_segment: int,
) -> Iterator[dict[str, Any]]:
    for segment in segments:
        frames = choose_frames(
            frames=frames_by_video.get(segment.video_id, []),
            segment=segment,
            pad_seconds=pad_seconds,
            max_frames=max_frames_per_segment,
        )
        for frame in frames:
            yield {
                "_op_type": "index",
                "_index": index,
                "_id": f"asr::{segment.segment_id}::{frame.keyframe_id}",
                "_source": asr_doc(segment, frame),
            }


def main() -> None:
    args = parse_args()
    settings = get_settings()
    asr_root = Path(args.asr_root).resolve()
    if not asr_root.is_dir():
        raise SystemExit(f"ASR root not found: {asr_root}")

    database_url = normalize_database_url(args.database_url or settings.database_url)
    elasticsearch_url = args.elasticsearch_url or settings.elasticsearch_url
    batches = split_batches(args.batches)

    print("Loading keyframe metadata from database...")
    frames_by_video = load_frame_index(
        database_url=database_url,
        dataset_code=args.dataset_code,
        no_dataset_filter=bool(args.no_dataset_filter),
    )
    frame_count = sum(len(frames) for frames in frames_by_video.values())
    print(f"Loaded {frame_count} keyframes across {len(frames_by_video)} videos.")

    segments = list(iter_asr_segments(asr_root, batches=batches, limit_videos=max(0, args.limit_videos)))
    actions_iter = build_actions(
        segments,
        frames_by_video=frames_by_video,
        index=args.index,
        pad_seconds=max(0.0, args.pad_seconds),
        max_frames_per_segment=args.max_frames_per_segment,
    )
    if args.dry_run:
        docs = list(actions_iter)
        mapped_segments = {doc["_source"]["segment_id"] for doc in docs}
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "asr_root": str(asr_root),
                    "index": args.index,
                    "batches": sorted(batches) if batches else "all",
                    "segments": len(segments),
                    "mapped_segments": len(mapped_segments),
                    "documents": len(docs),
                    "keyframes": frame_count,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    from elasticsearch import Elasticsearch, helpers

    client = Elasticsearch(elasticsearch_url, request_timeout=30, max_retries=1, retry_on_timeout=True)
    ensure_index(client, args.index)
    if args.replace_asr:
        client.delete_by_query(
            index=args.index,
            query={"term": {"source_type": "asr"}},
            conflicts="proceed",
            refresh=True,
            wait_for_completion=True,
        )

    indexed = 0
    errors = 0
    for batch in batched(actions_iter, max(1, args.batch_size)):
        ok, failed = helpers.bulk(client, batch, raise_on_error=False, request_timeout=60)
        indexed += int(ok)
        errors += len(failed)
        print(f"Indexed {indexed} docs; errors={errors}", flush=True)

    if args.refresh:
        client.indices.refresh(index=args.index)
    print(
        json.dumps(
            {
                "dry_run": False,
                "asr_root": str(asr_root),
                "index": args.index,
                "batches": sorted(batches) if batches else "all",
                "segments": len(segments),
                "documents_indexed": indexed,
                "errors": errors,
                "keyframes": frame_count,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

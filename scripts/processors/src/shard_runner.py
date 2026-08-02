from __future__ import annotations

import json
import logging
import time
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifact_io import ArtifactStore, join_uri
from .checkpoint_store import CheckpointStore, default_worker_id, utc_now
from .extractors import FrameFeatureExtractor
from .gcs_source import GCSFrameSource, chunked
from .manifest import frame_item_from_record, frame_record_from_item, load_manifest_rows
from .pipeline_config import PipelineConfig


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShardRunOptions:
    """Runtime options for one notebook shard."""

    stage: str
    shard_uri: str
    output_prefix: str
    checkpoint_root: str
    run_id: str
    worker_id: str = ""
    max_runtime_seconds: int = 0
    heartbeat_seconds: int = 120
    lease_ttl_seconds: int = 2700
    gcs_timeout: float = 60.0
    download_workers: int = 8
    batch_size: int = 8
    max_frames: int = 0
    warmup_models: bool = False
    dry_run: bool = False


def run_feature_shard(config: PipelineConfig, options: ShardRunOptions) -> dict[str, Any]:
    """Process one manifest shard and write feature artifact parts."""
    worker_id = options.worker_id or default_worker_id(options.stage)
    attempt_id = f"{worker_id}-{int(time.time())}"
    settings = config.settings
    checkpoint_store = CheckpointStore(
        root_uri=options.checkpoint_root,
        run_id=options.run_id,
        credentials_file=settings.gcs_credentials_file,
        lease_ttl_seconds=options.lease_ttl_seconds,
        timeout_seconds=options.gcs_timeout,
    )
    shard_id = _shard_id_from_uri(options.shard_uri)
    if checkpoint_store.is_complete(options.stage, shard_id):
        return {"status": "already_complete", "stage": options.stage, "shard_id": shard_id}
    lease = checkpoint_store.try_claim(options.stage, shard_id, worker_id)
    if not lease.claimed:
        return {
            "status": "lease_held",
            "stage": options.stage,
            "shard_id": shard_id,
            "owner": lease.owner,
            "expires_at_epoch": lease.expires_at_epoch,
        }

    rows = load_manifest_rows(options.shard_uri, credentials_file=settings.gcs_credentials_file, timeout_seconds=options.gcs_timeout)
    if options.max_frames:
        rows = rows[: options.max_frames]
    checkpoint = checkpoint_store.load(options.stage, shard_id)
    next_index = int(checkpoint.get("next_index") or 0)
    processed = int(checkpoint.get("processed") or 0)
    failed = int(checkpoint.get("failed") or 0)
    part_index = int(checkpoint.get("next_part_index") or 0)

    LOGGER.info("Shard %s stage=%s rows=%s resume_next_index=%s", shard_id, options.stage, len(rows), next_index)
    if options.dry_run:
        return {
            "status": "dry_run",
            "stage": options.stage,
            "shard_id": shard_id,
            "rows": len(rows),
            "resume_next_index": next_index,
        }

    extractor = FrameFeatureExtractor(config.raw.get("models", {}))
    if options.warmup_models:
        extractor.warmup()

    started = time.monotonic()
    last_heartbeat = 0.0
    output_parts: list[str] = list(checkpoint.get("output_parts") or [])
    source = _source_for_rows(rows, credentials_file=settings.gcs_credentials_file, timeout_seconds=options.gcs_timeout)

    with tempfile.TemporaryDirectory(prefix=f"{options.stage}_{shard_id}_") as tmp:
        tmp_root = Path(tmp)
        while next_index < len(rows):
            if options.max_runtime_seconds and time.monotonic() - started >= options.max_runtime_seconds:
                LOGGER.info("Max runtime guard reached for shard=%s next_index=%s", shard_id, next_index)
                break
            if not checkpoint_store.can_write(options.stage, shard_id, worker_id):
                LOGGER.warning("Lease lost before processing next batch for shard=%s worker=%s", shard_id, worker_id)
                break
            batch_rows = rows[next_index : next_index + options.batch_size]
            frames = [frame_item_from_record(row) for row in batch_rows]
            try:
                local_paths = _download_batch(source, frames, tmp_root, options.download_workers)
                contexts = [_caption_context(row, frame) for row, frame in zip(batch_rows, frames)]
                annotations, embeddings, timings = extractor.process_batch(local_paths, contexts=contexts)
                artifact_rows = _build_artifact_rows(
                    options=options,
                    shard_id=shard_id,
                    worker_id=worker_id,
                    attempt_id=attempt_id,
                    part_index=part_index,
                    rows=batch_rows,
                    frames=frames,
                    annotations=annotations,
                    embeddings=embeddings,
                    timings=timings,
                )
                if not checkpoint_store.can_write(options.stage, shard_id, worker_id):
                    LOGGER.warning("Lease lost before artifact write for shard=%s worker=%s", shard_id, worker_id)
                    break
                part_uri = _part_uri(options.output_prefix, options.run_id, options.stage, shard_id, worker_id, attempt_id, part_index)
                ArtifactStore(settings.gcs_credentials_file, options.gcs_timeout).write_jsonl(part_uri, artifact_rows)
                output_parts.append(part_uri)
                processed += len(batch_rows)
                next_index += len(batch_rows)
                part_index += 1
                for path in local_paths:
                    path.unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001 - checkpoint and continue with next batch.
                LOGGER.exception("Feature batch failed at shard=%s next_index=%s: %s", shard_id, next_index, exc)
                failed += len(batch_rows)
                next_index += len(batch_rows)

            now = time.monotonic()
            if now - last_heartbeat >= options.heartbeat_seconds:
                checkpoint_store.heartbeat(options.stage, shard_id, worker_id)
                last_heartbeat = now
            checkpoint_store.save(
                options.stage,
                shard_id,
                {
                    "worker_id": worker_id,
                    "attempt_id": attempt_id,
                    "next_index": next_index,
                    "next_part_index": part_index,
                    "processed": processed,
                    "failed": failed,
                    "rows": len(rows),
                    "output_parts": output_parts,
                    "last_keyframe_id": frames[-1].keyframe_id if frames else "",
                },
            )

    status = "completed" if next_index >= len(rows) else "paused"
    summary = {
        "status": status,
        "run_id": options.run_id,
        "stage": options.stage,
        "shard_id": shard_id,
        "worker_id": worker_id,
        "attempt_id": attempt_id,
        "rows": len(rows),
        "processed": processed,
        "failed": failed,
        "next_index": next_index,
        "output_parts": output_parts,
        "completed_at": utc_now() if status == "completed" else None,
    }
    checkpoint_store.save(options.stage, shard_id, summary)
    if status == "completed":
        checkpoint_store.mark_complete(options.stage, shard_id, summary)
    return summary


def _source_for_rows(rows: list[dict[str, Any]], credentials_file: str, timeout_seconds: float) -> GCSFrameSource:
    if not rows:
        raise ValueError("Manifest shard is empty.")
    first = frame_item_from_record(rows[0])
    return GCSFrameSource(bucket_name=first.bucket, credentials_file=credentials_file, timeout_seconds=timeout_seconds)


def _download_batch(source: GCSFrameSource, frames, tmp_root: Path, workers: int) -> list[Path]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    local_paths = [tmp_root / item.video_id / item.image_name for item in frames]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(source.download_to, item, path) for item, path in zip(frames, local_paths)]
        for future in as_completed(futures):
            future.result()
    return local_paths


def _build_artifact_rows(
    options: ShardRunOptions,
    shard_id: str,
    worker_id: str,
    attempt_id: str,
    part_index: int,
    rows: list[dict[str, Any]],
    frames,
    annotations: list[dict[str, Any]],
    embeddings,
    timings: dict[str, float],
) -> list[dict[str, Any]]:
    artifact_rows: list[dict[str, Any]] = []
    for local_index, (source_row, frame, annotation) in enumerate(zip(rows, frames, annotations)):
        embedding = None
        if embeddings is not None:
            embedding = embeddings[local_index].astype(float).tolist()
        artifact_rows.append(
            {
                "schema_version": "aic.feature_artifact.v1",
                "run_id": options.run_id,
                "stage": options.stage,
                "shard_id": shard_id,
                "worker_id": worker_id,
                "attempt_id": attempt_id,
                "part_index": part_index,
                "record_index": int(source_row.get("global_index") or source_row.get("shard_local_index") or 0),
                "created_at": utc_now(),
                "frame": frame_record_from_item(
                    frame,
                    {
                        "dataset_id": source_row.get("dataset_id"),
                        "batch": source_row.get("batch"),
                        "manifest_global_index": source_row.get("global_index"),
                    },
                ),
                "annotation": {
                    "caption": annotation.get("caption") or "",
                    "texts": annotation.get("texts") if isinstance(annotation.get("texts"), list) else [],
                    "objects": annotation.get("objects") if isinstance(annotation.get("objects"), list) else [],
                    "object_counts": annotation.get("object_counts") if isinstance(annotation.get("object_counts"), dict) else {},
                    "detections": annotation.get("detections") if isinstance(annotation.get("detections"), list) else [],
                },
                "embedding": embedding,
                "timings_seconds": {key: round(float(value), 4) for key, value in timings.items()},
                "status": "ok",
            }
        )
    return artifact_rows


def _part_uri(output_prefix: str, run_id: str, stage: str, shard_id: str, worker_id: str, attempt_id: str, part_index: int) -> str:
    return join_uri(
        output_prefix.rstrip("/"),
        f"run_id={run_id}",
        f"stage={stage}",
        f"shard_id={shard_id}",
        f"worker_id={_safe_path_part(worker_id)}",
        f"attempt_id={_safe_path_part(attempt_id)}",
        f"part-{part_index:05d}.jsonl",
    )


def _caption_context(row: dict[str, Any], frame) -> dict[str, Any]:
    return {
        "dataset_id": row.get("dataset_id"),
        "batch": row.get("batch"),
        "video_id": frame.video_id,
        "keyframe_id": frame.keyframe_id,
        "shot_id": frame.shot_id,
        "shot_index": frame.shot_index,
        "frame_idx": frame.frame_idx,
        "frame_seconds": frame.frame_seconds,
        "frame_type": frame.frame_type,
        "image_name": frame.image_name,
    }


def _shard_id_from_uri(uri: str) -> str:
    name = Path(uri.rstrip("/")).name
    if name.endswith(".jsonl"):
        name = name[: -len(".jsonl")]
    return name.replace("\\", "/").split("/")[-1]


def _safe_path_part(value: str) -> str:
    return str(value or "unknown").replace("\\", "_").replace("/", "_").replace("=", "-")


def format_summary(summary: dict[str, Any]) -> str:
    """Return a compact JSON summary for CLI output."""
    return json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)

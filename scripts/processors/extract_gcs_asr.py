from __future__ import annotations

import argparse
import json
import logging
import tempfile
import time
from pathlib import Path
from typing import Any

from src.artifact_io import ArtifactStore, gcs_client, join_uri
from src.checkpoint_store import CheckpointStore, default_worker_id, utc_now
from src.config import get_processor_settings


VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
LOGGER = logging.getLogger("gcs_asr_processor")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run faster-whisper ASR over GCS raw videos with checkpoint/resume.")
    parser.add_argument("--bucket", default="")
    parser.add_argument("--gcs-prefix", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--stage", default="asr")
    parser.add_argument("--worker-id", default="")
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument("--checkpoint-root", required=True)
    parser.add_argument("--model-size", default="small")
    parser.add_argument("--language", default="vi")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--compute-type", default="auto")
    parser.add_argument("--max-videos", type=int, default=0)
    parser.add_argument("--max-runtime-seconds", type=int, default=0)
    parser.add_argument("--gcs-timeout", type=float, default=60.0)
    parser.add_argument("--lease-ttl-seconds", type=int, default=2700)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    summary = run_asr(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


def run_asr(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_processor_settings()
    bucket_name = args.bucket or settings.gcs_bucket
    if not bucket_name:
        raise RuntimeError("Set --bucket or GCS_BUCKET.")
    worker_id = args.worker_id or default_worker_id("asr")
    attempt_id = f"{worker_id}-{int(time.time())}"
    shard_id = _safe_shard_id(args.gcs_prefix)
    checkpoint_store = CheckpointStore(
        root_uri=args.checkpoint_root,
        run_id=args.run_id,
        credentials_file=settings.gcs_credentials_file,
        lease_ttl_seconds=args.lease_ttl_seconds,
        timeout_seconds=args.gcs_timeout,
    )
    if checkpoint_store.is_complete(args.stage, shard_id):
        return {"status": "already_complete", "stage": args.stage, "shard_id": shard_id}
    lease = checkpoint_store.try_claim(args.stage, shard_id, worker_id)
    if not lease.claimed:
        return {"status": "lease_held", "owner": lease.owner, "expires_at_epoch": lease.expires_at_epoch}

    videos = _list_videos(bucket_name, args.gcs_prefix, settings.gcs_credentials_file, args.gcs_timeout)
    if args.max_videos:
        videos = videos[: args.max_videos]
    checkpoint = checkpoint_store.load(args.stage, shard_id)
    next_index = int(checkpoint.get("next_index") or 0)
    output_parts = list(checkpoint.get("output_parts") or [])
    if args.dry_run:
        return {"status": "dry_run", "videos": len(videos), "resume_next_index": next_index, "shard_id": shard_id}

    model = _load_model(args)
    store = ArtifactStore(settings.gcs_credentials_file, args.gcs_timeout)
    started = time.monotonic()
    processed = int(checkpoint.get("processed") or 0)
    failed = int(checkpoint.get("failed") or 0)
    part_index = int(checkpoint.get("next_part_index") or 0)
    with tempfile.TemporaryDirectory(prefix="gcs_asr_") as tmp:
        tmp_root = Path(tmp)
        while next_index < len(videos):
            if args.max_runtime_seconds and time.monotonic() - started >= args.max_runtime_seconds:
                break
            if not checkpoint_store.can_write(args.stage, shard_id, worker_id):
                LOGGER.warning("Lease lost before processing next video for shard=%s worker=%s", shard_id, worker_id)
                break
            item = videos[next_index]
            try:
                local_path = tmp_root / Path(item["blob_name"]).name
                _download_blob(bucket_name, item["blob_name"], local_path, settings.gcs_credentials_file, args.gcs_timeout)
                segments, info = model.transcribe(
                    str(local_path),
                    language=args.language or None,
                    vad_filter=True,
                )
                record = _build_asr_record(args, item, segments, info, worker_id, attempt_id)
                if not checkpoint_store.can_write(args.stage, shard_id, worker_id):
                    LOGGER.warning("Lease lost before ASR artifact write for shard=%s worker=%s", shard_id, worker_id)
                    break
                part_uri = _part_uri(args.output_prefix, args.run_id, args.stage, shard_id, worker_id, attempt_id, part_index)
                store.write_jsonl(part_uri, [record])
                output_parts.append(part_uri)
                processed += 1
                part_index += 1
                local_path.unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001 - checkpoint and continue.
                LOGGER.exception("ASR failed for %s: %s", item.get("gcs_uri"), exc)
                failed += 1
            next_index += 1
            checkpoint_store.heartbeat(args.stage, shard_id, worker_id)
            checkpoint_store.save(
                args.stage,
                shard_id,
                {
                    "worker_id": worker_id,
                    "attempt_id": attempt_id,
                    "next_index": next_index,
                    "next_part_index": part_index,
                    "processed": processed,
                    "failed": failed,
                    "videos": len(videos),
                    "output_parts": output_parts,
                },
            )

    status = "completed" if next_index >= len(videos) else "paused"
    summary = {
        "status": status,
        "run_id": args.run_id,
        "stage": args.stage,
        "shard_id": shard_id,
        "worker_id": worker_id,
        "attempt_id": attempt_id,
        "videos": len(videos),
        "processed": processed,
        "failed": failed,
        "next_index": next_index,
        "output_parts": output_parts,
        "shard_id": shard_id,
    }
    checkpoint_store.save(args.stage, shard_id, summary)
    if status == "completed":
        checkpoint_store.mark_complete(args.stage, shard_id, summary)
    return summary


def _load_model(args: argparse.Namespace):
    from faster_whisper import WhisperModel

    device = args.device
    if device == "auto":
        try:
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"
    compute_type = args.compute_type
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    return WhisperModel(args.model_size, device=device, compute_type=compute_type)


def _list_videos(bucket_name: str, prefix: str, credentials_file: str, timeout: float) -> list[dict[str, str]]:
    client = gcs_client(credentials_file)
    rows = []
    for blob in client.list_blobs(bucket_name, prefix=prefix.strip("/"), timeout=timeout):
        if Path(blob.name).suffix.lower() not in VIDEO_SUFFIXES:
            continue
        video_id = Path(blob.name).stem
        rows.append({"video_id": video_id, "bucket": bucket_name, "blob_name": blob.name, "gcs_uri": f"gs://{bucket_name}/{blob.name}"})
    return sorted(rows, key=lambda item: item["blob_name"])


def _download_blob(bucket_name: str, blob_name: str, destination: Path, credentials_file: str, timeout: float) -> None:
    client = gcs_client(credentials_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    client.bucket(bucket_name).blob(blob_name).download_to_filename(str(destination), timeout=timeout)


def _build_asr_record(args: argparse.Namespace, item: dict[str, str], segments, info: Any, worker_id: str, attempt_id: str) -> dict[str, Any]:
    rows = []
    for index, segment in enumerate(segments):
        rows.append(
            {
                "segment_id": f"{item['video_id']}_ASR_{index:06d}",
                "start_seconds": round(float(segment.start), 3),
                "end_seconds": round(float(segment.end), 3),
                "text": str(segment.text).strip(),
                "language": args.language,
                "confidence": None,
            }
        )
    return {
        "schema_version": "aic.asr_artifact.v1",
        "run_id": args.run_id,
        "stage": args.stage,
        "shard_id": _safe_shard_id(args.gcs_prefix),
        "worker_id": worker_id,
        "attempt_id": attempt_id,
        "created_at": utc_now(),
        "video_id": item["video_id"],
        "source": item,
        "segments": rows,
        "model": {
            "name": f"faster-whisper-{args.model_size}",
            "language": getattr(info, "language", args.language),
        },
    }


def _safe_shard_id(value: str) -> str:
    return value.strip("/").replace("/", "_").replace("=", "-")[:120]


def _part_uri(output_prefix: str, run_id: str, stage: str, shard_id: str, worker_id: str, attempt_id: str, part_index: int) -> str:
    return join_uri(
        output_prefix,
        f"run_id={run_id}",
        f"stage={stage}",
        f"shard_id={shard_id}",
        f"worker_id={_safe_path_part(worker_id)}",
        f"attempt_id={_safe_path_part(attempt_id)}",
        f"part-{part_index:05d}.jsonl",
    )


def _safe_path_part(value: str) -> str:
    return str(value or "unknown").replace("\\", "_").replace("/", "_").replace("=", "-")


if __name__ == "__main__":
    main()

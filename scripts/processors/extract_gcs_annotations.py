from __future__ import annotations

import argparse
import json
import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from cloud_sinks import CloudAnnotationSink
from cloud_sinks.config import SinkConfig
from extractors import FrameFeatureExtractor
from gcs_source import GCSFrameSource, chunked
from pipeline_config import apply_cli_overrides, load_pipeline_config


LOGGER = logging.getLogger("gcs_frame_processor")
DEFAULT_CONFIG = Path(__file__).resolve().with_name("processor_config.yaml")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the cloud frame annotation job."""
    parser = argparse.ArgumentParser(
        description="Extract annotations and embeddings from GCS frames into PostgreSQL, Zilliz/Milvus and Elasticsearch."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="YAML config path.")
    parser.add_argument("--profile", default="smoke", help="Config profile to merge, e.g. smoke, embedding_only, full.")
    parser.add_argument("--gcs-prefix", default="", help="GCS prefix containing video_id/*.jpg frames.")
    parser.add_argument("--bucket", default="", help="GCS bucket; defaults to GCS_BUCKET from .env.")
    parser.add_argument("--shot-segments", default="", help="Optional local path or gs://.../shot_segments.csv.")
    parser.add_argument("--dataset-code", default="")
    parser.add_argument("--dataset-name", default="")
    parser.add_argument("--dataset-version", default="")
    parser.add_argument("--features", default="", help="Optional comma-separated override: embedding,caption,ocr,objects.")
    parser.add_argument("--batch-size", type=int, default=0, help="Pipeline batch size override.")
    parser.add_argument("--download-workers", type=int, default=0, help="Parallel GCS download workers override.")
    parser.add_argument("--gcs-timeout", type=float, default=0, help="Per-request GCS timeout/retry deadline override.")
    parser.add_argument("--device", default="", help="auto, cpu, cuda, cuda:0...")
    parser.add_argument("--model-cache-dir", default="", help="Local model cache/checkpoint directory override.")
    parser.add_argument("--max-frames", type=int, default=0, help="Limit frames for smoke tests.")
    parser.add_argument("--video-id", action="append", default=[], help="Process only this video_id; repeatable.")
    parser.add_argument("--annotations-jsonl", default="", help="Optional local audit JSONL output.")
    parser.add_argument("--log-file", default="")
    parser.add_argument("--warmup-models", action="store_true", help="Initialize/download models before listing frames.")
    parser.add_argument("--dry-run", action="store_true", help="List frames and initialize lightweight clients only; skip models and writes.")
    return parser.parse_args()


def setup_logging(log_file: str) -> None:
    """Configure console and file logging."""
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_file, encoding="utf-8")],
    )
    logging.getLogger("elastic_transport").setLevel(logging.ERROR)


def main() -> None:
    """Run the GCS-to-cloud annotation extraction pipeline."""
    args = parse_args()
    pipeline_config = apply_cli_overrides(
        load_pipeline_config(args.config, profile=args.profile),
        vars(args),
    )
    raw = pipeline_config.raw
    run_config = raw.get("run", {})
    model_config = raw.get("models", {})
    sink_config = raw.get("sinks", {})
    dataset_config = raw.get("dataset", {})
    env_overrides = raw.get("env", {})

    setup_logging(str(run_config.get("log_file", "scripts/processors/processor.log")))
    bucket = str(env_overrides.get("bucket") or pipeline_config.settings.gcs_bucket)
    if not bucket:
        raise ValueError("GCS bucket is required via --bucket or GCS_BUCKET in .env")

    LOGGER.info("Using config=%s profile=%s features=%s", args.config, args.profile, sorted(pipeline_config.enabled_features))

    source = GCSFrameSource(
        bucket_name=bucket,
        credentials_file=pipeline_config.settings.gcs_credentials_file,
        timeout_seconds=float(run_config.get("gcs_timeout", 20)),
    )
    gcs_prefix = str(run_config.get("gcs_prefix", "frames")).strip("/")
    LOGGER.info("Listing frames from gs://%s/%s", bucket, gcs_prefix)
    try:
        frames = source.list_frames(
            prefix=gcs_prefix,
            shot_segments_path=str(run_config.get("shot_segments", "")),
            video_ids=set(run_config.get("video_ids") or []) or None,
            limit=int(run_config.get("max_frames") or 0) or None,
        )
    except Exception as exc:
        LOGGER.error("Could not list GCS frames: %s", exc)
        raise SystemExit(
            "Could not list GCS frames. Check GCS_BUCKET/GCS_CREDENTIALS_FILE, network/proxy access "
            f"to oauth2.googleapis.com and storage.googleapis.com, then retry. Root error: {exc}"
        ) from exc
    LOGGER.info("Found %s frames across %s videos", len(frames), len({item.video_id for item in frames}))
    if args.dry_run:
        print(json.dumps({"frames": len(frames), "videos": sorted({item.video_id for item in frames})[:20]}, ensure_ascii=False, indent=2))
        return

    extractor = FrameFeatureExtractor(model_config)
    if args.warmup_models:
        LOGGER.info("Warming up enabled models")
        extractor.warmup()

    sink = CloudAnnotationSink(
        SinkConfig(
            database_url=pipeline_config.settings.database_url,
            milvus_uri=pipeline_config.settings.milvus_uri,
            milvus_token=pipeline_config.settings.milvus_token,
            elasticsearch_url=pipeline_config.settings.elasticsearch_url,
            dataset_code=str(dataset_config.get("code", "aic-2026")),
            dataset_name=str(dataset_config.get("name", "aic-2026")),
            dataset_version=str(dataset_config.get("version", "v1")),
            dataset_root_uri=f"gs://{bucket}/{gcs_prefix}",
            gcs_public_url=pipeline_config.settings.gcs_public_url,
            milvus_collection=str(sink_config.get("milvus_collection", "keyframe_embeddings")),
            elasticsearch_index=str(sink_config.get("elasticsearch_index", "keyframe_annotations")),
            model_version=str(sink_config.get("model_version", "openclip-vit-b-32-laion2b_s34b_b79k")),
            write_pg=bool(sink_config.get("write_pg", True)),
            write_milvus=bool(sink_config.get("write_milvus", True)),
            write_elasticsearch=bool(sink_config.get("write_elasticsearch", True)),
            postgres_disable_prepared_statements=bool(sink_config.get("postgres_disable_prepared_statements", True)),
            fail_on_sink_error=bool(sink_config.get("fail_on_sink_error", False)),
            elasticsearch_request_timeout=float(sink_config.get("elasticsearch_request_timeout", 10)),
            elasticsearch_max_retries=int(sink_config.get("elasticsearch_max_retries", 1)),
            elasticsearch_probe_timeout=float(sink_config.get("elasticsearch_probe_timeout", 1)),
            elasticsearch_disable_after_error=bool(sink_config.get("elasticsearch_disable_after_error", True)),
        )
    )

    audit_handle = None
    annotations_jsonl = str(run_config.get("annotations_jsonl", ""))
    if annotations_jsonl:
        audit_path = Path(annotations_jsonl)
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_handle = audit_path.open("a", encoding="utf-8")

    totals = {"processed": 0, "pg": 0, "milvus": 0, "es": 0, "sink_errors": 0, "failed": 0}
    try:
        with tempfile.TemporaryDirectory(prefix="gcs_frames_") as tmp:
            tmp_root = Path(tmp)
            batch_size = int(run_config.get("batch_size", 8))
            batches = list(chunked(frames, batch_size))
            for batch in tqdm(batches, desc="Frame batches", unit="batch"):
                local_paths = _download_batch(source, batch, tmp_root, int(run_config.get("download_workers", 8)))
                try:
                    annotations, embeddings, timings = extractor.process_batch(local_paths)
                    for item, record in zip(batch, annotations):
                        record["video_id"] = item.video_id
                        record["gcs_uri"] = item.gcs_uri
                        record["keyframe_id"] = item.keyframe_id
                        if audit_handle:
                            audit_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    written = sink.upsert_batch(batch, annotations, embeddings)
                    totals["processed"] += len(batch)
                    totals["pg"] += written["pg"]
                    totals["milvus"] += written["milvus"]
                    totals["es"] += written["es"]
                    totals["sink_errors"] += written.get("sink_errors", 0)
                    LOGGER.info("Batch done: size=%s timings=%s writes=%s", len(batch), _round_timings(timings), written)
                except RuntimeError as exc:
                    totals["failed"] += len(batch)
                    LOGGER.exception("Batch failed: %s", exc)
                    _clear_cuda_cache()
                    if not bool(run_config.get("continue_on_batch_error", True)):
                        raise
                except Exception as exc:
                    totals["failed"] += len(batch)
                    LOGGER.exception("Batch failed: %s", exc)
                    if not bool(run_config.get("continue_on_batch_error", True)):
                        raise
                finally:
                    for path in local_paths:
                        path.unlink(missing_ok=True)
    finally:
        if audit_handle:
            audit_handle.close()

    LOGGER.info("Done: %s", totals)
    print(json.dumps(totals, ensure_ascii=False, indent=2))


def _download_batch(source: GCSFrameSource, batch, tmp_root: Path, workers: int) -> list[Path]:
    local_paths = [tmp_root / item.video_id / item.image_name for item in batch]
    with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
        futures = [pool.submit(source.download_to, item, path) for item, path in zip(batch, local_paths)]
        for future in as_completed(futures):
            future.result()
    return local_paths


def _round_timings(timings: dict[str, float]) -> dict[str, float]:
    return {key: round(value, 3) for key, value in timings.items()}


def _clear_cuda_cache() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        return


if __name__ == "__main__":
    main()

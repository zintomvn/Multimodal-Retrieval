"""Batch-oriented ingestion from Kaggle-mounted datasets to Google Cloud Storage.

This script is intentionally usable in two places:

1. Kaggle Notebook, where datasets are already mounted under /kaggle/input.
2. Local/VM/Cloud Composer worker, where --input-root points to a local folder.

Examples:

  python scripts/upload_kaggle_to_gcs.py --list-sources

  python scripts/upload_kaggle_to_gcs.py \
    --source-id l21_l30_ai_challenge_2025 \
    --batches L21,L22 \
    --dry-run

  python scripts/upload_kaggle_to_gcs.py \
    --source-id k01_k10_data_video_batch_2_1 \
    --batches K01 \
    --workers 4

Required for real uploads:
  - GCS_BUCKET from CLI, environment, or Kaggle Secrets.
  - One auth method: GCS_CREDENTIALS_JSON, GCS_CREDENTIALS_FILE, or ADC.
"""
from __future__ import annotations

import argparse
import copy
import csv
import fnmatch
import json
import logging
import mimetypes
import os
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv"}
ARCHIVE_EXTENSIONS = {".zip"}
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "data_ingestion_sources.yaml"
DEFAULT_RUN_DIR = Path("ingestion_runs")


@dataclass(frozen=True)
class RunPaths:
    run_dir: Path
    manifest: Path
    summary: Path
    errors: Path
    metrics: Path
    log: Path


@dataclass(frozen=True)
class UploadResult:
    status: str
    bytes_uploaded: int
    duration_ms: int
    error: str = ""
    generation: str = ""


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_{uuid.uuid4().hex[:8]}"


def normalize_prefix(prefix: str) -> str:
    return prefix.strip().strip("/")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("Install PyYAML first: pip install pyyaml") from exc
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid config root in {path}")
    if not isinstance(data.get("datasets"), list):
        raise ValueError(f"Config must define a datasets list: {path}")
    return data


def merged_sources(config: dict[str, Any]) -> list[dict[str, Any]]:
    defaults = config.get("defaults") or {}
    sources = []
    for source in config.get("datasets") or []:
        merged = deep_merge(defaults, source)
        merged["gcs"] = deep_merge(defaults.get("gcs", {}), source.get("gcs", {}))
        sources.append(merged)
    return sources


def select_source(config: dict[str, Any], source_id: str) -> dict[str, Any]:
    matches = [src for src in merged_sources(config) if src.get("source_id") == source_id]
    if not matches:
        known = ", ".join(src.get("source_id", "<missing>") for src in merged_sources(config))
        raise ValueError(f"Unknown source_id '{source_id}'. Known sources: {known}")
    source = matches[0]
    if not source.get("enabled", True):
        raise ValueError(f"Source is disabled: {source_id}")
    return source


def parse_batches(raw: str, expected_batches: list[str]) -> set[str]:
    expected = {batch.upper() for batch in expected_batches}
    if raw.strip().lower() in {"", "all", "*"}:
        return expected
    selected = {part.strip().upper() for part in raw.split(",") if part.strip()}
    unknown = selected - expected
    if unknown:
        raise ValueError(f"Unknown batch(es): {', '.join(sorted(unknown))}. Expected: {', '.join(sorted(expected))}")
    return selected


def matches_any(rel_path: str, patterns: list[str]) -> bool:
    rel_lower = rel_path.lower()
    return any(fnmatch.fnmatchcase(rel_lower, pattern.lower()) for pattern in patterns)


def detect_batch(rel_path: str, source: dict[str, Any]) -> str | None:
    detection = source.get("batch_detection") or {}
    if detection.get("strategy") != "regex":
        raise ValueError(f"Unsupported batch detection strategy: {detection.get('strategy')}")
    pattern = detection.get("pattern")
    if not pattern:
        raise ValueError("Missing batch_detection.pattern")
    match = re.search(pattern, rel_path)
    if not match:
        return None
    return match.group(1).upper()


def iter_source_files(root: Path, include_patterns: list[str], exclude_patterns: list[str]) -> list[Path]:
    if not root.exists():
        raise FileNotFoundError(f"Input root does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Input root must be a directory: {root}")

    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if include_patterns and not matches_any(rel, include_patterns):
            continue
        if exclude_patterns and matches_any(rel, exclude_patterns):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.as_posix())


def source_dataset_id(source: dict[str, Any]) -> str:
    dataset_id = str(source.get("dataset_id") or source.get("target_dataset_id") or "").strip()
    if not dataset_id:
        raise ValueError(f"Missing dataset_id for source_id={source.get('source_id', '<missing>')}")
    return dataset_id


def required_config_str(source: dict[str, Any], dotted_key: str) -> str:
    value: Any = source
    for key in dotted_key.split("."):
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"Missing {dotted_key} for source_id={source.get('source_id', '<missing>')}")
        value = value[key]
    result = str(value).strip()
    if not result:
        raise ValueError(f"Missing {dotted_key} for source_id={source.get('source_id', '<missing>')}")
    return result


def build_gcs_key(
    raw_prefix: str,
    dataset_id: str,
    source_version: str,
    batch_id: str,
    rel_path: str,
) -> str:
    parts = [
        normalize_prefix(raw_prefix),
        f"dataset={dataset_id}",
        f"source_version={source_version}",
        f"batch={batch_id}",
        "original",
        rel_path.strip("/"),
    ]
    return "/".join(part for part in parts if part)


def make_run_paths(base_dir: Path, run_id: str) -> RunPaths:
    run_dir = base_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return RunPaths(
        run_dir=run_dir,
        manifest=run_dir / "manifest.jsonl",
        summary=run_dir / "summary.json",
        errors=run_dir / "errors.jsonl",
        metrics=run_dir / "metrics.csv",
        log=run_dir / "ingest.log",
    )


def setup_logging(log_path: Path, verbose: bool) -> logging.Logger:
    logger = logging.getLogger("kaggle_ingest")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.addHandler(console_handler)
    return logger


def read_kaggle_secret(name: str) -> str:
    try:
        from kaggle_secrets import UserSecretsClient

        return UserSecretsClient().get_secret(name) or ""
    except Exception:
        return ""


def unquote_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"Env file not found: {path}")

    loaded: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                raise ValueError(f"Invalid env line {line_number} in {path}: missing '='")
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                raise ValueError(f"Invalid env line {line_number} in {path}: empty key")
            loaded[key] = unquote_env_value(value)

    for key, value in loaded.items():
        os.environ.setdefault(key, value)
    return loaded


def resolve_relative_path(value: str, base_dir: Path | None) -> str:
    if not value:
        return ""
    path = Path(value)
    if path.is_absolute():
        return str(path)
    if base_dir:
        candidate = base_dir / path
        if candidate.exists():
            return str(candidate)
    return str(path)


def resolve_bucket_name(args: argparse.Namespace, dry_run: bool) -> str:
    bucket = args.gcs_bucket or os.environ.get("GCS_BUCKET", "") or read_kaggle_secret("GCS_BUCKET")
    if not bucket and not dry_run:
        raise RuntimeError("Set --gcs-bucket, GCS_BUCKET env var, or Kaggle Secret GCS_BUCKET.")
    return bucket


def resolve_credentials(args: argparse.Namespace) -> tuple[str, str]:
    creds_file = args.gcs_credentials_file or os.environ.get("GCS_CREDENTIALS_FILE", "")
    creds_file = resolve_relative_path(creds_file, getattr(args, "env_file_dir", None))
    creds_json = os.environ.get("GCS_CREDENTIALS_JSON", "") or read_kaggle_secret("GCS_CREDENTIALS_JSON")
    return creds_file, creds_json


def make_gcs_bucket(bucket_name: str, credentials_file: str, credentials_json: str):
    from google.cloud import storage

    if credentials_json:
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_info(json.loads(credentials_json))
        client = storage.Client(project=credentials.project_id, credentials=credentials)
    elif credentials_file:
        client = storage.Client.from_service_account_json(credentials_file)
    else:
        client = storage.Client()
    return client.bucket(bucket_name)


def upload_one(bucket: Any, record: dict[str, Any], skip_existing: bool, overwrite: bool) -> UploadResult:
    start = time.perf_counter()
    blob = bucket.blob(record["gcs_key"])
    blob.chunk_size = 8 * 1024 * 1024

    try:
        if skip_existing and blob.exists():
            elapsed = int((time.perf_counter() - start) * 1000)
            return UploadResult(status="skipped", bytes_uploaded=0, duration_ms=elapsed, generation=str(blob.generation or ""))

        content_type = mimetypes.guess_type(record["local_path"])[0] or "application/octet-stream"
        kwargs: dict[str, Any] = {"content_type": content_type, "timeout": 600}
        if not overwrite:
            kwargs["if_generation_match"] = 0
        blob.upload_from_filename(record["local_path"], **kwargs)
        elapsed = int((time.perf_counter() - start) * 1000)
        return UploadResult(
            status="uploaded",
            bytes_uploaded=int(record["size_bytes"]),
            duration_ms=elapsed,
            generation=str(blob.generation or ""),
        )
    except Exception as exc:  # noqa: BLE001 - write every object-level failure to errors.jsonl.
        elapsed = int((time.perf_counter() - start) * 1000)
        return UploadResult(status="failed", bytes_uploaded=0, duration_ms=elapsed, error=str(exc))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def discover_manifest(
    source: dict[str, Any],
    input_root: Path,
    selected_batches: set[str],
    bucket_name: str,
    raw_prefix: str,
    source_version: str,
    max_files: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    include_patterns = list(source.get("include_patterns") or [])
    exclude_patterns = list(source.get("exclude_patterns") or [])
    expected_batches = {batch.upper() for batch in source.get("expected_batches") or []}
    dataset_id = source_dataset_id(source)

    planned: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    filtered_by_batch = 0

    for path in iter_source_files(input_root, include_patterns, exclude_patterns):
        rel = path.relative_to(input_root).as_posix()
        batch_id = detect_batch(rel, source)
        size_bytes = path.stat().st_size
        if not batch_id or batch_id not in expected_batches:
            unmapped.append(
                {
                    "relative_path": rel,
                    "local_path": str(path),
                    "size_bytes": size_bytes,
                    "reason": "batch_not_detected_or_unexpected",
                }
            )
            continue
        if batch_id not in selected_batches:
            filtered_by_batch += 1
            continue

        gcs_key = build_gcs_key(raw_prefix, dataset_id, source_version, batch_id, rel)
        planned.append(
            {
                "manifest_schema_version": 1,
                "source_id": source["source_id"],
                "source_type": source.get("source_type", "kaggle"),
                "dataset_ref": source.get("dataset_ref", ""),
                "dataset_id": dataset_id,
                "batch_id": batch_id,
                "source_version": source_version,
                "relative_path": rel,
                "local_path": str(path),
                "filename": path.name,
                "extension": path.suffix.lower(),
                "size_bytes": size_bytes,
                "gcs_bucket": bucket_name,
                "gcs_key": gcs_key,
                "gcs_uri": f"gs://{bucket_name}/{gcs_key}" if bucket_name else "",
                "planned_at": utc_now_iso(),
            }
        )

    if max_files is not None:
        planned = planned[:max_files]

    return planned, unmapped, filtered_by_batch


def write_initial_metrics(metrics_path: Path) -> csv.DictWriter:
    handle = metrics_path.open("w", encoding="utf-8", newline="")
    fieldnames = [
        "ts",
        "run_id",
        "source_id",
        "batch_id",
        "relative_path",
        "gcs_uri",
        "status",
        "size_bytes",
        "bytes_uploaded",
        "duration_ms",
        "generation",
        "error",
    ]
    writer = csv.DictWriter(handle, fieldnames=fieldnames)
    writer.writeheader()
    writer._handle = handle  # type: ignore[attr-defined]
    return writer


def close_metrics_writer(writer: csv.DictWriter) -> None:
    handle = getattr(writer, "_handle", None)
    if handle:
        handle.close()


def upload_run_artifacts(bucket: Any, source: dict[str, Any], paths: RunPaths, logger: logging.Logger) -> None:
    control_prefix = normalize_prefix(required_config_str(source, "gcs.control_prefix"))
    logs_prefix = normalize_prefix(required_config_str(source, "gcs.logs_prefix"))
    mappings = [
        (paths.manifest, f"{control_prefix}/run_id={paths.run_dir.name}/manifest.jsonl"),
        (paths.errors, f"{control_prefix}/run_id={paths.run_dir.name}/errors.jsonl"),
        (paths.metrics, f"{logs_prefix}/run_id={paths.run_dir.name}/metrics.csv"),
        (paths.log, f"{logs_prefix}/run_id={paths.run_dir.name}/ingest.log"),
        (paths.summary, f"{control_prefix}/run_id={paths.run_dir.name}/summary.json"),
    ]
    for local_path, key in mappings:
        blob = bucket.blob(key)
        content_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        blob.upload_from_filename(str(local_path), content_type=content_type)
        logger.info("uploaded run artifact gs://%s/%s", bucket.name, key)


def summarize(
    run_id: str,
    args: argparse.Namespace,
    source: dict[str, Any],
    planned: list[dict[str, Any]],
    unmapped: list[dict[str, Any]],
    filtered_by_batch: int,
    uploaded: int,
    skipped: int,
    failed: int,
    bytes_uploaded: int,
    started_at: float,
    artifact_upload_status: str,
) -> dict[str, Any]:
    per_batch: dict[str, dict[str, int]] = {}
    for row in planned:
        entry = per_batch.setdefault(row["batch_id"], {"planned": 0, "size_bytes": 0})
        entry["planned"] += 1
        entry["size_bytes"] += int(row["size_bytes"])

    elapsed_s = round(time.perf_counter() - started_at, 3)
    return {
        "run_id": run_id,
        "started_at": args.started_at,
        "finished_at": utc_now_iso(),
        "elapsed_seconds": elapsed_s,
        "dry_run": args.dry_run,
        "source_id": source["source_id"],
        "source_type": source.get("source_type", "kaggle"),
        "dataset_ref": source.get("dataset_ref", ""),
        "dataset_id": source_dataset_id(source),
        "input_root": str(args.resolved_input_root),
        "selected_batches": sorted(args.selected_batches),
        "workers": args.workers,
        "skip_existing": args.skip_existing,
        "overwrite": args.overwrite,
        "planned_files": len(planned),
        "unmapped_files": len(unmapped),
        "filtered_by_batch_files": filtered_by_batch,
        "uploaded_files": uploaded,
        "skipped_files": skipped,
        "failed_files": failed,
        "bytes_planned": sum(int(row["size_bytes"]) for row in planned),
        "bytes_uploaded": bytes_uploaded,
        "per_batch": per_batch,
        "artifact_upload_status": artifact_upload_status,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Batch ingest Kaggle-mounted media files into GCS.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to data_ingestion_sources.yaml.")
    parser.add_argument("--list-sources", action="store_true", help="Print configured sources and exit.")
    parser.add_argument("--source-id", default="", help="Source id from config.")
    parser.add_argument("--batches", default="all", help="Comma-separated batch list, e.g. L21,L22 or K01. Default: all.")
    parser.add_argument("--input-root", type=Path, default=None, help="Override mounted dataset root.")
    parser.add_argument("--gcs-bucket", default="", help="Target GCS bucket. Fallback: GCS_BUCKET/Kaggle Secret.")
    parser.add_argument("--gcs-prefix", default="", help="Override raw GCS prefix. Default from config.")
    parser.add_argument("--gcs-credentials-file", default="", help="Service account JSON file. Fallback: env GCS_CREDENTIALS_FILE.")
    parser.add_argument("--env-file", type=Path, default=None, help="Optional env file with GCS_BUCKET/GCS_CREDENTIALS_FILE.")
    parser.add_argument("--source-version", default="", help="Override source_version path segment.")
    parser.add_argument("--workers", type=int, default=4, help="Parallel upload workers.")
    parser.add_argument("--run-id", default="", help="Optional deterministic run id.")
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR, help="Local output directory for run artifacts.")
    parser.add_argument("--max-files", type=int, default=None, help="Limit planned files for smoke tests.")
    parser.add_argument("--dry-run", action="store_true", help="Create manifest/summary without uploading.")
    parser.add_argument("--skip-existing", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting existing objects. Off by default.")
    parser.add_argument("--upload-run-artifacts", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fail-on-unmapped", action="store_true", help="Fail if included files cannot be mapped to a batch.")
    parser.add_argument("--no-progress", action="store_true", help="Disable tqdm progress bar.")
    parser.add_argument("--verbose", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    args.started_at = utc_now_iso()
    started_at = time.perf_counter()
    args.env_file_dir = None
    if args.env_file:
        env_file = args.env_file.resolve()
        load_env_file(env_file)
        args.env_file_dir = env_file.parent

    config = load_config(args.config)

    if args.list_sources:
        for source in merged_sources(config):
            enabled = "enabled" if source.get("enabled", True) else "disabled"
            batches = ",".join(source.get("expected_batches") or [])
            print(f"{source.get('source_id')} [{enabled}] batches={batches} mount={source.get('kaggle_mount_path', '')}")
        return 0

    if not args.source_id:
        raise ValueError("--source-id is required unless --list-sources is used.")
    if args.workers < 1:
        raise ValueError("--workers must be >= 1")
    if args.max_files is not None and args.max_files < 1:
        raise ValueError("--max-files must be >= 1")
    if args.overwrite and args.skip_existing:
        raise ValueError("--overwrite conflicts with --skip-existing. Use --no-skip-existing --overwrite.")

    source = select_source(config, args.source_id)
    args.selected_batches = parse_batches(args.batches, list(source.get("expected_batches") or []))
    input_root = args.input_root or Path(required_config_str(source, "kaggle_mount_path"))
    args.resolved_input_root = input_root
    source_version = args.source_version or required_config_str(source, "source_version")
    raw_prefix = args.gcs_prefix or required_config_str(source, "gcs.raw_prefix")
    bucket_name = resolve_bucket_name(args, args.dry_run)

    run_id = args.run_id or new_run_id()
    paths = make_run_paths(args.run_dir, run_id)
    logger = setup_logging(paths.log, args.verbose)

    logger.info("run_id=%s source_id=%s input_root=%s dry_run=%s", run_id, source["source_id"], input_root, args.dry_run)
    logger.info("selected batches: %s", ",".join(sorted(args.selected_batches)))

    planned, unmapped, filtered_by_batch = discover_manifest(
        source=source,
        input_root=input_root,
        selected_batches=args.selected_batches,
        bucket_name=bucket_name,
        raw_prefix=raw_prefix,
        source_version=source_version,
        max_files=args.max_files,
    )

    for row in planned:
        row["run_id"] = run_id
    write_jsonl(paths.manifest, planned)
    write_jsonl(paths.errors, [])

    logger.info("planned=%d unmapped=%d filtered_by_batch=%d", len(planned), len(unmapped), filtered_by_batch)
    if unmapped:
        unmapped_path = paths.run_dir / "unmapped.jsonl"
        write_jsonl(unmapped_path, unmapped)
        logger.warning("wrote unmapped report: %s", unmapped_path)
        if args.fail_on_unmapped:
            raise RuntimeError(f"{len(unmapped)} included file(s) could not be mapped to an expected batch.")
    if not planned:
        raise RuntimeError("No files planned. Check --input-root, include patterns, and --batches.")

    uploaded = skipped = failed = bytes_uploaded = 0
    artifact_upload_status = "not_uploaded"
    writer = write_initial_metrics(paths.metrics)
    errors_handle = paths.errors.open("a", encoding="utf-8")

    try:
        if args.dry_run:
            for row in planned:
                writer.writerow(
                    {
                        "ts": utc_now_iso(),
                        "run_id": run_id,
                        "source_id": source["source_id"],
                        "batch_id": row["batch_id"],
                        "relative_path": row["relative_path"],
                        "gcs_uri": row["gcs_uri"],
                        "status": "planned",
                        "size_bytes": row["size_bytes"],
                        "bytes_uploaded": 0,
                        "duration_ms": 0,
                        "generation": "",
                        "error": "",
                    }
                )
            artifact_upload_status = "dry_run"
        else:
            credentials_file, credentials_json = resolve_credentials(args)
            bucket = make_gcs_bucket(bucket_name, credentials_file, credentials_json)
            progress = None
            if not args.no_progress:
                try:
                    from tqdm import tqdm

                    progress = tqdm(total=len(planned), unit="file")
                except ImportError:
                    progress = None

            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {
                    pool.submit(upload_one, bucket, row, args.skip_existing, args.overwrite): row
                    for row in planned
                }
                for future in as_completed(futures):
                    row = futures[future]
                    result = future.result()
                    if result.status == "uploaded":
                        uploaded += 1
                        bytes_uploaded += result.bytes_uploaded
                    elif result.status == "skipped":
                        skipped += 1
                    else:
                        failed += 1
                        error_row = {
                            **row,
                            "status": result.status,
                            "error": result.error,
                            "failed_at": utc_now_iso(),
                        }
                        errors_handle.write(json.dumps(error_row, ensure_ascii=False, sort_keys=True) + "\n")

                    writer.writerow(
                        {
                            "ts": utc_now_iso(),
                            "run_id": run_id,
                            "source_id": source["source_id"],
                            "batch_id": row["batch_id"],
                            "relative_path": row["relative_path"],
                            "gcs_uri": row["gcs_uri"],
                            "status": result.status,
                            "size_bytes": row["size_bytes"],
                            "bytes_uploaded": result.bytes_uploaded,
                            "duration_ms": result.duration_ms,
                            "generation": result.generation,
                            "error": result.error,
                        }
                    )
                    if progress:
                        progress.set_postfix(uploaded=uploaded, skipped=skipped, failed=failed, refresh=False)
                        progress.update(1)

            if progress:
                progress.close()

            if args.upload_run_artifacts:
                close_metrics_writer(writer)
                errors_handle.close()
                logging.shutdown()
                logger = setup_logging(paths.log, args.verbose)
                summary = summarize(
                    run_id,
                    args,
                    source,
                    planned,
                    unmapped,
                    filtered_by_batch,
                    uploaded,
                    skipped,
                    failed,
                    bytes_uploaded,
                    started_at,
                    "uploaded",
                )
                paths.summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
                try:
                    upload_run_artifacts(bucket, source, paths, logger)
                    artifact_upload_status = "uploaded"
                except Exception as exc:  # noqa: BLE001 - main uploads already completed; preserve local reports.
                    artifact_upload_status = f"failed: {exc}"
                    logger.error("run artifact upload failed: %s", exc)
                writer = None  # type: ignore[assignment]
                errors_handle = None  # type: ignore[assignment]
    finally:
        if writer is not None:
            close_metrics_writer(writer)
        if errors_handle is not None:
            errors_handle.close()

    summary = summarize(
        run_id,
        args,
        source,
        planned,
        unmapped,
        filtered_by_batch,
        uploaded,
        skipped,
        failed,
        bytes_uploaded,
        started_at,
        artifact_upload_status,
    )
    paths.summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    logger.info(
        "finished planned=%d uploaded=%d skipped=%d failed=%d bytes_uploaded=%d run_dir=%s",
        len(planned),
        uploaded,
        skipped,
        failed,
        bytes_uploaded,
        paths.run_dir,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Exception as exc:  # noqa: BLE001 - CLI should print actionable errors.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


_LEGACY_IMPLEMENTATION = r'''

Run this inside a Kaggle Notebook (dataset already mounted, no local download needed):

  Cell 1:
    !pip install google-cloud-storage tqdm -q

  Cell 2:
    !python upload_kaggle_to_gcs.py
    # or paste the script body directly into the cell

Kaggle Secrets required:
  GCS_CREDENTIALS_JSON  — full JSON content of GCS service account key
  GCS_BUCKET            — GCS bucket name

Settings (edit the CONFIG block below):
  GCS_PREFIX   — key prefix in the bucket, e.g. "aic2025/videos"
  VIDEOS_DIR   — path to the videos root inside the Kaggle input mount
  VIDEO_EXTS   — file extensions to upload
  SKIP_EXISTING — if True, skip files already present in GCS (safe to re-run)
  WORKERS      — parallel upload threads (4 is safe; raise to 8 on fast connections)
"""
from __future__ import annotations

import mimetypes
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────────────────────
GCS_PREFIX    = "aic2025/videos"
VIDEOS_DIR    = Path("/kaggle/input/ai-challenge-2025")   # adjust subfolder if needed
VIDEO_EXTS    = {".mp4", ".avi", ".mov", ".mkv"}
SKIP_EXISTING = True
WORKERS       = 4
# ─────────────────────────────────────────────────────────────────────────────


def _load_secrets() -> tuple[str, str]:
    """Load GCS credentials from Kaggle Secrets."""
    try:
        from kaggle_secrets import UserSecretsClient
        s = UserSecretsClient()
        return s.get_secret("GCS_BUCKET"), s.get_secret("GCS_CREDENTIALS_JSON")
    except Exception:
        # Fallback: read from environment (useful when running on a plain VM)
        bucket = os.environ.get("GCS_BUCKET", "")
        creds  = os.environ.get("GCS_CREDENTIALS_JSON", "")
        if not bucket or not creds:
            raise RuntimeError(
                "Set GCS_BUCKET and GCS_CREDENTIALS_JSON as Kaggle Secrets "
                "or environment variables."
            )
        return bucket, creds


def _make_bucket(bucket_name: str, creds_json: str):
    from google.cloud import storage

    creds_path = Path("/tmp/gcs_creds.json")
    creds_path.write_text(creds_json)
    client = storage.Client.from_service_account_json(str(creds_path))
    return client.bucket(bucket_name)


def _upload_one(bucket, local_path: Path, gcs_key: str) -> str:
    blob = bucket.blob(gcs_key)
    if SKIP_EXISTING and blob.exists():
        return "skip"
    ct = mimetypes.guess_type(local_path.name)[0] or "video/mp4"
    blob.upload_from_filename(str(local_path), content_type=ct)   # streaming — no OOM
    return "ok"


def main() -> None:
    print("Loading GCS credentials...")
    bucket_name, creds_json = _load_secrets()
    bucket = _make_bucket(bucket_name, creds_json)
    print(f"  bucket: gs://{bucket_name}/{GCS_PREFIX}/")

    print(f"\nScanning {VIDEOS_DIR} ...")
    video_files = sorted(
        p for p in VIDEOS_DIR.rglob("*")
        if p.suffix.lower() in VIDEO_EXTS
    )
    print(f"  found {len(video_files)} video files")
    if not video_files:
        print("Nothing to upload. Check VIDEOS_DIR and VIDEO_EXTS.")
        return

    ok = skip = err = 0
    errors: list[tuple[str, str]] = []

    try:
        from tqdm import tqdm
        progress = tqdm(total=len(video_files), unit="file")
    except ImportError:
        progress = None

    def _done(n: int, status: str) -> None:
        if progress:
            progress.set_postfix(ok=ok, skip=skip, err=err, refresh=False)
            progress.update(1)
        else:
            print(f"  [{n}/{len(video_files)}] {status}")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {}
        for p in video_files:
            rel     = p.relative_to(VIDEOS_DIR).as_posix()
            gcs_key = f"{GCS_PREFIX}/{rel}"
            futures[pool.submit(_upload_one, bucket, p, gcs_key)] = (p, gcs_key)

        for i, fut in enumerate(as_completed(futures), 1):
            local_p, gcs_key = futures[fut]
            try:
                status = fut.result()
                if status == "skip":
                    skip += 1
                else:
                    ok += 1
                _done(i, f"{status}: {gcs_key}")
            except Exception as exc:
                err += 1
                errors.append((str(local_p), str(exc)))
                _done(i, f"ERROR: {local_p.name}")

    if progress:
        progress.close()

    print(f"\n{'='*55}")
    print(f"  Uploaded : {ok}")
    print(f"  Skipped  : {skip}  (already on GCS)")
    print(f"  Errors   : {err}")
    print(f"{'='*55}")
    for path, exc in errors:
        print(f"  ERROR  {path}\n         {exc}")

    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
'''

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifact_io import ArtifactStore, join_uri
from .checkpoint_store import CheckpointStore
from .manifest import split_batches


DEFAULT_STAGES = ["visual_primary", "visual_secondary", "objects", "ocr", "caption", "asr"]


@dataclass(frozen=True)
class RunReconcileOptions:
    """Options for checking a feature ingest run before database import."""

    run_id: str
    manifest_summary_uri: str
    feature_output_prefix: str
    checkpoint_root: str
    stages: str = ",".join(DEFAULT_STAGES)
    gcs_credentials_file: str = ""
    gcs_timeout_seconds: float = 60.0
    strict: bool = False


def reconcile_feature_run(options: RunReconcileOptions) -> dict[str, Any]:
    """Compare manifest, checkpoint markers, and artifact part files."""
    store = ArtifactStore(credentials_file=options.gcs_credentials_file, timeout_seconds=options.gcs_timeout_seconds)
    manifest = store.read_json(options.manifest_summary_uri, default={}) or {}
    shards = [str(item) for item in manifest.get("shards") or []]
    frames = int(manifest.get("frames") or 0)
    stages = split_batches(options.stages.lower().replace("visual_primary", "VISUAL_PRIMARY").replace("visual_secondary", "VISUAL_SECONDARY"))
    if not stages:
        stages = DEFAULT_STAGES
    stages = [stage.lower() for stage in stages]

    checkpoint_store = CheckpointStore(
        root_uri=options.checkpoint_root,
        run_id=options.run_id,
        credentials_file=options.gcs_credentials_file,
        timeout_seconds=options.gcs_timeout_seconds,
    )
    stage_reports = [
        _stage_report(store, checkpoint_store, options, stage, shards, frames)
        for stage in stages
    ]
    ok = all(item["ok"] for item in stage_reports if item["stage"] != "asr")
    if options.strict:
        ok = ok and all(item["artifact_rows"] > 0 for item in stage_reports)
    return {
        "run_id": options.run_id,
        "manifest_summary_uri": options.manifest_summary_uri,
        "manifest_frames": frames,
        "manifest_shards": len(shards),
        "stages": stage_reports,
        "ok": ok,
    }


def _stage_report(
    store: ArtifactStore,
    checkpoint_store: CheckpointStore,
    options: RunReconcileOptions,
    stage: str,
    shards: list[str],
    frames: int,
) -> dict[str, Any]:
    artifact_prefix = join_uri(options.feature_output_prefix, f"run_id={options.run_id}", f"stage={stage}")
    files = store.list_jsonl(artifact_prefix)
    artifact_rows, asr_segments = _count_artifact_rows(store, files)
    shard_ids = [_shard_id_from_uri(uri) for uri in shards]
    completed_shards = 0
    if stage != "asr":
        completed_shards = sum(1 for shard_id in shard_ids if checkpoint_store.is_complete(stage, shard_id))
    ok = True
    if stage != "asr":
        ok = bool(shard_ids) and completed_shards == len(shard_ids) and artifact_rows >= frames
    else:
        ok = artifact_rows > 0 or asr_segments > 0
    return {
        "stage": stage,
        "artifact_prefix": artifact_prefix,
        "artifact_files": len(files),
        "artifact_rows": artifact_rows,
        "asr_segments": asr_segments,
        "expected_frames": frames if stage != "asr" else None,
        "completed_shards": completed_shards if stage != "asr" else None,
        "expected_shards": len(shard_ids) if stage != "asr" else None,
        "ok": ok,
    }


def _count_artifact_rows(store: ArtifactStore, files: list[str]) -> tuple[int, int]:
    rows = 0
    asr_segments = 0
    for uri in files:
        for item in store.read_jsonl(uri):
            rows += 1
            if item.get("schema_version") == "aic.asr_artifact.v1":
                asr_segments += len(item.get("segments") or [])
    return rows, asr_segments


def _shard_id_from_uri(uri: str) -> str:
    name = Path(uri.rstrip("/")).name
    if name.endswith(".jsonl"):
        name = name[: -len(".jsonl")]
    return name.replace("\\", "/").split("/")[-1]

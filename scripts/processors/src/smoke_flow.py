from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifact_io import ArtifactStore, join_uri
from .checkpoint_store import CheckpointStore
from .cloud_sinks.config import SinkConfig
from .config import ProcessorSettings
from .config_paths import DEFAULT_PROCESSOR_CONFIG
from .ingest_artifacts import ArtifactImportOptions, import_feature_artifacts
from .manifest import frame_item_from_record, load_manifest_rows, split_batches, write_manifest_and_shards
from .notebook_role_runner import NotebookRoleRunOptions, render_or_run_notebook_role
from .processor_doctor import DoctorOptions, run_processor_doctor
from .reconcile_run import RunReconcileOptions, reconcile_feature_run
from .role_planner import DEFAULT_CHECKPOINT_POLICY, DEFAULT_PIPELINE_PROFILES, RolePlanOptions, build_notebook_run_plan


FRAME_STAGES = ["visual_primary", "visual_secondary", "objects", "ocr", "caption"]


@dataclass(frozen=True)
class LocalSmokeFlowOptions:
    """Options for a fully local ingest smoke flow."""

    run_id: str = "smoke_local"
    workspace_root: str = ""
    batches: str = "L21"
    frame_count: int = 2
    frames_per_shard: int = 1
    pipeline_profiles_path: str = str(DEFAULT_PIPELINE_PROFILES)
    checkpoint_policy_path: str = str(DEFAULT_CHECKPOINT_POLICY)
    gcs_credentials_file: str = ""
    gcs_timeout_seconds: float = 60.0


def run_local_smoke_flow(options: LocalSmokeFlowOptions) -> dict[str, Any]:
    """Run a deterministic local smoke through planning, checkpoints, artifacts, reconcile, and import dry-run."""
    workspace = _workspace_root(options)
    manifest_root = workspace / "m"
    feature_root = workspace / "f"
    checkpoint_root = workspace / "c"
    manifest_root.mkdir(parents=True, exist_ok=True)
    feature_root.mkdir(parents=True, exist_ok=True)
    checkpoint_root.mkdir(parents=True, exist_ok=True)

    batches = split_batches(options.batches) or ["L21"]
    manifest_rows = _build_manifest_rows(batches, options.frame_count)
    manifest = write_manifest_and_shards(
        manifest_rows,
        output_root=str(manifest_root),
        run_id=options.run_id,
        frames_per_shard=options.frames_per_shard,
    )
    shards = manifest["shards"]
    shard_rows = {shard_uri: load_manifest_rows(shard_uri) for shard_uri in shards}
    rows_by_batch = _group_rows_by_batch(manifest_rows)

    doctors = _doctor_reports(options)
    plan = build_notebook_run_plan(
        RolePlanOptions(
            run_id=options.run_id,
            pipeline_profiles_path=options.pipeline_profiles_path,
            checkpoint_policy_path=options.checkpoint_policy_path,
            batches=",".join(batches),
            manifest_summary_uri=manifest["summary_uri"],
            shard_root=manifest["shard_root"],
            num_shards=len(shards),
            max_shards_per_role=1,
            gcs_credentials_file=options.gcs_credentials_file,
            gcs_timeout_seconds=options.gcs_timeout_seconds,
        )
    )
    primary_role_id = next(role["role_id"] for role in plan["machine_roles"] if role["stage"] == "visual_primary")
    role_render = render_or_run_notebook_role(
        NotebookRoleRunOptions(
            role_id=primary_role_id,
            run_id=options.run_id,
            batches=",".join(batches),
            manifest_summary_uri=manifest["summary_uri"],
            shard_root=manifest["shard_root"],
            num_shards=len(shards),
            shard_index=0,
        )
    )

    checkpoint = CheckpointStore(
        root_uri=str(checkpoint_root),
        run_id=options.run_id,
        lease_ttl_seconds=60,
        credentials_file=options.gcs_credentials_file,
        timeout_seconds=options.gcs_timeout_seconds,
    )
    for stage in FRAME_STAGES:
        for shard_uri, rows in shard_rows.items():
            shard_id = Path(shard_uri).stem
            worker_id = f"smoke-{stage}"
            claimed = checkpoint.try_claim(stage, shard_id, worker_id)
            if not claimed.claimed:
                raise RuntimeError(f"Failed to claim smoke checkpoint for stage={stage} shard_id={shard_id}")
            checkpoint.mark_complete(stage, shard_id, {"worker_id": worker_id, "processed": len(rows), "failed": 0})

    _write_frame_artifacts(feature_root, options.run_id, shard_rows)
    _write_asr_artifacts(feature_root, options.run_id, rows_by_batch)

    reconcile = reconcile_feature_run(
        RunReconcileOptions(
            run_id=options.run_id,
            manifest_summary_uri=manifest["summary_uri"],
            feature_output_prefix=str(feature_root),
            checkpoint_root=str(checkpoint_root),
            strict=True,
            gcs_credentials_file=options.gcs_credentials_file,
            gcs_timeout_seconds=options.gcs_timeout_seconds,
        )
    )

    settings = ProcessorSettings(
        database_url="postgresql+psycopg://smoke:smoke@localhost/db",
        gcs_bucket="smoke-bucket",
        gcs_credentials_file=options.gcs_credentials_file,
        gcs_public_url="https://storage.googleapis.com/smoke-bucket",
        milvus_uri="http://localhost:19530",
        milvus_token="",
        elasticsearch_url="http://localhost:9200",
    )
    import_summary = import_feature_artifacts(
        SinkConfig(
            database_url=settings.database_url,
            milvus_uri=settings.milvus_uri,
            milvus_token=settings.milvus_token,
            elasticsearch_url=settings.elasticsearch_url,
            dataset_code="ai_challenge_2025_mvp_l21",
            dataset_name="ai-challenge-2025-mvp-l21",
            dataset_version="mvp_v1",
            dataset_root_uri=join_uri(str(feature_root), "dataset=ai_challenge_2025"),
            gcs_public_url=settings.gcs_public_url,
            milvus_collection="keyframe_embeddings_pe_core_bigG_14_448",
            elasticsearch_index="keyframe_annotations",
            model_version="smoke_v1",
            write_pg=False,
            write_milvus=False,
            write_elasticsearch=False,
            dry_run=True,
        ),
        ArtifactImportOptions(
            artifact_uris=[str(feature_root)],
            batch_size=2,
            write_pg=False,
            write_milvus=False,
            write_elasticsearch=False,
            dry_run=True,
            gcs_credentials_file=options.gcs_credentials_file,
            write_text_embeddings=True,
        ),
    )

    ok = all(report["ok"] for report in doctors.values()) and role_render["ok"] and reconcile["ok"] and bool(import_summary["dry_run"])
    return {
        "ok": ok,
        "run_id": options.run_id,
        "workspace_root": str(workspace),
        "manifest": manifest,
        "doctors": doctors,
        "plan": plan,
        "role_render": role_render,
        "reconcile": reconcile,
        "import_dry_run": import_summary,
        "feature_root": str(feature_root),
        "checkpoint_root": str(checkpoint_root),
    }


def _workspace_root(options: LocalSmokeFlowOptions) -> Path:
    if options.workspace_root:
        return Path(options.workspace_root)
    return Path(tempfile.mkdtemp(prefix=f"processor_smoke_{options.run_id}_"))


def _build_manifest_rows(batches: list[str], frame_count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch in batches:
        rows.extend(_build_manifest_rows_for_batch(batch, frame_count))
    return rows


def _build_manifest_rows_for_batch(batch: str, frame_count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    video_id = f"{batch}_V001"
    for index in range(frame_count):
        frame_idx = index + 1
        rows.append(
            {
                "dataset_id": "ai_challenge_2025",
                "batch": batch,
                "video_id": video_id,
                "keyframe_id": f"{video_id}_F{frame_idx:06d}",
                "frame_idx": frame_idx,
                "frame_seconds": round((frame_idx - 1) * 1.5, 2),
                "frame_type": "middle",
                "gcs_uri": f"gs://smoke-bucket/processed/keyframes/batch={batch}/video_id={video_id}/frame_{frame_idx:06d}.jpg",
                "bucket": "smoke-bucket",
                "blob_name": f"processed/keyframes/batch={batch}/video_id={video_id}/frame_{frame_idx:06d}.jpg",
                "image_name": f"frame_{frame_idx:06d}.jpg",
            }
        )
    return rows


def _group_rows_by_batch(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        batch = str(row.get("batch") or "UNKNOWN")
        grouped.setdefault(batch, []).append(row)
    return grouped


def _doctor_reports(options: LocalSmokeFlowOptions) -> dict[str, Any]:
    base_settings = ProcessorSettings(
        database_url="postgresql+psycopg://smoke:smoke@localhost/db",
        gcs_bucket="smoke-bucket",
        gcs_credentials_file=options.gcs_credentials_file,
        gcs_public_url="https://storage.googleapis.com/smoke-bucket",
        milvus_uri="http://localhost:19530",
        milvus_token="",
        elasticsearch_url="http://localhost:9200",
    )
    return {
        "kaggle": run_processor_doctor(
            base_settings,
            DoctorOptions(runtime="kaggle", profile="smoke", config_path=str(DEFAULT_PROCESSOR_CONFIG)),
        ),
        "colab_caption": run_processor_doctor(
            base_settings,
            DoctorOptions(runtime="colab", profile="shot_context_caption_qwen_vl", features="caption", config_path=str(DEFAULT_PROCESSOR_CONFIG)),
        ),
        "importer": run_processor_doctor(
            base_settings,
            DoctorOptions(runtime="importer", profile="full_text_features", config_path=str(DEFAULT_PROCESSOR_CONFIG)),
        ),
    }


def _write_frame_artifacts(feature_root: Path, run_id: str, shard_rows: dict[str, list[dict[str, Any]]]) -> None:
    store = ArtifactStore()
    for stage_index, stage in enumerate(FRAME_STAGES):
        for shard_uri, rows in shard_rows.items():
            shard_id = Path(shard_uri).stem
            artifact_rows = [
                _feature_artifact_row(stage, run_id, shard_id, row, local_index, stage_index)
                for local_index, row in enumerate(rows)
            ]
            part_uri = join_uri(
                str(feature_root),
                f"run_id={run_id}",
                f"stage={stage}",
                f"sid={shard_id}",
                f"w=smoke_{stage}",
                "a=attempt-1",
                "p-00000.jsonl",
            )
            store.write_jsonl(part_uri, artifact_rows)


def _feature_artifact_row(stage: str, run_id: str, shard_id: str, source_row: dict[str, Any], local_index: int, stage_index: int) -> dict[str, Any]:
    frame = frame_item_from_record(source_row)
    annotation = {
        "caption": f"{stage} caption for {frame.keyframe_id}",
        "texts": [f"{stage}_text_{local_index + 1}"] if stage in {"ocr", "caption"} else [],
        "objects": ["person", "car"] if stage == "objects" else [],
        "object_counts": {"person": 1, "car": 1} if stage == "objects" else {},
        "detections": [{"label": "person", "confidence": 0.9}] if stage == "objects" else [],
    }
    embedding = [round(0.1 * (stage_index + 1), 4), round(0.2 * (local_index + 1), 4), round(0.3, 4)]
    if stage in {"objects", "ocr", "caption"}:
        embedding = None  # type: ignore[assignment]
    return {
        "schema_version": "aic.feature_artifact.v1",
        "run_id": run_id,
        "stage": stage,
        "shard_id": shard_id,
        "worker_id": f"smoke-{stage}",
        "attempt_id": "attempt-1",
        "part_index": 0,
        "record_index": local_index,
        "created_at": "2026-07-23T00:00:00Z",
        "frame": source_row,
        "annotation": annotation,
        "embedding": embedding,
        "timings_seconds": {"load": 0.0, "infer": 0.0},
        "status": "ok",
    }


def _write_asr_artifacts(feature_root: Path, run_id: str, rows_by_batch: dict[str, list[dict[str, Any]]]) -> None:
    store = ArtifactStore()
    for batch, rows in rows_by_batch.items():
        first_row = rows[0]
        first_video = str(first_row.get("video_id") or f"{batch}_V001")
        asr_uri = join_uri(
            str(feature_root),
            f"run_id={run_id}",
            "stage=asr",
            f"sid=raw-{batch}",
            "w=smoke_asr",
            "a=attempt-1",
            "p-00000.jsonl",
        )
        store.write_jsonl(
            asr_uri,
            [
                {
                    "schema_version": "aic.asr_artifact.v1",
                    "run_id": run_id,
                    "stage": "asr",
                    "video_id": first_video,
                    "source": {
                        "gcs_uri": f"gs://smoke-bucket/raw/source=kaggle/dataset=ai_challenge_2025/batch={batch}/video.mp4",
                        "video_id": first_video,
                    },
                    "segments": [
                        {
                            "segment_id": f"{first_video}_ASR_000001",
                            "start_seconds": 0.0,
                            "end_seconds": 1.5,
                            "text": "xin chao",
                            "language": "vi",
                            "confidence": 0.94,
                        },
                        {
                            "segment_id": f"{first_video}_ASR_000002",
                            "start_seconds": 1.5,
                            "end_seconds": 3.0,
                            "text": "video retrieval",
                            "language": "vi",
                            "confidence": 0.91,
                        },
                    ],
                    "model": {"name": "faster-whisper-small", "language": "vi"},
                }
            ],
        )

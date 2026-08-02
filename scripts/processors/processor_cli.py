from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from src.artifact_io import ArtifactStore
from src.cloud_sinks.config import SinkConfig
from src.config import get_processor_settings
from src.config_paths import DEFAULT_PROCESSOR_CONFIG, resolve_config_path
from src.ingest_artifacts import ArtifactImportOptions, import_feature_artifacts
from src.manifest import build_gcs_keyframe_manifest, gcs_prefixes_for_batches, split_batches, write_manifest_and_shards
from src.notebook_cells import NotebookCellsOptions, NotebookKitOptions, render_notebook_cells, render_notebook_kit
from src.notebook_role_runner import NotebookRoleRunOptions, render_or_run_notebook_role
from src.pipeline_config import apply_cli_overrides, load_pipeline_config
from src.processor_doctor import DoctorOptions, run_processor_doctor
from src.reconcile_run import RunReconcileOptions, reconcile_feature_run
from src.role_planner import DEFAULT_CHECKPOINT_POLICY, DEFAULT_PIPELINE_PROFILES, RolePlanOptions, build_notebook_run_plan
from src.shard_runner import ShardRunOptions, run_feature_shard
from src.smoke_flow import LocalSmokeFlowOptions, run_local_smoke_flow


DEFAULT_CONFIG = DEFAULT_PROCESSOR_CONFIG


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kaggle/Colab-first feature ingest CLI with manifest shards and checkpoint resume.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Processor YAML config path.")
    parser.add_argument("--profile", default="smoke", help="Processor config profile.")
    parser.add_argument("--log-level", default="INFO")
    sub = parser.add_subparsers(dest="command", required=True)

    discover = sub.add_parser("discover-gcs-keyframes", help="Create keyframe manifest and execution shards from GCS frames.")
    discover.add_argument("--bucket", default="")
    discover.add_argument("--dataset-id", default="ai_challenge_2025")
    discover.add_argument("--batches", default="L21")
    discover.add_argument("--keyframes-prefix", default="processed/keyframes")
    discover.add_argument("--frame-profile", default="autoshot_v1")
    discover.add_argument("--shot-segments-path", default="", help="Optional shot_segments.csv to enrich frame timing metadata.")
    discover.add_argument("--run-id", required=True)
    discover.add_argument("--output-root", default="")
    discover.add_argument("--frames-per-shard", type=int, default=512)
    discover.add_argument("--max-frames", type=int, default=0)
    discover.add_argument("--gcs-timeout", type=float, default=60.0)
    discover.add_argument("--gcs-credentials-file", default="")

    run = sub.add_parser("run-feature-shard", help="Process one manifest shard and write feature artifacts.")
    run.add_argument("--stage", required=True, help="Logical stage name, e.g. visual_primary, ocr, caption, objects.")
    run.add_argument("--shard-uri", required=True)
    run.add_argument("--output-prefix", required=True, help="GCS/local prefix for feature part files.")
    run.add_argument("--checkpoint-root", required=True, help="GCS/local root for checkpoint and lease files.")
    run.add_argument("--run-id", required=True)
    run.add_argument("--worker-id", default="")
    run.add_argument("--features", default="", help="Comma-separated override: embedding,caption,ocr,objects.")
    run.add_argument("--batch-size", type=int, default=0)
    run.add_argument("--download-workers", type=int, default=0)
    run.add_argument("--gcs-timeout", type=float, default=0)
    run.add_argument("--max-frames", type=int, default=0)
    run.add_argument("--max-runtime-seconds", type=int, default=0)
    run.add_argument("--heartbeat-seconds", type=int, default=120)
    run.add_argument("--lease-ttl-seconds", type=int, default=2700)
    run.add_argument("--model-cache-dir", default="")
    run.add_argument("--device", default="")
    run.add_argument("--warmup-models", action="store_true")
    run.add_argument("--dry-run", action="store_true")

    import_cmd = sub.add_parser("import-feature-artifacts", help="Merge feature artifacts from GCS/local storage and upsert databases.")
    import_cmd.add_argument("--artifact-uri", action="append", required=True, help="GCS/local JSONL file or prefix. Repeatable.")
    import_cmd.add_argument("--dataset-code", default="")
    import_cmd.add_argument("--dataset-name", default="")
    import_cmd.add_argument("--dataset-version", default="")
    import_cmd.add_argument("--dataset-root-uri", default="")
    import_cmd.add_argument("--milvus-collection", default="")
    import_cmd.add_argument("--elasticsearch-index", default="")
    import_cmd.add_argument("--asr-elasticsearch-index", default="")
    import_cmd.add_argument("--model-version", default="")
    import_cmd.add_argument("--batch-size", type=int, default=256)
    import_cmd.add_argument("--no-pg", action="store_true")
    import_cmd.add_argument("--no-milvus", action="store_true")
    import_cmd.add_argument("--no-elasticsearch", action="store_true")
    import_cmd.add_argument("--no-text-embeddings", action="store_true")
    import_cmd.add_argument("--text-embedding-collection", default="")
    import_cmd.add_argument("--text-embedding-model-name", default="dangvantuan/vietnamese-embedding")
    import_cmd.add_argument("--dry-run", action="store_true")
    import_cmd.add_argument("--report-uri", default="")

    plan = sub.add_parser("plan-notebook-run", help="Render Kaggle/Colab worker commands from notebook role YAML.")
    plan.add_argument("--run-id", required=True)
    plan.add_argument("--pipeline-profiles", default=str(DEFAULT_PIPELINE_PROFILES))
    plan.add_argument("--checkpoint-policy", default=str(DEFAULT_CHECKPOINT_POLICY))
    plan.add_argument("--batches", default="L21")
    plan.add_argument("--manifest-summary-uri", default="", help="Optional manifest_summary.json URI from discover-gcs-keyframes.")
    plan.add_argument("--shard-root", default="", help="Optional shard root if no manifest summary is available.")
    plan.add_argument("--num-shards", type=int, default=0)
    plan.add_argument("--frames-per-shard", type=int, default=0)
    plan.add_argument("--role-filter", default="", help="Comma-separated role ids to include.")
    plan.add_argument("--stage-filter", default="", help="Comma-separated stages to include.")
    plan.add_argument("--max-shards-per-role", type=int, default=0)
    plan.add_argument("--gcs-credentials-file", default="")
    plan.add_argument("--gcs-timeout", type=float, default=60.0)

    role = sub.add_parser("run-notebook-role", help="Render or execute one Kaggle/Colab role command from notebook role YAML.")
    role.add_argument("--role-id", required=True, help="Role id from plan-notebook-run, e.g. kaggle_l21_primary_visual.")
    role.add_argument("--run-id", required=True)
    role.add_argument("--batches", default="L21")
    role.add_argument("--manifest-summary-uri", default="", help="manifest_summary.json URI from discover-gcs-keyframes.")
    role.add_argument("--shard-root", default="", help="Optional shard root if no manifest summary is available.")
    role.add_argument("--num-shards", type=int, default=0)
    role.add_argument("--shard-index", type=int, default=0, help="Role-local shard index to render when --shard-uri is not set.")
    role.add_argument("--shard-uri", default="", help="Explicit shard URI for frame feature roles.")
    role.add_argument("--all-shards", action="store_true", help="Render or execute every shard assigned to this role.")
    role.add_argument("--execute", action="store_true", help="Actually run the rendered command(s). Omit for a dry-run JSON plan.")
    role.add_argument("--doctor-first", action="store_true", help="Run the role-specific doctor command before executing work.")
    role.add_argument("--pipeline-profiles", default=str(DEFAULT_PIPELINE_PROFILES))
    role.add_argument("--checkpoint-policy", default=str(DEFAULT_CHECKPOINT_POLICY))
    role.add_argument("--gcs-credentials-file", default="")
    role.add_argument("--gcs-timeout", type=float, default=60.0)

    cells = sub.add_parser("render-notebook-cells", help="Render copy-ready Kaggle/Colab notebook cells for one role.")
    cells.add_argument("--role-id", required=True, help="Role id from plan-notebook-run, e.g. kaggle_l21_primary_visual.")
    cells.add_argument("--run-id", required=True)
    cells.add_argument("--batches", default="L21")
    cells.add_argument("--manifest-summary-uri", default="", help="manifest_summary.json URI from discover-gcs-keyframes.")
    cells.add_argument("--shard-root", default="", help="Optional shard root if no manifest summary is available.")
    cells.add_argument("--num-shards", type=int, default=0)
    cells.add_argument("--shard-index", type=int, default=0)
    cells.add_argument("--shard-uri", default="", help="Explicit shard URI for frame feature roles.")
    cells.add_argument("--all-shards", action="store_true", help="Render every shard assigned to this role.")
    cells.add_argument("--pipeline-profiles", default=str(DEFAULT_PIPELINE_PROFILES))
    cells.add_argument("--checkpoint-policy", default=str(DEFAULT_CHECKPOINT_POLICY))
    cells.add_argument("--gcs-credentials-file", default="")
    cells.add_argument("--gcs-timeout", type=float, default=60.0)

    kit = sub.add_parser("render-notebook-kit", help="Write copy-ready Kaggle/Colab markdown cells for every selected role.")
    kit.add_argument("--run-id", required=True)
    kit.add_argument("--output-dir", default="", help="Directory for README.md, plan.json, and role markdown files.")
    kit.add_argument("--batches", default="L21")
    kit.add_argument("--manifest-summary-uri", default="", help="manifest_summary.json URI from discover-gcs-keyframes.")
    kit.add_argument("--shard-root", default="", help="Optional shard root if no manifest summary is available.")
    kit.add_argument("--num-shards", type=int, default=0)
    kit.add_argument("--all-shards", action="store_true", help="Render every shard assigned to each role.")
    kit.add_argument("--role-filter", default="", help="Comma-separated role ids to include.")
    kit.add_argument("--stage-filter", default="", help="Comma-separated stages to include.")
    kit.add_argument("--max-shards-per-role", type=int, default=0)
    kit.add_argument("--pipeline-profiles", default=str(DEFAULT_PIPELINE_PROFILES))
    kit.add_argument("--checkpoint-policy", default=str(DEFAULT_CHECKPOINT_POLICY))
    kit.add_argument("--gcs-credentials-file", default="")
    kit.add_argument("--gcs-timeout", type=float, default=60.0)

    smoke = sub.add_parser("smoke-local-flow", help="Run a fully local synthetic ingest smoke through plan, checkpoints, reconcile, and import dry-run.")
    smoke.add_argument("--run-id", default="smoke_local")
    smoke.add_argument("--workspace-root", default="")
    smoke.add_argument("--batches", default="L21")
    smoke.add_argument("--frame-count", type=int, default=2)
    smoke.add_argument("--frames-per-shard", type=int, default=1)
    smoke.add_argument("--pipeline-profiles", default=str(DEFAULT_PIPELINE_PROFILES))
    smoke.add_argument("--checkpoint-policy", default=str(DEFAULT_CHECKPOINT_POLICY))
    smoke.add_argument("--gcs-credentials-file", default="")
    smoke.add_argument("--gcs-timeout", type=float, default=60.0)

    reconcile = sub.add_parser("reconcile-run", help="Check manifest shards, checkpoint markers, and artifact part files before DB import.")
    reconcile.add_argument("--run-id", required=True)
    reconcile.add_argument("--manifest-summary-uri", required=True)
    reconcile.add_argument("--feature-output-prefix", default="")
    reconcile.add_argument("--checkpoint-root", default="")
    reconcile.add_argument("--stages", default="visual_primary,visual_secondary,objects,ocr,caption,asr")
    reconcile.add_argument("--gcs-credentials-file", default="")
    reconcile.add_argument("--gcs-timeout", type=float, default=60.0)
    reconcile.add_argument("--strict", action="store_true")

    doctor = sub.add_parser("doctor", help="Check Kaggle/Colab/importer runtime readiness before running ingest.")
    doctor.add_argument("--runtime", choices=["kaggle", "colab", "importer", "all"], default="all")
    doctor.add_argument("--features", default="", help="Optional comma-separated feature filter for runtime-specific checks.")
    doctor.add_argument("--check-gcs", action="store_true", help="Also probe GCS by listing at most one object.")
    doctor.add_argument("--bucket", default="")
    doctor.add_argument("--gcs-prefix", default="")
    doctor.add_argument("--gcs-credentials-file", default="")
    doctor.add_argument("--gcs-timeout", type=float, default=10.0)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    if args.command == "discover-gcs-keyframes":
        summary = _discover_gcs_keyframes(args)
    elif args.command == "run-feature-shard":
        summary = _run_feature_shard(args)
    elif args.command == "import-feature-artifacts":
        summary = _import_feature_artifacts(args)
    elif args.command == "plan-notebook-run":
        summary = _plan_notebook_run(args)
    elif args.command == "run-notebook-role":
        summary = _run_notebook_role(args)
    elif args.command == "render-notebook-cells":
        summary = _render_notebook_cells(args)
    elif args.command == "render-notebook-kit":
        summary = _render_notebook_kit(args)
    elif args.command == "smoke-local-flow":
        summary = _smoke_local_flow(args)
    elif args.command == "reconcile-run":
        summary = _reconcile_run(args)
    elif args.command == "doctor":
        summary = _doctor(args)
    else:
        raise ValueError(f"Unsupported command: {args.command}")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if _should_exit_nonzero(args, summary):
        raise SystemExit(2)


def _should_exit_nonzero(args: argparse.Namespace, summary: dict[str, Any]) -> bool:
    if not isinstance(summary, dict) or "ok" not in summary:
        return False
    if args.command == "doctor":
        return not bool(summary["ok"])
    if args.command == "reconcile-run":
        return not bool(summary["ok"])
    if args.command == "run-notebook-role" and bool(getattr(args, "execute", False)):
        return not bool(summary["ok"])
    if args.command == "smoke-local-flow":
        return not bool(summary["ok"])
    return False


def _discover_gcs_keyframes(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_processor_settings()
    bucket = args.bucket or settings.gcs_bucket
    if not bucket:
        raise RuntimeError("Set --bucket or GCS_BUCKET.")
    credentials_file = args.gcs_credentials_file or settings.gcs_credentials_file
    batches = [batch for batch in split_batches(args.batches) if batch != "L26"]
    prefixes = gcs_prefixes_for_batches(args.keyframes_prefix, args.dataset_id, args.frame_profile, batches)
    rows = build_gcs_keyframe_manifest(
        bucket_name=bucket,
        prefixes=prefixes,
        dataset_id=args.dataset_id,
        credentials_file=credentials_file,
        timeout_seconds=args.gcs_timeout,
        max_frames=args.max_frames,
        shot_segments_path=args.shot_segments_path,
    )
    output_root = args.output_root or f"gs://{bucket}/manifests/dataset={args.dataset_id}/pipeline=feature_ingest"
    return write_manifest_and_shards(
        rows,
        output_root=output_root,
        run_id=args.run_id,
        frames_per_shard=args.frames_per_shard,
        credentials_file=credentials_file,
        timeout_seconds=args.gcs_timeout,
    )


def _run_feature_shard(args: argparse.Namespace) -> dict[str, Any]:
    config = apply_cli_overrides(
        load_pipeline_config(args.config, profile=args.profile),
        {
            "features": args.features,
            "batch_size": args.batch_size,
            "download_workers": args.download_workers,
            "gcs_timeout": args.gcs_timeout,
            "model_cache_dir": args.model_cache_dir,
            "device": args.device,
        },
    )
    run_config = config.raw.get("run", {})
    options = ShardRunOptions(
        stage=args.stage,
        shard_uri=args.shard_uri,
        output_prefix=args.output_prefix,
        checkpoint_root=args.checkpoint_root,
        run_id=args.run_id,
        worker_id=args.worker_id,
        max_runtime_seconds=args.max_runtime_seconds,
        heartbeat_seconds=args.heartbeat_seconds,
        lease_ttl_seconds=args.lease_ttl_seconds,
        gcs_timeout=float(args.gcs_timeout or run_config.get("gcs_timeout") or 60.0),
        download_workers=int(args.download_workers or run_config.get("download_workers") or 8),
        batch_size=int(args.batch_size or run_config.get("batch_size") or 8),
        max_frames=int(args.max_frames or 0),
        warmup_models=bool(args.warmup_models),
        dry_run=bool(args.dry_run),
    )
    return run_feature_shard(config, options)


def _import_feature_artifacts(args: argparse.Namespace) -> dict[str, Any]:
    pipeline_config = load_pipeline_config(args.config, profile=args.profile)
    settings = pipeline_config.settings
    raw = pipeline_config.raw
    sink_config = raw.get("sinks", {})
    dataset_config = raw.get("dataset", {})
    config = SinkConfig(
        database_url=settings.database_url,
        milvus_uri=settings.milvus_uri,
        milvus_token=settings.milvus_token,
        elasticsearch_url=settings.elasticsearch_url,
        dataset_code=args.dataset_code or str(dataset_config.get("code", "aic-2026")),
        dataset_name=args.dataset_name or str(dataset_config.get("name", "aic-2026")),
        dataset_version=args.dataset_version or str(dataset_config.get("version", "v1")),
        dataset_root_uri=args.dataset_root_uri or str(sink_config.get("dataset_root_uri") or "gs://aic_ai_2026/processed/keyframes/dataset=ai_challenge_2025"),
        gcs_public_url=settings.gcs_public_url,
        milvus_collection=args.milvus_collection or str(sink_config.get("milvus_collection", "keyframe_embeddings_pe_core_bigG_14_448")),
        elasticsearch_index=args.elasticsearch_index or str(sink_config.get("elasticsearch_index", "keyframe_annotations")),
        model_version=args.model_version or str(sink_config.get("model_version", "unknown")),
        write_pg=not args.no_pg,
        write_milvus=not args.no_milvus,
        write_elasticsearch=not args.no_elasticsearch,
        postgres_disable_prepared_statements=bool(sink_config.get("postgres_disable_prepared_statements", True)),
        fail_on_sink_error=bool(sink_config.get("fail_on_sink_error", False)),
        elasticsearch_request_timeout=float(sink_config.get("elasticsearch_request_timeout", 10)),
        elasticsearch_max_retries=int(sink_config.get("elasticsearch_max_retries", 1)),
        elasticsearch_probe_timeout=float(sink_config.get("elasticsearch_probe_timeout", 1)),
        elasticsearch_disable_after_error=bool(sink_config.get("elasticsearch_disable_after_error", True)),
        dry_run=bool(args.dry_run),
    )
    summary = import_feature_artifacts(
        config,
        ArtifactImportOptions(
            artifact_uris=args.artifact_uri,
            batch_size=args.batch_size,
            write_pg=not args.no_pg,
            write_milvus=not args.no_milvus,
            write_elasticsearch=not args.no_elasticsearch,
            dry_run=args.dry_run,
            gcs_credentials_file=settings.gcs_credentials_file,
            asr_elasticsearch_index=args.asr_elasticsearch_index,
            text_embedding_collection=args.text_embedding_collection,
            text_embedding_model_name=args.text_embedding_model_name,
            write_text_embeddings=not args.no_text_embeddings,
        ),
    )
    if args.report_uri:
        ArtifactStore(credentials_file=settings.gcs_credentials_file).write_json(args.report_uri, summary)
    return summary


def _plan_notebook_run(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_processor_settings()
    return build_notebook_run_plan(
        RolePlanOptions(
            run_id=args.run_id,
            pipeline_profiles_path=args.pipeline_profiles,
            checkpoint_policy_path=args.checkpoint_policy,
            batches=args.batches,
            manifest_summary_uri=args.manifest_summary_uri,
            shard_root=args.shard_root,
            num_shards=args.num_shards,
            frames_per_shard=args.frames_per_shard,
            role_filter=args.role_filter,
            stage_filter=args.stage_filter,
            max_shards_per_role=args.max_shards_per_role,
            gcs_credentials_file=args.gcs_credentials_file or settings.gcs_credentials_file,
            gcs_timeout_seconds=args.gcs_timeout,
        )
    )


def _run_notebook_role(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_processor_settings()
    return render_or_run_notebook_role(
        NotebookRoleRunOptions(
            role_id=args.role_id,
            run_id=args.run_id,
            batches=args.batches,
            manifest_summary_uri=args.manifest_summary_uri,
            shard_root=args.shard_root,
            num_shards=args.num_shards,
            shard_index=args.shard_index,
            shard_uri=args.shard_uri,
            all_shards=args.all_shards,
            execute=args.execute,
            doctor_first=args.doctor_first,
            pipeline_profiles_path=args.pipeline_profiles,
            checkpoint_policy_path=args.checkpoint_policy,
            gcs_credentials_file=args.gcs_credentials_file or settings.gcs_credentials_file,
            gcs_timeout_seconds=args.gcs_timeout,
        )
    )


def _render_notebook_cells(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_processor_settings()
    return render_notebook_cells(
        NotebookCellsOptions(
            role_id=args.role_id,
            run_id=args.run_id,
            batches=args.batches,
            manifest_summary_uri=args.manifest_summary_uri,
            shard_root=args.shard_root,
            num_shards=args.num_shards,
            shard_index=args.shard_index,
            shard_uri=args.shard_uri,
            all_shards=bool(args.all_shards),
            pipeline_profiles_path=args.pipeline_profiles,
            checkpoint_policy_path=args.checkpoint_policy,
            gcs_credentials_file=args.gcs_credentials_file or settings.gcs_credentials_file,
            gcs_timeout_seconds=args.gcs_timeout,
        )
    )


def _render_notebook_kit(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_processor_settings()
    return render_notebook_kit(
        NotebookKitOptions(
            run_id=args.run_id,
            output_dir=args.output_dir,
            batches=args.batches,
            manifest_summary_uri=args.manifest_summary_uri,
            shard_root=args.shard_root,
            num_shards=args.num_shards,
            all_shards=bool(args.all_shards),
            role_filter=args.role_filter,
            stage_filter=args.stage_filter,
            max_shards_per_role=args.max_shards_per_role,
            pipeline_profiles_path=args.pipeline_profiles,
            checkpoint_policy_path=args.checkpoint_policy,
            gcs_credentials_file=args.gcs_credentials_file or settings.gcs_credentials_file,
            gcs_timeout_seconds=args.gcs_timeout,
        )
    )


def _smoke_local_flow(args: argparse.Namespace) -> dict[str, Any]:
    return run_local_smoke_flow(
        LocalSmokeFlowOptions(
            run_id=args.run_id,
            workspace_root=args.workspace_root,
            batches=args.batches,
            frame_count=args.frame_count,
            frames_per_shard=args.frames_per_shard,
            pipeline_profiles_path=args.pipeline_profiles,
            checkpoint_policy_path=args.checkpoint_policy,
            gcs_credentials_file=args.gcs_credentials_file,
            gcs_timeout_seconds=args.gcs_timeout,
        )
    )


def _reconcile_run(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_processor_settings()
    pipeline_config = load_pipeline_config(args.config, profile=args.profile)
    raw = pipeline_config.raw
    defaults = {}
    feature_output_prefix = args.feature_output_prefix or str(raw.get("run", {}).get("feature_output_prefix") or "")
    checkpoint_root = args.checkpoint_root or str(raw.get("run", {}).get("checkpoint_root") or "")
    if not feature_output_prefix or not checkpoint_root:
        profile_path = resolve_config_path(DEFAULT_PIPELINE_PROFILES)
        import yaml

        defaults = (yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}).get("run_defaults") or {}
        feature_output_prefix = feature_output_prefix or str(defaults.get("feature_output_prefix") or "")
        checkpoint_root = checkpoint_root or str(defaults.get("checkpoint_root") or "")
    return reconcile_feature_run(
        RunReconcileOptions(
            run_id=args.run_id,
            manifest_summary_uri=args.manifest_summary_uri,
            feature_output_prefix=feature_output_prefix,
            checkpoint_root=checkpoint_root,
            stages=args.stages,
            gcs_credentials_file=args.gcs_credentials_file or settings.gcs_credentials_file,
            gcs_timeout_seconds=args.gcs_timeout,
            strict=args.strict,
        )
    )


def _doctor(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_processor_settings()
    return run_processor_doctor(
        settings,
        DoctorOptions(
            runtime=args.runtime,
            profile=args.profile,
            features=args.features,
            config_path=args.config,
            check_gcs=args.check_gcs,
            bucket=args.bucket,
            gcs_prefix=args.gcs_prefix,
            gcs_credentials_file=args.gcs_credentials_file,
            gcs_timeout_seconds=args.gcs_timeout,
        ),
    )


if __name__ == "__main__":
    main()

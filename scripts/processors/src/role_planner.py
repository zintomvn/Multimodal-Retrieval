from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

from .artifact_io import ArtifactStore, join_uri
from .config_paths import DEFAULT_CHECKPOINT_POLICY, DEFAULT_PIPELINE_PROFILES, resolve_config_path
from .manifest import split_batches


@dataclass(frozen=True)
class RolePlanOptions:
    """Options for rendering a Kaggle/Colab notebook run plan."""

    run_id: str
    pipeline_profiles_path: str = str(DEFAULT_PIPELINE_PROFILES)
    checkpoint_policy_path: str = str(DEFAULT_CHECKPOINT_POLICY)
    batches: str = "L21"
    manifest_summary_uri: str = ""
    shard_root: str = ""
    num_shards: int = 0
    frames_per_shard: int = 0
    role_filter: str = ""
    stage_filter: str = ""
    max_shards_per_role: int = 0
    gcs_credentials_file: str = ""
    gcs_timeout_seconds: float = 60.0


def build_notebook_run_plan(options: RolePlanOptions) -> dict[str, Any]:
    """Build concrete Kaggle/Colab commands from pipeline role YAML."""
    profile = _read_yaml(options.pipeline_profiles_path)
    checkpoint_policy = (_read_yaml(options.checkpoint_policy_path).get("default") or {})
    defaults = profile.get("run_defaults") or {}
    batches = [batch for batch in split_batches(options.batches) if batch not in set(defaults.get("exclude_batches") or [])]
    roles = _selected_roles(profile.get("notebook_roles") or {}, options, batches)
    shards = _resolve_shards(options)
    frames_per_shard = int(options.frames_per_shard or _min_role_shard_size(roles) or 512)
    discovery_command = _discovery_command(defaults, options.run_id, batches, frames_per_shard)

    machine_roles = [
        _role_entry(
            role_id=role_id,
            role=role,
            defaults=defaults,
            run_id=options.run_id,
            batches=batches,
            shards=shards,
            checkpoint_policy=checkpoint_policy,
            max_shards_per_role=options.max_shards_per_role,
        )
        for role_id, role in roles
    ]

    return {
        "run_id": options.run_id,
        "batches": batches,
        "discovery": {
            "frames_per_shard": frames_per_shard,
            "command": discovery_command,
        },
        "manifest_summary_uri": options.manifest_summary_uri,
        "shard_root": _effective_shard_root(options, defaults),
        "shards": shards,
        "machine_roles": machine_roles,
        "imports": _import_commands(defaults, options.run_id, batches),
    }


def _read_yaml(path: str) -> dict[str, Any]:
    return yaml.safe_load(resolve_config_path(path).read_text(encoding="utf-8")) or {}


def _selected_roles(roles: dict[str, dict[str, Any]], options: RolePlanOptions, batches: list[str]) -> list[tuple[str, dict[str, Any]]]:
    role_filter = {item.strip() for item in str(options.role_filter or "").split(",") if item.strip()}
    stage_filter = {item.strip() for item in str(options.stage_filter or "").split(",") if item.strip()}
    selected: list[tuple[str, dict[str, Any]]] = []
    for role_id, role in roles.items():
        if role_filter and role_id not in role_filter:
            continue
        if stage_filter and str(role.get("stage") or "") not in stage_filter:
            continue
        selected.extend(_expand_role_batches(role_id, role, batches))
    return selected


def _expand_role_batches(role_id: str, role: dict[str, Any], batches: list[str]) -> list[tuple[str, dict[str, Any]]]:
    """ASR reads raw video prefixes, so it needs one concrete command per batch."""
    if str(role.get("stage") or "") != "asr" or len(batches) <= 1:
        return [(role_id, role)]

    expanded: list[tuple[str, dict[str, Any]]] = []
    for batch in batches:
        batch_role = dict(role)
        batch_role["batch"] = batch
        if role.get("raw_video_prefix"):
            batch_role["raw_video_prefix"] = _replace_batch_token(str(role["raw_video_prefix"]), batch)
        expanded.append((_batch_role_id(role_id, batch), batch_role))
    return expanded


def _batch_role_id(role_id: str, batch: str) -> str:
    batch_token = str(batch).lower()
    pattern = re.compile(r"(^|_)l\d{2}(_|$)", flags=re.IGNORECASE)
    if pattern.search(role_id):
        return pattern.sub(lambda match: f"{match.group(1)}{batch_token}{match.group(2)}", role_id, count=1)
    return f"{role_id}_{batch_token}"


def _replace_batch_token(prefix: str, batch: str) -> str:
    if "batch=" in prefix:
        return re.sub(r"batch=[^/]+", f"batch={batch}", prefix, count=1)
    return join_uri(prefix, f"batch={batch}")


def _resolve_shards(options: RolePlanOptions) -> list[str]:
    if options.manifest_summary_uri:
        summary = ArtifactStore(
            credentials_file=options.gcs_credentials_file,
            timeout_seconds=options.gcs_timeout_seconds,
        ).read_json(options.manifest_summary_uri, default={}) or {}
        shards = summary.get("shards") or []
        return [str(item) for item in shards]
    if options.shard_root and options.num_shards > 0:
        return [join_uri(options.shard_root, f"shard-{index:05d}.jsonl") for index in range(options.num_shards)]
    return ["<SHARD_URI>"]


def _effective_shard_root(options: RolePlanOptions, defaults: dict[str, Any]) -> str:
    if options.shard_root:
        return options.shard_root
    if options.manifest_summary_uri:
        summary = ArtifactStore(
            credentials_file=options.gcs_credentials_file,
            timeout_seconds=options.gcs_timeout_seconds,
        ).read_json(options.manifest_summary_uri, default={}) or {}
        return str(summary.get("shard_root") or "")
    root = str(defaults.get("manifest_output_root") or "")
    return join_uri(root, f"run_id={options.run_id}", "shards") if root else ""


def _min_role_shard_size(roles: list[tuple[str, dict[str, Any]]]) -> int:
    values: list[int] = []
    for _role_id, role in roles:
        raw = role.get("frames_per_shard")
        if isinstance(raw, int) and raw > 0:
            values.append(raw)
    return min(values) if values else 0


def _discovery_command(defaults: dict[str, Any], run_id: str, batches: list[str], frames_per_shard: int) -> str:
    dataset_id = str(defaults.get("dataset_id") or "ai_challenge_2025")
    frame_profile = str(defaults.get("frame_profile") or "autoshot_v1")
    keyframes_prefix = str(defaults.get("keyframes_prefix") or "processed/keyframes")
    manifest_output_root = str(defaults.get("manifest_output_root") or "")
    parts = [
        "python scripts/processors/processor_cli.py",
        "--profile smoke",
        "discover-gcs-keyframes",
        '--bucket "$GCS_BUCKET"',
        f"--dataset-id {dataset_id}",
        f"--batches {','.join(batches)}",
        f"--keyframes-prefix {keyframes_prefix}",
        f"--frame-profile {frame_profile}",
        "--shot-segments-path <SHOT_SEGMENTS_URI>",
        f"--run-id {run_id}",
        f"--frames-per-shard {frames_per_shard}",
    ]
    if manifest_output_root:
        parts.append(f"--output-root {manifest_output_root}")
    return " ".join(parts)


def _role_entry(
    role_id: str,
    role: dict[str, Any],
    defaults: dict[str, Any],
    run_id: str,
    batches: list[str],
    shards: list[str],
    checkpoint_policy: dict[str, Any],
    max_shards_per_role: int,
) -> dict[str, Any]:
    runtime = str(role.get("runtime") or "")
    stage = str(role.get("stage") or "")
    if stage == "asr":
        assigned_shards: list[str] = []
        command = _asr_command(role_id, role, defaults, run_id, checkpoint_policy)
    else:
        assigned_shards = shards[:max_shards_per_role] if max_shards_per_role > 0 else shards
        command = _feature_command(role_id, role, defaults, run_id, "<SHARD_URI>", checkpoint_policy)
    entry = {
        "role_id": role_id,
        "runtime": runtime,
        "batch": role.get("batch"),
        "batch_scope": [role.get("batch")] if stage == "asr" else batches,
        "stage": stage,
        "processor_profile": role.get("processor_profile"),
        "features": role.get("features"),
        "suggested_batch_size": role.get("suggested_batch_size"),
        "shards_to_run": assigned_shards,
        "command_template": command,
    }
    if stage == "asr":
        entry["raw_video_prefix"] = role.get("raw_video_prefix")
    return entry


def _feature_command(
    role_id: str,
    role: dict[str, Any],
    defaults: dict[str, Any],
    run_id: str,
    shard_uri: str,
    checkpoint_policy: dict[str, Any],
) -> str:
    runtime = str(role.get("runtime") or "")
    max_runtime = _max_runtime_seconds(checkpoint_policy, runtime)
    parts = [
        "python scripts/processors/processor_cli.py",
        f"--profile {role.get('processor_profile')}",
        "run-feature-shard",
        f"--run-id {run_id}",
        f"--stage {role.get('stage')}",
        f"--features {role.get('features')}",
        f"--shard-uri {shard_uri}",
        f"--output-prefix {defaults.get('feature_output_prefix')}",
        f"--checkpoint-root {defaults.get('checkpoint_root')}",
        f"--worker-id {role_id}",
        f"--batch-size {role.get('suggested_batch_size')}",
        f"--heartbeat-seconds {int(checkpoint_policy.get('heartbeat_seconds') or 120)}",
        f"--lease-ttl-seconds {int(checkpoint_policy.get('lease_ttl_seconds') or 2700)}",
        f"--max-runtime-seconds {max_runtime}",
        "--warmup-models",
    ]
    return " ".join(parts)


def _asr_command(role_id: str, role: dict[str, Any], defaults: dict[str, Any], run_id: str, checkpoint_policy: dict[str, Any]) -> str:
    runtime = str(role.get("runtime") or "")
    model = str(role.get("model") or "faster-whisper-small").replace("faster-whisper-", "")
    parts = [
        "python scripts/processors/extract_gcs_asr.py",
        '--bucket "$GCS_BUCKET"',
        f"--gcs-prefix {role.get('raw_video_prefix')}",
        f"--run-id {run_id}",
        f"--output-prefix {defaults.get('feature_output_prefix')}",
        f"--checkpoint-root {defaults.get('checkpoint_root')}",
        f"--worker-id {role_id}",
        f"--model-size {model}",
        "--language vi",
        f"--lease-ttl-seconds {int(checkpoint_policy.get('lease_ttl_seconds') or 2700)}",
        f"--max-runtime-seconds {_max_runtime_seconds(checkpoint_policy, runtime)}",
    ]
    return " ".join(parts)


def _max_runtime_seconds(checkpoint_policy: dict[str, Any], runtime: str) -> int:
    values = checkpoint_policy.get("max_runtime_seconds") or {}
    if isinstance(values, dict):
        return int(values.get(runtime) or 0)
    return 0


def _import_commands(defaults: dict[str, Any], run_id: str, batches: list[str]) -> list[dict[str, str]]:
    output_prefix = str(defaults.get("feature_output_prefix") or "")
    dataset_id = str(defaults.get("dataset_id") or "ai_challenge_2025")
    dataset_code, dataset_name = _dataset_labels(dataset_id, batches)
    base = [
        "python scripts/processors/processor_cli.py",
        "--profile full_text_features",
        "import-feature-artifacts",
        f"--dataset-code {dataset_code}",
        f"--dataset-name {dataset_name}",
        "--dataset-version mvp_v1",
    ]
    return [
        {
            "name": "primary_visual_to_zilliz",
            "command": " ".join(
                [
                    "python scripts/processors/processor_cli.py",
                    "--profile visual_primary_pe_core",
                    "import-feature-artifacts",
                    f"--artifact-uri {join_uri(output_prefix, f'run_id={run_id}', 'stage=visual_primary')}",
                    f"--dataset-code {dataset_code}",
                    f"--dataset-name {dataset_name}",
                    "--dataset-version mvp_v1",
                    "--milvus-collection keyframe_embeddings_pe_core_bigG_14_448",
                    "--model-version pe-core-bigG-14-448",
                    "--no-elasticsearch",
                ]
            ),
        },
        {
            "name": "secondary_visual_to_zilliz",
            "command": " ".join(
                [
                    "python scripts/processors/processor_cli.py",
                    "--profile visual_secondary_openclip_vith14",
                    "import-feature-artifacts",
                    f"--artifact-uri {join_uri(output_prefix, f'run_id={run_id}', 'stage=visual_secondary')}",
                    f"--dataset-code {dataset_code}",
                    f"--dataset-name {dataset_name}",
                    "--dataset-version mvp_v1",
                    "--milvus-collection keyframe_embeddings_openclip_vith14",
                    "--model-version openclip-vit-h-14-laion2b_s32b_b79k",
                    "--no-elasticsearch",
                ]
            ),
        },
        {
            "name": "text_metadata_to_supabase_elasticsearch_text_milvus",
            "command": " ".join(
                [
                    *base,
                    f"--artifact-uri {join_uri(output_prefix, f'run_id={run_id}', 'stage=objects')}",
                    f"--artifact-uri {join_uri(output_prefix, f'run_id={run_id}', 'stage=ocr')}",
                    f"--artifact-uri {join_uri(output_prefix, f'run_id={run_id}', 'stage=caption')}",
                    f"--artifact-uri {join_uri(output_prefix, f'run_id={run_id}', 'stage=asr')}",
                    "--elasticsearch-index keyframe_annotations",
                    "--model-version text_features_v1",
                    "--no-milvus",
                ]
            ),
        },
    ]


def _dataset_labels(dataset_id: str, batches: list[str]) -> tuple[str, str]:
    normalized_batches = [str(batch).lower() for batch in batches if batch]
    if normalized_batches == ["l21"]:
        suffix = "mvp_l21"
    elif len(normalized_batches) <= 3:
        suffix = "mvp_" + "_".join(normalized_batches)
    else:
        suffix = "mvp_multi_no_l26"
    return f"{dataset_id}_{suffix}", f"ai-challenge-2025-{suffix.replace('_', '-')}"

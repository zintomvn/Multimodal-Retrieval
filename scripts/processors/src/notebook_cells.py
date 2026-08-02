from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config_paths import PROCESSORS_ROOT
from .notebook_role_runner import NotebookRoleRunOptions, render_or_run_notebook_role
from .role_planner import DEFAULT_CHECKPOINT_POLICY, DEFAULT_PIPELINE_PROFILES, RolePlanOptions, build_notebook_run_plan


@dataclass(frozen=True)
class NotebookCellsOptions:
    """Options for rendering copy-ready Kaggle/Colab notebook cells."""

    role_id: str
    run_id: str
    batches: str = "L21"
    manifest_summary_uri: str = ""
    shard_root: str = ""
    num_shards: int = 0
    shard_index: int = 0
    shard_uri: str = ""
    all_shards: bool = False
    pipeline_profiles_path: str = ""
    checkpoint_policy_path: str = ""
    gcs_credentials_file: str = ""
    gcs_timeout_seconds: float = 60.0


@dataclass(frozen=True)
class NotebookKitOptions:
    """Options for writing a complete Kaggle/Colab notebook kit to disk."""

    run_id: str
    output_dir: str = ""
    batches: str = "L21"
    manifest_summary_uri: str = ""
    shard_root: str = ""
    num_shards: int = 0
    all_shards: bool = False
    role_filter: str = ""
    stage_filter: str = ""
    max_shards_per_role: int = 0
    pipeline_profiles_path: str = str(DEFAULT_PIPELINE_PROFILES)
    checkpoint_policy_path: str = str(DEFAULT_CHECKPOINT_POLICY)
    gcs_credentials_file: str = ""
    gcs_timeout_seconds: float = 60.0


def render_notebook_cells(options: NotebookCellsOptions) -> dict[str, Any]:
    role_report = render_or_run_notebook_role(
        NotebookRoleRunOptions(
            role_id=options.role_id,
            run_id=options.run_id,
            batches=options.batches,
            manifest_summary_uri=options.manifest_summary_uri,
            shard_root=options.shard_root,
            num_shards=options.num_shards,
            shard_index=options.shard_index,
            shard_uri=options.shard_uri,
            all_shards=options.all_shards,
            execute=False,
            doctor_first=False,
            pipeline_profiles_path=options.pipeline_profiles_path or str(DEFAULT_PIPELINE_PROFILES),
            checkpoint_policy_path=options.checkpoint_policy_path or str(DEFAULT_CHECKPOINT_POLICY),
            gcs_credentials_file=options.gcs_credentials_file,
            gcs_timeout_seconds=options.gcs_timeout_seconds,
        )
    )
    role = role_report["role"]
    runtime = str(role.get("runtime") or "")
    cells = [
        _markdown_cell("Role", _role_markdown(role)),
        _bash_cell("Install", _install_commands(runtime, str(role.get("stage") or ""))),
        _bash_cell("Environment", _environment_template(runtime)),
        _bash_cell("Doctor", role_report["doctor_command"]),
        _bash_cell("Run role", "\n".join(role_report["commands"])),
        _markdown_cell("Resume", _resume_markdown(role)),
    ]
    return {
        "ok": True,
        "role_id": options.role_id,
        "run_id": options.run_id,
        "runtime": runtime,
        "stage": role.get("stage"),
        "cells": cells,
        "markdown": _render_markdown(cells),
        "role_report": role_report,
    }


def render_notebook_kit(options: NotebookKitOptions) -> dict[str, Any]:
    plan = build_notebook_run_plan(
        RolePlanOptions(
            run_id=options.run_id,
            pipeline_profiles_path=options.pipeline_profiles_path,
            checkpoint_policy_path=options.checkpoint_policy_path,
            batches=options.batches,
            manifest_summary_uri=options.manifest_summary_uri,
            shard_root=options.shard_root,
            num_shards=options.num_shards,
            role_filter=options.role_filter,
            stage_filter=options.stage_filter,
            max_shards_per_role=options.max_shards_per_role,
            gcs_credentials_file=options.gcs_credentials_file,
            gcs_timeout_seconds=options.gcs_timeout_seconds,
        )
    )
    output_dir = _kit_output_dir(options)
    output_dir.mkdir(parents=True, exist_ok=True)

    files: list[dict[str, Any]] = []
    plan_path = output_dir / "plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    files.append({"type": "plan", "path": str(plan_path)})

    role_files: list[dict[str, Any]] = []
    for role in plan.get("machine_roles") or []:
        role_id = str(role.get("role_id") or "")
        if not role_id:
            continue
        cells_report = render_notebook_cells(
            NotebookCellsOptions(
                role_id=role_id,
                run_id=options.run_id,
                batches=options.batches,
                manifest_summary_uri=options.manifest_summary_uri,
                shard_root=options.shard_root,
                num_shards=options.num_shards,
                all_shards=options.all_shards,
                pipeline_profiles_path=options.pipeline_profiles_path,
                checkpoint_policy_path=options.checkpoint_policy_path,
                gcs_credentials_file=options.gcs_credentials_file,
                gcs_timeout_seconds=options.gcs_timeout_seconds,
            )
        )
        role_path = output_dir / f"{_safe_name(role_id)}.md"
        role_path.write_text(cells_report["markdown"] + "\n", encoding="utf-8")
        entry = {
            "type": "role_markdown",
            "role_id": role_id,
            "runtime": role.get("runtime"),
            "stage": role.get("stage"),
            "path": str(role_path),
        }
        role_files.append(entry)
        files.append(entry)

    index_path = output_dir / "README.md"
    index_path.write_text(_kit_index(plan, role_files), encoding="utf-8")
    files.insert(0, {"type": "index", "path": str(index_path)})
    return {
        "ok": True,
        "run_id": options.run_id,
        "batches": plan.get("batches") or [],
        "output_dir": str(output_dir),
        "files": files,
        "roles": role_files,
    }


def _role_markdown(role: dict[str, Any]) -> str:
    shards = role.get("shards_to_run") or []
    shard_text = str(len(shards)) if shards else "ASR raw-video prefix"
    return "\n".join(
        [
            f"# {role.get('role_id')}",
            "",
            f"- runtime: `{role.get('runtime')}`",
            f"- stage: `{role.get('stage')}`",
            f"- processor profile: `{role.get('processor_profile')}`",
            f"- feature set: `{role.get('features') or role.get('stage')}`",
            f"- assigned shards: `{shard_text}`",
        ]
    )


def _install_commands(runtime: str, stage: str) -> str:
    repo_dir = "/kaggle/working/Multimodal-Retrieval" if runtime == "kaggle" else "/content/Multimodal-Retrieval"
    if runtime not in {"kaggle", "colab"}:
        repo_dir = "$PWD"
    commands = [
        "set -euo pipefail",
        f"cd {repo_dir}",
        "python -m pip install -r scripts/processors/requirements.txt",
    ]
    if stage == "ocr":
        commands.append("python -m pip install -r scripts/processors/requirements-ocr.txt")
    if stage == "asr":
        commands.append("python -m pip install -r scripts/processors/requirements-asr.txt")
    return "\n".join(commands)


def _environment_template(runtime: str) -> str:
    lines = [
        "set -euo pipefail",
        'export GCS_BUCKET="${GCS_BUCKET:-aic_ai_2026}"',
        'if [ -n "${GCS_SERVICE_ACCOUNT_JSON:-}" ]; then',
        '  export GCS_CREDENTIALS_FILE="${GCS_CREDENTIALS_FILE:-/tmp/gcs-sa.json}"',
        "  python - <<'PY'",
        "import os",
        "from pathlib import Path",
        "path = Path(os.environ['GCS_CREDENTIALS_FILE'])",
        "path.parent.mkdir(parents=True, exist_ok=True)",
        "path.write_text(os.environ['GCS_SERVICE_ACCOUNT_JSON'], encoding='utf-8')",
        "PY",
        "elif [ -z \"${GCS_CREDENTIALS_FILE:-}\" ]; then",
        '  echo "Using ADC/default GCS credentials."',
        "fi",
    ]
    if runtime == "colab":
        lines.extend(
            [
                'export VLM_BASE_URL="${VLM_BASE_URL:-http://localhost:8001/v1}"',
                'export VLM_API_KEY="${VLM_API_KEY:-}"',
            ]
        )
    return "\n".join(lines)


def _resume_markdown(role: dict[str, Any]) -> str:
    stage = role.get("stage")
    if stage == "asr":
        return "Rerun the same ASR cell if the notebook stops. The command reuses the GCS checkpoint namespace for this raw-video prefix."
    return "Rerun the same role cell with the same shard URI if the notebook stops. Completed shards return `already_complete`; partial shards resume from `checkpoint.next_index`."


def _bash_cell(title: str, source: str) -> dict[str, str]:
    return {"type": "bash", "title": title, "source": source}


def _markdown_cell(title: str, source: str) -> dict[str, str]:
    return {"type": "markdown", "title": title, "source": source}


def _render_markdown(cells: list[dict[str, str]]) -> str:
    chunks: list[str] = []
    for cell in cells:
        chunks.append(f"## {cell['title']}")
        if cell["type"] == "bash":
            chunks.append("```bash")
            chunks.append(cell["source"])
            chunks.append("```")
        else:
            chunks.append(cell["source"])
        chunks.append("")
    return "\n".join(chunks).strip()


def _kit_output_dir(options: NotebookKitOptions) -> Path:
    if options.output_dir:
        return Path(options.output_dir)
    return PROCESSORS_ROOT / "notebook_kits" / f"run_id={_safe_name(options.run_id)}"


def _safe_name(value: str) -> str:
    chars: list[str] = []
    for char in value:
        if char.isalnum() or char in {"-", "_", "."}:
            chars.append(char)
        else:
            chars.append("_")
    return "".join(chars).strip("._") or "item"


def _kit_index(plan: dict[str, Any], role_files: list[dict[str, Any]]) -> str:
    lines = [
        f"# Notebook kit for `{plan.get('run_id')}`",
        "",
        f"- batches: `{','.join(plan.get('batches') or [])}`",
        f"- manifest summary: `{plan.get('manifest_summary_uri') or '<not provided>'}`",
        f"- shard root: `{plan.get('shard_root') or '<not provided>'}`",
        "",
        "## Control commands",
        "",
        "```bash",
        str((plan.get("discovery") or {}).get("command") or ""),
        "```",
        "",
        "## Role notebooks",
        "",
    ]
    for item in role_files:
        lines.append(f"- `{item['role_id']}` ({item['runtime']}/{item['stage']}): `{Path(item['path']).name}`")
    lines.extend(["", "## Import commands", ""])
    for item in plan.get("imports") or []:
        lines.extend(
            [
                f"### {item.get('name')}",
                "",
                "```bash",
                str(item.get("command") or ""),
                "```",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any

from .role_planner import DEFAULT_CHECKPOINT_POLICY, DEFAULT_PIPELINE_PROFILES, RolePlanOptions, build_notebook_run_plan


@dataclass(frozen=True)
class NotebookRoleRunOptions:
    """Options for rendering or executing one Kaggle/Colab notebook role."""

    role_id: str
    run_id: str
    batches: str = "L21"
    manifest_summary_uri: str = ""
    shard_root: str = ""
    num_shards: int = 0
    shard_index: int = 0
    shard_uri: str = ""
    all_shards: bool = False
    execute: bool = False
    doctor_first: bool = False
    pipeline_profiles_path: str = str(DEFAULT_PIPELINE_PROFILES)
    checkpoint_policy_path: str = str(DEFAULT_CHECKPOINT_POLICY)
    gcs_credentials_file: str = ""
    gcs_timeout_seconds: float = 60.0


def render_or_run_notebook_role(options: NotebookRoleRunOptions) -> dict[str, Any]:
    """Render concrete commands for one notebook role and optionally execute them."""
    plan = build_notebook_run_plan(
        RolePlanOptions(
            run_id=options.run_id,
            pipeline_profiles_path=options.pipeline_profiles_path,
            checkpoint_policy_path=options.checkpoint_policy_path,
            batches=options.batches,
            manifest_summary_uri=options.manifest_summary_uri,
            shard_root=options.shard_root,
            num_shards=options.num_shards,
            max_shards_per_role=0,
            gcs_credentials_file=options.gcs_credentials_file,
            gcs_timeout_seconds=options.gcs_timeout_seconds,
        )
    )
    role = _find_role(plan, options.role_id)
    commands = _commands_for_role(role, options)
    doctor_command = _doctor_command(role)
    executed: list[dict[str, Any]] = []
    skipped_commands: list[str] = []
    if options.execute:
        if options.doctor_first:
            doctor_result = _execute(doctor_command)
            executed.append(doctor_result)
            if doctor_result["returncode"] != 0:
                skipped_commands.extend(commands)
                return _role_report(options, role, doctor_command, commands, executed, skipped_commands)
        for command_index, command in enumerate(commands):
            result = _execute(command)
            executed.append(result)
            if result["returncode"] != 0:
                skipped_commands.extend(commands[command_index + 1 :])
                break
    return _role_report(options, role, doctor_command, commands, executed, skipped_commands)


def _role_report(
    options: NotebookRoleRunOptions,
    role: dict[str, Any],
    doctor_command: str,
    commands: list[str],
    executed: list[dict[str, Any]],
    skipped_commands: list[str],
) -> dict[str, Any]:
    return {
        "ok": all(item.get("returncode", 0) == 0 for item in executed),
        "run_id": options.run_id,
        "role": role,
        "doctor_command": doctor_command,
        "commands": commands,
        "execute": options.execute,
        "doctor_first": options.doctor_first,
        "executed": executed,
        "skipped_commands": skipped_commands,
    }


def _find_role(plan: dict[str, Any], role_id: str) -> dict[str, Any]:
    for role in plan.get("machine_roles") or []:
        if role.get("role_id") == role_id:
            return role
    available = [str(role.get("role_id")) for role in plan.get("machine_roles") or []]
    raise ValueError(f"Unknown role_id {role_id!r}. Available roles: {', '.join(available)}")


def _commands_for_role(role: dict[str, Any], options: NotebookRoleRunOptions) -> list[str]:
    template = str(role.get("command_template") or "")
    if role.get("stage") == "asr":
        return [template]
    if options.shard_uri:
        shards = [options.shard_uri]
    else:
        shards = [str(item) for item in role.get("shards_to_run") or []]
        if not options.all_shards:
            if options.shard_index < 0 or options.shard_index >= len(shards):
                raise ValueError(f"shard_index {options.shard_index} is outside the available shard range 0..{len(shards) - 1}")
            shards = [shards[options.shard_index]]
    return [template.replace("<SHARD_URI>", shard) for shard in shards]


def _doctor_command(role: dict[str, Any]) -> str:
    runtime = str(role.get("runtime") or "all")
    features = str(role.get("features") or role.get("stage") or "")
    profile = str(role.get("processor_profile") or "smoke")
    if profile == "asr":
        profile = "smoke"
    parts = [
        "python scripts/processors/processor_cli.py",
        f"--profile {profile}",
        "doctor",
        f"--runtime {runtime}",
    ]
    if features:
        parts.append(f"--features {features}")
    return " ".join(parts)


def _execute(command: str) -> dict[str, Any]:
    completed = subprocess.run(command, shell=True, check=False, capture_output=True, text=True)  # noqa: S602 - commands are generated from local YAML profiles.
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": _trim_output(completed.stdout),
        "stderr": _trim_output(completed.stderr),
    }


def _trim_output(value: str, limit: int = 12000) -> str:
    text = value or ""
    if len(text) <= limit:
        return text
    return text[-limit:]

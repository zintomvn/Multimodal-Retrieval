from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from config import ProcessorSettings, get_processor_settings


@dataclass(frozen=True)
class PipelineConfig:
    """Merged processor configuration loaded from YAML and .env."""

    raw: dict[str, Any]
    settings: ProcessorSettings
    profile: str

    @property
    def enabled_features(self) -> set[str]:
        """Return feature names enabled by the model config."""
        models = self.raw.get("models", {})
        return {
            name
            for name in ("embedding", "caption", "ocr", "objects")
            if bool(models.get(name, {}).get("enabled", False))
        }


def load_pipeline_config(path: str | Path, profile: str = "smoke") -> PipelineConfig:
    """Load YAML config, deep-merge the selected profile, and attach env settings."""
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    merged = copy.deepcopy(raw)
    profile_patch = (raw.get("profiles") or {}).get(profile, {})
    if profile_patch:
        merged = deep_merge(merged, profile_patch)
    merged.pop("profiles", None)
    return PipelineConfig(raw=merged, settings=get_processor_settings(), profile=profile)


def apply_cli_overrides(config: PipelineConfig, overrides: dict[str, Any]) -> PipelineConfig:
    """Apply non-empty CLI overrides to the merged config."""
    raw = copy.deepcopy(config.raw)
    mapping = {
        "gcs_prefix": ("run", "gcs_prefix"),
        "bucket": ("env", "bucket"),
        "shot_segments": ("run", "shot_segments"),
        "dataset_code": ("dataset", "code"),
        "dataset_name": ("dataset", "name"),
        "dataset_version": ("dataset", "version"),
        "batch_size": ("run", "batch_size"),
        "download_workers": ("run", "download_workers"),
        "gcs_timeout": ("run", "gcs_timeout"),
        "max_frames": ("run", "max_frames"),
        "annotations_jsonl": ("run", "annotations_jsonl"),
        "log_file": ("run", "log_file"),
        "device": ("models", "device"),
        "model_cache_dir": ("models", "cache_dir"),
    }
    for key, value in overrides.items():
        if value in (None, "", [], 0):
            continue
        target = mapping.get(key)
        if not target:
            continue
        if target[0] == "env":
            raw.setdefault("env", {})[target[1]] = value
            continue
        raw.setdefault(target[0], {})[target[1]] = value

    features = overrides.get("features")
    if features:
        enabled = {item.strip().lower() for item in str(features).split(",") if item.strip()}
        for name in ("embedding", "caption", "ocr", "objects"):
            raw.setdefault("models", {}).setdefault(name, {})["enabled"] = name in enabled

    video_ids = overrides.get("video_id")
    if video_ids:
        raw.setdefault("run", {})["video_ids"] = list(video_ids)

    return PipelineConfig(raw=raw, settings=config.settings, profile=config.profile)


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge patch onto base and return a new dict."""
    merged = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged

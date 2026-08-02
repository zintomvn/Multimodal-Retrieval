from __future__ import annotations

from pathlib import Path


PROCESSORS_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = PROCESSORS_ROOT / "configs"
PIPELINE_CONFIG_ROOT = CONFIG_ROOT / "pipeline"
RUNTIME_CONFIG_ROOT = CONFIG_ROOT / "runtime"

DEFAULT_PROCESSOR_CONFIG = PIPELINE_CONFIG_ROOT / "processor.yaml"
DEFAULT_PIPELINE_PROFILES = PIPELINE_CONFIG_ROOT / "notebook_roles.yaml"
DEFAULT_CHECKPOINT_POLICY = RUNTIME_CONFIG_ROOT / "checkpoint_policy.yaml"

_LEGACY_CONFIG_PATHS = {
    PROCESSORS_ROOT / "processor_config.yaml": DEFAULT_PROCESSOR_CONFIG,
    PROCESSORS_ROOT / "pipeline_profiles.yaml": DEFAULT_PIPELINE_PROFILES,
    PROCESSORS_ROOT / "checkpoint_policy.yaml": DEFAULT_CHECKPOINT_POLICY,
}
_LEGACY_CONFIG_FILENAMES = {path.name: target for path, target in _LEGACY_CONFIG_PATHS.items()}


def resolve_config_path(path: str | Path) -> Path:
    """Return the configured YAML path, accepting old root-level defaults."""
    candidate = Path(path)
    if candidate.exists():
        return candidate
    return _legacy_replacement(candidate) or candidate


def _legacy_replacement(candidate: Path) -> Path | None:
    replacement = _LEGACY_CONFIG_FILENAMES.get(candidate.name)
    if replacement is None:
        return None
    if len(candidate.parts) == 1:
        return replacement
    if _matches_legacy_path(candidate):
        return replacement
    return None


def _matches_legacy_path(candidate: Path) -> bool:
    absolute_candidate = candidate.resolve()
    return any(absolute_candidate == legacy_path.resolve() for legacy_path in _LEGACY_CONFIG_PATHS)

from __future__ import annotations

import importlib.util
import platform
import sys
from dataclasses import dataclass
from typing import Any

import yaml

from .artifact_io import gcs_client
from .config import ProcessorSettings
from .config_paths import resolve_config_path


BASE_PACKAGES = ["google.cloud.storage", "numpy", "PIL", "yaml"]
FEATURE_PACKAGES = {
    "embedding": ["torch", "open_clip"],
    "objects": ["ultralytics"],
    "ocr": ["easyocr", "cv2"],
    "caption": ["transformers"],
    "asr": ["faster_whisper"],
    "text_embedding": ["sentence_transformers"],
    "importer": ["sqlalchemy", "psycopg", "pymilvus", "elasticsearch"],
}
KNOWN_FEATURES = set(FEATURE_PACKAGES)
RUNTIME_FEATURES = {
    "kaggle": ["embedding", "objects"],
    "colab": ["ocr", "caption", "asr"],
    "importer": ["text_embedding", "importer"],
    "all": ["embedding", "objects", "ocr", "caption", "asr", "text_embedding", "importer"],
}


@dataclass(frozen=True)
class DoctorOptions:
    """Options for processor runtime readiness checks."""

    runtime: str = "all"
    profile: str = "smoke"
    features: str = ""
    config_path: str = ""
    check_gcs: bool = False
    bucket: str = ""
    gcs_prefix: str = ""
    gcs_credentials_file: str = ""
    gcs_timeout_seconds: float = 10.0


def run_processor_doctor(settings: ProcessorSettings, options: DoctorOptions) -> dict[str, Any]:
    """Return a structured readiness report for Kaggle, Colab, or importer runtimes."""
    runtime = options.runtime.lower()
    selected_features = _selected_features(runtime, options.features)
    feature_report = _feature_report(selected_features)
    packages = sorted(set(BASE_PACKAGES + _packages_for_features(selected_features)))
    env_report = _env_report(settings, runtime)
    package_report = [{"module": name, "ok": _module_available(name)} for name in packages]
    profile_report = _profile_report(options.config_path, options.profile) if options.config_path else {"ok": True, "profile": options.profile}
    python_report = _python_report(selected_features)
    gcs_report = _gcs_report(settings, options) if options.check_gcs else {"checked": False, "ok": True}
    ok = (
        bool(feature_report["ok"])
        and all(item["ok"] for item in env_report)
        and all(item["ok"] for item in package_report)
        and bool(profile_report["ok"])
        and bool(python_report["ok"])
        and bool(gcs_report["ok"])
    )
    return {
        "runtime": runtime,
        "features": selected_features,
        "feature_config": feature_report,
        "profile": options.profile,
        "python": platform.python_version(),
        "python_compatibility": python_report,
        "platform": platform.platform(),
        "env": env_report,
        "packages": package_report,
        "profile_config": profile_report,
        "gcs": gcs_report,
        "ok": ok,
    }


def _selected_features(runtime: str, raw_features: str) -> list[str]:
    requested = [item.strip() for item in str(raw_features or "").split(",") if item.strip()]
    if requested:
        return requested
    return RUNTIME_FEATURES.get(runtime, RUNTIME_FEATURES["all"])


def _packages_for_features(features: list[str]) -> list[str]:
    packages: list[str] = []
    for feature in features:
        packages.extend(FEATURE_PACKAGES.get(feature, []))
    return packages


def _feature_report(features: list[str]) -> dict[str, Any]:
    unknown = sorted(set(features) - KNOWN_FEATURES)
    if unknown:
        return {
            "ok": False,
            "reason": f"unknown feature(s): {', '.join(unknown)}",
            "unknown_features": unknown,
        }
    return {"ok": True, "reason": "", "unknown_features": []}


def _env_report(settings: ProcessorSettings, runtime: str) -> list[dict[str, Any]]:
    required = ["gcs_bucket"]
    if runtime == "importer" or runtime == "all":
        required.extend(["database_url", "milvus_uri", "elasticsearch_url"])
    values = {
        "gcs_bucket": settings.gcs_bucket,
        "gcs_credentials_file": settings.gcs_credentials_file,
        "database_url": settings.database_url,
        "milvus_uri": settings.milvus_uri,
        "milvus_token": settings.milvus_token,
        "elasticsearch_url": settings.elasticsearch_url,
    }
    rows: list[dict[str, Any]] = []
    for key, value in values.items():
        required_flag = key in required
        check = _env_check(key, value, runtime, required_flag)
        rows.append(
            {
                "name": key,
                "required": required_flag,
                "configured": bool(value),
                "ok": check["ok"],
                "warning": check.get("warning", ""),
                "reason": check.get("reason", ""),
            }
        )
    return rows


def _env_check(key: str, value: str, runtime: str, required: bool) -> dict[str, Any]:
    if required and not value:
        return {"ok": False, "reason": "missing required setting"}
    if key == "database_url" and runtime in {"importer", "all"}:
        if not str(value).startswith("postgresql"):
            return {"ok": False, "reason": "importer must target Supabase/PostgreSQL, not the local SQLite fallback"}
    if key == "milvus_uri" and runtime in {"importer", "all"} and str(value).startswith("http://localhost"):
        return {"ok": True, "warning": "using local Milvus URI; set Zilliz MILVUS_URI/MILVUS_TOKEN for competition import"}
    if key == "elasticsearch_url" and runtime in {"importer", "all"} and "localhost" in str(value):
        return {"ok": True, "warning": "using local Elasticsearch URL; verify this is the intended importer endpoint"}
    return {"ok": bool(value) if required else True}


def _python_report(features: list[str]) -> dict[str, Any]:
    version = sys.version_info
    requires_ocr_stack = any(feature in {"ocr", "asr"} for feature in features)
    if requires_ocr_stack and version >= (3, 13):
        return {
            "ok": False,
            "reason": "OCR dependencies are pinned for Python <3.13; use Python 3.11 or 3.12 for OCR/Colab workers.",
        }
    return {"ok": True, "reason": ""}


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def _profile_report(config_path: str, profile: str) -> dict[str, Any]:
    path = resolve_config_path(config_path)
    if not path.exists():
        return {"ok": False, "profile": profile, "error": f"missing config: {path}"}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profiles = raw.get("profiles") or {}
    if profile not in profiles and profile not in {"", "default"}:
        return {"ok": False, "profile": profile, "available_profiles": sorted(profiles)}
    return {"ok": True, "profile": profile, "available_profiles": sorted(profiles)}


def _gcs_report(settings: ProcessorSettings, options: DoctorOptions) -> dict[str, Any]:
    bucket_name = options.bucket or settings.gcs_bucket
    if not bucket_name:
        return {"checked": True, "ok": False, "error": "missing GCS bucket"}
    try:
        credentials_file = options.gcs_credentials_file or settings.gcs_credentials_file
        client = gcs_client(credentials_file)
        prefix = options.gcs_prefix.strip("/")
        blobs = list(client.list_blobs(bucket_name, prefix=prefix, max_results=1, timeout=options.gcs_timeout_seconds))
        return {"checked": True, "ok": True, "bucket": bucket_name, "prefix": prefix, "sample_count": len(blobs)}
    except Exception as exc:  # noqa: BLE001 - readiness report should summarize external failures.
        return {"checked": True, "ok": False, "bucket": bucket_name, "error": str(exc)}

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[4] / ".env")

REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _resolve_repo_path(raw: str, *, fallback_base: Path = REPO_ROOT) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path

    candidates = [
        BACKEND_ROOT / path,
        REPO_ROOT / path,
        Path.cwd() / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return (fallback_base / path).resolve()


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def normalize_database_url(raw: str) -> str:
    value = (raw or "").strip()
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


def database_connect_args(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False, "timeout": 30}
    if database_url.startswith("postgresql"):
        args: dict = {"prepare_threshold": None}
        parsed = urlparse(database_url)
        if "supabase.com" in (parsed.hostname or "") and "sslmode=" not in (parsed.query or ""):
            args["sslmode"] = "require"
        return args
    return {}


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "local")
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "8000"))
    database_url: str = normalize_database_url(os.getenv("DATABASE_URL", "sqlite:///./data/dev.db"))
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    milvus_uri: str = os.getenv("MILVUS_URI", "http://localhost:19530")
    milvus_token: str = os.getenv("MILVUS_TOKEN", "")
    elasticsearch_url: str = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
    s3_endpoint: str = os.getenv("S3_ENDPOINT", "http://localhost:9000")
    s3_access_key: str = os.getenv("S3_ACCESS_KEY", "")
    s3_secret_key: str = os.getenv("S3_SECRET_KEY", "")
    s3_bucket: str = os.getenv("S3_BUCKET", "multimodal-assets")
    r2_public_url: str = os.getenv("R2_PUBLIC_URL", "")
    gcs_bucket: str = os.getenv("GCS_BUCKET", "")
    gcs_credentials_file: str = os.getenv("GCS_CREDENTIALS_FILE", "")
    gcs_public_url: str = os.getenv("GCS_PUBLIC_URL", "")
    storage_provider: str = os.getenv("STORAGE_PROVIDER", "gcs")  # "local" | "r2" | "gcs"
    data_root: Path = _resolve_repo_path(os.getenv("DATA_ROOT", "./data"))
    model_registry_path: Path = _resolve_repo_path(os.getenv("MODEL_REGISTRY_PATH", "../../configs/model_registry.yaml"))
    retrieval_profiles_path: Path = _resolve_repo_path(os.getenv("RETRIEVAL_PROFILES_PATH", "../../configs/retrieval_profiles.yaml"))
    agent_config_path: Path = _resolve_repo_path(os.getenv("AGENT_CONFIG_PATH", "../../configs/agent.yaml"))
    # DRES credentials are intentionally backend-only. Never expose either
    # value through a VITE_ variable or an API response.
    dres_base_url: str = os.getenv("DRES_BASE_URL", "https://eventretrieval.one/api/v2").rstrip("/")
    dres_session_id: str = os.getenv("DRES_SESSION_ID", "").strip()
    dres_evaluation_name: str = os.getenv("DRES_EVALUATION_NAME", "").strip()
    dres_kis_evaluation_name: str = os.getenv("DRES_KIS_EVALUATION_NAME", "").strip()
    dres_qa_evaluation_name: str = os.getenv("DRES_QA_EVALUATION_NAME", "").strip()
    dres_trake_evaluation_name: str = os.getenv("DRES_TRAKE_EVALUATION_NAME", "").strip()
    dres_timeout_seconds: float = float(os.getenv("DRES_TIMEOUT_SECONDS", "12"))
    skip_db_init: bool = _bool_env("SKIP_DB_INIT", False)

    @property
    def cors_origins(self) -> list[str]:
        raw = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
        return [origin.strip() for origin in raw.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_root.mkdir(parents=True, exist_ok=True)
    return settings

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

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


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "local")
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "8000"))
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/dev.db")
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

    @property
    def cors_origins(self) -> list[str]:
        raw = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
        return [origin.strip() for origin in raw.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_root.mkdir(parents=True, exist_ok=True)
    return settings

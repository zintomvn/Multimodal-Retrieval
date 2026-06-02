from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "local")
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "8000"))
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/dev.db")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    milvus_host: str = os.getenv("MILVUS_HOST", "localhost")
    milvus_port: int = int(os.getenv("MILVUS_PORT", "19530"))
    elasticsearch_url: str = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
    s3_endpoint: str = os.getenv("S3_ENDPOINT", "http://localhost:9000")
    s3_bucket: str = os.getenv("S3_BUCKET", "multimodal-assets")
    data_root: Path = Path(os.getenv("DATA_ROOT", "./data"))
    model_registry_path: Path = Path(os.getenv("MODEL_REGISTRY_PATH", "../../configs/model_registry.yaml"))
    retrieval_profiles_path: Path = Path(os.getenv("RETRIEVAL_PROFILES_PATH", "../../configs/retrieval_profiles.yaml"))
    mock_mode: bool = os.getenv("MOCK_MODE", "true").lower() in {"1", "true", "yes", "on"}

    @property
    def cors_origins(self) -> list[str]:
        raw = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
        return [origin.strip() for origin in raw.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_root.mkdir(parents=True, exist_ok=True)
    return settings

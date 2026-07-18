from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_project_env() -> None:
    """Load the repository .env file without overriding existing variables."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class ProcessorSettings:
    """Runtime settings read from .env and CLI defaults."""

    database_url: str
    gcs_bucket: str
    gcs_credentials_file: str
    gcs_public_url: str
    milvus_uri: str
    milvus_token: str
    elasticsearch_url: str


def get_processor_settings() -> ProcessorSettings:
    """Return settings after loading the project-level .env file."""
    load_project_env()
    return ProcessorSettings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///./data/dev.db"),
        gcs_bucket=os.getenv("GCS_BUCKET", ""),
        gcs_credentials_file=os.getenv("GCS_CREDENTIALS_FILE", ""),
        gcs_public_url=os.getenv("GCS_PUBLIC_URL", ""),
        milvus_uri=os.getenv("MILVUS_URI", "http://localhost:19530"),
        milvus_token=os.getenv("MILVUS_TOKEN", ""),
        elasticsearch_url=os.getenv("ELASTICSEARCH_URL", "http://localhost:9200"),
    )


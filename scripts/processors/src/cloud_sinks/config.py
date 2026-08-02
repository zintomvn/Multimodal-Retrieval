from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SinkConfig:
    """Cloud sink configuration merged from YAML and .env."""

    database_url: str
    milvus_uri: str
    milvus_token: str
    elasticsearch_url: str
    dataset_code: str
    dataset_name: str
    dataset_version: str
    dataset_root_uri: str
    gcs_public_url: str
    milvus_collection: str
    elasticsearch_index: str
    model_version: str
    write_pg: bool = True
    write_milvus: bool = True
    write_elasticsearch: bool = True
    postgres_disable_prepared_statements: bool = True
    fail_on_sink_error: bool = False
    elasticsearch_request_timeout: float = 10.0
    elasticsearch_max_retries: int = 1
    elasticsearch_probe_timeout: float = 1.0
    elasticsearch_disable_after_error: bool = True
    dry_run: bool = False

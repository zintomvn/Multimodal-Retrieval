from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

from app.adapters.object_storage.base import ObjectStorageClient
from app.adapters.text_search.base import TextSearchClient
from app.adapters.model_runtime.base import QueryExpander, TextImageEmbedder, VisualQaModel
from app.adapters.vector_db.base import VectorSearchClient
from app.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_vector_client() -> VectorSearchClient:
    settings = get_settings()
    if settings.mock_mode:
        from app.adapters.vector_db.mock import InMemoryVectorSearchClient

        return InMemoryVectorSearchClient()
    from app.adapters.vector_db.milvus import MilvusVectorSearchClient

    return MilvusVectorSearchClient(uri=settings.milvus_uri, token=settings.milvus_token)


@lru_cache(maxsize=1)
def get_text_client() -> TextSearchClient:
    settings = get_settings()
    if settings.mock_mode:
        from app.adapters.text_search.mock import InMemoryTextSearchClient

        return InMemoryTextSearchClient()
    from app.adapters.text_search.elasticsearch import ElasticsearchTextSearchClient

    return ElasticsearchTextSearchClient(url=settings.elasticsearch_url)


@lru_cache(maxsize=1)
def get_object_storage() -> ObjectStorageClient:
    settings = get_settings()
    if settings.mock_mode or settings.storage_provider == "mock":
        from app.adapters.object_storage.mock import InMemoryObjectStorageClient

        return InMemoryObjectStorageClient()
    if settings.storage_provider == "local":
        from app.adapters.object_storage.local import LocalObjectStorageClient

        return LocalObjectStorageClient(data_root=settings.data_root)
    if settings.storage_provider == "gcs":
        from app.adapters.object_storage.gcs import GCSObjectStorageClient

        return GCSObjectStorageClient(
            bucket=settings.gcs_bucket,
            credentials_file=settings.gcs_credentials_file,
            public_base_url=settings.gcs_public_url,
        )
    # default: r2
    from app.adapters.object_storage.r2 import R2ObjectStorageClient

    return R2ObjectStorageClient(
        endpoint=settings.s3_endpoint,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        bucket=settings.s3_bucket,
        public_base_url=settings.r2_public_url,
    )


@lru_cache(maxsize=1)
def get_model_registry_service():  # noqa: ANN201 — avoids circular import with ModelRegistryService
    from app.adapters.model_runtime.mock import MockEmbedder, MockQueryExpander, MockVisualQaModel
    from app.adapters.model_runtime.openai_compatible import (
        OpenAICompatibleQueryExpander,
        OpenAICompatibleTextEmbedder,
        OpenAICompatibleVisualQaModel,
    )
    from app.modules.models.service import ModelRegistryService

    settings = get_settings()
    registry = ModelRegistryService.load_registry(settings.model_registry_path)

    def first_enabled_entry(group: str) -> tuple[str, dict[str, Any]] | None:
        entries = registry.get(group)
        if not isinstance(entries, dict):
            return None
        for name, config in entries.items():
            if isinstance(config, dict) and config.get("enabled"):
                return str(name), config
        return None

    def api_key(config: dict[str, Any]) -> str:
        key = str(config.get("api_key", "")).strip()
        if key:
            return key
        env_name = str(config.get("api_key_env", "")).strip()
        if env_name:
            return os.getenv(env_name, "")
        return os.getenv("OPENAI_API_KEY", "")

    def bool_value(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    embedder: TextImageEmbedder
    query_expander: QueryExpander
    visual_qa: VisualQaModel

    embedder_entry = first_enabled_entry("embedders")
    if embedder_entry:
        embedder_name, embedder_cfg = embedder_entry
        provider = str(embedder_cfg.get("provider", "mock")).lower()
        configured_dim = int(embedder_cfg.get("dim", settings.mock_embedding_dim))
        if provider == "mock":
            embedder = MockEmbedder(dim=configured_dim)
        elif provider == "openai_compatible":
            base_url = str(embedder_cfg.get("base_url", "")).strip().rstrip("/")
            model = str(embedder_cfg.get("model", "")).strip()
            if base_url and model:
                embedder = OpenAICompatibleTextEmbedder(
                    base_url=base_url,
                    model=model,
                    api_key=api_key(embedder_cfg),
                    expected_dim=configured_dim if configured_dim > 0 else None,
                    l2_normalize=bool_value(embedder_cfg.get("l2_normalize"), default=False),
                )
            else:
                logger.warning(
                    "Enabled embedder '%s' missing base_url/model; falling back to mock embedder.",
                    embedder_name,
                )
                embedder = MockEmbedder(dim=settings.mock_embedding_dim)
        else:
            logger.warning(
                "Enabled embedder '%s' uses unsupported provider '%s'; falling back to mock embedder.",
                embedder_name,
                provider,
            )
            embedder = MockEmbedder(dim=settings.mock_embedding_dim)
    else:
        embedder = MockEmbedder(dim=settings.mock_embedding_dim)

    llm_entry = first_enabled_entry("llm")
    if llm_entry:
        llm_name, llm_cfg = llm_entry
        provider = str(llm_cfg.get("provider", "mock")).lower()
        if provider == "mock":
            query_expander = MockQueryExpander()
        elif provider == "openai_compatible":
            base_url = str(llm_cfg.get("base_url", "")).strip().rstrip("/")
            model = str(llm_cfg.get("model", "")).strip()
            if base_url and model:
                query_expander = OpenAICompatibleQueryExpander(
                    base_url=base_url,
                    model=model,
                    api_key=api_key(llm_cfg),
                )
            else:
                logger.warning(
                    "Enabled query expander '%s' missing base_url/model; falling back to mock query expander.",
                    llm_name,
                )
                query_expander = MockQueryExpander()
        else:
            logger.warning(
                "Enabled query expander '%s' uses unsupported provider '%s'; falling back to mock query expander.",
                llm_name,
                provider,
            )
            query_expander = MockQueryExpander()
    else:
        query_expander = MockQueryExpander()

    vlm_entry = first_enabled_entry("vision_language")
    if vlm_entry:
        vlm_name, vlm_cfg = vlm_entry
        provider = str(vlm_cfg.get("provider", "mock")).lower()
        if provider == "mock":
            visual_qa = MockVisualQaModel()
        elif provider == "openai_compatible":
            base_url = str(vlm_cfg.get("base_url", "")).strip().rstrip("/")
            model = str(vlm_cfg.get("model", "")).strip()
            if base_url and model:
                visual_qa = OpenAICompatibleVisualQaModel(
                    base_url=base_url,
                    model=model,
                    api_key=api_key(vlm_cfg),
                )
            else:
                logger.warning(
                    "Enabled visual QA model '%s' missing base_url/model; falling back to mock visual QA.",
                    vlm_name,
                )
                visual_qa = MockVisualQaModel()
        else:
            logger.warning(
                "Enabled visual QA model '%s' uses unsupported provider '%s'; falling back to mock visual QA.",
                vlm_name,
                provider,
            )
            visual_qa = MockVisualQaModel()
    else:
        visual_qa = MockVisualQaModel()

    return ModelRegistryService(
        embedder=embedder,
        query_expander=query_expander,
        visual_qa=visual_qa,
        registry=registry,
    )

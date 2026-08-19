from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from app.adapters.object_storage.base import ObjectStorageClient
from app.adapters.model_runtime.base import QueryExpander, TextImageEmbedder, TextReranker, VisualQaModel
from app.adapters.text_search.base import TextSearchClient
from app.adapters.vector_db.base import VectorSearchClient
from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_vector_client() -> VectorSearchClient:
    settings = get_settings()
    from app.adapters.vector_db.milvus import MilvusVectorSearchClient

    return MilvusVectorSearchClient(uri=settings.milvus_uri, token=settings.milvus_token)


@lru_cache(maxsize=1)
def get_text_client() -> TextSearchClient:
    settings = get_settings()
    backend = os.getenv("TEXT_SEARCH_BACKEND", "").strip().lower()
    if not backend:
        backend = "postgres" if settings.database_url.startswith("postgresql") else "elasticsearch"
    if backend in {"postgres", "postgresql", "supabase"}:
        from app.adapters.text_search.postgres import PostgresTextSearchClient

        return PostgresTextSearchClient(database_url=settings.database_url)
    from app.adapters.text_search.elasticsearch import ElasticsearchTextSearchClient

    return ElasticsearchTextSearchClient(url=settings.elasticsearch_url)


@lru_cache(maxsize=1)
def get_object_storage() -> ObjectStorageClient:
    settings = get_settings()
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
    from app.adapters.model_runtime.fallback import (
        PassthroughQueryExpander,
        UnavailableTextImageEmbedder,
        UnavailableTextReranker,
        UnavailableVisualQaModel,
    )
    from app.adapters.model_runtime.cross_encoder import CrossEncoderTextReranker
    from app.adapters.model_runtime.openai_compatible import (
        OpenAICompatibleQueryExpander,
        OpenAICompatibleTextEmbedder,
        OpenAICompatibleVisualQaModel,
    )
    from app.adapters.model_runtime.siglip2 import Siglip2TextEmbedder
    from app.modules.models.service import ModelRegistryService

    settings = get_settings()
    registry = ModelRegistryService.load_registry(settings.model_registry_path)

    def first_enabled_entry(group: str, supported_providers: set[str] | None = None) -> tuple[str, dict[str, Any]] | None:
        entries = registry.get(group)
        if not isinstance(entries, dict):
            return None
        for name, config in entries.items():
            if not isinstance(config, dict) or not config.get("enabled"):
                continue
            provider = str(config.get("provider", "")).lower()
            if supported_providers is not None and provider not in supported_providers:
                continue
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
    reranker: TextReranker

    siglip2_providers = {"siglip2", "transformers_siglip2", "huggingface_siglip2"}
    embedder_entry = first_enabled_entry("embedders", {"openai_compatible", *siglip2_providers})
    if embedder_entry:
        embedder_name, embedder_cfg = embedder_entry
        provider = str(embedder_cfg.get("provider", "")).lower()
        configured_dim = int(embedder_cfg.get("dim", 0))
        if provider == "openai_compatible":
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
                raise RuntimeError(f"Enabled embedder '{embedder_name}' is missing base_url/model.")
        elif provider in siglip2_providers:
            model = str(embedder_cfg.get("model") or embedder_cfg.get("checkpoint_uri") or "").strip()
            if model:
                embedder = Siglip2TextEmbedder(
                    model_name=model,
                    device=str(embedder_cfg.get("device", "")).strip() or None,
                    cache_dir=str(embedder_cfg.get("cache_dir", "")).strip() or None,
                    local_files_only=bool_value(embedder_cfg.get("local_files_only"), default=True),
                    expected_dim=configured_dim if configured_dim > 0 else None,
                    l2_normalize=bool_value(embedder_cfg.get("l2_normalize"), default=True),
                )
            else:
                raise RuntimeError(f"Enabled embedder '{embedder_name}' is missing model/checkpoint_uri.")
        else:
            raise RuntimeError(f"Enabled embedder '{embedder_name}' uses unsupported provider '{provider}'.")
    else:
        embedder = UnavailableTextImageEmbedder()

    llm_entry = first_enabled_entry("llm", {"openai_compatible"})
    if llm_entry:
        llm_name, llm_cfg = llm_entry
        provider = str(llm_cfg.get("provider", "")).lower()
        if provider == "openai_compatible":
            base_url = str(llm_cfg.get("base_url", "")).strip().rstrip("/")
            model = str(llm_cfg.get("model", "")).strip()
            if base_url and model:
                query_expander = OpenAICompatibleQueryExpander(
                    base_url=base_url,
                    model=model,
                    api_key=api_key(llm_cfg),
                )
            else:
                raise RuntimeError(f"Enabled query expander '{llm_name}' is missing base_url/model.")
        else:
            raise RuntimeError(f"Enabled query expander '{llm_name}' uses unsupported provider '{provider}'.")
    else:
        query_expander = PassthroughQueryExpander()

    vlm_entry = first_enabled_entry("vision_language", {"openai_compatible"})
    if vlm_entry:
        vlm_name, vlm_cfg = vlm_entry
        provider = str(vlm_cfg.get("provider", "")).lower()
        if provider == "openai_compatible":
            base_url = str(vlm_cfg.get("base_url", "")).strip().rstrip("/")
            model = str(vlm_cfg.get("model", "")).strip()
            if base_url and model:
                visual_qa = OpenAICompatibleVisualQaModel(
                    base_url=base_url,
                    model=model,
                    api_key=api_key(vlm_cfg),
                )
            else:
                raise RuntimeError(f"Enabled visual QA model '{vlm_name}' is missing base_url/model.")
        else:
            raise RuntimeError(f"Enabled visual QA model '{vlm_name}' uses unsupported provider '{provider}'.")
    else:
        visual_qa = UnavailableVisualQaModel()

    reranker_entry = first_enabled_entry("rerankers") or first_enabled_entry("reranker")
    if reranker_entry:
        reranker_name, reranker_cfg = reranker_entry
        provider = str(reranker_cfg.get("provider", "")).lower()
        if provider in {"sentence_transformers", "cross_encoder"}:
            model = str(reranker_cfg.get("model") or reranker_cfg.get("checkpoint_uri") or "").strip()
            if model:
                reranker = CrossEncoderTextReranker(
                    model=model,
                    device=str(reranker_cfg.get("device", "")).strip() or None,
                    max_length=int(reranker_cfg.get("max_length", 0) or 0) or None,
                    batch_size=int(reranker_cfg.get("batch_size", 0) or 0) or None,
                    fallback_to_overlap=bool_value(reranker_cfg.get("fallback_to_overlap"), default=True),
                )
            else:
                raise RuntimeError(f"Enabled reranker '{reranker_name}' is missing model/checkpoint_uri.")
        else:
            raise RuntimeError(f"Enabled reranker '{reranker_name}' uses unsupported provider '{provider}'.")
    else:
        reranker = UnavailableTextReranker()

    return ModelRegistryService(
        embedder=embedder,
        query_expander=query_expander,
        visual_qa=visual_qa,
        reranker=reranker,
        registry=registry,
    )

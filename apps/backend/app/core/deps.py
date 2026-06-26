from __future__ import annotations

from functools import lru_cache

from app.adapters.object_storage.base import ObjectStorageClient
from app.adapters.vector_db.base import VectorSearchClient
from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_vector_client() -> VectorSearchClient:
    settings = get_settings()
    if settings.mock_mode:
        from app.adapters.vector_db.mock import InMemoryVectorSearchClient

        return InMemoryVectorSearchClient()
    from app.adapters.vector_db.milvus import MilvusVectorSearchClient

    return MilvusVectorSearchClient(uri=settings.milvus_uri, token=settings.milvus_token)


@lru_cache(maxsize=1)
def get_object_storage() -> ObjectStorageClient:
    settings = get_settings()
    if settings.mock_mode or settings.storage_provider == "mock":
        from app.adapters.object_storage.mock import InMemoryObjectStorageClient

        return InMemoryObjectStorageClient()
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
    from app.modules.models.service import ModelRegistryService

    settings = get_settings()
    if settings.mock_mode:
        embedder = MockEmbedder(dim=64)
        query_expander = MockQueryExpander()
        visual_qa = MockVisualQaModel()
    else:
        raise NotImplementedError("Non-mock model runtime not yet wired. Enable a model in model_registry.yaml.")

    return ModelRegistryService(embedder=embedder, query_expander=query_expander, visual_qa=visual_qa)

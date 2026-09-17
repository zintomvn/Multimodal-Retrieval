from __future__ import annotations

from pathlib import Path
import sys

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.deps import get_model_registry_service
from app.adapters.model_runtime.openai_compatible import OpenAICompatibleTextEmbedder
from app.modules.models.service import ModelRegistryService


def test_model_runtime_uses_openai_compatible_entries_when_enabled(monkeypatch) -> None:
    registry = {
        "embedders": {
            "embedder_main": {
                "task": "multimodal_embedding",
                "provider": "openai_compatible",
                "base_url": "http://localhost:8001/v1",
                "model": "test-embedding-model",
                "dim": 512,
                "l2_normalize": True,
                "enabled": True,
            }
        },
        "llm": {
            "llm_main": {
                "task": "query_expansion",
                "provider": "openai_compatible",
                "base_url": "http://localhost:8001/v1",
                "model": "test-query-model",
                "enabled": True,
            }
        },
        "vision_language": {
            "vlm_main": {
                "task": "visual_qa",
                "provider": "openai_compatible",
                "base_url": "http://localhost:8001/v1",
                "model": "test-vqa-model",
                "enabled": True,
            }
        },
    }
    monkeypatch.setattr(ModelRegistryService, "load_registry", staticmethod(lambda _: registry))
    get_model_registry_service.cache_clear()

    svc = get_model_registry_service()

    assert svc.embedder.__class__.__name__ == "OpenAICompatibleTextEmbedder"
    assert getattr(svc.embedder, "l2_normalize", False) is True
    assert svc.query_expander.__class__.__name__ == "OpenAICompatibleQueryExpander"
    assert svc.visual_qa.__class__.__name__ == "OpenAICompatibleVisualQaModel"


def test_model_runtime_uses_configured_base_url_environment_variable(monkeypatch) -> None:
    registry = {
        "embedders": {
            "qwen3_vl_embedding": {
                "task": "multimodal_embedding",
                "provider": "openai_compatible",
                "base_url": "http://localhost:8004/v1",
                "base_url_env": "TEST_QWEN3_VL_BASE_URL",
                "model": "Qwen/Qwen3-VL-Embedding-2B",
                "dim": 2048,
                "timeout_s": 60,
                "max_retries": 3,
                "enabled": True,
            }
        }
    }
    monkeypatch.setenv("TEST_QWEN3_VL_BASE_URL", "https://embedding.example/v1")
    monkeypatch.setattr(ModelRegistryService, "load_registry", staticmethod(lambda _: registry))
    get_model_registry_service.cache_clear()

    svc = get_model_registry_service()
    embedder = svc.embedders["qwen3_vl_embedding"]

    assert getattr(embedder, "base_url", None) == "https://embedding.example/v1"
    assert getattr(embedder, "timeout_s", None) == 60
    assert getattr(embedder, "max_retries", None) == 3


@pytest.mark.parametrize("vector", [[], [float("nan")], [float("inf")]])
def test_openai_compatible_embedder_rejects_invalid_vectors(monkeypatch, vector: list[float]) -> None:
    embedder = OpenAICompatibleTextEmbedder(
        base_url="https://embedding.example/v1",
        model="test-model",
    )
    monkeypatch.setattr(embedder, "_post", lambda *_args, **_kwargs: {"data": [{"index": 0, "embedding": vector}]})

    with pytest.raises(ValueError, match="empty or contains NaN/Inf"):
        embedder.embed_text("test")


def test_model_runtime_uses_siglip2_embedder_when_enabled(monkeypatch) -> None:
    registry = {
        "embedders": {
            "siglip2_main": {
                "task": "multimodal_embedding",
                "provider": "siglip2",
                "model": "google/siglip2-so400m-patch14-384",
                "dim": 1152,
                "l2_normalize": True,
                "local_files_only": True,
                "enabled": True,
            }
        }
    }
    monkeypatch.setattr(ModelRegistryService, "load_registry", staticmethod(lambda _: registry))
    get_model_registry_service.cache_clear()

    svc = get_model_registry_service()

    assert svc.embedder.__class__.__name__ == "Siglip2TextEmbedder"
    assert getattr(svc.embedder, "expected_dim", None) == 1152
    assert getattr(svc.embedder, "local_files_only", False) is True


def test_model_runtime_raises_when_enabled_embedder_is_invalid(monkeypatch) -> None:
    registry = {
        "embedders": {
            "broken_embedder": {
                "task": "multimodal_embedding",
                "provider": "openai_compatible",
                "base_url": "http://localhost:8001/v1",
                "enabled": True,
            }
        }
    }
    monkeypatch.setattr(ModelRegistryService, "load_registry", staticmethod(lambda _: registry))
    get_model_registry_service.cache_clear()

    with pytest.raises(RuntimeError, match="missing base_url/model"):
        get_model_registry_service()


def test_model_runtime_uses_non_model_fallbacks_when_optional_services_are_disabled(monkeypatch) -> None:
    registry = {}
    monkeypatch.setattr(ModelRegistryService, "load_registry", staticmethod(lambda _: registry))
    get_model_registry_service.cache_clear()

    svc = get_model_registry_service()

    assert svc.embedder.__class__.__name__ == "UnavailableTextImageEmbedder"
    assert svc.query_expander.__class__.__name__ == "PassthroughQueryExpander"
    assert svc.visual_qa.__class__.__name__ == "UnavailableVisualQaModel"

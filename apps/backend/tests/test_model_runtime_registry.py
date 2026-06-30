from __future__ import annotations

from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core import deps
from app.core.deps import get_model_registry_service
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
    monkeypatch.setattr(deps, "logger", deps.logger)
    get_model_registry_service.cache_clear()

    svc = get_model_registry_service()

    assert svc.embedder.__class__.__name__ == "OpenAICompatibleTextEmbedder"
    assert getattr(svc.embedder, "l2_normalize", False) is True
    assert svc.query_expander.__class__.__name__ == "OpenAICompatibleQueryExpander"
    assert svc.visual_qa.__class__.__name__ == "OpenAICompatibleVisualQaModel"


def test_model_runtime_falls_back_to_mock_when_enabled_embedder_is_invalid(monkeypatch) -> None:
    registry = {
        "embedders": {
            "broken_embedder": {
                "task": "multimodal_embedding",
                "provider": "openai_compatible",
                "base_url": "http://localhost:8001/v1",
                "enabled": True,
            }
        },
        "llm": {"mock_query": {"task": "query_expansion", "provider": "mock", "enabled": True}},
        "vision_language": {"mock_vqa": {"task": "visual_qa", "provider": "mock", "enabled": True}},
    }
    monkeypatch.setattr(ModelRegistryService, "load_registry", staticmethod(lambda _: registry))
    get_model_registry_service.cache_clear()

    svc = get_model_registry_service()

    assert svc.embedder.__class__.__name__ == "MockEmbedder"

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.adapters.model_runtime.mock import MockEmbedder, MockQueryExpander, MockVisualQaModel
from app.core.config import get_settings


class ModelRegistryService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.registry = self._load_yaml(self.settings.model_registry_path)
        self.embedder = MockEmbedder(dim=64)
        self.query_expander = MockQueryExpander()
        self.visual_qa = MockVisualQaModel()

    def _load_yaml(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}

    def list_models(self) -> dict[str, Any]:
        return self.registry

    def enabled_models(self) -> list[dict[str, Any]]:
        enabled: list[dict[str, Any]] = []
        for group, entries in self.registry.items():
            if not isinstance(entries, dict):
                continue
            for name, config in entries.items():
                if isinstance(config, dict) and config.get("enabled"):
                    enabled.append({"group": group, "name": name, **config})
        return enabled


model_registry_service = ModelRegistryService()

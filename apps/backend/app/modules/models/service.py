from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.adapters.model_runtime.base import QueryExpander, TextImageEmbedder, VisualQaModel
from app.core.config import get_settings


class ModelRegistryService:
    def __init__(
        self,
        embedder: TextImageEmbedder,
        query_expander: QueryExpander,
        visual_qa: VisualQaModel,
        registry: dict[str, Any] | None = None,
    ) -> None:
        self.settings = get_settings()
        self.registry = registry if registry is not None else self.load_registry(self.settings.model_registry_path)
        self.embedder = embedder
        self.query_expander = query_expander
        self.visual_qa = visual_qa

    @staticmethod
    def load_registry(path: Path) -> dict[str, Any]:
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

    def first_enabled(self, group: str) -> tuple[str, dict[str, Any]] | None:
        entries = self.registry.get(group)
        if not isinstance(entries, dict):
            return None
        for name, config in entries.items():
            if isinstance(config, dict) and config.get("enabled"):
                return str(name), config
        return None

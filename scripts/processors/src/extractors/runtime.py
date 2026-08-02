from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class ModelRuntime:
    """Shared runtime state for local model execution."""

    def __init__(self, model_config: dict[str, Any]) -> None:
        self.config = model_config
        raw_cache_dir = os.path.expandvars(str(model_config.get("cache_dir", "models/processor-cache")))
        self.cache_dir = Path(raw_cache_dir).expanduser().resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.device = self._resolve_device(str(model_config.get("device", "auto")))
        self.torch = self._load_torch()
        if bool(model_config.get("enable_tf32", True)) and self.device.startswith("cuda"):
            self.torch.backends.cuda.matmul.allow_tf32 = True
            self.torch.backends.cudnn.allow_tf32 = True
            self.torch.backends.cudnn.benchmark = True
            self.torch.set_float32_matmul_precision("high")

    def _load_torch(self):
        import torch

        return torch

    def _resolve_device(self, raw_device: str) -> str:
        if raw_device != "auto":
            return raw_device
        torch = self._load_torch()
        return "cuda" if torch.cuda.is_available() else "cpu"

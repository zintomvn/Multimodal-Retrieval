from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .runtime import ModelRuntime


DEFAULT_OPENCLIP_MODEL_NAME = "hf-hub:timm/PE-Core-bigG-14-448"
DEFAULT_OPENCLIP_PRETRAINED = ""


class OpenClipImageEmbedder:
    """OpenCLIP image embedding extractor."""

    def __init__(self, runtime: ModelRuntime, config: dict[str, Any]) -> None:
        self.runtime = runtime
        self.config = config
        self.model = None
        self.preprocess = None

    def warmup(self) -> None:
        """Download and initialize the OpenCLIP model."""
        self._ensure_model()

    def encode(self, image_paths: list[Path]) -> np.ndarray:
        """Encode image paths into normalized float32 embeddings."""
        self._ensure_model()
        torch = self.runtime.torch
        tensors = [self.preprocess(Image.open(path).convert("RGB")) for path in image_paths]
        batches: list[np.ndarray] = []
        batch_size = int(self.config.get("batch_size", 64))
        with torch.inference_mode():
            for start in range(0, len(tensors), batch_size):
                batch = torch.stack(tensors[start : start + batch_size]).to(self.runtime.device, non_blocking=True)
                with _maybe_autocast(torch, self.runtime.device, str(self.config.get("precision", "fp16"))):
                    emb = self.model.encode_image(batch, normalize=bool(self.config.get("l2_normalize", True)))
                batches.append(emb.detach().cpu().numpy().astype("float32"))
        return np.concatenate(batches, axis=0)

    def _ensure_model(self) -> None:
        if self.model is not None:
            return
        import open_clip

        model_name = str(self.config.get("model_name", DEFAULT_OPENCLIP_MODEL_NAME))
        create_kwargs: dict[str, object] = {
            "cache_dir": str(self.runtime.cache_dir),
            "device": self.runtime.device,
        }
        pretrained = str(self.config.get("pretrained", DEFAULT_OPENCLIP_PRETRAINED) or "").strip()
        if pretrained:
            create_kwargs["pretrained"] = pretrained
        model, _, preprocess = open_clip.create_model_and_transforms(model_name, **create_kwargs)
        self.model = model.eval()
        self.preprocess = preprocess


def _maybe_autocast(torch, device: str, precision: str):  # noqa: ANN001 - torch module type varies.
    if device.startswith("cuda") and precision in {"fp16", "bf16"}:
        dtype = torch.float16 if precision == "fp16" else torch.bfloat16
        return torch.autocast(device_type="cuda", dtype=dtype)
    return _NullContext()


class _NullContext:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN001 - context manager protocol.
        return False

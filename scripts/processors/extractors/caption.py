from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PIL import Image

from extractors.runtime import ModelRuntime


class Blip2Captioner:
    """Hugging Face BLIP/BLIP-2 image caption extractor."""

    def __init__(self, runtime: ModelRuntime, config: dict[str, Any]) -> None:
        self.runtime = runtime
        self.config = config
        self.processor = None
        self.model = None
        self.input_device = runtime.device

    def warmup(self) -> None:
        """Download and initialize the caption model."""
        self._ensure_model()

    def caption(self, image_paths: list[Path]) -> list[str]:
        """Generate captions for image paths."""
        self._ensure_model()
        torch = self.runtime.torch
        captions: list[str] = []
        batch_size = int(self.config.get("batch_size", 2))
        prompt = str(self.config.get("prompt", "a photo of"))
        generation = dict(self.config.get("generation") or {})
        for start in range(0, len(image_paths), batch_size):
            paths = image_paths[start : start + batch_size]
            images = [Image.open(path).convert("RGB") for path in paths]
            inputs = self.processor(
                images=images,
                text=[prompt] * len(images),
                return_tensors="pt",
                padding=True,
            ).to(self.input_device)
            with torch.inference_mode():
                generated_ids = self.model.generate(**inputs, use_cache=True, **generation)
            captions.extend(self.processor.batch_decode(generated_ids, skip_special_tokens=True))
        return [caption.strip() for caption in captions]

    def _ensure_model(self) -> None:
        if self.model is not None:
            return
        from transformers import AutoProcessor

        model_name = str(self.config.get("model_name", "Salesforce/blip2-opt-2.7b"))
        self.processor = AutoProcessor.from_pretrained(model_name, cache_dir=str(self.runtime.cache_dir))
        dtype = _torch_dtype(self.runtime.torch, str(self.config.get("dtype", "fp16")), self.runtime.device)
        load_kwargs: dict[str, Any] = {
            "torch_dtype": dtype,
            "cache_dir": str(self.runtime.cache_dir),
        }
        if bool(self.config.get("low_cpu_mem_usage", False)):
            load_kwargs["low_cpu_mem_usage"] = True

        device_map = str(self.config.get("device_map", "") or "").strip()
        if device_map:
            load_kwargs["device_map"] = device_map
            max_memory = _max_memory(self.config, self.runtime.device)
            if max_memory:
                load_kwargs["max_memory"] = max_memory
            offload_folder = _expanded_path(str(self.config.get("offload_folder", "") or ""))
            if offload_folder:
                offload_folder.mkdir(parents=True, exist_ok=True)
                load_kwargs["offload_folder"] = str(offload_folder)

        model_cls = _model_class(str(self.config.get("model_class", "Blip2ForConditionalGeneration")))
        self.model = model_cls.from_pretrained(model_name, **load_kwargs).eval()
        if not device_map:
            self.model = self.model.to(self.runtime.device)
        self.input_device = _input_device(self.model, self.runtime.device)


def _torch_dtype(torch, raw_dtype: str, device: str):  # noqa: ANN001 - torch module type varies.
    if not device.startswith("cuda"):
        return torch.float32
    if raw_dtype == "bf16":
        return torch.bfloat16
    if raw_dtype == "fp32":
        return torch.float32
    return torch.float16


def _model_class(class_name: str):  # noqa: ANN001 - Hugging Face classes share API.
    """Resolve the configured Transformers caption model class."""
    if class_name == "BlipForConditionalGeneration":
        from transformers import BlipForConditionalGeneration

        return BlipForConditionalGeneration
    if class_name == "Blip2ForConditionalGeneration":
        from transformers import Blip2ForConditionalGeneration

        return Blip2ForConditionalGeneration
    raise ValueError(f"Unsupported caption model_class: {class_name}")


def _expanded_path(raw_path: str) -> Path | None:
    """Expand env vars and user markers for optional local model paths."""
    if not raw_path:
        return None
    return Path(os.path.expandvars(raw_path)).expanduser().resolve()


def _max_memory(config: dict[str, Any], runtime_device: str) -> dict[Any, str]:
    """Build a Transformers max_memory map from YAML values."""
    max_memory: dict[Any, str] = {}
    if runtime_device.startswith("cuda"):
        gpu_memory = str(config.get("max_gpu_memory", "") or "").strip()
        if gpu_memory:
            max_memory[0] = gpu_memory
    cpu_memory = str(config.get("max_cpu_memory", "") or "").strip()
    if cpu_memory:
        max_memory["cpu"] = cpu_memory
    return max_memory


def _input_device(model: Any, fallback_device: str) -> str:
    """Choose where caption inputs should be placed for normal or device-mapped models."""
    device_map = getattr(model, "hf_device_map", None)
    if not device_map:
        return fallback_device
    for device in device_map.values():
        if isinstance(device, int):
            return f"cuda:{device}"
        if isinstance(device, str) and device not in {"cpu", "disk"}:
            return device
    return "cpu"

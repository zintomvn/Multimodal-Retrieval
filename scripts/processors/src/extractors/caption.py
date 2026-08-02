from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any
from urllib import request as urlrequest

from PIL import Image

from .runtime import ModelRuntime


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

    def caption(self, image_paths: list[Path], contexts: list[dict[str, Any]] | None = None) -> list[str]:
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


class OpenAiCompatibleVlmCaptioner:
    """Shot-context captioner for Qwen-VL, Gemini proxies, or other OpenAI-compatible VLM servers."""

    def __init__(self, runtime: ModelRuntime, config: dict[str, Any]) -> None:
        self.runtime = runtime
        self.config = config
        self.base_url = _env_expanded(str(config.get("base_url", "") or "")).rstrip("/")
        self.api_key = os.getenv(str(config.get("api_key_env", "VLM_API_KEY") or "VLM_API_KEY"), "")
        self.model_name = str(config.get("model_name") or config.get("model") or "Qwen/Qwen2.5-VL-3B-Instruct")

    def warmup(self) -> None:
        """Validate endpoint configuration without making a remote request."""
        if not self.base_url:
            raise RuntimeError("Set caption.base_url or VLM_BASE_URL for openai_compatible_vlm captioning.")

    def caption(self, image_paths: list[Path], contexts: list[dict[str, Any]] | None = None) -> list[str]:
        """Generate one shot-aware caption for each image via a VLM chat-completions endpoint."""
        self.warmup()
        contexts = contexts or [{} for _ in image_paths]
        return [self._caption_one(path, context) for path, context in zip(image_paths, contexts)]

    def _caption_one(self, image_path: Path, context: dict[str, Any]) -> str:
        endpoint = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": str(self.config.get("system_prompt") or "You caption video keyframes for retrieval.")},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self._prompt(context)},
                        {"type": "image_url", "image_url": {"url": _data_uri(image_path)}},
                    ],
                },
            ],
            "temperature": float(self.config.get("temperature", 0.1)),
            "max_tokens": int(self.config.get("max_tokens", 96)),
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urlrequest.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urlrequest.urlopen(req, timeout=float(self.config.get("request_timeout", 120))) as response:  # noqa: S310 - user-configured endpoint.
            raw = json.loads(response.read().decode("utf-8"))
        return _extract_chat_content(raw).strip()

    def _prompt(self, context: dict[str, Any]) -> str:
        template = str(
            self.config.get("prompt")
            or "Describe this video keyframe for retrieval. Include visible people, objects, actions, scene text, place, and temporal context. Answer in Vietnamese when useful."
        )
        context_lines = [
            f"video_id={context.get('video_id', '')}",
            f"shot_id={context.get('shot_id', '')}",
            f"frame_idx={context.get('frame_idx', '')}",
            f"frame_seconds={context.get('frame_seconds', '')}",
            f"frame_type={context.get('frame_type', '')}",
        ]
        return template + "\n\nShot context:\n" + "\n".join(line for line in context_lines if not line.endswith("="))


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


def _env_expanded(raw: str) -> str:
    value = os.path.expandvars(raw).strip()
    if value == raw and (
        (value.startswith("${") and value.endswith("}"))
        or (value.startswith("$") and "/" not in value)
        or (value.startswith("%") and value.endswith("%"))
    ):
        return ""
    return value


def _data_uri(image_path: Path) -> str:
    suffix = image_path.suffix.lower().lstrip(".") or "jpeg"
    mime = "image/jpeg" if suffix in {"jpg", "jpeg"} else f"image/{suffix}"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _extract_chat_content(raw: dict[str, Any]) -> str:
    choices = raw.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else {}
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return " ".join(parts)
    return str(content or "")

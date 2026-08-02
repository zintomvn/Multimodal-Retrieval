from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from .caption import Blip2Captioner, OpenAiCompatibleVlmCaptioner
from .embedding import OpenClipImageEmbedder
from .objects import YoloObjectDetector
from .ocr import EasyOcrExtractor, VietOcrExtractor
from .runtime import ModelRuntime


class FrameFeatureExtractor:
    """Coordinate enabled feature extractors for frame batches."""

    def __init__(self, model_config: dict[str, Any]) -> None:
        self.config = model_config
        self.runtime = ModelRuntime(model_config)
        self.embedder = self._build_embedder()
        self.captioner = self._build_captioner()
        self.ocr = self._build_ocr()
        self.object_detector = self._build_object_detector()

    def warmup(self) -> None:
        """Initialize all enabled models so downloads happen before extraction."""
        for component in (self.embedder, self.captioner, self.ocr, self.object_detector):
            if component is not None:
                component.warmup()

    def process_batch(
        self,
        image_paths: list[Path],
        contexts: list[dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], np.ndarray | None, dict[str, float]]:
        """Process one batch and return annotation rows, embeddings and timings."""
        started = time.perf_counter()
        contexts = contexts or [{} for _ in image_paths]
        timings: dict[str, float] = {}
        captions = ["" for _ in image_paths]
        texts_by_image = [[] for _ in image_paths]
        obj_results = [{"detections": [], "objects": [], "object_counts": {}} for _ in image_paths]
        embeddings: np.ndarray | None = None

        if self.embedder is not None:
            t0 = time.perf_counter()
            embeddings = self.embedder.encode(image_paths)
            timings["embedding"] = time.perf_counter() - t0
        if self.captioner is not None:
            t0 = time.perf_counter()
            captions = self.captioner.caption(image_paths, contexts=contexts)
            timings["caption"] = time.perf_counter() - t0
        if self.ocr is not None:
            t0 = time.perf_counter()
            texts_by_image = self.ocr.extract(image_paths)
            timings["ocr"] = time.perf_counter() - t0
        if self.object_detector is not None:
            t0 = time.perf_counter()
            obj_results = self.object_detector.detect(image_paths)
            timings["objects"] = time.perf_counter() - t0

        records = []
        for path, caption, texts, objects in zip(image_paths, captions, texts_by_image, obj_results):
            records.append(
                {
                    "image_path": str(path),
                    "image_name": path.name,
                    "caption": caption,
                    "texts": texts,
                    "objects": objects["objects"],
                    "object_counts": objects["object_counts"],
                    "detections": objects["detections"],
                }
            )
        timings["total"] = time.perf_counter() - started
        return records, embeddings, timings

    def _build_embedder(self) -> OpenClipImageEmbedder | None:
        config = dict(self.config.get("embedding") or {})
        if not config.get("enabled", False):
            return None
        if str(config.get("provider", "openclip")) != "openclip":
            raise ValueError(f"Unsupported embedding provider: {config.get('provider')}")
        return OpenClipImageEmbedder(self.runtime, config)

    def _build_captioner(self) -> Blip2Captioner | OpenAiCompatibleVlmCaptioner | None:
        config = dict(self.config.get("caption") or {})
        if not config.get("enabled", False):
            return None
        provider = str(config.get("provider", "blip")).lower()
        if provider in {"blip", "blip2"}:
            return Blip2Captioner(self.runtime, config)
        if provider in {"openai_compatible_vlm", "qwen_vl", "gemini_proxy"}:
            return OpenAiCompatibleVlmCaptioner(self.runtime, config)
        raise ValueError(f"Unsupported caption provider: {config.get('provider')}")

    def _build_ocr(self) -> VietOcrExtractor | EasyOcrExtractor | None:
        config = dict(self.config.get("ocr") or {})
        if not config.get("enabled", False):
            return None
        provider = str(config.get("provider", "vietocr")).lower()
        if provider in {"vietocr", "paddle_vietocr"}:
            return VietOcrExtractor(self.runtime, config)
        if provider in {"easyocr", "craft_easyocr"}:
            return EasyOcrExtractor(self.runtime, config)
        raise ValueError(f"Unsupported OCR provider: {config.get('provider')}")

    def _build_object_detector(self) -> YoloObjectDetector | None:
        config = dict(self.config.get("objects") or {})
        if not config.get("enabled", False):
            return None
        if str(config.get("provider", "yolo")) != "yolo":
            raise ValueError(f"Unsupported object provider: {config.get('provider')}")
        return YoloObjectDetector(self.runtime, config)

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .runtime import ModelRuntime


class VietOcrExtractor:
    """PaddleOCR text detector plus VietOCR recognizer."""

    def __init__(self, runtime: ModelRuntime, config: dict[str, Any]) -> None:
        self.runtime = runtime
        self.config = config
        self.detector = None
        self.predictor = None

    def warmup(self) -> None:
        """Download and initialize OCR models."""
        self._ensure_model()

    def extract(self, image_paths: list[Path]) -> list[list[str]]:
        """Extract visible text lines for each image path."""
        self._ensure_model()
        import cv2

        outputs: list[list[str]] = []
        for path in image_paths:
            img = cv2.imread(str(path))
            if img is None:
                outputs.append([])
                continue
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            detections = list(self.detector.predict(str(path)))
            polys = _extract_polys(detections[0] if detections else {})
            texts: list[str] = []
            for box in _merge_boxes_by_line(
                polys,
                y_thresh=int(self.config.get("line_y_threshold", 35)),
                x_gap_thresh=int(self.config.get("line_x_gap_threshold", 180)),
            ):
                crop = _crop_xyxy(rgb, box, pad=int(self.config.get("crop_padding", 12)))
                if crop is None or crop.shape[0] < 5 or crop.shape[1] < 5:
                    continue
                text = self.predictor.predict(Image.fromarray(crop)).strip()
                if text:
                    texts.append(text)
            outputs.append(texts)
        return outputs

    def _ensure_model(self) -> None:
        if self.detector is not None:
            return
        if sys.version_info >= (3, 13):
            raise RuntimeError(
                "OCR profile is not supported on Python 3.13/3.14 yet. "
                "PaddleOCR/PaddleX currently depends on Pillow 10.2.x, which has no Windows cp313/cp314 wheel. "
                "Create a Python 3.11 or 3.12 venv and install scripts/processors/requirements-ocr.txt there."
            )
        from paddleocr import TextDetection
        from vietocr.tool.config import Cfg
        from vietocr.tool.predictor import Predictor

        config = Cfg.load_config_from_name(str(self.config.get("recognizer_model", "vgg_seq2seq")))
        config["cnn"]["pretrained"] = False
        config["device"] = self.runtime.device
        config["weights"] = str(self._recognizer_weights(config))
        self.predictor = Predictor(config)
        self.detector = TextDetection(
            model_name=str(self.config.get("detector_model", "PP-OCRv5_mobile_det")),
            device="gpu" if self.runtime.device.startswith("cuda") else "cpu",
            limit_side_len=int(self.config.get("detector_limit_side_len", 960)),
            limit_type=str(self.config.get("detector_limit_type", "max")),
        )

    def _recognizer_weights(self, vietocr_config: dict[str, Any]) -> Path:
        """Return a stable local VietOCR weight path, downloading it when needed."""
        configured_path = str(self.config.get("recognizer_weights_path", "") or "").strip()
        if configured_path:
            weights_path = Path(os.path.expandvars(configured_path)).expanduser().resolve()
        else:
            weights_path = self.runtime.cache_dir / "vietocr" / f"{self.config.get('recognizer_model', 'vgg_seq2seq')}.pth"
        weights_path.parent.mkdir(parents=True, exist_ok=True)
        if weights_path.exists() and weights_path.stat().st_size > 0:
            return weights_path

        weights_url = str(self.config.get("recognizer_weights_url", "") or vietocr_config.get("weights", "")).strip()
        if not weights_url.startswith("http"):
            source_path = Path(os.path.expandvars(weights_url)).expanduser().resolve()
            if not source_path.exists():
                raise FileNotFoundError(f"VietOCR weights not found: {source_path}")
            return source_path

        _download_with_progress(weights_url, weights_path)
        return weights_path


class EasyOcrExtractor:
    """EasyOCR extractor using CRAFT detection and Vietnamese-capable recognition."""

    def __init__(self, runtime: ModelRuntime, config: dict[str, Any]) -> None:
        self.runtime = runtime
        self.config = config
        self.reader = None

    def warmup(self) -> None:
        """Download and initialize EasyOCR models."""
        self._ensure_model()

    def extract(self, image_paths: list[Path]) -> list[list[str]]:
        """Extract visible text lines for each image path."""
        self._ensure_model()
        outputs: list[list[str]] = []
        detail = int(self.config.get("detail", 1))
        paragraph = bool(self.config.get("paragraph", False))
        min_confidence = float(self.config.get("min_confidence", 0.2))
        for path in image_paths:
            results = self.reader.readtext(str(path), detail=detail, paragraph=paragraph)
            texts: list[str] = []
            for result in results:
                if detail == 0:
                    text = str(result).strip()
                    if text:
                        texts.append(text)
                    continue
                if not isinstance(result, (list, tuple)) or len(result) < 2:
                    continue
                text = str(result[1]).strip()
                confidence = float(result[2]) if len(result) > 2 else 1.0
                if text and confidence >= min_confidence:
                    texts.append(text)
            outputs.append(texts)
        return outputs

    def _ensure_model(self) -> None:
        if self.reader is not None:
            return
        import easyocr

        languages = self.config.get("languages") or ["vi", "en"]
        gpu = self.runtime.device.startswith("cuda")
        self.reader = easyocr.Reader(
            list(languages),
            gpu=gpu,
            model_storage_directory=str(self.runtime.cache_dir / "easyocr"),
            download_enabled=bool(self.config.get("download_enabled", True)),
        )


def _extract_polys(det_result: object) -> list[np.ndarray]:
    boxes = det_result.get("dt_polys", []) if isinstance(det_result, dict) else getattr(det_result, "dt_polys", [])
    polys = []
    for box in boxes:
        arr = np.asarray(box, dtype=np.float32)
        if arr.shape == (4, 2):
            polys.append(arr)
    return polys


def _merge_boxes_by_line(polys: list[np.ndarray], y_thresh: int, x_gap_thresh: int) -> list[list[float]]:
    boxes = [_poly_to_xyxy(poly) for poly in polys]
    boxes = sorted(boxes, key=lambda box: (box[1], box[0]))
    lines: list[dict[str, list[float]]] = []
    for box in boxes:
        x1, y1, x2, y2 = box
        cy = (y1 + y2) / 2
        for line in lines:
            lx1, ly1, lx2, ly2 = line["box"]
            lcy = (ly1 + ly2) / 2
            if abs(cy - lcy) < y_thresh and x1 - lx2 < x_gap_thresh:
                line["box"] = [min(lx1, x1), min(ly1, y1), max(lx2, x2), max(ly2, y2)]
                break
        else:
            lines.append({"box": box})
    return [line["box"] for line in lines]


def _poly_to_xyxy(poly: np.ndarray) -> list[float]:
    return [float(np.min(poly[:, 0])), float(np.min(poly[:, 1])), float(np.max(poly[:, 0])), float(np.max(poly[:, 1]))]


def _crop_xyxy(img_rgb: np.ndarray, box: list[float], pad: int) -> np.ndarray:
    h, w = img_rgb.shape[:2]
    x1, y1, x2, y2 = map(int, box)
    x1 = max(0, x1 - pad)
    y1 = max(0, y1 - pad)
    x2 = min(w, x2 + pad)
    y2 = min(h, y2 + pad)
    return img_rgb[y1:y2, x1:x2]


def _download_with_progress(url: str, destination: Path) -> None:
    """Download a file with a tqdm progress bar and atomic replace."""
    from tqdm import tqdm

    tmp_destination = destination.with_suffix(destination.suffix + ".tmp")
    with tqdm(unit="B", unit_scale=True, unit_divisor=1024, desc=f"Downloading {destination.name}") as progress:
        def reporthook(blocks: int, block_size: int, total_size: int) -> None:
            if total_size > 0:
                progress.total = total_size
            downloaded = blocks * block_size
            progress.update(max(0, downloaded - progress.n))

        urllib.request.urlretrieve(url, tmp_destination, reporthook)
    tmp_destination.replace(destination)

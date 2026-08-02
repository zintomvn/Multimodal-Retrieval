from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .runtime import ModelRuntime


class YoloObjectDetector:
    """Ultralytics YOLO object detector."""

    def __init__(self, runtime: ModelRuntime, config: dict[str, Any]) -> None:
        self.runtime = runtime
        self.config = config
        self.model = None

    def warmup(self) -> None:
        """Download and initialize the YOLO model."""
        self._ensure_model()

    def detect(self, image_paths: list[Path]) -> list[dict[str, Any]]:
        """Detect objects for each image path."""
        self._ensure_model()
        records: list[dict[str, Any]] = []
        batch_size = int(self.config.get("batch_size", 8))
        image_size = int(self.config.get("image_size", 640))
        confidence = float(self.config.get("confidence", 0.25))
        for start in range(0, len(image_paths), batch_size):
            paths = [str(path) for path in image_paths[start : start + batch_size]]
            results = self.model(paths, batch=len(paths), imgsz=image_size, conf=confidence, verbose=False)
            for result in results:
                height, width = _result_shape(result)
                detections = [
                    {
                        "label": self.model.names[int(box.cls.item())],
                        "confidence": round(float(box.conf.item()), 4),
                        "bbox_xyxy_px": _round_box(box.xyxy[0].detach().cpu().tolist()),
                        "bbox_xyxy_norm": _normalize_box(box.xyxy[0].detach().cpu().tolist(), width, height),
                    }
                    for box in result.boxes
                ]
                labels = [item["label"] for item in detections]
                records.append(
                    {
                        "detections": detections,
                        "objects": sorted(set(labels)),
                        "object_counts": dict(Counter(labels)),
                    }
                )
        return records

    def _ensure_model(self) -> None:
        if self.model is not None:
            return
        from ultralytics import YOLO

        model_name = str(self.config.get("model_name", "yolo12n.pt"))
        model_path = Path(model_name)
        if model_path.parent == Path(".") and model_path.suffix == ".pt":
            model_path = self.runtime.cache_dir / model_path.name
        self.model = YOLO(str(model_path))
        try:
            self.model.fuse()
        except Exception:
            pass


def _result_shape(result: Any) -> tuple[int, int]:
    """Return result image shape as (height, width)."""
    shape = getattr(result, "orig_shape", None)
    if isinstance(shape, tuple) and len(shape) >= 2:
        return int(shape[0]), int(shape[1])
    return 1, 1


def _round_box(values: list[float]) -> list[float]:
    """Round a YOLO xyxy box for compact JSON output."""
    return [round(float(value), 2) for value in values[:4]]


def _normalize_box(values: list[float], width: int, height: int) -> list[float]:
    """Normalize a YOLO xyxy box into [0, 1] coordinates."""
    safe_width = max(int(width), 1)
    safe_height = max(int(height), 1)
    x1, y1, x2, y2 = [float(value) for value in values[:4]]
    return [
        round(max(0.0, min(1.0, x1 / safe_width)), 6),
        round(max(0.0, min(1.0, y1 / safe_height)), 6),
        round(max(0.0, min(1.0, x2 / safe_width)), 6),
        round(max(0.0, min(1.0, y2 / safe_height)), 6),
    ]

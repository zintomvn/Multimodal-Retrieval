from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from extractors.runtime import ModelRuntime


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
                detections = [
                    {
                        "label": self.model.names[int(box.cls.item())],
                        "confidence": round(float(box.conf.item()), 4),
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

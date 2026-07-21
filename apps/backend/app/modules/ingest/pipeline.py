from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class PipelineContext:
    dataset_id: str
    dataset_root: str
    model_versions: dict[str, str] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    stats: dict[str, int | float | str] = field(default_factory=dict)


class PipelineStage(Protocol):
    name: str

    def run(self, context: PipelineContext) -> PipelineContext:
        ...


DEFAULT_STAGE_ORDER = [
    "dataset_scan",
    "shot_detection",
    "keyframe_extraction",
    "frame_dedup",
    "ocr",
    "asr",
    "object_detection",
    "captioning",
    "scene_classification",
    "embedding",
    "event_segmentation",
    "milvus_index",
    "text_index",
]

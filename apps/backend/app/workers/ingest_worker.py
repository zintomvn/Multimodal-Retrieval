from __future__ import annotations

from app.modules.ingest.pipeline import PipelineContext, build_mock_pipeline


def run_mock_ingest(dataset_id: str, dataset_root: str) -> PipelineContext:
    context = PipelineContext(dataset_id=dataset_id, dataset_root=dataset_root)
    for stage in build_mock_pipeline():
        context = stage.run(context)
    return context

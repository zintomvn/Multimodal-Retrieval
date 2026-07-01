from __future__ import annotations

import logging
from pathlib import Path

import yaml

from app.db.models import Job
from app.db.session import SessionLocal
from app.modules.ingest.pipeline import PipelineContext, PipelineStage
from app.modules.ingest.stages.dataset_scan import DatasetScanStage
from app.modules.ingest.stages.embedding_stage import EmbeddingStage
from app.modules.ingest.stages.gcs_upload import GCSUploadStage
from app.modules.ingest.stages.milvus_index_stage import MilvusIndexStage

logger = logging.getLogger(__name__)


def build_real_pipeline() -> list[PipelineStage]:
    return [
        DatasetScanStage(),   # 1. scan keyframes dir → create DB records (local image_uri)
        EmbeddingStage(),     # 2. load .npy features or run CLIP on local images
        GCSUploadStage(),     # 3. upload images to GCS, update image_uri to GCS key
        MilvusIndexStage(),   # 4. index embedding vectors into Milvus collection
    ]


def run_ingest_job(job_id: str, dataset_id: str, manifest_path: str) -> None:
    """Execute the real ingestion pipeline in the background."""
    if not manifest_path or not Path(manifest_path).exists():
        _update_job(job_id, "FAILED", 0.0, f"Manifest not found: {manifest_path}")
        return

    with open(manifest_path) as f:
        manifest = yaml.safe_load(f)

    from app.core.config import get_settings
    settings = get_settings()
    dataset_root = manifest.get("root") or str(settings.data_root)

    context = PipelineContext(
        dataset_id=dataset_id,
        dataset_root=dataset_root,
        artifacts={"manifest_path": manifest_path},
    )

    stages = build_real_pipeline()
    n = len(stages)
    _update_job(job_id, "RUNNING", 0.0, "Pipeline started")

    for i, stage in enumerate(stages):
        try:
            logger.info("[%d/%d] %s …", i + 1, n, stage.name)
            _update_job(job_id, "RUNNING", i / n, f"Running {stage.name} …")
            context = stage.run(context)
        except Exception as exc:
            logger.exception("Stage %s failed", stage.name)
            _update_job(job_id, "FAILED", i / n, f"{stage.name} failed: {exc}")
            return

    stats_summary = ", ".join(f"{k}={v}" for k, v in context.stats.items())
    _update_job(job_id, "COMPLETED", 1.0, f"Done — {stats_summary}")
    logger.info("Ingest pipeline completed: %s", context.stats)


def _update_job(job_id: str, status: str, progress: float, message: str) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job:
            job.status = status
            job.progress = round(progress, 3)
            job.message = message
            db.commit()

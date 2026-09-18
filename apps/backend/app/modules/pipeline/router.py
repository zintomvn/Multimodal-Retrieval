from __future__ import annotations

import logging
import os

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_object_storage, get_vector_client
from app.db.models import Job
from app.db.session import SessionLocal, get_db
from app.modules.pipeline.schemas import PipelineJobRequest, PipelineJobResponse
from app.modules.pipeline.service import PipelineService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


def _update_job(job_id: str, status: str, progress: float, message: str) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job:
            job.status = status
            job.progress = progress
            job.message = message
            db.commit()


def _run_pipeline_job(
    job_id: str,
    request_data: dict,
) -> None:
    """Background worker for the video pipeline."""
    try:
        request = PipelineJobRequest(**request_data)
        with SessionLocal() as db:
            object_storage = get_object_storage()
            vector_client = get_vector_client()
            # text_client deferred to Phase 2 annotation stages —
            # not needed for embedding-only pipeline.

            job = db.get(Job, job_id)
            if not job:
                logger.error("Job %s not found", job_id)
                return

            job.status = "RUNNING"
            job.message = "Pipeline started."
            db.commit()

            service = PipelineService(
                db=db,
                vector_client=vector_client,
                text_client=None,
                object_storage=object_storage,
            )
            service.run(job=job, request=request)
    except Exception as exc:
        logger.error("Pipeline job %s failed: %s", job_id, exc, exc_info=True)
        _update_job(job_id, "FAILED", 0.0, f"Pipeline failed: {exc}")


@router.post("/jobs", response_model=PipelineJobResponse)
def create_pipeline_job(
    request: PipelineJobRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> PipelineJobResponse:
    job = Job(
        kind="VIDEO_PIPELINE",
        status="PENDING",
        progress=0.0,
        message="Pipeline queued.",
        payload=request.model_dump(mode="json"),
    )
    if os.getenv('JOB_EXECUTION_MODE', 'background') == 'worker':
        from app.modules.jobs.durable import enqueue
        enqueue(db, job)
        return PipelineJobResponse(job_id=job.id, status=job.status, message=job.message or '')
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(_run_pipeline_job, job.id, request.model_dump(mode="json"))

    return PipelineJobResponse(
        job_id=job.id,
        status=job.status,
        message=job.message or "",
    )

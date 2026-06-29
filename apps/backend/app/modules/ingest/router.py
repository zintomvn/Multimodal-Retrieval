from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import get_object_storage, get_text_client, get_vector_client
from app.db.models import Job
from app.db.session import get_db
from app.modules.ingest.schemas import IngestJobRequest, IngestJobResponse
from app.modules.ingest.service import DemoIngestService

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.post("/jobs")
def create_ingest_job(
    request: IngestJobRequest,
    db: Session = Depends(get_db),
) -> IngestJobResponse:
    settings = get_settings()
    job = Job(
        kind="INGEST",
        status="RUNNING",
        progress=0.0,
        message="Ingestion started.",
        payload=request.model_dump(mode="json"),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    if request.mode == "mock":
        job.status = "COMPLETED"
        job.progress = 1.0
        job.message = "Mock ingest completed."
        job.payload = {
            **request.model_dump(mode="json"),
            "report": {
                "mode": "mock",
                "dataset_root": str(settings.data_root),
                "targets": request.targets,
                "dry_run": request.dry_run,
            },
        }
        db.commit()
        db.refresh(job)
        return IngestJobResponse(job_id=job.id, status=job.status, message=job.message or "", report=job.payload.get("report", {}))

    selected_targets = set(request.targets)
    object_storage = get_object_storage() if {"pg", "media"} & selected_targets else None
    vector_client = get_vector_client() if "milvus" in selected_targets else None
    text_client = get_text_client() if "es" in selected_targets else None

    service = DemoIngestService(
        db=db,
        vector_client=vector_client,
        text_client=text_client,
        object_storage=object_storage,
    )
    try:
        report = service.run(request)
        job.status = "COMPLETED"
        job.progress = 1.0
        job.message = "Demo ingestion completed."
        job.payload = {
            **request.model_dump(mode="json"),
            "report": report,
        }
    except Exception as exc:  # noqa: BLE001 - propagate error through job payload first.
        db.rollback()
        job = db.query(Job).filter(Job.id == job.id).one()
        job.status = "FAILED"
        job.progress = 0.0
        job.message = f"Ingestion failed: {exc}"
        job.payload = {
            **request.model_dump(mode="json"),
            "error": str(exc),
        }
        db.commit()
        db.refresh(job)
        return IngestJobResponse(job_id=job.id, status=job.status, message=job.message or "", report={})

    db.commit()
    db.refresh(job)
    return IngestJobResponse(
        job_id=job.id,
        status=job.status,
        message=job.message or "",
        report=job.payload.get("report", {}),
    )

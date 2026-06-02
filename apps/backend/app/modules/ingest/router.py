from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.models import Job
from app.db.session import get_db

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


class IngestJobRequest(BaseModel):
    dataset_id: str | None = None
    manifest_path: str | None = None
    mode: str = "mock"


@router.post("/jobs")
def create_ingest_job(request: IngestJobRequest, db: Session = Depends(get_db)) -> dict:
    job = Job(
        kind="INGEST",
        status="COMPLETED" if request.mode == "mock" else "PENDING",
        progress=1.0 if request.mode == "mock" else 0.0,
        message="Mock dataset is already seeded." if request.mode == "mock" else "Job queued for worker.",
        payload=request.model_dump(mode="json"),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return {"job_id": job.id, "status": job.status, "message": job.message}

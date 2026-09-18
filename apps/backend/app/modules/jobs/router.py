from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import Job
from app.db.session import get_db

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post('/{job_id}/retry')
def retry_job(job_id: str, db: Session = Depends(get_db)):
    from app.modules.jobs.durable import JobLease
    lease = db.get(JobLease, job_id)
    job = db.get(Job, job_id)
    if not lease or not job:
        raise HTTPException(404, 'Durable job not found')
    changed = db.query(JobLease).filter(JobLease.job_id == job_id, JobLease.state == 'FAILED').update({
        JobLease.state: 'QUEUED', JobLease.owner: None, JobLease.expires: 0,
        JobLease.available_at: 0, JobLease.attempts: 0}, synchronize_session=False)
    if not changed:
        db.rollback()
        raise HTTPException(409, 'Only failed durable jobs can be retried')
    job.status = 'PENDING'; job.message = 'Retry queued; completed video checkpoints retained'
    db.commit()
    return {'id': job_id, 'status': 'PENDING'}


@router.get("/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)) -> dict:
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "payload": job.payload,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }

"""Opt-in durable queue with database leases and bounded at-least-once retry.

Provider operations must be idempotent. A timeout does not undo remote writes.
Initialize the additive table with `python -m app.modules.jobs.worker --init`.
"""
from time import time
from uuid import uuid4

from sqlalchemy import Column, Float, Integer, String, or_, update
from app.db.models import Base, Job


class JobLease(Base):
    __tablename__ = 'job_leases'
    job_id = Column(String(36), primary_key=True)
    state = Column(String(16), nullable=False, default='QUEUED', index=True)
    owner = Column(String(36))
    expires = Column(Float, nullable=False, default=0)
    available_at = Column(Float, nullable=False, default=0)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)


def enqueue(db, job):
    db.add(job)
    db.flush()
    db.add(JobLease(job_id=job.id))
    db.commit()


def claim(db, lease_seconds=60):
    now = time()
    eligible = (JobLease.state.in_(['QUEUED', 'RUNNING']), JobLease.available_at <= now,
                JobLease.expires < now, JobLease.attempts < JobLease.max_attempts)
    ids = [row[0] for row in db.query(JobLease.job_id).filter(*eligible).order_by(JobLease.available_at, JobLease.job_id).limit(16)]
    for job_id in ids:
        owner = str(uuid4())
        changed = db.execute(update(JobLease).where(JobLease.job_id == job_id, *eligible).values(
            state='RUNNING', owner=owner, expires=now+lease_seconds, attempts=JobLease.attempts+1)).rowcount
        db.commit()
        if changed:
            job = db.get(Job, job_id)
            if not job:
                finish(db, job_id, owner, False)
                continue
            job.status = 'RUNNING'; job.message = 'Durable worker running'
            db.commit()
            return job_id, owner
    # Final-attempt crashes must not remain RUNNING forever.
    expired = db.query(JobLease).filter(JobLease.state == 'RUNNING', JobLease.expires < now,
        JobLease.attempts >= JobLease.max_attempts).all()
    for lease in expired:
        lease.state = 'FAILED'
        job = db.get(Job, lease.job_id)
        if job:
            job.status = 'FAILED'; job.message = 'Worker lease expired; retry limit reached'
    db.commit()
    return None


def heartbeat(db, job_id, owner, seconds=60):
    changed = db.execute(update(JobLease).where(JobLease.job_id == job_id, JobLease.owner == owner,
        JobLease.state == 'RUNNING', JobLease.expires >= time()).values(expires=time()+seconds)).rowcount
    db.commit()
    return bool(changed)


def finish(db, job_id, owner, success):
    lease = db.query(JobLease).filter(JobLease.job_id == job_id, JobLease.owner == owner,
        JobLease.state == 'RUNNING', JobLease.expires >= time()).first()
    if not lease:
        return False
    retry = not success and lease.attempts < lease.max_attempts
    lease.state = 'COMPLETED' if success else 'QUEUED' if retry else 'FAILED'
    lease.available_at = time() + min(300, 2 ** lease.attempts) if retry else 0
    lease.expires = 0; lease.owner = None
    job = db.get(Job, job_id)
    if job:
        job.status = 'PENDING' if retry else lease.state
        if retry:
            job.message = f'Retrying after attempt {lease.attempts}/{lease.max_attempts}'
    db.commit()
    return True

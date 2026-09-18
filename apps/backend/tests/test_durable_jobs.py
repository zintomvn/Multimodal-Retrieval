from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.models import Base, Job
from app.modules.jobs.durable import JobLease, enqueue, claim, heartbeat, finish


def test_exclusive_claim_retry_backoff_and_crash_recovery(tmp_path):
    factory = sessionmaker(bind=create_engine('sqlite:///'+str(tmp_path/'jobs.db')))
    Base.metadata.create_all(factory.kw['bind'])
    with factory() as db:
        job = Job(kind='VIDEO_PIPELINE', payload={})
        enqueue(db, job)
        job_id, owner = claim(db)
    with factory() as other:
        assert claim(other) is None
        assert not heartbeat(other, job_id, 'wrong-owner')
        assert heartbeat(other, job_id, owner)
        assert not finish(other, job_id, 'wrong-owner', True)
        assert finish(other, job_id, owner, False)
        assert claim(other) is None  # bounded retry backoff
        lease = other.get(JobLease, job_id)
        lease.available_at = 0; other.commit()
        _, second_owner = claim(other)
        assert second_owner != owner
        lease = other.get(JobLease, job_id)
        lease.expires = 0; other.commit()  # simulate process death
    with factory() as recovery:
        _, third_owner = claim(recovery)
        assert third_owner != second_owner
        assert not finish(recovery, job_id, second_owner, True)
        assert finish(recovery, job_id, third_owner, False)
        assert recovery.get(Job, job_id).status == 'FAILED'
        assert claim(recovery) is None


def test_crash_on_last_attempt_becomes_failed(tmp_path):
    factory = sessionmaker(bind=create_engine('sqlite:///'+str(tmp_path/'last.db')))
    Base.metadata.create_all(factory.kw['bind'])
    with factory() as db:
        job = Job(kind='VIDEO_PIPELINE', payload={}); enqueue(db, job)
        lease = db.get(JobLease, job.id); lease.max_attempts = 1; db.commit()
        claim(db)
        lease = db.get(JobLease, job.id); lease.expires = 0; db.commit()
        assert claim(db) is None
        assert db.get(Job, job.id).status == 'FAILED'

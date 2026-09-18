"""Run separately from the API: python -m app.modules.jobs.worker [--init|--once]."""
import argparse
import os
from threading import Event, Thread
from time import sleep, monotonic

from app.db.models import Job
from app.db.session import SessionLocal, engine
from app.modules.jobs.durable import JobLease, claim, heartbeat, finish


def dispatch(job_id, kind, payload):
    if kind == 'VIDEO_PIPELINE':
        from app.modules.pipeline.router import _run_pipeline_job
        _run_pipeline_job(job_id, payload)
    elif kind == 'GCS_UPLOAD':
        from app.modules.ingest.router import _run_gcs_upload
        _run_gcs_upload(job_id, payload['source_path'], payload['source_type'], payload.get('dataset_id'))
    elif kind == 'MILVUS_UPLOAD':
        from app.modules.ingest.router import _run_milvus_upload
        _run_milvus_upload(job_id, payload['features_file'], payload['collection'], payload.get('dataset_id'))
    else:
        raise ValueError('Unsupported durable job kind')


def run_once():
    with SessionLocal() as db:
        claimed = claim(db)
        if not claimed:
            return False
        job_id, owner = claimed
        job = db.get(Job, job_id)
        kind, payload = job.kind, dict(job.payload)
    stopped = Event()
    started = monotonic()
    def renew():
        while not stopped.wait(10):
            if monotonic() - started > float(os.getenv('JOB_MAX_SECONDS', '3600')):
                os._exit(3)
            try:
                with SessionLocal() as db:
                    if heartbeat(db, job_id, owner):
                        continue
            except Exception:
                pass
            # Fail closed before another worker resumes this job.
            os._exit(2)
    thread = Thread(target=renew, daemon=True)
    thread.start()
    success = False
    try:
        dispatch(job_id, kind, payload)
        with SessionLocal() as db:
            success = db.get(Job, job_id).status == 'COMPLETED'
    except Exception:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            job.status = 'FAILED'; job.message = 'Worker failed; inspect worker logs'
            db.commit()
    finally:
        stopped.set(); thread.join()
        with SessionLocal() as db:
            finish(db, job_id, owner, success)
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--init', action='store_true', help='Create only the additive lease table; run no jobs')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if args.init:
        JobLease.__table__.create(engine, checkfirst=True)
    else:
        while True:
            worked = run_once()
            if args.once:
                break
            if not worked:
                sleep(2)

import json
import os
from pathlib import Path
import subprocess
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.models import Base, Job
from app.modules.jobs.durable import JobLease, enqueue


def test_persisted_job_is_claimed_after_worker_process_exits(tmp_path):
    url = 'sqlite:///'+str(tmp_path/'restart.db')
    factory = sessionmaker(bind=create_engine(url))
    Base.metadata.create_all(factory.kw['bind'])
    with factory() as db:
        job=Job(kind='VIDEO_PIPELINE',payload={});enqueue(db,job)
        job_id=job.id
    code = '''
import json,sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.modules.jobs.durable import claim
with sessionmaker(bind=create_engine(sys.argv[1]))() as db:
    print(json.dumps(claim(db,lease_seconds=0)))
'''
    env = {**os.environ, 'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
    first=json.loads(subprocess.check_output([sys.executable,'-c',code,url],env=env,text=True))
    second=json.loads(subprocess.check_output([sys.executable,'-c',code,url],env=env,text=True))
    assert first[0] == second[0] == job_id
    assert first[1] != second[1]
    with factory() as db:
        assert db.get(JobLease,job_id).attempts == 2

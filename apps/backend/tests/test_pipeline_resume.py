from pathlib import Path
from types import SimpleNamespace
from app.db.models import Dataset, Job, Video
from app.modules.pipeline import service as module
from app.modules.pipeline.schemas import PipelineJobRequest, VideoPipelineResult
from tests.test_qa_trake_hardening import _build_db


def test_retry_resumes_after_completed_video_checkpoint(tmp_path, monkeypatch):
    db = _build_db(tmp_path)
    dataset = Dataset(dataset_code='resume', name='resume', version='1', root_uri='local')
    job = Job(kind='VIDEO_PIPELINE', payload={})
    db.add_all([dataset,job]); db.commit()
    svc = module.PipelineService(db, None, None, None)
    monkeypatch.setattr(module, '_load_source_config', lambda _: SimpleNamespace(source_dataset_id='resume', source_id='resume', display_name='resume', source_version='1'))
    monkeypatch.setattr(svc, '_upsert_dataset', lambda **_: dataset)
    calls = []
    failing = [True]
    def process(**kwargs):
        key = kwargs['video_key']; calls.append(key)
        if key == 'b.mp4' and failing[0]:
            raise RuntimeError('provider unavailable')
        return VideoPipelineResult(video_id=Path(key).stem, status='COMPLETED', num_keyframes=2)
    monkeypatch.setattr(svc, '_process_video', process)
    request = PipelineJobRequest(source_id='resume', video_keys=['a.mp4','b.mp4'])
    svc.run(job,request)
    assert job.status == 'FAILED'
    assert set(job.payload['completed_videos']) == {'a.mp4'}
    failing[0] = False
    svc.run(job,request)
    assert job.status == 'COMPLETED'
    assert calls == ['a.mp4','b.mp4','b.mp4']
    db.close()


def test_incomplete_video_is_not_mistaken_for_completed_import(tmp_path):
    db = _build_db(tmp_path)
    dataset = Dataset(dataset_code='resume', name='resume', version='1', root_uri='local')
    db.add(dataset); db.commit()
    video = Video(video_id='v', dataset_id=dataset.id, video_code='v', num_keyframes=5,
        source_video_path='v.mp4', extra_metadata={'pipeline_complete':False})
    svc = module.PipelineService(db,None,None,None)
    assert svc._check_collision(video,dataset,'v.mp4','v',False) is None
    video.extra_metadata = {'pipeline_complete':True}
    assert svc._check_collision(video,dataset,'v.mp4','v',False).status == 'SKIPPED'
    db.close()

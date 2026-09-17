from app.db.models import Dataset, Frame
from app.modules.media.router import list_frames
from tests.test_qa_trake_hardening import _build_db, _seed_video_with_frames


def test_gallery_pagination_spans_videos_without_leaking_datasets(tmp_path):
    db = _build_db(tmp_path)
    one = Dataset(dataset_code='one',name='one',version='1',root_uri='local')
    two = Dataset(dataset_code='two',name='two',version='1',root_uri='local')
    db.add_all([one,two]); db.commit()
    _seed_video_with_frames(db,one,'A',[10,20,30])
    _seed_video_with_frames(db,one,'B',[10,20])
    _seed_video_with_frames(db,two,'C',[10])
    for f in db.query(Frame).all(): f.is_media_present = True
    db.commit()
    response = list_frames(dataset_id=one.id,offset=2,limit=2,db=db)
    assert response['total'] == 5
    assert [(r['video_code'],r['frame_idx']) for r in response['frames']] == [('A',30),('B',10)]
    assert list_frames(dataset_id=one.id,offset=5,limit=2,db=db)['frames'] == []
    exact = list_frames(dataset_id=one.id,video_code='B',db=db)
    assert exact['total'] == 2 and all(r['video_code']=='B' for r in exact['frames'])
    db.close()

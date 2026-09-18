from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.modules.media import router as media


def test_evidence_pins_physical_index_and_does_not_hide_outage(monkeypatch):
    class Client:
        fail = False
        def search(self, **kwargs):
            self.kwargs=kwargs
            if self.fail: raise RuntimeError('provider down')
            return {'hits':{'hits':[{'_source':{'source_type':'ocr','keyframe_id':'f1','text_value':'red'}}]}}
    client=Client()
    monkeypatch.setattr(media,'get_text_client',lambda:SimpleNamespace(client=client))
    payload=media._video_evidence_payload('video',anchor_frame_id='f1',index_version='annotations_v1')
    assert client.kwargs['index']=='annotations_v1'
    assert payload['index_version']=='annotations_v1'
    assert payload['evidence']['ocr'][0]['matches_selected_frame']
    client.fail=True
    with pytest.raises(HTTPException) as error:
        media._video_evidence_payload('video',anchor_frame_id='f1')
    assert error.value.status_code==503

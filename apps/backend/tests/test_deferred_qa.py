import pytest
from fastapi import HTTPException

from tests.test_qa_trake_hardening import _build_db, _seed_video_with_frames, TextClientForQa
from tests.fakes import DeterministicEmbedder, ExpandingQueryExpander
from app.db.models import Dataset
from app.modules.models.service import ModelRegistryService
from app.modules.retrieval.schemas import SearchRequest, SearchOptions
from app.modules.retrieval.service import RetrievalService
from app.modules.retrieval import answers
from app.core.readiness import probe


def test_deferred_qa_uses_preview_evidence_once_and_persists_citations(tmp_path, monkeypatch):
    class Qa:
        calls = []
        def answer(self, question, evidence, hint):
            self.calls.append(evidence)
            return 'red'
    db = _build_db(tmp_path)
    dataset = Dataset(dataset_code='qa-deferred', name='QA', version='1', root_uri='file:///demo')
    db.add(dataset); db.commit()
    _seed_video_with_frames(db, dataset, 'L30_V001', [10])
    model = Qa()
    registry = ModelRegistryService(DeterministicEmbedder(), ExpandingQueryExpander(), model)
    response = RetrievalService(db, registry, text_client=TextClientForQa()).search(SearchRequest(
        dataset_id=dataset.id, query_type='QA', query_text='what color',
        options=SearchOptions(defer_qa=True, use_agent_query_planning=False, use_query_expansion=False)))
    assert response.results and response.results[0].answer is None
    assert not model.calls
    calls = []
    def evidence(video, **kwargs):
        calls.append((video, kwargs))
        return {'evidence': {'ocr': [{'text': 'red', 'frame_id': 'L30_V001_F000010'}]}}
    monkeypatch.setattr(answers, '_video_evidence_payload', evidence)
    result = answers.answer_result(response.results[0].id, db, registry)
    assert result['answer'] == 'red'
    assert result['evidence'][0]['source'] == 'ocr'
    assert calls[0][1]['anchor_frame_id'] == 'L30_V001_F000010'
    assert answers.answer_result(response.results[0].id, db, registry) == result
    assert len(model.calls) == 1
    with pytest.raises(HTTPException) as error:
        answers.answer_result('missing', db, registry)
    assert error.value.status_code == 404
    db.close()


def test_readiness_does_not_expose_provider_error_secrets():
    def broken():
        raise RuntimeError('secret-provider-token')
    assert probe(broken) == {'status': 'unavailable', 'reason': 'RuntimeError'}

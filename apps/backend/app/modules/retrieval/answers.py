"""On-demand text-evidence QA with exactly the citations shown in preview."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.budget import bounded_call
from app.core.deps import get_model_registry_service
from app.core.telemetry import stage
from app.db.models import RetrievalResult
from app.db.session import get_db
from app.modules.media.router import _video_evidence_payload
from app.modules.retrieval.service import RetrievalService

router = APIRouter(prefix='/api/retrieval', tags=['retrieval'])


@router.post('/results/{result_id}/answer')
def answer_result(result_id: str, db: Session = Depends(get_db), registry=Depends(get_model_registry_service)):
    # Result 
    result = db.get(RetrievalResult, result_id)
    if not result or not result.frame or result.query_run.query_type != 'QA':
        raise HTTPException(404, 'QA result not found')
    if result.answer and result.score_breakdown.get('qa_evidence'):
        return {'answer': result.answer, 'evidence': result.score_breakdown['qa_evidence'], 'mode': 'text_evidence'}
    frame = result.frame

    # Payload 
    payload = _video_evidence_payload(result.video_id, anchor_frame_id=frame.id,
        index_version=(result.score_breakdown.get('text_hit') or {}).get('index_version'),
        anchor_seconds=float(frame.frame_seconds) if frame.frame_seconds is not None else None)
    evidence = [{**item, 'source': source} for source, items in payload['evidence'].items() for item in items]

    if not evidence:
        raise HTTPException(422, 'No aligned evidence. Inspect the frame and enter an answer manually.')
    text = '\n'.join(f'[{i+1} {item["source"]}] {item["text"]}' for i,item in enumerate(evidence))
    try:
        with stage('qa'):
            raw = bounded_call(registry.visual_qa.answer, result.query_run.query_text, text, None, max_seconds=15)
    except TimeoutError as exc:
        raise HTTPException(504, 'Answer generation timed out') from exc
    except Exception as exc:
        raise HTTPException(503, 'Answer model unavailable') from exc
    answer = RetrievalService._postprocess_qa_answer(raw)
    if not answer:
        raise HTTPException(422, 'The model returned no supported answer')
    result.answer = answer
    result.score_breakdown = {**result.score_breakdown, 'qa_evidence': evidence, 'qa_mode': 'text_evidence'}
    db.commit()
    return {'answer': answer, 'evidence': evidence, 'mode': 'text_evidence'}

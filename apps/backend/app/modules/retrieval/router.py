from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.retrieval.schemas import SearchRequest, SearchResponse, SelectResultsRequest
from app.modules.retrieval.service import RetrievalService

router = APIRouter(prefix="/api/retrieval", tags=["retrieval"])


@router.post("/search", response_model=SearchResponse)
def search(request: SearchRequest, db: Session = Depends(get_db)) -> SearchResponse:
    try:
        return RetrievalService(db).search(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/qa", response_model=SearchResponse)
def qa_search(request: SearchRequest, db: Session = Depends(get_db)) -> SearchResponse:
    request.query_type = "QA"
    return search(request, db)


@router.post("/trake", response_model=SearchResponse)
def trake_search(request: SearchRequest, db: Session = Depends(get_db)) -> SearchResponse:
    request.query_type = "TRAKE"
    return search(request, db)


@router.get("/runs/{run_id}", response_model=SearchResponse)
def get_run(run_id: str, db: Session = Depends(get_db)) -> SearchResponse:
    return RetrievalService(db).get_run(run_id)


@router.post("/runs/{run_id}/select")
def select_results(run_id: str, request: SelectResultsRequest, db: Session = Depends(get_db)) -> dict:
    _ = run_id
    updated = RetrievalService(db).select_results(request.result_ids, request.selected)
    return {"updated": updated}

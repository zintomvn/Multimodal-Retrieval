from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_model_registry_service
from app.db.session import get_db
from app.modules.models.service import ModelRegistryService
from app.modules.retrieval.schemas import SearchRequest, SearchResponse, SelectResultsRequest
from app.modules.retrieval.service import RetrievalService

router = APIRouter(prefix="/api/retrieval", tags=["retrieval"])


@router.post("/search", response_model=SearchResponse)
def search(
    request: SearchRequest,
    db: Session = Depends(get_db),
    model_registry: ModelRegistryService = Depends(get_model_registry_service),
) -> SearchResponse:
    try:
        return RetrievalService(db, model_registry).search(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/qa", response_model=SearchResponse)
def qa_search(
    request: SearchRequest,
    db: Session = Depends(get_db),
    model_registry: ModelRegistryService = Depends(get_model_registry_service),
) -> SearchResponse:
    request.query_type = "QA"
    try:
        return RetrievalService(db, model_registry).search(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/trake", response_model=SearchResponse)
def trake_search(
    request: SearchRequest,
    db: Session = Depends(get_db),
    model_registry: ModelRegistryService = Depends(get_model_registry_service),
) -> SearchResponse:
    request.query_type = "TRAKE"
    try:
        return RetrievalService(db, model_registry).search(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}", response_model=SearchResponse)
def get_run(
    run_id: str,
    db: Session = Depends(get_db),
    model_registry: ModelRegistryService = Depends(get_model_registry_service),
) -> SearchResponse:
    return RetrievalService(db, model_registry).get_run(run_id)


@router.post("/runs/{run_id}/select")
def select_results(
    run_id: str,
    request: SelectResultsRequest,
    db: Session = Depends(get_db),
    model_registry: ModelRegistryService = Depends(get_model_registry_service),
) -> dict:
    _ = run_id
    updated = RetrievalService(db, model_registry).select_results(request.result_ids, request.selected)
    return {"updated": updated}

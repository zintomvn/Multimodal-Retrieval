from __future__ import annotations
from typing import Literal
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.models import Submission
from app.db.session import get_db
from app.adapters.dres.client import DresConfigurationError, DresRemoteError
from app.modules.submissions.dres import DresSubmissionService
from app.modules.submissions.schemas import DresSubmitRequest, SubmissionCreate, SubmissionExportResponse, SubmissionItemsRequest
from app.modules.submissions.service import SubmissionService

router = APIRouter(prefix="/api/submissions", tags=["submissions"])


@router.post("/dres")
def submit_to_dres(request: DresSubmitRequest, db: Session = Depends(get_db)) -> dict:
    try:
        return DresSubmissionService(db).submit(request.dataset_id, request.rows)
    except DresConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except DresRemoteError as exc:
        status = 422 if exc.status_code is not None and 400 <= exc.status_code < 500 else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("")
def create_submission(request: SubmissionCreate, db: Session = Depends(get_db)) -> dict:
    submission = SubmissionService(db).create(request.dataset_id, request.name)
    return {"id": submission.id, "status": submission.status}


@router.post("/{submission_id}/items")
def add_items(submission_id: str, request: SubmissionItemsRequest, db: Session = Depends(get_db)) -> dict:
    count = SubmissionService(db).add_items(submission_id, request.rows)
    return {"submission_id": submission_id, "rows": count}


@router.post("/{submission_id}/validate")
def validate_submission(submission_id: str, db: Session = Depends(get_db)) -> dict:
    return SubmissionService(db).validate(submission_id)


@router.post("/{submission_id}/export", response_model=SubmissionExportResponse)
def export_submission(submission_id: str, format: Literal["csv", "zip"] = "csv", db: Session = Depends(get_db)) -> SubmissionExportResponse:
    service = SubmissionService(db)
    submission, report = service.export_zip(submission_id) if format == "zip" else service.export_csv(submission_id)
    return SubmissionExportResponse(
        submission_id=submission.id,
        status=submission.status,
        csv_uri=service.artifact_uri(submission, "csv") if report["valid"] else None,
        zip_uri=service.artifact_uri(submission, "zip") if report["valid"] else None,
        validation_report=report,
    )


@router.get("/{submission_id}/download")
def download_submission(submission_id: str, format: Literal["csv", "zip"] = "csv", db: Session = Depends(get_db)) -> FileResponse:
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    uri = SubmissionService.artifact_uri(submission, format) if submission and submission.status == "EXPORTED" else None
    if not uri or not Path(uri).is_file():
        raise HTTPException(status_code=404, detail="Validated export not found")
    return FileResponse(uri, filename=f"{Path(submission.name).name}.{format}", media_type="application/zip" if format == "zip" else "text/csv")

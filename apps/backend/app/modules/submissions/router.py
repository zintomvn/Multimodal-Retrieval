from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.models import Submission
from app.db.session import get_db
from app.modules.submissions.schemas import SubmissionCreate, SubmissionExportResponse, SubmissionItemsRequest
from app.modules.submissions.service import SubmissionService

router = APIRouter(prefix="/api/submissions", tags=["submissions"])


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
def export_submission(submission_id: str, db: Session = Depends(get_db)) -> SubmissionExportResponse:
    submission, report = SubmissionService(db).export_csv(submission_id)
    return SubmissionExportResponse(
        submission_id=submission.id,
        status=submission.status,
        csv_uri=submission.zip_uri,
        zip_uri=submission.zip_uri,
        validation_report=report,
    )


@router.get("/{submission_id}/download")
def download_submission(submission_id: str, db: Session = Depends(get_db)) -> FileResponse:
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission or not submission.zip_uri:
        raise HTTPException(status_code=404, detail="Exported CSV not found")
    return FileResponse(submission.zip_uri, filename=f"{submission.name}.csv", media_type="text/csv")

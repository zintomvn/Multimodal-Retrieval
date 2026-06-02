from __future__ import annotations

import csv
import zipfile
from collections import defaultdict
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Submission, SubmissionItem
from app.modules.submissions.schemas import SubmissionRow


class SubmissionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

    def create(self, dataset_id: str, name: str) -> Submission:
        submission = Submission(dataset_id=dataset_id, name=name, status="DRAFT")
        self.db.add(submission)
        self.db.commit()
        self.db.refresh(submission)
        return submission

    def add_items(self, submission_id: str, rows: list[SubmissionRow]) -> int:
        existing = self.db.query(SubmissionItem).filter(SubmissionItem.submission_id == submission_id).all()
        for item in existing:
            self.db.delete(item)
        self.db.flush()
        for row in rows:
            self.db.add(
                SubmissionItem(
                    submission_id=submission_id,
                    query_name=row.query_name,
                    query_type=row.query_type,
                    rank=row.rank,
                    video_code=row.video_code,
                    frame_indices=row.frame_indices,
                    answer=row.answer,
                )
            )
        self.db.commit()
        return len(rows)

    def validate(self, submission_id: str) -> dict:
        submission = self.db.query(Submission).filter(Submission.id == submission_id).one()
        errors: list[str] = []
        warnings: list[str] = []
        grouped: dict[str, list[SubmissionItem]] = defaultdict(list)
        for item in submission.items:
            grouped[item.query_name].append(item)
        if not grouped:
            errors.append("Submission has no rows.")
        for query_name, rows in grouped.items():
            rows.sort(key=lambda item: item.rank)
            if len(rows) > 100:
                errors.append(f"{query_name}: has {len(rows)} rows, maximum is 100.")
            for row in rows:
                if row.query_type == "KIS":
                    if len(row.frame_indices) != 1:
                        errors.append(f"{query_name} rank {row.rank}: KIS must have exactly 1 frame index.")
                elif row.query_type == "QA":
                    if len(row.frame_indices) != 1:
                        errors.append(f"{query_name} rank {row.rank}: QA must have exactly 1 frame index.")
                    if not row.answer:
                        warnings.append(f"{query_name} rank {row.rank}: QA answer is empty.")
                    if row.answer and len(row.answer) > 100:
                        errors.append(f"{query_name} rank {row.rank}: QA answer exceeds 100 characters.")
                elif row.query_type == "TRAKE":
                    if len(row.frame_indices) < 1:
                        errors.append(f"{query_name} rank {row.rank}: TRAKE must have at least 1 frame index.")
                if row.video_code.endswith(".mp4"):
                    errors.append(f"{query_name} rank {row.rank}: video code must not include .mp4.")
        report = {"valid": not errors, "errors": errors, "warnings": warnings, "query_count": len(grouped)}
        submission.validation_report = report
        submission.status = "VALID" if report["valid"] else "FAILED"
        self.db.commit()
        return report

    def export_zip(self, submission_id: str) -> tuple[Submission, dict]:
        report = self.validate(submission_id)
        submission = self.db.query(Submission).filter(Submission.id == submission_id).one()
        if not report["valid"]:
            return submission, report

        base_dir = self.settings.data_root / "submissions" / submission.id
        submission_dir = base_dir / "submission"
        submission_dir.mkdir(parents=True, exist_ok=True)

        grouped: dict[str, list[SubmissionItem]] = defaultdict(list)
        for item in submission.items:
            grouped[item.query_name].append(item)

        for query_name, rows in grouped.items():
            csv_path = submission_dir / f"{query_name}.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                for row in sorted(rows, key=lambda item: item.rank)[:100]:
                    if row.query_type == "QA":
                        writer.writerow([row.video_code, row.frame_indices[0], row.answer or ""])
                    else:
                        writer.writerow([row.video_code, *row.frame_indices])

        zip_path = base_dir / f"{submission.name}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for csv_file in sorted(submission_dir.glob("*.csv")):
                archive.write(csv_file, arcname=str(Path("submission") / csv_file.name))

        submission.zip_uri = str(zip_path)
        submission.status = "EXPORTED"
        self.db.commit()
        return submission, report

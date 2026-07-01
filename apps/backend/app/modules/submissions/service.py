from __future__ import annotations

import csv
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Submission, SubmissionItem
from app.modules.submissions.schemas import SubmissionRow


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    query_name: str | None = None
    rank: int | None = None
    line_number: int | None = None
    field: str | None = None

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "query_name": self.query_name,
            "rank": self.rank,
            "line_number": self.line_number,
            "field": self.field,
        }


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
            normalized_answer = self._normalize_answer(row.answer)
            self.db.add(
                SubmissionItem(
                    submission_id=submission_id,
                    query_name=row.query_name,
                    query_type=row.query_type,
                    rank=row.rank,
                    video_code=row.video_code,
                    frame_indices=[int(value) for value in row.frame_indices],
                    answer=normalized_answer,
                )
            )
        self.db.commit()
        return len(rows)

    def validate(self, submission_id: str) -> dict:
        submission = self.db.query(Submission).filter(Submission.id == submission_id).one()
        issues: list[ValidationIssue] = []
        grouped: dict[str, list[SubmissionItem]] = defaultdict(list)
        for item in submission.items:
            grouped[item.query_name].append(item)
        if not grouped:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="submission.empty",
                    message="Submission has no rows.",
                )
            )
        for query_name, rows in grouped.items():
            rows.sort(key=lambda item: item.rank)
            if len(rows) > 100:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="query.max_rows_exceeded",
                        message=f"{query_name}: has {len(rows)} rows, maximum is 100.",
                        query_name=query_name,
                    )
                )
            for line_number, row in enumerate(rows, start=1):
                if row.rank <= 0:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="row.rank_invalid",
                            message=f"{query_name} rank {row.rank}: rank must be positive.",
                            query_name=query_name,
                            rank=row.rank,
                            line_number=line_number,
                            field="rank",
                        )
                    )
                if not row.video_code:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="row.video_code_empty",
                            message=f"{query_name} rank {row.rank}: video code is required.",
                            query_name=query_name,
                            rank=row.rank,
                            line_number=line_number,
                            field="video_code",
                        )
                    )
                if row.query_type == "KIS":
                    if len(row.frame_indices) != 1:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                code="kis.invalid_frame_count",
                                message=f"{query_name} rank {row.rank}: KIS must have exactly 1 frame index.",
                                query_name=query_name,
                                rank=row.rank,
                                line_number=line_number,
                                field="frame_indices",
                            )
                        )
                elif row.query_type == "QA":
                    if len(row.frame_indices) != 1:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                code="qa.invalid_frame_count",
                                message=f"{query_name} rank {row.rank}: QA must have exactly 1 frame index.",
                                query_name=query_name,
                                rank=row.rank,
                                line_number=line_number,
                                field="frame_indices",
                            )
                        )
                    normalized_answer = self._normalize_answer(row.answer)
                    if not normalized_answer:
                        issues.append(
                            ValidationIssue(
                                severity="warning",
                                code="qa.answer_empty",
                                message=f"{query_name} rank {row.rank}: QA answer is empty.",
                                query_name=query_name,
                                rank=row.rank,
                                line_number=line_number,
                                field="answer",
                            )
                        )
                    if normalized_answer and len(normalized_answer) > 100:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                code="qa.answer_too_long",
                                message=f"{query_name} rank {row.rank}: QA answer exceeds 100 characters.",
                                query_name=query_name,
                                rank=row.rank,
                                line_number=line_number,
                                field="answer",
                            )
                        )
                elif row.query_type == "TRAKE":
                    if len(row.frame_indices) < 1:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                code="trake.empty_sequence",
                                message=f"{query_name} rank {row.rank}: TRAKE must have at least 1 frame index.",
                                query_name=query_name,
                                rank=row.rank,
                                line_number=line_number,
                                field="frame_indices",
                            )
                        )
                    elif any(
                        row.frame_indices[idx] >= row.frame_indices[idx + 1]
                        for idx in range(len(row.frame_indices) - 1)
                    ):
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                code="trake.non_monotonic_sequence",
                                message=f"{query_name} rank {row.rank}: TRAKE frame indices must be strictly increasing.",
                                query_name=query_name,
                                rank=row.rank,
                                line_number=line_number,
                                field="frame_indices",
                            )
                        )
                for frame_idx in row.frame_indices:
                    if int(frame_idx) < 0:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                code="row.frame_index_negative",
                                message=f"{query_name} rank {row.rank}: frame index must be non-negative.",
                                query_name=query_name,
                                rank=row.rank,
                                line_number=line_number,
                                field="frame_indices",
                            )
                        )
                if row.video_code.endswith(".mp4"):
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="row.video_code_has_extension",
                            message=f"{query_name} rank {row.rank}: video code must not include .mp4.",
                            query_name=query_name,
                            rank=row.rank,
                            line_number=line_number,
                            field="video_code",
                        )
                    )

        error_messages = [item.message for item in issues if item.severity == "error"]
        warning_messages = [item.message for item in issues if item.severity == "warning"]
        report = {
            "valid": not error_messages,
            "errors": error_messages,
            "warnings": warning_messages,
            "violations": [item.as_dict() for item in issues],
            "query_count": len(grouped),
            "total_rows": sum(len(rows) for rows in grouped.values()),
        }
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
                    normalized_answer = self._normalize_answer(row.answer)
                    if row.query_type == "QA":
                        writer.writerow([row.video_code, row.frame_indices[0], normalized_answer or ""])
                    elif row.query_type == "KIS":
                        writer.writerow([row.video_code, row.frame_indices[0]])
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

    def _normalize_answer(self, answer: str | None) -> str | None:
        if answer is None:
            return None
        normalized = " ".join(str(answer).split())
        return normalized or None

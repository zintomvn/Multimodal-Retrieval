from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path
import sys
import zipfile

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.db.models import Base, Dataset
from app.modules.submissions.schemas import SubmissionRow
from app.modules.submissions.service import SubmissionService


def _build_submission_service(tmp_path: Path, monkeypatch) -> tuple[Session, SubmissionService]:
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    get_settings.cache_clear()

    db_path = tmp_path / "submission.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()

    dataset = Dataset(dataset_code="submission-demo", name="submission-demo", version="v1", root_uri="file:///demo", status="READY")
    db.add(dataset)
    db.commit()

    return db, SubmissionService(db)


def test_m5_submission_validation_report_contains_detailed_line_violations(tmp_path: Path, monkeypatch) -> None:
    db, service = _build_submission_service(tmp_path, monkeypatch)
    dataset = db.query(Dataset).one()
    submission = service.create(dataset_id=dataset.dataset_id, name="invalid-case")

    rows = [
        SubmissionRow(query_name="query-1-kis", query_type="KIS", rank=1, video_code="L00_V000.mp4", frame_indices=[1234]),
        SubmissionRow(query_name="query-2-qa", query_type="QA", rank=1, video_code="L01_V028", frame_indices=[3450], answer="x" * 101),
        SubmissionRow(query_name="query-3-trake", query_type="TRAKE", rank=1, video_code="L10_V001", frame_indices=[2100, 1850]),
    ]
    service.add_items(submission.id, rows)
    report = service.validate(submission.id)

    assert report["valid"] is False
    codes = {item["code"] for item in report["violations"]}
    assert "row.video_code_has_extension" in codes
    assert "qa.answer_too_long" in codes
    assert "trake.non_monotonic_sequence" in codes

    qa_issue = next(item for item in report["violations"] if item["code"] == "qa.answer_too_long")
    assert qa_issue["line_number"] == 1
    assert qa_issue["field"] == "answer"
    db.close()


def test_m5_submission_export_zip_matches_codabench_structure(tmp_path: Path, monkeypatch) -> None:
    db, service = _build_submission_service(tmp_path, monkeypatch)
    dataset = db.query(Dataset).one()
    submission = service.create(dataset_id=dataset.dataset_id, name="valid-case")

    rows = [
        SubmissionRow(query_name="query-1-kis", query_type="KIS", rank=1, video_code="L00_V000", frame_indices=[1234]),
        SubmissionRow(query_name="query-2-qa", query_type="QA", rank=1, video_code="L01_V028", frame_indices=[3450], answer="  Mau   do "),
        SubmissionRow(query_name="query-3-trake", query_type="TRAKE", rank=1, video_code="L10_V001", frame_indices=[1200, 1850, 2100]),
    ]
    service.add_items(submission.id, rows)

    exported_submission, report = service.export_zip(submission.id)
    assert report["valid"] is True
    assert exported_submission.status == "EXPORTED"
    assert exported_submission.zip_uri is not None

    zip_path = Path(exported_submission.zip_uri)
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path, "r") as archive:
        names = sorted(archive.namelist())
        assert names == [
            "submission/query-1-kis.csv",
            "submission/query-2-qa.csv",
            "submission/query-3-trake.csv",
        ]
        qa_csv = archive.read("submission/query-2-qa.csv").decode("utf-8")
        qa_rows = list(csv.reader(StringIO(qa_csv)))
        assert qa_rows[0] == ["L01_V028", "3450", "Mau do"]
        assert "video_code" not in qa_csv.lower()

    db.close()

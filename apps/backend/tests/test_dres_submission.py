from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.adapters.dres.client import DresEvaluation
from app.core.config import get_settings
from app.db.models import Base, Dataset, DresSubmission, Frame, Video
from app.modules.submissions.dres import DresSubmissionService
from app.modules.submissions.schemas import SubmissionRow


class FakeDresClient:
    def __init__(self) -> None:
        self.payloads: list[tuple[str, dict]] = []

    def active_evaluation(self, expected_name: str) -> DresEvaluation:
        assert expected_name in {
            "AIC2026 - Textual KIS Test 2",
            "AIC2026 - QA Test 2",
            "AIC2026 - TRAKE Test 2",
        }
        return DresEvaluation("evaluation-2", expected_name, "ACTIVE")

    def submit_kis(self, evaluation_id: str, payload: dict) -> dict:
        self.payloads.append((evaluation_id, payload))
        return {"accepted": True}


def _service(tmp_path: Path) -> tuple[object, DresSubmissionService, FakeDresClient, str]:
    engine = create_engine(f"sqlite:///{tmp_path / 'dres.sqlite3'}")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    dataset = Dataset(dataset_code="aic", name="AIC", version="2026", root_uri="file:///aic", status="READY")
    db.add(dataset)
    db.flush()
    video = Video(video_id="v-1", dataset_id=dataset.dataset_id, video_code="L21_V006", uri="gs://aic/video.mp4")
    db.add(video)
    db.add(
        Frame(
            keyframe_id="v-1_F001250",
            video_id=video.video_id,
            frame_idx=1250,
            timestamp_ms=50_000,
            frame_seconds=50,
            image_uri="gs://aic/frame.jpg",
        )
    )
    db.commit()
    client = FakeDresClient()
    service = DresSubmissionService(db, client=client)  # type: ignore[arg-type]
    service.settings = replace(
        service.settings,
        dres_evaluation_name="AIC2026 - Textual KIS Test 2",
        dres_kis_evaluation_name="AIC2026 - Textual KIS Test 2",
        dres_qa_evaluation_name="AIC2026 - QA Test 2",
        dres_trake_evaluation_name="AIC2026 - TRAKE Test 2",
    )
    return db, service, client, dataset.dataset_id


def test_submit_kis_resolves_timestamp_and_uses_dres_payload(tmp_path: Path) -> None:
    db, service, client, dataset_id = _service(tmp_path)
    row = SubmissionRow(
        query_name="AIC2026 - Textual KIS Test 2",
        query_type="KIS",
        rank=1,
        video_code="L21_V006",
        frame_indices=[1250],
    )

    result = service.submit_kis(dataset_id, [row])

    assert result["timestamp_ms"] == 50_000
    assert client.payloads == [
        (
            "evaluation-2",
            {
                "answerSets": [
                    {
                        "answers": [
                            {
                                "mediaItemName": "L21_V006",
                                "start": 50_000,
                                "end": 50_000,
                            }
                        ]
                    }
                ]
            },
        )
    ]
    assert db.query(DresSubmission).one().status == "SUBMITTED"
    db.close()


def test_submit_kis_blocks_the_same_answer_twice(tmp_path: Path) -> None:
    db, service, client, dataset_id = _service(tmp_path)
    row = SubmissionRow(
        query_name="AIC2026 - Textual KIS Test 2",
        query_type="KIS",
        rank=1,
        video_code="L21_V006",
        frame_indices=[1250],
    )
    service.submit_kis(dataset_id, [row])

    with pytest.raises(ValueError, match="already sent"):
        service.submit_kis(dataset_id, [row])

    assert len(client.payloads) == 1
    db.close()


def test_submit_qa_uses_the_manual_answer_and_timestamp(tmp_path: Path) -> None:
    db, service, client, dataset_id = _service(tmp_path)
    row = SubmissionRow(
        query_name="AIC2026 - Textual KIS Test 2",
        query_type="QA",
        rank=1,
        video_code="L21_V006",
        frame_indices=[1250],
        answer="red umbrella",
    )

    service.submit(dataset_id, [row])

    assert client.payloads[0][1] == {
        "answerSets": [
            {"answers": [{"text": "QA-red umbrella-L21_V006-50000"}]}
        ]
    }
    db.close()


def test_submit_trake_uses_the_selected_frame_indices(tmp_path: Path) -> None:
    db, service, client, dataset_id = _service(tmp_path)
    # Add two later frames from the same video to model a three-event sequence.
    db.add_all(
        [
            Frame(keyframe_id="v-1_F001500", video_id="v-1", frame_idx=1500, timestamp_ms=60_000, frame_seconds=60, image_uri="gs://aic/1500.jpg"),
            Frame(keyframe_id="v-1_F001750", video_id="v-1", frame_idx=1750, timestamp_ms=70_000, frame_seconds=70, image_uri="gs://aic/1750.jpg"),
        ]
    )
    db.commit()
    row = SubmissionRow(
        query_name="AIC2026 - TRAKE Test 2",
        query_type="TRAKE",
        rank=1,
        video_code="L21_V006",
        frame_indices=[1250, 1500, 1750],
    )

    service.submit(dataset_id, [row])

    assert client.payloads[0][1] == {
        "answerSets": [
            {"answers": [{"text": "TR-L21_V006-1250,1500,1750"}]}
        ]
    }
    db.close()

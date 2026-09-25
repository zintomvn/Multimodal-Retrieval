from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.dres.client import DresClient, DresRemoteError
from app.core.config import Settings, get_settings
from app.db.models import DresSubmission, Frame, Video
from app.modules.submissions.schemas import SubmissionRow


class DresSubmissionService:
    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | None = None,
        client: DresClient | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.client = client or DresClient(
            base_url=self.settings.dres_base_url,
            session_id=self.settings.dres_session_id,
            timeout_seconds=self.settings.dres_timeout_seconds,
        )

    def submit(self, dataset_id: str, rows: list[SubmissionRow]) -> dict[str, Any]:
        if len(rows) != 1:
            raise ValueError("Submit exactly one selected frame to DRES.")
        row = rows[0]
        if row.query_type not in {"KIS", "QA", "TRAKE"}:
            raise ValueError("DRES Submit accepts KIS, QA, or TRAKE selections.")
        if row.query_type in {"KIS", "QA"} and len(row.frame_indices) != 1:
            raise ValueError("KIS and QA each require exactly one selected frame.")
        if row.query_type == "TRAKE" and not row.frame_indices:
            raise ValueError("TRAKE requires at least one selected frame.")
        if row.query_type == "TRAKE" and any(
            row.frame_indices[index] >= row.frame_indices[index + 1]
            for index in range(len(row.frame_indices) - 1)
        ):
            raise ValueError("TRAKE frame indices must be strictly increasing.")
        if row.video_code.endswith(".mp4"):
            raise ValueError("DRES mediaItemName must not include the video extension.")
        qa_answer = (row.answer or "").strip() if row.query_type == "QA" else ""
        if row.query_type == "QA" and not qa_answer:
            raise ValueError("Enter a QA answer before submitting to DRES.")

        frames = (
            self.db.query(Frame)
            .join(Video)
            .filter(
                Video.dataset_id == dataset_id,
                Video.video_code == row.video_code,
                Frame.frame_idx.in_(row.frame_indices),
            )
            .all()
        )
        frames_by_index = {frame.frame_idx: frame for frame in frames}
        if any(index not in frames_by_index for index in row.frame_indices):
            raise ValueError("At least one selected frame is not indexed in the active dataset.")
        frame = frames_by_index[row.frame_indices[0]]

        evaluation = self.client.active_evaluation(self._evaluation_name_for(row.query_type))
        receipt = DresSubmission(
            evaluation_id=evaluation.id,
            evaluation_name=evaluation.name,
            media_item_name=row.video_code,
            timestamp_ms=int(frame.timestamp_ms),
            status="PENDING",
        )
        self.db.add(receipt)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ValueError("This exact frame was already sent to DRES for the active evaluation.") from exc

        if row.query_type == "KIS":
            answer: dict[str, Any] = {
                "mediaItemName": row.video_code,
                "start": int(frame.timestamp_ms),
                "end": int(frame.timestamp_ms),
            }
        elif row.query_type == "QA":
            answer = {
                "text": f"QA-{qa_answer}-{row.video_code}-{int(frame.timestamp_ms)}"
            }
        else:
            answer = {"text": f"TR-{row.video_code}-" + ",".join(map(str, row.frame_indices))}
        payload = {"answerSets": [{"answers": [answer]}]}
        try:
            response = self.client.submit_kis(evaluation.id, payload)
        except DresRemoteError as exc:
            # A 4xx response is a confirmed rejection, so it is safe to let the
            # operator correct and re-submit. Transport/5xx failures stay locked
            # because DRES may already have received the answer.
            if exc.status_code is not None and 400 <= exc.status_code < 500:
                self.db.delete(receipt)
            else:
                receipt.status = "UNKNOWN"
                receipt.response = {"error": str(exc)}
            self.db.commit()
            raise

        receipt.status = "SUBMITTED"
        receipt.response = response if isinstance(response, dict) else {"response": response}
        self.db.commit()
        return {
            "evaluation_id": evaluation.id,
            "evaluation_name": evaluation.name,
            "media_item_name": row.video_code,
            "timestamp_ms": int(frame.timestamp_ms),
            "dres_response": receipt.response,
        }

    # Retain the old method name for integrations created before QA submission
    # support. The wire format is selected from SubmissionRow.query_type.
    def submit_kis(self, dataset_id: str, rows: list[SubmissionRow]) -> dict[str, Any]:
        return self.submit(dataset_id, rows)

    def _evaluation_name_for(self, query_type: str) -> str:
        configured = {
            "KIS": self.settings.dres_kis_evaluation_name,
            "QA": self.settings.dres_qa_evaluation_name,
            "TRAKE": self.settings.dres_trake_evaluation_name,
        }[query_type]
        # Preserve the original one-evaluation configuration for KIS users.
        return configured or self.settings.dres_evaluation_name

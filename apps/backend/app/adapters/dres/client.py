from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx


class DresConfigurationError(RuntimeError):
    pass


class DresRemoteError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class DresEvaluation:
    id: str
    name: str
    status: str


class DresClient:
    """Small backend-only client for the official DRES v2 participant API."""

    def __init__(self, *, base_url: str, session_id: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id.strip()
        self.timeout_seconds = timeout_seconds

    def _require_session(self) -> None:
        if not self.session_id:
            raise DresConfigurationError("DRES_SESSION_ID is not configured on the backend.")

    def active_evaluation(self, expected_name: str) -> DresEvaluation:
        self._require_session()
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False) as client:
                response = client.get(
                    f"{self.base_url}/client/evaluation/list",
                    params={"session": self.session_id},
                )
        except httpx.HTTPError as exc:
            raise DresRemoteError("Could not reach DRES while listing evaluations.") from exc
        evaluations = self._json_response(response, "DRES rejected evaluation lookup")
        if not isinstance(evaluations, list):
            raise DresRemoteError("DRES returned an invalid evaluation list.")

        active = [
            DresEvaluation(str(item.get("id", "")), str(item.get("name", "")), str(item.get("status", "")))
            for item in evaluations
            if isinstance(item, dict)
            and str(item.get("status", "")).upper() == "ACTIVE"
            and item.get("id")
        ]
        if expected_name:
            active = [item for item in active if item.name == expected_name]
            if not active:
                raise DresRemoteError(
                    "The configured DRES evaluation is not active or its name does not match."
                )
        if len(active) != 1:
            raise DresRemoteError(
                "DRES must expose exactly one target active evaluation; set DRES_EVALUATION_NAME."
            )
        return active[0]

    def submit_kis(self, evaluation_id: str, payload: dict[str, Any]) -> Any:
        self._require_session()
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False) as client:
                response = client.post(
                    f"{self.base_url}/submit/{quote(evaluation_id, safe='')}",
                    params={"session": self.session_id},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise DresRemoteError("DRES submission outcome is unknown; do not retry this frame automatically.") from exc
        return self._json_response(response, "DRES rejected submission")

    @staticmethod
    def _json_response(response: httpx.Response, error_prefix: str) -> Any:
        if not response.is_success:
            detail = response.text.strip().replace("\n", " ")[:500]
            raise DresRemoteError(
                f"{error_prefix} ({response.status_code})" + (f": {detail}" if detail else ""),
                status_code=response.status_code,
            )
        try:
            return response.json()
        except ValueError:
            return {"message": response.text.strip()[:500]}

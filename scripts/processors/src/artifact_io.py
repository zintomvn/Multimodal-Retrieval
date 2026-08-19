from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class GcsUri:
    """Parsed Google Cloud Storage URI."""

    bucket: str
    blob: str


def is_gcs_uri(uri: str) -> bool:
    """Return whether the URI points to Google Cloud Storage."""
    return str(uri or "").startswith("gs://")


def parse_gcs_uri(uri: str) -> GcsUri:
    """Parse a gs://bucket/blob URI."""
    raw = str(uri or "").strip()
    if not raw.startswith("gs://"):
        raise ValueError(f"Expected gs:// URI, got: {uri}")
    tail = raw[len("gs://") :].strip("/")
    if "/" not in tail:
        raise ValueError(f"GCS URI must include object key: {uri}")
    bucket, blob = tail.split("/", 1)
    if not bucket or not blob:
        raise ValueError(f"Invalid GCS URI: {uri}")
    return GcsUri(bucket=bucket, blob=blob.strip("/"))


def join_uri(root: str, *parts: str) -> str:
    """Join URI/path parts without introducing duplicate separators."""
    cleaned = [str(part).replace("\\", "/").strip("/") for part in parts if str(part or "").strip("/")]
    if is_gcs_uri(root):
        return f"{root.rstrip('/')}/{'/'.join(cleaned)}" if cleaned else root.rstrip("/")
    return str(Path(root, *cleaned))


def gcs_client(credentials_file: str = ""):
    """Create a GCS client from a file, service-account JSON env, or ADC."""
    from google.cloud import storage

    resolved_file = str(credentials_file or "").strip()
    if resolved_file:
        return storage.Client.from_service_account_json(os.path.expanduser(os.path.expandvars(resolved_file)))

    service_account_json = os.getenv("GCS_SERVICE_ACCOUNT_JSON", "").strip()
    if service_account_json:
        try:
            return storage.Client.from_service_account_info(json.loads(service_account_json))
        except json.JSONDecodeError:
            possible_path = Path(os.path.expanduser(os.path.expandvars(service_account_json)))
            if possible_path.exists():
                return storage.Client.from_service_account_json(str(possible_path))
            raise
    return storage.Client()


class ArtifactStore:
    """Read and write local or GCS JSON/JSONL artifacts."""

    def __init__(self, credentials_file: str = "", timeout_seconds: float = 60.0) -> None:
        self.credentials_file = credentials_file
        self.timeout_seconds = timeout_seconds
        self._gcs_client = None

    def exists(self, uri: str) -> bool:
        """Return whether an artifact exists."""
        if is_gcs_uri(uri):
            parsed = parse_gcs_uri(uri)
            return self._client().bucket(parsed.bucket).blob(parsed.blob).exists(timeout=self.timeout_seconds)
        return Path(uri).exists()

    def read_text(self, uri: str) -> str:
        """Read a UTF-8 text artifact."""
        if is_gcs_uri(uri):
            parsed = parse_gcs_uri(uri)
            blob = self._client().bucket(parsed.bucket).blob(parsed.blob)
            blob.reload(timeout=self.timeout_seconds)
            return blob.download_as_text(
                encoding="utf-8",
                timeout=self.timeout_seconds,
            )
        return Path(uri).read_text(encoding="utf-8-sig")

    def write_text(self, uri: str, text: str, content_type: str = "text/plain") -> None:
        """Write a UTF-8 text artifact with local atomic replace when possible."""
        if is_gcs_uri(uri):
            parsed = parse_gcs_uri(uri)
            self._client().bucket(parsed.bucket).blob(parsed.blob).upload_from_string(
                text,
                content_type=content_type,
                timeout=self.timeout_seconds,
            )
            return

        path = Path(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            Path(tmp_name).replace(path)
        finally:
            Path(tmp_name).unlink(missing_ok=True)

    def read_json(self, uri: str, default: Any | None = None) -> Any:
        """Read a JSON artifact, returning default if it does not exist."""
        if not self.exists(uri):
            return default
        return json.loads(self.read_text(uri))

    def write_json(self, uri: str, payload: Any) -> None:
        """Write a JSON artifact."""
        self.write_text(
            uri,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            content_type="application/json",
        )

    def read_jsonl(self, uri: str) -> list[dict[str, Any]]:
        """Read a JSONL artifact into dictionaries."""
        text = self.read_text(uri)
        rows: list[dict[str, Any]] = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected object at {uri}:{line_no}")
            rows.append(value)
        return rows

    def write_jsonl(self, uri: str, rows: Iterable[dict[str, Any]]) -> None:
        """Write dictionaries as JSONL."""
        text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
        self.write_text(uri, text, content_type="application/jsonl")

    def list_jsonl(self, uri_or_prefix: str) -> list[str]:
        """Return JSONL files under a local or GCS URI/prefix."""
        if is_gcs_uri(uri_or_prefix):
            parsed = parse_gcs_uri(uri_or_prefix)
            if parsed.blob.endswith(".jsonl") and self.exists(uri_or_prefix):
                return [uri_or_prefix]
            blobs = self._client().list_blobs(parsed.bucket, prefix=parsed.blob.rstrip("/") + "/", timeout=self.timeout_seconds)
            return [f"gs://{parsed.bucket}/{blob.name}" for blob in blobs if blob.name.endswith(".jsonl")]

        path = Path(uri_or_prefix)
        if path.is_file() and path.suffix == ".jsonl":
            return [str(path)]
        if not path.exists():
            return []
        return [str(item) for item in sorted(path.rglob("*.jsonl"))]

    def _client(self):
        if self._gcs_client is not None:
            return self._gcs_client
        self._gcs_client = gcs_client(self.credentials_file)
        return self._gcs_client

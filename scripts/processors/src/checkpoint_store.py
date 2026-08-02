from __future__ import annotations

import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .artifact_io import ArtifactStore, join_uri


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def utc_epoch() -> float:
    """Return current UTC epoch seconds."""
    return time.time()


def default_worker_id(prefix: str = "worker") -> str:
    """Create a readable worker id for notebook sessions."""
    host = socket.gethostname().replace(" ", "-")[:32]
    return f"{prefix}-{host}-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class LeaseResult:
    """Result of trying to claim a shard lease."""

    claimed: bool
    owner: str
    expires_at_epoch: float


class CheckpointStore:
    """Checkpoint and lease manager backed by local files or GCS."""

    def __init__(
        self,
        root_uri: str,
        run_id: str,
        credentials_file: str = "",
        lease_ttl_seconds: int = 2700,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.root_uri = root_uri.rstrip("/")
        self.run_id = run_id
        self.lease_ttl_seconds = int(lease_ttl_seconds)
        self.store = ArtifactStore(credentials_file=credentials_file, timeout_seconds=timeout_seconds)

    def checkpoint_uri(self, stage: str, shard_id: str) -> str:
        return join_uri(self.root_uri, f"run_id={self.run_id}", f"stage={stage}", f"shard_id={shard_id}", "checkpoint.json")

    def lease_uri(self, stage: str, shard_id: str) -> str:
        return join_uri(self.root_uri, f"run_id={self.run_id}", f"stage={stage}", f"shard_id={shard_id}", "lease.json")

    def complete_uri(self, stage: str, shard_id: str) -> str:
        return join_uri(self.root_uri, f"run_id={self.run_id}", f"stage={stage}", f"shard_id={shard_id}", "_SUCCESS.json")

    def load(self, stage: str, shard_id: str) -> dict[str, Any]:
        """Load checkpoint state for a shard."""
        return self.store.read_json(self.checkpoint_uri(stage, shard_id), default={}) or {}

    def can_write(self, stage: str, shard_id: str, worker_id: str) -> bool:
        """Return whether the current worker may update checkpoint state."""
        lease = self.store.read_json(self.lease_uri(stage, shard_id), default={}) or {}
        now = utc_epoch()
        owner = str(lease.get("worker_id") or "")
        expires_at = float(lease.get("expires_at_epoch") or 0.0)
        return not (owner and owner != worker_id and expires_at > now)

    def save(self, stage: str, shard_id: str, payload: dict[str, Any]) -> None:
        """Persist checkpoint state for a shard."""
        worker_id = str(payload.get("worker_id") or "")
        if worker_id and not self.can_write(stage, shard_id, worker_id):
            raise RuntimeError(f"Lease lost for stage={stage} shard_id={shard_id} worker_id={worker_id}")
        checkpoint = {
            **payload,
            "run_id": self.run_id,
            "stage": stage,
            "shard_id": shard_id,
            "updated_at": utc_now(),
        }
        self.store.write_json(self.checkpoint_uri(stage, shard_id), checkpoint)

    def is_complete(self, stage: str, shard_id: str) -> bool:
        """Return whether the shard has a terminal success marker."""
        return self.store.exists(self.complete_uri(stage, shard_id))

    def mark_complete(self, stage: str, shard_id: str, payload: dict[str, Any]) -> None:
        """Write a terminal success marker for a shard."""
        worker_id = str(payload.get("worker_id") or "")
        if worker_id and not self.can_write(stage, shard_id, worker_id):
            raise RuntimeError(f"Lease lost for stage={stage} shard_id={shard_id} worker_id={worker_id}")
        marker = {
            **payload,
            "run_id": self.run_id,
            "stage": stage,
            "shard_id": shard_id,
            "status": "COMPLETED",
            "completed_at": utc_now(),
        }
        self.store.write_json(self.complete_uri(stage, shard_id), marker)

    def try_claim(self, stage: str, shard_id: str, worker_id: str) -> LeaseResult:
        """Claim a shard lease if no active owner exists."""
        uri = self.lease_uri(stage, shard_id)
        lease = self.store.read_json(uri, default={}) or {}
        now = utc_epoch()
        owner = str(lease.get("worker_id") or "")
        expires_at = float(lease.get("expires_at_epoch") or 0.0)
        if owner and owner != worker_id and expires_at > now:
            return LeaseResult(claimed=False, owner=owner, expires_at_epoch=expires_at)
        new_lease = {
            "run_id": self.run_id,
            "stage": stage,
            "shard_id": shard_id,
            "worker_id": worker_id,
            "claimed_at": lease.get("claimed_at") or utc_now(),
            "heartbeat_at": utc_now(),
            "expires_at_epoch": now + self.lease_ttl_seconds,
        }
        self.store.write_json(uri, new_lease)
        return LeaseResult(claimed=True, owner=worker_id, expires_at_epoch=new_lease["expires_at_epoch"])

    def heartbeat(self, stage: str, shard_id: str, worker_id: str) -> None:
        """Refresh the active shard lease."""
        if not self.can_write(stage, shard_id, worker_id):
            raise RuntimeError(f"Lease lost for stage={stage} shard_id={shard_id} worker_id={worker_id}")
        now = utc_epoch()
        lease = {
            "run_id": self.run_id,
            "stage": stage,
            "shard_id": shard_id,
            "worker_id": worker_id,
            "heartbeat_at": utc_now(),
            "expires_at_epoch": now + self.lease_ttl_seconds,
        }
        self.store.write_json(self.lease_uri(stage, shard_id), lease)

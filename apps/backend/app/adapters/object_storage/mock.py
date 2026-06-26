from __future__ import annotations


class InMemoryObjectStorageClient:
    """In-memory object storage for mock/dev mode. Not safe for concurrent use."""

    def __init__(self, public_base_url: str = "http://localhost:9000/mock") -> None:
        self._store: dict[str, bytes] = {}
        self._public_base_url = public_base_url.rstrip("/")

    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self._store[key] = data
        return key

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return f"{self._public_base_url}/{key}?expires_in={expires_in}"

    def delete_object(self, key: str) -> None:
        self._store.pop(key, None)

    def public_url(self, key: str) -> str:
        return f"{self._public_base_url}/{key}"

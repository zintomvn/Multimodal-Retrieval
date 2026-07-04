from __future__ import annotations

from pathlib import Path


class LocalObjectStorageClient:
    """Local file system object storage for dev mode."""

    def __init__(self, data_root: Path | str, public_base_url: str = "/api/media/static") -> None:
        self.data_root = Path(data_root)
        self.public_base_url = public_base_url.rstrip("/")
        self.data_root.mkdir(parents=True, exist_ok=True)

    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        file_path = self.data_root / key
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(data)
        return key

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return self.public_url(key)

    def delete_object(self, key: str) -> None:
        file_path = self.data_root / key
        if file_path.exists():
            file_path.unlink()

    def public_url(self, key: str) -> str:
        return f"{self.public_base_url}/{key}"

    def list_objects(self, prefix: str) -> list[str]:
        base = self.data_root / prefix
        if not base.is_dir():
            return []
        return [
            str(path.relative_to(self.data_root).as_posix())
            for path in base.rglob("*")
            if path.is_file()
        ]

    def download_to_file(self, key: str, local_path: Path | str) -> None:
        import shutil

        dest = Path(local_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = self.data_root / key
        if not src.exists():
            raise FileNotFoundError(f"Object not found: {key}")
        shutil.copy2(str(src), str(dest))

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from botocore.config import Config

if TYPE_CHECKING:
    pass


class R2ObjectStorageClient:
    """Cloudflare R2 object storage via boto3 S3-compatible API.

    R2 quirks vs AWS S3: endpoint_url required, region must be "auto",
    signature_version must be s3v4.
    """

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        public_base_url: str = "",
    ) -> None:
        import boto3

        self._bucket = bucket
        self._public_base_url = public_base_url.rstrip("/")
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )

    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)
        return key

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def delete_object(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def public_url(self, key: str) -> str:
        if self._public_base_url:
            return f"{self._public_base_url}/{key}"
        return self.get_presigned_url(key)

    def list_objects(self, prefix: str) -> list[str]:
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    def download_to_file(self, key: str, local_path) -> None:
        from pathlib import Path as _Path

        dest = _Path(local_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self._bucket, key, str(dest))


@lru_cache(maxsize=1)
def get_r2_client() -> R2ObjectStorageClient:
    from app.core.config import get_settings

    s = get_settings()
    return R2ObjectStorageClient(
        endpoint=s.s3_endpoint,
        access_key=s.s3_access_key,
        secret_key=s.s3_secret_key,
        bucket=s.s3_bucket,
        public_base_url=s.r2_public_url,
    )

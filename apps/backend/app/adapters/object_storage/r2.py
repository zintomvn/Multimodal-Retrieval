from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from botocore.config import Config

if TYPE_CHECKING:
    import boto3 as boto3_type


@lru_cache(maxsize=1)
def get_r2_client() -> "boto3_type.client":
    """Return a boto3 S3 client configured for Cloudflare R2.

    R2 is S3-compatible but requires endpoint_url, signature_version s3v4,
    and region_name "auto" (AWS region names are rejected).
    """
    import boto3

    from app.core.config import get_settings

    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )

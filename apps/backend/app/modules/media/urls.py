from __future__ import annotations

from urllib.parse import quote


def split_gcs_uri(value: str, default_bucket: str = "") -> tuple[str, str] | None:
    """Return a `(bucket, key)` pair for `gs://...` URIs or bare GCS object keys."""
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.startswith("gs://"):
        tail = raw[5:].strip("/")
        if "/" not in tail:
            return None
        bucket, key = tail.split("/", 1)
        return (bucket.strip(), key.strip("/")) if bucket and key else None
    if "://" in raw or raw.startswith("/"):
        return None
    bucket = default_bucket.strip()
    key = raw.strip("/")
    return (bucket, key) if bucket and key else None


def gcs_public_url(value: str, default_bucket: str = "", public_base_url: str = "") -> str | None:
    """Convert a GCS URI or object key into a browser-loadable public URL."""
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://") or raw.startswith("/"):
        return raw
    parsed = split_gcs_uri(raw, default_bucket=default_bucket)
    if parsed is None:
        return None
    bucket, key = parsed
    encoded_key = quote(key, safe="/")
    if public_base_url and (not default_bucket or bucket == default_bucket):
        return f"{public_base_url.rstrip('/')}/{encoded_key}"
    return f"https://storage.googleapis.com/{bucket}/{encoded_key}"

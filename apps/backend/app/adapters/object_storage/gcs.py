from __future__ import annotations

from datetime import timedelta


class GCSObjectStorageClient:
    """Google Cloud Storage adapter implementing ObjectStorageClient Protocol.

    Auth priority:
    - If GCS_CREDENTIALS_FILE is set → load service account JSON (required for signed URLs).
    - Otherwise → Application Default Credentials (ADC); signed URLs fall back to public_url().
    """

    def __init__(
        self,
        bucket: str,
        credentials_file: str = "",
        public_base_url: str = "",
    ) -> None:
        from google.cloud import storage as gcs

        self._bucket_name = bucket
        self._public_base_url = public_base_url.rstrip("/")
        self._credentials_file = credentials_file

        if credentials_file:
            self._client = gcs.Client.from_service_account_json(credentials_file)
        else:
            self._client = gcs.Client()  # ADC

        self._bucket = self._client.bucket(bucket)

    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        blob = self._bucket.blob(key)
        blob.upload_from_string(data, content_type=content_type)
        return key

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        if not self._credentials_file:
            # ADC cannot sign URLs without impersonation; fall back to public URL.
            return self.public_url(key)
        blob = self._bucket.blob(key)
        return blob.generate_signed_url(
            expiration=timedelta(seconds=expires_in),
            method="GET",
            version="v4",
        )

    def delete_object(self, key: str) -> None:
        blob = self._bucket.blob(key)
        blob.delete(if_generation_match=None)

    def public_url(self, key: str) -> str:
        if self._public_base_url:
            return f"{self._public_base_url}/{key}"
        return f"https://storage.googleapis.com/{self._bucket_name}/{key}"

"""Upload videos from a Kaggle dataset directly to Google Cloud Storage.

Run this inside a Kaggle Notebook (dataset already mounted, no local download needed):

  Cell 1:
    !pip install google-cloud-storage tqdm -q

  Cell 2:
    !python upload_kaggle_to_gcs.py
    # or paste the script body directly into the cell

Kaggle Secrets required:
  GCS_CREDENTIALS_JSON  — full JSON content of GCS service account key
  GCS_BUCKET            — GCS bucket name

Settings (edit the CONFIG block below):
  GCS_PREFIX   — key prefix in the bucket, e.g. "aic2025/videos"
  VIDEOS_DIR   — path to the videos root inside the Kaggle input mount
  VIDEO_EXTS   — file extensions to upload
  SKIP_EXISTING — if True, skip files already present in GCS (safe to re-run)
  WORKERS      — parallel upload threads (4 is safe; raise to 8 on fast connections)
"""
from __future__ import annotations

import mimetypes
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────────────────────
GCS_PREFIX    = "aic2025/videos"
VIDEOS_DIR    = Path("/kaggle/input/ai-challenge-2025")   # adjust subfolder if needed
VIDEO_EXTS    = {".mp4", ".avi", ".mov", ".mkv"}
SKIP_EXISTING = True
WORKERS       = 4
# ─────────────────────────────────────────────────────────────────────────────


def _load_secrets() -> tuple[str, str]:
    """Load GCS credentials from Kaggle Secrets."""
    try:
        from kaggle_secrets import UserSecretsClient
        s = UserSecretsClient()
        return s.get_secret("GCS_BUCKET"), s.get_secret("GCS_CREDENTIALS_JSON")
    except Exception:
        # Fallback: read from environment (useful when running on a plain VM)
        bucket = os.environ.get("GCS_BUCKET", "")
        creds  = os.environ.get("GCS_CREDENTIALS_JSON", "")
        if not bucket or not creds:
            raise RuntimeError(
                "Set GCS_BUCKET and GCS_CREDENTIALS_JSON as Kaggle Secrets "
                "or environment variables."
            )
        return bucket, creds


def _make_bucket(bucket_name: str, creds_json: str):
    from google.cloud import storage

    creds_path = Path("/tmp/gcs_creds.json")
    creds_path.write_text(creds_json)
    client = storage.Client.from_service_account_json(str(creds_path))
    return client.bucket(bucket_name)


def _upload_one(bucket, local_path: Path, gcs_key: str) -> str:
    blob = bucket.blob(gcs_key)
    if SKIP_EXISTING and blob.exists():
        return "skip"
    ct = mimetypes.guess_type(local_path.name)[0] or "video/mp4"
    blob.upload_from_filename(str(local_path), content_type=ct)   # streaming — no OOM
    return "ok"


def main() -> None:
    print("Loading GCS credentials...")
    bucket_name, creds_json = _load_secrets()
    bucket = _make_bucket(bucket_name, creds_json)
    print(f"  bucket: gs://{bucket_name}/{GCS_PREFIX}/")

    print(f"\nScanning {VIDEOS_DIR} ...")
    video_files = sorted(
        p for p in VIDEOS_DIR.rglob("*")
        if p.suffix.lower() in VIDEO_EXTS
    )
    print(f"  found {len(video_files)} video files")
    if not video_files:
        print("Nothing to upload. Check VIDEOS_DIR and VIDEO_EXTS.")
        return

    ok = skip = err = 0
    errors: list[tuple[str, str]] = []

    try:
        from tqdm import tqdm
        progress = tqdm(total=len(video_files), unit="file")
    except ImportError:
        progress = None

    def _done(n: int, status: str) -> None:
        if progress:
            progress.set_postfix(ok=ok, skip=skip, err=err, refresh=False)
            progress.update(1)
        else:
            print(f"  [{n}/{len(video_files)}] {status}")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {}
        for p in video_files:
            rel     = p.relative_to(VIDEOS_DIR).as_posix()
            gcs_key = f"{GCS_PREFIX}/{rel}"
            futures[pool.submit(_upload_one, bucket, p, gcs_key)] = (p, gcs_key)

        for i, fut in enumerate(as_completed(futures), 1):
            local_p, gcs_key = futures[fut]
            try:
                status = fut.result()
                if status == "skip":
                    skip += 1
                else:
                    ok += 1
                _done(i, f"{status}: {gcs_key}")
            except Exception as exc:
                err += 1
                errors.append((str(local_p), str(exc)))
                _done(i, f"ERROR: {local_p.name}")

    if progress:
        progress.close()

    print(f"\n{'='*55}")
    print(f"  Uploaded : {ok}")
    print(f"  Skipped  : {skip}  (already on GCS)")
    print(f"  Errors   : {err}")
    print(f"{'='*55}")
    for path, exc in errors:
        print(f"  ERROR  {path}\n         {exc}")

    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

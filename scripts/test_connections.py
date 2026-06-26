"""Test connections to Milvus, PostgreSQL, and Cloudflare R2."""
import os
import sys
from pathlib import Path

# Load .env from project root
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

sys.path.insert(0, str(Path(__file__).parent.parent / "apps/backend"))

from app.core.config import get_settings

settings = get_settings()
results = []

print("=" * 60)
print("CONNECTION DIAGNOSTIC")
print(f"Env: {settings.app_env}  |  Mock: {settings.mock_mode}")
print("=" * 60)
print()


# PostgreSQL
print("--- PostgreSQL ------------------------")
try:
    from sqlalchemy import text
    from app.db.session import SessionLocal

    db = SessionLocal()
    result = db.execute(text("SELECT 1"))
    row = result.scalar()
    db.close()
    if row == 1:
        print("  [OK] Connection OK - basic query passed")
        results.append(("PostgreSQL", True))
    else:
        print("  [FAIL] Query returned unexpected result")
        results.append(("PostgreSQL", False))
except Exception as e:
    print(f"  [FAIL] {e}")
    results.append(("PostgreSQL", False))


# Milvus (Zilliz Cloud)
print()
print("--- Milvus (Zilliz Cloud) -------------")
try:
    from pymilvus import MilvusClient

    client = MilvusClient(uri=settings.milvus_uri, token=settings.milvus_token)
    info = client.get_collection_stats("frame_embeddings_mock_aic_2026_clip_mock")
    print(f"  [OK] Connection OK - collection exists, stats: {info}")
    results.append(("Milvus", True))
except Exception:
    try:
        client = MilvusClient(uri=settings.milvus_uri, token=settings.milvus_token)
        collections = client.list_collections()
        print(f"  [OK] Connection OK - collections: {collections}")
        results.append(("Milvus", True))
    except Exception as e:
        print(f"  [FAIL] {e}")
        results.append(("Milvus", False))


# Cloudflare R2
print()
print("--- Cloudflare R2 -----------------------")
try:
    from botocore.config import Config
    import boto3

    r2 = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )
    buckets = r2.list_buckets()
    bucket_names = [b["Name"] for b in buckets.get("Buckets", [])]
    target = settings.s3_bucket
    if target in bucket_names:
        print(f"  [OK] Connection OK - bucket '{target}' found")
        results.append(("Cloudflare R2", True))
    else:
        print(f"  [WARN] Connected, bucket '{target}' not found. Available: {bucket_names}")
        results.append(("Cloudflare R2", True))
except Exception as e:
    print(f"  [FAIL] {e}")
    results.append(("Cloudflare R2", False))


# Summary
print()
print("=" * 60)
print("SUMMARY")
print("=" * 60)
all_ok = True
for name, ok in results:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}]  {name}")
    if not ok:
        all_ok = False

print()
if all_ok:
    print("All connections successful!")
else:
    print("Some connections failed - check errors above.")
print()

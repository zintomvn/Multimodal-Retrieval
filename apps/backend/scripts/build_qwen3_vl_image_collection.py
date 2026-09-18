#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import math
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import httpx
import numpy as np
from PIL import Image
from pymilvus import DataType, MilvusClient


OPENCLIP_COLLECTION = "keyframe_embeddings_clip_vith14_quickgelu_dfn5b_v2"
SIGLIP2_COLLECTION = "keyframe_embeddings_siglip2_so400m16_384_webli_openclip_1152_v1"
QWEN_COLLECTION = "keyframe_embeddings_qwen3_vl_embedding_2b_2048_v1"
QWEN_MODEL = "Qwen/Qwen3-VL-Embedding-2B"
QWEN_DIMENSION = 2048


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or resume the canonical Qwen3-VL image-vector collection from SQLite keyframes."
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--collection", default=QWEN_COLLECTION)
    parser.add_argument("--model", default=QWEN_MODEL)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--download-workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--timeout-s", type=float, default=60.0)
    parser.add_argument("--checkpoint", type=Path, default=Path("qwen3_vl_ingest_checkpoint.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-baseline-count-mismatch", action="store_true")
    return parser.parse_args()


def chunks(values: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def vector_dimension(description: dict[str, Any]) -> int:
    field = next(item for item in description.get("fields", []) if item.get("name") == "vector")
    params = field.get("params") or {}
    return int(params.get("dim") or field.get("dim") or 0)


def collection_count(client: MilvusClient, name: str, expected_dim: int) -> int:
    if not client.has_collection(collection_name=name):
        raise RuntimeError(f"Required baseline collection does not exist: {name}")
    actual_dim = vector_dimension(client.describe_collection(collection_name=name))
    if actual_dim != expected_dim:
        raise RuntimeError(f"{name}: expected dim={expected_dim}, got {actual_dim}")
    return int(client.get_collection_stats(collection_name=name).get("row_count") or 0)


def ensure_qwen_collection(client: MilvusClient, name: str) -> None:
    if client.has_collection(collection_name=name):
        actual_dim = vector_dimension(client.describe_collection(collection_name=name))
        if actual_dim != QWEN_DIMENSION:
            raise RuntimeError(f"{name}: expected dim={QWEN_DIMENSION}, got {actual_dim}")
        return

    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=True)
    schema.add_field(field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=128)
    schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=QWEN_DIMENSION)
    index_params = MilvusClient.prepare_index_params()
    index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
    client.create_collection(collection_name=name, schema=schema, index_params=index_params)


def load_keyframes(database: Path, offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
    if not database.exists():
        raise FileNotFoundError(database)
    connection = sqlite3.connect(str(database))
    connection.row_factory = sqlite3.Row
    total = int(connection.execute("SELECT COUNT(*) FROM keyframes WHERE is_media_present = 1").fetchone()[0])
    sql = """
        SELECT keyframe_id, video_id, frame_idx, frame_seconds, timestamp_ms,
               image_url, image_uri, image_storage_key
        FROM keyframes
        WHERE is_media_present = 1
        ORDER BY keyframe_id
        LIMIT ? OFFSET ?
    """
    query_limit = limit if limit > 0 else max(0, total - offset)
    rows = [dict(row) for row in connection.execute(sql, (query_limit, offset)).fetchall()]
    connection.close()
    return rows, total


def public_url(row: dict[str, Any]) -> str:
    value = str(row.get("image_url") or "").strip()
    if value.startswith(("http://", "https://")):
        return value
    uri = str(row.get("image_uri") or "").strip()
    if uri.startswith("gs://"):
        bucket_and_key = uri[5:].split("/", 1)
        if len(bucket_and_key) == 2:
            return f"https://storage.googleapis.com/{bucket_and_key[0]}/{quote(bucket_and_key[1], safe='/')}"
    raise ValueError(f"No usable image URL for {row.get('keyframe_id')}")


class ImageDownloader:
    def __init__(self, timeout_s: float) -> None:
        self.timeout_s = timeout_s

    def __call__(self, row: dict[str, Any]) -> Image.Image:
        url = public_url(row)
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = httpx.get(url, timeout=self.timeout_s, follow_redirects=True)
                response.raise_for_status()
                image = Image.open(io.BytesIO(response.content)).convert("RGB")
                image.load()
                return image
            except Exception as exc:  # noqa: BLE001 - preserve the final transport/image error
                last_error = exc
                time.sleep(2**attempt)
        raise RuntimeError(f"Failed to download {row.get('keyframe_id')} from {url}: {last_error}")


def existing_ids(client: MilvusClient, collection: str, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    rows = client.get(collection_name=collection, ids=ids, output_fields=["id"])
    return {str(row.get("id")) for row in rows}


def write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    if args.batch_size < 1 or args.download_workers < 1:
        raise ValueError("batch-size and download-workers must be positive")
    uri = os.getenv("MILVUS_URI", "").strip()
    token = os.getenv("MILVUS_TOKEN", "").strip()
    if not uri or not token:
        raise RuntimeError("MILVUS_URI and MILVUS_TOKEN are required")

    keyframes, source_total = load_keyframes(args.database, args.offset, args.limit)
    client = MilvusClient(uri=uri, token=token, timeout=30)
    openclip_count = collection_count(client, OPENCLIP_COLLECTION, 1024)
    siglip2_count = collection_count(client, SIGLIP2_COLLECTION, 1152)
    if openclip_count != siglip2_count:
        raise RuntimeError(
            f"Baseline corpus mismatch: OpenCLIP={openclip_count}, SigLIP2={siglip2_count}. "
            "A controlled ablation requires identical keyframes."
        )
    if source_total != openclip_count and not args.allow_baseline_count_mismatch:
        raise RuntimeError(
            f"Canonical SQLite keyframes={source_total}, baseline vectors={openclip_count}. "
            "Fix corpus parity before Qwen ingest or explicitly pass --allow-baseline-count-mismatch."
        )

    print(
        json.dumps(
            {
                "source_total": source_total,
                "selected": len(keyframes),
                "offset": args.offset,
                "openclip_vectors": openclip_count,
                "siglip2_vectors": siglip2_count,
                "target_collection": args.collection,
            },
            ensure_ascii=False,
        )
    )
    if args.dry_run:
        return

    ensure_qwen_collection(client, args.collection)
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(args.model, device=args.device, trust_remote_code=True)
    downloader = ImageDownloader(args.timeout_s)
    processed = 0
    skipped = 0
    started = time.perf_counter()

    with ThreadPoolExecutor(max_workers=args.download_workers) as executor:
        for batch_number, batch in enumerate(chunks(keyframes, args.batch_size), start=1):
            ids = [str(row["keyframe_id"]) for row in batch]
            already_present = existing_ids(client, args.collection, ids)
            pending = [row for row in batch if str(row["keyframe_id"]) not in already_present]
            skipped += len(batch) - len(pending)
            if not pending:
                continue

            images = list(executor.map(downloader, pending))
            vectors = model.encode(
                images,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
                batch_size=len(images),
            )
            vectors = np.asarray(vectors, dtype=np.float32)
            if vectors.ndim != 2 or vectors.shape != (len(pending), QWEN_DIMENSION):
                raise RuntimeError(f"Unexpected Qwen embedding shape: {vectors.shape}")
            if not np.isfinite(vectors).all():
                raise RuntimeError("Qwen embeddings contain NaN or Inf")
            norms = np.linalg.norm(vectors, axis=1)
            if not np.all(np.isfinite(norms)) or not np.allclose(norms, 1.0, atol=1e-3):
                raise RuntimeError("Qwen embeddings are not L2-normalized")

            payload = []
            for row, vector in zip(pending, vectors):
                keyframe_id = str(row["keyframe_id"])
                payload.append(
                    {
                        "id": keyframe_id,
                        "vector": vector.tolist(),
                        "keyframe_id": keyframe_id,
                        "frame_id": keyframe_id,
                        "video_id": str(row["video_id"]),
                        "frame_idx": int(row["frame_idx"]),
                        "frame_seconds": float(row["frame_seconds"]),
                        "timestamp_ms": int(row["timestamp_ms"]),
                        "image_uri": str(row.get("image_uri") or ""),
                        "extractor_version": "qwen3-vl-embedding-2b-2048-v1",
                        "model_name": args.model,
                        "embedding_dim": QWEN_DIMENSION,
                    }
                )
            client.upsert(collection_name=args.collection, data=payload)
            processed += len(payload)
            elapsed = time.perf_counter() - started
            rate = processed / elapsed if elapsed > 0 else 0.0
            remaining = max(0, len(keyframes) - processed - skipped)
            checkpoint = {
                "batch": batch_number,
                "processed_this_run": processed,
                "skipped_existing": skipped,
                "selected": len(keyframes),
                "frames_per_second": rate,
                "estimated_remaining_hours": (remaining / rate / 3600.0) if rate > 0 else math.inf,
                "last_keyframe_id": payload[-1]["id"],
            }
            write_checkpoint(args.checkpoint, checkpoint)
            print(json.dumps(checkpoint, ensure_ascii=False))

    client.flush(collection_name=args.collection)
    final_count = int(client.get_collection_stats(collection_name=args.collection).get("row_count") or 0)
    elapsed = time.perf_counter() - started
    rate = processed / elapsed if elapsed > 0 else 0.0
    remaining = max(0, openclip_count - final_count)
    summary = {
        "collection": args.collection,
        "processed_this_run": processed,
        "skipped_existing": skipped,
        "row_count": final_count,
        "baseline_count": openclip_count,
        "elapsed_seconds": elapsed,
        "frames_per_second": rate,
        "estimated_remaining_hours": (remaining / rate / 3600.0) if rate > 0 else math.inf,
        "complete": final_count == openclip_count,
    }
    write_checkpoint(args.checkpoint, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.limit <= 0 and final_count != openclip_count:
        raise RuntimeError(f"Qwen collection is incomplete: {final_count} != {openclip_count}")


if __name__ == "__main__":
    main()

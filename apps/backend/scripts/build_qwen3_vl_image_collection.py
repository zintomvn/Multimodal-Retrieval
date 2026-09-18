#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import torch
import torch.distributed as dist
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
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path("/kaggle/input/datasets/lcdngthnh/aic-2026"),
        help="Attached lcdngthnh/aic-2026 dataset root containing local keyframes/.",
    )
    parser.add_argument("--collection", default=QWEN_COLLECTION)
    parser.add_argument("--model", default=QWEN_MODEL)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--max-pixels", type=int, default=256 * 256)
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


def resolve_input_root(configured: Path) -> Path:
    candidates = [
        configured,
        Path("/kaggle/input/aic-2026"),
        Path("/kaggle/input/datasets/lcdngthnh/aic-2026"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Attach lcdngthnh/aic-2026; none of these roots exists: {candidates}")


def local_image_path(row: dict[str, Any], input_root: Path) -> Path:
    storage_key = str(row.get("image_storage_key") or "").replace("\\", "/").lstrip("/")
    uri = str(row.get("image_uri") or "").strip()
    if not storage_key and uri.startswith("gs://"):
        storage_key = uri[5:].split("/", 1)[1]
    tail = storage_key
    marker = "processed/keyframes/"
    if marker in tail:
        tail = tail.split(marker, 1)[1]
    candidates = [
        input_root / "keyframes" / tail,
        input_root / tail,
        input_root / storage_key,
        Path("/kaggle/input") / storage_key,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"Cannot resolve local image for {row.get('keyframe_id')} from {storage_key}; "
        f"checked: {[str(path) for path in candidates]}"
    )


def load_local_image(row: dict[str, Any], input_root: Path, max_pixels: int) -> Image.Image:
    path = local_image_path(row, input_root)
    with Image.open(path) as source:
        image = source.convert("RGB")
        width, height = image.size
        if max_pixels > 0 and width * height > max_pixels:
            scale = math.sqrt(max_pixels / float(width * height))
            size = (max(1, int(width * scale)), max(1, int(height * scale)))
            image = image.resize(size, Image.Resampling.LANCZOS)
        return image.copy()


def existing_ids(client: MilvusClient, collection: str, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    rows = client.get(collection_name=collection, ids=ids, output_fields=["id"])
    return {str(row.get("id")) for row in rows}


def write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def distributed_runtime(device_arg: str) -> tuple[int, int, str]:
    world_size = int(os.getenv("WORLD_SIZE", "1"))
    rank = int(os.getenv("RANK", "0"))
    local_rank = int(os.getenv("LOCAL_RANK", "0"))
    if world_size > 1:
        if not torch.cuda.is_available():
            raise RuntimeError("Multi-process ingest requires CUDA/NCCL")
        torch.cuda.set_device(local_rank)
        dist.init_process_group(backend="nccl")

    if device_arg == "auto":
        device = f"cuda:{local_rank}" if torch.cuda.is_available() else "cpu"
    elif device_arg == "cuda" and world_size > 1:
        device = f"cuda:{local_rank}"
    else:
        device = device_arg
    return rank, world_size, device


def reduce_totals(processed: int, skipped: int, elapsed: float, device: str) -> tuple[int, int, float]:
    if not dist.is_initialized():
        return processed, skipped, elapsed
    counts = torch.tensor([processed, skipped], dtype=torch.long, device=torch.device(device))
    duration = torch.tensor([elapsed], dtype=torch.float64, device=torch.device(device))
    dist.all_reduce(counts, op=dist.ReduceOp.SUM)
    dist.all_reduce(duration, op=dist.ReduceOp.MAX)
    return int(counts[0].item()), int(counts[1].item()), float(duration[0].item())


def main() -> None:
    args = parse_args()
    if args.batch_size < 1 or args.max_pixels < 1:
        raise ValueError("batch-size and max-pixels must be positive")
    uri = os.getenv("MILVUS_URI", "").strip()
    token = os.getenv("MILVUS_TOKEN", "").strip()
    if not uri or not token:
        raise RuntimeError("MILVUS_URI and MILVUS_TOKEN are required")

    rank, world_size, device = distributed_runtime(args.device)
    input_root = resolve_input_root(args.input_root)
    keyframes, source_total = load_keyframes(args.database, args.offset, args.limit)
    selected_total = len(keyframes)
    keyframes = keyframes[rank::world_size]
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

    if rank == 0:
        print(
            json.dumps(
                {
                    "source_total": source_total,
                    "selected": selected_total,
                    "offset": args.offset,
                    "openclip_vectors": openclip_count,
                    "siglip2_vectors": siglip2_count,
                    "target_collection": args.collection,
                    "input_root": str(input_root),
                    "world_size": world_size,
                    "batch_size_per_gpu": args.batch_size,
                    "max_pixels": args.max_pixels,
                },
                ensure_ascii=False,
            )
        )
    if args.dry_run:
        if dist.is_initialized():
            dist.destroy_process_group()
        return

    if rank == 0:
        ensure_qwen_collection(client, args.collection)
    if dist.is_initialized():
        dist.barrier()
    from sentence_transformers import SentenceTransformer

    model_kwargs: dict[str, Any] = {}
    if device.startswith("cuda"):
        model_kwargs = {"torch_dtype": torch.float16, "attn_implementation": "sdpa"}
    model = SentenceTransformer(
        args.model,
        device=device,
        trust_remote_code=True,
        model_kwargs=model_kwargs,
    )
    processed = 0
    skipped = 0
    started = time.perf_counter()

    for batch_number, batch in enumerate(chunks(keyframes, args.batch_size), start=1):
        ids = [str(row["keyframe_id"]) for row in batch]
        already_present = existing_ids(client, args.collection, ids)
        pending = [row for row in batch if str(row["keyframe_id"]) not in already_present]
        skipped += len(batch) - len(pending)
        if not pending:
            continue

        images = [load_local_image(row, input_root, args.max_pixels) for row in pending]
        try:
            vectors = model.encode(
                images,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
                batch_size=len(images),
            )
        finally:
            for image in images:
                image.close()
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
        if rank == 0:
            checkpoint = {
                "batch": batch_number,
                "rank": rank,
                "processed_this_run": processed,
                "skipped_existing": skipped,
                "selected_on_rank": len(keyframes),
                "frames_per_second": rate,
                "estimated_remaining_hours": (remaining / rate / 3600.0) if rate > 0 else math.inf,
                "last_keyframe_id": payload[-1]["id"],
            }
            write_checkpoint(args.checkpoint, checkpoint)
            print(json.dumps(checkpoint, ensure_ascii=False))

    client.flush(collection_name=args.collection)
    if dist.is_initialized():
        dist.barrier()
    elapsed = time.perf_counter() - started
    processed, skipped, elapsed = reduce_totals(processed, skipped, elapsed, device)
    if rank == 0:
        final_count = int(client.get_collection_stats(collection_name=args.collection).get("row_count") or 0)
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
            "world_size": world_size,
            "batch_size_per_gpu": args.batch_size,
            "max_pixels": args.max_pixels,
        }
        write_checkpoint(args.checkpoint, summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if args.limit <= 0 and final_count != openclip_count:
            raise RuntimeError(f"Qwen collection is incomplete: {final_count} != {openclip_count}")
    if dist.is_initialized():
        dist.destroy_process_group()


if __name__ == "__main__":
    main()

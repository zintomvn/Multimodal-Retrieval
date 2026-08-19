from __future__ import annotations

import argparse
import heapq
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import torch
from transformers import AutoModel, AutoProcessor

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parents[1]
PROCESSORS_ROOT = REPO_ROOT / "scripts" / "processors"
SCRIPT_ROOT = Path(__file__).resolve().parent

for path in (BACKEND_ROOT, PROCESSORS_ROOT, SCRIPT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.core.config import get_settings  # noqa: E402
from scripts.insert.import_gcs_npy_embeddings_to_milvus import (  # noqa: E402
    DEFAULT_BATCHES,
    DEFAULT_DATASET_ID,
    DEFAULT_EXTRACTOR_VERSION,
    DEFAULT_FRAME_PROFILE,
    DEFAULT_GCS_FEATURE_PREFIX,
    DEFAULT_MODEL_NAME,
    list_video_artifacts,
    read_csv_rows,
    read_npy,
    split_batches,
)
from src.artifact_io import gcs_client  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search GCS SigLIP2 .npy embeddings without Milvus.")
    parser.add_argument("--query", required=True)
    parser.add_argument("--batches", default=DEFAULT_BATCHES)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--target-video", default="")
    parser.add_argument("--dataset-id", default=DEFAULT_DATASET_ID)
    parser.add_argument("--frame-profile", default=DEFAULT_FRAME_PROFILE)
    parser.add_argument("--extractor-version", default=DEFAULT_EXTRACTOR_VERSION)
    parser.add_argument("--gcs-feature-prefix", default=DEFAULT_GCS_FEATURE_PREFIX)
    parser.add_argument("--gcs-bucket", default="")
    parser.add_argument("--gcs-credentials-file", default="")
    parser.add_argument("--gcs-timeout", type=float, default=60.0)
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    return parser.parse_args()


def text_embedding(query: str, model_name: str) -> np.ndarray:
    processor = AutoProcessor.from_pretrained(model_name, local_files_only=True)
    model = AutoModel.from_pretrained(model_name, local_files_only=True)
    model.eval()
    inputs = processor(text=[query], padding="max_length", return_tensors="pt")
    with torch.no_grad():
        output = model.get_text_features(**inputs)
    tensor = output if isinstance(output, torch.Tensor) else output.pooler_output
    vector = tensor.detach().cpu().numpy().astype("float32")[0]
    norm = float(np.linalg.norm(vector))
    if norm > 0:
        vector = vector / norm
    return vector


def push_hit(heap: list[tuple[float, dict[str, Any]]], hit: dict[str, Any], top_k: int) -> None:
    score = float(hit["score"])
    item = (score, hit)
    if len(heap) < top_k:
        heapq.heappush(heap, item)
        return
    if score > heap[0][0]:
        heapq.heapreplace(heap, item)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    settings = get_settings()
    bucket_name = args.gcs_bucket or settings.gcs_bucket
    credentials_file = args.gcs_credentials_file or settings.gcs_credentials_file
    if not bucket_name:
        raise RuntimeError("Set --gcs-bucket or GCS_BUCKET.")

    query_vector = text_embedding(args.query, args.model_name)
    storage_client = gcs_client(credentials_file)
    artifacts = []
    for batch_id in split_batches(args.batches):
        artifacts.extend(list_video_artifacts(storage_client, bucket_name, args, batch_id))

    top_heap: list[tuple[float, dict[str, Any]]] = []
    target_best: dict[str, Any] | None = None
    target_higher_scores = 0
    video_best: dict[str, dict[str, Any]] = {}
    total_vectors = 0

    for artifact in artifacts:
        rows = read_csv_rows(storage_client, bucket_name, artifact.map_key, args.gcs_timeout)
        vectors = read_npy(storage_client, bucket_name, artifact.embedding_key, args.gcs_timeout)
        if len(rows) != int(vectors.shape[0]):
            raise ValueError(
                f"Map/vector mismatch for {artifact.video_id}: rows={len(rows)} vectors={int(vectors.shape[0])}"
            )
        scores = vectors @ query_vector
        for index, score_raw in enumerate(scores):
            row = rows[index]
            score = float(score_raw)
            total_vectors += 1
            hit = {
                "score": score,
                "batch_id": artifact.batch_id,
                "video_id": artifact.video_id,
                "keyframe_id": str(row.get("keyframe_id") or ""),
                "keyframe_number": int(float(row.get("keyframe_number") or row.get("n") or index + 1)),
                "embedding_index_0": index,
                "image_rel_path": str(row.get("image_rel_path") or ""),
            }
            push_hit(top_heap, hit, max(1, args.top_k))
            current_video_best = video_best.get(artifact.video_id)
            if current_video_best is None or score > float(current_video_best["score"]):
                video_best[artifact.video_id] = hit
            if args.target_video and artifact.video_id == args.target_video:
                if target_best is None or score > float(target_best["score"]):
                    target_best = hit

    if args.target_video and target_best is not None:
        target_higher_scores = sum(
            1 for hit in video_best.values() if float(hit["score"]) > float(target_best["score"])
        )

    top_hits = [hit for _, hit in sorted(top_heap, key=lambda item: item[0], reverse=True)]
    top_videos = sorted(video_best.values(), key=lambda item: float(item["score"]), reverse=True)[: args.top_k]
    output = {
        "query": args.query,
        "model_name": args.model_name,
        "batches": split_batches(args.batches),
        "vectors_scanned": total_vectors,
        "videos_scanned": len(video_best),
        "top_hits": top_hits,
        "top_videos": top_videos,
    }
    if args.target_video:
        output["target_video"] = args.target_video
        output["target_video_best"] = target_best
        output["target_video_rank_among_videos"] = target_higher_scores + 1 if target_best else None
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

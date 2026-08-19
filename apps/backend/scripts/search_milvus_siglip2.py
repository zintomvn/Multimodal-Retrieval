from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import torch
from pymilvus import MilvusClient
from transformers import AutoModel, AutoProcessor

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  


DEFAULT_COLLECTION = "keyframe_embeddings_siglip2_base_patch16_256"
DEFAULT_MODEL_NAME = "google/siglip2-base-patch16-256"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search SigLIP2 vectors in Zilliz/Milvus.")
    parser.add_argument("--query", required=True)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--target-video", default="")
    return parser.parse_args()


def text_embedding(query: str, model_name: str) -> list[float]:
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
    return vector.astype(float).tolist()

# Convert a search hit to a dictionary
def hit_to_dict(hit: Any) -> dict[str, Any]:
    entity = hit.get("entity", {}) if hasattr(hit, "get") else {}
    item_id = hit.get("id") if hasattr(hit, "get") else None
    distance = hit.get("distance") if hasattr(hit, "get") else None
    return {
        "id": str(item_id),
        "score": float(distance),
        "keyframe_id": str(entity.get("keyframe_id") or item_id),
        "original_keyframe_id": entity.get("original_keyframe_id"),
        "canonical_keyframe_id": entity.get("canonical_keyframe_id"),
        "video_id": str(entity.get("video_id") or ""),
        "batch_id": str(entity.get("batch_id") or ""),
        "keyframe_number": entity.get("keyframe_number"),
        "frame_idx": entity.get("frame_idx"),
        "canonical_frame_idx": entity.get("canonical_frame_idx"),
        "embedding_index_0": entity.get("embedding_index_0"),
        "image_rel_path": entity.get("image_rel_path"),
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    settings = get_settings()
    uri = os.getenv("MILVUS_URI", settings.milvus_uri)
    token = os.getenv("MILVUS_TOKEN", settings.milvus_token)
    client = MilvusClient(uri=uri, token=token)
    query_vector = text_embedding(args.query, args.model_name)
    # Client
    raw = client.search(
        collection_name=args.collection,
        data=[query_vector],
        limit=max(1, args.top_k),
        output_fields=[
            "keyframe_id",
            "video_id",
            "batch_id",
            "keyframe_number",
            "frame_idx",
            "canonical_frame_idx",
            "embedding_index_0",
            "image_rel_path",
            "original_keyframe_id",
            "canonical_keyframe_id",
        ],
    )
    hits = [hit_to_dict(hit) for hit in raw[0] if raw]
    video_best: dict[str, dict[str, Any]] = {}
    for hit in hits:
        video_id = str(hit.get("video_id") or "")
        if not video_id:
            continue
        current = video_best.get(video_id)
        if current is None or float(hit["score"]) > float(current["score"]):
            video_best[video_id] = hit
    top_videos = sorted(video_best.values(), key=lambda item: float(item["score"]), reverse=True)
    output = {
        "collection": args.collection,
        "model_name": args.model_name,
        "top_k": args.top_k,
        "top_hits": hits,
        "top_videos_in_returned_hits": top_videos,
    }
    if args.target_video:
        target = next((item for item in top_videos if item["video_id"] == args.target_video), None)
        output["target_video"] = args.target_video
        output["target_video_best_in_returned_hits"] = target
        output["target_video_rank_in_returned_hits"] = (
            next((index + 1 for index, item in enumerate(top_videos) if item["video_id"] == args.target_video), None)
        )
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

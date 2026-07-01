from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import numpy as np
import yaml

from app.db.models import Frame, Video
from app.db.session import SessionLocal
from app.modules.ingest.pipeline import PipelineContext

logger = logging.getLogger(__name__)

_DEFAULT_DIM = 768


class EmbeddingStage:
    name = "embedding"

    def run(self, context: PipelineContext) -> PipelineContext:
        manifest_path = context.artifacts.get("manifest_path", "")
        if not manifest_path:
            raise ValueError("manifest_path missing from context.artifacts")

        with open(manifest_path) as f:
            manifest = yaml.safe_load(f)

        dataset_root = Path(context.dataset_root)
        all_frame_ids: list[str] = []
        all_vectors: list[list[float]] = []

        with SessionLocal() as db:
            for video_conf in manifest.get("videos", []):
                video_code = video_conf["video_code"]
                video = (
                    db.query(Video)
                    .filter(Video.dataset_id == context.dataset_id, Video.video_code == video_code)
                    .first()
                )
                if not video:
                    logger.warning("Video not in DB: %s — run dataset_scan first", video_code)
                    continue

                frames = (
                    db.query(Frame)
                    .filter(Frame.video_id == video.id)
                    .order_by(Frame.frame_idx)
                    .all()
                )
                if not frames:
                    continue

                features_file = video_conf.get("features_file", "")
                vectors = _load_or_embed(frames, features_file, dataset_root)
                all_frame_ids.extend(f.id for f in frames)
                all_vectors.extend(vectors)

        if not all_frame_ids:
            logger.warning("embedding: no frames found")
            context.stats["embeddings_count"] = 0
            return context

        # Save to temp .npz so milvus_index stage can consume it
        tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
        tmp.close()
        np.savez(
            tmp.name,
            frame_ids=np.array(all_frame_ids),
            vectors=np.array(all_vectors, dtype=np.float32),
        )

        context.artifacts["embeddings_file"] = tmp.name
        context.stats["embeddings_count"] = len(all_frame_ids)
        dim = len(all_vectors[0]) if all_vectors else _DEFAULT_DIM
        logger.info("embedding: %d vectors, dim=%d", len(all_frame_ids), dim)
        return context


def _load_or_embed(frames: list[Frame], features_file: str, dataset_root: Path) -> list[list[float]]:
    if features_file:
        feat_path = dataset_root / features_file
        if feat_path.exists():
            return _load_npy(feat_path, len(frames))
        logger.warning("features_file not found: %s — falling back to CLIP", feat_path)
    return _clip_embed(frames)


def _load_npy(path: Path, n_frames: int) -> list[list[float]]:
    """Load pre-computed .npy feature array of shape (N, dim)."""
    arr = np.load(str(path))
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    dim = arr.shape[-1]
    vectors: list[list[float]] = arr[:n_frames].tolist()
    # Pad with zeros if the .npy has fewer rows than frames
    while len(vectors) < n_frames:
        vectors.append([0.0] * dim)
    logger.info("Loaded %d vectors from %s (dim=%d)", len(vectors), path.name, dim)
    return vectors


def _clip_embed(frames: list[Frame]) -> list[list[float]]:
    try:
        return _run_clip(frames)
    except ImportError:
        logger.warning("transformers/PIL not installed — using zero vectors (install with: pip install transformers pillow torch)")
        return [[0.0] * _DEFAULT_DIM for _ in frames]


def _run_clip(frames: list[Frame]) -> list[list[float]]:
    import torch
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model_name = "openai/clip-vit-base-patch16"
    logger.info("Loading CLIP model %s on %s …", model_name, device)
    processor = CLIPProcessor.from_pretrained(model_name)
    model = CLIPModel.from_pretrained(model_name).to(device)
    model.eval()

    vectors: list[list[float]] = []
    batch_size = 16

    for i in range(0, len(frames), batch_size):
        batch = frames[i : i + batch_size]
        images: list[Image.Image] = []
        for frame in batch:
            try:
                images.append(Image.open(frame.image_uri).convert("RGB"))
            except Exception:
                images.append(Image.new("RGB", (224, 224), color=0))

        inputs = processor(images=images, return_tensors="pt", padding=True).to(device)
        with torch.no_grad():
            feats = model.get_image_features(**inputs)
            feats = feats / feats.norm(dim=-1, keepdim=True)
        vectors.extend(feats.cpu().float().tolist())
        logger.info("CLIP: %d/%d frames embedded", min(i + batch_size, len(frames)), len(frames))

    return vectors

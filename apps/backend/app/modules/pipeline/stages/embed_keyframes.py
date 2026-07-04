"""OpenCLIP image embedding stage.

Loads ViT-B-32 with laion2b_s34b_b79k weights and encodes decoded JPG tensors.
Must stay in the same vector space as query-time embedding in
scripts/serve_openclip_embeddings.py.

Config from configs/model_registry.yaml's embedders.openai_embedding entry:
  openclip_model: ViT-B-32
  openclip_pretrained: laion2b_s34b_b79k
  openclip_device: auto
  openclip_max_batch: 32
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import torch
from PIL import Image

logger = logging.getLogger(__name__)


def _load_openclip_model(
    model_name: str = "ViT-B-32",
    pretrained: str = "laion2b_s34b_b79k",
    device: str = "auto",
):
    import open_clip

    device = _resolve_device(device)
    model, _, preprocess = open_clip.create_model_and_transforms(
        model_name, pretrained=pretrained, device=device
    )
    tokenizer = open_clip.get_tokenizer(model_name)
    model.eval()
    return model, preprocess, tokenizer


def embed_keyframe_images(
    image_paths: list[Path],
    batch_size: int = 32,
    model_name: str = "ViT-B-32",
    pretrained: str = "laion2b_s34b_b79k",
    device: str = "auto",
) -> np.ndarray:
    """Embed a list of keyframe image paths using OpenCLIP.

    Returns numpy array of shape (N, embedding_dim) with L2-normalized vectors.
    """
    if not image_paths:
        return np.array([], dtype=np.float32)

    device = _resolve_device(device)
    model, preprocess, _ = _load_openclip_model(model_name, pretrained, device)
    all_embeddings: list[np.ndarray] = []

    with torch.no_grad():
        for i in range(0, len(image_paths), batch_size):
            batch_paths = image_paths[i : i + batch_size]
            images = []
            for p in batch_paths:
                try:
                    img = Image.open(p).convert("RGB")
                    images.append(preprocess(img))
                except Exception as exc:
                    logger.warning("Failed to load image %s: %s", p, exc)
                    # Use zero vector as placeholder
                    images.append(torch.zeros(3, 224, 224))

            batch_tensor = torch.stack(images).to(device)
            features = model.encode_image(batch_tensor)
            # L2 normalize
            features = features / features.norm(dim=-1, keepdim=True)
            all_embeddings.append(features.cpu().numpy())

    result = np.concatenate(all_embeddings, axis=0)
    logger.info("Embedded %d images -> shape %s", len(image_paths), result.shape)
    return result


def load_model_config(model_registry_path: Path | None = None) -> dict:
    """Load embedding config from model_registry.yaml."""
    import yaml

    if model_registry_path is None:
        model_registry_path = Path(__file__).resolve().parents[6] / "configs" / "model_registry.yaml"

    with open(model_registry_path, "r") as f:
        registry = yaml.safe_load(f)

    embedders = registry.get("embedders", {})
    for name, config in embedders.items():
        if isinstance(config, dict) and config.get("enabled") and config.get("task") == "multimodal_embedding":
            return {
                "model_name": config.get("openclip_model", "ViT-B-32"),
                "pretrained": config.get("openclip_pretrained", "laion2b_s34b_b79k"),
                "device": config.get("openclip_device", "auto"),
                "batch_size": int(config.get("openclip_max_batch", 32)),
                "dim": int(config.get("dim", 512)),
            }

    # Defaults
    return {
        "model_name": "ViT-B-32",
        "pretrained": "laion2b_s34b_b79k",
        "device": "auto",
        "batch_size": 32,
        "dim": 512,
    }


def _resolve_device(device: str) -> str:
    if device == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return device

from __future__ import annotations

import logging

import numpy as np

from app.db.models import Frame, Video
from app.db.session import SessionLocal
from app.modules.ingest.pipeline import PipelineContext

logger = logging.getLogger(__name__)

_COLLECTION = "frames"
_MODEL_VERSION = "clip-vit-base-patch16-v1"
_BATCH_SIZE = 500


class MilvusIndexStage:
    name = "milvus_index"

    def run(self, context: PipelineContext) -> PipelineContext:
        from app.core.config import get_settings
        from app.adapters.vector_db.milvus import MilvusVectorSearchClient

        embeddings_file = context.artifacts.get("embeddings_file", "")
        if not embeddings_file:
            raise ValueError("embeddings_file missing — ensure embedding stage ran first")

        data = np.load(embeddings_file, allow_pickle=True)
        frame_ids: list[str] = data["frame_ids"].tolist()
        vectors: list[list[float]] = data["vectors"].tolist()

        if not frame_ids:
            logger.warning("milvus_index: no embeddings to index")
            context.stats["milvus_upserted"] = 0
            return context

        dim = len(vectors[0])
        settings = get_settings()
        client = MilvusVectorSearchClient(uri=settings.milvus_uri, token=settings.milvus_token)
        client.ensure_collection(_COLLECTION, dim)

        # Build frame metadata from DB
        frame_meta = _load_frame_meta(context.dataset_id)

        upserted = 0
        for i in range(0, len(frame_ids), _BATCH_SIZE):
            batch_ids = frame_ids[i : i + _BATCH_SIZE]
            batch_vecs = vectors[i : i + _BATCH_SIZE]
            records = [
                (
                    fid,
                    vec,
                    frame_meta.get(
                        fid,
                        {"frame_id": fid, "video_id": "", "frame_idx": 0, "event_id": "", "model_version": _MODEL_VERSION},
                    ),
                )
                for fid, vec in zip(batch_ids, batch_vecs)
            ]
            upserted += client.upsert(_COLLECTION, records)
            logger.info("milvus_index: %d / %d vectors upserted", min(i + _BATCH_SIZE, len(frame_ids)), len(frame_ids))

        context.stats["milvus_upserted"] = upserted
        context.artifacts["milvus_collection"] = _COLLECTION
        logger.info("milvus_index: total %d vectors → collection '%s'", upserted, _COLLECTION)
        return context


def _load_frame_meta(dataset_id: str) -> dict[str, dict]:
    meta: dict[str, dict] = {}
    with SessionLocal() as db:
        videos = db.query(Video).filter(Video.dataset_id == dataset_id).all()
        for video in videos:
            frames = db.query(Frame).filter(Frame.video_id == video.id).all()
            for frame in frames:
                meta[frame.id] = {
                    "frame_id": frame.id,
                    "video_id": video.id,
                    "frame_idx": frame.frame_idx,
                    "event_id": "",
                    "model_version": _MODEL_VERSION,
                }
    return meta

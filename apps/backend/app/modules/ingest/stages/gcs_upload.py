from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from app.db.models import Frame, Video
from app.db.session import SessionLocal
from app.modules.ingest.pipeline import PipelineContext

logger = logging.getLogger(__name__)


class GCSUploadStage:
    name = "gcs_upload"

    def run(self, context: PipelineContext) -> PipelineContext:
        from app.core.config import get_settings
        from app.adapters.object_storage.gcs import GCSObjectStorageClient

        settings = get_settings()
        if not settings.gcs_bucket:
            logger.warning("GCS_BUCKET not configured — skipping gcs_upload")
            context.stats["gcs_uploaded"] = 0
            return context

        client = GCSObjectStorageClient(
            bucket=settings.gcs_bucket,
            credentials_file=settings.gcs_credentials_file,
            public_base_url=settings.gcs_public_url,
        )

        uploaded = 0
        skipped = 0
        errors = 0

        with SessionLocal() as db:
            videos = db.query(Video).filter(Video.dataset_id == context.dataset_id).all()
            for video in videos:
                frames = (
                    db.query(Frame)
                    .filter(Frame.video_id == video.id)
                    .order_by(Frame.frame_idx)
                    .all()
                )
                for frame in frames:
                    if not frame.image_uri:
                        skipped += 1
                        continue
                    local_path = Path(frame.image_uri)
                    if not local_path.exists():
                        logger.debug("Local file missing: %s", local_path)
                        skipped += 1
                        continue

                    gcs_key = f"{context.dataset_id}/{video.video_code}/{local_path.name}"
                    content_type = mimetypes.guess_type(local_path.name)[0] or "image/jpeg"

                    try:
                        with open(local_path, "rb") as f:
                            client.put_object(key=gcs_key, data=f.read(), content_type=content_type)
                        frame.image_uri = gcs_key
                        frame.thumbnail_uri = gcs_key
                        uploaded += 1
                    except Exception as exc:
                        logger.error("GCS upload failed %s: %s", local_path.name, exc)
                        errors += 1

            db.commit()

        context.stats["gcs_uploaded"] = uploaded
        context.stats["gcs_skipped"] = skipped
        context.stats["gcs_errors"] = errors
        logger.info("gcs_upload: %d uploaded, %d skipped, %d errors", uploaded, skipped, errors)
        return context

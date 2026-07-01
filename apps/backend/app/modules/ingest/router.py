from __future__ import annotations

from fastapi import APIRouter, Depends
import logging
import mimetypes
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import get_object_storage, get_text_client, get_vector_client
from app.db.models import Job
from app.db.session import get_db
from app.modules.ingest.schemas import IngestJobRequest, IngestJobResponse
from app.modules.ingest.service import DemoIngestService
from app.db.session import SessionLocal, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.post("/jobs")
def create_ingest_job(
    request: IngestJobRequest,
    db: Session = Depends(get_db),
) -> IngestJobResponse:
    settings = get_settings()
    job = Job(
        kind="INGEST",
        status="RUNNING",
        progress=0.0,
        message="Ingestion started.",
        payload=request.model_dump(mode="json"),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    if request.mode == "mock":
        job.status = "COMPLETED"
        job.progress = 1.0
        job.message = "Mock ingest completed."
        job.payload = {
            **request.model_dump(mode="json"),
            "report": {
                "mode": "mock",
                "dataset_root": str(settings.data_root),
                "targets": request.targets,
                "dry_run": request.dry_run,
            },
        }
        db.commit()
        db.refresh(job)
        return IngestJobResponse(job_id=job.id, status=job.status, message=job.message or "", report=job.payload.get("report", {}))

    selected_targets = set(request.targets)
    object_storage = get_object_storage() if {"pg", "media"} & selected_targets else None
    vector_client = get_vector_client() if "milvus" in selected_targets else None
    text_client = get_text_client() if "es" in selected_targets else None

    service = DemoIngestService(
        db=db,
        vector_client=vector_client,
        text_client=text_client,
        object_storage=object_storage,
    )
    try:
        report = service.run(request)
        job.status = "COMPLETED"
        job.progress = 1.0
        job.message = "Demo ingestion completed."
        job.payload = {
            **request.model_dump(mode="json"),
            "report": report,
        }
    except Exception as exc:  # noqa: BLE001 - propagate error through job payload first.
        db.rollback()
        job = db.query(Job).filter(Job.id == job.id).one()
        job.status = "FAILED"
        job.progress = 0.0
        job.message = f"Ingestion failed: {exc}"
        job.payload = {
            **request.model_dump(mode="json"),
            "error": str(exc),
        }
        db.commit()
        db.refresh(job)
        return IngestJobResponse(job_id=job.id, status=job.status, message=job.message or "", report={})

    db.commit()
    db.refresh(job)
    return IngestJobResponse(
        job_id=job.id,
        status=job.status,
        message=job.message or "",
        report=job.payload.get("report", {}),
    )
    from app.modules.ingest.runner import run_ingest_job

    background_tasks.add_task(
        run_ingest_job,
        job_id=job.id,
        dataset_id=request.dataset_id or "",
        manifest_path=manifest_path,
    )

    return {"job_id": job.id, "status": "PENDING", "message": "Pipeline started in background."}


# --- Direct cloud upload endpoints ---


class GCSUploadRequest(BaseModel):
    source_path: str
    source_type: Literal["folder", "zip"] = "folder"
    dataset_id: str | None = None


class MilvusIndexRequest(BaseModel):
    features_file: str
    collection: str = "frames"
    dataset_id: str | None = None


@router.post("/upload/gcs")
def upload_to_gcs(
    request: GCSUploadRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    job = Job(
        kind="GCS_UPLOAD",
        status="PENDING",
        progress=0.0,
        message="GCS upload queued.",
        payload=request.model_dump(mode="json"),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    background_tasks.add_task(_run_gcs_upload, job.id, request.source_path, request.source_type, request.dataset_id)
    return {"job_id": job.id, "status": job.status, "message": job.message}


@router.post("/upload/milvus")
def upload_to_milvus(
    request: MilvusIndexRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    job = Job(
        kind="MILVUS_UPLOAD",
        status="PENDING",
        progress=0.0,
        message="Milvus index queued.",
        payload=request.model_dump(mode="json"),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    background_tasks.add_task(_run_milvus_upload, job.id, request.features_file, request.collection, request.dataset_id)
    return {"job_id": job.id, "status": job.status, "message": job.message}


# --- Background runners ---


def _update_job(job_id: str, status: str, progress: float, message: str) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job:
            job.status = status
            job.progress = progress
            job.message = message
            db.commit()


def _run_gcs_upload(job_id: str, source_path: str, source_type: str, dataset_id: str | None = None) -> None:
    from app.adapters.object_storage.gcs import GCSObjectStorageClient
    from app.core.config import get_settings

    _update_job(job_id, "RUNNING", 0.0, "Preparing source...")
    tmp_dir: str | None = None
    try:
        settings = get_settings()
        if not settings.gcs_bucket:
            raise ValueError("GCS_BUCKET not configured")

        client = GCSObjectStorageClient(
            bucket=settings.gcs_bucket,
            credentials_file=settings.gcs_credentials_file,
            public_base_url=settings.gcs_public_url,
        )

        if source_type == "zip":
            tmp_dir = tempfile.mkdtemp()
            with zipfile.ZipFile(source_path, "r") as zf:
                for member in zf.namelist():
                    member_path = Path(tmp_dir) / member
                    if not member_path.resolve().is_relative_to(Path(tmp_dir).resolve()):
                        raise ValueError(f"Unsafe zip member: {member}")
                zf.extractall(tmp_dir)
            folder_path = Path(tmp_dir)
        else:
            folder_path = Path(source_path)

        _update_job(job_id, "RUNNING", 0.05, "Scanning files...")
        _GCS_EXTS = {".jpg", ".jpeg", ".png", ".mp4", ".avi", ".mov", ".mkv"}
        media_files = [
            p for p in folder_path.rglob("*")
            if p.suffix.lower() in _GCS_EXTS
        ]
        n = len(media_files)
        if n == 0:
            raise ValueError("No image or video files found in the specified path")

        for i, media_path in enumerate(media_files):
            rel = media_path.relative_to(folder_path).as_posix()
            key = f"{dataset_id}/{rel}" if dataset_id else rel
            content_type = mimetypes.guess_type(media_path.name)[0] or "application/octet-stream"
            with open(media_path, "rb") as f:
                client.put_object(key=key, data=f.read(), content_type=content_type)
            _update_job(job_id, "RUNNING", 0.1 + 0.9 * (i + 1) / n, f"Uploaded {i + 1}/{n}...")

        _update_job(job_id, "COMPLETED", 1.0, f"Uploaded {n} files to GCS.")
        logger.info("GCS upload complete: %d images from %s", n, source_path)
    except Exception as exc:
        logger.error("GCS upload failed: %s", exc)
        _update_job(job_id, "FAILED", 0.0, str(exc))
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


_MILVUS_BATCH = 500


def _load_features(features_file: str) -> tuple[list[str], list[list[float]]]:
    """Load frame_ids and vectors from .npz (keyed) or .npy (raw 2-D array)."""
    raw = np.load(features_file, allow_pickle=True)
    if hasattr(raw, "files"):
        # .npz with explicit keys
        if "frame_ids" not in raw.files or "vectors" not in raw.files:
            raise ValueError(
                "Expected .npz with keys 'frame_ids' and 'vectors'. "
                f"Found keys: {raw.files}"
            )
        frame_ids: list[str] = raw["frame_ids"].tolist()
        vectors: list[list[float]] = raw["vectors"].tolist()
    else:
        # .npy plain 2-D array shape (N, D) — generate sequential IDs
        arr = np.asarray(raw)
        if arr.ndim != 2:
            raise ValueError(
                f"Expected 2-D array (N, dim) in .npy file, got shape {arr.shape}"
            )
        frame_ids = [f"vec_{i}" for i in range(len(arr))]
        vectors = arr.tolist()
    return frame_ids, vectors


def _run_milvus_upload(job_id: str, features_file: str, collection: str, dataset_id: str | None = None) -> None:
    from app.adapters.vector_db.milvus import MilvusVectorSearchClient
    from app.core.config import get_settings

    _update_job(job_id, "RUNNING", 0.0, "Loading vectors...")
    try:
        frame_ids, vectors = _load_features(features_file)
        n = len(frame_ids)
        if n == 0:
            raise ValueError("No vectors found in the features file")

        dim = len(vectors[0])
        _update_job(job_id, "RUNNING", 0.2, "Ensuring collection...")
        settings = get_settings()
        client = MilvusVectorSearchClient(uri=settings.milvus_uri, token=settings.milvus_token)
        client.ensure_collection(collection, dim)

        _update_job(job_id, "RUNNING", 0.3, f"Upserting {n} vectors in batches...")
        upserted = 0
        for i in range(0, n, _MILVUS_BATCH):
            batch_ids = frame_ids[i : i + _MILVUS_BATCH]
            batch_vecs = vectors[i : i + _MILVUS_BATCH]
            meta: dict = {}
            if dataset_id:
                meta["dataset_id"] = dataset_id
            records = [(fid, vec, {**meta, "frame_id": fid}) for fid, vec in zip(batch_ids, batch_vecs)]
            upserted += client.upsert(collection, records)
            progress = 0.3 + 0.7 * min(i + _MILVUS_BATCH, n) / n
            _update_job(job_id, "RUNNING", progress, f"Upserted {upserted}/{n}...")

        _update_job(job_id, "COMPLETED", 1.0, f"Indexed {n} vectors into '{collection}'.")
        logger.info("Milvus upload complete: %d vectors -> %s", n, collection)
    except Exception as exc:
        logger.error("Milvus upload failed: %s", exc)
        _update_job(job_id, "FAILED", 0.0, str(exc))


# --- Browser file upload endpoints ---


_MAX_UPLOAD_BYTES = 2 * 1024 ** 3  # 2 GB


async def _read_upload(file: UploadFile) -> bytes:
    from fastapi import HTTPException
    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 2 GB)")
    return content


@router.post("/upload/file/gcs")
async def upload_file_to_gcs(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    dataset_id: str | None = Form(None),
) -> dict:
    content = await _read_upload(file)
    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix == ".zip":
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            f.write(content)
            tmp_path = f.name
        source_type = "zip"
        cleanup_path = tmp_path
    else:
        tmp_dir = tempfile.mkdtemp()
        dest = Path(tmp_dir) / (file.filename or f"upload{suffix}")
        dest.write_bytes(content)
        tmp_path = tmp_dir
        source_type = "folder"
        cleanup_path = tmp_dir

    job = Job(
        kind="GCS_UPLOAD",
        status="PENDING",
        progress=0.0,
        message="GCS upload queued.",
        payload={"source_path": tmp_path, "source_type": source_type, "dataset_id": dataset_id},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    background_tasks.add_task(_run_gcs_file_upload, job.id, tmp_path, source_type, dataset_id, cleanup_path)
    return {"job_id": job.id, "status": job.status, "message": job.message}


@router.post("/upload/file/milvus")
async def upload_file_to_milvus(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    collection: str = Form("frames"),
    dataset_id: str | None = Form(None),
) -> dict:
    content = await _read_upload(file)
    suffix = Path(file.filename or "upload").suffix.lower() or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(content)
        tmp_path = f.name

    job = Job(
        kind="MILVUS_UPLOAD",
        status="PENDING",
        progress=0.0,
        message="Milvus index queued.",
        payload={"features_file": tmp_path, "collection": collection, "dataset_id": dataset_id},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    background_tasks.add_task(_run_milvus_file_upload, job.id, tmp_path, collection, dataset_id)
    return {"job_id": job.id, "status": job.status, "message": job.message}


def _run_gcs_file_upload(
    job_id: str, source_path: str, source_type: str, dataset_id: str | None, cleanup_path: str
) -> None:
    try:
        _run_gcs_upload(job_id, source_path, source_type, dataset_id)
    finally:
        shutil.rmtree(cleanup_path, ignore_errors=True)


def _run_milvus_file_upload(
    job_id: str, temp_path: str, collection: str, dataset_id: str | None
) -> None:
    features_path = temp_path
    extract_dir: str | None = None
    try:
        if temp_path.endswith(".zip"):
            extract_dir = tempfile.mkdtemp()
            with zipfile.ZipFile(temp_path, "r") as zf:
                for member in zf.namelist():
                    member_path = (Path(extract_dir) / member).resolve()
                    if not member_path.is_relative_to(Path(extract_dir).resolve()):
                        raise ValueError(f"Unsafe zip member: {member}")
                zf.extractall(extract_dir)
            npy_files = list(Path(extract_dir).rglob("*.npz")) + list(Path(extract_dir).rglob("*.npy"))
            if not npy_files:
                raise ValueError("No .npy or .npz file found inside zip")
            features_path = str(npy_files[0])
        _run_milvus_upload(job_id, features_path, collection, dataset_id)
    finally:
        shutil.rmtree(temp_path, ignore_errors=True)
        if extract_dir:
            shutil.rmtree(extract_dir, ignore_errors=True)

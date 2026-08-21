from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.db.bootstrap import init_db
from app.modules.datasets.router import router as datasets_router
from app.modules.ingest.router import router as ingest_router
from app.modules.jobs.router import router as jobs_router
from app.modules.media.router import router as media_router
from app.modules.models.router import router as models_router
from app.modules.pipeline.router import router as pipeline_router
from app.modules.retrieval.router import router as retrieval_router
from app.modules.submissions.router import router as submissions_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not settings.skip_db_init:
        init_db()
    yield


settings = get_settings()
app = FastAPI(title="Multimodal Retrieval Assistant API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(datasets_router)
app.include_router(models_router)
app.include_router(ingest_router)
app.include_router(jobs_router)
app.include_router(retrieval_router)
app.include_router(media_router)
app.include_router(pipeline_router)
app.include_router(submissions_router)

if settings.storage_provider == "local":
    from fastapi.staticfiles import StaticFiles

    app.mount("/api/media/static", StaticFiles(directory=settings.data_root), name="static")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "env": settings.app_env, "storage_provider": settings.storage_provider}


@app.get("/readyz")
def readyz() -> dict:
    return {"status": "ready"}

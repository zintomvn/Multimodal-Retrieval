from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.db.bootstrap import init_db, seed_mock_data
from app.db.session import SessionLocal
from app.modules.datasets.router import router as datasets_router
from app.modules.ingest.router import router as ingest_router
from app.modules.jobs.router import router as jobs_router
from app.modules.media.router import router as media_router
from app.modules.models.router import router as models_router
from app.modules.retrieval.router import router as retrieval_router
from app.modules.submissions.router import router as submissions_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        seed_mock_data(db)
    finally:
        db.close()
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
app.include_router(submissions_router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "env": settings.app_env, "mock_mode": settings.mock_mode}


@app.get("/readyz")
def readyz() -> dict:
    return {"status": "ready"}

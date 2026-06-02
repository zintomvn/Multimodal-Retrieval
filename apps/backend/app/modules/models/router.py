from __future__ import annotations

from fastapi import APIRouter

from app.modules.models.service import model_registry_service

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
def list_models() -> dict:
    return {"models": model_registry_service.list_models(), "enabled": model_registry_service.enabled_models()}

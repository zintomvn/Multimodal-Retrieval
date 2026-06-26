from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import get_model_registry_service
from app.modules.models.service import ModelRegistryService

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
def list_models(svc: ModelRegistryService = Depends(get_model_registry_service)) -> dict:
    return {"models": svc.list_models(), "enabled": svc.enabled_models()}

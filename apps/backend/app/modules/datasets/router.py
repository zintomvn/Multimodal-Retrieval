from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.models import Dataset
from app.db.session import get_db

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


class DatasetCreate(BaseModel):
    name: str
    version: str
    root_uri: str


@router.get("")
def list_datasets(db: Session = Depends(get_db)) -> dict:
    datasets = db.query(Dataset).order_by(Dataset.created_at.desc()).all()
    return {
        "datasets": [
            {
                "id": item.id,
                "name": item.name,
                "version": item.version,
                "root_uri": item.root_uri,
                "status": item.status,
                "video_count": len(item.videos),
            }
            for item in datasets
        ]
    }


@router.post("")
def create_dataset(request: DatasetCreate, db: Session = Depends(get_db)) -> dict:
    dataset = Dataset(name=request.name, version=request.version, root_uri=request.root_uri, status="DRAFT")
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return {"id": dataset.id, "status": dataset.status}

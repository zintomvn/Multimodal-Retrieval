from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.adapters.object_storage.local import LocalObjectStorageClient
from app.adapters.text_search.mock import InMemoryTextSearchClient
from app.adapters.vector_db.mock import InMemoryVectorSearchClient
from app.db.models import Base, Dataset, Event, EventKeyframe, Frame, FrameAnnotation, Shot, Video
from app.modules.ingest.schemas import IngestJobRequest
from app.modules.ingest.service import DemoIngestService


def _write_demo_fixture(root: Path) -> Path:
    demo = root / "demo"
    (demo / "annotations" / "L30_V001").mkdir(parents=True, exist_ok=True)
    (demo / "features" / "vit-ViT-B-32-laion2b_s34b_b79k").mkdir(parents=True, exist_ok=True)
    (demo / "features" / "events").mkdir(parents=True, exist_ok=True)
    (demo / "features" / "map-keyframes").mkdir(parents=True, exist_ok=True)
    (demo / "features" / "map-event").mkdir(parents=True, exist_ok=True)
    (demo / "frames" / "L30_V001").mkdir(parents=True, exist_ok=True)

    (demo / "per_video_summary.csv").write_text(
        "video_id,num_keyframes,embedding_shape,feature_path,map_path,seconds\n"
        "L30_V001,2,\"[2, 4]\",/tmp/L30_V001.npy,/tmp/L30_V001.csv,1.0\n",
        encoding="utf-8",
    )

    (demo / "shot_segments.csv").write_text(
        "video_name,video_path,shot_id,shot_start_frame,shot_end_frame,shot_start_sec,shot_end_sec,"
        "frame_type,frame_idx,frame_sec,image_path,boundary_threshold,saved,fps,total_frames_opencv\n"
        "L30_V001.mp4,/tmp/L30_V001.mp4,0,0,25,0.0,1.0,first,0,0.0,/tmp/frames/L30_V001/shot_0000_first_f000000.jpg,0.296,True,25.0,25\n"
        "L30_V001.mp4,/tmp/L30_V001.mp4,0,0,25,0.0,1.0,last,25,1.0,/tmp/frames/L30_V001/shot_0000_last_f000025.jpg,0.296,True,25.0,25\n",
        encoding="utf-8",
    )

    (demo / "features" / "map-keyframes" / "L30_V001.csv").write_text(
        "n,pts_time,fps,frame_idx\n"
        "1,0.0,25.0,0\n"
        "2,1.0,25.0,25\n",
        encoding="utf-8",
    )

    np.save(
        demo / "features" / "vit-ViT-B-32-laion2b_s34b_b79k" / "L30_V001.npy",
        np.array([[0.1, 0.2, 0.3, 0.4], [0.5, 0.2, 0.1, 0.2]], dtype=np.float32),
    )

    (demo / "features" / "map-event" / "L30_V001.csv").write_text(
        "event_id,event_embedding_index,video_id,start_n,end_n,start_sec,end_sec,start_frame,end_frame,keyframe_ns,n_keyframes\n"
        "L30_V001_E0000,0,L30_V001,1,2,0.0,1.0,0,25,1 2,2\n",
        encoding="utf-8",
    )
    np.save(demo / "features" / "events" / "L30_V001.npy", np.array([[0.7, 0.2, 0.1, 0.5]], dtype=np.float32))

    annotations = [
        {
            "image_path": "/tmp/frames/L30_V001/shot_0000_first_f000000.jpg",
            "image_name": "shot_0000_first_f000000.jpg",
            "caption": "co mot nguoi dang di bo",
            "texts": ["duong pho"],
            "objects": ["person"],
            "object_counts": {"person": 1},
            "detections": [{"label": "person", "confidence": 0.9}],
            "video_id": "L30_V001",
        },
        {
            "image_path": "/tmp/frames/L30_V001/shot_0000_last_f000025.jpg",
            "image_name": "shot_0000_last_f000025.jpg",
            "caption": "xe may tren duong",
            "texts": ["ban tin"],
            "objects": ["motorbike"],
            "object_counts": {"motorbike": 1},
            "detections": [{"label": "motorbike", "confidence": 0.88}],
            "video_id": "L30_V001",
        },
    ]
    with (demo / "annotations" / "L30_V001" / "annotations.jsonl").open("w", encoding="utf-8") as handle:
        for row in annotations:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    (demo / "frames" / "L30_V001" / "shot_0000_first_f000000.jpg").write_bytes(b"frame-0")
    (demo / "frames" / "L30_V001" / "shot_0000_last_f000025.jpg").write_bytes(b"frame-25")

    return demo


def _build_service(tmp_path: Path) -> tuple[DemoIngestService, Session, InMemoryVectorSearchClient, InMemoryTextSearchClient]:
    db_path = tmp_path / "ingest.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    vector = InMemoryVectorSearchClient()
    text = InMemoryTextSearchClient()
    storage = LocalObjectStorageClient(data_root=tmp_path / "storage", public_base_url="/api/media/static")
    service = DemoIngestService(db=session, vector_client=vector, text_client=text, object_storage=storage)
    return service, session, vector, text


def test_m2_ingestion_pipeline_imports_pg_media_milvus_and_es(tmp_path: Path) -> None:
    demo_root = _write_demo_fixture(tmp_path)
    service, db, vector, text = _build_service(tmp_path)
    request = IngestJobRequest(
        mode="demo",
        dataset_root=str(demo_root),
        targets=["pg", "media", "milvus", "es"],
        dataset_code="l30-demo",
        dataset_name="l30-demo",
        dataset_version="v1",
    )

    report = service.run(request)

    assert report["reconcile"]["unique_keyframes"] == 2
    assert report["reconcile"]["annotations_rows"] == 2
    assert report["reconcile"]["event_mapping_rows"] == 1
    assert report["reconcile"]["event_embeddings_rows"] == 1
    assert report["pg"]["videos_inserted"] == 1
    assert report["pg"]["keyframes_inserted"] == 2
    assert report["pg"]["event_keyframes_upserted"] == 2
    assert report["media"]["uploaded"] == 2
    assert report["milvus"]["keyframe_vectors_upserted"] == 2
    assert report["milvus"]["event_vectors_upserted"] == 1
    assert report["es"]["docs_indexed"] == 2

    assert db.query(Dataset).count() == 1
    assert db.query(Video).count() == 1
    assert db.query(Shot).count() == 1
    assert db.query(Frame).count() == 2
    assert db.query(FrameAnnotation).count() == 2
    assert db.query(Event).count() == 1
    assert db.query(EventKeyframe).count() == 2
    assert len(vector._collections["keyframe_embeddings"]) == 2
    assert len(vector._collections["event_embeddings"]) == 1
    assert len(text._indices["keyframe_annotations"]) == 2

    db.close()


def test_m2_reconcile_uses_per_video_annotations_instead_of_root_jsonl(tmp_path: Path) -> None:
    demo_root = _write_demo_fixture(tmp_path)
    (demo_root / "annotations.jsonl").write_text(
        json.dumps(
            {
                "video_id": "L30_V001",
                "image_name": "shot_0000_first_f000000.jpg",
                "image_path": "/tmp/frames/L30_V001/shot_0000_first_f000000.jpg",
                "caption": "legacy-row-should-be-ignored",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    service, db, _vector, _text = _build_service(tmp_path)
    request = IngestJobRequest(
        mode="demo",
        dataset_root=str(demo_root),
        targets=["pg", "media", "milvus", "es"],
        dataset_code="l30-demo",
        dataset_name="l30-demo",
        dataset_version="v1",
    )

    report = service.run(request)

    assert report["reconcile"]["annotations_rows"] == 2
    assert report["es"]["annotations_rows"] == 2
    assert db.query(FrameAnnotation).count() == 2
    db.close()


def test_m2_ingestion_pipeline_is_idempotent(tmp_path: Path) -> None:
    demo_root = _write_demo_fixture(tmp_path)
    service, db, vector, text = _build_service(tmp_path)
    request = IngestJobRequest(
        mode="demo",
        dataset_root=str(demo_root),
        targets=["pg", "media", "milvus", "es"],
        dataset_code="l30-demo",
        dataset_name="l30-demo",
        dataset_version="v1",
    )

    first = service.run(request)
    second = service.run(request)

    assert first["pg"]["keyframes_inserted"] == 2
    assert second["pg"]["keyframes_inserted"] == 0
    assert second["pg"]["keyframes_updated"] >= 2
    assert db.query(Dataset).count() == 1
    assert db.query(Video).count() == 1
    assert db.query(Shot).count() == 1
    assert db.query(Frame).count() == 2
    assert db.query(FrameAnnotation).count() == 2
    assert db.query(Event).count() == 1
    assert db.query(EventKeyframe).count() == 2
    assert len(vector._collections["keyframe_embeddings"]) == 2
    assert len(vector._collections["event_embeddings"]) == 1
    assert len(text._indices["keyframe_annotations"]) == 2

    db.close()

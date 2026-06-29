from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.deps import get_object_storage, get_text_client, get_vector_client
from app.db.session import SessionLocal
from app.modules.ingest.schemas import IngestJobRequest
from app.modules.ingest.service import DemoIngestService


def main() -> None:
    parser = argparse.ArgumentParser(description="Run full demo ingestion (PG + media + Milvus + ES).")
    parser.add_argument("--dataset-root", default="demo", help="Path to demo/ directory.")
    parser.add_argument("--dataset-code", default="l30-demo")
    parser.add_argument("--dataset-name", default="aic-2026-l30-demo")
    parser.add_argument("--dataset-version", default="v1")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    request = IngestJobRequest(
        mode="demo",
        dataset_code=args.dataset_code,
        dataset_name=args.dataset_name,
        dataset_version=args.dataset_version,
        dataset_root=args.dataset_root,
        targets=["pg", "media", "milvus", "es"],
        dry_run=args.dry_run,
    )
    db = SessionLocal()
    try:
        service = DemoIngestService(
            db=db,
            vector_client=get_vector_client(),
            text_client=get_text_client(),
            object_storage=get_object_storage(),
        )
        report = service.run(request)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()

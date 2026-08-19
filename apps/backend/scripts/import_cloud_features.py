from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parents[1]
PROCESSORS_ROOT = REPO_ROOT / "scripts" / "processors"

for path in (BACKEND_ROOT, PROCESSORS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.db.bootstrap import init_db  # noqa: E402
from src.cloud_sinks.config import SinkConfig  # noqa: E402
from src.config import get_processor_settings  # noqa: E402
from src.ingest_artifacts import ArtifactImportOptions, import_feature_artifacts  # noqa: E402


DEFAULT_LOCAL_ARTIFACTS = [
    *(path for path in (REPO_ROOT / "data").glob("processor_vector_L*.jsonl") if "_1000" not in path.stem),
    *(REPO_ROOT / "data" / "extracted").glob("*.jsonl"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import real cloud feature artifacts into Supabase/PostgreSQL and Zilliz/Milvus."
    )
    parser.add_argument(
        "--artifact-uri",
        action="append",
        default=[],
        help="Local JSONL file, local folder, GCS JSONL file, or GCS prefix. Repeatable.",
    )
    parser.add_argument("--dataset-code", default="aic-2026")
    parser.add_argument("--dataset-name", default="aic-ai-challenge-2025")
    parser.add_argument("--dataset-version", default="v1")
    parser.add_argument(
        "--dataset-root-uri",
        default="gs://aic_ai_2026/processed/keyframes/dataset=ai_challenge_2025",
    )
    parser.add_argument(
        "--milvus-collection",
        default=os.getenv("MILVUS_COLLECTION", "keyframe_embeddings_pe_core_bigG_14_448"),
    )
    parser.add_argument("--elasticsearch-index", default="keyframe_annotations")
    parser.add_argument("--model-version", default="cloud-feature-import")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--no-local-defaults", action="store_true", help="Do not use data/*.jsonl defaults.")
    parser.add_argument("--no-pg", action="store_true")
    parser.add_argument("--no-milvus", action="store_true")
    parser.add_argument("--no-elasticsearch", action="store_true")
    parser.add_argument(
        "--text-embedding-collection",
        default="",
        help="Optional Zilliz collection for Vietnamese text embeddings.",
    )
    parser.add_argument("--text-embedding-model-name", default="dangvantuan/vietnamese-embedding")
    parser.add_argument(
        "--no-text-embeddings",
        action="store_true",
        help="Skip text embeddings; visual embeddings are still upserted when artifacts contain `embedding`.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-init-db", action="store_true", help="Skip Alembic/table initialization.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_processor_settings()

    artifact_uris = list(args.artifact_uri)
    if not artifact_uris and not args.no_local_defaults:
        artifact_uris = [str(path) for path in sorted(DEFAULT_LOCAL_ARTIFACTS) if path.exists()]
    if not artifact_uris:
        raise SystemExit("No artifact URIs found. Pass --artifact-uri or keep local defaults enabled.")

    if not args.dry_run and not args.skip_init_db and not args.no_pg:
        init_db()

    config = SinkConfig(
        database_url=settings.database_url,
        milvus_uri=settings.milvus_uri,
        milvus_token=settings.milvus_token,
        elasticsearch_url=settings.elasticsearch_url,
        dataset_code=args.dataset_code,
        dataset_name=args.dataset_name,
        dataset_version=args.dataset_version,
        dataset_root_uri=args.dataset_root_uri,
        gcs_public_url=settings.gcs_public_url,
        milvus_collection=args.milvus_collection,
        elasticsearch_index=args.elasticsearch_index,
        model_version=args.model_version,
        write_pg=not args.no_pg,
        write_milvus=not args.no_milvus,
        write_elasticsearch=not args.no_elasticsearch,
        dry_run=args.dry_run,
    )
    summary = import_feature_artifacts(
        config,
        ArtifactImportOptions(
            artifact_uris=artifact_uris,
            batch_size=args.batch_size,
            write_pg=not args.no_pg,
            write_milvus=not args.no_milvus,
            write_elasticsearch=not args.no_elasticsearch,
            dry_run=args.dry_run,
            gcs_credentials_file=settings.gcs_credentials_file,
            text_embedding_collection=args.text_embedding_collection,
            text_embedding_model_name=args.text_embedding_model_name,
            write_text_embeddings=not args.no_text_embeddings,
        ),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

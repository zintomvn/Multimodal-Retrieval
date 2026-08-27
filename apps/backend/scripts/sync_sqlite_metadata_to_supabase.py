from __future__ import annotations

import argparse
import copy
import json
import re
import sqlite3
import sys
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, and_, create_engine, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import database_connect_args, get_settings, normalize_database_url  # noqa: E402
from app.modules.media.urls import gcs_public_url, split_gcs_uri  # noqa: E402


DEFAULT_SOURCE_DB = REPO_ROOT / "data" / "dev_search_local.db"

METADATA_TABLES = (
    "datasets",
    "model_registry",
    "videos",
    "shots",
    "keyframes",
    "events",
    "event_keyframes",
    "frame_annotations",
    "index_builds",
)
HISTORY_TABLES = (
    "query_runs",
    "retrieval_results",
    "submissions",
    "submission_items",
    "jobs",
)
TABLE_ORDER = (*METADATA_TABLES, *HISTORY_TABLES)

JSON_COLUMNS: dict[str, dict[str, Any]] = {
    "videos": {"extra_metadata": {}},
    "frame_annotations": {
        "json_value": {},
        "ocr_texts": [],
        "detected_objects": [],
        "object_counts": {},
        "detections": [],
    },
    "model_registry": {"config": {}},
    "index_builds": {"stats": {}},
    "query_runs": {"normalized_query": {}, "options": {}},
    "retrieval_results": {"score_breakdown": {}, "sequence_frames": []},
    "submissions": {"validation_report": {}},
    "submission_items": {"frame_indices": []},
    "jobs": {"payload": {}},
}

BOOLEAN_COLUMNS: dict[str, set[str]] = {
    "keyframes": {"is_media_present"},
    "retrieval_results": {"selected"},
}

DATETIME_COLUMNS = {"created_at", "updated_at", "completed_at"}

DATASET_FK_TABLES = {"videos", "index_builds", "query_runs", "submissions"}
PK_COLUMNS: dict[str, tuple[str, ...]] = {
    "datasets": ("dataset_id",),
    "videos": ("video_id",),
    "shots": ("shot_id",),
    "keyframes": ("keyframe_id",),
    "events": ("event_id",),
    "event_keyframes": ("event_id", "seq_no"),
    "frame_annotations": ("id",),
    "model_registry": ("id",),
    "index_builds": ("id",),
    "query_runs": ("id",),
    "retrieval_results": ("id",),
    "submissions": ("id",),
    "submission_items": ("id",),
    "jobs": ("id",),
}

BATCH_FILTER_COLUMNS = {
    "videos": "video_code",
    "shots": "video_id",
    "keyframes": "video_id",
    "events": "video_id",
    "event_keyframes": "keyframe_id",
    "frame_annotations": "frame_id",
    "retrieval_results": "frame_id",
}


def chunked(items: list[Any], size: int) -> Iterator[list[Any]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def parse_json_value(raw: Any, default: Any) -> Any:
    if raw is None or raw == "":
        return copy.deepcopy(default)
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return copy.deepcopy(default)
    return raw


def parse_datetime_value(raw: Any) -> Any:
    if raw in (None, "") or isinstance(raw, datetime):
        return raw
    value = str(raw).strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return raw


def parse_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw in (None, ""):
        return False
    if isinstance(raw, (int, float)):
        return bool(raw)
    return str(raw).strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def parse_batches(raw: str) -> list[str]:
    batches = [item.strip().upper() for item in (raw or "").split(",") if item.strip()]
    invalid = [item for item in batches if not re.fullmatch(r"L\d{2}", item)]
    if invalid:
        raise SystemExit(f"Invalid batch id(s): {', '.join(invalid)}. Use values like L21,L25,L26.")
    return batches


def resolve_tables(raw_tables: str, include_history: bool) -> list[str]:
    raw = (raw_tables or "metadata").strip().lower()
    if raw == "metadata":
        requested = list(METADATA_TABLES)
    elif raw == "all":
        requested = list(TABLE_ORDER)
    else:
        requested = [item.strip() for item in raw_tables.split(",") if item.strip()]

    if include_history:
        requested.extend(item for item in HISTORY_TABLES if item not in requested)

    unknown = [table for table in requested if table not in TABLE_ORDER]
    if unknown:
        raise SystemExit(f"Unknown table(s): {', '.join(unknown)}")
    return [table for table in TABLE_ORDER if table in set(requested)]


def sqlite_columns(source: sqlite3.Connection, table_name: str) -> list[str]:
    rows = source.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [str(row["name"]) for row in rows]


def sqlite_table_exists(source: sqlite3.Connection, table_name: str) -> bool:
    row = source.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def sqlite_where_clause(table_name: str, batches: list[str]) -> tuple[str, list[str]]:
    column = BATCH_FILTER_COLUMNS.get(table_name)
    if not batches or not column:
        return "", []
    placeholders = ",".join("?" for _ in batches)
    return f"WHERE substr({column}, 1, 3) IN ({placeholders})", batches


def source_count(source: sqlite3.Connection, table_name: str, batches: list[str]) -> int:
    where_sql, params = sqlite_where_clause(table_name, batches)
    return int(source.execute(f"SELECT count(*) FROM {table_name} {where_sql}", params).fetchone()[0])


def source_primary_keys(source: sqlite3.Connection, table_name: str, batches: list[str]) -> list[Any]:
    pk_cols = PK_COLUMNS[table_name]
    if len(pk_cols) != 1:
        return []
    where_sql, params = sqlite_where_clause(table_name, batches)
    pk = pk_cols[0]
    rows = source.execute(f"SELECT {pk} FROM {table_name} {where_sql}", params).fetchall()
    return [row[0] for row in rows]


def source_rows(
    source: sqlite3.Connection,
    table_name: str,
    batches: list[str],
    batch_size: int,
    limit: int,
) -> Iterator[list[sqlite3.Row]]:
    pk_cols = PK_COLUMNS[table_name]
    order_sql = ", ".join(pk_cols)
    where_sql, params = sqlite_where_clause(table_name, batches)
    fetched = 0
    offset = 0
    while True:
        page_size = batch_size
        if limit > 0:
            remaining = limit - fetched
            if remaining <= 0:
                return
            page_size = min(page_size, remaining)
        rows = source.execute(
            f"SELECT * FROM {table_name} {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
            [*params, page_size, offset],
        ).fetchall()
        if not rows:
            return
        fetched += len(rows)
        offset += len(rows)
        yield rows


def make_gcs_uri(bucket: str, object_key: str) -> str:
    key = str(object_key or "").strip("/")
    return f"gs://{bucket}/{key}" if bucket and key else ""


def enrich_keyframe_cloud_fields(row: dict[str, Any], *, bucket: str, public_base_url: str) -> None:
    image_storage_key = str(row.get("image_storage_key") or "").strip()
    if not image_storage_key:
        parsed = split_gcs_uri(str(row.get("image_uri") or ""), default_bucket=bucket)
        image_storage_key = parsed[1] if parsed else ""
    if not image_storage_key:
        return

    row["image_storage_key"] = image_storage_key
    if not row.get("image_uri") and bucket:
        row["image_uri"] = make_gcs_uri(bucket, image_storage_key)

    public_url = gcs_public_url(
        row.get("image_url") or row.get("thumbnail_uri") or image_storage_key,
        default_bucket=bucket,
        public_base_url=public_base_url,
    )
    if public_url:
        row["image_url"] = public_url
        row["thumbnail_uri"] = public_url


def remap_dataset_json(payload: dict[str, Any], dataset_id_map: dict[str, str]) -> None:
    for column in ("options", "normalized_query", "validation_report", "payload", "stats"):
        value = payload.get(column)
        if not isinstance(value, dict):
            continue
        old_dataset_id = value.get("dataset_id")
        if old_dataset_id in dataset_id_map:
            value["dataset_id"] = dataset_id_map[old_dataset_id]


def normalize_payload(
    table_name: str,
    row: sqlite3.Row,
    target_columns: set[str],
    dataset_id_map: dict[str, str],
    *,
    bucket: str,
    public_base_url: str,
) -> dict[str, Any]:
    payload = {key: row[key] for key in row.keys() if key in target_columns}

    if table_name in DATASET_FK_TABLES and payload.get("dataset_id") in dataset_id_map:
        payload["dataset_id"] = dataset_id_map[payload["dataset_id"]]

    for column, default in JSON_COLUMNS.get(table_name, {}).items():
        if column in payload:
            payload[column] = parse_json_value(payload[column], default)

    for column in BOOLEAN_COLUMNS.get(table_name, set()):
        if column in payload:
            payload[column] = parse_bool(payload[column])

    for column in DATETIME_COLUMNS:
        if column in payload:
            payload[column] = parse_datetime_value(payload[column])

    if table_name == "keyframes":
        enrich_keyframe_cloud_fields(payload, bucket=bucket, public_base_url=public_base_url)
    if table_name in {"query_runs", "submissions", "jobs", "index_builds"}:
        remap_dataset_json(payload, dataset_id_map)

    return payload


def dedupe_rows(rows: list[dict[str, Any]], pk_cols: tuple[str, ...]) -> list[dict[str, Any]]:
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(column) for column in pk_cols)
        by_key[key] = row
    return list(by_key.values())


def upsert_batch(target_conn: Any, table: Table, rows: list[dict[str, Any]], *, insert_only: bool) -> int:
    if not rows:
        return 0
    pk_cols = PK_COLUMNS[table.name]
    rows = dedupe_rows(rows, pk_cols)
    stmt = pg_insert(table).values(rows)
    if insert_only:
        stmt = stmt.on_conflict_do_nothing(index_elements=list(pk_cols))
    else:
        update_cols = {
            column.name: stmt.excluded[column.name]
            for column in table.columns
            if column.name not in pk_cols
        }
        if update_cols:
            stmt = stmt.on_conflict_do_update(index_elements=list(pk_cols), set_=update_cols)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=list(pk_cols))
    target_conn.execute(stmt)
    # psycopg may report -1 for multi-row INSERT .. ON CONFLICT statements.
    # Returning the attempted row count gives a stable sync summary.
    return len(rows)


def reflect_tables(target_engine: Any, table_names: Iterable[str]) -> dict[str, Table]:
    metadata = MetaData()
    metadata.reflect(bind=target_engine, only=list(table_names))
    return {name: metadata.tables[name] for name in table_names}


def build_dataset_id_map(
    source: sqlite3.Connection,
    target_engine: Any,
    dataset_table: Table,
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not sqlite_table_exists(source, "datasets"):
        return mapping
    rows = source.execute("SELECT * FROM datasets ORDER BY dataset_code, version").fetchall()
    with target_engine.connect() as conn:
        for row in rows:
            source_id = str(row["dataset_id"])
            existing_id = conn.execute(
                select(dataset_table.c.dataset_id).where(
                    or_(
                        dataset_table.c.dataset_code == row["dataset_code"],
                        and_(
                            dataset_table.c.name == row["name"],
                            dataset_table.c.version == row["version"],
                        ),
                    )
                )
            ).scalar_one_or_none()
            mapping[source_id] = str(existing_id or source_id)
    return mapping


def sync_datasets(
    source: sqlite3.Connection,
    target_engine: Any,
    dataset_table: Table,
    dataset_id_map: dict[str, str],
    *,
    dry_run: bool,
) -> dict[str, int]:
    stats = {"source_rows": 0, "target_rows_before": 0, "upserted_rows": 0}
    if not sqlite_table_exists(source, "datasets"):
        return stats

    rows = source.execute("SELECT * FROM datasets ORDER BY dataset_code, version").fetchall()
    stats["source_rows"] = len(rows)
    with target_engine.connect() as conn:
        stats["target_rows_before"] = int(conn.execute(select(func.count()).select_from(dataset_table)).scalar_one())

    if dry_run:
        return stats

    target_columns = {column.name for column in dataset_table.columns}
    with target_engine.begin() as conn:
        for row in rows:
            payload = {key: row[key] for key in row.keys() if key in target_columns}
            target_dataset_id = dataset_id_map.get(str(row["dataset_id"]), str(row["dataset_id"]))
            payload["dataset_id"] = target_dataset_id
            existing = conn.execute(
                select(dataset_table.c.dataset_id).where(dataset_table.c.dataset_id == target_dataset_id)
            ).scalar_one_or_none()
            if existing:
                update_values = {key: value for key, value in payload.items() if key != "dataset_id"}
                conn.execute(
                    dataset_table.update()
                    .where(dataset_table.c.dataset_id == target_dataset_id)
                    .values(**update_values)
                )
                stats["upserted_rows"] += 1
            else:
                conn.execute(dataset_table.insert().values(**payload))
                stats["upserted_rows"] += 1
    return stats


def target_existing_single_pk_count(target_engine: Any, table: Table, keys: list[Any], pk_col: str) -> int:
    if not keys:
        return 0
    existing = 0
    with target_engine.connect() as conn:
        for key_chunk in chunked(keys, 5000):
            rows = conn.execute(select(table.c[pk_col]).where(table.c[pk_col].in_(key_chunk))).all()
            existing += len(rows)
    return existing


def inspect_table(
    source: sqlite3.Connection,
    target_engine: Any,
    table: Table,
    table_name: str,
    batches: list[str],
    dataset_id_map: dict[str, str],
) -> dict[str, Any]:
    source_rows_n = source_count(source, table_name, batches)
    with target_engine.connect() as conn:
        target_rows_n = int(conn.execute(select(func.count()).select_from(table)).scalar_one())
    stats: dict[str, Any] = {
        "source_rows": source_rows_n,
        "target_rows_before": target_rows_n,
    }
    pk_cols = PK_COLUMNS[table_name]
    if table_name == "datasets":
        keys = [str(key) for key in source_primary_keys(source, table_name, batches)]
        mapped_target_ids = {dataset_id_map.get(key, key) for key in keys}
        with target_engine.connect() as conn:
            existing_ids = {
                str(row[0])
                for row in conn.execute(
                    select(table.c.dataset_id).where(table.c.dataset_id.in_(list(mapped_target_ids)))
                ).all()
            }
        existing_for_source = sum(1 for key in keys if dataset_id_map.get(key, key) in existing_ids)
        stats["existing_source_keys"] = existing_for_source
        stats["missing_source_keys"] = max(0, len(keys) - existing_for_source)
        return stats

    if len(pk_cols) == 1:
        keys = source_primary_keys(source, table_name, batches)
        existing_for_source = target_existing_single_pk_count(target_engine, table, keys, pk_cols[0])
        stats["existing_source_keys"] = existing_for_source
        stats["missing_source_keys"] = max(0, len(keys) - existing_for_source)
    return stats


def sync_table(
    source: sqlite3.Connection,
    target_engine: Any,
    table: Table,
    table_name: str,
    dataset_id_map: dict[str, str],
    *,
    batches: list[str],
    batch_size: int,
    limit: int,
    insert_only: bool,
    bucket: str,
    public_base_url: str,
) -> dict[str, int]:
    target_columns = {column.name for column in table.columns}
    stats = {"source_rows": source_count(source, table_name, batches), "upserted_rows": 0}
    for source_batch in source_rows(source, table_name, batches, batch_size=batch_size, limit=limit):
        rows = [
            normalize_payload(
                table_name,
                row,
                target_columns,
                dataset_id_map,
                bucket=bucket,
                public_base_url=public_base_url,
            )
            for row in source_batch
        ]
        with target_engine.begin() as target_conn:
            stats["upserted_rows"] += upsert_batch(target_conn, table, rows, insert_only=insert_only)
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync backend metadata from local SQLite into Supabase/PostgreSQL."
    )
    parser.add_argument("--source-db", default=str(DEFAULT_SOURCE_DB), help="Local SQLite DB to read.")
    parser.add_argument(
        "--database-url",
        default="",
        help="Target PostgreSQL URL. Defaults to DATABASE_URL from .env; avoid passing secrets in shell history.",
    )
    parser.add_argument("--tables", default="metadata", help="'metadata', 'all', or comma-separated table names.")
    parser.add_argument("--include-history", action="store_true", help="Also copy query/submission/job history.")
    parser.add_argument("--batches", default="", help="Optional batch filter, for example L21,L25,L26.")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--limit", type=int, default=0, help="Limit rows per table for smoke tests.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect missing rows without writing.")
    parser.add_argument("--insert-only", action="store_true", help="Do not update existing target rows.")
    parser.add_argument("--allow-sqlite-target", action="store_true", help="Allow non-PostgreSQL target for local tests.")
    parser.add_argument("--gcs-bucket", default="", help="Defaults to GCS_BUCKET from .env.")
    parser.add_argument("--gcs-public-url", default="", help="Defaults to GCS_PUBLIC_URL from .env.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    source_db = Path(args.source_db).expanduser().resolve()
    if not source_db.is_file():
        raise SystemExit(f"Source SQLite DB not found: {source_db}")

    database_url = normalize_database_url(args.database_url or settings.database_url)
    if not database_url.startswith("postgresql") and not args.allow_sqlite_target:
        raise SystemExit("Target must be PostgreSQL/Supabase. Set DATABASE_URL or pass --database-url.")

    tables = resolve_tables(args.tables, include_history=args.include_history)
    batches = parse_batches(args.batches)
    bucket = args.gcs_bucket or settings.gcs_bucket
    public_base_url = args.gcs_public_url or settings.gcs_public_url

    source = sqlite3.connect(source_db)
    source.row_factory = sqlite3.Row
    missing = [table for table in tables if not sqlite_table_exists(source, table)]
    if missing:
        raise SystemExit(f"Source DB is missing table(s): {', '.join(missing)}")

    target_engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args=database_connect_args(database_url),
    )
    reflected = reflect_tables(target_engine, tables)
    dataset_id_map = build_dataset_id_map(source, target_engine, reflected["datasets"])

    summary: dict[str, Any] = {
        "dry_run": args.dry_run,
        "source_db": str(source_db),
        "target": "postgresql",
        "tables": tables,
        "batches": batches or "all",
        "batch_size": args.batch_size,
        "limit": args.limit,
        "insert_only": args.insert_only,
        "dataset_id_map": dataset_id_map,
        "counts": {},
    }

    try:
        if args.dry_run:
            for table_name in tables:
                summary["counts"][table_name] = inspect_table(
                    source,
                    target_engine,
                    reflected[table_name],
                    table_name,
                    batches,
                    dataset_id_map,
                )
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return

        for table_name in tables:
            if table_name == "datasets":
                summary["counts"][table_name] = sync_datasets(
                    source,
                    target_engine,
                    reflected[table_name],
                    dataset_id_map,
                    dry_run=False,
                )
                continue
            summary["counts"][table_name] = sync_table(
                source,
                target_engine,
                reflected[table_name],
                table_name,
                dataset_id_map,
                batches=batches,
                batch_size=max(1, args.batch_size),
                limit=max(0, args.limit),
                insert_only=args.insert_only,
                bucket=bucket,
                public_base_url=public_base_url,
            )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        source.close()
        target_engine.dispose()


if __name__ == "__main__":
    main()

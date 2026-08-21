from __future__ import annotations

from collections.abc import Generator
import logging
import time
from urllib.parse import urlparse

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import database_connect_args, get_settings


settings = get_settings()
logger = logging.getLogger(__name__)


def _is_supabase_transaction_pooler(database_url: str) -> bool:
    if not database_url.startswith("postgresql"):
        return False
    parsed = urlparse(database_url)
    return "supabase.com" in (parsed.hostname or "") and parsed.port == 6543


def _engine_options(database_url: str) -> dict:
    options: dict = {"connect_args": database_connect_args(database_url)}
    if database_url.startswith("sqlite"):
        options["pool_pre_ping"] = True
        return options
    if _is_supabase_transaction_pooler(database_url):
        # Supabase transaction pooler owns the actual pooling. Holding SQLAlchemy
        # idle connections can exhaust Supavisor checkout slots and make the UI
        # hang on /api/datasets or search requests.
        options["poolclass"] = NullPool
        return options
    options.update(
        {
            "pool_pre_ping": True,
            "pool_size": 2,
            "max_overflow": 2,
            "pool_recycle": 300,
            "pool_use_lifo": True,
        }
    )
    return options


SQLITE_RUNTIME_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_frame_annotations_frame_id ON frame_annotations(frame_id)",
    "CREATE INDEX IF NOT EXISTS ix_frame_annotations_frame_id_kind ON frame_annotations(frame_id, kind)",
    "CREATE INDEX IF NOT EXISTS ix_keyframes_video_frame_idx ON keyframes(video_id, frame_idx)",
    "CREATE INDEX IF NOT EXISTS ix_videos_dataset_id ON videos(dataset_id)",
    "CREATE INDEX IF NOT EXISTS ix_query_runs_created_at ON query_runs(created_at)",
)


engine = create_engine(settings.database_url, **_engine_options(settings.database_url))


if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
            except Exception:
                # Docker Desktop bind mounts can intermittently reject switching
                # journal mode while another process has the file open. Keep the
                # connection usable; the shorter write transactions and timeout
                # still prevent the search-history commits from failing.
                logger.debug("SQLite WAL pragma was skipped for this connection.", exc_info=True)
        finally:
            cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=engine)


def _prepare_sqlite_database() -> None:
    try:
        with engine.begin() as connection:
            for statement in SQLITE_RUNTIME_INDEXES:
                connection.exec_driver_sql(statement)
    except Exception:  # pragma: no cover - runtime DB may not be initialized yet.
        logger.warning("SQLite runtime index preparation failed.", exc_info=True)


def warm_database(max_attempts: int = 3, retry_delay_seconds: float = 1.5) -> None:
    """Open one real connection before the app accepts requests."""
    if settings.database_url.startswith("sqlite"):
        _prepare_sqlite_database()
        return
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        started = time.monotonic()
        try:
            with engine.connect() as connection:
                connection.execute(text("select 1"))
            elapsed = time.monotonic() - started
            logger.info("Database connection ready in %.2fs", elapsed)
            return
        except Exception as exc:  # pragma: no cover - depends on network state
            last_error = exc
            logger.warning(
                "Database warmup attempt %s/%s failed: %s",
                attempt,
                max_attempts,
                exc,
            )
            if attempt < max_attempts:
                time.sleep(retry_delay_seconds)
    if last_error is not None:
        raise last_error


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        if db.in_transaction():
            db.rollback()
        db.close()

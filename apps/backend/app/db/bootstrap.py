from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings


def init_db() -> None:
    settings = get_settings()
    backend_root = Path(__file__).resolve().parents[2]
    alembic_ini = backend_root / "alembic.ini"
    if alembic_ini.exists():
        try:
            from alembic import command
            from alembic.config import Config

            cfg = Config(str(alembic_ini))
            cfg.set_main_option("sqlalchemy.url", settings.database_url)
            command.upgrade(cfg, "head")
            return
        except Exception:
            # Fall back to metadata create in environments where Alembic is unavailable.
            pass
    # Fallback for environments where Alembic is not present yet.
    from app.db.models import Base
    from app.db.session import engine

    Base.metadata.create_all(bind=engine)

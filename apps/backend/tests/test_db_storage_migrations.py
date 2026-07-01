from __future__ import annotations

from pathlib import Path
import sys

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _alembic_config(db_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", db_url)
    return config


def test_m1_upgrade_creates_core_tables_and_constraints(tmp_path: Path) -> None:
    db_path = tmp_path / "m1_upgrade.db"
    db_url = f"sqlite:///{db_path}"
    cfg = _alembic_config(db_url)

    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    expected_core = {"datasets", "videos", "shots", "keyframes", "frame_annotations", "events", "event_keyframes"}
    assert expected_core.issubset(tables)

    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(
            text(
                """
                INSERT INTO datasets (dataset_id, dataset_code, name, version, root_uri, status)
                VALUES ('d1', 'mock-aic-2026', 'mock-aic-2026', 'v0', 'data/mock', 'READY')
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO videos (
                  video_id, dataset_id, video_code, video_name, uri, fps, duration_seconds, duration_ms, extra_metadata
                ) VALUES (
                  'L30_V001', 'd1', 'L30_V001', 'L30_V001.mp4', 'data/mock/videos/L30_V001.mp4', 25, 12.0, 12000, '{}'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO shots (
                  shot_id, video_id, shot_index, start_frame, end_frame, start_seconds, end_seconds
                ) VALUES ('L30_V001_S0000', 'L30_V001', 0, 0, 100, 0.0, 4.0)
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO keyframes (
                  keyframe_id, video_id, shot_id, frame_idx, frame_seconds, timestamp_ms, image_uri
                ) VALUES ('L30_V001_F000001', 'L30_V001', 'L30_V001_S0000', 1, 0.04, 40, 'mock://L30_V001/1.jpg')
                """
            )
        )

        with pytest.raises(Exception):
            conn.execute(
                text(
                    """
                    INSERT INTO videos (
                      video_id, dataset_id, video_code, video_name, uri, fps, duration_seconds, duration_ms, extra_metadata
                    ) VALUES (
                      'L30_V001_DUP', 'd1', 'L30_V001', 'L30_V001_dup.mp4', 'data/mock/videos/L30_V001_dup.mp4', 25, 12.0, 12000, '{}'
                    )
                    """
                )
            )

        with pytest.raises(Exception):
            conn.execute(
                text(
                    """
                    INSERT INTO keyframes (
                      keyframe_id, video_id, shot_id, frame_idx, frame_seconds, timestamp_ms, image_uri
                    ) VALUES ('L30_V001_F000002', 'L30_V001', 'MISSING_SHOT', 2, 0.08, 80, 'mock://L30_V001/2.jpg')
                    """
                )
            )

        orphan_count = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM keyframes k
                LEFT JOIN shots s ON s.shot_id = k.shot_id
                WHERE k.shot_id IS NOT NULL AND s.shot_id IS NULL
                """
            )
        ).scalar_one()
        assert orphan_count == 0


def test_m1_downgrade_drops_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "m1_downgrade.db"
    db_url = f"sqlite:///{db_path}"
    cfg = _alembic_config(db_url)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(db_url)
    inspector = inspect(engine)
    remaining_tables = set(inspector.get_table_names()) - {"alembic_version"}
    assert not remaining_tables

from __future__ import annotations

from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.modules.pipeline.service import _load_source_config


def test_load_drive_source_config_uses_drive_prefix() -> None:
    config = _load_source_config("l21_l30_ai_challenge_2025_drive")
    assert config.source_type == "drive"
    assert config.source_dataset_id == "ai_challenge_2025"
    assert config.raw_prefix.startswith("raw/source=drive/dataset=ai_challenge_2025")


def test_load_kaggle_source_config_keeps_kaggle_prefix() -> None:
    config = _load_source_config("l21_l30_ai_challenge_2025")
    assert config.source_type == "kaggle"
    assert config.raw_prefix.startswith("raw/source=kaggle/dataset=ai_challenge_2025")

from __future__ import annotations

from pathlib import Path
import sys

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.adapters.object_storage.key_format import (  # noqa: E402
    build_keyframe_object_key,
    build_keyframe_object_key_from_rel_path,
)


def test_build_keyframe_object_key() -> None:
    key = build_keyframe_object_key(video_id="L30_V001", filename="shot_0000_first_f000001.jpg")
    assert key == "keyframes/L30_V001/shot_0000_first_f000001.jpg"


def test_build_keyframe_object_key_from_rel_path() -> None:
    key = build_keyframe_object_key_from_rel_path("L30_V001/shot_0000_first_f000001.jpg")
    assert key == "keyframes/L30_V001/shot_0000_first_f000001.jpg"


def test_build_keyframe_object_key_validates_input() -> None:
    with pytest.raises(ValueError):
        build_keyframe_object_key(video_id="", filename="frame.jpg")
    with pytest.raises(ValueError):
        build_keyframe_object_key_from_rel_path("frame.jpg")

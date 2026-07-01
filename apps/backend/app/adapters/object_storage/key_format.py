from __future__ import annotations

from pathlib import PurePosixPath


def build_keyframe_object_key(video_id: str, filename: str, prefix: str = "keyframes") -> str:
    safe_video = video_id.strip().replace("\\", "/").strip("/")
    safe_file = filename.strip().replace("\\", "/").split("/")[-1]
    if not safe_video:
        raise ValueError("video_id must not be empty")
    if not safe_file:
        raise ValueError("filename must not be empty")
    return str(PurePosixPath(prefix) / safe_video / safe_file)


def build_keyframe_object_key_from_rel_path(relative_path: str, prefix: str = "keyframes") -> str:
    normalized = relative_path.strip().replace("\\", "/").strip("/")
    if not normalized:
        raise ValueError("relative_path must not be empty")
    parts = normalized.split("/")
    if len(parts) < 2:
        raise ValueError("relative_path must include video folder and filename")
    video_id = parts[0]
    filename = parts[-1]
    return build_keyframe_object_key(video_id=video_id, filename=filename, prefix=prefix)

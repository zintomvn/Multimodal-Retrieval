from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from import_asr_to_elasticsearch import load_frame_index


def test_frame_pages_preserve_dataset_filter_and_chronological_order(tmp_path):
    path = tmp_path / "metadata.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE datasets (dataset_id TEXT, dataset_code TEXT);
            CREATE TABLE videos (video_id TEXT, video_code TEXT, dataset_id TEXT);
            CREATE TABLE keyframes (keyframe_id TEXT PRIMARY KEY, video_id TEXT,
                frame_idx INTEGER, frame_seconds REAL, timestamp_ms INTEGER);
            INSERT INTO datasets VALUES ('d1', 'wanted'), ('d2', 'other');
            INSERT INTO videos VALUES ('v1', 'L21_V001', 'd1'), ('v2', 'L22_V001', 'd2');
        """)
        db.executemany("INSERT INTO keyframes VALUES (?, ?, ?, ?, ?)",
            [(f"f{i:05d}", "v1", 5001-i, float(5001-i), (5001-i)*1000) for i in range(5002)])
        db.execute("INSERT INTO keyframes VALUES ('other', 'v2', 0, 0, 0)")
    result = load_frame_index(f"sqlite:///{path}", "wanted", False)
    assert set(result) == {"v1"}
    assert len(result["v1"]) == 5002
    assert len({frame.keyframe_id for frame in result["v1"]}) == 5002
    assert [frame.frame_seconds for frame in result["v1"]] == list(range(5002))
    assert set(load_frame_index(f"sqlite:///{path}", "wanted", True)) == {"v1", "v2"}

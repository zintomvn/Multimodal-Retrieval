"""Register M/N/S source videos from GCS raw storage before keyframe processing finishes."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path, PurePosixPath

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import database_connect_args, get_settings  # noqa: E402
from app.db.models import Video  # noqa: E402


RAW_VIDEO_PREFIX = 'raw/source=kaggle/dataset=aiteam_dataset_batch_2/source_version=kaggle_current/'
RAW_VIDEO_PATTERN = re.compile(
    r'^batch=(?P<folder>N\d{3}|S-M\d{2}|S01)/videos/'
    r'(?P<video>N\d{3}-V\d+|M\d{2}_V\d+|S01-V\d+)\.(?P<ext>mov|mp4|mkv|avi)$', re.I,
)


def source_videos(client, bucket: str):
    for blob in client.list_blobs(bucket, prefix=RAW_VIDEO_PREFIX, timeout=60):
        relative = blob.name.removeprefix(RAW_VIDEO_PREFIX)
        match = RAW_VIDEO_PATTERN.fullmatch(relative)
        if not match:
            continue
        video_id = match.group('video').upper()
        batch_id = video_id.split('-')[0] if video_id.startswith(('N', 'S')) else video_id.split('_')[0]
        expected_folder = f'S-{batch_id}' if batch_id.startswith('M') else batch_id
        if match.group('folder').upper() != expected_folder:
            continue
        yield {
            'batch_id': batch_id,
            'video_id': video_id,
            'video_name': PurePosixPath(blob.name).name,
            'uri': f'gs://{bucket}/{blob.name}',
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-code', default='aic-2026')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    settings = get_settings()
    from google.cloud import storage
    client = storage.Client.from_service_account_json(settings.gcs_credentials_file)
    videos = list(source_videos(client, settings.gcs_bucket))
    if len({video['video_id'] for video in videos}) != len(videos):
        raise RuntimeError('Duplicate raw video IDs in GCS')
    print(json.dumps({'discovered': len(videos),
                      'batches': dict(sorted(Counter(v['batch_id'] for v in videos).items()))}), flush=True)
    if args.dry_run:
        return

    engine = create_engine(settings.database_url, pool_pre_ping=True,
                           connect_args=database_connect_args(settings.database_url))
    with engine.begin() as connection:
        dataset_id = connection.execute(
            text('SELECT dataset_id FROM datasets WHERE dataset_code=:code'),
            {'code': args.dataset_code},
        ).scalar_one()
        existing = set(connection.execute(text("SELECT video_id FROM videos WHERE left(video_id, 1) IN ('M', 'N', 'S')")).scalars())
        for start in range(0, len(videos), 500):
            rows = [{
                'video_id': v['video_id'], 'dataset_id': dataset_id,
                'video_code': v['video_id'], 'video_name': v['video_name'],
                'uri': v['uri'], 'source_video_path': v['uri'],
                'num_keyframes': 0,
                'extra_metadata': {'batch_id': v['batch_id'], 'source': 'raw_gcs_video',
                                   'source_video_available': True},
            } for v in videos[start:start + 500]]
            statement = pg_insert(Video.__table__).values(rows)
            connection.execute(statement.on_conflict_do_update(
                index_elements=['video_id'],
                set_={field: getattr(statement.excluded, field)
                      for field in ('video_name', 'uri', 'source_video_path')},
            ))
    print(json.dumps({'inserted': sum(v['video_id'] not in existing for v in videos),
                      'updated': sum(v['video_id'] in existing for v in videos)}), flush=True)


if __name__ == '__main__':
    main()

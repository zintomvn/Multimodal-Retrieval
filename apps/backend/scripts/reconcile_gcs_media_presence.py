"""Check imported M/N/S frame objects and update keyframes.is_media_present."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import database_connect_args, get_settings  # noqa: E402


PREFIXES = (
    'processed/keyframes/dataset=aic_batch_m/',
    'raw/source=kaggle/dataset=aiteam_dataset_batch_2_keyframes_m/',
    'processed/keyframes/dataset=aic_batch_n/',
    'raw/source=kaggle/dataset=batch_s01/',
)


def chunks(items: list[str], size: int = 1000):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True,
                           connect_args=database_connect_args(settings.database_url))
    with engine.connect() as connection:
        rows = connection.execute(text('''
            SELECT k.keyframe_id, k.image_storage_key, k.is_media_present, v.video_id
            FROM keyframes k JOIN videos v ON v.video_id = k.video_id
            WHERE left(v.video_id, 1) IN ('M', 'N', 'S')
        ''')).mappings().all()
    needed = {row['image_storage_key'] for row in rows if row['image_storage_key']}

    from google.cloud import storage
    client = storage.Client.from_service_account_json(settings.gcs_credentials_file)
    found: set[str] = set()
    listed = Counter()
    for prefix in PREFIXES:
        for blob in client.list_blobs(settings.gcs_bucket, prefix=prefix, timeout=60):
            listed[prefix] += 1
            if blob.name in needed:
                found.add(blob.name)
        print(json.dumps({'listed_prefix': prefix, 'objects': listed[prefix]}), flush=True)

    to_true: list[str] = []
    to_false: list[str] = []
    by_batch = Counter()
    for row in rows:
        present = row['image_storage_key'] in found
        by_batch[(str(row['video_id'])[0], present)] += 1
        if present != bool(row['is_media_present']):
            (to_true if present else to_false).append(row['keyframe_id'])
    report = {
        'frames': len(rows), 'found': len(found),
        'by_batch': {f'{batch}:{"present" if present else "missing"}': count
                     for (batch, present), count in sorted(by_batch.items())},
        'set_present': len(to_true), 'set_missing': len(to_false),
        'dry_run': args.dry_run,
    }
    if not args.dry_run:
        with engine.begin() as connection:
            for value, ids in ((True, to_true), (False, to_false)):
                for part in chunks(ids):
                    connection.execute(text('''
                        UPDATE keyframes SET is_media_present = :present
                        WHERE keyframe_id = ANY(:ids)
                    '''), {'present': value, 'ids': part})
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()

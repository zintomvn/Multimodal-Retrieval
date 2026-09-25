"""Import M/N/S VLM archives into PostgreSQL and Elasticsearch.

The archives contain local image URIs. This importer reconstructs their GCS
object keys and uses one canonical frame ID across PG, ES and Zilliz.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import uuid
from collections import defaultdict
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import database_connect_args, get_settings  # noqa: E402
from app.db.models import Frame, FrameAnnotation, Video  # noqa: E402


def archive_rows(archives: list[Path]):
    for archive in archives:
        with ZipFile(archive) as source:
            for name in sorted(source.namelist()):
                if not name.endswith('/annotations.jsonl') or name.startswith('__MACOSX/'):
                    continue
                with source.open(name) as handle:
                    for line in handle:
                        if line.strip():
                            yield json.loads(line)


def canonical_id(row: dict) -> str:
    return f"{row['video_id']}_F{int(row['frame_idx']):06d}"


def image_key(row: dict, s_roles: dict[tuple[str, str, int], str]) -> str:
    batch = str(row['batch_id'])
    video = str(row['video_id'])
    frame_idx = int(row['frame_idx'])
    if batch.startswith('M'):
        image_name = PurePosixPath(str(row['image_gcs_uri'])).name
        source = (
            'processed/keyframes/dataset=aic_batch_m'
            if int(batch[1:]) <= 3
            else 'raw/source=kaggle/dataset=aiteam_dataset_batch_2_keyframes_m'
        )
        return (f'{source}/source_version=kaggle_current/batch=M/'
                f'{int(batch[1:]):03d}_Keyframes_{batch}/keyframes/{video}/{image_name}')
    if batch.startswith('N'):
        image_name = PurePosixPath(str(row['image_gcs_uri'])).name
        return f'processed/keyframes/dataset=aic_batch_n/batch={batch}/frames/{image_name}'
    shot = str(row['shot_id'])
    shot_number = int(shot.rsplit('_S', 1)[1])
    role = s_roles[(video, shot, frame_idx)]
    image_name = f'{video}__shot_{shot_number:04d}_{role}_f{frame_idx:06d}.jpg'
    return (f'raw/source=kaggle/dataset=batch_s01/source_version=kaggle_current/'
            f'batch=S01/keyframes-s01-v{int(video.rsplit("V", 1)[1]):03d}/frames/{image_name}')


def frame_seconds(row: dict) -> float:
    seconds = float(row.get('frame_sec') or 0)
    if str(row['batch_id']).startswith('N'):
        match = re.search(r'_t(\d+(?:\.\d+)?)s', str(row['keyframe_id']))
        if match:
            seconds = float(match.group(1))
    return seconds


def prepare(archives: list[Path]):
    video_stats: dict[str, dict] = {}
    shot_frames: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row in archive_rows(archives):
        video = str(row['video_id'])
        batch = str(row['batch_id'])
        stats = video_stats.setdefault(video, {'batch': batch, 'frames': set(), 'duration': 0.0})
        stats['frames'].add(int(row['frame_idx']))
        stats['duration'] = max(stats['duration'], frame_seconds(row))
        if batch.startswith('S'):
            shot_frames[(video, str(row['shot_id']))].append(int(row['frame_idx']))
    s_roles = {}
    for (video, shot), frames in shot_frames.items():
        ordered = sorted(set(frames))
        for index, frame in enumerate(ordered):
            role = 'first' if index == 0 else 'last' if index == len(ordered) - 1 else 'middle'
            s_roles[(video, shot, frame)] = role
    return video_stats, s_roles


def batches(items, size=500):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def upsert(connection, table, rows: list[dict], keys: list[str], updates: list[str]) -> None:
    if not rows:
        return
    for chunk in batches(rows):
        statement = pg_insert(table).values(chunk)
        statement = statement.on_conflict_do_update(
            index_elements=keys,
            set_={key: getattr(statement.excluded, key) for key in updates},
        )
        connection.execute(statement)


def pg_import(engine, archives: list[Path], stats: dict, s_roles: dict, bucket: str,
              dataset_code: str, map_dir: Path, video_only: bool = False) -> dict:
    counters = defaultdict(int)
    map_rows: dict[str, dict[int, tuple[int, float]]] = defaultdict(dict)
    with engine.begin() as connection:
        dataset_id = connection.execute(
            text('SELECT dataset_id FROM datasets WHERE dataset_code=:code'),
            {'code': dataset_code},
        ).scalar_one()
        videos = []
        for video, item in stats.items():
            batch = item['batch']
            raw_folder = f'S-{batch}' if batch.startswith('M') else batch
            extension = '.mov' if batch.startswith('N') else '.mp4'
            source_video = (
                f'gs://{bucket}/raw/source=kaggle/dataset=aiteam_dataset_batch_2/'
                f'source_version=kaggle_current/batch={raw_folder}/videos/{video}{extension}'
            )
            videos.append({
                'video_id': video, 'dataset_id': dataset_id, 'video_code': video,
                'video_name': video + extension,
                'uri': source_video,
                'source_video_path': source_video,
                'duration_seconds': item['duration'],
                'duration_ms': round(item['duration'] * 1000),
                'num_keyframes': len(item['frames']),
                'extra_metadata': {'batch_id': batch, 'source': 'object_detection_vlm_archive',
                                   'source_video_available': bool(source_video)},
            })
        upsert(connection, Video.__table__, videos, ['video_id'],
               ['video_name', 'uri', 'source_video_path', 'duration_seconds', 'duration_ms',
                'num_keyframes', 'extra_metadata'])
        counters['videos'] = len(videos)

    if video_only:
        return dict(counters)

    seen: set[str] = set()
    frames: list[dict] = []
    annotations: list[dict] = []
    for row in archive_rows(archives):
        frame_id = canonical_id(row)
        if frame_id in seen:
            counters['duplicate_frame_rows'] += 1
            continue
        seen.add(frame_id)
        video = str(row['video_id'])
        key = image_key(row, s_roles)
        seconds = frame_seconds(row)
        frame_idx = int(row['frame_idx'])
        image_name = PurePosixPath(key).name
        map_n = None
        if str(row['batch_id']).startswith('M'):
            map_n = int(PurePosixPath(image_name).stem)
            map_rows[video][map_n] = (frame_idx, seconds)
        frames.append({
            'keyframe_id': frame_id, 'video_id': video,
            'frame_idx': frame_idx, 'frame_seconds': seconds,
            'timestamp_ms': round(seconds * 1000), 'frame_type': 'middle',
            'map_n': map_n, 'embedding_index_0': map_n - 1 if map_n else None,
            'image_rel_path': f'{video}/{image_name}',
            'image_storage_key': key, 'image_uri': f'gs://{bucket}/{key}',
            'is_media_present': False,
        })
        annotations.append({
            'id': str(uuid.uuid5(uuid.NAMESPACE_URL, f'vlm:{frame_id}')),
            'frame_id': frame_id, 'kind': str(row.get('kind') or 'semantic_factors'),
            'text_value': row.get('text_value') or '',
            'json_value': row.get('json_value') or {},
            'confidence': float(row.get('confidence') or 1),
            'model_version': row.get('model_version') or 'unknown',
            'caption': row.get('caption') or '',
            'ocr_texts': row.get('ocr_texts') or [],
            'detected_objects': row.get('detected_objects') or [],
            'object_counts': row.get('object_counts') or {},
            'detections': row.get('detections') or [],
            'annotation_version': row.get('annotation_version'),
        })
        if len(frames) >= 500:
            with engine.begin() as connection:
                upsert(connection, Frame.__table__, frames, ['keyframe_id'],
                       ['frame_seconds', 'timestamp_ms', 'image_rel_path', 'image_storage_key',
                        'image_uri', 'map_n', 'embedding_index_0'])
                upsert(connection, FrameAnnotation.__table__, annotations, ['id'],
                       ['text_value', 'json_value', 'caption', 'ocr_texts', 'detected_objects',
                        'object_counts', 'detections', 'model_version', 'annotation_version'])
            counters['frames'] += len(frames)
            frames.clear(); annotations.clear()
    if frames:
        with engine.begin() as connection:
            upsert(connection, Frame.__table__, frames, ['keyframe_id'],
                   ['frame_seconds', 'timestamp_ms', 'image_rel_path', 'image_storage_key',
                    'image_uri', 'map_n', 'embedding_index_0'])
            upsert(connection, FrameAnnotation.__table__, annotations, ['id'],
                   ['text_value', 'json_value', 'caption', 'ocr_texts', 'detected_objects',
                    'object_counts', 'detections', 'model_version', 'annotation_version'])
        counters['frames'] += len(frames)
    map_dir.mkdir(parents=True, exist_ok=True)
    for video, rows in map_rows.items():
        with (map_dir / f'{video}.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=['n', 'frame_idx', 'pts_time'])
            writer.writeheader()
            writer.writerows({'n': n, 'frame_idx': frame, 'pts_time': seconds}
                             for n, (frame, seconds) in sorted(rows.items()))
    counters['map_files'] = len(map_rows)
    return dict(counters)


def es_import(archives: list[Path], url: str, index: str, s_roles: dict) -> dict:
    from elasticsearch import Elasticsearch, helpers

    client = Elasticsearch(url, request_timeout=60)
    if not client.ping():
        raise RuntimeError(f'Elasticsearch unavailable at {url}')
    from app.adapters.text_search.elasticsearch import ElasticsearchTextSearchClient
    ElasticsearchTextSearchClient(url)._ensure_index(index)
    seen: set[str] = set()

    def actions():
        for row in archive_rows(archives):
            frame_id = canonical_id(row)
            if frame_id in seen:
                continue
            seen.add(frame_id)
            shared = {
                'keyframe_id': frame_id, 'frame_id': frame_id,
                'video_id': row['video_id'], 'video_code': row['video_id'],
                'dataset_code': 'aic-2026', 'batch_id': row['batch_id'],
                'frame_idx': int(row['frame_idx']),
                'frame_seconds': frame_seconds(row),
                'timestamp_ms': round(frame_seconds(row) * 1000),
                'detected_objects': row.get('detected_objects') or [],
                'image_storage_key': image_key(row, s_roles),
            }
            caption = ' '.join(filter(None, [row.get('caption') or '',
                                               ' '.join(row.get('detected_objects') or [])]))
            if caption:
                yield {'_index': index, '_id': f'caption::{frame_id}', '_source': {
                    **shared, 'source_type': 'caption', 'kind': 'caption',
                    'caption': caption, 'text_value': row.get('text_value') or caption,
                }}
            ocr = row.get('ocr_texts') or []
            if ocr:
                yield {'_index': index, '_id': f'ocr::{frame_id}', '_source': {
                    **shared, 'source_type': 'ocr', 'kind': 'ocr',
                    'ocr_texts': ocr, 'text_value': ' '.join(ocr),
                }}

    ok, errors = helpers.bulk(client, actions(), chunk_size=500, raise_on_error=False)
    client.indices.refresh(index=index)
    return {'documents': ok, 'errors': len(errors), 'first_errors': errors[:2]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archives', nargs='*', type=Path,
                        default=sorted(REPO_ROOT.glob('Object Detection VLM - Batch *.zip')))
    parser.add_argument('--dataset-code', default='aic-2026')
    parser.add_argument('--index', default='keyframe_annotations')
    parser.add_argument('--elasticsearch-url', default='', help='Host URL, e.g. http://localhost:9200.')
    parser.add_argument('--targets', choices=['pg', 'es', 'all'], default='all')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--video-only', action='store_true',
                        help='Refresh video URIs and metadata without reimporting frames.')
    args = parser.parse_args()
    settings = get_settings()
    stats, s_roles = prepare(args.archives)
    report = {'videos': len(stats), 'frames': sum(len(v['frames']) for v in stats.values()),
              'batches': sorted({v['batch'] for v in stats.values()})}
    print(json.dumps({'prepared': report}), flush=True)
    if args.dry_run:
        return
    if args.targets in {'pg', 'all'}:
        engine = create_engine(settings.database_url, pool_pre_ping=True,
                               connect_args=database_connect_args(settings.database_url))
        result = pg_import(engine, args.archives, stats, s_roles, settings.gcs_bucket,
                           args.dataset_code, REPO_ROOT / 'data' / 'map-keyframes',
                           video_only=args.video_only)
        print(json.dumps({'postgresql': result}), flush=True)
    if args.targets in {'es', 'all'}:
        result = es_import(args.archives, args.elasticsearch_url or settings.elasticsearch_url,
                           args.index, s_roles)
        print(json.dumps({'elasticsearch': result}), flush=True)


if __name__ == '__main__':
    main()

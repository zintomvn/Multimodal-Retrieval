# M2 Ingestion Runbook (Demo -> Supabase / Elasticsearch / Zilliz)

Tài liệu này chốt cách chạy Module 2 sau khi M1 schema đã sẵn sàng.

## 1. Mục tiêu M2

- Nạp metadata vào PostgreSQL (`datasets/videos/shots/keyframes/frame_annotations/events/event_keyframes`).
- Upload ảnh keyframe vào storage theo key format `keyframes/{video_id}/{filename}`.
- Nạp vector vào Milvus/Zilliz:
  - `keyframe_embeddings` từ `demo/features/vit-.../*.npy`.
  - `event_embeddings` từ `demo/Event Embedding/event_embeddings.npy`.
- Nạp text documents vào Elasticsearch index `keyframe_annotations`.
- Có report reconcile + failed rows + idempotency.

## 2. API ingest (module ready)

Endpoint:

- `POST /api/ingest/jobs`

Payload mẫu:

```json
{
  "mode": "demo",
  "dataset_code": "l30-demo",
  "dataset_name": "aic-2026-l30-demo",
  "dataset_version": "v1",
  "dataset_root": "/Users/tawannt/Study/Github/Multimodal-Retrieval/demo",
  "targets": ["pg", "media", "milvus", "es"],
  "dry_run": false
}
```

`targets` cho phép chạy từng phần: `pg`, `media`, `milvus`, `es`.

## 3. CLI scripts theo từng target

Từ `apps/backend/`:

```bash
python scripts/import_pg.py --dataset-root ../../demo
python scripts/import_media.py --dataset-root ../../demo
python scripts/import_milvus.py --dataset-root ../../demo
python scripts/import_es.py --dataset-root ../../demo
python scripts/import_all.py --dataset-root ../../demo
```

## 4. Test gate M2

Chạy test module:

```bash
pytest tests/test_ingestion_pipeline.py -q
```

Gate cần pass:

- PG count parity từ fixture ingest.
- ES docs count = số dòng `annotations.jsonl` đã map thành công.
- Milvus vectors count cho keyframe/event đúng theo mapping hợp lệ.
- Chạy ingest lần 2 không tạo duplicate.

## 5. Verify nhanh trên DB thật

```sql
select count(*) from videos;
select count(*) from shots;
select count(*) from keyframes;
select count(*) from frame_annotations;
select count(*) from events;
select count(*) from event_keyframes;
```

Kiểm tra orphan:

```sql
select count(*) from keyframes k
left join shots s on s.shot_id = k.shot_id
where k.shot_id is not null and s.shot_id is null;

select count(*) from frame_annotations a
left join keyframes k on k.keyframe_id = a.frame_id
where k.keyframe_id is null;
```

## 6. Lưu ý reconcile

- `demo/frames` có thể thiếu ảnh so với metadata embedding.
- Report `reconcile.missing_media_count` và `failed_rows` là dữ liệu chuẩn để debug import.
- Mapping bắt buộc tuân theo quy tắc `row i = n - 1` từ `map-keyframes`.

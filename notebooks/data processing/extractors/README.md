# Kaggle Frame Vector Embedding Extractor

Folder này chứa notebook Kaggle để đọc keyframe đã trích xuất từ Google Cloud Storage,
chạy OpenCLIP image embedding, và xuất artifact để nạp PostgreSQL/Supabase cùng Zilliz/Milvus.

Notebook chính:

```text
fe-vector-embedding-v1.ipynb
```

Báo cáo thiết kế:

```text
fe-vector-embedding-v1-report.md
```

## Input GCS

Notebook bám theo format của `scripts/loaders/README.MD`.

Keyframe images:

```text
gs://<bucket>/processed/keyframes/
  dataset=ai_challenge_2025/
  batch=<batch_id>/
  profile=autoshot_v1/
  video_id=<video_id>/
    shot_0000_first_f000000.jpg
    shot_0000_middle_f000037.jpg
    shot_0000_last_f000074.jpg
    frames_manifest.jsonl
```

Shot manifest:

```text
gs://<bucket>/processed/keyframes_manifests/
  dataset=ai_challenge_2025/
  batch=<batch_id>/
  profile=autoshot_v1/
  run_id=<frame_extraction_run_id>/
    shot_segments.csv
    _SUCCESS
```

Nếu không khai báo `KEYFRAME_RUN_ID_BY_BATCH` hoặc `SHOT_SEGMENTS_URI_BY_BATCH`, notebook có thể tự tìm
`shot_segments.csv` mới nhất có `_SUCCESS` khi `AUTO_DISCOVER_SHOT_SEGMENTS = True`.

## Kaggle Secrets

Tạo secrets trong Kaggle Notebook:

| Secret | Nội dung |
| --- | --- |
| `GCS_BUCKET` | Tên bucket, ví dụ `aic_ai_2026` |
| `GCS_CREDENTIALS_JSON` | Full JSON service account có quyền đọc keyframe và ghi artifact |

Notebook cũng hỗ trợ tên cũ `GCS_SERVICE_ACCOUNT_JSON` để tương thích với notebook extractor trước đó.

## Config quan trọng

Sửa trong cell `Config`:

| Tham số | Ý nghĩa |
| --- | --- |
| `BATCHES` | Batch mặc định cho dry run, ví dụ `"L21"` hoặc `"all"` |
| `KEYFRAME_RUN_ID_BY_BATCH` | Map batch sang run id của bước video-to-frame |
| `SHOT_SEGMENTS_URI_BY_BATCH` | Map batch sang URI `shot_segments.csv` nếu muốn chỉ rõ |
| `PIPELINE_BATCH_SIZE` | Số frame download và xử lý trong một outer batch |
| `EMBED_BATCH_SIZE` | Batch size chạy OpenCLIP trên GPU |
| `DOWNLOAD_WORKERS` | Số thread CPU tải ảnh từ GCS |
| `IMAGE_DECODE_WORKERS` | Số worker DataLoader decode ảnh |
| `UPLOAD_WORKERS` | Số thread upload artifact lên GCS |
| `BUILD_EVENTS` | Bật/tắt gom event theo logic từ `event-embeddings.ipynb` |
| `WRITE_ZILLIZ_JSONL` | Ghi JSONL payload cho Zilliz/Milvus |

Default model bám backend:

```text
OpenCLIP ViT-B-32 / laion2b_s34b_b79k
FEATURE_DIR_NAME = vit-ViT-B-32-laion2b_s34b_b79k
Vector dim = 512
L2 normalize = True
Metric khuyến nghị = COSINE
```

## Cách chạy

1. Chạy `Install Dependencies`.
2. Sửa `Config`.
3. Chạy `Dry Run`.
4. Chạy `Smoke Test`.
5. Chạy `Demo 1 Batch`.
6. Nếu ổn, sửa:

```python
CONFIRM_FULL_RUN = "RUN_FULL_DATASET"
FULL_BATCHES = "all"
FULL_MAX_FRAMES = None
FULL_MAX_VIDEOS = None
```

7. Chạy `Full Run`.

## Output local

Mỗi run tạo:

```text
/kaggle/working/feature_extraction_runs/<run_id>/
  artifacts/
    processing_manifest.jsonl
    input_sources.json
    shot_segments.csv
    batch_metrics.csv
    errors.jsonl
    summary.json
    report.md
  features/
    vit-ViT-B-32-laion2b_s34b_b79k/<video_id>.npy
    map-keyframes/<video_id>.csv
    events/<video_id>.npy
    map-event/<video_id>.csv
    event_summary.csv
  postgres/
    datasets.csv
    videos.csv
    shots.csv
    keyframes.csv
    events.csv
    event_keyframes.csv
    model_registry.csv
    index_builds.csv
  zilliz/
    keyframe_embeddings/keyframe_embeddings-part-*.jsonl
    event_embeddings/event_embeddings-part-*.jsonl
  per_video_summary.csv
  run.log
```

## Output GCS

Khi `UPLOAD_ARTIFACTS_TO_GCS = True`, notebook upload cùng cấu trúc lên:

```text
gs://<bucket>/processed/features/fe-vector-embedding-v1/
  dataset=ai_challenge_2025/
  profile=autoshot_v1/
  run_id=<run_id>/
```

## Mapping PostgreSQL/Supabase

| File | Bảng |
| --- | --- |
| `postgres/datasets.csv` | `datasets` |
| `postgres/videos.csv` | `videos` |
| `postgres/shots.csv` | `shots` |
| `postgres/keyframes.csv` | `keyframes` |
| `postgres/events.csv` | `events` |
| `postgres/event_keyframes.csv` | `event_keyframes` |
| `postgres/model_registry.csv` | `model_registry` |
| `postgres/index_builds.csv` | `index_builds` |

Vector không được lưu vào `frame_annotations` mặc định. Notebook xuất vector sang Zilliz/Milvus để giữ Postgres nhẹ.

## Mapping Zilliz/Milvus

| Folder | Collection | Vector field | Primary id |
| --- | --- | --- | --- |
| `zilliz/keyframe_embeddings/*.jsonl` | `keyframe_embeddings` | `vector` | `keyframe_id` |
| `zilliz/event_embeddings/*.jsonl` | `event_embeddings` | `vector` | `event_id` |

Metadata đi kèm: `dataset_id`, `dataset_code`, `video_id`, `frame_idx`, `timestamp_ms`,
`model_version`, `profile_version`, `image_uri`, `image_storage_key`.

## Progress và tốc độ

Notebook log mỗi processing batch:

```text
[batch 12/480 2.50%] video_id=L21_V001 frames=512 processed=6144/245760 download_ms=... inference_ms=... zilliz_ms=... total_ms=... speed=... fps
```

Và log từng file upload artifact:

```text
[upload 10/180 5.56%] gs://... bytes=... duration_ms=...
```

Xem chi tiết trong:

```text
artifacts/batch_metrics.csv
artifacts/summary.json
artifacts/report.md
run.log
```

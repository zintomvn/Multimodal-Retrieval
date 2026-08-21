# Backend Scripts

Thư mục này chứa các script hỗ trợ việc nạp dữ liệu (ingestion) vào hệ thống (PostgreSQL, Elasticsearch, Milvus, Supabase Storage/MinIO) thông qua các Pipeline đã được định nghĩa trong `app/modules/ingest`.

## Danh sách các script

- `import_all.py`: Nạp toàn bộ dữ liệu (metadata, vectors, frames, object mapping) vào tất cả các storage engines cùng một lúc.
- `import_pg.py`: Chỉ nạp metadata vào PostgreSQL.
- `import_es.py`: Chỉ nạp text metadata (annotations, OCR, objects) vào Elasticsearch.
- `import_milvus.py`: Chỉ nạp vectors vào Milvus.
- `import_media.py`: Chỉ upload ảnh frames vào Object Storage (Supabase Storage/MinIO).
- `import_gcs_frame_metadata.py`: Tạo metadata dataset/video/shot/keyframe trong PostgreSQL từ `shot_segments.csv` trên Google Cloud Storage; không tải frame về local.
- `serve_openclip_embeddings.py`: Chạy local OpenCLIP embedding service theo chuẩn OpenAI-compatible (`/v1/models`, `/v1/embeddings`).
- `check_embedding_endpoint.py`: Verify endpoint embedding (model id, vector dimension, L2 normalization).

## Hướng dẫn sử dụng `import_all.py`

Đây là cách bạn có thể import tất cả dữ liệu (toàn bộ các mục tiêu DB) từ tập dữ liệu mẫu `demo` và lưu report kết quả vào một file `.json`:

```bash
# Di chuyển vào thư mục backend
cd apps/backend

# Chạy script thông qua môi trường ảo (virtual environment) ở thư mục gốc
../../venv/bin/python scripts/import_all.py \
    --dataset-root ../../demo \
    > /tmp/import_report.json
```

### Các cờ (Flags) hỗ trợ cho `import_all.py`

- `--dataset-root`: Đường dẫn tới thư mục chứa dữ liệu L30/Demo (mặc định là `demo`).
- `--dataset-code`: Mã của dataset (mặc định là `l30-demo`).
- `--dataset-name`: Tên hiển thị của dataset (mặc định là `aic-2026-l30-demo`).
- `--dataset-version`: Phiên bản của dataset (mặc định là `v1`).
- `--dry-run`: Chạy thử nhưng không thực sự ghi vào DB (thường dùng để test/verify trước).

_Lưu ý: Bạn cần chắc chắn các dịch vụ database trong `docker-compose` đã được chạy trước khi gọi script._

## Hướng dẫn chạy `import_gcs_frame_metadata.py`

Script này dùng khi frame đã nằm trên Google Cloud Storage và PostgreSQL/Supabase chưa có metadata frame. Script chỉ đọc manifest GCS và upsert các bảng `datasets`, `videos`, `shots`, `keyframes`; không dùng `demo/frames`, `DATA_ROOT`, hay ảnh local.

Sau khi import xong, web đọc frame qua backend:

```text
/api/media/frames/{keyframe_id}/thumbnail
```

Backend sẽ lấy `keyframes.image_storage_key`, tạo signed URL từ GCS, rồi redirect `307` sang ảnh trong private bucket.

### Env cần có

Đặt các biến này trong `.env` ở root repository:

```env
DATABASE_URL=postgresql+psycopg://...
STORAGE_PROVIDER=gcs
GCS_BUCKET=aic_ai_2026
GCS_CREDENTIALS_FILE=apps/secrets/<service-account>.json
MILVUS_URI=https://...
MILVUS_TOKEN=...
```

Ghi chú:

- `DATABASE_URL` dùng để ghi metadata vào Supabase PostgreSQL.
- `GCS_BUCKET` và `GCS_CREDENTIALS_FILE` dùng để đọc `_SUCCESS`, `shot_segments.csv`, và verify object frame.
- `MILVUS_URI` và `MILVUS_TOKEN` chưa cần cho script metadata này; chúng được dùng ở bước ingest embedding/vector sau.

### Format GCS đầu vào

Script tìm manifest theo format:

```text
gs://<bucket>/processed/keyframes_manifests/
  dataset=ai_challenge_2025/
  batch=<batch_id>/
  profile=autoshot_v1/
  run_id=<run_id>/
    shot_segments.csv
    _SUCCESS
```

Nếu không truyền `--run-id`, script tự discover run mới nhất có đủ `_SUCCESS` và `shot_segments.csv` cho từng batch.

### Dry run một batch

Chạy từ root repository:

```powershell
python apps/backend/scripts/import_gcs_frame_metadata.py `
  --dataset-id ai_challenge_2025 `
  --dataset-code aic-2026 `
  --dataset-name aic-ai-challenge-2025 `
  --dataset-version v1 `
  --batches L21 `
  --profile-version autoshot_v1 `
  --max-videos 10 `
  --dry-run
```

Kết quả mong đợi:

- In JSON report gồm `batches_loaded`, `prepared_rows`, `failures`.
- Không ghi gì vào PostgreSQL.
- Nếu batch thiếu `_SUCCESS` hoặc `shot_segments.csv`, report sẽ nói rõ batch nào bị thiếu.

### Import thật một batch

```powershell
python apps/backend/scripts/import_gcs_frame_metadata.py `
  --dataset-id ai_challenge_2025 `
  --dataset-code aic-2026 `
  --dataset-name aic-ai-challenge-2025 `
  --dataset-version v1 `
  --batches L21 `
  --profile-version autoshot_v1 `
  --auto-discover-run `
  --max-videos 10 `
  --verify-objects
```

`--verify-objects` chỉ gọi metadata check trên GCS object, không download ảnh. Nếu object thiếu, row `keyframes` vẫn được import nhưng `is_media_present=false`.
`--max-videos` chỉ giới hạn số video cho smoke test; bỏ cờ này khi muốn import toàn batch.

Với L21 hiện có cả demo run và full run. Nếu cần chạy đúng full run 10 video, thay `--auto-discover-run` bằng:

```powershell
--run-id full_autoshot_l21_20260709_193632 --max-videos 10
```

### Import full L21-L30

```powershell
python apps/backend/scripts/import_gcs_frame_metadata.py `
  --dataset-id ai_challenge_2025 `
  --dataset-code aic-2026 `
  --dataset-name aic-ai-challenge-2025 `
  --dataset-version v1 `
  --batches L21,L22,L23,L24,L25,L26,L27,L28,L29,L30 `
  --profile-version autoshot_v1 `
  --auto-discover-run `
  --verify-objects `
  --verify-workers 32
```

Tăng/giảm `--verify-workers` tùy network và quota GCS. Nếu bucket public và muốn lưu URL public vào DB, thêm `--public-urls`; với private bucket thì không cần.

### Kiểm tra sau import

Mở PostgreSQL Supabase bằng `psql`:

```powershell
$env:DATABASE_URL = (Select-String '^DATABASE_URL=' .env).Line.Split('=',2)[1].Trim('"').Trim("'")
psql "$env:DATABASE_URL"
```

Chạy các câu SQL:

```sql
select count(*) from datasets;
select count(*) from videos;
select count(*) from shots;
select count(*) from keyframes;

select video_id, num_keyframes
from videos
order by video_id
limit 10;

select keyframe_id, image_storage_key, image_uri, is_media_present
from keyframes
order by keyframe_id
limit 10;
```

Kiểm tra backend media endpoint:

```powershell
Invoke-WebRequest `
  -MaximumRedirection 0 `
  http://127.0.0.1:8000/api/media/frames/<keyframe_id>/thumbnail
```

Kết quả đúng với private GCS bucket là HTTP `307` redirect sang signed URL.

## Chạy local embedding service (ViT-H-14-quickgelu + dfn5b)

Đây là cách khuyến nghị để backend retrieval gọi model thật qua `openai_compatible`:

```bash
cd apps/backend
../../venv/bin/pip install -r requirements-embedding-service.txt

# Backend API in `be.cmd` uses 8010, so the embedding endpoint can use 8001.
../../venv/bin/python scripts/serve_openclip_embeddings.py --port 8001 --device auto
```

Best practice:

- Keep `embedders.clip_vith14_quickgelu_dfn5b_v2.base_url` aligned with the service port. This repo currently uses `http://localhost:8001/v1`.
- Cấu hình model/pretrained/device/batch trong `configs/model_registry.yaml` (entry embedder đang `enabled: true`).
- Script tự đọc `.env` (fallback `.env.example`) để lấy `MODEL_REGISTRY_PATH` và `EMBED_HOST/EMBED_PORT`.
- Nếu muốn dùng file env khác: `--env-file /path/to/file.env`.
- Nếu muốn dùng registry khác: `--model-registry /path/to/model_registry.yaml`.

Kiểm tra endpoint trước khi bật retrieval backend:

```bash
cd apps/backend
../../venv/bin/python scripts/check_embedding_endpoint.py \
  --base-url http://127.0.0.1:8001/v1 \
  --model ViT-H-14-quickgelu-dfn5b \
  --expected-dim 1024
```

Kết quả mong đợi:

- `"ok": true`
- `"dim": 1024`
- `"norm_close_to_1": true`

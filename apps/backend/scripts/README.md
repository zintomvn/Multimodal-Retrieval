# Backend Scripts

Thư mục này chứa các script hỗ trợ việc nạp dữ liệu (ingestion) vào hệ thống (PostgreSQL, Elasticsearch, Milvus, Supabase Storage/MinIO) thông qua các Pipeline đã được định nghĩa trong `app/modules/ingest`.

## Danh sách các script

- `import_all.py`: Nạp toàn bộ dữ liệu (metadata, vectors, frames, object mapping) vào tất cả các storage engines cùng một lúc.
- `import_pg.py`: Chỉ nạp metadata vào PostgreSQL.
- `import_es.py`: Chỉ nạp text metadata (annotations, OCR, objects) vào Elasticsearch.
- `import_milvus.py`: Chỉ nạp vectors vào Milvus.
- `import_media.py`: Chỉ upload ảnh frames vào Object Storage (Supabase Storage/MinIO).
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

*Lưu ý: Bạn cần chắc chắn các dịch vụ database trong `docker-compose` đã được chạy trước khi gọi script.*

## Chạy local embedding service (ViT-B-32 + laion2b_s34b_b79k)

Đây là cách khuyến nghị để backend retrieval gọi model thật qua `openai_compatible`:

```bash
cd apps/backend
../../venv/bin/pip install -r requirements-embedding-service.txt

# Start service at http://127.0.0.1:8001/v1
../../venv/bin/python scripts/serve_openclip_embeddings.py
```

Best practice:
- Cấu hình model/pretrained/device/batch trong `configs/model_registry.yaml` (entry embedder đang `enabled: true`).
- Script tự đọc `.env` (fallback `.env.example`) để lấy `MODEL_REGISTRY_PATH` và `EMBED_HOST/EMBED_PORT`.
- Nếu muốn dùng file env khác: `--env-file /path/to/file.env`.
- Nếu muốn dùng registry khác: `--model-registry /path/to/model_registry.yaml`.

Kiểm tra endpoint trước khi bật retrieval backend:

```bash
cd apps/backend
../../venv/bin/python scripts/check_embedding_endpoint.py \
  --base-url http://127.0.0.1:8001/v1 \
  --model ViT-B-32-laion2b_s34b_b79k \
  --expected-dim 512
```

Kết quả mong đợi:
- `"ok": true`
- `"dim": 512`
- `"norm_close_to_1": true`

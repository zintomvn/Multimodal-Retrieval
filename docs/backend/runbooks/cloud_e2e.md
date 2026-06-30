# Backend Cloud E2E Runbook (Supabase + Zilliz + Elasticsearch + Redis)

Tài liệu này là hướng dẫn đầy đủ để một thành viên mới có thể:
- cấu hình môi trường,
- import dữ liệu `demo/` lên Supabase + Milvus + Elasticsearch,
- chạy backend FastAPI,
- kiểm tra retrieval E2E với stack thật.

Mặc định hướng dẫn cho macOS/Linux (bash/zsh).

## 1. Kiến trúc chạy khuyến nghị

- PostgreSQL: Supabase (cloud)
- Vector DB: Zilliz/Milvus (cloud)
- Text search: Elasticsearch (local Docker)
- Cache/runtime: Redis (local Docker)
- Embedding query: OpenCLIP service local `http://127.0.0.1:8001/v1`
- Backend API: FastAPI local `http://127.0.0.1:8000`

Ghi chú:
- Redis hiện chủ yếu phục vụ runtime/cache. Import dữ liệu không ghi record vào Redis.
- Nếu bạn dùng Elasticsearch cloud thì chỉ cần đổi `ELASTICSEARCH_URL`.

## 2. Yêu cầu trước khi chạy

- Python 3.11+ (đã có virtualenv ở repo root: `venv`).
- Docker Desktop (để chạy Redis + Elasticsearch local).
- Đã có:
  - Supabase connection string hợp lệ.
  - Zilliz endpoint + token hợp lệ.
- Dữ liệu demo tồn tại tại `demo/`.

## 3. Chuẩn bị `.env`

Từ root repo:

```bash
cp .env.example .env
```

Mở `.env` và chỉnh các biến bắt buộc:

```bash
APP_ENV=cloud
MOCK_MODE=false

DATABASE_URL=postgresql+psycopg://postgres.<project-ref>:<password>@aws-<region>.pooler.supabase.com:6543/postgres?sslmode=require
MILVUS_URI=https://<your-zilliz-endpoint>
MILVUS_TOKEN=<your-zilliz-token>
ELASTICSEARCH_URL=http://localhost:9200
REDIS_URL=redis://localhost:6379/0

DATA_ROOT=/absolute/path/to/Multimodal-Retrieval/demo
MODEL_REGISTRY_PATH=/absolute/path/to/Multimodal-Retrieval/configs/model_registry.yaml
RETRIEVAL_PROFILES_PATH=/absolute/path/to/Multimodal-Retrieval/configs/retrieval_profiles.yaml
```

Biến cho local OpenCLIP embedding service:

```bash
EMBED_HOST=127.0.0.1
EMBED_PORT=8001
```

Cấu hình model/pretrained/device/batch lấy từ `configs/model_registry.yaml`
(entry embedder đang `enabled: true`):

```yaml
embedders:
  openai_embedding:
    provider: openai_compatible
    model: ViT-B-32-laion2b_s34b_b79k
    openclip_model: ViT-B-32
    openclip_pretrained: laion2b_s34b_b79k
    openclip_device: cpu
    openclip_max_batch: 32
    l2_normalize: true
    enabled: true
```

Lưu ý:
- Chỉ để một giá trị `ELASTICSEARCH_URL` duy nhất.
- Không commit `.env` lên Git.

## 4. Start Redis + Elasticsearch

Từ root repo:

```bash
docker compose up -d redis elasticsearch
```

Kiểm tra:

```bash
docker compose ps
curl -s http://localhost:9200 | head
redis-cli -h 127.0.0.1 -p 6379 ping
```

Kết quả mong đợi:
- Elasticsearch trả JSON cluster info.
- Redis trả `PONG`.

## 5. Cài dependencies backend

```bash
cd apps/backend
../../venv/bin/pip install -r requirements.txt
../../venv/bin/pip install -r requirements-embedding-service.txt
```

## 6. Start embedding service (OpenCLIP)

Từ `apps/backend`:

```bash
../../venv/bin/python scripts/serve_openclip_embeddings.py
```

Script tự đọc `.env` ở root repo (fallback `.env.example`), nên không cần `export` thủ công.

Kiểm tra endpoint:

```bash
../../venv/bin/python scripts/check_embedding_endpoint.py \
  --base-url http://127.0.0.1:8001/v1 \
  --model ViT-B-32-laion2b_s34b_b79k \
  --expected-dim 512
```

Kết quả mong đợi:
- `"ok": true`
- `"dim": 512`
- `"norm_close_to_1": true`

## 7. Import dữ liệu demo lên PG + media + Milvus + ES

Mở terminal mới, từ `apps/backend`:

### 7.1 Dry-run trước

```bash
../../venv/bin/python scripts/import_all.py \
  --dataset-root ../../demo \
  --dry-run > ./tmp/import_report_dryrun.json
```

### 7.2 Import thật

```bash
../../venv/bin/python scripts/import_all.py \
  --dataset-root ../../demo > /tmp/import_report.json
```

Nếu cần import từng phần:

```bash
../../venv/bin/python scripts/import_pg.py --dataset-root ../../demo
../../venv/bin/python scripts/import_media.py --dataset-root ../../demo
../../venv/bin/python scripts/import_milvus.py --dataset-root ../../demo
../../venv/bin/python scripts/import_es.py --dataset-root ../../demo
```

## 8. Verify sau import

### 8.1 Supabase (SQL Editor)

```sql
select count(*) from videos;
select count(*) from shots;
select count(*) from keyframes;
select count(*) from frame_annotations;
select count(*) from events;
select count(*) from event_keyframes;
```

Kiểm tra duplicate rank (sau khi đã chạy query retrieval):

```sql
select rank, count(*)
from retrieval_results
where query_run_id = '<query_run_id>'
group by rank
having count(*) > 1;
```

### 8.2 Elasticsearch

```bash
curl -s "http://localhost:9200/keyframe_annotations/_count" | jq
```

### 8.3 Milvus/Zilliz

Có thể verify nhanh qua Zilliz console:
- collection `keyframe_embeddings`
- collection `event_embeddings`

## 9. Start backend API

Từ `apps/backend`:

```bash
../../venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Kiểm tra:

```bash
curl -s http://127.0.0.1:8000/healthz
```

Kết quả mong đợi:
- `status: ok`
- `mock_mode: false`

Swagger:
- `http://localhost:8000/docs`

## 10. Smoke test retrieval (strict hybrid)

Ví dụ KIS:

```bash
curl -s -X POST "http://127.0.0.1:8000/api/retrieval/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query_type": "KIS",
    "query_name": "e2e-kis-cli",
    "query_text": "nguoi ao do",
    "top_k": 5,
    "profile": "competition_default",
    "options": {
      "use_query_expansion": false,
      "strict_hybrid": true
    }
  }' | jq
```

Pass tối thiểu:
- HTTP `200`.
- `results` không rỗng.
- Có `score_breakdown.semantic_score`, `text_score`, `quality_score`, `final_score`.

## 11. Redis trong workflow này dùng để làm gì?

- Redis là runtime dependency cho cache/lock/job-state.
- Import pipeline dữ liệu (`import_all.py`) không lưu dataset records vào Redis.
- Redis vẫn nên bật để môi trường vận hành backend đầy đủ.

## 12. Troubleshooting nhanh

- `healthz` trả `mock_mode: true`:
  - kiểm tra `.env` có `MOCK_MODE=false`.
  - restart backend process.

- `strict_hybrid` báo backend retrieval failed:
  - kiểm tra OpenCLIP service `:8001`.
  - kiểm tra Milvus/Zilliz token + endpoint.
  - kiểm tra Elasticsearch `http://localhost:9200`.

- Supabase host không resolve:
  - dùng đúng host/pooler từ dashboard Supabase.
  - thử pooler port `6543` thay vì direct host khi mạng chặn direct DB.

- Kết quả trả về `mock thumbnail`:
  - kiểm tra `DATA_ROOT` trỏ đúng thư mục `demo`.
  - kiểm tra file frame tồn tại trong `demo/frames/<video_id>/`.

## 13. Lệnh mẫu chạy full (copy/paste)

Terminal 1 (infra local):

```bash
cd /absolute/path/to/Multimodal-Retrieval
docker compose up -d redis elasticsearch
```

Terminal 2 (embedding service):

```bash
cd /absolute/path/to/Multimodal-Retrieval/apps/backend
../../venv/bin/python scripts/serve_openclip_embeddings.py
```

Terminal 3 (import + backend):

```bash
cd /absolute/path/to/Multimodal-Retrieval/apps/backend
../../venv/bin/python scripts/import_all.py --dataset-root ../../demo > /tmp/import_report.json
../../venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

# Backend Service

FastAPI backend cho Multimodal Retrieval Assistant. Service này chịu trách nhiệm quản lý dataset metadata, model registry, ingest/index jobs, retrieval orchestration, temporal alignment, QA answer generation và submission export.

## 1. Vai trò trong hệ thống

Backend là lớp điều phối trung tâm giữa frontend, database, vector database, text search, object storage và model runtime.

```text
Search Web
   |
   | REST / JSON
   v
FastAPI Backend
   |-- PostgreSQL: dataset, videos, frames, runs, submissions
   |-- Milvus: frame/event vector embeddings
   |-- Elasticsearch: OCR/ASR/caption/object text search
   |-- Redis: cache, locks, job state
   |-- MinIO: videos, keyframes, thumbnails, artifacts
   `-- Model Runtime: mock/local/HF/OpenAI-compatible adapters
```

Bản hiện tại chạy được bằng mock data/mock model. Các adapter thật được đặt sẵn để sau này gắn PE/OpenCLIP, BEiT-3, PaddleOCR, WhisperX, VLM hoặc LLM.

## 2. Cấu trúc thư mục

```text
apps/backend/
├── app/
│   ├── main.py                     # FastAPI app, routers, startup bootstrap
│   ├── core/                       # Settings, logging/observability hooks
│   ├── db/                         # SQLAlchemy models, session, seed mock data
│   ├── adapters/
│   │   ├── model_runtime/          # Model interfaces + mock adapter
│   │   ├── vector_db/              # Milvus + in-memory vector clients
│   │   └── text_search/            # Elasticsearch + in-memory text clients
│   ├── modules/
│   │   ├── datasets/               # Dataset APIs
│   │   ├── ingest/                 # Pipeline stages and ingest job API
│   │   ├── jobs/                   # Job status API
│   │   ├── media/                  # Frame context and thumbnail APIs
│   │   ├── models/                 # Model registry API
│   │   ├── retrieval/              # KIS/QA/TRAKE retrieval orchestration
│   │   ├── submissions/            # CSV/ZIP validation and export
│   │   └── temporal/               # Adaptive Temporal Search
│   └── workers/                    # Worker entry points
├── Dockerfile
└── requirements.txt
```

## 3. Luồng nghiệp vụ chính

### 3.1 Startup

1. FastAPI khởi động trong `app.main`.
2. `init_db()` tạo schema nếu chưa có.
3. `seed_mock_data()` tạo dataset `mock-aic-2026`, video, frames, annotations và index build mock.
4. Routers được mount dưới `/api/*`.

### 3.2 KIS / QA retrieval

1. Client gọi `POST /api/retrieval/search` hoặc `POST /api/retrieval/qa`.
2. Backend tạo `QueryRun` trong PostgreSQL.
3. Query được normalize:
   - tách token,
   - sinh query variants nếu bật multiperspective expansion,
   - nhận diện temporal events nếu có.
4. Retrieval service chạy flow thật:
   - `embed_text(query)` từ model runtime,
   - Milvus ANN trên collection `keyframe_embeddings`,
   - Elasticsearch multi-match trên index `keyframe_annotations`,
   - gộp điểm với quality score và xếp hạng final.
5. Với QA, `VisualQaModel` sinh answer ngắn từ evidence text và answer hint.
6. Kết quả được lưu vào `retrieval_results`, trả về frontend kèm `score_breakdown`.

`score_breakdown` chuẩn M3 cho `/api/retrieval/search` và `/api/retrieval/qa`:

```json
{
  "semantic_score": 0.9342,
  "text_score": 0.7125,
  "quality_score": 0.95,
  "final_score": 0.8266
}
```

M4 fusion + filter hỗ trợ thêm trong `SearchRequest.options`:

```json
{
  "video_codes": ["L30_V001"],
  "time_range_start_seconds": 0,
  "time_range_end_seconds": 120,
  "objects": ["person", "motorbike"],
  "scene": "nguoi ao do",
  "debug_filters": true
}
```

- Fusion profile: weighted sum + RRF (theo `configs/retrieval_profiles.yaml`).
- Mỗi result có `score_breakdown.filter_debug` để debug lý do match filter.

M5 hardening:
- QA answer luôn được normalize và giới hạn tối đa 100 ký tự.
- TRAKE response có ordering metadata ổn định trong `score_breakdown.ordering` và `sequence_frames[*].order_index`.

### 3.3 TRAKE retrieval

1. Client gọi `POST /api/retrieval/trake`.
2. Query được tách thành sub-events.
3. Mỗi sub-event lấy candidate frames độc lập.
4. `adaptive_temporal_search()` nhóm candidate theo video và dựng chuỗi frame hợp lệ:
   - cùng video,
   - đúng thứ tự thời gian,
   - khoảng cách frame không vượt ngưỡng,
   - cho phép partial match.
5. Trả về ranked sequence, mỗi sequence có `sequence_frames` để export CSV.

### 3.4 Submission export

1. Client tạo draft bằng `POST /api/submissions`.
2. Client thêm rows bằng `POST /api/submissions/{id}/items`.
3. Backend validate:
   - tối đa 100 dòng/query,
   - KIS/QA có đúng 1 frame,
   - QA answer <= 100 ký tự,
   - TRAKE có danh sách frame,
   - video code không có `.mp4`.
4. `POST /api/submissions/{id}/export` tạo:

```text
submission/
├── query-1-kis.csv
├── query-2-qa.csv
└── query-3-trake.csv
```

5. `GET /api/submissions/{id}/download` trả file ZIP.

## 4. Chạy bằng Docker

Từ root repository:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Backend chạy tại:

- API: `http://localhost:8000`
- Swagger docs: `http://localhost:8000/docs`
- Healthcheck: `http://localhost:8000/healthz`

Kiểm tra trạng thái:

```powershell
docker compose ps
Invoke-RestMethod http://localhost:8000/healthz
```

## 5. Chạy backend local không dùng Docker

Dùng SQLite để dev nhanh:

```powershell
cd apps\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:DATABASE_URL="sqlite:///./data/dev.db"
$env:MODEL_REGISTRY_PATH="../../configs/model_registry.yaml"
$env:RETRIEVAL_PROFILES_PATH="../../configs/retrieval_profiles.yaml"
$env:DATA_ROOT="../../data"
$env:MOCK_MODE="true"

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## 6. Environment variables

| Biến | Mặc định | Mô tả |
| --- | --- | --- |
| `APP_ENV` | `local` | Môi trường chạy. |
| `DATABASE_URL` | SQLite fallback | SQLAlchemy database URL. Docker dùng PostgreSQL. |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis cho cache/job state. |
| `MILVUS_HOST` / `MILVUS_PORT` | `localhost` / `19530` | Milvus endpoint. |
| `ELASTICSEARCH_URL` | `http://localhost:9200` | Elasticsearch endpoint. |
| `S3_ENDPOINT` | `http://localhost:9000` | MinIO/S3 endpoint. |
| `MODEL_REGISTRY_PATH` | `configs/model_registry.yaml` | File khai báo model. |
| `RETRIEVAL_PROFILES_PATH` | `configs/retrieval_profiles.yaml` | File trọng số retrieval. |
| `DATA_ROOT` | `./data` | Nơi ghi submissions/artifacts. |
| `MOCK_MODE` | `true` | Bật mock mode khi chưa có model thật. |
| `MOCK_EMBEDDING_DIM` | `512` | Số chiều vector cho mock embedder (đặt khớp dimension Milvus collection). |

## 7. API surface

| Method | Endpoint | Mục đích |
| --- | --- | --- |
| `GET` | `/healthz` | Liveness check. |
| `GET` | `/readyz` | Readiness check. |
| `GET` | `/api/datasets` | Liệt kê dataset. |
| `POST` | `/api/datasets` | Tạo dataset draft. |
| `GET` | `/api/models` | Xem model registry và enabled models. |
| `POST` | `/api/ingest/jobs` | Tạo ingest job (demo import pipeline thật hoặc mock). |
| `GET` | `/api/jobs/{job_id}` | Xem trạng thái job. |
| `POST` | `/api/retrieval/search` | KIS/freeform search. |
| `POST` | `/api/retrieval/qa` | QA retrieval + answer. |
| `POST` | `/api/retrieval/trake` | Temporal retrieval. |
| `GET` | `/api/retrieval/runs/{run_id}` | Lấy lại result của run. |
| `GET` | `/api/media/frames/{frame_id}/context` | Lấy frame trước/sau. |
| `GET` | `/api/media/frames/{frame_id}/thumbnail` | Thumbnail mock/S3-ready. |
| `POST` | `/api/submissions` | Tạo draft submission. |
| `POST` | `/api/submissions/{id}/items` | Ghi rows vào submission. |
| `POST` | `/api/submissions/{id}/validate` | Validate CSV rules. |
| `POST` | `/api/submissions/{id}/export` | Tạo ZIP. |
| `GET` | `/api/submissions/{id}/download` | Download ZIP. |

## 8. Gắn model thật

Quy trình chuẩn:

1. Copy checkpoint vào `models/`.
2. Sửa `configs/model_registry.yaml`.
3. Bật `enabled: true` cho model thật, tắt model mock tương ứng nếu cần.
4. Implement adapter trong `app/adapters/model_runtime/` nếu model chưa có adapter.
5. Chạy lại ingest/index.
6. Benchmark với `scripts/benchmark_retrieval.py`.

Tài liệu chi tiết: `docs/model_pipeline_guide.md`.

## 9. Development workflow

### 9.0 M2 ingestion pipeline (demo data)

Chạy ingest full:

```powershell
cd apps\backend
python scripts/import_all.py --dataset-root ..\..\demo
```

Chạy từng phần:

```powershell
python scripts/import_pg.py --dataset-root ..\..\demo
python scripts/import_media.py --dataset-root ..\..\demo
python scripts/import_milvus.py --dataset-root ..\..\demo
python scripts/import_es.py --dataset-root ..\..\demo
```

Test gate M2:

```powershell
pytest tests/test_ingestion_pipeline.py -q
```

Test docs với Supabase + Zilliz + Elasticsearch (không dùng mock vector/text):

```powershell
$env:MOCK_MODE="false"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Mở `http://localhost:8000/docs` và gọi `POST /api/retrieval/search`.

### 9.1 Smoke test nhanh trong container

```powershell
docker compose run --rm --no-deps `
  -e DATABASE_URL=sqlite:////tmp/multimodal_smoke.db `
  -e DATA_ROOT=/tmp/data `
  backend python -c "from app.db.bootstrap import init_db; init_db(); print('ok')"
```

### 9.2 Test syntax Python không ghi bytecode

```powershell
$env:PYTHONDONTWRITEBYTECODE="1"
python -c "import ast, pathlib; [ast.parse(p.read_text(encoding='utf-8')) for p in pathlib.Path('apps/backend/app').rglob('*.py')]; print('syntax ok')"
```

### 9.5 Test gate M3 (core retrieval)

```powershell
pytest tests/test_retrieval_pipeline.py -q
```

Nội dung gate:
- KIS trả về breakdown chuẩn `semantic_score/text_score/quality_score/final_score`.
- QA trả về answer hợp lệ.
- Persist `query_runs` + `retrieval_results` đúng thứ hạng và kiểm tra input validation.

### 9.6 Test gate M4 (fusion and filtering)

```powershell
pytest tests/test_fusion_filtering.py -q
```

Nội dung gate:
- Weighted profile và RRF profile cho thứ tự khác nhau theo fixture.
- Filter `video_codes/time_range/objects/scene` loại đúng kết quả ngoài phạm vi.
- `score_breakdown.filter_debug` có metadata debug filter.

### 9.7 Test gate M5 (qa-trake + submission)

```powershell
pytest tests/test_qa_trake_hardening.py -q
pytest tests/test_submission_hardening.py -q
```

Nội dung gate:
- QA answer được post-process <= 100 ký tự.
- TRAKE sequence có ordering metadata ổn định giữa các lần gọi.
- Submission validator trả report chi tiết lỗi theo dòng (`violations`).
- ZIP export đúng cấu trúc `submission/*.csv`, UTF-8, không header.

### 9.3 Build image

```powershell
docker compose build backend
```

### 9.4 Migration workflow (M1 db-storage)

```powershell
cd apps\backend
alembic upgrade head
alembic downgrade base
alembic upgrade head
```

Kiểm tra nhanh core schema:

```sql
select count(*) from datasets;
select count(*) from videos;
select count(*) from shots;
select count(*) from keyframes;
select count(*) from events;
```

## 10. Quy ước code

- Module API đặt trong `modules/<domain>/router.py`.
- Business logic đặt trong `modules/<domain>/service.py`.
- Schema request/response đặt trong `modules/<domain>/schemas.py`.
- External system phải đi qua `adapters/*`, không gọi trực tiếp trong router.
- Mỗi result phải có `score_breakdown` để debug ranking.
- Mọi thay đổi embedding/OCR/ASR/caption phải gắn model/index version.
- Không commit `.env`, model weights, raw dataset hoặc generated submissions.

## 11. Troubleshooting

| Vấn đề | Cách xử lý |
| --- | --- |
| `ModuleNotFoundError` khi chạy local | Kiểm tra đã activate venv và `pip install -r requirements.txt`. |
| Backend không connect PostgreSQL | Chạy `docker compose ps`, đảm bảo `postgres` healthy. |
| Search không có dataset | Gọi `GET /api/datasets`; nếu rỗng, restart backend để seed mock hoặc tạo dataset. |
| Export ZIP invalid | Gọi `/api/submissions/{id}/validate` để xem `errors`. |
| Model thật không load | Kiểm tra `checkpoint_uri`, `device`, dependency GPU và adapter init. |
| Milvus/Elasticsearch chưa dùng dữ liệu thật | Hiện scaffold rank bằng DB/mock; implement index worker rồi bật adapter thật. |

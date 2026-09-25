# Multimodal Retrieval Assistant

Trợ lý truy xuất multimedia. Hệ thống hỗ trợ ingest dữ liệu video/hình ảnh/âm thanh/văn bản, preprocessing metadata, tìm kiếm KIS/QA/TRAKE.

Backend hiện chạy với metadata trong PostgreSQL, vector embeddings trong Milvus/Zilliz, text index trong Elasticsearch và media trên object storage như GCS/S3. Luồng dữ liệu chính đi từ frame đã xử lý trên cloud sang PostgreSQL/Zilliz, không dùng dữ liệu mẫu local.

## 1. Mục Tiêu Hệ Thống

- **Textual KIS**: tìm đúng video và frame từ mô tả văn bản.
- **QA**: tìm frame liên quan và sinh câu trả lời ngắn để nộp.
- **TRAKE**: tìm chuỗi frame theo thứ tự sự kiện thời gian.
- **Human-in-the-loop**: người thi xem ranked results, kiểm tra context và chọn đáp án.
- **Submission builder**: xuất CSV/ZIP đúng cấu trúc `submission/`.
- **Model-ready**: có registry để bật/tắt embedding, VLM và LLM thật theo môi trường.

## 2. Kiến Trúc Tổng Quan

```text
apps/web
  React + TypeScript search workspace
        |
        | REST API
        v
apps/backend
  FastAPI modular backend
        |
        |-- PostgreSQL: metadata, runs, submissions, jobs
        |-- Milvus: vector embeddings
        |-- Elasticsearch: OCR/ASR/caption/object text index
        |-- MinIO: media artifacts
        |-- Redis: cache/job state
        `-- Model adapters: local/HF/OpenAI-compatible
```

## 3. Cấu Trúc Thư Mục

```text
Multimodal-Retrieval/
├── apps/
│   ├── backend/                 # FastAPI backend service
│   │   ├── app/
│   │   │   ├── adapters/         # Milvus, Elasticsearch, model runtime adapters
│   │   │   ├── core/             # Settings/config
│   │   │   ├── db/               # SQLAlchemy models, session, seed data
│   │   │   ├── modules/          # datasets, ingest, retrieval, submissions, jobs
│   │   │   └── workers/          # worker entry points
│   │   ├── Dockerfile
│   │   ├── README.md             # Backend-specific documentation
│   │   └── requirements.txt
│   └── web/                      # React + TypeScript frontend
│       ├── src/
│       ├── Dockerfile
│       ├── README.md             # Frontend-specific documentation
│       └── package.json
├── configs/
│   ├── dataset_manifest.real.example.yaml  # Manifest mẫu cho dataset thật
│   ├── model_registry.yaml       # Bật/tắt model và khai báo checkpoint
│   └── retrieval_profiles.yaml   # Trọng số ranking/retrieval
├── data/                         # Báo cáo xử lý, artifacts nhỏ và output tạm
├── docs/
│   ├── blueprint/                # Proposal + technical design
│   ├── tasks/                    # Milestone/phase task plans
│   ├── database_schema_demo_v1.md # Schema contract bám dữ liệu demo
│   ├── database_erd.md           # PostgreSQL ERD
│   ├── model_pipeline_guide.md   # Hướng dẫn cắm model/thay pipeline
│   └── runbook.md                # Lệnh vận hành thường dùng
├── models/                       # Đặt model weights local, không commit
├── scripts/
│   ├── benchmark_retrieval.py
│   ├── export_submission.py
│   ├── test_connections.py        # kiểm tra kết nối PostgreSQL / Milvus / R2
│   └── upload_kaggle_to_gcs.py   # upload video từ Kaggle lên GCS (chạy trên Kaggle Notebook)
├── docker-compose.yml
├── .env.example
└── README.md
```

## 4. Yêu Cầu Môi Trường

Khuyến nghị:

- Docker Desktop mới.
- PowerShell trên Windows.
- Node.js 22+ nếu chạy frontend không qua Docker.
- Python 3.11+ nếu chạy backend không qua Docker.
- GPU CUDA cần cho các bước embedding/extraction chạy local; cloud model runtime có thể chạy qua endpoint riêng.

## 5. Chạy Nhanh Bằng Docker

Từ root repository:

```powershell
Copy-Item .env.example .env
docker compose up -d --build
```

Kiểm tra service:

```powershell
docker compose ps
Invoke-RestMethod http://localhost:8000/healthz
Invoke-RestMethod http://localhost:8000/api/datasets
Invoke-RestMethod http://localhost:9200
```

URL sau khi chạy:

| Thành phần     | URL                             |
| -------------- | ------------------------------- |
| Web app        | `http://localhost:5173`         |
| API docs       | `http://localhost:8000/docs`    |
| Backend health | `http://localhost:8000/healthz` |
| Elasticsearch  | `http://localhost:9200`         |
| MinIO console  | `http://localhost:9001`         |
| Milvus         | `localhost:19530`               |

Dừng stack:

```powershell
docker compose down
```

## 5.1 Current Retrieval Stack (OpenAI Planner)

The default LLM query planner uses `openai_gpt4o`. Create `.env` from the
example file once, then set these values without committing the file:

```text
OPENAI_API_KEY=<your-openai-api-key>
AGENT_LLM_PROFILE=openai_gpt4o
```

Start or rebuild the complete local search stack from the repository root:

```powershell
docker compose up -d --build
docker compose ps
```

After changing `.env` or `configs/agent.yaml`, recreate only the backend so it
loads the new LLM profile:

```powershell
docker compose up -d --build backend
docker compose logs --tail 100 backend
```

Verify the API, frontend proxy, and a TRAKE query. The response contains
`langchain_direct_llm` when GPT-4o created the query plan.

```powershell
Invoke-RestMethod http://localhost:8000/healthz
Invoke-RestMethod http://localhost:5173/api/datasets

$dataset = (Invoke-RestMethod http://localhost:8000/api/datasets).datasets[0].id
$body = @{
  dataset_id = $dataset
  query_name = "smoke-trake-openai"
  query_type = "TRAKE"
  query_text = "E1: batter is added to a bowl of asparagus. E2: asparagus contacts oil in a pan. E3: asparagus is removed from the pan. E4: asparagus rests completely on a plate."
  top_k = 5
  profile = "competition_default"
  options = @{
    use_query_expansion = $true
    use_agent_query_planning = $true
    use_metadata = $true
    use_reranker = $true
    delta_t_max_ms = 180000
  }
} | ConvertTo-Json -Depth 8

$response = Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/retrieval/search `
  -ContentType "application/json" `
  -Body $body

$response.normalized_query.agent_query_plan
$response.results[0].sequence_frames
```

Run focused retrieval checks:

```powershell
docker compose run --rm --no-deps backend pytest -q `
  tests/test_retrieval_pipeline.py -k agent `
  tests/test_qa_trake_hardening.py::test_m5_trake_returns_stable_ordering_metadata `
  tests/test_aithena_ats.py
```

## 5.2 Short Local Commands

### Raw M/N/S videos before frame processing is complete

The source videos live under `gs://aic_ai_2026/raw/source=kaggle/dataset=aiteam_dataset_batch_2/source_version=kaggle_current/`. Register them in PostgreSQL independently of the keyframe pipeline:

```powershell
.\.venv\Scripts\python.exe apps\backend\scripts\import_raw_gcs_videos.py --dry-run
.\.venv\Scripts\python.exe apps\backend\scripts\import_raw_gcs_videos.py
```

The importer is safe to rerun. It links M, N, and S videos to their actual `.mp4` or `.mov` GCS objects while preserving existing frame counts and annotations. Use the **Video** lookup in the web app with a code such as `N050-V001`; when no processed frame exists, the raw video opens directly. The preview API is `/api/media/videos/{video_id}/preview`.

When frame artifacts are ready, import the VLM archives and reconcile which frame objects actually exist in GCS:

```powershell
.\.venv\Scripts\python.exe apps\backend\scripts\import_object_detection_batches.py --dry-run
.\.venv\Scripts\python.exe apps\backend\scripts\import_object_detection_batches.py --targets pg
.\.venv\Scripts\python.exe apps\backend\scripts\import_object_detection_batches.py --targets es --elasticsearch-url http://localhost:9200
.\.venv\Scripts\python.exe apps\backend\scripts\reconcile_gcs_media_presence.py
```

The frame importer keeps `is_media_present` false until the reconciliation confirms each GCS object. The Elasticsearch documents and PostgreSQL annotations use the same canonical frame ID; M vector IDs use generated `data/map-keyframes/*.csv` files for lookup. The raw videos remain playable when frames are missing.

For Kaggle SigLIP2 serving, configure the Kaggle Secret `SIGLIP2_API_KEY` with the same value as `.env`, run `notebooks/search pipeline/siglip2-server-v1.2.ipynb`, then set `SIGLIP2_EMBEDDING_BASE_URL` in `.env` to the notebook's Cloudflare `/v1` URL. `SIGLIP2_MODEL_KEY` in the notebook names the model registry entry; it is not the API authentication secret.

From the repository root, use these wrappers for local development:

Backend:

```powershell
.\be
```

This uses the environment from `.env`. If `.env` points to Supabase/GCS, the backend will start against those remote services.

Run DB migrations before starting the backend when the remote schema is not up to date:

```powershell
.\migrate
```

Frontend, in a second terminal:

```powershell
.\fe
```

URLs:

| Service      | URL                          |
| ------------ | ---------------------------- |
| Frontend     | `http://localhost:5173`      |
| Backend docs | `http://127.0.0.1:8010/docs` |

## 6. Chạy Backend Local

Dùng SQLite để phát triển nhanh backend mà không cần Postgres:

```powershell
cd apps\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:DATABASE_URL="sqlite:///./data/dev.db"
$env:MODEL_REGISTRY_PATH="../../configs/model_registry.yaml"
$env:RETRIEVAL_PROFILES_PATH="../../configs/retrieval_profiles.yaml"
$env:DATA_ROOT="../../data"
$env:STORAGE_PROVIDER="gcs"
$env:GCS_BUCKET="<bucket-name>"
$env:GCS_CREDENTIALS_FILE="apps/secrets/<service-account>.json"

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Swagger docs:

```text
http://localhost:8000/docs
```

## 7. Chạy Frontend Local

Backend cần chạy trước tại `http://localhost:8000`.

```powershell
cd apps\web
npm install
$env:VITE_API_BASE_URL="http://localhost:8000"
npm run dev
```

Mở:

```text
http://localhost:5173
```

Build frontend:

```powershell
npm run build
```

## 11. Documents

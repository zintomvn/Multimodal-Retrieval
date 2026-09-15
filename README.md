# Multimodal Retrieval Assistant

Trợ lý truy xuất multimedia cho AI Challenge 2026. Hệ thống hỗ trợ ingest dữ liệu video/hình ảnh/âm thanh/văn bản, preprocessing metadata, tìm kiếm KIS/QA/TRAKE, xem context frame, chọn kết quả và export `submission.zip` đúng format Codabench.

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

## 11. Tài Liệu Liên Quan

| Tài liệu                                        | Nội dung                                      |
| ----------------------------------------------- | --------------------------------------------- |
| `docs/blueprint/proposal.md`                    | Đề xuất hệ thống, mục tiêu, phạm vi.          |
| `docs/blueprint/design.md`                      | Thiết kế kỹ thuật, module, schema, ERD.       |
| `docs/backend/specs/database_schema_demo_v1.md` | Schema contract mục tiêu cho dữ liệu `demo/`. |
| `docs/backend/specs/database_erd.md`            | ERD PostgreSQL riêng để xem nhanh.            |
| `docs/backend/guides/model_pipeline.md`         | Cách bỏ model vào và sửa pipeline.            |
| `docs/backend/runbooks/operations.md`           | Lệnh vận hành thường dùng.                    |
| `docs/tasks/milestone1.md`                      | Phân công nhóm và test milestone 1.           |
| `docs/tasks/phase1.md`                          | Checklist thực thi giai đoạn đầu.             |
| `apps/backend/README.md`                        | Tài liệu backend service.                     |
| `apps/web/README.md`                            | Tài liệu frontend app.                        |

## 12. Quy Ước Làm Việc

- Không commit `.env`.
- Không commit model weights.
- Không commit raw dataset/video lớn.
- Không commit generated submissions.
- API thay đổi phải cập nhật `apps/backend/README.md` và `apps/web/src/types.ts`.
- Pipeline/model thay đổi phải cập nhật `docs/backend/guides/model_pipeline.md`.
- Retrieval profile thay đổi phải ghi lý do trong PR hoặc task note.

## 13. Troubleshooting Nhanh

| Vấn đề                         | Cách xử lý                                                                      |
| ------------------------------ | ------------------------------------------------------------------------------- |
| Backend không lên              | `docker compose logs backend`                                                   |
| DB chưa healthy                | `docker compose ps postgres`                                                    |
| Elasticsearch không phản hồi   | `Invoke-RestMethod http://localhost:9200`                                       |
| Frontend không load dataset    | Kiểm tra `VITE_API_BASE_URL` và backend `/healthz`.                             |
| Port bị chiếm                  | Dừng stack cũ bằng `docker compose down --remove-orphans`.                      |
| Search không đúng              | Kiểm tra dataset đã import, query text, vector collection và `score_breakdown`. |
| Export ZIP lỗi                 | Gọi `/api/submissions/{id}/validate` để xem lỗi format.                         |
| Upload GCS lỗi 413             | File vượt giới hạn 2 GB — dùng server path hoặc Kaggle script.                  |
| Upload GCS không tìm thấy file | Backend không mount đường dẫn đó — kiểm tra path tuyệt đối trong container.     |
| Milvus upload lỗi key          | File `.npz` cần hai key `frame_ids` và `vectors` — xem `_load_features()`.      |
| GCS_BUCKET chưa cấu hình       | Set env var `GCS_BUCKET` và `GCS_CREDENTIALS_FILE` trong `.env`.                |

## 14. L21-L30 Cloud Feature Import Và Search Test

Trạng thái kiểm tra ngày 2026-08-12:

- GCS keyframes cho `L21` có `25,581` ảnh; `L21_V015` có `1,104` ảnh. Frame gần `25800` nhất đang thấy trên GCS là `L21_V015_F025846`.
- Feature artifacts L21 hiện có dưới `gs://aic_ai_2026/features/extractors/dataset=ai_challenge_2025/batch=L21/...` chỉ phủ `L21_V001` và `L21_V002`.
- Đã import được feature artifacts cloud L21-L30 hiện có vào local DB `data/dev_search_local.db` và Elasticsearch index `keyframe_annotations`.
- Local DB hiện có `286,610` keyframes L21-L30. Elasticsearch hiện có `286,605` docs L21-L30.
- Caption hiện chỉ có cho L21 (`1,890` rows). OCR hiện có cho L21-L24 (`42,503` rows). Object detection hiện có cho L21-L30 (`256,044` rows có object label; các frame không detect được object giữ list rỗng).
- Supabase trong `.env` chưa connect được: pooler trả `tenant/user ... not found`, direct host `db.<project-ref>.supabase.co` không resolve. Cần cập nhật lại `DATABASE_URL` trước khi import vào Supabase thật.
- Zilliz đang trả `cluster status STOPPED`; cần bật cluster trước khi kiểm collection hoặc import vector.
- Chưa thấy full visual embedding artifact cho L21 trong GCS, nên chưa thể upsert vector L21 vào Zilliz.

### 14.1 Chạy Elasticsearch local

```powershell
docker compose up -d elasticsearch
Invoke-RestMethod http://localhost:9200
```

### 14.2 Import L21 coverage artifact từ GCS

Lệnh dưới đây import vào local SQLite fallback và Elasticsearch để frontend test ngay:

```powershell
$env:DATABASE_URL="sqlite:///D:/University/Projects/Individual projects/Multimodal-Retrieval/data/dev_search_local.db"
$env:TEXT_SEARCH_BACKEND="postgres"

python apps/backend/scripts/import_cloud_features.py `
  --no-local-defaults `
  --artifact-uri "gs://aic_ai_2026/features/import_bundles/dataset=ai_challenge_2025/batch=L21/frame_profile=autoshot_v1/latest.json" `
  --batch-size 512 `
  --no-milvus `
  --no-text-embeddings `
  --skip-init-db
```

Bundle `latest.json` trên GCS đang trỏ tới artifact patch đủ `25,581` frames / `29` videos. Các artifact `full_l21` cũ vẫn được giữ nguyên nhưng metadata đã được đánh dấu `SUCCESS_INCOMPLETE_COVERAGE` và `do_not_use_for_full_batch_import=true` để tránh import nhầm.

Khi `DATABASE_URL` Supabase đã đúng, bỏ dòng override SQLite và chạy lại cùng lệnh nhưng không dùng `--skip-init-db`.

### 14.3 Kiểm tra import

```powershell
Invoke-RestMethod `
  -Uri "http://localhost:9200/keyframe_annotations/_count" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"query":{"prefix":{"video_id":"L21"}}}'
```

Kết quả expected sau khi import bundle L21 patch: `count = 25581`.

### 14.4 Chạy backend và frontend để search

```powershell
$env:DATABASE_URL="sqlite:///D:/University/Projects/Individual projects/Multimodal-Retrieval/data/dev_search_local.db"
$env:TEXT_SEARCH_BACKEND="postgres"
$env:ELASTICSEARCH_URL="http://localhost:9200"
$env:STORAGE_PROVIDER="gcs"
$env:CORS_ORIGINS="http://localhost:5173,http://127.0.0.1:5173"

cd apps/backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Mở terminal khác:

```powershell
$env:VITE_API_BASE_URL="http://localhost:8000"
cd apps/web
npm run dev -- --host 127.0.0.1 --port 5173
```

Frontend: `http://127.0.0.1:5173`

### 14.5 Smoke test query tiger

Query:

```text
A news report introduces a tiger population in a southern locality that has recently welcomed between three and six cubs. This is a rare tiger breed.
```

Kết quả API hiện tại không có `L21_V015` quanh frame `25800` trong top 20 hoặc top 100. Đây là đúng với trạng thái dữ liệu hiện tại, vì feature artifacts L21 chưa chứa `L21_V015`; chỉ có keyframe ảnh gốc trên GCS.

Sau khi caption/OCR/object/vector artifacts full L21 có `L21_V015`, chạy lại import ở mục 14.2 và thêm artifact embedding vào lệnh import để bật vector:

```powershell
python apps/backend/scripts/import_cloud_features.py `
  --artifact-uri "<gcs-jsonl-co-truong-embedding>" `
  --milvus-collection keyframe_embeddings_pe_core_bigG_14_448 `
  --no-text-embeddings
```

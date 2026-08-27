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

| Thành phần | URL |
| --- | --- |
| Web app | `http://localhost:5173` |
| API docs | `http://localhost:8000/docs` |
| Backend health | `http://localhost:8000/healthz` |
| Elasticsearch | `http://localhost:9200` |
| MinIO console | `http://localhost:9001` |
| Milvus | `localhost:19530` |

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

| Service | URL |
| --- | --- |
| Frontend | `http://localhost:5173` |
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

## 8. Luồng Sử Dụng Chính

### 8.0 Quan Hệ Giữa Các Store

```
PostgreSQL (nguồn sự thật)
  Dataset → Video → Frame (id, image_uri, thumbnail_uri)
                  ↑               ↑
             frame_id         GCS key
                  │               │
         Milvus collection    GCS bucket
         id = Frame.id        key = "{dataset_id}/{video_code}/{file}"
         vector = CLIP emb.
```

Khi search: Milvus trả về `frame_id` → JOIN vào Postgres → lấy `thumbnail_uri` (GCS URL).

### 8.1 Ingest/Preprocessing

Luồng hiện tại import metadata frame từ GCS vào PostgreSQL, sau đó ingest vector vào Milvus/Zilliz bằng processor.

```powershell
.\scripts\ingest_dataset.ps1
```

Kiểm tra dữ liệu PostgreSQL:

```powershell
docker compose exec postgres psql -U multimodal -d multimodal -c "select count(*) from datasets;"
docker compose exec postgres psql -U multimodal -d multimodal -c "select count(*) from videos;"
docker compose exec postgres psql -U multimodal -d multimodal -c "select count(*) from frames;"
docker compose exec postgres psql -U multimodal -d multimodal -c "select kind, count(*) from frame_annotations group by kind;"
```

### 8.2 Retrieval

Có thể test qua frontend hoặc Swagger.

KIS smoke test bằng PowerShell:

```powershell
$dataset=(Invoke-RestMethod http://localhost:8000/api/datasets).datasets[0].id
$body=@{
  dataset_id=$dataset
  query_name="query-1-kis"
  query_type="KIS"
  query_text="royal decorative panel dragon cloud PHU XUAN GIA DINH"
  top_k=3
  profile="competition_default"
  options=@{
    use_query_expansion=$true
    use_metadata=$true
    delta_t_max_ms=180000
  }
} | ConvertTo-Json -Depth 6

Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/retrieval/search `
  -ContentType "application/json" `
  -Body $body
```

Kỳ vọng khi đã ingest dữ liệu thật:

```text
Top result trả về `video_code`, `frame_idx` và thumbnail qua `/api/media/frames/{frame_id}/thumbnail`.
```

### 8.3 Upload Dữ Liệu Lên Cloud

Mở modal **Upload to cloud** từ nút trên giao diện web để đẩy dữ liệu lên GCS và Milvus.

**GCS — upload ảnh và video:**

- *Server path*: nhập đường dẫn folder hoặc file ZIP trên server backend.
- *From machine*: chọn file `.jpg/.jpeg/.png/.mp4/.avi/.mov/.mkv/.zip` trực tiếp từ máy tính — ZIP sẽ được giải nén tự động.
- Backend nhận file, lưu tạm, chạy background job, và trả về `job_id` để poll tiến trình.
- API endpoints: `POST /api/ingest/upload/gcs` (path) và `POST /api/ingest/upload/file/gcs` (multipart).

**Milvus — index vector embeddings:**

- *Features file path*: đường dẫn `.npy` hoặc `.npz` trên server.
- *From machine*: chọn file `.npy/.npz/.zip` từ máy tính — ZIP sẽ được giải nén và file `.npy/.npz` đầu tiên được dùng.
- Format `.npz` cần hai key: `frame_ids` (mảng string) và `vectors` (mảng 2D float). File `.npy` thuần cần mảng 2D `(N, D)`.
- API endpoints: `POST /api/ingest/upload/milvus` (path) và `POST /api/ingest/upload/file/milvus` (multipart).

Giới hạn upload từ browser: **2 GB**. File lớn hơn dùng server path hoặc script Kaggle bên dưới.

### 8.4 Pipeline Ingest GCS Ưu Tiên Kaggle

Pipeline chính để đẩy 3 bộ data lên Google Cloud Storage là `scripts/upload_kaggle_to_gcs.py`. Script này chạy được ngay trên Kaggle Notebook và tạo run artifacts để giám sát: `manifest.jsonl`, `summary.json`, `errors.jsonl`, `metrics.csv`, `ingest.log`.

Nguồn data được khai báo trong `configs/data_ingestion_sources.yaml`; script không còn fallback sang danh sách dataset hardcode trong Python. Mỗi source cần có `source_id`, `dataset_id`, `kaggle_mount_path`, `expected_batches`, `batch_detection`, và có thể override `gcs.raw_prefix`. Nếu Kaggle mount có folder wrapper dư, cấu hình thêm `relative_path_prefixes_to_strip` để chuẩn hóa `relative_path` trước khi tạo GCS key.

| Source id | Kaggle dataset | Batch |
| --- | --- | --- |
| `l21_l30_ai_challenge_2025` | `aresusayhi/ai-challenge-2025` | `L21-L30` |
| `k01_k10_data_video_batch_2_1` | `tuktuai/data-video-batch-2-1` | `K01-K10` |
| `k11_k20_data_video_batch_2_2` | `tuktuai/data-video-batch2-2` | `K11-K20` |

Chuẩn bị trên Kaggle:

1. Tạo Notebook từ dataset cần ingest và bật **Internet = On**.
2. Add Kaggle Secrets:
   - `GCS_BUCKET`: tên bucket GCS.
   - `GCS_CREDENTIALS_JSON`: nội dung JSON của service account có quyền upload vào bucket.
3. Đưa code ingest và config lên Kaggle. Script cần đọc được file YAML `configs/data_ingestion_sources.yaml`; nếu chạy từ repo đầy đủ thì không cần truyền `--config`, còn nếu chỉ upload riêng script/YAML thì phải truyền path YAML bằng `--config`.
4. Cài dependency:

```python
!pip install google-cloud-storage pyyaml tqdm -q
```

Layout khuyến nghị trên Kaggle nếu upload/clone cả repo:

```text
/kaggle/working/Multimodal-Retrieval/
├── scripts/upload_kaggle_to_gcs.py
└── configs/data_ingestion_sources.yaml
```

Khi đó chạy từ repo root:

```python
%cd /kaggle/working/Multimodal-Retrieval
!python scripts/upload_kaggle_to_gcs.py --list-sources
```

Nếu chỉ upload riêng file script và YAML, truyền config rõ ràng:

```python
!python /kaggle/working/upload_kaggle_to_gcs.py \
  --config /kaggle/input/your-config-dataset/data_ingestion_sources.yaml \
  --list-sources
```

Kiểm tra source và batch mapping trước khi upload:

```python
!python scripts/upload_kaggle_to_gcs.py --list-sources

!python scripts/upload_kaggle_to_gcs.py \
  --source-id l21_l30_ai_challenge_2025 \
  --batches L21 \
  --dry-run \
  --max-files 5
```

Nếu dùng file env local, truyền trực tiếp file đó:

```powershell
python scripts\upload_kaggle_to_gcs.py `
  --env-file "env(Thắng -30_6 updated)" `
  --source-id l21_l30_ai_challenge_2025 `
  --batches L21 `
  --dry-run `
  --max-files 5
```

File env cần có các key:

```text
GCS_BUCKET=aic_ai_2026
GCS_CREDENTIALS_FILE=apps\secrets\gen-lang-client-0547522732-410672fac05f.json
GCS_PUBLIC_URL=https://storage.googleapis.com/aic_ai_2026
```

Nếu `GCS_CREDENTIALS_FILE` là relative path, script sẽ ưu tiên resolve từ thư mục chứa env-file. Đảm bảo file JSON thật sự nằm ở `apps\secrets\...`; folder này đang được `.gitignore` để tránh commit credential.

Upload từng batch:

```python
!python scripts/upload_kaggle_to_gcs.py \
  --source-id l21_l30_ai_challenge_2025 \
  --batches L21,L22 \
  --workers 4

!python scripts/upload_kaggle_to_gcs.py \
  --source-id k01_k10_data_video_batch_2_1 \
  --batches K01 \
  --workers 4

!python scripts/upload_kaggle_to_gcs.py \
  --source-id k11_k20_data_video_batch_2_2 \
  --batches K11 \
  --workers 4
```

Tham số quan trọng:

| Tham số | Ý nghĩa |
| --- | --- |
| `--batches L21,L22` | Chọn batch cần ingest; dùng `all` để chạy toàn bộ source. |
| `--input-root /path/to/data` | Override mount path nếu Kaggle mount khác mặc định. |
| `--gcs-prefix raw/source=kaggle` | Override prefix raw trong bucket. |
| `--max-files 10` | Smoke test với số file giới hạn. |
| `--dry-run` | Chỉ tạo manifest/summary, không upload. |
| `--skip-existing` | Mặc định bật; chạy lại an toàn nếu bị gián đoạn. |
| `--no-skip-existing --overwrite` | Chỉ dùng khi có chủ ý ghi đè object cũ. |

GCS key mặc định:

```text
raw/source=kaggle/dataset=<dataset_id>/source_version=<source_version>/batch=<batch_id>/<relative_path>
```

Riêng source `l21_l30_ai_challenge_2025`, script strip các prefix `ai-challenge-2025/Videos/Videos/` hoặc `Videos/Videos/` trước khi upload, nên object không còn segment `original/` và không còn hai folder `Videos` dư. Ví dụ:

```text
raw/source=kaggle/dataset=ai_challenge_2025/source_version=kaggle_current/batch=L22/Videos_L22_a/video/<file_name>.mp4
```

Report vận hành chi tiết: `docs/data processing/cloud/kaggle_to_cloud_report.md`.

Run artifacts local nằm trong `ingestion_runs/<run_id>/`. Khi upload thật, script cũng đẩy artifacts lên:

```text
manifests/pipeline=kaggle_ingest/run_id=<run_id>/
logs/pipeline=kaggle_ingest/run_id=<run_id>/
```

`metrics.csv` có thể dùng để làm dashboard theo `run_id`, `source_id`, `batch_id`, `status`, `size_bytes`, `bytes_uploaded`, `duration_ms`. `errors.jsonl` là danh sách file lỗi để retry riêng.

### 8.5 Skeleton Monitoring Airflow / Cloud Composer

DAG mẫu nằm tại `dags/data_ingestion_kaggle.py`. DAG này gọi cùng script `upload_kaggle_to_gcs.py`, nên format manifest/log/metrics giống Kaggle Notebook.

Trong Cloud Composer, đặt các biến môi trường tùy theo cách deploy repo:

```text
INGESTION_REPO_ROOT=/home/airflow/gcs/data/Multimodal-Retrieval
INGESTION_CONFIG_PATH=/home/airflow/gcs/data/Multimodal-Retrieval/configs/data_ingestion_sources.yaml
INGESTION_RUN_DIR=/home/airflow/gcs/data/ingestion_runs
```

DAG cũng có param `env_file`; có thể đặt tới file env đã upload vào Composer worker nếu không muốn khai báo `GCS_BUCKET`/credential bằng environment variables.

Dashboard tối thiểu:

- Trạng thái upload theo batch: đếm `uploaded`, `skipped`, `failed`.
- Số byte đã upload theo batch và run.
- Thời lượng theo file và theo run.
- Bảng lỗi từ `errors.jsonl` / Cloud Logging.
- Trạng thái Airflow DAG run và số lần retry của task.

Cloud Monitoring có thể tạo log-based metrics từ `ingest.log` hoặc dùng `metrics.csv` trong GCS làm nguồn cho Looker Studio/BigQuery sau này.

### 8.6 Export Submission

Qua frontend:

1. Chạy query `KIS`, `QA`, hoặc `TRAKE`.
2. Chọn result bằng `Select`.
3. Bấm `Export ZIP`.
4. Download ZIP.

Qua CLI:

```powershell
python scripts\export_submission.py --dataset-id <dataset_id> --rows-json rows.json
```

## 9. Cấu Hình Model

Model thật đặt trong `models/`:

```text
models/
├── pe-core-bigg/
├── beit3/
├── paddle-vietocr/
├── whisperx/
└── qwen2.5-vl/
```

Không commit model weights.

Bật/tắt model trong:

```text
configs/model_registry.yaml
```

Ví dụ:

```yaml
embedders:
  openai_embedding:
    provider: openai_compatible
    base_url: http://localhost:8002/v1
    model: openclip-ViT-B-32
    dimension: 512
    enabled: true
```

Sau khi đổi embedding/OCR/ASR/caption model, cần chạy lại ingest/index.

Xem hướng dẫn chi tiết:

- `docs/backend/guides/model_pipeline.md`
- `models/README.md`

## 10. Test Và Verification

### 10.1 Backend smoke test trong container

```powershell
docker compose run --rm --no-deps `
  -e DATABASE_URL=sqlite:////tmp/multimodal_smoke.db `
  -e DATA_ROOT=/tmp/data `
  backend python -c "from app.db.bootstrap import init_db; init_db(); print('db ok')"
```

Kỳ vọng:

```text
db ok
```

### 10.2 Frontend build test

```powershell
docker compose run --rm --no-deps web npm run build
```

### 10.3 Phase 1 testing plan

Test plan chi tiết:

- `docs/tasks/phase1.md`
- `docs/tasks/milestone1.md`

## 11. Tài Liệu Liên Quan

| Tài liệu | Nội dung |
| --- | --- |
| `docs/blueprint/proposal.md` | Đề xuất hệ thống, mục tiêu, phạm vi. |
| `docs/blueprint/design.md` | Thiết kế kỹ thuật, module, schema, ERD. |
| `docs/backend/specs/database_schema_demo_v1.md` | Schema contract mục tiêu cho dữ liệu `demo/`. |
| `docs/backend/specs/database_erd.md` | ERD PostgreSQL riêng để xem nhanh. |
| `docs/backend/guides/model_pipeline.md` | Cách bỏ model vào và sửa pipeline. |
| `docs/backend/runbooks/operations.md` | Lệnh vận hành thường dùng. |
| `docs/tasks/milestone1.md` | Phân công nhóm và test milestone 1. |
| `docs/tasks/phase1.md` | Checklist thực thi giai đoạn đầu. |
| `apps/backend/README.md` | Tài liệu backend service. |
| `apps/web/README.md` | Tài liệu frontend app. |

## 12. Quy Ước Làm Việc

- Không commit `.env`.
- Không commit model weights.
- Không commit raw dataset/video lớn.
- Không commit generated submissions.
- API thay đổi phải cập nhật `apps/backend/README.md` và `apps/web/src/types.ts`.
- Pipeline/model thay đổi phải cập nhật `docs/backend/guides/model_pipeline.md`.
- Retrieval profile thay đổi phải ghi lý do trong PR hoặc task note.

## 13. Troubleshooting Nhanh

| Vấn đề | Cách xử lý |
| --- | --- |
| Backend không lên | `docker compose logs backend` |
| DB chưa healthy | `docker compose ps postgres` |
| Elasticsearch không phản hồi | `Invoke-RestMethod http://localhost:9200` |
| Frontend không load dataset | Kiểm tra `VITE_API_BASE_URL` và backend `/healthz`. |
| Port bị chiếm | Dừng stack cũ bằng `docker compose down --remove-orphans`. |
| Search không đúng | Kiểm tra dataset đã import, query text, vector collection và `score_breakdown`. |
| Export ZIP lỗi | Gọi `/api/submissions/{id}/validate` để xem lỗi format. |
| Upload GCS lỗi 413 | File vượt giới hạn 2 GB — dùng server path hoặc Kaggle script. |
| Upload GCS không tìm thấy file | Backend không mount đường dẫn đó — kiểm tra path tuyệt đối trong container. |
| Milvus upload lỗi key | File `.npz` cần hai key `frame_ids` và `vectors` — xem `_load_features()`. |
| GCS_BUCKET chưa cấu hình | Set env var `GCS_BUCKET` và `GCS_CREDENTIALS_FILE` trong `.env`. |

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


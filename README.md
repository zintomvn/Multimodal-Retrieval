# Multimodal Retrieval Assistant

Trợ lý truy xuất multimedia cho AI Challenge 2026. Hệ thống hỗ trợ ingest dữ liệu video/hình ảnh/âm thanh/văn bản, preprocessing metadata, tìm kiếm KIS/QA/TRAKE, xem context frame, chọn kết quả và export `submission.zip` đúng format Codabench.

Backend hiện chạy được bằng mock data/mock model để nhóm phát triển song song trước khi có dataset và model thật. Kiến trúc đã chuẩn bị sẵn PostgreSQL, Milvus, Elasticsearch, MinIO, Redis và model adapters để nâng cấp dần.

## 1. Mục Tiêu Hệ Thống

- **Textual KIS**: tìm đúng video và frame từ mô tả văn bản.
- **QA**: tìm frame liên quan và sinh câu trả lời ngắn để nộp.
- **TRAKE**: tìm chuỗi frame theo thứ tự sự kiện thời gian.
- **Human-in-the-loop**: người thi xem ranked results, kiểm tra context và chọn đáp án.
- **Submission builder**: xuất CSV/ZIP đúng cấu trúc `submission/`.
- **Model-ready**: có registry để thay mock model bằng model thật.

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
        `-- Model adapters: mock/local/HF/OpenAI-compatible
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
│   ├── dataset_manifest.example.yaml
│   ├── model_registry.yaml       # Bật/tắt model và khai báo checkpoint
│   └── retrieval_profiles.yaml   # Trọng số ranking/retrieval
├── data/
│   └── mock/                     # Query/data mẫu cho mock mode
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
│   ├── build_index.ps1
│   ├── export_submission.py
│   └── ingest_dataset.ps1
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
- GPU CUDA chỉ cần khi bật model thật; mock mode không cần GPU.

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
$env:MOCK_MODE="true"

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

### 8.1 Ingest/Preprocessing

Giai đoạn hiện tại hỗ trợ mock ingest để kiểm tra pipeline/API/DB trước.

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

Kỳ vọng mock mode:

```text
Top result: L00_V000, frame 1234
```

### 8.3 Export Submission

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
  clip_mock:
    provider: mock
    enabled: false
  pe_core_bigg:
    provider: huggingface
    checkpoint_uri: models/pe-core-bigg
    device: cuda:0
    dtype: fp16
    enabled: true
```

Sau khi đổi embedding/OCR/ASR/caption model, cần chạy lại ingest/index.

Xem hướng dẫn chi tiết:

- `docs/model_pipeline_guide.md`
- `models/README.md`

## 10. Test Và Verification

### 10.1 Backend smoke test trong container

```powershell
docker compose run --rm --no-deps `
  -e DATABASE_URL=sqlite:////tmp/multimodal_smoke.db `
  -e DATA_ROOT=/tmp/data `
  backend python -c "from app.db.bootstrap import init_db, seed_mock_data; from app.db.session import SessionLocal; from app.db.models import Dataset; from app.modules.retrieval.service import RetrievalService; from app.modules.retrieval.schemas import SearchRequest; init_db(); db=SessionLocal(); seed_mock_data(db); ds=db.query(Dataset).first(); resp=RetrievalService(db).search(SearchRequest(dataset_id=ds.id, query_name='query-1-kis', query_type='KIS', query_text='royal decorative panel dragon cloud PHU XUAN GIA DINH', top_k=3)); print(resp.results[0].video_code, resp.results[0].frame_idx); db.close()"
```

Kỳ vọng:

```text
L00_V000 1234
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
| `docs/database_schema_demo_v1.md` | Schema contract mục tiêu cho dữ liệu `demo/`. |
| `docs/database_erd.md` | ERD PostgreSQL riêng để xem nhanh. |
| `docs/model_pipeline_guide.md` | Cách bỏ model vào và sửa pipeline. |
| `docs/runbook.md` | Lệnh vận hành thường dùng. |
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
- Pipeline/model thay đổi phải cập nhật `docs/model_pipeline_guide.md`.
- Retrieval profile thay đổi phải ghi lý do trong PR hoặc task note.

## 13. Troubleshooting Nhanh

| Vấn đề | Cách xử lý |
| --- | --- |
| Backend không lên | `docker compose logs backend` |
| DB chưa healthy | `docker compose ps postgres` |
| Elasticsearch không phản hồi | `Invoke-RestMethod http://localhost:9200` |
| Frontend không load dataset | Kiểm tra `VITE_API_BASE_URL` và backend `/healthz`. |
| Port bị chiếm | Dừng stack cũ bằng `docker compose down --remove-orphans`. |
| Search không đúng | Kiểm tra mock dataset, query text, `score_breakdown`. |
| Export ZIP lỗi | Gọi `/api/submissions/{id}/validate` để xem lỗi format. |

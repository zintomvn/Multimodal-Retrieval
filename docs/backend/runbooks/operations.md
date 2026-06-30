# Runbook vận hành

Runbook đầy đủ cho backend cloud stack:
- `docs/backend/runbooks/cloud_e2e.md`

## 1. Khởi động local

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Endpoint:

- Web: http://localhost:5173
- API docs: http://localhost:8000/docs
- Healthcheck: http://localhost:8000/healthz

## 2. Chạy backend không dùng Docker

```powershell
cd apps\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DATABASE_URL="sqlite:///./data/dev.db"
$env:MODEL_REGISTRY_PATH="../../configs/model_registry.yaml"
$env:RETRIEVAL_PROFILES_PATH="../../configs/retrieval_profiles.yaml"
uvicorn app.main:app --reload --port 8000
```

## 3. Chạy frontend không dùng Docker

```powershell
cd apps\web
npm install
npm run dev
```

## 4. Quy trình search và nộp bài

1. Mở web.
2. Chọn dataset.
3. Chọn mode `KIS`, `QA` hoặc `TRAKE`.
4. Nhập query hoặc copy từ file `query-*.txt`.
5. Bấm `Run`.
6. Kiểm tra result và context.
7. Bấm `Select` cho các dòng muốn nộp.
8. Bấm `Export ZIP`.
9. Tải file zip ở link `Download`.

ZIP xuất ra có cấu trúc:

```text
submission/
├── query-1-kis.csv
├── query-2-qa.csv
└── query-3-trake.csv
```

## 5. Chạy ingest mock

```powershell
.\scripts\ingest_dataset.ps1
```

## 6. Export submission bằng CLI

Tạo file rows:

```json
[
  {
    "query_name": "query-1-kis",
    "query_type": "KIS",
    "rank": 1,
    "video_code": "L00_V000",
    "frame_indices": [1234]
  }
]
```

Chạy:

```powershell
python scripts\export_submission.py --dataset-id <dataset_id> --rows-json rows.json
```

## 7. Khi service ngoài chưa bật

Backend vẫn chạy bằng mock nếu:

- Milvus chưa sẵn sàng.
- Elasticsearch chưa sẵn sàng.
- Chưa có model thật.

Khi chuyển sang adapter thật, bật model trong `configs/model_registry.yaml`, đảm bảo service tương ứng chạy, rồi re-index.

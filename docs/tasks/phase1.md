# Phase 1 - Checklist Thực Thi Milestone 1

> File này là checklist vận hành theo ngày cho giai đoạn khởi đầu. Chi tiết phân vai và test cases nằm trong `docs/tasks/milestone1.md`.

## 1. Mục Tiêu Phase 1

Phase 1 phải chứng minh được 4 luồng:

1. **Ingest/preprocessing**: manifest/video source được đưa vào PostgreSQL.
2. **Retrieval**: KIS/QA/TRAKE chạy được trên dữ liệu đã ingest.
3. **Frontend**: người dùng chọn result và export ZIP.
4. **Testing**: có unit test + integration test đầu tiên.

## 2. Checklist Theo Thứ Tự Làm

### Bước 1 - Chuẩn bị môi trường

- [ ] Cả nhóm pull code mới nhất.
- [ ] Tạo `.env` từ `.env.example`.
- [ ] Chạy `docker compose up -d --build`.
- [ ] Kiểm tra:

```powershell
Invoke-RestMethod http://localhost:8000/healthz
Invoke-RestMethod http://localhost:8000/api/datasets
Invoke-RestMethod http://localhost:9200
```

- [ ] Mở frontend `http://localhost:5173`.

### Bước 2 - Test ingest/preprocessing mock

- [ ] Gọi ingest API:

```powershell
$body=@{
  manifest_path="configs/dataset_manifest.example.yaml"
  mode="mock"
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/api/ingest/jobs" `
  -ContentType "application/json" `
  -Body $body
```

- [ ] Kiểm tra PostgreSQL:

```powershell
docker compose exec postgres psql -U multimodal -d multimodal -c "select count(*) from datasets;"
docker compose exec postgres psql -U multimodal -d multimodal -c "select count(*) from videos;"
docker compose exec postgres psql -U multimodal -d multimodal -c "select count(*) from frames;"
docker compose exec postgres psql -U multimodal -d multimodal -c "select kind, count(*) from frame_annotations group by kind;"
docker compose exec postgres psql -U multimodal -d multimodal -c "select count(*) from events;"
```

- [ ] Chạy lại ingest cùng manifest.
- [ ] Kiểm tra dữ liệu không bị nhân đôi bất thường.
- [ ] Ghi bug nếu job status không rõ hoặc DB thiếu record.

### Bước 3 - Tạo test fixtures cho preprocessing thật

- [ ] Tạo thư mục:

```text
data/test-fixtures/
├── videos/
├── manifests/
└── expected/
```

- [ ] Chuẩn bị 1-2 video `.mp4` nhỏ.
- [ ] Tạo `sample_manifest.yaml`.
- [ ] Không commit video lớn.
- [ ] Nếu cần commit fixture, dùng video synthetic rất nhỏ.

### Bước 4 - Viết unit test ingest

Tạo file:

```text
apps/backend/tests/test_ingest_pipeline.py
apps/backend/tests/test_preprocessing_persistence.py
```

Test cần có:

- [ ] Manifest parser đọc đúng video list.
- [ ] Pipeline stages chạy đúng thứ tự.
- [ ] Video import tạo `Dataset` và `Video`.
- [ ] Preprocess tạo `Frame`.
- [ ] Annotation stages tạo `FrameAnnotation`.
- [ ] Event segmentation tạo `Event`.
- [ ] Index build tạo `MILVUS` và `ELASTICSEARCH`.
- [ ] Ingest idempotent khi chạy lại cùng manifest.

### Bước 5 - Test retrieval sau ingest

- [ ] Chạy KIS API, kỳ vọng top 1 `L00_V000, 1234`.
- [ ] Chạy QA API, kỳ vọng answer `Disney`.
- [ ] Chạy TRAKE API, kỳ vọng video `L10_V001` và có `sequence_frames`.
- [ ] Kiểm tra result có `score_breakdown`.

### Bước 6 - Test frontend E2E

- [ ] Mở web.
- [ ] Chạy KIS, chọn 1 result.
- [ ] Chạy QA, chọn 1 result.
- [ ] Chạy TRAKE, chọn 1 result.
- [ ] Export ZIP.
- [ ] Download ZIP.
- [ ] Giải nén kiểm tra folder `submission/`.
- [ ] Kiểm tra CSV không header và đúng format.

## 3. Người Phụ Trách

| Việc | Owner chính | Người hỗ trợ |
| --- | --- | --- |
| Ingest API + DB persistence | Người 1 | Người 4 |
| Preprocessing pipeline stages | Người 4 | Người 1 |
| Retrieval KIS/QA | Người 2 | Người 5 |
| TRAKE/ATS | Người 3 | Người 2 |
| Frontend E2E | Người 5 | Cả nhóm |
| Test report | Người 5 | Từng owner module |

## 4. Test Report Mẫu

```text
Date:
Tester:
Commit:

Environment:
- Docker:
- OS:
- GPU:

Checks:
- Backend health:
- Elasticsearch:
- Dataset API:
- Ingest mock:
- DB persistence:
- KIS:
- QA:
- TRAKE:
- Export ZIP:

Bugs:
1.
2.

Notes:
```

## 5. Điều Kiện Kết Thúc Phase 1

- [ ] Cả nhóm chạy được Docker stack.
- [ ] Ingest mock ghi dữ liệu vào PostgreSQL.
- [ ] Có test idempotency ingest.
- [ ] Có ít nhất 2 test cho preprocessing persistence.
- [ ] Retrieval chạy được sau ingest.
- [ ] Frontend export được ZIP.
- [ ] Mỗi module có owner rõ cho giai đoạn tối ưu.

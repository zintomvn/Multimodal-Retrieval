# Milestone 1 - Khởi Động Hệ Thống Retrieval

> Mục tiêu giai đoạn: dựng nền hệ thống chạy được end-to-end bằng mock data/model, đồng thời chuẩn bị sẵn đường cắm model thật để mỗi thành viên có thể tối ưu module của mình ở milestone sau.

## 1. Mục Tiêu Cần Đạt

Milestone 1 không tập trung tối ưu điểm retrieval. Trọng tâm là **phân tách module đúng**, **chạy được pipeline đầu tiên**, **có test tối thiểu**, và **mỗi người hiểu rõ phần mình phụ trách**.

Kết quả cuối milestone:

- Chạy được stack bằng `docker compose up --build`.
- Backend health OK tại `http://localhost:8000/healthz`.
- Frontend mở được tại `http://localhost:5173`.
- Có mock dataset và mock retrieval cho 3 dạng query: `KIS`, `QA`, `TRAKE`.
- Có test ingest/preprocessing tối thiểu: nhận video hoặc mock video manifest, extract/preprocess artifact, lưu metadata vào PostgreSQL, sẵn sàng index Milvus/Elasticsearch.
- Export được `submission.zip` đúng format.
- Có model registry rõ ràng để bật/tắt model thật.
- Có unit test cho các module lõi: query parser/scoring, ATS, submission validator, model adapters mock.
- Có checklist integration test bản đầu tiên.
- Mỗi thành viên sở hữu một module rõ ràng để tối ưu ở milestone sau.

## 2. Phân Vai Nhóm 5 Người

| Thành viên | Vai trò chính | Module sở hữu | Kết quả cần bàn giao |
| --- | --- | --- | --- |
| Người 1 | Backend/API & Database Lead | FastAPI, PostgreSQL schema, API contracts, submission export | API ổn định, DB seed mock, export CSV/ZIP, docs API. |
| Người 2 | Retrieval & Ranking Lead | Query parser, hybrid scoring, retrieval profiles | Search KIS/QA cơ bản, score breakdown, cấu hình trọng số. |
| Người 3 | Temporal/TRAKE Lead | Adaptive Temporal Search, event sequence, temporal tests | TRAKE search trả sequence đúng thứ tự, unit test ATS. |
| Người 4 | Model & Data Pipeline Lead | Model registry, model adapters, ingest pipeline, Elasticsearch/Milvus adapters | Hướng dẫn cài model, mock/real adapter skeleton, ingest stages. |
| Người 5 | Frontend & QA/Test Lead | React workspace, result grid, context viewer, selected tray, test plan | UI dùng được, quy trình test end-to-end, bug report template. |

Quy tắc làm việc:

- Mỗi người chỉ sửa sâu trong module mình sở hữu.
- Khi cần đổi API/schema dùng chung, tạo issue ngắn hoặc ghi vào `docs/tasks/milestone1.md` phần "Open Questions".
- Không tối ưu model/ranking quá sớm; milestone này ưu tiên contract rõ và test được.
- Mọi model thật đặt trong `models/`, không commit weight.
- Mọi output sinh ra đặt trong `data/submissions`, `data/processed`, hoặc `data/raw`, không commit.

## 3. Phân Rã Công Việc Theo Module

### 3.1 Người 1 - Backend/API & Database

Mục tiêu: backend có API contract sạch để frontend và các module retrieval gọi ổn định.

Tasks:

- Kiểm tra và hoàn thiện các endpoint:
  - `GET /healthz`
  - `GET /api/datasets`
  - `GET /api/models`
  - `POST /api/retrieval/search`
  - `POST /api/retrieval/qa`
  - `POST /api/retrieval/trake`
  - `POST /api/submissions`
  - `POST /api/submissions/{id}/items`
  - `POST /api/submissions/{id}/validate`
  - `POST /api/submissions/{id}/export`
  - `GET /api/submissions/{id}/download`
- Rà lại SQLAlchemy models:
  - `Dataset`
  - `Video`
  - `Frame`
  - `Event`
  - `FrameAnnotation`
  - `QueryRun`
  - `RetrievalResult`
  - `Submission`
  - `SubmissionItem`
- Đảm bảo seed mock data tạo đủ:
  - ít nhất 3 video mock,
  - ít nhất 1 query KIS đúng,
  - ít nhất 1 query QA có `answer_hint`,
  - ít nhất 1 query TRAKE có 3 frame theo thứ tự.
- Bổ sung response/error rõ ràng cho API.
- Viết unit test cho submission validator.

Cách làm:

1. Chạy backend bằng Docker hoặc SQLite local.
2. Dùng Swagger `http://localhost:8000/docs` gọi từng endpoint.
3. Ghi lại request/response mẫu vào README backend nếu thiếu.
4. Khi sửa schema, đảm bảo frontend không vỡ type.

Unit test cần có:

- KIS row hợp lệ: `video_code + 1 frame`.
- QA row hợp lệ: `video_code + 1 frame + answer <= 100 ký tự`.
- QA row lỗi khi answer > 100 ký tự.
- TRAKE row hợp lệ khi có nhiều frame.
- Lỗi khi `video_code` có `.mp4`.
- Lỗi khi query có hơn 100 dòng.

### 3.2 Người 2 - Retrieval & Ranking

Mục tiêu: retrieval KIS/QA có pipeline rõ, có score breakdown để milestone sau dễ tối ưu.

Tasks:

- Rà `apps/backend/app/modules/retrieval/service.py`.
- Tách rõ các bước:
  - normalize query,
  - query expansion,
  - frame ranking,
  - metadata scoring,
  - score fusion,
  - result persistence.
- Chuẩn hóa `score_breakdown` gồm:
  - `semantic_score`,
  - `metadata_score`,
  - `quality_score`,
  - sau này có thể thêm `rerank_score`.
- Rà `configs/retrieval_profiles.yaml`.
- Thêm profile thử nghiệm:
  - `semantic_fast`,
  - `metadata_heavy`,
  - `qa_audio_text`.
- Viết unit test cho:
  - token normalization,
  - overlap scoring,
  - ranking top result với mock query.

Cách làm:

1. Bắt đầu từ mock data trong `seed_mock_data()`.
2. Chạy query KIS mẫu:

```powershell
$dataset=(Invoke-RestMethod http://localhost:8000/api/datasets).datasets[0].id
$body=@{
  dataset_id=$dataset
  query_name="query-1-kis"
  query_type="KIS"
  query_text="royal decorative panel dragon cloud PHU XUAN GIA DINH"
  top_k=5
  profile="competition_default"
  options=@{
    use_query_expansion=$true
    use_metadata=$true
    delta_t_max_ms=180000
  }
} | ConvertTo-Json -Depth 6
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/retrieval/search -ContentType "application/json" -Body $body
```

3. Kiểm tra top 1 kỳ vọng là `L00_V000, 1234`.
4. Không hard-code theo query mẫu; nếu cần cải thiện, cải thiện scoring chung.

Unit test cần có:

- Query có uppercase/lowercase vẫn match.
- Query expansion không sinh trùng variant.
- Top result của KIS mock là frame `1234`.
- QA mock trả answer `Disney` cho query castle/company.

### 3.3 Người 3 - Temporal/TRAKE

Mục tiêu: ATS có thể nhận nhiều sub-events, dựng sequence đúng thứ tự frame, và có test riêng độc lập retrieval.

Tasks:

- Rà `apps/backend/app/modules/temporal/ats.py`.
- Làm rõ input/output:
  - `Candidate`
  - `TemporalSequence`
  - `adaptive_temporal_search()`
- Viết test cho các case:
  - sequence đúng thứ tự được nhận,
  - sequence ngược thứ tự bị loại,
  - sequence vượt `delta_frame_max` bị loại,
  - partial match vẫn hợp lệ khi đạt `min_match`,
  - nhiều video thì không trộn frame giữa video.
- Rà logic tách sub-events trong retrieval service.
- Đảm bảo result TRAKE có `sequence_frames` để export CSV.

Cách làm:

1. Test ATS bằng candidate giả, không phụ thuộc DB.
2. Sau khi unit test pass, chạy API `/api/retrieval/trake`.
3. Với mock query bicycle race, kỳ vọng sequence thuộc `L10_V001`.
4. Kiểm tra export CSV có dạng:

```csv
L10_V001,1200,1850,2100
```

Unit test cần có:

- `test_ats_keeps_chronological_order`.
- `test_ats_rejects_cross_video_sequence`.
- `test_ats_respects_delta_frame_max`.
- `test_ats_allows_partial_match_when_min_match_met`.
- `test_ats_ranks_higher_score_sequence_first`.

### 3.4 Người 4 - Model & Data Pipeline

Mục tiêu: nhóm có quy trình cài model thật, thay model, và mở rộng pipeline mà không phá API.

Tasks:

- Rà `configs/model_registry.yaml`.
- Rà `docs/model_pipeline_guide.md`.
- Tạo checklist cài model cho từng loại:
  - embedding,
  - OCR,
  - ASR,
  - VLM QA,
  - LLM query expansion.
- Rà adapter interfaces:
  - `TextImageEmbedder`
  - `QueryExpander`
  - `VisualQaModel`
- Rà Elasticsearch adapter:
  - `apps/backend/app/adapters/text_search/elasticsearch.py`
- Rà Milvus adapter:
  - `apps/backend/app/adapters/vector_db/milvus.py`
- Thiết kế pipeline stages thật nhưng chưa cần implement hết:
  - dataset scan,
  - video download/import,
  - video integrity check,
  - shot detection,
  - keyframe extraction,
  - frame deduplication,
  - OCR,
  - ASR,
  - object detection,
  - caption,
  - embedding,
  - event segmentation,
  - Milvus index,
  - Elasticsearch index.
- Thiết kế và chạy test ingest/preprocessing tối thiểu:
  - đọc `dataset_manifest`,
  - tạo dataset/video records,
  - tạo frame records,
  - tạo annotation records,
  - lưu artifact URI,
  - kiểm tra idempotency khi chạy ingest 2 lần.

Cách cài model giai đoạn đầu:

1. Tạo thư mục trong `models/`:

```text
models/
├── pe-core-bigg/
├── beit3/
├── paddle-vietocr/
├── whisperx/
└── qwen2.5-vl/
```

2. Không commit model weights.
3. Sửa `configs/model_registry.yaml`.
4. Bật model bằng `enabled: true`.
5. Tắt mock model tương ứng nếu adapter thật đã ổn.
6. Restart backend:

```powershell
docker compose restart backend
```

7. Re-run ingest/index khi model ảnh hưởng đến annotation hoặc embedding.

Nhiệm vụ tìm hiểu model:

| Nhóm model | Việc cần tìm hiểu | Output cần nộp |
| --- | --- | --- |
| Embedding | PE/OpenCLIP, BEiT-3, SigLIP, dim vector, batch size, GPU RAM | Ghi model chọn thử vào `model_registry.yaml`, kèm lý do. |
| OCR | PaddleOCR, VietOCR, correction tiếng Việt | Ghi pipeline OCR đề xuất và sample output. |
| ASR | WhisperX, ngôn ngữ Việt/Anh, timestamp alignment | Ghi cách cắt audio và map transcript về frame. |
| VLM QA | Qwen2.5-VL, LLaVA, OpenAI-compatible endpoint | Ghi prompt QA trả answer <= 100 ký tự. |
| LLM Expansion | Qwen/Mistral/GPT-compatible, JSON output | Ghi prompt query expansion + temporal event parsing. |

Unit test cần có:

- Mock embedder trả vector ổn định cho cùng input.
- Mock query expander không trả quá `max_variants`.
- Mock VLM QA trả answer <= 100 ký tự.
- Model registry đọc được YAML và list đúng model `enabled`.
- Dataset manifest parser đọc đúng video code, URI, FPS.
- Pipeline stage chạy đúng thứ tự.
- Ingest mock không tạo trùng `Dataset`, `Video`, `Frame` khi chạy lại cùng manifest.

#### 3.4.1 Test module tải video và preprocessing

Module ingest/preprocessing cần được test như một pipeline riêng, không trộn với retrieval. Mục tiêu của test là chứng minh rằng dữ liệu multimedia đi từ **manifest/video source → preprocessing artifacts → PostgreSQL → index-ready state**.

Luồng chuẩn cần test:

```text
DatasetManifest
  -> VideoImportStage
  -> VideoIntegrityStage
  -> ShotDetectionStage
  -> KeyframeExtractionStage
  -> DedupStage
  -> AnnotationStages
  -> EmbeddingStage
  -> EventSegmentationStage
  -> PostgreSQL upsert
  -> Milvus/Elasticsearch index jobs
```

Trong milestone 1, nếu chưa có video thật, dùng 2 chế độ:

| Chế độ | Mục đích | Cách làm |
| --- | --- | --- |
| `mock` | Test nhanh logic DB/pipeline không cần file video | Dùng manifest + frame giả, artifact URI dạng `mock://...`. |
| `sample-video` | Test preprocessing thật với file nhỏ | Dùng 1-2 video mp4 ngắn, extract frame bằng OpenCV/PyAV/ffmpeg. |

Dataset test đề xuất:

```text
data/test-fixtures/
├── videos/
│   ├── L00_V000.mp4
│   └── L10_V001.mp4
├── manifests/
│   └── sample_manifest.yaml
└── expected/
    ├── expected_frames.json
    └── expected_annotations.json
```

Không commit video lớn. Nếu cần commit fixture, chỉ dùng video rất nhỏ hoặc tạo video synthetic bằng script.

Test cases bắt buộc cho ingest/preprocessing:

| Test case | Mục tiêu | Kỳ vọng |
| --- | --- | --- |
| `test_manifest_parser_reads_videos` | Đọc manifest đúng | Trả đúng `video_code`, `uri`, `fps`, `duration_ms`. |
| `test_video_import_creates_dataset_and_videos` | Lưu DB cấp dataset/video | PostgreSQL có 1 dataset và N videos. |
| `test_preprocess_creates_frames` | Extract/preprocess tạo frame | Bảng `frames` có record, `frame_idx` là số nguyên, `image_uri` không rỗng. |
| `test_annotations_are_saved` | Lưu OCR/ASR/object/caption mock | Bảng `frame_annotations` có `kind`, `text_value`, `model_version`. |
| `test_events_are_created` | Event segmentation tối thiểu | Bảng `events` có representative frame. |
| `test_ingest_is_idempotent` | Chạy lại không nhân đôi dữ liệu | Số `videos/frames` không tăng bất thường khi ingest cùng manifest lần 2. |
| `test_failed_video_goes_to_failed_state` | Video lỗi không làm hỏng cả job | Job có error report, video lỗi được đánh dấu failed/quarantine. |
| `test_index_build_records_created` | Sẵn sàng cho Milvus/Elasticsearch | Bảng `index_builds` có record `MILVUS` và `ELASTICSEARCH` ở mock mode. |

Cách chạy test ingest bằng API:

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

Cách kiểm tra DB sau ingest:

```powershell
docker compose exec postgres psql -U multimodal -d multimodal -c "select name, version, status from datasets;"
docker compose exec postgres psql -U multimodal -d multimodal -c "select video_code, fps, duration_ms from videos;"
docker compose exec postgres psql -U multimodal -d multimodal -c "select count(*) from frames;"
docker compose exec postgres psql -U multimodal -d multimodal -c "select kind, count(*) from frame_annotations group by kind;"
docker compose exec postgres psql -U multimodal -d multimodal -c "select index_type, status, stats from index_builds;"
```

Tiêu chí pass cho ingest/preprocessing milestone 1:

- Một manifest hợp lệ tạo được dataset/video/frame/annotation/event records.
- Chạy lại cùng manifest không tạo trùng video/frame.
- Mọi frame có `video_id`, `frame_idx`, `timestamp_ms`, `image_uri`.
- Mọi annotation có `kind`, `model_version`.
- Có `index_builds` cho vector/text index, kể cả khi vẫn là mock.
- API trả job status rõ ràng.

### 3.5 Người 5 - Frontend & QA/Test

Mục tiêu: UI chạy được cho luồng thi thật và nhóm có quy trình test rõ ràng.

Tasks frontend:

- Rà `apps/web/src/App.tsx`.
- Đảm bảo UI có:
  - dataset selector,
  - mode selector `KIS/QA/TRAKE`,
  - query text,
  - top-k input,
  - toggle `MV`,
  - toggle `Meta`,
  - result grid,
  - score breakdown,
  - context viewer,
  - selected tray,
  - export ZIP.
- Rà `apps/web/src/api/client.ts`.
- Đảm bảo frontend không gọi fetch trực tiếp ngoài `client.ts`.
- Thêm UX nhỏ nếu kịp:
  - loading state rõ hơn,
  - error message gọn,
  - nút clear selected,
  - QA answer edit trước export.

Tasks QA/test:

- Viết checklist manual test.
- Chạy test mỗi khi merge module lớn.
- Ghi bug theo format bên dưới.

Manual test checklist:

1. Mở `http://localhost:5173`.
2. Dataset hiển thị `mock-aic-2026`.
3. KIS query chạy được, top result là `L00_V000`.
4. QA query chạy được, answer gợi ý là `Disney`.
5. TRAKE query chạy được, result có sequence frames.
6. Bấm result, context viewer hiển thị frame trước/sau.
7. Bấm `Select`, row xuất hiện trong selected tray.
8. Export ZIP thành công.
9. Download ZIP mở ra có folder `submission/`.
10. CSV không có header.

Bug report template:

```text
Title:
Module:
Steps to reproduce:
Expected:
Actual:
Screenshot/log:
Severity: low | medium | high | blocker
Owner:
```

## 4. Quy Trình Cài Đặt Và Chạy Lần Đầu

### 4.1 Chạy bằng Docker

```powershell
Copy-Item .env.example .env
docker compose up --build
```

URL:

- Frontend: `http://localhost:5173`
- API docs: `http://localhost:8000/docs`
- Backend health: `http://localhost:8000/healthz`
- Elasticsearch: `http://localhost:9200`
- MinIO console: `http://localhost:9001`
- Milvus: `localhost:19530`

### 4.2 Chạy backend local bằng SQLite

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

### 4.3 Chạy frontend local

```powershell
cd apps\web
npm install
$env:VITE_API_BASE_URL="http://localhost:8000"
npm run dev
```

## 5. Quy Trình Testing Bản Đầu Tiên

### 5.1 Test levels

| Level | Người phụ trách chính | Mục tiêu |
| --- | --- | --- |
| Unit test | Từng owner module | Bắt lỗi logic nhỏ trong scoring, ATS, validator, adapter. |
| Integration test | Người 1 + Người 4 | API + DB + config + mock pipeline chạy cùng nhau. |
| Frontend smoke test | Người 5 | UI gọi API và export được ZIP. |
| End-to-end test | Cả nhóm | Một query pack đi từ search đến submission ZIP. |

### 5.2 Unit test cần viết trong milestone 1

Backend nên tạo thư mục:

```text
apps/backend/tests/
├── test_submission_validator.py
├── test_retrieval_scoring.py
├── test_temporal_ats.py
├── test_ingest_pipeline.py
├── test_preprocessing_persistence.py
├── test_model_registry.py
└── test_mock_adapters.py
```

Cài test dependencies nếu chưa có:

```text
pytest
pytest-cov
```

Thêm vào `apps/backend/requirements.txt` hoặc tạo `requirements-dev.txt`.

Chạy test:

```powershell
cd apps\backend
pytest -q
```

Chạy coverage:

```powershell
pytest --cov=app --cov-report=term-missing
```

Mục tiêu coverage milestone 1:

- Không cần cao toàn repo.
- Các module lõi phải có test:
  - `submissions/service.py`: >= 70%
  - `temporal/ats.py`: >= 80%
  - `ingest/pipeline.py`: >= 70%
  - `retrieval/service.py`: test các hàm scoring quan trọng
  - `model_runtime/mock.py`: test deterministic behavior

### 5.3 Integration test ingest/preprocessing

Integration test này kiểm tra pipeline từ manifest đến database. Người 1 và Người 4 cùng phụ trách.

Kịch bản `mock`:

1. Start stack:

```powershell
docker compose up -d --build
```

2. Gọi ingest job:

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

3. Kiểm tra DB:

```powershell
docker compose exec postgres psql -U multimodal -d multimodal -c "select count(*) from datasets;"
docker compose exec postgres psql -U multimodal -d multimodal -c "select count(*) from videos;"
docker compose exec postgres psql -U multimodal -d multimodal -c "select count(*) from frames;"
docker compose exec postgres psql -U multimodal -d multimodal -c "select count(*) from frame_annotations;"
docker compose exec postgres psql -U multimodal -d multimodal -c "select count(*) from events;"
```

4. Chạy lại ingest cùng manifest.
5. Kiểm tra số lượng video/frame không bị nhân đôi.

Kịch bản `sample-video` khi đã có video nhỏ:

1. Đặt video vào `data/test-fixtures/videos/`.
2. Tạo manifest `data/test-fixtures/manifests/sample_manifest.yaml`.
3. Chạy ingest mode `sample-video`.
4. Kiểm tra artifact:
   - keyframe file tồn tại,
   - thumbnail file tồn tại,
   - audio transcript mock/thật được lưu,
   - DB frame count > 0.

Ghi chú: hiện code scaffold mới có mock ingest placeholder. Nhiệm vụ milestone 1 là viết test trước và implement dần cho đến khi các test trên pass.

### 5.4 Integration test retrieval tối thiểu

Chạy stack:

```powershell
docker compose up -d --build
```

Kiểm tra:

```powershell
Invoke-RestMethod http://localhost:8000/healthz
Invoke-RestMethod http://localhost:8000/api/datasets
Invoke-RestMethod http://localhost:9200
```

Chạy search smoke:

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
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/retrieval/search -ContentType "application/json" -Body $body
```

Kỳ vọng:

- API trả `results`.
- Top 1 là `L00_V000`.
- `frame_idx` là `1234`.
- Có `score_breakdown`.

### 5.5 End-to-end test

Luồng:

1. Mở web.
2. Chạy KIS, QA, TRAKE.
3. Select ít nhất 1 result mỗi query.
4. Export ZIP.
5. Download ZIP.
6. Giải nén kiểm tra:

```text
submission/
├── query-1-kis.csv
├── query-2-qa.csv
└── query-3-trake.csv
```

7. Mở CSV bằng text editor:
   - không header,
   - delimiter là dấu phẩy,
   - video code không có `.mp4`,
   - frame là số nguyên,
   - QA answer không quá 100 ký tự.

## 6. Quy Trình Làm Việc Nhóm

### 6.1 Branching

Đề xuất branch:

```text
main
feature/backend-api
feature/retrieval-ranking
feature/temporal-ats
feature/model-pipeline
feature/frontend-workspace
feature/tests
```

### 6.2 Commit message

Format:

```text
<module>: <short action>
```

Ví dụ:

```text
retrieval: add metadata-heavy profile
temporal: test partial match in ATS
frontend: add selected tray clear action
models: document qwen vl adapter config
```

### 6.3 Pull request checklist

Trước khi merge:

- Code chạy được local hoặc Docker.
- Không commit `.env`.
- Không commit model weights.
- Không commit raw video/dataset thật.
- Có test hoặc manual test note.
- API change đã cập nhật `apps/backend/README.md` hoặc `apps/web/src/types.ts`.
- Nếu đổi model/pipeline, cập nhật `docs/model_pipeline_guide.md`.

## 7. Definition Of Done Cho Milestone 1

Milestone 1 hoàn thành khi:

- Cả 5 người chạy được project trên máy mình.
- Backend API docs mở được.
- Ingest/preprocessing mock tạo được dataset/video/frame/annotation/event records trong PostgreSQL.
- Có test idempotency ingest: chạy lại không nhân đôi dữ liệu.
- Frontend search được KIS/QA/TRAKE.
- Export được ZIP đúng format.
- Có ít nhất 10 unit tests backend cho các module lõi.
- Có manual E2E test report.
- `docs/model_pipeline_guide.md` đủ để người 4 cắm model thật đầu tiên.
- Mỗi thành viên có module riêng để tối ưu ở milestone sau.

## 8. Chuẩn Bị Cho Milestone 2

Milestone 2 sẽ tập trung tối ưu. Để chuẩn bị, mỗi người cần ghi lại vấn đề của module mình:

| Người | Câu hỏi cần trả lời trước milestone 2 |
| --- | --- |
| Người 1 | DB schema/API có nghẽn gì khi ingest dataset thật không? |
| Người 2 | Trọng số nào ảnh hưởng nhất đến KIS/QA? Cần reranker không? |
| Người 3 | ATS cần tuning `delta_t_max_ms` và `min_match_ratio` như thế nào? |
| Người 4 | Model nào chạy được trên GPU hiện có? Batch size bao nhiêu? |
| Người 5 | UI thao tác nào làm chậm người thi nhất? Cần phím tắt nào? |

## 9. Open Questions

- Dataset thật sẽ có format metadata như thế nào?
- Có cần hỗ trợ video playback thật ở milestone 1 không, hay frame context mock là đủ?
- GPU của nhóm có bao nhiêu VRAM để chọn model embedding/VLM phù hợp?
- Nhóm muốn dùng Elasticsearch full-text ngay hay giữ DB/mock retrieval cho đến khi ingest thật?
- Có cần tạo `requirements-dev.txt` riêng cho test/lint không?

# E2E Manual Test Runbook (FastAPI `/docs`, Cloud DB/Index)

Tài liệu này dùng để test end-to-end trực tiếp bằng Swagger UI tại `http://localhost:8000/docs` với stack thật:
- Supabase (PostgreSQL)
- Zilliz/Milvus
- Elasticsearch

## 1. Preconditions

- Đã ingest data mới nhất theo M2.
- `.env` phải có:
  - `MOCK_MODE=false`
  - `DATABASE_URL` trỏ Supabase
  - `MILVUS_URI` + `MILVUS_TOKEN` trỏ Zilliz
  - `ELASTICSEARCH_URL` trỏ ES đang chạy

Khuyến nghị khi chạy local ngoài Docker network:
- Đặt `ELASTICSEARCH_URL=http://localhost:9200` để tránh warning resolve host `elasticsearch`.

## 2. Start Backend

Từ `apps/backend/`:

```bash
../../venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Check nhanh:
- `GET http://127.0.0.1:8000/healthz` phải trả `200`.
- `mock_mode` trong body phải là `false`.

## 3. Open Swagger UI

- Mở `http://localhost:8000/docs`
- Dùng 3 endpoint:
  - `POST /api/retrieval/search`
  - `POST /api/retrieval/qa`
  - `POST /api/retrieval/trake`

## 4. Payloads Copy-Paste

### 4.1 KIS / Search

```json
{
  "query_type": "KIS",
  "query_name": "e2e-kis-docs",
  "query_text": "Đoạn video nói về hiến máu. Trong video, có xuất hiện nhiều người nằm hiến máu và một tấm phồng nền lớn phía sau nhắc đến thông điệp Một giọt máu cho đi, Một cuộc đời ở lại",
  "top_k": 5,
  "profile": "competition_default",
  "options": {
    "use_query_expansion": false
  }
}
```

Pass criteria:
- HTTP `200`
- `results.length > 0`
- `score_breakdown` có đủ: `semantic_score`, `text_score`, `quality_score`, `final_score`
- Để xem các hình ảnh trả về: nó sẽ hiện `/api/media/frames/L30_V083_F006970/thumbnail`, lúc này muốn xem ảnh thì mở tab mới và truy cập:
`http://127.0.0.1:8000/api/media/frames/L30_V083_F006970/thumbnail` để xem ảnh trả về.

### 4.2 QA

```json
{
  "query_type": "QA",
  "query_name": "e2e-qa-docs",
  "query_text": "Đoạn video về một nam sinh kể về câu chuyện của mình. Đoạn video ghi lại hình ảnh một người đàn ông mặc áo sơ mi trắng, đang ngồi trong căn phòng nhỏ. Ở phía sau người đàn ông có cầu thang và rèm cửa. Hỏi rèm cửa màu gì?",
  "top_k": 3,
  "profile": "competition_default",
  "options": {
    "use_query_expansion": false
  }
}
```

Pass criteria:
- HTTP `200`
- `results.length > 0`
- `answer` top-1 có giá trị và `<= 100` ký tự
- Để xem các hình ảnh trả về: nó sẽ hiện `/api/media/frames/L30_V083_F006970/thumbnail`, lúc này muốn xem ảnh thì mở tab mới và truy cập:
`http://127.0.0.1:8000/api/media/frames/L30_V083_F006970/thumbnail` để xem ảnh trả về.

### 4.3 TRAKE

```json
{
  "query_type": "TRAKE",
  "query_name": "e2e-trake-docs",
  "query_text": "Người đi bộ sau đó đi xe máy",
  "top_k": 3,
  "profile": "competition_default",
  "options": {
    "use_query_expansion": false,
    "min_match": 1,
    "delta_t_max_ms": 120000
  }
}
```

Pass criteria:
- HTTP `200`
- `results.length > 0`
- `sequence_frames` tồn tại
- `score_breakdown` có `temporal_score`, `matched_events`, `expected_events`, `ordering`
- Để xem các hình ảnh trả về: nó sẽ hiện `/api/media/frames/L30_V083_F006970/thumbnail`, lúc này muốn xem ảnh thì mở tab mới và truy cập:
`http://127.0.0.1:8000/api/media/frames/L30_V083_F006970/thumbnail` để xem ảnh trả về.

### 4.4 Filter Regression (M4)

```json
{
  "query_type": "KIS",
  "query_name": "e2e-filter-docs",
  "query_text": "nguoi ao do",
  "top_k": 10,
  "profile": "competition_default",
  "options": {
    "use_query_expansion": false,
    "video_codes": ["L30_V001"],
    "time_range_start_seconds": 0,
    "time_range_end_seconds": 60,
    "objects": ["person"],
    "debug_filters": true
  }
}
```

Pass criteria:
- HTTP `200`
- Tất cả kết quả thuộc `video_code=L30_V001`
- `score_breakdown.filter_debug` có thông tin filter

## 5. Negative Cases

### 5.1 Blank Query

```json
{
  "query_type": "KIS",
  "query_name": "e2e-neg-blank-docs",
  "query_text": "   ",
  "top_k": 1,
  "profile": "competition_default",
  "options": {
    "use_query_expansion": false
  }
}
```

Expect:
- HTTP `400`
- detail: `query_text must not be empty`

### 5.2 Invalid Time Range

```json
{
  "query_type": "KIS",
  "query_name": "e2e-neg-time-docs",
  "query_text": "nguoi ao do",
  "top_k": 1,
  "profile": "competition_default",
  "options": {
    "use_query_expansion": false,
    "time_range_start_seconds": 5,
    "time_range_end_seconds": 1
  }
}
```

Expect:
- HTTP `400`
- detail: `options.time_range_start_seconds must be <= options.time_range_end_seconds`

## 6. Persistence Check (Supabase)

Sau khi test xong, chạy SQL:

```sql
select count(*) from query_runs;
select count(*) from retrieval_results;
```

Check rank uniqueness cho 1 `query_run_id` vừa chạy:

```sql
select rank, count(*)
from retrieval_results
where query_run_id = '<query_run_id>'
group by rank
having count(*) > 1;
```

Kết quả đúng: query trên trả `0 rows`.

## 7. Gate 0 Reference Counts (`l30-demo`)

Đối chiếu nhanh sau ingest sạch:
- `videos = 96`
- `shots = 4426`
- `keyframes = 13278`
- `frame_annotations = 13278`
- `events = 4419`
- `event_keyframes = 13278`
- ES `keyframe_annotations = 13278`
- Milvus:
  - `keyframe_embeddings = 13278`
  - `event_embeddings = 4419`

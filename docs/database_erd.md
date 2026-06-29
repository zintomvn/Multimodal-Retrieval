# PostgreSQL ERD (Target Demo-Aligned)

Tài liệu này mô tả ERD mục tiêu cho backend retrieval, đồng bộ với schema contract:
- `docs/database_schema_demo_v1.md`

Mục đích:
- chốt quan hệ bảng cho Module 1/2;
- đảm bảo import dữ liệu từ `demo/` không mất thông tin;
- giữ contract ổn định để map Elasticsearch và Milvus.

Nguồn dữ liệu ingest mặc định:
- `demo/per_video_summary.csv`
- `demo/shot_segments.csv`
- `demo/annotations/<video_id>/annotations.jsonl`
- `demo/features/map-keyframes/<video_id>.csv`
- `demo/features/map-event/<video_id>.csv`
- `demo/features/vit-ViT-B-32-laion2b_s34b_b79k/<video_id>.npy`
- `demo/features/events/<video_id>.npy`

Nguồn legacy (không dùng làm source-of-truth): `demo/annotations.jsonl`, `demo/Event Embedding/*`.

## 1. ERD Tổng Quan

```mermaid
erDiagram
    DATASETS ||--o{ VIDEOS : contains
    VIDEOS ||--o{ SHOTS : has
    SHOTS ||--o{ KEYFRAMES : has
    KEYFRAMES ||--|| FRAME_ANNOTATIONS : annotates

    VIDEOS ||--o{ EVENTS : has
    EVENTS ||--o{ EVENT_KEYFRAMES : expands_to
    KEYFRAMES ||--o{ EVENT_KEYFRAMES : participates_in
    KEYFRAMES ||--o{ EVENTS : representative_for

    DATASETS ||--o{ INDEX_BUILDS : builds
    DATASETS ||--o{ QUERY_RUNS : runs
    QUERY_RUNS ||--o{ RETRIEVAL_RESULTS : returns
    VIDEOS ||--o{ RETRIEVAL_RESULTS : result_video
    KEYFRAMES ||--o{ RETRIEVAL_RESULTS : result_keyframe
    EVENTS ||--o{ RETRIEVAL_RESULTS : result_event

    DATASETS ||--o{ SUBMISSIONS : owns
    SUBMISSIONS ||--o{ SUBMISSION_ITEMS : contains

    DATASETS {
        uuid dataset_id PK
        text dataset_code UK
        text name
        text version
        text root_uri
        text status
        timestamptz created_at
    }

    VIDEOS {
        text video_id PK
        uuid dataset_id FK
        text video_name
        float duration_seconds
        float fps
        int num_keyframes
        text embedding_shape
        text source_video_path
    }

    SHOTS {
        text shot_id PK
        text video_id FK
        int shot_index
        int start_frame
        int end_frame
        float start_seconds
        float end_seconds
        float boundary_threshold
    }

    KEYFRAMES {
        text keyframe_id PK
        text video_id FK
        text shot_id FK
        int frame_idx
        float frame_seconds
        text frame_type
        int map_n
        int embedding_index_0
        text image_rel_path
        text image_storage_key
        text image_url
        bool is_media_present
    }

    FRAME_ANNOTATIONS {
        text keyframe_id PK,FK
        text caption
        jsonb ocr_texts
        jsonb detected_objects
        jsonb object_counts
        jsonb detections
        text annotation_version
    }

    EVENTS {
        text event_id PK
        text video_id FK
        int embedding_index_0
        float start_seconds
        float end_seconds
        int start_frame
        int end_frame
        text representative_keyframe_id FK
        int n_shots
        int n_keyframes
    }

    EVENT_KEYFRAMES {
        text event_id PK,FK
        int seq_no PK
        text keyframe_id FK
        int keyframe_embedding_index_0
    }

    INDEX_BUILDS {
        uuid index_build_id PK
        uuid dataset_id FK
        text index_type
        text target_name
        text model_name
        text model_version
        text status
        jsonb stats
    }

    QUERY_RUNS {
        uuid query_run_id PK
        uuid dataset_id FK
        text query_name
        text query_type
        text query_text
        jsonb normalized_query
        jsonb options
        text status
    }

    RETRIEVAL_RESULTS {
        uuid result_id PK
        uuid query_run_id FK
        int rank
        text video_id FK
        text keyframe_id FK
        text event_id FK
        text answer
        float score
        jsonb score_breakdown
        jsonb sequence_frames
    }

    SUBMISSIONS {
        uuid submission_id PK
        uuid dataset_id FK
        text name
        text zip_uri
        text status
        jsonb validation_report
    }

    SUBMISSION_ITEMS {
        uuid submission_item_id PK
        uuid submission_id FK
        text query_name
        text query_type
        int rank
        text video_code
        jsonb frame_indices
        text answer
    }
```

## 2. Luồng dữ liệu chính

```text
datasets
  -> videos
      -> shots
          -> keyframes
              -> frame_annotations
      -> events
          -> event_keyframes
```

- `keyframes` là điểm giao giữa relational metadata và vector/text indexes.
- `event_keyframes` giúp truy vấn TRAKE theo chuỗi theo thứ tự mà không cần parse chuỗi text.

## 3. Constraint quan trọng

- `videos`: unique theo business id `video_id`.
- `shots`: unique `(video_id, shot_index)`.
- `keyframes`: unique `(video_id, frame_idx)`.
- `events`: unique `(video_id, embedding_index_0)`.
- `event_keyframes`: PK `(event_id, seq_no)` va unique `(event_id, keyframe_id)`.
- `retrieval_results`: unique `(query_run_id, rank)`.
- `submission_items`: unique `(submission_id, query_name, rank)`.

## 4. Index ưu tiên cao

```sql
create index if not exists idx_videos_dataset on videos(dataset_id);
create index if not exists idx_shots_video_time on shots(video_id, start_seconds, end_seconds);
create index if not exists idx_keyframes_video_time on keyframes(video_id, frame_seconds);
create index if not exists idx_keyframes_shot on keyframes(shot_id, frame_idx);
create index if not exists idx_events_video_time on events(video_id, start_seconds, end_seconds);
create index if not exists idx_event_keyframes_keyframe on event_keyframes(keyframe_id);
```

## 5. Boundary với Elasticsearch và Milvus

- PostgreSQL: source-of-truth.
- Elasticsearch: text retrieval index (caption, OCR, objects), id tham chiếu `keyframe_id`.
- Milvus: ANN vector index, id tham chiếu `keyframe_id` và `event_id`.
  - keyframe vectors: từ `features/vit-.../*.npy` theo mapping `map-keyframes`.
  - event vectors: từ `features/events/*.npy` theo mapping `map-event.event_embedding_index`.

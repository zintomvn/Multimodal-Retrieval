# FE Vector Embedding v1 Report

## Mục tiêu

Tạo một notebook Kaggle có thể lấy keyframe từ Google Cloud Storage, trích xuất vector
embedding bằng OpenCLIP, và sinh artifact sẵn sàng cho hai hướng nạp:

- PostgreSQL/Supabase theo schema backend trong `apps/backend/app/db/models.py`.
- Zilliz/Milvus theo adapter `VectorSearchClient.upsert(collection, vectors)`.

## Căn cứ từ repo

- `scripts/loaders/README.MD`: chuẩn GCS `processed/keyframes/...`,
  `processed/keyframes_manifests/.../shot_segments.csv`, batch L21-L30, log tiến độ.
- `notebooks/data processing/processors/get-features.ipynb`: style Config/Data/Model/helper/run
  và dùng GPU cho các model feature.
- `notebooks/data processing/processors/event-embeddings.ipynb`: thuật toán gom event dựa trên
  khoảng cách thời gian và cosine similarity.
- `apps/backend/app/db/models.py`: schema `datasets`, `videos`, `shots`, `keyframes`,
  `events`, `event_keyframes`, `model_registry`, `index_builds`.
- `apps/backend/app/adapters/vector_db/milvus.py`: collection dùng primary field `id`,
  vector field `vector`, dynamic metadata và metric COSINE.

## Thiết kế notebook

Notebook có các phần:

- `Config`: toàn bộ tham số có thể chỉnh trong một cell.
- `Data`: đọc `shot_segments.csv` từ GCS, fallback list frame trực tiếp từ GCS.
- `Model`: OpenCLIP image encoder, dùng CUDA, fp16 autocast, DataLoader workers, pinned memory.
- `Artifacts`: ghi NPY/CSV/JSONL, report và upload artifact lên GCS.
- `Event Embeddings`: port logic event grouping từ notebook cũ.
- `Run Helpers`: dry run, smoke test, demo 1 batch, guarded full run.

## Output contract

Notebook ghi local run folder:

```text
/kaggle/working/feature_extraction_runs/<run_id>/
```

Các artifact chính:

- `features/vit-ViT-B-32-laion2b_s34b_b79k/<video_id>.npy`
- `features/map-keyframes/<video_id>.csv`
- `features/events/<video_id>.npy`
- `features/map-event/<video_id>.csv`
- `postgres/*.csv`
- `zilliz/keyframe_embeddings/*.jsonl`
- `zilliz/event_embeddings/*.jsonl`
- `artifacts/summary.json`
- `artifacts/batch_metrics.csv`
- `run.log`

## Tối ưu Kaggle

- GPU: `EMBED_BATCH_SIZE`, fp16/bf16 autocast, TF32, cuDNN benchmark.
- CPU/network: `DOWNLOAD_WORKERS` cho GCS download, `IMAGE_DECODE_WORKERS` cho image decode.
- Disk: xóa frame local sau mỗi batch với `DELETE_LOCAL_AFTER_BATCH = True`.
- GCS upload: upload artifact bằng `UPLOAD_WORKERS`, log phần trăm theo file.

## Các mode

- Dry run: list frame metadata, không download/model/upload.
- Smoke test: chạy ít frame, mặc định không upload.
- Demo 1 batch: chạy một batch thật, upload artifact nếu bật.
- Full run: có guard `CONFIRM_FULL_RUN = "RUN_FULL_DATASET"`.

## Lưu ý vận hành

- Nên truyền `shot_segments.csv` để metadata thời gian/shot chính xác.
- Nếu không truyền, notebook vẫn list ảnh từ GCS nhưng `frame_seconds` chỉ được ước lượng bằng `DEFAULT_FPS`.
- Vector OpenCLIP được L2 normalize, nên Zilliz/Milvus nên dùng COSINE hoặc IP nhất quán.
- JSONL vector có thể lớn; có thể tắt `WRITE_ZILLIZ_JSONL` nếu chỉ muốn dùng `.npy + map-keyframes`.

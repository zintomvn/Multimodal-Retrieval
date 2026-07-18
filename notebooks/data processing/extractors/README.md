# Kaggle Feature Extraction Notebooks

These notebooks read keyframes from Google Cloud Storage (GCS), run one extraction task per notebook, and write JSON output back to GCS. Supabase/Postgres formatting is intentionally left for a later import function.

## Files

| Notebook | Task | Main model | Output |
| --- | --- | --- | --- |
| `fe-captioning-v1.ipynb` | Captioning | `Salesforce/blip-image-captioning-base` | Frame annotation records, `kind=CAPTION` |
| `fe-ocr-v1.ipynb` | OCR | PaddleOCR `PP-OCRv5_mobile_det` + VietOCR `vgg_seq2seq` | Frame annotation records, `kind=OCR` |
| `fe-object-detection-v1.ipynb` | Object detection | Ultralytics `yolo12n.pt` | Frame annotation records, `kind=OBJECTS` |
| `fe-vector-embedding-v1.ipynb` | Vector embedding | OpenCLIP `ViT-B-32` / `laion2b_s34b_b79k` | Frame annotation records, `kind=VECTOR_EMBEDDING` |
| `fe-scene-detection-v1.ipynb` | Scene/event segmentation | Threshold grouping over vector embeddings | Event records for `events` and `event_keyframes` |

## Expected GCS input

Frame notebooks expect images under:

```text
gs://YOUR_GCS_BUCKET/processed/keyframes/<video_id>/*.jpg
```

Partition-style folders are also supported:

```text
gs://YOUR_GCS_BUCKET/processed/keyframes/video_id=L21_V001/*.jpg
```

Frame filenames must contain a token like `f000123`, for example `shot_0007_middle_f000583.jpg`. If you have `shot_segments.csv`, set `SHOT_SEGMENTS_URI` so records include `shot_id`, `frame_sec`, `fps`, and `frame_type`.

## How to run on Kaggle

1. Upload your GCP service-account JSON as a private Kaggle Dataset.
2. Enable GPU for captioning, OCR, object detection, and vector embedding.
3. Edit the `Parameters` cell in each notebook:
   - `GCS_BUCKET`
   - `FRAME_PREFIX`
   - `OUTPUT_PREFIX`
   - `GCS_SERVICE_ACCOUNT_JSON`
   - `SHOT_SEGMENTS_URI` if available
   - `VIDEO_IDS`, `MAX_FRAMES`, `DEFAULT_FPS`, `PIPELINE_BATCH_SIZE`, `DOWNLOAD_WORKERS`, and task-specific batch sizes.
4. Run `Install dependencies`.
5. Run `Dry run`.
6. Run `Demo one batch`.
7. Set `RUN_FULL = True` in the full-run cell when the demo output looks correct.

## Recommended order

1. `fe-vector-embedding-v1.ipynb`
2. `fe-scene-detection-v1.ipynb` with `VECTOR_MANIFEST_URI` or `VECTOR_INPUT_PREFIX` pointing to the vector run
3. `fe-captioning-v1.ipynb`
4. `fe-ocr-v1.ipynb`
5. `fe-object-detection-v1.ipynb`

Captioning, OCR, and object detection can run independently because they write different output prefixes.

## Output layout

Frame notebooks write JSON array shards:

```text
gs://YOUR_GCS_BUCKET/<OUTPUT_PREFIX>/<run_id>/annotations/part-000001.json
gs://YOUR_GCS_BUCKET/<OUTPUT_PREFIX>/<run_id>/annotations/part-000002.json
gs://YOUR_GCS_BUCKET/<OUTPUT_PREFIX>/<run_id>/manifest.json
```

Scene detection writes:

```text
gs://YOUR_GCS_BUCKET/<OUTPUT_PREFIX>/<run_id>/events/part-000001.json
gs://YOUR_GCS_BUCKET/<OUTPUT_PREFIX>/<run_id>/manifest.json
```

The manifest stores totals, uploaded parts, batch/video metrics, elapsed time, average throughput, model metadata, and Supabase schema hints.

## Backend schema alignment

Frame annotation records include:

| JSON field | Backend target |
| --- | --- |
| `dataset.code`, `dataset.version` | `datasets.dataset_code`, `datasets.version` |
| `video_id` | `videos.video_id` |
| `keyframe_id` | `keyframes.keyframe_id`, `frame_annotations.frame_id` |
| `frame_idx`, `frame_seconds`, `timestamp_ms` | `keyframes` columns |
| `image_uri`, `image_storage_key`, `image_url` | `keyframes` media columns |
| `annotation.kind` | `frame_annotations.kind` |
| `annotation.text_value` | `frame_annotations.text_value` |
| `annotation.json_value` | `frame_annotations.json_value` |
| `annotation.model_version` | `frame_annotations.model_version` |

Scene records include `event_id`, `video_id`, `event_order`, `start_seconds`, `end_seconds`, `start_frame_idx`, `end_frame_idx`, `representative_keyframe_id`, `keyframe_ids`, and `event_embedding`.

## Logging

Every frame notebook logs each processed batch:

```text
[captioning] batch 12/480 | frames=32 processed=384/15360 (2.50%) | download=1.20s inference=5.80s upload=0.40s batch=7.60s | speed=4.21 frames/s | upload=gs://...
```

Tune these first:

| Parameter | Meaning |
| --- | --- |
| `PIPELINE_BATCH_SIZE` | Frames downloaded and processed per outer batch |
| `DOWNLOAD_WORKERS` | CPU threads for GCS downloads |
| `DEFAULT_FPS` | Fallback FPS used to estimate `frame_seconds` when `shot_segments.csv` is absent |
| `OUTPUT_SHARD_SIZE` | Records per uploaded JSON part |
| `CAPTION_BATCH_SIZE`, `YOLO_BATCH_SIZE`, `EMBED_BATCH_SIZE` | GPU inference batch sizes |
| `IMAGE_DECODE_WORKERS` | CPU workers for image decoding in embedding |
| `DELETE_LOCAL_AFTER_BATCH` | Prevents Kaggle disk from filling during long runs |

## Report

Implemented:

- One notebook per extraction task.
- A dedicated `Parameters` cell.
- Dry run, demo one batch, and guarded full run.
- Batch progress with percent complete, download/inference/upload time, batch time, and frames/sec.
- JSON part files uploaded to GCS before Supabase formatting.
- Function docstrings throughout the helper/model/run code.
- Markdown notes before every code cell.
- GPU use for captioning, OCR recognition, YOLO, and OpenCLIP when CUDA is available; CPU workers for download and image decode.

Important: `fe-scene-detection-v1.ipynb` consumes vector JSON output from `fe-vector-embedding-v1.ipynb`.

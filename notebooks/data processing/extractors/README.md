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

## Notebook outputs

All notebooks write a `manifest.json` plus sharded JSON array files. The manifest is the source of truth for a later merge job because it records `run_id`, input prefix, model metadata, parameters, timing, totals, and every uploaded part URI.

| Notebook | GCS parts | Record type | Important fields |
| --- | --- | --- | --- |
| `fe-captioning-v1.ipynb` | `annotations/part-*.json` | One frame annotation per keyframe | `keyframe_id`, `video_id`, `frame_idx`, `frame_seconds`, `image_uri`, `annotation.kind=CAPTION`, `annotation.caption`, `annotation.text_value`, `annotation.model_version` |
| `fe-ocr-v1.ipynb` | `annotations/part-*.json` | One OCR annotation per keyframe | `annotation.kind=OCR`, `annotation.ocr_texts`, `annotation.text_value`, `annotation.json_value.regions[*].bbox_xyxy` |
| `fe-object-detection-v1.ipynb` | `annotations/part-*.json` | One object annotation per keyframe | `annotation.kind=OBJECTS`, `annotation.detected_objects`, `annotation.object_counts`, `annotation.detections[*].bbox_xyxy`, `annotation.detections[*].confidence` |
| `fe-vector-embedding-v1.ipynb` | `annotations/part-*.json` | One vector annotation per keyframe | `annotation.kind=VECTOR_EMBEDDING`, `annotation.embedding`, `annotation.embedding_dim`, `annotation.l2_normalized`, `embedding_index_0` |
| `fe-scene-detection-v1.ipynb` | `events/part-*.json` | One event/scene record per detected event | `event_id`, `video_id`, `event_order`, `start_seconds`, `end_seconds`, `start_frame_idx`, `end_frame_idx`, `representative_keyframe_id`, `keyframe_ids`, `event_embedding` |

The frame notebooks intentionally write separate task outputs. Do not expect one final `annotations.json` from Kaggle yet; the later merge/import step should create the final Supabase and Zilliz payloads from these manifests.

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

## Kaggle Secrets

The notebooks read GCS credentials from Kaggle Secrets. Create these two secrets before running any notebook:

| Secret name | Value |
| --- | --- |
| `GCS_BUCKET` | Your bucket name only, for example `aic_ai_2026` |
| `GCS_SERVICE_ACCOUNT_JSON` | The full Google Cloud service-account JSON string with permission to read/write the bucket |

In Kaggle, open **Add-ons -> Secrets**, add both secrets, and enable notebook access to them. Do not paste service-account JSON directly into notebook cells.

The `Parameters` cell reads those values like this:

```python
KAGGLE_SECRET_GCS_BUCKET = "GCS_BUCKET"
KAGGLE_SECRET_GCS_SERVICE_ACCOUNT_JSON = "GCS_SERVICE_ACCOUNT_JSON"

GCS_BUCKET = read_kaggle_secret(KAGGLE_SECRET_GCS_BUCKET)
GCS_SERVICE_ACCOUNT_JSON = read_kaggle_secret(KAGGLE_SECRET_GCS_SERVICE_ACCOUNT_JSON)
```

For local testing outside Kaggle, the same code falls back to environment variables named `GCS_BUCKET` and `GCS_SERVICE_ACCOUNT_JSON`. If `GCS_SERVICE_ACCOUNT_JSON` is a file path instead of raw JSON, the notebooks also accept it.

## How to run on Kaggle

1. Create Kaggle Secrets named `GCS_BUCKET` and `GCS_SERVICE_ACCOUNT_JSON`.
2. Enable GPU for captioning, OCR, object detection, and vector embedding.
3. Edit the `Parameters` cell in each notebook:
   - `FRAME_PREFIX`
   - `OUTPUT_PREFIX`
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

## Merge for Supabase and Zilliz later

Use the five `manifest.json` files as inputs to a separate merge/import script. A good pattern is:

1. Read each manifest and download every URI in `output_parts`.
2. Build a staging dictionary keyed by `keyframe_id`.
3. Merge caption, OCR, object, and vector records into that staging dictionary.
4. Read scene/event records separately and build event rows plus event-keyframe join rows.
5. Write/import two families of outputs:
   - Supabase/Postgres metadata and annotations.
   - Zilliz vector payloads.

Recommended staging shape:

```python
by_frame[keyframe_id] = {
    "frame": {
        "keyframe_id": "...",
        "video_id": "...",
        "frame_idx": 123,
        "frame_seconds": 4.92,
        "timestamp_ms": 4920,
        "image_uri": "gs://...",
        "image_storage_key": "processed/keyframes/...",
        "image_url": "https://...",
    },
    "caption": "...",
    "ocr_texts": ["..."],
    "objects": ["person", "car"],
    "object_counts": {"person": 2},
    "detections": [...],
    "embedding": [...],
    "embedding_model_version": "openclip-vit-b-32-laion2b_s34b_b79k",
}
```

### Supabase import

For Supabase/Postgres, keep vectors out of `frame_annotations` unless you explicitly want to store a copy there. Supabase should mainly receive relational metadata and text/object annotations:

| Target table | Build from | Upsert key |
| --- | --- | --- |
| `datasets` | Manifest dataset block | `dataset_code` |
| `videos` | Distinct `video_id` values from frame records | `video_id` or `(dataset_id, video_code)` |
| `keyframes` | Common frame fields from any frame notebook | `keyframe_id` |
| `frame_annotations` | Merged caption/OCR/object payloads | Deterministic annotation id |
| `events` | Scene detection event records | `event_id` |
| `event_keyframes` | `event_id` + each `keyframe_id` in `keyframe_ids` | `(event_id, seq_no)` |

Two annotation strategies are reasonable:

| Strategy | When to use | Shape |
| --- | --- | --- |
| Separate rows per task | Best for auditing each extractor independently | One row per `(keyframe_id, kind, model_version)` where `kind` is `CAPTION`, `OCR`, or `OBJECTS` |
| One fused `MULTIMODAL` row | Best if the backend retrieval path expects one combined annotation row | One row per `keyframe_id` with `caption`, `ocr_texts`, `detected_objects`, `object_counts`, `detections`, and combined `text_value` |

For the current backend schema, the fused row can map like this:

```python
frame_annotation = {
    "frame_id": keyframe_id,
    "kind": "MULTIMODAL",
    "text_value": " ".join([caption, " ".join(ocr_texts), " ".join(objects)]).strip(),
    "json_value": {
        "caption_manifest": "...",
        "ocr_manifest": "...",
        "object_manifest": "...",
        "image_uri": frame["image_uri"],
    },
    "caption": caption,
    "ocr_texts": ocr_texts,
    "detected_objects": objects,
    "object_counts": object_counts,
    "detections": detections,
    "model_version": "merged-features-v1",
    "annotation_version": "merged-features-v1",
}
```

Use deterministic IDs so the import can be re-run safely, for example `uuid5("frame-annotation:{keyframe_id}:multimodal:v1")`.

### Zilliz import

For Zilliz, import only vector records:

| Collection | Source | Vector field | Suggested scalar fields |
| --- | --- | --- | --- |
| `keyframe_embeddings` | `fe-vector-embedding-v1` records | `annotation.embedding` | `keyframe_id`, `video_id`, `frame_idx`, `timestamp_ms`, `image_uri`, `model_version`, `embedding_index_0` |
| `event_embeddings` or similar | `fe-scene-detection-v1` records | `event_embedding` | `event_id`, `video_id`, `event_order`, `start_seconds`, `end_seconds`, `representative_keyframe_id`, `segmentation_version` |

The merge job should keep the same vector normalization and metric assumptions as the notebook: OpenCLIP vectors are L2-normalized, so cosine/IP search is appropriate. Store `model_version` on every Zilliz row so you can rebuild or compare indexes later.

Minimal merge/import flow:

```text
caption manifest  \
ocr manifest       > merge by keyframe_id -> Supabase datasets/videos/keyframes/frame_annotations
objects manifest  /

vector manifest -----> Zilliz keyframe_embeddings

scene manifest ------> Supabase events/event_keyframes
                  \-> Zilliz event_embeddings, optional
```

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

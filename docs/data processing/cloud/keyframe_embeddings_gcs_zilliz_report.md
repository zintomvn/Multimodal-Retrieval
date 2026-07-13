# Keyframe Embeddings From GCS To Zilliz

## Purpose

`notebooks/data processing/keyframe-embeddings-gcs-zilliz.ipynb` is a Kaggle notebook that reads keyframe images from a public Google Cloud Storage bucket, embeds them with OpenCLIP, writes the same per-video artifacts as the existing local notebook, and upserts the vectors directly to Zilliz Cloud.

Zilliz secrets are intentionally not stored in the notebook. Configure them in Kaggle Secrets.

## Existing Notebook I/O

The source notebook is `notebooks/data processing/keyframe-embeddings.ipynb`.

Input:

- Kaggle-mounted AutoShot output at `/kaggle/input/datasets/khngxuninh/autoshot-output`.
- Optional `autoshot_output.zip`.
- Required `shot_segments.csv`.
- Required frame images under `frames/<video_id>/<image_file>.jpg`.

Output:

```text
/kaggle/working/embedding_per_video/
  per_video_summary.csv
  model_info.json
  features/
    vit-ViT-B-32-laion2b_s34b_b79k/
      <video_id>.npy
    map-keyframes/
      <video_id>.csv
```

The `.npy` file contains one `float32` L2-normalized vector per saved keyframe. The map CSV contains:

| Column | Meaning |
| --- | --- |
| `n` | 1-based row number in the per-video `.npy` file. |
| `pts_time` | Frame timestamp in seconds when available. |
| `fps` | Source video FPS when available. |
| `frame_idx` | Original frame index in the source video. |

## GCS Input Format

The bucket is public readable. The actual processed keyframe layout is:

```text
gs://<GCS_BUCKET>/processed/keyframes/dataset=ai_challenge_2025/batch=<BATCH>/profile=autoshot_v1/video_id=<VIDEO_ID>/<image_file>.jpg
```

Examples:

```text
gs://aic_ai_2026/processed/keyframes/dataset=ai_challenge_2025/batch=L21/profile=autoshot_v1/video_id=L21_V001/shot_0000_first_f000000.jpg
gs://aic_ai_2026/processed/keyframes/dataset=ai_challenge_2025/batch=L21/profile=autoshot_v1/video_id=L21_V001/shot_0000_middle_f000026.jpg
gs://aic_ai_2026/processed/keyframes/dataset=ai_challenge_2025/batch=L21/profile=autoshot_v1/video_id=L21_V001/shot_0000_last_f000053.jpg
```

The notebook uses this L21 prefix by default because L30 currently has raw videos but no processed keyframe image prefix:

```text
processed/keyframes/dataset=ai_challenge_2025/batch=L21/profile=autoshot_v1
```

When L30 keyframes are generated, change `RunConfig.gcs_keyframe_prefix` to:

```text
processed/keyframes/dataset=ai_challenge_2025/batch=L30/profile=autoshot_v1
```

The parser extracts:

- `video_id` from the path segment `video_id=<VIDEO_ID>`.
- `frame_idx` from the filename pattern `_f000123`.

The notebook accepts `.jpg`, `.jpeg`, `.png`, and `.webp`, and ignores non-image files such as manifests.

If `shot_segments.csv` is not present in the same GCS prefix, the notebook infers `frame_idx` from the filename pattern `f000123`. In that fallback path, `pts_time` and `fps` are written as `NaN`.

## Kaggle Secrets

Required Kaggle Secrets / env vars:

| Secret | Required | Description |
| --- | --- | --- |
| `GCS_BUCKET` | Yes | GCS bucket name, for example `aic_ai_2026`. |
| `MILVUS_URI` | Yes | Zilliz Cloud endpoint. |
| `MILVUS_TOKEN` | Yes | Zilliz Cloud token. |
| `GCS_PUBLIC_URL` | No | Public URL base, default `https://storage.googleapis.com/<GCS_BUCKET>`. |

The notebook uses `storage.Client.create_anonymous_client()` and does not need `GOOGLE_APPLICATION_CREDENTIALS_JSON`, `GCP_SERVICE_ACCOUNT_JSON`, or `GCS_KEYFRAME_PREFIX` as secrets. The L21 prefix is in `RunConfig.gcs_keyframe_prefix`.

The notebook also checks environment variables with the same names, which is useful outside Kaggle.

## Zilliz Collection Format

Collection:

```text
keyframe_embeddings
```

Vector field:

```text
vector: FLOAT_VECTOR dim=512 metric=COSINE
```

Primary key:

```text
id = keyframe_id = <video_id>_F<frame_idx:06d>
```

Metadata mirrors the backend DB model context from `apps/backend/app/db/models.py`:

| Metadata | Backend model field |
| --- | --- |
| `keyframe_id` | `Frame.keyframe_id` |
| `video_id` | `Frame.video_id` |
| `frame_idx` | `Frame.frame_idx` |
| `frame_seconds` | `Frame.frame_seconds` |
| `timestamp_ms` | `Frame.timestamp_ms` |
| `image_storage_key` | `Frame.image_storage_key` |
| `image_uri` | `Frame.image_uri` |
| `image_url` | `Frame.image_url` |
| `model_version` | `Video.embedding_shape` / index metadata context |

The backend retrieval adapter requests these fields during search, so the notebook upsert payload includes them as dynamic fields.

## Run Checklist

1. Add the Kaggle Secrets above.
2. Enable internet in the Kaggle notebook so dependencies and cloud endpoints work.
3. Run the install/import/setup cells.
4. Run the scan cell first with:

```python
MAX_VIDEOS = 1
MAX_IMAGES_PER_VIDEO = 5
```

5. Run the smoke embed/upsert cells and verify:

- Embedding shape is `(N, 512)`.
- Vector norms are approximately `1.0`.
- Zilliz collection stats can be read.

6. Set both limits to `None` for the full run.

## Important Notes

- The notebook only upserts vectors to Zilliz. PostgreSQL rows for `datasets`, `videos`, `keyframes`, and media URLs still need backend ingest if the app database is not already populated.
- Re-running the notebook is idempotent for Zilliz because it uses deterministic keyframe IDs as primary keys.
- Do not paste Zilliz keys directly into the notebook. Use Kaggle Secrets.

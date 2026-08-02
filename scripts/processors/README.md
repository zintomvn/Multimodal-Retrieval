# GCS Frame Annotation Processor

This processor reads keyframes from Google Cloud Storage, extracts multimodal annotations in batches, and writes the results to:

- Supabase PostgreSQL: dataset, video, keyframe, and annotation metadata.
- Zilliz/Milvus: image embedding vectors.
- Elasticsearch: text-search documents built from captions, OCR text, and object labels.

Secrets and cloud endpoints stay in `.env`. Model names, batch sizes, cache paths, prompts, generation settings, and sink names stay in `scripts/processors/configs/pipeline/processor.yaml`.

## Source Layout

Root `scripts/processors/` is intentionally kept for runnable entry points and operator-facing files:

```text
scripts/processors/
├── processor_cli.py
├── extract_gcs_annotations.py
├── extract_gcs_asr.py
├── download_models.py
├── configs/
├── logs/
├── tests/
└── src/
```

Helper modules live under `scripts/processors/src/`:

- `src/artifact_io.py`, `src/manifest.py`, `src/checkpoint_store.py`: artifact storage, manifest, and checkpoint primitives.
- `src/shard_runner.py`, `src/ingest_artifacts.py`, `src/reconcile_run.py`: shard processing, artifact import, and reconciliation logic.
- `src/role_planner.py`, `src/notebook_role_runner.py`, `src/notebook_cells.py`, `src/processor_doctor.py`: notebook planning, generated cells, execution wrappers, and runtime checks.
- `src/extractors/` and `src/cloud_sinks/`: model extractors and database/search/vector sink adapters.

## Kaggle/Colab L21 Ingest Runbook

This is the recommended competition path. Kaggle/Colab workers write feature artifacts and checkpoints to GCS first. A single trusted machine then imports those artifacts into Supabase PostgreSQL, Zilliz/Milvus, and Elasticsearch.

### Runtime Roles

| Runtime                      | First target         | Processor profile                  | Stage              | Output                                                   |
| ---------------------------- | -------------------- | ---------------------------------- | ------------------ | -------------------------------------------------------- |
| Local or Kaggle control cell | L21 manifest         | none                               | discovery          | `manifests/.../shards/*.jsonl`                           |
| Kaggle GPU 1                 | L21 primary visual   | `visual_primary_pe_core`           | `visual_primary`   | PE-Core JSONL artifact                                   |
| Kaggle GPU 2                 | L21 secondary visual | `visual_secondary_openclip_vith14` | `visual_secondary` | OpenCLIP ViT-H/14 JSONL artifact                         |
| Kaggle GPU 3                 | L21 objects          | `objects_only`                     | `objects`          | YOLO object JSONL artifact                               |
| Colab GPU 1                  | L21 OCR              | `craft_easyocr_only`               | `ocr`              | CRAFT/EasyOCR JSONL artifact                             |
| Colab GPU 2                  | L21 caption          | `shot_context_caption_qwen_vl`     | `caption`          | Qwen-VL/Gemini-style shot-context caption JSONL artifact |
| Colab GPU 3                  | L21 ASR              | `extract_gcs_asr.py`               | `asr`              | faster-whisper JSONL artifact                            |
| Local/backend machine        | L21 DB import        | importer                           | import             | Supabase PostgreSQL / Zilliz-Milvus / Elasticsearch      |

### L21 Machine Map

Use this as the concrete first-pass assignment. Each runtime handles one shard at a time and resumes from checkpoint if it stops.

| Machine  | Batch | Stage            | Profile                            | Notes                                               |
| -------- | ----- | ---------------- | ---------------------------------- | --------------------------------------------------- |
| Kaggle-1 | L21   | visual_primary   | `visual_primary_pe_core`           | Primary PE-Core embedding, highest priority first.  |
| Kaggle-2 | L21   | visual_secondary | `visual_secondary_openclip_vith14` | Secondary embedding branch for fusion and fallback. |
| Kaggle-3 | L21   | objects          | `objects_only`                     | Object labels, counts, detections.                  |
| Colab-1  | L21   | ocr              | `craft_easyocr_only`               | OCR lines for QA and signage.                       |
| Colab-2  | L21   | caption          | `shot_context_caption_qwen_vl`     | Shot-context captions for metadata search.          |
| Colab-3  | L21   | asr              | `extract_gcs_asr.py`               | Speech segments from raw video.                     |

After L21 is green, duplicate the same pattern for L22, L23, L24, L25, L27, L28, L29, and L30. Keep `L26` out of MVP runs unless you explicitly want a stress test.

### Shared Environment

Set these as Kaggle Secrets, Colab environment variables, or local `.env` values:

```env
GCS_BUCKET=aic_ai_2026
GCS_CREDENTIALS_FILE=/path/to/service-account.json
GCS_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'  # optional Kaggle/Colab secret alternative
GCS_PUBLIC_URL=https://storage.googleapis.com/aic_ai_2026
DATABASE_URL=postgresql+psycopg://...
MILVUS_URI=https://...
MILVUS_TOKEN=...
ELASTICSEARCH_URL=http://...
```

Kaggle/Colab feature workers only need `GCS_BUCKET` and GCS credentials. Use either `GCS_CREDENTIALS_FILE` or a secret named `GCS_SERVICE_ACCOUNT_JSON`; generated notebook cells materialize that JSON to `/tmp/gcs-sa.json` automatically. Keep `DATABASE_URL`, `MILVUS_URI`, and `ELASTICSEARCH_URL` on the final importer machine unless you intentionally allow notebook workers to access databases.

### Drive-backed source

If the organizing team uploads new data to Drive, register it through `configs/data_ingestion_sources.yaml` as `source_type: drive` and mirror or normalize it into the same GCS layout before running the processor CLI. The processors stay GCS-first; Drive is an upstream source, not a direct worker input.

## Running instruction

### 1. Install on Kaggle or Colab

```bash
cd /kaggle/working/Multimodal-Retrieval  # or /content/Multimodal-Retrieval
python -m pip install -r scripts/processors/requirements.txt
```

For OCR workers:

```bash
python -m pip install -r scripts/processors/requirements-ocr.txt
```

For ASR workers:

```bash
python -m pip install -r scripts/processors/requirements-asr.txt
```

For Qwen-VL/Gemini shot-context caption workers, expose an OpenAI-compatible VLM endpoint:

```bash
export VLM_BASE_URL=http://localhost:8001/v1
export VLM_API_KEY=
```

Use profile `shot_context_caption_gemini` instead when your Gemini gateway exposes an OpenAI-compatible endpoint through `GEMINI_OPENAI_BASE_URL` and `GEMINI_API_KEY`. If no VLM endpoint is available and the runtime has enough GPU/RAM headroom, use BLIP-2 through `caption_only` or `blip2_caption_verification`.

Run doctor before spending GPU time:

```bash
python scripts/processors/processor_cli.py --profile smoke doctor --runtime kaggle
python scripts/processors/processor_cli.py --profile shot_context_caption_qwen_vl doctor --runtime colab --features caption
python scripts/processors/processor_cli.py --profile craft_easyocr_only doctor --runtime colab --features ocr
python scripts/processors/processor_cli.py --profile full_text_features doctor --runtime importer
```

Use `--check-gcs --bucket "$GCS_BUCKET" --gcs-prefix processed/keyframes/dataset=ai_challenge_2025/batch=L21/profile=autoshot_v1` when the runtime has GCS credentials and you want to verify cloud access.
Omit `--features` to check the full default stack for a runtime. Use a feature filter for single-purpose notebooks so a caption-only Colab does not fail because OCR/ASR packages are not installed.
For OCR/ASR Colab workers, doctor will fail on Python `3.13+`; use Python `3.11` or `3.12` for that environment.
For the importer runtime, doctor fails if `DATABASE_URL` is still the SQLite fallback. Local Milvus/Elasticsearch endpoints are allowed but reported with warnings so you can distinguish local smoke tests from the Supabase/Zilliz/Elasticsearch import target.
`doctor` exits non-zero when the report has `ok: false`, so notebook shell cells and CI can stop immediately instead of relying on manual JSON inspection.

### 2. Create L21 Manifest and Shards

Run once from a control runtime:

```bash
RUN_ID=mvp_l21_$(date -u +%Y%m%d_%H%M%S)

python scripts/processors/processor_cli.py \
  --profile smoke \
  discover-gcs-keyframes \
  --bucket "$GCS_BUCKET" \
  --dataset-id ai_challenge_2025 \
  --batches L21 \
  --frame-profile autoshot_v1 \
  --shot-segments-path <SHOT_SEGMENTS_URI> \
  --run-id "$RUN_ID" \
  --frames-per-shard 128
```

The command prints `manifest_uri`, `summary_uri`, `shard_root`, and a list of shard JSONL files. Use those shard URIs in worker commands.
If the batch already has a known `shot_segments.csv` path, pass it to enrich `frame_seconds` and `shot_id` metadata. If not, discovery still works from GCS frame paths alone.

### 2.5. Render Kaggle/Colab Assignment Plan

Generate a machine-specific plan from `configs/pipeline/notebook_roles.yaml` and `configs/runtime/checkpoint_policy.yaml`:

```bash
python scripts/processors/processor_cli.py \
  --profile smoke \
  plan-notebook-run \
  --run-id "$RUN_ID" \
  --batches L21 \
  --manifest-summary-uri <SUMMARY_URI_FROM_DISCOVERY>
```

The JSON output contains `machine_roles[*].command_template` and `machine_roles[*].shards_to_run`. For frame stages, replace `<SHARD_URI>` with one shard from that role's list. ASR uses `raw_video_prefix` instead of frame shards.

### 2.6. Render or Execute One Notebook Role

Inside a Kaggle/Colab notebook, use `run-notebook-role` to avoid manually editing the command template. By default it only prints the exact command and the matching doctor command:

```bash
python scripts/processors/processor_cli.py \
  --profile smoke \
  run-notebook-role \
  --role-id kaggle_l21_primary_visual \
  --run-id "$RUN_ID" \
  --manifest-summary-uri <SUMMARY_URI_FROM_DISCOVERY> \
  --shard-index 0
```

When the rendered command looks right, run the same command with `--doctor-first --execute`:

```bash
python scripts/processors/processor_cli.py \
  --profile smoke \
  run-notebook-role \
  --role-id kaggle_l21_primary_visual \
  --run-id "$RUN_ID" \
  --manifest-summary-uri <SUMMARY_URI_FROM_DISCOVERY> \
  --shard-index 0 \
  --doctor-first \
  --execute
```

Use the role id for the notebook you are on: `kaggle_l21_primary_visual`, `kaggle_l21_secondary_visual`, `kaggle_l21_objects`, `colab_l21_ocr`, `colab_l21_caption`, or `colab_l21_asr`.
With `--doctor-first --execute`, the runner stops before model work if doctor fails. If a shard command fails while running multiple shards, later shard commands are reported under `skipped_commands`.

To generate copy-ready cells for a Kaggle or Colab notebook, render the same role as notebook cells:

```bash
python scripts/processors/processor_cli.py \
  --profile smoke \
  render-notebook-cells \
  --role-id kaggle_l21_primary_visual \
  --run-id "$RUN_ID" \
  --manifest-summary-uri <SUMMARY_URI_FROM_DISCOVERY> \
  --shard-index 0
```

The JSON output includes a `markdown` field with setup, environment, doctor, run, and resume cells. Use the same command with `colab_l21_ocr`, `colab_l21_caption`, or `colab_l21_asr` to create the Colab cells for those machines.

To write one markdown file per selected Kaggle/Colab machine, export a notebook kit:

```bash
python scripts/processors/processor_cli.py \
  --profile smoke \
  render-notebook-kit \
  --run-id "$RUN_ID" \
  --manifest-summary-uri <SUMMARY_URI_FROM_DISCOVERY> \
  --output-dir scripts/processors/notebook_kits/run_id=${RUN_ID}
```

The kit contains `README.md`, `plan.json`, and role files such as `kaggle_l21_primary_visual.md`, `colab_l21_ocr.md`, and `colab_l21_asr.md`. For multi-batch runs, pass `--batches L21,L22,L23,L24,L25,L27,L28,L29,L30`; ASR role files are generated per batch.

### 2.7. Local End-to-End Smoke

When you want a fast wiring check without Kaggle, Colab, or GCS, run the synthetic local smoke:

```bash
python scripts/processors/processor_cli.py \
  --profile smoke \
  smoke-local-flow \
  --run-id smoke_local
```

It builds a small manifest, writes fake feature and ASR artifacts, creates checkpoints, runs doctors, renders a notebook role, reconciles the run, and finishes with an import dry-run. This is the quickest way to check the whole processor stack before moving to cloud notebooks.
You can also pass `--batches L21,L22` to smoke a multi-batch scope locally.

### 2.8. Live GCS Smoke Before Full L21

After local smoke passes and credentials are installed on Kaggle/Colab, run a tiny cloud smoke before the full L21 job. This writes real artifacts/checkpoints to GCS, but limits the manifest to two frames:

```bash
RUN_ID=cloud_smoke_l21_$(date -u +%Y%m%d_%H%M%S)

python scripts/processors/processor_cli.py \
  --profile smoke \
  doctor \
  --runtime kaggle \
  --check-gcs \
  --bucket "$GCS_BUCKET" \
  --gcs-prefix processed/keyframes/dataset=ai_challenge_2025/batch=L21/profile=autoshot_v1

python scripts/processors/processor_cli.py \
  --profile smoke \
  discover-gcs-keyframes \
  --bucket "$GCS_BUCKET" \
  --dataset-id ai_challenge_2025 \
  --batches L21 \
  --frame-profile autoshot_v1 \
  --run-id "$RUN_ID" \
  --frames-per-shard 1 \
  --max-frames 2
```

Use the printed `summary_uri`, then execute exactly one shard on the target notebook:

```bash
python scripts/processors/processor_cli.py \
  --profile smoke \
  run-notebook-role \
  --role-id kaggle_l21_primary_visual \
  --run-id "$RUN_ID" \
  --manifest-summary-uri <SUMMARY_URI_FROM_DISCOVERY> \
  --shard-index 0 \
  --doctor-first \
  --execute
```

On the importer machine, reconcile and dry-run import before writing to Supabase/Zilliz/Elasticsearch:

```bash
python scripts/processors/processor_cli.py \
  --profile smoke \
  reconcile-run \
  --run-id "$RUN_ID" \
  --manifest-summary-uri <SUMMARY_URI_FROM_DISCOVERY> \
  --stages visual_primary

python scripts/processors/processor_cli.py \
  --profile visual_primary_pe_core \
  import-feature-artifacts \
  --artifact-uri gs://aic_ai_2026/features/dataset=ai_challenge_2025/frame_profile=autoshot_v1/feature_profile=mvp_v1/run_id=${RUN_ID}/stage=visual_primary \
  --dataset-code ai_challenge_2025_cloud_smoke_l21 \
  --dataset-name ai-challenge-2025-cloud-smoke-l21 \
  --dataset-version mvp_v1 \
  --milvus-collection keyframe_embeddings_pe_core_bigG_14_448 \
  --model-version pe-core-bigG-14-448 \
  --no-elasticsearch \
  --dry-run
```

If the notebook stops during this smoke, rerun the same `run-notebook-role` command. It should resume from the GCS checkpoint or skip the shard if `_SUCCESS.json` already exists.

### 3. Kaggle-1: Primary Visual Embedding

Use one shard per notebook session. If the runtime stops, rerun the same command; it resumes from `checkpoint.next_index`.

```bash
RUN_ID=<printed-run-id>
SHARD_URI=gs://aic_ai_2026/manifests/dataset=ai_challenge_2025/pipeline=feature_ingest/run_id=${RUN_ID}/shards/shard-00000.jsonl

python scripts/processors/processor_cli.py \
  --profile visual_primary_pe_core \
  run-feature-shard \
  --run-id "$RUN_ID" \
  --stage visual_primary \
  --features embedding \
  --shard-uri "$SHARD_URI" \
  --output-prefix gs://aic_ai_2026/features/dataset=ai_challenge_2025/frame_profile=autoshot_v1/feature_profile=mvp_v1 \
  --checkpoint-root gs://aic_ai_2026/checkpoints/pipeline=feature_ingest \
  --batch-size 16 \
  --download-workers 8 \
  --warmup-models
```

### 4. Kaggle-2: Secondary Visual Embedding

```bash
python scripts/processors/processor_cli.py \
  --profile visual_secondary_openclip_vith14 \
  run-feature-shard \
  --run-id "$RUN_ID" \
  --stage visual_secondary \
  --features embedding \
  --shard-uri "$SHARD_URI" \
  --output-prefix gs://aic_ai_2026/features/dataset=ai_challenge_2025/frame_profile=autoshot_v1/feature_profile=mvp_v1 \
  --checkpoint-root gs://aic_ai_2026/checkpoints/pipeline=feature_ingest \
  --batch-size 24 \
  --download-workers 8 \
  --warmup-models
```

### 5. Kaggle-3: Objects

```bash
python scripts/processors/processor_cli.py \
  --profile objects_only \
  run-feature-shard \
  --run-id "$RUN_ID" \
  --stage objects \
  --features objects \
  --shard-uri "$SHARD_URI" \
  --output-prefix gs://aic_ai_2026/features/dataset=ai_challenge_2025/frame_profile=autoshot_v1/feature_profile=mvp_v1 \
  --checkpoint-root gs://aic_ai_2026/checkpoints/pipeline=feature_ingest \
  --batch-size 32 \
  --download-workers 8 \
  --warmup-models
```

### 6. Colab-1: OCR

```bash
python scripts/processors/processor_cli.py \
  --profile craft_easyocr_only \
  run-feature-shard \
  --run-id "$RUN_ID" \
  --stage ocr \
  --features ocr \
  --shard-uri "$SHARD_URI" \
  --output-prefix gs://aic_ai_2026/features/dataset=ai_challenge_2025/frame_profile=autoshot_v1/feature_profile=mvp_v1 \
  --checkpoint-root gs://aic_ai_2026/checkpoints/pipeline=feature_ingest \
  --batch-size 8 \
  --download-workers 8 \
  --warmup-models
```

### 7. Colab-2: Caption

```bash
python scripts/processors/processor_cli.py \
  --profile shot_context_caption_qwen_vl \
  run-feature-shard \
  --run-id "$RUN_ID" \
  --stage caption \
  --features caption \
  --shard-uri "$SHARD_URI" \
  --output-prefix gs://aic_ai_2026/features/dataset=ai_challenge_2025/frame_profile=autoshot_v1/feature_profile=mvp_v1 \
  --checkpoint-root gs://aic_ai_2026/checkpoints/pipeline=feature_ingest \
  --batch-size 1 \
  --download-workers 4 \
  --warmup-models
```

### 8. Colab-3: ASR

```bash
python scripts/processors/extract_gcs_asr.py \
  --bucket "$GCS_BUCKET" \
  --gcs-prefix raw/source=kaggle/dataset=ai_challenge_2025/source_version=kaggle_current/batch=L21 \
  --run-id "$RUN_ID" \
  --output-prefix gs://aic_ai_2026/features/dataset=ai_challenge_2025/frame_profile=autoshot_v1/feature_profile=mvp_v1 \
  --checkpoint-root gs://aic_ai_2026/checkpoints/pipeline=feature_ingest \
  --model-size small \
  --language vi
```

ASR writes part files under `run_id=<RUN_ID>/stage=asr/shard_id=<normalized-gcs-prefix>/...`, so each batch or video prefix has its own checkpoint namespace and cannot overwrite another run by accident.
Feature-stage part files also include `worker_id` and `attempt_id` in the path so retry runs append new files instead of clobbering an earlier partial upload.

### 9. Import L21 Artifacts into Databases

Run this on the local/backend machine that has Supabase, Zilliz/Milvus, and Elasticsearch credentials.

Before importing, reconcile artifact and checkpoint counts:

```bash
python scripts/processors/processor_cli.py \
  --profile smoke \
  reconcile-run \
  --run-id "$RUN_ID" \
  --manifest-summary-uri <SUMMARY_URI_FROM_DISCOVERY>
```

The report should show `ok: true` for frame stages before the DB import. Use `--strict` when you also want ASR and every configured stage to have non-empty artifacts.
`reconcile-run` exits non-zero when `ok: false`, which makes it safe to use as the gate before database import.

Import PE-Core embeddings:

```bash
python scripts/processors/processor_cli.py \
  --profile visual_primary_pe_core \
  import-feature-artifacts \
  --artifact-uri gs://aic_ai_2026/features/dataset=ai_challenge_2025/frame_profile=autoshot_v1/feature_profile=mvp_v1/run_id=${RUN_ID}/stage=visual_primary \
  --dataset-code ai_challenge_2025_mvp_l21 \
  --dataset-name ai-challenge-2025-mvp-l21 \
  --dataset-version mvp_v1 \
  --milvus-collection keyframe_embeddings_pe_core_bigG_14_448 \
  --model-version pe-core-bigG-14-448 \
  --no-elasticsearch
```

Import secondary OpenCLIP embeddings:

```bash
python scripts/processors/processor_cli.py \
  --profile visual_secondary_openclip_vith14 \
  import-feature-artifacts \
  --artifact-uri gs://aic_ai_2026/features/dataset=ai_challenge_2025/frame_profile=autoshot_v1/feature_profile=mvp_v1/run_id=${RUN_ID}/stage=visual_secondary \
  --dataset-code ai_challenge_2025_mvp_l21 \
  --dataset-name ai-challenge-2025-mvp-l21 \
  --dataset-version mvp_v1 \
  --milvus-collection keyframe_embeddings_openclip_vith14 \
  --model-version openclip-vit-h-14-laion2b_s32b_b79k \
  --no-elasticsearch
```

Import text/object/ASR artifacts into Supabase and Elasticsearch:

```bash
python scripts/processors/processor_cli.py \
  --profile full_text_features \
  import-feature-artifacts \
  --artifact-uri gs://aic_ai_2026/features/dataset=ai_challenge_2025/frame_profile=autoshot_v1/feature_profile=mvp_v1/run_id=${RUN_ID}/stage=objects \
  --artifact-uri gs://aic_ai_2026/features/dataset=ai_challenge_2025/frame_profile=autoshot_v1/feature_profile=mvp_v1/run_id=${RUN_ID}/stage=ocr \
  --artifact-uri gs://aic_ai_2026/features/dataset=ai_challenge_2025/frame_profile=autoshot_v1/feature_profile=mvp_v1/run_id=${RUN_ID}/stage=caption \
  --artifact-uri gs://aic_ai_2026/features/dataset=ai_challenge_2025/frame_profile=autoshot_v1/feature_profile=mvp_v1/run_id=${RUN_ID}/stage=asr \
  --dataset-code ai_challenge_2025_mvp_l21 \
  --dataset-name ai-challenge-2025-mvp-l21 \
  --dataset-version mvp_v1 \
  --elasticsearch-index keyframe_annotations \
  --model-version text_features_v1 \
  --no-milvus
```

The import command also builds Vietnamese text embeddings from the merged caption/OCR/ASR text stream and stores them in Milvus collection `text_embeddings_vietnamese` by default. Use the same command after each new artifact batch if you want text-vector search updated.

### 10. Scale from L21 to More Batches

After L21 succeeds, create a new manifest with multiple batches:

```bash
python scripts/processors/processor_cli.py \
  --profile smoke \
  discover-gcs-keyframes \
  --bucket "$GCS_BUCKET" \
  --dataset-id ai_challenge_2025 \
  --batches L21,L22,L23,L24,L25,L27,L28,L29,L30 \
  --frame-profile autoshot_v1 \
  --run-id mvp_all_no_l26_$(date -u +%Y%m%d_%H%M%S) \
  --frames-per-shard 512
```

Then distribute shard URIs across Kaggle/Colab workers. Never include `L26` in MVP runs unless intentionally testing capacity.

Render a fresh assignment plan for that multi-batch run:

```bash
python scripts/processors/processor_cli.py \
  --profile smoke \
  plan-notebook-run \
  --run-id "$RUN_ID" \
  --batches L21,L22,L23,L24,L25,L27,L28,L29,L30 \
  --manifest-summary-uri <SUMMARY_URI_FROM_DISCOVERY>
```

For multi-batch ASR, `plan-notebook-run` expands the ASR role per batch, for example `colab_l22_asr`, `colab_l23_asr`, and so on. Use the same `run-notebook-role` helper with `--batches L21,L22,L23,L24,L25,L27,L28,L29,L30` and the expanded role id.
For multi-batch imports, the planner also changes `--dataset-code` away from `ai_challenge_2025_mvp_l21`, so prefer the generated import commands instead of reusing the L21 commands by hand.

### Checkpoint Rules

- Checkpoint root: `gs://aic_ai_2026/checkpoints/pipeline=feature_ingest/run_id=<RUN_ID>/...`
- A shard is done only when `_SUCCESS.json` exists.
- Rerunning the same command is safe: completed shards return `already_complete`; partial shards resume from `next_index`.
- If another notebook has claimed the lease, the stale worker stops before saving more checkpoint state.
- If a notebook hangs, wait for lease expiry or use a different `worker-id` after confirming the old runtime stopped.

## Current Installation Report

Installed package versions:

| Package                  | Version        |
| ------------------------ | -------------- |
| `torch`                  | `2.13.0+cu126` |
| `torchvision`            | `0.28.0+cu126` |
| `open_clip_torch`        | `3.3.0`        |
| `transformers`           | `5.14.1`       |
| `accelerate`             | `1.14.0`       |
| `ultralytics`            | `8.4.100`      |
| `opencv-python-headless` | `5.0.0.93`     |
| `google-cloud-storage`   | `3.13.0`       |
| `SQLAlchemy`             | `2.0.51`       |
| `psycopg`                | `3.3.4`        |
| `pymilvus`               | `3.0.0`        |
| `elasticsearch`          | `9.4.1`        |
| `paddleocr`              | `3.7.0`        |
| `paddlex`                | `3.7.2`        |
| `paddlepaddle`           | `3.3.1`        |
| `vietocr`                | `0.3.13`       |
| `setuptools`             | `80.10.2`      |

Verification completed:

- `python -m pip check`: passed.
- `python -m compileall -q scripts/processors`: passed.
- `python scripts/processors/download_models.py --profile visual_primary_pe_core --features embedding`: expected PE-Core warmup path.
- `python scripts/processors/extract_gcs_annotations.py --profile full --dry-run`: found `310,298` frames / `873` videos.

## Model Inventory

The processor visual embedding path is PE-Core primary plus OpenCLIP ViT-H/14 secondary. BLIP-2 is kept out of visual embedding and is available only through heavy caption/verification profiles.

| Feature                      | Model/config                                          | Profile                                      | Notes |
| ---------------------------- | ----------------------------------------------------- | -------------------------------------------- | ----- |
| Primary visual embedding     | OpenCLIP `hf-hub:timm/PE-Core-bigG-14-448`            | `visual_primary_pe_core`, `embedding_only`   | Default processor embedding target. |
| Secondary visual embedding   | OpenCLIP `ViT-H-14`, pretrained `laion2b_s32b_b79k`   | `visual_secondary_openclip_vith14`           | Fast secondary branch for fusion/fallback. |
| Heavy caption/verification   | `Salesforce/blip2-opt-2.7b`                           | `blip2_caption_verification`, `full_blip2`   | Use only when GPU/RAM is sufficient. |
| Shot-context caption         | Qwen/Gemini OpenAI-compatible VLM                     | `shot_context_caption_qwen_vl`, Gemini alt   | Main metadata caption workflow. |
| Object detection             | Ultralytics `yolo12n.pt`                              | `objects_only`                               | Object labels, counts, detections. |
| OCR                          | CRAFT/EasyOCR or PaddleOCR/VietOCR                    | `craft_easyocr_only`, `ocr_only`             | Text metadata for QA/search. |

### Visual Embedding Profiles

- Primary: `visual_primary_pe_core` uses OpenCLIP model name `hf-hub:timm/PE-Core-bigG-14-448` with empty `pretrained`.
- Secondary: `visual_secondary_openclip_vith14` uses OpenCLIP model name `ViT-H-14` and pretrained weights `laion2b_s32b_b79k`.
- Vector normalization: enabled (`models.embedding.l2_normalize: true`).
- Milvus/Zilliz metric recommendation: cosine.
- Default primary collection: `keyframe_embeddings_pe_core_bigG_14_448`.
- Secondary collection: `keyframe_embeddings_openclip_vith14`.

### Caption And Verification Profiles

- Main caption workflow: `shot_context_caption_qwen_vl` or `shot_context_caption_gemini`.
- Heavy local verification: `blip2_caption_verification`.
- Full heavy experiment: `full_blip2`.
- BLIP-2 model: `Salesforce/blip2-opt-2.7b`.
- Model class: `Blip2ForConditionalGeneration`.
- BLIP-2 is not used by `visual_primary_pe_core` or `visual_secondary_openclip_vith14`.

### YOLO Object Detection

- Model: `yolo12n.pt`
- Input inference size: `640`
- Confidence threshold: `0.25`
- Model summary from warmup: `159` layers, `2,590,824` parameters, `6.5 GFLOPs`
- Default object batch size: `8`
- Output per frame: `objects`, `object_counts`, and detailed `detections`

### OCR

- Detector: PaddleOCR/PaddleX `PP-OCRv5_mobile_det`
- Recognizer: VietOCR `vgg_seq2seq`
- Detector runtime: PaddlePaddle CPU on this Windows environment
- Recognizer runtime: PyTorch device from `models.device` (`auto` resolves to CUDA here)
- Detection limits: `detector_limit_side_len=960`, `detector_limit_type=max`
- Text line merge parameters: `line_y_threshold=35`, `line_x_gap_threshold=180`, `crop_padding=12`
- OCR dependencies are pinned for Python `<3.13` because PaddleOCR/PaddleX pulls Pillow `10.2.x`, which does not provide Windows wheels for Python `3.13`/`3.14`.

## Install And Warm Up

Use the Python `3.12` processor environment on `C:`:

```powershell
& "$env:USERPROFILE\.venvs\multimodal-processor-py312\Scripts\Activate.ps1"
```

Install CUDA PyTorch and processor dependencies:

```powershell
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
python -m pip install -r scripts/processors/requirements.txt
python -m pip install -r scripts/processors/requirements-ocr.txt
```

Download and warm up the primary PE-Core embedding stack:

```powershell
python scripts/processors/download_models.py --profile visual_primary_pe_core --features embedding
```

Warm up only selected features:

```powershell
python scripts/processors/download_models.py --profile visual_secondary_openclip_vith14 --features embedding
python scripts/processors/download_models.py --profile objects_only --features objects
```

Try the heavy BLIP-2 profile only when the runtime has enough GPU/RAM headroom:

```powershell
python scripts/processors/download_models.py --profile blip2_caption_verification --features caption
```

## Configuration

Main YAML file:

```text
scripts/processors/configs/pipeline/processor.yaml
```

Related YAML files:

- `configs/model_registry.yaml`: model registry for PE-Core, OpenCLIP ViT-H/14, Qwen/Gemini VLM, Vietnamese text embedding, and cross-encoder reranker.
- `configs/retrieval_profiles.yaml`: weighted fusion/RRF profile, visual model weights, query-type weights, and reranking blend.
- `scripts/processors/configs/pipeline/notebook_roles.yaml`: Kaggle/Colab role map and shard assignment defaults.
- `scripts/processors/configs/runtime/checkpoint_policy.yaml`: lease TTL, heartbeat, and resume policy.

The CLI still resolves the old root-level YAML names for compatibility, but new edits should go under `scripts/processors/configs/`.

Common tuning fields:

- `run.gcs_prefix`: GCS keyframe prefix, currently `processed/keyframes`.
- `run.batch_size`: outer pipeline batch size.
- `run.download_workers`: parallel GCS download workers.
- `run.log_file`: default processor log path, currently `scripts/processors/logs/processor.log`.
- `run.max_frames`: `0` means no frame limit.
- `models.cache_dir`: local model cache, currently on `C:`.
- `models.device`: `auto`, `cpu`, `cuda`, or `cuda:0`.
- `models.embedding.*`: OpenCLIP model, pretrained weights, precision, batch size.
- `models.caption.*`: caption provider, model class, model name, prompt, generation parameters, memory/offload settings.
- `models.ocr.*`: detector/recognizer model, OCR thresholds, crop padding, local recognizer weight path.
- `models.objects.*`: YOLO model, image size, confidence threshold, batch size.
- `sinks.milvus_collection`: default `keyframe_embeddings_pe_core_bigG_14_448`.
- `sinks.elasticsearch_index`: default `keyframe_annotations`.
- `sinks.fail_on_sink_error`: keep `false` to continue PG/Milvus when Elasticsearch is offline.

Environment variables expected in `.env`:

```env
DATABASE_URL=postgresql+psycopg://...
GCS_BUCKET=...
GCS_CREDENTIALS_FILE=...
GCS_SERVICE_ACCOUNT_JSON=...
GCS_PUBLIC_URL=...
MILVUS_URI=...
MILVUS_TOKEN=...
ELASTICSEARCH_URL=http://elasticsearch:9200
```

On a Windows host, the Elasticsearch client falls back from `http://elasticsearch:9200` to `http://localhost:9200`.

## Run

Dry-run the source inventory without models or sink writes:

```powershell
python scripts/processors/extract_gcs_annotations.py --profile full --dry-run
```

Run the full production profile across all frames:

```powershell
python scripts/processors/extract_gcs_annotations.py --profile full --max-frames 0 --warmup-models
```

Run vector indexing first if text/OCR/caption must be delayed:

```powershell
python scripts/processors/extract_gcs_annotations.py --profile embedding_only --max-frames 0 --warmup-models
```

Run only GCS frame-to-vector ingestion into Zilliz when Supabase PostgreSQL metadata has already been imported:

```powershell
python scripts/processors/extract_gcs_annotations.py `
  --profile zilliz_embedding_only `
  --gcs-prefix "processed/keyframes/dataset=ai_challenge_2025/batch=L22/profile=autoshot_v1" `
  --max-frames 100 `
  --batch-size 128 `
  --download-workers 10 `
  --gcs-timeout 60 `
  --log-file "scripts/processors/logs/vector_L22_smoke.log" `
  --annotations-jsonl "data/processor_vector_L22_smoke.jsonl" `
  --warmup-models
```

For the full `L22` through `L29` vector ingestion:

```powershell
$batches = "L22","L23","L24","L25","L26","L27","L28","L29"
foreach ($b in $batches) {
  python scripts/processors/extract_gcs_annotations.py `
    --profile zilliz_embedding_only `
    --gcs-prefix "processed/keyframes/dataset=ai_challenge_2025/batch=$b/profile=autoshot_v1" `
    --batch-size 128 `
    --download-workers 10 `
    --gcs-timeout 60 `
    --log-file "scripts/processors/logs/vector_$b.log" `
    --annotations-jsonl "data/processor_vector_$b.jsonl" `
    --warmup-models
}
```

Use a dry run before the full loop to confirm that the GCS prefix resolves:

```powershell
$batches = "L22","L23","L24","L25","L26","L27","L28","L29"
foreach ($b in $batches) {
  python scripts/processors/extract_gcs_annotations.py `
    --profile zilliz_embedding_only `
    --gcs-prefix "processed/keyframes/dataset=ai_challenge_2025/batch=$b/profile=autoshot_v1" `
    --max-frames 20 `
    --dry-run
}
```

`zilliz_embedding_only` sets `write_pg: false`, `write_milvus: true`, and `write_elasticsearch: false`, so it does not rewrite imported Supabase PostgreSQL metadata and does not require Elasticsearch.

Run a single video with the full model stack. This includes BLIP-2 captioning, so use it only on a runtime with enough GPU/RAM headroom:

```powershell
python scripts/processors/extract_gcs_annotations.py --profile full --video-id L21_V001 --warmup-models
```

Start Elasticsearch before text indexing:

```powershell
docker compose up -d elasticsearch
```

If Elasticsearch is intentionally offline, set `sinks.write_elasticsearch: false` in YAML or keep `sinks.fail_on_sink_error: false` so Supabase PostgreSQL and Milvus can continue.

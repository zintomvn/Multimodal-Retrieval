# GCS Frame Annotation Processor

This processor reads keyframes from Google Cloud Storage, extracts multimodal annotations in batches, and writes the results to:

- PostgreSQL: dataset, video, keyframe, and annotation metadata.
- Zilliz/Milvus: image embedding vectors.
- Elasticsearch: text-search documents built from captions, OCR text, and object labels.

Secrets and cloud endpoints stay in `.env`. Model names, batch sizes, cache paths, prompts, generation settings, and sink names stay in `processor_config.yaml`.

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
- `python scripts/processors/download_models.py --profile full`: passed.
- `python scripts/processors/extract_gcs_annotations.py --profile full --dry-run`: found `310,298` frames / `873` videos.

## Model Inventory

Default `profile full` is configured to run on the current laptop GPU/RAM. BLIP-2 is still available through `profile full_blip2`, but it is not the default because it cannot load with the current Windows paging-file limit.

| Feature                | Default model                                       | Status                                          | Local cache                                                            |
| ---------------------- | --------------------------------------------------- | ----------------------------------------------- | ---------------------------------------------------------------------- |
| Image embedding        | OpenCLIP `ViT-B-32`, pretrained `laion2b_s34b_b79k` | Downloaded and warmup passed                    | `models--laion--CLIP-ViT-B-32-laion2B-s34B-b79K`, `577.1 MB`           |
| Caption                | `Salesforce/blip-image-captioning-base`             | Downloaded and warmup passed                    | `models--Salesforce--blip-image-captioning-base`, `1.84 GB`            |
| Object detection       | Ultralytics `yolo12n.pt`                            | Downloaded and warmup passed                    | `yolo12n.pt`, `5.34 MB`                                                |
| OCR detection          | PaddleOCR `PP-OCRv5_mobile_det`                     | Downloaded and warmup passed on CPU             | `%USERPROFILE%/.paddlex/official_models/PP-OCRv5_mobile_det`, `4.7 MB` |
| OCR recognition        | VietOCR `vgg_seq2seq`                               | Downloaded and warmup passed                    | `processor-cache/vietocr/vgg_seq2seq.pth`, `85.4 MB`                   |
| Optional heavy caption | `Salesforce/blip2-opt-2.7b`                         | Weights downloaded, load failed on this machine | `models--Salesforce--blip2-opt-2.7b`, `13.95 GB`                       |

### OpenCLIP Embedding

- Model: `ViT-B-32`
- Pretrained weights: `laion2b_s34b_b79k`
- Output vector dimension: `512`
- Image input size: `224 x 224`
- Vision tower: `12` layers, width `768`, patch size `32`
- Text tower: context length `77`, vocab size `49,408`, width `512`, `8` heads, `12` layers
- Vector normalization: enabled (`models.embedding.l2_normalize: true`)
- Milvus/Zilliz metric recommendation: cosine
- Default embedding batch size: `64`

### BLIP Caption Model

- Default model: `Salesforce/blip-image-captioning-base`
- Model class: `BlipForConditionalGeneration`
- Vision tower: image size `384`, patch size `16`, hidden size `768`, `12` layers, `12` heads
- Text decoder: hidden size `768`, `12` layers, `12` heads, vocab size `30,524`
- Projection dimension: `512`
- Default caption batch size: `4`
- Prompt: `a photo of`
- Generation defaults: `max_new_tokens=40`, `min_new_tokens=5`, `num_beams=1`, `repetition_penalty=1.2`, `length_penalty=1.1`

### Optional BLIP-2 Profile

`profile full_blip2` keeps the larger BLIP-2 configuration for future experiments.

- Model: `Salesforce/blip2-opt-2.7b`
- Model class: `Blip2ForConditionalGeneration`
- Query tokens: `32`
- Vision tower: image size `224`, patch size `14`, hidden size `1408`, `39` layers, `16` heads
- Q-Former: hidden size `768`, `12` layers, `12` heads, cross-attention frequency `2`
- OPT language model: hidden size `2560`, `32` layers, `32` heads, vocab size `50,304`
- Local weights are downloaded, but warmup failed with `OSError: The paging file is too small for this operation to complete. (os error 1455)`.
- To use this profile later, increase Windows paging file/RAM headroom and keep at least `20 GB` free disk, or switch to a smaller caption model in YAML.

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

Download and warm up the default full model stack:

```powershell
python scripts/processors/download_models.py --profile full
```

Warm up only selected features:

```powershell
python scripts/processors/download_models.py --profile full --features embedding,objects,ocr
python scripts/processors/download_models.py --profile full --features caption
```

Try the heavy BLIP-2 profile only after fixing pagefile/disk constraints:

```powershell
python scripts/processors/download_models.py --profile full_blip2 --features caption
```

## Configuration

Main YAML file:

```text
scripts/processors/processor_config.yaml
```

Common tuning fields:

- `run.gcs_prefix`: GCS keyframe prefix, currently `processed/keyframes`.
- `run.batch_size`: outer pipeline batch size.
- `run.download_workers`: parallel GCS download workers.
- `run.max_frames`: `0` means no frame limit.
- `models.cache_dir`: local model cache, currently on `C:`.
- `models.device`: `auto`, `cpu`, `cuda`, or `cuda:0`.
- `models.embedding.*`: OpenCLIP model, pretrained weights, precision, batch size.
- `models.caption.*`: caption provider, model class, model name, prompt, generation parameters, memory/offload settings.
- `models.ocr.*`: detector/recognizer model, OCR thresholds, crop padding, local recognizer weight path.
- `models.objects.*`: YOLO model, image size, confidence threshold, batch size.
- `sinks.milvus_collection`: default `keyframe_embeddings`.
- `sinks.elasticsearch_index`: default `keyframe_annotations`.
- `sinks.fail_on_sink_error`: keep `false` to continue PG/Milvus when Elasticsearch is offline.

Environment variables expected in `.env`:

```env
DATABASE_URL=postgresql+psycopg://...
GCS_BUCKET=...
GCS_CREDENTIALS_FILE=...
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

Run only GCS frame-to-vector ingestion into Zilliz when PostgreSQL metadata has already been imported:

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

`zilliz_embedding_only` sets `write_pg: false`, `write_milvus: true`, and `write_elasticsearch: false`, so it does not rewrite imported PostgreSQL metadata and does not require Elasticsearch.

Run a single video with the full model stack:

```powershell
python scripts/processors/extract_gcs_annotations.py --profile full --video-id L21_V001 --warmup-models
```

Start Elasticsearch before text indexing:

```powershell
docker compose up -d elasticsearch
```

If Elasticsearch is intentionally offline, set `sinks.write_elasticsearch: false` in YAML or keep `sinks.fail_on_sink_error: false` so PostgreSQL and Milvus can continue.

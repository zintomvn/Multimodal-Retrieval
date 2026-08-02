# Feature Ingest Technical Report

> Scope đầu tiên: `L21`  
> Scale-out: `L21,L22,L23,L24,L25,L27,L28,L29,L30`  
> Excluded: `L26`  
> Runtime chính: Kaggle + Google Colab  
> Storage trung gian: Google Cloud Storage  
> Databases: Supabase PostgreSQL, Zilliz/Milvus, Elasticsearch

## 1. Mục tiêu

Luồng ingest mới biến notebook Kaggle/Colab thành các worker độc lập. Worker không ghi trực tiếp vào database. Worker chỉ đọc manifest shard, chạy model, ghi feature artifact lên GCS và cập nhật checkpoint. Sau đó một máy tin cậy đọc artifact từ GCS và import tập trung vào Supabase, Zilliz/Milvus và Elasticsearch.
Source registry trong `configs/data_ingestion_sources.yaml` hiện có cả nguồn Kaggle và nguồn Drive; backend loader dùng `source_type` để chọn raw prefix tương ứng.
Với processors, Drive được xem là upstream source; dữ liệu cần được mirror hoặc normalize vào cùng layout GCS trước khi discovery và shard processing.

Thiết kế này giải quyết ba điểm quan trọng:

1. Chạy được L21 trước, sau đó mở rộng batch.
2. Resume được khi Kaggle/Colab dừng giữa chừng.
3. Thay model bằng YAML/profile, không sửa runner.
4. Discovery có thể enrich `frame_seconds` và `shot_id` từ `shot_segments.csv`; nếu thiếu file này thì vẫn fallback theo GCS path.

## 2. Kinh nghiệm model áp dụng từ top player

Từ `top_video_retrieval_pipeline_2025.md`, hệ thống hiện dùng các quyết định sau:

| Thành phần                 | Quyết định                                                                                                        |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Keyframe extraction        | AutoShot đã hoàn tất trên GCS, giữ nguyên làm frame source                                                        |
| Primary visual embedding   | PE-Core-bigG-14-448 qua OpenCLIP `hf-hub:timm/PE-Core-bigG-14-448`                                                |
| Secondary visual embedding | OpenCLIP ViT-H/14 la nhanh chinh hoăc SigCLIP; BLIP-2 giu lam profile caption/verification nang khi du tai nguyen |
| OCR                        | EasyOCR/CRAFT tiếng Việt-Anh hoặc PaddleOCR + VietOCR                                                             |
| ASR                        | faster-whisper                                                                                                    |
| Caption                    | Qwen-VL/Gemini-style OpenAI-compatible VLM cho shot-context caption, BLIP base giữ làm fallback ổn định           |
| Text embedding             | `dangvantuan/vietnamese-embedding` để materialize text vector từ caption/OCR/ASR                                  |
| Metadata search            | Elasticsearch                                                                                                     |
| Vector search              | Zilliz/Milvus                                                                                                     |
| Fusion                     | Weighted RRF profile `competition_mvp_v1`                                                                         |
| Reranking                  | Cross-encoder reranker tren top candidates; optional MLLM verification qua Qwen/Gemini-compatible VLM             |
| Temporal search            | Subquery retrieval + two-pointer grouping                                                                         |
| Frontend                   | Keyframe grid, neighboring-frame context, video preview/proxy scrubbing                                           |

## 3. Code đã thêm

Root `scripts/processors/` hiện chỉ giữ các entrypoint/operator files. Các module hỗ trợ được gom vào package `scripts/processors/src/` để CLI, notebook runner, import/reconcile và model adapters có ranh giới rõ hơn.

| File                                                        | Vai trò                                                                                         |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `scripts/processors/processor_cli.py`                       | CLI chung: discover, run shard, import, plan, doctor, reconcile                                 |
| `scripts/processors/extract_gcs_asr.py`                     | faster-whisper ASR cho raw videos trên GCS                                                      |
| `scripts/processors/extract_gcs_annotations.py`             | legacy/direct GCS frame annotation entrypoint                                                   |
| `scripts/processors/download_models.py`                     | warm up/download enabled model stack                                                            |
| `scripts/processors/src/artifact_io.py`                     | đọc/ghi JSON, JSONL local hoặc GCS; hỗ trợ `GCS_CREDENTIALS_FILE` và `GCS_SERVICE_ACCOUNT_JSON` |
| `scripts/processors/src/manifest.py`                        | tạo manifest keyframe và execution shard                                                        |
| `scripts/processors/src/checkpoint_store.py`                | checkpoint, lease, heartbeat, `_SUCCESS`                                                        |
| `scripts/processors/src/shard_runner.py`                    | chạy một feature shard trên Kaggle/Colab                                                        |
| `scripts/processors/src/ingest_artifacts.py`                | merge artifact JSONL và import DB                                                               |
| `scripts/processors/src/role_planner.py`                    | sinh plan chi tiết cho từng máy Kaggle/Colab từ YAML                                            |
| `scripts/processors/src/notebook_role_runner.py`            | render/execute một role Kaggle/Colab cụ thể từ plan                                             |
| `scripts/processors/src/notebook_cells.py`                  | sinh copy-ready notebook cells va notebook kit cho từng role Kaggle/Colab                       |
| `scripts/processors/src/reconcile_run.py`                   | kiểm manifest, checkpoint và artifact trước khi import DB                                       |
| `scripts/processors/src/processor_doctor.py`                | kiểm runtime/env/package/GCS readiness cho Kaggle, Colab, importer                              |
| `scripts/processors/src/text_embedding.py`                  | sentence-transformers text embedder cho tiếng Việt                                              |
| `scripts/processors/src/extractors/`                        | image/text feature extractors and model runtime helpers                                         |
| `scripts/processors/src/cloud_sinks/`                       | Supabase/PostgreSQL, Elasticsearch, and Milvus sink adapters                                    |
| `scripts/processors/configs/pipeline/processor.yaml`        | model, run, sink, and profile settings                                                          |
| `scripts/processors/configs/pipeline/notebook_roles.yaml`   | role map cho Kaggle/Colab                                                                       |
| `scripts/processors/configs/runtime/checkpoint_policy.yaml` | policy checkpoint/resume                                                                        |
| `scripts/processors/requirements-asr.txt`                   | ASR dependencies                                                                                |

Các extractor hiện có cũng được nâng cấp:

- `embedding.py`: cho phép OpenCLIP model từ `hf-hub:*`, dùng cho PE-Core.
- `objects.py`: YOLO output có `bbox_xyxy_px` và `bbox_xyxy_norm`.
- `ocr.py`: thêm provider `craft_easyocr`.
- `pipeline.py`: route OCR provider `vietocr`, `paddle_vietocr`, `easyocr`, `craft_easyocr`.
- `caption.py`: thêm provider `openai_compatible_vlm` cho Qwen-VL/Gemini proxy, nhận shot context từ manifest row.

## 4. Artifact contract

Mỗi feature part là JSONL:

```json
{
  "schema_version": "aic.feature_artifact.v1",
  "run_id": "mvp_l21_20260722_000000",
  "stage": "visual_primary",
  "shard_id": "shard-00000",
  "frame": {
    "dataset_id": "ai_challenge_2025",
    "batch": "L21",
    "video_id": "L21_V001",
    "keyframe_id": "L21_V001_F000023",
    "frame_idx": 23,
    "gcs_uri": "gs://..."
  },
  "annotation": {
    "caption": "",
    "texts": [],
    "objects": [],
    "object_counts": {},
    "detections": []
  },
  "embedding": [0.01, 0.02],
  "status": "ok"
}
```

Output prefix:

```text
gs://aic_ai_2026/features/dataset=ai_challenge_2025/
  frame_profile=autoshot_v1/
  feature_profile=mvp_v1/
  run_id=<RUN_ID>/
  stage=<stage>/
  shard_id=<shard-id>/
  part-00000.jsonl
```

## 5. Checkpoint contract

Checkpoint:

```text
gs://aic_ai_2026/checkpoints/pipeline=feature_ingest/
  run_id=<RUN_ID>/
  stage=<stage>/
  shard_id=<shard-id>/
    checkpoint.json
    lease.json
    _SUCCESS.json
```

Important fields:

- `next_index`: manifest row index to resume from.
- `next_part_index`: next artifact part number.
- `processed`: successfully written frame count.
- `failed`: failed frame count.
- `output_parts`: feature JSONL files created by this shard.
- ASR artifacts also carry `shard_id` derived from the normalized GCS prefix so batch-level runs cannot overwrite each other.
- Feature and ASR artifact paths also include `worker_id` and `attempt_id` so retries append new part files instead of clobbering partial uploads.
- If a shard lease has been handed to another notebook, the stale worker stops before persisting more checkpoint state.

Resume rule:

- If `_SUCCESS.json` exists, skip shard.
- If `lease.json` owner is active, do not take shard.
- If lease expired, a new worker can continue from `checkpoint.next_index`.

## 6. Database import

Database import is centralized:

```text
GCS feature artifacts
  -> merge by keyframe_id
  -> PostgresAnnotationSink
  -> ElasticsearchAnnotationSink
  -> MilvusEmbeddingSink
  -> MilvusTextEmbeddingSink
```

Collections:

- Primary visual: `keyframe_embeddings_pe_core_bigG_14_448`.
- Secondary visual: `keyframe_embeddings_openclip_vith14`.
- Legacy visual fallback: `keyframe_embeddings`.

Elasticsearch index:

- `keyframe_annotations`.
- `keyframe_annotations_asr_segments`.

PostgreSQL tables touched:

- `datasets`
- `videos`
- `shots`
- `keyframes`
- `frame_annotations`

Text embeddings:

- `text_embeddings_vietnamese` stores frame-level text and ASR segment vectors.
- Text is built from merged caption + OCR + object labels and ASR segments.

## 7. L21-first operation

1. Run `processor_cli.py doctor` on each Kaggle/Colab/importer runtime. Use `--features caption` for caption-only Colab notebooks and `--features ocr,asr` for OCR/ASR notebooks so each machine checks only the stack it will execute.
2. Create L21 manifest and shards with `processor_cli.py discover-gcs-keyframes`.
3. Render machine assignments with `processor_cli.py plan-notebook-run`.
4. Use `processor_cli.py run-notebook-role` in each Kaggle/Colab notebook to render or execute one role/shard from the plan.
5. Run `processor_cli.py smoke-local-flow` locally to validate manifest, checkpoint, artifact, reconcile, and import dry-run before cloud execution.
6. Run one shard through primary visual embedding on Kaggle.
7. Run the same or another shard through object/OCR/caption workers.
8. Run ASR over L21 raw videos if available.
9. Reconcile manifest/checkpoint/artifact counts with `processor_cli.py reconcile-run`.
10. Import primary visual artifacts into Zilliz.
11. Import object/OCR/caption/ASR artifacts into Supabase + Elasticsearch.
12. Materialize Vietnamese text embeddings into Milvus.
13. Smoke search with backend profile `competition_mvp_v1`.

The exact commands are documented in `scripts/processors/README.md`.
`doctor`, `reconcile-run`, and executed `run-notebook-role` runs return a non-zero exit code when their JSON report has `ok: false`, so they can be used as notebook/CI gates.

## 8. Scale-out strategy

After L21 succeeds:

- create one manifest for `L21,L22,L23,L24,L25,L27,L28,L29,L30`;
- distribute shard files across Kaggle/Colab;
- regenerate commands with `plan-notebook-run` so ASR roles are expanded per batch and import commands use the correct multi-batch dataset code;
- keep stage-specific shard sizes:
  - visual/object: 512 frames;
  - OCR/caption: 128 frames;
  - ASR: video-level;
- import each visual model into its own Milvus collection;
- import text/object stages into the shared Elasticsearch index.

## 9. Verification performed

Local verification already run:

```bash
python -m compileall -q apps/backend/app scripts/processors
python -m pytest scripts/processors/tests/test_processor_pipeline.py -q
python -m pytest apps/backend/tests -q
python scripts/processors/processor_cli.py --profile smoke render-notebook-cells --role-id kaggle_l21_primary_visual --run-id smoke_cells_gcsjson --shard-root gs://bucket/manifests/run_id=smoke_cells_gcsjson/shards --num-shards 1
python scripts/processors/processor_cli.py --profile smoke smoke-local-flow --run-id smoke_verify_gcsjson --batches L21,L22 --frame-count 1
```

Observed result:

- processor tests: `32 passed`
- backend tests: `65 passed`
- compileall: passed
- rendered notebook cells include `GCS_SERVICE_ACCOUNT_JSON` handling and materialize it to `/tmp/gcs-sa.json`
- multi-batch local smoke: `ok: true`, `manifest.frames = 2`, `import_dry_run.records = 2`, `import_dry_run.asr_records = 4`

CLI entry points checked:

```bash
python scripts/processors/processor_cli.py --profile smoke discover-gcs-keyframes --help
python scripts/processors/processor_cli.py --profile smoke run-feature-shard --help
python scripts/processors/processor_cli.py --profile smoke import-feature-artifacts --help
python scripts/processors/processor_cli.py --profile smoke plan-notebook-run --help
python scripts/processors/processor_cli.py --profile smoke run-notebook-role --help
python scripts/processors/processor_cli.py --profile smoke render-notebook-cells --help
python scripts/processors/processor_cli.py --profile smoke render-notebook-kit --help
python scripts/processors/processor_cli.py --profile smoke smoke-local-flow --help
python scripts/processors/processor_cli.py --profile smoke reconcile-run --help
python scripts/processors/extract_gcs_asr.py --help
```

Cloud/runtime verification requires valid GCS credentials and is intentionally separated from local compile tests.

## 10. Remaining production hardening

- Add true atomic compare-and-set lease if multiple autonomous workers run without manual coordination.
- Add Qwen/Gemini shot-context captioner adapter when API/runtime is ready.
- Add event embedding build/import after frame-level search is green.
- Add a reconciliation command to compare manifest count, artifact count, DB count and Milvus count.

## 11. Downstream retrieval and UI

The ingest pipeline feeds the existing retrieval stack:

- `configs/retrieval_profiles.yaml` defines `competition_mvp_v1` with weighted fusion across primary PE-Core, secondary OpenCLIP ViT-H/14, and the legacy visual collection.
- `configs/model_registry.yaml` exposes a `rerankers` group so the cross-encoder can be swapped by YAML.
- `apps/backend/app/modules/retrieval/service.py` performs hybrid search, weighted score blending, RRF, cross-encoder reranking, optional VLM verification, and temporal clip grouping.
- `apps/backend/app/modules/retrieval/query_planning.py` expands and decomposes queries for temporal and multi-signal search.
- `apps/web/src/App.tsx` already renders a keyframe grid, neighboring-frame context, and a video preview modal backed by the media API.

This means the ingest layout is not isolated. The same `run_id`, `batch`, `video_id`, and `keyframe_id` contract is consumed by search, review, and submission UI.

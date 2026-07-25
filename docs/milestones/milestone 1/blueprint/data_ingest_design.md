# Blueprint ingest ưu tiên Kaggle/Colab

> Ngày cập nhật: 2026-07-23  
> Phạm vi: pipeline L21-L30 hiện tại và dữ liệu mới do ban tổ chức upload lên Drive/Kaggle

## 1. Mục tiêu

Mục tiêu của blueprint này là xây dựng một ingest pipeline:

- Kaggle và Google Colab là runtime chính cho tính toán.
- Google Cloud Storage là artifact bus và nơi kiểm tra trạng thái.
- Frame extraction đã hoàn tất trên GCS và được xem là canonical source cho keyframe.
- Dữ liệu mới có thể đến từ Kaggle hoặc Drive, nhưng sau khi normalize phải đi qua cùng một schema.
- Mỗi stage phải có shard, checkpoint, resume, và append-only output.
- Việc thay model mới nên ưu tiên chỉ đổi cấu hình, không sửa code luồng.

Không nên xem local/backend machine là nơi chạy tính toán chính. Vai trò của máy này chỉ là import trung tâm, kiểm tra, và phát hiện lỗi.

## 2. Trạng thái hiện tại

Hiện tại repo đã có các phần nền tảng sau:

- `scripts/processors/processor_cli.py`: entry point chạy discovery, shard worker, import, planner, doctor, và reconcile.
- `scripts/processors/src/manifest.py`: discover keyframe manifest và chia shard.
- `scripts/processors/src/shard_runner.py`: chạy worker theo shard, ghi part files, cập nhật checkpoint.
- `scripts/processors/src/checkpoint_store.py`: lease, heartbeat, resume.
- `scripts/processors/extract_gcs_asr.py`: ASR worker riêng.
- `scripts/processors/src/role_planner.py`: đọc YAML role/checkpoint và sinh command cho từng notebook.
- `scripts/processors/src/notebook_role_runner.py`: render/execute một role cụ thể trong notebook Kaggle/Colab.
- `scripts/processors/src/notebook_cells.py`: sinh copy-ready notebook cells và notebook kit cho từng role.
- `scripts/processors/src/reconcile_run.py`: kiểm tra manifest, checkpoint, và artifact trước import.
- `scripts/processors/src/processor_doctor.py`: kiểm runtime/env/package/GCS readiness trước khi chạy GPU/import.
- `scripts/processors/src/ingest_artifacts.py`: import artifact vào Supabase PostgreSQL/Zilliz/Milvus/Elasticsearch.
- `scripts/processors/src/extractors/caption.py`: BLIP/BLIP-2 fallback và OpenAI-compatible VLM captioner cho Qwen-VL/Gemini proxy.
- `scripts/processors/src/artifact_io.py`: đọc/ghi local và GCS JSONL, hỗ trợ `GCS_CREDENTIALS_FILE` hoặc `GCS_SERVICE_ACCOUNT_JSON`.
- `scripts/processors/src/cloud_sinks/`: adapter cho Supabase PostgreSQL, Zilliz/Milvus, Elasticsearch.

Frame extraction trên GCS đã có thể kiểm tra trực tiếp. Blueprint này lấy `processed/keyframes/...` làm đầu vào chuẩn cho các stage sau.

## 3. Canonical Flow

```text
Source upload (Kaggle hoặc Drive)
  -> raw landing / source registry
  -> keyframe discovery trên GCS
  -> manifest shards
  -> Kaggle/Colab workers
  -> feature artifacts (append-only JSONL)
  -> central import
  -> Supabase PostgreSQL / Zilliz/Milvus / Elasticsearch
  -> retrieval profiles
```

Quy tắc:

- Không hardcode batch.
- Không hardcode model name trong worker.
- Không ghi đè artifact cũ.
- Mỗi shard phải có checkpoint riêng.
- Mỗi run phải có `run_id`.

## 4. File Layout Và Vai Trò

### 4.1 Code

| Khu vực | File / thư mục | Vai trò |
| --- | --- | --- |
| Core processor | `scripts/processors/processor_cli.py` | Lệnh chạy chính cho discover, run shard, import |
| Discovery | `scripts/processors/src/manifest.py` | List GCS, build manifest, chia shard |
| Worker | `scripts/processors/src/shard_runner.py` | Claim lease, xử lý shard, ghi part files |
| Checkpoint | `scripts/processors/src/checkpoint_store.py` | Resume, heartbeat, lease TTL |
| Artifact IO | `scripts/processors/src/artifact_io.py` | Đọc/ghi JSONL, local và GCS |
| Import | `scripts/processors/src/ingest_artifacts.py` | Merge artifact vào Supabase PostgreSQL/Zilliz/Milvus/Elasticsearch |
| Planner | `scripts/processors/src/role_planner.py` | Render command cho Kaggle/Colab từ YAML |
| Notebook runner | `scripts/processors/src/notebook_role_runner.py` | Render/execute đúng một role trên Kaggle/Colab |
| Notebook cells | `scripts/processors/src/notebook_cells.py` | Xuất markdown/cell kit cho Kaggle/Colab, gồm install, env, doctor, run, resume |
| Smoke flow | `scripts/processors/src/smoke_flow.py` | Local synthetic end-to-end smoke cho manifest, checkpoint, artifact, reconcile, import dry-run |
| Reconcile | `scripts/processors/src/reconcile_run.py` | Gate kiểm artifact/checkpoint trước DB import |
| Doctor | `scripts/processors/src/processor_doctor.py` | Preflight readiness cho Kaggle/Colab/importer |
| ASR | `scripts/processors/extract_gcs_asr.py` | Run faster-whisper trên raw video |
| Extractors | `scripts/processors/src/extractors/` | Wrapper cho embedding, OCR, object, caption |
| Sinks | `scripts/processors/src/cloud_sinks/` | Adapter cuối cho database/search |

### 4.2 YAML

| File | Mục đích |
| --- | --- |
| `configs/data_ingestion_sources.yaml` | Đăng ký source Kaggle/Drive, rule nhận batch, include/exclude path |
| `configs/dataset_manifest.real.example.yaml` | Mẫu manifest dataset chuẩn hóa |
| `configs/model_registry.yaml` | Đăng ký model, checkpoint, provider, collection, device, batch size |
| `scripts/processors/configs/pipeline/processor.yaml` | Default runtime và profile theo stage |
| `scripts/processors/configs/pipeline/notebook_roles.yaml` | Map vai trò Kaggle/Colab, shard size, batch size gợi ý |
| `scripts/processors/configs/runtime/checkpoint_policy.yaml` | Lease TTL, heartbeat, resume policy |
| `configs/retrieval_profiles.yaml` | Trọng số hybrid retrieval và fusion profile |

## 5. Thiết Kế Parallel

### 5.1 Đơn vị song song

Đơn vị song song là shard, không phải file lẻ. Một notebook / một runtime xử lý:

- một shard
- một stage
- một profile model

Nếu runtime dừng giữa chừng, shard đó có thể được claim lại sau khi lease hết hạn.

### 5.2 Phân vai runtime

| Runtime | Việc chính | Profile gợi ý |
| --- | --- | --- |
| Kaggle | Primary visual embedding, secondary visual embedding, objects | `visual_primary_pe_core`, `visual_secondary_openclip_vith14`, `objects_only` |
| Colab | OCR, caption, ASR | `craft_easyocr_only`, `caption_only`, ASR worker riêng |
| Backend/import machine | Import trung tâm, QA, benchmark, verify | importer |

Trước khi chạy GPU/import, mỗi runtime nên chạy `processor_cli.py doctor`:

- Kaggle visual/object: `doctor --runtime kaggle`.
- Colab caption-only/VLM: `doctor --runtime colab --features caption`.
- Colab OCR/ASR: `doctor --runtime colab --features ocr,asr`.
- Importer: `doctor --runtime importer`.

Nếu không truyền `--features`, doctor sẽ kiểm full stack mặc định của runtime. Khi chạy notebook đơn mục đích, nên truyền feature cụ thể để tránh fail nhầm vì package của stage khác.

`doctor` và `reconcile-run` phải exit non-zero khi `ok: false`; `run-notebook-role --doctor-first --execute` phải dừng trước khi chạy model nếu preflight fail.

Kaggle/Colab có thể dùng `GCS_CREDENTIALS_FILE` nếu upload key file vào runtime, hoặc dùng secret/env `GCS_SERVICE_ACCOUNT_JSON`; notebook cells sẽ materialize secret này thành `/tmp/gcs-sa.json` trước khi chạy lệnh GCS.

### 5.3 Notebook Kit

Không copy command thủ công cho từng máy. Bước điều phối nên sinh plan và notebook kit:

```bash
python scripts/processors/processor_cli.py --profile smoke plan-notebook-run --run-id "$RUN_ID" --batches L21 --manifest-summary-uri <SUMMARY_URI>
python scripts/processors/processor_cli.py --profile smoke render-notebook-kit --run-id "$RUN_ID" --batches L21 --manifest-summary-uri <SUMMARY_URI>
```

Mỗi role file gồm 6 cell: role metadata, install, environment, doctor, run role, resume note. Khi scale nhiều batch, render lại kit với `--batches L21,L22,L23,L24,L25,L27,L28,L29,L30` để planner tự chia shard và tạo ASR role theo batch.

### 5.4 Quy tắc checkpoint

Mỗi shard phải lưu:

- `next_index`
- `processed`
- `failed`
- `last_heartbeat`
- `lease_owner`

Output phải là append-only:

- `part-00000.jsonl`
- `part-00001.jsonl`
- ...
- `_SUCCESS.json`

Part files nằm trong `worker_id`/`attempt_id` namespace, nên retry không ghi đè artifact đã upload một phần.

Nếu notebook chết:

1. Giữ nguyên shard URI.
2. Chạy lại cùng lệnh.
3. Worker đọc checkpoint cũ và tiếp tục từ `next_index`.

## 6. Discovery Và Normalize

Discovery phải là bước source-agnostic. Quy trình đề xuất:

1. Đọc source registry trong `configs/data_ingestion_sources.yaml`.
2. Thu thập raw path từ Kaggle hoặc Drive.
3. Normalize về schema chung:
   - `dataset_id`
   - `batch`
   - `video_id`
   - `keyframe_id`
   - `shot_id`
   - `frame_idx`
   - `frame_seconds`
   - `gcs_uri`
4. Build manifest và shard JSONL.
5. Kiểm tra:
   - batch có nằm trong scope không
   - batch bị exclude có được loại bỏ không
   - id có hợp lệ không
   - timing metadata có cần enrich từ `shot_segments.csv` không

Với benchmark hiện tại, `L26` nên giữ ở mức profile/config, không hardcode trong code.

## 7. Dữ Liệu Mới Từ Drive

Khi ban tổ chức upload dữ liệu mới lên Drive, phần pipeline này vẫn phải chạy được mà không đổi worker code.

Chiến lược:

- Thêm `source_type: drive` vào `configs/data_ingestion_sources.yaml`.
- Đưa folder Drive về raw landing zone có schema chung.
- Normalize sang cùng manifest schema như Kaggle.
- Nếu naming batch/video khác, chỉ đổi regex/path rule trong YAML.
- Giữ worker phụ thuộc vào manifest, không phụ thuộc vào layout gốc của source.
- Backend loader trong repo đã dùng `source_type` để chọn `raw_prefix` phù hợp, nên Drive có thể đi qua cùng cơ chế với Kaggle.

Nói cách khác, source có thể đổi, nhưng `manifest -> shard -> worker -> artifact -> import` phải giữ nguyên.

## 8. Model Swap Strategy

Để thay model mới mà không làm vỡ pipeline:

1. Đăng ký model trong `configs/model_registry.yaml`.
2. Tạo hoặc đổi profile trong `scripts/processors/configs/pipeline/processor.yaml`.
3. Nếu cần, tạo collection/index mới thay vì ghi đè collection cũ.
4. Giữ nguyên artifact schema, chỉ đổi `model_name`, `model_version`, `provider`, `collection`.

Nguyên tắc:

- Worker chỉ biết `logical role` như `embedding`, `ocr`, `caption`, `objects`, `text_embedding`.
- Profile quyết định model nào được bật.
- Sink quyết định ghi vào collection/index nào.

Không nên hardcode:

- checkpoint path của model
- collection name trong code xử lý
- batch size cố định
- device string ở nhiều nơi

## 8.1 Top-Player Baseline

Bộ baseline ưu tiên theo kinh nghiệm của top player và khối top-player review trong repo:

| Modality | Baseline chính | Fallback / alt |
| --- | --- | --- |
| Keyframe extraction | Autoshot | 3 frame/shot chỉ để smoke hoặc stress |
| Primary visual embedding | PE-Core-bigG-14-448 | - |
| Secondary visual embedding | OpenCLIP ViT-H/14 | BLIP-2 khi cần profile nặng hơn |
| ASR | faster-whisper | Whisper-compatible fallback |
| OCR | CRAFT + Vietnamese-capable recognizer | EasyOCR / PaddleOCR khi cần |
| Captioning | Qwen-VL hoặc Gemini với shot context | BLIP / shot-context fallback |
| Vietnamese text embedding | `dangvantuan/vietnamese-embedding` | - |
| Text and metadata DB | Elasticsearch | - |
| Vector DB | Milvus / Zilliz | - |
| Fusion | Weighted Reciprocal Rank Fusion | RRF nếu cần nhanh |
| Reranking | Cross-encoder | Optional MLLM verification |
| Temporal search | Subquery retrieval + two-pointer clip grouping | Sliding window fallback |
| Frontend | Keyframe grid + neighboring shots + proxy scrubbing | - |

Baseline này phù hợp với phân chia runtime:

- Kaggle giữ visual embedding và object detection.
- Colab giữ OCR, caption, và ASR.
- Importer giữ text embedding và ingest vào Supabase/Zilliz/Elasticsearch.

Khi thay model mới, chỉ đổi `model_registry.yaml` và `processor.yaml`; không đổi phần phân chia runtime.

## 9. Bố Cục Output

### 9.1 Manifest và shard

```text
manifests/dataset=<dataset_id>/pipeline=feature_ingest/run_id=<run_id>/
  keyframes.jsonl
  manifest_summary.json
  shards/shard-00000.jsonl
  shards/shard-00001.jsonl
```

### 9.2 Feature artifact

```text
features/dataset=<dataset_id>/frame_profile=<frame_profile>/feature_profile=<feature_profile>/
  run_id=<run_id>/
    stage=<stage>/
      part-00000.jsonl
      part-00001.jsonl
      _SUCCESS.json
```

### 9.3 Checkpoint

```text
checkpoints/pipeline=feature_ingest/run_id=<run_id>/stage=<stage>/shard=<shard_name>.json
```

ASR artifacts có thể dùng chung checkpoint này theo `shard_id` để không ghi đè khi chạy song song.

## 10. Tối Ưu Runtime

Ưu tiên:

- Embedding: shard lớn hơn, batch lớn hơn.
- OCR và caption: shard nhỏ hơn, batch nhỏ hơn.
- ASR: chia theo video, không ép chung vào shard frame.
- Import DB: chạy tập trung, không để notebook direct write nếu không cần.

Gợi ý profile:

- Kaggle embedding: batch 16-24, shard 512 frame.
- Kaggle objects: batch 32, shard 512 frame.
- Colab OCR: batch 8, shard 128 frame.
- Colab caption: batch 4, shard 128 frame.
- ASR: theo video, checkpoint sau mỗi video.

Lưu ý:

- `download_workers` chỉ nên tăng vừa phải.
- Nếu một runtime trễ, chia ít shard cho runtime khác.
- Mỗi stage phải có log và đo latency riêng.

## 11. Thứ Tự Rollout

1. Giữ GCS keyframe tree làm canonical input.
2. Chạy discovery để tạo manifest và shard.
3. Chạy live GCS smoke với `doctor --check-gcs`, manifest 2 frame, và 1 shard primary visual.
4. Chạy song song Kaggle cho visual/object.
5. Chạy song song Colab cho OCR/caption/ASR.
6. Gom artifact về GCS và chạy `reconcile-run`.
7. Import trung tâm vào Supabase PostgreSQL, Zilliz/Milvus, Elasticsearch.
8. Thêm model mới bằng registry/profile, không sửa worker core.
9. Khi có dữ liệu mới từ Drive, chỉ cập nhật source registry và run manifest.

Khi scale từ L21 sang nhiều batch, dùng `plan-notebook-run` để sinh lại command thay vì copy command L21. Planner sẽ loại `L26`, tạo ASR role riêng theo batch, và đổi dataset code import từ `mvp_l21` sang scope multi-batch tương ứng.

## 12. Definition Of Done

Blueprint này được xem là đạt khi:

- Một source mới có thể được thêm vào registry mà không sửa worker core.
- Một shard bị dừng có thể chạy lại và resume đúng vị trí.
- Kaggle/Colab có thể chạy song song mà không ghi đè nhau.
- Model mới có thể swap bằng registry/profile thay vì refactor code.
- Dữ liệu mới từ Drive có thể đi qua cùng manifest schema và cùng import path.
- Live GCS smoke pass trên notebook thật trước khi chạy full L21.
- Artifact, checkpoint, và collection/index đều phân biệt theo `run_id` và `model_version`.

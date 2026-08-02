# Technical Report — `fe-captioning-v1.ipynb`

| Thuộc tính | Giá trị |
|---|---|
| Tài liệu nguồn | `fe-captioning-v1.ipynb` |
| Loại hệ thống | Frame/keyframe image captioning pipeline |
| Phiên bản annotation | `fe-captioning-v1` |
| Extractor name | `captioning` |
| Nền tảng chạy mục tiêu | Kaggle Notebook có GPU và Internet |
| Kho dữ liệu | Google Cloud Storage (GCS) |
| Ngày lập báo cáo | 24/07/2026 |
| Trạng thái đánh giá | Phân tích tĩnh notebook; chưa chạy benchmark GPU end-to-end trong tài liệu này |

---

## 1. Mục tiêu của notebook

`fe-captioning-v1.ipynb` là notebook trích xuất caption từ các keyframe ảnh đã được tạo trước đó và lưu trên Google Cloud Storage. Notebook được thiết kế để:

1. Đọc manifest chứa thông tin keyframe từ GCS.
2. Chuẩn hóa metadata của từng frame về một contract thống nhất.
3. Tải ảnh keyframe từ GCS về thư mục tạm trên Kaggle.
4. Sinh caption bằng một model được chọn trong `MODEL_REGISTRY`.
5. Ghi annotation, lỗi, metric và summary thành artifact cục bộ.
6. Tùy chọn upload artifact lên GCS.
7. Hỗ trợ resume để không caption lại những keyframe đã được xử lý.
8. Benchmark tuần tự ba model trên năm video và tạo bảng pandas để so sánh caption, thời gian và VRAM.

Notebook chỉ caption **ảnh keyframe độc lập**. Nó không đưa chuỗi nhiều frame hoặc toàn bộ video vào model, vì vậy đây chưa phải pipeline video captioning có ngữ cảnh thời gian.

---

## 2. Tổng quan kiến trúc

```text
GCS bucket
  │
  ├── Keyframe manifest
  │     ├── shot_segments.csv
  │     └── frames_manifest.jsonl
  │
  ▼
Manifest discovery và normalization
  │
  ▼
Resume filter theo keyframe_id
  │
  ▼
Concurrent frame downloader
  │
  ▼
Local frame scratch directory
  │
  ▼
MODEL_REGISTRY
  ├── Qwen2.5-VL-3B-Instruct
  ├── BLIP-2 OPT-2.7B
  └── Qwen3-VL-8B-Instruct
  │
  ▼
Backend-specific preprocessing và generation
  │
  ▼
Caption annotation contract
  │
  ├── annotations.jsonl
  ├── errors.jsonl
  ├── metrics.csv
  ├── summary.json
  └── run.log
  │
  ▼
Optional upload lên GCS + _SUCCESS marker
```

Các khối chức năng chính:

| Khối | Vai trò |
|---|---|
| Parameters | Khai báo dataset, model, batch, prompt, GCS, benchmark và runtime options |
| Shared GCS helpers | Authentication, manifest discovery, download, resume, logging và upload |
| Model registry | Tách cấu hình model khỏi logic chạy |
| Model loader | Load model theo `backend`, device, dtype và quantization |
| Inference adapter | Chuẩn hóa cách gọi Qwen2.5-VL, Qwen3-VL và BLIP-2 |
| Extractor runner | Chạy pipeline dry/demo/full và ghi artifact |
| Benchmark runner | Chạy từng model tuần tự trên cùng tập frame để so sánh |

---

## 3. Các model được cấu hình

### 3.1 Bảng cấu hình mặc định

| Model key | Hugging Face model | Backend | Batch nội bộ model | Token sinh tối đa | Quantization | Prompt mặc định |
|---|---|---:|---:|---:|---|---|
| `qwen25_vl_3b` | `Qwen/Qwen2.5-VL-3B-Instruct` | `qwen25_vl` | 4 | 80 | `none` | Prompt tiếng Việt 1–3 câu |
| `blip2_opt_2_7b` | `Salesforce/blip2-opt-2.7b` | `blip2` | 8 | 80 | `none` | `a photo of` |
| `qwen3_vl_8b` | `Qwen/Qwen3-VL-8B-Instruct` | `qwen3_vl` | 1 | 96 | `4bit` | Prompt tiếng Việt 1–3 câu |

### 3.2 Tham số riêng của BLIP-2

| Tham số | Giá trị |
|---|---:|
| `min_new_tokens` | 10 |
| `num_beams` | 1 |
| `repetition_penalty` | 1.2 |
| `length_penalty` | 1.1 |

### 3.3 Giới hạn kích thước ảnh cho Qwen

Qwen2.5-VL và Qwen3-VL đều được cấu hình:

```python
max_pixels = 1024 * 1024
```

Giới hạn này giúp kiểm soát số visual token, thời gian inference và VRAM. Ảnh lớn hơn có thể được resize trong pipeline xử lý ảnh của Qwen.

### 3.4 Model dùng cho Demo và Full Run

```python
ACTIVE_MODEL_KEY = "qwen25_vl_3b"
```

`ACTIVE_MODEL_KEY` quyết định model được sử dụng trong `Demo Run` và `Full Run`.

### 3.5 Model dùng cho benchmark

```python
BENCHMARK_MODEL_KEYS = [
    "qwen25_vl_3b",
    "blip2_opt_2_7b",
    "qwen3_vl_8b",
]
```

Các model được load và unload **tuần tự**, không giữ cả ba model đồng thời trên GPU.

---

## 4. Prompt captioning

### 4.1 Prompt tiếng Việt cho Qwen

Prompt yêu cầu model:

- Mô tả nội dung chính của ảnh bằng tiếng Việt.
- Tập trung vào người, hành động, vật thể, bối cảnh, địa điểm và sự kiện.
- Ghi lại chữ, logo, biển báo hoặc phụ đề khi nhìn thấy rõ.
- Không suy đoán thông tin không chắc chắn.
- Trả về một đoạn ngắn từ một đến ba câu.

Prompt này phù hợp với video retrieval vì ưu tiên entity, action, scene và visible text.

### 4.2 Prompt của BLIP-2

BLIP-2 giữ prompt từ notebook nguồn:

```text
a photo of
```

Do prompt này bằng tiếng Anh và model không được instruction-tune theo cùng cách với Qwen, caption BLIP-2 nhiều khả năng sẽ có phong cách và ngôn ngữ khác hai model Qwen. Vì vậy, việc so sánh trực tiếp chất lượng caption cần xem xét khác biệt prompt, không chỉ khác biệt model.

---

## 5. Thông số dataset và GCS

### 5.1 Dataset mặc định

| Tham số | Giá trị |
|---|---|
| `GCS_BUCKET` | `aic_ai_2026` |
| `DATASET_ID` | `ai_challenge_2025` |
| `PROFILE_VERSION` | `autoshot_v1` |
| `KEYFRAMES_PREFIX` | `processed/keyframes` |
| `MANIFESTS_PREFIX` | `processed/keyframes_manifests` |

### 5.2 Các batch mặc định

```text
L21, L22, L23, L24, L26, L27, L28, L29, L30
```

### 5.3 Cách tìm manifest

Nếu `INPUT_MANIFEST_URI` được điền, notebook đọc trực tiếp manifest đó.

Nếu không, với mỗi batch notebook tìm manifest mới nhất dưới prefix:

```text
processed/keyframes_manifests/
  dataset={DATASET_ID}/
  batch={BATCH_ID}/
  profile={PROFILE_VERSION}/
```

Thứ tự ưu tiên file:

1. `shot_segments.csv`
2. `frames_manifest.jsonl`

Notebook chọn blob mới nhất theo thời gian cập nhật của GCS.

### 5.4 Authentication và thứ tự ưu tiên

#### Bucket name

Bucket được resolve theo thứ tự:

1. `GCS_BUCKET` trong Parameters.
2. Environment variable `GCS_BUCKET`.
3. Kaggle Secret có tên trong `GCS_BUCKET_SECRET_NAME`.

#### Credentials

Storage client được tạo theo thứ tự:

1. JSON credential trong environment variable `GCS_CREDENTIALS_JSON`.
2. JSON credential trong Kaggle Secret `GCS_CREDENTIALS_JSON`.
3. Service-account file tại `GCS_CREDENTIALS_FILE`.
4. Application Default Credentials của môi trường.

Cấu hình file mặc định:

```text
/kaggle/input/datasets/nguyentranthienan/
gcs-credentials-file/
gen-lang-client-0547522732-410672fac05f.json
```

---

## 6. Contract của manifest đầu vào

Notebook có thể đọc CSV hoặc JSONL. Sau khi normalize, mỗi frame record có các trường chính:

| Trường | Ý nghĩa |
|---|---|
| `dataset_id` | ID dataset |
| `batch_id` | Batch logic, ví dụ `L21` |
| `video_id` | ID video |
| `video_name` | Tên video nếu manifest cung cấp |
| `shot_id` | ID shot |
| `shot_start_frame` | Frame bắt đầu shot |
| `shot_end_frame` | Frame kết thúc shot |
| `frame_type` | Loại keyframe |
| `frame_idx` | Chỉ số frame trong video |
| `frame_sec` | Timestamp theo giây |
| `timestamp_ms` | Timestamp theo mili giây |
| `keyframe_id` | ID duy nhất của keyframe |
| `image_rel_path` | Đường dẫn tương đối |
| `image_gcs_uri` | URI đầy đủ `gs://...` |
| `image_storage_key` | Object key trong bucket |
| `fps` | FPS của video |
| `profile_version` | Phiên bản profile tạo keyframe |

### 6.1 Lọc record

Nếu manifest có cột `saved`, notebook chỉ giữ các hàng có giá trị tương đương `true`, `1` hoặc `yes`.

Các record không có `image_gcs_uri`, `gcs_uri` hoặc `image_uri` hợp lệ sẽ bị bỏ qua.

### 6.2 Giá trị fallback

- `video_id`: lấy từ manifest; nếu thiếu thì suy ra từ thư mục cha của ảnh.
- `keyframe_id`: nếu thiếu, tạo theo mẫu `{video_id}_F{frame_idx:06d}`.
- `timestamp_ms`: được tính từ `frame_sec`.

---

## 7. Quy trình hoạt động chi tiết

## 7.1 Bước 1 — Load Parameters

Cell Parameters:

1. Khai báo bucket, dataset, batch và manifest.
2. Khai báo prompt.
3. Khai báo `MODEL_REGISTRY`.
4. Chọn model active và model benchmark.
5. Khai báo output path, runtime path và execution controls.
6. Tạo object `cfg` từ tất cả biến viết hoa.
7. Kiểm tra model key có tồn tại trong registry.

Lưu ý: sau khi đổi `ACTIVE_MODEL_KEY`, phải chạy lại cell Parameters để cập nhật:

```python
EXTRACTOR_VERSION
MODEL_VERSION
cfg
```

## 7.2 Bước 2 — Cài dependency

Notebook cài:

```text
transformers>=4.57.0,<5
accelerate
qwen-vl-utils
bitsandbytes
sentencepiece
google-cloud-storage
pandas
tqdm
numpy
pillow
```

`flash-attn` là tùy chọn và chỉ nên cài khi GPU/runtime hỗ trợ.

Nếu package được nâng cấp trong session đang chạy, cần restart kernel rồi chạy lại từ Parameters.

## 7.3 Bước 3 — Khởi tạo GCS và helper

Cell Shared Helpers cung cấp:

- Tạo GCS client.
- Resolve bucket.
- Tìm manifest mới nhất.
- Đọc CSV/JSONL.
- Normalize record.
- Tạo run ID.
- Tạo cấu trúc thư mục run.
- Download frame song song.
- Ghi JSON, JSONL và CSV.
- Upload artifact.
- Tìm annotation cũ để resume.
- Dry run.

## 7.4 Bước 4 — Model loading

`load_caption_model()` thực hiện:

1. Lấy model spec từ registry.
2. Resolve device.
3. Resolve dtype.
4. Tạo quantization config nếu cần.
5. Load model theo backend.
6. Load `AutoProcessor` tương ứng.
7. Đặt model ở chế độ `eval()`.
8. Đặt tokenizer padding về bên trái nếu tokenizer tồn tại.
9. Trả về model context chứa model, processor, backend, dtype và device.

### Device selection

| `DEVICE` | Hành vi |
|---|---|
| `auto` | Chọn CUDA nếu có, nếu không dùng CPU |
| `cuda` | Bắt buộc CUDA; báo lỗi nếu CUDA không khả dụng |
| `cpu` | Chạy CPU |

### Dtype selection

| Điều kiện | Dtype |
|---|---|
| CPU | `float32` |
| CUDA hỗ trợ BF16 | `bfloat16` |
| CUDA không hỗ trợ BF16 | `float16` |

### Quantization

| Giá trị | Hành vi |
|---|---|
| `none` | Không dùng BitsAndBytes quantization |
| `4bit` | NF4, double quantization, BF16/FP16 compute |
| `8bit` | BitsAndBytes 8-bit |

Quantization chỉ được phép khi dùng CUDA.

## 7.5 Bước 5 — Frame download

`download_frames()` dùng `ThreadPoolExecutor` với:

```python
DOWNLOAD_WORKERS = 8
```

Mỗi frame được tải về:

```text
{RUN_ROOT}/{run_id}/frames/{video_id}/{image_filename}
```

Nếu file đã tồn tại và kích thước lớn hơn 0, notebook không tải lại.

Thời gian download của từng frame được lưu tạm trong `download_ms`, nhưng trường này chưa được đưa vào metric cuối của extractor.

## 7.6 Bước 6 — Preprocessing và inference Qwen

Đối với Qwen2.5-VL và Qwen3-VL:

1. Chuyển local path thành file URI.
2. Tạo conversation theo chat template gồm image và prompt.
3. Dùng `processor.apply_chat_template()` để tạo text input.
4. Dùng `qwen_vl_utils.process_vision_info()` để xử lý ảnh.
5. Với Qwen3-VL, lấy `patch_size` từ image processor và truyền vào `process_vision_info()`.
6. Với Qwen3-VL, đặt `do_resize=False` khi gọi processor vì ảnh đã được qwen-vl-utils resize.
7. Chuyển tensor về input device.
8. Gọi `model.generate()` với:
   - `do_sample=False`
   - `use_cache=True`
   - `max_new_tokens` theo registry
9. Loại bỏ token input khỏi output token.
10. Decode phần caption mới sinh.

## 7.7 Bước 7 — Preprocessing và inference BLIP-2

Đối với BLIP-2:

1. Mở ảnh bằng PIL.
2. Convert sang RGB.
3. Processor nhận danh sách ảnh và prompt `a photo of`.
4. Floating tensor được chuyển sang dtype của runtime.
5. Gọi `model.generate()` với tham số beam, repetition và length penalty trong registry.
6. Decode output.
7. Đóng toàn bộ đối tượng PIL trong khối `finally`.

## 7.8 Bước 8 — Hai lớp batch

Notebook có hai mức batch khác nhau:

### Pipeline batch

```python
PIPELINE_BATCH_SIZE = 8
```

Đây là số record được đưa vào một lần gọi `extract_task_batch()` và là đơn vị ghi metric/error.

### Model micro-batch

Mỗi model có `batch_size` riêng trong `MODEL_REGISTRY`:

- Qwen2.5-VL: 4
- BLIP-2: 8
- Qwen3-VL: 1

`generate_caption_batch()` tiếp tục chia pipeline batch thành micro-batch phù hợp với model.

Ví dụ với `PIPELINE_BATCH_SIZE=8` và Qwen3 `batch_size=1`, một pipeline batch sẽ thực hiện tám lần generation nội bộ.

## 7.9 Bước 9 — Tạo annotation

Mỗi caption được ghép với metadata frame và tạo thành annotation JSON.

Các trường caption-specific:

```json
{
  "kind": "captioning",
  "caption": "...",
  "text_value": "...",
  "json_value": {
    "caption": "...",
    "model_key": "qwen25_vl_3b",
    "model_id": "Qwen/Qwen2.5-VL-3B-Instruct",
    "backend": "qwen25_vl",
    "quantization": "none"
  }
}
```

Ngoài ra annotation giữ các field chung phục vụ downstream pipeline:

```text
dataset_id, batch_id, video_id, keyframe_id, frame_id,
shot_id, frame_idx, frame_sec, timestamp_ms, frame_type,
image_gcs_uri, image_storage_key, ocr_texts,
detected_objects, object_counts, detections, confidence,
model_version, annotation_version, run_id, created_at
```

`confidence` hiện được đặt cố định là `1.0`; đây không phải confidence được model hiệu chỉnh.

## 7.10 Bước 10 — Ghi artifact

Sau mỗi pipeline batch thành công:

- Append output vào `annotations.jsonl`.
- Append batch metric vào `metrics.csv`.

Nếu batch lỗi:

- Ghi mỗi frame vào `errors.jsonl` cùng error message.
- Ghi metric failed vào `metrics.csv`.
- Nếu lỗi chứa `out of memory`, gọi `torch.cuda.empty_cache()`.
- Nếu `FAIL_FAST=True`, exception được raise ngay.
- Nếu `FAIL_FAST=False`, pipeline tiếp tục với batch sau.

## 7.11 Bước 11 — Giải phóng model

Sau benchmark từng model hoặc sau extractor run:

1. Xóa reference model và processor.
2. Chạy garbage collection.
3. Synchronize CUDA.
4. Xóa CUDA cache.
5. Chạy garbage collection lần nữa.

Cơ chế này giảm nguy cơ cộng dồn VRAM khi benchmark nhiều model.

---

## 8. Các chế độ chạy

## 8.1 Dry Run

```python
dry_summary = dry_run(cfg, max_frames=cfg.DRY_RUN_MAX_FRAMES)
```

Mặc định:

```python
DRY_RUN_MAX_FRAMES = 20
```

Dry Run thực hiện:

- Kết nối GCS.
- Resolve bucket.
- Tìm manifest.
- Đọc và normalize tối đa 20 record.
- Trả về sample record.

Dry Run không:

- Tải ảnh.
- Load model.
- Sinh caption.
- Upload output.

Output mẫu:

```python
{
    "status": "DRY_RUN_OK",
    "bucket": "...",
    "batches": [...],
    "planned_frames": 20,
    "sample_records": [...]
}
```

## 8.2 Benchmark 5 video

Cấu hình mặc định:

```python
BENCHMARK_BATCHES = ["L21"]
BENCHMARK_VIDEO_LIMIT = 5
BENCHMARK_FRAMES_PER_VIDEO = 1
BENCHMARK_SAVE_CSV = True
```

Quy trình:

1. Đọc toàn bộ manifest của `BENCHMARK_BATCHES`.
2. Group record theo `video_id`.
3. Sắp xếp `video_id` và chọn năm video đầu tiên.
4. Sắp xếp keyframe của mỗi video theo `frame_idx`.
5. Nếu lấy một frame/video, chọn frame ở vị trí giữa danh sách keyframe.
6. Nếu lấy nhiều frame/video, chọn frame trải đều bằng `numpy.linspace()`.
7. Tải các frame đã chọn một lần.
8. Với từng model:
   - Reset peak-memory counter.
   - Load model.
   - Đo load time.
   - Caption cùng một tập ảnh.
   - Đo inference time và VRAM.
   - Ghi kết quả.
   - Unload model trước khi chuyển sang model tiếp theo.
9. Tạo ba DataFrame.
10. Lưu ba file CSV nếu `BENCHMARK_SAVE_CSV=True`.

Nếu batch không có đủ năm `video_id` khác nhau, benchmark báo lỗi.

### DataFrame 1 — `benchmark_long_df`

Mỗi dòng là một cặp `(frame, model)`.

Các cột chính:

```text
batch_id, video_id, keyframe_id, frame_idx, frame_sec,
image_gcs_uri, model_key, model_id, backend, quantization,
caption, model_load_seconds, inference_seconds_total,
seconds_per_frame, total_seconds, model_loaded_vram_gb,
peak_vram_allocated_gb, peak_vram_reserved_gb
```

### DataFrame 2 — `benchmark_comparison_df`

Mỗi dòng là một keyframe. Các metric và caption được pivot thành cột theo model, ví dụ:

```text
caption__qwen25_vl_3b
caption__blip2_opt_2_7b
caption__qwen3_vl_8b
seconds_per_frame__qwen25_vl_3b
peak_vram_allocated_gb__qwen3_vl_8b
```

Bảng này phù hợp để đọc output của các model cạnh nhau.

### DataFrame 3 — `benchmark_summary_df`

Mỗi dòng là summary của một model:

```text
model_key, model_id, backend, quantization,
videos, frames, model_load_seconds,
inference_seconds_total, seconds_per_frame,
total_seconds, model_loaded_vram_gb,
peak_vram_allocated_gb, peak_vram_reserved_gb
```

### CSV benchmark

```text
benchmark_long.csv
benchmark_comparison.csv
benchmark_model_summary.csv
```

Các file benchmark hiện được lưu cục bộ dưới run directory. Hàm benchmark không gọi hàm upload standard artifacts, vì vậy CSV benchmark không tự động upload lên GCS.

## 8.3 Demo Run

Cấu hình mặc định:

```python
DEMO_BATCHES = ["L21"]
DEMO_MAX_FRAMES = 32
```

Demo Run chạy end-to-end trên tối đa 32 frame bằng `ACTIVE_MODEL_KEY`:

```python
demo_summary = run_demo(cfg)
```

Đây là bước cần chạy trước Full Run để kiểm tra:

- Model có load được hay không.
- VRAM có đủ hay không.
- Caption output có đúng ngôn ngữ và độ dài hay không.
- Annotation contract có đúng không.
- Artifact có upload lên đúng GCS prefix không.

## 8.4 Full Run

Full Run được bảo vệ bằng safety gate:

```python
CONFIRM_FULL_RUN = ""
```

Để mở khóa:

```python
CONFIRM_FULL_RUN = "RUN_FULL_DATASET"
```

Sau đó phải chạy lại cell Parameters rồi mới chạy:

```python
full_summaries = run_full(cfg)
```

Full Run xử lý từng batch trong `BATCHES` bằng một run riêng. Vì vậy mỗi batch có run ID, local directory, summary và GCS prefix riêng.

---

## 9. Resume và idempotency

Các tham số:

```python
SKIP_EXISTING = True
OVERWRITE = False
RESUME_ANNOTATIONS_URI = ""
```

### Cách hoạt động

1. Nếu `SKIP_EXISTING=False` hoặc `OVERWRITE=True`, resume filter bị tắt.
2. Nếu `RESUME_ANNOTATIONS_URI` được điền, notebook đọc file annotation đó.
3. Nếu không, notebook tìm `annotations.jsonl` mới nhất dưới output prefix tương ứng.
4. Notebook đọc các dòng không có `error` và lấy `keyframe_id`.
5. Các keyframe đã có trong tập processed bị loại khỏi run mới.

### Tách kết quả theo model

```python
EXTRACTOR_VERSION = f"fe-captioning-v1-{ACTIVE_MODEL_KEY}"
```

Model key được đưa vào extractor version, nên annotation của các model khác nhau không bị resume nhầm vào cùng namespace, với điều kiện cell Parameters được chạy lại sau khi đổi `ACTIVE_MODEL_KEY`.

---

## 10. Metric thời gian và VRAM

## 10.1 Metric benchmark

| Metric | Ý nghĩa |
|---|---|
| `model_load_seconds` | Thời gian từ trước khi load đến khi model sẵn sàng |
| `inference_seconds_total` | Tổng thời gian caption toàn bộ tập benchmark |
| `seconds_per_frame` | `inference_seconds_total / frames_count` |
| `total_seconds` | Load time + inference time |
| `model_loaded_vram_gb` | PyTorch allocated memory ngay sau khi model load |
| `peak_vram_allocated_gb` | Peak tensor memory do PyTorch cấp phát |
| `peak_vram_reserved_gb` | Peak CUDA caching allocator memory được PyTorch reserve |

## 10.2 Metric extractor

Mỗi pipeline batch ghi:

```text
run_id, batch_index, model_key, model_id,
frames, processed, failed, seconds,
frames_per_second, peak_vram_allocated_gb,
peak_vram_reserved_gb
```

Summary cuối run ghi:

```text
model_load_seconds, duration_seconds,
peak_vram_allocated_gb, peak_vram_reserved_gb
```

## 10.3 Cách đo

Notebook gọi `torch.cuda.synchronize()` trước hoặc sau các đoạn cần đo. Điều này giúp tránh đo thiếu thời gian do CUDA chạy bất đồng bộ.

Peak-memory counter được reset trước khi benchmark từng model.

## 10.4 Ý nghĩa và giới hạn của số VRAM

Các số VRAM là số liệu từ PyTorch CUDA allocator, không phải toàn bộ GPU memory hiển thị bởi `nvidia-smi`.

- `allocated`: memory đang được tensor sử dụng.
- `reserved`: memory PyTorch giữ trong cache để tái sử dụng.
- Metric được cộng trên tất cả GPU visible.
- Driver memory, CUDA context và memory do thư viện ngoài PyTorch sử dụng có thể không được phản ánh đầy đủ.

---

## 11. Cấu trúc output

## 11.1 Local run layout

```text
/kaggle/working/feature_extractor_runs/
└── {run_id}/
    ├── frames/
    │   └── {video_id}/
    │       └── {image_filename}
    ├── artifacts/
    │   ├── annotations.jsonl
    │   ├── errors.jsonl
    │   ├── metrics.csv
    │   ├── summary.json
    │   ├── benchmark_long.csv
    │   ├── benchmark_comparison.csv
    │   └── benchmark_model_summary.csv
    └── run.log
```

Không phải run nào cũng có toàn bộ file. Benchmark có CSV benchmark; Demo/Full có standard extractor artifacts.

## 11.2 GCS output prefix

```text
features/extractors/
dataset={DATASET_ID}/
batch={BATCH_ID}/
frame_profile={PROFILE_VERSION}/
extractor=captioning/
extractor_version={EXTRACTOR_VERSION}/
run_id={RUN_ID}/
```

Ví dụ:

```text
gs://aic_ai_2026/features/extractors/
dataset=ai_challenge_2025/
batch=L21/
frame_profile=autoshot_v1/
extractor=captioning/
extractor_version=fe-captioning-v1-qwen25_vl_3b/
run_id=demo_20260724T.../
```

## 11.3 Standard artifacts upload

Khi cả hai tham số sau là `True`:

```python
UPLOAD_TO_GCS = True
UPLOAD_RUN_ARTIFACTS = True
```

Notebook upload các file tồn tại:

- `annotations.jsonl`
- `errors.jsonl`
- `metrics.csv`
- `summary.json`
- `run.log`

Nếu run không có lỗi, notebook tạo thêm marker:

```text
_SUCCESS
```

## 11.4 Cleanup local frames

```python
CLEANUP_LOCAL_FRAMES_AFTER_RUN = True
```

Sau Demo hoặc Full Run, thư mục `frames/` được xóa để tiết kiệm Kaggle disk. Artifact vẫn được giữ.

Benchmark hiện không thực hiện cleanup frame directory sau khi hoàn tất.

---

## 12. Hướng dẫn chạy trên Kaggle

## 12.1 Chuẩn bị runtime

Trong Kaggle Notebook Settings:

1. Chọn GPU accelerator.
2. Bật Internet để tải package và model từ Hugging Face.
3. Attach dataset chứa service-account JSON hoặc tạo Kaggle Secret.
4. Đảm bảo service account có quyền đọc manifest/keyframe và quyền ghi output nếu upload được bật.

## 12.2 Kiểm tra Parameters

Tối thiểu cần kiểm tra:

```python
GCS_BUCKET
GCS_CREDENTIALS_FILE
DATASET_ID
PROFILE_VERSION
BATCHES
ACTIVE_MODEL_KEY
```

Nếu dùng manifest cụ thể:

```python
INPUT_MANIFEST_URI = "gs://bucket/path/shot_segments.csv"
```

## 12.3 Thứ tự chạy cell

Chạy đúng thứ tự:

1. `1. Parameters`
2. `2. Install Dependencies`
3. Restart kernel nếu package vừa được nâng cấp.
4. Chạy lại `1. Parameters`.
5. `3. Shared GCS, Manifest, Run Helpers`
6. `3b. Captioning Model Registry And Extraction Logic`
7. `4. Dry Run`
8. `Benchmark 5 videos`
9. `5. Demo Run`
10. `6. Full Run` khi đã xác nhận output demo.
11. `7. Inspect Latest Local Artifacts`

## 12.4 Chạy Dry Run

```python
dry_summary = dry_run(cfg, max_frames=cfg.DRY_RUN_MAX_FRAMES)
dry_summary
```

Cần xác nhận:

- `status == "DRY_RUN_OK"`
- Bucket đúng.
- Batch đúng.
- `image_gcs_uri` của sample record hợp lệ.
- `keyframe_id`, `video_id`, `frame_idx` được parse đúng.

## 12.5 Chạy benchmark

```python
benchmark_long_df, benchmark_comparison_df, benchmark_summary_df = \
    benchmark_caption_models(cfg)
```

Xem summary:

```python
display(benchmark_summary_df)
```

Xem caption cạnh nhau:

```python
display(benchmark_comparison_df)
```

Xem raw rows:

```python
display(benchmark_long_df)
```

## 12.6 Chạy riêng Qwen2.5-VL-3B

```python
ACTIVE_MODEL_KEY = "qwen25_vl_3b"
```

Chạy lại Parameters, sau đó chạy Demo Run.

## 12.7 Chạy riêng BLIP-2 OPT-2.7B

```python
ACTIVE_MODEL_KEY = "blip2_opt_2_7b"
```

Chạy lại Parameters, sau đó chạy Demo Run.

## 12.8 Chạy riêng Qwen3-VL-8B

```python
ACTIVE_MODEL_KEY = "qwen3_vl_8b"
```

Mặc định model được load 4-bit. Chạy lại Parameters rồi chạy Demo Run.

Nếu GPU đủ VRAM và cần full precision tương đối đồng nhất với model khác:

```python
MODEL_REGISTRY["qwen3_vl_8b"]["quantization"] = "none"
```

Sau đó tạo lại `cfg` bằng cách chạy lại toàn bộ cell Parameters.

## 12.9 Chạy Full Run

Sau khi Demo Run thành công:

```python
CONFIRM_FULL_RUN = "RUN_FULL_DATASET"
```

Chạy lại Parameters rồi chạy:

```python
full_summaries = run_full(cfg)
```

Không nên mở khóa Full Run trước khi kiểm tra caption, VRAM, output prefix và resume behavior trên Demo Run.

---

## 13. Cách thêm model

## 13.1 Thêm model dùng backend đã có

Nếu model mới tương thích hoàn toàn với một trong các backend hiện tại, thêm một entry vào registry.

Ví dụ model Qwen2.5-VL khác:

```python
MODEL_REGISTRY["my_qwen25_model"] = {
    "backend": "qwen25_vl",
    "hf_id": "organization/model-name",
    "prompt": CAPTION_PROMPT_VI,
    "batch_size": 2,
    "max_new_tokens": 80,
    "quantization": "4bit",
    "max_pixels": 1024 * 1024,
}
```

Sau đó:

```python
BENCHMARK_MODEL_KEYS.append("my_qwen25_model")
```

Hoặc dùng cho Demo/Full:

```python
ACTIVE_MODEL_KEY = "my_qwen25_model"
```

## 13.2 Thêm kiến trúc mới

Nếu kiến trúc mới không tương thích với ba backend hiện có, cần sửa hai vị trí:

### Loader

Thêm branch trong:

```python
load_caption_model()
```

Ví dụ:

```python
elif backend == "new_backend":
    model = NewModelClass.from_pretrained(model_id, **common_kwargs)
```

### Inference

Viết hàm preprocessing/generation mới và thêm branch trong:

```python
generate_caption_batch()
```

Ví dụ:

```python
elif backend == "new_backend":
    captions.extend(_generate_new_backend_batch(path_batch, ctx))
```

Backend mới phải trả về đúng một caption cho mỗi input image.

## 13.3 Checklist khi thêm model

- Model ID tải được bằng Transformers.
- Processor tương thích.
- Dtype phù hợp GPU.
- Quantization tương thích kiến trúc.
- Batch size không gây OOM.
- Output count bằng input count.
- Caption được strip và là string.
- Model được unload sau benchmark.
- `ACTIVE_MODEL_KEY` hoặc `BENCHMARK_MODEL_KEYS` được cập nhật.
- Cell Parameters được chạy lại.

---

## 14. Khuyến nghị tối ưu VRAM và tốc độ

### Khi bị CUDA OOM

Giảm theo thứ tự:

1. `batch_size` của model trong registry.
2. `PIPELINE_BATCH_SIZE`.
3. `max_pixels`.
4. `max_new_tokens`.
5. Chuyển model sang `4bit` hoặc `8bit` nếu kiến trúc hỗ trợ.
6. Chỉ benchmark một số model tại một thời điểm.
7. Tắt `USE_FLASH_ATTENTION_2` nếu runtime không tương thích.

### Cấu hình an toàn ban đầu

| Model | Gợi ý khởi đầu |
|---|---|
| Qwen2.5-VL-3B | Batch 1–4, không quantize |
| BLIP-2 OPT-2.7B | Batch 2–8 tùy GPU |
| Qwen3-VL-8B | 4-bit, batch 1 |

Các giá trị thực tế phải được xác nhận bằng benchmark trên đúng GPU Kaggle được cấp. Notebook không chứa số benchmark thực nghiệm cố định.

### So sánh công bằng

Benchmark mặc định chưa hoàn toàn đồng precision:

- Qwen2.5-VL: không quantize.
- BLIP-2: không quantize.
- Qwen3-VL: 4-bit.

Do đó số tốc độ, VRAM và chất lượng của Qwen3 chịu ảnh hưởng bởi quantization. Khi nghiên cứu trade-off model thuần túy, nên ghi rõ precision hoặc chạy thêm một cấu hình Qwen3 `quantization="none"` trên GPU đủ lớn.

---

## 15. Xử lý lỗi thường gặp

## 15.1 Không tìm thấy manifest

Thông báo dạng:

```text
No shot_segments.csv or frames_manifest.jsonl found under gs://...
```

Kiểm tra:

- `DATASET_ID`
- `PROFILE_VERSION`
- `BATCHES`
- `MANIFESTS_PREFIX`
- Quyền list/read GCS

Hoặc đặt trực tiếp:

```python
INPUT_MANIFEST_URI = "gs://.../manifest.csv"
```

## 15.2 GCS authentication thất bại

Kiểm tra:

- File credential có tồn tại trong Kaggle input không.
- JSON secret có hợp lệ không.
- Service account có quyền đọc bucket không.
- Bucket name không chứa sai prefix/path.

## 15.3 Qwen3 class không tồn tại

Đảm bảo cell dependency đã cài phiên bản Transformers phù hợp và restart kernel sau khi upgrade.

## 15.4 Quantization chạy trên CPU

`4bit` và `8bit` trong notebook yêu cầu CUDA. Khi chạy CPU, đặt:

```python
quantization = "none"
```

## 15.5 Benchmark không đủ năm video

Giảm:

```python
BENCHMARK_VIDEO_LIMIT
```

Hoặc chọn batch có ít nhất năm `video_id` khác nhau.

## 15.6 Caption count mismatch

Lỗi này xảy ra khi backend trả về số caption khác số ảnh đầu vào. Cần kiểm tra preprocessing, batching và decode của backend vừa thêm.

## 15.7 CUDA OOM

Giảm model batch, pipeline batch, `max_pixels`, token output hoặc bật quantization. Sau OOM, nên restart kernel nếu GPU memory không được giải phóng hoàn toàn.

---

## 16. Giới hạn và điểm cần lưu ý trong implementation hiện tại

1. **Không có temporal context:** mỗi keyframe được caption độc lập; model không biết frame trước/sau.
2. **Không có metric chất lượng tự động:** benchmark chỉ so sánh output, time và VRAM; chưa có CIDEr, BLEU, METEOR, SPICE, CLIPScore hoặc human rating.
3. **Prompt không đồng nhất:** Qwen dùng prompt tiếng Việt chi tiết, BLIP-2 dùng `a photo of`; chất lượng không được so sánh trong điều kiện prompt tương đương.
4. **Precision không đồng nhất:** Qwen3 mặc định 4-bit, hai model còn lại không quantize.
5. **Chọn video theo thứ tự ID:** benchmark lấy năm `video_id` đầu tiên sau khi sort, không random và không stratified.
6. **Một frame/video mặc định:** kết quả benchmark nhỏ và có thể không đại diện cho toàn dataset.
7. **Metric VRAM là PyTorch allocator metric:** không tương đương hoàn toàn `nvidia-smi`.
8. **Không cố định random seed:** generation dùng `do_sample=False`, nhưng notebook chưa cấu hình deterministic seed cho toàn runtime.
9. **`confidence=1.0` là placeholder:** không phản ánh độ tin cậy thực của caption.
10. **Benchmark artifact chỉ lưu local:** chưa tự động upload CSV benchmark lên GCS.
11. **`UPLOAD_WORKERS` được khai báo nhưng chưa được sử dụng:** standard artifacts hiện được upload tuần tự.
12. **`KEYFRAMES_PREFIX` được khai báo nhưng không được dùng trong discovery:** ảnh được xác định từ `image_gcs_uri` trong manifest.
13. **Download time chưa có trong summary:** `download_ms` được tạo trên record nhưng không được tổng hợp thành metric chính.
14. **Benchmark không cleanup frame directory:** có thể giữ ảnh local đến hết Kaggle session.
15. **Model registry chỉ đơn giản hóa model tương thích backend:** kiến trúc mới vẫn cần viết loader và inference adapter.
16. **Đổi active model phải chạy lại Parameters:** nếu không, `cfg`, `EXTRACTOR_VERSION` và output namespace có thể vẫn mang model cũ.
17. **Full Run chạy từng batch độc lập:** phù hợp resume và quản lý artifact, nhưng model được load lại cho từng batch, làm tăng tổng model-load overhead.

---

## 17. Đề xuất nâng cấp tiếp theo

### Mức ưu tiên cao

1. Thêm `random_seed` và tùy chọn chọn video benchmark ngẫu nhiên có seed.
2. Thêm `download_seconds_total` và end-to-end throughput.
3. Upload benchmark CSV và benchmark summary lên GCS.
4. Thêm cột `tokens_generated` và `tokens_per_second` cho Qwen.
5. Chuẩn hóa prompt/ngôn ngữ giữa các model khi benchmark chất lượng.
6. Cho phép benchmark nhiều cấu hình của cùng model, ví dụ Qwen3 4-bit và BF16.
7. Thêm human evaluation schema: correctness, relevance, detail, hallucination và language quality.

### Mức ưu tiên trung bình

1. Thêm CLIPScore hoặc embedding similarity nếu có reference caption hoặc retrieval objective.
2. Thêm frame-level error handling thay vì fail cả pipeline batch.
3. Tái sử dụng model giữa các batch Full Run để giảm model-load overhead.
4. Thêm automatic adaptive batch size khi OOM.
5. Ghi package version, GPU name, CUDA version và Transformers version vào summary.
6. Thêm checksum hoặc manifest version vào output để tăng reproducibility.

### Hướng phát triển video captioning

Để caption có temporal context, có thể mở rộng record từ một ảnh thành một đoạn clip hoặc một nhóm keyframe:

```text
shot keyframes → temporal sampling → multi-image/video VLM input
              → shot-level caption → video-level aggregation
```

Khi đó cần bổ sung:

- `frames_per_shot`
- temporal ordering
- clip duration
- multi-image prompt
- shot-level output contract
- metric latency theo clip thay vì theo frame

---

## 18. Checklist vận hành

### Trước khi chạy

- [ ] Kaggle GPU đã bật.
- [ ] Internet đã bật.
- [ ] Credential GCS hợp lệ.
- [ ] Bucket, dataset, profile và batch đúng.
- [ ] `ACTIVE_MODEL_KEY` đúng.
- [ ] Cell Parameters đã chạy lại sau khi sửa model.
- [ ] Dependency đã cài và kernel đã restart nếu cần.

### Trước Full Run

- [ ] Dry Run thành công.
- [ ] Benchmark chạy được ít nhất model dự kiến dùng.
- [ ] Demo Run tạo caption hợp lệ.
- [ ] Không có CUDA OOM.
- [ ] `annotations.jsonl` đúng contract.
- [ ] GCS output prefix đúng.
- [ ] Resume filter hoạt động đúng.
- [ ] Disk local đủ cho keyframe tạm.
- [ ] Đã đặt `CONFIRM_FULL_RUN = "RUN_FULL_DATASET"`.

### Sau khi chạy

- [ ] Kiểm tra `summary.json`.
- [ ] Kiểm tra `failed_frames`.
- [ ] Kiểm tra `errors.jsonl` nếu tồn tại.
- [ ] Kiểm tra `_SUCCESS` trên GCS.
- [ ] Kiểm tra caption ngẫu nhiên ở nhiều video.
- [ ] Lưu benchmark summary cùng thông tin GPU/runtime.

---

## 19. Kết luận

`fe-captioning-v1.ipynb` cung cấp một pipeline captioning keyframe có cấu trúc tương đối hoàn chỉnh cho Kaggle và GCS. Điểm mạnh chính là model registry, backend abstraction, chạy dry/demo/full, resume theo `keyframe_id`, artifact contract thống nhất và benchmark tuần tự giúp tránh giữ nhiều model trong VRAM.

Cấu hình mặc định phù hợp để bắt đầu đánh giá ba hướng:

- Qwen2.5-VL-3B làm lựa chọn active cân bằng.
- BLIP-2 OPT-2.7B làm baseline captioning truyền thống.
- Qwen3-VL-8B 4-bit làm model lớn hơn trong giới hạn VRAM.

Tuy nhiên, benchmark hiện mới đo hiệu năng hệ thống và hỗ trợ xem caption thủ công. Trước khi chọn model cho production hoặc AIC retrieval pipeline, cần chạy trên GPU thực tế, ghi thông tin runtime, đánh giá hallucination, độ chi tiết, ngôn ngữ, tốc độ end-to-end và chất lượng retrieval downstream.

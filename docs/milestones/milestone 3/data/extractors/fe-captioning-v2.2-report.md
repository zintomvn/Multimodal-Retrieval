# Technical Report — `fe-captioning-v2.2.ipynb`

## 1. Phạm vi cải tiến

`fe-captioning-v2.2.ipynb` tối ưu captioning keyframe trên Kaggle **2× NVIDIA T4**. Các thay đổi tập trung vào:

- chạy hai GPU theo kiểu **model replication** thay vì chia layer;
- đưa batch ảnh thực vào model;
- xử lý ảnh song song trên CPU;
- giảm visual token và số token sinh;
- giảm số lần đồng bộ CUDA khi ghi metric;
- benchmark theo throughput và VRAM từng GPU.

Cấu hình mặc định:

```python
ACTIVE_MODEL_KEY = "qwen3_vl_4b"
GPU_EXECUTION_MODE = "replicated"
GPU_IDS = [0, 1]
PIPELINE_BATCH_SIZE = 8
```

---

## 2. Kỹ thuật cải tiến chính

### 2.1. Replicated multi-GPU inference

Mỗi T4 giữ một bản model độc lập:

```text
GPU 0: Qwen3-VL-4B replica 0 → 4 ảnh
GPU 1: Qwen3-VL-4B replica 1 → 4 ảnh
```

`load_caption_model_pool()` hỗ trợ:

| Chế độ       | Cách chạy                               | Mục đích                               |
| ------------ | --------------------------------------- | -------------------------------------- |
| `replicated` | Một model trên mỗi GPU                  | Tăng throughput, mặc định cho 4B 4-bit |
| `single`     | Một model trên GPU đầu tiên             | Debug hoặc benchmark một GPU           |
| `sharded`    | Một model chia bằng `device_map="auto"` | Chỉ dùng khi model không vừa một GPU   |

`generate_caption_batch_parallel()` chia batch theo dung lượng từng GPU, chạy đồng thời bằng `ThreadPoolExecutor`, sau đó ghép caption về đúng thứ tự input.

### 2.2. Batch inference thực

Phiên bản mới truyền cả danh sách ảnh vào model pool:

```python
generate_caption_batch_parallel(image_paths, model_pool)
```

Với cấu hình mặc định:

```text
per-GPU batch_size = 4
số GPU = 2
PIPELINE_BATCH_SIZE = 8
```

Một outer batch gồm 8 ảnh được chia thành hai batch 4 ảnh chạy đồng thời. Batch cuối được cân bằng giữa các GPU và không vượt dung lượng của từng replica.

### 2.3. CPU preprocessing song song

Qwen cần đọc ảnh, decode, resize và tạo vision input trước khi inference. Mỗi model replica dùng thread pool riêng:

```python
"preprocess_workers": 2
```

`process_vision_info()` được chạy song song nhưng giới hạn worker theo kích thước batch. Cách này giảm thời gian GPU chờ CPU mà không tạo quá nhiều thread.

### 2.4. Tối ưu input và decoding

Cấu hình Qwen3-VL-4B:

```python
"batch_size": 4
"max_new_tokens": 256
"quantization": "4bit"
"max_pixels": 640 * 640
"preprocess_workers": 2
```

Tác dụng:

- **4-bit NF4:** đủ VRAM để đặt một model trên mỗi T4;
- **640×640:** giảm CPU resize, visual token và activation;
- **64 token:** phù hợp caption 1–3 câu, giảm thời gian sinh tự hồi quy;
- **batch 4/GPU:** cấu hình khởi đầu an toàn để benchmark.

Khi cần đọc chữ nhỏ, có thể tăng `max_pixels` lên `768 * 768` và giảm batch nếu OOM.

### 2.5. Benchmark đúng tải thực tế

```python
BENCHMARK_FRAME_LIMIT = 32
BENCHMARK_WARMUP = True
BENCHMARK_SHOW_IMAGES = False
```

Benchmark:

- chọn 32 frame trải đều trên manifest;
- warm-up một batch trước khi đo;
- reset peak VRAM sau warm-up;
- chạy batch thật trên model pool;
- ghi `frames_per_second`, execution mode, replica count và VRAM từng GPU;
- không hiển thị ảnh trong lúc đo để tránh CPU overhead.

Metric cần ưu tiên:

```text
frames_per_second
seconds_per_frame
gpu_0_peak_allocated_gb
gpu_1_peak_allocated_gb
failed batches
```

GPU utilization tức thời không phải tiêu chí duy nhất vì autoregressive generation dao động theo từng token.

### 2.6. Giảm CUDA synchronization

Thu thập VRAM có thể buộc GPU đồng bộ. Pipeline chỉ lấy memory metric định kỳ:

```python
METRICS_EVERY_N_BATCHES = 10
```

Memory được đo ở batch đầu, mỗi 10 batch, batch cuối, batch lỗi và khi kết thúc run.

### 2.7. Quản lý bộ nhớ và lỗi

- Batch được tạo bằng generator, không tạo trước danh sách toàn bộ batch.
- Sau run, executor và các model replica được giải phóng.
- CUDA cache được dọn sau khi unload model.
- Khi OOM, lỗi được ghi theo batch và pipeline tiếp tục nếu `FAIL_FAST=False`.
- Số caption luôn được kiểm tra phải bằng số ảnh input.

---

## 3. Cấu hình khuyến nghị

### Kaggle 2×T4 — Qwen3-VL-4B

```python
ACTIVE_MODEL_KEY = "qwen3_vl_4b"

MODEL_REGISTRY["qwen3_vl_4b"].update({
    "batch_size": 4,
    "max_new_tokens": 64,
    "quantization": "4bit",
    "max_pixels": 640 * 640,
    "preprocess_workers": 2,
})

GPU_EXECUTION_MODE = "replicated"
GPU_IDS = [0, 1]
PIPELINE_BATCH_SIZE = 8
METRICS_EVERY_N_BATCHES = 10
```

### Debug trên một GPU

```python
GPU_EXECUTION_MODE = "single"
GPU_IDS = [0]
PIPELINE_BATCH_SIZE = 4
```

### Model không vừa một T4

```python
GPU_EXECUTION_MODE = "sharded"
GPU_IDS = [0, 1]
```

`sharded` chỉ dùng để đủ VRAM; không phải chế độ ưu tiên cho throughput.

---

## 4. Cách chạy quan trọng

### 4.1. Chuẩn bị Kaggle

1. Chọn accelerator `GPU T4 ×2`.
2. Bật Internet.
3. Tạo Kaggle Secrets:

```text
GCS_BUCKET
GCS_CREDENTIALS_JSON
```

Không ghi service-account JSON trực tiếp vào notebook.

### 4.2. Thứ tự chạy cell

```text
1. Install Dependencies Safely
2. Parameters
3. Shared GCS, Manifest, Run Helpers
3b. Captioning Model Registry And Extraction Logic
4. Dry Run
4b. Benchmark 32 Frames
5. Demo Run
6. Full Run
```

Sau khi đổi model, batch size, GPU mode, prompt hoặc quantization, phải chạy lại cell **Parameters** và **3b**.

### 4.3. Dry Run

```python
dry_summary = dry_run(cfg, max_frames=cfg.DRY_RUN_MAX_FRAMES)
dry_summary
```

Xác nhận bucket, manifest, batch và các trường `image_gcs_uri`, `video_id`, `keyframe_id`. Dry Run không load model.

### 4.4. Benchmark

```python
benchmark_long_df, benchmark_comparison_df, benchmark_summary_df = (
    benchmark_caption_models(cfg)
)

display(benchmark_summary_df)
```

Kết quả đúng cần có:

```text
gpu_execution_mode = replicated
replicas = 2
gpu_ids = 0,1
global_batch_size = 8
```

Có thể thử tăng dần:

```text
per-GPU batch: 4 → 5 → 6
PIPELINE_BATCH_SIZE: 8 → 10 → 12
```

Chọn cấu hình có `frames_per_second` cao nhất nhưng không OOM và không làm chất lượng caption giảm đáng kể.

### 4.5. Demo Run

```python
demo_summary = run_demo(cfg)
demo_summary
```

Demo mặc định xử lý 32 frame. Kiểm tra caption, `errors.jsonl`, `metrics.csv`, `summary.json` và GCS output trước Full Run.

### 4.6. Full Run

Trong Parameters:

```python
CONFIRM_FULL_RUN = "RUN_FULL_DATASET"
```

Chạy lại Parameters và cell 3b, sau đó:

```python
full_summaries = run_full(cfg)
full_summaries
```

Resume mặc định:

```python
SKIP_EXISTING = True
OVERWRITE = False
```

---

## 5. Điều chỉnh nhanh

### CUDA OOM

Giảm theo thứ tự:

```text
batch_size/GPU
→ PIPELINE_BATCH_SIZE
→ max_pixels
→ max_new_tokens
```

### CPU cao, GPU thấp

Kiểm tra:

```text
GPU_EXECUTION_MODE = replicated
replicas = 2
inference_batch_size > 1
PIPELINE_BATCH_SIZE = tổng batch của hai GPU
BENCHMARK_SHOW_IMAGES = False
```

Có thể tăng `preprocess_workers` từ 2 lên 3, nhưng phải benchmark lại vì quá nhiều thread có thể gây tranh chấp CPU.

### Hai GPU không cân bằng

Nếu log ghi `sharded`, hai GPU đang xử lý các nhóm layer khác nhau nên tải không nhất thiết cân bằng. Muốn hai GPU chạy hai batch độc lập, dùng `replicated`.

### Caption quá chậm

Giảm:

```python
"max_new_tokens": 48
```

Giữ:

```python
do_sample = False
use_cache = True
```

---

## 6. Artifact và metric chính

Artifact mỗi run:

```text
annotations.jsonl
errors.jsonl
metrics.csv
summary.json
run.log
```

CSV benchmark:

```text
benchmark_long.csv
benchmark_comparison.csv
benchmark_model_summary.csv
```

Các trường cần giữ khi đánh giá:

```text
gpu_execution_mode
gpu_ids
replicas
global_batch_capacity
pipeline_batch_size
frames_per_second
model_load_seconds
peak_vram_allocated_gb
gpu_0_peak_allocated_gb
gpu_1_peak_allocated_gb
processed_frames
failed_frames
```

Chọn cấu hình dựa trên throughput, VRAM, lỗi và chất lượng caption; không dựa riêng vào mức GPU utilization hiển thị tức thời.

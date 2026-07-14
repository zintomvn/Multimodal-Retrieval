# 4. Frame to Multimodal Features - Caption, OCR và object detection

Tài liệu này mô tả notebook `get-features.ipynb`.

Notebook đọc các keyframe đã trích từ AutoShot, sau đó tạo feature dạng text và object cho từng ảnh. Output chính là các file `annotations.jsonl` theo từng video.

## Vai trò trong pipeline

Notebook này tạo metadata giàu ngữ nghĩa cho keyframe:

```text
frames/
  -> caption ảnh
  -> OCR chữ trong ảnh
  -> detect object
  -> gộp thành record JSON
  -> lưu annotations.jsonl theo từng video
```

Các feature này hữu ích cho:

- tìm kiếm theo mô tả ngôn ngữ
- lọc theo object xuất hiện trong ảnh
- tìm theo chữ/phụ đề/logo/biển báo trong frame
- bổ sung dữ liệu text cho hệ thống retrieval đa phương thức

## Lưu ý quan trọng khi chạy

Cell đầu tiên cài lại nhiều package:

```bash
pip uninstall -y paddlepaddle paddleocr paddlex
pip install paddlepaddle-gpu==3.2.2 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
pip install paddleocr
pip install --quiet vietocr
pip install qwen-vl-utils
pip install -U ultralytics
```

Sau khi chạy cell này, notebook yêu cầu restart kernel rồi chạy các cell còn lại. Trên Kaggle có thể chọn:

```text
Run -> Restart & clear cell outputs
```

Không nên chạy lại cell cài đặt sau khi restart nếu môi trường đã ổn.

## Input

Notebook đọc frame từ Kaggle dataset AutoShot:

```python
frame_root = Path("/kaggle/input/datasets/khngxuninh/autoshot-output/frames")
```

Mỗi video là một folder:

```text
frames/
  L30_V001/
    shot_0000_first_f000000.jpg
    shot_0000_middle_f000037.jpg
    ...
  L30_V002/
    ...
```

Notebook tự lấy danh sách video:

```python
CFG.video_ids = sorted([
    p.name
    for p in CFG.frame_root.iterdir()
    if p.is_dir()
])
```

## Output

Output root mặc định:

```python
output_root = Path("/kaggle/working")
```

Với mỗi video, notebook tạo:

```text
/kaggle/working/<video_id>/annotations.jsonl
```

Ví dụ:

```text
/kaggle/working/L30_V001/annotations.jsonl
/kaggle/working/L30_V002/annotations.jsonl
```

Cuối notebook, toàn bộ `/kaggle/working` được zip thành:

```text
working.zip
```

## Config chính

Notebook dùng dataclass:

```python
FeatureConfig
```

Các nhóm config quan trọng:

### Bật hoặc tắt loại feature

```python
use_caption = True
use_ocr = True
use_objects = True
```

Có thể tắt từng nhánh nếu muốn chạy nhanh hơn hoặc debug riêng từng phần.

### Model caption

```python
captioning_model = "blip"  # "qwen" hoặc "blip"
```

Mặc định notebook dùng BLIP-2:

```text
Salesforce/blip2-opt-2.7b
```

Nếu chọn `qwen`, notebook dùng:

```text
Qwen/Qwen2.5-VL-3B-Instruct
```

### Model OCR

```python
ocr_model = "vietocr"      # "vietocr" hoặc "qwen"
vietocr_model = "vgg_seq2seq"
```

Mặc định:

- PaddleOCR TextDetection để detect vùng chữ
- VietOCR để nhận dạng chữ trên crop

### Model object detection

```python
yolo_model = "yolo12s.pt"
yolo_imgsz = 640
```

Notebook dùng Ultralytics YOLO để detect object trong ảnh.

### Batch size

Các batch size chính:

- `caption_batch_size = 8`
- `qwen_ocr_batch_size = 16`
- `combined_qwen_batch_size = 8`
- `ocr_det_batch_size = 16`
- `ocr_recog_batch_size = 64`
- `yolo_batch_size = 32`
- `pipeline_batch_size = 64`

Nếu bị hết GPU memory, giảm các batch size này.

## Model và logic từng nhánh

### 1. Caption ảnh

Notebook hỗ trợ hai hướng caption.

Nếu dùng Qwen:

- Model: `Qwen/Qwen2.5-VL-3B-Instruct`
- Prompt yêu cầu mô tả ảnh bằng tiếng Việt, tập trung vào người, hành động, vật thể, bối cảnh, địa điểm và sự kiện.
- Output là đoạn văn 1-3 câu.

Nếu dùng BLIP:

- Model: `Salesforce/blip2-opt-2.7b`
- Prompt ngắn: `a photo of`
- Output thường là tiếng Anh vì BLIP-2 model này không được prompt chuyên biệt tiếng Việt.

Trong config hiện tại, `captioning_model = "blip"`, nên caption mặc định có khả năng là tiếng Anh.

### 2. OCR chữ trong ảnh

Notebook hỗ trợ hai hướng OCR.

Nếu dùng VietOCR:

1. Đọc ảnh bằng OpenCV.
2. Dùng PaddleOCR `TextDetection` để tìm polygon vùng chữ.
3. Chuyển polygon thành box.
4. Gộp các box gần nhau theo dòng bằng `merge_boxes_by_line`.
5. Crop từng dòng chữ.
6. Dùng VietOCR predict text trên crop.
7. Trả về list text cho từng ảnh.

Nếu dùng Qwen:

1. Gửi ảnh và OCR prompt cho Qwen.
2. Yêu cầu model trả về đúng JSON array.
3. Parse output bằng `json.loads`.
4. Nếu parse lỗi thì trả về list rỗng.

Prompt OCR yêu cầu giữ nguyên:

- chữ hoa/thường
- dấu tiếng Việt
- dấu câu

Nếu không thấy chữ, trả về:

```json
[]
```

### 3. Object detection

Notebook dùng:

```python
YOLO(CFG.yolo_model)
```

Mỗi kết quả detect được format thành:

```json
{
  "label": "person",
  "confidence": 0.9342
}
```

Sau đó notebook tạo thêm:

- `objects`: danh sách label object duy nhất, đã sort
- `object_counts`: số lần xuất hiện của từng label
- `detections`: danh sách detection đầy đủ kèm confidence

## Helper quan trọng

### `chunked(items, batch_size)`

Chia danh sách ảnh thành batch để xử lý tuần tự.

### `_qwen_generate_batch(image_paths, prompt, max_new_tokens)`

Tạo message cho Qwen, xử lý image input bằng `qwen_vl_utils`, gọi `generate`, rồi decode text output.

### `get_ocr_batch(image_paths)`

Trả về list OCR text cho từng ảnh. Hàm này có implementation khác nhau tùy `OCR_MODEL`.

### `get_caption_batch(image_paths)`

Trả về caption cho từng ảnh. Hàm này có implementation khác nhau tùy `CAPTIONING_MODEL`.

### `get_obj_batch(image_paths)`

Trả về object detection record cho từng ảnh.

### `get_all_batch(image_paths, verbose=True)`

Đây là hàm gom toàn bộ pipeline cho một batch:

1. caption
2. OCR
3. object detection
4. gộp record bằng `_combine_feature_record`

Hàm cũng in thời gian chạy từng nhánh:

```text
caption=..., ocr=..., objects=..., total=...
```

## Format một record output

Mỗi dòng trong `annotations.jsonl` là một JSON object:

```json
{
  "image_path": "/kaggle/input/datasets/khngxuninh/autoshot-output/frames/L30_V001/shot_0000_last_f000074.jpg",
  "image_name": "shot_0000_last_f000074.jpg",
  "caption": "a photo of ...",
  "texts": ["Tuổi Trẻ TV", "tv.tuoitre.vn"],
  "objects": ["person", "tv"],
  "object_counts": {
    "person": 2,
    "tv": 1
  },
  "detections": [
    {
      "label": "person",
      "confidence": 0.9342
    }
  ],
  "video_id": "L30_V001"
}
```

Nếu một batch bị lỗi, notebook ghi record lỗi cho từng ảnh:

```json
{
  "image_path": "...",
  "video_id": "L30_V001",
  "error": "..."
}
```

Điều này giúp pipeline tiếp tục chạy thay vì dừng toàn bộ notebook.

## Cơ chế resume và overwrite

Config:

```python
overwrite_output = True
skip_existing = True
```

Logic hiện tại:

- Nếu `overwrite_output=True`, file output sẽ được ghi mới.
- Nếu `overwrite_output=False` và `skip_existing=True`, notebook đọc file `annotations.jsonl` cũ, bỏ qua các ảnh đã xử lý thành công.

Vì mặc định `overwrite_output=True`, mỗi lần chạy sẽ tạo lại output từ đầu.

## Vòng lặp xử lý chính

Notebook chạy theo từng video:

```python
for vid in video_ids:
    video_dir = FRAME_ROOT / vid
    output_dir = OUTPUT_ROOT / vid
    output_path = output_dir / "annotations.jsonl"
```

Với mỗi video:

1. Lấy toàn bộ ảnh `.jpg`.
2. Nếu bật resume, loại ảnh đã xử lý.
3. Chia ảnh thành batch theo `pipeline_batch_size`.
4. Gọi `get_all_batch`.
5. Ghi từng record JSONL.
6. Flush file mỗi `save_every_n_batches`.

## Combined Qwen caption + OCR

Notebook có option:

```python
combine_qwen_caption_ocr = False
```

Nếu bật option này, đồng thời:

- `use_caption=True`
- `use_ocr=True`
- `captioning_model="qwen"`
- `ocr_model="qwen"`

thì notebook chỉ gọi Qwen một lần cho mỗi ảnh để lấy cả:

```json
{
  "caption": "...",
  "texts": ["..."]
}
```

Cách này có thể giảm số lần gọi model, nhưng phụ thuộc vào khả năng Qwen trả JSON ổn định.

## Lưu ý khi dùng lại

- Nếu muốn caption tiếng Việt ổn hơn, cân nhắc dùng `captioning_model="qwen"` thay vì BLIP.
- Nếu muốn OCR tiếng Việt tốt và có vùng chữ rõ, cấu hình mặc định `vietocr` là hợp lý.
- Nếu GPU yếu, giảm `pipeline_batch_size`, `caption_batch_size`, `ocr_recog_batch_size` và `yolo_batch_size`.
- Nếu chỉ cần object hoặc OCR, có thể tắt các nhánh còn lại để tiết kiệm thời gian.
- `annotations.jsonl` lưu theo từng video, nên backend cần quét nhiều file nếu muốn build index toàn bộ dataset.

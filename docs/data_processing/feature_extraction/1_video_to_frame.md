# 1. Video to Frame - Trích xuất keyframe bằng AutoShot

Tài liệu này mô tả notebook `get-keyframe-autoshot.ipynb`.

Notebook nhận video gốc, dùng model AutoShot để phát hiện ranh giới giữa các shot, sau đó trích 3 frame đại diện cho mỗi shot: frame đầu, frame giữa và frame cuối.

## Vai trò trong pipeline

Đây là bước đầu tiên của luồng xử lý feature:

```text
video gốc
  -> phát hiện shot boundary bằng AutoShot
  -> chia video thành các shot
  -> lưu first/middle/last frame của mỗi shot
  -> tạo shot_segments.csv
```

Output của bước này được các notebook sau sử dụng để tạo embedding, event và feature text/object.

## Input

Notebook dùng các đường dẫn Kaggle sau:

- `REPO_DIR`: `/kaggle/working/AutoShot`
- `VIDEO_ROOT`: thư mục chứa video cần xử lý, ví dụ `/kaggle/input/datasets/aresusayhi/ai-challenge-2025/Videos/Videos/Video_L30_a/video`
- `CKPT_PATH`: checkpoint AutoShot, ví dụ `/kaggle/input/models/khngxuninh/autoshot/pytorch/default/1/ckpt_0_200_0.pth`

Các định dạng video được quét:

- `.mp4`
- `.avi`
- `.mov`
- `.mkv`
- `.webm`

## Model sử dụng

Notebook clone repo chính thức:

```text
https://github.com/wentaozhu/AutoShot
```

Model chính được import từ repo AutoShot:

```python
TransNetV2Supernet
```

Checkpoint được load từ file `ckpt_0_200_0.pth`. Code hỗ trợ hai kiểu checkpoint:

- checkpoint có key `net`
- checkpoint là `state_dict` trực tiếp

Khi load weight, notebook chỉ nhận các tham số có tên và shape khớp với model hiện tại. Điều này giúp tránh lỗi nếu checkpoint có thêm metadata hoặc key không cần thiết.

## Các bước xử lý chính

### 1. Cài đặt và chuẩn bị AutoShot

Notebook clone repo AutoShot vào `/kaggle/working/AutoShot` và cài các thư viện cần thiết:

- `ffmpeg-python`
- `einops`
- `opencv-python`
- `tqdm`

`ffmpeg-python` được dùng trong hàm đọc video của AutoShot. `einops` được model AutoShot import trong file kiến trúc.

### 2. Kiểm tra đường dẫn input

Notebook kiểm tra:

- repo AutoShot có tồn tại không
- thư mục video có tồn tại không
- checkpoint có tồn tại không
- số lượng video tìm thấy

Bước này giúp phát hiện sớm lỗi attach dataset hoặc checkpoint sai trong Kaggle.

### 3. Ghi script `autoshot_extract_frames.py`

Notebook tạo file script:

```text
/kaggle/working/autoshot_extract_frames.py
```

Script này chứa toàn bộ pipeline chạy AutoShot và trích frame.

### 4. Dự đoán shot boundary cho từng frame

Hàm chính:

```python
predict_boundary_scores(model, video_path, repo_dir, device)
```

AutoShot đọc video bằng hàm `get_frames()` trong repo gốc. Frame được resize về `48x27 RGB`, đúng với input model.

Mỗi batch có dạng ban đầu:

```text
[T, H, W, C]
```

với:

- `T = 100`
- `H = 27`
- `W = 48`
- `C = 3`

Trước khi đưa vào model, batch được chuyển thành:

```text
[B, C, T, H, W]
```

Sau khi model trả logit, notebook dùng `sigmoid` để chuyển thành xác suất boundary cho từng frame.

Notebook chỉ lấy đoạn `[25:75]` trong mỗi batch 100 frame. Đây là logic theo inference gốc của AutoShot để tránh vùng padding hoặc vùng biên không ổn định.

### 5. Chuyển boundary thành shot

Hàm chính:

```python
boundaries_to_shots(boundary_frames, num_frames, min_shot_len=5)
```

Nếu boundary nằm ở frame `20` và `60`, video được chia thành:

```text
[0, 20]
[21, 60]
[61, 99]
```

Tham số `min_shot_len=5` loại bỏ các shot quá ngắn do model detect nhiễu.

Nếu không tạo được shot nào, code coi toàn bộ video là một shot duy nhất.

### 6. Trích 3 frame đại diện cho mỗi shot

Với mỗi shot `[start, end]`, notebook lưu:

- `first`: frame `start`
- `middle`: frame `(start + end) // 2`
- `last`: frame `end`

AutoShot dự đoán trên frame resize nhỏ, nhưng frame lưu ra được đọc lại từ video gốc bằng OpenCV để giữ chất lượng tốt hơn.

Tên ảnh output có dạng:

```text
shot_0000_first_f000001.jpg
shot_0000_middle_f000035.jpg
shot_0000_last_f000074.jpg
```

## Lệnh chạy chính

Notebook chạy script bằng lệnh:

```bash
python /kaggle/working/autoshot_extract_frames.py \
  --repo_dir /kaggle/working/AutoShot \
  --video_root /kaggle/input/datasets/aresusayhi/ai-challenge-2025/Videos/Videos/Video_L30_a/video \
  --ckpt /kaggle/input/models/khngxuninh/autoshot/pytorch/default/1/ckpt_0_200_0.pth \
  --out_dir /kaggle/working/autoshot_output \
  --threshold 0.296 \
  --min_shot_len 5
```

Ý nghĩa tham số:

- `--threshold 0.296`: ngưỡng xác suất để coi một frame là shot boundary. Tăng ngưỡng sẽ tạo ít boundary hơn, giảm ngưỡng sẽ nhạy hơn.
- `--min_shot_len 5`: shot phải có ít nhất 5 frame.

## Output

Thư mục output:

```text
/kaggle/working/autoshot_output
```

Cấu trúc chính:

```text
autoshot_output/
  shot_segments.csv
  frames/
    L30_V001/
      shot_0000_first_f000000.jpg
      shot_0000_middle_f000037.jpg
      shot_0000_last_f000074.jpg
      ...
    L30_V002/
      ...
```

File `shot_segments.csv` chứa metadata cho từng frame được lưu:

| Cột | Ý nghĩa |
| --- | --- |
| `video_name` | Tên file video gốc |
| `video_path` | Đường dẫn video gốc |
| `shot_id` | ID shot trong video |
| `shot_start_frame` | Frame bắt đầu shot |
| `shot_end_frame` | Frame kết thúc shot |
| `shot_start_sec` | Thời điểm bắt đầu shot theo giây |
| `shot_end_sec` | Thời điểm kết thúc shot theo giây |
| `frame_type` | `first`, `middle` hoặc `last` |
| `frame_idx` | Index frame được trích từ video gốc |
| `frame_sec` | Thời điểm của frame theo giây |
| `image_path` | Đường dẫn ảnh output trong Kaggle working |
| `boundary_threshold` | Ngưỡng AutoShot đã dùng |
| `saved` | Frame có lưu thành công không |
| `fps` | FPS của video theo OpenCV |
| `total_frames_opencv` | Tổng số frame OpenCV đọc được |

## Đóng gói và upload Kaggle Dataset

Sau khi chạy xong, notebook nén thư mục:

```text
/kaggle/working/autoshot_output.zip
```

Sau đó tạo Kaggle dataset với metadata:

```json
{
  "title": "AutoShot Output",
  "id": "khngxuninh/autoshot-output"
}
```

Dataset này là input cho các notebook xử lý feature tiếp theo.

## Lưu ý khi dùng lại

- Nếu đổi tập video, cần sửa `VIDEO_ROOT`.
- Nếu đổi checkpoint AutoShot, cần sửa `CKPT_PATH`.
- Nếu số shot quá nhiều, tăng `threshold` hoặc tăng `min_shot_len`.
- Nếu số shot quá ít, giảm `threshold`.
- Nên kiểm tra `saved=True` trong `shot_segments.csv` trước khi dùng output cho bước embedding.

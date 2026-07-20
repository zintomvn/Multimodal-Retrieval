# Report: Quy trình Frame Extraction và Upload lên Cloud

**File nguồn:** `video_to_frame_gcs.py`  
**Phiên bản báo cáo:** v1

## 1. Mục tiêu hệ thống

Pipeline xử lý video dung lượng lớn được lưu trên Google Cloud Storage (GCS), dùng AutoShot để phát hiện các đoạn cảnh (*shot*), trích xuất các frame đại diện và upload kết quả trở lại cloud.

Luồng tổng quát:

```text
Raw videos trên GCS
        ↓
Kiểm tra môi trường
        ↓
Khám phá dữ liệu và tạo manifest
        ↓
Chia video thành các shard
        ↓
Download từng video về local scratch
        ↓
AutoShot phát hiện shot boundary
        ↓
Trích xuất first/middle/last frame
        ↓
Upload keyframe và metadata lên GCS
        ↓
Gộp kết quả và kiểm tra chất lượng
        ↓
Tạo _SUCCESS nếu batch hợp lệ
```

## 2. Kiến trúc pipeline

Code chia quá trình thành bốn lệnh độc lập:

| Stage | Chức năng |
|---|---|
| `doctor` | Kiểm tra kết nối GCS, cấu trúc dữ liệu và dependency |
| `discover` | Liệt kê video, tạo manifest và chia shard |
| `extract` | Xử lý một shard, cắt frame và upload kết quả |
| `merge` | Gộp kết quả, kiểm tra chất lượng và tạo output cuối |

Cách tách stage giúp pipeline dễ debug, retry, mở rộng nhiều worker và tránh phải chạy lại toàn bộ dữ liệu khi một phần bị lỗi.

## 3. Stage `doctor`

`doctor` thực hiện kiểm tra ban đầu:

- Đọc cấu hình từ `.env`.
- Khởi tạo GCS client.
- Xác định bucket và prefix chứa raw video.
- Kiểm tra có video hợp lệ trong batch hay không.
- Kiểm tra các thư viện cần thiết như `torch`, `numpy` và `cv2`.

Stage này áp dụng nguyên tắc **fail fast**: dừng sớm nếu sai credential, sai prefix hoặc thiếu dependency.

## 4. Stage `discover`

`discover` quét các video trên GCS theo cấu trúc:

```text
raw/source=kaggle/
└── dataset=<dataset_id>/
    └── source_version=<source_version>/
        └── batch=<batch_id>/
```

Mỗi video được chuyển thành một record trong `processing_manifest.jsonl`, gồm:

- `video_id`
- đường dẫn GCS đầu vào
- generation và kích thước object
- output prefix
- threshold của AutoShot
- `min_shot_len`
- `run_id`
- trạng thái xử lý

Manifest là danh sách công việc cố định của một lần chạy, giúp tránh xử lý trùng, thiếu video hoặc thay đổi dữ liệu giữa quá trình chạy.

Sau đó manifest được chia thành nhiều shard:

```text
shard-00000.jsonl
shard-00001.jsonl
shard-00002.jsonl
...
```

Mỗi shard chứa một số lượng video cố định, mặc định là 16.

## 5. Stage `extract`

Mỗi lần gọi `extract` chỉ xử lý một shard.

### 5.1 Khởi tạo

Pipeline:

1. Đọc shard JSONL.
2. Chọn thiết bị `cuda` nếu có, nếu không dùng CPU.
3. Download checkpoint nếu checkpoint nằm trên GCS.
4. Load model AutoShot một lần cho toàn bộ shard.
5. Tạo thư mục scratch cục bộ cho shard.

### 5.2 Xử lý từng video

Với mỗi video:

#### Bước 1: Download video

Video được tải từ GCS về local scratch.

Pipeline kiểm tra:

- file không được rỗng;
- kích thước file phải khớp với metadata trong manifest;
- có thể cố định đúng object generation đã được discover.

#### Bước 2: Phát hiện shot boundary

FFmpeg decode video thành các frame RGB kích thước `48 × 27`.

AutoShot xử lý video theo các cửa sổ frame và trả về boundary score cho từng frame.

Các frame có:

```text
score > threshold
```

được xem là ranh giới giữa hai shot.

#### Bước 3: Tạo shot segment

Danh sách boundary được chuyển thành các đoạn:

```text
shot_start_frame → shot_end_frame
```

Các shot ngắn hơn `min_shot_len` bị loại bỏ. Nếu không có boundary hợp lệ, toàn bộ video được xem là một shot.

#### Bước 4: Trích xuất frame đại diện

Với mỗi shot, OpenCV lấy ba frame ở độ phân giải gốc:

```text
first  = shot_start_frame
middle = (shot_start_frame + shot_end_frame) // 2
last   = shot_end_frame
```

Tên file có dạng:

```text
shot_0001_middle_f000370.jpg
```

#### Bước 5: Upload lên GCS

Keyframe được upload theo cấu trúc:

```text
processed/keyframes/
└── dataset=<dataset_id>/
    └── batch=<batch_id>/
        └── profile=<profile_version>/
            └── video_id=<video_id>/
```

Pipeline mặc định bỏ qua object đã tồn tại, giúp chạy lại mà không upload trùng.

#### Bước 6: Ghi metadata

Mỗi video có một file `frames_manifest.jsonl` chứa:

- shot ID
- frame index
- timestamp
- loại frame: first, middle hoặc last
- GCS URI của ảnh
- FPS
- profile version
- run ID

Metadata này được dùng cho các bước downstream như embedding và indexing.

## 6. Kết quả cấp shard

Mỗi shard sinh ra ba file:

```text
<shard>.shot_segments.csv
<shard>.errors.jsonl
<shard>.result.json
```

Trong đó:

- `shot_segments.csv`: metadata của các keyframe thành công.
- `errors.jsonl`: lỗi theo từng video.
- `result.json`: thống kê số video thành công, thất bại và thời gian chạy.

Lỗi của một video được bắt riêng, vì vậy một video hỏng không làm mất kết quả của toàn bộ shard.

## 7. Stage `merge`

`merge` đọc toàn bộ kết quả shard và tạo:

```text
shot_segments.csv
errors.jsonl
summary.json
_SUCCESS
```

Trước khi tạo `_SUCCESS`, pipeline thực hiện quality gate:

- tất cả video trong manifest phải có kết quả;
- không được có video lỗi;
- danh sách keyframe không được rỗng;
- `keyframe_id` không được trùng;
- frame phải nằm trong khoảng shot;
- profile version phải đúng;
- ảnh phải thực sự tồn tại trên GCS.

`_SUCCESS` chỉ được tạo khi toàn bộ batch hợp lệ. Các pipeline downstream nên chỉ xử lý batch có marker này.

## 8. Cách pipeline hỗ trợ Big Data

### Sharding

Dataset được chia thành nhiều shard nhỏ để:

- giới hạn tài nguyên của mỗi job;
- chạy song song trên nhiều worker;
- retry riêng phần bị lỗi;
- theo dõi tiến độ rõ ràng.

### Manifest-driven processing

Manifest cố định danh sách video và metadata đầu vào, giúp pipeline có tính tái lập và tránh phụ thuộc vào trạng thái GCS thay đổi trong lúc xử lý.

### Local scratch processing

Video được tải về local để FFmpeg decode tuần tự và OpenCV seek chính xác tới từng frame. Sau khi xử lý, chỉ keyframe và metadata cần thiết được giữ trên cloud.

### Idempotency

Pipeline hỗ trợ bỏ qua keyframe đã tồn tại. Điều này giúp chạy lại an toàn mà không tạo dữ liệu trùng.

### Fault isolation

Lỗi được ghi ở cấp video và shard. Một video bị hỏng không làm toàn bộ batch dừng ngay lập tức.

### Quality gate

Output chỉ được xem là hoàn chỉnh sau khi kiểm tra đầy đủ số video, metadata và object trên GCS.

## 9. Phân loại dữ liệu trên GCS

Pipeline tạo hai nhóm dữ liệu:

### Dữ liệu chính

```text
processed/keyframes/
```

Chứa ảnh keyframe và manifest theo video.

### Artifact vận hành

```text
processed/keyframes_manifests/
```

Chứa manifest, shard, kết quả trung gian, lỗi, summary và `_SUCCESS`.

Việc tách hai nhóm giúp quản lý rõ dữ liệu phục vụ mô hình và dữ liệu phục vụ vận hành pipeline.

## 10. Kết luận

`video_to_frame_gcs.py` triển khai pipeline theo mô hình:

```text
Plan → Partition → Process → Validate → Commit
```

Tương ứng:

```text
discover → shard → extract → merge → _SUCCESS
```

Thiết kế này phù hợp với xử lý video quy mô lớn vì có khả năng mở rộng, chạy lại từng phần, kiểm soát lỗi và xác nhận chất lượng trước khi dữ liệu được đưa sang bước embedding hoặc indexing.

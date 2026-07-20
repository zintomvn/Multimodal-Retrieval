# 3. Frame to Event - Gom keyframe thành event embedding

Tài liệu này mô tả notebook `event-embeddings.ipynb`.

Notebook đọc embedding keyframe theo từng video, gom các keyframe liền kề thành event dựa trên khoảng cách thời gian và độ giống nhau về ngữ cảnh, sau đó tạo embedding đại diện cho mỗi event.

## Vai trò trong pipeline

Bước này nâng cấp truy hồi từ cấp keyframe lên cấp event:

```text
keyframe embeddings + map-keyframes
  -> sắp xếp keyframe theo thời gian
  -> gom keyframe liên tiếp thành event
  -> lấy trung bình embedding trong event
  -> normalize event embedding
  -> lưu events `.npy` và map-event `.csv`
```

Event giúp truy hồi theo đoạn nội dung thay vì chỉ theo từng ảnh đơn lẻ.

## Input

Notebook đọc dataset embedding:

```python
INPUT_ROOT = Path('/kaggle/input/datasets/phngtrnhng/embeddings-new')
```

Cấu trúc input kỳ vọng:

```text
INPUT_ROOT/
  features/
    <model_folder>/
      L30_V001.npy
      L30_V002.npy
      ...
  map-keyframes/
    L30_V001.csv
    L30_V002.csv
    ...
```

Notebook tự detect model folder đầu tiên trong `features/`:

```python
model_dirs = sorted([p for p in FEATURES_ROOT.iterdir() if p.is_dir()])
FEATURE_DIR = model_dirs[0]
```

Vì vậy nếu input có nhiều model folder, notebook hiện tại sẽ dùng folder đầu tiên theo thứ tự sort.

## Output

Output được ghi vào:

```python
OUTPUT_ROOT = Path('/kaggle/working/event_embeddings_per_video')
EVENT_DIR = OUTPUT_ROOT / 'events'
MAP_EVENT_DIR = OUTPUT_ROOT / 'map-event'
```

Cấu trúc:

```text
event_embeddings_per_video/
  events/
    L30_V001.npy
    L30_V002.npy
    ...
  map-event/
    L30_V001.csv
    L30_V002.csv
    ...
  event_summary.csv
```

## Rule gom event

Notebook dùng 3 tham số chính:

```python
MAX_TIME_GAP_SEC = 6.0
SCENE_SIMILARITY_THRESHOLD = 0.72
MAX_EVENT_DURATION_SEC = 45.0
```

Ý nghĩa:

- Nếu hai keyframe liên tiếp cách nhau quá `6.0` giây, tách event.
- Nếu cosine similarity giữa keyframe mới và event hiện tại nhỏ hơn `0.72`, tách event.
- Nếu thêm keyframe mới làm event dài hơn `45.0` giây, tách event.

Có thể đặt `MAX_EVENT_DURATION_SEC = None` nếu không muốn giới hạn độ dài event.

## Helper đọc CSV an toàn

Notebook có hàm:

```python
read_csv_safe(path)
```

Hàm này xử lý một số lỗi thường gặp:

- CSV có BOM UTF-8
- CSV bị encoding `cp1252`
- dấu quote cong `“”`
- tên cột có ký tự thừa

Việc này giúp notebook bền hơn khi CSV được sinh hoặc chỉnh sửa từ nhiều môi trường khác nhau.

## Các bước xử lý chính

### 1. Load embedding và map của từng video

Với mỗi `video_id`, notebook đọc:

```text
features/<model_folder>/<video_id>.npy
map-keyframes/<video_id>.csv
```

Map CSV cần có các cột:

- `n`
- `pts_time`
- `frame_idx`

Notebook ép kiểu:

```python
key_map['n'] = key_map['n'].astype(int)
key_map['pts_time'] = key_map['pts_time'].astype(float)
key_map['frame_idx'] = key_map['frame_idx'].astype(int)
```

### 2. Sắp xếp theo thời gian

Notebook sort keyframe theo `pts_time`:

```python
order = np.argsort(key_map['pts_time'].values)
key_map = key_map.iloc[order].reset_index(drop=True)
```

Sau đó reorder embedding theo cột `n`:

```python
embeddings = embeddings[key_map['n'].values - 1]
```

Điều này giữ đúng quy ước:

```text
dòng n trong CSV <-> vector index n - 1 trong `.npy`
```

### 3. Normalize keyframe embeddings

Notebook normalize lại embedding:

```python
embeddings = embeddings / np.maximum(norms, 1e-12)
```

Sau bước này, cosine similarity giữa hai vector có thể tính bằng dot product:

```python
similarity = float(np.dot(current_vector, embeddings[i]))
```

### 4. Duyệt keyframe và quyết định tách event

Notebook bắt đầu event đầu tiên từ keyframe index `0`.

Với mỗi keyframe tiếp theo, code tính:

- `time_gap`: khoảng cách thời gian với keyframe trước đó
- `event_duration_if_added`: độ dài event nếu thêm keyframe này
- `similarity`: độ giống nhau giữa keyframe mới và vector event hiện tại

Event sẽ bị tách nếu một trong các điều kiện đúng:

```python
time_gap > MAX_TIME_GAP_SEC
similarity < SCENE_SIMILARITY_THRESHOLD
event_duration_if_added > MAX_EVENT_DURATION_SEC
```

Nếu không tách, keyframe được thêm vào event hiện tại và vector event tạm thời được cập nhật bằng trung bình các embedding trong event.

### 5. Tạo event embedding

Khi kết thúc một event, notebook lấy trung bình các keyframe embedding trong event:

```python
event_vec = embeddings[idx].mean(axis=0)
```

Sau đó normalize:

```python
event_vec = event_vec / norm(event_vec)
```

Embedding cuối cùng được lưu ở dtype:

```text
float32
```

## Format file event `.npy`

Mỗi file:

```text
events/<video_id>.npy
```

có shape:

```text
[num_events, embedding_dim]
```

Dòng thứ `event_embedding_index` trong `.npy` tương ứng với event cùng index trong file `map-event/<video_id>.csv`.

## Format file `map-event`

Mỗi file:

```text
map-event/<video_id>.csv
```

có các cột:

| Cột                     | Ý nghĩa                              |
| ----------------------- | ------------------------------------ |
| `event_id`              | ID event, ví dụ `L30_V001_E0000`     |
| `event_embedding_index` | Index vector event trong file `.npy` |
| `video_id`              | ID video                             |
| `start_n`               | Số thứ tự keyframe bắt đầu event     |
| `end_n`                 | Số thứ tự keyframe kết thúc event    |
| `start_sec`             | Thời điểm bắt đầu event              |
| `end_sec`               | Thời điểm kết thúc event             |
| `start_frame`           | Frame index bắt đầu                  |
| `end_frame`             | Frame index kết thúc                 |
| `keyframe_ns`           | Danh sách keyframe `n` thuộc event   |
| `n_keyframes`           | Số keyframe trong event              |

## File summary

Notebook lưu:

```text
event_summary.csv
```

Mỗi dòng gồm:

- `video_id`
- `num_keyframes`
- `num_events`
- `embedding_dim`
- `event_feature_path`
- `event_map_path`

File này giúp kiểm tra nhanh video nào có bao nhiêu event.

## Đóng gói output

Cuối notebook, output được zip:

```bash
cd /kaggle/working && zip -r event_embeddings_per_video.zip event_embeddings_per_video
```

File zip có thể tải xuống hoặc tạo Kaggle Dataset mới.

## Lưu ý khi chỉnh tham số

- Tăng `MAX_TIME_GAP_SEC` sẽ gom nhiều keyframe xa nhau hơn vào cùng event.
- Giảm `MAX_TIME_GAP_SEC` sẽ tách event nhỏ hơn theo thời gian.
- Tăng `SCENE_SIMILARITY_THRESHOLD` sẽ tách event khắt khe hơn khi hình ảnh thay đổi.
- Giảm `SCENE_SIMILARITY_THRESHOLD` sẽ gom các cảnh hơi khác nhau vào cùng event.
- Tăng `MAX_EVENT_DURATION_SEC` cho phép event dài hơn.
- Nếu output có quá nhiều event một keyframe, có thể threshold similarity đang quá cao hoặc keyframe quá thưa.

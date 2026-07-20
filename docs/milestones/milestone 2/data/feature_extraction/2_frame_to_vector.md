# 2. Frame to Vector - Tạo embedding cho keyframe

Tài liệu này mô tả notebook `keyframe-embeddings.ipynb`.

Notebook đọc output từ bước AutoShot, encode từng keyframe thành vector bằng OpenCLIP, rồi lưu embedding theo từng video.

## Vai trò trong pipeline

Đây là bước biến ảnh keyframe thành vector phục vụ truy hồi:

```text
frames/ + shot_segments.csv
  -> đọc danh sách keyframe đã lưu
  -> encode ảnh bằng OpenCLIP
  -> lưu vector `.npy` theo từng video
  -> lưu file map-keyframes `.csv`
```

Output của bước này được dùng trực tiếp cho truy hồi text-image hoặc làm input cho bước gom event.

## Input

Notebook đọc Kaggle dataset đã tạo từ bước AutoShot:

```python
DATASET_ROOT = Path('/kaggle/input/datasets/khngxuninh/autoshot-output')
ARCHIVE_PATH = DATASET_ROOT / 'autoshot_output.zip'
AUTOSHOT_ROOT = Path('/kaggle/working/autoshot_input')
```

Notebook hỗ trợ hai trường hợp:

- Dataset đã có sẵn dạng folder chứa `shot_segments.csv` và `frames/`.
- Dataset chỉ có file `autoshot_output.zip`, khi đó notebook unzip vào `/kaggle/working/autoshot_input`.

File quan trọng nhất là:

```text
shot_segments.csv
```

Trong file này, notebook chỉ giữ các dòng có `saved=True` nếu cột `saved` tồn tại.

## Model sử dụng

Notebook dùng OpenCLIP:

```python
MODEL_NAME = 'ViT-B-32'
PRETRAINED = 'laion2b_s34b_b79k'
```

Model được load bằng:

```python
open_clip.create_model_and_transforms(
    MODEL_NAME,
    pretrained=PRETRAINED,
    device=DEVICE,
)
```

Embedding ảnh được tạo bằng:

```python
model.encode_image(images, normalize=True)
```

`normalize=True` nghĩa là vector output đã được L2-normalize. Khi đó cosine similarity có thể tính bằng dot product.

## Config chính

```python
OUTPUT_ROOT = Path('/kaggle/working/embedding_per_video')
BATCH_SIZE = 256
NUM_WORKERS = 2
DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'
```

Tên folder model được tạo tự động:

```text
vit-ViT-B-32-laion2b_s34b_b79k
```

Output được chia thành:

```text
embedding_per_video/
  features/<model_folder>/
  map-keyframes/
```

## Các bước xử lý chính

### 1. Load AutoShot output

Notebook tìm `shot_segments.csv`, đọc bằng pandas, sau đó chuẩn hóa thêm các cột:

- `video_id`: lấy từ tên folder cha của `image_path`
- `filename`: lấy từ tên file ảnh
- `local_image_path`: đường dẫn ảnh thực tế trong môi trường Kaggle hiện tại

Vì `image_path` trong CSV ban đầu trỏ tới `/kaggle/working/autoshot_output/...`, notebook không dùng nguyên đường dẫn cũ. Thay vào đó, nó dựng lại đường dẫn local:

```python
AUTOSHOT_ROOT / 'frames' / video_id / filename
```

Sau đó dữ liệu được sort theo:

- `video_id`
- `shot_id`
- `frame_idx`
- `frame_type`

Việc sort giúp thứ tự vector trong `.npy` khớp với metadata trong `.csv`.

### 2. Định nghĩa Dataset đọc ảnh

Class chính:

```python
class ImagePathDataset(Dataset)
```

Mỗi item:

- mở ảnh bằng PIL
- convert sang RGB
- apply preprocess của OpenCLIP

Preprocess này đảm bảo ảnh được resize, normalize và chuyển tensor đúng format mà model cần.

### 3. Encode theo từng video

Notebook group dataframe theo `video_id`.

Với mỗi video:

1. Lấy danh sách `local_image_path`.
2. Tạo `DataLoader`.
3. Encode từng batch ảnh bằng OpenCLIP.
4. Ghép các batch embedding bằng `np.concatenate`.
5. Lưu vector ra `.npy`.
6. Lưu metadata map ra `.csv`.

Embedding được ép về:

```text
float32
```

## Output

Thư mục output chính:

```text
/kaggle/working/embedding_per_video
```

Cấu trúc:

```text
embedding_per_video/
  features/
    vit-ViT-B-32-laion2b_s34b_b79k/
      L30_V001.npy
      L30_V002.npy
      ...
  map-keyframes/
    L30_V001.csv
    L30_V002.csv
    ...
  per_video_summary.csv
  model_info.json
```

## Format file `.npy`

Mỗi file:

```text
features/<model_folder>/<video_id>.npy
```

chứa toàn bộ embedding keyframe của một video.

Shape thường có dạng:

```text
[num_keyframes, embedding_dim]
```

Với `ViT-B-32`, `embedding_dim` thường là `512`.

Vector ở dòng index `i` trong `.npy` tương ứng với dòng `n = i + 1` trong file map CSV.

## Format file `map-keyframes`

Mỗi file:

```text
map-keyframes/<video_id>.csv
```

có các cột:

| Cột | Ý nghĩa |
| --- | --- |
| `n` | Thứ tự keyframe trong video, bắt đầu từ 1 |
| `pts_time` | Thời điểm keyframe theo giây, lấy từ `frame_sec` |
| `fps` | FPS của video nếu AutoShot có lưu |
| `frame_idx` | Index frame gốc trong video |

Quy ước ánh xạ:

```text
vector thứ n - 1 trong `.npy` <-> dòng có n trong `.csv`
```

## File `model_info.json`

Notebook lưu thông tin model và dataset vào:

```text
model_info.json
```

Các thông tin quan trọng:

- thời điểm tạo
- dataset input
- output root
- model name
- pretrained checkpoint
- batch size
- số video
- số keyframe
- vector đã L2-normalize
- dtype là `float32`
- layout `.npy`

File này giúp backend hoặc các notebook sau biết vector được tạo bằng model nào.

## Kiểm tra nhanh

Cuối notebook, code load thử một video:

```python
sample_features = np.load(FEATURE_DIR / f'{sample_video}.npy')
sample_map = pd.read_csv(MAP_DIR / f'{sample_video}.csv')
```

Mục tiêu là kiểm tra:

- file `.npy` đọc được
- shape embedding hợp lệ
- file map CSV có đúng metadata

## Lưu ý khi dùng lại

- Nếu đổi dataset AutoShot output, sửa `DATASET_ROOT`.
- Nếu muốn model mạnh hơn, có thể đổi `MODEL_NAME` và `PRETRAINED`, nhưng cần đảm bảo backend/truy hồi dùng đúng cùng model.
- Vì vector đã normalize, khi search có thể dùng dot product như cosine similarity.
- Không nên xáo trộn thứ tự ảnh khi encode, vì `.npy` phụ thuộc vào thứ tự map CSV.

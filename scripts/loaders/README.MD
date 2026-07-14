# Pipeline trích xuất keyframe AutoShot từ Google Cloud Storage

Tài liệu này hướng dẫn chạy pipeline `video -> frame` theo plan
`docs/data_processing/feature_extraction/1_video_to_frame_cloud_plan.md`.
Pipeline đọc video raw đã upload lên Google Cloud Storage, tạo manifest xử lý,
chạy AutoShot để phát hiện shot boundary, trích `first/middle/last` frame cho
mỗi shot, rồi ghi `shot_segments.csv` và `_SUCCESS` khi quality gate đạt.

File loader chính:

```text
scripts/loaders/video_to_frame_gcs.py
```

## 1. Format GCS đã kiểm tra

Bucket hiện tại trong `.env`:

```text
GCS_BUCKET=aic_ai_2026
GCS_CREDENTIALS_FILE=apps\secrets\gen-lang-client-0547522732-410672fac05f.json
```

Raw video trên GCS đang có dạng:

```text
gs://aic_ai_2026/raw/source=kaggle/
  dataset=ai_challenge_2025/
  source_version=kaggle_current/
  batch=L21/
  ai-challenge-2025/Videos/Videos/Videos_L21_a/video/L21_V001.mp4
```

Các batch đã thấy dưới `ai_challenge_2025`:

```text
L21, L22, L23, L24, L25, L26, L27, L28, L29, L30
```

Output theo đúng plan sẽ được ghi vào:

```text
gs://aic_ai_2026/processed/keyframes/
  dataset=<dataset_id>/
  batch=<batch_id>/
  profile=autoshot_v1/
  video_id=<video_id>/
    shot_0000_first_f000000.jpg
    shot_0000_middle_f000037.jpg
    shot_0000_last_f000074.jpg
    frames_manifest.jsonl

gs://aic_ai_2026/processed/keyframes_manifests/
  dataset=<dataset_id>/
  batch=<batch_id>/
  profile=autoshot_v1/
  run_id=<run_id>/
    processing_manifest.jsonl
    shards/
    results/
    shot_segments.csv
    errors.jsonl
    summary.json
    _SUCCESS
```

## 2. Yêu cầu môi trường

Cần có credential đọc/ghi GCS trong `.env` hoặc truyền qua CLI:

```text
GCS_BUCKET=aic_ai_2026
GCS_CREDENTIALS_FILE=apps\secrets\gen-lang-client-0547522732-410672fac05f.json
```

Tất cả thư viện Python cần cho loader đã được khai báo ở file `requirements.txt`
ngay đầu dự án. Cài một lần từ project root:

```powershell
pip install -r requirements.txt
```

Các nhóm thư viện chính trong `requirements.txt`:

| Thư viện                 | Dùng cho                                                                             |
| ------------------------ | ------------------------------------------------------------------------------------ |
| `google-cloud-storage`   | Đọc raw video, upload keyframe và manifest lên GCS                                   |
| `numpy`                  | Xử lý score boundary và frame index                                                  |
| `torch`                  | Load và chạy model AutoShot                                                          |
| `opencv-python-headless` | Đọc video gốc và lưu frame JPG chất lượng cao qua `cv2`, phù hợp worker không có GUI |
| `ffmpeg-python`          | Gọi FFmpeg để đọc video theo logic AutoShot `get_frames`                             |
| `imageio-ffmpeg`         | Cung cấp FFmpeg binary fallback khi máy chưa có `ffmpeg` trong PATH                  |
| `einops`                 | Dependency của kiến trúc model AutoShot                                              |
| `matplotlib`             | Dependency import trực tiếp trong AutoShot `utils.py`                                |
| `tqdm`                   | Progress/log tiện ích khi mở rộng worker                                             |
| `PyYAML`                 | Đọc config YAML trong các loader/cloud utility khác                                  |

Ngoài Python packages, bước `extract` cần chuẩn bị thêm:

- AutoShot repo local chứa `supernet_flattransf_3_8_8_8_13_12_0_16_60.py` và
  `utils.py`; xem mục 6.1 để clone/kiểm tra cụ thể.
- Checkpoint `ckpt_0_200_0.pth` ở local hoặc GCS; xem mục 6.2 để đặt file và
  kiểm tra dung lượng.
- FFmpeg binary. Loader ưu tiên biến môi trường `FFMPEG_BINARY`, sau đó tìm
  `ffmpeg` trong PATH, cuối cùng fallback sang binary của `imageio-ffmpeg`.

## 3. Khi chạy video-to-frame thì những luồng nào chạy?

Loader hiện chia pipeline thành 4 luồng rõ ràng, bám theo plan:

| Luồng | Lệnh       | Mục đích                                                                   | Có chạy AutoShot không? | Có ghi GCS không?                       |
| ----- | ---------- | -------------------------------------------------------------------------- | ----------------------- | --------------------------------------- |
| 1     | `doctor`   | Kiểm tra credential, GCS raw format, dependency                            | Không                   | Không                                   |
| 2     | `discover` | List raw video trên GCS, tạo `processing_manifest.jsonl` và shard          | Không                   | Chỉ ghi GCS nếu có `--upload-artifacts` |
| 3     | `extract`  | Download video, chạy AutoShot, trích `first/middle/last`, upload ảnh JPG   | Có                      | Có nếu chạy thật; `--dry-run` thì không |
| 4     | `merge`    | Gộp shard result, chạy quality gate, tạo `shot_segments.csv` và `_SUCCESS` | Không                   | Có nếu có `--upload-final`              |

Luồng chạy đầy đủ cho một batch:

```text
doctor
  -> discover
  -> extract từng shard
  -> merge
  -> _SUCCESS
```

Trong đó:

- `doctor` dùng để kiểm tra trước khi chạy.
- `discover` tạo danh sách video cần xử lý và chia nhỏ thành shard.
- `extract` là luồng nặng nhất: tải video, load AutoShot, predict boundary,
  tách shot, lưu 3 frame đại diện mỗi shot.
- `merge` là cổng chất lượng cuối: nếu còn lỗi hoặc thiếu ảnh thì không ghi
  `_SUCCESS`.

## 4. Kiểm tra nhanh GCS và dependency

Lệnh này chỉ đọc GCS, không ghi output:

```powershell
python scripts/loaders/video_to_frame_gcs.py doctor `
  --env-file .env `
  --dataset-id ai_challenge_2025 `
  --batch-id L21 `
  --source-version kaggle_current
```

Kết quả cần thấy:

```json
{
  "bucket": "aic_ai_2026",
  "batch_prefix": "raw/source=kaggle/dataset=ai_challenge_2025/source_version=kaggle_current/batch=L21/",
  "sample_video": "gs://aic_ai_2026/raw/source=kaggle/dataset=ai_challenge_2025/source_version=kaggle_current/batch=L21/ai-challenge-2025/Videos/Videos/Videos_L21_a/video/L21_V001.mp4"
}
```

## 5. Chạy test nhỏ tạo manifest một video

Lệnh này list GCS raw video và tạo manifest local cho đúng 1 video. Nó chưa
download video và chưa chạy AutoShot, nên rất phù hợp để test format.

```powershell
python scripts/loaders/video_to_frame_gcs.py discover `
  --env-file .env `
  --dataset-id ai_challenge_2025 `
  --batch-id L21 `
  --source-version kaggle_current `
  --max-videos 1 `
  --videos-per-shard 1 `
  --run-id smoke_autoshot_l21_001
```

Output local:

```text
scripts/loaders/frame_extraction_runs/smoke_autoshot_l21_001/
  processing_manifest.jsonl
  errors.jsonl
  summary.json
  shards/
    shard-00000.jsonl
```

Manifest mẫu đã kiểm tra:

```text
video_id=L21_V001
input_gcs_uri=gs://aic_ai_2026/raw/source=kaggle/dataset=ai_challenge_2025/source_version=kaggle_current/batch=L21/ai-challenge-2025/Videos/Videos/Videos_L21_a/video/L21_V001.mp4
output_prefix=processed/keyframes/dataset=ai_challenge_2025/batch=L21/profile=autoshot_v1/video_id=L21_V001/
threshold=0.296
min_shot_len=5
```

Nếu muốn upload manifest/shards lên GCS để worker cloud đọc, thêm:

```powershell
--upload-artifacts
```

## 6. Chạy AutoShot cho một shard

### 6.1. Chuẩn bị AutoShot repo local

Repo AutoShot đặt trong folder `loaders` của `scripts`:

```text
scripts/loaders/AutoShot
```

Đây cũng là default của `video_to_frame_gcs.py`, nên khi repo đã nằm đúng chỗ
thì lệnh `extract` không cần truyền `--autoshot-repo-dir`.

Loader sẽ import trực tiếp 2 file trong thư mục này:

```text
supernet_flattransf_3_8_8_8_13_12_0_16_60.py
utils.py
```

Clone AutoShot một lần từ project root:

```powershell
git clone https://github.com/wentaozhu/AutoShot.git "scripts/loaders/AutoShot"
```

Kiểm tra đúng thư mục:

```powershell
Test-Path "scripts/loaders/AutoShot/supernet_flattransf_3_8_8_8_13_12_0_16_60.py"
Test-Path "scripts/loaders/AutoShot/utils.py"
```

Cả 2 lệnh phải trả về `True`.

Nếu muốn truyền rõ đường dẫn thay vì dùng default:

```powershell
--autoshot-repo-dir "scripts/loaders/AutoShot"
```

Trong Docker image, copy hoặc clone repo vào cùng path tương đối trong project:

```bash
git clone https://github.com/wentaozhu/AutoShot.git "scripts/loaders/AutoShot"
```

### 6.2. Chuẩn bị checkpoint

Tải file checkpoint chính thức `ckpt_0_200_0.pth` theo hướng dẫn AutoShot, rồi
đặt vào project:

```text
models/autoshot/ckpt_0_200_0.pth
```

Kiểm tra trên Windows:

```powershell
Get-Item .\models\autoshot\ckpt_0_200_0.pth | Select-Object FullName,Length
```

Dung lượng checkpoint chính thức cần gần đúng:

```text
57243097 bytes
```

Khi chạy local, dùng checkpoint local:

```powershell
--checkpoint-uri "models/autoshot/ckpt_0_200_0.pth"
```

Nếu muốn worker cloud tải checkpoint từ GCS, upload checkpoint một lần:

```powershell
gcloud storage cp .\models\autoshot\ckpt_0_200_0.pth gs://aic_ai_2026/models/autoshot/ckpt_0_200_0.pth
```

Sau đó đổi tham số thành:

```powershell
--checkpoint-uri gs://aic_ai_2026/models/autoshot/ckpt_0_200_0.pth
```

### 6.3. Kiểm tra shard trước khi chạy thật

Sau bước `discover`, file shard smoke test phải tồn tại:

```powershell
Test-Path "scripts/loaders/frame_extraction_runs/smoke_autoshot_l21_001/shards/shard-00000.jsonl"
```

Xem 1 record đầu để chắc shard đang trỏ đúng video GCS:

```powershell
Get-Content "scripts/loaders/frame_extraction_runs/smoke_autoshot_l21_001/shards/shard-00000.jsonl" -First 1
```

Chạy dry-run cho shard. Lệnh này chỉ đọc shard và ghi result rỗng, chưa load
AutoShot, chưa tải video, chưa upload keyframe:

```powershell
python scripts/loaders/video_to_frame_gcs.py extract `
  --env-file .env `
  --manifest-shard "scripts/loaders/frame_extraction_runs/smoke_autoshot_l21_001/shards/shard-00000.jsonl" `
  --run-id smoke_autoshot_l21_001 `
  --dry-run
```

### 6.4. Chạy AutoShot thật cho một shard

Lệnh dưới đây chạy thật 1 video trong shard smoke test bằng checkpoint local:

```powershell
python scripts/loaders/video_to_frame_gcs.py extract `
  --env-file .env `
  --manifest-shard "scripts/loaders/frame_extraction_runs/smoke_autoshot_l21_001/shards/shard-00000.jsonl" `
  --checkpoint-uri "models/autoshot/ckpt_0_200_0.pth" `
  --run-id smoke_autoshot_l21_001 `
  --device auto `
  --upload-results
```

Ý nghĩa các tham số quan trọng:

| Tham số | Ý nghĩa |
| --- | --- |
| `--manifest-shard` | File JSONL chứa danh sách video cần xử lý trong shard |
| `--checkpoint-uri` | Đường dẫn local hoặc `gs://...` tới `ckpt_0_200_0.pth` |
| `--autoshot-repo-dir` | Optional; mặc định là `scripts/loaders/AutoShot` |
| `--device auto` | Tự dùng CUDA nếu có, nếu không thì dùng CPU |
| `--upload-results` | Upload artifact của shard lên GCS dưới `processed/keyframes_manifests/.../results/` |

Lưu ý: trong `extract`, ảnh keyframe JPG và `frames_manifest.jsonl` luôn được
upload lên `processed/keyframes/...`. Tham số `--upload-results` chỉ điều khiển
việc upload thêm `shard-00000.shot_segments.csv`, `shard-00000.errors.jsonl` và
`shard-00000.result.json`.

Loader sẽ làm đúng thuật toán trong notebook:

1. Download video từ GCS về scratch local.
2. Load `TransNetV2Supernet`.
3. Đọc frame bằng cùng cấu hình FFmpeg của AutoShot `get_frames`:
   resize `48x27`, `rgb24`.
4. Chạy batch 100 frame, lấy `prob[25:75]`.
5. Detect boundary với `threshold=0.296`.
6. Convert boundary thành shot với `min_shot_len=5`.
7. Dùng OpenCV đọc lại video gốc và lưu `first/middle/last`.
8. Upload ảnh JPG và `frames_manifest.jsonl` lên `processed/keyframes/...`.
9. Ghi shard result vào `results/`.

Output local sau `extract`:

```text
scripts/loaders/frame_extraction_runs/smoke_autoshot_l21_001/results/
  shard-00000.shot_segments.csv
  shard-00000.errors.jsonl
  shard-00000.result.json
```

## 7. Merge result và ghi `_SUCCESS`

Sau khi các shard đã chạy xong:

```powershell
python scripts/loaders/video_to_frame_gcs.py merge `
  --env-file .env `
  --dataset-id ai_challenge_2025 `
  --batch-id L21 `
  --source-version kaggle_current `
  --profile-version autoshot_v1 `
  --run-id smoke_autoshot_l21_001 `
  --manifest-uri "scripts/loaders/frame_extraction_runs/smoke_autoshot_l21_001/processing_manifest.jsonl" `
  --upload-final
```

Quality gate kiểm tra:

- Mỗi video planned có result.
- Không có `errors.jsonl`.
- Mọi row trong `shot_segments.csv` có `saved=true`.
- Không trùng `keyframe_id`.
- Không trùng `(video_id, frame_idx)`.
- `frame_idx` nằm trong khoảng `[shot_start_frame, shot_end_frame]`.
- `profile_version` đúng.
- `image_gcs_uri` tồn tại trên GCS.

Chỉ khi tất cả kiểm tra đạt, loader mới ghi:

```text
_SUCCESS
```

## 8. Chạy pilot nhiều video

Ví dụ chạy 10 video đầu của batch `L21`, mỗi shard 2 video:

```powershell
python scripts/loaders/video_to_frame_gcs.py discover `
  --env-file .env `
  --dataset-id ai_challenge_2025 `
  --batch-id L21 `
  --source-version kaggle_current `
  --max-videos 10 `
  --videos-per-shard 2 `
  --run-id pilot_autoshot_l21_010 `
  --upload-artifacts
```

Sau đó chạy lần lượt các shard:

```powershell
python scripts/loaders/video_to_frame_gcs.py extract `
  --env-file .env `
  --manifest-shard "scripts/loaders/frame_extraction_runs/pilot_autoshot_l21_010/shards/shard-00000.jsonl" `
  --checkpoint-uri "models/autoshot/ckpt_0_200_0.pth" `
  --run-id pilot_autoshot_l21_010 `
  --upload-results
```

Lặp lại với `shard-00001.jsonl`, `shard-00002.jsonl`, ...

Cuối cùng merge:

```powershell
python scripts/loaders/video_to_frame_gcs.py merge `
  --env-file .env `
  --dataset-id ai_challenge_2025 `
  --batch-id L21 `
  --profile-version autoshot_v1 `
  --run-id pilot_autoshot_l21_010 `
  --manifest-uri "scripts/loaders/frame_extraction_runs/pilot_autoshot_l21_010/processing_manifest.jsonl" `
  --upload-final
```

## 9. Chạy toàn bộ data L21-L30

Script dưới đây chạy tuần tự toàn bộ batch `L21` đến `L30`. Mỗi batch có
`run_id` riêng, gồm đủ các bước: kiểm tra GCS, tạo manifest, extract tất cả
shard, upload keyframe/result, rồi merge và upload final artifact kèm
`_SUCCESS`.

```powershell
$ErrorActionPreference = "Stop"

$envFile = ".env"
$datasetId = "ai_challenge_2025"
$sourceVersion = "kaggle_current"
$profileVersion = "autoshot_v1"
$checkpointUri = "models/autoshot/ckpt_0_200_0.pth"
$videosPerShard = 16
$runTag = Get-Date -Format "yyyyMMdd_HHmmss"
$batches = 21..30 | ForEach-Object { "L{0:D2}" -f $_ }

foreach ($batch in $batches) {
  $runId = "full_autoshot_$($batch.ToLower())_$runTag"
  $runDir = "scripts/loaders/frame_extraction_runs/$runId"

  python scripts/loaders/video_to_frame_gcs.py doctor `
    --env-file $envFile `
    --dataset-id $datasetId `
    --batch-id $batch `
    --source-version $sourceVersion `
    --profile-version $profileVersion

  python scripts/loaders/video_to_frame_gcs.py discover `
    --env-file $envFile `
    --dataset-id $datasetId `
    --batch-id $batch `
    --source-version $sourceVersion `
    --profile-version $profileVersion `
    --videos-per-shard $videosPerShard `
    --run-id $runId `
    --upload-artifacts

  $shards = Get-ChildItem -LiteralPath "$runDir/shards" -Filter "*.jsonl" |
    Sort-Object Name

  foreach ($shard in $shards) {
    python scripts/loaders/video_to_frame_gcs.py extract `
      --env-file $envFile `
      --manifest-shard $($shard.FullName) `
      --checkpoint-uri $checkpointUri `
      --run-id $runId `
      --device auto `
      --upload-results
  }

  python scripts/loaders/video_to_frame_gcs.py merge `
    --env-file $envFile `
    --dataset-id $datasetId `
    --batch-id $batch `
    --source-version $sourceVersion `
    --profile-version $profileVersion `
    --run-id $runId `
    --manifest-uri "$runDir/processing_manifest.jsonl" `
    --upload-final
}
```

Gợi ý vận hành:

- Giữ `$videosPerShard = 16` theo default của loader; nếu muốn retry từng video
  dễ hơn, đổi thành `1`.
- Nếu chạy lại batch đã xử lý một phần, mặc định `extract` sẽ giữ
  `--skip-existing`, nên keyframe đã có trên GCS sẽ không bị upload lại.
- Nếu chạy distributed trên nhiều worker, dùng phần `discover --upload-artifacts`
  một lần, chạy `extract` cho từng shard trên worker, rồi chạy
  `merge --download-results --upload-final` ở bước cuối.

## 10. Contract `shot_segments.csv`

File cuối cùng giữ đúng contract trong plan, gồm các cột chính:

```text
dataset_id
batch_id
video_id
video_name
video_gcs_uri
video_gcs_generation
shot_id
shot_id_local
shot_start_frame
shot_end_frame
shot_start_sec
shot_end_sec
frame_type
frame_idx
frame_sec
keyframe_id
image_rel_path
image_gcs_uri
image_storage_key
boundary_threshold
min_shot_len
saved
fps
total_frames_opencv
profile_version
run_id
```

Mapping sang database backend:

| CSV                                                                                                                 | Bảng        |
| ------------------------------------------------------------------------------------------------------------------- | ----------- |
| `video_id`, `video_name`, `fps`, `total_frames_opencv`, `video_gcs_uri`                                             | `videos`    |
| `shot_id`, `video_id`, `shot_start_frame`, `shot_end_frame`, `shot_start_sec`, `shot_end_sec`, `boundary_threshold` | `shots`     |
| `keyframe_id`, `video_id`, `shot_id`, `frame_idx`, `frame_sec`, `frame_type`, `image_rel_path`, `image_storage_key` | `keyframes` |

## 11. Lưu ý vận hành

- Khi chạy production bằng Docker, bake sẵn AutoShot vào image tại
  `scripts/loaders/AutoShot`.
- Mỗi thay đổi threshold/checkpoint/min shot length nên tạo
  `profile_version` mới.
- Raw video không bị sửa bởi pipeline này.
- Re-run cùng output prefix sẽ an toàn hơn khi giữ `--skip-existing`.
- Chỉ dùng `--overwrite` khi thật sự muốn ghi đè object output.
- Nếu chạy trên Cloud Batch, dùng `discover --upload-artifacts`, sau đó truyền
  URI shard dạng `gs://.../shards/shard-00000.jsonl` cho từng worker.

---

# Tham khảo legacy: hướng dẫn điền notebook upload Kaggle lên GCS

File notebook can dien cau hinh:

`notebooks/data processing/up_load_kaggle_to_gcs.ipynb`

Notebook nay chay truc tiep tren Kaggle. Thay vi doc file `.yaml`, toan bo cau hinh nam trong cell `CONFIG` va nhom bien runtime ngay ben duoi `CONFIG`.

## 1. Chuan bi tren Kaggle

1. Mo Kaggle Notebook.
2. Attach dataset Kaggle can upload vao notebook.
3. Vao `Add-ons` -> `Secrets` va them:
   - `GCS_BUCKET`: ten bucket GCS, vi du `my-aic-bucket`.
   - `GCS_CREDENTIALS_JSON`: noi dung JSON cua Google Cloud service account key.
4. Bat Internet cho Kaggle Notebook neu can cai package va upload len GCS.

Service account can co quyen ghi vao bucket, toi thieu la `Storage Object Creator`. Neu can bo qua file da ton tai bang `SKIP_EXISTING = True`, service account cung can quyen doc object metadata, vi du `Storage Object Viewer`.

## 2. Chon dataset can upload

Trong cell cau hinh, sua `SOURCE_ID` theo dataset da attach:

| Dataset                      | `SOURCE_ID`                    | Mount path mac dinh                  | Batch hop le    |
| ---------------------------- | ------------------------------ | ------------------------------------ | --------------- |
| AI Challenge 2025 L21-L30    | `l21_l30_ai_challenge_2025`    | `/kaggle/input/ai-challenge-2025`    | `L21` den `L30` |
| Data Video Batch 2.1 K01-K10 | `k01_k10_data_video_batch_2_1` | `/kaggle/input/data-video-batch-2-1` | `K01` den `K10` |
| Data Video Batch 2.2 K11-K20 | `k11_k20_data_video_batch_2_2` | `/kaggle/input/data-video-batch2-2`  | `K11` den `K20` |

Vi du:

```python
SOURCE_ID = 'k01_k10_data_video_batch_2_1'
```

Neu mount path tren Kaggle khac voi mac dinh, dien lai `INPUT_ROOT`:

```python
INPUT_ROOT = '/kaggle/input/ten-folder-thuc-te'
```

Neu mount path dung voi bang tren, giu:

```python
INPUT_ROOT = None
```

## 3. Chon batch can upload

Upload toan bo batch cua source:

```python
BATCHES = 'all'
```

Chi upload mot vai batch:

```python
BATCHES = 'L21,L22'
```

Hoac:

```python
BATCHES = ['K01', 'K02']
```

Notebook tu dong nhan batch tu duong dan file bang regex trong `CONFIG`. Neu file khong co batch trong ten folder/ten file, file do se vao `unmapped.jsonl`.

## 4. Dien bucket va credential

Khuyen nghi dung Kaggle Secrets:

```python
GCS_BUCKET = ''
GCS_CREDENTIALS_FILE = ''
```

Khi de trong, notebook se lay:

- `GCS_BUCKET` tu Kaggle Secret hoac environment variable.
- `GCS_CREDENTIALS_JSON` tu Kaggle Secret hoac environment variable.

Neu muon dien bucket truc tiep trong notebook:

```python
GCS_BUCKET = 'my-aic-bucket'
```

Neu ban copy ca duong dan GCS, notebook cung co the tu tach bucket va prefix:

```python
GCS_BUCKET = 'gs://my-aic-bucket/raw/source=kaggle'
GCS_PREFIX = ''
```

Trong truong hop nay, `raw/source=kaggle` se duoc dung lam raw prefix. Khong dien prefix vao ca `GCS_BUCKET` va `GCS_PREFIX` cung luc, vi notebook se bao loi de tranh tao duong dan bi lap.

Chi dung `GCS_CREDENTIALS_FILE` neu ban da co file JSON service account trong moi truong notebook:

```python
GCS_CREDENTIALS_FILE = '/kaggle/working/service-account.json'
```

Khong nen hard-code noi dung private key JSON vao notebook neu notebook co the duoc chia se.

## 5. Dien prefix va version tren GCS

Mac dinh notebook upload raw files vao prefix:

```python
GCS_PREFIX = ''
```

Gia tri rong nghia la:

- Neu `GCS_BUCKET` la `gs://bucket/prefix`, dung prefix trong `GCS_BUCKET`.
- Neu `GCS_BUCKET` chi la ten bucket, dung default:

```text
raw/source=kaggle
```

Neu muon chi ro prefix rieng, de `GCS_BUCKET` la ten bucket thuan:

```python
GCS_BUCKET = 'my-aic-bucket'
GCS_PREFIX = 'raw/source=kaggle'
```

Duong dan object tren GCS se co dang:

```text
gs://<bucket>/<raw_prefix>/dataset=<dataset_id>/source_version=<source_version>/batch=<batch_id>/original/<relative_path>
```

Mac dinh `SOURCE_VERSION = ''` se dung:

```text
kaggle_current
```

Neu can gan version rieng:

```python
SOURCE_VERSION = 'kaggle_2026_07_07'
```

## 6. Chay dry run truoc

Lan dau nen chay test nho:

```python
DRY_RUN = True
MAX_FILES = 1
```

Dry run se tao manifest, metrics va summary local trong:

```text
ingestion_runs/<run_id>/
```

Dry run khong upload file len GCS va khong yeu cau bucket neu `GCS_BUCKET` dang rong.

Sau khi summary dung, bo gioi han file:

```python
MAX_FILES = None
```

## 7. Upload that

Khi dry run da dung, doi:

```python
DRY_RUN = False
```

Giu cau hinh an toan de re-run:

```python
SKIP_EXISTING = True
OVERWRITE = False
```

Neu can ghi de object da ton tai, phai tat skip existing:

```python
SKIP_EXISTING = False
OVERWRITE = True
```

`WORKERS = 4` la muc mac dinh on dinh. Neu mang on dinh va dataset lon, co the thu:

```python
WORKERS = 8
```

## 8. Artifact dau ra

Moi lan chay tao mot folder:

```text
ingestion_runs/<run_id>/
```

Trong do co:

- `manifest.jsonl`: danh sach file du kien upload va GCS URI.
- `metrics.csv`: trang thai tung file.
- `errors.jsonl`: loi upload tung file neu co.
- `unmapped.jsonl`: file khong map duoc batch, chi co khi phat sinh.
- `summary.json`: tong ket run.
- `ingest.log`: log chi tiet.

Neu `UPLOAD_RUN_ARTIFACTS = True` va `DRY_RUN = False`, notebook upload artifact len GCS:

```text
manifests/pipeline=kaggle_ingest/run_id=<run_id>/
logs/pipeline=kaggle_ingest/run_id=<run_id>/
```

## 9. Cau hinh mau

Upload batch `K01` va `K02` cua Data Video Batch 2.1:

```python
SOURCE_ID = 'k01_k10_data_video_batch_2_1'
BATCHES = 'K01,K02'
INPUT_ROOT = None
GCS_BUCKET = ''
GCS_PREFIX = ''
GCS_CREDENTIALS_FILE = ''
SOURCE_VERSION = ''
WORKERS = 4
RUN_ID = ''
RUN_DIR = Path('ingestion_runs')
MAX_FILES = 1
DRY_RUN = True
SKIP_EXISTING = True
OVERWRITE = False
UPLOAD_RUN_ARTIFACTS = True
FAIL_ON_UNMAPPED = False
NO_PROGRESS = False
VERBOSE = False
```

Sau khi dry run dung, sua:

```python
MAX_FILES = None
DRY_RUN = False
```

# Version 2 - Feature extraction: chay trich xuat frame AutoShot tren Kaggle

Phan nay dung de chay notebook trich xuat keyframe truc tiep tren Kaggle, sau do upload anh keyframe va artifact len GCS.

File chinh:

- `scripts/loaders/video_to_frame_gcs.ipynb`: notebook mang len Kaggle de chay.
- `scripts/loaders/video_to_frame_gcs_params.py`: file tham so, sua truoc khi chay.
- `scripts/loaders/video_to_frame_gcs_kaggle.py`: helper pipeline cho Kaggle.

Notebook nay dung format output tuong tu `video_to_frame_gcs.py`:

```text
gs://<bucket>/processed/keyframes/
  dataset=ai_challenge_2025/
  batch=<batch_id>/
  profile=autoshot_v1/
  video_id=<video_id>/
    shot_0000_first_f000000.jpg
    shot_0000_middle_f000037.jpg
    shot_0000_last_f000074.jpg
    frames_manifest.jsonl

gs://<bucket>/processed/keyframes_manifests/
  dataset=ai_challenge_2025/
  batch=<batch_id>/
  profile=autoshot_v1/
  run_id=<run_id>/
    processing_manifest.jsonl
    shot_segments.csv
    errors.jsonl
    video_summaries.jsonl
    summary.json
    run.log
    _SUCCESS
```

## 1. Chuan bi Kaggle notebook

Tao Kaggle notebook moi va attach cac dataset/model can thiet:

- Dataset video: `aresusayhi/ai-challenge-2025`.
- AutoShot checkpoint, vi du path mac dinh:

```text
/kaggle/input/models/khngxuninh/autoshot/pytorch/default/1/ckpt_0_200_0.pth
```

Neu checkpoint nam o path khac, sua `CHECKPOINT_PATH` trong `video_to_frame_gcs_params.py`.

Kaggle input co the co cac dang folder nhu:

```text
/kaggle/input/ai-challenge-2025/Videos/Videos/Videos_L21_a/video/L21_V001.mp4
/kaggle/input/ai-challenge-2025/Videos/Videos/Video_L30_a/video/L30_V001.mp4
```

Notebook se tu quet de quy va detect batch tu path/video name.

## 2. Cau hinh secrets GCS

Khuyen nghi dung Kaggle Secrets:

```text
GCS_BUCKET
GCS_CREDENTIALS_JSON
```

Trong `video_to_frame_gcs_params.py`, giu rong neu da co secrets:

```python
GCS_BUCKET = ""
GCS_CREDENTIALS_FILE = ""
GCS_BUCKET_SECRET_NAME = "GCS_BUCKET"
GCS_CREDENTIALS_JSON_SECRET_NAME = "GCS_CREDENTIALS_JSON"
```

Neu bucket raw video tren GCS dang theo format cu cua repo:

```text
raw/source=kaggle/dataset=ai_challenge_2025/source_version=kaggle_current/batch=L21/ai-challenge-2025/Videos/...
```

giu:

```python
RAW_RELATIVE_PATH_PREFIX = "ai-challenge-2025"
```

Neu raw object tren GCS khong co folder `ai-challenge-2025`, sua thanh:

```python
RAW_RELATIVE_PATH_PREFIX = ""
```

## 3. Sua tham so chay

Trong Kaggle, chay cell dau cua notebook de tao 2 file:

```text
/kaggle/working/video_to_frame_gcs_params.py
/kaggle/working/video_to_frame_gcs_kaggle.py
```

Sau do sua file `/kaggle/working/video_to_frame_gcs_params.py`.

Cac tham so quan trong:

```python
DATASET_ID = "ai_challenge_2025"
SOURCE_VERSION = "kaggle_current"
PROFILE_VERSION = "autoshot_v1"

THRESHOLD = 0.296
MIN_SHOT_LEN = 5
DEVICE = "auto"

DEMO_BATCHES = "L21"
DEMO_MAX_VIDEOS = 2

FULL_BATCHES = "all"
FULL_MAX_VIDEOS = None

SKIP_EXISTING = True
OVERWRITE = False
UPLOAD_TO_GCS = True
UPLOAD_RUN_ARTIFACTS = True
```

Neu chi muon test local artifact, khong upload GCS:

```python
UPLOAD_TO_GCS = False
```

## 4. Chay dry run

Trong notebook, chay cell `Dry Run`.

Dry run chi quet video va tao manifest, khong load AutoShot, khong upload GCS.

Output can xem:

```text
/kaggle/working/frame_extraction_runs/<run_id>/artifacts/processing_manifest.jsonl
/kaggle/working/frame_extraction_runs/<run_id>/artifacts/summary.json
/kaggle/working/frame_extraction_runs/<run_id>/run.log
```

Neu `planned_videos = 0`, kiem tra lai:

- `INPUT_ROOT`
- `EXPECTED_BATCHES`
- `BATCH_REGEX`
- folder dataset da attach tren Kaggle

## 5. Chay demo 1 batch

Sau khi dry run dung, chay cell `Demo One Batch`.

Mac dinh:

```python
DEMO_BATCHES = "L21"
DEMO_MAX_VIDEOS = 2
```

Cell nay se:

- clone AutoShot neu `/kaggle/working/AutoShot` chua ton tai.
- load checkpoint AutoShot.
- trich first/middle/last frame cho moi shot.
- upload anh keyframe len GCS neu `UPLOAD_TO_GCS = True`.
- ghi log theo tung video, co phan tram tien do va thoi gian.

Log dang:

```text
[1/2 0.0%] start video_id=L21_V001 batch=L21 path=...
[1/2 50.0%] done video_id=L21_V001 shots=... keyframes=... upload_ms=... total_ms=...
```

## 6. Chay full dataset

De tranh bam nham `Run All`, cell full run co guard. Truoc khi chay full, sua:

```python
CONFIRM_FULL_RUN = "RUN_FULL_DATASET"
FULL_BATCHES = "all"
FULL_MAX_VIDEOS = None
```

Neu muon chay mot so batch:

```python
FULL_BATCHES = "L21,L22,L23"
```

Notebook se chay tung batch rieng, moi batch co mot `run_id` rieng de artifact tren GCS duoc group gon theo batch.

## 7. Artifact va cot CSV

File `shot_segments.csv` gom cac cot chinh phu hop voi data model `videos`, `shots`, `keyframes`:

- `dataset_id`
- `batch_id`
- `video_id`
- `video_name`
- `video_gcs_uri`
- `shot_id`
- `shot_id_local`
- `shot_start_frame`
- `shot_end_frame`
- `shot_start_sec`
- `shot_end_sec`
- `frame_type`
- `frame_idx`
- `frame_sec`
- `keyframe_id`
- `image_rel_path`
- `image_gcs_uri`
- `image_storage_key`
- `boundary_threshold`
- `min_shot_len`
- `saved`
- `fps`
- `total_frames_opencv`
- `profile_version`
- `run_id`

Moi video cung co:

```text
frames_manifest.jsonl
```

duoi prefix keyframe cua video do.

## 8. Theo doi toc do va loi

Trong Kaggle notebook, xem:

```text
/kaggle/working/frame_extraction_runs/<run_id>/run.log
/kaggle/working/frame_extraction_runs/<run_id>/artifacts/video_summaries.jsonl
/kaggle/working/frame_extraction_runs/<run_id>/artifacts/errors.jsonl
/kaggle/working/frame_extraction_runs/<run_id>/artifacts/summary.json
```

`video_summaries.jsonl` co cac metric:

- `num_frames`
- `num_boundaries`
- `num_shots`
- `num_keyframes`
- `score_ms`
- `frame_extract_ms`
- `upload_ms`
- `duration_ms`

`summary.json` co tong quan:

- `planned_videos`
- `succeeded_videos`
- `failed_videos`
- `keyframes`
- `duration_ms`
- `videos_per_hour`
- `keyframes_per_min`

Neu `errors.jsonl` khong rong, xem `video_id`, `error_code`, `error_message` de retry batch/video do.

# Kaggle To Cloud Ingestion Report

## Muc tieu

Pipeline `scripts/upload_kaggle_to_gcs.py` dung de dua du lieu video/archive da mount san tren Kaggle Notebook, VM, hoac worker Composer len Google Cloud Storage. Script khong download truc tiep tu Kaggle API; no chi doc file tu thu muc input va upload theo manifest.

## Nguon cau hinh

Source of truth la `configs/data_ingestion_sources.yaml`.

Script doc cac nhom tham so chinh tu YAML:

| Nhom | Truong |
| --- | --- |
| Source | `source_id`, `display_name`, `source_type`, `enabled` |
| Kaggle | `dataset_ref`, `dataset_url`, `kaggle_mount_path` |
| Dataset dich | `dataset_id` |
| Batch | `expected_batches`, `batch_detection.strategy`, `batch_detection.pattern` |
| File filter | `include_patterns`, `exclude_patterns` |
| GCS | `gcs.raw_prefix`, `gcs.control_prefix`, `gcs.logs_prefix`, `gcs.quarantine_prefix` |

Script van chap nhan `target_dataset_id` nhu fallback de tuong thich voi config cu, nhung config moi nen dung `dataset_id`.

## Luong xu ly

1. Load YAML config va merge `defaults` vao tung source.
2. Chon source bang `--source-id`.
3. Chon batch bang `--batches`; mac dinh la `all`.
4. Xac dinh input root tu `--input-root` hoac `kaggle_mount_path`.
5. Scan file theo `include_patterns` va loai tru theo `exclude_patterns`.
6. Detect batch tu relative path bang regex trong `batch_detection.pattern`.
7. Tao manifest cho cac file map duoc vao batch duoc chon.
8. Neu `--dry-run`, chi ghi artifacts local va khong upload.
9. Neu upload that, upload song song bang `--workers`.
10. Ghi metrics, errors, summary va upload run artifacts len GCS neu `--upload-run-artifacts` dang bat.

## Layout GCS raw

Object raw duoc ghi theo format:

```text
raw/source=kaggle/dataset=<dataset_id>/source_version=<source_version>/batch=<batch_id>/original/<relative_path>
```

Y nghia:

| Segment | Y nghia |
| --- | --- |
| `raw/source=kaggle` | Vung raw cho nguon Kaggle |
| `dataset=<dataset_id>` | Dataset dich trong he thong |
| `source_version=<source_version>` | Version nguon, mac dinh tu YAML |
| `batch=<batch_id>` | Batch da detect, vi du `L21`, `K01` |
| `original/` | File goc chua xu ly |
| `<relative_path>` | Duong dan tu input root |

Vi du:

```text
gs://aic_ai_2026/raw/source=kaggle/dataset=ai_challenge_2025/source_version=kaggle_current/batch=L21/original/L21/video_001.mp4
```

## Dieu khien run

| Tham so | Tac dung |
| --- | --- |
| `--config` | Chon file YAML config |
| `--list-sources` | Liet ke source trong YAML |
| `--source-id` | Chon source can ingest |
| `--batches` | Chon batch, vi du `L21,L22` hoac `all` |
| `--input-root` | Override mount path |
| `--gcs-bucket` | Bucket GCS dich |
| `--gcs-prefix` | Override raw prefix |
| `--source-version` | Override source version |
| `--workers` | So thread upload song song |
| `--run-id` | Dinh danh run co dinh neu can retry/audit |
| `--run-dir` | Thu muc ghi artifacts local |
| `--max-files` | Gioi han so file de smoke test |
| `--dry-run` | Chi lap ke hoach, khong upload |
| `--skip-existing` | Bo qua object da ton tai, mac dinh bat |
| `--no-skip-existing --overwrite` | Cho phep ghi de co chu y |
| `--fail-on-unmapped` | Fail run neu co file khong map duoc batch |
| `--no-progress` | Tat progress bar |

## Monitoring va artifacts

Moi run tao artifacts trong:

```text
ingestion_runs/<run_id>/
```

| File | Noi dung |
| --- | --- |
| `manifest.jsonl` | Danh sach file duoc lap ke hoach upload |
| `summary.json` | Tong ket run: planned/uploaded/skipped/failed/bytes |
| `errors.jsonl` | File loi kem thong tin exception |
| `metrics.csv` | Trang thai tung file, duration, bytes, generation |
| `ingest.log` | Log text cua run |
| `unmapped.jsonl` | Chi co khi co file khong detect duoc batch |

Neu upload that va `--upload-run-artifacts` dang bat, artifacts duoc day len:

```text
manifests/pipeline=kaggle_ingest/run_id=<run_id>/
logs/pipeline=kaggle_ingest/run_id=<run_id>/
```

Dashboard toi thieu nen doc tu `metrics.csv` va `summary.json`:

| Metric | Nguon |
| --- | --- |
| So file uploaded/skipped/failed | `metrics.csv.status` |
| Tong bytes upload | `summary.json.bytes_uploaded` hoac sum `metrics.csv.bytes_uploaded` |
| Loi theo file | `errors.jsonl` |
| Thoi gian theo file | `metrics.csv.duration_ms` |
| Trang thai run | `summary.json.failed_files`, exit code |

## Lenh mau

Kiem tra source:

```bash
python scripts/upload_kaggle_to_gcs.py --list-sources
```

Smoke test khong upload:

```bash
python scripts/upload_kaggle_to_gcs.py \
  --source-id l21_l30_ai_challenge_2025 \
  --batches L21 \
  --dry-run \
  --max-files 5
```

Upload that:

```bash
python scripts/upload_kaggle_to_gcs.py \
  --source-id l21_l30_ai_challenge_2025 \
  --batches L21,L22 \
  --workers 4
```

## Luu y van hanh

- Chay `--dry-run --max-files` truoc khi upload that de kiem tra mapping batch va GCS key.
- Dung `--skip-existing` khi retry de tranh upload lai object da thanh cong.
- Chi dung `--overwrite` khi chac chan can thay object cu.
- Neu `unmapped.jsonl` co nhieu file, can sua `batch_detection.pattern` trong YAML truoc khi upload tiep.
- Neu chay tren Composer, xem them DAG `dags/data_ingestion_kaggle.py`; DAG chi delegate ve script nay nen artifacts co cung format.

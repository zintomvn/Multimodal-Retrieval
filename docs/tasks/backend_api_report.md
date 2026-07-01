# Backend API Stability Report

Ngay kiem tra: 2026-07-01

## Ket luan nhanh

Backend FastAPI co cau truc module ro rang va phan core retrieval/submission da co test bao phu kha tot. Tuy nhien chua nen ket luan "on dinh production" cho tat ca endpoint. Nhom endpoint doc, health, job status, retrieval fallback/mock, media context/thumbnail va submission happy path tuong doi on. Cac rui ro chinh nam o dataset creation, missing-resource handling, cau hinh model runtime, ingest/upload file lon va cleanup temp file.

## Pham vi da ra soat

- Entrypoint va router: `apps/backend/app/main.py`, `modules/*/router.py`.
- Business logic chinh: retrieval, ingest, submissions, model registry, DB bootstrap.
- Schema/config: `configs/model_registry.yaml`, `configs/retrieval_profiles.yaml`, SQLAlchemy models.
- Test suite trong `apps/backend/tests`.

## Kiem chung da chay

| Lenh / kiem tra | Ket qua |
| --- | --- |
| `ast.parse` toan bo `apps/backend/app/**/*.py` | Pass: 62 file Python parse OK, khong loi cu phap. |
| `py -3.13 -m pytest tests/test_model_runtime_registry.py tests/test_storage_key_format.py -q` voi `DATA_ROOT` override | Pass: 5/5. |
| `python -m pytest tests -q` bang Python 3.14 he thong | Bi chan: Python 3.14 khong co `pytest`. |
| Full pytest bang Python 3.13 | Bi chan boi sandbox/tmp + SQLite file I/O (`PermissionError`, `sqlite3.OperationalError: disk I/O error`). |
| Smoke TestClient voi SQLite file | Bi chan boi SQLite file I/O trong moi truong hien tai. |
| Smoke TestClient voi SQLite `:memory:` | Bi chan do Alembic tao schema tren connection khac, seed gap `no such table: datasets`. |

Ghi chu: cac loi full-test o tren la gioi han moi truong chay hien tai, khong phai bang chung logic backend fail. Can chay lai trong Docker/dev env co temp dir va DB file/Postgres ghi duoc.

## Tong quan endpoint

| Endpoint | Danh gia hien tai | Ghi chu |
| --- | --- | --- |
| `GET /healthz`, `GET /readyz` | On | Don gian, khong phu thuoc DB. |
| `GET /api/datasets` | Kha on | Can luu y lazy load `item.videos` co the N+1 khi dataset lon. |
| `POST /api/datasets` | Can sua | Co nguy co 500 do `dataset_code` default trung seed mock. |
| `GET /api/models` | On co dieu kien | Phu thuoc `DATA_ROOT` writable va config registry doc duoc. |
| `POST /api/ingest/jobs` | Mock on, demo can hardening | Demo chay dong bo trong request; loi duoc ghi vao job `FAILED`. Co code unreachable. |
| `POST /api/ingest/upload/gcs`, `/upload/milvus` | Phu thuoc ha tang | Queue job duoc, ket qua thuc phu thuoc GCS/Milvus/path. Nen validate config/path som hon. |
| `POST /api/ingest/upload/file/gcs`, `/upload/file/milvus` | Can hardening | Doc upload vao RAM toi 2GB va co nguy co ro ri temp file. |
| `GET /api/jobs/{job_id}` | On | Missing job tra 404 dung ky vong. |
| `POST /api/retrieval/search`, `/qa`, `/trake` | On trong fallback/mock | Neu strict hybrid hoac backend model/vector/text that chua san sang thi se 400/fallback tuy option. |
| `GET /api/retrieval/runs/{run_id}` | Can sua | Missing run hien co nguy co 500 vi `.one()` khong bat loi. |
| `POST /api/retrieval/runs/{run_id}/select` | Can sua | `run_id` dang bi bo qua; co the update result khong thuoc run. |
| `GET /api/media/frames/{frame_id}/context` | On | Missing frame tra 404. |
| `GET /api/media/frames/{frame_id}/thumbnail` | Kha on | Co fallback SVG, nhung text chua escape XML. |
| `POST /api/submissions*` | Happy path on, missing-resource can sua | Validate/export dung luong thi on; missing submission co nguy co 500. |

## Van de uu tien

1. `POST /api/datasets` de bi unique collision.
   - `DatasetCreate` khong co `dataset_code`: `apps/backend/app/modules/datasets/router.py:13`.
   - Tao `Dataset(...)` khong set `dataset_code`: `apps/backend/app/modules/datasets/router.py:37`.
   - Model lai co default `dataset_code="mock-aic-2026"` va unique: `apps/backend/app/db/models.py:34`.
   - De xuat: them `dataset_code` vao request hoac sinh unique code tu name/version; bat `IntegrityError` va tra 409 thay vi 500.

2. Missing resource chua duoc map sang 404 o retrieval/submission.
   - `RetrievalService.get_run()` dung `.one()`: `apps/backend/app/modules/retrieval/service.py:112`.
   - Router `/runs/{run_id}` khong catch `NoResultFound`: `apps/backend/app/modules/retrieval/router.py:61`.
   - `SubmissionService.validate()` va `export_zip()` cung dung `.one()`: `apps/backend/app/modules/submissions/service.py:71`, `apps/backend/app/modules/submissions/service.py:241`.
   - De xuat: dung `.first()`/`db.get()` va raise `HTTPException(404)`.

3. Select retrieval result bo qua `run_id`.
   - Router gan `_ = run_id` roi update theo `result_ids`: `apps/backend/app/modules/retrieval/router.py:70`.
   - De xuat: filter `RetrievalResult.query_run_id == run_id`; neu khong co run thi 404, result khong thuoc run thi bo qua hoac 400.

4. Cau hinh model runtime co the lam dev/mock khong on dinh.
   - `configs/model_registry.yaml` dang enable `openai_embedding` den `localhost:8001`: `configs/model_registry.yaml:8`, `configs/model_registry.yaml:19`.
   - Trong `MOCK_MODE=true`, vector/text client la mock nhung embedder van lay entry enabled dau tien tu registry: `apps/backend/app/core/deps.py:88`.
   - He qua: search non-strict co the fallback, nhung `strict_hybrid=true` se fail neu embedding service 8001 khong chay.
   - De xuat: neu `MOCK_MODE=true` thi force mock model runtime, hoac tach registry dev/prod.

5. Ingest/upload can hardening.
   - Code sau `return` khong bao gio chay: `apps/backend/app/modules/ingest/router.py:100`.
   - Browser upload doc toan bo file vao memory toi 2GB: `apps/backend/app/modules/ingest/router.py:310`.
   - Cleanup temp file dung `shutil.rmtree()` ca voi file path, de sot file upload don le: `apps/backend/app/modules/ingest/router.py:386`, `apps/backend/app/modules/ingest/router.py:395`.
   - De xuat: stream upload ra temp file, dung `Path.unlink()` cho file va `shutil.rmtree()` cho folder, validate config GCS/Milvus truoc khi queue neu co the.

6. `get_settings()` co side effect tao thu muc.
   - `settings.data_root.mkdir(...)` chay ngay khi lay settings: `apps/backend/app/core/config.py:45`.
   - Da quan sat test fail khi `.env` tro `DATA_ROOT=/app/data` trong Windows sandbox.
   - De xuat: chi tao thu muc o startup hoac cac flow can ghi; log ro loi config.

7. Thumbnail SVG chua escape text.
   - `title` va `caption` duoc chen thang vao SVG: `apps/backend/app/modules/media/router.py:102`.
   - De xuat: `html.escape()` de tranh invalid XML khi caption co `&`, `<`, `>`.

8. Alembic fallback dang nuot moi exception.
   - `init_db()` catch `Exception` roi fallback `create_all`: `apps/backend/app/db/bootstrap.py:24`.
   - De xuat: log warning/exception day du; chi fallback trong dev/test mode.

## De xuat buoc tiep theo

1. Sua 4 diem uu tien: dataset create, 404 handling, run-scoped select, upload temp cleanup.
2. Them API-level tests bang `TestClient` voi DB fixture dung `StaticPool` cho SQLite in-memory, hoac Postgres test container.
3. Chay lai full suite trong Docker/dev env:

```powershell
cd apps\backend
$env:DATA_ROOT="..\..\data\pytest_backend"
$env:DATABASE_URL="sqlite:///./data/test.db"
py -3.13 -m pytest tests -q
```

4. Chay smoke that voi stack day du neu dung retrieval strict: embedding service `localhost:8001`, Milvus, Elasticsearch, object storage.


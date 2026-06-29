## Kế Hoạch Triển Khai Backend Theo Module (test + push từng module)

### Tóm tắt
- Triển khai trên **branch hiện tại**: `feature/backend-api`.
- Sau mỗi module: **dừng**, chạy test gate của module, commit theo chuẩn conventional scope, push, mở PR riêng.
- Chuỗi module backend: `M0 Contract/Preflight` → `M1 DB/Storage` → `M2 Ingestion` → `M3 Core Retrieval` → `M4 Fusion/Filter` → `M5 QA/TRAKE/Submission hardening`.
- Scope commit bắt buộc: `db-storage`, `ingestion`, `retrieval`, `fusion`, `qa-trake`, `submission`, `cloud`, `test`, `docs`, `chore`.

### Kế hoạch chi tiết theo module
1. **M0 — Contract & Data Preflight**
- Thực thi: khóa schema contract theo `docs/database_schema_demo_v1.md` + `docs/database_erd.md` + `db_attribute_mapping.md`, xác nhận nguồn ingest chuẩn (`demo/features/*`, `demo/annotations/*/annotations.jsonl`), đánh dấu rõ nguồn legacy.
- Interface thay đổi: không đổi API runtime; chuẩn hóa tài liệu để mọi module sau dùng cùng một contract.
- Test gate:
  - kiểm tra thống kê dữ liệu nguồn (videos/keyframes/events/annotations) được ghi rõ trong docs;
  - không còn mô tả ingest mặc định dùng `demo/Event Embedding/*` hoặc `demo/annotations.jsonl`;
  - execution plan và implementation spec đồng bộ nguồn ingest.
- Commit:
  - `docs(chore): align data contract to features-and-annotations structure`
- PR title: `docs(chore): module M0 contract and preflight`.

2. **M1 — DB & Storage Foundation**
- Thực thi: Alembic baseline theo `docs/database_schema_demo_v1.md`, bảng core (`datasets/videos/shots/keyframes/frame_annotations/events/event_keyframes`), index bắt buộc, storage bootstrap (local/S3 key format).
- Interface thay đổi: schema DB chính thức; contract id nghiệp vụ (`video_id/shot_id/keyframe_id/event_id`).
- Test gate:
  - migration up/down chạy được;
  - unique/FK/orphan checks pass;
  - smoke SQL count query chạy đúng.
- Commit:
  - `feat(db-storage): add demo-aligned relational schema and indexes`
  - `test(db-storage): add migration integrity and constraint checks`
  - `docs(db-storage): document migration and schema contract`
- PR title: `feat(db-storage): module M1 db-storage foundation`.

3. **M2 — Ingestion & Import Pipeline**
- Thực thi: `import_pg`, `import_milvus`, `import_es`, media upload; preflight reconcile cho `demo/frames`, mapping `demo/features/*`, và annotations per-video trong `demo/annotations/*/annotations.jsonl`.
- Interface thay đổi: ingest job payload/report bổ sung thống kê reconcile + failed rows; mapping chuẩn `row i = n-1`.
- Test gate:
  - PG keyframes count khớp expected hợp lệ;
  - ES docs count khớp tổng dòng hợp lệ từ `demo/annotations/*/annotations.jsonl`;
  - Milvus vectors count khớp keyframes/events nhập thành công;
  - idempotency ingest lần 2 không nhân đôi.
- Commit:
  - `feat(ingestion): implement pg/es/milvus/media import pipeline`
  - `feat(cloud): add Supabase and Zilliz import integration`
  - `test(ingestion): add reconcile, count parity, and idempotency tests`
  - `docs(ingestion): add import runbook and troubleshooting`
- PR title: `feat(ingestion): module M2 import pipeline`.

4. **M3 — Core Retrieval APIs**
- Thực thi: retrieval flow thật `embed -> Milvus ANN -> ES text -> score_breakdown -> persist query_runs/results`.
- Interface thay đổi:
  - `/api/retrieval/search` trả breakdown có `semantic_score`, `text_score`, `quality_score`, `final_score`;
  - thống nhất response contract cho KIS/QA/TRAKE.
- Test gate:
  - KIS/QA smoke pass với dataset đã ingest;
  - run persistence đúng rank uniqueness;
  - latency snapshot và lỗi input validation.
- Commit:
  - `feat(retrieval): implement core vector and text retrieval pipeline`
  - `test(retrieval): add ranking, contract, and persistence tests`
  - `docs(retrieval): update api examples and score breakdown`
- PR title: `feat(retrieval): module M3 core retrieval apis`.

5. **M4 — Fusion & Filtering**
- Thực thi: Weighted Sum + RRF theo profile; filter `video_code`, `time_range`, `objects/scene`.
- Interface thay đổi:
  - `SearchRequest.options` bổ sung filter fields rõ ràng;
  - response ghi lý do lọc trong metadata debug.
- Test gate:
  - filter loại đúng out-of-scope results;
  - RRF/weighted profile cho kết quả đúng thứ tự kỳ vọng fixture;
  - regression test không phá M3 contract.
- Commit:
  - `feat(fusion): add weighted-sum and rrf fusion engine`
  - `feat(retrieval): add metadata filters for video time and objects`
  - `test(fusion): add ranking and filter behavior tests`
- PR title: `feat(fusion): module M4 fusion and filtering`.

6. **M5 — QA/TRAKE/Submission Hardening**
- Thực thi: QA post-process <=100 chars; TRAKE sequence stability; submission validator + export chuẩn Codabench.
- Interface thay đổi:
  - `/api/retrieval/qa` đảm bảo answer constraint;
  - `/api/retrieval/trake` trả sequence ổn định có ordering metadata;
  - `/api/submissions/*` validation report chi tiết lỗi theo dòng.
- Test gate:
  - QA length + answer format pass;
  - TRAKE order/delta/min_match pass;
  - zip export đúng cấu trúc `submission/*.csv`, no header.
- Commit:
  - `feat(qa-trake): harden qa and temporal sequence retrieval`
  - `feat(submission): enforce codabench export validation rules`
  - `test(submission): add csv-zip format and validation tests`
  - `docs(submission): update export contract and examples`
- PR title: `feat(qa-trake): module M5 advanced retrieval and submission`.

### Quy trình push/PR chuẩn (áp dụng cho mọi module)
1. Hoàn tất code + test module.
2. Chạy test gate module và ghi log ngắn vào PR description.
3. Commit theo chuẩn conventional scope ở trên.
4. `git push origin feature/backend-api`.
5. Mở PR mới cho module (không gộp nhiều module trong một PR).
6. Chờ review xong module hiện tại mới bắt đầu module tiếp theo.

### Assumptions đã khóa
- Làm backend modules trước, chưa triển khai frontend module.
- Dùng chuẩn commit `feat(scope): ...` như bạn chọn.
- Mỗi module có PR riêng, dù cùng một branch.
- Tài liệu schema mới hiện tại là contract chính để code theo.

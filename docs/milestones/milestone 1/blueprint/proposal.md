# Multimodal Retrieval Assistant — Project Proposal

> Blueprint hệ thống thi AI Challenge 2026 • Phiên bản 1.0

## 1. Vấn đề

AI Challenge 2026 yêu cầu xây dựng **trợ lý ảo thông minh hỗ trợ phân tích và truy xuất thông tin chuyên sâu trong dữ liệu multimedia lớn** gồm video, hình ảnh, âm thanh và văn bản. Bài toán có tính chất gần với Lifelog Search Challenge và Video Browser Showdown: người dùng nhận truy vấn tự nhiên, sau đó hệ thống phải tìm đúng video, đúng frame hoặc đúng chuỗi frame trong thời gian ngắn.

Ba dạng truy vấn của vòng sơ tuyển đặt ra các khó khăn khác nhau:

- **Textual Known Item Search (KIS)**: truy vấn mô tả một khoảnh khắc cụ thể; hệ thống phải trả về tối đa 100 dòng `<video_id>, <frame_idx>`.
- **Visual Question Answering (QA)**: hệ thống không chỉ tìm frame đúng mà còn phải sinh câu trả lời ngắn, đúng ngữ nghĩa, tối đa 100 ký tự.
- **Temporal Retrieval and Alignment of Key Events (TRAKE)**: truy vấn gồm nhiều sự kiện theo thời gian; hệ thống phải trả về đúng video và đúng thứ tự các frame đại diện cho từng event.

Nếu chỉ dùng một mô hình embedding đơn lẻ, hệ thống dễ gặp các lỗi sau:

- Truy vấn tiếng Việt/Anh mô tả cùng một cảnh bằng nhiều cách khác nhau, làm giảm recall.
- OCR, ASR hoặc object detection thiếu một modality quan trọng, làm mất manh mối.
- Các truy vấn có quan hệ thời gian như "sau đó", "trước khi", "lần lượt" không thể xử lý tốt bằng search top-k độc lập.
- Người thi cần duyệt nhanh ngữ cảnh trước/sau frame; UI chỉ hiển thị ảnh rời sẽ làm chậm thao tác.
- Dữ liệu lớn cần index trước, tái lập được pipeline và dễ thay model khi tải checkpoint mới.

Vì vậy, dự án cần một hệ thống **build thật**, có pipeline ingest/index offline, backend FastAPI có module rõ ràng, frontend TypeScript phục vụ tìm kiếm tương tác, Milvus cho vector search, PostgreSQL cho dữ liệu quan hệ, và các adapter model có thể chạy mock lúc chưa có checkpoint nhưng chuyển sang model thật mà không đổi API nghiệp vụ.

## 2. Mục tiêu

### 2.1 Mục tiêu nghiệp vụ

- Hỗ trợ đầy đủ quy trình thi: nhập gói query, search tương tác, xem ngữ cảnh, chọn đáp án, sinh CSV, đóng gói `submission.zip`.
- Tối ưu cho 3 loại truy vấn chính: KIS, QA và TRAKE; mở rộng sẵn cho image query/VKIS nếu đề thi có.
- Cho phép người dùng phối hợp giữa search tự động và human-in-the-loop để tăng điểm Top-k R-Score.
- Có khả năng chạy với mock data và mock model khi chưa có dataset/model thật.
- Khi model thật được tải về, hệ thống chỉ cần chỉnh `model_registry.yaml`/environment, không phải viết lại pipeline.

### 2.2 Mục tiêu kỹ thuật

| Chỉ tiêu | Mục tiêu thiết kế |
| --- | --- |
| Số câu trả lời mỗi query | Tối đa 100 dòng, đúng format Codabench |
| Latency truy vấn semantic interactive | p95 `< 2.5s` với index đã warm và top-k <= 1000 |
| Latency lọc metadata/OCR/ASR | p95 `< 700ms` qua Elasticsearch/PostgreSQL index |
| Latency ATS cho TRAKE | p95 `< 5s` với 3-6 sub-events và top-k ứng viên đã giới hạn |
| Thời gian mở frame context | p95 `< 500ms` nếu thumbnail/keyframe đã cache |
| Batch ingest video | Chạy bất đồng bộ, resumable, không chặn API |
| Khả năng thay model | Qua adapter + registry, không đổi API/domain logic |
| Khả năng tái lập kết quả | Lưu model version, embedding version, index build id |
| Submission | Sinh CSV UTF-8, không header, zip đúng cấu trúc `submission/` |

## 3. Cơ sở tham khảo từ paper

Thiết kế lấy **AIthena-Vision** làm backbone chính vì paper này bám sát AI Challenge HCMC: keyframe extraction, multimodal metadata, semantic vector retrieval, Adaptive Temporal Search và LLM multiperspective query expansion. Các điểm đưa vào blueprint:

- Tách pipeline thành **Data Preprocessing** và **Retrieval Processing**.
- Dùng shot detection/keyframe extraction, chọn frame đầu/giữa/cuối mỗi shot.
- Loại frame trùng bằng embedding similarity.
- Dùng nhiều embedding model, chuẩn hóa score và ensemble trọng số.
- Dùng OCR, ASR, object detection làm metadata bổ trợ.
- Dùng **Adaptive Temporal Search (ATS)** cho query nhiều event.
- Dùng LLM để sinh nhiều góc nhìn truy vấn thay vì search literal một câu.

Thiết kế cũng tham khảo **MEMORIA LSC2025** cho các quyết định:

- Lưu embedding ảnh trong **Milvus** để cải thiện text-to-image và image-to-image retrieval.
- Tạo annotation nhiều lớp: object, OCR, caption, scene, metadata.
- Tổ chức dữ liệu thành event/segment thay vì chỉ frame rời.
- Cho phép query parser trích entity/topic, nhưng vẫn có chế độ dùng nguyên query vì paper ghi nhận parser có thể không luôn cải thiện vector retrieval.
- UI cần hỗ trợ xem event, ngữ cảnh trước/sau frame, chọn nhiều frame và thao tác nộp nhanh.

## 4. Người dùng và nhu cầu

| Vai trò | Nhu cầu chính | Ràng buộc |
| --- | --- | --- |
| **Searcher / Người thi** | Nhập query, bật/tắt modality, xem top-k, xem context, chọn đáp án, sinh CSV. | Cần phản hồi nhanh trong thời gian thi; ưu tiên phím tắt và thao tác ít bước. |
| **Reviewer / Đồng đội kiểm chứng** | So sánh nhiều ứng viên, kiểm tra frame trước/sau, xác nhận answer QA. | Cần lịch sử thao tác và đánh dấu độ tin cậy. |
| **Data Engineer** | Chạy ingest, theo dõi job, re-index khi đổi model, kiểm tra lỗi OCR/ASR. | Dataset lớn, job dài, cần resume và log rõ. |
| **Model Engineer** | Tải model, đăng ký checkpoint, benchmark model mới, đổi trọng số ensemble. | Cần adapter ổn định, không phụ thuộc UI/backend nghiệp vụ. |
| **System Admin** | Quản lý tài khoản, cấu hình storage, healthcheck, backup DB/index. | Chạy local workstation/GPU server trước, nhưng có đường nâng cấp production. |

## 5. Phạm vi

### 5.1 Trong phạm vi

- Web frontend TypeScript cho search, review, event/temporal workspace và submission builder.
- Backend FastAPI theo modular monolith, có worker bất đồng bộ cho ingest/index/model inference batch.
- PostgreSQL lưu metadata quan hệ, query, runs, answers, audit, model/index version.
- Milvus lưu vector embeddings theo frame/event/model version.
- Elasticsearch hoặc PostgreSQL full-text/trigram cho OCR, ASR, caption, object labels.
- MinIO hoặc local object storage cho video, keyframe, thumbnail, audio chunk và artifacts.
- Redis cho cache, job state ngắn hạn, rate limit nội bộ và lock nhẹ.
- Pipeline preprocessing: video scan, shot detection, keyframe extraction, dedup, OCR, ASR, object detection, caption, scene, embedding, event segmentation.
- Retrieval engine: semantic vector search, metadata search, hybrid fusion, query expansion, ATS, QA answer generation/reranking.
- Model registry và adapter: hỗ trợ mock model, Hugging Face/local checkpoint, OpenAI-compatible/VLM endpoint nếu cần.
- Mock dataset generator để dev khi chưa có data thật.
- Bộ công cụ sinh CSV/ZIP theo đúng hướng dẫn Codabench.
- Observability: structured logs, metrics, job dashboard, trace id cho mỗi retrieval run.

### 5.2 Ngoài phạm vi ban đầu

- Huấn luyện model nền tảng từ đầu.
- Multi-region production hoặc autoscaling cloud phức tạp.
- Tích hợp trực tiếp hệ thống nộp bài Codabench qua API riêng nếu BTC không cung cấp.
- Fine-tune bắt buộc trên private dataset khi chưa có ground truth đủ lớn.
- Mobile app riêng; giao diện web responsive là đủ cho vòng thi.

## 6. Rủi ro và hướng giảm thiểu

| # | Rủi ro | Tác động | Hướng giảm thiểu |
| --- | --- | --- | --- |
| R1 | Dataset thật chưa có hoặc format thay đổi | Pipeline bị kẹt | Dùng `datasets/mock_aic` và schema ingest adapter theo `DatasetManifest`; parser chịu được thiếu field. |
| R2 | Model chưa tải hoặc GPU không đủ | Không chạy được inference thật | `ModelAdapter` có `mock`, `cpu-small`, `gpu-full`; model registry khai báo device, batch size, dtype. |
| R3 | Embedding model đơn lẻ bỏ sót query khó | Điểm Top-k thấp | Ensemble PE/OpenCLIP + BEiT-3/SigLIP/CLIP, score normalization, RRF và fusion có trọng số. |
| R4 | Query tiếng Việt có lỗi chính tả/OCR nhiễu | Metadata search sai | Language detect, translation, Vietnamese correction, fuzzy search, synonym/query expansion. |
| R5 | TRAKE cần frame theo thứ tự thời gian | Search độc lập không đủ | Áp dụng ATS: candidate per subquery, group by video, sequential/proximity constraints, partial match threshold. |
| R6 | QA trả lời dài hoặc không đúng format | CSV bị sai/điểm 0 | QA module giới hạn 100 ký tự, normalize answer, kiểm tra CSV trước export. |
| R7 | Re-index tốn thời gian | Chậm thử model mới | Lưu index version, job resumable, incremental index, ưu tiên re-embed subset benchmark trước. |
| R8 | Milvus/Elasticsearch/PostgreSQL lệch dữ liệu | Kết quả thiếu hoặc sai | Dùng `index_builds`, checksum artifact, reconciliation job và trạng thái `READY/STALE/FAILED`. |
| R9 | UI quá chậm khi duyệt nhiều frame | Người thi mất thời gian | Thumbnail cache, virtualized grid, keyboard shortcuts, context strip tải lazy. |
| R10 | Nộp sai format CSV/ZIP | Mất lượt nộp | Submission validator tự động kiểm tra tên file, số dòng, số cột, UTF-8, không header. |

## 7. Tiêu chí thành công

Dự án được xem là đạt yêu cầu blueprint khi:

1. `docker compose up` khởi động được PostgreSQL, Milvus, Redis, object storage, backend và frontend.
2. Chạy được pipeline ingest trên mock data: tạo keyframe, metadata giả lập/thật tùy adapter, embedding mock/thật, ghi DB và Milvus.
3. Search KIS trả về ranked list tối đa 100 dòng, có video_id/frame_idx và score breakdown.
4. Search QA trả về frame ứng viên kèm answer ngắn, có cơ chế sửa tay và export đúng format.
5. Search TRAKE nhận N sub-events, trả về chuỗi frame theo thứ tự thời gian và cho phép chỉnh từng event.
6. Frontend có workspace thực dụng: mode selector, query expansion toggle, modality filters, result grid, context viewer, selected answers tray, submission builder.
7. Thay model từ mock sang checkpoint thật chỉ cần cấu hình registry và chạy lại job index tương ứng.
8. Export `submission.zip` có thư mục `submission/`, mỗi query một CSV UTF-8 không header, tối đa 100 dòng.
9. Có benchmark script offline tính R@1/R@5/R@20/R@50/R@100 hoặc Mean of Top-k R-Score khi có ground truth.
10. Có tài liệu vận hành: ingest data, tải model, re-index, chạy server, export submission và rollback index.

## 8. Mốc triển khai đề xuất

| Giai đoạn | Kết quả bàn giao |
| --- | --- |
| Phase 1 — Skeleton thật | Monorepo, Docker Compose, FastAPI modules, React app, PostgreSQL schema, Milvus collection, mock data/model. |
| Phase 2 — Ingest pipeline | Video/keyframe manifest, object storage, metadata tables, worker job, index status dashboard. |
| Phase 3 — Retrieval core | Semantic search, metadata search, hybrid fusion, query run logging, score breakdown. |
| Phase 4 — Temporal & QA | ATS cho TRAKE, VLM/LLM QA adapter, answer normalizer, context viewer. |
| Phase 5 — Competition UX | Multi-query panel, selected tray, keyboard shortcuts, CSV/ZIP validator/export. |
| Phase 6 — Model hardening | Cắm checkpoint thật, benchmark, ensemble tuning, index versioning và runbook. |

## 9. Công nghệ đề xuất

| Lớp | Công nghệ |
| --- | --- |
| Frontend | TypeScript, React, Vite, TanStack Query, Zustand, TailwindCSS hoặc shadcn/ui |
| Backend | Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic |
| Worker | Celery/RQ hoặc Dramatiq; nâng cấp Ray khi cần batch GPU/distributed |
| Database | PostgreSQL 16, Milvus 2.x, Redis 7 |
| Text Index | Elasticsearch 8.x hoặc PostgreSQL FTS/trigram ở bản gọn |
| Object Storage | MinIO/S3-compatible storage |
| Model Serving | Local PyTorch/Hugging Face, ONNX/TensorRT tùy model, vLLM/Ollama/OpenAI-compatible cho LLM |
| Observability | OpenTelemetry-ready structured logs, Prometheus metrics, `/healthz`, `/readyz` |
| DevOps | Docker Compose trước, cấu trúc sẵn để tách Kubernetes sau |

## 10. Kết luận

Hệ thống được thiết kế như một **retrieval workstation hoàn chỉnh cho thi thật**, không phải MVP thử nghiệm. Trọng tâm là khả năng ingest dữ liệu lớn, index đa modality, tìm kiếm semantic/metadata/temporal, hỗ trợ người thi thao tác nhanh và xuất submission đúng chuẩn. Kiến trúc modular giúp bắt đầu bằng mock data/model nhưng vẫn giữ đường nâng cấp rõ ràng khi bạn tải model thật và dataset thật về.

## 11. Tài liệu tham khảo

- `docs/requirements.md`: yêu cầu đề tài, công nghệ bắt buộc và định hướng build.
- `docs/huong_dan_nop_bai_so_tuyen.md`: format query, CSV và quy tắc nộp bài.
- `docs/codabench_scoring.md`: công thức Mean of Top-k R-Scores.
- `docs/papers/AIThena.pdf`: paper chính về AIthena-Vision, multimodal retrieval, ATS và LLM multiperspective search.
- `docs/papers/MEMORIA.pdf`: paper tham khảo về MEMORIA LSC2025, Milvus vector retrieval, annotation pipeline và event retrieval.
- Codabench competition page: https://www.codabench.org/competitions/10187/
- MEMORIA LSC2025 DOI: https://doi.org/10.1145/3729459.3748693

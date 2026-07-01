# Web Application

TypeScript React frontend cho Multimodal Retrieval Assistant. Ứng dụng này là search workspace dành cho người thi: nhập query, chọn mode KIS/QA/TRAKE, xem ranked candidates, kiểm tra frame context, chọn đáp án và export `submission.zip`.

## 1. Vai trò trong hệ thống

Frontend tập trung vào tốc độ thao tác trong lúc thi. Đây không phải landing page; màn hình đầu tiên là workspace làm việc thật.

```text
User
  |
  v
Search Workspace
  |-- Query Panel: mode, query text, top-k, toggles
  |-- Result Grid: ranked frames/sequences, score breakdown
  |-- Context Viewer: frame trước/sau, metadata text
  `-- Selected Tray: rows sẽ export CSV/ZIP
```

Ứng dụng giao tiếp với backend qua REST API tại `VITE_API_BASE_URL`.

## 2. Cấu trúc thư mục

```text
apps/web/
├── src/
│   ├── App.tsx              # Main workspace
│   ├── main.tsx             # React entry
│   ├── styles.css           # Application styling
│   ├── types.ts             # Shared TypeScript contracts
│   ├── vite-env.d.ts        # Vite env typings
│   └── api/
│       └── client.ts        # Backend REST client
├── Dockerfile
├── index.html
├── package.json
├── tsconfig.json
└── vite.config.ts
```

## 3. Luồng sử dụng chính

### 3.1 Search KIS

1. Chọn dataset.
2. Chọn mode `KIS`.
3. Nhập `query_name`, ví dụ `query-1-kis`.
4. Nhập query text.
5. Bật/tắt `MV` nếu muốn query expansion.
6. Bật/tắt `Meta` nếu muốn dùng OCR/ASR/object metadata.
7. Bấm `Run`.
8. Xem result grid, mở context, chọn candidate đúng bằng `Select`.

Khi export, KIS row có format:

```csv
L00_V000,1234
```

### 3.2 Search QA

1. Chọn mode `QA`.
2. Nhập câu hỏi.
3. Bấm `Run`.
4. Result card hiển thị frame và answer backend gợi ý.
5. Chọn result tốt nhất.

Khi export, QA row có format:

```csv
L01_V028,3450,"Disney"
```

Answer được backend validate tối đa 100 ký tự.

### 3.3 Search TRAKE

1. Chọn mode `TRAKE`.
2. Nhập query có nhiều event theo thời gian.
3. Bấm `Run`.
4. Result card hiển thị `sequence_frames`.
5. Chọn sequence đúng nhất.

Khi export, TRAKE row có format:

```csv
L10_V001,1200,1850,2100
```

## 4. Chạy bằng Docker

Từ root repository:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Mở:

```text
http://localhost:5173
```

Backend API docs:

```text
http://localhost:8000/docs
```

## 5. Chạy local không dùng Docker

Yêu cầu:

- Node.js 22+
- npm 10+
- Backend đang chạy tại `http://localhost:8000`

Commands:

```powershell
cd apps\web
npm install
$env:VITE_API_BASE_URL="http://localhost:8000"
npm run dev
```

Build production:

```powershell
npm run build
```

Preview production build:

```powershell
npm run preview
```

## 6. Environment variables

| Biến | Mặc định | Mô tả |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `http://localhost:8000` | Backend API base URL. |

Trong Docker Compose, biến này được set trong service `web`.

## 7. API client

File `src/api/client.ts` gom toàn bộ backend calls:

| Function | Endpoint | Mục đích |
| --- | --- | --- |
| `listDatasets()` | `GET /api/datasets` | Lấy dataset list. |
| `runSearch()` | `POST /api/retrieval/search|qa|trake` | Chạy retrieval theo mode. |
| `getFrameContext()` | `GET /api/media/frames/{id}/context` | Lấy context trước/sau frame. |
| `createAndExportSubmission()` | `/api/submissions/*` | Tạo submission, ghi rows, export ZIP. |
| `mediaUrl()` | local helper | Convert relative thumbnail URL thành absolute URL. |

Nguyên tắc: component UI không gọi `fetch` trực tiếp; mọi API call đi qua `client.ts`.

## 8. State model

State chính nằm trong `App.tsx`:

| State | Ý nghĩa |
| --- | --- |
| `datasets`, `datasetId` | Dataset hiện tại. |
| `queryType` | `KIS`, `QA`, `TRAKE`. |
| `queryName`, `queryText` | Query file name và nội dung. |
| `topK` | Số kết quả lấy từ backend. |
| `useExpansion` | Toggle multiperspective query expansion. |
| `useMetadata` | Toggle metadata search. |
| `results` | Ranked candidates. |
| `context` | Frame context đang mở. |
| `selected` | Rows sẽ đưa vào submission. |
| `downloadUrl` | Link tải ZIP sau export. |

Khi app lớn hơn, nên tách state theo feature:

```text
features/
├── query-workspace/
├── result-grid/
├── context-viewer/
└── submission-builder/
```

Blueprint đã mô tả cấu trúc này trong `docs/blueprint/design.md`.

## 9. UI conventions

- Controls quan trọng dùng icon từ `lucide-react`.
- Query type dùng segmented control.
- Toggle boolean dùng button trạng thái active/inactive.
- Result grid phải có score breakdown để searcher hiểu lý do ranking.
- Context viewer luôn hiển thị frame trước/sau để kiểm chứng nhanh.
- Selected tray là nguồn dữ liệu duy nhất để export submission.
- Không đưa text hướng dẫn dài vào UI; tài liệu vận hành nằm trong `docs/backend/runbooks/operations.md`.

## 10. Validation và build checks

TypeScript build:

```powershell
cd apps\web
npm run build
```

Docker build:

```powershell
docker compose build web
```

Smoke check sau khi `docker compose up -d`:

```powershell
(Invoke-WebRequest -Uri http://localhost:5173 -UseBasicParsing).StatusCode
```

Kết quả kỳ vọng: `200`.

## 11. Khi backend đổi API

Checklist cập nhật frontend:

1. Sửa type trong `src/types.ts`.
2. Sửa request/response mapper trong `src/api/client.ts`.
3. Kiểm tra `App.tsx` có còn dùng field cũ không.
4. Chạy `npm run build`.
5. Chạy end-to-end bằng UI: search KIS, QA, TRAKE và export ZIP.

## 12. Troubleshooting

| Vấn đề | Cách xử lý |
| --- | --- |
| Trang trắng | Mở DevTools, kiểm tra console và network. |
| Không load dataset | Kiểm tra backend `http://localhost:8000/healthz`. |
| CORS error | Kiểm tra `CORS_ORIGINS` backend có `http://localhost:5173`. |
| Search báo lỗi | Xem response body trong Network tab hoặc Swagger docs. |
| Thumbnail không hiện | Gọi thử `/api/media/frames/{frame_id}/thumbnail`. |
| Export không có link | Kiểm tra selected tray có row và backend validate không lỗi. |

## 13. Roadmap frontend

- Tách `App.tsx` thành các feature modules.
- Thêm keyboard shortcuts cho chọn result nhanh.
- Thêm editor sửa QA answer và TRAKE sequence trước export.
- Thêm import query pack từ thư mục/file.
- Thêm diff view giữa nhiều retrieval profiles.
- Thêm offline cache cho recent runs nếu cần thi trên mạng yếu.


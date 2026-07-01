# Tài Liệu Đặc Tả Yêu Cầu Phát Triển Hệ Thống (Phase 2)

## Thay đổi về Storage Provider (Local Mock)
Trong quá trình chuẩn bị cho Phase 2, hệ thống đã được cấu hình thêm `LocalObjectStorageClient` để hỗ trợ phục vụ file ảnh trực tiếp từ ổ cứng (local filesystem) thay vì phải tải lên Cloudflare R2.

### 1. File cấu hình đã thêm
- **File tạo mới:** `apps/backend/app/adapters/object_storage/local.py` chứa class `LocalObjectStorageClient`.
- **Logic hoạt động:**
  - `put_object()`: Ghi file vật lý vào thư mục `DATA_ROOT`.
  - `public_url()`: Trả về link dạng `/api/media/static/TÊN_FILE`.
- **Cập nhật Dependency Injection:** Cập nhật file `apps/backend/app/core/deps.py` để khi biến môi trường `STORAGE_PROVIDER=local`, hệ thống sẽ trả về `LocalObjectStorageClient(data_root=settings.data_root)`.

### 2. Cấu hình FastAPI
- **File cập nhật:** `apps/backend/app/main.py`.
- **Sự thay đổi:** Nếu `STORAGE_PROVIDER=local`, ứng dụng sẽ mount thư mục tĩnh (StaticFiles) tại endpoint `/api/media/static`.

### 3. Hướng dẫn sử dụng ở Local
Để sử dụng tính năng này, hãy cấu hình file `.env` như sau:
```env
# Lưu file vào thư mục local thay vì R2
STORAGE_PROVIDER=local
# Đường dẫn tuyệt đối tới thư mục chứa dữ liệu demo của bạn
DATA_ROOT=/Users/tawannt/Study/Github/Multimodal-Retrieval/demo
# Tắt chế độ Mock giả lập DB để lưu dữ liệu thật
MOCK_MODE=false
```

Khi chạy app, thư mục `demo` của bạn sẽ đóng vai trò như một Object Storage nội bộ, ảnh trên giao diện frontend sẽ được tải trực tiếp từ máy của bạn!

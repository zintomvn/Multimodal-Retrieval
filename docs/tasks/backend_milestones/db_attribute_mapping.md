# Hướng Dẫn Ánh Xạ Thuộc Tính Dữ Liệu L30 Vào Hệ Thống Lưu Trữ (DB Attribute Mapping)

Tài liệu này đặc tả chi tiết **vị trí lưu trữ của từng thuộc tính (attribute)** trích xuất từ tập dữ liệu mẫu `L30` vào 4 thành phần lưu trữ của hệ thống: **PostgreSQL, Elasticsearch, Milvus, và MinIO**. 

Lập trình viên hoặc Lập trình Agent tiếp theo cần bám sát bảng ánh xạ này để viết các script import dữ liệu nghiệp vụ (`import_pg.py`, `import_es.py`, `import_milvus.py`) và thiết kế các câu lệnh truy vấn.

> Schema contract chot de implement:
> - `docs/database_schema_demo_v1.md`
> - `docs/database_erd.md`
>
> Neu co bat ky mau thuan nao giua tai lieu nay va code hien tai, uu tien schema contract o tren.

---

## 1. BẢNG ÁNH XẠ THUỘC TÍNH TỔNG HỢP (ATTRIBUTE MAPPING MATRIX)

Ký hiệu:
*   `✅`: **Có lưu trữ** (Lưu trữ giá trị thực tế của thuộc tính).
*   `❌`: **Không lưu trữ** (Tránh dư thừa dữ liệu).
*   `🔑`: **Chỉ lưu mã liên kết** (Khóa chính hoặc khóa ngoại để thực hiện JOIN/Map giữa các DB).

| File Gốc | Thuộc Tính Gốc | PostgreSQL | Elasticsearch | Milvus | MinIO | Ghi Chú Triển Khai |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **per_video_summary.csv** | `video_id` | `✅` | `🔑` | `🔑` | `❌` | Khóa chính của bảng `videos`, dùng làm khóa ngoại ở các DB khác để filter. |
| | `num_keyframes` | `✅` | `❌` | `❌` | `❌` | Chỉ lưu ở Postgres phục vụ hiển thị dashboard/thống kê. |
| | `embedding_shape` | `✅` | `❌` | `❌` | `❌` | Chỉ lưu ở Postgres dạng chuỗi (ví dụ: `"[165, 512]"`). |
| | `feature_path` | `❌` | `❌` | `❌` | `❌` | **Bỏ qua** (đây là đường dẫn local trên Kaggle cũ). |
| | `map_path` | `❌` | `❌` | `❌` | `❌` | **Bỏ qua** (đây là đường dẫn local trên Kaggle cũ). |
| | `seconds` | `✅` | `❌` | `❌` | `❌` | Tổng thời lượng video (giây). |
| **shot_segments.csv** | `shot_id` | `✅` | `🔑` | `❌` | `❌` | Khóa chính của bảng `shots` (Định dạng: `{video_id}_S{shot_id:04d}`). |
| | `shot_start_frame` / `end_frame` | `✅` | `❌` | `❌` | `❌` | Phạm vi khung hình của shot (chỉ lưu Postgres). |
| | `shot_start_sec` / `end_sec` | `✅` | `❌` | `❌` | `❌` | Phạm vi thời gian của shot (chỉ lưu Postgres). |
| | `frame_type` | `✅` | `❌` | `❌` | `❌` | Loại keyframe (`first`/`middle`/`last`). |
| | `frame_idx` | `✅` | `🔑` | `🔑` | `❌` | Chỉ số khung hình thực tế trong video. |
| | `frame_sec` | `✅` | `✅` | `❌` | `❌` | Lưu ở Postgres để tính toán, lưu ở ES để lọc nhanh theo thời gian. |
| | `image_path` | `✅` | `❌` | `❌` | `🔑` | Postgres lưu URL của ảnh trên MinIO; MinIO lưu file ảnh thực tế. |
| | `fps` | `✅` | `❌` | `❌` | `❌` | Lưu ở Postgres để phục vụ đổi từ frame_idx sang giây. |
| **annotations.jsonl** | `caption` | `✅` | `✅` | `❌` | `❌` | Postgres lưu dạng thô; ES đánh chỉ mục văn bản (Vietnamese Analyzer). |
| | `texts` (OCR) | `✅` | `✅` | `❌` | `❌` | Postgres lưu dạng JSON array thô; ES đánh chỉ mục văn bản tiếng Việt. |
| | `objects` | `✅` | `✅` | `❌` | `❌` | Postgres lưu dạng JSON array; ES lưu dạng mảng `keyword` để lọc. |
| | `object_counts` | `✅` | `✅` | `❌` | `❌` | Postgres lưu dạng JSON; ES lưu dạng `nested object` để truy vấn số lượng. |
| | `detections` | `✅` | `❌` | `❌` | `❌` | Chỉ lưu ở Postgres làm siêu dữ liệu chi tiết khi người dùng click xem. |
| **Event Embedding** | `event_id` | `✅` | `🔑` | `🔑` | `❌` | Định dạng: `{video_id}_E{event_index:06d}`. Khóa chính bảng `events`. |
| | `event_embeddings.npy` | `❌` | `❌` | `✅` | `❌` | Lưu vector 512 chiều vào Milvus collection `event_embeddings`. |
| | `vit-.../[video_id].npy` | `❌` | `❌` | `✅` | `❌` | Lưu vector 512 chiều vào Milvus collection `keyframe_embeddings`. |

---

## 2. CHI TIẾT TRIỂN KHAI CHO TỪNG CƠ SỞ DỮ LIỆU

### 2.1. PostgreSQL (SQLAlchemy Models)
PostgreSQL đóng vai trò là **Source of Truth**. Tất cả dữ liệu nghiệp vụ và quan hệ bắt buộc phải có mặt tại đây.

Bang ingestion core can co:
- `datasets`
- `videos`
- `shots`
- `keyframes`
- `frame_annotations`
- `events`
- `event_keyframes` (bang join de truy van event -> danh sach keyframe co thu tu)

```python
# Ví dụ định nghĩa Model cho Agent triển khai
class Video(Base):
    __tablename__ = 'videos'
    id = Column(String(50), primary_key=True) # e.g., "L30_V001"
    name = Column(String(255), nullable=False) # e.g., "L30_V001.mp4"
    duration_seconds = Column(Float, nullable=False)
    fps = Column(Float, nullable=False)

class Shot(Base):
    __tablename__ = 'shots'
    id = Column(String(100), primary_key=True) # e.g., "L30_V001_S0000"
    video_id = Column(String(50), ForeignKey('videos.id', ondelete='CASCADE'))
    shot_index = Column(Integer, nullable=False)
    start_frame = Column(Integer, nullable=False)
    end_frame = Column(Integer, nullable=False)
    start_seconds = Column(Float, nullable=False)
    end_seconds = Column(Float, nullable=False)

class Keyframe(Base):
    __tablename__ = 'keyframes'
    id = Column(String(100), primary_key=True) # e.g., "L30_V001_F000037"
    video_id = Column(String(50), ForeignKey('videos.id', ondelete='CASCADE'))
    shot_id = Column(String(100), ForeignKey('shots.id', ondelete='CASCADE'))
    frame_idx = Column(Integer, nullable=False)
    frame_seconds = Column(Float, nullable=False)
    frame_type = Column(String(10)) # "first", "middle", "last"
    image_url = Column(String(512)) # URL dẫn tới MinIO
    
    # Dữ liệu annotation lưu thô phục vụ backup/export
    caption = Column(Text)
    ocr_texts = Column(JSON) # Array: ["Tuổi Trẻ TV", "tv.tuoitre.vn"]
    detected_objects = Column(JSON) # Array: ["person", "car"]
    object_counts = Column(JSON) # Dict: {"person": 2, "car": 1}
    detections_detail = Column(JSON) # Chi tiết bbox và confidence

class Event(Base):
    __tablename__ = 'events'
    id = Column(String(100), primary_key=True) # e.g., "L30_V001_E000000"
    video_id = Column(String(50), ForeignKey('videos.id', ondelete='CASCADE'))
    start_seconds = Column(Float, nullable=False)
    end_seconds = Column(Float, nullable=False)
    start_frame = Column(Integer, nullable=False)
    end_frame = Column(Integer, nullable=False)
    representative_frame_id = Column(String(100), ForeignKey('keyframes.id'))
    embedding_index = Column(Integer, nullable=False) # Chỉ số dòng trong file .npy
```

---

### 2.2. Elasticsearch (Search Index Mapping)
Elasticsearch chỉ lưu trữ dữ liệu văn bản phục vụ tìm kiếm từ khóa, tuyệt đối **không lưu** các trường dữ liệu nhị phân hoặc dữ liệu cấu trúc không dùng để tìm kiếm (như chi tiết khung hình bắt đầu/kết thúc của shot, đường dẫn ảnh gốc).

```json
{
  "mappings": {
    "properties": {
      "keyframe_id": { "type": "keyword" },
      "video_id": { "type": "keyword" },
      "shot_id": { "type": "keyword" },
      "frame_seconds": { "type": "float" },
      "caption": { 
        "type": "text", 
        "analyzer": "vietnamese" 
      },
      "ocr_texts": { 
        "type": "text", 
        "analyzer": "vietnamese",
        "fields": {
          "raw": { "type": "keyword" }
        }
      },
      "detected_objects": { "type": "keyword" }
    }
  }
}
```

---

### 2.3. Milvus (Vector Schema)
Milvus **chỉ lưu trữ** ID thực thể khóa chính (`keyframe_id` hoặc `event_id`) và vector đặc trưng. Tránh nạp bất kỳ thông tin text hay metadata nào khác vào Milvus để tối ưu hiệu năng bộ nhớ RAM của Vector DB.

*   **Collection `keyframe_embeddings`**:
    *   `id`: Kiểu `INT64` (Khóa chính tự tăng của Milvus).
    *   `keyframe_id`: Kiểu `VARCHAR(100)` (Dùng để tham chiếu ngược về bảng `keyframes` trong Postgres).
    *   `vector`: Kiểu `FLOAT_VECTOR` (độ dài 512 chiều).
*   **Collection `event_embeddings`**:
    *   `id`: Kiểu `INT64`.
    *   `event_id`: Kiểu `VARCHAR(100)` (Dùng để tham chiếu ngược về bảng `events` trong Postgres).
    *   `vector`: Kiểu `FLOAT_VECTOR` (độ dài 512 chiều).

---

### 2.4. MinIO (Object Storage)
MinIO **chỉ lưu trữ** các file ảnh thực tế dưới dạng nhị phân (`.jpg`). Các thông tin về tên file, dung lượng, đường dẫn ảo đều được quản lý bởi PostgreSQL.

*   **Cấu trúc thư mục lưu trữ (S3 Buckets)**:
    ```text
    s3://keyframes/
    ├── L30_V001/
    │   ├── shot_0000_first_f000000.jpg
    │   ├── shot_0000_middle_f000037.jpg
    │   └── shot_0000_last_f000074.jpg
    └── L30_V002/
        └── ...
    ```

---

## 3. CHECKLIST TRIỂN KHAI CHO AGENT TIẾP THEO
Khi bắt đầu viết code module nạp dữ liệu, hãy bám sát danh sách kiểm tra sau:

- [ ] **Bước 1**: Đọc file `shot_segments.csv`, tạo định danh `keyframe_id` thống nhất dạng `{video_id}_F{frame_idx:06d}`.
- [ ] **Bước 2**: Đẩy ảnh lên MinIO trước để lấy được danh sách `image_url` dạng `http://[minio_host]:[port]/keyframes/[video_id]/[file_name]`.
- [ ] **Bước 3**: Chạy script nạp PostgreSQL để lưu toàn bộ thực thể gốc kèm `image_url` vừa tạo.
- [ ] **Bước 4**: Chạy script nạp Milvus. Đảm bảo rằng chỉ số dòng `i` của file `.npy` tương ứng với khóa ngoại `keyframe_id` (lấy từ cột `frame_idx` khớp với dòng có `n = i + 1` trong file mapping CSV).
- [ ] **Bước 5**: Chạy script nạp Elasticsearch. Thực hiện nối (join) thông tin text từ `annotations.jsonl` với `keyframe_id` tương ứng trước khi insert tài liệu phẳng vào ES.

## 4. Data quality gates (bat buoc)

- [ ] So dong `videos` = so dong trong `per_video_summary.csv`.
- [ ] So dong `keyframes` = so keyframe hop le tu `shot_segments.csv` sau khi reconcile media.
- [ ] So dong `events` = so dong trong `event_mapping.csv`.
- [ ] Khong co orphan:
  - `keyframes.shot_id` khong duoc mo coi trong `shots`.
  - `frame_annotations.keyframe_id` khong duoc mo coi trong `keyframes`.
  - `events.representative_keyframe_id` neu co phai ton tai trong `keyframes`.

# UniHub Workshop — Project Proposal

> Phần 1 / Blueprint • Tài liệu đề xuất • Phiên bản 1.0

## 1. Vấn đề

Trường Đại học A tổ chức **Tuần lễ kỹ năng và nghề nghiệp** mỗi năm: 5 ngày, 8–12 workshop song song mỗi ngày tại nhiều phòng khác nhau. Quy trình hiện tại dựa hoàn toàn vào **Google Form + email thủ công**, dẫn đến hàng loạt vấn đề:

- **Không kiểm soát được tranh chấp chỗ ngồi**: Google Form không có khoá mức bản ghi nên cùng một slot cuối cùng có thể được "cấp" cho nhiều sinh viên. Ban tổ chức phải xử lý khiếu nại sau sự kiện.
- **Không chịu được tải đột biến**: Khi mở đăng ký, hàng nghìn sinh viên truy cập cùng lúc làm Form chậm hoặc lỗi 500.
- **Không có check-in tự động**: Nhân sự đối chiếu danh sách bằng tay, mất nhiều thời gian, dễ sai và không thống kê được.
- **Không có thông báo có cấu trúc**: Email gửi thủ công, không có lịch sử, không thể mở rộng sang kênh khác (Zalo, Telegram).
- **Không tích hợp dữ liệu sinh viên**: Mỗi lần phải xuất danh sách sinh viên thủ công để đối chiếu MSSV.
- **Không có thanh toán**: Workshop có phí phải xử lý chuyển khoản ngoài hệ thống, dễ thất lạc.
- **Không có dữ liệu phân tích**: Ban tổ chức không biết workshop nào hấp dẫn, tỉ lệ no-show bao nhiêu.

Hậu quả định lượng (ước lượng nội bộ từ mùa gần nhất, dùng làm giả định thiết kế):

- ~8% đăng ký bị **double-booked** (2 SV cùng 1 ghế) → khiếu nại.
- Form treo trung bình **6–8 phút** ngay sau khi mở.
- ~15% sinh viên không nhận được email xác nhận đúng giờ.
- Check-in trung bình **40 giây/SV**, gây ùn tắc cửa phòng.

## 2. Mục tiêu

### 2.1 Mục tiêu nghiệp vụ

- Số hoá toàn bộ vòng đời sự kiện: từ tạo workshop → đăng ký → thanh toán → check-in → thống kê.
- Cung cấp ứng dụng riêng cho 3 nhóm người dùng với UX phù hợp từng vai trò.
- Giảm thời gian check-in trung bình xuống **dưới 5 giây/SV**.
- Loại bỏ hoàn toàn lỗi double-booking.

### 2.2 Mục tiêu kỹ thuật (định lượng)

| Chỉ tiêu                                              | Mục tiêu                                                                                    |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Sinh viên đồng thời truy cập trong 10 phút mở đăng ký | **12.000**                                                                                  |
| Phân bổ tải                                           | **60%** trong 3 phút đầu                                                                    |
| Latency p95 API danh sách workshop                    | **< 300 ms**                                                                                |
| Latency p95 API đăng ký (free)                        | **< 800 ms**                                                                                |
| Tỉ lệ trùng ghế                                       | **0%** (đảm bảo bằng khoá DB + Redis)                                                       |
| Khả dụng tính năng xem lịch khi cổng thanh toán down  | **100%** (Graceful Degradation)                                                             |
| Trừ tiền 2 lần (duplicate charge)                     | **0** ca                                                                                    |
| Mất dữ liệu check-in offline                          | **0** bản ghi trong điều kiện app không bị xoá local storage / thiết bị không factory reset |
| Job CSV nightly không gây downtime                    | **Bắt buộc**                                                                                |

## 3. Người dùng và nhu cầu

| Vai trò                               | Nhu cầu chính                                                                                         | Ràng buộc                                                      |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| **Sinh viên (Student)**               | Xem nhanh lịch workshop, biết còn chỗ trống không, đăng ký được ngay, có QR check-in, được thông báo. | Đa số dùng mobile browser; mạng wifi trường có thể chậm.       |
| **Ban tổ chức (Organizer)**           | Tạo/sửa/huỷ workshop, đổi phòng, đổi giờ, upload PDF để AI tóm tắt, xem thống kê đăng ký/check-in.    | Nhiều người cùng thao tác; cần audit log.                      |
| **Nhân sự check-in (Check-in Staff)** | Quét QR thật nhanh, kể cả khi không có mạng, không thao tác phức tạp.                                 | Mạng yếu/mất ở một số phòng; thiết bị: smartphone Android/iOS. |
| **Quản trị hệ thống (System Admin)**  | Quản lý tài khoản nội bộ, gán role, xem log import CSV, xem health system.                            | Ít người, nhưng quyền cao nhất.                                |

## 4. Phạm vi

### 4.1 Trong phạm vi (In scope)

- Web app cho sinh viên (xem + đăng ký workshop, xem QR).
- Web admin cho ban tổ chức (CRUD workshop, upload PDF, xem thống kê).
- Mobile app (React Native) cho nhân sự check-in, hỗ trợ offline.
- Backend API (NestJS / TypeScript), PostgreSQL, Redis, RabbitMQ.
- **Mock payment gateway** (service riêng, có thể giả lập lỗi/timeout) để demo Circuit Breaker và Idempotency Key.
- **Gemini AI provider** qua `GEMINI_API_KEY` để demo pipeline AI thật; `services/mock-ai` chỉ còn là legacy/dev fallback, không nằm trong luồng chính.
- Notification qua **email (SMTP)** + **in-app**, kiến trúc cho phép cắm thêm Telegram/Zalo bằng adapter.
- CSV importer (cron job) đọc từ thư mục `data/csv-drop/`.
- Rate Limiting (Token Bucket trên Redis) + Circuit Breaker + Idempotency Key — **cài đặt thật, không stub**.
- Seed data và `docker compose up` chạy được toàn bộ stack.

### 4.2 Ngoài phạm vi (Out of scope)

- Tích hợp cổng thanh toán thật (VNPay/Momo/Stripe) — chỉ mock, nhưng giữ contract giống thật.
- Hạ tầng production thật (auto-scaling, multi-region, CDN). Hệ thống chỉ chạy bằng Docker Compose 1 node.
- Push notification mobile (FCM/APNs) — dùng polling/SSE trong app thay thế.
- Tính năng SSO với hệ thống xác thực sinh viên trường (chưa có API) — dùng mật khẩu local + đối chiếu MSSV qua dữ liệu CSV.
- App di động cho sinh viên (chỉ làm web responsive).
- Đa ngôn ngữ (chỉ tiếng Việt).

## 5. Rủi ro và ràng buộc đã biết

| #   | Rủi ro / Ràng buộc                                                     | Tác động                                    | Hướng giảm thiểu                                                                                 |
| --- | ---------------------------------------------------------------------- | ------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| R1  | **Tranh chấp chỗ ngồi** ở các workshop hot (60 chỗ, 1000+ SV cùng bấm) | Mất uy tín, khiếu nại                       | Atomic `DECR` Redis + DB unique constraint `(workshop_id, student_id)` + check trong transaction |
| R2  | **Tải đột biến** 12K SV / 10 phút (60% trong 3 phút)                   | API sập, timeout chuỗi                      | Token Bucket per-IP + per-user, queue đăng ký theo workshop, cache Redis cho catalog                              |
| R3  | **Cổng thanh toán không ổn định**                                      | Người dùng không thanh toán được, treo tiền | Circuit Breaker (Closed/Open/Half-Open) + Graceful Degradation (catalog vẫn xem được)            |
| R4  | **Trừ tiền 2 lần** do client retry                                     | Khiếu nại tài chính, mất uy tín             | Idempotency Key bắt buộc cho POST `/payments`, lưu Redis TTL 24h + durable snapshot trong PostgreSQL |
| R5  | **Mất mạng tại cửa phòng** lúc check-in                                | Không cho SV vào kịp giờ, mất dữ liệu       | Mobile lưu SQLite local, sync batch idempotent khi có mạng                                       |
| R6  | **Tích hợp 1 chiều** với hệ thống sinh viên cũ (chỉ có CSV)            | Dữ liệu lệch, không xác thực được MSSV      | Cron đọc CSV → staging table → upsert idempotent → quarantine file lỗi                           |
| R7  | **PDF upload không kiểm soát** (mã độc, file lớn)                      | Bảo mật, tốn tài nguyên                     | Giới hạn size, MIME-type, scan, queue worker xử lý ngoài request                                 |
| R8  | **AI provider down hoặc tốn chi phí**                                  | Summary thiếu                               | Async pipeline, retry, fallback "đang xử lý", cache theo file hash                               |
| R9  | **Lạm dụng đăng ký** (1 SV bấm 100 workshop)                           | Lãng phí ghế                                | Giới hạn N workshop/ngày, ràng buộc DB                                                           |
| R10 | **Rò rỉ JWT / chiếm phiên**                                            | Truy cập trái phép trang admin              | JWT ngắn hạn + refresh token + role check ở mọi endpoint                                         |

## 6. Tiêu chí thành công (Success Criteria)

Đồ án được xem là thành công khi:

1. Cả 3 cơ chế bảo vệ (Rate Limit, Circuit Breaker, Idempotency Key) **chứng minh được hành vi thật** trong demo (có log/metrics).
2. Load test (k6/artillery) với **3000 vRPS trong 60 giây** không làm DB lỗi; hệ thống trả `429` cho per-user/IP limit và `202 QUEUED` cho global registration overload.
3. Demo race condition: 100 client cùng đăng ký 1 ghế cuối → đúng **1 client thắng**.
4. Demo offline check-in: bật airplane mode, check-in 5 SV, bật mạng → **5 bản ghi đồng bộ, 0 mất**.
5. Demo trừ tiền 2 lần: 5 lần POST với cùng `Idempotency-Key` → đúng **1 giao dịch**.
6. Pipeline AI: upload PDF 5MB → có summary trong **< 30s**.
7. CSV importer: drop file 10K dòng (có 5% lỗi format) → import 9500 dòng, 500 dòng bị quarantine với log rõ ràng.
8. `docker compose up` → toàn bộ hệ thống lên trong **< 90s** với seed data sẵn.

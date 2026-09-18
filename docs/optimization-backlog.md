# Tình trạng 22 mục tối ưu

Cập nhật 17/09/2026. Nhánh `feat/ui-search-optimization`; base main `5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f`. Chưa push/merge. Backend local: 8010.

Đã triển khai thay đổi cho cả 22 nhóm và sửa B01/B02. **18 mục đủ bằng chứng trong phạm vi local; 4 mục còn mở nghiệm thu sâu: A09, A10, A16, A22.** Có code hoặc test fixture xanh không đồng nghĩa chất lượng model/tải production đã đạt.

## Từng mục và bằng chứng

| Mã | Trạng thái | Đã làm / giới hạn |
|---|---|---|
| A01 | Đã kiểm chứng local | Error/empty/degraded riêng; bỏ mock fallback; evidence lỗi trả 503 và Retry. Browser outage/search/export qua. |
| A02 | Đã kiểm chứng local | Abort và request ownership; Enter trùng bị chặn; task switch không nhận response cũ. Không coi abort client là hủy model server. |
| A03 | Đã kiểm chứng local | Bỏ quét toàn keyframes; deadline provider, tối đa 8 công việc, không có queue vô hạn. 8 no-match concurrent qua. SQL/commit/HTTP overhead không nằm trong deadline chờ SDK. |
| A04 | Đã kiểm chứng local | Hai sidebar không che Search ở 390/900/1024/1152/1180/1440 CSS px; không tràn ngang. 1152 là vùng làm việc tương đương 1440 ở 125%, chưa đo native browser zoom. |
| A05 | Đã kiểm chứng local | UUID query mới; lưu draft theo task, options/source/filter/history/selection/TRAKE picks/scroll. Báo lỗi quota/corrupt save. Reload và task-switch qua. |
| A06 | Đã kiểm chứng local | CSV một query, ZIP nhiều query; validation trước download; artifact/MIME/tên file đúng; B02 đã sửa. Tải CSV và ZIP từ backend thật qua. |
| A07 | Đã kiểm chứng local | Chỉ hiển thị Search; Auto/Chat chưa nối thật được ẩn; attachment disabled có lý do. |
| A08 | Đã kiểm chứng local | Probe metadata/text/model/vector; cache 15 giây; UI báo rõ ready/degraded/unavailable/reachable/unverified. Connectivity không được coi là inference thành công. |
| A09 | Còn nghiệm thu | OCR/ASR/scene override thật, weights phản ánh lựa chọn; tests và text live qua. Còn quality gate: 6 câu KIS, exact-frame recall@20 = 0 khi semantic unavailable; cần model đúng phiên bản. |
| A10 | Còn nghiệm thu | Retrieval trước, QA on-demand, giới hạn legacy mặc định 3 ứng viên; citation/index chung preview. Fixture/browser qua; chưa kiểm độ đúng đáp án với QA model thật. |
| A11 | Đã kiểm chứng local | Web bỏ /plan riêng trước /search; normalize một lần. Browser kiểm không gọi plan trùng. |
| A12 | Đã kiểm chứng local | ES msearch, Milvus batch nhiều vector; deadline/admission/partial failure. Không đưa DB session qua worker thread. Tests batch/filter/timeout qua. |
| A13 | Đã kiểm chứng local | Exact video code resolve trước frame query; giữ ordering/pagination; cache count 64 khóa/10 giây. EXPLAIN dùng index sẵn. Gallery warm p95 30.26ms, lookup 31.57ms (30 mẫu mỗi loại). |
| A14 | Đã kiểm chứng local | Preview mở ngay, URL đến sau; context/evidence cache 64 mục/30 giây, gộp request, evict lỗi; evidence on-demand và Retry. Không cache signed URL. |
| A15 | Đã kiểm chứng local | Source/video/time filter, lọc kết quả hiện tại, snippet và điểm chi tiết; kết quả cũ có nhãn khi đang tìm. Backend là nơi duy nhất diversification. |
| A16 | Còn nghiệm thu | Sửa 2–8 events; sửa E2 giữ E1/E3, rerun giữ picks; validator bảo vệ cùng video/thứ tự tăng. Browser/fixture qua; còn benchmark KIS temporal và TRAKE với model thật. |
| A17 | Đã kiểm chứng local | Preview/Settings quản lý focus, Tab, Escape, restore; card dùng bàn phím; toggle aria-pressed/expanded. Màu chữ phụ đạt 4.5:1 trên active background light/dark; chưa phải chứng nhận WCAG toàn trang. |
| A18 | Đã kiểm chứng local | ResizeObserver đo vùng kết quả, clamp cột; control báo cột thực; bỏ CSS cưỡng ép số cột. |
| A19 | Đã kiểm chứng local | Memoized ResultGrid/stable callbacks; giảm đọc/serialize workspace. 0 grid render khi typing và timeupdate mô phỏng. Profiler với 100 kết quả: 0 grid commits khi typing; max Event Timing 64ms. Đây là lab proxy, không phải field INP. |
| A20 | Đã kiểm chứng local | Request ID/Server-Timing, spans provider/QA; benchmark metadata/golden; regression race/outage/task-switch/export/responsive. Báo riêng fixture, live, degraded; không tuyên bố recall tăng. |
| A21 | Đã kiểm chứng local | Dataset/video filter trước top-k; manifest source/model/hash registry; alias guarded promote/rollback; pin evidence physical index. Test ES thật trên hai index tạm qua; dữ liệu/alias hiện tại không đổi. |
| A22 | Còn nghiệm thu | Worker opt-in, DB lease/heartbeat/backoff/3 attempts; checkpoint completed video; retry API. Hai process phục hồi sau exit và partial-video resume qua. Chưa bật worker hiện tại hoặc đo search đồng thời ingest/provider thật. |

## Kết quả kiểm thử

- Backend: 144 passed, 1 cảnh báo deprecation TestClient. Web: 7 tests passed, production build qua.
- Browser: race/outage/retry, export validation, CSV thật, ZIP thật, workspace, QA theo yêu cầu, TRAKE sửa event, responsive/keyboard qua. QA/TRAKE dùng response kiểm soát; gallery/context/export/alias có kiểm tra dịch vụ thật.
- Gallery: warm p95 525.68 -> 30.26ms; median 323.68 -> 18.745ms, 30 mẫu mỗi lần. Cold hiện 352.63ms. Count có thể cũ tối đa 10 giây; frame page vẫn đọc mới. Đo khác thời điểm, không phải tải production.
- Golden KIS: 6 câu không rỗng; 0 lỗi HTTP; p50 4743ms, p95 7792ms; exact-frame recall@20 = 0. Dòng CSV nguồn trống từng gây 422 đã bị loại khỏi tập đo hợp lệ. Chất lượng chưa đạt.
- Live readiness: metadata/text ready; 783835 annotations (Caption 310212, OCR 291909, ASR 181714). OpenCLIP probe lỗi; SigLIP remote chưa chạy inference trong health; schema hai collection vector đọc được.
- Alias promote/rollback/pinned evidence qua trên 2 index ES tạm, 6 document; đã dọn fixtures. Không đổi read alias của dữ liệu đang dùng.

## Lỗi main đã sửa

**B01:** test TRAKE đòi mọi event gọi OCR/ASR/Caption dù OCR heuristic đang tắt. Fake giờ ghi query/source, assertion kiểm đủ ASR/Caption và bốn event/frame đúng thứ tự. Không ép OCR bật để làm test xanh.

**B02:** main đổi ZIP sang CSV nhưng test/schema/README còn hợp đồng ZIP và lưu CSV trong zip_uri. Đã tách format, serializer chung, CSV một query/ZIP mỗi query một file, artifact đúng loại, hỗ trợ legacy path. Test chỉ ghi tmp_path.

**Phát hiện thêm:** evidence nuốt lỗi thành rỗng; pipeline partial failure báo COMPLETED; video có metadata nhưng thiếu embedding bị skip khi retry. Đã sửa và thêm regression.

## Nghiệm thu còn lại

1. A09: chạy đúng OpenCLIP checkpoint/dimension, inference preflight SigLIP, đo lại KIS có nhãn. Cache máy có CLIP B-32, không thể dùng thay cho index ViT-H-14.
2. A10: kiểm QA model thật trên frame có evidence, độ đúng citation/answer và latency.
3. A16: benchmark KIS temporal/TRAKE trên sequence có nhãn sau khi A09 ổn định.
4. A22: bật worker trong sandbox đủ model/storage, dừng/khởi động lúc ingest và đo search concurrent. Chưa chạy lại pipeline ghi provider trên dữ liệu đang dùng.

Hướng dẫn: `docs/optimization-runbook.md`. Số liệu local: `data/ui-optimization/`; ảnh/browser artifacts: `output/playwright/`. Không đưa data/secret vào Git.

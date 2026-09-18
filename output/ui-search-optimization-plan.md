# Kế hoạch tối ưu web và search

<!-- backlog-start -->
## Tình trạng 22 mục tối ưu

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

<!-- backlog-end -->

## 01. Mục tiêu và phạm vi

Làm cho luồng nhập truy vấn -> xem kết quả -> xem bằng chứng -> chọn đáp án -> export đáng tin và ít chờ hơn. Kế hoạch bao phủ đủ 22 mục audit, giữ nguyên mã A01–A22 để đối chiếu sơ đồ.

Trạng thái: đang triển khai từ 17/09/2026; xem bảng cập nhật trước lộ trình gốc. Cơ sở là audit code và kiểm thử local ngày 16/09; cần xác minh lại hiện trạng ở G0.

Audit phân loại 11 mục P1 và 11 mục P2. P1 thể hiện mức ảnh hưởng, không phải mọi P1 đều phải triển khai trước mọi P2: A20 cần làm nền trước, A13 có thể cải thiện nhanh, A10 cần nhiều điều kiện hơn.

- Bao gồm web React, API FastAPI, retrieval, media, session/export, readiness và luồng dữ liệu liên quan.
- Agent được xem là một dịch vụ lập kế hoạch/suy luận; chỉ điều chỉnh contract, số lần gọi và evidence, không thiết kế lại kiến trúc bên trong.
- Không mặc định thay framework, thay model, reimport toàn bộ, chuyển DB hay thêm hệ thống queue ngay từ đầu.
- Giữ các tối ưu đang có: ảnh lazy/async, batch embedding, cache plan thành công.

## 02. Cách chia đợt bàn giao

Đợt đầu đề xuất: G0 + G1. Sau đó bàn giao G2 để ổn định workspace, rồi G3 để giảm thời gian chờ. G4 là mở rộng chất lượng chuyên sâu; G5 phụ thuộc nhu cầu vận hành.

Ước lượng là ngày công kỹ thuật, đã dành thời gian kiểm thử ở mỗi đợt; chưa phải lịch cam kết. Tổng G0–G3: 16–27 ngày công; G4: 6–10; G5: 3–6 nếu kích hoạt. Toàn bộ: 25–43 ngày công. Cần điều chỉnh sau G0 theo readiness model và schema thật.

Không quy đổi tự động thành ngày lịch hay giả định nhiều người làm song song. Các công việc chạm chung App.tsx, request schema hoặc retrieval service phải tích hợp theo thứ tự rõ ràng.

- Mỗi đợt có PR/change set nhỏ, bằng chứng trước/sau, ca hồi quy liên quan và hướng rollback.
- Hoàn thành nghĩa là tiêu chí nghiệm thu đạt, không chỉ build/lint thành công.
- Không đánh dấu toàn bộ 22 mục hoàn tất nếu G4/G5 còn hoãn; ghi rõ hoàn tất, một phần, chờ điều kiện hoặc chưa làm.

## Lộ trình thực hiện

| Giai đoạn | Hạng mục | Ước lượng |
|---|---|---|
| G0 — Đo hiện trạng và chốt hợp đồng dữ liệu | A20 | 1–2 ngày công |
| G1 — Giữ kết quả đúng và thao tác không bị chặn | A01, A02, A03, A04, A06, A08, A17 | 5–8 ngày công |
| G2 — Giữ phiên làm việc và làm rõ cách tìm | A05, A07, A09, A18, A19 | 5–8 ngày công |
| G3 — Rút ngắn thời gian chờ và hỗ trợ tinh chỉnh | A11, A12, A13, A14, A15 | 5–9 ngày công |
| G4 — Cải thiện QA, TRAKE và tính nhất quán dữ liệu | A10, A16, A21 | 6–10 ngày công |
| G5 — Làm bền import khi vận hành thường xuyên | A22 | 3–6 ngày công |

### G0. Đo hiện trạng và chốt hợp đồng dữ liệu

Có số đo đáng tin trước khi sửa; chốt điều gì được coi là kết quả đúng.

**Phạm vi:** A20. **Ước lượng:** 1–2 ngày công.

**Phụ thuộc:** Không phụ thuộc giai đoạn khác.

1. Ghi lại commit, cấu hình local, dataset/index/model version và trạng thái từng nguồn; backend dùng 8010, không tác động cổng 8000.
2. Bổ sung trace ID và timing từng chặng, bao gồm thời gian client chờ plan, commit SQL và cache. Không ghi secrets hoặc nội dung truy vấn nhạy cảm vào log mặc định.
3. Tạo bộ kiểm thử cố định có frame/segment đáp án cho OCR, ASR, Caption, KIS; thêm QA, TRAKE, no-match và nguồn lỗi. Kiểm tra nguồn hit thực, không suy luận từ nhãn test.
4. Đọc schema và validator hiện có để chốt error/source status, định danh query, phạm vi export và format file. Ghi fixture trước khi sửa contract.

**Cổng nghiệm thu:** Có báo cáo baseline, fixture và phép đo lặp lại được. A20 tiếp tục bổ sung qua mọi giai đoạn.


### G1. Giữ kết quả đúng và thao tác không bị chặn

Search không che lỗi, không ghi đè kết quả mới và không xuất file chưa hợp lệ.

**Phạm vi:** A01, A02, A03, A04, A06, A08, A17. **Ước lượng:** 5–8 ngày công.

**Phụ thuộc:** G0; A06 hoàn tất đối chiếu query ID sau G2.

1. Tách empty/error/degraded; loại mock ngầm khỏi đường dùng thật. Hiển thị nguồn không sẵn sàng và nút thử lại gần thao tác.
2. Thêm request generation guard, AbortSignal, timeout và trạng thái cancel. Chặn Enter trùng ở handler, không chỉ disable nút. Abort trên trình duyệt không đồng nghĩa model phía server đã dừng.
3. Bỏ nhánh ORM quét toàn dataset khỏi search tương tác; fallback chỉ dùng candidate có giới hạn và deadline. Nguồn lỗi khác no-match, kết quả suy giảm được gắn nhãn.
4. Thống nhất validator và format export; chỉ download sau valid. Ưu tiên export query hiện tại; export tất cả chỉ bật khi contract multi-query đã có fixture đúng. Bỏ fallback local tự chạy khi server báo lỗi.
5. Sửa sidebar, modal focus/Escape và tương phản. Readiness kiểm tra từng nguồn với timeout/cache ngắn, phân biệt sống và sẵn sàng.

**Cổng nghiệm thu:** Ca outage/race/no-match/export invalid đều qua; Search và modal dùng được bằng bàn phím ở các viewport đã chọn.


### G2. Giữ phiên làm việc và làm rõ cách tìm

Người dùng đổi task, tải lại trang và tìm nguồn cụ thể mà không mất công đã làm.

**Phạm vi:** A05, A07, A09, A18, A19. **Ước lượng:** 5–8 ngày công.

**Phụ thuộc:** G1 request/error contract; A19 hỗ trợ A05, không chờ refactor toàn bộ mới giao tính năng.

1. Tách session reducer/API hooks và các vùng SearchWorkspace, ResultGrid, Preview, SelectionTray từng bước; giữ hành vi cũ bằng regression tests. Không viết lại toàn bộ App trong một lần.
2. Query ID riêng; lưu draft/options/selection/history theo task và dataset. Persist schema có version, kiểm tra dữ liệu phục hồi; quota/corrupt storage phải có thông báo. Không lưu media blob vào localStorage.
3. Mặc định giữ một luồng Search rõ ràng. Đổi nhãn hoặc tắt control chưa có backend thật; chưa xây Auto nhiều vòng hoặc upload-chat trong đợt này.
4. Thêm chọn nguồn Auto / chữ trong hình / lời thoại / cảnh; override của người dùng không bị heuristic tắt. UI cho biết nguồn thực sự đã chạy.
5. Đồng bộ mật độ grid với chiều rộng vùng nội dung; resize drawer rõ ràng. Profile typing/playback trước khi quyết định memo hoặc virtualization.

**Cổng nghiệm thu:** Reload/đổi task/history phục hồi đúng; hai query cùng loại không trộn selection; source override có hit đúng và export vẫn đúng query.


### G3. Rút ngắn thời gian chờ và hỗ trợ tinh chỉnh

Tìm video nhanh hơn, preview phản hồi ngay, search tránh việc lặp không cần thiết.

**Phạm vi:** A11, A12, A13, A14, A15. **Ước lượng:** 5–9 ngày công.

**Phụ thuộc:** G0 đo lường; G1 deadline/race; G2 session/source contract. A13 có thể làm sớm sau G0.

1. A13: exact video code resolve trước rồi range seek; tránh ABS/count/offset toàn tập khi không cần. Kiểm tra EXPLAIN và kết quả tương đương trước khi thêm index.
2. A14: mở panel bằng poster ngay; URL resolve sau. Cache context/evidence có giới hạn, key bao gồm dataset/index version; refresh URL hết hạn, prefetch chỉ lân cận.
3. A11: chọn một lượt normalization/planning do server sở hữu, UI dùng plan đi cùng kết quả. Chỉ thêm plan_id nếu cần duyệt plan trước khi chạy. Cache 600s đã có, không mặc định đang gọi LLM hai lần.
4. A12: batch ES/vector khi adapter hỗ trợ; song song có giới hạn các I/O độc lập, tổng deadline và giới hạn call. Không chia sẻ SQLAlchemy Session giữa worker.
5. A15: giữ kết quả cũ có nhãn đang cập nhật, snippet/source badge, bộ lọc có phạm vi rõ; một nơi sở hữu diversification. Không giữ kết quả cũ như thể thuộc query mới.

**Cổng nghiệm thu:** Có báo cáo trước/sau theo stage, p50/p95, số call và chất lượng; không đổi ranking ngoài ý muốn hoặc tăng tải không kiểm soát.


### G4. Cải thiện QA, TRAKE và tính nhất quán dữ liệu

Đáp án dựa trên bằng chứng người dùng nhìn thấy; chuỗi sự kiện có thể sửa và dữ liệu được lọc đúng phạm vi.

**Phạm vi:** A10, A16, A21. **Ước lượng:** 6–10 ngày công.

**Phụ thuộc:** G2 query/session/source, G3 planning và deadline. Thay schema/reindex cần kiểm tra dung lượng và rollback trước thực thi.

1. A21 trước: xác định schema dataset/video/time thực; push filter xuống ES/Milvus, fixture hai dataset. Xây manifest coverage/model/index version và phương án alias/rollback nếu cần reindex.
2. A10: dùng evidence service chung cho QA và preview, citation frame/segment/version; retrieval trả trước, sinh đáp án qua bước riêng cho bằng chứng đã chọn. Có state pending/error/cancel riêng.
3. Giới hạn số model call bằng budget cấu hình, cache theo evidence version. Adapter hiện nhận text; chỉ công bố khả năng nhìn ảnh sau khi image contract được triển khai và kiểm thử.
4. A16: event editor dùng số bước được schema hỗ trợ, sửa/reorder sự kiện và kiểm tra timeline cùng video. Khóa event/chạy lại một phần chỉ bật khi backend có contract hỗ trợ và test.
5. Đánh giá QA/TRAKE riêng bằng dữ liệu gán nhãn; không lấy việc endpoint trả 200 làm tiêu chí chất lượng.

**Cổng nghiệm thu:** QA và panel dùng cùng evidence; không rơi candidate vì dataset ngoài phạm vi; sửa một event giữ phần đã chọn; benchmark riêng đạt tiêu chí đã chốt.


### G5. Làm bền import khi vận hành thường xuyên

Job import có thể phục hồi mà không làm search tranh tài nguyên quá mức.

**Phạm vi:** A22. **Ước lượng:** 3–6 ngày công.

**Phụ thuộc:** G4 manifest/version; không chặn bàn giao G1–G3.

1. Chỉ kích hoạt khi có ingest thường xuyên, chạy đồng thời search hoặc yêu cầu phục hồi sau restart. Nếu hiện chỉ import thủ công, giữ mục này trong backlog có điều kiện.
2. Tách worker với job claim/lease, checkpoint, idempotency, retry/backoff và giới hạn tài nguyên; DB lưu trạng thái có thể phục hồi.
3. Readiness từng nguồn dựa trên manifest coverage, không chỉ job completed. Thử restart giữa job trong môi trường kiểm thử rồi xác minh không trùng/mất dữ liệu.
4. Đo search có và không có ingest; điều chỉnh concurrency/budget từ số đo.

**Cổng nghiệm thu:** Job phục hồi có bằng chứng; coverage đúng; search đạt budget đã chốt khi ingest chạy cùng.

## 03. Chỉ số và cách nghiệm thu

Các con số dưới đây là mục tiêu đề xuất, chưa phải hiệu năng đã đạt. G0 xác nhận trên cùng máy, dataset, query set và cấu hình model; tách cold/warm, tải một người và tải đồng thời.

Đo ít nhất 30 lượt warm hợp lệ cho mỗi luồng đại diện và báo số mẫu, median, p95, lỗi; cold start báo riêng. Với model ngoài có chi phí, chốt budget mẫu trước benchmark. Bộ chất lượng phải có đáp án chuẩn và phân nguồn.

- Độ đúng: 0 lần stale response ghi đè trong bộ race; 0 mock ngầm khi nguồn lỗi; 0 file tải khi validation thất bại.
- Phiên: reload/đổi task/history giữ đúng query, options và selection; hai query KIS không trộn đáp án.
- Phản hồi UI: mở khung preview <100ms ở môi trường test; INP mục tiêu <=200ms với tương tác đại diện. Không tính video tải xong là thời điểm panel mở.
- Lookup exact video/frame: mục tiêu p95 <300ms local; gallery và search báo trước/sau. Mẫu 3 lượt cũ (~1,12s gallery/~2,14s lookup) chỉ là tín hiệu, chưa phải p95.
- Retrieval G3: mục tiêu giảm p95 tối thiểu 25% trên tập truy vấn được tối ưu so với baseline; không giảm recall@k/nDCG trên tập chuẩn ngoài sai số đã chốt. Nếu không đạt, ghi kết quả và nguyên nhân, không tuyên bố tối ưu thành công.
- Deadline/fallback: cấu hình deadline theo task sau G0; có test timeout cưỡng bức, giới hạn candidates/calls và quan sát RAM. Không có truy vấn search tương tác tải toàn dataset.
- QA: thời điểm trả frame tách thời điểm có answer; budget số model call độc lập Top K mặc định; citation trỏ đúng evidence/version.
- Responsive/accessibility: 390/1024/1180/1440px, zoom 125%, bàn phím; không che hành động chính, focus modal đúng, chữ nhỏ mục tiêu contrast 4,5:1.
- Export: fixture KIS/QA/TRAKE, multi-query, giới hạn từng query, filename và invalid data; kiểm nội dung file thực sau download.

## 04. Quyết định mặc định để triển khai sau khi duyệt

- Search là luồng chính; tính năng Auto/Chat/attachment chưa có hiệu ứng backend sẽ được ghi đúng phạm vi hoặc tắt. Xây hội thoại nhiều vòng/upload là phạm vi riêng.
- Session lưu local có version trước; đồng bộ nhiều thiết bị/tài khoản chưa thuộc đợt đầu. Kết quả lớn chỉ lưu ID/run reference khi hợp lý, không nhét toàn bộ payload vào storage.
- Export query hiện tại là mặc định. Export tất cả theo contract repo đã xác minh; không tự sáng tạo format hoặc chuyển lỗi server thành local success.
- Planning ưu tiên một lần do server sở hữu. Không thêm streaming/queue chỉ để có thanh tiến trình nếu HTTP hiện tại đáp ứng.
- Giữ ES ASR theo frame trong đợt đầu. Chỉ cân nhắc index segment riêng hoặc reindex sau benchmark và kế hoạch rollback.
- G5 hoãn nếu chưa có nhu cầu ingest thường xuyên. Đây là điều kiện phạm vi, không phải yêu cầu chờ xác nhận cho các bước chuẩn bị/read-only.

## 05. Rủi ro, triển khai và rollback

- Bảo toàn thay đổi đang có trong workspace; ghi base/head khi bắt đầu triển khai và chia change set theo giai đoạn.
- Contract API thay theo cách tương thích khi có client cũ; backend chấp nhận payload hiện tại trong giai đoạn chuyển tiếp. Contract tests kiểm response lỗi, degraded, nguồn hit và export.
- Session schema có migration nhỏ và bản dự phòng; dữ liệu cũ hỏng không làm crash app và không bị xóa âm thầm.
- Cache có giới hạn dung lượng/TTL và key query+dataset+model/index version; không tái sử dụng evidence hoặc signed URL hết hạn.
- Reindex/migration chỉ sau kiểm tra dung lượng, snapshot/backup và mapping; xây index mới, kiểm coverage, đổi alias có đường quay lại. Không xóa index đang dùng để thử nghiệm.
- Rollback theo commit/config/index alias phù hợp. Không bật lại mock ngầm hoặc export bỏ validation để chữa sự cố.
- Concurrency có semaphore/budget và session DB riêng; đo tải trước khi tăng. Nguồn chậm phải có timeout, không kéo dài cả lượt vô hạn.
- Đợt test dùng local/sandbox, không restart hoặc thay đổi dịch vụ dự án khác; port backend giữ 8010. Không đưa .env hay secrets vào báo cáo/commit.

## 06. Checklist bàn giao mỗi giai đoạn

- [ ] Ghi rõ Axx đã hoàn tất, hoàn tất một phần hoặc còn điều kiện.
- [ ] Test phù hợp thay đổi: unit/contract cho logic, integration cho DB/index, browser E2E cho thao tác chính.
- [ ] Chạy với API và dữ liệu thật cho luồng bình thường; dùng mock có chủ đích để tái hiện timeout, outage và race.
- [ ] KIS -> preview -> chọn -> export có file đúng; QA/TRAKE được kiểm riêng khi thay đổi chạm tới.
- [ ] Lưu screenshot/video cần thiết, benchmark có số mẫu và query set; ghi rõ giới hạn chưa kiểm chứng.
- [ ] Rollback cụ thể, không rò secrets, không có thay đổi ngoài phạm vi.
- [ ] Review kết quả trước khi chuyển sang giai đoạn có phụ thuộc.

## 07. Backlog chi tiết / đủ 22 mục

Các vị trí code là mốc audit 16/09; có thể đổi số dòng sau refactor. Mức tin cậy của phát hiện được giữ nguyên từ audit.

### A01. Hiển thị trạng thái thật; bỏ dữ liệu mẫu ngầm

**P1 | G1 | UI / tin cậy**

- Bằng chứng: Đã tái hiện + code. Mô phỏng /datasets và /frames trả 503: UI vẫn hiện 36 frame mẫu, không có thông báo lỗi. status có nhiều nơi set nhưng không được render; lỗi search còn rơi vào màn hình “No frames match”.
- Công việc: Tách loading / empty / error / degraded; hiện thông báo gần hành động, Retry và trạng thái nguồn. Mock chỉ bật trong chế độ demo rõ ràng; không giả context khi API lỗi.
- Phụ thuộc: G0 error contract; phối hợp A08
- Nghiệm thu: 503 phải hiện lỗi và không tạo frame giả; lỗi export/TRAKE phải nhìn thấy; không dùng thông báo “không tìm thấy” cho lỗi mạng.
- Vị trí: `apps/web/src/App.tsx:1581; apps/web/src/App.tsx:1719; apps/web/src/App.tsx:1897; apps/web/src/App.tsx:2123; apps/web/src/App.tsx:2847`

### A02. Hủy search và chặn phản hồi cũ ghi đè

**P1 | G1 | Search / concurrency**

- Bằng chứng: Đã tái hiện bằng response mô phỏng. KIS đang chờ, chuyển QA rồi Enter: response KIS chậm xuất hiện dưới tiêu đề QA. Enter gọi submitSearch dù nút Run search đang disabled. requestJson chưa nhận AbortSignal qua các hàm search; không có request generation guard.
- Công việc: Dùng request ID tăng dần + kiểm tra task/dataset; AbortController cho client; guard tại submitSearch và submitVideoLookup; nút Cancel. Hủy phía server cần cơ chế riêng, không mặc định abort fetch là dừng model.
- Phụ thuộc: G0 trace/request identity
- Nghiệm thu: Enter liên tục chỉ tạo một lượt hợp lệ; response A không được cập nhật UI sau B; Cancel trả quyền thao tác ngay, task switch không bị ghi đè.
- Vị trí: `apps/web/src/App.tsx:2017; apps/web/src/App.tsx:2152; apps/web/src/App.tsx:3064; apps/web/src/api/client.ts:57`

### A03. Loại quét toàn dataset khỏi fallback tương tác

**P1 | G1 | Backend / fallback**

- Bằng chứng: Code + dấu hiệu runtime. Không có candidate thì _fallback_rank_frames tải toàn bộ Frame cùng annotations vào Python và tính overlap. Một smoke query trước đó mất 110.943 ms, không có semantic_hit/text_hit; đây là dấu hiệu phù hợp nhánh fallback, chưa phải đo stage để quy toàn bộ thời gian cho nó. Dataset hiện có 310.301 keyframe.
- Công việc: Fallback có giới hạn candidate và deadline; phân biệt no-match với backend unavailable; dùng lexical index có sẵn thay vì ORM toàn bảng. Trả retrieval_mode và source_status rõ ràng.
- Phụ thuộc: A01/A08 status; A20 timings
- Nghiệm thu: Không dùng .all() toàn keyframes trong request search; test no-match và nguồn lỗi không tăng RAM theo toàn dataset; không gắn score lexical thành visual thật.
- Vị trí: `apps/backend/app/modules/retrieval/service.py:1673; apps/backend/app/modules/retrieval/service.py:2155; apps/backend/app/adapters/text_search/elasticsearch.py:63`

### A04. Giữ nút Search luôn thao tác được khi mở sidebar

**P1 | G1 | UI / responsive**

- Bằng chứng: Đã tái hiện. Tại 1024×768, element tại tâm nút Run search là export-button của sidebar. Drawer fixed z-index 70 phủ composer; không có backdrop/interaction policy rõ ràng.
- Công việc: Dùng drawer có backdrop và close rõ ràng ở màn hẹp, hoặc bố trí panel không phủ composer; ưu tiên nội dung và input. Xác định desktop tối thiểu theo chiều rộng vùng làm việc.
- Phụ thuộc: Không chờ backend
- Nghiệm thu: Search không bị che ở 1024/1180/1440px, cả hai sidebar và browser zoom 125%; overlay có cách đóng bằng chuột và bàn phím.
- Vị trí: `apps/web/src/styles.css:2209; apps/web/src/styles.css:2223; apps/web/src/App.tsx:1590`

### A05. Giữ draft, lịch sử và lựa chọn theo từng câu hỏi

**P1 | G2 | Workflow / session**

- Bằng chứng: Đã tái hiện + code. KIS → QA → KIS mất draft vì changeType đặt sampleQueries. history/selected chỉ ở React state, history giới hạn 12; restoreHistory không khôi phục đầy đủ cấu hình, exportFileName hay video frame query. New search không tạo query ID mới.
- Công việc: Workspace theo query ID: draft, task, dataset, options, kết quả, selection và scroll. Autosave local, có schema version; phục hồi run từ backend; New query có định danh riêng.
- Phụ thuộc: A02; A19 tách state tối thiểu
- Nghiệm thu: Reload phục hồi draft và selected; đổi task không mất nội dung; mở history phục hồi cùng task/options/export context; 2 query KIS không trộn đáp án.
- Vị trí: `apps/web/src/App.tsx:1544; apps/web/src/App.tsx:1846; apps/web/src/App.tsx:1872; apps/web/src/App.tsx:2327; apps/web/src/App.tsx:2610`

### A06. Thống nhất phạm vi và validation khi xuất đáp án

**P1 | G1 | Submission**

- Bằng chứng: Code; chưa tái hiện bằng dữ liệu lỗi. UI export toàn selected, không chỉ query hiện tại. Server ghi nhiều nhóm query vào một CSV không có query_name; fallback local lại dùng format có header cho nhiều query. UI download trước khi kiểm tra report, rồi catch mọi lỗi để xuất local; có thể bỏ qua invalid report. Giới hạn 100 ở UI áp dụng toàn tray, server kiểm theo query.
- Công việc: Chọn rõ Export current query / Export all queries; cùng một contract server/local; kiểm validation trước download; xuất tất cả theo cấu trúc được quy định trong repo. Cho chỉnh QA answer, rank, Undo và xem trước file.
- Phụ thuộc: G0 export fixture; tích hợp lại sau A05
- Nghiệm thu: Fixture KIS/QA/TRAKE và multi-query có byte/format parity; invalid report không tự tải fallback; mỗi query có giới hạn riêng; filename gắn đúng query.
- Vị trí: `apps/web/src/App.tsx:400; apps/web/src/App.tsx:1923; apps/web/src/App.tsx:2576; apps/web/src/api/client.ts:202; apps/backend/app/modules/submissions/service.py:239`

### A07. Làm rõ Search, Auto, Chat và attachment thực sự làm gì

**P1 | G2 | Workflow / tính năng**

- Bằng chứng: Code. Search và Auto dùng chung submitSearch/runSearch; autoEnabled chủ yếu đổi UI/label. Chat gửi query độc lập, không truyền history; attachedFiles chỉ lấy tên, không upload trong submitChat. Copy lại mời upload video cho QA.
- Công việc: Ưu tiên một Search workspace; Auto thành chiến lược rõ ràng với điều kiện dừng; Chat ghi đúng phạm vi. Hoặc nối attachment tới upload/job/dataset thực, hoặc ẩn/disable và giải thích trước khi dùng.
- Phụ thuộc: A08 capability thực
- Nghiệm thu: Mọi control có hiệu ứng backend được chứng minh; file hiển thị đã sẵn sàng tìm trước khi dùng; follow-up Chat có context hoặc nêu rõ độc lập.
- Vị trí: `apps/web/src/App.tsx:2017; apps/web/src/App.tsx:2225; apps/web/src/App.tsx:2249; apps/web/src/App.tsx:2896; apps/web/src/App.tsx:3219`

### A08. Hiển thị khả năng sẵn sàng của từng nguồn tìm kiếm

**P1 | G1 | Vận hành / readiness**

- Bằng chứng: Code + runtime phiên import. /readyz luôn trả ready, /api/models chủ yếu liệt kê config. .env Drive có PostgreSQL không kết nối được; phiên kiểm thử dùng SQLite snapshot. Visual score bằng 0 trong các smoke không đủ chứng minh vector đang sẵn sàng. Redis cache log thiếu package.
- Công việc: Preflight read-only cho metadata DB, ES/index coverage, embedder, Milvus collection/model dimension và media; short cache, timeout; status trên UI và disable lựa chọn không sẵn sàng. Chuẩn hóa launcher local 8010 và env scope.
- Phụ thuộc: G0 inventory cấu hình và nguồn
- Nghiệm thu: Ready không chỉ là process sống; ngắt một nguồn phải báo đúng nguồn, text còn dùng được; không gọi nhầm 8000; cấu hình local/cloud thể hiện rõ.
- Vị trí: `apps/backend/app/main.py:57; apps/backend/app/core/deps.py:28; apps/backend/app/modules/models/router.py:12; apps/backend/app/modules/retrieval/service.py:216; be.cmd:3; docker-compose.yml:27`

### A09. Cho người dùng chọn tìm chữ, lời thoại hay cảnh

**P1 | G2 | Retrieval / intent**

- Bằng chứng: Code + smoke trước đó. Heuristic mặc định OCR weight = 0 nếu query không có cue chữ hiển thị; _gate_ocr_weight còn có thể ghi đè lựa chọn planner. Smoke gắn nhãn OCR chỉ có 5 kết quả fallback, không phải OCR hits; smoke gắn nhãn ASR lại trả top hits từ Caption.
- Công việc: Thêm Auto / Visible text / Speech / Scene, hoặc source chips có override rõ ràng. Giữ exact phrase và cue tiếng Việt; explain nguồn đã tìm, không suy ra từ tên smoke test.
- Phụ thuộc: A08; source contract client/server
- Nghiệm thu: Bộ query OCR và ASR được chọn trước phải có source hits đúng loại; người dùng bật OCR thì không bị heuristic tắt; đo recall/ranking trước-sau.
- Vị trí: `apps/backend/app/modules/retrieval/query_planning.py:910; apps/backend/app/modules/retrieval/query_planning.py:965; apps/backend/app/modules/retrieval/service.py:2069; apps/web/src/api/client.ts:92`

### A10. Tách tìm frame khỏi sinh đáp án QA, thống nhất bằng chứng

**P1 | G4 | QA / evidence**

- Bằng chứng: Code; chưa benchmark QA tải thật. _search_frame_level gọi visual_qa.answer tuần tự cho mỗi candidate tới top_k. Evidence lấy từ Frame.annotations trong DB; panel video lại đọc ES. Adapter OpenAICompatibleVisualQaModel nhận text evidence, không nhận ảnh ở contract này.
- Công việc: Trả retrieval trước; generate answer cho top evidence đã gom hoặc frame được chọn; cache theo query+evidence version. Dùng cùng evidence service cho QA và UI; nếu cần nhìn ảnh thì bổ sung image contract thực.
- Phụ thuộc: A05/A09; A21 evidence version
- Nghiệm thu: Kết quả đầu xuất hiện trước QA generation; số model call không tăng tuyến tính theo Top K mặc định; answer có citation frame/segment và dùng đúng index version.
- Vị trí: `apps/backend/app/modules/retrieval/service.py:889; apps/backend/app/modules/retrieval/service.py:900; apps/backend/app/modules/retrieval/service.py:2671; apps/backend/app/adapters/model_runtime/openai_compatible.py:155`

### A11. Một lượt planning có thể tái sử dụng trong search

**P2 | G3 | Latency / planning**

- Bằng chứng: Code; không khẳng định luôn gọi LLM hai lần. Web await /plan rồi /search; cả hai gọi _normalize_query. Planner có cache 600 giây cho kết quả hợp lệ, fallback không cache; query expansion nằm ngoài cache planner nên vẫn có thể lặp. Chưa có plan_id trong SearchRequest.
- Công việc: Một endpoint phát progress + results, hoặc plan_id/hash được server quản lý và tái dùng; cache có version config/index, coalesce request giống nhau; lỗi transient có backoff ngắn phù hợp.
- Phụ thuộc: A02/A20; giữ contract cho A16
- Nghiệm thu: Một search chỉ thực hiện một plan/expansion hợp lệ; UI dùng đúng plan của kết quả; đo success, fallback và multi-worker riêng.
- Vị trí: `apps/web/src/App.tsx:2055; apps/backend/app/modules/retrieval/service.py:136; apps/backend/app/modules/retrieval/service.py:237; apps/backend/app/modules/retrieval/query_planning.py:139; apps/backend/app/modules/retrieval/query_planning.py:326`

### A12. Batch và giới hạn song song các nhánh retrieval

**P2 | G3 | Latency / retrieval**

- Bằng chứng: Code; lợi ích cần benchmark. _rank_frames chạy semantic trước text. Semantic lặp collection/variant và Milvus nhận một vector mỗi search; _text_scores lặp source/variant; TRAKE lặp event. Embedding đã có embed_texts theo batch, cần giữ điểm tốt này.
- Công việc: ES _msearch; batch vector queries; song song có giới hạn các call độc lập, tuyệt đối không chia sẻ SQLAlchemy Session giữa worker. Tổng deadline, circuit breaker và budget theo task.
- Phụ thuộc: A03/A20; query parity fixtures
- Nghiệm thu: Ghi stage timings và số call; giảm p95 mà giữ top-k/recall; lỗi một nguồn không treo nguồn còn lại; có giới hạn concurrency.
- Vị trí: `apps/backend/app/modules/retrieval/service.py:1646; apps/backend/app/modules/retrieval/service.py:1818; apps/backend/app/modules/retrieval/service.py:2075; apps/backend/app/adapters/vector_db/milvus.py:32; apps/backend/app/modules/retrieval/service.py:998`

### A13. Tối ưu tra video/frame và tải gallery

**P2 | G3 | SQL / gallery**

- Bằng chứng: Đã đo + EXPLAIN read-only. Trung vị 3 lần local: gallery 48 frame 1.120 ms; lookup 12 frame 2.137 ms. Endpoint dùng contains ilike, count(), abs(frame_idx-target) và offset. EXPLAIN truy vấn tương đương: SCAN keyframes, TEMP B-TREE ORDER BY; index video/frame đã có.
- Công việc: Video code chính xác: resolve video trước rồi range seek bằng index; tách tìm tên gần đúng. Dùng hai range trước/sau target thay ABS toàn tập; cân nhắc count cache/lazy và cursor khi phân trang sâu.
- Phụ thuộc: A20; EXPLAIN đúng DB đang chạy
- Nghiệm thu: Lấy cùng tập frame đúng thứ tự; p95 lookup local mục tiêu <300 ms sau benchmark đủ mẫu; tránh full scan ở nhánh exact code; không thêm index trùng.
- Vị trí: `apps/backend/app/modules/media/router.py:536; apps/backend/app/modules/media/router.py:558; apps/backend/app/db/session.py:49`

### A14. Mở preview ngay và tái sử dụng context/evidence

**P2 | G3 | Media / preview**

- Bằng chứng: Code; context/evidence hiện đã nhanh. openVideoPreview đợi preview-url trước khi mở modal, rồi gọi context dù đã có context; effect gọi evidence cả khi panel đang đóng. Prev/Next đi API mỗi lần. 19 ảnh hiển thị ở smoke đã tải được; chưa có bằng chứng cần rewrite image pipeline.
- Công việc: Mở modal bằng poster ngay, URL resolve sau; cache ngắn theo video/frame/index version, prefetch lân cận có giới hạn; evidence fetch on demand hoặc reuse. Chỉ tối ưu thumbnail/CDN sau khi đo bytes và decode.
- Phụ thuộc: A02/A05; version key khi A21 sẵn sàng
- Nghiệm thu: Phản hồi mở panel mục tiêu <100 ms; mở lại frame không gọi context trùng; error khác empty; URL hết hạn được refresh.
- Vị trí: `apps/web/src/App.tsx:1667; apps/web/src/App.tsx:2340; apps/web/src/App.tsx:2469; apps/backend/app/modules/media/router.py:402; apps/web/src/App.tsx:903`

### A15. Biến kết quả thành không gian lọc và so sánh

**P2 | G3 | Results / refinement**

- Bằng chứng: Code + UI. Filter row và dataset selector đang comment; score breakdown trên mọi card chiếm chỗ nhưng không có đoạn chữ match ở card. Đang search thì thay toàn bộ kết quả bằng skeleton. Có diversification ở backend và thêm ở frontend.
- Công việc: Giữ kết quả cũ có nhãn stale khi refine; đưa source badge và snippet/highlight lên gần thumbnail; nhóm theo video với mở rộng frame; filter video/time/source rõ ràng; technical scores vào disclosure. Chọn một nơi sở hữu diversification.
- Phụ thuộc: A05/A09; một owner diversification
- Nghiệm thu: Refine không mất scroll/selection; biết vì sao frame khớp; lọc có phạm vi server/client ghi rõ; số kết quả không mất bí ẩn sau hai lần diversification.
- Vị trí: `apps/web/src/App.tsx:2824; apps/web/src/App.tsx:2841; apps/web/src/App.tsx:2103; apps/backend/app/modules/retrieval/service.py:1794`

### A16. Cho sửa chuỗi sự kiện trước khi chạy lượt tìm dài

**P2 | G4 | TRAKE / thao tác**

- Bằng chứng: Code; chưa đánh giá chất lượng TRAKE live. Backend hỗ trợ temporal_events, min_match, anchor và nhiều strategy; payload web không gửi phần lớn các control này. UI đã kiểm cùng video và thứ tự frame, nhưng số event mặc định 4 rồi cập nhật sau kết quả.
- Công việc: Event editor 2–8 bước, reorder/merge/split, khóa frame/event đã đúng, chạy lại event thiếu; timeline chung video và trạng thái khớp. Ưu tiên sửa plan trước khi tăng thuật toán.
- Phụ thuộc: A05/A11; kiểm schema temporal
- Nghiệm thu: User sửa E2 không mất E1/E3 đã chọn; sequence đúng video, tăng thời gian; event count khớp plan thực; có benchmark riêng KIS temporal và TRAKE.
- Vị trí: `apps/backend/app/modules/retrieval/schemas.py:24; apps/web/src/api/client.ts:92; apps/web/src/App.tsx:1573; apps/web/src/App.tsx:1955; apps/web/src/App.tsx:1978`

### A17. Sửa focus, Escape và tương phản chữ phụ

**P1 | G1 | Accessibility / keyboard**

- Bằng chứng: Đã tái hiện + tính contrast. Modal Video có aria-modal nhưng Escape không đóng, focus vẫn ngoài dialog khi mở. Các toggle chỉ đổi class, không có aria-pressed. --text-3 #8a8a8a trên trắng đạt ~3,45:1; đang dùng cho chữ nhỏ. Có 4 nút nhỏ hơn 44×44 trong các viewport đã kiểm, đây là rủi ro usability chứ chưa đủ kết luận vi phạm kích thước WCAG.
- Công việc: Dialog/focus trap, restore focus, Escape; aria-pressed và tab semantics; chỉnh token muted. Phím tắt search, next/prev, pick có hướng dẫn và không kích hoạt khi đang gõ.
- Phụ thuộc: A04 drawer/dialog chung
- Nghiệm thu: Tab nằm trong modal, Escape đóng và trả focus; toggle đọc được trạng thái; chữ nhỏ đạt mục tiêu 4,5:1; thao tác chính dùng được không cần chuột.
- Vị trí: `apps/web/src/App.tsx:3431; apps/web/src/App.tsx:3381; apps/web/src/styles.css:18; apps/web/src/styles.css:88`

### A18. Thống nhất control mật độ và bố cục theo viewport

**P2 | G2 | UI / density**

- Bằng chứng: Đã tái hiện + code. Chọn 2 frame/hàng tại 1024px nhưng computed grid vẫn 4 do !important ở breakpoint. Sidebar state chỉ khởi tạo từ window.innerWidth, không đồng bộ resize. Các label Intelligence/Metadata/Agent plan phân tán và khó suy ra tác dụng.
- Công việc: Mật độ Comfortable/Compact hoặc số cột có clamp rõ ràng theo content width; resize policy cho drawer; gom control search gần input, nâng cao trong một disclosure; thống nhất ngôn ngữ UI.
- Phụ thuộc: A04; phối hợp A19
- Nghiệm thu: Giá trị control khớp số cột thực; 390/1024/1440 không tràn ngang; người dùng nhận ra mode và nguồn đang bật không cần mở nhiều menu.
- Vị trí: `apps/web/src/styles.css:2187; apps/web/src/styles.css:2303; apps/web/src/App.tsx:1590; apps/web/src/App.tsx:3107; apps/web/src/App.tsx:3381`

### A19. Tách state và render theo vùng tương tác

**P2 | G2 | Frontend / cấu trúc**

- Bằng chứng: Code; chưa có React Profiler benchmark. App.tsx 3.608 dòng, CSS 2.407 dòng; state input, video playback, results, tray, history trong cùng component. FrameCard chưa memo, callbacks inline; useMemo và ảnh lazy/async đã có.
- Công việc: Tách SearchWorkspace, ResultGrid, Preview, SelectionTray, session reducer và API hooks; profile trước/sau. Memo ở ranh giới có lợi; virtualization chỉ khi số card/cost chứng minh cần.
- Phụ thuộc: Profile G0; refactor tăng dần cùng A05
- Nghiệm thu: Typing và playback không render lại toàn grid không đổi; đo React commits/INP; giữ parity KIS/QA/TRAKE, preview, export.
- Vị trí: `apps/web/src/App.tsx:1543; apps/web/src/App.tsx:2869; apps/web/src/App.tsx:3489; apps/web/src/styles.css:1`

### A20. Đo thời gian và chất lượng theo từng giai đoạn

**P2 | G0 | Observability / đánh giá**

- Bằng chứng: Code + kết quả smoke. API chỉ có latency_ms tổng, tính trước commit/cache history hoàn tất; frontend có thêm /plan nên khác thời gian người dùng đợi. Redis ghi sync sau search, chưa dùng để phục hồi history trên UI. Có unit tests backend nhưng chưa thấy test script E2E trong package web.
- Công việc: Trace ID xuyên UI/API; timers plan/expand/embed/vector/ES/DB/rerank/QA/serialize; đo time-to-first-result, p50/p95, fallback rate, recall@k và thời gian tới CSV. Bộ golden queries phân OCR/ASR/Caption/KIS/QA/TRAKE.
- Phụ thuộc: Bắt đầu G0, nghiệm thu mỗi giai đoạn
- Nghiệm thu: Dashboard/test report phân biệt source thật và fallback; latency client/server đối chiếu được; regression suite bao gồm race, outage, switch task, export và responsive.
- Vị trí: `apps/backend/app/modules/retrieval/service.py:136; apps/backend/app/modules/retrieval/service.py:163; apps/backend/app/modules/retrieval/service.py:216; apps/web/package.json:6; apps/backend/tests/test_retrieval_pipeline.py:1`

### A21. Lọc dataset trước retrieval và quản lý index version

**P2 | G4 | Data / phạm vi tìm kiếm**

- Bằng chứng: Code; rủi ro lớn hơn khi có nhiều dataset. ES search lọc source_type nhưng không nhận dataset; service lọc video sau khi lấy top-k. Milvus adapter có filters nhưng call site không truyền dataset. ASR được nhân thành doc theo frame; import hiện dùng index cố định.
- Công việc: Push dataset/video/time filters xuống ES/Milvus theo schema thật; index alias theo version, manifest coverage và model compatibility. Cân nhắc ASR segment index riêng chỉ sau benchmark mapping/recall.
- Phụ thuộc: A08/A09; schema/index inventory
- Nghiệm thu: Hai dataset chung index không làm rơi candidate hợp lệ; import count và source coverage kiểm được; đổi index có rollback và không làm mất evidence.
- Vị trí: `apps/backend/app/adapters/text_search/elasticsearch.py:25; apps/backend/app/modules/retrieval/service.py:1834; apps/backend/app/modules/retrieval/service.py:2099; apps/backend/scripts/import_asr_to_elasticsearch.py:21`

### A22. Tách job nặng khỏi request server, giữ tiến độ có thể phục hồi

**P2 | G5 | Ingestion / vận hành**

- Bằng chứng: Code; chưa chạy tải ingest đồng thời. Pipeline và upload dùng FastAPI BackgroundTasks trong process; Job đã có progress/status nhưng runner chưa thể hiện cơ chế claim/lease/retry/resume bền vững. Script import có summary riêng, UI chưa thể hiện coverage từng nguồn.
- Công việc: Worker/job queue bền vững khi nhu cầu ingest thường xuyên; giới hạn tài nguyên, checkpoint, idempotency, retry có kiểm soát; dataset readiness theo metadata/media/OCR/Caption/ASR/vector.
- Phụ thuộc: A21; nhu cầu ingest đồng thời được xác nhận
- Nghiệm thu: Restart thử trong sandbox phục hồi được; search latency không tăng ngoài budget khi ingest; UI chỉ báo ready cho nguồn đã validate coverage.
- Vị trí: `apps/backend/app/modules/pipeline/router.py:29; apps/backend/app/modules/pipeline/router.py:64; apps/backend/app/modules/ingest/runner.py:28; apps/backend/app/modules/jobs/router.py:13`

## 08. Tài liệu đối chiếu

- [Sơ đồ kiến trúc có chú thích](system-architecture-annotated.html)
- [Audit gốc](../docs/reviews/2026-09-16-ui-search-optimization-audit.html)
- [Bản HTML của kế hoạch](ui-search-optimization-plan.html)

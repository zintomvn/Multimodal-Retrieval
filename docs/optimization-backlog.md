# Tình trạng 22 mục và lỗi có sẵn trên main

Đối chiếu ngày 17/09/2026 trên nhánh `feat/ui-search-optimization`, HEAD triển khai `7e6a40bedad1ca15b7657b459ccfb9176756640f`. Base main: `5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f`.

## Cách đếm

Đã chạm tới 7/22 mục, nhưng chỉ 1 mục đủ điều kiện đóng trong phạm vi đã định. Còn 21 mục mở: 6 mục làm một phần và 15 mục chưa bắt đầu. Không dùng số commit để suy ra số mục hoàn tất. Đây là số lượng mục, không phải phần trăm khối lượng còn lại.

- Hoàn tất phạm vi: A02 (hủy/chặn stale response phía client). Không bao gồm hủy công việc model phía server.
- Làm một phần: A01, A03, A04, A06, A17, A20.
- Chưa bắt đầu: A05, A07–A16, A18, A19, A21, A22.
- B01 và B02 dưới đây là hai việc con bổ sung vào các mục gốc; không cộng thành 24 mục tối ưu. Nếu theo dõi ticket riêng thì có thêm hai ticket sửa regression đang mở.

## Bảng công việc còn lại

| Mã | Trạng thái | Điều còn cần làm để đóng mục |
|---|---|---|
| A01 | Một phần | Lỗi evidence hiện vẫn rơi vào nội dung trống; thống nhất error/empty/degraded ở các mode và panel, có ca hồi quy. Dataset/gallery mock ngầm đã bỏ. |
| A02 | Hoàn tất phạm vi | Request gate, abort, chống Enter trùng và stale response đã kiểm thử. Server cancellation là việc riêng thuộc budget/backend. |
| A03 | Một phần | Đã bỏ quét toàn keyframes; hoàn tất deadline/budget phía server, kiểm tải/RAM và suy giảm từng nguồn. Giữ liên kết A12, không làm lại cùng hạng mục. |
| A04 | Một phần | Đã kiểm Search ở 390/1024/1180/1440px; kiểm bổ sung hai sidebar, chuyển breakpoint và zoom 125%. |
| A05 | Chưa bắt đầu | Query ID độc lập, lưu/phục hồi draft, options, history, selections; không trộn nhiều câu hỏi KIS. |
| A06 | Một phần | Đã chặn tải trước validation và bỏ local fallback; còn fixture QA/TRAKE, query identity, contract multi-query, B02 và đồng bộ schema/tài liệu. |
| A07 | Chưa bắt đầu | Nêu đúng phạm vi Search/Auto/Chat; tắt hoặc kết nối thật attachment/control chưa có backend. |
| A08 | Chưa bắt đầu | Readiness có kiểm từng nguồn; /readyz hiện còn tĩnh. Phân biệt cấu hình có sẵn với dịch vụ chạy được. |
| A09 | Chưa bắt đầu | Source override OCR/ASR/scene, query chuẩn và recall/ranking; liên kết B01 nhưng không bật OCR toàn cục để chữa test. |
| A10 | Chưa bắt đầu | QA trả retrieval trước, giới hạn số model call, evidence/citation chung với preview. |
| A11 | Chưa bắt đầu | Tái sử dụng normalization/plan/expansion; đo cache hiện có trước khi thay contract. |
| A12 | Chưa bắt đầu | Batch/parallel có giới hạn, deadline và partial failures; không chia sẻ DB session giữa worker. |
| A13 | Chưa bắt đầu | Tối ưu query lookup/gallery dựa trên EXPLAIN và mẫu đại diện; baseline mới đã đo, chưa có thay đổi query. |
| A14 | Chưa bắt đầu | Preview phản hồi ngay; cache/prefetch context có giới hạn; evidence on demand và trạng thái lỗi. |
| A15 | Chưa bắt đầu | Lọc nguồn/video/time, snippet, giữ kết quả cũ có nhãn, thống nhất diversification. |
| A16 | Chưa bắt đầu | Sửa chuỗi event và timeline, giữ phần đã chọn, benchmark TRAKE riêng; B01 là nền test trước bước này. |
| A17 | Một phần | Focus/Tab/Escape đã sửa; còn toggle semantics, contrast audit theo nền/theme và luồng bàn phím toàn bộ. |
| A18 | Chưa bắt đầu | Control số cột khớp grid thật, chính sách resize và bố cục control. |
| A19 | Chưa bắt đầu | Tách state/component theo vùng, đo render bằng profiler trước khi memo/virtualize. |
| A20 | Một phần | Đã có trace/stage timings và baseline đọc metadata; còn golden queries, timing provider/QA, chỉ số chất lượng và hồi quy E2E đầy đủ. |
| A21 | Chưa bắt đầu | Filter dataset trước top-k; manifest coverage/model/index version, alias và rollback. |
| A22 | Chưa bắt đầu, có điều kiện | Worker/job retry/resume bền vững khi ingest thường xuyên/đồng thời search; chưa cần chặn đợt web đầu. |

## B01. Test TRAKE còn giả định luôn tìm cả ba nguồn

Phân loại: kỳ vọng test không còn khớp chính sách routing hiện tại. Chưa có bằng chứng ca này làm mất một sự kiện TRAKE. Ưu tiên P2 nhưng làm sớm trong G0 để bộ regression đáng tin; liên kết A09/A16/A20.

Nguyên nhân: `test_trake_labeled_query_searches_every_event_as_separate_query` đòi mỗi sự kiện được truy vấn ba lần (ASR/OCR/Caption). Bốn câu mô tả nấu ăn không có cue chữ hiển thị. `infer_retrieval_strategy` và `_gate_ocr_weight` đặt OCR = 0; `_text_scores` bỏ nguồn có boost bằng 0. Thực tế là tám call (bốn sự kiện x ASR/Caption), không phải mười hai. Fake chỉ ghi query, không ghi nguồn, nên assertion vừa giòn vừa không kiểm đúng điều cần bảo vệ.

Bằng chứng: lỗi đã tái hiện trên base main. Trong một bản probe tạm dùng code main, đổi kỳ vọng thành ASR/Caption và ghi cả source_types: test qua, bao gồm toàn bộ assertions phía sau về bốn sự kiện, frames 100/200/300/400, event_index và matched_events. Không sửa test production hoặc ép OCR chạy trong bước điều tra này.

Đề xuất sửa:

- Fake ghi từng request theo query và source_types; so sánh tập/multiset cặp event–source mong đợi, không phụ thuộc thứ tự thực thi hay số call cố định sau khi batching.
- Giữ assertions về từng event, thứ tự frame, cùng video và đủ bốn bước; không đơn thuần đổi range(3) thành range(2).
- Tách test chính sách: truy vấn không có cue OCR không gọi OCR; có cue chữ hiển thị phải gọi OCR. Khi A09 thêm explicit override, thêm test override không bị heuristic tắt.
- Khi A12 đổi batching, kiểm payload bao phủ đủ event/source thay vì khóa cách triển khai bằng call count.

Nghiệm thu: test phân rã bốn event qua trên main logic hiện tại; xóa một event hoặc route sai nguồn phải làm test thất bại; vẫn kiểm đủ chuỗi đáp án. Không gọi model/network thật.

Vị trí: `apps/backend/tests/test_qa_trake_hardening.py:66` và `:266`; `apps/backend/app/modules/retrieval/query_planning.py:910` và `:965`; `apps/backend/app/modules/retrieval/service.py:2086`.

## B02. Contract export ZIP bị thay bằng CSV nhưng test/tài liệu còn cũ

Phân loại: lệch contract giữa implementation, tests, schema và tài liệu; không chỉ là đổi tên test. Ưu tiên P1, thuộc A06/G1; triển khai backend contract trước, UI multi-query sau A05.

Nguyên nhân xác nhận từ git: commit `5b81741` thay `SubmissionService.export_zip` bằng `export_csv`, router export/download chuyển sang CSV. Test vẫn gọi export_zip nên dừng ở AttributeError trước khi kiểm nội dung. README vẫn mô tả ZIP `submission/*.csv`. Service hiện còn ghi đường dẫn CSV vào cột zip_uri; router trả cùng đường dẫn đó cho cả csv_uri và zip_uri. Với nhiều query, export_csv nối các nhóm vào một CSV không có định danh query, gây mất ranh giới câu hỏi.

Đề xuất ưu tiên: giữ CSV cho một query như UI đang dùng và bổ sung ZIP nhiều query một cách tường minh, tương thích client hiện tại. Không chỉ thêm alias export_zip -> export_csv để làm test xanh.

- Dùng chung serializer/validator tạo CSV UTF-8, không header, đúng format KIS/QA/TRAKE; export_csv chỉ chấp nhận một query, trả lỗi có cấu trúc nếu nhận nhiều query.
- Khôi phục chức năng ZIP riêng: mỗi query một file `submission/<query_name>.csv`; kiểm tên file an toàn, tên trùng sau chuẩn hóa, giới hạn từng query và thứ tự rank.
- Contract export có format rõ ràng, mặc định csv để giữ tương thích; download chọn đúng artifact/filename/MIME. Schema lưu và trả csv_uri/zip_uri đúng nghĩa; nếu thêm cột thì migration/backfill có kiểm soát cho bản ghi cũ chứa CSV trong zip_uri.
- Cập nhật README, types và tests cùng lần thay contract. UI chỉ bật Export all sau khi A05 bảo đảm query identity và test đầy đủ.
- Thêm fixture invalid report không tạo artifact; single-query CSV, multi-query ZIP, ký tự tiếng Việt/ngoặc kép/dấu phẩy, QA answer normalization và TRAKE thứ tự tăng.
- Rà fixture `_build_submission_service`: Settings đọc DATA_ROOT ở class definition lúc import nên monkeypatch env + cache_clear chưa bảo đảm đổi root. Inject settings/data_root thực sự vào service test và assert artifact nằm dưới tmp_path để không ghi vào data local khi chạy test mới.

Nghiệm thu: API single-query CSV vẫn tương thích; ZIP có đúng cây thư mục và nội dung mỗi query; CSV multi-query bị từ chối; cả API/download và tests khớp format; invalid không tải artifact; artifact test chỉ ở thư mục tạm. Quy tắc ZIP ở đây dựa vào hợp đồng repo, chưa phải xác minh quy định cuộc thi hiện hành.

Vị trí: `apps/backend/app/modules/submissions/service.py:239`; `apps/backend/app/modules/submissions/router.py:32`; `apps/backend/tests/test_submission_hardening.py:21` và `:60`; `apps/backend/README.md:131` và `:352`; `apps/backend/app/core/config.py:66`.

## Thứ tự tiếp theo

1. B01: sửa regression assertion đúng ý nghĩa; hoàn thiện baseline A20.
2. A08 và phần còn lại A01/A03: readiness, error/degraded và budget backend.
3. B02 cùng A06: thống nhất export contract và fixtures; A04/A17 hoàn tất kiểm thử UI còn thiếu.
4. G2: A05/A07/A09/A18/A19, sau đó G3–G4 theo phụ thuộc đã ghi; G5 giữ điều kiện kích hoạt.

Hai lỗi main hiện mới được phân tích và lên phương án, chưa sửa trong lượt cập nhật này. Kết quả full suite gần nhất vẫn là 121 passed / 2 failed; probe tạm B01 qua không thay thế kết quả suite.

# E2E và số liệu trước / sau tối ưu

## Kết quả chính

Gallery cải thiện rõ, search KIS chưa nhanh hơn đáng kể. Bộ smoke E2E chung đạt **10/19 trước -> 19/19 sau**; đây là các tiêu chí kiểm tra cụ thể bên dưới, không phải tỷ lệ hoàn thành toàn bộ 22 mục tối ưu.

Không có thay đổi source ứng dụng trong đợt đo này. Bản trước là main `5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f`; bản sau là `03d16085fd91ebbf36ec08a55a3657239eeec8eb`, nhánh `feat/ui-search-optimization`. Các commit báo cáo/test sau đó không thuộc code được đo.

## Số liệu trước / sau

Đơn vị: ms. Số âm là nhanh hơn; số dương là chậm hơn. p95 dùng nearest-rank; mẫu nhỏ chỉ là tín hiệu, chưa đủ để kết luận có ý nghĩa thống kê.

| Luồng | N / bản | p50 trước | p50 sau | p95 trước | p95 sau | Thay đổi p95 |
|---|---:|---:|---:|---:|---:|---:|
| API gallery (48 frame) | 30 | 305.46 | 17.13 | 460.75 | 55.16 | -88.0% |
| API tìm video (12 frame) | 30 | 16.52 | 13.81 | 29.05 | 19.41 | -33.2% |
| API context | 20 | 8.21 | 16.33 | 26.11 | 33.65 | +28.9% |
| API evidence | 20 | 19.35 | 19.56 | 50.07 | 34.68 | -30.7% |
| API gallery, 4 client đồng thời | 40 | 705.48 | 29.56 | 865.04 | 53.35 | -93.8% |
| API KIS top 20 (3 query x 3) | 9 | 4989.55 | 4927.39 | 5508.40 | 5554.19 | +0.8% |
| Web: điều hướng -> 48 card hiện | 10 | 453.50 | 209.50 | 900.00 | 929.00 | +3.2% |
| Web: click -> mở preview | 5 | 88.00 | 174.00 | 136.00 | 281.00 | +106.6% |
| Web: click Search -> kết quả KIS | 3 | 6061.00 | 5650.00 | 6110.00 | 6617.00 | +8.3% |

Thời gian tải CSV: 315 -> 280 ms (chỉ 1 lần / bản, không suy rộng). Video lookup trên UI: 83 -> 73 ms (1 lần / bản). Nội dung CSV hai bản giống hệt `L21_V001,0`.

Gallery API được đo xen kẽ hai bản, đảo thứ tự mỗi lượt; 30 lượt sau lần quan sát đầu. Gallery/count cache của bản sau có TTL 10 giây, gồm cả lượt có thể hết hạn cache. Lần quan sát đầu: 382,38 -> 2254,26 ms; main đã được mở bằng trình duyệt trước đó, nên KHÔNG được dùng cặp này làm số liệu cold-start. Không đo OS/disk cold-start; không xóa cache máy người dùng.

Browser đo production build, gồm mọi lượt trong chuỗi (10 gallery, 5 preview, 3 KIS), không loại outlier. Gallery browser p95 và preview hiện chậm hơn; chỉ p50 gallery cải thiện rõ. Các luồng browser/context/evidence và tải 4 client chạy trước rồi sau, không xen kẽ; nhiễu môi trường/remote vẫn có thể ảnh hưởng.

## E2E thật và dữ liệu đúng

Trình duyệt Chromium, API thật, SQLite thật, Elasticsearch thật; không mock trong suite chung.

| Tiêu chí | Trước | Sau |
|---|---|---|
| Hiện đủ 48 card | PASS | PASS |
| Focus vào preview | FAIL | PASS |
| Escape đóng preview | FAIL | PASS |
| Pick frame | PASS | PASS |
| Tải CSV | PASS | PASS |
| Giữ draft khi reload | FAIL | PASS |
| Giữ frame đã chọn khi reload | FAIL | PASS |
| CSV đúng L21_V001,0 | PASS | PASS |
| KIS 3 lần trả 50 kết quả | PASS | PASS |
| Video lookup đúng mã | PASS | PASS |
| Video thực sự tăng thời gian khi play | PASS | PASS |
| Có 3 mục OCR / ASR / Caption | PASS | PASS |
| Không có uncaught JS error trong suite chung | PASS | PASS |
| Search không bị drawer che ở 390px | FAIL | PASS |
| Search không bị drawer che ở 900px | FAIL | PASS |
| Search không bị drawer che ở 1024px | FAIL | PASS |
| Search không bị drawer che ở 1152px | FAIL | PASS |
| Search không bị drawer che ở 1180px | FAIL | PASS |
| Search không bị drawer che ở 1440px | PASS | PASS |

9/9 cặp KIS API giữ nguyên top 20 và thứ tự. Gallery/lookup giữ nguyên danh sách và thứ tự frame. API KIS: 0/9 lỗi mỗi bản; KIS qua browser: 0/3 lỗi mỗi bản. Tổng HTTP trong các benchmark tuần tự/tải nhỏ: 151 request mỗi bản, 0 lỗi. Con số này không bao gồm traffic media từ browser.

ZIP mới: 3 CSV, cả 3 dòng đúng frame đã chọn, không file rỗng. Đây là test tính năng mới, không có phần trăm cải thiện so với main.

Mở lại cùng preview trong TTL: main gọi lại context + evidence **2 request**, sau tối ưu **0 request**. Preview URL vẫn được gọi; bản sau còn dùng endpoint stream video, nên không được diễn giải là mọi request media đều giảm về 0. Video thật tiến được khoảng 0,56 giây trong kiểm tra play ngắn ở cả hai bản; chưa kiểm tra phát dài/đứt mạng/seek toàn video.

## Regression có kiểm soát và độ tin cậy

4 suite bổ sung dùng API thật đã PASS: workspace, keyboard/layout, responsive/settings/contrast, ZIP. 4 suite có response giả lập đã PASS sau chạy riêng: mất kết nối/stale response/duplicate Enter, QA answer-on-demand, sửa TRAKE, chặn export sai/lỗi. QA và TRAKE ở đây chỉ chứng minh hành vi UI, không chứng minh chất lượng model.

Không giấu lỗi harness: suite mất kết nối timeout 2 lần khi chờ Retry data; sau đó chạy riêng PASS và 5/5 vòng healthy -> outage đều trả đúng lỗi, 0 card, Search disabled. Runner đã tách session từng suite và mở about:blank để tránh startup/state chồng nhau. Chưa có bằng chứng đủ để quy kết lỗi sản phẩm cụ thể; cần giữ test lặp trong CI.

Suite media bổ sung từng mất session CLI 3 lần. Bỏ `new URL()` khỏi callback theo dõi request trong sandbox của CLI thì cùng luồng hoàn tất, gồm playback thật. Đây là vấn đề harness đã sửa; không được tính 3 lần đó là lỗi HTTP ứng dụng.

Hai trường gridRenders... trong dữ liệu QA fixture KHÔNG dùng làm bằng chứng render ở production: instrumentation chỉ tồn tại ở dev. Không công bố INP/LCP/CLS từ lần test này.

## Môi trường và giới hạn

Ngày đo: 17/09/2026. Cùng máy Windows, production Vite build, hai API độc lập tại 8021/8022 và hai web 5174/5175. Dùng SQLite backup một lần rồi copy sang hai DB; 310.301 frame, 873 video. Elasticsearch dùng chung, chỉ đọc: caption 310.212; OCR 291.909; ASR 181.714; tổng 783.835 document. Phiên đang dùng ở 8010/5173 và dự án cổng 8000 được giữ nguyên.

API benchmark: KIS auto, top_k=20; planning/expansion/reranker tắt giống nhau. UI KIS: top_k=50, tắt Expansion và Agent plan bằng Settings, reranker giữ mặc định bật ở cả hai bản. Không lấy hai nhóm này so chéo. HTTP latency bao gồm client + server; không phải thời gian thuần SQL.

Readiness sau tối ưu vẫn degraded: text ready, OpenCLIP endpoint unavailable, SigLIP remote inference chưa xác minh. Vì vậy chỉ kết luận tốc độ/fallback text và tính ổn định top-k trong môi trường này. Chưa có Recall@K/MRR có ground truth hợp lệ để kết luận search chính xác hơn. Không có kết luận về semantic search, QA model thật, TRAKE model thật hay concurrent ingestion. Không nhập lại data, không gọi job ingestion, không benchmark tải ghi lên ES.

N=3/5/9/10 ở một số luồng là ít. Chưa có confidence interval, test mạng WAN/mobile, nhiều người dùng thực tế hoặc soak test. Không dùng p95 mẫu nhỏ để cam kết SLA.

## Điểm cần làm tiếp

1. **Search KIS còn khoảng 5-6 giây**: khôi phục đúng OpenCLIP runtime, đo từng stage embedding/vector/text/rerank, rồi chạy lại golden set có nhãn video + cửa sổ frame. Chưa đạt bằng chứng tăng tốc tổng thể.
2. **Preview/context có dấu hiệu chậm hơn**: profile production khi mở/đóng modal, tách URL/stream/focus/React commit. Đo lại xen kẽ >=30 lượt với cùng media cache; chỉ sửa khi có trace xác định nguyên nhân.
3. **Gallery tail trên browser chưa giảm** dù API rất nhanh: tách tải thumbnail, decode ảnh, JS/layout và chi phí điều hướng; kiểm tra network waterfall và long task.
4. **Ổn định harness**: giữ test lỗi bootstrap theo hai trạng thái phiên mới/khôi phục, chờ hoàn tất điều hướng, tách context. Hai timeout ban đầu cần được theo dõi trong CI.
5. **Bốn acceptance còn mở**: semantic quality, QA thật, TRAKE thật, ingestion đồng thời. Các PASS fixture không đóng được bốn mục này.

## Tái chạy và bằng chứng

Scripts chính: `scripts/prepare_e2e_comparison.py`, `measure_e2e_comparison.py`, `measure_media_comparison.py`, `measure_search_comparison.py`, `check_e2e_comparison.js`, `check_live_search_comparison.js`, `check_live_media_comparison.js`, `run_browser_regressions.py`, `build_e2e_report.py`.

Chuẩn bị baseline worktree ở SHA main nêu trên, cùng dependencies và .env local. Chạy prepare một lần vào thư mục fixture mới; không tái sử dụng DB đã có query-run để tuyên bố snapshot sạch. Không chạy hai bộ benchmark đồng thời. Browser dùng `rtk proxy npx --yes --package @playwright/cli playwright-cli -s=<session riêng> run-code --filename=<script>`; mở about:blank trước, chờ lệnh trước xong.

Dữ liệu tổng hợp: [raw-results.json](raw-results.json). Screenshot và CSV/ZIP: `output/playwright/e2e-*-*.png`, `e2e-before-export.download`, `e2e-after-export.download`, `optimization-live-export.zip`. Các file này ở local, không cần gửi credentials/log backend.

Thời gian đo là kết quả quan sát trên máy này; không lấy số benchmark cũ ở phiên trước ghép với số mới để tính cải thiện.

# Nhận xét chính tả, ngữ pháp, logic và biên tập bài báo

- File được kiểm tra: `F:\MyData\samplepaper.pdf`.
- Phạm vi: toàn bộ 16 trang; số trang bên dưới theo PDF.
- Ngày ghi báo cáo: 19/09/2026.
- Trạng thái: chỉ đọc và báo cáo; không chỉnh sửa file PDF hay nội dung trên Prism.

Các lỗi tìm thấy tập trung ở trang 6–10. Dưới đây là câu/cụm gốc và đề xuất để tác giả tự sửa. Những đề xuất phụ thuộc ý nghĩa chuyên môn được ghi rõ điều kiện, không mặc định thay đổi nội dung.

## 1. Chính tả, viết hoa và tên thuật ngữ

Các nhận xét về cách viết được tách riêng dưới đây. Trường hợp phụ thuộc ý nghĩa chuyên môn không được coi là lỗi chính tả chắc chắn.

## 2. Ngữ pháp, cấu trúc câu và cách diễn đạt

### 2.1. Lỗi ngữ pháp và cấu trúc câu

| Trang / vị trí           | Nội dung gốc                                                                                               | Lỗi và đề xuất sửa                                                                                                                                                                                               |
| ------------------------ | ---------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 3 — Architecture         | “selects a diagnostic event to identify candidate videos, removes redundant frames”                        | Thiếu liên từ giữa hai động từ: “…identify candidate videos **and removes** redundant frames”.                                                                                                                   |
| 4 — Indexing and storage | “Dense keyframe and embeddings”                                                                            | Cụm danh từ bị thiếu thành phần. Nếu nói đến hai loại embedding đã mô tả: “Dense **keyframe and event embeddings**”.                                                                                             |
| 6 — §3.3                 | “We employ LLM as a query planner”                                                                         | Thiếu mạo từ: “We employ **an LLM** as a query planner”.                                                                                                                                                         |
| 6 — §3.3                 | “the LLM generates plan including”                                                                         | Thiếu mạo từ: “the LLM generates **a plan including**”.                                                                                                                                                          |
| 6 — §3.3                 | “are expressed in English view”                                                                            | Cấu trúc không tự nhiên: “are expressed **in English**” hoặc “are expressed **as English-language views**”.                                                                                                      |
| 6 — §3.3                 | “metadata evidences”                                                                                       | Trong ngữ cảnh này, _evidence_ không đếm được: “metadata **evidence**”.                                                                                                                                          |
| 6 — §3.3                 | “to ensure alignment evidences in metadata to better match OCR and ASR annotations”                        | Cấu trúc sai và ý chưa rõ. Nếu muốn nói bằng chứng khớp với annotation: “to ensure **that the metadata evidence aligns with OCR and ASR annotations**”.                                                          |
| 7 — System workflow      | “evidence-aware retrieval routing, event-aware reasoning”                                                  | Trong câu liệt kê bốn giai đoạn, thêm **and** trước “event-aware reasoning”.                                                                                                                                     |
| 7 — Stage 1              | “Each event we use LLM to generates k views”                                                               | Thiếu giới từ, mạo từ và sai động từ sau _to_: “**For each event, we use an LLM to generate k views**”.                                                                                                          |
| 7 — Stage 1              | “with Chain-Of-Thought (CoT) rules ensuring rich in information”                                           | “ensuring rich” thiếu thành phần. Đề xuất: “with **chain-of-thought (CoT) rules to ensure that the views are rich in information**”.                                                                             |
| 7 — Stage 1              | “subject, actions, object and evidence factors”                                                            | Nên thống nhất số ít/số nhiều: “**subjects, actions, objects, and evidence factors**”.                                                                                                                           |
| 7 — Stage 2              | “Iᵢ is the event importance measures the contribution…”                                                    | Hai vị ngữ nối sai: “Iᵢ is the **event importance score, which measures** the contribution…”.                                                                                                                    |
| 7 — Stage 2              | “Set of modality weights … for visual and text retrieval scores and set of metadata weights …”             | Câu thiếu động từ chính. Đề xuất: “**The modality weights … control visual and text retrieval scores, while the metadata weights … control OCR, caption, and ASR retrieval scores.**”                            |
| 7 — Stage 4              | “through temporal intent, anchor policy”                                                                   | Thiếu liên từ: “through **temporal intent and anchor policy**”.                                                                                                                                                  |
| 7 — §3.4                 | “In AI Challenge”                                                                                          | Trong cách dùng này nên là “In **the** AI Challenge”.                                                                                                                                                            |
| 7 — §3.4                 | “we introduces”                                                                                            | Sai hòa hợp chủ ngữ–động từ: “we **introduce**”.                                                                                                                                                                 |
| 7 — §3.4                 | “performs a event-level retrieval”                                                                         | Sai mạo từ. Sửa tối thiểu: “performs **an** event-level retrieval”; tự nhiên hơn: “performs **event-level retrieval**”.                                                                                          |
| 8 — Stage 2              | “and the score-margin signal mᵢ.”                                                                          | Vế này thiếu động từ: “and the score-margin signal **is mᵢ**.” Nếu đang định nghĩa cách tính, cần bổ sung định nghĩa tương ứng.                                                                                  |
| 8 — Stage 2              | “to computes score”                                                                                        | Sau _to_ dùng nguyên mẫu; thiếu mạo từ: “to **compute a score**”.                                                                                                                                                |
| 8 — Stage 2              | “DEV filters videos based on scores and save to set of videos V\*”                                         | Sai động từ và thiếu tân ngữ: “DEV filters videos based on **their scores and saves the selected videos to the set V\***.”                                                                                       |
| 8 — Stage 3              | “Second restricted video retrieval rather than a post filtering operation over the global search results.” | Đây là câu chưa hoàn chỉnh. Đề xuất: “**The second retrieval stage is restricted to the selected videos rather than merely filtering the global search results.**”                                               |
| 8 — Stage 3              | “The local NMS window and retains a computed candidates for each…”                                         | Câu sai cấu trúc; “a … candidates” cũng sai số ít/số nhiều. Cần xác nhận ý: nếu nói áp dụng NMS rồi giữ ứng viên, có thể viết “**DEV applies local NMS and retains the resulting candidates for each … group.**” |
| 8 — Stage 3              | “where the planner gap classes cᵢ ∈ C_timestamp”                                                           | Cụm sau _where_ chưa hoàn chỉnh. Đề xuất: “where **cᵢ denotes a planner-assigned gap class in C_timestamp**”.                                                                                                    |
| 8 — Stage 4              | “in both temporal directions with anchor frame”                                                            | Thiếu mạo từ; quan hệ với anchor chưa tự nhiên: “in both temporal directions **from the anchor frame**”.                                                                                                         |
| 8 — Stage 4              | “is scored using weighted event evidence, event coverage, then the resulting sequences are diversified…”   | Thiếu _and_ trong liệt kê và nối hai mệnh đề độc lập bằng dấu phẩy. Sửa: “…using **weighted event evidence and event coverage. The resulting sequences are then diversified**…”.                                 |
| 9 — Algorithm 1, Require | “Set of heuristic configures H”                                                                            | _Configures_ là động từ, không phù hợp ở đây: “**a set of heuristic parameters H**” hoặc “a set of heuristic configurations H”, tùy ý nghĩa.                                                                     |
| 9 — Algorithm 1, dòng 14 | “for all video v occurring…”                                                                               | Sai số ít/số nhiều: “**for each video v occurring…**”.                                                                                                                                                           |
| 10 — §4.1                | “We summarize and label AIC 2026 ground-truth benchmark”                                                   | Thiếu mạo từ: “…label **the AIC 2026 ground-truth benchmark**”.                                                                                                                                                  |
| 10 — §4.1                | “to make dataset for this experiments”                                                                     | Thiếu mạo từ và sai số: “to **create a dataset for these experiments**”.                                                                                                                                         |
| 10 — §4.1                | “We evaluated with Recall@k”                                                                               | Thiếu đối tượng đánh giá, đồng thời lệch thì với đoạn xung quanh. Đề xuất: “**We evaluate the methods using Recall@k**”.                                                                                         |
| 10 — §4.1                | “We compare 3 algorithm ATS, Vortex, and DEV”                                                              | Danh từ phải ở số nhiều; cần dấu phân cách: “We compare **three algorithms: ATS, Vortex, and DEV**”.                                                                                                             |

### 2.2. Cách diễn đạt cần rà lại

Các điểm này không phải tất cả đều là lỗi ngữ pháp bắt buộc.

| Trang  | Nội dung                                     | Nhận xét                                                                                                                                                      |
| ------ | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 7      | “It estimates firstly which events…”         | Tự nhiên hơn: “**It first estimates which events…**”.                                                                                                         |
| 7      | “the lack of videos in top K when searching” | Ý chưa rõ: thiếu video phù hợp hay thiếu sự đa dạng video? Nếu là thiếu video phù hợp: “**the limited coverage of relevant videos among the top-K results**”. |
| 10     | “Research question.”                         | Bên dưới có ba câu hỏi; nên dùng “**Research questions.**”                                                                                                    |
| 11, 12 | “Result”                                     | Khi trình bày nhiều kết quả, tiêu đề “**Results**” phù hợp hơn.                                                                                               |
| 14     | “Limits”                                     | Trong bài báo khoa học, “**Limitations**” tự nhiên hơn khi nói về hạn chế nghiên cứu.                                                                         |

### 2.3. Những đoạn cần ưu tiên rà lại

Những câu ở **trang 6–8** cần ưu tiên rà lại, đặc biệt câu bắt đầu bằng **“Semantic rewrites…”** và hai câu đầu của **Stage 3: Video-local retrieval**, vì cấu trúc hiện tại khiến ý nghĩa khó xác định chính xác.

## 3. Logic, phương pháp và diễn giải kết quả

Nhìn chung, ý tưởng hệ thống có mạch hợp lý, nhưng có một số **mâu thuẫn nội bộ**, **định nghĩa còn thiếu** và **kết luận vượt quá bằng chứng được trình bày**. Nhận xét này dựa trên bài báo, gồm lập luận, công thức, thuật toán và cách diễn giải kết quả; chưa đối chiếu mã nguồn hoặc dữ liệu thí nghiệm. Các điểm thiếu mô tả không đồng nghĩa với việc thuật toán thực tế sai.

### 3.1. Thuật toán phục hồi chuỗi chưa thống nhất: beam search hay greedy search? — Ưu tiên cao

- **Trang 2, cuối Related Work:** nói sử dụng “bounded beam search”.
- **Trang 8, Stage 3 và Stage 4:** đều mô tả “greedily expands”.
- **Trang 9:** chỉ gọi `RECOVERSEQUENCES`, không giải thích cách tìm kiếm.

Hai cách này không tương đương: greedy thường giữ lựa chọn tốt nhất tại mỗi bước; beam search có thể giữ nhiều chuỗi ứng viên.

**Đề xuất:** xác nhận thuật toán thực tế rồi thống nhất mô tả. Nếu kết hợp cả hai, cần nói rõ greedy ở bước nào, beam search ở bước nào và beam width bằng bao nhiêu.

### 3.2. Kết luận về TRAKE chưa được bằng chứng trong bài hỗ trợ — Ưu tiên cao

**Trang 13, §4.3** viết:

> “No TRAKE-effectiveness claim can be made from this benchmark”

Nhưng **Conclusion cùng trang** khẳng định hệ thống:

> “effectively narrowed candidate videos for complex KIS and TRAKE queries”

Các thí nghiệm mô tả chưa cung cấp đánh giá riêng cho TRAKE. Bảng điểm tổng cũng chưa tách đóng góp của tác vụ này.

**Đề xuất:** phân biệt giữa **hệ thống hỗ trợ TRAKE** và **hiệu quả TRAKE đã được kiểm chứng**. Nếu không có thí nghiệm bổ sung, nên giới hạn kết luận theo các tác vụ đã đánh giá.

### 3.3. Abstract và Bảng 4 báo hai bộ điểm nhưng chưa giải thích quan hệ — Ưu tiên cao

- **Trang 1:** RScore là **11.6, 13.4, 12.0**.
- **Trang 13, Bảng 4:** Official Score là **21.27, 27.20, 27.75**.

Chưa thể kết luận bộ nào sai vì có thể là hai thước đo khác nhau. Tuy nhiên, người đọc không biết RScore được định nghĩa thế nào và liên quan gì đến Official Score.

**Đề xuất:** định nghĩa cả hai, chỉ rõ nguồn và quan hệ; nếu thực chất cùng một loại điểm thì cần đối chiếu lại số liệu.

### 3.4. Các tập đánh giá chưa được liên kết rõ — Ưu tiên cao

| Thí nghiệm | Tập truy vấn được mô tả                                          |
| ---------- | ---------------------------------------------------------------- |
| §4.1       | 78 truy vấn: 58 KIS temporal + 20 QA                             |
| §4.2       | 58 truy vấn, chưa nói rõ có phải cùng 58 KIS trên không          |
| §4.3       | 98 truy vấn; trang 13 nhắc 23 QA, chưa mô tả đầy đủ phần còn lại |

Dùng tập khác nhau không sai, nhưng hiện chưa biết chúng được chọn như thế nào, có giao nhau không và vì sao thay đổi.

**Đề xuất:** thêm bảng mô tả từng tập: số lượng, loại tác vụ, tiêu chí chọn, annotation và mục đích sử dụng. Điều này cũng giúp giải thích chênh lệch kết quả giữa các bảng.

### 3.5. Recall@k chưa có cùng một định nghĩa rõ ràng xuyên suốt — Ưu tiên cao

- **Bảng 1:** chú thích chỉ nói “the result” xuất hiện trong k ứng viên đầu, chưa xác định kết quả đúng là video, frame hay chuỗi.
- **Bảng 2:** chưa nói rõ đánh giá exact frame, frame trong khoảng thời gian cho phép hay video.
- **Bảng 3:** phân biệt rõ video-level và exact-frame recall.

Vì vậy, **93.59% ở Bảng 1 và 0.1379, tức 13.79%, ở Bảng 2 không thể đem so trực tiếp** từ thông tin hiện có. Chênh lệch chưa chứng minh số liệu sai.

**Đề xuất:** mỗi bảng cần ghi rõ đơn vị xếp hạng, điều kiện khớp ground truth, dung sai thời gian nếu có, và Recall dùng tỷ lệ hay phần trăm.

Với QA, cũng cần phân biệt **tìm đúng bằng chứng/video** với **trả lời đúng câu hỏi**.

### 3.6. Thí nghiệm chưa tách được đóng góp riêng của cơ chế diagnostic event — Ưu tiên cao

Bảng 1 so DEV với ATS và Vortex, nhưng DEV đồng thời có nhiều thành phần: global probing, chọn video, local retrieval, NMS và phục hồi chuỗi.

Kết quả hiện tại hỗ trợ so sánh **toàn bộ cấu hình DEV**, chưa xác định phần cải thiện đến từ diagnostic-event selection hay từ việc truy xuất thêm trong bước local.

**Đề xuất:** nếu muốn khẳng định đóng góp của diagnostic event, cần so sánh cùng pipeline khi:

- Có và không có diagnostic prior.
- Chọn anchor theo diagnostic score so với importance hoặc quy tắc cố định.
- Giữ ngân sách truy xuất tương đương.

Đây là đề xuất bổ sung bằng chứng, không phải khẳng định thí nghiệm hiện tại vô giá trị.

### 3.7. Kết quả multi-view chưa chứng minh lợi ích của query expansion

**Bảng 2, trang 11–12:**

- OpenCLIP: multi-view làm giảm các chỉ số được báo cáo.
- SigLIP2: 3–5 views giữ nguyên R@10 và R@100, nhưng MRR giảm.
- Độ trễ tăng khi thêm views.

Phần diễn giải Bảng 2 hiện khá đúng. Tuy nhiên, nếu toàn bài muốn trình bày multi-view như một thành phần giúp cải thiện truy xuất thì bằng chứng này chưa hỗ trợ.

Ngoài ra, **§3.2 nói dùng query gốc cùng các rewrites**, còn §4.2 chưa làm rõ chế độ `View.` có giữ query gốc hay chỉ dùng rewrites.

**Đề xuất:** làm rõ thành phần query của từng chế độ và giới hạn tuyên bố: đây là khả năng hệ thống cung cấp, chưa cho thấy lợi ích trên tập thử nghiệm này.

### 3.8. Query expansion disabled khiến thí nghiệm prompt khó diễn giải

**Trang 12, §4.3:** tắt query expansion, nhưng vẫn ablate các thành phần liên quan đến semantic views, rewrite length và root rewrite.

Không nhất thiết mâu thuẫn nếu “query expansion” là một mô-đun bổ sung khác với planner rewrites. Bài hiện chưa phân biệt.

**Đề xuất:** giải thích rõ:

- Planner vẫn tạo những đầu ra nào?
- Đầu ra nào thực sự được retrieval sử dụng?
- “Query expansion disabled” cụ thể tắt bước nào?

Nếu một đầu ra không được sử dụng, việc bỏ chỉ dẫn tạo đầu ra đó khó được diễn giải như kiểm tra đóng góp truy xuất của nó.

### 3.9. Đường tính điểm cuối cùng chưa hoàn chỉnh — Ưu tiên cao

**Trang 5–6:**

- Công thức (6) chuẩn hóa điểm.
- Công thức (8) kết hợp `Svis`, `Smeta` và `Squal`.
- Công thức (9) tính RRF.
- Phần văn bản nói có thể thêm RRF vào hybrid ranker.

Nhưng chưa rõ:

- `Svis` và `Smeta` trong (8) đã chuẩn hóa theo (6) chưa?
- `Squal` là gì, tính như thế nào?
- Dùng `Shyb`, `SRRF` hay kết hợp cả hai để xếp hạng cuối?
- Các hệ số trong công thức liên hệ thế nào với trọng số do planner sinh?

**Đề xuất:** mô tả một chuỗi tính điểm thống nhất từ điểm từng nhánh đến điểm cuối; ghi rõ các chế độ tùy chọn và cấu hình dùng trong thí nghiệm.

### 3.10. Một số công thức thiếu quy ước cho trường hợp rỗng hoặc bằng 0

| Vị trí                                      | Trường hợp chưa được xử lý trong mô tả                                              |
| ------------------------------------------- | ----------------------------------------------------------------------------------- |
| Công thức (6), trang 5                      | Mẫu số bằng 0 nếu toàn bộ điểm của một nhánh bằng 0; tập ứng viên cũng có thể rỗng. |
| Công thức RRF (5), (9)                      | Frame chỉ xuất hiện trong một nhánh thì rank ở nhánh còn lại được xử lý thế nào?    |
| `uᵢ = 1 − số video phân biệt / Nᵢ`, trang 8 | Không xác định khi event không có ứng viên, tức `Nᵢ = 0`.                           |
| Algorithm 1, dòng 16                        | Với video không có ứng viên cho event i, phép `max` chạy trên tập rỗng.             |
| Algorithm 1, dòng 13                        | Nếu tất cả importance bằng 0 thì chuẩn hóa như thế nào?                             |

**Đề xuất:** bổ sung quy ước xử lý rõ ràng. Đây là thiếu sót của đặc tả trong bài; chưa thể suy ra code cũng mắc lỗi.

### 3.11. Điểm video selectivity có thể phản ánh frame trùng lặp hơn là khả năng phân biệt

**Trang 8:** `uᵢ = 1 − |Uᵢ| / Nᵢ`.

Ví dụ:

- 100 frame đều từ một video: `uᵢ = 0.99`.
- Chỉ một frame rất phù hợp từ một video: `uᵢ = 0`.

Điểm này đo mức độ tập trung ứng viên vào ít video, nhưng **không tự chứng minh event xác định đúng video**. Nhiều frame gần nhau của một cảnh lặp cũng có thể tạo điểm cao.

Trong pseudocode, `EVENTPROBE` được gọi **trước NMS**, nên sự trùng lặp có thể ảnh hưởng tín hiệu này.

**Đề xuất:** gọi đúng bản chất là tín hiệu tập trung video, giải thích vì sao dùng nó như heuristic cho diagnosticity và kiểm tra ảnh hưởng của trùng frame. Không nên coi đây là lỗi công thức chắc chắn vì nó còn được kết hợp với các tín hiệu khác.

### 3.12. Pseudocode thiếu định nghĩa quan trọng để tái lập

**Trang 8–9:**

- Chọn hai diagnostic events thành tập `D`, nhưng dùng chỉ số `d` ở dòng 18 và 29 mà chưa gán.
- Chưa rõ event thứ hai đóng vai trò gì ngoài bước NMS.
- `mᵢ`, tức score margin, chưa có công thức.
- `DIVERSEVIDEOSELECTION`, `ADAPTIVEMINMATCH`, `RECOVERSEQUENCES` và `TWOTIERRANK` chưa đủ mô tả.
- Chưa công bố rõ budget, ngưỡng NMS, hệ số heuristic và công thức chấm điểm chuỗi.

**Đề xuất:** định nghĩa `d`, vai trò của hai event và các quy tắc ảnh hưởng trực tiếp đến kết quả. Các chi tiết dài có thể đưa vào phụ lục.

### 3.13. Ràng buộc thời gian chưa đủ chặt trong biểu thức

**Trang 8:** điều kiện được ghi là:

`t(fⱼ) − t(fᵢ) ≤ Δ(cᵢ)`

Điều kiện này riêng lẻ vẫn cho phép chênh lệch âm. Văn bản có nói bảo đảm thứ tự thời gian, nhưng công thức chưa thể hiện điều đó.

**Đề xuất:** nếu hai event phải theo thứ tự, cần thể hiện cả cận dưới, ví dụ:

`0 ≤ t(fᵢ₊₁) − t(fᵢ) ≤ Δ(cᵢ)`

Dùng `<` hay `≤` tùy có cho phép cùng timestamp. Đồng thời cần định nghĩa:

- Ánh xạ `short/medium/long` sang ngưỡng.
- Cách xử lý `unknown`.
- Cách xử lý thiếu event trung gian.
- `m_min` có cho phép chuỗi chưa đầy đủ xuất hiện trong kết quả hay không.

### 3.14. Event embedding và một số metadata chưa có vai trò rõ trong retrieval

**Trang 4–5:** mô tả event embeddings, object labels và video captions. Nhưng:

- Công thức truy xuất tập trung vào frame embeddings.
- Công thức metadata chỉ có `asr`, `ocr`, `caption`.
- Chưa rõ `caption` bao gồm caption ảnh, caption video hay cả hai.
- Chưa rõ event embeddings tham gia bước nào.
- ASR và video caption được gắn với frame theo quy tắc nào?

**Đề xuất:** nối từng đầu ra preprocessing với bước retrieval sử dụng nó. Nếu thành phần chỉ được lưu trữ hoặc dùng cho giao diện thì nói rõ.

### 3.15. So sánh độ trễ cần cùng phạm vi đo

- **Bảng 1:** median khoảng **10.8–37.4 giây**.
- **Bảng 2:** mean khoảng **150–250 mili giây**.
- §4.1 dùng GPT-4o; §4.3 dùng GPT-4o mini.

Các khác biệt này có thể hợp lý vì các thí nghiệm đo những phần khác nhau. Tuy nhiên, bài chưa nói rõ latency có bao gồm tạo query bằng LLM, mạng, metadata retrieval và temporal recovery hay không.

**Đề xuất:** ghi phạm vi đo, phần cứng, caching, số lần chạy và chế độ song song. Nếu Bảng 2 không tính thời gian sinh views, cần tránh diễn giải đó là toàn bộ chi phí của multi-view.

Theo Bảng 1, DEV chậm khoảng **2.81 lần ATS** và **3.45 lần Vortex**. Vì vậy cần phân biệt **thu hẹp không gian ứng viên** với **giảm thời gian thực thi**; kết quả hiện tại chỉ cho thấy đánh đổi recall–latency.

### 3.16. Một số kết luận cần giới hạn hơn

- **Trang 13, §4.4:** điểm tương đối thấp hơn ở Round 3 chưa đủ để suy ra nguyên nhân là truy vấn phức tạp hơn. Cần dữ liệu về độ khó hoặc phân tích lỗi.
- **Conclusion:** camera transitions và visually ambiguous sequences được nêu như hạn chế quan sát được, nhưng chưa có ví dụ hay phân tích lỗi hỗ trợ.
- **Trang 4:** tuyên bố kết hợp hai embedding spaces tăng robustness chưa được Bảng 2 kiểm chứng, vì bảng chỉ đánh giá từng encoder riêng.
- Việc hệ thống đạt điểm tổng tốt không tách được đóng góp riêng của planner, DEV hay thao tác người dùng.

**Đề xuất:** phân biệt kết quả đã đo, quan sát định tính và giả thuyết giải thích.

### 3.17. Những phần kiểm tra thấy hợp lý

- MeanR của Full Prompt: `(35.71 + 51.70 + 70.41 + 73.81 + 77.21) / 5 = 61.768%`, làm tròn thành **61.77%** là đúng.
- Tổng điểm và các tỷ lệ phần trăm ở **Bảng 4** khớp.
- Diễn giải **Bảng 1** đúng với số liệu: ATS tốt hơn ở R@1/R@5; DEV tốt hơn từ R@10 nhưng chậm hơn.
- **Bảng 3:** các chênh lệch MeanR khớp với Full Prompt; đúng là 12 giá trị tăng và hai giá trị giảm.
- Nhận định “chưa có hiệu ứng thành phần nào có ý nghĩa thống kê” phù hợp với các CI và p-value được trình bày. Tuy nhiên, chưa có dữ liệu gốc để kiểm tra cách tính chúng.
- Phân biệt LLM lập kế hoạch trước retrieval với retrieval agent lặp ở Future Work là nhất quán.

### 3.18. Thứ tự ưu tiên xử lý

**Nên ưu tiên xử lý các mục 3.1–3.5, 3.9, 3.12 và 3.13 trước**, vì chúng ảnh hưởng trực tiếp đến việc người đọc hiểu đúng phương pháp và tin cậy kết luận.

## 4. Lỗi biên tập nhìn thấy trong lúc đọc

- **Trang 12, tiêu đề §4.3:** ghi **“(RQ2)”**, trong khi nội dung về thành phần prompt tương ứng với **RQ3** ở trang 10.
- **Trang 3, 5, 7, 9, 11, 13, 15:** đầu trang còn dòng **“Title Suppressed Due to Excessive Length”**.
- **Trang 10, §4.1:** sau “this experiments.” có số chú thích **4** và dấu chấm đứng tách bất thường; cần rà vị trí chú thích.
- **Trang 14, §5.1 “Limits”:** chưa có nội dung.
- **Tên phương pháp chưa thống nhất:** xuất hiện cả “Diagnostic-Event-Video-First”, “Diagnostic Event Video-first”, “DEV” và “DEV-first”.
- **References [7] và [29], trang 14 và 16:** URL hiển thị một phần giống công thức toán, mất dấu phân cách và có khoảng trắng bất thường. Đây là lỗi hiển thị có thể thấy trực tiếp trong PDF; chưa kiểm tra đích liên kết.

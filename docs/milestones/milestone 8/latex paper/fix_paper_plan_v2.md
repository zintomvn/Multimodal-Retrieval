# Changelog `aic_2026_paper_v1` - Temporal Search v2

## Phạm vi chỉnh sửa

Chỉ chỉnh sửa `\subsection{Temporal Search}` và Algorithm DEV trong
`aic_2026_paper_v1/samplepaper.tex`. Các section, bảng, hình, citations và
thí nghiệm khác không thay đổi.

## Các chỉnh sửa đã thực hiện

1. Sửa phần mở đầu và grammar:
   - Đổi tên nhất quán thành **Diagnostic-Event Video-First Search (DEV)**.
   - Thay câu mở đầu không chuẩn ngữ pháp bằng mục tiêu rõ ràng: tăng
     candidate-video coverage trong top-$K$.
   - Sửa `an event-level retrieval`, subject--verb agreement, và các câu thiếu
     động từ/chủ ngữ.

2. Sửa logic strategy để khớp flow triển khai:
   - Stage 1 là global, bounded event probing; kết quả không được coi là final
     temporal sequence.
   - Stage 2 chọn diagnostic event bằng planner prior + concentration + score
     margin + top confidence; sau đó chọn đa dạng candidate videos bằng
     diagnostic support, importance-weighted cross-event evidence, và coverage.
   - Stage 3 nói rõ local pass là **retrieval mới có giới hạn
     `allowed_video_ids`**, không phải post-filter global candidates.
   - Temporal recovery dùng diagnostic anchor, greedy expansion trái/phải trong
     cùng video, strict chronological order, edge-specific timestamp gap khi
     timestamp có sẵn, và `frame_idx` order-only khi không có timestamp.
   - Bổ sung fallback anchor: nếu diagnostic event vắng ở một video, dùng event
     available có importance cao nhất.
   - Stage 4 mô tả đúng components: calibrated evidence, weighted coverage,
     diagnostic support, missing-event/gap penalties, sequence NMS và per-video
     cap.

3. Rút gọn pseudocode:
   - Algorithm đổi thành hàm `DEV(...)` có input là event plans, importance,
     diagnostic priors, temporal graph, top-$K$, và configuration.
   - Còn 12 dòng thuật toán chính: global probe/calibration, diagnostic event
     selection, diverse video selection, local retrieval/NMS, recovery, scoring
     and diversification, rồi trả về top-$K$.
   - Bỏ các loop/biến score nội bộ quá chi tiết vì đã được mô tả trong system
     flow.

4. Bổ sung complexity:
   - Global probing: $O(MP)$ với $M$ events và $P$ global candidates/event.
   - Local temporal recovery: $O(VMb^2)$ với $V$ candidate videos và tối đa
     $b$ retained candidates/video/event; chi phí remote retrieval là dominant
     cost trong thực tế.

## Kiểm tra biên dịch

Đã thử biên dịch bằng `latexmk` và `pdflatex` trong thư mục
`aic_2026_paper_v1`. Không thể tạo PDF trong môi trường hiện tại do MiKTeX bị
lỗi môi trường trước khi đọc source LaTex:

- `latexmk`: không tìm thấy Perl script engine;
- `pdflatex`: không thể đọc attributes của
  `C:\Users\Mario\AppData\Roaming\npm\opencode.cmd\`, đồng thời MiKTeX báo
  Windows version unsupported.

Vì vậy không có PDF mới để giao. Source đã được kiểm tra cấu trúc: subsection,
Algorithm `DEV`, các `\For`/`\EndFor`, và `\Function`/`\EndFunction` đều cân
bằng. Sau khi sửa/cài lại MiKTeX (và Perl nếu dùng `latexmk`), compile từ thư
mục `aic_2026_paper_v1` bằng `pdflatex samplepaper.tex` hai lần hoặc dùng
Overleaf/TeX Live.

## Compilation update

The PDF was subsequently compiled successfully as `aic_2026_paper_v1/samplepaper.pdf`
(15 pages) with `pdflatex -> bibtex -> pdflatex -> pdflatex`. The build used an
isolated `PATH` to avoid the MiKTeX failure caused by the `opencode.cmd` entry.
The two pages containing Temporal Search and Algorithm~\ref{alg:dev} were
rendered and visually inspected; the revised subsection and 14-line algorithm
render correctly. Remaining undefined figure references are expected because
the three image blocks remain disabled for fast compilation.

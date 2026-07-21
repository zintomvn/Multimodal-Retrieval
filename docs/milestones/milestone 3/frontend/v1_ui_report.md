# ChatShasimi V1 UI Technical Report

## 1. Mục tiêu thiết kế

V1 UI của ChatShasimi được thiết kế cho bối cảnh thi AIC, nơi người dùng cần tìm frame nhanh, chọn frame chính xác và xuất file nộp bài với ít thao tác nhất. Giao diện lấy cảm hứng từ ChatGPT: sidebar trái cho workflow, thanh chuyển mode dạng pill, composer ở đáy màn hình, sidebar phải cho selected frames và export.

Các nguyên tắc chính:

- Ít chữ phụ, không hiển thị metadata kỹ thuật nếu không giúp ra quyết định.
- Search box là điểm thao tác chính, tự co giãn theo query.
- Sidebar có thể đóng mở để tối ưu diện tích xem frame.
- Loading state phải cho người dùng biết hệ thống đang search nhưng không làm nhiễu.
- Mock data được giữ để UI vẫn demo được khi backend hoặc dataset thật chưa sẵn sàng.

## 2. Frontend stack

Frontend hiện tại nằm trong `apps/web`.

```json
{
  "framework": "React 19",
  "language": "TypeScript",
  "bundler": "Vite 6",
  "styling": "CSS variables + responsive CSS",
  "icons": "lucide-react, dùng tối thiểu",
  "zip_export": "JSZip"
}
```

Các script chính:

```bash
npm run dev
npm run build
npm run preview
```

## 3. App shell và mode architecture

UI chính được điều khiển bởi 3 mode:

```ts
type AppMode = "Search" | "Auto" | "Chat";
```

Mỗi mode dùng chung composer và phần lớn state truy vấn, nhưng khác phần hiển thị trung tâm:

- `Search`: search thủ công, hiển thị grid frame.
- `Auto`: search có reasoning/trace và hỗ trợ workflow agent.
- `Chat`: giao diện chat QA, có upload file và reasoning trace.

Shell layout được chia thành 3 vùng:

```tsx
<main className={`chat-shell ${leftSidebarOpen ? "" : "left-collapsed"} ${rightSidebarOpen ? "" : "right-collapsed"}`}>
  <aside className="left-sidebar" />
  <section className="main-pane" />
  <aside className="right-sidebar" />
</main>
```

CSS grid điều khiển sidebar collapse:

```css
.chat-shell {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr) 320px;
  height: 100dvh;
  overflow: hidden;
}

.chat-shell.left-collapsed {
  grid-template-columns: 0 minmax(0, 1fr) 320px;
}

.chat-shell.right-collapsed {
  grid-template-columns: 260px minmax(0, 1fr) 0;
}
```

Ở breakpoint nhỏ, sidebar được chuyển thành overlay fixed để giữ vùng search đủ rộng.

## 4. State model

Các state chính trong `App.tsx`:

```ts
const [mode, setMode] = useState<AppMode>("Search");
const [queryType, setQueryType] = useState<QueryType>("KIS");
const [queryName, setQueryName] = useState(queryNameByType.KIS);
const [queryText, setQueryText] = useState(sampleQueries.KIS);
const [topK, setTopK] = useState(100);
const [frameColumns, setFrameColumns] = useState(5);
const [results, setResults] = useState<SearchResult[]>([]);
const [selected, setSelected] = useState<SubmissionRow[]>([]);
const [history, setHistory] = useState<SearchHistoryItem[]>([]);
```

State được giữ local vì UI hiện tại là một single-page tool. Điều này giảm overhead và giúp thao tác nhanh trong lúc thi.

## 5. Data contracts

Frontend dùng các type chính trong `apps/web/src/types.ts`.

```ts
export type QueryType = "KIS" | "QA" | "TRAKE";

export interface SearchResult {
  id: string;
  rank: number;
  video_id: string;
  video_code: string;
  frame_id: string | null;
  frame_idx: number | null;
  timestamp_ms: number | null;
  answer: string | null;
  score: number;
  sequence_frames: Array<{
    frame_id: string;
    frame_idx: number;
    video_code: string;
    score: number;
  }>;
  thumbnail_url: string | null;
  image_url: string | null;
  video_url: string | null;
}
```

Submission row dùng chung cho KIS, QA và TRAKE:

```ts
export interface SubmissionRow {
  query_name: string;
  query_type: QueryType;
  rank: number;
  video_code: string;
  frame_indices: number[];
  answer?: string | null;
}
```

## 6. API integration

API client nằm trong `apps/web/src/api/client.ts`.

Search route được chọn theo query type:

```ts
const path =
  input.queryType === "QA"
    ? "/api/retrieval/qa"
    : input.queryType === "TRAKE"
      ? "/api/retrieval/trake"
      : "/api/retrieval/search";
```

Request body:

```ts
body: JSON.stringify({
  dataset_id: input.datasetId,
  query_name: input.queryName,
  query_type: input.queryType,
  query_text: input.queryText,
  top_k: input.topK,
  profile: "competition_default",
  options: {
    use_query_expansion: input.useExpansion,
    use_metadata: input.useMetadata,
    delta_t_max_ms: 180000
  }
})
```

Nếu backend lỗi hoặc dataset chưa có, UI fallback sang mock results để demo không bị trắng màn hình.

## 7. Search flow

Flow chính của `submitSearch`:

```ts
async function submitSearch() {
  if (mode === "Chat") {
    submitChat();
    return;
  }
  if (!datasetId || !queryText.trim()) return;

  setLoading(true);
  setHasSearched(true);
  setDownloadUrl(null);

  try {
    const response = await runSearch({ datasetId, queryType, queryName, queryText, topK, useExpansion, useMetadata });
    const nextResults = response.results.length > 0 ? response.results : makeMockResults(queryType, queryName);
    setResults(nextResults);
    rememberSearch(nextResults);
    if (nextResults[0]) void openFrameContext(nextResults[0]);
  } catch {
    const nextResults = makeMockResults(queryType, queryName);
    setResults(nextResults);
    rememberSearch(nextResults);
    if (nextResults[0]) void openFrameContext(nextResults[0]);
  } finally {
    setLoading(false);
  }
}
```

Điểm quan trọng:

- `setHasSearched(true)` được gọi trước khi API hoàn tất để UI chuyển sang search state.
- Loading grid xuất hiện ngay trong vùng frame.
- Nếu có kết quả đầu tiên, sidebar phải tự có context frame tương ứng.

## 8. Frame card design

Frame card được tối giản để giảm nhiễu:

```tsx
<article className={`frame-card ${selected ? "selected" : ""}`} onClick={onOpen}>
  <div className="frame-thumb">
    <CloudFrameImage candidates={candidates} alt={`${result.video_code} frame ${frameText}`} eager={eager} />
    <span className="rank-chip">#{result.rank}</span>
  </div>
  <div className="frame-card-body">
    <div className="frame-title-row">
      <strong>{result.video_code}</strong>
      <span>{formatScore(result.score)}</span>
    </div>
    <p>Frame {frameText}</p>
    <div className="frame-actions">
      <button type="button" className="ghost-button">Video</button>
      <button type="button" className="select-button">{selected ? "Added" : "Pick"}</button>
    </div>
  </div>
</article>
```

Các metadata như `gallery: manual`, `frame_type`, `video_code` label kỹ thuật không được render ra UI. UI chỉ giữ lại thông tin cần để ra quyết định: video id, frame id, score, rank và action.

## 9. Composer giống ChatGPT

Composer là điểm nhập chính cho Search, Auto và Chat.

Auto-resize dùng `scrollHeight`:

```ts
useEffect(() => {
  const element = composerRef.current;
  if (!element) return;
  element.style.height = "0px";
  const nextHeight = Math.min(Math.max(element.scrollHeight, 44), 220);
  element.style.height = `${nextHeight}px`;
}, [attachedFiles.length, mode, queryText]);
```

CSS ẩn scrollbar xấu khi query dài:

```css
.composer textarea {
  max-height: 220px;
  resize: none;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: none;
  word-break: break-word;
}

.composer textarea::-webkit-scrollbar {
  display: none;
}
```

Lớp fade dưới composer giúp vùng đáy màn hình nhẹ hơn:

```css
.composer-wrap::before {
  content: "";
  position: absolute;
  left: 50%;
  bottom: 0;
  width: min(1040px, calc(100vw - 10px));
  height: 138px;
  transform: translateX(-50%);
  pointer-events: none;
  background: linear-gradient(
    to top,
    rgba(var(--bg-panel-rgb), 0.98) 0%,
    rgba(var(--bg-panel-rgb), 0.82) 48%,
    rgba(var(--bg-panel-rgb), 0) 100%
  );
  filter: blur(8px);
}
```

## 10. Intelligence và Settings

V1 dùng input số thay vì slider để người thi nhập chính xác thông số.

```tsx
<label>
  Frames per row
  <input
    type="number"
    min={1}
    max={8}
    step={1}
    value={frameColumns}
    onChange={(event) => updateFrameColumns(Number(event.target.value))}
  />
</label>
```

Clamp value bảo vệ UI:

```ts
function clampNumber(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

function updateFrameColumns(value: number) {
  setFrameColumns(Math.round(clampNumber(value, 1, 8)));
}

function updateTopK(value: number) {
  setTopK(Math.round(clampNumber(value, 1, 100)));
}
```

Settings cũng dùng input số cho các search coefficients:

```tsx
<input
  type="number"
  min={0}
  max={1}
  step={0.01}
  value={weights[key]}
  onChange={(event) => updateWeight(key, Number(event.target.value))}
/>
```

## 11. Loading animation

Khi bấm Search, UI không dùng spinner lớn ở giữa mà dùng ghost frame grid, mô phỏng frame đang được load.

```tsx
function SearchLoadingStage({ frameColumns }: { frameColumns: number }) {
  const columns = Math.round(clampNumber(frameColumns, 1, 8));
  const ghostCount = Math.max(9, columns * 3);

  return (
    <div className="search-loading-stage" aria-label="Searching">
      <div className="search-loading-grid" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
        {Array.from({ length: ghostCount }).map((_, index) => (
          <span key={index} className="search-loading-frame" style={{ animationDelay: `${index * 80}ms` }} />
        ))}
      </div>
    </div>
  );
}
```

Animation chỉ dùng `opacity` và `transform` để giảm jank:

```css
@keyframes frame-flicker {
  0%,
  100% {
    opacity: 0.42;
    transform: translateY(0) scale(0.985);
  }
  50% {
    opacity: 0.9;
    transform: translateY(-2px) scale(1);
  }
}
```

Reduced motion được hỗ trợ:

```css
@media (prefers-reduced-motion: reduce) {
  .skeleton-card,
  .search-loading-frame,
  .search-loading-frame::after,
  .spin-icon {
    animation: none;
  }
}
```

## 12. Reasoning UI cho Auto và Chat

Reasoning trace dùng `details/summary`, có thể đóng mở tự nhiên như ChatGPT.

```tsx
<details className={`reasoning-disclosure ${compact ? "compact" : ""}`} open={running}>
  <summary>
    <span>{running ? "Thinking" : "Reasoning"}</span>
    <small>{doneCount}/{steps.length} checks</small>
  </summary>
  <div className="reasoning-steps">
    {steps.map((step) => (
      <div className={`reasoning-step ${step.status}`} key={step.title}>
        <small className="reasoning-status">
          {step.status === "done" ? "Done" : step.status === "running" ? "Running" : "Waiting"}
        </small>
        <span>
          <strong>{step.title}</strong>
          <small>{step.detail}</small>
        </span>
      </div>
    ))}
  </div>
</details>
```

Không dùng icon trong reasoning để tránh nhiễu thị giác.

## 13. Selected frames và export

Khi user chọn frame, frontend tạo `SubmissionRow`.

```ts
const row: SubmissionRow = {
  query_name: queryName,
  query_type: queryType,
  rank: selected.length + 1,
  video_code: result.video_code,
  frame_indices:
    queryType === "TRAKE" && result.sequence_frames.length > 0
      ? result.sequence_frames.map((item) => item.frame_idx)
      : result.frame_idx === null
        ? []
        : [result.frame_idx],
  answer: queryType === "QA" ? result.answer ?? "" : null
};
```

Rank được normalize lại sau mỗi lần thêm/xóa:

```ts
function normalizeRanks(rows: SubmissionRow[]): SubmissionRow[] {
  const counts = new Map<string, number>();
  return rows.map((row) => {
    const count = (counts.get(row.query_name) ?? 0) + 1;
    counts.set(row.query_name, count);
    return { ...row, rank: count };
  });
}
```

Nút remove trong selected frame dùng CSS cross thay vì text `X`:

```css
.selected-remove span::before,
.selected-remove span::after {
  content: "";
  position: absolute;
  top: 6px;
  left: 2px;
  width: 10px;
  height: 1.7px;
  border-radius: 999px;
  background: currentColor;
}
```

## 14. Local zip fallback

Nếu backend export không khả dụng, frontend dùng JSZip để tạo zip local.

```ts
async function exportLocalSubmission() {
  const zip = new JSZip();
  const folder = zip.folder("submission");
  if (!folder) throw new Error("Cannot create submission folder");

  const grouped = new Map<string, SubmissionRow[]>();
  selected.forEach((row) => {
    const filename = queryFilename(row.query_name);
    grouped.set(filename, [...(grouped.get(filename) ?? []), row]);
  });

  grouped.forEach((rows, filename) => {
    folder.file(filename, `${rows.map(submissionCsvLine).join("\r\n")}\r\n`);
  });

  const blob = await zip.generateAsync({ type: "blob" });
  const url = URL.createObjectURL(blob);
  setDownloadUrl(url);
}
```

CSV không có header, đúng yêu cầu nộp bài.

```ts
function submissionCsvLine(row: SubmissionRow): string {
  const cells: Array<string | number> = [row.video_code, ...row.frame_indices];
  if (row.query_type === "QA") cells.push(row.answer ?? "");
  return cells.map(csvCell).join(",");
}
```

## 15. Responsive strategy

Các breakpoint chính:

- `1280px`: giảm sidebar/grid columns.
- `1180px`: right sidebar chuyển thành fixed overlay.
- `900px`: left sidebar chuyển thành fixed overlay, main pane còn 1 column.
- `520px`: frame grid về 1 column, Intelligence popover chuyển fixed full-width gần composer.

Ví dụ:

```css
@media (max-width: 1180px) {
  .right-sidebar {
    position: fixed;
    top: 58px;
    right: 0;
    bottom: 0;
    width: min(360px, 88vw);
    transform: translateX(0);
  }

  .chat-shell.right-collapsed .right-sidebar {
    transform: translateX(100%);
    opacity: 0;
  }
}
```

Mobile composer:

```css
@media (max-width: 520px) {
  .frame-grid,
  .skeleton-grid {
    grid-template-columns: 1fr !important;
  }

  .intelligence-popover {
    position: fixed;
    left: 14px;
    right: 14px;
    bottom: 92px;
    width: auto;
  }
}
```

## 16. Theme tokens

Theme dùng CSS variables, không hard-code màu trong component.

```css
:root {
  --bg-base: #f7f7f8;
  --bg-panel: #ffffff;
  --bg-panel-rgb: 255, 255, 255;
  --text-1: #0d0d0d;
  --text-2: #5f5f5f;
  --accent: #087f5b;
  --accent-strong: #065f46;
  --radius: 16px;
  --radius-sm: 12px;
}

.theme-dark {
  --bg-base: #212121;
  --bg-panel: #2f2f2f;
  --bg-panel-rgb: 47, 47, 47;
  --text-1: #f5f5f5;
  --accent: #12a873;
}
```

Accent green dùng solid state thay vì nền xanh nhạt để UI hiện đại hơn.

## 17. Accessibility và UX details

Các điểm đã áp dụng:

- Button sidebar có `aria-label`.
- Composer có `aria-label` riêng cho Chat và Search.
- Loading stage có `aria-label="Searching"`.
- File remove có `aria-label` theo tên file.
- Selected remove chỉ vẽ icon bằng CSS nhưng vẫn có `aria-label`.
- Motion có fallback cho `prefers-reduced-motion`.
- Text trong card và sidebar dùng `overflow-wrap`, `text-overflow`, `min-width: 0` để tránh tràn.

## 18. Validation

Các kiểm tra đã chạy trong quá trình hoàn thiện V1 UI:

```bash
npm run build
```

Kết quả: build production pass.

Playwright regression đã kiểm các viewport:

```txt
1440x920
1180x820
900x800
768x900
390x844
320x720
```

Các case đã kiểm:

- Không horizontal overflow ở desktop/mobile.
- Long query không làm composer lộ scrollbar xấu.
- Intelligence popover nằm trong viewport.
- Settings và Intelligence dùng input number.
- Selected frame có thể add/remove.
- Right sidebar overlay đóng mở được.
- Search loading stage xuất hiện khi API retrieval đang pending.
- Không còn text thừa: `Manual search`, `AIC zip`, `submission/*.csv`, slider range, score metadata strip.

Kết quả Playwright:

```json
{
  "failures": [],
  "count": 0
}
```

## 19. Hướng mở rộng

Các slot hiện được giữ cho future features:

- Filter nâng cao trong Intelligence.
- Reranker hoặc model profile selector.
- Auto agent thật thay cho mock trace.
- QA video upload pipeline nối backend.
- Persist history bằng localStorage hoặc backend user session.

V1 hiện ưu tiên giao diện thi đấu: ít chữ, nhanh, responsive, dễ chọn frame và xuất zip.

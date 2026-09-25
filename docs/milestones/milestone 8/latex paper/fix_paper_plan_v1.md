# Kế hoạch rà soát và chỉnh sửa `aic_2026_paper` (v1)

## Phạm vi và nguyên tắc sửa

Đã đối chiếu `samplepaper.tex`, triển khai temporal search tại
`apps/backend/app/modules/retrieval/temporal/`, cấu hình/kết quả benchmark
`exp1_temporal_search_v2`, và hai paper tham khảo AIthena-Vision và Vortex.
Ưu tiên sửa theo thứ tự: (P0) sai khác giữa paper và code/kết quả, (P1) giao
thức thực nghiệm, (P2) đóng góp và cách trình bày, (P3) ngôn ngữ/độ dài.

Không đưa chain-of-thought vào paper. Mô tả planner bằng *structured outputs*
(events, views, weights, priors, edges) và chỉ báo cáo prompt/schema cần thiết
để tái lập thí nghiệm.

## P0 - Temporal search: cần khớp lại với code

### Các sai khác cần sửa

| Vị trí hiện tại | Vấn đề | Sửa theo implementation đã kiểm tra |
|---|---|---|
| Sec. 3, mở đầu Temporal Search | Viết "for KIS and TRAKE" nhưng RQ1 đánh giá 58 temporal KIS + 20 QA; benchmark không có TRAKE. | Tách **phạm vi triển khai** (temporal KIS/TRAKE-capable) khỏi **phạm vi đánh giá** (78 query, không có TRAKE). Không suy diễn hiệu quả TRAKE từ bảng RQ1. |
| Stage 1 | `C_i^g=R(P_i;b_g,V_all)` và "heuristic coefficients" quá mơ hồ. | Nêu: mỗi event được global-probe bằng primary English view; candidate score trộn rank, absolute semantic score (nếu có), và hybrid score. Budget bị chặn; source code dùng `P=max(probe_top_k, top_k+output_video_buffer)`, cap global pool là 160. Ghi toàn bộ hệ số/budget vào bảng configuration hoặc supplementary. |
| Stage 2 | `u_i=1-|U_i|/N_i` được gọi là video selectivity nhưng không mô tả margin; `m_i` chưa định nghĩa; paper nói giữ hai event nhưng pseudocode không cố định số lượng. | Định nghĩa probe quality từ **concentration, score margin, top confidence**; diagnostic score là prior của planner + probe quality. Không hard-code "two" nếu config/code có thể thay đổi; ghi `TopDiagnosticEvents` với số event cấu hình. |
| Stage 2/Algorithm 1 | `x_{v,i}=max_{f in C_i^g}...` không xử lý video không có candidate (max rỗng); `A_v=x_{v,d}` ký hiệu `d` chưa định nghĩa rõ. | Đặt `x_{v,i}=0` khi tập rỗng; định nghĩa `d` là diagnostic-event index; chuẩn hoá importance trước aggregate. Mô tả diversity reservation theo event để tránh nhiều frame cùng một video chiếm pool. |
| Stage 3 | Câu "The local NMS window and retains a computed candidates" không có động từ; nói chỉ dùng timestamp gap. | Viết lại: local retrieval là **một lần truy hồi mới có `allowed_video_ids`**, không phải post-filter của global results; local views gồm tối đa hai English views và một Vietnamese SigLIP2 view; NMS giữ tối đa 12 candidates/video/event. Khi có timestamp dùng ms; không có timestamp chỉ kiểm tra `frame_idx` tăng dần và không quy đổi ms sang frame. |
| Stage 3 | Điều kiện chỉ ghi `t(f_j)-t(f_i)<=Delta(c_i)` và không nêu strict order/unknown gap. | Dùng `t(next)>t(prev)`; upper bound chỉ áp dụng khi edge có timestamp-compatible gap. Classes: short=45 s, medium=120 s, loose=300 s, unknown=unbounded. |
| Stage 4 + Algorithm 1 | Gọi "bounded beam search" ở Architecture nhưng code DEV dùng greedy Vortex-style anchor expansion; `RecoverSequences`/`TwoTierRank` che mất cách làm thực tế. | Thay bằng: với mỗi anchor của diagnostic event, greedy mở rộng trái/phải bằng candidate compatibility tốt nhất trong cùng video; nếu diagnostic event vắng, dùng high-importance available event làm fallback anchor và áp missing-event penalty. Sau đó score evidence + coverage + strong coverage + diagnostic support - missing-event/compactness penalties, sequence-NMS và per-video cap. |
| Introduction/Architecture/Conclusion | Claim "annotation-aware temporal refinement" và "adaptive sampling" không được triển khai như phần DEV mô tả; code có dynamic context samples nhưng **score 0, không phải matched event**. | Chỉ giữ claim nếu chỉ rõ dynamic samples được thêm sau top-K để hỗ trợ xem xét, không làm thay đổi score/coverage. Nếu không có thí nghiệm về chúng, rút claim khỏi abstract và contribution. |
| Pseudocode | `h_DD_i` thiếu khoảng cách; `w_A,w_X,w_C`, `CalibrateCandidates`, `TemporalNMS`, `AdaptiveMinMatch`, `TwoTierRank` chưa có định nghĩa. | Chuẩn hoá notation, thêm bảng ký hiệu/hyperparameters, hoặc rút pseudocode về 4 stage có các operator được định nghĩa trong text. |

### Đoạn mô tả DEV nên thay thế (bản nháp tiếng Anh)

> DEV first probes each planned event globally with a bounded candidate budget.
> It selects diagnostic events by combining the planner prior with probe
> concentration, score margin, and top confidence. Candidate videos are ranked
> by diagnostic evidence, importance-weighted cross-event evidence, and event
> coverage, while event-wise reservation promotes video diversity. DEV then
> performs a second, video-restricted retrieval pass for every event. Within
> each selected video, it expands a diagnostic anchor greedily to the best
> chronologically compatible events on either side. Timestamps enforce strict
> order and, when available, edge-specific upper-gap constraints; otherwise
> only increasing frame indices are enforced. Final sequence scores combine
> calibrated evidence, weighted coverage, diagnostic support, and penalties for
> missing events and excessive temporal gaps.

### So sánh công bằng với AIthena và Vortex

- ATS nên được mô tả đúng theo AIthena: independent candidate retrieval,
  partial matching, beam construction, chronological/gap constraints và
  weighted-average sequence score.
- Vortex baseline là k-context, **anchor-centered greedy reranking**: từng
  anchor được boost bởi best compatible before/after events trong cùng video;
  missing context đóng góp 0. Không gọi đó là full sequence alignment.
- DEV không gọi ATS hay legacy Vortex filter, nhưng local constructor có tính
  chất Vortex-style. Nêu rõ điều này để tránh claim baseline không đúng.

## P0 - Sửa protocol, research questions và kết quả

### Research questions đề xuất

1. **RQ1 (temporal strategy):** Under an identical planner, retrieval backend,
   candidate budget, and evaluation set, how do ATS, Vortex k-context, and DEV
   trade off video recall, sequence/event alignment, and latency?
2. **RQ2 (retrieval representation):** With a fixed query formulation and
   candidate budget, how do OpenCLIP, SigLIP2, and their RRF fusion affect
   semantic retrieval? Separately, what is the marginal effect of 0/3/5/7
   planner-generated views?
3. **RQ3 (planner):** Which structured planner fields materially affect
   retrieval, and are effects statistically distinguishable under a paired,
   repeated evaluation? State this as prompt-sensitivity, not isolated causal
   importance.

### Vấn đề trong setup hiện tại

| Hạng mục | Vấn đề | Hành động |
|---|---|---|
| Tập query RQ1 | 78 queries trộn 58 temporal KIS và 20 QA; temporal alignment không đồng nghĩa QA. | Báo cáo overall **và** KIS/QA strata. Nếu QA không dùng multi-event temporal plan, loại khỏi primary temporal result hoặc giải thích rõ task routing. |
| Target metric | Footnote nói Recall@k nhưng không nói video-level hay exact-frame; bảng metric JSON có `exact_frame_hits` và `sequence_frame_hits`. | Đặt metric chính là video-level Recall@k (định nghĩa hit), thêm exact-frame/sequence alignment success làm metric secondary với tolerance và label policy. |
| So sánh latency | Median có nhưng thiếu hardware, database state, số run/warm-up, có/không LLM planning và aggregation policy. | Một bảng shared setup: machine/GPU, model/version, corpus size, indexes, top-k, planner model/temp, cache, number of repetitions, median + IQR/p95. Phân tách planning, retrieval, temporal reranking và end-to-end latency. |
| RQ1 fairness | DEV intrinsically có local second retrieval nên latency cao; các budget chưa được report bên cạnh baseline. | Báo cáo global/local calls, candidate frames/video budget, candidates after NMS, selected videos và sequence pool. Có thể thêm budget-matched DEV (hoặc ghi rõ comparison is effectiveness--cost, not equal compute). |
| RQ2 confound | Số view thay đổi đồng thời strategy; current table bold toàn bộ từng encoder không giúp so sánh toàn bảng. RQ đề cập model choice nhưng không có RRF row dù system dùng RRF. | Tách factorial ablation: encoder (OpenCLIP/SigLIP2/RRF) x views (0/3/5/7), cùng queries/candidate depth. Bold **global best per metric**, thêm delta vs full query and CI. |
| RQ3 inconsistency | RQ3 viết 98 queries nhưng RQ1 là 78; text nói `gpt-4o-mini` còn RQ1 uses GPT-4o. Các component removal có thể làm output schema invalid. | Giải thích dataset split và reason; validate schema and report invalid/repair rate per condition. Freeze planner/retrieval versions; publish seeds, outputs and run manifest. |
| Official score | % và round scores đúng số học, nhưng source/protocol/challenge status chưa đủ để coi là controlled experiment. | Giữ thành **Official challenge outcome**, tách khỏi ablation results; ghi official rules, round/date, task mix, score computation, whether manual interaction was permitted. Không dùng kết quả này chứng minh riêng DEV. |

### Bảng RQ1 nên sửa

- Đổi caption thành: `Video-level Recall@k (%) and end-to-end latency (ms) on the 78-query temporal-routing benchmark.` Chỉ dùng nếu tất cả 78 thật sự routed temporal.
- Thêm `R@1`, `R@5`, `R@10`, `R@100`, `Median`, `IQR/P95`, `# global/local retrieval calls`, `candidate-video budget`. Có thể chuyển các rank trung gian sang appendix để bảng main gọn hơn.
- Hiện conclusion "DEV outperforms ... from Recall@10 onward" được số liệu hỗ trợ, nhưng cần thêm paired CI/significance trước dùng từ *outperforms*. Nếu chưa có, dùng *achieves higher observed recall*.
- Nêu crossover: ATS tốt nhất early rank (R@1=41.03, R@5=64.10); DEV cao nhất từ R@10 (76.92) đến R@100 (93.59) nhưng latency 37,410 ms, cao hơn Vortex 10,832.5 ms và ATS 13,320 ms.

### Bảng RQ2 nên sửa

- Các `\textbf{}` hiện tại bold nhiều hàng trong từng encoder; chỉ bold một best overall theo cột hoặc dùng shading/arrow.
- `View.` đổi thành `Multi-view`; `Lat.` đổi `Latency`; giải thích `n=1` là full query và `n>1` là số LLM views.
- Kết quả không cho thấy multi-view cải thiện over full query: với SigLIP2, R@10/R@100 giữ nguyên ở 3/5 view nhưng MRR giảm; OpenCLIP giảm rõ. Sửa framing từ claim lợi ích thành cost/robustness analysis.
- Thêm RRF baseline (vì architecture claims dual-encoder RRF), confidence interval/repetitions và corpus/top-k fixed setup.

### Bảng RQ3 nên sửa

- Bảng 14 component quá dày cho main paper; giữ Full + các removal có effect size lớn nhất, đưa full 14 rows vào appendix/supplement.
- Dòng Full Prompt không nằm trong table; thêm hàng `Full (reference)`, có MeanR và `--` ở delta/CI/p.
- Vì mọi CI chứa zero và p>0.05, abstract/conclusion không được claim bất kỳ component nào hiệu quả. C7/C9 chỉ là directional observations.
- 14 tests cần nêu unadjusted; current statement correction will not change conclusion là hợp lý nhưng giữ ở limitation/appendix ngắn.

## P1 - Cấu hình dùng chung cho ba thực nghiệm

Tạo subsection `Common Experimental Protocol` trước RQ1, dùng cùng một bảng
thay vì lặp setup. Đóng băng và report các biến sau:

| Nhóm | Thiết lập chung cần công bố |
|---|---|
| Data/labels | dataset release/hash, số video/keyframe, query IDs, task strata, GT unit (video/frame/sequence), duplicate/invalid-label policy |
| Retrieval corpus | snapshot date, AutoShot/sampling parameters, caption/OCR/ASR versions, Milvus/Elasticsearch collection/index version |
| Models | OpenCLIP exact checkpoint, SigLIP2 exact checkpoint, LLM model/version, decoding temperature/max tokens, prompt/schema version |
| Search | top-k output, per-view retrieval depth, RRF constant/weights, metadata boosts, filters, reranker, cache state |
| Temporal | order unit, gap classes, NMS windows, per-video cap, min-match and diagnostic-event count |
| Runtime | hardware, GPU/CPU/RAM, service concurrency, warm-up, network/remote LLM inclusion, repetitions and latency statistic |
| Statistics | primary metric, paired unit (query), bootstrap/sign-flip settings, CI and multiple-testing rule |

RQ-specific factors mới được thay đổi: RQ1=temporal algorithm; RQ2=encoder và
view count; RQ3=prompt component. Nếu một factor thay đổi bắt buộc (ví dụ DEV
second-pass), report nó như compute cost chứ không giấu trong setup.

## P1 - Cách viết multimodal retrieval và đóng góp

- Dùng nhất quán **multimodal video retrieval** (không xen kẽ `multi-modal`,
  `multimodel`, `multimedia search` khi không có chủ ý). Dùng `vision-language`
  với en dash trong LaTex là `vision--language`.
- Phân biệt rõ: **modality** = visual/caption/ASR/OCR; **retrieval branch** =
  vector vs metadata; **evidence source** = caption, OCR, ASR; **encoder** =
  OpenCLIP/SigLIP2. `text retrieval` không đồng nghĩa toàn bộ metadata nếu object labels/video captions cũng được index.
- Architecture currently states both max-view aggregation and RRF; xác định score thực sự được dùng để sort, tránh trình bày `S_hyb` và `S_RRF` như hai final scores cạnh tranh. Thêm thứ tự pipeline: retrieve -> normalize/calibrate -> fuse -> rerank/diversify -> temporal (when applicable).
- Paper cần nói frame/event representation nào thật sự được truy hồi trong mỗi experiment. Data Processing tạo mean event embeddings nhưng retrieval section/code benchmark chủ yếu thao tác keyframe candidates; nếu event embedding không được dùng online, chuyển chi tiết aggregation sang future work hoặc document its exact use.
- Contributions nên giảm còn 2--3 claims kiểm chứng được: (i) structured planner + evidence routing, (ii) DEV coarse-to-fine temporal strategy, (iii) reproducible empirical trade-off. Không claim tất cả component là novelty nếu chỉ tích hợp từ prior systems.

## P2 - Rút gọn và tổ chức lại

| Phần | Đề nghị |
|---|---|
| Abstract | Rút xuống problem -> method -> 1--2 measured findings -> scope. Bỏ/giảm phrase "without requiring large-scale task-specific training" nếu không benchmark training alternative. Thay `outperforms` bằng observation có scope. |
| Introduction | Có ba lần lặp lại contributions/results (mở đầu, contribution paragraph, cuối introduction). Giữ một contribution list và một result sentence. |
| Related Work | Hiện dài và có nhiều claim tuyệt đối như "generally not explicitly determined". Nhóm thành: multimodal indexing/fusion; LLM query planning; temporal alignment. Mỗi nhóm 2--3 citations + precise gap. |
| Data Processing | Mô tả tool/version rất chi tiết nhưng không liên kết experiment. Giữ pipeline và outputs/indexes ở main; chuyển threshold, storage and model implementation detail sang appendix. |
| Semantic/Metadata Retrieval | Có code LaTex cũ comment-out rất lớn. Xoá khỏi clean paper (history nằm Git); giữ một formulation final. |
| Planner | Bốn stage có grammar yếu và lặp definitions. Đổi sang schema table + one short paragraph; tránh CoT claim. |
| DEV | Đây là phần đóng góp; giữ 4-stage concise text + clean algorithm + one scoring equation. Đưa budgets/hyperparameters, fallback/dynamic sampling vào appendix. |
| UI | Một đoạn + figure là đủ; nếu paper page-limited, chuyển figure UI sang appendix/demo. |
| Experiments | Bỏ toàn bộ block RQ3 cũ đang comment; đưa full ablation and extended tables appendix. |
| Conclusion | Không lặp full architecture. Nêu findings, early-rank/latency limitation, no-TRAKE limitation, then future work. |

## P2 - Grammar, vocabulary và LaTex cần sửa ngay

| Vị trí | Hiện tại | Đề nghị |
|---|---|---|
| Abstract | `Diagnostic-Event-Video-First (DEV)` | `Diagnostic-Event Video-First (DEV)` hoặc định nghĩa tên có dấu nối nhất quán toàn paper. |
| Related Work | `multimodel fusion` | `multimodal fusion`. |
| Planner Stage 1 | `Each event we use LLM to generates k views with Chain-Of-Thought (CoT) rules ensuring rich in information...` | `For each event, the planner generates k retrievable views that preserve the subject, action, object, and observable attributes.` |
| Planner Stage 2 | `I_i is the event importance measures...`; `Set of modality weights...` | `I_i denotes event importance, i.e., its contribution to the target result. The planner also outputs modality and metadata-source weights.` |
| Planner Stage 4 | `gap class to get information about...` | `gap class that expresses a relative temporal constraint without predicting an exact duration.` |
| DEV opener | `In AI Challenge, to solve the lack of videos in top K when searching, we introduces...` | `To improve candidate-video coverage at rank K, we propose DEV, a coarse-to-fine temporal retrieval strategy for temporal queries.` |
| DEV Stage 2 | `combines ... to computes score` | `combines ... to compute the diagnostic score`. |
| DEV Stage 3 | `Second restricted video retrieval rather than a post filtering operation...` | `The second stage performs a new video-restricted retrieval pass rather than post-filtering global results.` |
| RQ1 setup | `We evaluated with ... median latency.We compare` | `We evaluate ... median latency. We compare`. |
| RQ2 | `multi-view query expansion` / `perspective-based` | Chọn một term (`multi-view query expansion`) và định nghĩa một lần. |
| Tables | `Latency (ms)` và `Lat. (ms)` | Dùng một label; thêm arrow `Latency (ms) downarrow` nếu cần. |
| General | `keyframes`, `keyframe`, `frame` lẫn nhau | `keyframe` cho indexed representative image; `frame` chỉ khi referring source video frame. |

Ngoài ra: bỏ duplicate `\usepackage{graphicx}`; `H` float placement cần
`\usepackage{float}` (hoặc đổi `[H]` thành `[t]`/`[tbp]`); chuẩn hoá dấu nối
trong title/keywords; kiểm tra các citation keys có year `2027` trong paper
2026 và bibliography metadata trước submission.

## Trình tự thực hiện đề nghị

1. **Freeze evidence (P0):** lưu manifest, query IDs, planner outputs, profile,
   code commit; xác minh mỗi bảng có source artifact.
2. **Rewrite Sec. 3:** sửa DEV theo bảng sai khác, clean notation/pseudocode,
   remove unsupported claims.
3. **Rewrite Sec. 4 protocol/RQs:** thêm Common Experimental Protocol, strata
   results, metric definitions và latency accounting.
4. **Rebuild tables:** RQ1 trade-off + uncertainty; RQ2 factorial/RRF; RQ3
   Full reference and move detailed table to appendix.
5. **Tighten narrative:** abstract/introduction/related work/conclusion theo
   scope đã kiểm chứng; sửa grammar theo bảng.
6. **Compile and audit:** resolve all undefined references/citations, overflow,
   table readability; final pass verifies every quantitative claim against saved
   JSON/CSV.

## Bản compile nhanh

Đã tạo `aic_2026_paper_v1` từ bản gốc. Trong `samplepaper.tex` của bản copy,
ba figure đang hoạt động (system overview, data processing, UI) đã được bọc
trong `\iffalse ... \fi` với marker `FAST-COMPILE`; bỏ hai marker này để khôi
phục figure cho bản camera-ready. Các `\ref` tới figure có thể warning khi
compile bản nhanh, điều này là dự kiến và không ảnh hưởng việc sửa text/bảng.

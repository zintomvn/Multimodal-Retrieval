# H1 Experiment: Query Expansion Pipeline

## 1. Mục Tiêu

Thực nghiệm H1 tập trung tối ưu riêng bước **query expansion** cho hệ thống truy xuất đa phương thức. Ở giai đoạn này chưa đánh giá retrieval, vì hệ thống chưa cần corpus/index/ranker hoàn chỉnh. Mục tiêu là tìm cấu hình model, system prompt và hyperparameter tạo ra các query tiếng Anh:

- đúng ý query gốc tiếng Việt,
- không thêm chi tiết không có trong query gốc,
- đủ đa dạng để hỗ trợ nhiều kênh truy xuất sau này như visual, OCR, ASR, caption, keyword,
- ổn định khi chạy nhiều lần,
- có bảng so sánh để tiếp tục mở rộng thí nghiệm.

Notebook chính: [`h1_expand_query_pipeline.ipynb`](h1_expand_query_pipeline.ipynb)

Output thí nghiệm: [`experiments/h1`](experiments/h1)

## 2. Câu Hỏi Nghiên Cứu

| ID | Câu hỏi | Cách đo trong H1 |
| --- | --- | --- |
| Q1 | Model có trả đúng số lượng query yêu cầu không? | `exact_k`, `unique_ratio` |
| Q2 | Expanded queries có giữ đúng ý nghĩa query gốc không? | `required_concept_coverage`, `reference_token_overlap` |
| Q3 | Model có hạn chế query drift/hallucination không? | `forbidden_avoidance`, `query_drift_risk` |
| Q4 | Expanded queries có đủ đa dạng không? | `pairwise_lexical_diversity`, `distinct_1`, `distinct_2` |
| Q5 | Output có dùng được cho search không? | `englishish_score`, `length_score`, JSON parsing/schema validation |
| Q6 | Chi phí vận hành có hợp lý không? | `latency_sec`, usage metadata nếu provider trả về |

Các metric retrieval như Recall@K, MRR, nDCG chưa dùng trong H1 vì chúng phụ thuộc vào index, corpus, ground-truth result và ranker. Những metric đó nên được đưa vào H2 sau khi retrieval pipeline sẵn sàng.

## 3. Dữ Liệu Synthetic

Notebook tạo synthetic data tại:

```text
notebooks/agent/experiments/h1/synthetic_cases.jsonl
```

Mỗi case gồm:

- `case_id`: tên định danh ổn định cho case.
- `query_type`: loại query, ví dụ `KIS`, `QA`, `TRAKE`.
- `query`: query gốc tiếng Việt.
- `required_concepts`: các concept bắt buộc phải được giữ lại trong expanded queries. Mỗi concept có nhiều alias tiếng Anh.
- `forbidden_terms`: các term nếu xuất hiện thì xem là dấu hiệu query drift hoặc hallucination.
- `reference_expansions`: các expanded query mẫu để đo overlap/semantic relevance nội tại.

Synthetic data không thay thế ground truth retrieval. Nó là bộ kiểm thử nhanh để phát hiện lỗi prompt/model trước khi tốn chi phí chạy retrieval thật.

## 4. Model Và Prompt

Notebook cấu hình ba nhóm model:

| Alias | Provider mặc định | Ghi chú |
| --- | --- | --- |
| `gpt_oss_120b` | Groq | Model giống notebook ban đầu, mặc định `openai/gpt-oss-120b` |
| `gemma` | Groq | Mặc định `gemma2-9b-it`, có thể override bằng `GEMMA_MODEL` |
| `nemotron` | OpenAI-compatible | Dùng `NEMOTRON_BASE_URL`, `NEMOTRON_API_KEY`, `NEMOTRON_MODEL` |

Prompt được quản lý trong `PROMPT_VARIANTS`:

- `strict_multimedia_v1`: prompt chặt, nhấn mạnh multimedia retrieval, chống hallucination.
- `keyword_control_v1`: prompt hướng nhiều hơn về các góc tìm kiếm như visual/OCR/ASR/keyword/temporal.
- `minimal_translation_v1`: baseline tối giản để so sánh.

Khi thêm prompt mới, chỉ cần thêm entry vào `PROMPT_VARIANTS`, sau đó đưa `prompt_id` vào experiment grid.

## 5. Metric

Các metric chính:

| Metric | Ý nghĩa |
| --- | --- |
| `exact_k` | 1 nếu model trả đúng `k` query duy nhất, ngược lại 0 |
| `unique_ratio` | Tỉ lệ query không trùng lặp |
| `required_concept_coverage` | Tỉ lệ concept bắt buộc xuất hiện trong expanded queries |
| `forbidden_avoidance` | Mức độ tránh các term bị cấm |
| `reference_token_overlap` | Jaccard overlap giữa token sinh ra và reference expansions |
| `pairwise_lexical_diversity` | Độ khác nhau trung bình giữa các expanded query |
| `distinct_1`, `distinct_2` | Độ đa dạng unigram/bigram |
| `englishish_score` | Kiểm tra output có thiên về tiếng Anh/ASCII hay không |
| `length_score` | Kiểm tra query có độ dài hợp lý cho search hay không |
| `overall_score` | Điểm tổng hợp có trọng số để sort bảng so sánh |
| `query_drift_risk` | Rủi ro drift, tính từ coverage thấp hoặc forbidden hit cao |

Embedding metric là optional vì có thể cần tải model `sentence-transformers`. Khi cần bật, truyền `use_embeddings=True` vào `run_local_experiment`.

## 6. Quy Trình Chạy Local

1. Cấu hình `.env`:

```text
GROQ_API_KEY=...
NEMOTRON_BASE_URL=...
NEMOTRON_API_KEY=...
NEMOTRON_MODEL=...
LANGSMITH_API_KEY=...
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=multimodal-retrieval-h1-expand-query
```

2. Mở notebook và chạy các cell từ trên xuống.

3. Để chạy model thật, đổi:

```python
RUN_LOCAL_EXPERIMENT = True
```

4. Tùy chỉnh grid:

```python
GRID = build_experiment_grid(
    model_aliases=("gpt_oss_120b", "gemma", "nemotron"),
    prompt_ids=("strict_multimedia_v1", "keyword_control_v1"),
    temperatures=(0.2, 0.4),
    reasoning_efforts=("medium",),
    k_values=(5,),
    output_modes=("json_prompt",),
)
```

5. Đọc bảng kết quả:

```python
summary_df = load_latest_summary()
compare_by_prompt_or_model(summary_df)
```

## 7. Output File

Mỗi lần chạy local sẽ tạo:

| File | Nội dung |
| --- | --- |
| `h1_qe_<timestamp>_predictions.jsonl` | Log chi tiết từng prediction, expanded queries và usage events |
| `h1_qe_<timestamp>_results.csv` | Bảng case-level, mỗi dòng là một case/model/prompt/hyperparameter |
| `h1_qe_<timestamp>_summary.csv` | Bảng aggregate để so sánh model/prompt/hyperparameter |

Nên dùng `summary.csv` để chọn candidate tốt, rồi mở `predictions.jsonl` hoặc LangSmith trace để xem lỗi cụ thể.

## 8. LangSmith Evaluation

LangSmith được dùng để:

- lưu synthetic dataset như một evaluation dataset,
- trace từng lần gọi model,
- hiển thị bảng so sánh evaluator theo experiment,
- kiểm tra output cụ thể khi model hallucinate hoặc thiếu concept.

Để chạy:

```python
RUN_LANGSMITH_EXPERIMENT = True
```

Hoặc gọi trực tiếp:

```python
run_langsmith_experiment(
    model_alias="gpt_oss_120b",
    prompt_id="strict_multimedia_v1",
    params=GenerationParams(temperature=0.2),
)
```

## 9. Cách Diễn Giải Kết Quả

Ưu tiên chọn cấu hình có:

- `overall_score` cao,
- `required_concept_coverage` cao,
- `forbidden_avoidance` gần 1,
- `query_drift_risk` thấp,
- `pairwise_lexical_diversity` đủ cao nhưng không hy sinh coverage,
- latency hợp lý.

Nếu một model đa dạng cao nhưng coverage thấp, model đó đang paraphrase mạnh hoặc drift. Nếu coverage cao nhưng diversity thấp, prompt có thể quá chặt và sinh các câu gần giống nhau. Nếu `forbidden_avoidance` thấp, cần siết prompt hallucination hoặc giảm temperature.

## 10. Hướng Mở Rộng

Các bước tiếp theo hợp lý:

1. Thêm synthetic cases từ query thật trong `scripts/query-p1-groupA`.
2. Tách metric theo `query_type` để biết model mạnh/yếu ở KIS, QA, TRAKE.
3. Thêm prompt variants chuyên biệt cho từng query type.
4. Chạy nhiều `repetitions` để đo ổn định.
5. Khi retrieval sẵn sàng, dùng top cấu hình H1 làm candidate cho H2 và bổ sung Recall@K, MRR, nDCG.

## 11. Tài Liệu Tham Khảo

- LangSmith evaluation: https://docs.langchain.com/langsmith/evaluate-llm-application
- Sentence-BERT semantic similarity: https://arxiv.org/abs/1908.10084
- BERTScore: https://arxiv.org/abs/1904.09675
- Text generation diversity metrics: https://arxiv.org/abs/1904.03971
- Query expansion survey/drift discussion: https://arxiv.org/abs/2509.07794

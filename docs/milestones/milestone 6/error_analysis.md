# Multimodal-Retrieval — Error Analysis & Optimization Plan

**Role:** AI Research / AI Engineering audit  
**Repository:** `zintomvn/Multimodal-Retrieval`  
**Audited snapshot:** `5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f`  
**Audit date:** 2026-09-16  
**Focus:** latency, KIS/QA/TRAKE retrieval logic, temporal search, search-strategy extensibility, UI verification, streaming, and submission correctness.

---

## 0. Executive summary

The system already has several good foundations: hybrid vector/text retrieval, RRF, timestamp support in KIS temporal search, temporal NMS, DEV/Vortex/ATS variants, cloud-first media delivery, result inspection, and a human-in-the-loop submission workflow.

The main remaining problems are not isolated model-quality issues. They are concentrated in five structural failure modes:

1. **Search fan-out is too large and mostly sequential.** A normal search can issue many Milvus and Elasticsearch calls; temporal/multi-view search multiplies this by event count and view count.
2. **Several temporal strategies do not share one time model.** KIS has been migrated toward `timestamp_ms`, while TRAKE still builds candidates without timestamps and converts milliseconds to frames with a fixed 30 FPS assumption.
3. **Scoring semantics are inconsistent across strategies.** Examples: Vortex does not apply the anchor event weight; ATS partial-sequence normalization is not coverage-safe; multi-perspective fusion has hard-coded coefficients; DEV configuration contains weights that are not actually active.
4. **Configuration and strategy APIs are not the single source of truth.** Several config keys are dead or unused (`query_type_weights`, `object_boost`, `final_top_k`, `temporal_weight` in current ranking path), while strategy names are hard-coded across backend schemas, service branching, TypeScript types, and UI.
5. **The submission/verification flow has correctness regressions.** The current service exports one CSV while tests/docs still require `submission/<query_name>.csv` inside a ZIP; frontend fallback can bypass backend validation; selected rows are not bound to a dataset; server validation checks format but not whether the submitted video/frame actually belongs to the selected dataset.

### Highest-priority fixes

| ID | Severity | Finding | Why it matters |
|---|---|---|---|
| P0-1 | **P0** | Full-dataset Python fallback when hybrid retrieval returns no candidates | A Milvus/ES outage can turn a search into an O(N) DB + annotation scan |
| P0-2 | **P0** | TRAKE temporal logic still uses `frame_idx` + fixed 30 FPS conversion | Wrong temporal windows for 25/50/60 FPS, VFR, or non-uniform sampling |
| P0-3 | **P0** | Submission implementation no longer matches its own Codabench test/docs | Can export the wrong artifact structure |
| P0-4 | **P0** | Frontend catches any export failure and silently generates a local CSV | Backend validation failure can be bypassed |
| P0-5 | **P0** | Submission validation does not verify dataset/video/frame existence | A syntactically valid but impossible answer can be exported |
| P1-1 | **P1** | Milvus/Elasticsearch request fan-out is sequential | High p95/p99 latency and poor concurrent-user throughput |
| P1-2 | **P1** | Vortex anchor score ignores configured event weight | Event importance is applied inconsistently |
| P1-3 | **P1** | TRAKE has no event-level temporal NMS before ATS | Near-duplicate frames can consume per-video candidate budget |
| P1-4 | **P1** | `OpenAICompatibleVisualQaModel` receives text only, not pixels | QA/rerank is not actually visual despite the name |
| P1-5 | **P1** | UI rewrites backend rank for KIS/QA | Displayed rank can diverge from persisted/search rank |

---

# 1. Methodology and evidence policy

This report separates findings into three classes.

- **Confirmed code defect:** the behavior follows directly from the current executable code or from a code/test contract mismatch.
- **Design debt:** the code is internally consistent, but the architecture produces avoidable latency, coupling, or operational risk.
- **Benchmark-required risk:** the code reveals a plausible accuracy/latency failure, but the magnitude must be measured on the competition dataset.

No item is included merely because it is mentioned in a repository note. Repository documentation was used only as a secondary consistency check; executable code is the primary source.

External references are used to validate engineering recommendations:

- Milvus filtered search narrows ANN search using metadata before vector search: https://milvus.io/docs/filtered-search.md
- Milvus hybrid/multi-vector search supports multiple ANN requests and RRF/weighted reranking: https://milvus.io/docs/multi-vector-search.md
- Elasticsearch `_msearch` is designed to execute multiple searches in one request: https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-msearch
- Elasticsearch recommends filter context for exact structured restrictions: https://www.elastic.co/docs/reference/query-languages/query-dsl/query-filter-context
- RRF reference: Cormack, Clarke, Büttcher, SIGIR 2009: https://doi.org/10.1145/1571941.1572114
- Google SRE recommends latency distributions/p95/p99 instead of averages: https://sre.google/sre-book/service-level-objectives/
- HTTP byte-range semantics: RFC 9110 §14: https://www.rfc-editor.org/rfc/rfc9110.html#name-range-requests
- Cloud CDN byte-range/cache behavior: https://cloud.google.com/cdn/docs/caching
- VBS studies show that browsing/context inspection and multiple query modes are material to interactive retrieval performance:  
  https://pmc.ncbi.nlm.nih.gov/articles/PMC8791088/  
  https://ieeexplore.ieee.org/document/10539100/  
  https://link.springer.com/article/10.1007/s13735-024-00325-9
- Temporal moment-retrieval literature models target moments in continuous temporal spans rather than assuming a universal frame rate; e.g. recent WACV 2026 work: https://openaccess.thecvf.com/content/WACV2026/html/Gordeev_Saliency-Guided_DETR_for_Moment_Retrieval_and_Highlight_Detection_WACV_2026_paper.html

---

# 2. Latency audit

## LAT-01 — catastrophic full-dataset fallback on retrieval failure

**Classification:** Confirmed code defect  
**Severity:** P0

`_rank_frames()` falls back to `_fallback_rank_frames()` when the union of semantic and text candidates is empty and `strict_hybrid=False`.

Evidence:

- [`service.py` L1677-L1697](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L1677-L1697)
- [`service.py` L2155-L2239](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L2155-L2239)

The fallback performs:

```text
DB: load all frames in dataset
DB: selectinload all frame annotations
Python: compute token overlap for every frame
Python: sort/rerank/diversify
```

The frontend explicitly sends `strict_hybrid: false`:

- [`client.ts` L99-L110](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/web/src/api/client.ts#L99-L110)

Therefore an unavailable/misconfigured Milvus or Elasticsearch backend can degrade into a large in-process scan instead of failing fast.

### Impact

- Search latency can move from seconds to tens of seconds/minutes on a large corpus.
- DB memory pressure increases because all `Frame` objects and annotations are materialized.
- Multiple concurrent fallback searches can saturate DB and application workers.
- The operator sees a slow search rather than a clear infrastructure failure.

### Fix

Production competition profile:

```python
if not candidate_ids:
    if profile["fallback"]["allow_full_scan"] is False:
        raise RetrievalBackendUnavailable(...)
```

Allow the Python overlap fallback only for tiny demo/test datasets with a hard cap, e.g.:

```yaml
fallback:
  enabled: false
  max_frames: 5000
```

Also report backend health in the UI instead of silently changing algorithms.

---

## LAT-02 — Milvus calls are sequential across models × query variants

**Classification:** Design debt  
**Severity:** P1

`_semantic_scores()` loops through every semantic collection and every query variant, calling `vector_client.search()` one at a time:

- [`service.py` L1806-L1855](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L1806-L1855)
- [`milvus.py` L37-L61](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/adapters/vector_db/milvus.py#L37-L61)

The profile allows up to five query variants, and the UI can select multiple visual models.

For the default interactive `top_k=50`, candidate-pool logic commonly drives ANN `top_k` to 1000:

- [`service.py` L1632-L1643](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L1632-L1643)
- [`retrieval_profiles.yaml` L26-L38](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/configs/retrieval_profiles.yaml#L26-L38)

### Approximate request amplification

With 5 semantic variants:

```text
1 visual model  -> 5 sequential Milvus searches
2 visual models -> 10 sequential Milvus searches
```

In temporal multi-view search this is multiplied again by event count and per-event views.

### Fix

Prefer one of:

1. **Batch/concurrent query execution** in the adapter.
2. **Milvus hybrid search** when multiple vector fields/models can be represented in one collection.
3. Run independent model searches concurrently with a bounded executor.
4. Apply dataset/video filters inside Milvus instead of after retrieval.

Milvus explicitly supports filtered ANN and multi-vector hybrid search; these are a better match for this workload than repeated client-side sequential calls.

---

## LAT-03 — Elasticsearch performs up to 3 × number-of-variants sequential requests

**Classification:** Design debt  
**Severity:** P1

`_text_scores()` separately searches ASR, OCR, and caption, and loops over each variant:

- [`service.py` L2058-L2115](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L2058-L2115)

With five lexical/caption variants, this can be up to 15 Elasticsearch HTTP requests for one `_rank_frames()` call.

### Fix

Use Elasticsearch `_msearch` to submit all independent ASR/OCR/caption searches in one network request, then fuse locally.

Additionally, exact constraints such as dataset ID, video ID, source type, and time range should be in filter context rather than post-filtered in Python.

Reference: Elasticsearch `_msearch` and query/filter context documentation listed above.

---

## LAT-04 — candidate filtering happens after global Milvus retrieval

**Classification:** Design debt  
**Severity:** P1

The code first retrieves ANN hits globally, then checks whether each result's video belongs to the selected dataset:

- [`service.py` L1825-L1835](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L1825-L1835)

Milvus ingest metadata in the repository includes dataset/video metadata, while the adapter already accepts a `filters` argument:

- [`milvus.py` L37-L47](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/adapters/vector_db/milvus.py#L37-L47)

### Impact

A global `top_k=1000` can waste retrieval budget on videos that will be discarded, reducing both recall within the requested dataset and latency efficiency.

### Fix

Pass scalar filters to Milvus:

```text
dataset_id == requested_dataset
```

For DEV/video-first stages, support:

```text
video_id in [candidate_video_ids...]
```

The current `_to_filter_expr()` only supports equality composition, so extend it to safe `in` expressions or use partitions.

---

## LAT-05 — DEV-first re-runs global retrieval multiple times

**Classification:** Design debt  
**Severity:** P1

DEV-first performs:

1. a probe retrieval for every event,
2. a second deeper retrieval for the selected diagnostic event,
3. an optional narrative retrieval,
4. another retrieval for every non-diagnostic event,
5. widening retries up to 1000,
6. possible global recovery.

Evidence:

- [`service.py` L962-L1096](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L962-L1096)

The candidate-video shortlist is applied **after** each global frame retrieval:

```python
found = retrieve(event_index, current_top_k)
filtered = [item for item in found if item.video_id in candidate_video_ids]
```

### Fix

Make DEV truly video-first:

```text
global diagnostic probe
    -> candidate_video_ids
    -> server-side filtered event search only inside candidate videos
    -> sequence construction
```

Reuse probe candidates instead of repeating the exact same event/view requests where possible. Add a request-local cache keyed by:

```text
(model, query/view, dataset filter, video filter, top_k)
```

---

## LAT-06 — `_rank_frames_multiperspective()` re-executes the entire hybrid pipeline per semantic view

**Classification:** Design debt  
**Severity:** P1

- [`service.py` L579-L701](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L579-L701)

Each view invokes `_rank_frames()`, which itself performs ANN, text search, DB loading, reranking, and diversification.

### Impact

For 4 events × 5 views, temporal KIS can invoke the full retrieval path ~20 times before temporal reasoning.

### Fix

Split the pipeline into explicit stages:

```text
encode_views()
retrieve_semantic_batch()
retrieve_text_batch()
fuse_per_view()
fuse_across_views()
load_frame_metadata_once()
temporal_reason()
```

Do not repeat DB frame loads and annotation loads for every view.

---

## LAT-07 — reported `latency_ms` excludes Redis history-cache latency

**Classification:** Confirmed observability defect  
**Severity:** P2

Search latency is measured before `_cache_search_history()`:

- [`service.py` L135-L194](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L135-L194)

Redis caching then runs synchronously with connect/read timeouts:

- [`service.py` L216-L239](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L216-L239)

The browser therefore can experience more latency than `normalized_query.latency_ms` reports.

### Fix

Record per-stage timers and a true HTTP end-to-end metric.

Suggested metrics:

```text
planner_ms
embedding_ms
milvus_ms
elasticsearch_ms
db_load_ms
fusion_ms
rerank_ms
temporal_ms
persistence_ms
cache_ms
http_total_ms
```

Track p50/p95/p99, not only average. This follows Google SRE guidance on tail latency.

---

## LAT-08 — QA can perform one model call per returned result

**Classification:** Confirmed latent performance issue  
**Severity:** P1 when VQA is enabled

For every top result, QA calls:

```python
visual_qa.answer(...)
```

- [`service.py` L887-L908](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L887-L908)

With `top_k=50`, an enabled remote QA model can therefore receive 50 sequential calls. The OpenAI-compatible adapter has a 20-second timeout:

- [`openai_compatible.py` L154-L191](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/adapters/model_runtime/openai_compatible.py#L154-L191)

### Fix

Two-stage QA:

```text
retrieve 50
rerank 20
VQA verify top 5–10
generate answers lazily/on demand for deeper results
```

If the model server supports batching, batch image-question inference.

---

# 3. Core hybrid-search logic

## SRCH-01 — `query_type_weights` is configured but not consumed

**Classification:** Confirmed config/code mismatch  
**Severity:** P1

`competition_mvp_v1` defines KIS/QA/TRAKE-specific weights:

- [`retrieval_profiles.yaml` L162-L184](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/configs/retrieval_profiles.yaml#L162-L184)

No executable search code reads `query_type_weights`; repository search finds the key only in configuration.

### Impact

The configuration suggests task-specific tuning exists, but runtime ranking does not use it. QA and TRAKE can therefore run with weights different from what experimenters think they configured.

### Fix

Resolve all runtime weights once:

```python
ResolvedRetrievalConfig.from_profile(profile, query_type, agent_plan)
```

Log the final resolved configuration in every query run.

---

## SRCH-02 — `object_boost` is dead configuration in the text-search path

**Classification:** Confirmed config/code mismatch  
**Severity:** P2

Profiles define `object_boost`, but `_text_scores()` only issues searches for:

```text
ASR
OCR
caption
```

- [`retrieval_profiles.yaml` L40-L48](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/configs/retrieval_profiles.yaml#L40-L48)
- [`service.py` L2070-L2079](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L2070-L2079)

`detected_objects` exists in Elasticsearch mapping, but is not part of the source searches.

### Fix

Either remove `object_boost` from configuration or implement an object evidence branch and evaluate it separately.

---

## SRCH-03 — `final_top_k` is configured but ignored

**Classification:** Confirmed config/code mismatch  
**Severity:** P2

Profiles contain:

```yaml
milvus:
  final_top_k: 50
```

but current ranking uses the request `top_k` and candidate pool settings; no executable code consumes `final_top_k`.

- [`retrieval_profiles.yaml` L34-L39](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/configs/retrieval_profiles.yaml#L34-L39)

### Fix

Remove the dead key or make it an explicit stage limit. Dead tuning knobs are dangerous in research because experiment reports can claim settings that never affected execution.

---

## SRCH-04 — score normalization is query-pool-relative, reducing cross-event comparability

**Classification:** Benchmark-required accuracy risk  
**Severity:** P1

Semantic and text scores are divided by the maximum score within the current filtered pool:

- [`service.py` L1715-L1727](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L1715-L1727)

This means an intrinsically weak event can still have a normalized top score of `1.0`.

### Why this matters for temporal search

Temporal sequence builders compare candidates from different event searches. Per-event max normalization erases absolute confidence differences.

### Fix

Keep both:

```text
raw_similarity
calibrated_similarity
rank-based score
```

Use RRF/rank for robustness, but do not throw away raw similarity. Calibrate per model with held-out queries (temperature/isotonic/z-score by model distribution) if cross-event absolute confidence is required.

---

## SRCH-05 — multi-perspective fusion ignores extra text views

**Classification:** Confirmed logic bug  
**Severity:** P1

`_rank_frames_multiperspective()` creates `view_queries` by iterating **only over `semantic_views`**:

- [`service.py` L608-L615](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L608-L615)

If:

```text
semantic_views = 1
text_views = 5
```

only one text view is searched in this branch.

### Fix

Build view slots with `max(len(semantic_views), len(text_views))` and explicit missing-modality behavior, or model semantic/text views as separate ranked lists and fuse them independently.

---

## SRCH-06 — multi-perspective fusion coefficients are hard-coded

**Classification:** Design debt  
**Severity:** P2

Current formula:

```text
0.58 * best
+ 0.24 * average
+ 0.13 * view_RRF
+ 0.05 * coverage
```

and RRF `k=60` are hard-coded:

- [`service.py` L645-L674](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L645-L674)

### Fix

Move into profile config and run ablations. Research code should make the exact scoring function reproducible.

---

# 4. KIS temporal-search audit

## KIS-01 — Vortex does not apply the event weight to the anchor

**Classification:** Confirmed logic defect  
**Severity:** P1

Vortex initializes:

```python
score = anchor.score
```

Then applies weights only to non-anchor candidates:

- [`vortex.py` L49-L56](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/temporal/vortex.py#L49-L56)

If the planner marks the anchor event with importance `0.3` or `2.0` relative to other events, that importance does not affect the anchor contribution.

### Fix

Use one consistent weighted sequence formula:

```python
score = sum(w[event_i] * s_i for event_i in matched_events)
```

Then normalize separately by expected or matched weight according to the intended semantics.

---

## KIS-02 — ATS partial-sequence normalization can over-reward partial matches

**Classification:** Confirmed scoring inconsistency  
**Severity:** P1

ATS computes:

```python
base_score = weighted_score_sum / len(sequence)
```

- [`ats.py` L158-L174](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/temporal/ats.py#L158-L174)

This is not normalized by:

- total expected event weight, or
- sum of matched event weights.

For a partial sequence containing only a high-weight event, score scale can be inflated.

### Fix

For retrieval where coverage matters:

```text
evidence = sum(w_i*s_i) / sum(all_expected_w)
coverage = sum(matched_w) / sum(all_expected_w)
final = evidence + λ*coverage - μ*missing_penalty
```

DEV already moves in this direction; ATS should use the same scoring contract.

---

## KIS-03 — `prefer_full_sequences=True` is a hard filter, not a preference

**Classification:** Confirmed logic behavior / accuracy risk  
**Severity:** P1

ATS removes every partial sequence if at least one full sequence exists:

- [`ats.py` L77-L80](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/temporal/ats.py#L77-L80)

A weak false-positive full chain can therefore eliminate a strong 3/4 chain from the correct video.

### Fix

Do not filter. Add a coverage bonus/penalty to score, then rank full and partial sequences together.

---

## KIS-04 — timestamp and frame-gap criteria are both applied in frame diversification

**Classification:** Confirmed logic inconsistency  
**Severity:** P2

When timestamps are available, `_is_frame_sufficiently_separated()` still also checks `frame_idx` gap:

- [`service.py` L2377-L2392](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L2377-L2392)

That makes separation depend on source FPS even when real timestamps exist.

### Fix

Use:

```text
timestamp gap if both timestamps exist
ELSE frame gap
```

This is already the pattern used by ATS/Vortex and should be standardized.

---

## KIS-05 — top-k can intentionally shrink because final diversification keeps one sequence per video

**Classification:** Design choice with recall trade-off  
**Severity:** P2

KIS temporal applies:

```text
max_sequences_per_video = 1
```

by default:

- [`service.py` L1316-L1324](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L1316-L1324)
- [`retrieval_profiles.yaml` L68-L73](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/configs/retrieval_profiles.yaml#L68-L73)

This explains why requesting 50 results can return substantially fewer distinct results after temporal filtering/diversification.

### Fix

Separate two objectives:

```text
exploration_top_k: diversity-first, 1 sequence/video
recall_top_k: score-first, e.g. 3–5 sequences/video
```

The UI can default to exploration while exposing “more from this video”.

---

# 5. DEV-first audit

## DEV-01 — mixed timestamp/frame units in fallback paths

**Classification:** Confirmed logic defect for partially missing timestamps  
**Severity:** P1

`temporal_nms()` independently chooses timestamp or frame index for each candidate, then subtracts them and compares against `window_ms`:

- [`dev_first.py` L80-L99](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/temporal/dev_first.py#L80-L99)

If one candidate has `timestamp_ms` and another does not, this can compare:

```text
milliseconds - frame_index
```

Even if both lack timestamps, `frame_idx` difference is still compared against a value named/configured in milliseconds.

Similar mixed-coordinate behavior exists in DEV edge/scoring helpers:

- [`dev_first.py` L211-L241](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/temporal/dev_first.py#L211-L241)

### Fix

Create one shared `TemporalCoordinate` abstraction:

```python
distance(a, b, video_meta):
    if a.ts is not None and b.ts is not None:
        return milliseconds
    return frame_distance converted using video fps/timebase
```

Never compare frame units to millisecond thresholds.

---

## DEV-02 — configured absolute calibration weight is effectively disabled

**Classification:** Confirmed config/runtime mismatch  
**Severity:** P1

DEV configuration assigns:

```yaml
score_calibration:
  relative_weight: 0.60
  absolute_weight: 0.20
  hybrid_weight: 0.20
```

- [`retrieval_profiles.yaml` L97](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/configs/retrieval_profiles.yaml#L97)

But retrieval constructs `DevFirstCandidate` with:

```python
semantic_raw_score=None
text_raw_score=None
```

- [`service.py` L986-L996](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L986-L996)

`calibrate_candidates()` detects missing absolute score and sets the absolute weight to zero.

- [`dev_first.py` L56-L76](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/temporal/dev_first.py#L56-L76)

### Fix

Propagate raw model similarity separately from normalized/fused score, or remove the absolute term until a valid raw score exists.

---

## DEV-03 — `view_agreement_weight` is configured but agreement is hard-coded to zero

**Classification:** Confirmed config/runtime mismatch  
**Severity:** P2

`score_candidate_videos()` explicitly sets:

```python
agreement = 0.0
```

while the profile assigns a non-zero weight:

- [`dev_first.py` L133-L150](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/temporal/dev_first.py#L133-L150)
- [`retrieval_profiles.yaml` L79](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/configs/retrieval_profiles.yaml#L79)

### Fix

Either propagate view IDs through `FrameScore`, or dynamically renormalize weights over active signals when agreement is unavailable.

---

## DEV-04 — video-first filtering is client-side, so DEV is not latency-efficient video-first

**Classification:** Design debt  
**Severity:** P1

DEV does identify candidate videos, but subsequent event retrieval remains global and then filters by `video_id`.

This preserves logic but misses the main systems advantage of video-first retrieval.

### Fix

Extend the retrieval APIs to accept:

```python
allowed_video_ids: set[str] | None
```

and push that constraint into Milvus/Elasticsearch.

---

# 6. TRAKE audit

## TRAKE-01 — TRAKE still assumes 30 FPS

**Classification:** Confirmed logic defect  
**Severity:** P0

TRAKE candidates are created without `timestamp_ms`:

- [`service.py` L1441-L1464](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L1441-L1464)

Then:

```python
delta_frames = delta_t_max_ms / 1000 * 30
```

- [`service.py` L1483](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L1483)

ATS therefore operates on frame indices, not actual media time.

### Impact

The same `180000 ms` constraint becomes:

```text
5400 frames
```

which means:

- 216 s at 25 FPS
- 180 s at 30 FPS
- 90 s at 60 FPS

and is not well-defined for VFR.

### Fix

Populate `Candidate.timestamp_ms` in TRAKE exactly as KIS does, pass `delta_t_max_ms` to `adaptive_temporal_search()`, and use frame index only for legacy records without timestamps.

---

## TRAKE-02 — TRAKE lacks event-level temporal NMS before sequence construction

**Classification:** Confirmed KIS/TRAKE inconsistency  
**Severity:** P1

KIS temporal calls `nms_event_candidates()` after multi-view fusion:

- [`service.py` L1232-L1241](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L1232-L1241)

TRAKE directly passes all per-event ranked candidates into ATS:

- [`service.py` L1412-L1499](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L1412-L1499)

### Impact

Near-identical frames can consume the `per_query_video_limit=24` budget, causing temporally useful but lower-ranked moments to be pruned.

### Fix

Reuse the same event NMS module for TRAKE.

---

## TRAKE-03 — sequence deduplication is effectively too weak when timestamps are absent

**Classification:** Confirmed behavior  
**Severity:** P1

`adaptive_temporal_search()` defaults to:

```text
sequence_nms_window_ms=1500
sequence_nms_window_frames=1
per_video_sequence_limit=0
```

- [`ats.py` L32-L46](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/temporal/ats.py#L32-L46)

TRAKE does not override these values and candidates have no timestamps, so sequence NMS falls back to a one-frame window.

### Result

Many nearly identical sequences from the same video can survive.

### Fix

After timestamp migration:

```yaml
trake:
  event_nms_window_ms: 1500
  sequence_nms_window_ms: 3000-10000
  max_sequences_per_video: 2-4
```

Use a separate deeper pool internally for recall and a diversified presentation pool for the UI.

---

## TRAKE-04 — no dedicated TRAKE scoring contract despite configured task weights

**Classification:** Confirmed design/config mismatch  
**Severity:** P1

`query_type_weights.trake` exists in config but is not applied by runtime ranking.

TRAKE therefore relies on generic `_rank_frames()` plus event-plan weights instead of a clearly reproducible task-level scoring configuration.

### Fix

Introduce `ResolvedQueryProfile(query_type="TRAKE")` and log it in `score_breakdown`.

---

# 7. QA audit

## QA-01 — the current “Visual QA” adapter is text-only

**Classification:** Confirmed naming/architecture defect  
**Severity:** P1

`OpenAICompatibleVisualQaModel.answer()` sends:

```text
question
evidence_text
answer_hint
```

to `/chat/completions`.

It does **not** send an image URL, image bytes, or visual tokens.

- [`openai_compatible.py` L154-L191](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/adapters/model_runtime/openai_compatible.py#L154-L191)

### Impact

Questions requiring direct pixel evidence depend entirely on captions/OCR/ASR/annotation hints.

### Fix

Rename the current interface to something like `EvidenceQaModel`, or implement a real multimodal VQA adapter:

```text
question + selected frame image + neighboring frames + textual evidence
```

---

## QA-02 — QA answer generation is coupled to retrieval top-k

**Classification:** Design debt  
**Severity:** P1

QA runs answer generation while materializing every result.

The retrieval stage should not be forced to generate answers for 50 candidates before the UI can show the first useful candidates.

### Fix

Separate APIs/stages:

```text
POST /search -> ranked frames
POST /qa/answer -> answer for selected/top-N frame(s)
```

or return first search results immediately and stream/defer answers.

---

## QA-03 — empty QA answers are only warnings during submission validation

**Classification:** Policy-dependent correctness risk  
**Severity:** P2

`SubmissionService.validate()` emits a warning, not an error, for an empty QA answer:

- [`submissions/service.py` L132-L169](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/submissions/service.py#L132-L169)

If the competition requires an answer field, this allows a formally “VALID” but unusable row.

### Fix

Make this rule competition-profile dependent and default to error when answer is mandatory.

---

# 8. Search-strategy architecture and extensibility

## ARCH-01 — adding a temporal strategy requires edits across multiple layers

**Classification:** Confirmed architectural coupling  
**Severity:** P1

Strategy names are hard-coded in:

1. backend Pydantic `Literal`:
   - [`schemas.py` L13-L31](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/schemas.py#L13-L31)
2. service `if/else` dispatch:
   - [`service.py` L1173-L1182](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/retrieval/service.py#L1173-L1182)
3. frontend TypeScript union:
   - [`client.ts` L73-L89](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/web/src/api/client.ts#L73-L89)
4. response types:
   - [`types.ts` L49-L74](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/web/src/types.ts#L49-L74)
5. UI controls/state.

### Fix: strategy registry

```python
class TemporalStrategy(Protocol):
    name: str
    supported_tasks: set[QueryType]
    def search(ctx: RetrievalContext) -> list[TemporalSequence]: ...

TEMPORAL_STRATEGIES = {
    "vortex_k_context": VortexStrategy(...),
    "aithena_weighted_ats": AtsStrategy(...),
    "dev_first_search": DevFirstStrategy(...),
}
```

Expose:

```text
GET /api/retrieval/capabilities
```

so the UI renders strategies dynamically.

---

## ARCH-02 — `RetrievalService` is a monolithic orchestration unit

**Classification:** Design debt  
**Severity:** P1

`service.py` is ~2700 lines and currently owns:

```text
planning
query normalization
semantic retrieval
text retrieval
fusion
reranking
filters
KIS temporal
TRAKE
DEV
persistence
media URL mapping
fallback ranking
history caching
```

This increases regression risk when changing one strategy.

### Recommended decomposition

```text
QueryPlanner
RetrievalCandidateProvider
SemanticRetriever
TextRetriever
FusionEngine
FrameReranker
TemporalStrategyRegistry
ResultDiversifier
QaAnswerer
SubmissionVerifier
SearchTelemetry
```

Keep `RetrievalService` as a thin orchestration layer.

---

## ARCH-03 — `score_breakdown` is an untyped dictionary

**Classification:** Design debt  
**Severity:** P2

Backend emits strategy-specific arbitrary keys. Frontend then guesses possible names:

```text
semantic_score / visual_score / semantic / visual
text_score / metadata_score / text
rrf_score / rrf
```

- [`App.tsx` L283-L323](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/web/src/App.tsx#L283-L323)

### Fix

Version an explicit score schema:

```json
{
  "schema_version": 2,
  "retrieval": {...},
  "fusion": {...},
  "rerank": {...},
  "temporal": {...}
}
```

This is especially important for experiment logging.

---

## ARCH-04 — strategy-specific tuning is partly hard-coded and partly dead config

**Classification:** Confirmed design problem  
**Severity:** P1

Examples:

- multi-view fusion coefficients hard-coded in Python,
- `query_type_weights` unused,
- `object_boost` unused,
- `final_top_k` unused,
- Vortex anchor weight inconsistent,
- DEV `absolute_weight` configured but disabled by missing raw score,
- DEV `view_agreement_weight` configured while agreement is always zero.

### Fix

At application startup:

1. parse profile into typed config,
2. reject unknown/dead keys,
3. expose resolved runtime config,
4. store a config hash with every `QueryRun`.

This makes experiments reproducible.

---

# 9. UI and interactive verification audit

Interactive video retrieval literature consistently shows that model quality alone is not enough; browsing speed, temporal context, and low-interaction verification materially affect task success. VBS studies specifically describe top systems using ranked thumbnails, neighboring keyframes, video playback, grouped-by-video browsing, temporal sequences, and keyboard-optimized inspection.

## UI-01 — frontend rewrites the backend rank

**Classification:** Confirmed logic/UI defect  
**Severity:** P1

For KIS and QA, `diversifyResultsForDisplay()` moves the first result from each video to the front and rewrites `rank`:

- [`App.tsx` L237-L258](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/web/src/App.tsx#L237-L258)

Backend already performs diversification.

### Impact

- displayed rank can differ from persisted rank,
- debugging offline metrics vs UI becomes confusing,
- rank-based user decisions are no longer traceable to backend output.

### Fix

Never mutate backend rank.

Use:

```text
backend_rank
display_group
display_position
```

If a video-grouped browsing mode is useful, make it an explicit UI view, not a rank mutation.

---

## UI-02 — selected submission rows persist across searches/query types and are not dataset-bound

**Classification:** Confirmed state-model risk  
**Severity:** P0/P1

`selected` is global React state, while `SubmissionRow` contains no `dataset_id` or result ID:

- [`App.tsx` L1572-L1585](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/web/src/App.tsx#L1572-L1585)
- [`types.ts` L227-L234](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/web/src/types.ts#L227-L234)

Changing search type/new session does not inherently bind existing selections to the active dataset.

### Fix

Store:

```ts
SelectedCandidate {
  datasetId
  queryRunId
  resultId
  queryName
  queryType
  videoId
  videoCode
  frameIds
  frameIndices
}
```

When dataset changes, either clear the tray or partition it by dataset.

---

## UI-03 — current-time video selection can verify one indexed frame but submit another theoretical frame index

**Classification:** Benchmark-required correctness risk  
**Severity:** P1

`seek_video_frame()` finds the nearest indexed frame by timestamp, but for `direction="nearest"` it returns:

```python
selection.frame_idx = round(seconds * fps)
selection.timestamp_ms = round(seconds * 1000)
```

rather than the indexed frame's actual `frame_idx`.

- [`media/router.py` L674-L733](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/media/router.py#L674-L733)

The UI displays the returned indexed frame image but stores `selection.frame_idx` for submission.

### Risk

If:

- FPS metadata is approximate,
- the source is VFR,
- indexed frames are sparse,

the operator may visually approve the nearest keyframe but submit a different source frame.

### Fix

For exact-frame submission, provide an **exact source-frame decode endpoint** around the current timestamp or clearly display:

```text
previewed indexed frame: 1230
source frame to submit: 1247
```

and allow the operator to inspect the exact submitted frame.

---

## UI-04 — evidence requests are not actually cancelled during rapid video/frame navigation

**Classification:** Design debt  
**Severity:** P2

React uses a `cancelled` boolean to prevent stale state updates, but the HTTP request itself is still sent and completed.

### Fix

Use `AbortController` and a short debounce during scrub/rapid stepping.

---

## UI-05 — recommended high-speed competition workspace

Based on VBS interface evidence and the current codebase, the fastest verification layout would be:

### Main result grid

Each card should show only:

```text
thumbnail
backend rank
video code
source time
final score
matched modalities
```

Avoid large reasoning details in the default card.

### Hover / keyboard context

- `J/K` or arrows: previous/next result
- `A/D`: previous/next indexed frame
- `Space`: play/pause video
- `Enter`: open exact verification view
- `1..9`: add candidate to tray
- `S`: submit currently verified candidate
- `Esc`: close preview

### Video-grouped exploration

One row per video, with 8–20 temporally ordered frames. This pattern is used by multiple VBS systems and is highly appropriate when many near-duplicate frames appear.

### Exact verification strip

For KIS/QA:

```text
[-2] [-1] [retrieved] [+1] [+2]
             |
      exact source-time frame
```

For temporal KIS/TRAKE:

```text
E1 -> E2 -> E3 -> E4
```

Each event should show:

```text
timestamp
frame index
event query
visual score
text evidence
```

### Submission guard

Do not enable the final submit/export action until:

```text
dataset verified
query name verified
video exists
frame exists / source range valid
TRAKE order valid
QA answer valid
server-side validation passed
```

---

# 10. Submission pipeline audit

## SUB-01 — current code no longer matches its Codabench ZIP test

**Classification:** Confirmed regression  
**Severity:** P0

The current service implements:

```python
export_csv()
```

and writes one file:

```text
<data_root>/submissions/<submission_id>/<submission.name>.csv
```

- [`submissions/service.py` L239-L268](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/submissions/service.py#L239-L268)

The current test still calls:

```python
service.export_zip(...)
```

and expects:

```text
submission/query-1-kis.csv
submission/query-2-qa.csv
submission/query-3-trake.csv
```

- [`test_submission_hardening.py` L63-L94](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/tests/test_submission_hardening.py#L63-L94)

Repository docs also state `submission/<query_name>.csv`, UTF-8, no header.

This is a direct code/test/documentation contract mismatch.

### Additional inconsistency

Router returns the same path as both `csv_uri` and `zip_uri`:

- [`submissions/router.py` L32-L41](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/submissions/router.py#L32-L41)

### Fix

Restore one canonical contract. If Codabench expects ZIP:

```text
submission.zip
└── submission/
    ├── query-1-kis.csv
    ├── query-2-qa.csv
    └── query-3-trake.csv
```

Make the integration test the contract source.

---

## SUB-02 — frontend can bypass server validation

**Classification:** Confirmed logic defect  
**Severity:** P0

`exportSubmission()` catches **any** error from:

```text
create submission
add items
server validation/export
download
```

and falls back to local CSV generation:

- [`App.tsx` L2586-L2608](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/web/src/App.tsx#L2586-L2608)

Therefore a backend validation failure, 500, network error, or format regression can still lead to a downloaded file that looks like a valid submission.

### Fix

Only allow local export in explicit offline/mock mode.

Production:

```ts
catch (error) {
  showBlockingExportError(error)
  // do NOT generate a competition submission
}
```

---

## SUB-03 — local fallback format is incompatible with the repository's multi-query Codabench contract

**Classification:** Confirmed logic defect  
**Severity:** P0

If more than one `query_name` is selected, `buildSubmissionCsv()` creates one CSV with a header:

```text
query_name,video_code,frame_indices,answer
```

- [`App.tsx` L394-L419](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/web/src/App.tsx#L394-L419)

Repository docs/tests expect separate headerless files per query inside a ZIP.

### Fix

Delete this fallback for production or make it generate the exact same ZIP structure as backend.

---

## SUB-04 — server validation does not verify submitted video/frame against the dataset

**Classification:** Confirmed correctness gap  
**Severity:** P0

`SubmissionService.validate()` checks:

- row count,
- positive rank,
- non-empty video code,
- frame count,
- QA answer length,
- TRAKE monotonic frame indices,
- non-negative frame indices,
- `.mp4` suffix.

It does **not** query `Video`/`Frame` to verify:

```text
video_code belongs to submission.dataset_id
frame index exists or is within valid source bounds
selected frame/result belongs to the originating query run
```

- [`submissions/service.py` L69-L237](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/submissions/service.py#L69-L237)

### Fix

Add semantic validation:

```python
video = lookup(dataset_id, video_code)
assert video exists

for frame_idx:
    assert 0 <= frame_idx < video.frame_count
    # or validate via duration*fps/source timebase

if retrieval_result_id supplied:
    assert result.query_run.dataset_id == submission.dataset_id
```

---

## SUB-05 — rank integrity is not validated

**Classification:** Confirmed validation gap  
**Severity:** P2

Rows are sorted by rank, but duplicate or non-contiguous ranks are not rejected.

### Fix

For each query:

```text
expected ranks = 1..N
actual ranks must be unique
```

---

# 11. Streaming / media-delivery audit

## MEDIA-01 — cloud-direct video path is good and should remain the default

**Classification:** Positive finding

`/preview-url` returns direct GCS/HTTP media where possible:

- [`media/router.py` L755-L779](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/media/router.py#L755-L779)

This keeps large video traffic away from the application server.

The design is aligned with HTTP byte-range streaming and CDN/object-storage practice.

---

## MEDIA-02 — local video fallback puts media bandwidth through FastAPI workers

**Classification:** Design debt  
**Severity:** P2 for local/dev, P1 if used in production

Local preview uses `StreamingResponse` and reads 1 MB chunks:

- [`media/router.py` L71-L128](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/media/router.py#L71-L128)

Range handling is correct in principle, but the application process remains on the hot data path.

### Fix

Production options:

1. keep all competition video in object storage/CDN;
2. use Nginx `X-Accel-Redirect`/equivalent for local files;
3. use a dedicated media server.

Do not scale API workers just to carry video bytes.

---

## MEDIA-03 — thumbnail redirect can add one application round trip per image

**Classification:** Design debt  
**Severity:** P2

`/frames/{id}/thumbnail` may generate/return a 307 signed/public URL per frame.

- [`media/router.py` L638-L671](https://github.com/zintomvn/Multimodal-Retrieval/blob/5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f/apps/backend/app/modules/media/router.py#L638-L671)

For a dense 50–100 frame result grid this can produce many extra API hits.

### Fix

Prefer direct cacheable thumbnail URLs in search results when security policy permits. For private content, consider CDN signed cookies/prefix authorization rather than individually signing many URLs. Lazy-load images below the fold.

---

# 12. Recommended target search architecture

```text
                           ┌──────────────────┐
Query ───────────────────► │ Query Planner     │
                           └────────┬─────────┘
                                    │ resolved typed plan
                                    ▼
                           ┌──────────────────┐
                           │ Search Executor   │
                           │ request cache     │
                           └──────┬─────┬─────┘
                                  │     │
                    ┌─────────────┘     └─────────────┐
                    ▼                                 ▼
        ┌─────────────────────┐           ┌─────────────────────┐
        │ Semantic Retrieval   │           │ Text Retrieval       │
        │ batch/concurrent ANN │           │ ES _msearch          │
        │ server-side filters  │           │ filter context       │
        └──────────┬──────────┘           └──────────┬──────────┘
                   └────────────────┬─────────────────┘
                                    ▼
                           ┌──────────────────┐
                           │ Fusion Engine     │
                           │ typed evidence    │
                           └────────┬─────────┘
                                    ▼
                           ┌──────────────────┐
                           │ Candidate Cache   │
                           │ load DB once      │
                           └────────┬─────────┘
                                    ▼
              ┌─────────────────────┼────────────────────┐
              ▼                     ▼                    ▼
           KIS frame            QA top-M           Temporal
                                                  Strategy Registry
                                                    │
                                 ┌──────────────────┼─────────────────┐
                                 ▼                  ▼                 ▼
                              Vortex              ATS               DEV
                                 └──────────────────┬─────────────────┘
                                                    ▼
                                           Result Diversifier
                                                    ▼
                                             UI / Verifier
                                                    ▼
                                         Server-side Submission
                                              Validation
```

### Key architectural rule

**Retrieval and presentation diversity must be separate.**

Internal candidate pool:

```text
score-first, recall-first, deeper
```

UI presentation:

```text
diversity-first, grouped-by-video, fast to inspect
```

Do not destroy internal recall merely to avoid duplicate cards.

---

# 13. Proposed latency budget and observability

The repository currently stores only one overall backend latency number. For interactive search, define stage budgets and tail objectives.

Example initial targets to benchmark, not guaranteed values:

| Stage | Suggested p95 budget |
|---|---:|
| Query planning cache hit | < 50 ms |
| Query planning remote LLM | measured separately |
| Text embedding | < 150 ms |
| Milvus retrieval | < 300 ms/model batch |
| Elasticsearch retrieval | < 250 ms `_msearch` |
| DB metadata load | < 150 ms |
| Fusion + diversification | < 50 ms |
| Temporal reasoning | < 200 ms after candidate pruning |
| First result paint after API response | < 300 ms |

Do not optimize only average latency. Track p50/p95/p99 as recommended by Google SRE.

### Required dimensions

```text
query_type
temporal_strategy
number_of_events
semantic_view_count
text_view_count
visual_model_count
ann_top_k
candidate_count
candidate_video_count
reranker_enabled
fallback_used
```

---

# 14. Benchmark plan to validate fixes

## 14.1 Offline retrieval quality

For KIS:

```text
Recall@1, @5, @10, @20, @50
MRR
video recall
frame/time tolerance recall
duplicate ratio in top-k
distinct-video count
```

For temporal KIS:

```text
target-video recall
anchor-frame recall
event coverage
sequence temporal validity
sequence duplicate ratio
```

For TRAKE:

```text
all-events-correct rate
ordered-sequence recall@k
event-wise recall
mean temporal error (ms)
duplicate-sequence ratio
```

For QA:

```text
retrieval Recall@k
answer exact/F1 where applicable
answer grounded-to-selected-frame rate
empty-answer rate
```

## 14.2 Latency

Measure:

```text
cold vs warm planner
1 vs 5 semantic views
1 vs 2 visual models
KIS vs QA vs TRAKE
2 / 4 / 8 temporal events
top_k = 20 / 50 / 100
1 / 5 concurrent users
```

Report p50/p95/p99.

## 14.3 Mandatory ablations

1. sequential search vs batched/concurrent search
2. global post-filter vs Milvus server-side dataset filter
3. TRAKE 30 FPS vs real timestamp
4. ATS hard full-sequence filter vs coverage-aware scoring
5. Vortex current anchor scoring vs weighted anchor
6. event NMS off/on for TRAKE
7. one sequence/video vs 3 sequences/video
8. current per-event max normalization vs raw+rank calibration
9. text-only QA vs true image-grounded VQA
10. backend rank vs UI rank rewriting

---

# 15. Prioritized implementation roadmap

## Phase 0 — correctness blockers

### P0-A. Restore submission contract

- restore `export_zip()` or update the complete contract atomically,
- pass `test_submission_hardening.py`,
- remove silent local fallback in production,
- validate dataset/video/frame existence.

### P0-B. Timestamp migration for TRAKE

- attach `timestamp_ms` to `Candidate`,
- pass `delta_t_max_ms`,
- use frame index only as fallback,
- add 25/30/60 FPS tests.

### P0-C. Disable unrestricted full-scan fallback in production

- explicit backend-unavailable error,
- demo-only capped fallback.

---

## Phase 1 — latency

### P1-A. Batch Elasticsearch

Replace repeated `.search()` with `_msearch`.

### P1-B. Concurrent/batch Milvus

- batch semantic views,
- run model searches concurrently,
- push dataset/video filters to Milvus.

### P1-C. Refactor multi-perspective search

Retrieve once per query/model batch; fuse views without repeating DB loads.

### P1-D. DEV server-side candidate-video filtering

Turn DEV into real video-first retrieval.

---

## Phase 2 — temporal scoring

### P2-A. One common temporal coordinate library

Used by:

```text
ATS
Vortex
DEV
KIS output diagnostics
TRAKE
UI seek/verification
```

### P2-B. One sequence score contract

Components:

```text
event evidence
coverage
strong coverage
missing penalty
temporal compactness
diagnostic evidence
```

### P2-C. TRAKE NMS/diversification

Reuse KIS temporal NMS with task-specific parameters.

---

## Phase 3 — architecture

### P3-A. Strategy registry

No strategy-name edits in four different layers.

### P3-B. Typed score/config schemas

Reject dead config keys.

### P3-C. Capabilities endpoint

Frontend reads enabled models/strategies/profile capabilities from backend.

---

## Phase 4 — operator UI

### P4-A. Preserve backend rank

Add grouped-by-video view without mutating rank.

### P4-B. Keyboard-first verification

Optimize result inspection and exact frame selection.

### P4-C. Exact source-frame verification

The displayed image and submitted frame index must refer to the same source moment.

---

# 16. Ten-pass verification record

The user requested repeated verification. A separate external “verifier model” was not available in this environment, so the report was checked in ten structured passes against independent code paths, tests, documentation, and external engineering references.

| Pass | Check | Result |
|---:|---|---|
| 1 | Trace request path: UI → client → router → service | Passed |
| 2 | Trace semantic/text retrieval fan-out and candidate limits | Passed |
| 3 | Trace KIS temporal path and timestamp semantics | Passed |
| 4 | Trace TRAKE path independently from KIS | Passed; 30 FPS mismatch confirmed |
| 5 | Trace ATS/Vortex/DEV scoring formulas line-by-line | Passed; weight/normalization issues confirmed |
| 6 | Compare runtime config keys with actual code references | Passed; dead/disabled keys identified |
| 7 | Trace media preview, range streaming, seek, and exact-frame selection | Passed |
| 8 | Trace selection → submission → validate → export → download | Passed; format/validation regressions confirmed |
| 9 | Compare current code against repository tests/docs | Passed; `export_zip` contract mismatch confirmed |
| 10 | Cross-check recommendations against Milvus, Elasticsearch, HTTP/CDN, SRE, and VBS literature | Passed |

### Confidence statement

- **Confirmed code defects:** high confidence because each item has a direct current-code trace.
- **Design-debt findings:** high confidence that the behavior exists; the exact latency gain after fixing it must be benchmarked.
- **Accuracy-risk findings:** medium-to-high confidence in the mechanism; the effect size must be measured on AIC/VBS/LSC queries.

It would be misleading to claim a scientifically measured “98% correctness” without executing the complete production stack and benchmark dataset. The audit instead targets **100% source traceability** for every finding labeled “confirmed.”

---

# 17. Final recommended order of work

If only one week is available, implement in this order:

1. **Fix submission ZIP/validation and remove silent local fallback.**
2. **Migrate TRAKE to `timestamp_ms` and add TRAKE event/sequence NMS.**
3. **Disable full-dataset fallback in competition mode.**
4. **Batch Elasticsearch and parallelize/batch Milvus calls.**
5. **Push dataset/video filters into retrieval backends.**
6. **Fix Vortex anchor weighting and ATS partial-sequence scoring.**
7. **Make DEV candidate-video filtering server-side.**
8. **Stop UI from rewriting backend ranks.**
9. **Add exact source-frame verification before submission.**
10. **Refactor strategy registry/config typing after correctness and latency are stable.**

The most important engineering principle for this repository is:

> **Keep the deep internal candidate pool recall-oriented; apply diversity only at the presentation layer; use real timestamps for every temporal task; and make the submitted frame the exact frame the user verified.**

---

# Appendix A — Key code locations

- Retrieval orchestration:  
  `apps/backend/app/modules/retrieval/service.py`
- Query planner:  
  `apps/backend/app/modules/retrieval/query_planning.py`
- ATS:  
  `apps/backend/app/modules/temporal/ats.py`
- Vortex:  
  `apps/backend/app/modules/temporal/vortex.py`
- DEV-first:  
  `apps/backend/app/modules/temporal/dev_first.py`
- Temporal diversification:  
  `apps/backend/app/modules/temporal/diversification.py`
- Milvus adapter:  
  `apps/backend/app/adapters/vector_db/milvus.py`
- Elasticsearch adapter:  
  `apps/backend/app/adapters/text_search/elasticsearch.py`
- Visual-QA adapter:  
  `apps/backend/app/adapters/model_runtime/openai_compatible.py`
- Media delivery:  
  `apps/backend/app/modules/media/router.py`
- Submission service:  
  `apps/backend/app/modules/submissions/service.py`
- Submission tests:  
  `apps/backend/tests/test_submission_hardening.py`
- Frontend workspace:  
  `apps/web/src/App.tsx`
- Frontend API:  
  `apps/web/src/api/client.ts`
- Retrieval profile:  
  `configs/retrieval_profiles.yaml`
- Model registry:  
  `configs/model_registry.yaml`

# Appendix B — External technical references

1. Milvus, **Filtered Search** — https://milvus.io/docs/filtered-search.md  
2. Milvus, **Multi-Vector Hybrid Search** — https://milvus.io/docs/multi-vector-search.md  
3. Elasticsearch, **Multi Search API** — https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-msearch  
4. Elasticsearch, **Query and Filter Context** — https://www.elastic.co/docs/reference/query-languages/query-dsl/query-filter-context  
5. Cormack, Clarke, Büttcher, **Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods**, SIGIR 2009 — https://doi.org/10.1145/1571941.1572114  
6. Google SRE, **Service Level Objectives** — https://sre.google/sre-book/service-level-objectives/  
7. Google SRE, **Monitoring Distributed Systems** — https://sre.google/sre-book/monitoring-distributed-systems/  
8. RFC 9110, **Range Requests** — https://www.rfc-editor.org/rfc/rfc9110.html#name-range-requests  
9. Google Cloud CDN, **Caching / Byte Range Requests** — https://cloud.google.com/cdn/docs/caching  
10. VBS remote evaluation, **Interactive video retrieval evaluation at a distance** — https://pmc.ncbi.nlm.nih.gov/articles/PMC8791088/  
11. VBS 2023 analysis, **Evaluating Performance and Trends in Interactive Video Retrieval** — https://ieeexplore.ieee.org/document/10539100/  
12. **Interactive multimodal video search: an extended post-evaluation for the VBS 2022 competition** — https://link.springer.com/article/10.1007/s13735-024-00325-9  
13. **Multi-Modal Interactive Video Retrieval with Temporal Queries** — https://dbis.dmi.unibas.ch/publications/2022/vitrivr-VBS22/  
14. **The VISIONE Video Search System** — https://pmc.ncbi.nlm.nih.gov/articles/PMC8321359/  
15. WACV 2026, **Saliency-Guided DETR for Moment Retrieval and Highlight Detection** — https://openaccess.thecvf.com/content/WACV2026/html/Gordeev_Saliency-Guided_DETR_for_Moment_Retrieval_and_Highlight_Detection_WACV_2026_paper.html

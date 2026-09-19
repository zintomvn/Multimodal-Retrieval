# Experimental Protocol — Multimodal Retrieval System for AIC 2026

**Repository:** `zintomvn/Multimodal-Retrieval`  
**Benchmark purpose:** technical-report evaluation of the retrieval system under AIC 2026 competition rules  
**Code snapshot inspected:** commit `6ed20a5bf2ce3acc3069d3964c5f6c1c399d924c`  
**Evaluation mode:** machine-generated search results first, followed by human verification only  
**Primary tasks:** Textual KIS, Visual Question Answering (QA), TRAKE

---

## 1. Objective

This protocol evaluates the retrieval system in a way that is:

1. **Aligned with the organizer's scoring rule**, not only generic IR metrics.
2. **Reproducible**, with frozen code, configuration, model versions, index snapshots, and query sets.
3. **Separated into machine quality and human-assisted operational quality**.
4. **Suitable for a technical report**, including effectiveness, latency, resource usage, ablation studies, and failure analysis.
5. **Representative of the actual competition workflow**, where the system produces ranked candidates and a human verifies the candidates before submission.

The primary scientific result must be the **machine-only benchmark**. Human verification is reported separately so that algorithmic improvements are not mixed with operator skill.

---

# 2. Competition Requirements That Define the Benchmark

## 2.1 Query types

The benchmark contains three official task types:

- **Textual KIS:** output `<video_name>, <frame_idx>`.
- **QA:** output `<video_name>, <frame_idx>, <answer>`.
- **TRAKE:** output `<video_name>, <frame_1>, ..., <frame_N>` where the frame order follows the requested event order.

## 2.2 Submission constraints

For every query:

- At most **100 ranked rows** may be submitted.
- Video names must not contain `.mp4`.
- Frame IDs are integers.
- QA answers are limited to **100 characters**.
- TRAKE must contain exactly the number of frame IDs required by the query.
- TRAKE event frames must follow the requested temporal event order.
- CSV files contain no header and use UTF-8.
- Competition packages are submitted as CSV files inside a `submission/` directory.

These constraints must also be enforced by the local benchmark validator before any score is computed.

## 2.3 Official query score

For one query, let the ranked answers be:

$$
r_1,r_2,\ldots,r_{100}
$$

The official thresholds are:

$$
K=\{1,5,20,50,100\}
$$

For each threshold:

$$
R@k = \max_{1 \le i \le k}\mathrm{RScore}(r_i)
$$

The final score for one query is:

$$
\mathrm{FinalScore}
=
\frac{1}{5}
\sum_{k\in\{1,5,20,50,100\}}R@k
$$

This **Mean of Top-k R-Scores** is the primary metric of every experiment.

### Interpretation for binary KIS/QA

When R-Score is binary, the score is determined by the rank of the first fully correct result:

| First correct result | Final Score |
| -------------------- | ----------: |
| rank 1               |         1.0 |
| rank 2–5             |         0.8 |
| rank 6–20            |         0.6 |
| rank 21–50           |         0.4 |
| rank 51–100          |         0.2 |
| not found in top 100 |         0.0 |

Therefore, the benchmark must evaluate **ranking quality**, not merely whether a correct result exists somewhere in the candidate set.

---

# 3. Task-specific R-Score

## 3.1 Textual KIS

A prediction is correct only when:

1. predicted video is the ground-truth video, and
2. predicted frame is inside the accepted ground-truth frame interval.

$$
\mathrm{RScore}(r_i)
=
\mathbb{I}
(v_i=GT_v \land frame_i\in[s,e])
$$

Report the official Final Score and supporting retrieval metrics.

## 3.2 QA

A prediction is correct only when:

1. video matches,
2. frame lies inside the accepted interval, and
3. answer equals the ground-truth answer according to the organizer scorer.

$$
\mathrm{RScore}(r_i)
=
\mathbb{I}
(v_i=GT_v
\land frame_i\in[s,e]
\land answer_i=GT_a)
$$

For the strict benchmark, do **not** silently lowercase, trim, paraphrase, or semantically normalize answers unless the official organizer scorer explicitly performs that operation.

Report retrieval-only correctness separately from end-to-end QA correctness so that a wrong textual answer is not confused with retrieval failure.

## 3.3 TRAKE

The predicted video must match the ground-truth video. For a query containing \(N\) events:

$$
\mathrm{RScore}(r_i)
=
\frac{1}{N}
\sum_{j=1}^{N}
\mathbb{I}(frame_{i,j}\in[s_j,e_j])
$$

if the video is correct; otherwise R-Score is 0.

Before scoring, the result must pass structural validation:

- exactly \(N\) frames,
- integer frame IDs,
- event order preserved,
- same video for the sequence.

---

# 4. Required Fixes Before Running the Official Benchmark

The repository already contains `scripts/benchmark_retrieval.py` and the correct top-k thresholds, but the local scorer should be hardened before producing report numbers.

## 4.1 QA strict equality

Current implementation compares:

```python
str(predicted_answer).lower() == str(gt_answer).lower()
```

For organizer-compliant evaluation, use the exact behavior of the supplied scoring specification. If it is exact string equality, use:

```python
str(predicted_answer) == str(gt_answer)
```

Any optional normalized/semantic QA score must be reported only as a **secondary diagnostic metric**.

## 4.2 TRAKE length validation

A TRAKE row must contain exactly \(N\) event frames.

Reject or assign R-Score 0 to rows with:

```text
len(predicted_frames) != N
```

Do not ignore extra frames.

## 4.3 Submission format validation

Before scoring, validate:

- row count <= 100,
- no header,
- correct number of CSV columns,
- valid integer frame IDs,
- video name without extension,
- QA answer <= 100 characters,
- TRAKE frame count equals event count.

Store validation failures separately from retrieval failures.

---

# 5. Evaluation Tracks

Two evaluation tracks are mandatory.

## Track A — Machine-only Retrieval Benchmark

Purpose: measure the algorithm itself.

Rules:

- Query is passed to the system once using a frozen experiment configuration.
- The machine returns the ranked top 100.
- No human re-ranking.
- No human query rewriting.
- No manual result deletion.
- No manual answer correction.
- Score the raw exported ranking.

This is the **primary benchmark for the technical report**.

## Track B — Human-Verified Competition Simulation

Purpose: measure the actual contest workflow.

Rules:

- Start from exactly the same machine-generated results as Track A.
- Human may inspect thumbnails, neighboring frames, and video context.
- Human may accept/reject/reorder machine candidates for submission.
- Human must not launch an additional manually reformulated search in this track.
- Record every human action and elapsed verification time.
- For QA, record whether the final answer was accepted directly or corrected from visible/video evidence.

Track B reports the practical benefit and cost of human verification.

### Required comparison

For every experiment configuration report:

$$
\Delta_\text{human}
=
Score_\text{human-verified}
-
Score_\text{machine-only}
$$

Also report verification time. A system that gains a small amount of score but requires very long manual inspection should not be considered operationally superior without stating that trade-off.

---

# 6. Dataset and Query Protocol

## 6.1 Freeze query sets

Create immutable query manifests.

Recommended structure:

```text
benchmark/
├── queries/
│   ├── dev/
│   └── test/
├── ground_truth/
│   ├── dev_gt.json
│   └── test_gt.json
└── query_manifest.csv
```

`query_manifest.csv` should contain:

```text
query_id
query_type
query_text
num_events
query_pack
difficulty_tag
notes
```

## 6.2 Development vs test

Use:

- **DEV**: tuning retrieval weights, RRF parameters, prompt variants, temporal parameters.
- **TEST**: final frozen evaluation only.

Do not optimize parameters using TEST scores.

If only organizer query packs are available, freeze one subset before tuning and state the split explicitly in the report.

## 6.3 Stratification

Report results separately for:

### KIS

- mostly visual
- OCR-heavy
- ASR/speech-heavy
- object/action-heavy
- multi-clause
- temporal-context KIS

### QA

- visual attribute
- count
- OCR
- spoken/narrated fact
- mixed visual + text evidence

### TRAKE

- 2–3 events
- 4–5 events
- 6+ events
- visually similar neighboring events
- long temporal gaps
- mixed visual/ASR/OCR events

These tags are diagnostic only and must not modify the ground truth.

---

# 7. Reproducibility Manifest

Every experiment run must store a manifest.

Example:

```yaml
experiment_id: exp_2026_09_12_b04
git_commit: 6ed20a5bf2ce3acc3069d3964c5f6c1c399d924c
query_split: test_v1
retrieval_profile: competition_default
agent_profile: openai_gpt4o
agent_execution_mode: direct
query_expansion: true
max_variants: 5
rrf_enabled: true
rrf_k: 60
reranker_enabled: false
temporal_delta_t_max_ms: 180000
temporal_min_match_ratio: 0.67
milvus_collection: ...
elasticsearch_index: ...
dataset_snapshot: ...
model_registry_hash: ...
retrieval_profile_hash: ...
agent_config_hash: ...
machine:
  cpu: ...
  ram_gb: ...
  gpu: ...
  vram_gb: ...
software:
  python: ...
  cuda: ...
  milvus: ...
  elasticsearch: ...
```

Never report only a profile name. Store the resolved parameter values because configuration files may change later.

---

# 8. Current Repository Baseline to Freeze

The current repository exposes a hybrid architecture consisting of:

- Milvus/Zilliz vector retrieval,
- Elasticsearch lexical/metadata retrieval,
- PostgreSQL metadata resolution,
- RRF/weighted fusion,
- optional query expansion and LLM query planning,
- optional reranking,
- Adaptive Temporal Search for TRAKE,
- human inspection through the web UI.

The inspected `competition_default` profile currently contains, among other settings:

```text
semantic_weight       = 0.60
metadata_weight       = 0.30
temporal_weight       = 0.05
user_boost_weight     = 0.05
RRF k                 = 60
query expansion max   = 5
Milvus top_k/model    = 100
Milvus final_top_k    = 50
OCR boost             = 3.0
ASR boost             = 2.5
object boost          = 1.8
caption boost         = 5.0
delta_t_max_ms        = 180000
min_match_ratio       = 0.67
```

These values define a **baseline snapshot**, not universally optimal parameters. All tuned configurations must be given distinct experiment IDs.

---

# 9. Primary Effectiveness Metrics

## 9.1 Metrics required for all tasks

Report:

- **Official Mean of Top-k R-Score**
- `R@1`
- `R@5`
- `R@20`
- `R@50`
- `R@100`
- number of queries
- number and rate of zero-score queries
- mean score by task
- median per-query score
- total package score

When comparing systems on the same query set, also report a 95% bootstrap confidence interval over queries.

## 9.2 KIS supporting metrics

Report:

- `Hit@1`
- `Hit@5`
- `Hit@20`
- `Hit@50`
- `Hit@100`
- Mean Reciprocal Rank (MRR)
- median first-correct rank
- `Coverage@100`
- correct-video recall
- correct-frame-within-GT-window recall

### Why Coverage@100 matters

Because the actual workflow uses machine search followed by human verification, `Coverage@100` answers:

> Did the machine place at least one valid answer in the candidate set available to the human?

The official score remains the primary metric because it also rewards early ranking.

## 9.3 QA supporting metrics

Separate retrieval from answer generation.

### Retrieval metrics

Compute KIS-style metrics while ignoring the answer field:

- evidence `Hit@K`
- evidence MRR
- evidence `Coverage@100`

### End-to-end metrics

Compute:

- official QA Final Score
- exact-answer accuracy conditional on correct evidence
- answer accuracy overall
- frame-correct but answer-wrong rate
- answer-correct but frame-wrong rate
- invalid answer length rate

This decomposition identifies whether failures come from search or answer generation.

## 9.4 TRAKE supporting metrics

Report:

- official TRAKE Final Score
- correct-video `Hit@K`
- mean event match ratio
- full-sequence accuracy
- full-sequence `Hit@K`
- mean number of matched events
- order-validity rate
- exact-N-frame validity rate
- median first full-sequence rank
- `Coverage@100` for at least one full sequence
- average frame-to-GT-window distance for missed events

For each missed event, define distance to the accepted interval as:

$$
d(frame,[s,e])=
\begin{cases}
s-frame,& frame<s\\
0,& s\le frame\le e\\
frame-e,& frame>e
\end{cases}
$$

Report this only as a diagnostic metric; it is not part of official scoring.

---

# 10. Candidate-generation Diagnostics

The report should explain _why_ a correct result was or was not retrievable.

For every query, store:

```text
semantic_candidate_count
text_candidate_count
candidate_union_count
semantic_correct_candidate_present
text_correct_candidate_present
union_correct_candidate_present
post_fusion_correct_candidate_present
post_rerank_correct_candidate_present
```

Then compute:

- semantic-only candidate recall,
- text-only candidate recall,
- hybrid union candidate recall,
- loss caused by fusion/ranking,
- loss caused by diversification,
- loss caused by reranking.

This distinction is essential:

> If the correct frame never enters the candidate pool, changing final ranking weights cannot fix the failure.

---

# 11. Agent / Query-planning Diagnostics

The current system uses structured query planning and expansion. Measure it as a component, not as an opaque feature.

Record per query:

```text
planner_used
planner_model
planner_fallback
planner_latency_ms
num_semantic_variants
num_text_variants
num_temporal_events
visual_weight
text_weight
asr_weight
caption_weight
ocr_weight
```

Report:

- planner success rate,
- fallback rate,
- malformed-plan rate,
- mean number of variants,
- mean planner latency,
- optional API cost per query,
- score improvement over no-agent baseline.

For TRAKE also log each event-level query and event-level retrieval weights.

---

# 12. Latency Benchmark

Effectiveness alone is insufficient for an interactive competition search system.

## 12.1 End-to-end latency

Measure from API request arrival until complete ranked results are returned.

Report:

- p50
- p90
- p95
- p99
- mean
- standard deviation

Report separately for KIS, QA, and TRAKE.

## 12.2 Stage-level latency

Instrument at least:

```text
query_normalization_ms
agent_planner_ms
query_embedding_ms
milvus_search_ms
elasticsearch_search_ms
catalog_resolution_ms
fusion_ms
reranker_ms
temporal_search_ms
qa_generation_ms
persistence_ms
total_ms
```

The current retrieval service already records total `latency_ms`; add stage timers for the benchmark build.

## 12.3 Cold vs warm latency

Run two modes:

### Cold

- clear application-level query-plan cache where practical,
- no intentional repeated query,
- first request after service startup is reported separately.

### Warm

- services already initialized,
- model servers loaded,
- indexes available,
- repeated benchmark after warm-up.

Do not mix cold and warm latency in one number.

## 12.4 Repetition

For deterministic retrieval stages:

- run each latency configuration at least 3 times,
- report median across repeats.

For LLM-planned experiments:

- run effectiveness repeatedly if planner output is not guaranteed deterministic,
- report mean score and standard deviation across runs,
- preserve every generated plan.

---

# 13. Throughput and Multi-user Test

The competition UI may be used by several team members. Measure both single-user and concurrent load.

Recommended concurrency:

```text
1
3
5
```

For each level report:

- queries/second,
- p50 latency,
- p95 latency,
- error rate,
- timeout rate.

Use the same fixed query pool and do not compare throughput experiments that use different model/index configurations.

---

# 14. Resource and Cost Metrics

Collect:

- CPU utilization,
- process RAM,
- GPU utilization,
- peak VRAM,
- Milvus query latency,
- Elasticsearch query latency,
- network bytes if remote services dominate latency,
- LLM calls/query,
- LLM cost/query when paid APIs are used.

Recommended summary:

| Variant | GPU peak | RAM peak | p95 latency | LLM calls/query | Cost/query | Official score |
| ------- | -------: | -------: | ----------: | --------------: | ---------: | -------------: |

This exposes the quality/latency/cost trade-off.

---

# 15. Human Verification Protocol

Human evaluation must be controlled.

## 15.1 Operator role

The operator may:

- inspect ranked thumbnails,
- inspect neighboring frames,
- open the source video,
- verify temporal order,
- choose/reorder candidates for final submission,
- verify/correct the QA answer from retrieved evidence.

The operator may not:

- type a new retrieval query,
- manually search the dataset outside the system,
- use ground truth,
- add a candidate that the machine did not retrieve.

This keeps the test consistent with the intended **machine search -> human check** workflow.

## 15.2 Human metrics

Record:

```text
verification_start
verification_end
verification_time_s
num_candidates_opened
num_videos_opened
num_context_frames_viewed
machine_top1_accepted
machine_result_reordered
qa_answer_edited
final_selected_rank
```

Report:

- median verification time/query,
- p95 verification time/query,
- mean candidates inspected,
- top-1 acceptance rate,
- human correction rate,
- score after verification,
- score delta from machine-only.

## 15.3 Optional efficiency metric

Report:

$$
Utility =
\frac{\Delta Score_\text{human}}
{\text{median verification time in minutes}}
$$

This is not an official competition metric, but it makes the human-in-the-loop trade-off explicit.

---

# 16. Core Ablation Matrix

Run ablations on the same frozen TEST set.

## A0 — Visual baseline

```text
single visual embedding
query expansion OFF
agent planning OFF
text retrieval OFF
reranker OFF
```

Purpose: establish the pure semantic visual baseline.

## A1 — Text baseline

```text
visual retrieval OFF
Elasticsearch metadata/ASR/OCR/caption retrieval ON
agent planning OFF
reranker OFF
```

Purpose: measure lexical evidence alone.

## A2 — Hybrid without RRF

```text
visual ON
text ON
weighted fusion
RRF OFF
agent OFF
```

Purpose: isolate hybrid candidate benefit from rank fusion.

## A3 — Hybrid + RRF

```text
visual ON
text ON
RRF ON
agent OFF
```

Purpose: quantify RRF improvement.

## A4 — Hybrid + RRF + Query Expansion

```text
query expansion ON
agent planning OFF or deterministic expansion baseline
```

Purpose: quantify multi-view recall improvement.

## A5 — Hybrid + RRF + Agent Planner

```text
agent planning ON
dynamic modality weights ON
query expansion ON
```

Purpose: quantify structured query interpretation.

## A6 — + Reranker

```text
same as A5
reranker ON
```

Purpose: determine whether reranking improves early-rank quality after high-recall retrieval.

## A7 — Multi-visual-model fusion

Only run when all embedding collections are correctly populated and mapped.

Example:

```text
CLIP only
vs.
SigLIP2 only
vs.
CLIP + SigLIP2 visual RRF
```

Purpose: measure fine-grained visual ensemble benefit.

Do not report a model ensemble if one collection is incomplete or uses incompatible frame mapping.

---

# 17. Agent Ablations

Run:

```text
max_variants = 1
max_variants = 3
max_variants = 5
```

Compare:

- official score,
- candidate recall,
- latency,
- LLM cost,
- duplication rate among retrieved candidates.

Also compare:

```text
fixed visual/text weights
vs.
agent-derived visual/text weights
```

For QA and TRAKE, additionally compare:

```text
global query-level weights
vs.
event/clause-level dynamic weights
```

---

# 18. Retrieval-depth Ablations

Candidate depth affects both recall and latency.

Recommended sweep:

```text
top_k_per_model = 50
top_k_per_model = 100
top_k_per_model = 200
top_k_per_model = 500
```

Always keep output evaluation capped at the official maximum of 100 rows.

Report:

- candidate recall,
- official score,
- p95 latency,
- memory/load impact.

This experiment identifies the point where increasing recall depth stops improving the official top-100 score.

---

# 19. RRF Ablation

Recommended:

```text
RRF OFF
k = 20
k = 60
k = 100
```

For each setting report:

- official score,
- R@1/5/20/50/100,
- MRR for KIS/QA,
- TRAKE full-sequence `Hit@K`,
- p95 latency.

Use `k=60` as the current repository baseline.

---

# 20. Metadata-source Ablation

Evaluate each source and combinations:

```text
caption only
ASR only
OCR only
caption + ASR
caption + OCR
ASR + OCR
caption + ASR + OCR
```

Report results by query category, not only overall.

Expected analysis should answer questions such as:

- Does ASR improve spoken-information QA?
- Does OCR improve on-screen-text KIS/QA?
- Does caption retrieval improve fine-grained visual action search?
- Does adding a source improve recall but hurt early ranking?

Do not infer the answer in advance; measure it.

---

# 21. TRAKE-specific Protocol

The current repository uses event-wise retrieval followed by Adaptive Temporal Search with strict increasing frame order.

## 21.1 Temporal baseline

Create a naïve baseline:

- retrieve each event independently,
- choose event top-1 from the same predicted video where possible,
- enforce only increasing order.

Compare it against ATS.

## 21.2 ATS ablations

Test:

```text
ATS without compactness
ATS with compactness
prefer_full_sequences OFF
prefer_full_sequences ON
```

## 21.3 Temporal-window sweep

Recommended:

```text
delta_t_max = 60 s
delta_t_max = 120 s
delta_t_max = 180 s
delta_t_max = 300 s
```

Report:

- correct-video recall,
- event match ratio,
- full-sequence accuracy,
- official score,
- temporal-search latency.

## 21.4 Beam-width sweep

Recommended:

```text
beam_width = 100
beam_width = 200
beam_width = 400
beam_width = 800
```

Report quality against temporal-search latency.

## 21.5 Per-event candidate pruning

Sweep `per_query_video_limit` using at least:

```text
8
12
24
48
```

This measures whether temporal failure comes from ATS itself or from pruning away the correct event candidate before sequence construction.

---

# 22. Diversification Analysis

Because the system limits near-duplicate frames/videos in some profiles, evaluate diversification explicitly.

Report:

- unique videos in top 20 / top 50 / top 100,
- mean frames per video,
- near-duplicate rate,
- correct-result loss caused by diversification.

Compare:

```text
diversification OFF
vs.
diversification ON
```

A diversity strategy is beneficial only if it increases useful coverage without removing the correct candidate.

---

# 23. Failure Taxonomy

Every zero-score or low-score query should receive one primary failure label.

Recommended taxonomy:

```text
F1_QUERY_PLAN
F2_TRANSLATION_OR_EXPANSION
F3_VISUAL_EMBEDDING_RECALL
F4_TEXT_RETRIEVAL_RECALL
F5_METADATA_MISSING
F6_FUSION_RANKING
F7_DIVERSIFICATION_PRUNING
F8_RERANKER
F9_WRONG_VIDEO
F10_FRAME_LOCALIZATION
F11_QA_ANSWER
F12_TEMPORAL_EVENT_SPLIT
F13_TEMPORAL_PRUNING
F14_TEMPORAL_ORDERING
F15_GT_OR_DATA_MAPPING
F16_SYSTEM_ERROR_TIMEOUT
```

For each experiment report:

- number of failures per category,
- percentage of total failures,
- representative query IDs,
- proposed corrective action.

Do not use failure analysis to retroactively change TEST predictions.

---

# 24. Required Per-query Log

Save one JSON object per query containing at least:

```json
{
  "experiment_id": "...",
  "query_id": "...",
  "query_type": "KIS|QA|TRAKE",
  "raw_query": "...",
  "normalized_query": {},
  "agent_plan": {},
  "retrieval_profile": "...",
  "ranked_results": [],
  "official_r_at": {
    "1": 0,
    "5": 0,
    "20": 0,
    "50": 0,
    "100": 0
  },
  "official_final_score": 0,
  "first_correct_rank": null,
  "stage_latency_ms": {},
  "validation_errors": [],
  "failure_label": null
}
```

TRAKE results must also preserve event-level selected frames and event-level scores.

---

# 25. Experiment Output Structure

Recommended structure:

```text
experiments/
└── <experiment_id>/
    ├── manifest.yaml
    ├── queries.csv
    ├── raw_results/
    │   └── <query_id>.json
    ├── submission/
    │   └── *.csv
    ├── metrics/
    │   ├── aggregate.json
    │   ├── per_query.csv
    │   ├── per_task.csv
    │   ├── latency.csv
    │   ├── resources.csv
    │   └── failures.csv
    ├── human/
    │   └── verification.csv
    └── logs/
```

Never overwrite a completed experiment directory.

---

# 26. Step-by-step Execution Procedure

## Phase 0 — Freeze

1. Checkout the target commit.
2. Save commit SHA.
3. Freeze retrieval profile.
4. Freeze agent configuration.
5. Freeze model registry.
6. Record Milvus collection names.
7. Record Elasticsearch index/version.
8. Freeze query split and GT.
9. Create experiment ID.

## Phase 1 — Validate system

Run:

- backend health check,
- Milvus connectivity,
- Elasticsearch connectivity,
- database connectivity,
- model endpoint checks,
- sample KIS,
- sample QA,
- sample TRAKE.

Abort the experiment if a required component silently falls back when the experiment claims that component is enabled.

## Phase 2 — Validate scorer

Before measuring models, create synthetic predictions that test:

- correct rank 1,
- first correct at rank 5,
- first correct at rank 20,
- first correct at rank 50,
- first correct at rank 100,
- no correct result,
- QA wrong case/spacing if exact comparison is expected,
- TRAKE missing frame,
- TRAKE extra frame,
- TRAKE wrong event order,
- TRAKE partial event match.

The expected score must be asserted automatically.

## Phase 3 — Warm-up

Before warm-latency runs:

- issue several non-scored requests,
- ensure model processes are loaded,
- ensure connection pools are initialized.

Do not include warm-up requests in benchmark metrics.

## Phase 4 — Machine-only effectiveness

For each query:

1. Send exactly one search request.
2. Save raw response.
3. Export up to top 100.
4. Do not manually edit.
5. Score with organizer-compliant local scorer.
6. Save per-query metrics.

## Phase 5 — Human verification

Using the same raw machine ranking:

1. Start timer when results become visible.
2. Human inspects candidates.
3. Human selects/reorders only retrieved candidates.
4. Human confirms QA answer using retrieved evidence.
5. Stop timer when final answer/submission row is ready.
6. Save interaction log.
7. Score the human-verified output separately.

## Phase 6 — Latency and load

Run:

- cold single-query measurement,
- warm repeated measurement,
- concurrency 1,
- concurrency 3,
- concurrency 5.

Store stage timings and system resources.

## Phase 7 — Aggregate

Produce:

- overall table,
- per-task table,
- ablation table,
- latency table,
- resource table,
- human-verification table,
- failure distribution.

## Phase 8 — Final test lock

After choosing the best DEV configuration:

1. freeze it,
2. run TEST once for the primary result,
3. do not retune on TEST,
4. save the complete artifact directory.

---

# 27. Statistical Reporting

For effectiveness:

- report per-query scores,
- macro mean across queries,
- sum when matching the competition package scoring presentation,
- 95% bootstrap confidence interval for system comparisons.

For latency:

- report p50/p90/p95/p99,
- at least 3 repeated benchmark passes,
- distinguish cold and warm.

For non-deterministic planner experiments:

- use multiple full runs,
- report mean ± standard deviation,
- store planner output for every run.

When claiming one variant is better, report both absolute and relative differences:

$$
\Delta = Score_B - Score_A
\]


$$

\%\Delta =
\frac{Score_B-Score_A}{Score_A}\times100

$$

if \(Score_A>0\).

---

# 28. Main Technical-report Tables

## Table A — Overall effectiveness

| System | KIS Score | QA Score | TRAKE Score | Overall | R@1 | R@5 | R@20 | R@50 | R@100 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A0 Visual | | | | | | | | | |
| A1 Text | | | | | | | | | |
| A3 Hybrid + RRF | | | | | | | | | |
| A5 + Agent | | | | | | | | | |
| A6 + Reranker | | | | | | | | | |

## Table B — KIS/QA ranking diagnostics

| System | Hit@1 | Hit@5 | Hit@20 | Hit@100 | MRR | Median first-correct rank |
|---|---:|---:|---:|---:|---:|---:|
| | | | | | | |

## Table C — TRAKE

| System | Correct-video | Mean event match | Full sequence @1 | Full sequence @100 | Official score |
|---|---:|---:|---:|---:|---:|
| | | | | | |

## Table D — Latency

| System | Task | p50 | p95 | Planner | Milvus | ES | Fusion | Rerank | Temporal |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| | | | | | | | | | |

## Table E — Human verification

| Task | Machine score | Human-verified score | Delta | Median verify time | Top-1 accept |
|---|---:|---:|---:|---:|---:|
| KIS | | | | | |
| QA | | | | | |
| TRAKE | | | | | |

## Table F — Resource/cost trade-off

| System | Peak VRAM | Peak RAM | p95 latency | API cost/query | Official score |
|---|---:|---:|---:|---:|---:|
| | | | | | |

---

# 29. Minimum Benchmark Set for the Technical Report

If experiment time is limited, the minimum defensible benchmark is:

1. **Official scorer compliance test.**
2. **Machine-only official score** for all three task types.
3. **R@1/5/20/50/100**.
4. **KIS/QA Hit@K + MRR**.
5. **TRAKE event match + full-sequence accuracy**.
6. **Candidate recall before fusion**.
7. **p50/p95 end-to-end latency**.
8. **At least four ablations:**
   - visual only,
   - text only,
   - hybrid + RRF,
   - hybrid + RRF + agent.
9. **TRAKE ATS vs naïve baseline.**
10. **Human-verification score delta and verification time.**
11. **Failure taxonomy.**
12. **Frozen configuration and commit SHA.**

This set is sufficient to show both algorithmic contribution and competition usability.

---

# 30. Competition-specific Reporting Notes

The organizer allows up to 100 answers per query, and the official metric rewards the best R-Score found at rank thresholds 1, 5, 20, 50, and 100. Therefore:

- optimizing only Top-1 accuracy is insufficient;
- optimizing only Recall@100 is also insufficient;
- the system should maximize early correct ranking while preserving candidate coverage.

The public leaderboard uses only part of the organizer ground truth, whereas final ranking is determined from the full private evaluation. Public-leaderboard results should therefore not be treated as an unbiased TEST set for repeated tuning.

Because only a limited number of submissions are allowed per query package and the last submission is used for ranking, all large ablations should be executed locally. Official submission attempts should be reserved for final validation rather than hyperparameter search.

---

# 31. Recommended Primary Claim Format

A technical-report result should be written in the following form:

> Using the frozen TEST set and organizer-compatible Mean of Top-k R-Score, configuration **X** achieved **S** overall, compared with **B** for the visual-only baseline. Hybrid retrieval changed candidate Coverage@100 from **C1** to **C2**, while RRF changed early ranking R@5 from **R1** to **R2**. The median search latency was **L50 ms** and p95 was **L95 ms**. Under verification-only human evaluation, score changed by **ΔS** with a median verification cost of **T seconds/query**.

This makes the claim measurable and reproducible.

---

# 32. Final Checklist Before Publishing Benchmark Numbers

- [ ] Correct commit SHA recorded.
- [ ] Query split frozen.
- [ ] Ground truth frozen.
- [ ] No TEST-set tuning.
- [ ] Local scorer matches organizer rules.
- [ ] QA equality behavior validated.
- [ ] TRAKE exact event-count validation enabled.
- [ ] Maximum 100 rows enforced.
- [ ] Raw machine results preserved.
- [ ] Human-edited results stored separately.
- [ ] Retrieval configuration fully serialized.
- [ ] Model/index versions recorded.
- [ ] All fallback events recorded.
- [ ] Cold and warm latency separated.
- [ ] Stage-level latency recorded.
- [ ] Candidate-generation recall measured.
- [ ] Official score reported as primary.
- [ ] Secondary metrics clearly labeled.
- [ ] Ablations use identical query sets.
- [ ] Failed/timeout queries included rather than silently removed.
- [ ] Statistical uncertainty reported where appropriate.
- [ ] Report tables can be regenerated from saved experiment artifacts.

---

# 33. Source Basis

This protocol was designed from:

1. `aic_2026_requirements(1).html` — AIC 2026 query/output/submission rules.
2. `codabench_scoring.md` — official Mean of Top-k R-Score and task-specific R-Score definitions.
3. `https://github.com/zintomvn/Multimodal-Retrieval` — current system implementation and experiment configuration.
4. Repository snapshot inspected at commit `6ed20a5bf2ce3acc3069d3964c5f6c1c399d924c`.
5. Relevant repository components:
   - `scripts/benchmark_retrieval.py`
   - `configs/retrieval_profiles.yaml`
   - `configs/agent.yaml`
   - `configs/model_registry.yaml`
   - `apps/backend/app/modules/retrieval/service.py`
   - `apps/backend/app/modules/temporal/ats.py`

The organizer-compatible score should remain the primary result even when additional research metrics such as MRR, Recall/Hit@K, latency, or human verification efficiency are reported.
$$

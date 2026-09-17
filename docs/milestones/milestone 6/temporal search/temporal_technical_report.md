# DEV Temporal Retrieval: Technical Report

## Preliminary

### Objective

DEV (Diagnostic Event Video-first) answers long KIS video queries containing
ordered visual events over sparse AutoShot keyframes. It avoids treating
independently retrieved frames as one story, while avoiding an expensive search
of every frame in every video for every event.

The deployed DEV route is independent of legacy ATS. It uses a local,
Vortex-style same-video anchor expansion after global diagnostic selection;
`dev_first_search` does not call ATS or the legacy Vortex candidate filter.

### Input and output

Input is a `SearchRequest` with `temporal_strategy="dev_first_search"`. The
planner returns ordered events, importance, diagnostic prior, English semantic
views, Vietnamese SigLIP2 views, lexical views, optional anchor, and temporal
edges. Output is up to `top_k` results, each with a representative frame,
matched same-video evidence, score breakdown, and optional context samples.

### Verified invariants

| Invariant                | Enforcement                                                                                                                           |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| No ATS in DEV            | Static audit confirms no ATS/legacy-Vortex calls in `dev_first.py`.                                                                   |
| Bounded global work      | One primary English view/event plus at most one bilingual re-probe of the chosen diagnostic event; pool target `top_k + 80`, cap 160. |
| Local means local        | Second Milvus/text retrieval receives `allowed_video_ids`.                                                                            |
| Same-video ordering      | Candidates are grouped by video and extension requires strictly increasing time.                                                      |
| Correct units            | Millisecond deltas apply only with timestamps; otherwise frame-index order only.                                                      |
| Agent evidence survives  | Importance, prior, retrieval/text weights and `siglip2_views` survive normalization.                                                  |
| Sampling is not evidence | Dynamic samples have score zero and do not count as matched events.                                                                   |

## Methodology

### 1. Plan normalization

The planner turns the query into one to five independently retrievable visual
events. English `query`/`multi_views` serve CLIP/OpenCLIP. Vietnamese
`siglip2_views` serve local SigLIP2. Text views remain lexical evidence for
ASR, captions, and OCR. Weights are clamped to `[0,1]`; missing adjacent edges
are generated with unknown gap. A long fallback event may be atomized only in
DEV, while dividing its parent importance across children.

### 2. Global probes and diagnostic event

For each event, DEV retrieves `P` global frames using its primary English view:

```text
P = max(diagnostic_probe.top_k_per_event, top_k + output_video_buffer)
```

For `top_k=5`, `P=85`. Candidate event score combines log rank `r`, raw
semantic score `a` when available, and hybrid retrieval score `h`:

```text
s_i(frame) = w_rank*r + w_absolute*a + w_hybrid*h
```

The diagnostic event combines planner prior and probe quality:

```text
diagnostic(i) = 0.55 * planner_prior(i) + 0.45 * probe_score(i)
probe_score = 0.4 * concentration + 0.3 * margin + 0.3 * top_confidence
```

Concentration measures whether high ranked evidence is focused in few videos;
margin distinguishes the best frame from its tail.

After selection, DEV performs at most one additional global re-probe of the
diagnostic event using its grounded Vietnamese SigLIP2 view (when supplied).
This preserves bilingual recall for hard visual states without applying an
extra SigLIP2 view to every global event search.

### 3. Diverse video selection

DEV aggregates diagnostic support, importance-weighted cross-event support,
and importance-weighted coverage:

```text
video_score(v) = alpha*diagnostic(v)
               + beta*sum_i(w_i * event_score_i(v))/sum_i(w_i)
               + gamma*sum_{i matched by v}(w_i)/sum_i(w_i)
```

Before aggregate fill, it reserves two unseen videos per event, ordered by
importance. This prevents repetitive frames from one video consuming the local
budget. The pool is 85 videos for `top_k=5`, 130 for `top_k=50`, capped at 160.

### 4. Local constrained retrieval

Every event, including the diagnostic event, is retrieved again with
`allowed_video_ids`. This is a new filtered vector/text retrieval, not merely a
post-filter. The local pass uses up to two English semantic views plus one
Vietnamese SigLIP2 view. It starts at 200 results and widens to 400 only below
24 candidates. Timestamp NMS keeps at most 12 candidates/video/event.

### 5. Local Vortex-style construction

Candidates are grouped by video. DEV normally expands every diagnostic-event
candidate to best compatible events on both sides. A transition is valid when:

```text
time(next) > time(previous)
and (gap <= edge_delta, if an upper bound exists)
```

Gap classes are short=45 s, medium=120 s, loose=300 s, unknown=unbounded.
Without timestamps, DEV requires only increasing `frame_idx` and never converts
milliseconds to frames. If a selected video lacks the diagnostic event, the
highest-importance available event is used as an `event_fallback` anchor. The
missing diagnostic event still receives a scoring penalty.

### 6. Sequence scoring, filtering, and output frame

For a chain, DEV combines weighted evidence `E`, coverage `C`, strong coverage,
diagnostic support `D`, missing-event penalty, and compactness penalty `G`:

```text
sequence_score = cE*E + cC*C + cS*C_strong + cD*D
               - cM*(1-C) - cG*G
```

`G` is mean timestamp gap normalized by the configured 45-second reference.
It is zero for timestampless chains. Score is bounded to `[0,1]`. Sequence NMS
limits equivalent chains and caps results per video. Multi-event queries
default to two semantic matches; explicit `min_match=1` supports controlled
sparse-AutoShot diagnostics.

Frame-scoped queries return their requested anchor event. Video-sequence
queries return the highest calibrated matched event. Only after final top-k
truncation, DEV attaches at most one zero-score dynamic context sample for an
uncovered interval. It is flagged `is_dynamic_sample=true` and never changes
ranking.

### Pseudocode

```text
DEV_SEARCH(query, K):
    plan = PLAN_AND_NORMALIZE(query)
    E, W, edges = plan.events, plan.importance, plan.edges
    P = max(global_probe_size, K + output_video_buffer)

    for event in E:
        global[event] = CALIBRATE(GLOBAL_RETRIEVE(primary_english(event), P))

    d = ARGMAX(planner_prior + probe_quality(global))
    videos = DIVERSE_SELECT(global, d, W, candidate_limit(K))

    for event in E:
        views = local_english_views(event) + local_vietnamese_siglip2_view(event)
        local[event] = NMS(LOCAL_RETRIEVE(views, allowed_videos=videos, limit=200))
        if SIZE(local[event]) < 24:
            local[event] = NMS(LOCAL_RETRIEVE(views, videos, limit=400))

    sequences = []
    for video in VIDEOS(local):
        anchors = local[d][video]
        if EMPTY(anchors): anchors = BEST_AVAILABLE_EVENT_ANCHORS(video, W)
        for anchor in anchors:
            chain = BEST_COMPATIBLE_LEFT(anchor) + anchor + BEST_COMPATIBLE_RIGHT(anchor)
            if MATCHED_EVENTS(chain) >= min_match:
                sequences.add(SCORE(chain, W, d))

    return ADD_CONTEXT_SAMPLES(TOP_K(SEQUENCE_NMS(sequences), K))
```

### Worked example

For a cooking narrative: (1) orange vegetable enters broth, (2) green
vegetables and mushrooms enter, (3) sliced meat is poured in; use weights
`[0.2, 0.3, 0.5]`. If `V42` retrieves them at 10.0 s, 17.5 s, and 24.0 s, the
chain is ordered and satisfies its windows. Coverage is 1.0 and the distinctive
high-weight final event strongly contributes. A video with only one generic pot
frame at 100 s has lower coverage and larger gap penalty. If AutoShot misses
event 2, DEV can retain coverage 0.7; a contextual sample may be shown but
does not alter the semantic score.

## Complexity

Let `n` be events (`n <= 8`), `P` global candidates/event, `V` selected videos
(`V <= 160`), `L` local candidates/event (200--400 before NMS), `b` the
per-video/event cap (12), and `K` requested results.

| Phase                     | Time complexity                  | Space complexity       |
| ------------------------- | -------------------------------- | ---------------------- |
| Global retrieval          | `O((n+1) * R_global(P))` at most | `O(nP)`                |
| Video score and diversity | `O(nP + V log V)`                | `O(nP)`                |
| Local retrieval           | `O(n * R_local(V,L))`            | `O(nL)`                |
| Local NMS                 | `O(nL log L)` worst case         | `O(nL)`                |
| Temporal construction     | `O(V * n * b^2)`                 | `O(V*n*b)` plus chains |
| Sequence NMS and output   | `O(S log S + K*n)`               | `O(S + K*n)`           |

`R_global` and `R_local` are remote embedding/Milvus/text costs and dominate
runtime. The one-view global budget and bounded local views are the primary
speed controls. Dynamic sampling costs at most `O(K*n)` interval lookups
because it runs only after top-k truncation.

## Advantages

- Retains agent-generated importance, diagnostic priors, visual/text weights,
  text-source weights, and bilingual SigLIP2 semantic views.
- Separates video recall from frame precision using a real second retrieval.
- Enforces event-diverse video coverage before costly local retrieval.
- Supports close events and edge-specific deltas without a hidden FPS
  conversion.
- Uses a transparent fallback anchor when sparse AutoShot misses the chosen
  diagnostic event, but preserves missing-event penalties and min-match noise
  control.
- Makes each result auditable through selected video count, diagnostic scores,
  event evidence, sequence components, anchor source, and context samples.
- Produces more pre-filter candidates than requested results: in the live
  smoke run, 27 sequences were available before returning top 5.

## Disadvantages and threats to validity

- A target video omitted from every global event probe cannot be recovered by
  local temporal reasoning.
- Greedy local expansion is efficient but can miss a lower-scoring intermediate
  frame that would form a stronger complete chain than the greedy choice.
- Cross-model absolute similarities are empirically calibrated; they are not
  inherently identical scales.
- Without timestamps, order remains valid but physical-duration and
  compactness constraints cannot be measured safely.
- Planner decomposition and prompt compliance remain a source of recall error;
  a generic event can hide discriminative context.
- Dynamic sampling helps inspect an AutoShot gap but intentionally cannot
  manufacture semantic evidence.
- Benchmark outcome is empirical and depends on the stored planner output;
  it must not be inferred from the method description alone.

## Benchmark protocol and measured result

### Sources and reproducibility record

The benchmark queries and labels are from
`data/experiments/aic_2026_groundtruth/aic2026_round_3.csv`. DEV configuration
is from `configs/retrieval_profiles.yaml`; planner prompt/profile selection is
from `configs/agent.yaml`; implementation is in
`apps/backend/app/modules/retrieval/service.py` and
`apps/backend/app/modules/retrieval/temporal/dev_first.py`.

All five requests used: dataset `917efbbe-44b8-4476-98ef-3e7ec7ca7958`,
`top_k=5`, `dev_first_search`, `gpt-5-nano`, visual mode `both`,
`delta_t_max_ms=180000`, no query expansion, and no external reranker. The
persisted run names are `dev-paper-final-query-p2-*-kis`; their normalized LLM
plans are retained in the local `query_runs` table, which is the appropriate
artifact to report for exact LLM-plan reproducibility.

The numeric field in each benchmark label is treated as `frame_idx`, not a
timestamp. A strict GT-video result below means a final top-5 item has exactly
the requested `video_code`; frame delta is shown only for such a hit.

| Query | GT (`video`, `frame_idx`) | Runtime | Candidate videos | Sequence pool | Min-match | GT video rank | Returned frame index | Absolute frame delta |
| ----- | ------------------------- | ------: | ---------------: | ------------: | --------: | ------------: | -------------------: | -------------------: |
| p2-1  | `L26_V424`, 5111          | 70.91 s |               85 |            45 |         2 |             — |                    — |                    — |
| p2-2  | `L22_V026`, 1514          | 42.13 s |               85 |            30 |         2 |             1 |                 1534 |                   20 |
| p2-3  | `L26_V390`, 6010          | 43.68 s |               85 |            56 |         2 |             — |                    — |                    — |
| p2-4  | `L25_V075`, 975           | 59.02 s |               85 |            42 |         2 |             — |                    — |                    — |
| p2-11 | `L26_V483`, 599           | 52.85 s |               85 |            57 |         2 |             — |                    — |                    — |

The measured GT-video recall@5 is **1/5** for this final run. The average
request time is 53.72 seconds. Every query returned the requested five items,
and every sequence pool exceeded `top_k`; therefore the observed misses are
not caused by output truncation or an undersized candidate-video pool.

This result is not sufficient to claim benchmark success. The most direct
remaining source of error is global/event semantic recall: a video not present
in candidate evidence cannot be restored by local temporal construction. The
paper should report this limitation and the exact stored plan/run artifacts,
rather than asserting that all five labels are solved.

## Verification

The audit added DEV tests for preserving SigLIP2 views/prior, timestamp-less
ordering, timestamp-less NMS, and diagnostic-missing fallback anchoring.

```text
pytest -q tests/test_dev_first.py tests/test_aithena_ats.py
23 passed

pytest -q tests/test_retrieval_pipeline.py -k 'dev_first or temporal_search'
3 passed

python -m compileall -q app/modules/retrieval
passed
```

An end-to-end p2-1 DEV smoke request completed without a search error: five
results, 85 candidate videos, 27 pre-truncation sequences, timestamp-based
coordinates, and metadata reporting these stages:
`global_diverse_video_selection`, `local_video_retrieval`,
`vortex_temporal_rescore`, and `dev_noise_filter`.

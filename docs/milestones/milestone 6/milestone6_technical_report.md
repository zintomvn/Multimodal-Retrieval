# Multimodal-Retrieval: Diagnostic Event Video-first Temporal Search

**Technical report - AI Challenge TP.HCM 2026**  
**Milestone 6 | September 2026**

## Abstract

This report describes the temporal video-retrieval system used in our AI Challenge TP.HCM 2026 workflow and the Milestone 6 improvement, **Diagnostic Event Video-first search (DEV)**. The system indexes video keyframes with visual embeddings and textual evidence, then combines the evidence through hybrid retrieval. DEV addresses a recurrent weakness of frame-first temporal search: a long narrative query can produce many locally plausible frames without identifying the correct source video. It first decomposes a query into visual events, selects the most diagnostic event by combining an LLM prior and a small retrieval probe, retrieves candidate videos from that event, and only then searches remaining events inside those videos. The final sequence builder is timestamp-aware, respects per-transition gap classes, permits partial evidence without treating a missing event as free, and returns either a requested frame or a representative frame for a video-sequence result. DEV is implemented as a third selectable strategy; the existing Vortex K-context and AIThena weighted ATS baselines remain unchanged.

**Keywords:** video retrieval, multimodal retrieval, temporal search, LLM query planning, keyframe search, diagnostic event.

## 1. Introduction

AI Challenge queries frequently describe a chain of actions, scenes, or changes in a video rather than a single image. A retrieval system must therefore align visual semantics, OCR/caption/ASR evidence, and temporal order while remaining responsive enough for interactive use. Our baseline follows the broad architecture of AIThena-Vision: data preparation produces searchable keyframes and multimodal metadata; retrieval obtains semantic and textual candidates; a temporal stage validates ordered evidence. The Milestone 6 work focuses on the last stage.

ATS and Vortex are valuable baselines, but their frame-first design can spend the full retrieval budget on every event globally. In addition, frame-index distance can be inaccurate for variable-FPS videos or nonuniform keyframe sampling. DEV changes the search order to identify a likely video first, then builds an ordered sequence using `timestamp_ms`.

## 2. System architecture

### 2.1 Data preparation and indexes

The backend represents each keyframe with a video ID, frame index, timestamp, media URI, visual vector evidence, and annotations. Visual retrieval uses the configured OpenCLIP/SigLIP2-compatible embedding collections. Captions, OCR, detected objects, and ASR-related text are available to the hybrid metadata path. Metadata is searchable through the existing text-search adapter; frame/video metadata is stored in the application database.

This separation preserves a practical property for competition use: visual evidence can retrieve an uncommon object or action, while text evidence can resolve spoken facts, visible text, or captions that cannot reliably be recovered from pixels alone.

### 2.2 Query planning and hybrid candidate generation

An optional LLM planner converts a Vietnamese or English request into concise English semantic views and lexical text views. For temporal KIS with DEV selected, planning is LLM-first: it decides whether a request is a `single_event`, `ordered_sequence`, or `narrative_sequence`, emits independently retrievable events, estimates each event's `importance` and `diagnostic_prior`, and emits adjacent edges with `short`, `medium`, `loose`, or `unknown` semantic gaps. Structural validation and the deterministic parser remain a safe fallback if planning is unavailable or invalid.

Each event uses the existing multi-perspective fusion route: semantic views are ranked through the embedding retrieval stack, text views through metadata retrieval, and results are fused with the existing RRF/hybrid score. The new component does not introduce another embedding model or VLM reranker.

## 3. DEV temporal retrieval

### 3.1 Diagnostic event video-first search

For events $E_1,\ldots,E_n$, DEV performs a small global probe for each event. The probe records top calibrated evidence, score margin, and the number of distinct videos. The diagnostic score combines the planner's relative distinctiveness prior with deterministic probe selectivity. The highest-scoring event is retrieved globally at a deeper candidate budget, then temporal non-maximum suppression removes near-duplicate frames within a `(video_id, event_index)` group.

Candidate videos are scored from the best and top-$m$ diagnostic-frame evidence; raw duplicate count is deliberately not a reward. Remaining events are searched and retained within the candidate-video set. If an event has too few retained candidates, retrieval widens progressively; the final recall fallback is global event retrieval followed by the same DEV timestamp sequence scorer, never a silent switch to ATS or Vortex.

### 3.2 Timestamp-aware sequence construction

DEV uses a bounded beam dynamic program per video. A state may match the current event or skip it. A match is valid only if its timestamp follows the prior matched event and any supplied edge maximum is respected. `frame_idx` is used only as an explicitly labelled degraded fallback when timestamp data are unavailable; DEV never converts milliseconds to frames using a fixed FPS.

For a candidate sequence $s$, unmatched events remain in the denominator:

$$E(s) = \frac{\sum_{i=1}^{n} w_i m_i}{\sum_{i=1}^{n} w_i}, \qquad m_i=0\ \text{when event }i\text{ is missing}.$$

The final bounded score combines weighted evidence, weighted coverage, strong-event coverage, a small diagnostic-event term, a missing-event penalty, and smooth temporal compactness penalty. This avoids both failure modes: a weak complete chain does not automatically beat two strong matches, and one excellent generic frame does not automatically beat a strong well-supported chain.

### 3.3 Output semantics and observability

For a frame-target request, the requested anchor event must be matched. For a video-sequence request, no semantic anchor is invented: the returned frame is only a presentation representative, selected by highest matched confidence. The response records target scope, representative event, diagnostic-event scores, candidate video count, fallback stages, event counts, time coordinate, and sequence evidence. These fields make a ranking decision inspectable in the search UI and in saved query runs.

## 4. Interface and operator workflow

The React search workspace supports KIS/QA/TRAKE input, ranked result browsing, sequence-frame context, result selection, and submission export. For a temporal query, an operator enters the natural-language description, selects the desired temporal strategy, reviews the candidate video and ordered frames, then selects the final frame/video for submission. The existing result schema continues to carry a thumbnail/image URL, video code, frame index, timestamp, score breakdown, and sequence frames; DEV adds no separate UI-only data path.

Recommended screenshots for the final submission export are: (i) query input with temporal strategy selected, (ii) ranked sequence result showing timestamps and constituent frames, and (iii) selected-result/submission workflow. Screenshots are intentionally not fabricated in this Markdown report; they should be captured from the deployed competition workspace so they reflect the actual environment.

## 5. Representative query analysis

The Round 3 narrative query `query-p2-9-kis` describes cream-coloured patchwork fashion models, visitors viewing colourful art on a lawn, and handmade patterned textile dolls/balls. It has no supplied ground truth, so it is useful for inspecting planning and ranking but cannot contribute to accuracy claims. DEV should produce a `narrative_sequence` with `target_scope=video_sequence`; the textile doll/ball event is expected to receive a higher diagnostic prior than the generic crowd/lawn event. The returned representative frame must not alter the sequence score.

For cooking and classroom queries with supplied ground truth, the important distinction is more operational: the algorithm must preserve event order using each keyframe's stored timestamp, while allowing a difficult intermediate event to be absent with an explicit score penalty. This makes failure diagnosis possible: an operator can distinguish a missing retrieval candidate from a temporally invalid order.

## 6. Evaluation protocol and result record

The intended evaluation uses the first five ground-truthed rows of `data/benchmarks/aic_2026_round3.csv` with identical backend, profile, top-20 depth, and query text. The comparison is `aithena_weighted_ats` versus `dev_first_search`. A video hit is counted when the ground-truth video occurs in the top 20; an exact frame hit additionally requires the ground-truth frame index. The reusable runner is `scripts/run_aic_round3_devfirst.py`; it writes `data/benchmarks/aic_2026_round3_devfirst_5.json` when the retrieval services are available.

| Strategy | Queries | Video hits@20 | Exact frame hits@20 | Notes |
|---|---:|---:|---:|---|
| AIThena weighted ATS | 5 | Pending live service | Pending live service | Baseline, unchanged |
| DEV-first | 5 | Pending live service | Pending live service | Timestamp-aware video-first search |

The live comparison was attempted against the local data copy. It could not complete because the required remote retrieval dependencies (embedding/vector/text services) do not respond in this environment; the older already-running API also rejects the new strategy schema. Therefore the table deliberately makes no numerical gain claim. Unit regression is complete (36 passed) and covers timestamp ordering, edge constraints, temporal NMS isolation, duplicate-resistant video scoring, strong-partial versus weak-full ranking, explicit DEV service dispatch, and preservation of ATS/Vortex behavior.

## 7. Limitations and next steps

Diagnostic selectivity is a retrieval-time estimate, not ground-truth rarity. The supplied configuration values are initial ablation defaults and should be tuned only with held-out labelled queries. Candidate restriction can still miss the correct video when the diagnostic event itself is weak; progressive widening is therefore essential. Finally, timestamps depend on ingestion correctness; responses expose `frame_idx_fallback` when timestamp data are missing rather than presenting it as millisecond-accurate timing.

Future work is to evaluate a larger labelled split, calibrate model-specific absolute scores offline, add backend-native video-ID filters where available, and capture the three operator-interface screenshots from the deployed system.

## References

1. T. V. Nguyen et al. *AIthena-Vision: Adaptive Temporal Multimodal Event Retrieval with LLM-generated Multiperspective Fusion*. AI Challenge HCMC 2025 technical paper.
2. AI Challenge TP.HCM 2026. *Technical report requirements*.

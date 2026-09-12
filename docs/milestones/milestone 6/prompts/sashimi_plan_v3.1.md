# SASHIMI PLAN v3.1 — `dev_first_search`

## ROLE

You are a senior AI Engineer / Retrieval Engineer working on the multimodal video retrieval system in:

`https://github.com/zintomvn/Multimodal-Retrieval`

Your task is to implement a **third temporal search strategy** named:

```text
dev_first_search
```

where **DEV** means:

```text
Diagnostic Event Video-first
```

This strategy must coexist with the two existing temporal strategies:

```text
vortex_k_context
aithena_weighted_ats
dev_first_search
```

Do **not** replace ATS or Vortex. They must remain available as baselines for experiments and technical-report comparison.

The goal of `dev_first_search` is to solve the main weaknesses of the current temporal retrieval pipeline by changing the search order from:

```text
frame-first
→ retrieve every event globally
→ combine frame candidates
→ temporal rerank
```

into:

```text
LLM temporal decomposition
→ understand query target scope
→ identify the most diagnostic event
→ search diagnostic event globally
→ generate candidate videos
→ search remaining events inside candidate videos
→ remove duplicated candidates
→ construct timestamp-valid sequences
→ score complete and partial sequences fairly
→ return the best video sequence or target frame
```

Do NOT add VLM reranking in this version.

The new strategy must use the existing retrieval components where possible:

- query planner
- CLIP / SigLIP2 retrieval
- Elasticsearch metadata/caption retrieval
- RRF / existing frame-level fusion
- current database models
- current result schema

Do not rewrite the whole retrieval system.

---

# 1. PRIMARY PROBLEMS TO SOLVE

The implementation of `dev_first_search` must prioritize the following problems.

## Problem 1 — Temporal query decomposition is not robust

The current deterministic temporal parser depends heavily on explicit temporal cues such as:

```text
then
after
next
finally
sau đó
tiếp theo
E1 / E2 / E3
numbered steps
```

This fails for narrative descriptions where the user describes several consecutive scenes without explicit transition words.

Example:

> Đoạn clip bắt đầu với các người mẫu trình diễn những bộ trang phục màu kem dáng rộng, được điểm xuyết bằng các mảng vải nhiều màu sắc và họa tiết hình học. Nhiều tác phẩm đầy màu sắc được trưng bày trên thảm cỏ ngoài trời, thu hút đông đảo người dân và du khách tham quan. Những món đồ thủ công như búp bê và quả cầu được tạo nên từ nhiều mảnh vải màu sắc, hoa văn khác nhau.

This query should be interpreted as:

```text
E1
fashion models
loose cream/beige clothing
multicolored fabric patches
geometric patterns

E2
colorful artworks
displayed outdoors on grass/lawn
crowd / visitors

E3
handmade textile objects
dolls
round balls / spheres
multicolored patterned fabric
```

with order:

```text
E1 → E2 → E3
```

even though explicit words such as `then` or `sau đó` are absent.

For `dev_first_search`, **LLM decomposition must be the primary temporal decomposition mechanism**.

The deterministic parser should become fallback/support logic, not the primary source of truth for narrative queries.

---

## Problem 2 — The system does not distinguish video-sequence retrieval from exact-frame retrieval

The current Temporal KIS flow expects a `temporal_anchor_index`.

When the query describes an entire clip and does not explicitly identify a target frame, forcing an anchor can produce:

```text
correct video
correct sequence
wrong returned frame
```

The new strategy must distinguish:

```text
target_scope = frame
```

from:

```text
target_scope = video_sequence
```

A query describing an entire sequence without a specific target moment must not silently use the middle event as semantic truth.

---

## Problem 3 — Temporal distance currently relies on `frame_idx` and a hidden 30 FPS assumption

The current temporal search converts:

```text
delta_t_max_ms
```

to frame distance using approximately:

```text
delta_frames = delta_t_max_ms / 1000 * 30
```

This is unsafe because:

- source videos may have different FPS;
- extracted keyframes are not guaranteed to be uniformly spaced;
- frame indices do not represent wall-clock temporal distance;
- candidate videos may come from different FPS distributions.

`dev_first_search` must use:

```text
timestamp_ms
```

as the primary temporal coordinate.

`pts_time` may be used when it is the canonical available timestamp.

Do not convert time to frames.

---

## Problem 4 — One global temporal gap is too broad

The current default may allow approximately:

```text
180 seconds
```

between every event pair.

This can create a sequence such as:

```text
E1 at 00:20
E2 at 02:40
E3 at 05:10
```

simply because the events occur in the same video and in the correct order.

Different event transitions should use different temporal expectations.

The LLM planner must infer the relation for each adjacent pair.

Do not ask the LLM to invent exact timestamps.

Use semantic gap classes.

---

## Problem 5 — Hard preference for complete sequences can rank weak wrong videos over strong partial correct videos

Current logic may prefer:

```text
wrong video:
E1 weak
E2 weak
E3 weak
```

over:

```text
correct video:
E1 strong
E2 strong
E3 missing because retrieval failed
```

simply because the first sequence is complete.

`dev_first_search` must not hard-discard partial sequences just because a full sequence exists.

It must prefer sequences that contain **more strongly supported correct events**, not merely more event slots.

---

## Problem 6 — Partial sequence scoring does not penalize missing events correctly

If sequence score is normalized only by:

```text
number_of_matched_events
```

then a sequence containing only two easy strong events may obtain an artificially high score.

Missing events must remain part of the denominator or receive an explicit penalty.

---

## Problem 7 — Event score calibration is weak

The current retrieval flow may normalize an event using:

```text
raw_score / max_score_in_this_candidate_pool
```

The best candidate of a very weak event can therefore become:

```text
1.0
```

even when the absolute model evidence is poor.

The temporal algorithm must retain both:

```text
absolute retrieval evidence
relative/ranking evidence
```

and must not treat every event's best frame as equally confident.

---

## Problem 8 — Retrieval is frame-first instead of video-first

For a multi-event query, independently searching every event over the entire dataset is expensive and can produce large noisy candidate pools.

A better search strategy is:

```text
find the most discriminative event
→ use it to locate likely videos
→ search the remaining events inside those videos
```

This is the central design principle of `dev_first_search`.

---

## Problem 9 — Diagnostic / rare events are not exploited

Not all events are equally useful.

For example:

```text
people standing outdoors
```

is generic.

But:

```text
handmade dolls and fabric spheres made from multicolored patterned textile pieces
```

is highly discriminative.

The new algorithm must explicitly estimate how useful each event is for identifying the correct video.

---

## Problem 10 — Candidate duplication wastes retrieval budget

Many adjacent frames from the same shot can occupy a large part of top-k:

```text
frame at 10.0s score=.92
frame at 10.2s score=.91
frame at 10.5s score=.90
frame at 10.7s score=.89
```

These frames add little new evidence.

`dev_first_search` must apply temporal candidate deduplication / NMS before expensive sequence construction and before any future VLM stage.

---

# 2. DO NOT MODIFY THE MEANING OF ATS OR VORTEX

Existing strategies must remain:

```text
vortex_k_context
aithena_weighted_ats
```

The new strategy must be added as:

```text
dev_first_search
```

Update the search option schema so that:

```python
temporal_strategy: Literal[
    "vortex_k_context",
    "aithena_weighted_ats",
    "dev_first_search",
]
```

is accepted.

Do not silently route `dev_first_search` through ATS or Vortex.

Create a dedicated algorithm implementation.

Recommended new module:

```text
apps/backend/app/modules/temporal/dev_first.py
```

The service layer should dispatch to this implementation explicitly.

---

# 3. `dev_first_search` HIGH-LEVEL ALGORITHM

The complete strategy should be:

```text
User Query
    ↓
LLM Temporal Planner
    ↓
Event Chain E1 ... En
    ↓
Target Scope Detection
    ↓
Diagnostic Event Selection
    ↓
Diagnostic Event Global Retrieval
    ↓
Temporal NMS
    ↓
Candidate Video Generation
    ↓
Remaining Event Retrieval Restricted to Candidate Videos
    ↓
Per-event Temporal NMS
    ↓
Per-video Sequence Construction
    ↓
Timestamp + Per-edge Constraint Validation
    ↓
Partial/Full Sequence Scoring
    ↓
Video/Sequence Diversification
    ↓
Target-frame or Video-sequence Output
```

The algorithm should not perform full global retrieval for every event unless fallback is required.

---

# 4. PART A — LLM-FIRST TEMPORAL QUERY DECOMPOSITION

## 4.1 Required behavior

When all of the following are true:

```text
query_type == KIS
temporal_mode == true
temporal_strategy == dev_first_search
```

the LLM planner should be the primary temporal decomposition source.

Do not require the deterministic regex parser to first detect words such as:

```text
then
after
sau đó
next
```

before asking the LLM for temporal decomposition.

For `dev_first_search`, always ask the planner to determine whether the query contains:

```text
1 event
or
multiple chronological visual events
```

The planner must support:

- explicit before/after queries;
- E1/E2/E3 labelled queries;
- multi-sentence narratives;
- process descriptions;
- implicit visual scene changes;
- contextual KIS queries.

The planner should not create multiple events merely because a query has multiple sentences.

The event split must represent independently retrievable visual moments.

---

## 4.2 Planner output schema

Extend the temporal plan with:

```json
{
  "temporal_intent": "single_event|ordered_sequence|narrative_sequence",
  "target_scope": "frame|video_sequence",
  "anchor_policy": "explicit|inferred|none",
  "temporal_anchor_index": null,
  "temporal_events": [
    {
      "order": 1,
      "query": "English standalone semantic retrieval query",
      "multi_views": [],
      "text_query": "",
      "text_views": [],
      "importance": 0.0,
      "diagnostic_prior": 0.0
    }
  ],
  "temporal_edges": [
    {
      "from_event": 1,
      "to_event": 2,
      "relation": "after",
      "gap_class": "short|medium|loose|unknown"
    }
  ]
}
```

All scores must be in:

```text
[0, 1]
```

---

## 4.3 Temporal intent rules

### `single_event`

Use when the query describes one independently retrievable visual state.

Example:

```text
A chef pours orange sauce over shrimp.
```

### `ordered_sequence`

Use when the user explicitly gives event order.

Example:

```text
A man enters the room, then sits at a table, then opens a laptop.
```

### `narrative_sequence`

Use when multiple visual scenes are described in chronological narrative order without explicit temporal connectors.

Example:

```text
Models walk in cream outfits.
Visitors observe artworks on a lawn.
Handmade textile dolls and balls are displayed.
```

---

## 4.4 LLM validation

After receiving a plan, validate structurally:

```text
1 <= number_of_events <= max_temporal_events
orders are unique
orders are monotonic
queries are non-empty
temporal edge indices exist
anchor index is valid when present
```

Do not validate the existence of events using hard-coded vocabulary.

If the LLM returns invalid JSON or an invalid event chain:

1. attempt one repair request;
2. if repair fails, use the existing deterministic temporal parser;
3. if parser returns one event, execute the query as a single-event search.

Do not crash the search.

---

# 5. PART B — TARGET SCOPE AND ANCHOR LOGIC

## 5.1 New target scope

Add:

```text
frame
video_sequence
```

### `target_scope = frame`

Use when the query explicitly asks for a particular visual moment.

Examples:

```text
the moment when...
the frame where...
find the scene after...
khoảnh khắc...
cảnh mà...
sau khi X thì Y...
trước khi...
```

The planner should return:

```json
{
  "target_scope": "frame",
  "anchor_policy": "explicit",
  "temporal_anchor_index": 2
}
```

when the query clearly identifies E2 as target.

### `target_scope = video_sequence`

Use when the user describes the whole sequence without identifying one event as the desired frame.

Regression query should produce approximately:

```json
{
  "temporal_intent": "narrative_sequence",
  "target_scope": "video_sequence",
  "anchor_policy": "none",
  "temporal_anchor_index": null
}
```

---

## 5.2 Do not invent a semantic anchor

For `dev_first_search`:

```text
target_scope = video_sequence
```

must NOT trigger:

```text
anchor = middle event
```

The sequence itself is the retrieval object.

A representative frame may still be required by the current UI/result schema.

That representative frame is a **presentation decision**, not a semantic anchor.

Use:

```text
representative_frame_policy
```

with default:

```text
highest_confidence_matched_event
```

Possible policies:

```text
highest_confidence_matched_event
highest_diagnostic_event
middle_matched_event
```

The representative frame policy must not affect sequence ranking.

---

# 6. PART C — DIAGNOSTIC EVENT SELECTION

The central idea of `dev_first_search` is to identify the event that best narrows the search space.

## 6.1 Planner diagnostic prior

Each event receives:

```json
{
  "diagnostic_prior": 0.0
}
```

This represents the LLM's estimate of how visually distinctive the event is relative to the other events in the same query.

Increase prior when the event contains:

- unusual object combinations;
- distinctive materials;
- uncommon visual attributes;
- specific object relationships;
- unusual actions;
- rare scene/object combinations.

Decrease prior for generic events such as:

- generic crowd;
- person standing;
- people walking;
- generic outdoor scene;
- generic room;
- generic interview.

Do not use a fixed manually maintained rarity vocabulary.

The score must be inferred relative to the current event chain.

---

## 6.2 Optional retrieval probe

LLM prior alone can be wrong.

Add a configurable lightweight diagnostic probe.

Example config:

```yaml
dev_first:
  diagnostic_probe:
    enabled: true
    top_k_per_event: 40
```

For each event:

1. perform a small retrieval;
2. collect the top `K_probe` results;
3. compute:
   - number of unique videos;
   - top score;
   - score margin;
   - concentration of results by video.

A highly diagnostic event should generally have:

```text
strong top evidence
fewer plausible videos
clearer separation between strong and weak candidates
```

Define a deterministic retrieval selectivity signal.

One possible normalized form:

```text
video_selectivity =
1 - min(1, unique_video_count / K_probe)
```

```text
margin_signal =
clamp(top_score - mean(top_q_scores), 0, 1)
```

```text
probe_diagnostic =
w1 * video_selectivity
+ w2 * margin_signal
+ w3 * top_confidence
```

All weights must come from config.

Do not hardcode them inside the algorithm.

---

## 6.3 Final diagnostic score

Combine:

```text
LLM diagnostic prior
+
retrieval-based selectivity
```

Example:

```text
diagnostic_score =
planner_weight * diagnostic_prior
+ probe_weight * probe_diagnostic
```

Normalize weights.

If diagnostic probe is disabled or unavailable:

```text
diagnostic_score = diagnostic_prior
```

Choose:

```text
diagnostic_event_index =
argmax(diagnostic_score)
```

For the regression query, the expected event is approximately:

```text
E3
handmade textile dolls + fabric balls/spheres
```

because it is more distinctive than:

```text
generic outdoor visitors
```

---

# 7. PART D — GLOBAL SEARCH ONLY THE DIAGNOSTIC EVENT FIRST

After selecting the diagnostic event:

```text
E_d
```

perform the normal multimodal frame retrieval globally for this event.

Use the existing event-level multi-view retrieval.

Example:

```text
semantic views
→ CLIP / SigLIP2
→ caption / metadata
→ RRF / current frame scoring
```

Do not implement a separate embedding stack.

Use the existing `_rank_frames_multiperspective()` or refactor reusable logic cleanly.

Retrieve:

```text
diagnostic_candidate_frames
```

from config.

Example:

```yaml
dev_first:
  diagnostic_candidate_frames: 500
```

---

# 8. PART E — TEMPORAL NMS BEFORE VIDEO GROUPING

Before grouping diagnostic frames by video, remove near-duplicate temporal candidates.

## 8.1 Temporal NMS input

Candidates contain at minimum:

```python
frame_id
video_id
event_index
timestamp_ms
score
```

## 8.2 NMS rule

For candidates from the same:

```text
video_id
event_index
```

sort by calibrated score descending.

Greedily keep a candidate only if it is at least:

```text
event_nms_window_ms
```

away from already selected candidates.

Example config:

```yaml
dev_first:
  event_nms_window_ms: 2500
```

Example:

```text
10.0s score=.92
10.2s score=.91
10.6s score=.90
15.0s score=.82
```

With:

```text
window=2.5s
```

retain approximately:

```text
10.0s
15.0s
```

Do not compare candidates from different events.

Do not compare candidates from different videos.

Fallback to frame-index deduplication only if timestamps are missing.

The new strategy must not use frame index for normal temporal logic.

---

# 9. PART F — CANDIDATE VIDEO GENERATION

After NMS, group diagnostic event candidates by:

```text
video_id
```

Do not rank a video by the number of matching frames.

Otherwise one long/repetitive shot can dominate.

## 9.1 Video score

For each candidate video:

```text
diagnostic_video_score =
a * best_frame_score
+ b * mean(top_m_frame_scores)
+ c * view_agreement
```

where:

- `best_frame_score` = strongest diagnostic candidate;
- `top_m` should be small and configurable;
- `view_agreement` measures whether multiple diagnostic query views support the video.

Example config:

```yaml
dev_first:
  video_scoring:
    best_weight: 0.55
    top_m_mean_weight: 0.30
    view_agreement_weight: 0.15
    top_m: 3
```

Normalize all terms to `[0, 1]`.

Do not reward arbitrary duplicate frame count.

---

## 9.2 Candidate video limit

Keep:

```text
candidate_video_limit
```

videos.

Example:

```yaml
dev_first:
  candidate_video_limit: 40
```

Preserve their ranking:

```text
candidate_video_rank
candidate_video_score
```

for later debugging.

---

## 9.3 Fallback conditions

Diagnostic-video-first search should fall back to broader retrieval when:

```text
candidate videos < min_candidate_videos
```

or:

```text
diagnostic event confidence < minimum threshold
```

The threshold must be configurable.

Fallback behavior:

```text
use global retrieval for all events
but still use dev_first sequence scoring and timestamp logic
```

Do not fall back to ATS or Vortex unless explicitly configured.

---

# 10. PART G — SEARCH REMAINING EVENTS INSIDE CANDIDATE VIDEOS

For each event:

```text
E1 ... En
```

retrieve candidates restricted to:

```text
candidate_video_ids
```

whenever possible.

## Preferred behavior

If Milvus/vector backend supports metadata filtering:

```text
video_id IN candidate_video_ids
```

apply the filter during vector retrieval.

For Elasticsearch:

apply the equivalent video filter.

## Fallback behavior

If the backend cannot efficiently filter by video ID:

1. retrieve a larger event pool globally;
2. map hits to `video_id`;
3. discard frames outside candidate videos;
4. automatically widen top-k if too few candidates remain.

Do not silently return an event with zero candidates because initial global top-k was too small.

Use configurable widening:

```yaml
dev_first:
  event_retrieval:
    initial_top_k: 200
    max_top_k: 1000
    widening_factor: 2
    min_candidates_per_event: 20
```

Stop widening when:

```text
min candidates obtained
or
max_top_k reached
```

---

# 11. PART H — CANDIDATE DATA MODEL FOR `dev_first_search`

The temporal candidate must preserve absolute timing and scoring evidence.

Recommended:

```python
@dataclass(frozen=True)
class DevFirstCandidate:
    frame_id: str
    video_id: str
    video_code: str

    event_index: int

    timestamp_ms: int | None
    frame_idx: int

    final_retrieval_score: float

    semantic_raw_score: float | None
    semantic_relative_score: float
    text_raw_score: float | None
    text_relative_score: float

    rrf_score: float

    calibrated_event_score: float

    diagnostic_score: float = 0.0
```

Do not remove existing `Candidate` fields required by ATS/Vortex.

If reuse is cleaner, extend the shared candidate with optional timestamp/raw-score fields while keeping backward compatibility.

---

# 12. PART I — USE TIMESTAMPS, NOT 30 FPS FRAME CONVERSION

`dev_first_search` must never calculate temporal distance using:

```python
delta_t_max_ms / 1000 * 30
```

Primary temporal coordinate:

```text
timestamp_ms
```

For ordered candidates:

```python
next.timestamp_ms > previous.timestamp_ms
```

Gap:

```python
gap_ms = next.timestamp_ms - previous.timestamp_ms
```

If `timestamp_ms` is unavailable, try canonical `pts_time`.

Only if no timestamp information exists may the algorithm use frame order as degraded fallback.

Expose:

```json
{
  "temporal_coordinate": "timestamp_ms"
}
```

or:

```json
{
  "temporal_coordinate": "frame_idx_fallback"
}
```

Never pretend frame-index fallback is millisecond-accurate.

---

# 13. PART J — LLM PER-EDGE TEMPORAL CONSTRAINTS

The planner should generate temporal edges:

```json
{
  "temporal_edges": [
    {
      "from_event": 1,
      "to_event": 2,
      "relation": "after",
      "gap_class": "medium"
    },
    {
      "from_event": 2,
      "to_event": 3,
      "relation": "after",
      "gap_class": "short"
    }
  ]
}
```

Supported relation types initially:

```text
after
before
unknown
```

Normalize the final event ordering so the sequence builder operates left-to-right.

---

## 13.1 Gap classes

Do not ask the LLM for exact seconds.

Use:

```text
short
medium
loose
unknown
```

Example configurable mapping:

```yaml
dev_first:
  temporal_gap_classes:
    short:
      max_gap_ms: 45000
    medium:
      max_gap_ms: 120000
    loose:
      max_gap_ms: 300000
    unknown:
      max_gap_ms: null
```

`null` means:

```text
no semantic hard maximum
```

but the algorithm may still apply a soft compactness penalty.

Do not reuse `180000` for every pair.

---

## 13.2 Edge validation

For matched consecutive event candidates:

```text
candidate(E_i)
candidate(E_j)
```

where:

```text
i < j
```

validate:

```text
timestamp_j > timestamp_i
```

If an edge has `max_gap_ms`:

```text
timestamp_j - timestamp_i <= max_gap_ms
```

If an intermediate event is missing, preserve original event indices.

For:

```text
E1 matched
E2 missing
E3 matched
```

validate chronology:

```text
timestamp(E1) < timestamp(E3)
```

Do not pretend E3 is E2.

---

# 14. PART K — NEW SEQUENCE CONSTRUCTION ALGORITHM

Do not reuse ATS sequence generation or Vortex anchor expansion internally.

Implement a dedicated sequence builder in:

```text
dev_first.py
```

Recommended approach:

```text
dynamic programming / beam dynamic programming
```

per candidate video.

The algorithm must support:

- event match;
- event skip;
- timestamp order;
- per-edge temporal constraints;
- partial sequences;
- top-N alternative sequences;
- bounded complexity.

---

## 14.1 State

For each event index:

```text
i
```

maintain top states such as:

```python
state = {
    matched_candidates,
    last_timestamp_ms,
    event_mask,
    accumulated_evidence,
    accumulated_gap_penalty,
    matched_weight,
}
```

Each event transition supports:

```text
MATCH event i with candidate
SKIP event i
```

---

## 14.2 Match transition

A candidate may extend a state when:

```text
same video
candidate event index == current event
timestamp ordering is valid
edge constraint is valid
candidate has not already been used
```

---

## 14.3 Skip transition

Skipping an event must be allowed.

The event is marked missing.

Its weight remains in the final expected-event denominator.

This is the key difference from the current partial-sequence average.

---

## 14.4 Beam width

Keep only the top:

```text
sequence_beam_width
```

states after each event.

Example:

```yaml
dev_first:
  sequence_beam_width: 200
```

The beam ordering must use a partial upper-bound-compatible score, not only current average score.

At minimum consider:

```text
accumulated calibrated evidence
matched event count
diagnostic event matched
temporal compactness
```

Do not let a state with one perfect easy event permanently dominate a state with several strong events.

---

# 15. PART L — EVENT SCORE CALIBRATION

Do not use only:

```text
score / max_event_score
```

as the temporal confidence.

For every event candidate preserve:

```text
absolute model evidence
relative ranking evidence
```

---

## 15.1 Semantic signal

Keep:

```text
semantic_raw_score
semantic_rank
semantic_relative_score
```

If multiple visual models are used:

```text
CLIP raw score
SigLIP2 raw score
visual RRF
```

must remain inspectable separately.

Do not average incompatible raw model scales.

---

## 15.2 Relative evidence

Relative evidence may use:

```text
rank percentile
RRF
normalized rank signal
```

Example:

```text
rank_signal = 1 / (1 + log(1 + rank))
```

then normalize to `[0,1]`.

Do not normalize only by the maximum score.

---

## 15.3 Absolute confidence

Absolute confidence must be model-specific.

Preferred:

```text
fit calibration parameters offline
from benchmark positives/negatives
```

Possible mapping:

```text
sigmoid((raw_score - bias) / temperature)
```

Config:

```yaml
score_calibration:
  clip:
    bias: ...
    temperature: ...
  siglip2:
    bias: ...
    temperature: ...
```

Do not invent calibration values.

If no calibration parameters are available:

- preserve raw score;
- set `absolute_confidence_available=false`;
- rely more on rank/RRF;
- do not transform the event's best candidate automatically to confidence `1.0`.

---

## 15.4 Final candidate event score

Example:

```text
event_score =
relative_weight * relative_signal
+ absolute_weight * absolute_confidence
+ hybrid_weight * retrieval_final_score
```

Normalize weights.

If absolute confidence is unavailable:

renormalize using the remaining signals.

The final:

```text
calibrated_event_score
```

must be in:

```text
[0,1]
```

---

# 16. PART M — EVENT RETRIEVAL CONFIDENCE

For every event calculate retrieval health.

Example:

```json
{
  "event_index": 3,
  "top_calibrated_score": 0.86,
  "top_5_mean": 0.71,
  "score_margin": 0.15,
  "unique_video_count": 18,
  "retrieval_confidence": 0.82
}
```

Use deterministic signals.

Do not call the LLM here.

A simple configurable confidence formula may combine:

```text
top confidence
score margin
candidate diversity
```

This is useful for:

```text
effective_event_weight
```

but must not cause a hard event removal.

Example:

```text
effective_event_weight =
importance *
(
    confidence_floor
    + (1 - confidence_floor) * retrieval_confidence
)
```

This ensures a difficult event still contributes.

---

# 17. PART N — SEQUENCE SCORING

The new strategy must not use:

```text
sum matched scores / number matched
```

because this over-rewards short partial sequences.

The score must account for **all expected events**.

---

## 17.1 Event weights

For event `i`:

```text
base_weight_i = planner importance_i
```

Optionally:

```text
effective_weight_i =
base_weight_i
* confidence adjustment
```

Normalize only for interpretability.

The denominator of event evidence must include all expected events.

---

## 17.2 Weighted event evidence

For matched event `i`:

```text
m_i = calibrated_event_score_i
```

For missing event:

```text
m_i = 0
```

Then:

```text
weighted_event_evidence =
sum(w_i * m_i)
/
sum(w_i for every expected event)
```

This naturally penalizes missing events.

---

## 17.3 Coverage

Define:

```text
coverage =
sum(w_i for matched events)
/
sum(w_i for all expected events)
```

Also define optional:

```text
strong_coverage
```

where an event counts as strongly matched only when:

```text
calibrated_event_score >= configurable threshold
```

Do not hardcode the threshold.

Example:

```yaml
dev_first:
  strong_match_threshold: 0.55
```

If no calibrated absolute confidence exists, use a lower-confidence rank-based definition and mark it in debug metadata.

---

## 17.4 Missing ratio

```text
missing_ratio = 1 - coverage
```

---

## 17.5 Diagnostic event evidence

Define:

```text
diagnostic_match =
calibrated score of diagnostic event
```

If diagnostic event is missing:

```text
diagnostic_match = 0
```

This may be a small positive term, not a hard requirement.

---

## 17.6 Temporal gap penalty

For every matched transition calculate:

```text
gap_ms
```

If maximum gap exists:

```text
gap_ratio = gap_ms / max_gap_ms
```

Use a smooth penalty.

Example:

```text
edge_penalty = gap_penalty_weight * min(1, gap_ratio)
```

Do not penalize valid short gaps strongly.

For `unknown` gap class, use only compactness penalty, not a hard cutoff.

---

## 17.7 Final sequence formula

Use configurable coefficients.

Conceptually:

```text
sequence_score =
    evidence_weight * weighted_event_evidence
  + coverage_weight * coverage
  + strong_coverage_weight * strong_coverage
  + diagnostic_weight * diagnostic_match
  - missing_penalty_weight * missing_ratio
  - temporal_gap_penalty
```

Then clamp:

```text
sequence_score ∈ [0,1]
```

Recommended starting config:

```yaml
dev_first:
  sequence_scoring:
    evidence_weight: 0.55
    coverage_weight: 0.20
    strong_coverage_weight: 0.10
    diagnostic_weight: 0.05
    missing_penalty_weight: 0.10
    temporal_gap_penalty_weight: 0.10
```

These are initial experiment values only.

Do not claim they are optimal without benchmark results.

---

# 18. PART O — IMPORTANT RANKING BEHAVIOR

The algorithm must avoid both extremes:

### Incorrect behavior A

```text
Video A:
E1=.95
E2=.95
E3=missing

Video B:
E1=.40
E2=.40
E3=.40
```

Do not automatically rank B higher only because B has all three events.

### Incorrect behavior B

```text
Video A:
E1=.99
E2=missing
E3=missing

Video B:
E1=.80
E2=.80
E3=.80
```

Do not automatically rank A higher because its only event is very strong.

The sequence formula must balance:

```text
quality of matched events
+
quantity / coverage of supported events
```

This is a central acceptance criterion.

---

# 19. PART P — EVENT-LEVEL TEMPORAL NMS AFTER RESTRICTED RETRIEVAL

After retrieving each event inside candidate videos, apply NMS again.

For each:

```text
(video_id, event_index)
```

use:

```text
per_video_candidate_min_gap_ms
```

Example:

```yaml
dev_first:
  per_video_candidate_min_gap_ms: 1500
```

Algorithm:

```text
sort by calibrated_event_score descending

selected = []

for candidate:
    if candidate is not within suppression window
       of an already selected candidate:
        keep candidate

stop when per_event_per_video_limit reached
```

Example:

```yaml
dev_first:
  per_event_per_video_limit: 12
```

This ensures candidate diversity before sequence DP.

---

# 20. PART Q — SEQUENCE DIVERSIFICATION

One video may generate many nearly identical temporal sequences.

After scoring sequences, apply sequence-level diversification.

For sequences from the same video:

compare matched timestamps.

If all matched timestamps are within a configurable neighborhood of a better sequence:

```text
suppress duplicate sequence
```

Example config:

```yaml
dev_first:
  sequence_nms_window_ms: 3000
  max_sequences_per_video: 3
```

Do not allow one video with many equivalent sequence paths to fill the whole result list.

---

# 21. PART R — OUTPUT LOGIC FOR `frame` VS `video_sequence`

## 21.1 `target_scope = frame`

The requested anchor event must be present.

If:

```text
temporal_anchor_index = k
```

the final returned `frame_id` should be the matched candidate for event `k`.

If the best sequence is missing the required anchor event:

do not return that sequence as a valid frame-target result.

Continue to the next sequence.

---

## 21.2 `target_scope = video_sequence`

No semantic anchor is required.

Return:

```text
video_id
sequence score
all matched sequence frames
missing events
```

The current API may still require:

```text
frame_id
```

Select a representative frame using:

```text
highest_confidence_matched_event
```

by default.

Expose:

```json
{
  "target_scope": "video_sequence",
  "representative_frame_policy": "highest_confidence_matched_event",
  "representative_event_index": 3
}
```

This frame is for UI/result compatibility only.

---

# 22. PART S — `min_match` SEMANTICS

Do not use hard `prefer_full_sequences`.

Use `min_match` only as a minimum validity constraint.

Resolution order:

```text
1. request.options.min_match, if explicitly provided
2. dev_first profile min_match_ratio
3. safe default
```

Example:

```python
min_match = ceil(event_count * min_match_ratio)
```

With:

```yaml
min_match_ratio: 0.5
```

a 3-event query requires at least:

```text
2 matched events
```

But full coverage receives a scoring benefit.

Do not discard a high-quality 2/3 sequence merely because some weak 3/3 sequence exists.

---

# 23. PART T — PROGRESSIVE FALLBACK / RECALL RECOVERY

`dev_first_search` must protect recall.

Use staged fallback.

## Stage 1

```text
diagnostic video-first
```

## Stage 2

If too few candidate videos:

```text
increase diagnostic top-k
```

## Stage 3

If an event has too few candidates after video restriction:

```text
increase event retrieval top-k
```

## Stage 4

If sequence search still produces too few valid sequences:

```text
expand candidate_video_limit
```

## Stage 5

Final fallback:

```text
global event retrieval for all events
+
dev_first timestamp sequence scoring
```

Do not directly switch to ATS/Vortex unless the user explicitly selected those strategies.

Log which fallback stage was used.

---

# 24. NEW CONFIGURATION

Add a dedicated section.

Example:

```yaml
competition_default:
  ...

competition_dev_first_v1:
  semantic_weight: 0.60
  metadata_weight: 0.30
  user_boost_weight: 0.05

  rrf:
    enabled: true
    k: 60

  visual_rrf:
    enabled: true
    k: 60

  visual_models:
    clip_global:
      model_key: clip_vith14_quickgelu_dfn5b_v2
      collection: keyframe_embeddings_clip_vith14_quickgelu_dfn5b_v2
      weight: 1.0
      enabled: true

    siglip2_fine_grained:
      model_key: siglip2_so400m16_384_webli_openclip_1152_v1
      collection: keyframe_embeddings_siglip2_so400m16_384_webli_openclip_1152_v1
      weight: 1.0
      enabled: true

  dev_first:
    diagnostic_probe:
      enabled: true
      top_k_per_event: 40
      planner_weight: 0.55
      probe_weight: 0.45

    diagnostic_candidate_frames: 500
    candidate_video_limit: 40
    min_candidate_videos: 8

    video_scoring:
      best_weight: 0.55
      top_m_mean_weight: 0.30
      view_agreement_weight: 0.15
      top_m: 3

    event_retrieval:
      initial_top_k: 200
      max_top_k: 1000
      widening_factor: 2
      min_candidates_per_event: 20

    event_nms_window_ms: 2500
    per_video_candidate_min_gap_ms: 1500
    per_event_per_video_limit: 12

    sequence_beam_width: 200
    max_sequences_per_video: 3
    sequence_nms_window_ms: 3000

    min_match_ratio: 0.50

    temporal_gap_classes:
      short:
        max_gap_ms: 45000
      medium:
        max_gap_ms: 120000
      loose:
        max_gap_ms: 300000
      unknown:
        max_gap_ms: null

    score_calibration:
      relative_weight: 0.60
      absolute_weight: 0.20
      hybrid_weight: 0.20

    sequence_scoring:
      evidence_weight: 0.55
      coverage_weight: 0.20
      strong_coverage_weight: 0.10
      diagnostic_weight: 0.05
      missing_penalty_weight: 0.10
      temporal_gap_penalty_weight: 0.10

    strong_match_threshold: 0.55

    representative_frame_policy: highest_confidence_matched_event
```

All numbers above are experiment defaults.

Do not claim they are optimal.

They must be configurable for benchmark ablation.

---

# 25. CODE CHANGES

Inspect current code first.

Expected files to modify:

```text
configs/agent.yaml
configs/retrieval_profiles.yaml

apps/backend/app/modules/retrieval/query_planning.py
apps/backend/app/modules/retrieval/schemas.py
apps/backend/app/modules/retrieval/service.py

apps/backend/app/modules/temporal/ats.py
```

`ats.py` should only be changed if adding optional shared candidate fields such as `timestamp_ms` is necessary.

Do not modify ATS scoring behavior.

Expected new file:

```text
apps/backend/app/modules/temporal/dev_first.py
```

Optional helper modules:

```text
apps/backend/app/modules/temporal/scoring.py
apps/backend/app/modules/temporal/nms.py
```

only if they reduce duplication.

Do not create unnecessary abstractions.

---

# 26. `dev_first.py` RESPONSIBILITIES

The new module should contain testable functions for:

```text
select_diagnostic_event()
temporal_nms()
score_candidate_videos()
resolve_edge_constraints()
build_dev_first_sequences()
score_dev_first_sequence()
diversify_sequences()
```

Do not put retrieval backend calls inside the pure sequence algorithm where avoidable.

Preferred layering:

```text
RetrievalService
    ↓
retrieves candidates
    ↓
dev_first.py
    ↓
pure temporal/video-first algorithms
```

This makes unit testing easier.

---

# 27. SERVICE-LAYER FLOW

Pseudo-code:

```python
def _search_temporal_kis(...):
    if strategy == "aithena_weighted_ats":
        ...
    elif strategy == "vortex_k_context":
        ...
    elif strategy == "dev_first_search":
        return self._search_dev_first(...)
```

Then:

```python
def _search_dev_first(...):

    plan = normalized["temporal_event_plans"]
    target_scope = normalized["target_scope"]

    # 1. determine diagnostic event
    probe_results = maybe_probe_events(plan)

    diagnostic_event = select_diagnostic_event(
        plan,
        probe_results,
    )

    # 2. global diagnostic retrieval
    diagnostic_candidates = retrieve_event_globally(
        diagnostic_event,
        top_k=config.diagnostic_candidate_frames,
    )

    # 3. NMS
    diagnostic_candidates = temporal_nms(
        diagnostic_candidates,
        window_ms=config.event_nms_window_ms,
    )

    # 4. candidate videos
    candidate_videos = score_candidate_videos(
        diagnostic_candidates,
    )[:candidate_video_limit]

    # 5. fallback if candidate set is weak
    candidate_videos = maybe_expand_candidate_videos(...)

    # 6. retrieve every event inside candidate videos
    event_candidate_sets = []

    for event in events:
        candidates = retrieve_event_restricted_to_videos(
            event,
            candidate_videos,
        )

        candidates = temporal_nms(
            candidates,
            ...
        )

        event_candidate_sets.append(candidates)

    # 7. build sequences per video
    sequences = build_dev_first_sequences(
        event_candidate_sets,
        temporal_edges,
        ...
    )

    # 8. score partial/full sequences
    sequences = score_sequences(...)

    # 9. diversify
    sequences = diversify_sequences(...)

    # 10. map to result schema according to target_scope
    return build_results(...)
```

Do not duplicate existing hybrid retrieval logic.

---

# 28. LLM PLANNER PROMPT CHANGES

Update the planner prompt so it explicitly knows about `dev_first_search`.

Add rules similar to:

```text
When temporal_strategy is dev_first_search:

1. Determine whether the query is a single visual event or a chronological multi-event narrative.

2. For multi-event queries, split the query into independently retrievable visual events even if the user does not use explicit words such as "then", "after", or "sau đó".

3. Do not split a sentence only because it adds an attribute to the same visual state.

4. Determine whether the user wants:
   - one exact frame, or
   - a video/sequence matching the overall narrative.

5. Do not invent a temporal anchor for video_sequence queries.

6. Assign each event:
   - importance
   - diagnostic_prior

7. diagnostic_prior means how useful the event is for narrowing candidate videos relative to the other events in this query.

8. For each adjacent event pair infer:
   - relation
   - gap_class

9. Use semantic gap classes only:
   short, medium, loose, unknown.

10. Do not invent exact timestamps or durations not supported by the query.
```

The user payload should include:

```json
{
  "temporal_strategy": "dev_first_search"
}
```

when applicable.

---

# 29. REGRESSION QUERY EXPECTED PLAN

For:

> Đoạn clip bắt đầu với các người mẫu trình diễn những bộ trang phục màu kem dáng rộng, được điểm xuyết bằng các mảng vải nhiều màu sắc và họa tiết hình học. Nhiều tác phẩm đầy màu sắc được trưng bày trên thảm cỏ ngoài trời, thu hút đông đảo người dân và du khách tham quan. Những món đồ thủ công như búp bê và quả cầu được tạo nên từ nhiều mảnh vải màu sắc, hoa văn khác nhau.

Expected plan should be conceptually similar to:

```json
{
  "temporal_intent": "narrative_sequence",
  "target_scope": "video_sequence",
  "anchor_policy": "none",
  "temporal_anchor_index": null,

  "temporal_events": [
    {
      "order": 1,
      "query": "fashion models wearing loose cream outfits with multicolored geometric fabric patches",
      "importance": 0.90,
      "diagnostic_prior": 0.75
    },
    {
      "order": 2,
      "query": "colorful artworks displayed on an outdoor lawn with many visitors",
      "importance": 0.75,
      "diagnostic_prior": 0.45
    },
    {
      "order": 3,
      "query": "handmade textile dolls and round fabric balls made from multicolored patterned cloth",
      "importance": 0.90,
      "diagnostic_prior": 0.95
    }
  ],

  "temporal_edges": [
    {
      "from_event": 1,
      "to_event": 2,
      "relation": "after",
      "gap_class": "medium"
    },
    {
      "from_event": 2,
      "to_event": 3,
      "relation": "after",
      "gap_class": "medium"
    }
  ]
}
```

These exact scores are examples only.

The implementation must not assert exact values in tests.

Tests should verify relative/structural behavior.

Example:

```text
event count == 3
target_scope == video_sequence
anchor == None
diagnostic(E3) > diagnostic(E2)
```

when using a deterministic fake planner response.

---

# 30. DEBUG / OBSERVABILITY

Add debug metadata for `dev_first_search`.

Example:

```json
{
  "temporal_strategy": "dev_first_search",

  "temporal_intent": "narrative_sequence",
  "target_scope": "video_sequence",

  "diagnostic_selection": {
    "event_index": 3,
    "planner_prior": 0.95,
    "probe_score": 0.83,
    "final_diagnostic_score": 0.90
  },

  "candidate_video_stage": {
    "diagnostic_raw_frames": 500,
    "diagnostic_frames_after_nms": 173,
    "candidate_videos_before_limit": 92,
    "candidate_videos_used": 40
  },

  "event_retrieval": [
    {
      "event_index": 1,
      "candidate_count_before_nms": 210,
      "candidate_count_after_nms": 104
    }
  ],

  "sequence_search": {
    "beam_width": 200,
    "valid_sequence_count": 84,
    "full_sequence_count": 31,
    "partial_sequence_count": 53
  },

  "fallback_stage": null
}
```

For each final result include:

```json
{
  "sequence_score": 0.84,
  "matched_events": 3,
  "expected_events": 3,
  "coverage": 1.0,
  "strong_coverage": 0.67,

  "event_scores": [
    {
      "event_index": 1,
      "frame_id": "...",
      "timestamp_ms": 41000,
      "calibrated_event_score": 0.81,
      "semantic_raw_score": 0.32
    }
  ],

  "temporal_edges": [
    {
      "from_event": 1,
      "to_event": 2,
      "gap_ms": 28000,
      "gap_class": "medium",
      "valid": true
    }
  ],

  "missing_events": [],

  "representative_event_index": 3
}
```

Do not expose massive candidate arrays in normal responses.

Detailed candidate logs should only appear in debug mode or traces.

---

# 31. LATENCY OBSERVABILITY

Measure separately:

```text
query planning
diagnostic probe
diagnostic global retrieval
diagnostic NMS
candidate video scoring
restricted event retrieval
event NMS
sequence construction
sequence scoring
sequence diversification
total latency
```

This is required to compare:

```text
ATS
vs
Vortex
vs
dev_first_search
```

in experiments.

---

# 32. TESTING REQUIREMENTS

Add unit tests specifically for the new strategy.

## Test 1 — Narrative LLM decomposition

Use a fake planner output for the Vietnamese regression query.

Verify:

```text
3 events
narrative_sequence
video_sequence
no forced anchor
```

---

## Test 2 — Exact target query

Example:

```text
After the man enters the room, find the moment when he opens the laptop.
```

Verify:

```text
target_scope == frame
anchor points to laptop event
```

---

## Test 3 — Timestamp ordering independent of FPS

Construct candidates with equivalent timestamps but different frame indices.

Example:

```text
Video A: 24 FPS frame indices
Video B: 60 FPS frame indices
```

Both should produce the same temporal validity when timestamps are equal.

This proves there is no hidden 30 FPS assumption.

---

## Test 4 — Per-edge temporal gap

Create:

```text
E1 @ 10s
E2 @ 30s
E3 @ 200s
```

With:

```text
E1→E2 short max 45s
E2→E3 medium max 120s
```

E1→E2 is valid.

E2→E3 is invalid.

---

## Test 5 — Strong partial vs weak full

Video A:

```text
E1=.90
E2=.90
E3=missing
```

Video B:

```text
E1=.40
E2=.40
E3=.40
```

Verify ranking follows configured scoring and can rank A above B.

---

## Test 6 — Strong full beats partial

Video A:

```text
E1=.85
E2=.82
E3=.88
```

Video B:

```text
E1=.92
E2=.90
E3=missing
```

With normal coverage weights, A should be able to outrank B.

This confirms the algorithm rewards broad strong support.

---

## Test 7 — Single strong event does not dominate

Video A:

```text
E1=.99
E2=missing
E3=missing
```

Video B:

```text
E1=.75
E2=.76
E3=.74
```

B should generally win under the default `dev_first_search` profile.

---

## Test 8 — Diagnostic event video-first

Create fake event retrieval where:

```text
E1 matches 80 videos
E2 matches 60 videos
E3 strongly matches 5 videos
```

Verify E3 becomes the diagnostic event and candidate video set is reduced before deep E1/E2 retrieval.

---

## Test 9 — Diagnostic fallback

If diagnostic event retrieval returns no useful videos:

verify the system widens/falls back to global event retrieval and still returns results.

---

## Test 10 — Temporal NMS

Input:

```text
10.0s score=.90
10.2s score=.89
10.7s score=.87
15.0s score=.80
```

With:

```text
NMS window = 2.5s
```

verify near-duplicate 10-second candidates collapse.

---

## Test 11 — Sequence NMS

Several sequence paths using almost identical timestamps from the same video should not occupy all final top-k slots.

---

## Test 12 — Event identity preserved across skips

For:

```text
E1 matched
E2 missing
E3 matched
```

verify sequence stores:

```text
event indices = [1, 3]
missing_events = [2]
```

Do not relabel E3 as event 2.

---

## Test 13 — Existing ATS remains unchanged

Run ATS tests.

No expected ranking changes caused by the new strategy.

---

## Test 14 — Existing Vortex remains unchanged

Run Vortex tests.

No expected ranking changes caused by the new strategy.

---

## Test 15 — Single-event KIS

`dev_first_search` with one event should degrade gracefully to ordinary frame-level retrieval without unnecessary sequence DP.

---

# 33. EXPERIMENT / BENCHMARK OUTPUT

The implementation must make it possible to benchmark:

```text
aithena_weighted_ats
vortex_k_context
dev_first_search
```

using identical query sets.

Record at minimum:

```text
Recall@K for correct video
Recall@K for correct frame where ground truth exists
MRR
Hit@1
Hit@5
Hit@10

mean latency
p50 latency
p95 latency

average candidate frames processed
average candidate videos processed
average sequences constructed
```

For temporal queries also report:

```text
event decomposition accuracy
average matched event coverage
full-sequence rate
partial-sequence rate
diagnostic event success rate
fallback rate
```

Do not claim `dev_first_search` is better until benchmark results show improvement.

---

# 34. IMPLEMENTATION ORDER

Implement in this order.

## Phase 1 — Strategy plumbing

1. Add `dev_first_search` to schema.
2. Add service dispatch branch.
3. Create `dev_first.py`.
4. Add minimal smoke test.

## Phase 2 — Planner

5. Extend planner schema.
6. Add `temporal_intent`.
7. Add `target_scope`.
8. Add `anchor_policy`.
9. Add `diagnostic_prior`.
10. Add temporal edges and gap classes.
11. Make LLM decomposition primary for `dev_first_search`.

## Phase 3 — Timestamp foundation

12. Add candidate timestamps.
13. Remove any `*30` conversion inside `dev_first_search`.
14. Add timestamp edge validation.
15. Add per-edge gap handling.

## Phase 4 — Diagnostic video-first retrieval

16. Add diagnostic probe.
17. Add diagnostic event selection.
18. Add global diagnostic retrieval.
19. Add temporal NMS.
20. Add candidate video scoring.
21. Add restricted event retrieval.
22. Add progressive fallback.

## Phase 5 — New sequence algorithm

23. Add DP/beam sequence construction.
24. Add skip transitions.
25. Add calibrated event score.
26. Add weighted expected-event denominator.
27. Add coverage.
28. Add missing penalty.
29. Add diagnostic bonus.
30. Add temporal gap penalty.
31. Add sequence diversification.

## Phase 6 — Output and tests

32. Add frame vs video-sequence output behavior.
33. Add representative frame logic.
34. Add debug metadata.
35. Add latency tracing.
36. Add unit tests.
37. Run full regression test suite.

Do not implement everything inside `service.py`.

---

# 35. CODE QUALITY REQUIREMENTS

The implementation must:

- use type hints;
- keep algorithm logic testable outside FastAPI;
- avoid hidden FPS assumptions;
- avoid magic constants;
- use config for thresholds and weights;
- preserve ATS and Vortex behavior;
- preserve query event identity;
- support graceful fallback;
- avoid unnecessary LLM calls after planning;
- avoid unnecessary global searches;
- keep debug information inspectable;
- not silently swallow core algorithm exceptions;
- document scoring equations;
- document fallback conditions.

Do not add VLM ranking.

Do not add raw-video decoding in this version.

Do not add new external infrastructure unless required by existing backend capabilities.

---

# 36. ACCEPTANCE CRITERIA

The task is complete only when all of the following are true.

### Strategy

```text
temporal_strategy="dev_first_search"
```

works through the API.

### Planner

Narrative temporal queries can be decomposed by the LLM without requiring explicit temporal cue words.

### Scope

The system can distinguish:

```text
frame target
vs
video sequence target
```

and does not force a middle anchor for a sequence query.

### Timing

`dev_first_search` uses timestamp-based temporal ordering.

There is no hidden:

```text
* 30 FPS
```

conversion in this strategy.

### Temporal constraints

Different adjacent event pairs can have different gap constraints.

### Video-first

The algorithm can select a diagnostic event and produce candidate videos before searching the remaining events deeply.

### Partial sequence behavior

Partial sequences are allowed and missing events are penalized correctly.

### Full sequence behavior

Full sequences receive coverage benefit but are not automatically accepted when all event matches are weak.

### Calibration

The temporal scorer retains both absolute and relative retrieval information and does not treat every event's top frame as confidence 1.0.

### Deduplication

Near-duplicate frames do not consume most of the event candidate budget.

### Compatibility

ATS and Vortex continue to work.

### Tests

All existing tests pass and new `dev_first_search` tests pass.

---

# 37. FINAL DELIVERABLE FROM THE CODING AGENT

After implementation, report:

1. changed files;
2. new files;
3. exact `dev_first_search` pipeline;
4. planner schema changes;
5. target-scope logic;
6. diagnostic-event selection formula;
7. candidate-video scoring formula;
8. timestamp temporal-validation logic;
9. per-edge temporal-gap logic;
10. sequence-construction algorithm;
11. sequence-score formula;
12. missing-event handling;
13. score calibration behavior;
14. temporal-NMS behavior;
15. fallback stages;
16. latency instrumentation;
17. tests added;
18. existing tests passed/failed;
19. remaining limitations;
20. benchmark commands needed to compare ATS, Vortex, and DEV-first.

Do not claim improved retrieval accuracy without measured benchmark results.

If benchmark data is not yet available, explicitly state:

```text
The new strategy changes the retrieval architecture and addresses known failure modes,
but its accuracy improvement must be validated empirically against ATS and Vortex.
```

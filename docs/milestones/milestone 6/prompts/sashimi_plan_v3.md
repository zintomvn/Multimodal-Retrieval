# ROLE

You are a senior AI Engineer / Retrieval Engineer working on a multimodal video retrieval system for KIS, QA, and TRAKE.

Your task is to improve the current retrieval and temporal-search architecture.

Focus only on:

- query planning
- temporal event decomposition
- event target/anchor handling
- diagnostic-event selection
- video-first retrieval
- multimodal candidate fusion
- temporal sequence construction
- timestamp-based temporal constraints
- partial/full sequence scoring
- confidence calibration
- candidate diversification
- temporal NMS
- local exact-frame candidate expansion

Do not rewrite the whole system.

Modify the current architecture incrementally and preserve existing APIs whenever possible.

Before editing, inspect at minimum:

- `configs/agent.yaml`
- `configs/retrieval_profiles.yaml`
- `configs/model_registry.yaml`
- `apps/backend/app/modules/retrieval/query_planning.py`
- `apps/backend/app/modules/retrieval/temporal_query.py`
- `apps/backend/app/modules/retrieval/service.py`
- `apps/backend/app/modules/retrieval/schemas.py`
- `apps/backend/app/modules/temporal/ats.py`
- `apps/backend/app/modules/temporal/vortex.py`
- relevant retrieval tests

Do not implement based only on this prompt. First understand the existing code paths and reuse existing abstractions where possible.

---

# TARGET FAILURE CASE

Use this query as the primary regression case:

> Đoạn clip bắt đầu với các người mẫu trình diễn những bộ trang phục màu kem dáng rộng, được điểm xuyết bằng các mảng vải nhiều màu sắc và họa tiết hình học. Nhiều tác phẩm đầy màu sắc được trưng bày trên thảm cỏ ngoài trời, thu hút đông đảo người dân và du khách tham quan. Những món đồ thủ công như búp bê và quả cầu được tạo nên từ nhiều mảnh vải màu sắc, hoa văn khác nhau.

The desired semantic decomposition is approximately:

```text
E1:
fashion models
loose cream/beige clothing
multicolored fabric patches
geometric patterns

E2:
colorful artworks
displayed outdoors on grass/lawn
crowd / visitors

E3:
handmade textile objects
dolls
round balls / spheres
multicolored patterned fabric
```

The system should understand:

```text
E1 -> E2 -> E3
```

even though the query does not explicitly say:

```text
then
after that
next
sau đó
tiếp theo
```

The query describes a narrative sequence across sentences.

---

# PART 1 — IMPROVE TEMPORAL / NARRATIVE EVENT DETECTION

## Current problem

`temporal_query.py` mainly splits temporal events using explicit cues such as:

- then
- after that
- next
- finally
- sau đó
- tiếp theo
- E1/E2 labels
- numbered events
- semicolons

This misses multi-sentence narrative descriptions.

The regression query contains 3 visually different moments, but deterministic parsing may return one large event.

## Required change

Extend temporal parsing to recognize a new source type:

```text
narrative_sentences
```

Do NOT blindly split every sentence.

Create logic that detects whether consecutive sentences describe independently retrievable visual states/events.

Suggested approach:

1. Split query into sentences.
2. Only consider narrative decomposition when there are at least 2 meaningful sentences.
3. For each sentence, determine whether it contains enough event information:
   - subject
   - action
   - object
   - scene
   - visible attributes
4. Avoid splitting sentences that merely elaborate on the previous sentence.
5. Keep a maximum of 8 events.
6. Preserve original sentence order.

Implement deterministic support where possible, but allow the agent planner to override/improve the result.

Add metadata:

```json
{
  "temporal_event_source": "narrative_sentences"
}
```

Do not treat punctuation alone as proof of temporal ordering.

The logic should detect a narrative sequence when consecutive sentences describe distinct visual scenes or object groups.

## Important behavior

For:

```text
A man enters a kitchen. He is wearing a blue shirt.
```

do NOT automatically create:

```text
E1 = man enters kitchen
E2 = man wears blue shirt
```

because the second sentence may only be an attribute of the same event.

For:

```text
Models walk on a runway.
Visitors look at artworks on a lawn.
Handmade dolls and fabric balls are displayed.
```

create 3 events.

## Tests

Add tests for:

- explicit temporal cue queries
- E1/E2 labeled queries
- multi-sentence narrative queries
- same-event descriptive sentences
- single sentence queries
- Vietnamese queries
- English queries

Existing behavior must remain backward compatible.

---

# PART 2 — MAKE THE QUERY PLANNER DISTINGUISH FRAME TARGET FROM VIDEO SEQUENCE

## Current problem

Temporal KIS currently expects `temporal_anchor_index`.

For queries describing an entire clip without explicitly identifying a target frame, forcing an anchor can cause:

```text
correct video
correct temporal sequence
wrong returned frame
```

There must be a distinction between:

```text
find this exact visual moment
```

and:

```text
find a video containing this sequence
```

## Required planner schema change

Extend the query planner output with:

```json
{
  "target_scope": "frame|video_sequence",
  "anchor_policy": "explicit|inferred|none",
  "temporal_anchor_index": null
}
```

Rules:

### target_scope = frame

Use when the query explicitly asks for a specific moment.

Examples:

```text
the moment when...
the frame showing...
after X, find Y
before Z...
ở cảnh...
khoảnh khắc...
sau khi...
trước khi...
```

Then set:

```json
{
  "target_scope": "frame",
  "anchor_policy": "explicit",
  "temporal_anchor_index": 2
}
```

when supported.

### target_scope = video_sequence

Use when the query describes multiple events/scenes but does not identify one event as the target.

Example regression query:

```json
{
  "target_scope": "video_sequence",
  "anchor_policy": "none",
  "temporal_anchor_index": null
}
```

Do NOT force the middle event as the target.

## Backend changes

Update:

- planning result dataclass
- normalization
- schema if needed
- temporal search output handling

When `target_scope == video_sequence`:

- rank videos/sequences first
- do not discard a sequence because no arbitrary anchor was selected
- choose a representative frame only for UI display
- preserve the full sequence in `sequence_frames`
- explicitly mark the representative frame as a presentation choice, not a semantic anchor

Add fields such as:

```json
{
  "target_scope": "video_sequence",
  "representative_frame_policy": "highest_event_score"
}
```

Do not use the current middle-event fallback as semantic truth.

---

# PART 3 — ADD EVENT DIAGNOSTIC IMPORTANCE

## Problem

Not all events have the same retrieval value.

Generic event:

```text
people visiting colorful art outdoors
```

may occur in many videos.

Rare event:

```text
handmade dolls and round fabric balls made of patterned textile pieces
```

is far more discriminative.

Current event `importance` is not enough because importance and discriminative retrieval value are different concepts.

## Required change

Extend each temporal event plan with:

```json
{
  "importance": 0.9,
  "diagnostic_score": 0.95
}
```

Definitions:

`importance`
= how important the event is to the meaning of the query.

`diagnostic_score`
= how useful this event is for narrowing candidate videos.

Planner rules:

Increase diagnostic score for:

- rare object combinations
- unusual object + material combinations
- distinctive visual attributes
- specific actions
- specific spatial relationships

Decrease diagnostic score for:

- generic crowds
- generic outdoor scenes
- generic people standing/walking
- generic indoor scenes

Do not use a hard-coded domain-specific vocabulary.

Infer discriminativeness relative to the query.

For the regression query, expected relationship:

```text
E3 diagnostic_score > E1 diagnostic_score > E2 diagnostic_score
```

approximately.

---

# PART 4 — IMPLEMENT OPTIONAL DIAGNOSTIC-EVENT-FIRST / VIDEO-FIRST RETRIEVAL

## Goal

For multi-event temporal queries, avoid performing equally expensive global frame search for every event before knowing likely videos.

Implement an optional two-stage temporal retrieval mode.

Add profile config:

```yaml
temporal:
  retrieval_mode: diagnostic_video_first
  diagnostic_event_count: 1
  diagnostic_candidate_frames: 500
  candidate_video_limit: 50
  per_event_top_k: 300
```

Support at least:

```text
global_event_search
diagnostic_video_first
```

Maintain existing behavior as fallback.

## Stage 1 — Diagnostic event retrieval

Choose event(s) with highest:

```text
diagnostic_score
```

Retrieve globally.

Example:

```text
E3
```

Retrieve top N frames.

Group by `video_id`.

Calculate a video retrieval score.

Do not allow one video with 50 similar frames to dominate just because it contributes many candidates.

A possible video score:

```text
video_score =
    max_frame_score
    + lambda * mean(top_m_frame_scores)
```

where `m` is small, e.g. 3.

Or use RRF across event views.

Make the implementation configurable.

Return top candidate videos:

```text
20–50
```

depending on profile.

## Stage 2 — Search remaining events inside candidate videos

For E1, E2, E3:

perform retrieval constrained to candidate video IDs whenever the backend supports it.

If vector backend filtering by video ID is supported, use it.

If not, retrieve a somewhat larger pool and filter before downstream temporal sequence construction.

Do not silently reduce recall to near zero.

Include debug info:

```json
{
  "retrieval_mode": "diagnostic_video_first",
  "diagnostic_event": 3,
  "candidate_video_count": 40
}
```

## Fallback

If diagnostic event retrieval produces too few candidate videos:

```text
candidate_video_count < configured minimum
```

fall back to global event retrieval.

Do not fail the search.

---

# PART 5 — USE `must_have` CONSTRAINTS IN RETRIEVAL SCORING

## Current problem

Agent already generates:

```json
"must_have": [...]
```

but downstream retrieval does not meaningfully use them.

## Required change

Preserve `must_have` from planner through:

```text
QueryPlanningResult
-> normalized query
-> temporal_event_plans
-> event candidate scoring
```

Create a generic `constraint_coverage` signal.

Use currently available signals only:

- frame caption
- detected objects
- OCR if relevant
- metadata annotations
- optional text/object tags

For every event candidate calculate:

```text
constraint_coverage =
matched weighted constraints /
total weighted constraints
```

Planner may optionally emit:

```json
"must_have": [
  {
    "text": "cream loose clothing",
    "weight": 1.0,
    "critical": true
  },
  {
    "text": "geometric patterns",
    "weight": 0.8,
    "critical": false
  }
]
```

If changing schema this much is too invasive, support both:

```json
["cream clothing", "geometric pattern"]
```

and richer objects.

## Matching

Do not require exact string equality.

Use currently available text/object normalization.

At minimum:

- lowercase
- punctuation normalization
- simple token overlap
- object label matching

Keep logic deterministic.

Do not make LLM calls at scoring time.

## Candidate scoring

Add configurable blend:

```yaml
temporal:
  constraint_coverage_weight: 0.15
```

For an event candidate:

```text
new_event_score =
    (1 - constraint_weight) * retrieval_score
    + constraint_weight * constraint_coverage
```

For a critical constraint with no supporting evidence, optionally apply:

```text
critical_missing_penalty
```

Do NOT hard reject by default.

Make rejection configurable because captions/object tags may be incomplete.

Expose:

```json
{
  "constraint_coverage": 0.75,
  "matched_constraints": [...],
  "missing_constraints": [...]
}
```

in debug score breakdown.

---

# PART 6 — IMPROVE MULTI-PERSPECTIVE FUSION

## Current behavior

Current approximate formula:

```text
0.58 * best_score
+ 0.24 * avg_score
+ 0.13 * view_rrf
+ 0.05 * coverage
```

This over-rewards a candidate that strongly matches only one query view.

## Required change

Move fusion weights to config.

Example:

```yaml
multi_view_fusion:
  best_score_weight: 0.35
  average_score_weight: 0.25
  rrf_weight: 0.15
  view_coverage_weight: 0.10
  constraint_coverage_weight: 0.15
```

Normalize weights.

Do not hardcode new constants inside the algorithm.

Add minimum view-support information:

```text
matched_views
total_views
view_coverage
```

Allow optional penalty when:

```text
matched_views == 1
and total_views >= 3
```

but make it soft.

Suggested:

```text
support_penalty =
1 - alpha * (1 - coverage)
```

Then:

```text
score *= support_penalty
```

Do not destroy candidates simply because one variant failed.

Maintain recall.

---

# PART 7 — ENABLE AND PROPERLY CONFIGURE CLIP + SIGLIP2 VISUAL ENSEMBLE

## Current problem

The model registry contains SigLIP2, but default competition profile disables the SigLIP2 visual model.

## Required change

Create a new retrieval profile rather than silently changing every existing benchmark.

Example:

```yaml
competition_temporal_v2:
```

Use:

```yaml
visual_models:
  clip_global:
    enabled: true
    weight: 1.0

  siglip2_fine_grained:
    enabled: true
    weight: 1.0
```

Fuse with visual RRF.

Do not concatenate incompatible embeddings.

Preserve separate embedding spaces.

Use:

```text
CLIP ranking
+
SigLIP2 ranking
→ RRF
```

Expose per-model contribution in debug output.

Do not remove existing single-model modes.

---

# PART 8 — REPLACE FRAME-INDEX TEMPORAL DISTANCE WITH TIMESTAMP DISTANCE

## Critical problem

Current temporal code effectively does:

```python
delta_frames =
    delta_t_max_ms / 1000 * 30
```

This assumes 30 FPS.

Temporal validation then compares:

```text
frame_idx
```

This is not robust across:

- varying FPS
- irregular keyframe sampling
- extracted frame subsets
- videos with different frame rates

## Required change

Extend temporal `Candidate` with:

```python
timestamp_ms: int | None
```

Populate from `Frame.timestamp_ms`.

Use timestamp ordering as the primary temporal coordinate.

Temporal relation:

```python
candidate.timestamp_ms > previous.timestamp_ms
```

and gap:

```python
candidate.timestamp_ms - previous.timestamp_ms
```

Compare directly against:

```text
delta_t_max_ms
```

Do not convert milliseconds to frames.

## Fallback

If timestamp is unavailable:

fallback to `frame_idx`.

But explicitly mark:

```json
{
  "temporal_coordinate": "frame_idx_fallback"
}
```

Otherwise:

```json
{
  "temporal_coordinate": "timestamp_ms"
}
```

Update both:

- `ats.py`
- `vortex.py`

and any other temporal ordering logic.

Add tests with different FPS values proving that timestamp ordering gives consistent results.

---

# PART 9 — SUPPORT PER-EDGE TEMPORAL WINDOWS

## Problem

One global:

```text
delta_t_max_ms = 180000
```

is too broad and treats all event transitions equally.

## Planner change

Allow temporal relation metadata:

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

Do NOT ask the planner to hallucinate exact seconds.

Use discrete classes:

```text
near
short
medium
loose
```

Map these through config:

```yaml
temporal:
  gap_classes_ms:
    near: 15000
    short: 45000
    medium: 120000
    loose: 300000
```

Default to the current global max if edge information is missing.

## Temporal sequence search

When validating:

```text
E1 -> E2
```

use edge-specific threshold.

When validating:

```text
E2 -> E3
```

use its own threshold.

Do not use one constant threshold for all adjacent events.

---

# PART 10 — REMOVE HARD DEPENDENCE ON `prefer_full_sequences`

## Current problem

If any full sequence exists, current logic may discard all partial sequences.

This can cause:

```text
correct video:
E1 strong
E2 strong
E3 missed by retrieval

wrong video:
E1 weak
E2 weak
E3 weak
```

to prefer the wrong video because it has 3/3 weak matches.

## Required scoring redesign

Do not immediately discard partial sequences.

Add:

```text
coverage =
matched_event_count /
expected_event_count
```

Sequence score should include:

1. matched event evidence
2. event importance
3. event coverage
4. missing event penalty
5. temporal gap penalty
6. compactness
7. optional diagnostic event bonus

Suggested conceptual formula:

```text
weighted_event_score =
sum(event_weight_i * candidate_score_i)
/
sum(all_expected_event_weights)
```

This denominator is important.

Missing events should effectively contribute zero rather than disappear from the denominator.

Then:

```text
sequence_score =
weighted_event_score
+ coverage_weight * coverage
- missing_event_penalty * missing_ratio
- temporal_gap_penalty
```

After that apply compactness if desired.

Do not divide only by `len(sequence)`.

## Configuration

Add:

```yaml
temporal:
  sequence_coverage_weight: 0.15
  missing_event_penalty: 0.10
  prefer_full_sequences: false
```

Keep old hard behavior available as legacy mode if necessary.

---

# PART 11 — MAKE `min_match` USE PROFILE `min_match_ratio`

## Problem

Profile currently contains:

```yaml
min_match_ratio: 0.67
```

but KIS logic calculates its own approximately 0.6 behavior.

Unify this.

## Required logic

If user explicitly sets:

```text
options.min_match
```

respect it.

Otherwise:

```python
min_match =
ceil(num_events * profile.temporal.min_match_ratio)
```

with safe bounds:

```text
1 <= min_match <= num_events
```

For multi-event KIS, optionally require at least 2 when `num_events >= 2`.

Avoid duplicated constants across code.

---

# PART 12 — IMPROVE EVENT SCORE CALIBRATION

## Current problem

Current scoring normalizes semantic scores approximately as:

```text
semantic_score =
raw_score / max_score_in_candidate_pool
```

Therefore a weak event retrieval can still produce:

```text
1.0
```

for the best bad candidate.

Temporal search may interpret that candidate as highly confident.

## Required change

Preserve both:

```text
raw_score
relative_score
```

for semantic retrieval.

Do the same where useful for text retrieval.

Extend candidate debug information.

Add an event confidence signal:

```text
calibrated_score =
alpha * relative_score
+ (1 - alpha) * absolute_confidence
```

Do not guess one universal absolute threshold.

Make calibration configurable per visual model if necessary.

At minimum, do not discard raw similarity before temporal scoring.

Example Candidate fields:

```python
score
raw_visual_score
normalized_visual_score
text_score
rrf_score
```

If multiple visual models use RRF and absolute scores are not directly comparable, preserve per-model raw similarities separately.

The temporal layer should know when an event candidate comes from weak absolute evidence.

---

# PART 13 — ADD EVENT-LEVEL CONFIDENCE / RETRIEVAL HEALTH

For each event calculate metrics such as:

```json
{
  "event_index": 3,
  "top_score": 0.82,
  "score_margin": 0.14,
  "candidate_video_count": 27,
  "retrieval_confidence": 0.78
}
```

`score_margin` can compare:

```text
top score
vs
top-k mean / second score / percentile
```

Use deterministic signals.

Do not call an LLM.

Use this confidence later for sequence weighting.

Example:

```text
effective_event_weight =
importance
* retrieval_confidence
```

But clamp it so a difficult event is not completely ignored.

Expose this only as a configurable feature.

---

# PART 14 — ADD TEMPORAL NMS / CANDIDATE DEDUPLICATION

## Problem

Many adjacent keyframes from the same shot can occupy top-k slots.

This reduces candidate diversity and wastes temporal search budget.

## Required change

Implement temporal NMS before expensive sequence construction.

For candidates belonging to the same:

```text
video_id
event_index
```

if timestamps are closer than:

```yaml
temporal:
  event_nms_window_ms: 3000
```

keep the better-scoring candidate.

Optionally retain up to N candidates per temporal neighborhood.

Do NOT globally deduplicate across different events.

Example:

```text
E1:
frame 100 @ 10.0s score .91
frame 102 @ 10.2s score .90
frame 105 @ 10.5s score .89
```

should not consume 3 candidate positions by default.

Keep one representative candidate unless configured otherwise.

Expose before/after candidate counts in debug output.

---

# PART 15 — IMPROVE PER-VIDEO CANDIDATE PRUNING

## Current problem

`per_query_video_limit` keeps top candidates by score.

This can keep many almost-identical frames from one local region.

## Required change

Use diversity-aware per-video pruning.

Algorithm:

1. sort candidates by score descending
2. greedily select candidate
3. suppress candidates within configured timestamp radius
4. repeat until per-video limit reached

This should happen separately for each event.

Example config:

```yaml
temporal:
  per_query_video_limit: 24
  per_video_candidate_min_gap_ms: 1500
```

Fallback to current score-based pruning when timestamps are unavailable.

---

# PART 16 — ADD LOCAL EXACT-FRAME CANDIDATE EXPANSION

## Goal

After temporal search identifies the likely video and approximate event timestamp, generate denser nearby frame candidates.

## Required behavior

For the top temporal sequences, allow optional local expansion:

```yaml
temporal:
  local_refinement:
    enabled: true
    window_before_ms: 5000
    window_after_ms: 5000
    max_frames_per_event: 15
```

Use already ingested frames first.

Do not decode raw video unless there is an existing safe abstraction for doing so.

If nearby frames exist in the database:

retrieve frames around:

```text
anchor/event timestamp ± window
```

Rank local frames using existing:

- CLIP
- SigLIP2
- caption/text
- constraint coverage

Preserve original temporal event ordering.

Return:

```json
{
  "local_refinement": {
    "source_frame": "...",
    "candidate_count": 11,
    "best_local_frame": "..."
  }
}
```

This stage should refine exact frame location after the correct video/sequence has already been identified.

---

# PART 17 — REPRESENTATIVE FRAME SELECTION FOR `video_sequence`

When:

```text
target_scope == video_sequence
```

do not arbitrarily choose the middle event.

Implement configurable representative selection:

```yaml
temporal:
  representative_frame_policy: highest_event_score
```

Support:

```text
highest_event_score
highest_diagnostic_event
middle_event
```

Default new temporal profile:

```text
highest_event_score
```

This affects UI/result presentation only.

It must not affect temporal sequence ranking.

Expose:

```json
{
  "representative_event_index": 3,
  "representative_frame_policy": "highest_event_score"
}
```

---

# PART 18 — ADD TEMPORAL SEARCH DEBUG INFORMATION

For every result sequence, expose enough information to diagnose failures.

Include:

```json
{
  "sequence_score": 0.82,
  "matched_events": 3,
  "expected_events": 3,
  "coverage": 1.0,
  "event_scores": [
    {
      "event": 1,
      "frame": "...",
      "timestamp_ms": 10000,
      "retrieval_score": 0.81,
      "constraint_coverage": 0.75,
      "raw_visual_score": 0.31
    }
  ],
  "temporal_edges": [
    {
      "from": 1,
      "to": 2,
      "gap_ms": 27000,
      "max_gap_ms": 120000,
      "valid": true
    }
  ],
  "missing_events": [],
  "diagnostic_event_index": 3,
  "candidate_video_rank": 2
}
```

Do not add huge payloads unnecessarily.

Keep detailed debug behind existing debug settings if appropriate.

---

# PART 19 — CREATE A NEW PROFILE INSTEAD OF DESTROYING THE OLD ONE

Create something similar to:

```yaml
competition_temporal_v2:
```

It should enable the new logic.

Recommended starting values:

```yaml
competition_temporal_v2:
  semantic_weight: 0.65
  metadata_weight: 0.30
  user_boost_weight: 0.05

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

  temporal:
    retrieval_mode: diagnostic_video_first
    diagnostic_event_count: 1
    diagnostic_candidate_frames: 500
    candidate_video_limit: 40

    min_match_ratio: 0.67

    prefer_full_sequences: false
    sequence_coverage_weight: 0.15
    missing_event_penalty: 0.10

    compactness_weight: 0.20
    gap_penalty: 0.15

    per_query_video_limit: 24
    per_video_sequence_limit: 4

    event_nms_window_ms: 3000
    per_video_candidate_min_gap_ms: 1500

    constraint_coverage_weight: 0.15

    gap_classes_ms:
      near: 15000
      short: 45000
      medium: 120000
      loose: 300000

    representative_frame_policy: highest_event_score

    local_refinement:
      enabled: true
      window_before_ms: 5000
      window_after_ms: 5000
      max_frames_per_event: 15
```

Treat these numbers as initial defaults, not universally optimal constants.

Keep all important values configurable.

---

# PART 20 — EXPECTED PIPELINE AFTER THE REFACTOR

For ordinary single-event KIS:

```text
query
→ query expansion
→ CLIP/SigLIP2 retrieval
→ hybrid fusion
→ result
```

Do not unnecessarily invoke complex temporal logic.

For temporal/narrative KIS:

```text
query
↓
planner
↓
E1 / E2 / E3
↓
target_scope detection
↓
diagnostic event selection
↓
global diagnostic retrieval
↓
candidate videos
↓
remaining event retrieval inside candidate videos
↓
constraint-aware event scoring
↓
temporal NMS
↓
timestamp-based sequence search
↓
soft coverage sequence scoring
↓
sequence diversification
↓
optional local frame refinement
↓
final sequence / representative frame
```

For TRAKE:

retain strict event order behavior, but migrate temporal distance to timestamps and improve score handling without breaking TRAKE event labels.

---

# PART 21 — IMPORTANT ALGORITHMIC REQUIREMENTS

Do not implement temporal ordering using arbitrary list order.

Always validate:

```text
timestamp(E1) < timestamp(E2) < timestamp(E3)
```

when all events are present.

For partial sequences:

preserve event indices.

Example valid partial sequence:

```text
E1 -> E3
```

must still know that E2 is missing.

Do not reinterpret it as:

```text
event1 -> event2
```

Keep:

```python
candidate.event_index
```

as source of truth.

---

# PART 22 — SCORING REQUIREMENTS

Avoid uncontrolled score inflation.

Every score must have a documented range where practical.

Recommended normalized outputs:

```text
retrieval_score ∈ [0, 1]
constraint_coverage ∈ [0, 1]
coverage ∈ [0, 1]
compactness_multiplier ∈ [0, 1]
```

If using penalties, make it clear whether score can become negative.

Prefer final normalized scores in:

```text
[0, 1]
```

or document otherwise.

Do not mix:

```text
raw cosine similarity
BM25 score
RRF score
normalized semantic score
```

directly without normalization/calibration.

---

# PART 23 — TESTING REQUIREMENTS

Add unit tests and integration-style retrieval tests where possible.

Minimum tests:

## Query decomposition

Regression Vietnamese narrative query should produce:

```text
>= 3 temporal events
```

or planner-level equivalent.

## No false decomposition

```text
A man enters a kitchen. He wears a blue shirt.
```

should not necessarily become two temporal events if second sentence is same-state description.

## Timestamp temporal ordering

Test videos with:

```text
24 FPS
30 FPS
60 FPS
```

Equivalent timestamp sequences should produce identical validity.

## Partial sequence scoring

Test:

```text
Video A:
E1=.9
E2=.9
E3=missing

Video B:
E1=.55
E2=.55
E3=.55
```

The behavior must follow configured coverage/missing penalties, not blindly favor B because it is complete.

## Full strong sequence

```text
E1=.9
E2=.85
E3=.92
```

must outrank partial alternatives.

## Temporal NMS

Three almost identical adjacent frames should collapse to one candidate neighborhood.

## Diagnostic video retrieval

A rare strong E3 event should reduce the number of candidate videos before E1/E2 search.

## Missing diagnostic retrieval

When diagnostic retrieval fails, global event retrieval fallback must still work.

## Backward compatibility

Existing:

```text
single-event KIS
QA
TRAKE
```

tests must remain passing.

---

# PART 24 — OBSERVABILITY

Add timing measurements for:

```text
query planning
diagnostic retrieval
candidate video generation
per-event retrieval
temporal NMS
temporal sequence construction
local refinement
total latency
```

Include them in debug metadata when appropriate.

This is necessary to compare:

```text
old global retrieval
vs
diagnostic video-first retrieval
```

Do not log sensitive configuration or API keys.

---

# PART 25 — IMPLEMENTATION ORDER

Implement in this order:

1. Extend planning schema.
2. Add narrative decomposition.
3. Add `target_scope`.
4. Add diagnostic score.
5. Preserve `must_have`.
6. Change temporal candidates to use timestamps.
7. Refactor ATS/Vortex scoring.
8. Add soft sequence coverage.
9. Add temporal NMS.
10. Add diagnostic-video-first retrieval.
11. Add CLIP + SigLIP2 profile.
12. Add local frame refinement.
13. Add debug metadata.
14. Add tests.
15. Run existing tests and fix regressions.

Do not attempt all changes inside one massive function.

Create small testable helper functions/modules.

---

# PART 26 — CODE QUALITY

Requirements:

- type hints
- clear dataclasses/models
- avoid duplicated temporal calculations
- no hidden 30 FPS assumptions
- no magic constants when configuration is appropriate
- no silent exception swallowing for core logic
- retain graceful fallback for optional models/services
- preserve current API compatibility when possible
- document non-obvious scoring formulas
- add tests for every new algorithmic branch

Do not over-engineer with unnecessary abstractions.

---

# FINAL DELIVERABLE

After implementation, provide:

1. List of changed files.

2. For every changed file:
   - what changed
   - why it changed

3. Show the final temporal search pipeline.

4. Show the new sequence scoring formula.

5. Show how the regression query is decomposed.

6. Show which event was chosen as diagnostic and why.

7. Show how `target_scope` behaves for that query.

8. Show how timestamp-based temporal validation works.

9. Show fallback behavior.

10. Report tests:

- passed
- failed
- newly added tests

11. Identify any remaining limitations.

Do not claim an improvement in retrieval accuracy unless measured.

If no benchmark data is available, state that the changes improve the architecture and failure handling but must still be evaluated empirically.

# DEV hard-sample and close-event test report v2

## Scope

- Ground truth source: `data/experiments/aic_2026_groundtruth/aic2026_round_3.csv`.
- Evaluation set: `p2-1`, `p2-2`, `p2-3`, `p2-4`, `p2-15`; hard-sample
  diagnostic: `p2-11`.
- Current GT entries: `L26_V424,5111`; `L22_V026,1514`; `L26_V390,6010`;
  `L25_V075,975`; `L26_V480,4618`; hard sample `L26_V483,599`.

## Root-cause analysis by module

| Module | Observation | Consequence | Change |
| --- | --- | --- | --- |
| Prompt/planner | Earlier plans can reduce a visual state to generic verbs (`take`, `add`, `stir`) or split colour/shape/layout across event fragments. | Global retrieval chooses visually common cooking videos. | New v4 hard-visual-state prompt. |
| Global DEV retrieval | The pool was 75 videos for `top_k=50`; it was too small when a rare event ranks after generic candidates. | Target video may never enter local temporal search. | Pool now targets 130 videos (`top_k + 80`), capped at 160. |
| Local DEV retrieval | Local event request was top-80, insufficient to expose frames from every selected video. | Good video can be selected globally but have no representative local event frame. | Local top-k raised to 200, widening to 400 only when required. |
| Temporal scoring | Ordering allowed nearby events, but the score normalized gaps against 300 seconds, so it did not materially prefer genuinely close state changes. | Generic events could be stitched across remote shots. | DEV gap penalty reference is now configurable at 45 seconds; planner v4 labels immediate edges `short`. |
| Dynamic sampling | It samples only after a sequence has an event gap and correctly does not count as semantic evidence. | It cannot recover a video omitted by global retrieval. | Retained as safe contextual fallback; not misrepresented as a recall fix. |
| Vietnamese semantic views | The model does not always emit `siglip2_views`; Vietnamese-only direct probes for p2-11 did not return the GT video in top-100. | Adding Vietnamese blindly can add cost/noise rather than recall. | v4 makes the field mandatory and DEV consumes it only when present; results must be rechecked before assigning a positive blend. |

## p2-11 hard-sample audit

GT closest indexed keyframe is `L26_V483_F000590` (GT frame 599).  It shows
white expanded, interwoven strand-like food clustered on a white tray, with a
metal strainer.  The query's most discriminative evidence is therefore the
joint visual state: **white + expanded + connected strands + white support**.
It is not the generic actions "woman takes" or "adds ingredients".

Direct visual probes, used only to locate the failing module:

| Probe | GT rank | Interpretation |
| --- | ---: | --- |
| `white expanded strand-like food clustered together on a white plate` | 16 | English global semantic recall is weak but present. |
| `white puffed strands stuck together arranged on a white tray` | 6 | A compact conjunction is substantially better, but still fails top-5. |
| Vietnamese-only OpenCLIP/SigLIP2/both probes | not in top-100 | Current Vietnamese embedding query does not rescue this hard sample. |

This proves the immediate bottleneck is **global semantic recall**, not
temporal ordering: the GT video is not sufficiently high before DEV can apply
its local frame search.  Dynamic sampling cannot fix that condition because it
never searches outside selected videos.

## New prompt version

Created `configs/agent prompt/agent_v4_dev_hard_visual_states.md` and set it
as `agent_v4_dev_hard_visual_states` in configuration.  Previous v1, v2, and
v3 prompts remain unchanged.

V4 requires a dedicated appearance-state event for colour, expansion, shape,
texture, grouping, and layout; keeps those facts in one English OpenCLIP view
and one equivalent Vietnamese SigLIP2 view; constrains lexical Vietnamese
`text_query` to 12 words; and distinguishes nearby `short` transitions from
later steps.

## DEV changes

```yaml
candidate_video_limit: 32
output_video_buffer: 80
max_candidate_video_limit: 160
event_retrieval: {initial_top_k: 200, max_top_k: 400, min_candidates_per_event: 24}
temporal_gap_reference_ms: 45000
```

The global coarse pass still uses only one primary view/event.  Fine local
retrieval uses at most two English views plus one Vietnamese SigLIP2 view when
the planner supplies it.  Thus video recall is increased without multiplying
the global embedding/Milvus workload.

## Verification

```text
pytest -q tests/test_dev_first.py tests/test_aithena_ats.py
19 passed

pytest -q tests/test_retrieval_pipeline.py -k 'dev_first or temporal_search'
3 passed
```

The backend image was rebuilt after the changes.

## Result status

The five-query exact-GT top-5 success condition is **not yet met**.  It would
be invalid to claim otherwise: p2-11's strongest non-leaking visual
description is rank 6, and the other current-GT queries need a fresh complete
run after v4 with the planner emitting valid `siglip2_views`.

Next experiment: enforce `siglip2_views` with structured output and record
global video recall, selected-video recall, local-frame recall, and temporal
rank for each query.  That instrumentation will reveal whether the enlarged
pool recovers the target before making any further temporal-score change.

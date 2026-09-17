# DEV KIS benchmark test report v4

## Scope and sources

Benchmark labels were read from
`data/experiments/aic_2026_groundtruth/aic2026_round_3.csv`. Runs used the
local backend, dataset `917efbbe-44b8-4476-98ef-3e7ec7ca7958`,
`dev_first_search`, `gpt-5-nano`, visual mode `both`, `top_k=10`, no query
expansion, no external reranker, and `delta_t_max_ms=180000`.

Ground-truth numeric labels are frame indices, not timestamps. A hit below is
defined by the GT video appearing in final top-10; returned frame index is
reported where that occurs.

## Prompt audit

All prompt files under `configs/agent prompt/` were reviewed.

| Version | Finding |
| --- | --- |
| v1 | Reliable English-only baseline but does not request a SigLIP2 view. |
| v2 | Detailed coarse/fine and evidence rules, but its large schema can reduce output compliance. |
| v3 | Good bilingual event contract; optional fields were historically omitted. |
| v4 | Best hard appearance-state guidance but still verbose. |
| v5 | Compact reliable contract, but permits visual weight 0.70 and can over-admit metadata for purely visual KIS. |
| v6 | New evidence-adaptive contract. Visual cooking/action/appearance queries require visual >=0.90; OCR/ASR are reserved for explicitly textual evidence. |

`agent_v6_dev_evidence_adaptive.md` was added without overwriting older
versions and made active only for this experiment.

## Measured v6 results

| Query | GT video/frame index | Runtime | Candidate videos | Sequence pool | GT rank | Returned frame index | Result |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| p2-1 | `L26_V424`, 5111 | 103.8 s | 90 | 61 | 9 | 3891 | video top-10; wrong frame |
| p2-2 | `L22_V026`, 1514 | 55.7 s | 90 | 22 | 1 | 1504 | video top-10; delta 10 |
| p2-3 | `L26_V390`, 6010 | 57.5 s | 90 | 60 | — | — | miss |
| p2-4 | `L25_V075`, 975 | 54.5 s | 90 | 19 | 5 | 13973 | video top-10; wrong frame |
| p2-11 | `L26_V483`, 599 | 49.2 s | 90 | 37 | — | — | miss |

V6 achieves GT-video Recall@10 of **3/5**. Every request returned 10 results
and had a sequence pool larger than 10, so misses are not due to output
truncation. It does not satisfy the requested 5/5 top-10 criterion.

## Checkpoint diagnosis

### p2-1 and p2-4: video recall recovered, frame localization remains weak

V6 moved p2-1 to rank 9 and p2-4 to rank 5 by suppressing irrelevant lexical
evidence and prioritizing visual plans. Their returned frames differ greatly
from labels, so the remaining weakness is local frame ranking/representative
event selection, not lack of candidate videos.

### p2-2: correct video and near-correct frame

P2-2 is rank 1 with frame-index delta 10. This is evidence that the
visual-first school-line plan and local temporal ordering work when the global
visual evidence is distinctive.

### p2-3 and p2-11: global semantic recall bottleneck

Before temporal construction, direct full-query hybrid diagnostics failed to
place their GT videos in the global top-1,000. Final DEV traces also have
ample candidates (60 and 37 sequences), yet neither GT video reaches top-10.
No local Vortex, dynamic sampling, or min-match adjustment can recover a video
that lacks global event evidence. This localises the current limitation to
embedding/index semantic recall and prompt-to-event representation.

## DEV changes verified in this iteration

- The global pass uses one English view per event plus at most one bilingual
  SigLIP2 re-probe for the selected diagnostic event; it avoids global
  multiplication of views while retaining hard-state recall.
- Default multi-event min-match is capped at two to avoid rejecting sparse
  AutoShot narratives with three required events.
- A selected video missing the diagnostic event may use its strongest available
  event as a scored fallback anchor; missing evidence remains penalized.
- Timestamp-less sequences use strict frame order without comparing frame index
  to millisecond deltas.

## Verification

```text
pytest -q tests/test_dev_first.py tests/test_aithena_ats.py
23 passed

pytest -q tests/test_retrieval_pipeline.py -k 'dev_first or temporal_search'
3 passed
```

## Conclusion

The v6 prompt is the best measured prompt in this iteration: it improves the
final top-10 result to 3/5 and produces a near-exact p2-2 frame. It should not
be represented as a 5/5 solution. The next evidence-backed experiment should
improve global embedding recall for p2-3/p2-11 (for example, a model-specific
visual ensemble evaluated separately), then re-run the same stored query plans
to separate model recall from planner variance.

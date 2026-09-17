# Temporal search benchmark — AIC 2026 round 3

## Scope and protocol

- Dataset: `aic-ai-challenge-2025` (`917efbbe-44b8-4476-98ef-3e7ec7ca7958`), 873 videos.
- Ground truth: `data/experiments/aic_2026_groundtruth/aic2026_round_3.csv`.
- Queries: `query-p2-1-kis` to `query-p2-4-kis`, plus `query-p2-15-kis`.
- Search: `top_k=50`, temporal mode, `delta_t_max_ms=180000`, visual fusion `both` (OpenCLIP + SigLIP2), no reranker/query expansion.  The CSV label line (`Câu query-...`) was removed before sending the natural-language query.
- Candidate validation: exact video code, returned keyframe index, output count, and top-5 rank.  “Exact frame” means exact GT index; a nearby AutoShot frame is reported separately.

## ATS and Vortex results

The table records the final benchmark run with the configured `gpt-5.6-luna` agent profile.  That profile had no configured gateway endpoint and therefore used the service's deterministic fallback planner.  This is an environment limitation, not a silent planner success.

| Query | GT | ATS: time / output / GT rank | Vortex: time / output / GT rank | Top-5 result |
| --- | --- | --- | --- | --- |
| p2-1 | `L26_V183,5991` | 5.34 s / 50 / — | 9.08 s / 50 / — | miss |
| p2-2 | `L22_V026,1514` | 5.31 s / 50 / 3 (`1475`) | 5.00 s / 35 / 3 (`1475`) | video hit in top-5; frame is -39 frames (1.30 s) from GT |
| p2-3 | `L26_V390,6010` | 11.66 s / 18 / — | 10.74 s / 50 / — | miss |
| p2-4 | `L25_V075,975` | 7.53 s / 50 / 16 (`13612`) | 6.68 s / 50 / 25 (`13612`) | miss |
| p2-15 | `L26_V480,4618` | 7.95 s / 50 / 14 (`3771`) | 10.28 s / 50 / 16 (`3771`) | miss |

Mean wall time: ATS **7.56 s**, Vortex **8.36 s**.  Only p2-2 puts the correct video in top-5; it is not an exact-frame hit.  GT frame 1514 is not an indexed AutoShot keyframe; the closest stored keyframe is 1504, while both temporal methods returned 1475.

Therefore the requested all-query, exact-ground-truth top-5 success criterion is **not met**.  This report deliberately retains the failures for reproducibility.

## DEV changes and verification

Only DEV code/configuration was changed. ATS and standalone Vortex implementation were not modified.

1. **Video diversity and sufficient pool.** DEV now derives its video pool from all event probes, reserves unique videos per weighted event, then fills by weighted cross-event support.  The pool grows with the requested `top_k`: `top_k + 25`, capped at 100.  For p2-1: 75 selected videos, 51 sequence candidates before final top-50 truncation, and 50 returned results.
2. **Less redundant work.** The old second global top-500 diagnostic request was removed.  DEV reuses the first independent probe for diagnostic selection, saving one embedding/Milvus global pass; local retrieval remains a genuine second Milvus search constrained by `video_id in [...]`.
3. **Fallback-plan atomization.** Only DEV splits an overlong fallback event at sentence/temporal boundaries.  Child events inherit the parent's retrieval directives and divide its importance, conserving total planner weight.
4. **Temporal correctness.** DEV uses the Vortex-style same-video anchor expansion only after local retrieval.  Every extension is strictly ordered and bounded by the agent edge gap class or request delta.  The output identifies dynamic samples and never counts them as semantic event evidence.
5. **Noise control when full chains are sparse.** If complete/partial chains do not provide more than `top_k` candidates, DEV appends only previously unseen videos with one-event evidence.  Missing-event penalties keep them below strong ordered chains.  p2-1 used this fallback; p2-2 did not (58 candidates before final top-50).

DEV smoke checks:

| Query | Result | Candidate videos | Sequence pool | Notes |
| --- | --- | ---: | ---: | --- |
| p2-1 | no exception; 50 results | 75 | 51 | partial-completion fallback activated; GT not in results |
| p2-2 | no exception; 50 results | 60 | 58 | GT video rank 14 at frame 1475 |

Unit/integration verification after the change:

```text
pytest -q tests/test_dev_first.py tests/test_aithena_ats.py  -> 19 passed
pytest -q tests/test_retrieval_pipeline.py -k 'dev_first or temporal_search' -> 3 passed
```

## Diagnostics and next action

Both visual encoders were active and individually reachable.  A direct non-temporal visual diagnostic for p2-1, using an English atomic description of the egg-pouring frame, retrieved the exact `L26_V183_F005991` at rank 6/100 with OpenCLIP+SigLIP2 fusion.  Thus the primary residual issue is not an absent embedding/index but query-plan translation/decomposition quality and temporal score competition.

The direct `gpt-4o` planner profile is reachable; the configured `gpt-5.6-luna` profile is not, because `AGENT_GPT56_LUNA_BASE_URL` is unset.  Supplying that endpoint (or selecting `gpt-4o` for benchmark planning) is the next controlled experiment.  It should be evaluated without using ground-truth video/frame identifiers as filters, then compare exact-frame top-5 again.

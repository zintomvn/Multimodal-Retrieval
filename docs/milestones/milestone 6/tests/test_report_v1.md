# DEV bilingual prompt v1 test report

## Objective

Evaluate DEV on `aic2026_round_3.csv` for `p2-1` through `p2-4` and
`p2-15`, then improve only the DEV planning/retrieval route without replacing
the previous prompt versions.

## Changes under test

### New prompt version

Added `agent_v3_dev_bilingual` in `configs/agent.yaml`, with a new file:
`configs/agent prompt/agent_v3_dev_bilingual.md`.

It requests, for every temporal event:

- one or two concrete English views (9-20 words) for OpenCLIP/caption search;
- one grounded Vietnamese `siglip2_views` semantic view (9-24 words) for the
  SigLIP2 branch;
- a Vietnamese lexical view only for ASR/OCR;
- independent `diagnostic_prior` and `importance` values; and
- explicit order and temporal gap classes.

No prior prompt file was overwritten.

### DEV coarse-to-fine route

1. The planner now preserves prompt-supplied `siglip2_views` in each temporal
   event plan.
2. DEV includes those Vietnamese semantic views when visual mode is `siglip2`
   or `both`; English and Vietnamese visual phrases remain distinct from
   lexical ASR/OCR views.
3. The coarse global diagnostic pass uses only the primary English view per
   event.  The fine local pass caps work at two English semantic views plus one
   Vietnamese SigLIP2 view per event.  This prevents the previous 8-view/event
   multiplication from dominating latency.
4. DEV continues to use all event importance weights, chooses diverse videos
   from all event probes, runs a second Milvus retrieval restricted to those
   videos, and only then runs timestamp-ordered Vortex-style re-scoring.

## Ground-truth visual audit

The target keyframes are present in the index.  Direct non-temporal visual
probes were run strictly as diagnostics, not as benchmark results:

| Query | GT frame | Compact visual diagnostic | GT rank / returned frame |
| --- | --- | --- | --- |
| p2-1 | `L26_V183,5991` | thin beaten-egg stream into transparent amber soup pot | 1 / 5991 |
| p2-2 | `L22_V026,1514` | schoolchildren in blue uniforms holding class signs | 1 / 1534 |
| p2-3 | `L26_V390,6010` | amber glass pot with macaroni, meat, vegetables on gas flame | 1 / 6010 |
| p2-4 | `L25_V075,975` | pink English teacher beside board showing Past Simple/Past Continuous | 1 / 67 |
| p2-15 | `L26_V480,4618` | chef adding green leaves and yellow pieces to oiled orange pan | 4 / 4576 |

These probes establish that both vector collections can retrieve the target.
They must **not** be treated as a fair score for the original benchmark,
because p2-1 and p2-3 add visual attributes visible only after inspecting the
GT (amber/transparent pot).  Those attributes do not occur in the original
text and must not be injected into a production search plan.

## Original-query benchmark baseline

The prior reproducible ATS/Vortex run used `top_k=50`, visual mode `both`,
180-second temporal window, no reranker/expansion, and the original Vietnamese
query text (without the `Cau query-...` label line).

| Query | ATS GT rank | Vortex GT rank | Result |
| --- | ---: | ---: | --- |
| p2-1 | - | - | miss |
| p2-2 | 3 (frame 1475) | 3 (frame 1475) | correct video top-5; GT 1514 is not an AutoShot keyframe |
| p2-3 | - | - | miss |
| p2-4 | 16 | 25 | miss |
| p2-15 | 14 | 16 | miss |

The all-five exact-frame top-5 criterion is therefore **not yet met**.  It
would be misleading to claim success merely because the visual diagnostic
queries above can recover GT after seeing target-frame-only attributes.

## Planner observations

`gpt-5-nano` successfully loaded `agent_v3_dev_bilingual` and produced
chronological English events with suitable lengths for p2-1.  The model did
not consistently emit the required `siglip2_views` field and sometimes copied
the complete source query into every `text_query`.  DEV safely falls back to
its English views in that case, but the Vietnamese semantic branch has no
additional view to execute.  This is a prompt-compliance issue, not an index
or temporal-order failure.

The configured `gpt-5.6-luna` profile cannot be used for this test because its
required gateway endpoint is not configured.  `gpt-5-nano` and `gpt-4o` are
reachable alternatives.

## Verification

```text
pytest -q tests/test_dev_first.py tests/test_aithena_ats.py
19 passed

pytest -q tests/test_retrieval_pipeline.py -k 'dev_first or temporal_search'
3 passed
```

## Recommended next experiment

Use the same v3 schema with structured-output/function-calling enforcement for
`siglip2_views` and a per-event Vietnamese translation repair when the field is
missing.  Re-run the original queries without GT-derived visual attributes and
report exact-frame top-5 separately from same-video/nearby-AutoShot recall.

# KIS embedding ablation

## Research questions

1. Does decomposing one long KIS description into several short visual perspectives improve keyframe retrieval?
2. Which embedding backbone is strongest on the same short perspectives: OpenCLIP, SigLIP2, or Qwen3-VL-Embedding-2B?

The controlled factors are dataset, keyframes, ground truth, Top-100 cutoff, cosine/vector-search configuration, perspective text, and fusion rule. Metadata retrieval, temporal retrieval, reranking, query expansion during search, and result diversification are disabled by the `embedding_ablation` profile.

## Experiment matrix

By default, every KIS query marked `Chắn chắn` with a parseable answer in
`data/experiments/aic_2026_groundtruth/` is run with every model:

- `full_query`, n=1: the original long KIS description;
- `perspective`, n=3;
- `perspective`, n=5;
- `perspective`, n=7.

Perspectives are generated once and persisted to `perspectives.json`. Every model receives the identical strings, and n=3/5/7 use prefixes of the same ordered list. Do not regenerate that file between model runs.

Each short view is searched independently. The existing multi-perspective merge uses:

```text
score = 0.58 * best_view_score
      + 0.24 * mean_matched_view_score
      + 0.13 * normalized_view_RRF
      + 0.05 * view_coverage
```

This rule remains fixed for all three backbones.

## Preconditions

Each model must have its own populated vector collection:

```text
keyframe_embeddings_clip_vith14_quickgelu_dfn5b_v2
keyframe_embeddings_siglip2_so400m16_384_webli_openclip_1152_v1
keyframe_embeddings_qwen3_vl_embedding_2b_2048_v1
```

The corresponding text-embedding endpoints must be reachable. Configure URLs and keys in `.env`; never put credentials in `.env.example`.

## Run

Start the backend, then run a six-query smoke benchmark first:

```powershell
.venv\Scripts\python.exe scripts\run_embedding_ablation.py `
  --api-base http://127.0.0.1:8000 `
  --benchmark-csv data/experiments/aic_2026_groundtruth `
  --limit 6 `
  --output-dir data/experiments/embedding_ablation_smoke
```

After validating the frozen perspectives and all three collections, run the complete KIS set:

```powershell
.venv\Scripts\python.exe scripts\run_embedding_ablation.py `
  --api-base http://127.0.0.1:8000 `
  --benchmark-csv data/experiments/aic_2026_groundtruth `
  --output-dir data/experiments/embedding_ablation_round3
```

The first full run creates a complete `embedding_ablation_round3/perspectives.json`. Pass that file with `--perspectives-file` when repeating the full experiment.

Use `--frame-tolerance N` only if the evaluation protocol explicitly accepts a temporal neighborhood around the annotated frame. The default is exact frame matching.

## Outputs

- `perspectives.json`: frozen model-independent views;
- `per_query.csv`: rank and latency for every query/configuration;
- `summary.csv`: Recall@1/5/10/50/100, MRR, mean/p50/p95 latency;
- `rank_table.md` and `rank_table.tex`: publication-style rank table, with `-1` for a Top-100 miss;
- `raw_responses.jsonl`: audit trail containing the views and backend response;
- `run_config.json`: parameters needed to reproduce the run.

## Interpretation

Compare `full_query` against n=3/5/7 within each model to answer the decomposition question. Compare models within a fixed n to rank the backbones on short queries. Report both aggregate metrics and the per-query rank table because perspective search can improve recall while hurting individual queries or latency.

The long original query and generated short views may differ in both language and wording. If a strict causal claim about splitting alone is required, add a frozen single full-length rewrite in the same language as the short views and report it as an additional control.

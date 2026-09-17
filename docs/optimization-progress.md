# Web/search optimization progress

Branch: `feat/ui-search-optimization`.
Base: local `main` / `origin/main` at `5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f`.
Plan: `output/ui-search-optimization-plan.md` (local review artifact).

## G0 / A20: request diagnostics foundation

Implemented request-scoped trace ID, HTTP Server-Timing, content-free logging,
aggregated retrieval spans and call counts. Synchronous route threadpool context
is covered; concurrent requests keep separate measurements. Stages overlap.
Existing response bodies and legacy latency fields remain compatible.

Read-only benchmark: `py scripts/benchmark_web_reads.py --samples 30 --output
data/ui-optimization/baseline.json` against backend 8010. Measures first request
separately from warm reads; it does not benchmark model quality or search.

Validation: 35 tests passed across telemetry and retrieval pipeline suites.

G0 is partial: source-labelled golden queries, finer provider/QA timings and full
live baseline remain to complete. No claim that all 22 audit items are fixed.

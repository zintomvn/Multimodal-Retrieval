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

## G1 / A01-A02: honest states and request ownership

Dataset/gallery errors no longer create mock data. Errors are visible with Retry;
search errors are distinct from empty results. Context requests use identity guards
and no fabricated context. Search, video lookup and Chat share a synchronous
request gate; repeated Enter cannot start duplicates, task/mode/session changes
cancel the active request, and late responses/finalizers cannot replace newer work.
Client JSON requests have a 120-second timeout; cancel is client-side only.

Validation: web build, 2 request ownership tests, and Playwright outage/retry,
duplicate submit, KIS-to-QA stale response and source-error scenarios passed.
Browser checks use controlled failures; retry loaded real local gallery data.

Live read baseline (17/09, 30 warm samples each): gallery median 323.68ms,
p95 525.68ms; exact video lookup median 16.52ms, p95 31.43ms. First requests
2417.36ms and 18.84ms respectively. These are baseline measurements, not gains;
the old three-sample lookup result is not representative of this exact query.
Raw local report: `data/ui-optimization/baseline.json`.

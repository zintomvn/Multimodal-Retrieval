# Web/search optimization progress

Branch: `feat/ui-search-optimization`.
Base: local `main` / `origin/main` at `5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f`.
Plan: `output/ui-search-optimization-plan.md` (local review artifact).

## Reviewed completion count (17/09)

See [remaining work and main-test root causes](optimization-backlog.md).
Of 22 original items, A02 is complete within its client-side scope; six are partial
(A01/A03/A04/A06/A17/A20), fifteen are not started. Thus 21 remain open.
Two baseline failures are tracked as child tasks B01 (A09/A16/A20) and B02 (A06),
not extra top-level optimization items. Both Markdown/HTML local plans include
the updated count, remaining acceptance work and proposed fixes.

B01 probe on base main confirms all four-event sequence assertions pass when
expected sources are ASR/Caption, as dictated by OCR gating. B02 originates in
commit 5b81741 switching ZIP export to CSV without updating tests/docs; proposal
preserves single-query CSV and restores an explicit multi-query ZIP contract.
Neither baseline failure has been fixed in production code in this update.

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

## G1 / A03: remove unbounded metadata fallback

Removed full-dataset frame/annotation ranking on empty indexed candidates. No-match
returns empty; source failures remain visible through additive retrieval_mode and
source_status fields. Elasticsearch exceptions/missing index no longer become
successful empty hits. Strict hybrid behavior remains compatible. The web labels
partial search results. No new replacement lexical index or reimport was needed.

Validation: full backend suite 121 passed / 2 failed. Both failures reproduced on
untouched base SHA in detached worktree `../Multimodal-Retrieval-baseline-20260917`:
TRAKE test expects OCR calls although heuristic disables OCR; submission test calls
missing legacy `export_zip`. These existing failures are not counted as passes.
New tests verify empty/outage search does not SELECT keyframes and persists source
status; adapter outage raises a distinguishable failure. Server-side overall
deadline/cancellation and finer per-source budgets are still outstanding.

## G1 / A06: validate before download

Export explicitly targets the current query, validates the server report and CSV
artifact before download, and never silently falls back to local CSV on failure.
An in-flight guard blocks duplicate export. Selection limits apply per query.
Multi-query export remains disabled until its file contract is implemented; query
identity/session work in G2 is still needed to distinguish multiple KIS questions.

Validation: 4 web unit tests; Playwright invalid report and 503 produced zero
downloads. Real local gallery selection/export downloaded a CSV through the API
(`output/playwright/optimization-live-export.csv`). QA/TRAKE export fixtures and
query-scoped session restoration still require follow-up.

## G1 / A04, A17: accessible preview and nonblocking drawers

On narrow screens drawers and their dismiss backdrop stop above the measured
composer, keeping Search clickable. Escape/backdrop dismiss drawers. Video modal
acquires focus, traps Tab/Shift+Tab, closes on Escape and restores opener focus.
Muted text tokens darken in light mode and brighten in dark mode.

Validation: production web build; Playwright checks Search hit target at
390/1024/1180/1440px and modal focus/Tab/Escape/restore on a real media preview.
Zoom 125%, broader toggle semantics and complete contrast audit remain outstanding.

## Next work

Finish G0 labelled retrieval/QA/TRAKE baselines, G1 A08 actual source readiness,
server deadlines and outstanding accessibility/export contract checks. Then G2
query persistence/identity and truthful controls, followed by G3 performance.
G4/G5 have not started. Changes are local commits, not pushed/deployed.

## Live verification after integration

Reloaded only the backend started by this implementation session on 8010.
Elasticsearch container was stopped; started this project's existing container
and verified `keyframe_annotations` still contains 783,835 documents, without
reimport. Other projects' containers and port 8000 were not modified.

With sources down: search returned zero results, mode degraded and both sources
unavailable, with no metadata fallback. After Elasticsearch recovery: the query
`a man riding a motorbike` returned 5 results, text ok, semantic unavailable.
Planning/expansion/reranking were disabled for this smoke to avoid provider calls.
Request duration was about 5.29s, of which semantic took 4.67s and text 0.47s.
This is one smoke observation, not a p95 or a retrieval-quality benchmark.
Trace includes commit/history cache; raw local report is
`data/ui-optimization/live-smoke.json`.

Web build and 4 unit tests passed; browser checks cover real gallery/media/export
and controlled outage/race/invalid export. Full backend result remains 121 passed
and the 2 pre-existing failures documented above. Diff whitespace and credential
pattern scan passed; no env, dataset, generated screenshots or unrelated docs
were staged. Branch has no upstream yet.

Implementation commits:

- `356c20c` A20 request/stage diagnostics foundation.
- `edee212` A01/A02 honest states and latest-request ownership.
- `240bfff` A03 remove full-dataset fallback, expose source status.
- `7e31b93` A06 validate current-query export before download.
- `c45e063` A04/A17 nonblocking drawers and video-modal focus.

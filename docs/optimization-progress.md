# Web/search optimization implementation

## Current scope: original UI with internal frontend improvements (2026-09-18)

PR #23 restored the original interface. Branch `perf/frontend-logic-original-ui`, based on merged main `d0666de`, restores only internal frontend behavior:

- Request identity/abort guards for search, video lookup, Chat, context and preview; duplicate search/export submissions are rejected.
- Versioned workspace persistence for task drafts, options, selected rows, search history and active mode. Existing incompatible saves are preserved. New searches receive distinct query identities.
- Memoized result grids preserve the original DOM/classes, column count and selection presentation. Context/evidence caches remain bounded; cancelling a context reader does not cancel another reader's shared request.
- Existing Settings/Video dialogs acquire and restore focus and support Escape/Tab trapping without adding controls or changing layout.
- The existing Export CSV workflow still submits all selected rows. Invalid/failed backend exports no longer download an unvalidated local fallback. QA stays inline; no deferred-answer panel is introduced.

CSS is unchanged from `5f49c49`; backend files are unchanged. No health panel, filters, source selector, event editor, ZIP button, Retry/Cancel controls, or card redesign is added. Search/Auto/Chat and Attach remain present.

Validation: production build and 8 frontend utility tests passed. `scripts/check_original_ui_logic.js` passed against an original-UI comparison server at port 5174 and the current UI at 5173: matching controls/card content/layout bounds at 1440x900; draft/options/selection/history/mode reload; duplicate/stale request rejection; modal focus; invalid/outage export rejection; 0 grid renders while typing in Search and Auto (dev instrumentation). Its request-failure and Chat-answer checks use controlled responses, not real model quality tests. The suite creates and closes its own browser context.

Separate live smoke passed: 48 gallery frames, video preview loaded, 50 KIS results and backend CSV export with the expected `L21_V001,0` row. Inline QA request shape was checked with a fixture; real QA/TRAKE model quality was not revalidated. Screenshots and raw CLI output are local under `output/playwright/logic-ui-*.png` and `data/local-web-session/logic*-results.txt`.

The following is the historical optimization snapshot. Its UI feature and 19/19 E2E claims do not describe the restored interface; use the current validation scope above.

## Historical snapshot before the UI restoration

Branch: `feat/ui-search-optimization`. Base local main/origin-main: `5f49c493b9cc9db2bdb7dfb6ff7da864731b4f2f`. No upstream, push or merge.

Current status: [22-item backlog](optimization-backlog.md). All 22 workstreams have changes; 18 meet local verification scope and 4 retain runtime/quality gates. B01/B02 are fixed child tasks.

## Implementation commits

| Commit | Change |
|---|---|
| 4f96afb | CSV/ZIP contracts and baseline test repairs |
| 6e04056 | Workspace, query identity, source overrides, one planning request |
| 92c99c1 | Immediate preview, bounded caches, memoized grid |
| f4452c7 | Source batches, dataset prefilters, time budgets |
| 921a707 | Actual readiness, deferred QA, event editor, filters |
| e757f9b | Worker leases, retry, completed-video checkpoint |
| ddad01f | Metadata cache, versioned index/evidence, rollback |
| b062631 | Keyboard/responsive/temporal browser coverage |
| b5ce5a7 | Live exports, index rollback, worker restart and benchmark tooling |
| 7176846 | React Profiler and 100-frame interaction measurements |

Final validation/documentation commits follow these. `git log main..HEAD` gives exact current history.

Backend 144 tests, frontend 7 tests and web build pass. Real gallery/context/CSV/ZIP and disposable ES rollback were exercised; browser QA/TRAKE responses are controlled fixtures. Golden KIS exact-frame recall remains zero with semantic retrieval unavailable, an open quality gate.

Warm gallery p95: 30.26ms over 30 samples, earlier baseline 525.68ms. Cold: 352.63ms. Count cache TTL is 10 seconds; frames remain live. Native browser zoom, field INP and sustained provider ingestion were not measured.

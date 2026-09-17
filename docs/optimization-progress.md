# Web/search optimization implementation

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

# Temporal retrieval algorithms

This package is the algorithm boundary for timestamp-aware temporal search.
It must remain independent of SQLAlchemy, FastAPI, vector databases, and
`RetrievalService`.

| Module | Responsibility |
| --- | --- |
| `types.py` | Stable immutable input/output contracts (`Candidate`, `TemporalSequence`) |
| `ats.py` | AIThena adaptive temporal sequence construction |
| `vortex.py` | Anchor-centred Vortex context reranking |
| `dev_first.py` | Diagnostic Event Video-first pipeline, composed from ATS and Vortex |
| `diversification.py` | Temporal candidate/sequence NMS and presentation diversity |

## Developing a DEV-first variant

1. Create a focused module such as `dev_first_v2.py`; import only contracts
   from `types.py` and any explicitly reused algorithms from this package.
2. Give the variant one public builder that accepts candidate sets, weights,
   planner edge constraints, config, and returns `DevFirstSequence` objects.
3. Keep score calibration, candidate-video selection, sequence construction,
   and scoring as separate pure functions.  Do not query the database or make
   HTTP/vector-search calls here.
4. Add unit tests using synthetic timestamped candidates.  Tests should cover
   ordering, gap constraints, missing events, ties, and expected scoring.
5. Wire the experiment at the small dispatch point in `RetrievalService` only
   after its algorithm tests pass.  Candidate retrieval and result persistence
   stay in the service, so variants can be compared with identical inputs.

`Candidate.timestamp_ms` is the canonical temporal coordinate.  Algorithms
may fall back to `frame_idx` solely for old data that has no timestamp.

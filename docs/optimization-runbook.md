# Optimization verification and operation

Use backend **8010**. Port 8000 belongs to another local project. Keep `.env` and data local.

## Verify

```powershell
rtk proxy py -m pytest apps/backend/tests -q
rtk proxy npm --prefix apps/web test
rtk proxy npm --prefix apps/web run build
rtk proxy py scripts/benchmark_web_reads.py --samples 30 --output data/ui-optimization/metadata-final.json
rtk proxy npx --yes --package @playwright/cli playwright-cli -s=optimization open http://127.0.0.1:5173
rtk proxy npx --yes --package @playwright/cli playwright-cli -s=optimization run-code --filename=scripts/check_workspace.js
```

Run other browser scripts in that dedicated test session: `check_search_reliability`, `check_keyboard_layout`, `check_responsive_controls`, `check_qa_workflow`, `check_trake_editor`, `check_render_profile`, `check_export_validation`, `check_export_live`, `check_export_zip` (all `.js`). Workspace checks reset only that test browser's storage. QA/TRAKE responses are controlled; functional success is not model accuracy.

`benchmark_search_api.py` accepts JSON cases containing `id`, `query`, `source`, `expected_frames`, optional `type`/`events`. It records latency, Server-Timing, source status, exact-frame recall@20/MRR and HTTP failures. Search writes query history. `--models` enables expansion/planning/reranking; semantic embeddings may still run for auto/scene without this flag. This is not an offline benchmark. The original `benchmark_retrieval.py` scoring CLI remains unchanged.

## Readiness, search and QA

`GET /readyz` or `/api/readyz` provides per-source health, cached 15 seconds. Reachable verifies connectivity/schema, not inference. Remote inference is not invoked by health checks. Local endpoint preflight: `apps/backend/scripts/check_embedding_endpoint.py` and its README.

Options: `source_mode=auto|ocr|asr|scene`; `deadline_ms` 100–120000, default 30000; `defer_qa`; `qa_candidate_limit` 1–10, default 3. Web defers QA and explicitly calls `POST /api/retrieval/results/{result_id}/answer`. Answers use aligned text evidence, not direct image understanding; citations persist. Manual answer entry remains available.

Provider admission is capped at eight calls. Timed-out SDK threads cannot be force-killed and retain their slot until they exit. Deadlines bound provider waiting, not every SQL/commit/HTTP overhead millisecond. Each ORM session stays in its request thread.

## Index versions

From `apps/backend`, generate a read-only manifest. Import into a new physical index, review coverage, then explicitly promote a **new alias**. Never reuse the name of an existing physical index as an alias. Default read target remains `keyframe_annotations`; set `ANNOTATION_READ_INDEX=annotations_read` only once that alias exists.

```powershell
rtk proxy py -m app.modules.ingest.index_versions annotations_v2 --manifest ../../data/manifests/v2.json
# Explicit promotion after review:
rtk proxy py -m app.modules.ingest.index_versions annotations_v2 --manifest ../../data/manifests/v2.json --alias annotations_read --expected-current annotations_v1 --apply
# Rollback using the reviewed v1 manifest:
rtk proxy py -m app.modules.ingest.index_versions annotations_v1 --manifest ../../data/manifests/v1.json --alias annotations_read --expected-current annotations_v2 --apply
```

Serialize administrative promotions. The expected-old-target guard is not a distributed deployment coordinator. Keep old physical indices while saved results reference them. Indexed text hits pin their physical version for QA/preview; a deleted old index produces a visible error instead of silently reading different evidence.

From repo root, `rtk proxy py scripts/check_index_rollback.py` creates uniquely named disposable indices/alias, checks promote/rollback/pinned evidence, and deletes only its fixtures. The application's read target is unchanged.

## Durable video pipeline

Opt-in worker; existing API/background flows remain compatible. Initialize the additive lease table in the intended DB before enabling it; no existing data/table is rewritten.

```powershell
# From apps/backend:
rtk proxy py -m app.modules.jobs.worker --init
# Set JOB_EXECUTION_MODE=worker in API environment, then run separately:
rtk proxy py -m app.modules.jobs.worker
```

New `/api/pipeline/jobs` requests then enqueue. Workers process one job at a time, claim by compare-and-set, heartbeat every 10 seconds on a 60-second lease, retry at most three times with backoff. `JOB_MAX_SECONDS` defaults to 3600. Lost lease/hard limit terminates the worker; deployment should supervise/restart it. `POST /api/jobs/{id}/retry` accepts only failed durable jobs and preserves completed-video checkpoints.

Resume is per completed video, not every GPU stage. Partial videos rerun with stable IDs/upserts. Delivery is at-least-once, not transactional or exactly-once; provider upserts must be idempotent. Legacy completed videos without the new marker retain old skip behavior. Partial job failure now reports FAILED and is retryable.

Sandbox tests cover lease ownership/backoff, real process-exit recovery, and video checkpoint resume. This pass did not enable the worker or run a provider-writing import on the current dataset. Test sustained concurrent search/ingest with real model/storage before deployment. Temporary upload endpoints and synchronous demo ingest are not automatically migrated to durable execution.

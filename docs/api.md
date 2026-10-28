# Jobs and persistent history

Phase 05 exposes the existing benchmark experiment over FastAPI. The
[Phase 06 dashboard](dashboard.md) consumes these contracts. Each job runs the complete **1/2/4/8-worker
comparison**, using the same `run_experiment`, image transformations, timing,
provenance and statistics as `tachyron-benchmark run`. This preserves the merged
core instead of introducing the older blueprint's separate single-worker API.

## Configure and run

From the repository root, register explicit dataset roots with operator-owned
environment variables. Use the tiny generated dataset in
[processing](processing.md#reproducible-example), or your own PNG/JPEG dataset.
Clients supply identifiers, never input/output paths.

```powershell
uv sync --locked --project apps/server --extra api
$env:TACHYRON_DATASETS = '{"example":"datasets/example"}'
$env:TACHYRON_DATA_DIR = 'results/api'
uv run --locked --project apps/server uvicorn tachyron.api:app --host 127.0.0.1 --port 8000
```

The default history directory is `results/api`; an unset dataset registry is
empty. At most 32 identifiers matching `[a-z0-9][a-z0-9-]{0,63}` are accepted.
Each registered root is discovered once at startup, using existing file, byte,
entry and depth limits. Discovery returns manifest hash, file count and compressed
bytes; unavailable roots have `dataset_unavailable` and null metadata. Discovery
does not claim that all files decode successfully. Existing per-image safeguards
and hash revalidation still apply during execution. Restart to refresh manifests.
Output storage and registered datasets must not overlap. No roots are returned
over HTTP. Keep the app on loopback: this local MVP has no accounts or authentication,
and it does not enable CORS. Network hosting requires a separate security design.

Interactive schemas are served at `/docs`, with machine-readable OpenAPI at
`/openapi.json`. The typed request forbids extra fields and coerced integer/boolean
values. Example submission, explicitly labeled as a tiny functional test:

```powershell
$body = '{"dataset_id":"example","width":4,"height":3,"tiny":true,"repetitions":1}'
$job = Invoke-RestMethod http://127.0.0.1:8000/api/jobs -Method Post -ContentType 'application/json' -Headers @{'Idempotency-Key'='example-1'} -Body $body
Invoke-RestMethod "http://127.0.0.1:8000/api/jobs/$($job.id)"
Invoke-RestMethod "http://127.0.0.1:8000/api/jobs/$($job.id)/metrics"
```

The last request returns `409` until the job completes successfully. A tiny test
is not representative performance evidence. Omit `tiny` and `repetitions` for
the normal one warm-up plus five measured repetitions per configuration.

## HTTP contract

| Endpoint                     | Behavior                                                  |
| ---------------------------- | --------------------------------------------------------- |
| `GET /health`                | Lightweight process liveness, `{"status":"ok"}`           |
| `GET /api/datasets`          | Frozen allowlisted workload metadata and discovery errors |
| `POST /api/jobs`             | Durable bounded admission, `202`, job and `Location`      |
| `GET /api/jobs/{id}`         | State, configuration, timestamps and batch accounting     |
| `POST /api/jobs/{id}/cancel` | Cooperative request, `202`, current job snapshot          |
| `GET /api/jobs/{id}/metrics` | Complete successful evidence/statistics; otherwise `409`  |
| `GET /api/benchmarks`        | Terminal jobs and available summaries, newest first       |
| `GET /api/benchmarks/{id}`   | Terminal job and raw result, including partial evidence   |

History accepts `limit` (1–50, default 20) and `offset` (0–100000, default 0).
Invalid requests return `422`; unknown identifiers return `404`. Error bodies are
`{"detail":"stable_code"}` without reflected input, exception messages or local
paths. `429 queue_full` and `503` storage/coordinator failures include
`Retry-After: 1`. `/health` remains liveness even if job coordination is unavailable.
Malformed stored model documents return `503 history_unavailable` rather than
serving invented or inconsistent results.

Requests select only `resize-grayscale-v1`, width/height 1–4096, repetitions 5–20
(1–20 with explicit `tiny: true`), seed 0–2³²−1, and sampling interval 0.01–10 s.
The existing aggregate output-pixel limit is also enforced before admission.

An optional `Idempotency-Key` contains 1–64 ASCII letters, digits, dots, underscores
or hyphens. The same key and normalized request return the original job, including
after restart or completion. Reusing a key for a different request returns
`409 idempotency_conflict`. Keys have no automatic expiry. Without a key each POST
is a new experiment; retrying after a lost response can submit another job.

## Scheduling, cancellation and measurements

One consumer executes one benchmark at a time. The default waiting queue holds
**two** jobs in addition to the active job. Programmatic `Settings.queue_capacity`
may select 1–16 waiting jobs; there is no unbounded executor submission queue.
Admission is serialized, queued in memory and committed to SQLite before `202`.
Failed persistence rolls back the in-memory admission. No worker pool is created
at module import or while a job waits.

The consumer is a lifespan-owned thread; CPU-intensive image work uses the existing
bounded spawn process pool. Status/history routes run in FastAPI's thread pool;
the liveness route stays asynchronous. Startup discovery completes before serving.
Batch completion notifications and SQLite writes occur **outside** the measured
batch interval. API traffic and SQLite activity can still affect machine load and
subsequent cache conditions. Use the CLI with controlled background load for formal
experiments. See [benchmark methodology](benchmarking.md) for all unchanged timing,
CPU/RSS, throughput, scaling, numerical and missing-measurement policies.

Transitions are `QUEUED -> RUNNING -> COMPLETED | FAILED | CANCELLED`, with direct
`QUEUED -> CANCELLED` when work has not started. Cancel is idempotent for terminal
jobs. Running cancellation first records `cancel_requested: true`; it does **not**
claim completion until the current pool batch drains. Cancellation is checked before
the next batch. A failed batch remains `FAILED`; completing the entire experiment
before the next cancellation boundary remains `COMPLETED`.

`completed_batches/total_batches` is progress at batch boundaries. Image counters
sum known attempts **including warm-ups and repeated images**, not distinct images.
`unknown_batches` counts retained attempts whose counts could not be established.
These counters never imply a partial comparison is scientifically valid. A crash
can lose the in-flight batch's counts; `server_interrupted` explicitly marks this
uncertainty. Successful metrics require a fully completed experiment. Failed or
cancelled history may contain raw evidence with incomplete/null summaries; absent
evidence is null. Speedup uses the existing ratio of means against the one-worker
pool, and efficiency is expressed as a percentage. Actual worker utilization stays
null where the existing sampler cannot establish it.

## Storage, restart and autoreload

`history.sqlite3` stores requests, manifest identity, state, timestamps, committed
progress and terminal raw results/statistics. Job state and terminal result commit
in one transaction. Stored summaries are checked against raw evidence on read.
Each job exclusively claims an internally generated UUID directory below history.
New core artifacts live in `<history>/<job-id>/experiment/`; the core retains its
independent experiment/run identifiers and JSON/CSV artifacts. A pre-existing job
directory is rejected without importing or changing its contents. Failure recovery
reads only the current attempt's owned namespace and checks workload/configuration
identity before attaching evidence. Existing history and exports are not relocated.
History polling reads SQLite and does not depend on retained export files.

Schema v1 is a deterministic transactional upgrade from an empty v0 database,
tracked by SQLite `user_version`. Existing v1 data is preserved. Unknown/nonempty
v0 databases, incompatible schemas and future versions fail startup explicitly.
Future changes must add an ordered migration; deleting history or resetting its
version is not an upgrade strategy. No SQL migration framework or ORM is needed
for this first schema. Back up the directory while the server is stopped.

The store uses one serialized connection with
[SQLite exclusive locking](https://www.sqlite.org/pragma.html#pragma_locking_mode).
A second app using the same history fails startup before recovering live jobs.
This also prevents duplicate consumers under multiple server workers. Run one
Uvicorn worker. The existing account-wide benchmark lock additionally rejects
overlap with cooperating CLI experiments or APIs using another history directory.
External SQLite readers must wait until shutdown; use the history API while running.

FastAPI lifespan owns initialization and cleanup, following the
[lifespan contract](https://fastapi.tiangolo.com/advanced/events/). Graceful shutdown
stops admission, cancels waiting jobs, requests active cancellation, drains the
current pool, joins the consumer and closes SQLite. Shutdown task cancellation
waits for cleanup. Startup failures use the same cleanup path, including errors or
interruptions after the real consumer starts. Main-thread startup defers Ctrl+C
through thread registration. Development `--reload` uses the same lifecycle; module imports
create neither SQLite connections nor background workers. Allow shutdown to finish.
Native work has no new hard deadline or instantaneous termination guarantee.

On abrupt owner death, OS database locks are released. Next startup changes all
stored `QUEUED`/`RUNNING` jobs to `FAILED server_interrupted`, retaining their last
committed progress. It **never requeues or reruns** interrupted work and never
promotes leftover artifacts to a successful result. Completed history survives
restart. Retained files are not deleted implicitly. An abrupt OS/process kill can
leave child/native work briefly alive; stop it or let it finish before collecting
another controlled benchmark. Graceful reload/shutdown is the supported cleanup path.

## Decisions and validation

- **IMPLEMENT:** bounded local coordination, typed contracts, durable acceptance,
  idempotent retries, cooperative cancellation, versioned SQLite history and safe
  crash recovery.
- **KEEP:** existing processing validation, fixed comparison matrix, pool ownership,
  CLI behavior, metrics, exports and frontend. Optional core hooks run outside timing.
- **DEFER:** individual worker-selection jobs, dataset refresh while running,
  authentication/network deployment, cross-history aggregation, hard termination,
  retention deletion and any UI work.
- **REJECT:** Redis/Celery, generic scheduler/storage adapters, silent migration resets,
  arbitrary client filesystem paths, incomplete success metrics and quality-policy changes.

Tests use real tiny images for API/core agreement, repeated polling under a blocked
batch, saturation, retries, cancellation, failures, persistence, migrations, startup
cleanup and shutdown cancellation. Separate marked integration tests use actual
1/2/4/8 spawn pools and an abrupt subprocess-owner crash. Existing CI already runs
all backend tests on Linux/Windows and Python 3.11/3.13; no gate changes are required.
Local commands and their actual results are reported with the Phase 05 delivery.

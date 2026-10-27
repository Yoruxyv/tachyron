# Phase 05 implementation and validation

Validated locally on Windows on 2026-10-27. Branch: `feat/api-and-history`.
Verified base: `c3b07060ad3720c79bcc09ebbbf556b54401690c`.
GitHub metadata and local ancestry confirm merged PRs #4 and #5. Local `main`
and `origin/main` match that checkpoint. The starting checkout was clean; no
existing work was discarded. The initial delivery and independent audit performed
no Git publication. The maintainer subsequently authorized fixing the two audited
blockers, then committing and pushing this feature branch. No PR, merge or Phase 06
work is authorized.

## Delivered requirements and decisions

All Phase 05 implementation requirements are covered: allowlisted dataset discovery;
typed job submission, polling, metrics and history; bounded durable acceptance and
overload responses; one background consumer using existing child process pools;
all five job states, cooperative cancellation and partial accounting; SQLite terminal
results, transactional initial migration, restart recovery and exclusive ownership;
sanitized errors; OpenAPI and integration/failure tests. UI work stops at the existing
health shell. See [the API contract](api.md) for operation and limitations.

**IMPLEMENT:** one in-memory bounded waiting queue plus SQLite history. This needs
no Redis, ORM, generic scheduler, service adapters or new infrastructure. Persistent
idempotency keys prevent duplicate submissions across retries and restarts.

**KEEP:** one job is the existing full worker comparison. The earlier supporting
blueprint's single-worker job shape and directory layout do not override the merged
core. Processing safeguards, scientific formulas, provenance, CLI arguments, export
schemas and pool lifecycle remain intact. Pydantic was already locked/installed;
it is now declared directly in the optional API dependency set. No package version,
quality rule, threshold, workflow protection or frontend application code changed.

**DEFER:** single-worker job selection, dynamic dataset refresh, hard termination,
retention deletion, network authentication and UI. These do not expand Phase 05.
**REJECT:** arbitrary client paths, fabricated incomplete metrics, silent schema
resets, automatic crash replay and speculative distributed infrastructure.

## Changed-file responsibilities

| Files                                                                        | Responsibility                                                                              |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `apps/server/tachyron/api.py`                                                | App factory, explicit lifespan ownership, health and safe exception transport               |
| `apps/server/tachyron/jobs/__init__.py`                                      | Job package boundary                                                                        |
| `apps/server/tachyron/jobs/models.py`                                        | Typed bounded requests, job/history/result contracts, raw-evidence validation               |
| `apps/server/tachyron/jobs/routes.py`                                        | Dataset/job/cancellation/metrics/history endpoints and OpenAPI                              |
| `apps/server/tachyron/jobs/coordinator.py`                                   | Allowlist settings/discovery, bounded admission, single consumer, cancellation and progress |
| `apps/server/tachyron/persistence.py`                                        | SQLite v1 migration, exclusive owner, atomic state/results and restart history              |
| `apps/server/tachyron/benchmarking/runner.py`                                | Optional between-batch cancellation and outside-timing progress hooks                       |
| `apps/server/pyproject.toml`, `apps/server/uv.lock`                          | Direct optional API Pydantic dependency; existing versions preserved                        |
| `apps/server/tests/test_api_jobs.py`                                         | API/core agreement, overload, concurrent retries, failures, real spawn and integrity        |
| `apps/server/tests/test_job_history.py`                                      | Migration, restart/crash, storage failure, discovery and lifecycle cleanup                  |
| `apps/server/tests/test_benchmark_controls.py`                               | Cancellation boundaries, failure precedence and observer failure                            |
| `apps/server/tests/benchmark_helpers.py`, `apps/server/tests/job_helpers.py` | Shared focused fixtures, sequential pool substitute and bounded API polling                 |
| `apps/server/tests/test_health.py`                                           | Keep the unknown-route health regression after jobs acquire a real route                    |
| `README.md`, `docs/api.md`, this report                                      | Setup, contracts, reconciliation, decisions and actual validation evidence                  |

## Invariants and independent review

- Admission commits before `202`; a failed save removes its waiting entry/signal.
  Running-state publication precedes dequeuing, so a storage failure cannot lose
  ownership of an admitted job. Memory holds only queued/active job signals.
- Exactly one consumer owns a history database. SQLite commits state and terminal
  evidence together. Unknown migrations fail without deleting data. Recovery fails
  unfinished jobs, retains committed progress, and never starts them again.
- Processing remains outside the event loop. Cancellation waits for the active
  batch; image failures take precedence, and already-complete comparisons stay
  complete. Partial evidence cannot produce successful metrics.
- Batch progress and SQLite writes are outside batch timing. Known image counts
  include warm-ups/repetitions; missing counts and actual utilization are not invented.
- Dataset identifiers, UUIDs, fixed operations, resource limits, generated output
  directories and parameterized SQL preserve trust/namespace boundaries. Errors
  do not reflect private paths or request values. Stored statistics are recomputed
  for validation against raw evidence. New jobs exclusively create a private parent
  before core execution; failure recovery cannot import a pre-existing directory's
  results and validates workload/configuration identity.

The separate final review revisited cancellation/terminal races, queue/store commit
ordering, startup failures, shutdown notification/join ordering, owner exclusion,
schema rejection, corrupted history, lost responses and interrupted comparisons.
It inspected the complete tracked diff and all new files against `main`.

**FIX:** durable-admission rollback; dequeue only after RUNNING is committed;
shutdown wakeup/signalling before fallible writes; unavailable-consumer cancellation;
stored-metric consistency; dataset/storage overlap before writes; explicit connection
closure in tests; shared test helpers without imports from other test modules.

**KEEP:** the single coordinator's cohesive state/ownership workflow, short serialized
SQLite transactions, direct core calls and the existing timing/sampler exception
scaffolding. Further splitting would displace lifecycle reasoning across helpers.
The core's runtime missing-batch guard remains despite its annotated return type.
Method size is a review signal, not justification to restructure verified timing code.
**DEFER:** new scheduler/storage layers and decomposition solely for a CodeScene score.
Actual CodeScene findings are unavailable for this unpublished change; no score or
provider gate result is claimed.

## Executed validation

The following table records the initial implementation validation; the approved
post-audit repairs and their newer results appear below. Commands ran from the
repository root. Python versions were 3.13.9 and 3.11.15;
Node was 24.18.0 and npm 11.6.4. Windows filesystem/process tests required normal
permissions because the sandbox blocks existing hard-link publication.

| Command                                                                                                                                                        | Actual result                                                                                        |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `.\apps\server\.venv\Scripts\python.exe -B -m pytest apps/server/tests -q -rs --tb=short -p no:cacheprovider`                                                  | **369 passed, 1 skipped**, final run 62.46 s                                                         |
| `.\.cache\py311\Scripts\python.exe -B -m pytest apps/server/tests -q -rs --tb=short -p no:cacheprovider`                                                       | **369 passed, 1 skipped**, 66.46 s                                                                   |
| `.\apps\server\.venv\Scripts\ruff.exe check .`                                                                                                                 | Passed                                                                                               |
| `.\apps\server\.venv\Scripts\ruff.exe format --check .`                                                                                                        | Passed, 53 Python files                                                                              |
| `.\apps\server\.venv\Scripts\pyright.exe --project pyrightconfig.json`                                                                                         | 0 errors, 0 warnings                                                                                 |
| `.\apps\server\.venv\Scripts\pyright.exe --project pyrightconfig.json --pythonplatform Linux`                                                                  | 0 errors, 0 warnings; static analysis, not Linux execution                                           |
| `.\apps\server\.venv\Scripts\python.exe -B scripts/check_repository.py`                                                                                        | Passed                                                                                               |
| `npm --prefix apps/web run format:check`                                                                                                                       | Passed                                                                                               |
| `npm --prefix apps/web run lint`                                                                                                                               | Passed                                                                                               |
| `npm --prefix apps/web run typecheck`                                                                                                                          | Passed                                                                                               |
| `npm --prefix apps/web run test:coverage`                                                                                                                      | **15 passed**, 3 files; statements 83.33%, branches 72.22%, functions 100%, lines 87.5%              |
| `npm --prefix apps/web run build`                                                                                                                              | Passed                                                                                               |
| `npm --prefix apps/web run bundle:check`                                                                                                                       | Passed; JS 221917 raw bytes / 68855 gzip bytes                                                       |
| `.\.cache\lychee\lychee-x86_64-pc-windows-msvc\lychee.exe --offline --include-fragments=anchor-only README.md 'docs/**/*.md'`                                  | Passed; 21 OK, 0 errors, 17 excluded by existing rules                                               |
| `.\.cache\actionlint\actionlint.exe .github/workflows/quality.yml .github/workflows/links.yml .github/workflows/security.yml .github/workflows/pr-labeler.yml` | Passed                                                                                               |
| `uv sync --locked --cache-dir .cache/uv --project apps/server --extra dev`                                                                                     | Passed; 31 packages resolved, only local package rebuilt                                             |
| `uv lock --check --offline --cache-dir .cache/uv --project apps/server --python apps/server/.venv/Scripts/python.exe`                                          | Passed                                                                                               |
| `uv build --cache-dir .cache/uv --project apps/server --out-dir .cache/phase05-dist`                                                                           | Source distribution and wheel built; wheel matches all 23 production Python files and excludes tests |
| `git diff --check main`                                                                                                                                        | Passed                                                                                               |

Both full backend runs include all **22 marked spawn/subprocess tests**, including
the new API comparison and abrupt-owner-crash recovery tests. The single skip is
the existing OS symlink-creation permission case at `test_processing.py:400`.
The initial Phase 05 files added 46 meaningful test cases; the approved repairs add
six more below. No existing assertions were weakened.

Additional local probes (ignored `.cache/` artifacts, not production features):

- `python -B .cache/phase05-core-parity.py`: compared the actual main runner with
  the changed runner under controlled environment, identifiers, clocks and sampler.
  Eight scenarios (tiny/production success, image/pool/missing-batch/cleanup/provenance
  failure) have matching outcomes/errors and **149 byte-identical output files**.
- `uv run --locked --isolated --cache-dir .cache/uv --project apps/server --no-default-groups python -I -c "import importlib.util; import tachyron.cli; import tachyron.processing; import tachyron.workers; import tachyron.benchmark_cli; assert importlib.util.find_spec('fastapi') is None; assert importlib.util.find_spec('pydantic') is None; print('Base CLI imports without FastAPI or Pydantic')"`: passed.
- Real loopback Uvicorn HTTP completed an eight-batch spawn comparison while health
  and status polling remained responsive. Success metrics remained unavailable until
  completion. Native autoreload detection occurred but replacement startup stalled
  in this noninteractive Windows launcher. A separate health-only FastAPI control
  reproduced the same stall, without any Phase 05 coordinator. All probe-owned
  processes were cleaned up. This is **not** reported as a passed native reload test.

Initial failures were diagnosed rather than suppressed: sandbox hard-link/temporary
rename restrictions required normal-permission reruns; an initial baseline's
repository-local pytest temp root invalidated its non-Git-directory test, so final
suites used normal temp directories; test SQLite context managers were corrected
to close connections explicitly after ResourceWarnings. No warnings, gates or
assertions were disabled.

## Approved post-audit blocker repairs

The independent read-only audit withdrew the initial READY assessment after
reproducing two defects. The maintainer authorized fixing those blockers and
committing/pushing the feature branch. Both are now fixed:

1. **Output evidence ownership:** each new job must exclusively create its UUID
   parent before calling the core at `<history>/<job-id>/experiment/`. A colliding
   parent remains untouched and yields no imported result or image counts. Recovery
   checks manifest identity, image count, input bytes, transform and benchmark
   configuration. Existing SQLite history and exports are not moved or reset.
2. **Partial startup cleanup:** startup errors invoke the same stop/notify/join/close
   path as shutdown. Never-started threads are not joined; real started consumers
   are joined before SQLite closes. The existing startup-interrupt deferral protects
   main-thread registration, and original startup exceptions still propagate.

New regressions cover unrelated pre-existing exports, unrelated evidence inside an
owned namespace, and RuntimeError/KeyboardInterrupt before and after real thread
startup. Before implementation, all five initial regression cases failed against
the audited code. After the fixes, the affected API/history/control suite passed
**52 tests**. The startup tests also confirm exception identity and released SQLite
ownership, and their fallback teardown prevents test failures from leaking threads.

Final checks after repair:

- The same full-suite commands above: Python **3.13.9: 375 passed, 1 skipped**
  (60.37 s); Python **3.11.15: 375 passed, 1 skipped** (65.62 s). Both include the
  22 marked spawn/subprocess cases; the skip remains the OS symlink-permission case.
- Ruff lint/format and Pyright for Windows/Linux targets passed. Prettier,
  repository text/whitespace, offline links (21 OK, 0 errors, 17 existing exclusions),
  actionlint and diff checks passed.
- Locked metadata validation and rebuilt source distribution/wheel passed. The
  wheel again matches all 23 production Python files and excludes tests.
- The full frontend application checks were not repeated: frontend source and
  dependencies are unchanged from the passing initial validation above. Repository
  formatting was rerun because documentation changed.

The final review retained the single owner, explicit queue transactions and shared
benchmark core. Only coordinator ownership/lifecycle, their regressions, the API
artifact-path assertion and related documentation change in this repair. The
non-blocking audit findings remain outside this approved repair: SQLite exception
types still reach transport/coordinator, and partial export-publication failure
needs clearer contract/test coverage. No database adapters or quality-policy changes
were introduced. Native autoreload remains unverified as described above.

## Limits and readiness

**READY for human review**, with no confirmed unresolved in-scope code defects.
This is not a claim that every possible bug has been eliminated or hosted CI passed.

Native autoreload remains unverified in this Windows runner; lifecycle, shutdown
cancellation, owner exclusion, native pool cleanup and restart persistence are
tested independently. Actual Linux execution, hosted CI/CodeQL/secret scanning,
CodeScene provider analysis and external-link checks were not run. Only the approved
feature-branch commit/push is authorized; no PR, merge or Phase 06 work is included.
Browser E2E and load tests belong to later phases and are not added.

The API is local and unauthenticated, runs one active comparison, and uses cooperative
batch-boundary cancellation without hard native deadlines. Abrupt process death can
lose in-flight counts or leave native children briefly alive; recovery never calls
that work complete. Tiny tests establish functionality, not representative scaling
or performance claims. No Phase 05 implementation requirement was intentionally
omitted; these validation and operational limits remain explicit.

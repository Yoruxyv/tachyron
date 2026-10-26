# Tachyron Backend Agent Instructions

These instructions apply to every file under `apps/server/`. Follow any repository-root `AGENTS.md` as well. When instructions conflict, do not silently choose a weaker standard: identify the conflict and request a decision before changing policy.

## Mission and boundaries

Tachyron is a **single-host parallel image-processing benchmark**, developed as an academically defensible simulation of worker-based distributed processing. It is **not** a multi-node, fault-tolerant cluster. Build an accurate, reproducible benchmark of image workloads executed with configurable worker counts (initially 1, 2, 4, and 8), exposed through a CLI and optionally FastAPI. Support the dashboard, but keep all authoritative benchmarking logic on the backend.

The product's central question is whether, when, and why adding workers improves throughput and execution time relative to resource usage. An attractive API is never a substitute for trustworthy measurements.

## Engineering decision protocol

1. Inspect relevant code, tests, configuration, and the active implementation phase before editing.
2. Explain the problem and compare reasonable options; assess data ownership, coupling, failure modes, Windows compatibility, and future divergence.
3. **KEEP / DEFER / REJECT** are valid decisions. Do not invent work just because an instruction mentions a possible abstraction or technology.
4. Make the smallest coherent change that satisfies an accepted requirement. Avoid framework-heavy solutions, speculative adapters, and unrelated refactors.
5. Preserve behavior unless the task explicitly requests a change; test important normal and failure paths.
6. Never silently modify tool configurations, suppress failing checks, edit generated results, or claim a test was run when it was not.
7. Do not commit, push, merge, or publish without the user's explicit instruction.

## Architecture and responsibilities

- `tachyron/processing/`: pure or tightly scoped image transformations, file validation, output lifecycle. Pillow belongs here.
- `tachyron/workers/`: process-pool lifecycle and task submission. Worker callables must be top-level and picklable, compatible with Windows `spawn`.
- `tachyron/benchmarking/`: experiment orchestration, fixed workload manifests, timing, repeat runs, statistics, and provenance.
- `tachyron/monitoring/`: process-tree CPU/memory sampling, sample metadata, and overhead boundaries.
- `tachyron/persistence/`: durable run history and SQLite schema evolution, if storage is introduced.
- `tachyron/api/`: FastAPI request/response contracts and validation; do not replicate benchmark logic here.
- `tachyron/cli.py`: CLI entry point calling the same benchmark core as the API.

This is a **suggested responsibility map**, not a directive to generate empty packages. Adapt to the actual repository and document departures.

Keep dependencies directed inward: routes and CLI orchestrate services; services use focused interfaces; processing code does not import FastAPI, SQLite, or frontend concerns. Prefer direct dependencies over unnecessary dependency-injection layers. Never construct a process pool at module import time.

## Python quality: strict by default

- Target Python **3.11+**; the configured Ruff target is `py311`.
- Use `uv` and checked-in `uv.lock` once initialized. Keep dependency sets minimal and locked.
- Treat the repository-root `ruff.toml` as the **single source of truth** for Ruff. Adopt the supplied strict policy: `required-version = ">=0.16.3,<0.17"`, `cache-dir = ".cache/ruff"`, `extend-exclude = [".cache"]`, `line-length = 88`, `target-version = "py311"`, `preview = true`, `explicit-preview-rules = true`, and broad ignore **only** `E501`.
- Preserve the requested Ruff rule groups: `A, ARG, ASYNC, B, BLE, C4, C90, CPY001, DTZ, E, F, G, I, LOG, N, PERF, PGH, PIE, PLC, PLE, PLW, PTH, PT, RET, RSE, RUF, RUF069, S, SIM, T10, T20, TRY004, TRY201, TRY203, TRY400, TRY401, UP, W`.
- Preserve copyright author `Hans Valerie`, McCabe `max-complexity = 15`, spaces, automatic line endings, and double quotes. Adjust first-party import names to **actual Tachyron packages**, not Semantix modules.
- Never copy Semantix-only per-file ignores. Tests may need narrowly reviewed `S101`, `ARG001`, `ARG002`, or `ARG005` exceptions. Prefer a code fix over suppression; justify each additional exception.
- Use complete type annotations for public functions, return values, collections, and domain contracts. Prefer small immutable dataclasses / typed models where helpful.
- Follow **Google-style docstrings** for nontrivial public modules, functions, and classes; document `Args`, `Returns`, `Raises` when relevant. Do not add meaningless boilerplate to obvious one-line helpers.
- Log operational events with `logging`, not `print` in backend service code. Print is acceptable for purposeful CLI output, subject to reviewed lint policy.
- Use structured errors and preserve exception causes. Never catch broadly just to return success or fabricated metrics.

### Pyright

Use one active `pyrightconfig.json` (root or backend) as the type-checking SSOT, with the real source directories in `include`/`extraPaths`. Preserve `typeCheckingMode: "standard"` and set all the following to `"error"`:

`reportDeprecated`, `reportUnreachable`, `reportMissingTypeArgument`, `reportMissingParameterType`, `reportUnknownParameterType`, `reportUnknownArgumentType`, `reportUnknownMemberType`, `reportUnknownVariableType`, `reportImplicitOverride`, `reportUnnecessaryTypeIgnoreComment`.

Use Tachyron's actual `apps/server` paths; do not copy unrelated `packages/cache` or `packages/client` assumptions. Avoid blanket `type: ignore`, `Any`, or file exclusions to bypass typing. Narrow casts/ignores require a technical explanation.

## Benchmark correctness: non-negotiable

- Compare worker configurations with **identical ordered dataset, transformations, output format, environment, and workload**.
- Record dataset provenance (manifest/hash, image count, resolution/byte-size summary), code/config version, hardware, Python version, run identifier, timestamps, and requested/actual workers.
- Execute **one warm-up** plus **five measured repetitions** per worker setting unless a documented experiment explicitly overrides this. Record every repetition; calculate aggregates from raw runs, never from rounded or invented values.
- Measure wall-clock elapsed time with `time.perf_counter()`. Define the timing boundaries and include consistent input decode/output writes across worker counts.
- Compute processing throughput as **successfully processed images / elapsed seconds**. Explicitly report failures and incomplete work; do not mark a partial run as equivalent to a successful run.
- Compute speedup `T_1 / T_p` and efficiency `(T_1 / T_p) / p`, using consistent run aggregates for the same workload. An ideal line is theoretical, not observed data.
- CPU and RAM sampling must state **which processes** are included (ideally parent + worker descendants), sampling interval, normalization/denominator, aggregation method, and peak interpretation. A child-process exit must not silently corrupt measurements. Account for sampler cost.
- Differentiate processing throughput (images/s) from HTTP API throughput (requests/s); they are never interchangeable.
- Do not change algorithms, output fidelity, concurrency, or cache conditions between comparison runs. Where OS caches, disk I/O, heat, or CPU contention may affect results, disclose limitations rather than claiming proof of causation.
- Benchmark output must be traceable to real execution. Example/demo numbers are labeled simulated and never served as measured results.

## Concurrency, safety, and lifecycle

- Set upper bounds for dataset size, individual images, queue depth, active jobs, worker counts, and memory-related risks; reject excess work explicitly.
- Validate registered dataset identifiers, canonicalize paths, prevent traversal/symlink escape, and use safe output directories. Do not execute user-provided commands or arbitrarily access local file paths.
- Apply Pillow decompression-bomb safeguards, file-type validation, and explicit corrupted-image handling.
- For FastAPI, keep CPU-intensive work outside the event loop. A `202 Accepted` response means the job was **queued**, not completed. Supply honest status/progress and terminal failure states; define overload (e.g. `429`/`503`) and bounded queue semantics.
- Use explicit pool ownership, cleanup, shutdown, cancellation semantics, and exception propagation. Never claim already-running process tasks can always be cancelled instantly.
- Do not run competing benchmark workloads concurrently if they would contaminate comparisons, unless isolation is a tested design requirement.
- Treat SQLite schema changes as migrations; preserve user history and do not delete results implicitly.
- External services (Redis, Celery, Kubernetes, multi-host workers) are **out of scope** unless justified by an accepted phase and validated benefit.

## Testing and CI requirements

Add or update tests with each behavior change. At minimum cover:

- output dimensions, format, operation determinism, and corrupted/unsupported images;
- 1/2/4/8-worker operation, full task accounting, no duplicated/missing outputs;
- worker exceptions, failed jobs, queue limits, invalid inputs, cleanup, and Windows spawn behavior where testable;
- timing/math invariants, throughput, speedup, efficiency, zero-success/zero-time safeguards;
- job lifecycle, `202` semantics, status polling, incomplete-metrics responses, overload and persistence;
- concurrency and regression edge cases with deterministic, lightweight fixtures.

Use `pytest` and FastAPI test clients where appropriate. Keep tests independent of paid providers, network connectivity, and large private datasets. CI must run on Linux; include a Windows job for multiprocessing/spawn-sensitive behavior once the worker layer exists.

When configured, run from the appropriate directory:

```powershell
uv sync --locked --extra dev
uv run --locked ruff check ../..
uv run --locked ruff format --check ../..
uv run --locked pytest
```

Run Pyright using the chosen config location, e.g. `uv run --locked pyright -p ../../pyrightconfig.json` from `apps/server/` if that is the checked-in layout; verify the command rather than assuming it. Report any skipped or unavailable checks explicitly. CI must enforce the same commands; do not turn required gates into `continue-on-error`.

Lychee link checking and frontend E2E are repository-level gates and should not be duplicated as backend-only jobs unless needed. k6 is a separate performance suite; do not require flaky timing thresholds in normal unit-test CI.

## Definition of a completed change

Before declaring a phase complete, report: files changed, architectural decisions and deferred alternatives, user-visible behavior, tests run with outcomes, notable risks, documentation/configuration updates, and any unverified claims. Inspect the complete diff and check for secrets or generated data. Ask before moving to the next phase when the user has requested phase-by-phase delivery.

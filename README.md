# Tachyron

Reproducible parallel image-processing benchmarks on a single machine.

The Python package provides deterministic sequential and spawn-process batch
image processing through a minimal CLI. The web application remains an accessible
shell with a FastAPI liveness check. The benchmark CLI measures real pool scaling
and exports raw runs, sampled CPU/RSS, and comparison statistics. History and
dashboard charts are not implemented.

## Requirements

- Python 3.11+; CI targets 3.11 and 3.13 on Linux and Windows.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.11.32.
- Node.js 24.18.0 (`.node-version`) and npm 11.6.4.

Dependencies are locked in `apps/server/uv.lock` and `apps/web/package-lock.json`.
Use `uv sync --locked` and `npm ci`; updating locks is a deliberate maintenance task.

## Run locally

From the repository root in PowerShell (the same commands work in POSIX shells):

```powershell
uv sync --locked --project apps/server --extra dev
uv run --locked --project apps/server uvicorn tachyron.api:app --host 127.0.0.1 --port 8000
```

In a second terminal at the repository root:

```powershell
npm --prefix apps/web ci
npm --prefix apps/web run dev
```

Open [the application](http://127.0.0.1:5173) and select **Check connection**.
[GET /health](http://127.0.0.1:8000/health) returns `{"status":"ok"}`.
This reports process liveness only; it says nothing about processing readiness.

Both development servers bind to loopback. Vite proxies `/health` to the local
API, so no permissive CORS policy or browser environment secrets are needed.
Requests time out after five seconds; failures show a safe message and allow retry.
Stop either server with Ctrl+C.

For an API-only environment, install `--extra api` instead of `--extra dev`.
The base Python package depends only on Pillow and runs without FastAPI.

## Process images

From the repository root, choose a PNG/JPEG dataset and a **new** output directory
whose parent already exists:

```powershell
uv run --locked --project apps/server tachyron-process datasets/example --output results/example-1 --width 256 --height 256
```

Each image is oriented, composited onto white, resized with Lanczos, and converted
to grayscale PNG. A stable manifest and `run.json` preserve input identities,
output mappings, and real success/failure counts. Existing results are never
replaced. Exit codes are 0 for success, 1 for per-image failures, 2 for setup,
execution, or report errors, and 130 for interruption. There are no benchmark timings.

The command above remains sequential. Explicitly select a process pool with
`--workers 1`, `--workers 2`, `--workers 4`, or `--workers 8`:

```powershell
uv run --locked --project apps/server tachyron-process datasets/example --output results/parallel-2 --width 256 --height 256 --workers 2
```

See [parallel workers](docs/workers.md) for bounded scheduling, spawn compatibility,
failure/shutdown semantics, and the limits of cancellation. Worker counts are
explicit experimental inputs; selecting more than reported logical CPUs warns.

See [sequential processing](docs/processing.md) for a generated tiny dataset,
the exact image contract, safety limits, and Python usage.

## Reproducible benchmarks

Use the generated dataset and existing `results/` parent from the processing
example. A fresh experiment directory is required:

```powershell
uv run --locked --project apps/server tachyron-benchmark run datasets/example --output results/benchmark --width 4 --height 3
uv run --locked --project apps/server tachyron-benchmark compare results/benchmark/runs.json --output results/recomputed
```

The primary comparison uses 1/2/4/8 spawn workers, one warm-up and five measured
repetitions per configuration, seeded interleaved order, and consistent batch
timing including pool startup/shutdown and image I/O. JSON/CSV preserve raw runs
and summaries. Failed experiments never become complete scaling comparisons.
Use `--tiny` explicitly for a labeled one-repetition test; a tiny workload is not
representative performance evidence. See [benchmark methodology](docs/benchmarking.md)
for timing, baseline, CPU/RSS semantics, provenance, storage and cancellation limits.

## Quality checks

Run these from the repository root:

```powershell
uv sync --locked --project apps/server --extra dev
uv run --locked --project apps/server ruff check .
uv run --locked --project apps/server ruff format --check .
uv run --locked --project apps/server pyright --project pyrightconfig.json
uv run --locked --project apps/server pytest apps/server/tests -q
npm --prefix apps/web ci
npm --prefix apps/web run format:check
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run test:unit
npm --prefix apps/web run test:coverage
npm --prefix apps/web run build
npm --prefix apps/web run bundle:check
uv run --locked --project apps/server python scripts/check_repository.py
```

Ruff uses only the root `ruff.toml`; running at the root includes future `scripts/`
and other Python tooling. Pyright also scans the repository, including tests and
future scripts, with the configured error-level diagnostics. ESLint checks source,
tests, and configuration with type-aware rules. Warnings fail the lint gate.

The [CodeScene configuration](.codescene/code-health-rules.json) keeps `Large Method`
active at half its default weight only in `apps/server/tests/**`, where explicit
setup, execution, and assertions can form one readable test. Thresholds and all
other rules, including file length and cohesion, retain their defaults. Production
code receives no overrides. Test suites should still be organized by responsibility.

[CI](.github/workflows/quality.yml) runs genuine Python lint/format/type/tests and frontend
lint/type/tests/build checks on pull requests and pushes to `main`. Action revisions
and tool versions are pinned. The backend test matrix includes Linux and Windows.
Prettier, Git-aware text hygiene, offline documentation links, and workflow linting
also run in CI. External links run separately on a weekly schedule or manual trigger.
Real spawn integration runs in both OS/Python test matrices. Playwright E2E and
load testing remain deferred.

`npm run build` creates `apps/web/dist/`. A future deployment must serve `/health` from
the backend on the same origin; Vite's development proxy is not a production server.

## Ownership

- `apps/server/tachyron/`: reusable Python package; `api.py` owns HTTP transport only.
- `apps/server/tachyron/processing/`: manifest, image semantics, and sequential execution.
- `apps/server/tachyron/cli.py`: processing arguments, JSON reporting, and exit codes.
- `apps/server/tachyron/workers/`: bounded process-pool ownership and ordered aggregation.
- `apps/server/tachyron/benchmarking/`: schedule, observation, evidence and pure statistics.
- `apps/server/tachyron/benchmark_cli.py`: independent benchmark run/compare commands.
- `apps/web/src/api/`: browser HTTP contract validation and cancellation.
- `apps/web/src/App.tsx`: minimal application shell; no benchmark state or calculations.
- Root quality configuration and `.github/workflows/`: repository-wide gates.

See the [contributor quality guide](docs/quality.md) for formatter ownership,
repository text rules, local link checks, workflow validation, and bundle reporting.

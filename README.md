# Tachyron

Reproducible parallel image-processing benchmarks on a single machine.

The development foundation provides a FastAPI liveness endpoint and an
accessible React shell. Processing, worker pools, benchmark measurements, CLI,
history, and charts are not implemented yet. No benchmark results exist yet.

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
The base Python package has no runtime dependencies and imports without FastAPI.

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

[CI](.github/workflows/quality.yml) runs genuine Python lint/format/type/tests and frontend
lint/type/tests/build checks on pull requests and pushes to `main`. Action revisions
and tool versions are pinned. The backend test matrix includes Linux and Windows.
Prettier, Git-aware text hygiene, offline documentation links, and workflow linting
also run in CI. External links run separately on a weekly schedule or manual trigger.
Playwright E2E, worker integration, and load testing remain deferred.

`npm run build` creates `apps/web/dist/`. A future deployment must serve `/health` from
the backend on the same origin; Vite's development proxy is not a production server.

## Ownership

- `apps/server/tachyron/`: reusable Python package; `api.py` owns HTTP transport only.
- `apps/web/src/api/`: browser HTTP contract validation and cancellation.
- `apps/web/src/App.tsx`: minimal application shell; no benchmark state or calculations.
- Root quality configuration and `.github/workflows/`: repository-wide gates.

See the [contributor quality guide](docs/quality.md) for formatter ownership,
repository text rules, local link checks, workflow validation, and bundle reporting.

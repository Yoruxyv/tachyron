# Tachyron

Tachyron processes PNG and JPEG folders on one machine and helps you measure how
different process-pool sizes affect the same workload. Use the CLI for local
batches, the API to automate runs on registered datasets, or the dashboard
to launch work, watch observed worker activity, and compare saved measurements.

## Choose a workflow

| Goal                                                       | Workflow                                                   | What it produces                                                                                                                                       |
| ---------------------------------------------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Convert a folder with straightforward per-image reporting  | `tachyron-process`                                         | Grayscale PNGs and a batch report. Runs sequentially by default.                                                                                       |
| Finish a local batch with a known worker count             | **Manual** in Experiments or `tachyron-execute run`        | One execution with per-image receipts. The local CLI accepts up to 10,000 files, subject to resource limits.                                           |
| Let the current workload inform worker choice              | **Adaptive** in Experiments or `tachyron-execute adaptive` | A bounded experimental calibration followed by one full run when feasible. Insufficient evidence leads to an explicit fallback. Calibration adds time. |
| Compare worker counts under the same measurement procedure | **Benchmark** in Experiments or `tachyron-benchmark run`   | Repeated measurements, sampled CPU and memory, and portable evidence. The default matrix is 1/2/4/8.                                                   |

## Install and run locally

Use standard CPython 3.11–3.14 and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Continuous integration tests these Python versions on Linux and Windows.
The dashboard also uses Node.js 24.18.0 and npm 11.6.4.

Run a CLI-only installation from the repository root:

```powershell
uv sync --locked --project apps/server --no-dev
uv run --locked --project apps/server --no-dev tachyron-execute --help
```

The base Python package includes Pillow and psutil. For the dashboard or
HTTP integration, start the optional API:

```powershell
uv run --locked --project apps/server --no-dev --extra api uvicorn tachyron.api:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```powershell
npm --prefix apps/web ci
npm --prefix apps/web run dev
```

Open [Tachyron](http://127.0.0.1:5173) and choose **Experiments → Import folder**
to review and confirm a dataset. Alternatively, before starting the API, register
dataset IDs in the server environment. [Dashboard setup](docs/dashboard.md#run-locally)

Keep the API on loopback. It trusts the operator's OS account and has no
account authentication. Vite proxies `/api` and `/health` to the local API.
For native API calls, send `X-Tachyron-Request: 1` on mutations and
operational polling. [Local request rules and API examples](docs/api.md)

## Process a large local batch

The command below assumes `datasets/example` and the `results` parent already
exist. Choose a new output directory outside the input dataset. The
[generated processing example](docs/processing.md#reproducible-example) creates
a small input if you want to try the workflow first.

```powershell
uv run --locked --project apps/server tachyron-execute run datasets/example --output results/example-fixed4 --workers 4 --telemetry
uv run --locked --project apps/server tachyron-execute inspect results/example-fixed4
uv run --locked --project apps/server tachyron-execute inspect-telemetry results/example-fixed4
```

The transform applies EXIF orientation, composites transparency onto white,
resizes with Lanczos, and writes grayscale PNG. Operations account for
succeeded, failed, unknown, and unstarted inputs. An interrupted output
folder may contain acknowledged successes and unconfirmed work. Inspect
the receipts before deciding whether to start a fresh execution.
[Limits, exit codes, and recovery](docs/local-execution.md)

The 10,000-file limit applies to the operator-local execution path. Browser
registration and benchmark admission have separate limits.
[Dataset import limits](docs/dataset-imports.md#limits-and-image-acceptance)

## Compare and monitor

Run a benchmark on a dataset and an existing output parent:

```powershell
uv run --locked --project apps/server tachyron-benchmark run datasets/example --output results/benchmark
uv run --locked --project apps/server tachyron-benchmark compare results/benchmark/runs.json --output results/recomputed
```

Benchmarks time complete pool batches, including startup, image I/O and
shutdown. The default schedule uses one warm-up and five measured repetitions
per worker count. Use `--tiny` for a labeled functional check. When testing
custom counts, include `1` to measure the pool baseline used for speedup and
efficiency. [Benchmark methodology and export formats](docs/benchmarking.md)

In the dashboard, **View all worker results** opens the full table and
measurement provenance for a saved comparison. **Live Worker Monitor**
follows Manual and Adaptive operations and shows confirmed worker observations,
acknowledged progress and coverage gaps. A coverage gap means the event history
is incomplete. Use the execution receipt to inspect outcomes.
[Dashboard workflows](docs/dashboard.md) · [Telemetry API and interpretation](docs/operational-telemetry.md)

## Troubleshoot

- **API offline or port in use:** Check that the loopback server is running.
  `GET /health` and **API Tests → Check connection** check liveness. Resolve
  the port conflict before starting another instance. [Startup and recovery](docs/dashboard.md#troubleshooting)
- **Submission rejected:** Check the registered dataset, output limits, requested
  worker counts and whether another workload owns the coordinator or host lock.
  [API errors](docs/api.md#http-contract)
- **Adaptive fallback or slower work:** Read the selection reason and compare
  measured total time, including calibration, against a fixed run.
  [Adaptive policy and limitations](docs/adaptive-execution.md)
- **Cancelled or failed run:** Allow dispatched work to settle, then read
  the receipt. [Outcome categories](docs/local-execution.md#outcomes-and-interruption)

## Contribute

Use the [contributor quality guide](docs/quality.md) for the pinned tools,
local checks and browser tests. Start contributions on a focused feature, fix,
or docs branch. The pull request template asks for exact verification commands
and sanitized evidence. For a bug report, include
your platform, a reproducible workload description or safe fixture, the exact
command, and sanitized results. For performance changes, include the benchmark
context and raw evidence so reviewers can check the comparison.

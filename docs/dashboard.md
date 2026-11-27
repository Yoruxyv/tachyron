# Tachyron dashboard

Use the dashboard to register local datasets, run image-processing jobs, observe
worker activity, and compare saved benchmarks. The backend owns job status,
receipts and measurements.

## Run locally

From the repository root, start the loopback API:

```powershell
uv sync --locked --project apps/server --no-dev --extra api
uv run --locked --project apps/server --no-dev --extra api uvicorn tachyron.api:app --host 127.0.0.1 --port 8000
```

To register an existing directory instead of importing a folder from the browser,
set these variables in the API terminal before startup. The example relies on the
repository root as the current directory:

```powershell
$env:TACHYRON_DATASETS = '{"example":"datasets/example"}'
$env:TACHYRON_DATA_DIR = 'results/api'
```

In a second terminal, install the locked frontend dependencies and start Vite:

```powershell
npm --prefix apps/web ci
npm --prefix apps/web run dev
```

Open [the dashboard](http://127.0.0.1:5173). Vite proxies `/api` and `/health`
to the API. Set `TACHYRON_API_URL` in the Vite process only when using another
local API target. Serve those routes on the same origin when serving the built
frontend. The app assumes a trusted local operator. [API request boundary](api.md#local-request-boundary)

## Run a workload

1. Open **Experiments**. Choose a registered dataset or select **Import folder**.
   The import review lists selected paths and allows explicit exclusions of valid
   files. Remove unsupported entries from the folder before starting an upload.
   Wait for server validation and registration to finish. [Folder import limits and recovery](dataset-imports.md)
2. Choose the output dimensions and an execution mode.
   - **Benchmark** repeats the same dataset and transform across 1, 2, 4 and 8
     workers by default. **Benchmark settings → Custom worker counts** accepts
     up to ten distinct integers from 1 through 16, subject to server admission.
     Tiny functional tests are labeled separately. [Benchmark policy](benchmarking.md)
   - **Manual** processes the registered dataset once with 1, 2, 4 or 8 workers.
   - **Adaptive** profiles and evaluates bounded samples before processing the full
     dataset once. It can abstain and fall back to one worker. Compare total time,
     including calibration, against a fixed run. [Adaptive policy](adaptive-execution.md)
3. Start explicitly. An accepted job can still be queued. Manual and Adaptive
   runs attach automatically to **Live Worker Monitor**, which shows acknowledged
   progress and confirmed worker observations. Benchmark progress reports batches
   including warm-ups and repeated image attempts. [Operational telemetry](operational-telemetry.md#live-worker-monitor)
4. To stop, request cancellation from the corresponding job card or monitor.
   Dispatched work may still finish. Inspect the final outcome and counts before
   deciding whether to retry.

## Read a comparison

**Overview** shows selected measured KPIs and the elapsed-time and CPU charts.
**Compare** displays one selected saved benchmark with six metrics and a table.
**History** shows terminal benchmarks, paginated at 20 per page. Search and status
filters apply to the current page. Use the raw evidence link for the authoritative
run records and provenance.

**View all worker results** opens a saved comparison at
`#compare/workers?experiment=<saved-job-id>`. The page shows all configurations
with measured rounds, one selectable chart, a measured table, and the source
record. The elapsed-time summary chart pins the measured one-worker pool and the
selected count, then fills up to four slots by unrounded elapsed mean. Equal
means are disclosed. The inspection selector changes the displayed evidence.
It leaves the execution schedule intact.

| Metric                 | How to read it                                                                                              |
| ---------------------- | ----------------------------------------------------------------------------------------------------------- |
| Elapsed time           | Mean measured batch wall time in seconds, including pool startup and shutdown.                              |
| Throughput             | Mean of per-run completed images per second.                                                                |
| CPU                    | Observed process-tree percentage of one logical core. Values can exceed 100%.                               |
| RSS                    | Sampled process-tree resident memory. It can miss short peaks and count shared pages more than once.        |
| Speedup and efficiency | Ratios use the measured one-worker pool. When baseline evidence is unavailable, the ratios are unavailable. |

Warm-ups are excluded from benchmark statistics. Use repetition count, sample
standard deviation, range and resource coverage to interpret variability.
An ideal line is theoretical. Separate saved comparisons retain separate contexts.
[Measurement and export details](benchmarking.md#statistics-and-provenance)

## Troubleshooting

- **API offline:** Confirm the API terminal reached startup and
  [the health endpoint](http://127.0.0.1:8000/health) responds. The sidebar's
  **Troubleshoot** action provides the local startup command and a connection
  retry. Liveness alone leaves dataset and job availability unverified.
- **Port conflict:** A listener already owns the port. Use the running intended
  service or stop the instance you own before restarting. If using another local
  API port, point Vite at it. Browser requests also need an allowed origin.
- **Dataset unavailable or upload rejected:** Verify the registered path and
  restart the API to refresh its frozen manifest. For imports, inspect the review
  and processing messages and check format, count, and byte limits.
  [Dataset imports](dataset-imports.md#limits-and-image-acceptance)
- **Lost submission response:** Use **Retry same submission** for an uncertain
  benchmark. The dashboard retains the request and idempotency key in the current
  tab. For an uncertain operational submission, confirm its status before using
  **Allow a new submission**. [Operational submission recovery](operational-telemetry.md#live-worker-monitor)
- **Worker observations missing:** Inspect the coverage note. Slow or interrupted
  polling can miss part of the retained event window. After snapshots expire or
  the API restarts, inspect the local operational receipts.
  [Snapshot and receipt recovery](operational-telemetry.md#inspect-persisted-receipts)

## Frontend development

The app uses React, TypeScript, Vite, Tailwind and Chart.js. `apps/web/src/app/`
composes the workspace. Features own their request lifecycles and use runtime
decoders in `src/api/`. Chart components consume server evidence and release
their Chart.js instances on unmount. Results remain available in an accessible
table if chart rendering fails. The frontend keeps the registered dataset and
worker-inspection selection across views. Benchmark and operational state remain
independent.

Run the frontend quality checks and browser suite in the
[contributor guide](quality.md#local-commands). Playwright uses a production
preview on 4174 and a real isolated API on 8001. Its functional datasets and
fault injection belong to the test server. Tests keep synthetic contract fixtures
separate from measured evidence. Open the local HTML report from `apps/web/`:

```powershell
npx playwright show-report
```

Lighthouse uses a fresh API on 8001 and a production preview on 4173. Run it
from `apps/web/` after installing Chromium. It measures the empty application
with the API online. Scores are advisory. The runner writes a unique report
directory for each invocation. [Lighthouse workflow](../.github/workflows/lighthouse.yml)

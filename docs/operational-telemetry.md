# Operational jobs and live telemetry (Stage C2)

The local API admits explicit image-processing runs against dataset IDs from
[`GET /api/datasets`](dashboard.md). Use `manual` for a fixed worker cap or
`adaptive` to opt into Stage B's experimental calibrated selection and fallback.
The work uses the registered manifest and its resource limits. Scientific
benchmark jobs continue to use `/api/jobs` and `/api/benchmarks`.

## Requests

| Method | Path                                | Result                                                           |
| ------ | ----------------------------------- | ---------------------------------------------------------------- |
| POST   | `/api/operations`                   | 202 with the queued snapshot and a `Location` header             |
| GET    | `/api/operations/{run_id}`          | 200 with current state and the retained event window             |
| GET    | `/api/operations/{run_id}?after=42` | 200 with current state and the retained events after sequence 42 |
| POST   | `/api/operations/{run_id}/cancel`   | 202 with the current snapshot and cooperative cancellation state |

All calls require the server's allowed Host and `X-Tachyron-Request: 1`.
When supplied, Origin must be allowlisted. Cross-site browser requests are
rejected. Successful responses use `Cache-Control: no-store`. This is the
existing single-operator local trust boundary. Keep the server on loopback
or behind the configured trusted local proxy. Run IDs scope events and lookup.

Send a strict JSON body with `dataset_id`, `mode`, optional `width` and
`height` (each 1–4096, default 256), and `workers` for manual mode. Workers must
be 1, 2, 4, or 8. For adaptive mode, omit `workers` to use the Stage B default
policy. The server derives the output namespace and executes the existing
grayscale PNG transform.

```powershell
$headers = @{ 'X-Tachyron-Request' = '1' }
$api = 'http://127.0.0.1:8000'
$body = @{ dataset_id = 'tiny'; mode = 'manual'; workers = 2; width = 64; height = 64 } | ConvertTo-Json
$run = Invoke-RestMethod -Method Post -Uri "$api/api/operations" -Headers $headers -ContentType 'application/json' -Body $body
$view = Invoke-RestMethod -Uri "$api/api/operations/$($run.run_id)?after=$($run.snapshot_sequence)" -Headers $headers
```

A concurrent operation, queued or running benchmark, or import reservation
returns `409 workload_busy`. Admission is shared with benchmarks and imports in
the same lifespan coordinator. Across processes, the existing cooperative host
lock may reject an already accepted run, producing a terminal `FAILED` snapshot
with `workload_busy`. An unknown or expired run ID returns
`404 operation_unavailable`. An active owner that becomes unavailable can return
`503 operation_unavailable`. Invalid inputs return sanitized error codes.

## Reading snapshots

An abbreviated running manual response:

```json
{
  "schema_version": 1,
  "run_id": "fc146111-abcf-4320-93bf-7ca35e2f4ab0",
  "dataset_id": "tiny",
  "mode": "manual",
  "requested_workers": 2,
  "state": "RUNNING",
  "phase": "execution",
  "cancel_requested": false,
  "progress": {
    "total": 3,
    "pending": 2,
    "succeeded": 1,
    "failed": 0,
    "unknown": 0,
    "not_started": 0
  },
  "snapshot_sequence": 12,
  "first_available_sequence": 1,
  "resync_required": false,
  "coverage": "best_effort",
  "events": []
}
```

- `state` is `QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`, or
  `UNCONFIRMED`. `phase` records the latest reached stage even after completion.
  Queued cancellation proves the inputs unstarted. Active cancellation requests
  stop refill and drain dispatched work. Receipt acknowledgements can arrive after
  cancellation is requested. Unconfirmed shutdown closes admission until restart.
- `progress` counts final processing only. Before terminal reconciliation,
  `pending` is the number without an acknowledged image receipt or final
  classification. A terminal snapshot reconciles counts to the execution
  receipt when available. Calibration has a separate `pool.progress`.
- `pool` describes the latest admitted pool, including its execution ID,
  manifest digest, requested worker cap, `trial` scope (if any), and observed
  workers. The observed list can be shorter than the cap. `READY` describes
  an initializer observation. It does not describe worker idleness.
- Adaptive mode reports `calibration_trials_finished` and `selection`, including
  measured selection or planned fallback, its reason, and the number of profiled
  inputs. `final_execution_id` identifies the full processing receipt when one
  was admitted. A trial event carries a distinct `execution_id` and `trial_index`.
- Events use a sequence scoped to `run_id`. Store these values together. A
  successful poll always contains current counts and worker state, even when
  `events` is empty. If a cursor predates the retained window or is ahead of the
  current sequence, `resync_required` is true and the retained tail is returned.
  Reconcile with the snapshot and use its `snapshot_sequence` as the next cursor.
- The shared transport holds at most 512 observations for the active pool. Each
  run retains 256 sequenced events and the coordinator retains at most eight run
  snapshots. Transport drops, unquantified losses and unavailability are
  separate from history evictions. A zero drop bound leaves delivery unverified.
  Parent receive time orders observations within one run. Child monotonic times
  describe within-process observations.
- Polling keeps no subscriber queues or cursors on the server. Each response
  copies a bounded view before serialization. A slow client can fall behind
  retention and recover its current state with a new snapshot.

## Live Worker Monitor

In **Experiments**, choose **Benchmark**, **Manual**, or **Adaptive**.
Benchmark keeps the full 1/2/4/8-worker comparison. Manual processes one
registered dataset with a fixed worker count. Adaptive calibrates before final
processing, so it adds overhead and can fall back to one worker. It can take
longer than a fixed run.

Starting a manual or adaptive operation automatically attaches the **Live Worker
Monitor** when the server returns an accepted run ID. The current tab remembers
the ID across navigation and reloads. Use **Cancel operation** in the monitor to
request a cooperative stop. Dispatched work can finish before the run settles.
Use **Copy run ID** to share the monitored ID. Open **Attach existing run** to follow an operation started elsewhere if
you already have its ID.

If a submission loses its response, the dashboard pauses further operational
submissions. The operation may already be running. Check the server, attach if
you have the run ID, and explicitly choose **Allow a new submission** before
starting another operation. Admission remains subject to the existing server
resource and workload limits.

The monitor polls about every 800 ms while a run is active and keeps one request
in flight. It marks delayed or failed reads as stale and retries transient
failures with capped backoff. A terminal snapshot stops polling. An unavailable
run keeps a manual retry action. When the server requests a resync, the monitor
replaces its history with the returned window and uses the authoritative counts
and worker states from that snapshot.

The browser retains at most 256 events for the attached run and shows up to 10
recent observations in each event filter. Worker cards use confirmed instances
from the latest admitted pool. Calibration trial counts and outcomes remain
separate from final processing. Worker transitions animate only on fresh,
continuous observations, and respect reduced-motion settings. Use the poll
coverage details to distinguish event-window eviction from worker transport gaps.

The browser integration tests admit generated datasets through the real API.
They use deliberate delays to make cancellation and reconnection reproducible and
inject a real child process exit in the test server. The captures show the UI
alongside actual spawned-run evidence. All inputs and workloads are synthetic.

## Local evidence

Live snapshots are process-lifetime data. After eviction or a server restart,
operator-owned receipts under the configured data directory remain inspectable
with `tachyron-execute inspect` or `inspect-adaptive`. The server stores operational
results in `operations/<run_id>`. The local operator controls disk retention.

For a local adaptive run, opt in with
`tachyron-execute adaptive <dataset> --output <new-directory> --telemetry`.
The versioned `adaptive-telemetry.json` contains the terminal bounded view and
shares its run ID with the adaptive receipt's calibration ID. The existing
adaptive report and timing receipt retain their original measurement boundaries.
Fixed-worker `run --telemetry` and `inspect-telemetry` retain their
[Stage C1 artifact contract](local-execution.md#worker-telemetry-stage-c1).

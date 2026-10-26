# Parallel worker execution

Tachyron uses Python's `ProcessPoolExecutor` for single-host multiprocessing.
Each task calls the existing top-level `process_image()` with the same manifest
entry, transform, resource limits, and output mapping as the sequential runner.
There is no alternative transformation, broker, web dependency, or benchmark clock.

## Invocation and compatibility

The established command remains sequential when `--workers` is omitted. Even
`--workers 1` explicitly creates a process pool; it does not select the sequential
runner. Supported counts are exactly 1, 2, 4, and 8. Boolean/noninteger values in
Python and unsupported counts are rejected before any output directory or pool
is created. The command uses the existing exit codes:

| Code | Meaning                                                                   |
| ---- | ------------------------------------------------------------------------- |
| 0    | All images produced complete outputs                                      |
| 1    | At least one expected per-image failure; complete accounting is available |
| 2    | Invalid setup, catastrophic pool/worker failure, or report I/O failure    |
| 130  | Interrupted; shutdown has drained dispatched work                         |

Generate the tiny PNG/JPEG dataset using the [processing example](processing.md#reproducible-example).
With its existing `results/` parent, select fresh output directories:

```powershell
uv run --locked --project apps/server tachyron-process datasets/example --output results/pool-1 --width 4 --height 3 --workers 1
uv run --locked --project apps/server tachyron-process datasets/example --output results/pool-2 --width 4 --height 3 --workers 2
uv run --locked --project apps/server tachyron-process datasets/example --output results/pool-4 --width 4 --height 3 --workers 4
uv run --locked --project apps/server tachyron-process datasets/example --output results/pool-8 --width 4 --height 3 --workers 8
```

The PNGs, manifest identity, counts, and ordered outcomes match sequential
execution for unchanged inputs. Parallel `run.json` adds only an `execution`
object containing `mode`, `requested_workers`, `start_method`, and `max_in_flight`.
Sequential JSON retains its established shape. Execution metadata differs with
worker selection; it is not a benchmark measurement or a claim of improved speed.

Python callers use the same core:

```python
from pathlib import Path

from tachyron.processing import TransformConfig, build_manifest
from tachyron.workers import run_parallel

if __name__ == "__main__":
    manifest = build_manifest(Path("datasets/example"))
    result = run_parallel(
        manifest, Path("results/python-pool"), TransformConfig(4, 3), 2
    )
    assert result.succeeded + result.failed == len(manifest.entries)
```

The caller's main guard is required for spawn. Importing the package creates no
pool. Both Python 3.11 and 3.13 use an explicit `multiprocessing.get_context("spawn")`
on every OS, without changing the process-wide default start method. Paths,
immutable contracts, and outcomes cross the process boundary; decoded Pillow
images and compressed image bytes do not cross it.

## Scheduling and resource policy

The runner admits at most **twice the requested worker count** in submitted,
uncollected futures: 2, 4, 8, or 16. This allows one extra task per worker without
an unbounded queue. `Executor.map()` is deliberately not used because Python 3.11
collects its inputs eagerly. Parent-side records are small path/configuration
descriptors; file reads and transformations happen inside children.

The parent alone advances a manifest index and submits each entry once. It waits
for at least one completion, checks result identity, and stores outcomes by their
original index. Out-of-order completion never changes report ordering. There are
no retries, speculative submissions, or progress percentages.

The configured count is a maximum, not a promise that every child gets work or
that all workers run simultaneously. Small datasets and scheduling may use fewer
children. `requested_workers` is intentionally not named an observed worker count.

Counts above `os.cpu_count()` produce a warning and preserve the explicit choice.
This allows controlled oversubscribed experiments on smaller hosts and CI, with
a fixed ceiling of eight. The count is never automatically expanded or silently
reduced. The warning reflects reported logical CPUs; it does not discover cgroup
quotas, affinity restrictions, or available RAM. No oversubscription speedup is
assumed.

The [existing dataset/input/output limits](processing.md#resource-and-trust-boundaries)
still apply. Decode buffers exist per active worker, so memory use can multiply
by the selected count. This is not a process memory quota: use modest input limits
and worker counts for the machine. No new dependency is installed to estimate RAM.

## Ownership, failure, and shutdown

One invocation owns the new run directory and its executor. Shared `prepare_run`
and `finish_run` enforce the same output ownership and final accounting used by
the sequential runner. Existing directories are rejected; inputs and existing
results are never overwritten or removed.

- Corruption, unsupported images, content changes, missing inputs, limits, and
  output collisions return the existing per-image codes. Other entries continue.
- Unexpected task exceptions, `SystemExit`, broken workers, initialization errors,
  or submission failures abort the attempt with `WorkerExecutionError`. Its public
  message is sanitized and its exception cause is retained for Python debugging.
- The runner never retries an entry or fabricates results for unfinished work.
  Fatal/interrupt paths publish no completed `run.json`. Already-published images
  remain available; their presence alone does not indicate a completed batch.
- Final report write failures retain completed images and fail visibly, as in
  sequential execution. The CLI uses a fixed message for catastrophic worker errors.

For a pool whose management thread has started, shutdown continues to use
`shutdown(wait=True, cancel_futures=True)`.
Futures still cancellable are cancelled. Work already dispatched to a child may
be marked running before image processing actually starts; that queued work can
also finish. There is no promise of instantaneous cancellation or exactly one
remaining task per worker.

First-submission failures need separate recovery on CPython 3.11 and 3.13. A
child can already be initialized while the executor's management thread is absent
or has not started. Public `shutdown()` alone drops the process registry in the
first case, or raises while joining an unstarted thread in the second. The
isolated `workers/_startup.py` compatibility code stops and joins only children
registered to that executor, then lets public shutdown release its remaining
resources. An unrelated process is never selected for cleanup. No processing
task can have been dispatched by a management thread which never started.

This recovery necessarily reads `_processes` and `_executor_manager_thread`, and
clears the latter only when it never started. These are private CPython fields,
with matching lifecycles verified on 3.11.15 and 3.13.9. A layout check runs before
submission; an unknown layout fails visibly before starting children. Matching
field names alone cannot establish compatibility with a changed implementation:
runtime upgrades still require the real-process regressions. This is a narrow
startup workaround, not a supported executor extension point. A context manager
would invoke the same insufficient shutdown API; enumerating all child processes
would violate ownership. Python 3.11/3.13 have no public executor termination API.

On the main thread, Ctrl+C delivery is briefly deferred during constructor and
submission bookkeeping so it cannot interrupt child registration. The previous
handler is restored before delivery. If the operation itself fails, its original
exception takes precedence over a deferred interrupt. Explicit `KeyboardInterrupt`
exceptions still propagate after cleanup; other startup failures retain their
cause under `WorkerExecutionError`. No complete `run.json` is written.

Children ignore Ctrl+C so the parent controls orderly interruption. During its
join, the main-thread owner temporarily ignores additional Ctrl+C and restores
the previous handler afterward. A thread caller does not change signal handlers.
The runner then removes only `.tachyron-*.tmp` files directly in its exclusively
owned run directory, after children have stopped. It retains complete PNGs and
other files. OS-denied cleanup logs a warning naming the leftover temporary file.
Do not write unrelated files into another active run's output directory.

Python 3.11 offers no supported per-task hard-kill/deadline API here. A hung native
decoder or blocked filesystem operation can delay shutdown indefinitely. Forceful
parent termination, power loss, or shutdown failure cannot guarantee cleanup;
an incomplete directory must not be treated as a completed run. Startup recovery
does not impose hard cancellation deadlines on initialized pools or add a watchdog
service.

These behaviors follow Python's [executor shutdown contract](https://docs.python.org/3.11/library/concurrent.futures.html#concurrent.futures.Executor.shutdown)
and [spawn guidance](https://docs.python.org/3.11/library/multiprocessing.html#the-spawn-and-forkserver-start-methods).
The startup gap is visible in CPython's
[3.11.15 executor implementation](https://github.com/python/cpython/blob/v3.11.15/Lib/concurrent/futures/process.py)
and [3.13.9 executor implementation](https://github.com/python/cpython/blob/v3.13.9/Lib/concurrent/futures/process.py).

## Validation and boundaries

The `spawn` pytest marker selects real child-process integration, including
1/2/4/8-worker parity, child PID evidence, worker crashes, initializer failures,
parent interruption, partial-startup failures, CLI spawning, and cleanup. The
startup probes inject `KeyboardInterrupt` and `OSError` before management starts,
prove a real initialized child is reaped, keep an unrelated child alive, and then
verify recovery with sequential byte parity. Another probe delivers SIGINT in the
registration gap and verifies that the started manager drains its worker normally.
Private fault hooks are test-only and locate the failure deterministically;
subprocess timeouts, test-worker watchdogs, and explicit fallback cleanup bound
failed probes. The existing Ubuntu/Windows ×
Python 3.11/3.13 CI matrix runs ordinary tests and the spawn group in separate
steps, once each. Controlled futures test out-of-order completion and exact queue
bounds without sleeps or scheduling assumptions. Tests make no speedup assertions.

```powershell
uv run --locked --project apps/server pytest apps/server/tests -q -m "not spawn"
uv run --locked --project apps/server pytest apps/server/tests/test_workers.py -q -m spawn
```

Pillow operations and filesystem safety remain in `processing/`. Pool lifecycle
and bounded aggregation live in `workers/`. CLI parsing/reporting remains in
`cli.py`. All run without FastAPI or SQLite. Benchmark statistics, repeated runs,
CPU/RAM sampling, background HTTP jobs, persistence, charts, Docker, and multi-host
execution remain deferred.

# Benchmark methodology and export contract

Tachyron compares real executions of one immutable image manifest and transform
using 1, 2, 4, and 8 spawn workers. These measurements describe this workload on
this machine. A tiny generated workload is a functional test, not representative
performance evidence. More workers need not be faster.

## Timing and experiment schedule

The primary baseline is **one worker in a process pool**. The sequential
`tachyron-process` command remains available but is not mixed into this comparison.
`time.perf_counter()` brackets each complete `run_parallel()` call, including
validation, output directory creation, pool startup, reads, decoding, orientation,
compositing, resizing, grayscale conversion, PNG writes, per-batch report writing,
pool shutdown and worker temporary-file cleanup. Discovery, experiment planning,
provenance, monitor setup/teardown, benchmark export and image retention cleanup
are outside the timed boundary. Sampling itself overlaps execution and adds
overhead; the parent process CPU measurement includes that overhead.

Each configuration gets exactly one recorded warm-up followed by five measured
repetitions by default. All warm-ups precede measurements. A deterministic seeded
permutation orders the four configurations separately in every round, sorting
SHA-256 digests of `seed:round:workers`; actual execution order is exported.
Warm-ups never enter statistics. Production repeat
counts are 5–20. An explicit `--tiny` test workload permits one measured repetition
per configuration; it is labeled in every export and has no sample standard
deviation. It changes no image correctness checks or worker policies.

The runner neither flushes OS caches nor claims cold-cache measurements. Warm-ups,
interleaving and a fixed manifest cannot remove cache, disk, thermal, background
load, affinity or scheduler effects. Use an otherwise idle machine. A cooperative
OS file lock permits one benchmark invocation per OS account and local temporary
directory. This coordinates Tachyron benchmark commands, not other programs,
accounts, or the separate processing CLI. OS locks release on process exit; the
lock file is retained to avoid inode replacement races.

## CPU and memory observations

Only `psutil` is added for portable resource observations; its typing stubs are a
development dependency. The sampler uses a dedicated thread and nonblocking CPU
counter reads. It observes the benchmark parent and newly discovered descendants,
excluding descendants already present when that run's sampler starts. Use a
dedicated process for the Python runner so unrelated new children do not pollute
this scope. Descendants may include multiprocessing support processes. Therefore
requested pool size is recorded, but actual worker count is null: observing child
processes cannot establish how many workers performed tasks.

CPU is **logical-core-equivalent percent**: 100% means one fully occupied logical
CPU; multicore observations can exceed 100%. For each stable observed process set,
sum user/system CPU-time deltas and divide by the wall interval. First observations
prime counters. Births, exits, PID reuse or inaccessible counters invalidate that
CPU interval rather than fabricate zero for missing activity. The reported mean
is weighted by valid interval duration, with valid sample count and observed
seconds. CPU from processes which start and exit between polls is unobserved.
Worker-summary CPU means weight those same available intervals across measured
repetitions. Summary RSS is the maximum observed measured-run peak; availability
counts disclose how many repetitions contributed resource observations.

Memory is the maximum sampled sum of process-tree RSS divided by 1,048,576
bytes, in MiB. Shared pages can be counted repeatedly; RSS is neither unique
physical occupancy nor a memory quota. Sampling can miss short peaks. An
incomplete memory snapshot is unavailable, not a partial-tree total labeled as a
complete one. Missing metrics are JSON null / CSV empty cells; genuine observed
zero remains numeric zero.

The default interval is 0.1 seconds (permitted range 0.01–10). Observations carry
monotonic timestamps, and only observations inside the timed execution contribute
to its summary. Sampling storage is capped at 10,000 observations per run; the
export explicitly reports truncation. Thread/setup/read failures leave resource
metrics unavailable with a sanitized reason. Monitoring availability does not
invent or invalidate otherwise real elapsed time and image counts.

## Failures, ownership and bounded storage

One invocation exclusively creates a new experiment directory outside the dataset.
Existing directories and exports are never overwritten. Every run gets a unique
directory. Completed raw records are published independently before proceeding.
Successful runs delete only their known generated PNG outputs after timing;
their batch reports remain. Unknown files and failed/interrupted batch outputs
are retained. No recursive deletion or automatic history pruning occurs. Repeat
and dataset/output limits bound retained records, and at most one failed batch is
retained because the experiment stops at its first unsuccessful run.

Any image failure, catastrophic executor error, interruption, invalid timing,
provenance change or cleanup failure stops the experiment. Completed raw evidence
is retained. The whole comparison is incomplete; no successful subset is silently
presented as a complete experiment. Catastrophic exceptions and interrupts retain
their Python exception semantics after best-effort failure export. A missing final
experiment manifest means publication did not complete. Already-running native
operations still follow the worker runner's wait-and-drain limitation.

## Statistics and provenance

For a complete compatible experiment, each worker summary contains measured count,
mean, median, minimum, maximum, sample standard deviation (`ddof=1`, null for a
single observation), and arithmetic mean of per-run throughput. Each throughput
is successful images divided by its elapsed seconds. Speedup is mean one-worker
elapsed / mean selected-worker elapsed. Efficiency is speedup / workers × 100%.
The successful baseline is exactly 1.0× and 100%. Invalid/nonfinite durations and
undefined ratios remain unavailable or fail validation; they are never clamped.

Exports carry a schema version, experiment/run IDs, fixed context digest, manifest
digest/count/bytes, transform and resource policy, timing/CPU/cache policies,
warm-up flag, repetition index, execution order, UTC timestamps, status/counts,
elapsed/throughput, sampling availability, and units. Context includes Python,
Pillow, psutil and Tachyron versions; source hash; Git commit/dirty state when
reliably associated with the loaded source; OS, CPU description/counts/affinity,
memory and a hashed host identity. Installed wheels report their loaded source
hash and do not borrow a Git commit from the caller's current directory.

Raw JSON and CSV preserve each run, including warm-ups. Summary JSON and CSV derive
from those same records. JSON is authoritative for round trips; CSV includes
portable context metadata. Re-comparison validates schema, context identity,
record identities, planned order, accounting and completeness before aggregation.
Mixed manifests, transforms, environments, modes or test/production policies are
rejected. Portable exports omit absolute input/output paths and hostnames. Schema
validation checks consistency, not the authenticity of third-party measurements.

## CLI and Python usage

Install locked dependencies and generate the tiny dataset from the
[processing example](processing.md#reproducible-example). Its `results/` parent
must exist. Each invocation requires a fresh output directory:

```powershell
uv sync --locked --project apps/server --extra dev
uv run --locked --project apps/server tachyron-benchmark run datasets/example --output results/benchmark --width 4 --height 3
uv run --locked --project apps/server tachyron-benchmark compare results/benchmark/runs.json --output results/recomputed
uv run --locked --project apps/server tachyron-benchmark run datasets/example --output results/tiny-benchmark --width 4 --height 3 --tiny
```

The first command uses four warm-ups plus twenty measured executions. The last
uses four warm-ups plus four measured executions and labels all evidence `tiny`.
`--repetitions`, `--seed`, and `--sampling-interval` override their validated
defaults. All commands emit JSON on stdout. Exit codes are 0 for a complete
comparison, 1 for image failures, 2 for invalid setup/comparison or catastrophic
execution/export failure, and 130 for interruption. Error messages omit local
paths and exception details. `compare` accepts one complete `runs.json`, validates
it, and recomputes all statistics; it never joins unrelated experiments or trusts
precomputed summary values. A failed comparison creates no output directory.

The built wheel includes Pillow and psutil as dependencies; no API extra, server
or frontend is required. The source-tree equivalent is
`uv sync --locked --project apps/server` without the `dev`/`api` extras.

```python
from pathlib import Path

from tachyron.benchmarking.models import BenchmarkConfig
from tachyron.benchmarking.runner import run_experiment
from tachyron.processing import TransformConfig, build_manifest

if __name__ == "__main__":
    experiment = run_experiment(
        build_manifest(Path("datasets/example")),
        Path("results/python-benchmark"),
        TransformConfig(4, 3),
        BenchmarkConfig(),
    )
    assert experiment.status == "success"
```

## Schema version 1 and file lifecycle

| File                        | Contract                                                                                                                                   |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `plan.json`                 | Fixed context/configuration and complete seeded schedule; `running` is not a completion claim.                                             |
| `records/<run_id>.json`     | One independently published raw attempt, with schedule slot, status, image counts, timing and telemetry.                                   |
| `batches/<run_id>/run.json` | Existing processing accounting, when batch execution completed.                                                                            |
| `runs.json`                 | Authoritative immutable experiment evidence: schema/version, context and digest, configuration, plan, ordered records and explicit status. |
| `runs.csv`                  | The same raw records flattened with step/telemetry fields and `context_json`; no data rows when no run was recorded.                       |
| `workers.json`              | Context, status, four recomputed summaries and explicit units. Incomplete comparisons have null statistics.                                |
| `workers.csv`               | Flattened summaries with elapsed fields suffixed `_seconds` and resource/throughput fields as documented above.                            |
| `experiment.json`           | Published last, with status, planned/recorded counts and SHA-256 of all four exports. Absence means export publication did not finish.     |

`Step` fields are `run_id`, `workers`, `warmup`, `repetition` (zero for warm-up,
one-based for measurements), and zero-based `order`. Each raw record references
the full `context_id`; changing its manifest, transform, resource limits, policies
or environment without updating compatible raw evidence is rejected. Run counts
are null when catastrophic execution leaves the completed image count unknown.
They remain known after a completed batch even when subsequent cleanup fails.
`actual_workers` is deliberately null; `observed_descendants_peak` includes any
observed support processes and is not a worker-utilization claim.

Durations use `elapsed_seconds`; throughput uses
`throughput_images_per_second` in raw records and `mean_throughput` in summaries.
Resource fields explicitly use `core_percent`, `seconds`, and `mib`. Zero is valid
only when observed/calculated. Sample standard deviation is null for a single
measurement. Readers reject unknown schemas, duplicate JSON keys, nonfinite data,
invalid types, contradictory accounting, inconsistent throughput, and files above
4 MiB, reusing the existing regular-file/link/race checks. CSV is for analysis;
JSON preserves structure and types for re-comparison.

The `spawn` CI group includes real 1/2/4/8-worker benchmark parity, cross-process
exclusion and guarded CLI execution. Ordinary tests cover formulas, warm-up and
schedule accounting, unavailable/churning process observations, failure exports,
output ownership and compatibility rejection. No hardware performance threshold
is a passing criterion.

## Actual tiny workload example

The following is **real measured test data**, not representative performance
evidence. On 2026-10-26 at 15:23 UTC, an installed wheel processed nine generated
96×64 images (eight PNGs, one JPEG), resizing to 32×32. Each configuration ran one
warm-up and five measured repetitions with seed 2026 and 0.02-second sampling.
The host reported Windows 10, AMD64 Family 25 Model 80, eight physical/sixteen
logical CPUs, and 32,157 MiB RAM. Runtime: Python 3.11.15, Pillow 13.0.0,
psutil 7.2.2. FastAPI and pytest were absent from this installation.

Experiment ID: `d5aea4ff-6181-4740-87f9-52fdb0371476`.
Loaded source SHA-256:
`76955bda717c60f80728a47cd69f5a2293569f87783c2f4943c8935c3c1b7980`.
Git commit is unavailable for this installed wheel, as intended. The source hash
was verified against the working package and built wheel.

| Workers | Mean seconds | Sample stddev seconds | Mean images/s | Speedup | Efficiency % | Observed CPU core % | Peak tree RSS MiB |
| ------- | ------------ | --------------------- | ------------- | ------- | ------------ | ------------------- | ----------------- |
| 1       | 0.4915       | 0.0119                | 18.3217       | 1.0000  | 100.00       | 113.42              | 92.29             |
| 2       | 0.4945       | 0.0298                | 18.2496       | 0.9938  | 49.69        | 204.64              | 136.88            |
| 4       | 0.5296       | 0.0186                | 17.0124       | 0.9280  | 23.20        | 375.49              | 213.79            |
| 8       | 0.7101       | 0.0308                | 12.6937       | 0.6921  | 8.65         | 689.99              | 283.44            |

All 24 batches completed nine images without failure; the four warm-ups are
excluded from this table. CPU and RSS observations were available in all five
measured repetitions per configuration. These are observed resource values, not
normalization to host capacity or exact physical-memory occupancy. Pool startup
is substantial for this tiny input; the table establishes working measurements
and formulas, not a general scaling result.

Raw excerpt from the first measured repetition:

```json
{
  "step": {
    "run_id": "d995cc90-f96e-5b9b-a34a-cbdb0219ccbe",
    "workers": 1,
    "warmup": false,
    "repetition": 1,
    "order": 4
  },
  "elapsed_seconds": 0.5008642000029795,
  "succeeded": 9,
  "failed": 0,
  "status": "success"
}
```

Full raw JSON/CSV and summaries were written under the ignored local directory
`.cache/benchmark-example-final/experiment/`. Re-comparison reproduced the summaries,
all four export hashes matched the final manifest, and independent calculations
from the raw durations reproduced the means, sample deviations, throughput,
speedup and efficiency. The installed sequential and two-worker processing CLI
also produced byte-identical PNGs for this dataset. Generated images/results are
not repository fixtures or published representative benchmarks.

Benchmark timing/statistics live in `tachyron/benchmarking/`, and the focused
`tachyron-benchmark run` / `compare` CLI adapts that core. Existing processing and
worker modules remain independent. HTTP jobs, persistence, charts, distributed
execution and hard native-task cancellation remain deferred.

The existing [worker resource policy](workers.md#scheduling-and-resource-policy)
still applies: decode/transform memory can multiply with active workers. Available
RAM is provenance, not an exact admission quota. Use modest workloads and resource
limits for the host; no automatic reduction of the requested worker count occurs.

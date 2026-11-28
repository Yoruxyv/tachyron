# Adaptive image execution

**Experimental opt-in:** adaptive execution profiles an authorized image workload, measures bounded sample
batches on the current machine and either selects a measured worker configuration
or explicitly abstains. It then executes the full frozen workload once.
The CLI and Python interface accept an operator-selected path. The
[operational API](operational-telemetry.md) uses a registered dataset ID.

## Usage

Run from the repository root. Use an existing dataset and an unused output
location whose parent already exists. [Create an example dataset](processing.md#reproducible-example)

```powershell
uv sync --locked --project apps/server
uv run --locked --project apps/server tachyron-execute adaptive datasets/example --output results/adaptive-1
uv run --locked --project apps/server tachyron-execute inspect-adaptive results/adaptive-1
```

Defaults are 256x256 grayscale PNG, 32 sample files (64 on eligible large
workloads), three repetitions per sample size and candidate, seed 2026, a
60-second cooperative calibration budget, a 576-image trial-work cap and one
fallback worker. Calibration can be slower than a fixed
policy, even when a supported measured configuration is selected. Options are `--width`, `--height`, `--sample-files` (even, 16-64),
`--repetitions` (3-5), `--budget-seconds` (1-300), `--budget-images` (1-1920),
`--seed` (unsigned 32-bit) and
`--fallback-workers` (1/2/4/8). A requested fallback must satisfy current feasibility
checks; an infeasible fallback produces failure without full processing.

```python
from pathlib import Path

from tachyron.execution import inspect_adaptive, run_adaptive
from tachyron.processing import TransformConfig

if __name__ == "__main__":
    result = run_adaptive(
        Path("datasets/example"),
        Path("results/adaptive-1"),
        TransformConfig(256, 256),
    )
    print(result.calibration.selection)
    print(result.total_seconds)
    persisted = inspect_adaptive(Path("results/adaptive-1"))
```

The Python API also accepts the existing local limits, narrowed only, and a
cooperative `cancelled` callback. Spawn requires the entry-point guard. Exit codes
match local execution: 0 for completed full work (including an inconclusive
fallback), 1 for failed/unconfirmed, 2 for setup/publication errors, 130 for cancellation.

## Preflight and progressive budgets

The feasible candidate set is 1/2/4/8. CPU count, affinity, quotas
and memory observations only constrain feasibility; they never establish a winner.
The effective trial-image budget is `min(budget_images, input_count // 2)`.
A complete candidate block costs `repetitions * (sample_files + sample_files // 2)`
image executions. If two complete blocks do not fit, bypass header profiling and
trials, report `INCONCLUSIVE` with `calibration_work_budget_bypass`, and execute
once with the explicit feasible fallback. With the default sample sizes and repetitions, this budget allows two complete
blocks from 576 inputs upward. Smaller eligible datasets bypass the trials. This is a cost policy, not evidence that one worker is
fastest. A fixed larger policy may still be substantially faster.

When a full header reread would exceed 512 MiB, the automatic policy uses 64
sample files if two such candidate blocks still fit the work budget (at least
1,152 inputs with the default budget). Wider batches give the fit more work
relative to pool startup, at the cost of fewer candidates under the same cap.
An explicit Python policy or CLI sample size takes precedence. If another CLI
calibration option differs from its default, omitting the sample size uses 32.

For eligible workloads, test the fallback first, then other feasible counts in
descending order to measure the widest contrast before filling intermediate
configurations. Each block retains both batch sizes and all configured repetitions
in seeded order. A noisy or invalid fallback model stops further calibration:
without it, the unchanged relative-gain requirement cannot be established.

The original wall-time budget remains cooperative. After a complete supported
fallback/alternative comparison exists, tighten it to half the fallback's lower
estimated full-execution time, counting profiling and work already spent. It may
already have been exceeded when the comparison completes; in that case admit no
further trials and retain the supported remaining policy. Until a supported
comparison exists, only the declared absolute time/work allowances apply. A
fallback-only measurement cannot trigger this fractional cap. Reserve the largest observed completed block
cost before admitting another candidate. This reserve is an admission estimate,
not an upper bound on future work. Check the hard image-work allowance before each
block and the cooperative stop callback during trials. A running image or pool
shutdown may exceed the elapsed budget while draining; no hard deadline is promised.

These explicit work/time allowances bound calibration investment; they do not
prove total-workflow improvement. Stopping can leave faster configurations untested.
Select only among configurations with complete, valid evidence. Keep partial and
unconfirmed attempts as evidence, but never fit them as complete configurations.
The report retains the stop reason, feasible set and all trials, so untested counts
remain identifiable. User cancellation still stops full processing; budget exhaustion
allows one full execution with the best supported policy or the safe fallback.

A bypass uses the input policy's worst-case pixel bound for memory feasibility.
This can conservatively reject an expensive fallback whose actual images would
fit. The full run still applies local input validation and resource limits.

## Sampling and bounded measurements

The full manifest is discovered and hashed using the existing local filesystem
protections. Header profiling rereads and checks those identities. If rereading
every path would exceed the 512 MiB compressed-byte cap, profiling can reread
one representative per distinct size and SHA-256 identity. It checks that each
representative still matches the manifest and maps its header to paths with
the same frozen bytes. Every path still counts in the workload and sample
population. Trials and final execution check each path's identity before
decoding, even for copies.

The same profiling byte and time limits apply to the representative reads.
A partial profile, unsupported or corrupt header, or exhausted budget leaves
calibration inconclusive. Full execution applies the existing per-image
validation and error semantics.

For a complete profile, sort inputs by actual PNG/JPEG format, decoded pixels,
compressed size and path. Split that order into equal-population strata and select
one image per stratum by a deterministic hash of seed, path and content digest.
Seeded swaps within adjacent pairs create a nested half-size sample without always
choosing the lower-size stratum. Both samples must cover every observed format and
power-of-two pixel-size category. Missing rare categories cause abstention.

The full sample has at most 64 Mi decoded pixels in aggregate. Workloads with fewer
than twice the requested sample count skip calibration. Selection is deterministic
given the policy and frozen input bytes; measured timings and the resulting worker
choice naturally vary. Representative coverage of these observed attributes is
an approximation, not proof that image complexity or future data is fully captured.

Each admitted feasible candidate from 1/2/4/8 runs both sample sizes with three to
five repetitions. The seeded within-candidate order and complete-block progression
are declared above. At most 40 trial runs are admitted (24 by default), subject to
the stricter work and time allowances. All trials use the original processor,
bounded spawn scheduler, configuration, resource policy and operational receipts.
Trial outputs stay separate from the final outputs. Every failed/noisy trial is
retained. There is no warm-up exclusion in this operational calibration policy;
startup and cold/warm variability affect the actual evidence.

The time budget stops admitting new headers/trials and requests cancellation of
sample work. Already dispatched tasks drain, so this is not a hard deadline.
Budget exhaustion excludes incomplete configurations while retaining complete
evidence; selection can be supported or inconclusive. User cancellation prevents
full processing. Pool/receipt failures stop the workflow instead of triggering
a blind full-workload retry. A failed trial takes precedence over cancellation.

## Feasibility observations

Existing provenance records physical/logical CPUs, affinity, available memory,
runtime and source identity. CPU count is a feasibility ceiling, not an estimate
of an optimal worker count. Candidates also respect observed affinity and reliable
visible quota information when available.

On Linux, the implementation recognizes a unified cgroup v2 mount at
`/sys/fs/cgroup` whose mount root is `/`. It reads current membership and visible
ancestors' CPU quotas and memory headroom. Unrecognized mounts, cgroup v1,
inaccessible/malformed files and unsupported platforms are explicitly unavailable.
Hidden ancestor constraints cannot be inferred. CPU bandwidth and memory-file
semantics follow the [kernel cgroup v2 documentation](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html).
Windows Job Object quotas are not interrogated. Missing observations never become
fabricated capacity or a claim of unrestricted resources.

For memory, use the smaller available host/cgroup headroom observation, allow at
most half of it and retain at least 512 MiB. The per-worker heuristic is 64 MiB
plus 32 bytes per largest profiled input pixel, twice the largest compressed input
and 16 bytes per output pixel. An incomplete profile uses the existing maximum
decoded-pixel cap. Unknown memory/capacity restricts calibration to one worker;
no feasible fallback means no full execution. These are conservative heuristics,
not reservations or complete decoder-memory proofs.

Resources are reobserved before each trial and before full execution. An invalidated
selection falls back only if that fixed policy still fits. Sampled trial RSS must
be available and within the observed memory budget. Disk checks admit trial output
headroom as well as the existing full-run policy. Neither disk nor memory checks
enforce a hard OS quota; resources can change after observation.

## Selection objective and uncertainty

For each candidate, fit `T(n) = startup + per_image * n` using the medians of its
half/full sample durations. Durations include setup, provenance, pool lifecycle,
image I/O, acknowledgements, final operational receipts and the resource sampler.
The startup term is included once in projected full execution.

Require complete repetitions, positive incremental slope and a nonnegative startup
term. A within-size observed range greater than 25% of the median invalidates that
candidate's model. Min/max repetitions produce conservative observed-range slope
and projected-time bounds. These bounds are **not statistical confidence intervals**.
Truncated/unavailable monitoring invalidates a complete configuration. Incomplete
configurations cannot provide estimates; a valid completed fallback and alternative
can still support a remaining-execution choice after a budget stop.

The model projects fixed-worker setup and receipt costs from trial measurements.
The actual workflow total also includes writing the adaptive report and timing footer.

The objective is the smallest projected total completion time among supported fits:
observed setup plus calibration cost plus projected full execution. Selection
requires a valid measured fallback and at least one alternative. An alternative's
upper projected execution time must be below 90% of the fallback's lower projected
execution time. Choose the smallest projected total among those alternatives;
otherwise report `INCONCLUSIVE` and use the explicit fallback. No choice is
inferred merely from CPU specifications or the largest observed sample throughput.

Calibration is already spent at selection time and is common to every remaining
choice. It must not veto a faster remaining execution and cause a slower fallback.
Report its value separately: `amortized_gain_supported=true` requires calibration
cost plus the selected upper execution bound to remain below 90% of the fixed
fallback's lower execution bound. False means an overall gain is not established;
it does not mean calibration should be charged to only one candidate. A selected
configuration can therefore accompany an adaptive workflow that is slower than
going directly to a fixed policy. Estimated fixed cost uses the model's shared
setup boundary; compare actual standalone fixed workflows separately.

This establishes an empirical advantage against that fallback under the model,
not a globally optimal worker count. It does not require disjoint bounds between
every pair of alternatives. Startup, cache, I/O, compression complexity, thermal
state and nonlinear contention can invalidate extrapolation on the full workload.
The report distinguishes estimates from actual execution and total workflow cost.
An adaptive workflow being slower is valid evidence, including after abstention.

## Timing and receipts

The workflow holds the existing cooperative account/temp-namespace lock from
discovery through final publication. Shared fixed-N execution has an internal
manifest runner for callers already owning that lock; public `run_local` retains
its original locking, output and timing contract. There is no nested acquisition
or unlocked gap between calibration and final work.

A new adaptive output directory contains the following files.

- `adaptive-admission.json`: `adaptive-calibration-v4`, UUID, complete frozen
  manifest, transform, local limits, policy and self-described methodology.
- `trial-000/`, etc.: immutable local execution evidence for each sample run.
- `execution/`: one full-workload execution and its exact accounting.
- `adaptive.json`: profiles/categories, selected sample metadata, all measured
  trials, feasibility/resource observations, estimates, selection and execution ID.
- `timing.json`: hash of the primary report and duration through its publication.

`total_seconds` returned by Python and printed by the CLI measures entry through
publication of both final files. It includes discovery, hashing, admission, setup,
profiling, trials, resource observation, selection, full execution and publication.
`calibration.seconds` measures profile/trials/resource work immediately before
selection; selection computation is included in the total. Version 4 also records
`profile_seconds` and `trial_seconds` (including per-trial resource checks), the
effective `trial_image_budget` and `stop_reason`. Their phase durations do not
include every other calibration operation; the calibration total is authoritative. Persisted
`through_result_publication_seconds` includes primary report publication and
explicitly excludes the timing footer itself. That footer cannot measure its own
completed write. `elapsed_before_publication_seconds` is not a complete workflow
duration. For independent validation, time the entire call externally as well.

Inspection validates the method/policy, metadata coverage, trial identities,
counts/state links, model-based selections, full manifest identity and timing hash.
Missing primary/timing publication stays `UNCONFIRMED`; malformed evidence fails
closed. An interrupted trial's known earlier measurements are retained, with an
explicit `unconfirmed_trial` basename for any uncertain attempt. Inspect its
trial directory separately where admission exists. No file-existence heuristic
establishes a started task or successful image. An unconfirmed shutdown is not
automatically retried. There is no resume, exactly-once or power-loss guarantee.

Resource sampling uses the existing bounded 0.1-second process-tree sampler.
CPU uses stable PID/creation-time intervals, with 100% meaning one busy logical
core; births/exits and unreadable intervals remain missing. RSS is a sampled tree
sum, can count shared pages more than once and misses between-sample peaks. It is
not peak physical memory. Sampling cost is included, with no claim that its
overhead has been independently eliminated or that its observations establish
worker readiness, assignment or task-start events.

## Compatibility and telemetry

The producer writes `adaptive-calibration-v5`. It adds duplicate-aware profiling
and the wider automatic sample while keeping the same trial and selection rules.
Inspection also accepts historical
`adaptive-calibration-v2` reports using the exact original method, five-field policy
and exhaustive selection rule. It also reads the retained `adaptive-calibration-v3`
prototype under its original method: that prototype activated its fractional cap
after only the fallback and could pay calibration cost without measuring an
alternative. Version 4 corrects that activation rule and remains inspectable with
its original profiling method. Versions 3–5 have an explicit work budget,
phase costs and stop evidence; their six-field policy includes `max_trial_images`.
The timing footer format remains unchanged because its publication boundary is unchanged.

Adaptive receipts describe operational trials and the final run. Scientific
benchmarks have their own schedules, timing and exports.
[Benchmark evidence](benchmarking.md)

Pass `--telemetry` to retain the bounded terminal view. For registered datasets,
use the operational API's adaptive mode or the dashboard's **Adaptive** control.
[Adaptive telemetry and polling](operational-telemetry.md)

The same versioned calibration procedure applies on different machines, while
resource feasibility and measured choices can differ. This does not establish
cross-device performance or let a result from one device predict another. Validate
on the actual machine; Linux cgroup parsing has fixture coverage, not a claim of
full platform or hardware validation from a Windows run.

For a local run, add `--telemetry` to write `adaptive-telemetry.json`.
The [C2 telemetry contract](operational-telemetry.md) describes trial identity,
bounded retention, missing observations, and reconciliation with receipts.
Instrumentation contributes to elapsed workflow time and can affect time-budget
decisions. Compare telemetry modes under matching conditions before drawing
performance conclusions.

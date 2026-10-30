# Local image execution (Stage A)

`tachyron-execute` processes one explicitly selected local dataset with a fixed
1/2/4/8-process spawn pool. It supports admission of up to 10,000 files through a
separate `local-execution-v1` contract. Automatic worker selection, worker lifecycle
events, HTTP admission and dashboard integration are deferred.

## Run and inspect

From the repository root, after installing the locked Python project:

```powershell
uv sync --locked --project apps/server
uv run --locked --project apps/server tachyron-execute run E:/Images/batch --output E:/Results/batch-1 --workers 4
uv run --locked --project apps/server tachyron-execute inspect E:/Results/batch-1
```

Choose an existing dataset and a **new** output directory whose parent exists.
The default output is 256 by 256 grayscale PNG; `--width` and `--height` retain the
existing transform contract. Output directories are never reused or overwritten.
Console JSON contains counts, identity, duration and state; individual outcomes
are in `execution.json`. Exit codes are 0 for completed, 1 for failed/unconfirmed,
2 for admission/publication/shutdown errors and 130 for cancelled/interrupted.

Python callers use the same implementation. Spawn requires an entry-point guard:

```python
from pathlib import Path

from tachyron.execution import inspect_execution, run_local
from tachyron.processing import TransformConfig

if __name__ == "__main__":
    result = run_local(
        Path("E:/Images/batch"),
        Path("E:/Results/batch-1"),
        TransformConfig(256, 256),
        workers=4,
    )
    assert inspect_execution(Path("E:/Results/batch-1")) == result
```

`run_local` accepts an optional `cancelled` callback and a narrowed `ResourceLimits`
policy. Every limit must be no larger than `LOCAL_LIMITS`; arbitrary pool sizes
and expanded policies are rejected. Run it from the main thread when relying on
Ctrl+C handling. There is no background service or automatic resumption.

## Admission and filesystem boundaries

The local operator authorizes the root by invoking the CLI/Python function with
that path. This is a trusted local interface, with the operator's OS permissions;
it adds no browser path parameter, HTTP endpoint or remote authorization policy.

| Limit                                |         Local execution maximum |
| ------------------------------------ | ------------------------------: |
| Regular input files                  |                          10,000 |
| Total compressed input bytes         |                           8 GiB |
| Compressed bytes per input           |                          32 MiB |
| Decoded pixels per input             |                      16,777,216 |
| Input dimension                      |                          16,384 |
| Aggregate grayscale output pixels    |                   1,073,741,824 |
| Output dimension                     |                           4,096 |
| Discovered entries / directory depth |                     50,000 / 32 |
| Relative path UTF-8 bytes            |                           1,024 |
| Uncollected futures                  | twice the selected worker count |

The separate totals permit 10,000 default-size outputs. Existing browser imports,
registered benchmark datasets, scientific benchmarks and `tachyron-process` keep
their original limits. File count, compressed bytes, traversal, output pixel budget
and observed disk headroom are checked before creating the output directory.
Each file is then revalidated and decoded under the existing processing policy;
corrupt, unsupported, changed or over-pixel-budget inputs become individual errors.
Admission does not promise that all discovered files are valid images.

Existing processing checks reject links, Windows reparse points, nonregular files,
traversal and detected input replacement. Manifest hashes are checked again by
workers. Additional portable-name checks reject control characters and Unicode
normalization/case collisions. Inputs and outputs remain separate. Output names
are SHA-256 hashes of relative input paths. The existing exclusive, atomic hard-link
publication primitive protects existing destination files; the filesystem must
support hard links. These checks are not an OS sandbox against a hostile local
user changing the filesystem concurrently.

Disk admission requires observed free space of twice the requested output pixel
count plus 64 MiB. This is conservative headroom, **not a reservation or a hard
encoded-byte quota**. The queue is bounded, while manifest/outcome bookkeeping
grows with admitted file count. Pixel limits bound each decode, but multiple
Pillow buffers and workers can still exhaust memory. Stage A observes available
memory in provenance; it does not automatically reduce workers or enforce an OS
memory limit. Runtime disk exhaustion produces explicit image/receipt failures.

The existing cooperative experiment lock covers admission, execution and terminal
publication. Other cooperating local executions, scientific experiments and import
validation cannot run concurrently in that account/temp lock namespace. Unrelated
programs and different account/temp namespaces can still contend for resources.

## Outcomes and interruption

Every manifest input has exactly one terminal accounting category:

| Item state    | Evidence                                                                   |
| ------------- | -------------------------------------------------------------------------- |
| `SUCCEEDED`   | Parent received a successful output acknowledgement.                       |
| `FAILED`      | Parent received a sanitized per-image error.                               |
| `NOT_STARTED` | Never submitted, or its future was successfully cancelled before dispatch. |
| `UNKNOWN`     | Attempted submission without a confirmed outcome.                          |

Submission does not establish task startup, worker readiness or PID ownership.
An exception or abrupt worker exit is an execution-level `worker_failure`;
unacknowledged dispatched tasks remain unknown. It is not evidence that a
particular image caused the crash. Pools are shut down and children reaped using
the existing worker lifecycle helpers; no replacement/retry policy is added.

`COMPLETED` requires all inputs to succeed. Any image, worker or receipt failure
produces `FAILED`, with failure taking precedence over cancellation. A cancellation
request stops refilling the bounded queue, cancels cancellable futures and drains
dispatched work. The result is `CANCELLED` when work remains without another
failure. If everything finishes successfully before cancellation takes effect,
the result can be `COMPLETED` with `cancel_requested=true`.

There is no hard deadline for a running decode or pool shutdown. Ctrl+C during
pool execution requests this cooperative cancellation. An interruption before
admission, during final publication or an unconfirmed shutdown may leave no
terminal receipt. A second process can inspect retained evidence, but cannot
conclude from missing terminal evidence that the owner has stopped running.

## Receipt ownership and compatibility

The new output directory owns immutable files published without replacement:

- `admission.json`: execution UUID, UTC creation time, fixed worker cap, transform,
  local policy, portable hashed manifest and existing hardware/runtime provenance.
- `records/00000.json`, etc.: one bounded acknowledgement per received result,
  associated with the execution UUID and manifest index.
- `execution.json`: terminal state, all item outcomes, exact counts and operational
  processing duration, written only after confirmed pool shutdown.

Inspection validates consumed fields, manifest integrity, receipt ownership,
output mappings, policy and count consistency. A malformed terminal file is an
error; it does not fall back to partial success. Without a terminal file,
inspection returns `UNCONFIRMED`, recovering only valid acknowledgements and
classifying every other input as unknown. Cancellation and duration are unavailable
in this case. PNG existence never promotes an unknown input to success.

Receipts are bounded (64 MiB for admission/terminal and 4 KiB per acknowledgement).
Publication is atomic at the pathname level, with no fsync/power-loss guarantee,
authentication or protection against deliberate edits by the owning local user.
Inspection does not rehash output PNGs, resume work or promise exactly-once execution.
Keep both inputs and output evidence until they are no longer needed; cleanup is
an explicit operator action. Admission failure after directory creation may leave
an incomplete directory which cannot be inspected as an admitted execution.

`processing_seconds` includes pool startup/shutdown, image I/O, acknowledgement
publication and worker temporary-file cleanup. It excludes discovery/hashing,
provenance/admission publication and final receipt serialization. Measure the
whole `run_local` call separately for end-to-end operational cost. These durations
are not scientific benchmark samples and cannot be imported as benchmark-v1.

No benchmark Context, timing boundary, schedule, digest, statistics, JSON/CSV,
history schema, job/API contract, import policy or frontend state is changed.
Old scientific records retain their meaning. Newly captured source provenance
normally changes when new Python modules are added.

## Stage B prerequisites and smallest scope

Stage B requires separate authorization. Reuse this fixed-N runner and the current
image processor; keep scientific benchmark-v1 and this fixed-N CLI functional.
Before implementation, settle these specific contracts:

1. Define a versioned calibration/selection receipt referencing the full input
   manifest and config. Keep sample outputs and execution directories separate.
   Record total workflow time including discovery, calibration and final execution.
2. Specify deterministic sample selection and bounded inspection of format,
   dimensions and compressed size. Use a declared seed/digest and stratification;
   avoid selecting only the first files. Revalidate content identity before use.
3. Declare budgets for sample count/pixels/bytes, repetitions, candidate trials,
   elapsed time and observed memory/disk headroom. CPU count, affinity and memory
   provenance already exist; CPU quotas need an explicitly unavailable value when
   they cannot be reliably observed. Define feasible candidates from 1/2/4/8.
4. Choose an objective based on estimated **total** completion time, including
   calibration and startup costs. Define variability, inconclusive evidence,
   cancellation and explicit fixed-N fallback; preserve failed measurements.
5. Validate selection on representative holdout and adversarial workloads, including
   heterogeneous images, tiny batches, noisy trials and a slower adaptive result.
   Generated repeated fixtures prove mechanics only, not an optimal worker policy.

The smallest Stage B implementation is a local calibration command/report plus
an explicit selection result feeding `run_local` once. Hold the cooperative lock
across calibration and final execution without nested acquisition; this needs a
small ownership adjustment within operational execution, with exclusion tests.
No worker event transport, API job integration, SQLite migration or React change
is needed for that slice. Those belong to separately authorized later stages.

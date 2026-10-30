# Copyright (c) 2026 Hans Valerie
"""Explicit operator-local admission and one fixed-N operational execution."""

import shutil
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from tachyron.benchmarking.ownership import experiment_lock
from tachyron.benchmarking.provenance import capture_environment
from tachyron.execution.models import (
    LOCAL_LIMITS,
    ExecutionResult,
    ExecutionState,
    ItemResult,
    ItemState,
    validate_limits,
    validate_names,
)
from tachyron.execution.storage import encode, publish_outcome
from tachyron.processing import (
    DatasetManifest,
    ImageOutcome,
    ProcessingError,
    ResourceLimits,
    TransformConfig,
    build_manifest,
    prepare_run,
)
from tachyron.processing.processor import publish_bytes
from tachyron.workers.operational import PoolAccounting, run_accounted


def _admit(manifest: DatasetManifest, output: Path, config: TransformConfig) -> None:
    """Reject excessive local policy, ambiguous input identity and low disk space."""
    validate_names(manifest)
    manifest.limits.check_output(config, len(manifest.entries))
    # Conservative admission headroom, not a disk reservation or encoded-byte quota.
    required = 2 * config.width * config.height * len(manifest.entries) + 64 * 1024**2
    if shutil.disk_usage(output.parent.resolve(strict=True)).free < required:
        raise ProcessingError(
            "resource_limit", "Insufficient observed output disk headroom"
        )


def _item_state(outcome: ImageOutcome | None, not_started: bool) -> ItemState:
    """Classify received results separately from proof that work never started."""
    if outcome is not None and outcome.success:
        return "SUCCEEDED"
    if outcome is not None:
        return "FAILED"
    return "NOT_STARTED" if not_started else "UNKNOWN"


def _terminal_state(
    items: list[ItemResult], accounting: PoolAccounting
) -> tuple[ExecutionState, str | None]:
    """Give execution/image failures precedence over cooperative cancellation."""
    error = accounting.error_code
    if error is None and any(item.state == "FAILED" for item in items):
        error = "image_failure"
    if error is not None:
        return "FAILED", error
    if all(item.state == "SUCCEEDED" for item in items):
        return "COMPLETED", None
    if accounting.cancel_requested:
        return "CANCELLED", None
    return "FAILED", "worker_failure"


def _result(
    execution_id: str,
    manifest: DatasetManifest,
    workers: int,
    accounting: PoolAccounting,
    seconds: float,
) -> ExecutionResult:
    items = [
        ItemResult(
            entry.relative_path,
            _item_state(outcome, index in accounting.not_started),
            outcome,
        )
        for index, (entry, outcome) in enumerate(
            zip(manifest.entries, accounting.outcomes, strict=True)
        )
    ]
    state, error = _terminal_state(items, accounting)
    return ExecutionResult(
        execution_id,
        manifest.sha256,
        workers,
        state,
        tuple(items),
        accounting.cancel_requested,
        error,
        seconds,
    )


def run_local(
    root: Path,
    output: Path,
    config: TransformConfig,
    workers: int,
    *,
    limits: ResourceLimits = LOCAL_LIMITS,
    cancelled: Callable[[], bool] | None = None,
) -> ExecutionResult:
    """Authorize a local root explicitly and process its frozen image workload once.

    Args:
        root: Operator-selected server-local directory; never a browser-supplied path.
        output: New output directory outside the input root; parent must exist.
        config: Existing Pillow image semantics, with separate local pixel budget.
        workers: Explicit 1/2/4/8 spawn pool cap; no automatic selection.
        limits: Local safety policy, optionally narrowed for a smaller workload.
        cancelled: Stop submitting; drain work already dispatched before returning.

    Returns:
        An independent operational result with full known/unstarted/unknown accounting.

    Raises:
        ProcessingError: Unsafe inputs/output, excessive resources or low disk space.
        BenchmarkError: A cooperating scientific benchmark or import owns the host lock.
        WorkerExecutionError: Pool shutdown is unconfirmed; no terminal receipt is written.
        OSError: Receipt publication failed; acknowledged partial records remain inspectable.
        KeyboardInterrupt: Interrupted during admission, before operational work began.
    """
    if type(workers) is not int or workers not in (1, 2, 4, 8):
        raise ValueError("Worker count must be 1, 2, 4 or 8")
    validate_limits(limits)
    with experiment_lock():
        manifest = build_manifest(root, limits)
        _admit(manifest, output, config)
        directory = prepare_run(manifest, output, config)
        (directory / "records").mkdir()
        execution_id = str(uuid4())
        admission: dict[str, object] = {
            "schema_version": 1,
            "mode": "local-execution-v1",
            "execution_id": execution_id,
            "created_at": datetime.now(UTC).isoformat(),
            "workers": workers,
            "pipeline": "resize-grayscale-v1",
            "config": asdict(config),
            "limits": asdict(manifest.limits),
            "manifest": {
                "sha256": manifest.sha256,
                "total_bytes": manifest.total_bytes,
                "entries": [asdict(entry) for entry in manifest.entries],
            },
            "environment": asdict(capture_environment()),
        }
        publish_bytes(directory, "admission.json", encode(admission))
        start = perf_counter()
        accounting = run_accounted(
            manifest,
            directory,
            config,
            workers,
            cancelled=cancelled or (lambda: False),
            on_outcome=lambda index, outcome: publish_outcome(
                directory, execution_id, index, outcome
            ),
        )
        result = _result(
            execution_id, manifest, workers, accounting, perf_counter() - start
        )
        publish_bytes(directory, "execution.json", encode(result.to_dict()))
        return result

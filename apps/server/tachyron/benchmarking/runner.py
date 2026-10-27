# Copyright (c) 2026 Hans Valerie
"""Exclusive, reproducible orchestration over the existing bounded spawn runner."""

import hashlib
import logging
import sys
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import cast
from uuid import uuid4

from tachyron.benchmarking.exports import (
    encode_document,
    publish_exports,
    record_document,
)
from tachyron.benchmarking.models import (
    BenchmarkConfig,
    BenchmarkError,
    Context,
    Experiment,
    RunRecord,
    Telemetry,
    finite,
    make_plan,
)
from tachyron.benchmarking.monitoring import Sampler
from tachyron.benchmarking.ownership import experiment_lock
from tachyron.benchmarking.provenance import capture_environment
from tachyron.benchmarking.stats import throughput
from tachyron.processing import (
    BatchResult,
    DatasetManifest,
    TransformConfig,
    prepare_run,
)
from tachyron.processing.dataset import reject_link
from tachyron.processing.processor import publish_bytes
from tachyron.workers import run_parallel

logger = logging.getLogger(__name__)
DEFAULT_CONFIG = BenchmarkConfig()


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


def _check_environment(context: Context) -> None:
    current = capture_environment()
    # Free memory and unrelated working-tree changes vary without changing code.
    if replace(current, available_memory_mib=None, git_dirty=None) != replace(
        context.environment, available_memory_mib=None, git_dirty=None
    ):
        raise BenchmarkError(
            "Loaded code or machine configuration changed during the experiment"
        )


def _finish_sampler(
    sampler: Sampler,
    start: float | None,
    stop: float,
) -> Telemetry:
    pending = sys.exception()
    try:
        telemetry = sampler.stop(start if start is not None else stop, stop)
    except BaseException:
        if pending is None:
            raise
        logger.warning(
            "Resource sampler cleanup failed while an execution failure was already active"
        )
        telemetry = Telemetry(unavailable_reason="sampling_failed")
    return telemetry


def _remove_successful_images(batch: BatchResult) -> None:
    for outcome in batch.outcomes:
        expected = (
            hashlib.sha256(outcome.relative_path.encode("utf-8")).hexdigest() + ".png"
        )
        if outcome.output_name != expected:
            raise BenchmarkError(
                "Completed output does not match the owned filename mapping"
            )
        path = batch.output_dir / expected
        reject_link(path.lstat())
        path.unlink()


def _save_record(directory: Path, record: RunRecord) -> None:
    publish_bytes(
        directory / "records",
        f"{record.step.run_id}.json",
        encode_document(record_document(record)),
    )


def run_experiment(
    manifest: DatasetManifest,
    output_dir: Path,
    transform: TransformConfig,
    config: BenchmarkConfig = DEFAULT_CONFIG,
    *,
    cancelled: Callable[[], bool] | None = None,
    on_record: Callable[[RunRecord], None] | None = None,
) -> Experiment:
    """Run one controlled pool-scaling experiment and retain raw evidence.

    Args:
        manifest: Fixed ordered workload and existing resource limits.
        output_dir: New experiment directory outside the dataset; parent must exist.
        transform: One identical transform for all warm-ups and repetitions.
        config: Seeded schedule and observation policy; five repetitions by default.
        cancelled: Cooperative stop checked between fully drained pool batches.
        on_record: Observe published records outside timing; observer errors propagate.

    Returns:
        Successful evidence, or explicitly failed evidence after an image failure.

    Raises:
        BenchmarkError: Incompatible provenance, invalid timing, or another owner.
        ProcessingError: Invalid workload or output ownership.
        WorkerExecutionError: Catastrophic execution; original cause is preserved.
        KeyboardInterrupt: Interrupted; failed evidence is published best effort.
        OSError: Output publication or successful-image cleanup failed.
    """
    with experiment_lock():
        directory = prepare_run(manifest, output_dir, transform)
        context = Context(
            manifest.sha256,
            len(manifest.entries),
            manifest.total_bytes,
            transform,
            manifest.limits,
            capture_environment(),
            config.tiny,
            config.sampling_interval,
        )
        identifier = str(uuid4())
        plan = make_plan(config, identifier)
        (directory / "batches").mkdir()
        (directory / "records").mkdir()
        publish_bytes(
            directory,
            "plan.json",
            encode_document(
                {
                    "schema_version": 1,
                    "experiment_id": identifier,
                    "context_id": context.digest,
                    "context": asdict(context),
                    "config": asdict(config),
                    "plan": [asdict(step) for step in plan],
                    "status": "running",
                }
            ),
        )
        records: list[RunRecord] = []
        interrupted = False
        for step in plan:
            if cancelled is not None and cancelled():
                interrupted = True
                break
            sampler = Sampler(config.sampling_interval)
            batch: BatchResult | None = None
            start: float | None = None
            stamp = _stamp()
            record = RunRecord(
                step,
                context.digest,
                stamp,
                stamp,
                None,
                None,
                None,
                "failed",
                "execution_failure",
                Telemetry(),
            )
            code = "provenance_changed"
            try:
                try:
                    _check_environment(context)
                    code = "execution_failure"
                    sampler.start()
                    record = replace(record, started_at=_stamp())
                    start = perf_counter()
                    # Preserve the runtime guard if a pool violates its return contract.
                    batch = cast(
                        "BatchResult | None",
                        run_parallel(
                            manifest,
                            directory / "batches" / step.run_id,
                            transform,
                            step.workers,
                        ),
                    )
                finally:
                    stop = perf_counter() if start is not None else 0.0
                    elapsed = None if start is None else stop - start
                    record = replace(
                        record,
                        ended_at=_stamp(),
                        elapsed_seconds=elapsed if finite(elapsed) else None,
                        succeeded=batch.succeeded if batch is not None else None,
                        failed=batch.failed if batch is not None else None,
                        telemetry=Telemetry(unavailable_reason="sampling_failed"),
                    )
                    record = replace(
                        record, telemetry=_finish_sampler(sampler, start, stop)
                    )
                code = "invalid_duration"
                if batch is None:
                    raise BenchmarkError("Pool returned no completed batch")
                if throughput(batch.succeeded, record.elapsed_seconds) is None:
                    raise BenchmarkError(
                        "Elapsed duration cannot produce a finite image rate"
                    )
                code = "provenance_changed"
                _check_environment(context)
                code = "cleanup_failure"
                if batch.failed == 0:
                    _remove_successful_images(batch)
            except BaseException as error:
                status = (
                    "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
                )
                record = replace(record, status=status, error_code=code)
                records.append(record)
                failed = Experiment(
                    identifier, context, config, plan, tuple(records), status
                )
                try:
                    _save_record(directory, record)
                    publish_exports(failed, directory)
                except (OSError, BenchmarkError):
                    logger.warning(
                        "Failure evidence could not be fully published; no successful comparison is available"
                    )
                raise
            record = replace(
                record,
                status="failed" if batch.failed else "success",
                error_code="image_failure" if batch.failed else None,
            )
            records.append(record)
            _save_record(directory, record)
            if on_record is not None:
                on_record(record)
            if batch.failed:
                break
        status = "failed"
        if interrupted:
            status = "interrupted"
        elif len(records) == len(plan) and records[-1].status == "success":
            status = "success"
        experiment = Experiment(
            identifier,
            context,
            config,
            plan,
            tuple(records),
            status,
        )
        publish_exports(experiment, directory)
        return experiment

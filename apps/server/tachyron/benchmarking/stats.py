# Copyright (c) 2026 Hans Valerie
"""Pure, strict aggregation of complete compatible benchmark evidence."""

import math
import statistics
from dataclasses import dataclass, replace

from tachyron.benchmarking.models import (
    WORKERS,
    BenchmarkError,
    Context,
    Experiment,
    RunRecord,
    Step,
    finite,
    integer,
    make_plan,
)


@dataclass(frozen=True, slots=True)
class Distribution:
    """Sample statistics in the input's units, including undefined cases."""

    count: int
    mean: float | None
    median: float | None
    minimum: float | None
    maximum: float | None
    sample_stddev: float | None


def describe(values: tuple[float, ...]) -> Distribution:
    """Describe finite nonnegative observations; use sample deviation (ddof=1)."""
    if any(not finite(value) for value in values):
        raise BenchmarkError("Statistics require finite nonnegative observations")
    if not values:
        return Distribution(0, None, None, None, None, None)
    result = Distribution(
        len(values),
        statistics.mean(values),
        statistics.median(values),
        min(values),
        max(values),
        statistics.stdev(values) if len(values) > 1 else None,
    )
    if any(
        value is not None and not finite(value)
        for value in (result.mean, result.median, result.sample_stddev)
    ):
        raise BenchmarkError("Statistics exceed representable values")
    return result


def throughput(succeeded: int, seconds: float | None) -> float | None:
    """Return completed images/second, or null for undefined/nonfinite division."""
    if not integer(succeeded):
        raise BenchmarkError("Successful image count must be a nonnegative integer")
    if seconds is None or not finite(seconds) or seconds <= 0:
        return None
    value = succeeded / seconds
    return value if math.isfinite(value) else None


@dataclass(frozen=True, slots=True)
class WorkerSummary:
    """Measured statistics, or explicitly unavailable incomplete-comparison fields."""

    workers: int
    measured_count: int
    complete: bool
    elapsed: Distribution | None = None
    mean_throughput: float | None = None
    speedup: float | None = None
    efficiency_percent: float | None = None
    cpu_mean_core_percent: float | None = None
    rss_peak_mib: float | None = None
    cpu_runs_available: int = 0
    memory_runs_available: int = 0


def _validate_image_accounting(record: RunRecord, input_count: int) -> None:
    """Check known workload totals and reject incomplete successful results."""
    if (
        record.succeeded is not None
        and record.failed is not None
        and record.succeeded + record.failed != input_count
    ):
        raise BenchmarkError("Run counts do not account for the fixed workload")
    if record.status != "success":
        return
    if (
        record.succeeded != input_count
        or record.failed != 0
        or record.succeeded is None
        or throughput(record.succeeded, record.elapsed_seconds) is None
    ):
        raise BenchmarkError(
            "A successful run must have complete counts and valid timing"
        )


def _validate_record(record: RunRecord, step: Step, context: Context) -> None:
    """Check one scheduled attempt's identity, workload result and CPU coverage."""
    if record.step != step or record.context_id != context.digest:
        raise BenchmarkError(
            "Mixed context, duplicate, missing or reordered run records"
        )
    _validate_image_accounting(record, context.input_count)
    if record.telemetry.cpu_observed_seconds > 0 and (
        record.elapsed_seconds is None
        or record.telemetry.cpu_observed_seconds > record.elapsed_seconds
    ):
        raise BenchmarkError(
            "Observed CPU intervals exceed the run's measured duration"
        )


def validate(experiment: Experiment) -> None:
    """Reject mixed provenance, reordered/duplicate records or dishonest accounting."""
    if experiment.plan != make_plan(experiment.config, experiment.experiment_id):
        raise BenchmarkError("Run plan differs from its declared seeded schedule")
    if (
        experiment.context.tiny != experiment.config.tiny
        or experiment.context.sampling_interval != experiment.config.sampling_interval
    ):
        raise BenchmarkError("Configuration differs from the fixed comparison context")
    if len(experiment.records) > len(experiment.plan):
        raise BenchmarkError("More records than planned executions")
    if any(record.status != "success" for record in experiment.records[:-1]):
        raise BenchmarkError(
            "An experiment cannot execute more runs after its first failure"
        )
    for record, step in zip(experiment.records, experiment.plan, strict=False):
        _validate_record(record, step, experiment.context)
    complete = len(experiment.records) == len(experiment.plan) and all(
        record.status == "success" for record in experiment.records
    )
    if (
        experiment.status not in ("success", "failed", "interrupted")
        or (experiment.status == "success") != complete
    ):
        raise BenchmarkError("Experiment completion status contradicts its evidence")


def _summarize_worker(workers: int, records: tuple[RunRecord, ...]) -> WorkerSummary:
    """Aggregate one configuration's measured runs before baseline comparison."""
    durations = tuple(
        record.elapsed_seconds
        for record in records
        if record.elapsed_seconds is not None
    )
    elapsed = describe(durations)
    per_run_rates = tuple(
        throughput(record.succeeded, record.elapsed_seconds)
        for record in records
        if record.succeeded is not None
    )
    mean_throughput = statistics.mean(
        rate for rate in per_run_rates if rate is not None
    )
    cpu = tuple(
        record.telemetry
        for record in records
        if record.telemetry.cpu_mean_core_percent is not None
    )
    memory = tuple(
        record.telemetry.rss_peak_mib
        for record in records
        if record.telemetry.rss_peak_mib is not None
    )
    seconds = sum(sample.cpu_observed_seconds for sample in cpu)
    observed_cpu = (
        sum(
            sample.cpu_mean_core_percent * sample.cpu_observed_seconds
            for sample in cpu
            if sample.cpu_mean_core_percent is not None
        )
        / seconds
        if seconds > 0
        else None
    )
    return WorkerSummary(
        workers,
        elapsed.count,
        True,
        elapsed=elapsed,
        mean_throughput=mean_throughput,
        cpu_mean_core_percent=observed_cpu,
        rss_peak_mib=max(memory) if memory else None,
        cpu_runs_available=len(cpu),
        memory_runs_available=len(memory),
    )


def _compare_to_baseline(
    summary: WorkerSummary, baseline: float | None
) -> WorkerSummary:
    """Attach finite ratio-of-means scaling values without rounding intermediates."""
    duration = summary.elapsed.mean if summary.elapsed is not None else None
    if baseline is None or duration is None or duration <= 0:
        raise BenchmarkError("Baseline and selected durations must be positive")
    speedup = baseline / duration
    efficiency = speedup / summary.workers * 100
    if not math.isfinite(speedup) or not math.isfinite(efficiency):
        raise BenchmarkError("Speedup exceeds representable values")
    return replace(summary, speedup=speedup, efficiency_percent=efficiency)


def _complete_summaries(
    records: tuple[RunRecord, ...],
) -> tuple[WorkerSummary, ...]:
    """Aggregate complete evidence before comparing against the one-worker pool."""
    summaries = tuple(
        _summarize_worker(
            workers,
            tuple(
                record
                for record in records
                if record.step.workers == workers and not record.step.warmup
            ),
        )
        for workers in WORKERS
    )
    baseline = summaries[0].elapsed.mean if summaries[0].elapsed is not None else None
    return tuple(_compare_to_baseline(summary, baseline) for summary in summaries)


def summarize(
    experiment: Experiment, *, require_complete: bool = True
) -> tuple[WorkerSummary, ...]:
    """Compare means against the pool-based one-worker baseline, excluding warm-ups.

    Args:
        experiment: One fixed-context experiment with raw ordered evidence.
        require_complete: Reject failures, or return clearly incomplete null summaries.

    Returns:
        One worker summary per supported configuration, in numeric worker order.

    Raises:
        BenchmarkError: Invalid provenance/accounting or incomplete comparison.
    """
    validate(experiment)
    if experiment.status == "success":
        return _complete_summaries(experiment.records)
    if require_complete:
        raise BenchmarkError("An unsuccessful experiment is not a complete comparison")
    return tuple(
        WorkerSummary(
            workers,
            sum(
                not record.step.warmup and record.step.workers == workers
                for record in experiment.records
            ),
            False,
        )
        for workers in WORKERS
    )

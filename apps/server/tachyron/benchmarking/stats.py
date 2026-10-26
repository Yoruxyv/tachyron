# Copyright (c) 2026 Hans Valerie
"""Pure, strict aggregation of complete compatible benchmark evidence."""

import math
import statistics
from dataclasses import dataclass

from tachyron.benchmarking.models import (
    WORKERS,
    BenchmarkError,
    Experiment,
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
        if record.step != step or record.context_id != experiment.context.digest:
            raise BenchmarkError(
                "Mixed context, duplicate, missing or reordered run records"
            )
        if (
            record.succeeded is not None
            and record.failed is not None
            and record.succeeded + record.failed != experiment.context.input_count
        ):
            raise BenchmarkError("Run counts do not account for the fixed workload")
        if record.status == "success" and (
            record.succeeded != experiment.context.input_count
            or record.failed != 0
            or record.succeeded is None
            or throughput(record.succeeded, record.elapsed_seconds) is None
        ):
            raise BenchmarkError(
                "A successful run must have complete counts and valid timing"
            )
        if record.telemetry.cpu_observed_seconds > 0 and (
            record.elapsed_seconds is None
            or record.telemetry.cpu_observed_seconds > record.elapsed_seconds
        ):
            raise BenchmarkError(
                "Observed CPU intervals exceed the run's measured duration"
            )
    complete = len(experiment.records) == len(experiment.plan) and all(
        record.status == "success" for record in experiment.records
    )
    if (
        experiment.status not in ("success", "failed", "interrupted")
        or (experiment.status == "success") != complete
    ):
        raise BenchmarkError("Experiment completion status contradicts its evidence")


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
    if experiment.status != "success":
        if require_complete:
            raise BenchmarkError(
                "An unsuccessful experiment is not a complete comparison"
            )
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
    distributions: dict[int, Distribution] = {}
    rates: dict[int, float] = {}
    resources: dict[int, tuple[float | None, float | None, int, int]] = {}
    for workers in WORKERS:
        records = tuple(
            record
            for record in experiment.records
            if record.step.workers == workers and not record.step.warmup
        )
        durations = tuple(
            record.elapsed_seconds
            for record in records
            if record.elapsed_seconds is not None
        )
        distributions[workers] = describe(durations)
        per_run_rates = tuple(
            throughput(record.succeeded, record.elapsed_seconds)
            for record in records
            if record.succeeded is not None
        )
        rates[workers] = statistics.mean(
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
        resources[workers] = (
            observed_cpu,
            max(memory) if memory else None,
            len(cpu),
            len(memory),
        )
    baseline = distributions[1].mean
    results: list[WorkerSummary] = []
    for workers in WORKERS:
        duration = distributions[workers].mean
        if baseline is None or duration is None or duration <= 0:
            raise BenchmarkError("Baseline and selected durations must be positive")
        speedup = baseline / duration
        efficiency = speedup / workers * 100
        if not math.isfinite(speedup) or not math.isfinite(efficiency):
            raise BenchmarkError("Speedup exceeds representable values")
        results.append(
            WorkerSummary(
                workers,
                distributions[workers].count,
                True,
                distributions[workers],
                rates[workers],
                speedup,
                efficiency,
                *resources[workers],
            )
        )
    return tuple(results)

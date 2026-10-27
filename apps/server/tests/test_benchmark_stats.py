# Copyright (c) 2026 Hans Valerie
"""Benchmark arithmetic, compatibility, scheduling and resource aggregation."""

import math
from dataclasses import replace
from typing import Literal, cast

import pytest

from tachyron.benchmarking.models import (
    BenchmarkConfig,
    BenchmarkError,
    Telemetry,
    make_plan,
)
from tachyron.benchmarking.stats import WorkerSummary, describe, summarize, throughput
from tests.benchmark_helpers import example_experiment


def test_formulas_use_means_sample_deviation_and_completed_image_units() -> None:
    result = {row.workers: row for row in summarize(example_experiment())}
    baseline = result[1]
    assert baseline.measured_count == 2
    assert baseline.elapsed is not None
    assert baseline.elapsed.mean == baseline.elapsed.median == 12
    assert baseline.elapsed.minimum == 10
    assert baseline.elapsed.maximum == 14
    assert baseline.elapsed.sample_stddev == pytest.approx(math.sqrt(8))
    assert baseline.mean_throughput == pytest.approx((10 / 10 + 10 / 14) / 2)
    assert (baseline.speedup, baseline.efficiency_percent) == (1.0, 100.0)
    assert (result[2].speedup, result[2].efficiency_percent) == (2.0, 100.0)
    assert (result[8].speedup, result[8].efficiency_percent) == (6.0, 75.0)


def test_single_sample_stddev_and_absent_telemetry_remain_unavailable() -> None:
    experiment = example_experiment(1)
    assert all(
        record.telemetry.cpu_mean_core_percent is None for record in experiment.records
    )
    assert all(
        row.elapsed is not None and row.elapsed.sample_stddev is None
        for row in summarize(experiment)
    )
    assert describe(()).mean is None


@pytest.mark.parametrize("status", ["failed", "interrupted"])
def test_incomplete_comparisons_keep_counts_but_reject_partial_statistics(
    status: Literal["failed", "interrupted"],
) -> None:
    original = example_experiment()
    last = replace(
        original.records[5],
        status=status,
        error_code="execution_failure",
        elapsed_seconds=None,
        succeeded=None,
        failed=None,
        telemetry=Telemetry(),
    )
    experiment = replace(original, records=(*original.records[:5], last), status=status)
    rows = summarize(experiment, require_complete=False)
    counts: dict[int, int] = dict.fromkeys((1, 2, 4, 8), 0)
    counts[original.records[4].step.workers] += 1
    counts[last.step.workers] += 1
    assert rows == tuple(
        WorkerSummary(workers, count, False) for workers, count in counts.items()
    )
    with pytest.raises(BenchmarkError, match="unsuccessful"):
        summarize(experiment)
    mixed = replace(
        experiment,
        records=(
            replace(experiment.records[0], context_id="d" * 64),
            *experiment.records[1:],
        ),
    )
    with pytest.raises(BenchmarkError, match="Mixed context"):
        summarize(mixed, require_complete=False)


@pytest.mark.parametrize("seconds", [0.0, -1.0, math.nan, math.inf, 1e-320])
def test_invalid_or_unrepresentable_rates_are_unavailable(seconds: float) -> None:
    assert throughput(10, seconds) is None


def test_zero_success_and_small_representable_duration_are_not_fabricated() -> None:
    assert throughput(0, 1.0) == pytest.approx(0.0)
    assert throughput(1, 1e-100) == pytest.approx(1e100)
    with pytest.raises(BenchmarkError):
        throughput(-1, 1.0)


@pytest.mark.parametrize("invalid", [-1.0, math.nan, math.inf, True])
def test_invalid_statistical_inputs_are_rejected(invalid: float) -> None:
    with pytest.raises(BenchmarkError):
        describe((invalid,))


def test_complete_accounting_and_compatible_context_are_required() -> None:
    original = example_experiment()
    for changed in (
        replace(original, records=original.records[:-1], status="failed"),
        replace(
            original,
            records=(
                replace(original.records[0], context_id="d" * 64),
                *original.records[1:],
            ),
        ),
        replace(original, records=(original.records[0], *original.records)),
        replace(
            original,
            records=(
                replace(
                    original.records[0],
                    status="failed",
                    error_code="image_failure",
                    succeeded=9,
                    failed=1,
                ),
                *original.records[1:],
            ),
            status="failed",
        ),
    ):
        with pytest.raises(BenchmarkError):
            summarize(changed)


def test_seeded_interleaving_has_one_warmup_and_exact_repetition_accounting() -> None:
    experiment = example_experiment()
    assert make_plan(experiment.config, experiment.experiment_id) == experiment.plan
    assert [
        step.workers
        for step in make_plan(
            replace(experiment.config, seed=20), experiment.experiment_id
        )
    ] != [step.workers for step in experiment.plan]
    assert all(step.warmup for step in experiment.plan[:4])
    assert all(not step.warmup for step in experiment.plan[4:])
    for offset in range(0, len(experiment.plan), 4):
        assert {step.workers for step in experiment.plan[offset : offset + 4]} == {
            1,
            2,
            4,
            8,
        }


@pytest.mark.parametrize(
    "options",
    [
        {"repetitions": 1},
        {"repetitions": 21},
        {"repetitions": True},
        {"sampling_interval": 0},
        {"sampling_interval": math.nan},
        {"seed": True},
    ],
)
def test_invalid_configuration_rejects_before_execution(
    options: dict[str, object],
) -> None:
    with pytest.raises((ValueError, TypeError)):
        # Deliberately exercise invalid values crossing an untyped caller boundary.
        BenchmarkConfig(
            repetitions=cast("int", options.get("repetitions", 5)),
            seed=cast("int", options.get("seed", 2026)),
            sampling_interval=cast("float", options.get("sampling_interval", 0.1)),
            tiny=cast("bool", options.get("tiny", False)),
        )


def test_efficiency_overflow_is_rejected_before_export() -> None:
    original = example_experiment(1)
    changed = replace(
        original,
        records=tuple(
            replace(record, elapsed_seconds=1e308 if record.step.workers == 1 else 1.0)
            for record in original.records
        ),
    )
    with pytest.raises(BenchmarkError, match="representable"):
        summarize(changed)


def test_failed_run_cannot_be_followed_by_silently_included_work() -> None:
    original = example_experiment()
    first = replace(
        original.records[0],
        status="failed",
        error_code="image_failure",
        succeeded=9,
        failed=1,
    )
    changed = replace(original, records=(first, *original.records[1:]), status="failed")
    with pytest.raises(BenchmarkError, match="failure"):
        summarize(changed, require_complete=False)


def test_default_schedule_has_four_warmups_and_twenty_measurements() -> None:
    plan = make_plan(BenchmarkConfig(), "00000000-0000-4000-8000-000000000001")
    assert len(plan) == 24
    assert sum(step.warmup for step in plan) == 4
    for workers in (1, 2, 4, 8):
        assert sum(not step.warmup and step.workers == workers for step in plan) == 5


def test_resource_summaries_weight_available_cpu_intervals_and_exclude_warmups() -> (
    None
):
    original = example_experiment()
    records = tuple(
        replace(
            record,
            telemetry=Telemetry(
                observations=2,
                cpu_samples=1,
                memory_samples=2,
                cpu_observed_seconds=0.1 if record.step.repetition == 1 else 0.3,
                cpu_mean_core_percent=50 if record.step.repetition == 1 else 150,
                cpu_peak_core_percent=50 if record.step.repetition == 1 else 150,
                rss_peak_mib=999
                if record.step.warmup
                else (10 if record.step.repetition == 1 else 20),
            ),
        )
        for record in original.records
    )
    result = summarize(replace(original, records=records))
    assert all(row.cpu_mean_core_percent == pytest.approx(125) for row in result)
    assert all(row.rss_peak_mib == pytest.approx(20) for row in result)
    assert all(row.cpu_runs_available == 2 for row in result)


def test_resource_availability_counts_cpu_and_memory_independently() -> None:
    original = example_experiment()
    records = tuple(
        replace(
            record,
            telemetry=Telemetry(
                observations=2,
                cpu_samples=1,
                cpu_observed_seconds=0.3,
                cpu_mean_core_percent=0,
                cpu_peak_core_percent=0,
            )
            if record.step.repetition == 1
            else Telemetry(observations=1, memory_samples=1, rss_peak_mib=0),
        )
        for record in original.records
    )
    result = summarize(replace(original, records=records))
    assert all(row.cpu_mean_core_percent == 0 for row in result)
    assert all(row.rss_peak_mib == 0 for row in result)
    assert all(
        row.cpu_runs_available == row.memory_runs_available == 1 for row in result
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"succeeded": 9}, "Run counts do not account for the fixed workload"),
        (
            {"succeeded": 9, "failed": 1},
            "A successful run must have complete counts and valid timing",
        ),
        (
            {"succeeded": None, "failed": None},
            "A successful run must have complete counts and valid timing",
        ),
        (
            {"elapsed_seconds": None},
            "A successful run must have complete counts and valid timing",
        ),
        (
            {"elapsed_seconds": 0},
            "A successful run must have complete counts and valid timing",
        ),
        (
            {"elapsed_seconds": 1e-320},
            "A successful run must have complete counts and valid timing",
        ),
        (
            {"context_id": "d" * 64, "succeeded": 9},
            "Mixed context, duplicate, missing or reordered run records",
        ),
    ],
)
def test_workload_validation_preserves_error_precedence(
    changes: dict[str, object], message: str
) -> None:
    original = example_experiment()
    record = replace(original.records[0], **changes)
    experiment = replace(original, records=(record,), status="failed")
    with pytest.raises(BenchmarkError) as caught:
        summarize(experiment, require_complete=False)
    assert str(caught.value) == message


@pytest.mark.parametrize("elapsed", [None, 0.5, 1.0, 2.0])
def test_incomplete_attempts_validate_cpu_coverage_even_without_counts(
    elapsed: float | None,
) -> None:
    original = example_experiment()
    record = replace(
        original.records[0],
        status="interrupted",
        error_code="execution_failure",
        elapsed_seconds=elapsed,
        succeeded=None,
        failed=None,
        telemetry=Telemetry(
            observations=2,
            cpu_samples=1,
            cpu_observed_seconds=1,
            cpu_mean_core_percent=0,
            cpu_peak_core_percent=0,
        ),
    )
    experiment = replace(original, records=(record,), status="interrupted")
    if elapsed is None or elapsed < 1:
        with pytest.raises(BenchmarkError, match="Observed CPU intervals exceed"):
            summarize(experiment, require_complete=False)
    else:
        assert summarize(experiment, require_complete=False) == tuple(
            WorkerSummary(workers, 0, False) for workers in (1, 2, 4, 8)
        )


@pytest.mark.parametrize("baseline", [1e-100, 10.0, 1e100])
def test_scaling_preserves_small_large_and_slower_worker_comparisons(
    baseline: float,
) -> None:
    original = example_experiment(1)
    experiment = replace(
        original,
        records=tuple(
            replace(record, elapsed_seconds=baseline * record.step.workers)
            for record in original.records
        ),
    )
    rows = summarize(experiment)
    assert [row.speedup for row in rows] == [1, 0.5, 0.25, 0.125]
    assert [row.efficiency_percent for row in rows] == [100, 25, 6.25, 1.5625]

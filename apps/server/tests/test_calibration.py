# Copyright (c) 2026 Hans Valerie
"""Deterministic synthetic timing fixtures, never recorded performance evidence."""

from dataclasses import replace

import pytest

from tachyron.benchmarking.models import Telemetry
from tachyron.execution.calibration import CalibrationPolicy, Trial, select_workers


def observations() -> Telemetry:
    """Provide complete synthetic resource observations for pure selection tests."""
    return Telemetry(
        observations=3,
        cpu_samples=2,
        memory_samples=3,
        missed_observations=0,
        cpu_observed_seconds=0.2,
        cpu_mean_core_percent=150.0,
        cpu_peak_core_percent=200.0,
        rss_peak_mib=100.0,
        observed_descendants_peak=2,
        truncated=False,
        unavailable_reason=None,
    )


def trials(policy: CalibrationPolicy) -> tuple[Trial, ...]:
    """Create exact two-size timings with explicit nonzero startup costs."""
    result: list[Trial] = []
    for workers, slope in ((1, 0.1), (2, 0.06), (4, 0.03), (8, 0.04)):
        for size in (policy.sample_files // 2, policy.sample_files):
            for repetition in range(policy.repetitions):
                result.append(
                    Trial(
                        len(result),
                        workers,
                        size,
                        repetition,
                        "0" * 64,
                        "synthetic",
                        "COMPLETED",
                        None,
                        0.25 + size * slope,
                        observations(),
                    )
                )
    return tuple(result)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sample_files", 15),
        ("sample_files", 17),
        ("sample_files", True),
        ("repetitions", 2),
        ("max_seconds", float("nan")),
        ("max_seconds", 301),
        ("fallback_workers", 3),
        ("seed", -1),
    ],
)
def test_policy_rejects_unbounded_or_ambiguous_inputs(
    field: str, value: object
) -> None:
    with pytest.raises(ValueError, match=r"Calibration|Fallback"):
        replace(CalibrationPolicy(), **{field: value})


def test_selection_pays_calibration_and_counts_startup_once() -> None:
    policy = CalibrationPolicy()
    result = select_workers(trials(policy), 1000, (1, 2, 4, 8), policy, 10, 2)
    assert result.status == "SELECTED"
    assert result.workers == 4
    assert result.estimated_workflow_seconds == pytest.approx(42.25)
    assert result.estimated_fixed_seconds == pytest.approx(102.25)
    assert result.estimates[2].startup_seconds == pytest.approx(0.25)


def test_calibration_can_cost_more_than_it_saves() -> None:
    policy = CalibrationPolicy()
    result = select_workers(trials(policy), 1000, (1, 2, 4, 8), policy, 80, 2)
    assert result.status == "SELECTED"
    assert result.workers == 4
    assert result.amortized_gain_supported is False
    assert result.estimated_workflow_seconds is not None
    assert result.estimated_fixed_seconds is not None
    assert result.estimated_workflow_seconds > result.estimated_fixed_seconds
    assert result.estimated_workflow_seconds < 2 + 80 + 100.25


@pytest.mark.parametrize(
    "failure", ["noisy", "failed", "missing", "rss", "negative_slope"]
)
def test_inadequate_evidence_abstains(failure: str) -> None:
    policy = CalibrationPolicy()
    evidence = list(trials(policy))
    if failure == "noisy":
        evidence[0] = replace(evidence[0], seconds=20)
    elif failure == "failed":
        evidence[0] = replace(evidence[0], state="FAILED", error_code="worker_failure")
    elif failure == "missing":
        evidence = evidence[1:]
    elif failure == "rss":
        evidence[0] = replace(
            evidence[0], resources=Telemetry(unavailable_reason="setup_failed")
        )
    else:
        evidence = [
            replace(item, seconds=2.0) if item.workers == 1 else item
            for item in evidence
        ]
    result = select_workers(tuple(evidence), 1000, (1, 2, 4, 8), policy, 1, 1)
    assert result.status == "INCONCLUSIVE"
    assert result.workers == 1


def test_fewer_cores_change_feasibility_without_predicting_a_winner() -> None:
    policy = CalibrationPolicy()
    evidence = tuple(item for item in trials(policy) if item.workers <= 2)
    result = select_workers(evidence, 1000, (1, 2), policy, 1, 1)
    assert result.status == "SELECTED"
    assert result.workers == 2

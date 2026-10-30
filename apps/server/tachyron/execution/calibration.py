# Copyright (c) 2026 Hans Valerie
"""Versioned, conservative estimates for local operational worker selection."""

import hashlib
from dataclasses import dataclass
from statistics import median
from typing import Literal

from tachyron.benchmarking.models import Telemetry, finite, integer

WORKERS = (1, 2, 4, 8)
PROFILE_BYTES = 512 * 1024**2
SAMPLE_PIXELS = 64 * 1024**2
METHOD: dict[str, object] = {
    "sampling": "format-pixels-bytes-equal-population-hash-v1",
    "model": "two-batch-sizes-linear-observed-ranges-v1",
    "objective": "minimize estimated total completion time; pay calibration before claiming gain",
    "profile_byte_budget": PROFILE_BYTES,
    "sample_pixel_budget": SAMPLE_PIXELS,
    "maximum_relative_range": 0.25,
    "minimum_amortized_gain_fraction": 0.1,
    "minimum_execution_gain_fraction": 0.1,
    "selection": "minimize-remaining-time-with-sunk-calibration-v2",
    "available_memory_fraction": 0.5,
    "memory_reserve_mib": 512,
    "sampling_interval_seconds": 0.1,
    "warmups": 0,
}


@dataclass(frozen=True)
class CalibrationPolicy:
    """Bound trial count and admission time; running work still drains on stopping."""

    sample_files: int = 32
    repetitions: int = 3
    max_seconds: float = 60.0
    seed: int = 2026
    fallback_workers: int = 1
    max_trial_images: int = 576

    def __post_init__(self) -> None:
        if (
            not integer(self.sample_files, 16)
            or self.sample_files > 64
            or self.sample_files % 2
        ):
            raise ValueError("Calibration sample_files must be even and 16-64")
        if not integer(self.repetitions, 3) or self.repetitions > 5:
            raise ValueError("Calibration repetitions must be 3-5")
        if not finite(self.max_seconds, 1) or self.max_seconds > 300:
            raise ValueError("Calibration max_seconds must be 1-300")
        if not integer(self.max_trial_images, 1) or self.max_trial_images > 1920:
            raise ValueError("Calibration max_trial_images must be 1-1920")
        if not integer(self.seed) or self.seed > 2**32 - 1:
            raise ValueError("Calibration seed must be an unsigned 32-bit integer")
        if (
            type(self.fallback_workers) is not int
            or self.fallback_workers not in WORKERS
        ):
            raise ValueError("Fallback workers must be 1, 2, 4 or 8")


DEFAULT_CALIBRATION_POLICY = CalibrationPolicy()


@dataclass(frozen=True)
class Trial:
    """One actual sample execution, including startup, setup and receipt publication."""

    index: int
    workers: int
    sample_count: int
    repetition: int
    sample_sha256: str
    execution_id: str
    state: str
    error_code: str | None
    seconds: float
    resources: Telemetry
    feasible_workers: tuple[int, ...] = ()
    memory_budget_mib: float | None = None


@dataclass(frozen=True)
class Estimate:
    """Linear sample extrapolation and observed-range bounds, not confidence intervals."""

    workers: int
    seconds: float
    lower_seconds: float
    upper_seconds: float
    startup_seconds: float
    seconds_per_image: float


@dataclass(frozen=True)
class Selection:
    """Measured selection or explicit inconclusive fallback, with no optimality claim."""

    status: Literal["SELECTED", "INCONCLUSIVE"]
    workers: int
    reason: str
    estimates: tuple[Estimate, ...] = ()
    estimated_workflow_seconds: float | None = None
    estimated_fixed_seconds: float | None = None
    amortized_gain_supported: bool | None = None


def _estimate(
    workers: int, trials: tuple[Trial, ...], count: int, policy: CalibrationPolicy
) -> Estimate | None:
    sizes = (policy.sample_files // 2, policy.sample_files)
    if any(
        {
            trial.repetition
            for trial in trials
            if trial.workers == workers and trial.sample_count == size
        }
        != set(range(policy.repetitions))
        for size in sizes
    ):
        return None
    groups = [
        [
            trial.seconds
            for trial in trials
            if trial.workers == workers and trial.sample_count == size
        ]
        for size in sizes
    ]
    for values in groups:
        if len(values) != policy.repetitions or any(
            not finite(value, 0.000001) for value in values
        ):
            return None
        if (max(values) - min(values)) / median(values) > 0.25:
            return None
    small, large = groups
    distance = sizes[1] - sizes[0]
    slope = (median(large) - median(small)) / distance
    lower = (min(large) - max(small)) / distance
    upper = (max(large) - min(small)) / distance
    startup = median(small) - slope * sizes[0]
    if lower <= 0 or startup < 0:
        return None
    remaining = count - sizes[1]
    return Estimate(
        workers,
        median(large) + remaining * slope,
        min(large) + remaining * lower,
        max(large) + remaining * upper,
        startup,
        slope,
    )


def select_workers(
    trials: tuple[Trial, ...],
    count: int,
    candidates: tuple[int, ...],
    policy: CalibrationPolicy,
    calibration_seconds: float,
    setup_seconds: float,
) -> Selection:
    """Minimize projected completion time among empirically supported candidates.

    Startup is fitted once using half/full sample batches. Range bounds must
    support a 10% execution gain over the fixed fallback. Calibration is already
    spent at this decision; report its amortization separately instead of using
    sunk cost to choose slower remaining work. Bounds are not confidence intervals.
    """
    estimates = tuple(
        value
        for worker in candidates
        if (value := _estimate(worker, trials, count, policy)) is not None
    )
    fallback = next(
        (value for value in estimates if value.workers == policy.fallback_workers), None
    )
    if len(trials) != len(candidates) * 2 * policy.repetitions or any(
        trial.state != "COMPLETED"
        or trial.resources.rss_peak_mib is None
        or trial.resources.truncated
        or trial.resources.unavailable_reason is not None
        for trial in trials
    ):
        return Selection(
            "INCONCLUSIVE",
            policy.fallback_workers,
            "incomplete_observations",
            estimates,
        )
    if fallback is None or len(estimates) < 2:
        return Selection(
            "INCONCLUSIVE",
            policy.fallback_workers,
            "inadequate_or_noisy_measurements",
            estimates,
        )
    supported = [
        value
        for value in estimates
        if value.workers != policy.fallback_workers
        and value.upper_seconds < 0.9 * fallback.lower_seconds
    ]
    fixed = setup_seconds + fallback.seconds
    if not supported:
        return Selection(
            "INCONCLUSIVE",
            policy.fallback_workers,
            "no_clear_execution_gain",
            estimates,
            setup_seconds + calibration_seconds + fallback.seconds,
            fixed,
            False,
        )
    best = min(supported, key=lambda value: (value.seconds, value.workers))
    projected = setup_seconds + calibration_seconds + best.seconds
    return Selection(
        "SELECTED",
        best.workers,
        "measured_execution_gain",
        estimates,
        projected,
        fixed,
        calibration_seconds + best.upper_seconds < 0.9 * fallback.lower_seconds,
    )


METHOD_V3: dict[str, object] = {
    **METHOD,
    "selection": "complete-measured-candidates-with-sunk-calibration-v3",
    "trial_order": "fallback-then-descending-feasible-seeded-blocks-v3",
    "maximum_workload_image_fraction": 0.5,
    "maximum_estimated_fixed_time_fraction": 0.5,
    "next_block_reserve": "largest-observed-complete-block-cost",
}


def trial_image_budget(count: int, policy: CalibrationPolicy) -> int:
    """Cap attempted sample-image work without reducing repetitions or coverage."""
    return min(policy.max_trial_images, count // 2)


def candidate_schedule(
    candidates: tuple[int, ...], policy: CalibrationPolicy
) -> tuple[tuple[int, int, int], ...]:
    """Test complete candidates, fallback first, then the widest feasible contrast."""
    order = sorted(
        candidates, key=lambda worker: (worker != policy.fallback_workers, -worker)
    )
    result: list[tuple[int, int, int]] = []
    for worker in order:
        steps = [
            (worker, size, repetition)
            for repetition in range(policy.repetitions)
            for size in (policy.sample_files // 2, policy.sample_files)
        ]
        steps.sort(
            key=lambda step: hashlib.sha256(f"{policy.seed}:{step}".encode()).digest()
        )
        result.extend(steps)
    return tuple(result)


def select_progressive_workers(
    trials: tuple[Trial, ...],
    count: int,
    candidates: tuple[int, ...],
    policy: CalibrationPolicy,
    calibration_seconds: float,
    setup_seconds: float,
) -> Selection:
    """Apply unchanged evidence gates to complete configurations; retain all trials.

    Incomplete configurations provide no fit. Their cost remains in calibration.
    Untested candidates are unknown, not evidence of a worse configuration.
    """
    required = {
        (size, repetition)
        for size in (policy.sample_files // 2, policy.sample_files)
        for repetition in range(policy.repetitions)
    }
    complete = tuple(
        worker
        for worker in candidates
        if len([trial for trial in trials if trial.workers == worker]) == len(required)
        and {
            (trial.sample_count, trial.repetition)
            for trial in trials
            if trial.workers == worker
        }
        == required
    )
    evidence = tuple(trial for trial in trials if trial.workers in complete)
    if any(
        trial.memory_budget_mib is not None
        and trial.resources.rss_peak_mib is not None
        and trial.resources.rss_peak_mib > trial.memory_budget_mib
        for trial in evidence
    ):
        return Selection(
            "INCONCLUSIVE", policy.fallback_workers, "incomplete_observations"
        )
    return select_workers(
        evidence, count, complete, policy, calibration_seconds, setup_seconds
    )


METHOD_V4: dict[str, object] = {
    **METHOD_V3,
    "selection": "complete-measured-candidates-with-sunk-calibration-v4",
    "time_cap_activation": "after-first-supported-comparison-v4",
}


def select_supported_workers(
    trials: tuple[Trial, ...],
    count: int,
    candidates: tuple[int, ...],
    policy: CalibrationPolicy,
    calibration_seconds: float,
    setup_seconds: float,
) -> Selection:
    """Keep completed evidence when another candidate is interrupted by its budget.

    Failed/unknown executions cannot support automatic selection. A cancelled
    configuration is incomplete even when its last trial identity was recorded.
    Historical v3 inspection retains its original selector separately.
    """
    if any(trial.state not in {"COMPLETED", "CANCELLED"} for trial in trials):
        return Selection(
            "INCONCLUSIVE", policy.fallback_workers, "incomplete_observations"
        )
    eligible = tuple(
        worker
        for worker in candidates
        if all(
            trial.state == "COMPLETED" for trial in trials if trial.workers == worker
        )
    )
    evidence = tuple(trial for trial in trials if trial.workers in eligible)
    return select_progressive_workers(
        evidence, count, eligible, policy, calibration_seconds, setup_seconds
    )

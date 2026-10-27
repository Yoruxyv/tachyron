# Copyright (c) 2026 Hans Valerie
"""Counter priming, tree churn, missing data and bounded sampler lifecycle."""

import math
from unittest.mock import Mock

import pytest

from tachyron.benchmarking import monitoring
from tachyron.benchmarking.models import BenchmarkError, Telemetry
from tachyron.benchmarking.monitoring import (
    ProcessSample,
    Sampler,
    Snapshot,
    summarize_samples,
)


def snapshot(
    at: float, cpu: float, *, child: bool = True, complete: bool = True
) -> Snapshot:
    samples = (ProcessSample(1, 10.0, cpu, 2 * 1024**2),)
    if child:
        samples += (ProcessSample(2, 11.0, cpu * 2, 3 * 1024**2),)
    return Snapshot(at, samples, complete)


def test_cpu_core_equivalents_exceed_100_and_rss_is_tree_mib() -> None:
    result = summarize_samples((snapshot(0, 0), snapshot(1, 1), snapshot(2, 3)), 0, 2)
    assert result.cpu_mean_core_percent == pytest.approx(450)
    assert result.cpu_peak_core_percent == pytest.approx(600)
    assert result.cpu_observed_seconds == pytest.approx(2)
    assert result.rss_peak_mib == pytest.approx(5)
    assert result.cpu_samples == 2
    assert result.memory_samples == 3


def test_irregular_cpu_intervals_weight_observed_zero_and_active_time() -> None:
    observations = (
        snapshot(0, 10, child=False),
        snapshot(1, 10, child=False),
        snapshot(3, 12, child=False),
    )
    idle = summarize_samples(observations[:2], 0, 1)
    assert idle.cpu_mean_core_percent == 0
    assert idle.cpu_samples == 1
    result = summarize_samples(observations, 0, 3)
    assert result.cpu_mean_core_percent == pytest.approx(200 / 3)
    assert result.cpu_peak_core_percent == 100
    assert result.cpu_observed_seconds == 3
    assert result.cpu_samples == 2


@pytest.mark.parametrize(
    ("at", "cpu"),
    [(0, 1), (-1, 1), (1, -1), (1, math.nan), (1, math.inf), (1e-308, 1e308)],
)
def test_invalid_cpu_intervals_preserve_readable_memory(at: float, cpu: float) -> None:
    result = summarize_samples(
        (snapshot(0, 0, child=False), snapshot(at, cpu, child=False)), -1, 2
    )
    assert result.cpu_mean_core_percent is None
    assert result.cpu_peak_core_percent is None
    assert result.cpu_observed_seconds == 0
    assert result.cpu_samples == 0
    assert result.missed_observations == 1
    assert result.memory_samples == 2
    assert result.rss_peak_mib == 2


def test_birth_exit_and_unreadable_intervals_do_not_fabricate_zero_cpu() -> None:
    observations = (
        snapshot(0, 0, child=False),
        snapshot(1, 1),
        snapshot(2, 2, complete=False),
        snapshot(3, 3, child=False),
    )
    result = summarize_samples(observations, 0, 3)
    assert result.cpu_samples == 0
    assert result.cpu_mean_core_percent is None
    assert result.cpu_peak_core_percent is None
    assert result.memory_samples == 3
    assert result.missed_observations > 0


def test_pid_reuse_is_a_new_process_and_timing_window_is_respected() -> None:
    reused = Snapshot(2, (ProcessSample(1, 99.0, 4, 1024),), True)
    result = summarize_samples(
        (snapshot(-1, 0), snapshot(1, 1, child=False), reused, snapshot(10, 99)), 0, 3
    )
    assert result.observations == 2
    assert result.cpu_samples == 0
    assert result.cpu_mean_core_percent is None
    assert result.rss_peak_mib == pytest.approx(2)


def test_missing_observations_and_sampling_truncation_are_explicit() -> None:
    result = summarize_samples((), 0, 1, truncated=True, reason="sample_limit")
    assert result.rss_peak_mib is None
    assert result.cpu_mean_core_percent is None
    assert result.truncated is True
    assert result.unavailable_reason == "sample_limit"


def test_sampling_recovers_after_unreadable_birth_reuse_and_exit() -> None:
    observations = (
        snapshot(-1, 99),
        snapshot(0, 0, child=False),
        snapshot(1, 0.5, child=False),
        Snapshot(2, (), False),
        snapshot(3, 1, child=False),
        snapshot(4, 1.5),
        snapshot(5, 2),
        Snapshot(
            6,
            (
                ProcessSample(1, 10, 2.5, 2 * 1024**2),
                ProcessSample(2, 99, 0, 3 * 1024**2),
            ),
            True,
        ),
        Snapshot(
            7,
            (
                ProcessSample(1, 10, 3, 2 * 1024**2),
                ProcessSample(2, 99, 0.5, 3 * 1024**2),
            ),
            True,
        ),
        snapshot(8, 3.5, child=False),
        snapshot(9, 4, child=False),
        snapshot(10, 99),
    )
    assert summarize_samples(
        observations, 0, 9, truncated=True, reason="sample_limit"
    ) == Telemetry(
        observations=10,
        cpu_samples=4,
        memory_samples=9,
        missed_observations=4,
        cpu_observed_seconds=4,
        cpu_mean_core_percent=87.5,
        cpu_peak_core_percent=150,
        rss_peak_mib=5,
        observed_descendants_peak=1,
        truncated=True,
        unavailable_reason="sample_limit",
    )


def test_failed_sampler_start_is_unavailable_without_preventing_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        monitoring.threading.Thread, "start", Mock(side_effect=OSError("private"))
    )
    sampler = Sampler(0.1)
    sampler.start()
    result = sampler.stop(0, 1)
    assert result.unavailable_reason == "setup_failed"
    assert result.cpu_mean_core_percent is None
    assert result.rss_peak_mib is None


def test_cpu_sample_count_requires_positive_observed_time() -> None:
    with pytest.raises(BenchmarkError, match="observed"):
        Telemetry(
            observations=1,
            cpu_samples=1,
            cpu_mean_core_percent=10,
            cpu_peak_core_percent=10,
        )


def test_sampler_storage_stops_at_a_declared_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(monitoring, "MAX_OBSERVATIONS", 2)
    sampler = Sampler(0.01)
    monkeypatch.setattr(sampler, "_snapshot", Mock(return_value=snapshot(0.5, 0)))
    monkeypatch.setattr(sampler._stop, "wait", Mock(return_value=False))
    sampler._run()
    result = sampler.stop(0, 1)
    assert result.observations == 2
    assert result.truncated is True
    assert result.unavailable_reason == "sample_limit"

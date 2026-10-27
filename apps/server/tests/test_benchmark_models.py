# Copyright (c) 2026 Hans Valerie
"""Characterize provenance and run-record trust boundaries and error precedence."""

import math
from dataclasses import replace

import pytest

from tachyron.benchmarking.models import BenchmarkError
from tests.benchmark_helpers import example_experiment


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"python_version": ""}, "Runtime provenance must contain nonempty strings"),
        ({"architecture": 64}, "Runtime provenance must contain nonempty strings"),
        ({"host_id": "B" * 64}, "Invalid host or loaded-source identity"),
        ({"source_hash": "c" * 63}, "Invalid host or loaded-source identity"),
        ({"logical_cpus": True}, "CPU counts must be positive or unavailable"),
        ({"logical_cpus": 0}, "CPU counts must be positive or unavailable"),
        ({"physical_cpus": -1}, "CPU counts must be positive or unavailable"),
        ({"physical_cpus": 2.0}, "CPU counts must be positive or unavailable"),
        ({"total_memory_mib": True}, "Memory provenance must be finite or unavailable"),
        ({"total_memory_mib": -1}, "Memory provenance must be finite or unavailable"),
        (
            {"available_memory_mib": math.nan},
            "Memory provenance must be finite or unavailable",
        ),
        (
            {"available_memory_mib": math.inf},
            "Memory provenance must be finite or unavailable",
        ),
        ({"cpu_affinity": (True,)}, "Invalid CPU affinity"),
        ({"cpu_affinity": (-1,)}, "Invalid CPU affinity"),
        ({"cpu_affinity": (1.0,)}, "Invalid CPU affinity"),
        ({"git_commit": "A" * 40}, "Invalid Git commit identity"),
        ({"git_commit": "a" * 39}, "Invalid Git commit identity"),
        ({"git_dirty": 1}, "Git working-tree state must be boolean or unavailable"),
        ({"cpu_model": 123}, "Invalid CPU description"),
        (
            {"python_version": "", "host_id": "bad"},
            "Runtime provenance must contain nonempty strings",
        ),
        (
            {"host_id": "bad", "logical_cpus": False},
            "Invalid host or loaded-source identity",
        ),
        (
            {"logical_cpus": False, "total_memory_mib": -1},
            "CPU counts must be positive or unavailable",
        ),
        (
            {"total_memory_mib": -1, "cpu_affinity": (-1,)},
            "Memory provenance must be finite or unavailable",
        ),
        ({"cpu_affinity": (-1,), "git_commit": "bad"}, "Invalid CPU affinity"),
        ({"git_commit": "bad", "git_dirty": 1}, "Invalid Git commit identity"),
        (
            {"git_dirty": 1, "cpu_model": 123},
            "Git working-tree state must be boolean or unavailable",
        ),
    ],
)
def test_environment_rejects_invalid_provenance_in_original_order(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(BenchmarkError) as caught:
        replace(example_experiment().context.environment, **changes)
    assert str(caught.value) == message


@pytest.mark.parametrize(
    "changes",
    [
        {"cpu_model": "", "cpu_affinity": ()},
        {"logical_cpus": 1, "physical_cpus": 2, "cpu_affinity": (0, 0, 999)},
        {"total_memory_mib": 0, "available_memory_mib": 0.0},
        {"total_memory_mib": None, "available_memory_mib": None, "git_dirty": None},
        {"git_commit": "a" * 40, "git_dirty": False},
    ],
)
def test_environment_preserves_supported_optional_and_boundary_values(
    changes: dict[str, object],
) -> None:
    result = replace(example_experiment().context.environment, **changes)
    assert all(getattr(result, key) is value for key, value in changes.items())


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"context_id": "bad"}, BenchmarkError, "Invalid run context identity"),
        (
            {"started_at": "2026-01-01T00:00:00"},
            BenchmarkError,
            "Run timestamps must carry a timezone",
        ),
        ({"ended_at": "invalid"}, ValueError, "Invalid isoformat string: 'invalid'"),
        ({"elapsed_seconds": True}, BenchmarkError, "Invalid elapsed duration"),
        ({"elapsed_seconds": -1}, BenchmarkError, "Invalid elapsed duration"),
        ({"elapsed_seconds": math.nan}, BenchmarkError, "Invalid elapsed duration"),
        ({"elapsed_seconds": math.inf}, BenchmarkError, "Invalid elapsed duration"),
        ({"succeeded": True}, BenchmarkError, "Invalid image accounting"),
        ({"failed": -1}, BenchmarkError, "Invalid image accounting"),
        ({"succeeded": 1.0}, BenchmarkError, "Invalid image accounting"),
        ({"succeeded": None}, BenchmarkError, "Invalid image accounting"),
        ({"failed": None}, BenchmarkError, "Invalid image accounting"),
        ({"status": "unknown"}, BenchmarkError, "Invalid run status"),
        ({"error_code": "unknown"}, BenchmarkError, "Unsupported failure code"),
        (
            {"error_code": "image_failure"},
            BenchmarkError,
            "Run status contradicts its error code",
        ),
        ({"status": "failed"}, BenchmarkError, "Run status contradicts its error code"),
        (
            {"actual_workers": 0},
            BenchmarkError,
            "This sampler cannot determine actual worker utilization",
        ),
        (
            {"context_id": "bad", "started_at": "invalid"},
            BenchmarkError,
            "Invalid run context identity",
        ),
        (
            {"ended_at": "invalid", "elapsed_seconds": -1},
            ValueError,
            "Invalid isoformat string: 'invalid'",
        ),
        (
            {"elapsed_seconds": -1, "succeeded": True},
            BenchmarkError,
            "Invalid elapsed duration",
        ),
        (
            {"failed": None, "status": "unknown"},
            BenchmarkError,
            "Invalid image accounting",
        ),
        (
            {"status": "unknown", "error_code": "unknown"},
            BenchmarkError,
            "Invalid run status",
        ),
        (
            {"error_code": "unknown", "actual_workers": 1},
            BenchmarkError,
            "Unsupported failure code",
        ),
        (
            {"status": "failed", "actual_workers": 1},
            BenchmarkError,
            "Run status contradicts its error code",
        ),
    ],
)
def test_run_record_rejects_invalid_evidence_in_original_order(
    changes: dict[str, object], error: type[ValueError], message: str
) -> None:
    with pytest.raises(error) as caught:
        replace(example_experiment().records[0], **changes)
    assert type(caught.value) is error
    assert str(caught.value) == message


@pytest.mark.parametrize(
    "changes",
    [
        {"elapsed_seconds": 0},
        {"elapsed_seconds": None, "succeeded": None, "failed": None},
        {"elapsed_seconds": 1e-100, "succeeded": 0, "failed": 0},
        {"ended_at": "2025-01-01T00:00:00-03:30"},
        {
            "status": "failed",
            "error_code": "image_failure",
            "succeeded": 9,
            "failed": 1,
        },
        {
            "status": "interrupted",
            "error_code": "execution_failure",
            "succeeded": None,
            "failed": None,
        },
    ],
)
def test_run_record_retains_evidence_without_claiming_a_valid_comparison(
    changes: dict[str, object],
) -> None:
    result = replace(example_experiment().records[0], **changes)
    assert all(getattr(result, key) is value for key, value in changes.items())

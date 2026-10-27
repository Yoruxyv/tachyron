# Copyright (c) 2026 Hans Valerie
"""Benchmark runner ownership, failure accounting, cleanup and spawn parity."""

import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from tachyron.benchmarking import ownership, runner
from tachyron.benchmarking.exports import read_experiment
from tachyron.benchmarking.models import BenchmarkConfig, BenchmarkError
from tachyron.benchmarking.ownership import experiment_lock
from tachyron.benchmarking.stats import summarize
from tachyron.processing import (
    BatchResult,
    DatasetManifest,
    TransformConfig,
    run_sequential,
)
from tachyron.workers import WorkerExecutionError
from tests.benchmark_helpers import example_experiment, make_manifest


def test_cooperative_lock_rejects_a_second_owner_and_releases_after_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ownership.tempfile, "gettempdir", Mock(return_value=str(tmp_path))
    )

    def second_owner() -> None:
        with experiment_lock():
            pytest.fail("Concurrent owner admitted")

    with experiment_lock(), pytest.raises(BenchmarkError, match="Another benchmark"):
        second_owner()

    def fail_owner() -> None:
        with experiment_lock():
            raise RuntimeError("owner failed")

    with pytest.raises(RuntimeError, match="owner failed"):
        fail_owner()
    with experiment_lock():
        assert list(tmp_path.glob("tachyron-benchmark-*.lock"))


def test_runner_accounts_warmups_keeps_unknown_files_and_bounds_retained_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "input")
    calls: list[int] = []

    def execute(
        workload: DatasetManifest,
        directory: Path,
        transform: TransformConfig,
        workers: int,
    ) -> BatchResult:
        calls.append(workers)
        assert workload == manifest
        result = run_sequential(workload, directory, transform)
        (directory / "keep.txt").write_bytes(b"unrelated sentinel")
        return result

    monkeypatch.setattr(runner, "run_parallel", execute)
    monkeypatch.setattr(
        runner,
        "capture_environment",
        Mock(return_value=example_experiment().context.environment),
    )
    result = runner.run_experiment(
        manifest,
        tmp_path / "experiment",
        TransformConfig(2, 2),
        BenchmarkConfig(repetitions=1, tiny=True),
    )
    assert result.status == "success"
    assert len(result.records) == 8
    assert len(calls) == 8
    assert set(calls) == {1, 2, 4, 8}
    assert not list((tmp_path / "experiment").rglob("*.png"))
    assert len(list((tmp_path / "experiment").rglob("keep.txt"))) == 8
    assert len(list((tmp_path / "experiment/records").glob("*.json"))) == 8
    assert read_experiment(tmp_path / "experiment/runs.json") == result
    assert all(row.measured_count == 1 for row in summarize(result))


def test_image_failure_stops_without_presenting_successful_subset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "input", corrupt=True)

    def execute(
        workload: DatasetManifest,
        directory: Path,
        transform: TransformConfig,
        workers: int,
    ) -> BatchResult:
        return run_sequential(workload, directory, transform)

    monkeypatch.setattr(runner, "run_parallel", execute)
    result = runner.run_experiment(
        manifest,
        tmp_path / "experiment",
        TransformConfig(2, 2),
        BenchmarkConfig(repetitions=1, tiny=True),
    )
    assert result.status == "failed"
    assert len(result.records) == 1
    assert (result.records[0].succeeded, result.records[0].failed) == (3, 1)
    assert len(list((tmp_path / "experiment").rglob("*.png"))) == 3
    assert all(not row.complete for row in summarize(result, require_complete=False))
    with pytest.raises(BenchmarkError, match="unsuccessful"):
        summarize(result)


@pytest.mark.parametrize("failure", [KeyboardInterrupt, WorkerExecutionError, OSError])
def test_catastrophic_run_is_recorded_and_original_exception_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[BaseException]
) -> None:
    manifest = make_manifest(tmp_path / "input")
    original = failure("private failure detail")
    monkeypatch.setattr(runner, "run_parallel", Mock(side_effect=original))
    with pytest.raises(failure, match="private failure detail") as caught:
        runner.run_experiment(
            manifest,
            tmp_path / "experiment",
            TransformConfig(2, 2),
            BenchmarkConfig(repetitions=1, tiny=True),
        )
    assert caught.value is original
    result = read_experiment(tmp_path / "experiment/runs.json")
    assert result.status == (
        "interrupted" if failure is KeyboardInterrupt else "failed"
    )
    assert len(result.records) == 1
    assert result.records[0].succeeded is None
    assert result.records[0].failed is None
    assert (
        b"private failure detail"
        not in (tmp_path / "experiment/runs.json").read_bytes()
    )


@pytest.mark.parametrize("target", ["exists", "input/nested"])
def test_benchmark_directory_rejection_does_not_start_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    manifest = make_manifest(tmp_path / "input")
    (tmp_path / "exists").mkdir()
    execute = Mock(side_effect=AssertionError("unexpected pool"))
    monkeypatch.setattr(runner, "run_parallel", execute)
    with pytest.raises(ValueError, match=r"exist|outside"):
        runner.run_experiment(
            manifest, tmp_path / target, TransformConfig(2, 2), BenchmarkConfig()
        )
    execute.assert_not_called()


@pytest.mark.spawn
def test_real_tiny_experiment_preserves_spawn_output_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "input")
    transform = TransformConfig(2, 2)
    sequential = run_sequential(manifest, tmp_path / "sequential", transform)
    expected = {
        path.name: path.read_bytes() for path in sequential.output_dir.glob("*.png")
    }
    execute = runner.run_parallel

    def verify(
        workload: DatasetManifest,
        directory: Path,
        config: TransformConfig,
        workers: int,
    ) -> BatchResult:
        result = execute(workload, directory, config, workers)
        assert {
            path.name: path.read_bytes() for path in result.output_dir.glob("*.png")
        } == expected
        return result

    monkeypatch.setattr(runner, "run_parallel", verify)
    result = runner.run_experiment(
        manifest,
        tmp_path / "experiment",
        transform,
        BenchmarkConfig(repetitions=1, tiny=True, sampling_interval=0.01),
    )
    assert result.status == "success"
    assert {record.step.workers for record in result.records} == {1, 2, 4, 8}
    assert all(
        record.elapsed_seconds is not None and record.elapsed_seconds > 0
        for record in result.records
    )
    assert all(record.actual_workers is None for record in result.records)
    assert summarize(result)[0].speedup == pytest.approx(1)
    assert read_experiment(tmp_path / "experiment/runs.json") == result


@pytest.mark.spawn
def test_lock_excludes_another_python_process_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ownership.tempfile, "gettempdir", Mock(return_value=str(tmp_path))
    )
    script = "import sys,tempfile; tempfile.tempdir=sys.argv[1]; from tachyron.benchmarking.ownership import experiment_lock; from tachyron.benchmarking.models import BenchmarkError\ntry:\n with experiment_lock(): pass\nexcept BenchmarkError: sys.exit(3)"

    def attempt() -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 -- Fixed Python program and generated temp path, no shell.
            [sys.executable, "-I", "-c", script, str(tmp_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

    with experiment_lock():
        assert attempt().returncode == 3
    assert attempt().returncode == 0


def test_zero_duration_is_recorded_as_failure_not_a_successful_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "input")
    monkeypatch.setattr(runner, "perf_counter", Mock(return_value=10.0))

    def execute(
        workload: DatasetManifest,
        directory: Path,
        transform: TransformConfig,
        workers: int,
    ) -> BatchResult:
        return run_sequential(workload, directory, transform)

    monkeypatch.setattr(runner, "run_parallel", execute)
    with pytest.raises(BenchmarkError, match="finite image rate"):
        runner.run_experiment(
            manifest,
            tmp_path / "experiment",
            TransformConfig(2, 2),
            BenchmarkConfig(repetitions=1, tiny=True),
        )
    result = read_experiment(tmp_path / "experiment/runs.json")
    assert result.status == "failed"
    assert result.records[0].error_code == "invalid_duration"


def test_source_change_after_processing_invalidates_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "input")
    environment = example_experiment().context.environment
    monkeypatch.setattr(
        runner,
        "capture_environment",
        Mock(
            side_effect=[
                environment,
                environment,
                replace(environment, source_hash="d" * 64),
            ]
        ),
    )

    def execute(
        workload: DatasetManifest,
        directory: Path,
        transform: TransformConfig,
        workers: int,
    ) -> BatchResult:
        return run_sequential(workload, directory, transform)

    monkeypatch.setattr(runner, "run_parallel", execute)
    with pytest.raises(BenchmarkError, match="changed"):
        runner.run_experiment(
            manifest,
            tmp_path / "experiment",
            TransformConfig(2, 2),
            BenchmarkConfig(repetitions=1, tiny=True),
        )
    result = read_experiment(tmp_path / "experiment/runs.json")
    assert result.records[0].error_code == "provenance_changed"
    assert result.records[0].succeeded == 3
    assert len(list((tmp_path / "experiment").rglob("*.png"))) == 3


def test_cleanup_denial_stops_without_changing_image_accounting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "input")

    def execute(
        workload: DatasetManifest,
        directory: Path,
        transform: TransformConfig,
        workers: int,
    ) -> BatchResult:
        return run_sequential(workload, directory, transform)

    monkeypatch.setattr(runner, "run_parallel", execute)
    monkeypatch.setattr(
        runner,
        "_remove_successful_images",
        Mock(side_effect=PermissionError("private cleanup failure")),
    )
    with pytest.raises(PermissionError, match="private cleanup failure"):
        runner.run_experiment(
            manifest,
            tmp_path / "experiment",
            TransformConfig(2, 2),
            BenchmarkConfig(repetitions=1, tiny=True),
        )
    result = read_experiment(tmp_path / "experiment/runs.json")
    assert result.records[0].error_code == "cleanup_failure"
    assert (result.records[0].succeeded, result.records[0].failed) == (3, 0)
    assert len(result.records) == 1


def test_monitor_shutdown_error_does_not_mask_execution_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "input")
    original = WorkerExecutionError("original executor failure")
    monkeypatch.setattr(runner, "run_parallel", Mock(side_effect=original))
    sampler = Mock(
        start=Mock(), stop=Mock(side_effect=OSError("secondary sampler failure"))
    )
    monkeypatch.setattr(runner, "Sampler", Mock(return_value=sampler))
    with pytest.raises(
        WorkerExecutionError, match="original executor failure"
    ) as caught:
        runner.run_experiment(
            manifest,
            tmp_path / "experiment",
            TransformConfig(2, 2),
            BenchmarkConfig(repetitions=1, tiny=True),
        )
    assert caught.value is original
    result = read_experiment(tmp_path / "experiment/runs.json")
    assert result.records[0].telemetry.unavailable_reason == "sampling_failed"


def test_failure_in_final_planned_run_is_still_an_image_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "input")
    calls = 0

    def execute(
        workload: DatasetManifest,
        directory: Path,
        transform: TransformConfig,
        workers: int,
    ) -> BatchResult:
        nonlocal calls
        calls += 1
        if calls == 8:
            (workload.root / "0.png").write_bytes(b"changed between runs")
        return run_sequential(workload, directory, transform)

    monkeypatch.setattr(runner, "run_parallel", execute)
    result = runner.run_experiment(
        manifest,
        tmp_path / "experiment",
        TransformConfig(2, 2),
        BenchmarkConfig(repetitions=1, tiny=True),
    )
    assert result.status == "failed"
    assert len(result.records) == len(result.plan) == 8
    assert result.records[-1].error_code == "image_failure"
    assert (result.records[-1].succeeded, result.records[-1].failed) == (2, 1)
    assert read_experiment(tmp_path / "experiment/runs.json") == result
    assert all(not row.complete for row in summarize(result, require_complete=False))


def test_monitor_stop_failure_retains_known_completed_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "input")

    def execute(
        workload: DatasetManifest,
        directory: Path,
        transform: TransformConfig,
        workers: int,
    ) -> BatchResult:
        return run_sequential(workload, directory, transform)

    monkeypatch.setattr(runner, "run_parallel", execute)
    monkeypatch.setattr(
        runner,
        "Sampler",
        Mock(
            return_value=Mock(
                start=Mock(), stop=Mock(side_effect=OSError("monitor stop failed"))
            )
        ),
    )
    with pytest.raises(OSError, match="monitor stop failed"):
        runner.run_experiment(
            manifest,
            tmp_path / "experiment",
            TransformConfig(2, 2),
            BenchmarkConfig(repetitions=1, tiny=True),
        )
    result = read_experiment(tmp_path / "experiment/runs.json")
    assert result.records[0].succeeded == 3
    assert result.records[0].failed == 0
    assert result.records[0].elapsed_seconds is not None
    assert result.records[0].telemetry.unavailable_reason == "sampling_failed"

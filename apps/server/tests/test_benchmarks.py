# Copyright (c) 2026 Hans Valerie
"""Benchmark formulas, provenance, failure accounting and real spawn experiments."""

import csv
import json
import math
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import psutil
import pytest
from PIL import Image

from tachyron import benchmark_cli
from tachyron.benchmarking import exports, ownership, runner
from tachyron.benchmarking.exports import publish_exports, read_experiment
from tachyron.benchmarking.models import (
    BenchmarkConfig,
    BenchmarkError,
    Context,
    Environment,
    Experiment,
    RunRecord,
    Telemetry,
    make_plan,
)
from tachyron.benchmarking.ownership import experiment_lock
from tachyron.benchmarking.stats import describe, summarize, throughput
from tachyron.processing import (
    BatchResult,
    DatasetManifest,
    ResourceLimits,
    TransformConfig,
    build_manifest,
    run_sequential,
)
from tachyron.workers import WorkerExecutionError


def example_experiment(repetitions: int = 2) -> Experiment:
    """Controlled arithmetic evidence only; never used by production reporting."""
    config = BenchmarkConfig(repetitions=repetitions, tiny=True, seed=19)
    environment = Environment(
        python_version="test",
        pillow_version="test",
        psutil_version="test",
        tachyron_version="test",
        os_name="test",
        os_release="test",
        architecture="test",
        host_id="b" * 64,
        source_hash="c" * 64,
    )
    context = Context(
        "a" * 64,
        10,
        100,
        TransformConfig(2, 2),
        ResourceLimits(),
        environment,
        True,
        0.1,
    )
    plan = make_plan(config, "00000000-0000-4000-8000-000000000001")
    durations = {1: (10.0, 14.0), 2: (4.0, 8.0), 4: (2.0, 4.0), 8: (2.0, 2.0)}
    records = tuple(
        RunRecord(
            step=step,
            context_id=context.digest,
            started_at="2026-01-01T00:00:00+00:00",
            ended_at="2026-01-01T00:00:01+00:00",
            elapsed_seconds=999.0
            if step.warmup
            else durations[step.workers][(step.repetition - 1) % 2],
            succeeded=10,
            failed=0,
            status="success",
            error_code=None,
            telemetry=Telemetry(),
        )
        for step in plan
    )
    return Experiment(
        "00000000-0000-4000-8000-000000000001",
        context,
        config,
        plan,
        records,
        "success",
    )


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


def test_json_roundtrip_csv_units_and_exclusive_publication(tmp_path: Path) -> None:
    experiment = example_experiment()
    publish_exports(experiment, tmp_path)
    assert read_experiment(tmp_path / "runs.json") == experiment
    manifest = json.loads((tmp_path / "experiment.json").read_text())
    assert manifest["status"] == "success"
    with (tmp_path / "runs.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 12
    assert rows[0]["cpu_mean_core_percent"] == ""
    assert rows[0]["actual_workers"] == ""
    assert rows[0]["context_id"] == experiment.context.digest
    assert "context_json" in rows[0]
    before = (tmp_path / "experiment.json").read_bytes()
    with pytest.raises(FileExistsError):
        publish_exports(experiment, tmp_path)
    assert (tmp_path / "experiment.json").read_bytes() == before


@pytest.mark.parametrize(
    "changed",
    ["transform", "environment", "context_id", "duplicate", "schema", "throughput"],
)
def test_import_rejects_incompatible_or_dishonest_evidence(
    tmp_path: Path, changed: str
) -> None:
    publish_exports(example_experiment(), tmp_path)
    path = tmp_path / "runs.json"
    data = json.loads(path.read_text())
    if changed == "transform":
        data["context"]["transform"]["width"] = 3
    elif changed == "environment":
        data["context"]["environment"]["os_name"] = "different"
    elif changed == "context_id":
        data["records"][0]["context_id"] = "f" * 64
    elif changed == "duplicate":
        data["records"][1] = data["records"][0]
    elif changed == "schema":
        data["schema_version"] = 100
    else:
        data["records"][0]["throughput_images_per_second"] = 999
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(BenchmarkError):
        read_experiment(path)


def test_failed_export_never_publishes_a_completion_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    publish = exports.publish_bytes

    def fail(directory: Path, name: str, data: bytes) -> None:
        if name == "workers.csv":
            raise OSError("private write failure")
        publish(directory, name, data)

    monkeypatch.setattr(exports, "publish_bytes", fail)
    with pytest.raises(OSError, match="private write failure"):
        publish_exports(example_experiment(), tmp_path)
    assert not (tmp_path / "experiment.json").exists()


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


def make_manifest(root: Path, *, corrupt: bool = False) -> DatasetManifest:
    root.mkdir()
    for index in range(3):
        with Image.new("RGB", (4, 3), (index * 55, 60, 70)) as image:
            image.save(root / f"{index}.png")
    if corrupt:
        (root / "bad.png").write_bytes(b"invalid image")
    return build_manifest(root)


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


def test_compare_cli_recomputes_from_raw_evidence_and_rejects_failed_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    publish_exports(example_experiment(), source)
    assert (
        benchmark_cli.main(
            [
                "compare",
                str(source / "runs.json"),
                "--output",
                str(tmp_path / "comparison"),
            ]
        )
        == 0
    )
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "success"
    assert response["summaries"][0]["speedup"] == pytest.approx(1)
    failed = replace(example_experiment(), records=(), status="failed")
    failure_dir = tmp_path / "failed"
    failure_dir.mkdir()
    publish_exports(failed, failure_dir)
    assert (
        benchmark_cli.main(
            [
                "compare",
                str(failure_dir / "runs.json"),
                "--output",
                str(tmp_path / "invalid"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "invalid").exists()


def test_benchmark_cli_validates_before_discovery_and_sanitizes_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        benchmark_cli.main(
            [
                "run",
                str(tmp_path / "missing"),
                "--output",
                str(tmp_path / "experiment"),
                "--repetitions",
                "1",
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert not output.out
    assert str(tmp_path) not in output.err
    assert not (tmp_path / "experiment").exists()


@pytest.mark.spawn
def test_guarded_benchmark_cli_real_run_and_compare_without_http(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "input"
    make_manifest(dataset)
    command = [sys.executable, "-I", "-m", "tachyron.benchmark_cli"]
    run = subprocess.run(  # noqa: S603 -- Fixed installed module with generated paths, no shell.
        [
            *command,
            "run",
            str(dataset),
            "--output",
            str(tmp_path / "experiment"),
            "--tiny",
            "--width",
            "2",
            "--height",
            "2",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    assert run.returncode == 0, run.stderr
    response = json.loads(run.stdout)
    assert response["status"] == "success"
    assert response["tiny"] is True
    assert len(response["summaries"]) == 4
    compared = subprocess.run(  # noqa: S603 -- Same trusted module and owned input/output paths.
        [
            *command,
            "compare",
            str(tmp_path / "experiment/runs.json"),
            "--output",
            str(tmp_path / "comparison"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert compared.returncode == 0, compared.stderr
    assert json.loads(compared.stdout)["summaries"] == response["summaries"]


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


def test_empty_failed_evidence_produces_headers_without_a_fake_raw_run(
    tmp_path: Path,
) -> None:
    publish_exports(
        replace(example_experiment(), records=(), status="failed"), tmp_path
    )
    with (tmp_path / "runs.csv").open(newline="", encoding="utf-8") as stream:
        rows = csv.DictReader(stream)
        assert rows.fieldnames is not None
        assert "elapsed_seconds" in rows.fieldnames
        assert list(rows) == []


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


def test_default_schedule_has_four_warmups_and_twenty_measurements() -> None:
    plan = make_plan(BenchmarkConfig(), "00000000-0000-4000-8000-000000000001")
    assert len(plan) == 24
    assert sum(step.warmup for step in plan) == 4
    for workers in (1, 2, 4, 8):
        assert sum(not step.warmup and step.workers == workers for step in plan) == 5


def test_benchmark_cli_sanitizes_denied_process_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = make_manifest(tmp_path / "input")
    monkeypatch.setattr(
        benchmark_cli,
        "run_experiment",
        Mock(side_effect=psutil.AccessDenied(123, name="private process detail")),
    )
    assert (
        benchmark_cli.main(
            [
                "run",
                str(manifest.root),
                "--output",
                str(tmp_path / "experiment"),
                "--tiny",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert not captured.out
    assert "private process detail" not in captured.err
    assert str(tmp_path) not in captured.err


def test_integer_numeric_metadata_roundtrips_without_hash_drift(tmp_path: Path) -> None:
    original = example_experiment()
    context = replace(
        original.context,
        sampling_interval=1,
        environment=replace(original.context.environment, total_memory_mib=100),
    )
    experiment = replace(
        original,
        context=context,
        config=replace(original.config, sampling_interval=1),
        records=tuple(
            replace(record, context_id=context.digest) for record in original.records
        ),
    )
    publish_exports(experiment, tmp_path)
    assert read_experiment(tmp_path / "runs.json") == experiment


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

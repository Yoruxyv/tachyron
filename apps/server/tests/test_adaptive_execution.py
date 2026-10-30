# Copyright (c) 2026 Hans Valerie
"""Operational integration with synthetic clocks, plus separate real spawn coverage."""

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event
from typing import cast
from unittest.mock import Mock

import pytest

from tachyron.benchmarking.models import BenchmarkError, Telemetry
from tachyron.benchmarking.ownership import experiment_lock
from tachyron.execution import (
    CalibrationPolicy,
    adaptive,
    cli,
    inspect_adaptive,
    run_adaptive,
    runner,
)
from tachyron.execution.models import ExecutionResult
from tachyron.execution.resources import Resources
from tachyron.processing import (
    DatasetManifest,
    ImageOutcome,
    ProcessingError,
    TransformConfig,
)
from tachyron.processing.processor import process_image
from tachyron.workers import operational
from tachyron.workers.operational import PoolAccounting
from tests.benchmark_helpers import example_experiment, make_manifest
from tests.test_adaptive_sampling import image_dataset
from tests.test_calibration import observations
from tests.test_workers import crash_in_worker


@dataclass
class Clock:
    """Synthetic time only; these unit fixtures are not benchmark evidence."""

    now: float = 0
    trial_count: int = 0
    constrain: bool = False


@pytest.fixture
def controlled(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    environment = replace(
        example_experiment().context.environment,
        logical_cpus=8,
        physical_cpus=4,
        cpu_affinity=tuple(range(8)),
        available_memory_mib=8192,
    )

    def observe(
        config: TransformConfig, pixels: int, compressed_bytes: int
    ) -> Resources:
        return Resources(
            environment,
            None,
            None,
            "unsupported_platform_constraints",
            4096,
            65,
            (1,) if clock.constrain else (1, 2),
        )

    def account(
        manifest: DatasetManifest,
        directory: Path,
        config: TransformConfig,
        workers: int,
        *,
        cancelled: Callable[[], bool],
        on_outcome: Callable[[int, ImageOutcome], None],
    ) -> PoolAccounting:
        outcomes: list[ImageOutcome | None] = [None] * len(manifest.entries)
        for index, entry in enumerate(manifest.entries):
            if cancelled():
                return PoolAccounting(
                    tuple(outcomes), frozenset(range(index, len(outcomes))), True, None
                )
            outcomes[index] = outcome = process_image(
                manifest.root, entry, directory, config, manifest.limits
            )
            on_outcome(index, outcome)
        return PoolAccounting(tuple(outcomes), frozenset(), False, None)

    def measure(
        manifest: DatasetManifest,
        directory: Path,
        config: TransformConfig,
        workers: int,
        cancelled: Callable[[], bool],
    ) -> tuple[ExecutionResult, float, Telemetry]:
        with (
            pytest.raises(BenchmarkError, match="Another benchmark"),
            experiment_lock(),
        ):
            pytest.fail("The outer lock must cover every trial and final execution")
        result = runner._run_manifest(
            manifest, directory, config, workers, cancelled=cancelled
        )
        seconds = 0.2 + len(manifest.entries) * 0.05 / workers
        clock.now += seconds
        if directory.name.startswith("trial-"):
            clock.trial_count += 1
        return result, seconds, observations()

    monkeypatch.setattr(adaptive, "perf_counter", lambda: clock.now)
    # Trial durations and linked receipts must use the same synthetic clock.
    monkeypatch.setattr(runner, "perf_counter", lambda: clock.now)
    monkeypatch.setattr(adaptive, "observe_resources", observe)
    monkeypatch.setattr(adaptive, "_measure", measure)
    monkeypatch.setattr(runner, "run_accounted", account)
    return clock


def test_measured_selection_and_linked_receipts_under_one_lock(
    tmp_path: Path, controlled: Clock
) -> None:
    root = image_dataset(tmp_path / "input", 512)
    result = run_adaptive(
        root,
        tmp_path / "output",
        TransformConfig(2, 2),
        policy=CalibrationPolicy(sample_files=16),
    )
    assert result.state == "COMPLETED"
    assert result.calibration.selection.status == "SELECTED"
    assert result.calibration.selection.workers == 2
    assert result.execution is not None
    assert result.execution.counts["succeeded"] == 512
    assert controlled.trial_count == 12
    loaded = inspect_adaptive(tmp_path / "output")
    assert loaded["state"] == "COMPLETED"
    timing = cast("dict[str, object]", loaded["timing"])
    assert timing["through_result_publication_seconds"] == result.total_seconds
    with experiment_lock():
        pass


def test_budget_abstains_and_still_executes_full_workload(
    tmp_path: Path, controlled: Clock
) -> None:
    root = image_dataset(tmp_path / "input", 512)
    result = run_adaptive(
        root,
        tmp_path / "output",
        TransformConfig(2, 2),
        policy=CalibrationPolicy(sample_files=16, max_seconds=1),
    )
    assert result.state == "COMPLETED"
    assert result.calibration.selection.status == "INCONCLUSIVE"
    assert result.calibration.stop_reason == "calibration_stopped"
    assert result.calibration.trials
    assert result.execution is not None
    assert result.execution.workers == 1
    assert result.execution.counts["succeeded"] == 512
    assert inspect_adaptive(tmp_path / "output")["state"] == "COMPLETED"


def test_user_cancellation_during_calibration_does_not_start_full_work(
    tmp_path: Path, controlled: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = image_dataset(tmp_path / "input", 512)
    cancelled = Event()
    measure = adaptive._measure

    def cancel_after_trial(
        manifest: DatasetManifest,
        directory: Path,
        config: TransformConfig,
        workers: int,
        stop: Callable[[], bool],
    ) -> tuple[ExecutionResult, float, Telemetry]:
        result = measure(manifest, directory, config, workers, stop)
        cancelled.set()
        return result

    monkeypatch.setattr(adaptive, "_measure", cancel_after_trial)
    result = run_adaptive(
        root,
        tmp_path / "output",
        TransformConfig(2, 2),
        policy=CalibrationPolicy(sample_files=16),
        cancelled=cancelled.is_set,
    )
    assert result.state == "CANCELLED"
    assert len(result.calibration.trials) == 1
    assert result.execution is not None
    assert result.execution.counts == {
        "succeeded": 0,
        "failed": 0,
        "unknown": 0,
        "not_started": 512,
    }
    assert not list((tmp_path / "output" / "execution").glob("*.png"))
    assert inspect_adaptive(tmp_path / "output")["state"] == "CANCELLED"


def test_resource_change_invalidates_selection_before_full_execution(
    tmp_path: Path, controlled: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = image_dataset(tmp_path / "input", 512)
    measure = adaptive._measure

    def constrain_after_trials(
        manifest: DatasetManifest,
        directory: Path,
        config: TransformConfig,
        workers: int,
        stop: Callable[[], bool],
    ) -> tuple[ExecutionResult, float, Telemetry]:
        result = measure(manifest, directory, config, workers, stop)
        if controlled.trial_count == 12:
            controlled.constrain = True
        return result

    monkeypatch.setattr(adaptive, "_measure", constrain_after_trials)
    result = run_adaptive(
        root,
        tmp_path / "output",
        TransformConfig(2, 2),
        policy=CalibrationPolicy(sample_files=16),
    )
    assert result.state == "COMPLETED"
    assert result.calibration.selection.reason == "resources_changed"
    assert result.execution is not None
    assert result.execution.workers == 1
    assert inspect_adaptive(tmp_path / "output")["state"] == "COMPLETED"


def test_resource_rejection_prevents_any_full_processing(
    tmp_path: Path, controlled: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "input")
    current = adaptive.observe_resources(TransformConfig(2, 2), 1, 1)
    monkeypatch.setattr(
        adaptive,
        "observe_resources",
        Mock(return_value=replace(current, candidates=(), memory_budget_mib=0)),
    )
    result = run_adaptive(manifest.root, tmp_path / "output", TransformConfig(2, 2))
    assert result.state == "FAILED"
    assert result.calibration.error_code == "fallback_resource_limit"
    assert result.execution is None
    assert not (tmp_path / "output" / "execution").exists()
    assert inspect_adaptive(tmp_path / "output")["state"] == "FAILED"


def test_frozen_identity_survives_change_between_calibration_and_execution(
    tmp_path: Path, controlled: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "input")
    calibrate = adaptive._calibrate

    def mutate(
        manifest: DatasetManifest,
        directory: Path,
        config: TransformConfig,
        policy: CalibrationPolicy,
        control: adaptive._Control,
        setup: float,
    ) -> adaptive.Calibration:
        result = calibrate(manifest, directory, config, policy, control, setup)
        (manifest.root / "0.png").write_bytes(b"changed after admission")
        return result

    monkeypatch.setattr(adaptive, "_calibrate", mutate)
    result = run_adaptive(manifest.root, tmp_path / "output", TransformConfig(2, 2))
    assert result.state == "FAILED"
    assert result.execution is not None
    assert result.execution.counts["failed"] == 1
    assert result.execution.items[0].outcome is not None
    assert result.execution.items[0].outcome.error_code == "input_changed"


@pytest.mark.parametrize(
    "corruption", ["policy", "timing", "selected", "manifest", "counts"]
)
def test_inspection_rejects_corrupt_or_contradictory_evidence(
    tmp_path: Path, controlled: Clock, corruption: str
) -> None:
    manifest = make_manifest(tmp_path / "input")
    output = tmp_path / "output"
    run_adaptive(manifest.root, output, TransformConfig(2, 2))
    name = (
        "adaptive-admission.json"
        if corruption == "policy"
        else "timing.json"
        if corruption == "timing"
        else "adaptive.json"
    )
    path = output / name
    data = cast("dict[str, object]", json.loads(path.read_text()))
    if corruption == "policy":
        data["policy"] = {"unbounded": True}
    elif corruption == "timing":
        data["through_result_publication_seconds"] = float("nan")
    elif corruption == "selected":
        calibration = cast("dict[str, object]", data["calibration"])
        selection = cast("dict[str, object]", calibration["selection"])
        selection["status"] = "SELECTED"
    elif corruption == "manifest":
        data["manifest_sha256"] = "0" * 64
    else:
        data["state"] = "CANCELLED"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ProcessingError, match="invalid or unavailable"):
        inspect_adaptive(output)


def test_final_publication_failure_remains_unconfirmed(
    tmp_path: Path, controlled: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "input")
    publish = adaptive.publish_bytes

    def fail_footer(directory: Path, name: str, data: bytes) -> None:
        if name == "timing.json":
            raise OSError("private storage failure")
        publish(directory, name, data)

    monkeypatch.setattr(adaptive, "publish_bytes", fail_footer)
    with pytest.raises(OSError, match="private storage"):
        run_adaptive(manifest.root, tmp_path / "output", TransformConfig(2, 2))
    loaded = inspect_adaptive(tmp_path / "output")
    assert loaded["state"] == "UNCONFIRMED"
    assert loaded["recorded_state"] == "COMPLETED"


def test_unconfirmed_trial_preserves_prior_measurements_and_stops(
    tmp_path: Path, controlled: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = image_dataset(tmp_path / "input", 512)
    measure = adaptive._measure

    def fail_second(
        manifest: DatasetManifest,
        directory: Path,
        config: TransformConfig,
        workers: int,
        stop: Callable[[], bool],
    ) -> tuple[ExecutionResult, float, Telemetry]:
        if controlled.trial_count == 1:
            raise OSError("private second-trial failure")
        return measure(manifest, directory, config, workers, stop)

    monkeypatch.setattr(adaptive, "_measure", fail_second)
    result = run_adaptive(
        root,
        tmp_path / "output",
        TransformConfig(2, 2),
        policy=CalibrationPolicy(sample_files=16),
    )
    assert result.state == "FAILED"
    assert result.execution is None
    assert len(result.calibration.trials) == 1
    assert result.calibration.unconfirmed_trial == "trial-001"
    assert inspect_adaptive(tmp_path / "output")["state"] == "FAILED"


def test_host_lock_rejects_before_adaptive_admission(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path / "input")
    with experiment_lock(), pytest.raises(BenchmarkError, match="Another benchmark"):
        run_adaptive(manifest.root, tmp_path / "output", TransformConfig(2, 2))
    assert not (tmp_path / "output").exists()


def test_adaptive_cli_and_inspection_share_python_contract(
    tmp_path: Path, controlled: Clock, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = make_manifest(tmp_path / "input")
    output = tmp_path / "output"
    assert (
        cli.main(
            [
                "adaptive",
                str(manifest.root),
                "--output",
                str(output),
                "--width",
                "2",
                "--height",
                "2",
            ]
        )
        == 0
    )
    document = json.loads(capsys.readouterr().out)
    assert document["selection"]["status"] == "INCONCLUSIVE"
    assert document["counts"]["succeeded"] == 3
    assert cli.main(["inspect-adaptive", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "COMPLETED"


@pytest.mark.spawn
def test_actual_calibration_spawn_and_small_fallback_preserve_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = image_dataset(tmp_path / "input", 512)
    observe = adaptive.observe_resources

    def two_candidates(
        config: TransformConfig, pixels: int, compressed_bytes: int
    ) -> Resources:
        return replace(observe(config, pixels, compressed_bytes), candidates=(1, 2))

    monkeypatch.setattr(adaptive, "observe_resources", two_candidates)
    result = run_adaptive(
        root,
        tmp_path / "output",
        TransformConfig(2, 2),
        policy=CalibrationPolicy(sample_files=16),
    )
    assert result.state == "COMPLETED"
    assert result.calibration.trials
    assert result.execution is not None
    assert result.execution.counts["succeeded"] == 512
    assert inspect_adaptive(tmp_path / "output")["state"] == "COMPLETED"


@pytest.mark.spawn
@pytest.mark.parametrize("cancel_after_failure", [False, True])
def test_catastrophic_trial_failure_never_retries_full_workload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_after_failure: bool
) -> None:
    root = image_dataset(tmp_path / "input", 512)
    monkeypatch.setattr(operational, "process_image", crash_in_worker)
    cancel = Event()
    account = runner.run_accounted

    def cancel_on_failure(
        manifest: DatasetManifest,
        directory: Path,
        config: TransformConfig,
        workers: int,
        *,
        cancelled: Callable[[], bool],
        on_outcome: Callable[[int, ImageOutcome], None],
    ) -> PoolAccounting:
        result = account(
            manifest,
            directory,
            config,
            workers,
            cancelled=cancelled,
            on_outcome=on_outcome,
        )
        if result.error_code and cancel_after_failure:
            cancel.set()
        return result

    monkeypatch.setattr(runner, "run_accounted", cancel_on_failure)
    result = run_adaptive(
        root,
        tmp_path / "output",
        TransformConfig(2, 2),
        policy=CalibrationPolicy(sample_files=16),
        cancelled=cancel.is_set,
    )
    assert result.state == "FAILED"
    assert result.execution is None
    assert result.calibration.error_code == "worker_failure"
    assert not (tmp_path / "output" / "execution").exists()
    assert inspect_adaptive(tmp_path / "output")["state"] == "FAILED"

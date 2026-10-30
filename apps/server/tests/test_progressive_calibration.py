# Copyright (c) 2026 Hans Valerie
"""Progressive budgets and version compatibility; synthetic timings are not evidence."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from tachyron.benchmarking.models import Telemetry
from tachyron.execution import (
    CalibrationPolicy,
    adaptive,
    inspect_adaptive,
    run_adaptive,
)
from tachyron.execution.calibration import (
    METHOD,
    METHOD_V3,
    WORKERS,
    candidate_schedule,
    select_supported_workers,
    select_workers,
    trial_image_budget,
)
from tachyron.execution.calibration import (
    select_progressive_workers as legacy_select_progressive,
)
from tachyron.execution.models import ExecutionResult
from tachyron.execution.resources import Resources
from tachyron.execution.storage import encode
from tachyron.processing import DatasetManifest, ProcessingError, TransformConfig
from tachyron.workers import operational
from tests import test_adaptive_execution
from tests.test_adaptive_execution import Clock
from tests.test_adaptive_sampling import image_dataset
from tests.test_calibration import trials
from tests.test_local_execution import interrupt_in_worker

controlled = test_adaptive_execution.controlled


def test_progressive_order_and_budgets_preserve_all_default_candidates() -> None:
    policy = CalibrationPolicy()
    schedule = candidate_schedule(WORKERS, policy)
    assert WORKERS == (1, 2, 4, 8)
    assert [worker for worker, _, _ in schedule[::6]] == [1, 8, 4, 2]
    assert len(schedule) == 24
    assert len(set(schedule)) == 24
    assert schedule == candidate_schedule(WORKERS, policy)
    assert schedule != candidate_schedule(WORKERS, replace(policy, seed=17))
    assert trial_image_budget(96, policy) == 48
    assert trial_image_budget(384, policy) == 192
    assert trial_image_budget(1536, policy) == 576
    assert trial_image_budget(1536, replace(policy, max_trial_images=144)) == 144


@pytest.mark.parametrize("value", [0, True, 1921])
def test_invalid_image_work_budget_is_rejected(value: int) -> None:
    with pytest.raises(ValueError, match="max_trial_images"):
        CalibrationPolicy(max_trial_images=value)


def test_partial_candidates_cannot_win_but_sunk_cost_does_not_force_slow_work() -> None:
    policy = CalibrationPolicy()
    evidence = tuple(t for t in trials(policy) if t.workers in (1, 8))
    partial = tuple(t for t in trials(policy) if t.workers == 4)[:-1]
    result = select_supported_workers(evidence + partial, 1000, WORKERS, policy, 80, 2)
    assert result.status == "SELECTED"
    assert result.workers == 8
    assert [e.workers for e in result.estimates] == [1, 8]
    assert result.amortized_gain_supported is False
    assert result.estimated_workflow_seconds is not None
    assert result.estimated_fixed_seconds is not None
    assert result.estimated_workflow_seconds > result.estimated_fixed_seconds
    assert (
        select_workers(evidence, 1000, WORKERS, policy, 80, 2).status == "INCONCLUSIVE"
    )


@pytest.mark.parametrize(
    "failure", ["missing", "noisy", "rss", "memory_budget", "failed"]
)
def test_progressive_selection_preserves_evidence_thresholds(failure: str) -> None:
    policy = CalibrationPolicy()
    evidence = [t for t in trials(policy) if t.workers in (1, 8)]
    if failure == "missing":
        evidence.pop()
    elif failure == "noisy":
        evidence[-1] = replace(evidence[-1], seconds=20)
    elif failure == "rss":
        evidence[-1] = replace(
            evidence[-1],
            resources=replace(
                evidence[-1].resources, memory_samples=0, rss_peak_mib=None
            ),
        )
    elif failure == "failed":
        evidence[-1] = replace(
            evidence[-1], state="FAILED", error_code="worker_failure"
        )
    else:
        evidence[-1] = replace(evidence[-1], memory_budget_mib=50)
    result = select_supported_workers(tuple(evidence), 1000, WORKERS, policy, 1, 1)
    assert result.status == "INCONCLUSIVE"
    assert result.workers == 1


@pytest.mark.parametrize("fallback", [1, 2])
def test_small_work_bypasses_profiling_and_trials(
    tmp_path: Path, controlled: Clock, monkeypatch: pytest.MonkeyPatch, fallback: int
) -> None:
    root = image_dataset(tmp_path / "input", 96)
    profile = Mock(side_effect=AssertionError("Preflight bypass must not profile"))
    monkeypatch.setattr(adaptive, "profile_images", profile)
    result = run_adaptive(
        root,
        tmp_path / "output",
        TransformConfig(2, 2),
        policy=CalibrationPolicy(fallback_workers=fallback),
    )
    assert result.state == "COMPLETED"
    assert result.calibration.selection.status == "INCONCLUSIVE"
    assert result.calibration.stop_reason == "calibration_work_budget_bypass"
    assert result.calibration.selection.amortized_gain_supported is None
    assert result.execution is not None
    assert result.execution.workers == fallback
    assert result.execution.counts["succeeded"] == 96
    assert controlled.trial_count == 0
    profile.assert_not_called()
    assert inspect_adaptive(tmp_path / "output")["mode"] == "adaptive-calibration-v4"


def four_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expose identical synthetic hardware for budget tests, without predicting speed."""
    observe = adaptive.observe_resources

    def current(
        config: TransformConfig, pixels: int, compressed_bytes: int
    ) -> Resources:
        return replace(observe(config, pixels, compressed_bytes), candidates=WORKERS)

    monkeypatch.setattr(adaptive, "observe_resources", current)


@pytest.mark.parametrize(
    ("limit", "stop"), [("images", "trial_work_budget"), ("time", "trial_time_budget")]
)
def test_complete_measured_gain_survives_early_stopping(
    tmp_path: Path,
    controlled: Clock,
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
    stop: str,
) -> None:
    four_candidates(monkeypatch)
    root = image_dataset(tmp_path / "input", 512)
    policy = (
        CalibrationPolicy(sample_files=16, max_trial_images=144)
        if limit == "images"
        else CalibrationPolicy(sample_files=16, max_seconds=10)
    )
    result = run_adaptive(
        root, tmp_path / "output", TransformConfig(2, 2), policy=policy
    )
    assert result.state == "COMPLETED"
    assert result.calibration.stop_reason == stop
    assert result.calibration.selection.status == "SELECTED"
    assert result.calibration.selection.workers == 8
    assert controlled.trial_count == 12
    assert {t.workers for t in result.calibration.trials} == {1, 8}
    assert sum(t.sample_count for t in result.calibration.trials) == 144
    assert inspect_adaptive(tmp_path / "output")["state"] == "COMPLETED"


def test_bad_baseline_stops_before_testing_alternatives(
    tmp_path: Path, controlled: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = image_dataset(tmp_path / "input", 512)
    measure = adaptive._measure

    def constant(
        manifest: DatasetManifest,
        directory: Path,
        config: TransformConfig,
        workers: int,
        cancelled: Callable[[], bool],
    ) -> tuple[ExecutionResult, float, Telemetry]:
        result, seconds, resources = measure(
            manifest, directory, config, workers, cancelled
        )
        if directory.name.startswith("trial-"):
            controlled.now += 1 - seconds
            return result, 1.0, resources
        return result, seconds, resources

    monkeypatch.setattr(adaptive, "_measure", constant)
    result = run_adaptive(
        root,
        tmp_path / "output",
        TransformConfig(2, 2),
        policy=CalibrationPolicy(sample_files=16),
    )
    assert result.state == "COMPLETED"
    assert result.calibration.stop_reason == "baseline_evidence_inadequate"
    assert result.calibration.selection.status == "INCONCLUSIVE"
    assert controlled.trial_count == 6
    assert result.execution is not None
    assert result.execution.workers == 1
    assert inspect_adaptive(tmp_path / "output")["state"] == "COMPLETED"


def test_rss_uses_current_memory_budget_after_headroom_declines(
    tmp_path: Path, controlled: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = image_dataset(tmp_path / "input", 512)
    observe = adaptive.observe_resources
    calls = 0

    def declining(
        config: TransformConfig, pixels: int, compressed_bytes: int
    ) -> Resources:
        nonlocal calls
        calls += 1
        current = observe(config, pixels, compressed_bytes)
        return (
            replace(current, candidates=(1,), memory_budget_mib=80)
            if calls >= 3
            else current
        )

    monkeypatch.setattr(adaptive, "observe_resources", declining)
    result = run_adaptive(
        root,
        tmp_path / "output",
        TransformConfig(2, 2),
        policy=CalibrationPolicy(sample_files=16),
    )
    assert result.calibration.stop_reason == "resource_observations_inadequate"
    assert controlled.trial_count == 1
    assert result.calibration.selection.status == "INCONCLUSIVE"
    assert result.execution is not None
    assert result.execution.workers == 1
    assert inspect_adaptive(tmp_path / "output")["state"] == "COMPLETED"


def resign(directory: Path, report: dict[str, object]) -> None:
    """Keep the footer valid so corrupt field tests exercise semantic validation."""
    (directory / "adaptive.json").write_bytes(encode(report))
    path = directory / "timing.json"
    timing = json.loads(path.read_text())
    timing["report_sha256"] = hashlib.sha256(encode(report)).hexdigest()
    path.write_bytes(encode(timing))


@pytest.mark.parametrize(
    "corruption",
    ["budget", "phase", "order", "stop", "selection", "fallback_claim", "reason"],
)
def test_progressive_receipts_reject_semantic_corruption_even_with_valid_hash(
    tmp_path: Path, controlled: Clock, corruption: str
) -> None:
    root = image_dataset(tmp_path / "input", 512 if corruption == "order" else 3)
    directory = tmp_path / "output"
    run_adaptive(
        root,
        directory,
        TransformConfig(2, 2),
        policy=CalibrationPolicy(sample_files=16),
    )
    report = json.loads((directory / "adaptive.json").read_text())
    calibration = report["calibration"]
    if corruption == "budget":
        calibration["trial_image_budget"] += 1
    elif corruption == "phase":
        calibration["trial_seconds"] += calibration["seconds"] + 1
    elif corruption == "order":
        calibration["trials"][0]["repetition"] = 17
    elif corruption == "stop":
        calibration["stop_reason"] = "invented"
    elif corruption == "fallback_claim":
        calibration["selection"]["amortized_gain_supported"] = True
    elif corruption == "reason":
        calibration["selection"]["reason"] = "invented"
    else:
        calibration["selection"]["workers"] = 8
    resign(directory, report)
    with pytest.raises(ProcessingError, match="invalid or unavailable"):
        inspect_adaptive(directory)


@pytest.mark.parametrize("version", [2, 3])
def test_legacy_schemas_remain_inspectable(
    tmp_path: Path, controlled: Clock, version: int
) -> None:
    root = image_dataset(tmp_path / "input", 3)
    directory = tmp_path / "output"
    run_adaptive(root, directory, TransformConfig(2, 2))
    admission_path = directory / "adaptive-admission.json"
    admission = json.loads(admission_path.read_text())
    admission["mode"] = f"adaptive-calibration-v{version}"
    admission["method"] = METHOD if version == 2 else METHOD_V3
    if version == 2:
        del admission["policy"]["max_trial_images"]
    admission_path.write_bytes(encode(admission))
    report = json.loads((directory / "adaptive.json").read_text())
    report["mode"] = f"adaptive-calibration-v{version}"
    for field in (
        "stop_reason",
        "trial_image_budget",
        "profile_seconds",
        "trial_seconds",
    ):
        if version == 2:
            del report["calibration"][field]
    resign(directory, report)
    loaded = inspect_adaptive(directory)
    assert loaded["mode"] == f"adaptive-calibration-v{version}"
    assert loaded["state"] == "COMPLETED"


@pytest.mark.spawn
def test_child_interrupt_during_calibration_is_failure_without_full_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = image_dataset(tmp_path / "input", 512)
    monkeypatch.setattr(operational, "process_image", interrupt_in_worker)
    result = run_adaptive(
        root,
        tmp_path / "output",
        TransformConfig(2, 2),
        policy=CalibrationPolicy(sample_files=16),
    )
    assert result.state == "FAILED"
    assert result.execution is None
    assert result.calibration.error_code == "worker_failure"
    assert result.calibration.trials[0].state != "CANCELLED"
    assert not (tmp_path / "output" / "execution").exists()
    assert inspect_adaptive(tmp_path / "output")["state"] == "FAILED"


def test_unmeasured_interior_can_be_faster_than_the_observed_extreme() -> None:
    policy = CalibrationPolicy()
    evidence = tuple(
        replace(t, seconds=0.25 + 0.2 * t.sample_count) if t.workers == 8 else t
        for t in trials(policy)
        if t.workers in (1, 8)
    )
    result = select_supported_workers(evidence, 1000, WORKERS, policy, 1, 1)
    assert result.status == "INCONCLUSIVE"
    assert result.workers == 1
    assert result.reason == "no_clear_execution_gain"
    assert [estimate.workers for estimate in result.estimates] == [1, 8]
    assert select_workers(trials(policy), 1000, WORKERS, policy, 1, 1).workers == 4


def test_fractional_cap_cannot_strand_a_valid_baseline_without_a_comparison(
    tmp_path: Path, controlled: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    four_candidates(monkeypatch)
    root = image_dataset(tmp_path / "input", 512)
    profile = adaptive.profile_images

    def costly_profile(manifest: DatasetManifest, stopped: Callable[[], bool]):
        result = profile(manifest, stopped)
        controlled.now += 10
        return result

    monkeypatch.setattr(adaptive, "profile_images", costly_profile)
    result = run_adaptive(
        root,
        tmp_path / "output",
        TransformConfig(2, 2),
        policy=CalibrationPolicy(sample_files=16),
    )
    assert result.state == "COMPLETED"
    assert controlled.trial_count == 12
    assert {trial.workers for trial in result.calibration.trials} == {1, 8}
    assert result.calibration.selection.status == "SELECTED"
    assert result.calibration.selection.workers == 8
    assert result.calibration.stop_reason == "calibration_stopped"
    assert inspect_adaptive(tmp_path / "output")["state"] == "COMPLETED"


def test_cancelled_last_trial_cannot_mask_an_earlier_supported_policy() -> None:
    policy = CalibrationPolicy()
    valid = tuple(t for t in trials(policy) if t.workers in (1, 8))
    interrupted = [t for t in trials(policy) if t.workers == 4]
    interrupted[-1] = replace(interrupted[-1], state="CANCELLED")
    result = select_supported_workers(
        valid + tuple(interrupted), 1000, WORKERS, policy, 10, 1
    )
    assert result.status == "SELECTED"
    assert result.workers == 8
    assert result.estimated_workflow_seconds == pytest.approx(51.25)
    assert [estimate.workers for estimate in result.estimates] == [1, 8]

    assert (
        legacy_select_progressive(
            valid + tuple(interrupted), 1000, WORKERS, policy, 10, 1
        ).status
        == "INCONCLUSIVE"
    )


def test_cancelled_baseline_cannot_support_automatic_selection() -> None:
    policy = CalibrationPolicy()
    evidence = tuple(
        replace(t, state="CANCELLED")
        if t.workers == 1
        and t.repetition == 2
        and t.sample_count == policy.sample_files
        else t
        for t in trials(policy)
        if t.workers in (1, 8)
    )
    result = select_supported_workers(evidence, 1000, WORKERS, policy, 10, 1)
    assert result.status == "INCONCLUSIVE"
    assert result.workers == 1

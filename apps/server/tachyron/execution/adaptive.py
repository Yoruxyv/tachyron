# Copyright (c) 2026 Hans Valerie
"""Bounded local calibration followed by one frozen full-workload execution."""

import hashlib
import shutil
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from tachyron.benchmarking.models import Telemetry
from tachyron.benchmarking.monitoring import Sampler
from tachyron.benchmarking.ownership import experiment_lock
from tachyron.execution.calibration import (
    DEFAULT_CALIBRATION_POLICY,
    METHOD_V4,
    CalibrationPolicy,
    Selection,
    Trial,
    _estimate,
    candidate_schedule,
    select_supported_workers,
    trial_image_budget,
)
from tachyron.execution.models import LOCAL_LIMITS, ExecutionResult, validate_limits
from tachyron.execution.resources import Resources, observe_resources
from tachyron.execution.runner import _admit, _run_manifest
from tachyron.execution.sampling import (
    ImageProfile,
    choose_sample,
    profile_images,
    sample_manifest,
)
from tachyron.execution.storage import encode
from tachyron.processing import (
    DatasetManifest,
    ManifestEntry,
    ResourceLimits,
    TransformConfig,
    build_manifest,
    prepare_run,
)
from tachyron.processing.processor import publish_bytes
from tachyron.workers import WorkerExecutionError


@dataclass(frozen=True)
class Calibration:
    """All retained evidence behind a selected or explicitly inconclusive policy."""

    selection: Selection
    resources: Resources
    trials: tuple[Trial, ...]
    sample_paths: tuple[str, ...]
    profiled_inputs: int
    max_input_pixels: int
    seconds: float
    error_code: str | None = None
    profile_categories: tuple[tuple[str, int, int], ...] = ()
    sample_profiles: tuple[ImageProfile, ...] = ()
    unconfirmed_trial: str | None = None
    stop_reason: str | None = None
    trial_image_budget: int = 0
    profile_seconds: float = 0.0
    trial_seconds: float = 0.0


@dataclass(frozen=True)
class AdaptiveResult:
    """Returned timing includes final publication; persisted timing has a named boundary."""

    calibration_id: str
    state: str
    calibration: Calibration
    execution: ExecutionResult | None
    total_seconds: float

    def to_dict(self) -> dict[str, object]:
        """Return a bounded console summary; full sample evidence lives in adaptive.json."""
        return {
            "schema_version": 1,
            "mode": "adaptive-calibration-v4",
            "calibration_id": self.calibration_id,
            "state": self.state,
            "selection": asdict(self.calibration.selection),
            "calibration_seconds": self.calibration.seconds,
            "total_seconds": self.total_seconds,
            "execution_id": self.execution.execution_id if self.execution else None,
            "counts": self.execution.counts if self.execution else None,
        }


class _Control:
    def __init__(self, cancelled: Callable[[], bool] | None, deadline: float) -> None:
        self.started = perf_counter()
        self.callback = cancelled or (lambda: False)
        self.deadline = deadline
        self.requested = False
        self.budget_reached = False

    def cancelled(self) -> bool:
        self.requested = self.requested or self.callback()
        return self.requested

    def stopped(self) -> bool:
        self.budget_reached = self.budget_reached or perf_counter() >= self.deadline
        return self.cancelled() or self.budget_reached


def _measure(
    manifest: DatasetManifest,
    directory: Path,
    config: TransformConfig,
    workers: int,
    cancelled: Callable[[], bool],
) -> tuple[ExecutionResult, float, Telemetry]:
    start = perf_counter()
    sampler = Sampler(0.1)
    sampler.start()
    try:
        result = _run_manifest(
            manifest, directory, config, workers, cancelled=cancelled
        )
    finally:
        resources = sampler.stop(start, perf_counter())
    return result, perf_counter() - start, resources


def _trials(
    manifest: DatasetManifest,
    entries: tuple[ManifestEntry, ...],
    directory: Path,
    config: TransformConfig,
    policy: CalibrationPolicy,
    control: _Control,
    candidate: int,
    pixels: int,
    previous: tuple[Trial, ...] = (),
) -> tuple[tuple[Trial, ...], str | None, str | None]:
    schedule = candidate_schedule((candidate,), policy)
    trials: list[Trial] = list(previous)
    for workers, size, repetition in schedule:
        if control.stopped():
            return tuple(trials), "calibration_stopped", None
        current = observe_resources(
            config, pixels, max(entry.size_bytes for entry in manifest.entries)
        )
        if workers not in current.candidates:
            return tuple(trials), "resources_changed", None
        subset = sample_manifest(
            manifest, entries[::2] if size < policy.sample_files else entries
        )
        trial_name = f"trial-{len(trials):03d}"
        try:
            result, seconds, telemetry = _measure(
                subset, directory / trial_name, config, workers, control.stopped
            )
        except KeyboardInterrupt:
            control.requested = True
            return tuple(trials), "calibration_stopped", trial_name
        except (OSError, ValueError, WorkerExecutionError):
            return tuple(trials), "adaptive_failure", trial_name
        trials.append(
            Trial(
                len(trials),
                workers,
                size,
                repetition,
                subset.sha256,
                result.execution_id,
                result.state,
                result.error_code,
                seconds,
                telemetry,
                current.candidates,
                current.memory_budget_mib,
            )
        )
        if result.cancel_requested and not control.budget_reached:
            control.requested = True
        if result.state != "COMPLETED":
            return tuple(trials), result.error_code or "calibration_stopped", None
        if telemetry.rss_peak_mib is None or (
            current.memory_budget_mib is not None
            and telemetry.rss_peak_mib > current.memory_budget_mib
        ):
            return tuple(trials), "resource_observations_inadequate", None
    return tuple(trials), None, None


def _progressive_trials(
    manifest: DatasetManifest,
    entries: tuple[ManifestEntry, ...],
    directory: Path,
    config: TransformConfig,
    policy: CalibrationPolicy,
    control: _Control,
    resources: Resources,
    pixels: int,
) -> tuple[tuple[Trial, ...], str | None, str | None]:
    """Admit whole evidence blocks while work and observed time budgets permit."""
    trials: tuple[Trial, ...] = ()
    costs: list[float] = []
    block_images = policy.repetitions * (policy.sample_files + policy.sample_files // 2)
    order = tuple(
        dict.fromkeys(
            step[0] for step in candidate_schedule(resources.candidates, policy)
        )
    )
    for worker in order:
        if control.stopped():
            return trials, "calibration_stopped", None
        if sum(
            trial.sample_count for trial in trials
        ) + block_images > trial_image_budget(len(manifest.entries), policy):
            return trials, "trial_work_budget", None
        if costs and perf_counter() + max(costs) > control.deadline:
            return trials, "trial_time_budget", None
        start = perf_counter()
        trials, reason, unconfirmed = _trials(
            manifest,
            entries,
            directory,
            config,
            policy,
            control,
            worker,
            pixels,
            trials,
        )
        if reason is not None:
            return trials, reason, unconfirmed
        costs.append(perf_counter() - start)
        if (
            worker == policy.fallback_workers
            and _estimate(worker, trials, len(manifest.entries), policy) is None
        ):
            return trials, "baseline_evidence_inadequate", None
        comparison = select_supported_workers(
            trials,
            len(manifest.entries),
            resources.candidates,
            policy,
            perf_counter() - control.started,
            0,
        )
        if comparison.status != "SELECTED":
            continue
        baseline = next(
            estimate
            for estimate in comparison.estimates
            if estimate.workers == policy.fallback_workers
        )
        control.deadline = min(
            control.deadline, control.started + 0.5 * baseline.lower_seconds
        )
    return trials, None, None


def _calibrate(
    manifest: DatasetManifest,
    directory: Path,
    config: TransformConfig,
    policy: CalibrationPolicy,
    control: _Control,
    setup_seconds: float,
) -> Calibration:
    start = perf_counter()
    budget = trial_image_budget(len(manifest.entries), policy)
    minimum_images = (
        2 * policy.repetitions * (policy.sample_files + policy.sample_files // 2)
    )
    profiles: tuple[ImageProfile, ...] = ()
    reason = None
    if len(manifest.entries) < 2 * policy.sample_files:
        reason = "workload_too_small"
    elif minimum_images > budget:
        reason = "calibration_work_budget_bypass"
    else:
        profiles, reason = profile_images(manifest, control.stopped)
    profile_seconds = perf_counter() - start
    entries = ()
    if reason is None:
        entries, reason = choose_sample(profiles, policy)
    pixels = (
        max((value.pixels for value in profiles), default=manifest.limits.max_pixels)
        if len(profiles) == len(manifest.entries)
        else manifest.limits.max_pixels
    )
    resources = observe_resources(
        config, pixels, max(entry.size_bytes for entry in manifest.entries)
    )
    trials: tuple[Trial, ...] = ()
    unconfirmed_trial = None
    trial_seconds = 0.0
    if policy.fallback_workers not in resources.candidates and not control.cancelled():
        reason = "fallback_resource_limit"
    elif len(resources.candidates) < 2:
        reason = reason or "single_feasible_candidate"
    if reason is None:
        required = (
            2
            * config.width
            * config.height
            * policy.sample_files
            * policy.repetitions
            * len(resources.candidates)
            * 2
            + 64 * 1024**2
        )
        if shutil.disk_usage(directory).free < required:
            reason = "calibration_disk_budget"
        else:
            trial_start = perf_counter()
            trials, reason, unconfirmed_trial = _progressive_trials(
                manifest, entries, directory, config, policy, control, resources, pixels
            )
            trial_seconds = perf_counter() - trial_start
    elapsed = perf_counter() - start
    selection = (
        Selection("INCONCLUSIVE", policy.fallback_workers, reason or "cancelled")
        if reason
        not in {
            None,
            "trial_work_budget",
            "trial_time_budget",
            "baseline_evidence_inadequate",
            "calibration_stopped",
        }
        or control.requested
        else select_supported_workers(
            trials,
            len(manifest.entries),
            resources.candidates,
            policy,
            elapsed,
            setup_seconds,
        )
    )
    fatal = (
        reason
        if reason
        in {
            "worker_failure",
            "receipt_failure",
            "fallback_resource_limit",
            "adaptive_failure",
        }
        else None
    )
    profiled = {value.entry.relative_path: value for value in profiles}
    return Calibration(
        selection,
        resources,
        trials,
        tuple(entry.relative_path for entry in entries),
        len(profiles),
        pixels,
        elapsed,
        fatal,
        tuple(
            (format_name, scale, count)
            for (format_name, scale), count in sorted(
                Counter(value.category for value in profiles).items()
            )
        ),
        tuple(profiled[entry.relative_path] for entry in entries),
        unconfirmed_trial,
        reason,
        budget,
        profile_seconds,
        trial_seconds,
    )


def run_adaptive(
    root: Path,
    output: Path,
    config: TransformConfig,
    *,
    policy: CalibrationPolicy = DEFAULT_CALIBRATION_POLICY,
    limits: ResourceLimits = LOCAL_LIMITS,
    cancelled: Callable[[], bool] | None = None,
) -> AdaptiveResult:
    """Calibrate locally under one lock, then execute the original full manifest once.

    Args:
        root: Explicitly authorized local image dataset; never an HTTP path.
        output: New workflow directory outside inputs; parent must exist.
        config: Unchanged image transformation for trials and full execution.
        policy: Versioned sample/run/time budgets and explicit fixed fallback.
        limits: Existing local policy, optionally narrowed.
        cancelled: Cooperative user cancellation, independent of calibration budget.

    Returns:
        Actual measured workflow duration, decision and full execution if attempted.

    Raises:
        ProcessingError: Unsafe input/output or failed workflow setup/publication.
        BenchmarkError: Another cooperating workload owns the lock.
        KeyboardInterrupt: Interruption before the workflow admission was published.
    """
    start = perf_counter()
    validate_limits(limits)
    with experiment_lock():
        manifest = build_manifest(root, limits)
        _admit(manifest, output, config)
        directory = prepare_run(manifest, output, config)
        calibration_id = str(uuid4())
        admission: dict[str, object] = {
            "schema_version": 1,
            "mode": "adaptive-calibration-v4",
            "calibration_id": calibration_id,
            "config": asdict(config),
            "limits": asdict(limits),
            "policy": asdict(policy),
            "method": METHOD_V4,
            "manifest": {
                "sha256": manifest.sha256,
                "total_bytes": manifest.total_bytes,
                "entries": [asdict(entry) for entry in manifest.entries],
            },
        }
        publish_bytes(directory, "adaptive-admission.json", encode(admission))
        execution = None
        execution_seconds = 0.0
        full_resources = None
        execution_resources = None
        calibration = Calibration(
            Selection("INCONCLUSIVE", policy.fallback_workers, "not_started"),
            observe_resources(
                config,
                limits.max_pixels,
                max(entry.size_bytes for entry in manifest.entries),
            ),
            (),
            (),
            0,
            limits.max_pixels,
            0,
            trial_image_budget=trial_image_budget(len(manifest.entries), policy),
        )
        setup_seconds = perf_counter() - start
        control = _Control(cancelled, perf_counter() + policy.max_seconds)
        try:
            calibration = _calibrate(
                manifest, directory, config, policy, control, setup_seconds
            )
            full_resources = observe_resources(
                config,
                calibration.max_input_pixels,
                max(entry.size_bytes for entry in manifest.entries),
            )
            if (
                calibration.selection.workers not in full_resources.candidates
                and not control.cancelled()
            ):
                if policy.fallback_workers in full_resources.candidates:
                    calibration = replace(
                        calibration,
                        selection=Selection(
                            "INCONCLUSIVE",
                            policy.fallback_workers,
                            "resources_changed",
                            calibration.selection.estimates,
                        ),
                    )
                else:
                    calibration = replace(
                        calibration, error_code="fallback_resource_limit"
                    )
            if calibration.error_code is None:
                execution, execution_seconds, execution_resources = _measure(
                    manifest,
                    directory / "execution",
                    config,
                    calibration.selection.workers,
                    control.cancelled,
                )
            state = execution.state if execution else "FAILED"
        except KeyboardInterrupt:
            control.requested = True
            if calibration.selection.reason == "not_started":
                calibration = replace(
                    calibration,
                    selection=Selection(
                        "INCONCLUSIVE", policy.fallback_workers, "cancelled"
                    ),
                    seconds=perf_counter() - start - setup_seconds,
                )
            if not (directory / "execution").exists():
                execution, execution_seconds, execution_resources = _measure(
                    manifest,
                    directory / "execution",
                    config,
                    calibration.selection.workers,
                    control.cancelled,
                )
            state = execution.state if execution else "UNCONFIRMED"
        except (OSError, ValueError, WorkerExecutionError):
            if calibration.selection.reason == "not_started":
                calibration = replace(
                    calibration,
                    selection=Selection(
                        "INCONCLUSIVE", policy.fallback_workers, "adaptive_failure"
                    ),
                    seconds=perf_counter() - start - setup_seconds,
                )
            calibration = replace(calibration, error_code="adaptive_failure")
            state = "UNCONFIRMED" if (directory / "execution").exists() else "FAILED"
        report: dict[str, object] = {
            "schema_version": 1,
            "mode": "adaptive-calibration-v4",
            "calibration_id": calibration_id,
            "manifest_sha256": manifest.sha256,
            "state": state,
            "calibration": asdict(calibration),
            "resources_before_execution": asdict(full_resources)
            if full_resources
            else None,
            "setup_seconds": setup_seconds,
            "execution_seconds": execution_seconds,
            "execution_resources": asdict(execution_resources)
            if execution_resources
            else None,
            "elapsed_before_publication_seconds": perf_counter() - start,
            "execution_id": execution.execution_id if execution else None,
        }
        encoded = encode(report)
        publish_bytes(directory, "adaptive.json", encoded)
        timing: dict[str, object] = {
            "schema_version": 1,
            "mode": "adaptive-timing-v1",
            "calibration_id": calibration_id,
            "report_sha256": hashlib.sha256(encoded).hexdigest(),
            "through_result_publication_seconds": perf_counter() - start,
        }
        publish_bytes(directory, "timing.json", encode(timing))
        return AdaptiveResult(
            calibration_id, state, calibration, execution, perf_counter() - start
        )

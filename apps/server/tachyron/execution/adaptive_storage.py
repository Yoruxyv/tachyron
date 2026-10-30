# Copyright (c) 2026 Hans Valerie
"""Fail-closed inspection of adaptive policy and its linked operational evidence."""

import hashlib
from dataclasses import asdict, fields
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

from tachyron.benchmarking.exports import _environment, _exact, _number, _telemetry
from tachyron.execution.calibration import (
    METHOD,
    METHOD_V3,
    METHOD_V4,
    WORKERS,
    CalibrationPolicy,
    Estimate,
    Selection,
    Trial,
    candidate_schedule,
    select_progressive_workers,
    select_supported_workers,
    select_workers,
    trial_image_budget,
)
from tachyron.execution.resources import Resources
from tachyron.execution.sampling import ImageProfile, sample_manifest
from tachyron.execution.storage import (
    _array,
    _integer,
    _manifest,
    _read,
    _string,
    encode,
    inspect_execution,
)
from tachyron.processing import DatasetManifest, ProcessingError, TransformConfig
from tachyron.processing.dataset import dataset_root

_MEASURED_SELECTION_REASONS = frozenset(
    {
        "measured_execution_gain",
        "no_clear_execution_gain",
        "inadequate_or_noisy_measurements",
        "incomplete_observations",
    }
)


_CALIBRATION_V2_FIELDS = {
    "selection",
    "resources",
    "trials",
    "sample_paths",
    "profiled_inputs",
    "max_input_pixels",
    "seconds",
    "error_code",
    "profile_categories",
    "sample_profiles",
    "unconfirmed_trial",
}
_CALIBRATION_FIELDS = {
    "adaptive-calibration-v2": _CALIBRATION_V2_FIELDS,
    "adaptive-calibration-v3": _CALIBRATION_V2_FIELDS
    | {"stop_reason", "trial_image_budget", "profile_seconds", "trial_seconds"},
}


_CALIBRATION_FIELDS["adaptive-calibration-v4"] = _CALIBRATION_FIELDS[
    "adaptive-calibration-v3"
]
_METHODS = {
    "adaptive-calibration-v2": METHOD,
    "adaptive-calibration-v3": METHOD_V3,
    "adaptive-calibration-v4": METHOD_V4,
}


_SELECTORS = {
    "adaptive-calibration-v2": select_workers,
    "adaptive-calibration-v3": select_progressive_workers,
    "adaptive-calibration-v4": select_supported_workers,
}


def _optional_number(value: object) -> float | None:
    return None if value is None else _number(value)


def _workers(value: object) -> tuple[int, ...]:
    result = tuple(_integer(item) for item in _array(value))
    if result != tuple(sorted(set(result))) or any(
        item not in WORKERS for item in result
    ):
        raise ValueError("Invalid feasible worker observations")
    return result


def _resources(value: object) -> Resources:
    data = _exact(value, {field.name for field in fields(Resources)})
    status = _string(data["constraint_status"])
    if status not in {
        "visible_cgroup_v2",
        "unsupported_platform_constraints",
        "unavailable_platform_constraints",
    }:
        raise ValueError("Invalid platform constraint observation")
    result = Resources(
        _environment(data["environment"]),
        _optional_number(data["cpu_quota_cores"]),
        _optional_number(data["cgroup_memory_headroom_mib"]),
        status,
        _optional_number(data["memory_budget_mib"]),
        _number(data["estimated_worker_mib"]),
        _workers(data["candidates"]),
    )
    if result.estimated_worker_mib <= 0 or result.cpu_quota_cores == 0:
        raise ValueError("Invalid resource estimate")
    return result


def _policy(value: object, *, progressive: bool = False) -> CalibrationPolicy:
    names = {field.name for field in fields(CalibrationPolicy)}
    if not progressive:
        names.remove("max_trial_images")
    data = _exact(value, names)
    return CalibrationPolicy(
        _integer(data["sample_files"]),
        _integer(data["repetitions"]),
        _number(data["max_seconds"]),
        _integer(data["seed"]),
        _integer(data["fallback_workers"]),
        _integer(data["max_trial_images"]) if progressive else 576,
    )


def _selection(value: object) -> Selection:
    data = _exact(value, {field.name for field in fields(Selection)})
    estimates: list[Estimate] = []
    for value in _array(data["estimates"]):
        item = _exact(value, {field.name for field in fields(Estimate)})
        estimate = Estimate(
            _integer(item["workers"]),
            *(
                _number(item[name])
                for name in (
                    "seconds",
                    "lower_seconds",
                    "upper_seconds",
                    "startup_seconds",
                    "seconds_per_image",
                )
            ),
        )
        if (
            estimate.workers not in WORKERS
            or not estimate.lower_seconds <= estimate.seconds <= estimate.upper_seconds
        ):
            raise ValueError("Invalid extrapolation")
        estimates.append(estimate)
    if len(estimates) > 4 or len({value.workers for value in estimates}) != len(
        estimates
    ):
        raise ValueError("Duplicate worker estimate")
    status = data["status"]
    amortized = data["amortized_gain_supported"]
    if amortized is not None and not isinstance(amortized, bool):
        raise ValueError("Invalid amortization evidence")
    if (
        status not in {"SELECTED", "INCONCLUSIVE"}
        or _integer(data["workers"]) not in WORKERS
    ):
        raise ValueError("Invalid worker selection")
    return Selection(
        cast('Literal["SELECTED", "INCONCLUSIVE"]', status),
        _integer(data["workers"]),
        _string(data["reason"]),
        tuple(estimates),
        _optional_number(data["estimated_workflow_seconds"]),
        _optional_number(data["estimated_fixed_seconds"]),
        amortized,
    )


def _trials(
    directory: Path,
    data: dict[str, object],
    manifest: DatasetManifest,
    policy: CalibrationPolicy,
) -> tuple[Trial, ...]:
    names = tuple(_string(name) for name in _array(data["sample_paths"]))
    entries = {entry.relative_path: entry for entry in manifest.entries}
    if (
        len(names) > policy.sample_files
        or len(set(names)) != len(names)
        or any(name not in entries for name in names)
    ):
        raise ValueError("Invalid sample identity")
    values = _array(data["trials"])
    if len(values) > 8 * policy.repetitions:
        raise ValueError("Excessive calibration trials")
    results: list[Trial] = []
    for index, value in enumerate(values):
        item = _exact(value, {field.name for field in fields(Trial)})
        count = _integer(item["sample_count"])
        if (
            _integer(item["index"]) != index
            or _integer(item["repetition"]) >= policy.repetitions
            or count not in {policy.sample_files // 2, policy.sample_files}
            or len(names) != policy.sample_files
        ):
            raise ValueError("Invalid trial schedule")
        subset_names = names[::2] if count < policy.sample_files else names
        subset = sample_manifest(
            manifest, tuple(entries[name] for name in subset_names)
        )
        actual = inspect_execution(directory / f"trial-{index:03d}")
        seconds = _number(item["seconds"])
        feasible = _workers(item["feasible_workers"])
        budget = _optional_number(item["memory_budget_mib"])
        if (
            item["execution_id"] != actual.execution_id
            or _integer(item["workers"]) != actual.workers
            or item["sample_sha256"] != subset.sha256
            or actual.manifest_sha256 != subset.sha256
            or item["state"] != actual.state
            or item["error_code"] != actual.error_code
            or actual.processing_seconds is None
            or seconds < actual.processing_seconds
            or actual.workers not in feasible
        ):
            raise ValueError("Trial contradicts its operational evidence")
        results.append(
            Trial(
                index,
                actual.workers,
                count,
                _integer(item["repetition"]),
                subset.sha256,
                actual.execution_id,
                actual.state,
                actual.error_code,
                seconds,
                _telemetry(item["resources"]),
                feasible,
                budget,
            )
        )
    if len(
        {(value.workers, value.sample_count, value.repetition) for value in results}
    ) != len(results):
        raise ValueError("Duplicate trial identity")
    return tuple(results)


def _profile_categories(calibration: dict[str, object]) -> dict[tuple[str, int], int]:
    """Validate unique profile strata and their total observed input count."""
    categories: dict[tuple[str, int], int] = {}
    for value in _array(calibration["profile_categories"]):
        parts = _array(value)
        if (
            len(parts) != 3
            or parts[0] not in {"PNG", "JPEG"}
            or not 0 < _integer(parts[1]) <= 25
            or _integer(parts[2]) == 0
        ):
            raise ValueError("Invalid profile strata")
        key = (_string(parts[0]), _integer(parts[1]))
        if key in categories:
            raise ValueError("Duplicate profile stratum")
        categories[key] = _integer(parts[2])
    if sum(categories.values()) != _integer(calibration["profiled_inputs"]):
        raise ValueError("Invalid profile accounting")
    return categories


def _profiles(calibration: dict[str, object], manifest: DatasetManifest) -> None:
    """Validate observed variability and coverage separately from execution state."""
    categories = _profile_categories(calibration)
    sample_profiles = _array(calibration["sample_profiles"])
    names = _array(calibration["sample_paths"])
    if len(sample_profiles) != len(names):
        raise ValueError("Missing sample metadata")
    entries = {entry.relative_path: entry for entry in manifest.entries}
    observed_categories: list[tuple[str, int]] = []
    for name, value in zip(names, sample_profiles, strict=True):
        item = _exact(value, {field.name for field in fields(ImageProfile)})
        entry = entries[_string(name)]
        if item["entry"] != asdict(entry) or item["format"] not in {"PNG", "JPEG"}:
            raise ValueError("Sample metadata contradicts input identity")
        profile = ImageProfile(
            entry,
            _string(item["format"]),
            _integer(item["width"]),
            _integer(item["height"]),
        )
        if (
            not 0 < profile.pixels <= manifest.limits.max_pixels
            or max(profile.width, profile.height) > manifest.limits.max_dimension
        ):
            raise ValueError("Invalid sample dimensions")
        observed_categories.append(profile.category)
    if observed_categories and (
        set(observed_categories) != set(categories)
        or set(observed_categories[::2]) != set(categories)
    ):
        raise ValueError("Sample does not cover observed categories")


def _progressive_evidence(
    calibration: dict[str, object],
    manifest: DatasetManifest,
    policy: CalibrationPolicy,
    resources: Resources,
    trials: tuple[Trial, ...],
    seconds: float,
    selection: Selection,
    mode: str,
) -> None:
    """Check progressive budgets/order without reinterpreting older reports."""
    budget = _integer(calibration["trial_image_budget"])
    stop = calibration["stop_reason"]
    allowed: set[str | None] = {
        None,
        "workload_too_small",
        "calibration_work_budget_bypass",
        "trial_work_budget",
        "trial_time_budget",
        "baseline_evidence_inadequate",
        "calibration_stopped",
        "profile_byte_budget",
        "profile_resource_limit",
        "invalid_profile_input",
        "sample_coverage_inadequate",
        "sample_pixel_budget",
        "fallback_resource_limit",
        "single_feasible_candidate",
        "calibration_disk_budget",
        "resources_changed",
        "resource_observations_inadequate",
        "worker_failure",
        "receipt_failure",
        "adaptive_failure",
        "image_failure",
    }
    if selection.reason not in allowed | _MEASURED_SELECTION_REASONS | {"cancelled"}:
        raise ValueError("Invalid progressive selection reason")
    if selection.reason not in _MEASURED_SELECTION_REASONS:
        expected = Selection("INCONCLUSIVE", policy.fallback_workers, selection.reason)
        if selection.reason == "resources_changed":
            supported = _SELECTORS[mode](
                trials, len(manifest.entries), resources.candidates, policy, seconds, 0
            ).estimates
            if selection.estimates not in ((), supported):
                raise ValueError("Fallback estimates contradict their measurements")
            expected = Selection(
                "INCONCLUSIVE",
                policy.fallback_workers,
                selection.reason,
                selection.estimates,
            )
        if selection != expected:
            raise ValueError("Unmeasured fallback cannot claim performance evidence")
    if stop not in allowed or budget != trial_image_budget(
        len(manifest.entries), policy
    ):
        raise ValueError("Invalid progressive calibration budget or stopping reason")
    if sum(trial.sample_count for trial in trials) > budget:
        raise ValueError("Calibration exceeds its image work budget")
    observed = tuple(
        (trial.workers, trial.sample_count, trial.repetition) for trial in trials
    )
    if observed != candidate_schedule(resources.candidates, policy)[: len(trials)]:
        raise ValueError("Trials contradict the progressive schedule")
    if (
        _number(calibration["profile_seconds"]) + _number(calibration["trial_seconds"])
        > seconds + 1e-6
    ):
        raise ValueError("Calibration phase costs exceed its duration")
    if stop == "calibration_work_budget_bypass" and (
        trials
        or _integer(calibration["profiled_inputs"])
        or 2 * policy.repetitions * (policy.sample_files + policy.sample_files // 2)
        <= budget
    ):
        raise ValueError("Invalid pre-calibration work-budget bypass")


def _execution_evidence(
    directory: Path,
    data: dict[str, object],
    manifest: DatasetManifest,
    selection: Selection,
) -> None:
    """Validate the full execution link separately from calibration evidence."""
    state = data["state"]
    full_resources = (
        None
        if data["resources_before_execution"] is None
        else _resources(data["resources_before_execution"])
    )
    if data["execution_resources"] is not None:
        _telemetry(data["execution_resources"])
    if data["execution_id"] is not None:
        actual = inspect_execution(directory / "execution")
        if (
            actual.execution_id != data["execution_id"]
            or actual.manifest_sha256 != manifest.sha256
            or actual.workers != selection.workers
            or actual.state != state
            or (
                state == "COMPLETED"
                and (
                    full_resources is None
                    or actual.workers not in full_resources.candidates
                )
            )
        ):
            raise ValueError("Full execution contradicts adaptive receipt")
    elif state not in {"FAILED", "UNCONFIRMED"}:
        raise ValueError("Missing full execution evidence")


def _report(
    directory: Path,
    admission: dict[str, object],
    manifest: DatasetManifest,
    policy: CalibrationPolicy,
) -> dict[str, object]:
    progressive = admission["mode"] in {
        "adaptive-calibration-v3",
        "adaptive-calibration-v4",
    }
    data = _exact(
        _read(directory, "adaptive.json"),
        {
            "schema_version",
            "mode",
            "calibration_id",
            "manifest_sha256",
            "state",
            "calibration",
            "resources_before_execution",
            "setup_seconds",
            "execution_seconds",
            "execution_resources",
            "elapsed_before_publication_seconds",
            "execution_id",
        },
    )
    if (
        _integer(data["schema_version"]) != 1
        or data["mode"] != admission["mode"]
        or data["calibration_id"] != admission["calibration_id"]
        or data["manifest_sha256"] != manifest.sha256
    ):
        raise ValueError("Invalid adaptive receipt owner/version")
    state = data["state"]
    if state not in {"COMPLETED", "FAILED", "CANCELLED", "UNCONFIRMED"}:
        raise ValueError("Invalid adaptive state")
    setup, execution_seconds, elapsed = (
        _number(data[name])
        for name in (
            "setup_seconds",
            "execution_seconds",
            "elapsed_before_publication_seconds",
        )
    )
    calibration = _exact(
        data["calibration"], _CALIBRATION_FIELDS[_string(admission["mode"])]
    )
    seconds = _number(calibration["seconds"])
    _profiles(calibration, manifest)
    if calibration["error_code"] not in {
        None,
        "worker_failure",
        "receipt_failure",
        "fallback_resource_limit",
        "adaptive_failure",
    }:
        raise ValueError("Invalid calibration failure")
    if calibration["error_code"] is not None and (
        state not in {"FAILED", "UNCONFIRMED"} or data["execution_id"] is not None
    ):
        raise ValueError("Failed calibration cannot claim full execution")
    if (
        _integer(calibration["profiled_inputs"]) > len(manifest.entries)
        or not 0
        < _integer(calibration["max_input_pixels"])
        <= manifest.limits.max_pixels
        or elapsed + 1e-6 < setup + seconds + execution_seconds
    ):
        raise ValueError("Invalid adaptive accounting")
    resources = _resources(calibration["resources"])
    selection = _selection(calibration["selection"])
    trials = _trials(directory, calibration, manifest, policy)
    if progressive:
        _progressive_evidence(
            calibration,
            manifest,
            policy,
            resources,
            trials,
            seconds,
            selection,
            _string(admission["mode"]),
        )
    unconfirmed = calibration["unconfirmed_trial"]
    if unconfirmed is not None and (
        unconfirmed != f"trial-{len(trials):03d}"
        or len(trials) >= 8 * policy.repetitions
    ):
        raise ValueError("Invalid unconfirmed trial namespace")
    if sum(trial.seconds for trial in trials) > seconds + 1e-6:
        raise ValueError("Trial costs exceed calibration duration")
    if selection.reason in _MEASURED_SELECTION_REASONS:
        selector = _SELECTORS[_string(admission["mode"])]
        expected = selector(
            trials, len(manifest.entries), resources.candidates, policy, seconds, setup
        )
        if selection != expected:
            raise ValueError("Selection contradicts its measurements")
    elif (
        selection.status != "INCONCLUSIVE"
        or selection.workers != policy.fallback_workers
    ):
        raise ValueError("Unmeasured policy cannot be selected")
    _execution_evidence(directory, data, manifest, selection)
    return data


def inspect_adaptive(directory: Path) -> dict[str, object]:
    """Validate policy and linked receipts; incomplete publication stays unconfirmed.

    Returns persisted timing through primary-result publication. The final timing
    footer itself is excluded; returned/CLI total_seconds includes that footer.
    """
    try:
        directory = dataset_root(directory)
        admission = _exact(
            _read(directory, "adaptive-admission.json"),
            {
                "schema_version",
                "mode",
                "calibration_id",
                "config",
                "limits",
                "policy",
                "method",
                "manifest",
            },
        )
        identifier = _string(admission["calibration_id"])
        if (
            _integer(admission["schema_version"]) != 1
            or admission["mode"] not in _METHODS
            or str(UUID(identifier)) != identifier
        ):
            raise ValueError("Invalid adaptive admission")
        progressive = admission["mode"] in {
            "adaptive-calibration-v3",
            "adaptive-calibration-v4",
        }
        manifest, policy = (
            _manifest(admission),
            _policy(admission["policy"], progressive=progressive),
        )
        if admission["method"] != _METHODS[_string(admission["mode"])]:
            raise ValueError("Unsupported calibration methodology")
        config = _exact(admission["config"], {"width", "height", "output_format"})
        if config["output_format"] != "PNG":
            raise ValueError("Invalid image configuration")
        manifest.limits.check_output(
            TransformConfig(_integer(config["width"]), _integer(config["height"])),
            len(manifest.entries),
        )
        if (
            not (directory / "adaptive.json").exists()
            and not (directory / "adaptive.json").is_symlink()
        ):
            return {
                "mode": admission["mode"],
                "calibration_id": identifier,
                "state": "UNCONFIRMED",
                "manifest_sha256": manifest.sha256,
            }
        data = _report(directory, admission, manifest, policy)
        if (
            not (directory / "timing.json").exists()
            and not (directory / "timing.json").is_symlink()
        ):
            return {
                **data,
                "recorded_state": data["state"],
                "state": "UNCONFIRMED",
                "timing": None,
            }
        timing = _exact(
            _read(directory, "timing.json", 4096),
            {
                "schema_version",
                "mode",
                "calibration_id",
                "report_sha256",
                "through_result_publication_seconds",
            },
        )
        if (
            _integer(timing["schema_version"]) != 1
            or timing["mode"] != "adaptive-timing-v1"
            or timing["calibration_id"] != identifier
            or timing["report_sha256"] != hashlib.sha256(encode(data)).hexdigest()
            or _number(timing["through_result_publication_seconds"])
            < _number(data["elapsed_before_publication_seconds"])
        ):
            raise ValueError("Invalid adaptive publication timing")
        return {**data, "timing": timing}
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        raise ProcessingError(
            "invalid_path", "Adaptive receipts are invalid or unavailable"
        ) from error

# Copyright (c) 2026 Hans Valerie
"""Sampling coverage, filesystem identity and synthetic resource boundaries."""

from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from tachyron.execution import (
    LOCAL_LIMITS,
    CalibrationPolicy,
    adaptive_storage,
    resources,
)
from tachyron.execution.sampling import (
    ImageProfile,
    choose_sample,
    profile_images,
    sample_manifest,
)
from tachyron.processing import TransformConfig, build_manifest
from tests.benchmark_helpers import example_experiment, make_manifest


def image_dataset(root: Path, count: int = 96) -> Path:
    """Create distinct identities across common format and pixel-size strata."""
    root.mkdir()
    for index in range(count):
        extension = "png" if index % 2 else "jpg"
        dimension = 8 if index % 4 < 2 else 32
        with Image.new("RGB", (dimension, dimension), (index % 255, 20, 40)) as image:
            image.save(root / f"image-{index:04d}.{extension}")
    return root


def test_seeded_sampling_covers_observed_format_and_pixel_strata(
    tmp_path: Path,
) -> None:
    manifest = build_manifest(image_dataset(tmp_path / "input"), LOCAL_LIMITS)
    profiles, reason = profile_images(manifest, lambda: False)
    assert reason is None
    policy = CalibrationPolicy()
    entries, reason = choose_sample(profiles, policy)
    assert reason is None
    assert len(entries) == len(set(entries)) == 32
    assert choose_sample(profiles, policy)[0] == entries
    assert choose_sample(profiles, replace(policy, seed=42))[0] != entries
    assert sample_manifest(manifest, entries).sha256 != manifest.sha256
    categories = {value.entry.relative_path: value.category for value in profiles}
    assert {categories[value.relative_path] for value in entries[::2]} == set(
        categories.values()
    )


def test_rare_unrepresented_pixel_scale_abstains(tmp_path: Path) -> None:
    manifest = build_manifest(image_dataset(tmp_path / "input"), LOCAL_LIMITS)
    profiles, _ = profile_images(manifest, lambda: False)
    exceptional = replace(profiles[-1], width=4096, height=4096)
    sample, reason = choose_sample((*profiles[:-1], exceptional), CalibrationPolicy())
    assert not sample
    assert reason == "sample_coverage_inadequate"


def test_profile_stopping_invalid_input_and_byte_budget_are_explicit(
    tmp_path: Path,
) -> None:
    manifest = make_manifest(tmp_path / "input")
    assert profile_images(manifest, lambda: True)[1] == "calibration_stopped"
    (manifest.root / "0.png").write_bytes(b"changed after freezing")
    assert profile_images(manifest, lambda: False)[1] == "invalid_profile_input"
    oversized = replace(
        manifest,
        entries=tuple(
            replace(manifest.entries[0], size_bytes=32 * 1024**2) for _ in range(17)
        ),
    )
    assert profile_images(oversized, lambda: False)[1] == "profile_byte_budget"


def test_memory_and_affinity_bound_candidates_without_choosing_performance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = replace(
        example_experiment().context.environment,
        logical_cpus=16,
        physical_cpus=8,
        cpu_affinity=(0, 1, 2, 3),
        available_memory_mib=2048,
    )
    monkeypatch.setattr(
        resources, "capture_environment", Mock(return_value=environment)
    )
    monkeypatch.setattr(resources, "sys", Mock(platform="win32"))
    observed = resources.observe_resources(TransformConfig(), 16_777_216, 32 * 1024**2)
    assert observed.candidates == (1,)
    assert observed.cpu_quota_cores is None
    assert observed.memory_budget_mib == 1024
    monkeypatch.setattr(
        resources,
        "capture_environment",
        Mock(return_value=replace(environment, available_memory_mib=None)),
    )
    assert resources.observe_resources(TransformConfig(), 1, 1).candidates == (1,)
    monkeypatch.setattr(
        resources,
        "capture_environment",
        Mock(return_value=replace(environment, available_memory_mib=100)),
    )
    assert not resources.observe_resources(TransformConfig(), 1, 1).candidates


def test_visible_cgroup_limits_include_ancestors_and_mount_root(tmp_path: Path) -> None:
    proc, mount = tmp_path / "proc", tmp_path / "cgroup"
    (proc / "self").mkdir(parents=True)
    leaf = mount / "team" / "job"
    leaf.mkdir(parents=True)
    (proc / "self/mountinfo").write_text(f"1 0 0:1 / {mount} rw - cgroup2 cgroup rw\n")
    (proc / "self/cgroup").write_text("0::/team/job\n")
    (leaf / "cpu.max").write_text("300000 100000\n")
    (mount / "cpu.max").write_text("150000 100000\n")
    (mount / "team/memory.max").write_text(str(1024**3))
    (mount / "team/memory.current").write_text(str(256 * 1024**2))
    assert resources._cgroup_limits(proc, mount) == (1.5, 768)
    (proc / "self/cgroup").write_text("0::/../../outside\n")
    with pytest.raises(ValueError, match="membership"):
        resources._cgroup_limits(proc, mount)


def test_quota_and_cgroup_headroom_narrow_host_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = replace(
        example_experiment().context.environment,
        logical_cpus=16,
        cpu_affinity=tuple(range(16)),
        available_memory_mib=32_000,
    )
    monkeypatch.setattr(
        resources, "capture_environment", Mock(return_value=environment)
    )
    monkeypatch.setattr(resources, "sys", Mock(platform="linux"))
    monkeypatch.setattr(resources, "_cgroup_limits", Mock(return_value=(2.5, 2000)))
    result = resources.observe_resources(TransformConfig(), 1, 1)
    assert result.candidates == (1, 2)
    assert result.memory_budget_mib == 1000
    assert result.constraint_status == "visible_cgroup_v2"


@pytest.fixture
def cgroup_tree(tmp_path: Path) -> tuple[Path, Path]:
    """Provide a recognized unified mount with a nested current membership."""
    proc, mount = tmp_path / "proc", tmp_path / "cgroup"
    (proc / "self").mkdir(parents=True)
    (mount / "team/job").mkdir(parents=True)
    (proc / "self/mountinfo").write_text(
        f"1 0 0:1 / {mount} rw - cgroup2 cgroup rw\n", encoding="utf-8"
    )
    (proc / "self/cgroup").write_text("0::/team/job\n", encoding="utf-8")
    return proc, mount


@pytest.mark.parametrize(
    ("quota", "maximum", "current", "expected"),
    [
        (None, None, None, (None, None)),
        ("max 100000", "max", None, (None, None)),
        ("50000 100000", "1048576", "2097152", (0.5, 0)),
        (None, "0", "0", (None, 0)),
    ],
)
def test_cgroup_absent_unlimited_and_exhausted_observations(
    cgroup_tree: tuple[Path, Path],
    quota: str | None,
    maximum: str | None,
    current: str | None,
    expected: tuple[float | None, float | None],
) -> None:
    proc, mount = cgroup_tree
    for name, value in (
        ("cpu.max", quota),
        ("memory.max", maximum),
        ("memory.current", current),
    ):
        if value is not None:
            (mount / "team/job" / name).write_text(value, encoding="utf-8")
    # A limit outside the recognized mount must never narrow this observation.
    (mount.parent / "cpu.max").write_text("1 100000", encoding="utf-8")
    assert resources._cgroup_limits(proc, mount) == expected


@pytest.mark.parametrize(
    ("quota", "message"),
    [
        ("max 0", "Invalid CPU period"),
        ("max -1", "Invalid CPU period"),
        ("0 100000", "Invalid CPU quota"),
        ("-1 100000", "Invalid CPU quota"),
        ("1", "not enough values"),
        ("1 1 extra", "too many values"),
        ("1" * 257, "Excessive resource observation"),
    ],
)
def test_cgroup_invalid_cpu_observations_remain_errors(
    cgroup_tree: tuple[Path, Path], quota: str, message: str
) -> None:
    proc, mount = cgroup_tree
    (mount / "team/job/cpu.max").write_text(quota, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        resources._cgroup_limits(proc, mount)


@pytest.mark.parametrize(("maximum", "current"), [("-1", "0"), ("1", "-1")])
def test_cgroup_negative_memory_observations_remain_errors(
    cgroup_tree: tuple[Path, Path], maximum: str, current: str
) -> None:
    proc, mount = cgroup_tree
    (mount / "team/job/memory.max").write_text(maximum, encoding="utf-8")
    (mount / "team/job/memory.current").write_text(current, encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid memory observation"):
        resources._cgroup_limits(proc, mount)


def test_cgroup_missing_current_is_not_fabricated_headroom(
    cgroup_tree: tuple[Path, Path],
) -> None:
    proc, mount = cgroup_tree
    (mount / "team/job/memory.max").write_text("1048576", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        resources._cgroup_limits(proc, mount)


_PROFILE_STRATA_CASES: tuple[tuple[list[object], int, str], ...] = (
    ([["PNG", 4]], 3, "Invalid profile strata"),
    ([["GIF", 4, 3]], 3, "Invalid profile strata"),
    ([["PNG", 0, 3]], 3, "Invalid profile strata"),
    ([["PNG", 26, 3]], 3, "Invalid profile strata"),
    ([["PNG", 4, 0]], 0, "Invalid profile strata"),
    ([["PNG", True, 3]], 3, "Expected a nonnegative receipt integer"),
    ([["PNG", 4, 1], ["PNG", 4, 2]], 3, "Duplicate profile stratum"),
    ([["PNG", 4, 2]], 3, "Invalid profile accounting"),
)


@pytest.mark.parametrize(("categories", "profiled", "message"), _PROFILE_STRATA_CASES)
def test_profile_strata_and_accounting_reject_invalid_evidence(
    tmp_path: Path, categories: list[object], profiled: int, message: str
) -> None:
    manifest = make_manifest(tmp_path / "input")
    calibration: dict[str, object] = {
        "profile_categories": categories,
        "profiled_inputs": profiled,
        "sample_paths": [],
        "sample_profiles": [],
    }
    with pytest.raises(ValueError, match=message):
        adaptive_storage._profiles(calibration, manifest)


@pytest.mark.parametrize(
    ("width", "height", "message"),
    [
        (0, 3, "Invalid sample dimensions"),
        (4097, 4096, "Invalid sample dimensions"),
        (16385, 1, "Invalid sample dimensions"),
        (True, 3, "Expected a nonnegative receipt integer"),
    ],
)
def test_profile_dimensions_remain_bounded(
    tmp_path: Path, width: int, height: int, message: str
) -> None:
    manifest = make_manifest(tmp_path / "input")
    metadata = [asdict(ImageProfile(entry, "PNG", 4, 3)) for entry in manifest.entries]
    metadata[0].update(width=width, height=height)
    calibration: dict[str, object] = {
        "profile_categories": [["PNG", 4, 3]],
        "profiled_inputs": 3,
        "sample_paths": [entry.relative_path for entry in manifest.entries],
        "sample_profiles": metadata,
    }
    with pytest.raises(ValueError, match=message):
        adaptive_storage._profiles(calibration, manifest)


@pytest.mark.parametrize("corruption", ["identity", "format", "missing", "half"])
def test_profile_metadata_identity_and_half_sample_coverage(
    tmp_path: Path, corruption: str
) -> None:
    manifest = make_manifest(tmp_path / "input")
    metadata = [asdict(ImageProfile(entry, "PNG", 4, 3)) for entry in manifest.entries]
    calibration: dict[str, object] = {
        "profile_categories": [["PNG", 4, 3]],
        "profiled_inputs": 3,
        "sample_paths": [entry.relative_path for entry in manifest.entries],
        "sample_profiles": metadata,
    }
    # Start from independently valid metadata before changing one invariant.
    assert adaptive_storage._profiles(calibration, manifest) is None
    if corruption == "identity":
        metadata[0]["entry"] = asdict(replace(manifest.entries[0], sha256="0" * 64))
        message = "Sample metadata contradicts input identity"
    elif corruption == "format":
        metadata[0]["format"] = "GIF"
        message = "Sample metadata contradicts input identity"
    elif corruption == "missing":
        metadata.pop()
        message = "Missing sample metadata"
    else:
        metadata[1]["format"] = "JPEG"
        calibration["profile_categories"] = [["PNG", 4, 2], ["JPEG", 4, 1]]
        message = "Sample does not cover observed categories"
    with pytest.raises(ValueError, match=message):
        adaptive_storage._profiles(calibration, manifest)

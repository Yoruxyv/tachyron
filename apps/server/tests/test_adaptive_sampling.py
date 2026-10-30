# Copyright (c) 2026 Hans Valerie
"""Sampling coverage, filesystem identity and synthetic resource boundaries."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from tachyron.execution import LOCAL_LIMITS, CalibrationPolicy, resources
from tachyron.execution.sampling import choose_sample, profile_images, sample_manifest
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

# Copyright (c) 2026 Hans Valerie
"""Observed feasibility bounds, with explicit unavailable platform constraints."""

import math
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from tachyron.benchmarking.models import Environment
from tachyron.benchmarking.provenance import capture_environment
from tachyron.execution.calibration import WORKERS
from tachyron.processing import TransformConfig


@dataclass(frozen=True)
class Resources:
    """A resource snapshot and conservative heuristic, never a reservation."""

    environment: Environment
    cpu_quota_cores: float | None
    cgroup_memory_headroom_mib: float | None
    constraint_status: str
    memory_budget_mib: float | None
    estimated_worker_mib: float
    candidates: tuple[int, ...]


def _text(path: Path, maximum: int = 256) -> str:
    with path.open(encoding="utf-8") as stream:
        value = stream.read(maximum + 1)
    if len(value) > maximum:
        raise ValueError("Excessive resource observation")
    return value.strip()


def _cpu_quota(directory: Path) -> float | None:
    """Read one visible CPU limit, validating its period even when unlimited."""
    path = directory / "cpu.max"
    if not path.exists():
        return None
    quota, period = _text(path).split()
    if int(period) <= 0:
        raise ValueError("Invalid CPU period")
    if quota == "max":
        return None
    if int(quota) <= 0:
        raise ValueError("Invalid CPU quota")
    return int(quota) / int(period)


def _memory_headroom(directory: Path) -> float | None:
    """Read finite memory headroom without treating missing usage as capacity."""
    path = directory / "memory.max"
    if not path.exists():
        return None
    maximum = _text(path)
    if maximum == "max":
        return None
    current = int(_text(directory / "memory.current"))
    if current < 0 or int(maximum) < 0:
        raise ValueError("Invalid memory observation")
    return max(0, int(maximum) - current) / 1024**2


def _cgroup_limits(proc: Path, mount: Path) -> tuple[float | None, float | None]:
    """Read visible v2 ancestors only on the recognized unified root mount."""
    mounts = _text(proc / "self/mountinfo", 1024**2).splitlines()
    if not any(
        " - cgroup2 " in line and line.split()[3:5] == ["/", str(mount)]
        for line in mounts
    ):
        raise ValueError("Unrecognized cgroup mount")
    rows = _text(proc / "self/cgroup", 64 * 1024).splitlines()
    row = next(value[3:] for value in rows if value.startswith("0::"))
    relative = PurePosixPath(row)
    if not relative.is_absolute() or ".." in relative.parts or len(relative.parts) > 64:
        raise ValueError("Unrecognized cgroup membership")
    directory = mount.joinpath(*relative.parts[1:])
    quotas: list[float] = []
    memory: list[float] = []
    for parent in (directory, *directory.parents):
        quota = _cpu_quota(parent)
        if quota is not None:
            quotas.append(quota)
        headroom = _memory_headroom(parent)
        if headroom is not None:
            memory.append(headroom)
        if parent == mount:
            break
    return min(quotas) if quotas else None, min(memory) if memory else None


def observe_resources(
    config: TransformConfig, pixels: int, compressed_bytes: int
) -> Resources:
    """Bound candidates using current affinity, visible quota and memory headroom.

    Missing memory/capacity observations restrict calibration to one worker.
    Memory estimates include multiple decoder buffers and process overhead;
    sampled RSS can still miss short peaks, and available memory can change.
    """
    environment = capture_environment()
    quota = headroom = None
    status = "unsupported_platform_constraints"
    if sys.platform == "linux":
        try:
            quota, headroom = _cgroup_limits(Path("/proc"), Path("/sys/fs/cgroup"))
            status = "visible_cgroup_v2"
        except (OSError, ValueError, StopIteration):
            status = "unavailable_platform_constraints"
    capacities = [
        value
        for value in (
            environment.logical_cpus,
            len(environment.cpu_affinity) if environment.cpu_affinity else None,
        )
        if value is not None
    ]
    if quota is not None:
        capacities.append(max(1, math.floor(quota)))
    capacity = min(capacities) if capacities else 1
    available = [
        value
        for value in (environment.available_memory_mib, headroom)
        if value is not None
    ]
    budget = (
        max(0.0, min(min(available) * 0.5, min(available) - 512)) if available else None
    )
    estimate = (
        64
        + (32 * pixels + 2 * compressed_bytes + 16 * config.width * config.height)
        / 1024**2
    )
    candidates = tuple(
        worker
        for worker in WORKERS
        if worker <= capacity and (budget is None or worker * estimate <= budget)
    )
    if budget is None or not capacities:
        candidates = tuple(worker for worker in candidates if worker == 1)
    return Resources(environment, quota, headroom, status, budget, estimate, candidates)

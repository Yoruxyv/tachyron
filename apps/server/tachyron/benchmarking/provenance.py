# Copyright (c) 2026 Hans Valerie
"""Loaded-package identity and bounded, portable runtime/hardware provenance."""

import hashlib
import importlib.metadata
import os
import platform
import shutil
import socket
import subprocess
from pathlib import Path

import psutil
from PIL import __version__ as pillow_version

from tachyron.benchmarking.models import Environment


def source_hash() -> str:
    """Hash loaded package sources, using relative names rather than caller paths."""
    package = Path(__file__).resolve().parents[1]
    checksum = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        checksum.update(path.relative_to(package).as_posix().encode())
        checksum.update(b"\0")
        checksum.update(path.read_bytes())
        checksum.update(b"\0")
    return checksum.hexdigest()


def _git_identity() -> tuple[str | None, bool | None]:
    package = Path(__file__).resolve().parents[1]
    root = package.parents[2]
    git = shutil.which("git")
    if (
        git is None
        or not (root / ".git").exists()
        or root / "apps/server/tachyron" != package
    ):
        return None, None
    try:
        commit = (
            subprocess.run(  # noqa: S603 -- Resolved Git, fixed read-only argv and loaded source root.
                [git, "-C", str(root), "rev-parse", "HEAD"],
                capture_output=True,
                check=True,
                timeout=3,
            )
            .stdout.decode("ascii")
            .strip()
        )
        dirty = subprocess.run(  # noqa: S603 -- Fixed read-only Git status; paths are never exported.
            [
                git,
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
            ],
            capture_output=True,
            check=True,
            timeout=3,
        ).stdout
        return commit, bool(dirty)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None, None


def capture_environment() -> Environment:
    """Record obtainable hardware/runtime facts without inventing unavailable data."""
    try:
        memory = psutil.virtual_memory()
        total_memory, available_memory = (
            memory.total / 1024**2,
            memory.available / 1024**2,
        )
    except (psutil.Error, OSError, NotImplementedError):
        total_memory = available_memory = None
    try:
        affinity = tuple(psutil.Process(os.getpid()).cpu_affinity())
    except (psutil.Error, OSError, AttributeError, NotImplementedError):
        affinity = None
    commit, dirty = _git_identity()
    return Environment(
        python_version=platform.python_version(),
        pillow_version=pillow_version,
        psutil_version=psutil.__version__,
        tachyron_version=importlib.metadata.version("tachyron"),
        os_name=platform.system() or "unknown",
        os_release=platform.release() or "unknown",
        architecture=platform.machine() or "unknown",
        host_id=hashlib.sha256(socket.gethostname().encode()).hexdigest(),
        source_hash=source_hash(),
        cpu_model=platform.processor() or None,
        logical_cpus=psutil.cpu_count(),
        physical_cpus=psutil.cpu_count(logical=False),
        cpu_affinity=affinity,
        total_memory_mib=total_memory,
        available_memory_mib=available_memory,
        git_commit=commit,
        git_dirty=dirty,
    )

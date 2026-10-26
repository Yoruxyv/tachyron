# Copyright (c) 2026 Hans Valerie
"""Bounded, stable dataset discovery and revalidation of input snapshots."""

import hashlib
import json
import os
import stat
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from tachyron.processing.models import (
    DEFAULT_LIMITS,
    DatasetManifest,
    ManifestEntry,
    ProcessingError,
    ResourceLimits,
)


def reject_link(info: os.stat_result) -> None:
    """Reject symlinks and Windows reparse points, including directory junctions."""
    if (
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ProcessingError(
            "invalid_path", "Dataset and output links are not allowed"
        )


def dataset_root(path: Path) -> Path:
    """Resolve an explicitly selected root, rejecting links and non-directories."""
    reject_link(path.lstat())
    root = path.resolve(strict=True)
    if not root.is_dir():
        raise ProcessingError("invalid_path", "Dataset root must be a directory")
    return root


def validate_relative(name: str) -> tuple[str, ...]:
    """Validate a manifest name using the same portable policy on every OS."""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ProcessingError(
            "invalid_path", "Manifest paths must be valid UTF-8"
        ) from error
    parts = PurePosixPath(name).parts
    if (
        not name
        or "\\" in name
        or ":" in name
        or "\x00" in name
        or PurePosixPath(name).is_absolute()
        or ".." in parts
        or PurePosixPath(name).as_posix() != name
        or not parts
    ):
        raise ProcessingError(
            "invalid_path", "Manifest path is not a safe relative path"
        )
    return parts


def read_input(root: Path, name: str, limits: ResourceLimits) -> bytes:
    """Read a bounded regular file, rejecting links and detected concurrent edits.

    Args:
        root: Explicit allowed dataset root.
        name: Portable relative manifest path.
        limits: Compressed input byte limit.

    Returns:
        A bounded snapshot used for hashing and decoding.

    Raises:
        ProcessingError: Unsafe path, resource excess, or changed input.
        OSError: Input cannot be read.
    """
    parts = validate_relative(name)
    canonical = dataset_root(root)
    path = canonical
    for part in parts:
        path /= part
        reject_link(path.lstat())
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not path.resolve(strict=True).is_relative_to(
        canonical
    ):
        raise ProcessingError(
            "invalid_path", "Input must be a regular file inside the dataset"
        )
    if info.st_size > limits.max_file_bytes:
        raise ProcessingError("resource_limit", "Input byte limit exceeded")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = os.open(path, flags)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or (before.st_dev, before.st_ino) != (
                info.st_dev,
                info.st_ino,
            ):
                raise ProcessingError("input_changed", "Input changed while opening")
            data = stream.read(limits.max_file_bytes + 1)
            after = os.fstat(stream.fileno())
    finally:
        os.close(descriptor)
    if len(data) > limits.max_file_bytes:
        raise ProcessingError("resource_limit", "Input byte limit exceeded")
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) or len(data) != before.st_size:
        raise ProcessingError("input_changed", "Input changed while reading")
    return data


def manifest_digest(entries: tuple[ManifestEntry, ...]) -> str:
    """Hash ordered path, byte-size, and content identities independently of root."""
    encoded = json.dumps(
        [asdict(entry) for entry in entries], sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _discover(root: Path, limits: ResourceLimits) -> list[str]:
    pending = [(root, 0)]
    names: list[str] = []
    entries = total_bytes = 0
    while pending:
        directory, depth = pending.pop()
        reject_link(directory.lstat())
        with os.scandir(directory) as children:
            for child in children:
                entries += 1
                if entries > limits.max_entries:
                    raise ProcessingError(
                        "resource_limit", "Dataset entry limit exceeded"
                    )
                info = child.stat(follow_symlinks=False)
                reject_link(info)
                path = Path(child.path)
                if stat.S_ISDIR(info.st_mode):
                    if depth + 1 > limits.max_depth:
                        raise ProcessingError(
                            "resource_limit", "Dataset depth limit exceeded"
                        )
                    pending.append((path, depth + 1))
                elif stat.S_ISREG(info.st_mode):
                    names.append(path.relative_to(root).as_posix())
                    total_bytes += info.st_size
                    if (
                        len(names) > limits.max_files
                        or total_bytes > limits.max_total_bytes
                    ):
                        raise ProcessingError(
                            "resource_limit", "Dataset file or byte limit exceeded"
                        )
                else:
                    raise ProcessingError(
                        "invalid_path", "Dataset contains a non-regular file"
                    )
    return sorted(names)


def build_manifest(
    root: Path, limits: ResourceLimits = DEFAULT_LIMITS
) -> DatasetManifest:
    """Discover every regular file and fix its sorted, hashed workload identity.

    Non-image files remain candidates and receive explicit processing failures.
    Directory, byte and file limits fail discovery before any output is created.

    Args:
        root: Explicit local dataset directory.
        limits: Safety caps for discovery and processing.

    Returns:
        An immutable, relocatable workload description with a canonical root.

    Raises:
        ProcessingError: Invalid, empty, unreadable, or excessive dataset.
    """
    try:
        canonical = dataset_root(root)
        names = _discover(canonical, limits)
        if not names:
            raise ProcessingError("invalid_path", "Dataset is empty")
        entries: list[ManifestEntry] = []
        total_bytes = 0
        for name in names:
            data = read_input(canonical, name, limits)
            total_bytes += len(data)
            if total_bytes > limits.max_total_bytes:
                raise ProcessingError("resource_limit", "Dataset byte limit exceeded")
            entries.append(
                ManifestEntry(name, len(data), hashlib.sha256(data).hexdigest())
            )
        ordered = tuple(entries)
        return DatasetManifest(canonical, ordered, manifest_digest(ordered), limits)
    except OSError as error:
        raise ProcessingError("io_error", "Dataset cannot be read") from error


def validate_manifest(manifest: DatasetManifest) -> None:
    """Reject forged or inconsistent manifests before creating a run directory."""
    entries = manifest.entries
    if (
        not entries
        or len(entries) > manifest.limits.max_files
        or manifest.total_bytes > manifest.limits.max_total_bytes
    ):
        raise ProcessingError("resource_limit", "Manifest file or byte limit exceeded")
    names = [entry.relative_path for entry in entries]
    if names != sorted(set(names)) or manifest_digest(entries) != manifest.sha256:
        raise ProcessingError("input_changed", "Manifest identity or order is invalid")
    for entry in entries:
        validate_relative(entry.relative_path)
        if not 0 <= entry.size_bytes <= manifest.limits.max_file_bytes:
            raise ProcessingError(
                "resource_limit", "Manifest input byte limit exceeded"
            )

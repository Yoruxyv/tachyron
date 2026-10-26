# Copyright (c) 2026 Hans Valerie
"""Immutable processing contracts, independent of HTTP and benchmark timing."""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from PIL import __version__ as pillow_version

ErrorCode = Literal[
    "invalid_path",
    "input_changed",
    "invalid_image",
    "unsupported_image",
    "resource_limit",
    "output_collision",
    "io_error",
]


class ProcessingError(ValueError):
    """An expected validation failure with a safe message and stable code."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: ErrorCode = code


@dataclass(frozen=True, slots=True)
class TransformConfig:
    """Exact-size Lanczos resize followed by 8-bit grayscale PNG encoding."""

    width: int = 256
    height: int = 256
    output_format: Literal["PNG"] = "PNG"

    def __post_init__(self) -> None:
        for dimension in (self.width, self.height):
            if isinstance(dimension, bool) or not isinstance(dimension, int):
                raise TypeError("Output dimensions must be integers")
            if not 1 <= dimension <= 4096:
                raise ValueError("Output dimensions must be between 1 and 4096")
        if self.output_format not in ("PNG",):
            raise ValueError("Only lossless PNG output is supported")


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """Local safety caps; callers may deliberately choose smaller workloads."""

    max_files: int = 1000
    max_file_bytes: int = 32 * 1024 * 1024
    max_total_bytes: int = 512 * 1024 * 1024
    max_pixels: int = 16_777_216
    max_output_pixels: int = 268_435_456
    max_dimension: int = 16_384
    max_entries: int = 10_000
    max_depth: int = 32

    def __post_init__(self) -> None:
        for value in asdict(self).values():
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError("Resource limits must be integers")
            if value <= 0:
                raise ValueError("Resource limits must be positive")

    def check_output(self, config: TransformConfig, count: int = 1) -> None:
        """Bound requested grayscale output pixels before allocating or writing."""
        if config.width * config.height * count > self.max_output_pixels:
            raise ProcessingError("resource_limit", "Batch output pixel limit exceeded")


DEFAULT_LIMITS = ResourceLimits()


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """One relative POSIX path and the identity of its compressed input bytes."""

    relative_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    """Ordered workload snapshot; the digest excludes its machine-specific root."""

    root: Path
    entries: tuple[ManifestEntry, ...]
    sha256: str
    limits: ResourceLimits

    @property
    def total_bytes(self) -> int:
        """Return the compressed size of the fixed workload."""
        return sum(entry.size_bytes for entry in self.entries)


@dataclass(frozen=True, slots=True)
class ImageOutcome:
    """Exactly one successful output or a sanitized failure for one input."""

    relative_path: str
    output_name: str | None = None
    error_code: ErrorCode | None = None
    input_size: tuple[int, int] | None = None

    @property
    def success(self) -> bool:
        """Return whether a complete image was published."""
        return self.output_name is not None and self.error_code is None


@dataclass(frozen=True, slots=True)
class BatchResult:
    """Complete accounting of an execution attempt, including per-image failures."""

    manifest: DatasetManifest
    config: TransformConfig
    output_dir: Path
    outcomes: tuple[ImageOutcome, ...]
    requested_workers: int | None = None

    @property
    def succeeded(self) -> int:
        """Return the count of published images."""
        return sum(outcome.success for outcome in self.outcomes)

    @property
    def failed(self) -> int:
        """Return the count of failed inputs."""
        return len(self.outcomes) - self.succeeded

    def to_dict(self) -> dict[str, object]:
        """Return a portable report without absolute input/output paths or timing."""
        report: dict[str, object] = {
            "schema_version": 1,
            "pipeline": "resize-grayscale-v1",
            "pillow_version": pillow_version,
            "manifest": {
                "sha256": self.manifest.sha256,
                "total_bytes": self.manifest.total_bytes,
                "entries": [asdict(entry) for entry in self.manifest.entries],
            },
            "limits": asdict(self.manifest.limits),
            "config": asdict(self.config),
            "succeeded": self.succeeded,
            "failed": self.failed,
            "outcomes": [asdict(outcome) for outcome in self.outcomes],
        }
        if self.requested_workers is not None:
            report["execution"] = {
                "mode": "process_pool",
                "requested_workers": self.requested_workers,
                "start_method": "spawn",
                "max_in_flight": 2 * self.requested_workers,
            }
        return report

# Copyright (c) 2026 Hans Valerie
"""Identity-checked header profiling and deterministic equal-population strata."""

import hashlib
import io
import warnings
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image

from tachyron.execution.calibration import (
    PROFILE_BYTES,
    SAMPLE_PIXELS,
    CalibrationPolicy,
)
from tachyron.processing import DatasetManifest, ManifestEntry, ProcessingError
from tachyron.processing.dataset import manifest_digest, read_input


@dataclass(frozen=True)
class ImageProfile:
    """Header observation of an unchanged input; full validation happens in trials."""

    entry: ManifestEntry
    format: str
    width: int
    height: int

    @property
    def pixels(self) -> int:
        """Return original decoded pixel count."""
        return self.width * self.height

    @property
    def category(self) -> tuple[str, int]:
        """Describe format and power-of-two pixel scale for coverage checks."""
        return self.format, self.pixels.bit_length()


def profile_images(
    manifest: DatasetManifest, stopped: Callable[[], bool]
) -> tuple[tuple[ImageProfile, ...], str | None]:
    """Inspect every header within a declared compressed-byte/time budget.

    Partial profiles are insufficient for calibration. Reads reuse the same
    filesystem protections and recheck frozen content before trusting headers.
    """
    if manifest.total_bytes > PROFILE_BYTES:
        return (), "profile_byte_budget"
    profiles: list[ImageProfile] = []
    try:
        for entry in manifest.entries:
            if stopped():
                return tuple(profiles), "calibration_stopped"
            data = read_input(manifest.root, entry.relative_path, manifest.limits)
            if (
                len(data) != entry.size_bytes
                or hashlib.sha256(data).hexdigest() != entry.sha256
            ):
                raise ProcessingError("input_changed", "Input changed during profiling")
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                with Image.open(io.BytesIO(data), formats=("PNG", "JPEG")) as image:
                    width, height = image.size
                    if (
                        width * height > manifest.limits.max_pixels
                        or max(width, height) > manifest.limits.max_dimension
                    ):
                        return tuple(profiles), "profile_resource_limit"
                    profiles.append(
                        ImageProfile(entry, image.format or "unknown", width, height)
                    )
    except (OSError, ValueError, SyntaxError, Warning, Image.DecompressionBombError):
        return tuple(profiles), "invalid_profile_input"
    return tuple(profiles), None


def choose_sample(
    profiles: tuple[ImageProfile, ...], policy: CalibrationPolicy
) -> tuple[tuple[ManifestEntry, ...], str | None]:
    """Choose one seeded hash-ranked image from each equal-population stratum.

    Sort strata by format, decoded pixels and compressed bytes. Abstain if either
    sample misses an observed format/pixel category, or exceeds the pixel budget.
    """
    count = policy.sample_files
    if len(profiles) < 2 * count:
        return (), "workload_too_small"
    ordered = sorted(
        profiles,
        key=lambda value: (
            value.format,
            value.pixels,
            value.entry.size_bytes,
            value.entry.relative_path,
        ),
    )
    sample: list[ImageProfile] = []
    for index in range(count):
        group = ordered[
            index * len(ordered) // count : (index + 1) * len(ordered) // count
        ]
        sample.append(
            min(
                group,
                key=lambda value: hashlib.sha256(
                    f"{policy.seed}:{value.entry.relative_path}:{value.entry.sha256}".encode()
                ).digest(),
            )
        )
    categories = {value.category for value in profiles}
    for index in range(0, count, 2):
        if hashlib.sha256(f"{policy.seed}:half:{index}".encode()).digest()[0] & 1:
            sample[index], sample[index + 1] = sample[index + 1], sample[index]
    if any(
        {value.category for value in values} != categories
        for values in (sample, sample[::2])
    ):
        return (), "sample_coverage_inadequate"
    if sum(value.pixels for value in sample) > SAMPLE_PIXELS:
        return (), "sample_pixel_budget"
    # Preserve the quantile order here so every second image is the smaller sample.
    return tuple(value.entry for value in sample), None


def sample_manifest(
    manifest: DatasetManifest, entries: tuple[ManifestEntry, ...]
) -> DatasetManifest:
    """Create a valid subset identity without copying or mutating originals."""
    ordered = tuple(sorted(entries, key=lambda entry: entry.relative_path))
    return DatasetManifest(
        manifest.root, ordered, manifest_digest(ordered), manifest.limits
    )

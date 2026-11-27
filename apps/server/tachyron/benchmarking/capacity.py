# Copyright (c) 2026 Hans Valerie
"""Conservative host admission for custom benchmark schedules."""

from collections.abc import Callable

from tachyron.benchmarking.models import BenchmarkCapacityError, CustomBenchmarkConfig
from tachyron.execution.resources import observe_resources
from tachyron.execution.sampling import profile_images
from tachyron.processing import DatasetManifest, TransformConfig


def admit_custom(
    manifest: DatasetManifest,
    transform: TransformConfig,
    config: CustomBenchmarkConfig,
    cancelled: Callable[[], bool] | None,
) -> None:
    """Verify workload budgets and current host capacity before any pool starts.

    Args:
        manifest: Frozen benchmark evidence source.
        transform: Shared output configuration.
        config: Canonical custom comparison schedule.
        cancelled: Optional cooperative cancellation predicate.

    Raises:
        BenchmarkError: Work or observed CPU and memory capacity exceed supported bounds.
    """
    config.validate_budget(len(manifest.entries), transform)
    profiles, error = profile_images(manifest, cancelled or (lambda: False))
    if cancelled is not None and cancelled():
        return
    pixels = (
        max(profile.pixels for profile in profiles)
        if profiles and error is None
        else manifest.limits.max_pixels
    )
    resources = observe_resources(
        transform,
        pixels,
        max((entry.size_bytes for entry in manifest.entries), default=0),
        worker_counts=config.counts,
    )
    if resources.candidates != config.counts:
        raise BenchmarkCapacityError(
            "Requested comparison exceeds observed host capacity"
        )

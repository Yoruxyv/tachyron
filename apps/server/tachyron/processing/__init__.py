# Copyright (c) 2026 Hans Valerie
"""Reusable sequential image processing; adapters depend on this package."""

import json
from pathlib import Path

from tachyron.processing.dataset import build_manifest, dataset_root, validate_manifest
from tachyron.processing.models import (
    BatchResult,
    DatasetManifest,
    ImageOutcome,
    ManifestEntry,
    ProcessingError,
    ResourceLimits,
    TransformConfig,
)
from tachyron.processing.processor import process_image, publish_bytes

__all__ = [
    "BatchResult",
    "DatasetManifest",
    "ImageOutcome",
    "ManifestEntry",
    "ProcessingError",
    "ResourceLimits",
    "TransformConfig",
    "build_manifest",
    "finish_run",
    "prepare_run",
    "process_image",
    "run_sequential",
]


def prepare_run(
    manifest: DatasetManifest, output_dir: Path, config: TransformConfig
) -> Path:
    """Validate the workload and exclusively create its owned output directory.

    Args:
        manifest: Immutable workload and resource policy from discovery.
        output_dir: New per-run directory whose parent already exists.
        config: Identical transformation for every entry.

    Returns:
        The canonical new run directory outside the dataset.

    Raises:
        ProcessingError: Invalid manifest, resource excess, or unsafe/unwritable output.
    """
    try:
        validate_manifest(manifest)
        manifest.limits.check_output(config, len(manifest.entries))
        root = dataset_root(manifest.root)
        parent = output_dir.parent.resolve(strict=True)
        destination = parent / output_dir.name
        if destination.is_relative_to(root):
            raise ProcessingError("invalid_path", "Outputs must be outside the dataset")
        destination.mkdir(mode=0o700)
        return destination
    except FileExistsError as error:
        raise ProcessingError(
            "output_collision", "Run directory must not already exist"
        ) from error
    except OSError as error:
        raise ProcessingError("io_error", "Run directory cannot be created") from error


def finish_run(
    manifest: DatasetManifest,
    output_dir: Path,
    config: TransformConfig,
    outcomes: tuple[ImageOutcome, ...],
    *,
    workers: int | None = None,
) -> BatchResult:
    """Publish complete, ordered accounting shared by both execution modes.

    Args:
        manifest: Fixed input identity and ordering.
        output_dir: Owned directory returned by prepare_run.
        config: Transformation applied to every input.
        outcomes: Exactly one result for each manifest entry, in manifest order.
        workers: Requested process-pool size, or None for sequential execution.

    Returns:
        The complete result, also published as run.json.

    Raises:
        ProcessingError: Inconsistent accounting or failed report publication.
    """
    if tuple(outcome.relative_path for outcome in outcomes) != tuple(
        entry.relative_path for entry in manifest.entries
    ):
        raise ProcessingError(
            "input_changed", "Batch outcomes do not match the manifest"
        )
    result = BatchResult(
        manifest, config, output_dir, outcomes, requested_workers=workers
    )
    try:
        report = (json.dumps(result.to_dict(), sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
        publish_bytes(output_dir, "run.json", report)
        return result
    except FileExistsError as error:
        raise ProcessingError(
            "output_collision", "Run report must not already exist"
        ) from error
    except OSError as error:
        raise ProcessingError("io_error", "Run report cannot be written") from error


def run_sequential(
    manifest: DatasetManifest, output_dir: Path, config: TransformConfig
) -> BatchResult:
    """Process a fixed manifest in order, retaining all successes and failures.

    Args:
        manifest: Immutable workload and resource policy from discovery.
        output_dir: New per-run directory whose parent already exists.
        config: Identical transformation for every entry.

    Returns:
        Complete per-entry accounting, also published as run.json.

    Raises:
        ProcessingError: Invalid workload/output or failed report creation.
            Completed images remain after report failure or interruption.
    """
    destination = prepare_run(manifest, output_dir, config)
    outcomes = tuple(
        process_image(manifest.root, entry, destination, config, manifest.limits)
        for entry in manifest.entries
    )
    return finish_run(manifest, destination, config, outcomes)

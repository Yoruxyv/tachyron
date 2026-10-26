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
    "process_image",
    "run_sequential",
]


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
        ProcessingError: Invalid manifest/output or failed run/report creation.
            Completed image files are retained after report failure or interruption.
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
        outcomes = tuple(
            process_image(root, entry, destination, config, manifest.limits)
            for entry in manifest.entries
        )
        result = BatchResult(manifest, config, destination, outcomes)
        report = (json.dumps(result.to_dict(), sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
        publish_bytes(destination, "run.json", report)
        return result
    except FileExistsError as error:
        raise ProcessingError(
            "output_collision", "Run directory must not already exist"
        ) from error
    except OSError as error:
        raise ProcessingError(
            "io_error", "Run directory or report cannot be written"
        ) from error

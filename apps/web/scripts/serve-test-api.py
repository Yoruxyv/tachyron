# Copyright (c) 2026 Hans Valerie
"""Serve the real API with isolated state for browser tests and advisory audits."""

import argparse
import os
import sys
import tempfile
from pathlib import Path
from threading import Thread
from time import sleep

import uvicorn
from PIL import Image

from tachyron.api import create_app
from tachyron.processing import (
    ImageOutcome,
    ManifestEntry,
    ResourceLimits,
    TransformConfig,
)
from tachyron.processing.processor import process_image
from tachyron.settings import Settings
from tachyron.workers import operational


def prepare_dataset(root: Path) -> dict[str, Path]:
    """Create the fixed browser-test registry inside the caller-owned workspace.

    Args:
        root: Existing temporary directory owned by the server lifespan.

    Returns:
        The browser-test identifier mapped to eight deterministic RGB PNGs.

    Raises:
        OSError: Image storage cannot be created or written.
    """
    dataset = root / "images"
    dataset.mkdir()
    for index in range(8):
        with Image.new("RGB", (16, 12), (index * 25, 60, 90)) as image:
            image.save(dataset / f"{index}.png")
    return {"browser-test": dataset}


def prepare_operational_datasets(root: Path) -> dict[str, Path]:
    """Register generated inputs for real API and dashboard executions."""
    datasets: dict[str, Path] = {}
    for name, count in (
        ("comparison-test", 16),
        ("monitor-manual", 224),
        ("monitor-adaptive", 640),
        ("monitor-worker-exit", 32),
    ):
        dataset = root / name
        dataset.mkdir()
        sample = dataset / "0000.png"
        with Image.new("RGB", (24, 18), (90, 130, 60)) as image:
            image.save(sample)
        contents = sample.read_bytes()
        for index in range(1, count):
            (dataset / f"{index:04}.png").write_bytes(contents)
        datasets[name] = dataset
    return datasets


def observed_test_image(
    root: Path,
    entry: ManifestEntry,
    output_dir: Path,
    config: TransformConfig,
    limits: ResourceLimits,
) -> ImageOutcome:
    """Provide time to reconnect or cancel, or exercise an actual child exit."""
    if root.name == "monitor-manual":
        sleep(0.065)
    if root.name == "monitor-worker-exit":
        sleep(0.5)
        os._exit(23)
    return process_image(root, entry, output_dir, config, limits)


def wait_for_owner(server: uvicorn.Server) -> None:
    """Request cooperative shutdown when parent stdin closes or sends a byte.

    Args:
        server: Server owned by the caller; its lifespan performs actual cleanup.
    """
    if sys.stdin is not None:
        sys.stdin.read(1)
    server.should_exit = True


def serve(
    port: int,
    *,
    with_test_dataset: bool,
    stdio_shutdown: bool,
    with_operational_datasets: bool = False,
) -> None:
    """Own temporary history and optionally generated test-only images.

    Args:
        port: Loopback-only HTTP port.
        with_test_dataset: Register eight tiny generated images for functional E2E.
        stdio_shutdown: Shut down gracefully when the owning Node process closes stdin.
        with_operational_datasets: Register live-monitor inputs and enable test-only faults.

    The audit default has no datasets or history and cannot start image work.
    Temporary files are removed only after the API lifespan finishes cleanup.
    """
    with tempfile.TemporaryDirectory(prefix="tachyron-browser-") as directory:
        root = Path(directory)
        datasets = prepare_dataset(root) if with_test_dataset else {}
        if with_operational_datasets:
            datasets.update(prepare_operational_datasets(root))
            operational.process_image = observed_test_image
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(Settings(datasets=datasets, data_dir=root / "history")),
                host="127.0.0.1",
                port=port,
                log_level="warning",
            )
        )
        if stdio_shutdown:
            Thread(target=wait_for_owner, args=(server,), daemon=True).start()
        server.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--with-test-dataset", action="store_true")
    parser.add_argument("--with-operational-datasets", action="store_true")
    parser.add_argument("--stdio-shutdown", action="store_true")
    arguments = parser.parse_args()
    serve(
        arguments.port,
        with_test_dataset=arguments.with_test_dataset,
        stdio_shutdown=arguments.stdio_shutdown,
        with_operational_datasets=arguments.with_operational_datasets,
    )

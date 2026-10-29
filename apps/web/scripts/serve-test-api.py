# Copyright (c) 2026 Hans Valerie
"""Serve the real API with isolated state for browser tests and advisory audits."""

import argparse
import sys
import tempfile
from pathlib import Path
from threading import Thread

import uvicorn
from PIL import Image

from tachyron.api import create_app
from tachyron.settings import Settings


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


def wait_for_owner(server: uvicorn.Server) -> None:
    """Request cooperative shutdown when parent stdin closes or sends a byte.

    Args:
        server: Server owned by the caller; its lifespan performs actual cleanup.
    """
    if sys.stdin is not None:
        sys.stdin.read(1)
    server.should_exit = True


def serve(port: int, *, with_test_dataset: bool, stdio_shutdown: bool) -> None:
    """Own temporary history and optionally generated test-only images.

    Args:
        port: Loopback-only HTTP port.
        with_test_dataset: Register eight tiny generated images for functional E2E.
        stdio_shutdown: Shut down gracefully when the owning Node process closes stdin.

    The audit default has no datasets or history and cannot start image work.
    Temporary files are removed only after the API lifespan finishes cleanup.
    """
    with tempfile.TemporaryDirectory(prefix="tachyron-browser-") as directory:
        root = Path(directory)
        datasets = prepare_dataset(root) if with_test_dataset else {}
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
    parser.add_argument("--stdio-shutdown", action="store_true")
    arguments = parser.parse_args()
    serve(
        arguments.port,
        with_test_dataset=arguments.with_test_dataset,
        stdio_shutdown=arguments.stdio_shutdown,
    )

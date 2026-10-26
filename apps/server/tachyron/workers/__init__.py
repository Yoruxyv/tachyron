# Copyright (c) 2026 Hans Valerie
"""Owned, bounded spawn-process execution of the existing image processor."""

import logging
import multiprocessing
import os
import signal
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from pathlib import Path

from tachyron.processing import (
    BatchResult,
    DatasetManifest,
    ImageOutcome,
    TransformConfig,
    finish_run,
    prepare_run,
    process_image,
)
from tachyron.workers._startup import (
    check_startup_support,
    defer_startup_interrupt,
    reap_unmanaged_children,
)

logger = logging.getLogger(__name__)


class WorkerExecutionError(RuntimeError):
    """The attempt is incomplete; completed images remain without a final report."""


def _initialize_worker() -> None:
    # The parent owns Ctrl+C and drains running tasks before returning.
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def _shutdown_pool(pool: ProcessPoolExecutor) -> None:
    handler = (
        signal.getsignal(signal.SIGINT)
        if threading.current_thread() is threading.main_thread()
        else None
    )
    if handler is not None:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        reap_unmanaged_children(pool)
        pool.shutdown(wait=True, cancel_futures=True)
    finally:
        if handler is not None:
            signal.signal(signal.SIGINT, handler)


def _cleanup_worker_temps(directory: Path) -> None:
    # Only the exclusive run owner calls this, after every worker has stopped.
    for path in directory.glob(".tachyron-*.tmp"):
        try:
            path.unlink()
        except OSError:
            logger.warning("Unable to remove owned worker temporary file %s", path.name)


def _collect(
    pool: ProcessPoolExecutor,
    manifest: DatasetManifest,
    directory: Path,
    config: TransformConfig,
    window: int,
) -> tuple[ImageOutcome, ...]:
    pending: dict[Future[ImageOutcome], int] = {}
    completed: dict[int, ImageOutcome] = {}
    cursor = 0
    while cursor < len(manifest.entries) or pending:
        while cursor < len(manifest.entries) and len(pending) < window:
            with defer_startup_interrupt():
                future = pool.submit(
                    process_image,
                    manifest.root,
                    manifest.entries[cursor],
                    directory,
                    config,
                    manifest.limits,
                )
                pending[future] = cursor
                cursor += 1
        done, _ = wait(pending, return_when=FIRST_COMPLETED)
        for future in sorted(done, key=pending.__getitem__):
            index = pending.pop(future)
            outcome = future.result()
            if outcome.relative_path != manifest.entries[index].relative_path:
                raise WorkerExecutionError("Worker result does not match its input")
            completed[index] = outcome
    return tuple(completed[index] for index in range(len(manifest.entries)))


def _execute(
    manifest: DatasetManifest, directory: Path, config: TransformConfig, workers: int
) -> tuple[ImageOutcome, ...]:
    pool: ProcessPoolExecutor | None = None
    try:
        with defer_startup_interrupt():
            pool = ProcessPoolExecutor(
                max_workers=workers,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=_initialize_worker,
            )
            check_startup_support(pool)
        return _collect(pool, manifest, directory, config, 2 * workers)
    finally:
        if pool is not None:
            _shutdown_pool(pool)
        _cleanup_worker_temps(directory)


def run_parallel(
    manifest: DatasetManifest, output_dir: Path, config: TransformConfig, workers: int
) -> BatchResult:
    """Execute a fixed workload with an explicitly selected spawn-process pool.

    At most twice the worker count is submitted but not yet collected. Expected
    image failures remain outcomes; unexpected executor/worker failures abort the
    attempt without run.json. Cancellable pending futures are cancelled; work
    already dispatched to a child may still run and is drained before returning.

    Args:
        manifest: Immutable ordered workload and resource policy.
        output_dir: New per-run directory outside the dataset.
        config: Existing Pillow transformation shared with sequential execution.
        workers: Explicit pool limit: 1, 2, 4, or 8. Oversubscription warns.

    Returns:
        Complete manifest-ordered outcomes and a portable run.json report.

    Raises:
        TypeError: Worker count is not an integer (including booleans).
        ValueError: Worker count is unsupported.
        ProcessingError: Workload/output validation or final report write failed.
        WorkerExecutionError: A worker or pool failed; completed images remain.
        KeyboardInterrupt: Interrupted; shutdown finishes before propagation.
    """
    if isinstance(workers, bool) or not isinstance(workers, int):
        raise TypeError("Worker count must be an integer")
    if workers not in (1, 2, 4, 8):
        raise ValueError("Worker count must be 1, 2, 4, or 8")
    cpu_count = os.cpu_count()
    if cpu_count is not None and workers > cpu_count:
        logger.warning(
            "Requested %d workers exceeds %d reported logical CPUs; preserving explicit selection",
            workers,
            cpu_count,
        )
    directory = prepare_run(manifest, output_dir, config)
    try:
        outcomes = _execute(manifest, directory, config, workers)
    except BaseException as error:
        if isinstance(error, KeyboardInterrupt):
            raise
        # A child's SystemExit must not terminate the parent with a success code.
        raise WorkerExecutionError(
            "Parallel execution failed; completed outputs retained; no complete run report"
        ) from error
    return finish_run(manifest, directory, config, outcomes, workers=workers)

# Copyright (c) 2026 Hans Valerie
"""Accounted fixed-size pools for operational runs; scientific execution is separate."""

import hashlib
import multiprocessing
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

from tachyron.processing import DatasetManifest, ImageOutcome, TransformConfig
from tachyron.processing.processor import process_image
from tachyron.workers import (
    WorkerExecutionError,
    _cleanup_worker_temps,
    _initialize_worker,
    _shutdown_pool,
)
from tachyron.workers._startup import check_startup_support, defer_startup_interrupt


@dataclass(frozen=True)
class PoolAccounting:
    """Confirmed outcomes, proven unstarted indices and conservatively unknown work."""

    outcomes: tuple[ImageOutcome | None, ...]
    not_started: frozenset[int]
    cancel_requested: bool
    error_code: str | None


class _Collection:
    """Own bounded submission, acknowledgements and stopping for one pool."""

    def __init__(
        self,
        manifest: DatasetManifest,
        directory: Path,
        config: TransformConfig,
        on_outcome: Callable[[int, ImageOutcome], None],
    ) -> None:
        self.manifest = manifest
        self.directory = directory
        self.config = config
        self.on_outcome = on_outcome
        self.pending: dict[Future[ImageOutcome], int] = {}
        self.outcomes: list[ImageOutcome | None] = [None] * len(manifest.entries)
        self.cursor = 0
        self.not_started: set[int] = set()
        self.cancel_requested = False
        self.error_code: str | None = None

    @property
    def stopping(self) -> bool:
        return self.cancel_requested or self.error_code is not None

    def submit(self, pool: ProcessPoolExecutor, window: int) -> None:
        """Bound uncollected futures; a failed submission may have dispatched work."""
        while self.cursor < len(self.outcomes) and len(self.pending) < window:
            with defer_startup_interrupt():
                index = self.cursor
                self.cursor += 1
                future = pool.submit(
                    process_image,
                    self.manifest.root,
                    self.manifest.entries[index],
                    self.directory,
                    self.config,
                    self.manifest.limits,
                )
                self.pending[future] = index

    def cancel_pending(self) -> None:
        """Only a successfully cancelled future proves that its work did not run."""
        for future, index in tuple(self.pending.items()):
            if future.cancel():
                self.not_started.add(index)
                del self.pending[future]

    def collect(self) -> None:
        """Acknowledge results without inferring task startup from submission."""
        done, _ = wait(self.pending, timeout=0.1, return_when=FIRST_COMPLETED)
        for future in sorted(done, key=self.pending.__getitem__):
            index = self.pending.pop(future)
            try:
                failure = future.exception()
                if failure is not None:
                    raise WorkerExecutionError("Worker task failed") from failure
                outcome = future.result()
                expected = self.manifest.entries[index].relative_path
                name = hashlib.sha256(expected.encode()).hexdigest() + ".png"
                if (
                    not isinstance(outcome, ImageOutcome)
                    or outcome.relative_path != expected
                    or (outcome.output_name is None) == (outcome.error_code is None)
                    or (outcome.output_name is not None and outcome.output_name != name)
                ):
                    raise WorkerExecutionError("Worker outcome identity is invalid")
            except BaseException as error:
                if isinstance(error, KeyboardInterrupt):
                    raise
                self.error_code = self.error_code or "worker_failure"
                continue
            self._acknowledge(index, outcome)

    def _acknowledge(self, index: int, outcome: ImageOutcome) -> None:
        """Keep received outcomes even after durable acknowledgement fails."""
        self.outcomes[index] = outcome
        if self.error_code == "receipt_failure":
            return
        try:
            self.on_outcome(index, outcome)
        except (OSError, ValueError, RuntimeError):
            self.error_code = "receipt_failure"

    def execute(
        self,
        pool: ProcessPoolExecutor,
        window: int,
        cancelled: Callable[[], bool],
    ) -> None:
        """Drive the bounded work queue; the caller owns pool startup and shutdown."""
        while self.cursor < len(self.manifest.entries) or self.pending:
            try:
                if cancelled():
                    self.cancel_requested = True
                if self.stopping:
                    self.cancel_pending()
                else:
                    self.submit(pool, window)
                if self.pending:
                    self.collect()
                elif self.stopping:
                    break
            except KeyboardInterrupt:
                self.cancel_requested = True
            except (OSError, RuntimeError, ValueError):
                self.error_code = self.error_code or "worker_failure"
                self.cancel_pending()
                break

    def snapshot(self) -> PoolAccounting:
        """Leave all unacknowledged attempted submissions unknown."""
        return PoolAccounting(
            tuple(self.outcomes),
            frozenset(self.not_started | set(range(self.cursor, len(self.outcomes)))),
            self.cancel_requested,
            self.error_code,
        )


def run_accounted(
    manifest: DatasetManifest,
    directory: Path,
    config: TransformConfig,
    workers: int,
    *,
    cancelled: Callable[[], bool],
    on_outcome: Callable[[int, ImageOutcome], None],
) -> PoolAccounting:
    """Execute once with bounded submission, cooperative cancellation and accounting.

    Args:
        manifest: Frozen validated workload with the local execution policy.
        directory: Newly owned, prepared output directory.
        config: Unchanged image transformation.
        workers: Explicit supported pool cap, 1/2/4/8.
        cancelled: Stop admission and cancel pending work when true.
        on_outcome: Publish an acknowledgement after each verified worker result.

    Returns:
        Manifest-ordered known outcomes, proven unstarted work and failure state.
        Running tasks drain on cancellation; submitted work is not observed startup.

    Raises:
        WorkerExecutionError: Shutdown failed; no terminal execution is confirmed.
    """
    if type(workers) is not int or workers not in (1, 2, 4, 8):
        raise ValueError("Worker count must be 1, 2, 4 or 8")
    state = _Collection(manifest, directory, config, on_outcome)
    pool: ProcessPoolExecutor | None = None
    try:
        if cancelled():
            state.cancel_requested = True
            return state.snapshot()
        with defer_startup_interrupt():
            pool = ProcessPoolExecutor(
                max_workers=workers,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=_initialize_worker,
            )
            check_startup_support(pool)
        state.execute(pool, 2 * workers, cancelled)
    except KeyboardInterrupt:
        state.cancel_requested = True
    except (OSError, RuntimeError, ValueError):
        state.error_code = state.error_code or "worker_failure"
    finally:
        if pool is not None:
            try:
                _shutdown_pool(pool)
            except BaseException as error:
                raise WorkerExecutionError(
                    "Operational pool shutdown is unconfirmed; inspect partial receipts"
                ) from error
        _cleanup_worker_temps(directory)
    return state.snapshot()

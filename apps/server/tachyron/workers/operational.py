# Copyright (c) 2026 Hans Valerie
"""Accounted fixed-size pools for operational runs; scientific execution is separate."""

import hashlib
import multiprocessing
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from contextlib import ExitStack
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from tachyron.processing import DatasetManifest, ImageOutcome, TransformConfig
from tachyron.processing.processor import process_image
from tachyron.workers import (
    WorkerExecutionError,
    _cleanup_worker_temps,
    _initialize_worker,
    _shutdown_pool,
)
from tachyron.workers._lifetime import worker_initializer
from tachyron.workers._startup import (
    check_startup_support,
    defer_startup_interrupt,
    owned_processes,
)
from tachyron.workers.telemetry import (
    ObservedOutcome,
    WorkerTelemetry,
    execute_observed_task,
    initialize_observed_worker,
)


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
        telemetry: WorkerTelemetry | None,
    ) -> None:
        self.manifest = manifest
        self.directory = directory
        self.config = config
        self.on_outcome = on_outcome
        self.telemetry = telemetry
        self.pending: dict[Future[ImageOutcome | ObservedOutcome], int] = {}
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
                self.pending[self._submit_task(pool, index)] = index

    def _submit_task(
        self, pool: ProcessPoolExecutor, index: int
    ) -> Future[ImageOutcome | ObservedOutcome]:
        inputs = (
            self.manifest.root,
            self.manifest.entries[index],
            self.directory,
            self.config,
            self.manifest.limits,
        )
        if self.telemetry is None:
            return pool.submit(process_image, *inputs)
        try:
            future = pool.submit(execute_observed_task, process_image, index, *inputs)
        except (OSError, RuntimeError, ValueError):
            self.telemetry.task("submission_unknown", index)
            raise
        self.telemetry.task("task_submitted", index)
        return future

    def cancel_pending(self) -> None:
        """Only a successfully cancelled future proves that its work did not run."""
        for future, index in tuple(self.pending.items()):
            if future.cancel():
                self.not_started.add(index)
                del self.pending[future]
                if self.telemetry is not None:
                    self.telemetry.task("task_not_started", index)

    def collect(self) -> None:
        """Acknowledge results without inferring task startup from submission."""
        done, _ = wait(self.pending, timeout=0.1, return_when=FIRST_COMPLETED)
        if self.telemetry is not None:
            self.telemetry.drain()
        for future in sorted(done, key=self.pending.__getitem__):
            index = self.pending.pop(future)
            try:
                failure = future.exception()
                if failure is not None:
                    raise WorkerExecutionError("Worker task failed") from failure
                received = future.result()
                observation = (
                    received if isinstance(received, ObservedOutcome) else None
                )
                outcome = (
                    received.outcome
                    if isinstance(received, ObservedOutcome)
                    else received
                )
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
            self._acknowledge(index, outcome, observation)

    def _acknowledge(
        self, index: int, outcome: ImageOutcome, observation: ObservedOutcome | None
    ) -> None:
        """Keep received outcomes even after durable acknowledgement fails."""
        self.outcomes[index] = outcome
        if self.telemetry is not None:
            self.telemetry.task("task_result_received", index, observation)
        if self.error_code == "receipt_failure":
            return
        try:
            self.on_outcome(index, outcome)
        except (OSError, ValueError, RuntimeError):
            self.error_code = "receipt_failure"
        else:
            if self.telemetry is not None:
                self.telemetry.task(
                    "task_succeeded" if outcome.success else "task_failed",
                    index,
                    observation,
                )

    def request_cancel(self) -> None:
        self.cancel_requested = True
        if self.telemetry is not None:
            self.telemetry.request_cancel()

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
                    self.request_cancel()
                if self.stopping:
                    self.cancel_pending()
                else:
                    self.submit(pool, window)
                if self.pending:
                    self.collect()
                elif self.stopping:
                    break
            except KeyboardInterrupt:
                self.request_cancel()
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
    telemetry: WorkerTelemetry | None = None,
) -> PoolAccounting:
    """Execute once with bounded submission, cooperative cancellation and accounting.

    Args:
        manifest: Frozen validated workload with the local execution policy.
        directory: Newly owned, prepared output directory.
        config: Unchanged image transformation.
        workers: Explicit supported pool cap, 1/2/4/8.
        cancelled: Stop admission and cancel pending work when true.
        on_outcome: Publish an acknowledgement after each verified worker result.
        telemetry: Optional lifecycle observations independent of outcome accounting.

    Returns:
        Manifest-ordered known outcomes, proven unstarted work and failure state.
        Running tasks drain on cancellation; submitted work is not observed startup.

    Raises:
        WorkerExecutionError: Shutdown failed; no terminal execution is confirmed.
    """
    if type(workers) is not int or workers not in (1, 2, 4, 8):
        raise ValueError("Worker count must be 1, 2, 4 or 8")
    state = _Collection(manifest, directory, config, on_outcome, telemetry)
    pool: ProcessPoolExecutor | None = None
    started_dispatch = False
    with ExitStack() as lifetime:
        try:
            if cancelled():
                state.request_cancel()
                return state.snapshot()
            initialize = (
                _initialize_worker
                if telemetry is None
                else partial(
                    initialize_observed_worker, telemetry.channel, telemetry.run_id
                )
            )
            with defer_startup_interrupt():
                pool = ProcessPoolExecutor(
                    max_workers=workers,
                    mp_context=multiprocessing.get_context("spawn"),
                    initializer=lifetime.enter_context(worker_initializer(initialize)),
                )
                check_startup_support(pool)
            started_dispatch = True
            state.execute(pool, 2 * workers, cancelled)
        except KeyboardInterrupt:
            state.request_cancel()
        except (OSError, RuntimeError, ValueError):
            state.error_code = state.error_code or "worker_failure"
        finally:
            processes = (
                owned_processes(pool)
                if pool is not None and telemetry is not None and started_dispatch
                else ()
            )
            try:
                if pool is not None:
                    _shutdown_pool(pool)
                lifetime.close()
            except BaseException as error:
                raise WorkerExecutionError(
                    "Operational pool shutdown is unconfirmed; inspect partial receipts"
                ) from error
            if telemetry is not None and pool is not None:
                telemetry.pool_stopped(processes)
            _cleanup_worker_temps(directory)
    return state.snapshot()

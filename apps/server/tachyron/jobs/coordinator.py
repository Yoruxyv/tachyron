# Copyright (c) 2026 Hans Valerie
"""One lifespan-owned consumer, a bounded waiting queue, and durable job states."""

import json
import logging
import os
import re
import sqlite3
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Condition, Event, Thread
from typing import cast
from uuid import uuid4

import psutil

from tachyron.benchmarking.exports import read_experiment
from tachyron.benchmarking.models import BenchmarkError, RunRecord
from tachyron.benchmarking.runner import run_experiment
from tachyron.imports.models import TERMINAL as IMPORT_TERMINAL
from tachyron.imports.models import DatasetImportError, ImportLimits, Operation
from tachyron.imports.service import Session
from tachyron.imports.storage import Catalog
from tachyron.jobs.models import (
    TERMINAL,
    DatasetInfo,
    Job,
    Metrics,
    RunRequest,
)
from tachyron.persistence import HistoryStore
from tachyron.processing import DatasetManifest, ProcessingError, build_manifest
from tachyron.processing.dataset import dataset_root
from tachyron.workers import WorkerExecutionError, defer_startup_interrupt

logger = logging.getLogger(__name__)


def stamp() -> str:
    """Use timezone-aware UTC for durable lifecycle timestamps."""
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Settings:
    """Operator-owned configuration; HTTP clients cannot select filesystem paths."""

    datasets: dict[str, Path] = field(default_factory=dict[str, Path])
    data_dir: Path = Path("results/api")
    queue_capacity: int = 2
    dataset_dir: Path | None = None
    import_limits: ImportLimits = field(default_factory=ImportLimits)
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:4174",
        "http://127.0.0.1:4173",
    )

    def __post_init__(self) -> None:
        if len(self.datasets) > 32 or any(
            re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name) is None
            for name in self.datasets
        ):
            raise ValueError("Allow at most 32 portable dataset identifiers")
        if type(self.queue_capacity) is not int or not 1 <= self.queue_capacity <= 16:
            raise ValueError("Waiting queue capacity must be between 1 and 16")

    @classmethod
    def from_environment(cls) -> "Settings":
        """Load a small explicit allowlist and an operator-owned history directory."""
        raw = os.environ.get("TACHYRON_DATASETS", "{}")
        if len(raw) > 16_384:
            raise ValueError("Dataset configuration is too large")
        configured: object = json.loads(raw)
        if not isinstance(configured, dict):
            raise TypeError("Dataset configuration must be an object")
        datasets: dict[str, Path] = {}
        for name, path in cast("dict[str, object]", configured).items():
            if not isinstance(name, str) or not isinstance(path, str):
                raise TypeError("Dataset identifiers and roots must be strings")
            datasets[name] = Path(path)
        return cls(
            datasets,
            Path(os.environ.get("TACHYRON_DATA_DIR", "results/api")),
            dataset_dir=Path(
                os.environ.get("TACHYRON_DATASET_DIR", "datasets/imported")
            ),
        )


class AdmissionError(ValueError):
    """A safe admission/cancellation rejection, separate from core failures."""

    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class Coordinator:
    """Own one benchmark consumer and only queued/active cancellation signals."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._condition = Condition()
        self._queue: deque[str] = deque()
        self._signals: dict[str, Event] = {}
        self._closing = False
        self._healthy = False
        self._thread: Thread | None = None
        self._manifests: dict[str, DatasetManifest] = {}
        self.datasets: list[DatasetInfo] = []
        self.catalog: Catalog
        self.import_session: Session | None = None
        self._import_reserved = False

    def start(self) -> None:
        """Initialize history and discovery before accepting work; clean up on error.

        Raises:
            ValueError: Invalid settings or unsupported history schema.
            OSError: Unsafe or inaccessible storage.
            sqlite3.Error: Another owner or unavailable/corrupt history.
        """
        if self._thread is not None:
            raise RuntimeError("Coordinator already started")
        candidate = self.settings.data_dir.resolve()
        managed_path = self.settings.dataset_dir or self.settings.data_dir.with_name(
            self.settings.data_dir.name + "-datasets"
        )
        managed = managed_path.resolve()
        for other in (
            candidate,
            *(root.resolve() for root in self.settings.datasets.values()),
        ):
            if managed.is_relative_to(other) or other.is_relative_to(managed):
                raise ValueError(
                    "Managed datasets, history and inputs must not overlap"
                )
        for root in self.settings.datasets.values():
            canonical = root.resolve()
            if candidate.is_relative_to(canonical) or canonical.is_relative_to(
                candidate
            ):
                raise ValueError("History and datasets must not overlap")
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.directory = dataset_root(self.settings.data_dir)
        self.store = HistoryStore(self.directory / "history.sqlite3")
        try:
            self.catalog = Catalog(managed_path, self.settings.import_limits)
            self.catalog.recover()
            self._discover()
            if set(self.settings.datasets) & {
                item.dataset_id for item in self.catalog.datasets()
            }:
                raise ValueError("Environment and managed dataset identifiers collide")
            self.store.recover(stamp())
            self._healthy = True
            self._thread = Thread(target=self._work, name="tachyron-jobs")
            with defer_startup_interrupt():
                self._thread.start()
        except BaseException:
            self._healthy = False
            self.close()
            raise

    def _discover(self) -> None:
        """Freeze each allowlisted manifest; preserve unavailable dataset entries."""
        for name, root in sorted(self.settings.datasets.items()):
            try:
                canonical = dataset_root(root)
                manifest = build_manifest(canonical)
            except (OSError, ProcessingError):
                self.datasets.append(
                    DatasetInfo(dataset_id=name, error_code="dataset_unavailable")
                )
                continue
            self._manifests[name] = manifest
            self.datasets.append(
                DatasetInfo(
                    dataset_id=name,
                    manifest_sha256=manifest.sha256,
                    input_count=len(manifest.entries),
                    total_bytes=manifest.total_bytes,
                )
            )

    def submit(self, request: RunRequest, key: str | None = None) -> Job:
        """Persist admission before returning 202; never start an unqueued job."""
        with self._condition:
            if not self._healthy or self._closing:
                raise AdmissionError("coordinator_unavailable", 503)
            if key is not None and (previous := self.store.by_key(key)) is not None:
                if previous.request != request:
                    raise AdmissionError("idempotency_conflict", 409)
                return previous
            if self._import_reserved:
                raise AdmissionError("import_in_progress", 409)
            manifest = self.resolve_manifest(request.dataset_id)
            manifest.limits.check_output(request.transform(), len(manifest.entries))
            if len(self._queue) >= self.settings.queue_capacity:
                raise AdmissionError("queue_full", 429)
            job = Job(
                id=str(uuid4()),
                request=request,
                manifest_sha256=manifest.sha256,
                input_count=len(manifest.entries),
                created_at=stamp(),
                total_batches=4 * (request.repetitions + 1),
            )
            self._signals[job.id] = Event()
            self._queue.append(job.id)
            try:
                self.store.save(job, key=key)
            except BaseException:
                self._queue.remove(job.id)
                del self._signals[job.id]
                raise
            self._condition.notify()
            return job

    def get(self, identifier: str) -> Job:
        """Read durable status, reporting an unavailable consumer honestly."""
        with self._condition:
            job = self.store.get(identifier)
            if job is None:
                raise AdmissionError("job_not_found", 404)
            if job.state not in TERMINAL and not self._healthy:
                raise AdmissionError("coordinator_unavailable", 503)
            return job

    def cancel(self, identifier: str) -> Job:
        """Cancel queued work immediately, or request a stop after the active batch."""
        with self._condition:
            job = self.store.get(identifier)
            if job is None:
                raise AdmissionError("job_not_found", 404)
            if job.state in TERMINAL:
                return job
            if not self._healthy and not self._closing:
                raise AdmissionError("coordinator_unavailable", 503)
            signal = self._signals.get(identifier)
            if signal is None:
                raise AdmissionError("coordinator_unavailable", 503)
            updated = job.model_copy(update={"cancel_requested": True})
            if job.state == "QUEUED":
                updated = updated.model_copy(
                    update={"state": "CANCELLED", "ended_at": stamp()}
                )
            self.store.save(updated)
            signal.set()
            if job.state == "QUEUED":
                self._queue.remove(identifier)
                del self._signals[identifier]
            return updated

    def close(self) -> None:
        """Stop admission, cancel waiting jobs, then drain the active child pool."""
        with self._condition:
            self._closing = True
            for signal in self._signals.values():
                signal.set()
            self._condition.notify_all()
            session = self.import_session
        if session is not None:
            try:
                session.cancel()
            except DatasetImportError:
                logger.warning("Import cancellation persistence failed during shutdown")
            if not session.done.wait(
                timeout=self.settings.import_limits.image_seconds + 15
            ):
                raise DatasetImportError("worker_unavailable", 503)
            # The request owner has finished. Retry a previously failed reap
            # before releasing storage ownership or admitting another owner.
            if session.process is not None:
                session.close()
                self.release_import(session)
        try:
            with self._condition:
                for identifier in tuple(self._signals):
                    self.cancel(identifier)
        finally:
            # Joining before closing storage also covers partial shutdown failures.
            if self._thread is not None and self._thread.ident is not None:
                self._thread.join()
            self.store.close()
            if hasattr(self, "catalog"):
                self.catalog.close()

    def begin_import(
        self, identifier: str, digest: str, boundary: str
    ) -> Session | Operation:
        """Persist and attach the owner before helper startup can block or fail."""
        with self._condition:
            previous = self.catalog.replay(identifier, digest)
            if previous is not None:
                return previous
            if self._closing or not self._healthy:
                raise DatasetImportError("coordinator_unavailable", 503)
            if self._signals or self._queue:
                raise DatasetImportError("workload_busy", 409)
            if self._import_reserved:
                raise DatasetImportError("import_busy", 429)
            if "import-" + identifier.replace("-", "") in self.settings.datasets:
                raise DatasetImportError("dataset_id_conflict", 409)
            session = Session(self.catalog, identifier, digest, boundary)
            self.import_session = session
            self._import_reserved = True
            return session

    def release_import(self, session: Session) -> None:
        """Fail closed when the request finishes without confirming helper exit."""
        with self._condition:
            if self.import_session is not session:
                raise RuntimeError("Import owner does not match")
            if session.process is not None:
                self._healthy = False
                self._condition.notify_all()
                return
            self.import_session = None
            self._import_reserved = False
            self._condition.notify_all()

    def cancel_import(self, identifier: str) -> Operation:
        """Serialize cancellation with durable admission and session attachment."""
        with self._condition:
            session = self.import_session
            if session is not None and session.identifier == identifier:
                return session.cancel()
            operation = self.catalog.get(identifier)
            if operation.state in IMPORT_TERMINAL:
                return operation
            return self.catalog.update(
                operation.model_copy(update={"cancel_requested": True})
            )

    def resolve_manifest(
        self, identifier: str, expected: str | None = None
    ) -> DatasetManifest:
        """Resolve frozen environment or durable managed identity, never rediscover."""
        manifest = self._manifests.get(identifier) or self.catalog.manifest(identifier)
        if manifest is None or (expected is not None and manifest.sha256 != expected):
            raise AdmissionError("dataset_unavailable", 404)
        return manifest

    def _work(self) -> None:
        """Run one job at a time; a storage fault closes admission until restart."""
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: bool(self._queue) or self._closing)
                    if self._closing:
                        return
                    identifier = self._queue[0]
                    job = self.store.get(identifier)
                    if job is None:
                        raise RuntimeError("Accepted job disappeared")
                    job = job.model_copy(
                        update={"state": "RUNNING", "started_at": stamp()}
                    )
                    self.store.save(job)
                    self._queue.popleft()
                self._execute(job, self._signals[identifier])
        except (sqlite3.Error, OSError, ValueError, RuntimeError):
            logger.warning("Job coordinator stopped unexpectedly; restart required")
        finally:
            with self._condition:
                self._healthy = False

    def _progress(self, identifier: str, record: RunRecord) -> None:
        """Commit only batch evidence already published outside the measured region."""
        with self._condition:
            job = self.store.get(identifier)
            if job is None:
                raise RuntimeError("Active job disappeared")
            self.store.save(
                job.model_copy(
                    update={
                        "completed_batches": job.completed_batches + 1,
                        "succeeded_images": job.succeeded_images
                        + (record.succeeded or 0),
                        "failed_images": job.failed_images + (record.failed or 0),
                        "unknown_batches": job.unknown_batches
                        + (record.succeeded is None),
                    }
                )
            )

    def _execute(self, job: Job, signal: Event) -> None:
        """Call the CLI's core, retaining safe failures and partial evidence."""
        parent = self.directory / job.id
        directory = parent / "experiment"
        owned = False
        result: Metrics | None = None
        error_code: str | None = None
        try:
            parent.mkdir(mode=0o700)
            owned = True
            experiment = run_experiment(
                self.resolve_manifest(job.request.dataset_id, job.manifest_sha256),
                directory,
                job.request.transform(),
                job.request.benchmark_config(),
                cancelled=signal.is_set,
                on_record=lambda record: self._progress(job.id, record),
            )
            result = Metrics.from_experiment(experiment)
        except (ValueError, OSError, WorkerExecutionError, psutil.Error):
            error_code = "execution_failure"
            if owned:
                result = self._recover_result(job, directory)
        state = "FAILED"
        if error_code is None and result is not None:
            if result.experiment.status == "success":
                state = "COMPLETED"
            elif result.experiment.status == "interrupted":
                state = "CANCELLED"
            else:
                error_code = "image_failure"
        with self._condition:
            current = self.store.get(job.id)
            if current is None:
                raise RuntimeError("Active job disappeared")
            updates: dict[str, object] = {
                "state": state,
                "ended_at": stamp(),
                "error_code": error_code,
            }
            if result is not None:
                records = result.experiment.records
                updates.update(
                    completed_batches=len(records),
                    succeeded_images=sum(record.succeeded or 0 for record in records),
                    failed_images=sum(record.failed or 0 for record in records),
                    unknown_batches=sum(record.succeeded is None for record in records),
                )
            self.store.save(current.model_copy(update=updates), result)
            del self._signals[job.id]

    def _recover_result(self, job: Job, directory: Path) -> Metrics | None:
        """Recover only matching evidence inside this attempt's owned namespace."""
        with suppress(BenchmarkError, OSError):
            experiment = read_experiment(directory / "runs.json")
            context = experiment.context
            manifest = self.resolve_manifest(
                job.request.dataset_id, job.manifest_sha256
            )
            if (
                context.manifest_sha256 == job.manifest_sha256
                and context.input_count == job.input_count
                and context.input_bytes == manifest.total_bytes
                and context.transform == job.request.transform()
                and experiment.config == job.request.benchmark_config()
            ):
                return Metrics.from_experiment(experiment)
        return None

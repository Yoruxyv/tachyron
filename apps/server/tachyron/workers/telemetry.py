# Copyright (c) 2026 Hans Valerie
"""Bounded lifecycle observations for fixed operational spawn pools."""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Callable, Iterator
from ctypes import c_ubyte, c_uint
from datetime import UTC, datetime
from multiprocessing import current_process, get_context
from multiprocessing.process import BaseProcess
from pathlib import Path
from struct import Struct
from time import monotonic_ns
from typing import Literal, NamedTuple, cast

import psutil

from tachyron.processing import (
    ImageOutcome,
    ManifestEntry,
    ResourceLimits,
    TransformConfig,
)
from tachyron.workers import _initialize_worker

EVENT_WINDOW = 256
CHANNEL_CAPACITY = 512
ChildEvent = Literal["worker_started", "worker_ready", "task_started"]
CHILD_EVENTS: tuple[ChildEvent, ...] = (
    "worker_started",
    "worker_ready",
    "task_started",
)
# Fixed-size frames live in shared memory, with no pipe, feeder or blocking join.
_FRAME = Struct("!B16sIdQi")
_Frame = tuple[int, bytes, int, float, int, int]


class _Worker(NamedTuple):
    instance_id: bytes
    pid: int
    birth_time: float | None


class ObservedOutcome(NamedTuple):
    """The original image result with optional process metadata."""

    outcome: ImageOutcome
    worker: _Worker


class ObservationChannel:
    """A bounded batch of child observations shared with the parent.

    All lock acquisition is nonblocking. A killed holder can strand the channel,
    but even then no worker result, receipt or shutdown waits on telemetry.
    """

    def __init__(self, capacity: int = CHANNEL_CAPACITY) -> None:
        if not 1 <= capacity <= CHANNEL_CAPACITY:
            raise ValueError("Invalid telemetry channel capacity")
        context = get_context("spawn")
        self.capacity = capacity
        self._buffer = context.RawArray(c_ubyte, capacity * _FRAME.size)
        self._used = context.RawValue(c_uint)
        self.dropped = context.RawValue(c_uint)
        self.incomplete = context.RawValue(c_ubyte)
        self.lock = context.Lock()

    def send(
        self, kind: ChildEvent, worker: _Worker, observed: int, *, index: int = -1
    ) -> None:
        """Append a child observation or record that it could not be delivered."""
        if not self.lock.acquire(False):
            self.incomplete.value = 1
            return
        try:
            used = self._used.value
            if used == self.capacity:
                self.dropped.value += 1
                return
            _FRAME.pack_into(
                self._buffer,
                used * _FRAME.size,
                CHILD_EVENTS.index(kind),
                worker.instance_id,
                worker.pid,
                worker.birth_time or 0.0,
                observed,
                index,
            )
            self._used.value = used + 1
        finally:
            self.lock.release()

    def drain(self) -> Iterator[_Frame]:
        """Detach the current batch, or defer if a child owns the buffer."""
        if not self.lock.acquire(False):
            return iter(())
        try:
            used = self._used.value
            frames = bytes(memoryview(self._buffer)[: used * _FRAME.size])
            self._used.value = 0
        finally:
            self.lock.release()
        return cast("Iterator[_Frame]", _FRAME.iter_unpack(frames))

    def finish(self) -> None:
        """Flag inaccessible observations after owned children have been reaped."""
        if self.lock.acquire(False):
            self.lock.release()
        else:
            self.incomplete.value = 1


def _instance_id(process: BaseProcess, run_id: str) -> bytes:
    # A fixed executor uses multiprocessing's unique default names, serialized
    # to each child. Match the same process handle even if the OS reuses its PID.
    return hashlib.blake2s(f"{run_id}:{process.name}".encode(), digest_size=16).digest()


class _ChildState:
    def __init__(self) -> None:
        self.channel: ObservationChannel | None = None
        self.worker: _Worker | None = None


_child = _ChildState()


def initialize_observed_worker(channel: ObservationChannel, run_id: str) -> None:
    """Observe the spawned process and completion of its worker initializer."""
    _initialize_worker()
    started = monotonic_ns()
    process = current_process()
    pid = process.pid
    if pid is None:
        channel.incomplete.value = 1
        return
    try:
        birth_time = psutil.Process(pid).create_time()
    except (OSError, psutil.Error):
        birth_time = None
    worker = _Worker(_instance_id(process, run_id), pid, birth_time)
    _child.channel = channel
    _child.worker = worker
    channel.send("worker_started", worker, started)
    channel.send("worker_ready", worker, monotonic_ns())


def execute_observed_task(
    operation: Callable[
        [Path, ManifestEntry, Path, TransformConfig, ResourceLimits], ImageOutcome
    ],
    index: int,
    root: Path,
    entry: ManifestEntry,
    directory: Path,
    config: TransformConfig,
    limits: ResourceLimits,
) -> ImageOutcome | ObservedOutcome:
    """Observe actual child-side entry, then run the original operation."""
    channel, worker = _child.channel, _child.worker
    if channel is not None and worker is not None:
        channel.send("task_started", worker, monotonic_ns(), index=index)
    outcome = operation(root, entry, directory, config, limits)
    return outcome if worker is None else ObservedOutcome(outcome, worker)


TaskEvent = Literal[
    "task_submitted",
    "submission_unknown",
    "task_not_started",
    "task_unknown",
    "task_result_received",
    "task_succeeded",
    "task_failed",
]


class WorkerTelemetry:
    """Sequence one execution's observations and retain its latest events."""

    def __init__(
        self,
        run_id: str,
        manifest_sha256: str,
        requested_workers: int,
        *,
        window: int = EVENT_WINDOW,
        queue_capacity: int = CHANNEL_CAPACITY,
        on_event: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        if not 1 <= window <= EVENT_WINDOW:
            raise ValueError("Invalid telemetry event window")
        self.run_id = run_id
        self.manifest_sha256 = manifest_sha256
        self.requested_workers = requested_workers
        self.channel = ObservationChannel(queue_capacity)
        self._events: deque[dict[str, object]] = deque(maxlen=window)
        self._workers: dict[str, dict[str, object]] = {}
        self._sequence = 0
        self._cancel_requested = False
        self._on_event = on_event
        self._event("execution_started")

    def _event(self, kind: str, origin: str = "parent", /, **fields: object) -> None:
        self._sequence += 1
        event: dict[str, object] = {
            "version": 1,
            "sequence": self._sequence,
            "type": kind,
            "origin": origin,
            "received_monotonic_ns": monotonic_ns(),
            **fields,
        }
        self._events.append(event)
        if self._on_event is not None:
            self._on_event(event)

    def _observe(self, worker: _Worker, kind: str) -> dict[str, object]:
        fields: dict[str, object] = {
            "instance_id": worker.instance_id.hex(),
            "pid": worker.pid,
            "birth_time": worker.birth_time,
        }
        self._workers.setdefault(
            worker.instance_id.hex(),
            {
                **fields,
                "observed_from": kind,
                "state": "OBSERVED",
            },
        )
        return fields

    def _attempt(self, index: int) -> dict[str, object]:
        # The fixed runner dispatches at most one attempt per manifest index.
        return {"index": index, "attempt_id": f"{self.run_id}:{index}"}

    def task(
        self, kind: TaskEvent, index: int, result: ObservedOutcome | None = None
    ) -> None:
        """Record a coordinator observation, with worker attribution when known."""
        fields = self._observe(result.worker, kind) if result is not None else {}
        self._event(kind, **self._attempt(index), **fields)

    def request_cancel(self) -> None:
        """Observe the owner's cancellation request once."""
        if not self._cancel_requested:
            self._cancel_requested = True
            self._event("cancel_requested")

    def drain(self) -> None:
        """Sequence actual child observations in received order."""
        for code, identity, pid, birth, observed, index in self.channel.drain():
            kind = CHILD_EVENTS[code]
            fields = self._observe(_Worker(identity, pid, birth or None), kind)
            if kind == "task_started":
                fields.update(self._attempt(index))
            self._event(kind, "child", worker_monotonic_ns=observed, **fields)

    def pool_stopped(self, processes: tuple[BaseProcess, ...]) -> None:
        """Record verified exits using the same handles owned by the fixed pool."""
        self.drain()
        self.channel.finish()
        for process in processes:
            try:
                pid, exit_code = process.pid, process.exitcode
            except (OSError, ValueError):
                self.channel.incomplete.value = 1
                continue
            if pid is None or exit_code is None:
                self.channel.incomplete.value = 1
                continue
            identity = _instance_id(process, self.run_id)
            fields = self._observe(_Worker(identity, pid, None), "worker_exited")
            worker = self._workers[identity.hex()]
            worker.update(state="EXITED", exit_code=exit_code)
            fields["birth_time"] = worker["birth_time"]
            self._event("worker_exited", exit_code=exit_code, **fields)

    def terminal_snapshot(
        self,
        state: str,
        counts: dict[str, int],
        *,
        cancel_requested: bool,
        error_code: str | None,
    ) -> dict[str, object]:
        """Finish the artifact using the published receipt's exact accounting."""
        if cancel_requested:
            self.request_cancel()
        self._event("execution_terminal", run_state=state, error_code=error_code)
        evicted = self._sequence - len(self._events)
        dropped = self.channel.dropped.value
        incomplete = bool(self.channel.incomplete.value)
        return {
            "schema_version": 1,
            "mode": "operational-worker-telemetry-v1",
            "run_id": self.run_id,
            "manifest_sha256": self.manifest_sha256,
            "snapshot_sequence": self._sequence,
            "observed_at": datetime.now(UTC).isoformat(),
            "run_state": state,
            "error_code": error_code,
            "requested_worker_cap": self.requested_workers,
            "observed_workers": list(self._workers.values()),
            "counts": counts,
            "cancel_requested": cancel_requested,
            "recent_events": list(self._events),
            "dropped_event_count_lower_bound": evicted + dropped,
            "event_window_evictions": evicted,
            "transport_dropped_lower_bound": dropped,
            "coverage": (
                "best_effort_with_gaps"
                if evicted or dropped or incomplete
                else "best_effort_unverified_delivery"
            ),
            "transport_loss_count_unquantified": incomplete,
        }

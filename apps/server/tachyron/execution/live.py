# Copyright (c) 2026 Hans Valerie
"""Execution-scoped polling snapshots built from C1 observations and receipts."""

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Event, Lock
from time import monotonic_ns
from typing import Literal

from tachyron.execution.calibration import Selection
from tachyron.execution.models import ExecutionResult, ExecutionState
from tachyron.processing import ImageOutcome
from tachyron.workers.telemetry import EVENT_WINDOW, ObservationChannel

Mode = Literal["manual", "adaptive"]
State = Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "UNCONFIRMED"]
Phase = Literal["queued", "preparing", "calibration", "execution"]
Coverage = Literal["best_effort", "best_effort_with_gaps", "unavailable"]
TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "UNCONFIRMED")


@dataclass(frozen=True, slots=True)
class TrialScope:
    """Identify a real trial inside the admitted calibration."""

    index: int
    repetition: int


@dataclass(frozen=True, slots=True)
class PoolSnapshot:
    """Latest owned pool with observed workers and receipt-derived counts."""

    execution_id: str
    manifest_sha256: str
    requested_workers: int
    trial: TrialScope | None
    state: Literal["RUNNING"] | ExecutionState
    progress: dict[str, int]
    observed_workers: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class LiveSnapshot:
    """A self-contained snapshot plus a bounded suffix of sequenced observations."""

    schema_version: Literal[1]
    run_id: str
    dataset_id: str | None
    mode: Mode
    requested_workers: int | None
    manifest_sha256: str
    state: State
    phase: Phase
    cancel_requested: bool
    error_code: str | None
    progress: dict[str, int]
    pool: PoolSnapshot | None
    selection: dict[str, object] | None
    calibration_trials_finished: int
    final_execution_id: str | None
    observed_at: str
    snapshot_sequence: int
    first_available_sequence: int
    resync_required: bool
    event_window_evictions: int
    transport_dropped_lower_bound: int
    transport_loss_count_unquantified: bool
    transport_unavailable: bool
    coverage: Coverage
    events: list[dict[str, object]]


@dataclass(slots=True)
class _Progress:
    total: int
    counts: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(
            ("succeeded", "failed", "unknown", "not_started"), 0
        )
    )

    def view(self) -> dict[str, int]:
        return {
            **self.counts,
            "total": self.total,
            "pending": self.total - sum(self.counts.values()),
        }

    def acknowledge(self, outcome: ImageOutcome) -> None:
        self.counts["succeeded" if outcome.success else "failed"] += 1

    def finish(self, result: ExecutionResult | None, *, attempted: bool) -> None:
        if result is not None:
            self.counts = result.counts
        else:
            self.counts["unknown" if attempted else "not_started"] += self.total - sum(
                self.counts.values()
            )


@dataclass(slots=True)
class _Pool:
    execution_id: str
    manifest_sha256: str
    requested_workers: int
    trial: TrialScope | None
    progress: _Progress
    state: Literal["RUNNING"] | ExecutionState = "RUNNING"
    workers: dict[str, dict[str, object]] = field(
        default_factory=dict[str, dict[str, object]]
    )

    def view(self) -> PoolSnapshot:
        return PoolSnapshot(
            self.execution_id,
            self.manifest_sha256,
            self.requested_workers,
            self.trial,
            self.state,
            self.progress.view(),
            [dict(worker) for worker in self.workers.values()],
        )

    def observe(self, event: dict[str, object]) -> None:
        identity = event.get("instance_id")
        if not isinstance(identity, str):
            return
        worker = self.workers.setdefault(
            identity,
            {
                "instance_id": identity,
                "pid": event["pid"],
                "birth_time": event["birth_time"],
                "state": "OBSERVED",
            },
        )
        kind = event["type"]
        if kind == "worker_exited":
            worker.update(state="EXITED", exit_code=event["exit_code"])
        elif kind == "worker_ready":
            worker["state"] = "READY"
        elif kind == "worker_started" and worker["state"] == "OBSERVED":
            worker["state"] = "STARTED"


class LiveTelemetry:
    """One workflow's bounded live projection, fed by the existing C1 collector."""

    def __init__(
        self,
        run_id: str,
        mode: Mode,
        manifest_sha256: str,
        total: int,
        *,
        dataset_id: str | None = None,
        requested_workers: int | None = None,
    ) -> None:
        self.run_id = run_id
        self.mode: Mode = mode
        self.requested_workers = requested_workers
        self.manifest_sha256 = manifest_sha256
        self.dataset_id = dataset_id
        self._lock = Lock()
        self._cancel = Event()
        self._state: State = "QUEUED"
        self._phase: Phase = "queued"
        self._progress = _Progress(total)
        self._pool: _Pool | None = None
        self._final_execution_id: str | None = None
        self._channel: ObservationChannel | None = None
        self._selection: dict[str, object] | None = None
        self._error_code: str | None = None
        self._trials_finished = 0
        self._sequence = 0
        self._events: deque[dict[str, object]] = deque(maxlen=EVENT_WINDOW)
        self._dropped = 0
        self._incomplete = False
        self._unavailable = False
        self._ever_available = False
        self._event("operation_queued")

    def _append(self, event: dict[str, object]) -> None:
        self._sequence += 1
        self._events.append(
            {
                **event,
                "sequence": self._sequence,
                "phase": self._phase,
            }
        )

    def _event(self, kind: str, **fields: object) -> None:
        self._append(
            {
                "type": kind,
                "origin": "parent",
                "version": 1,
                "received_monotonic_ns": monotonic_ns(),
                **fields,
            }
        )

    def start(self) -> None:
        """Record actual acceptance by the execution owner."""
        with self._lock:
            self._phase = "preparing"
            self._state = "RUNNING"
            self._event("operation_started")

    def start_calibration(self) -> None:
        """Record the beginning of calibration, before any trials."""
        with self._lock:
            self._phase = "calibration"
            self._event("calibration_started")

    def select(self, decision: Selection, profiled_inputs: int) -> None:
        """Record the final decision after Stage B resource revalidation."""
        with self._lock:
            selection: dict[str, object] = {
                "status": decision.status,
                "workers": decision.workers,
                "fallback": decision.status == "INCONCLUSIVE",
                "reason": decision.reason,
                "profiled_inputs": profiled_inputs,
            }
            if self._selection != selection:
                self._selection = selection
                self._event("selection_decided", **selection)

    def start_pool(
        self,
        execution_id: str,
        manifest_sha256: str,
        workers: int,
        total: int,
        trial: TrialScope | None,
    ) -> None:
        """Open a distinct receipt and worker namespace for a real pool run."""
        with self._lock:
            self._phase = "calibration" if trial is not None else "execution"
            progress = _Progress(total) if trial is not None else self._progress
            self._pool = _Pool(execution_id, manifest_sha256, workers, trial, progress)
            if trial is None:
                self._final_execution_id = execution_id
            self._event(
                "pool_admitted",
                execution_id=execution_id,
                requested_workers=workers,
                input_count=total,
                trial_index=trial.index if trial else None,
                repetition=trial.repetition if trial else None,
            )

    def attach(self, channel: ObservationChannel | None) -> None:
        """Expose availability independently of durable progress accounting."""
        with self._lock:
            self._channel = channel
            self._unavailable |= channel is None
            self._ever_available |= channel is not None
            if channel is None and self._pool is not None:
                self._event(
                    "worker_observations_unavailable",
                    execution_id=self._pool.execution_id,
                    trial_index=self._pool.trial.index if self._pool.trial else None,
                )

    def observe(self, event: dict[str, object]) -> None:
        """Ingest a C1 event from the owner, without invoking any subscriber."""
        with self._lock:
            pool = self._pool
            if pool is None:
                raise RuntimeError("No telemetry pool is admitted")
            pool.observe(event)
            self._append(
                {
                    **event,
                    "execution_id": pool.execution_id,
                    "trial_index": pool.trial.index if pool.trial else None,
                }
            )

    def acknowledge(self, outcome: ImageOutcome) -> None:
        """Count a published image receipt independently of worker delivery."""
        with self._lock:
            if self._pool is None:
                raise RuntimeError("No telemetry pool is admitted")
            self._pool.progress.acknowledge(outcome)

    def finish_pool(self, result: ExecutionResult | None) -> None:
        """Reconcile a pool with its terminal receipt, or report uncertainty."""
        with self._lock:
            pool = self._pool
            if pool is None:
                raise RuntimeError("No telemetry pool is admitted")
            channel = self._channel
            if channel is not None:
                self._dropped += channel.dropped.value
                self._incomplete |= bool(channel.incomplete.value)
            self._channel = None
            self._incomplete |= result is None
            pool.progress.finish(result, attempted=True)
            pool.state = result.state if result is not None else "UNCONFIRMED"
            if pool.trial is not None and result is not None:
                self._trials_finished += 1
            self._event(
                "pool_terminal",
                execution_id=pool.execution_id,
                state=pool.state,
                error_code=result.error_code if result else None,
                trial_index=pool.trial.index if pool.trial else None,
            )

    def cancelled(self) -> bool:
        """Return the cooperative workflow stop signal."""
        return self._cancel.is_set()

    def request_cancel(self) -> bool:
        """Record a cancellation request and finish queued work immediately."""
        with self._lock:
            if self._state in TERMINAL or self._cancel.is_set():
                return False
            self._cancel.set()
            self._event("cancel_requested")
            if self._state == "QUEUED":
                self._finish("CANCELLED", None)
                return True
            return False

    def _finish(self, state: ExecutionState, error_code: str | None) -> None:
        if self._final_execution_id is None:
            self._progress.finish(None, attempted=False)
        self._state = state
        self._error_code = error_code
        self._event("operation_terminal", state=state, error_code=error_code)

    def finish(self, state: ExecutionState, error_code: str | None = None) -> None:
        """Report terminal evidence only after the workflow owner has returned."""
        with self._lock:
            self._finish(state, error_code)

    def snapshot(self, after: int | None = None) -> LiveSnapshot:
        """Return a detached bounded snapshot, refreshing stale or future cursors."""
        with self._lock:
            first = self._sequence - len(self._events) + 1
            gap = after is not None and not first - 1 <= after <= self._sequence
            events = [
                dict(event)
                for sequence, event in enumerate(self._events, first)
                if gap or after is None or sequence > after
            ]
            dropped = self._dropped
            incomplete = self._incomplete
            if self._channel is not None:
                dropped += self._channel.dropped.value
                incomplete |= bool(self._channel.incomplete.value)
            coverage: Coverage = "best_effort"
            if self._unavailable and not self._ever_available:
                coverage = "unavailable"
            elif first > 1 or dropped or incomplete or self._unavailable:
                coverage = "best_effort_with_gaps"
            return LiveSnapshot(
                schema_version=1,
                run_id=self.run_id,
                dataset_id=self.dataset_id,
                mode=self.mode,
                requested_workers=self.requested_workers,
                manifest_sha256=self.manifest_sha256,
                state=self._state,
                phase=self._phase,
                cancel_requested=self._cancel.is_set(),
                error_code=self._error_code,
                progress=self._progress.view(),
                pool=self._pool.view() if self._pool else None,
                selection=dict(self._selection) if self._selection else None,
                calibration_trials_finished=self._trials_finished,
                final_execution_id=self._final_execution_id,
                observed_at=datetime.now(UTC).isoformat(),
                snapshot_sequence=self._sequence,
                first_available_sequence=first,
                resync_required=gap,
                event_window_evictions=first - 1,
                transport_dropped_lower_bound=dropped,
                transport_loss_count_unquantified=incomplete,
                transport_unavailable=self._unavailable,
                coverage=coverage,
                events=events,
            )

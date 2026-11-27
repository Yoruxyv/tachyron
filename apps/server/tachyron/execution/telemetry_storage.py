# Copyright (c) 2026 Hans Valerie
"""Validate one terminal telemetry artifact against its owning receipt."""

from datetime import datetime, timedelta
from math import isfinite
from pathlib import Path
from re import fullmatch

from tachyron.execution.models import ExecutionResult
from tachyron.execution.storage import (
    _array,
    _integer,
    _object,
    _read,
    inspect_execution,
)
from tachyron.processing import ProcessingError
from tachyron.processing.dataset import dataset_root
from tachyron.workers.telemetry import CHILD_EVENTS, EVENT_WINDOW

MAX_SNAPSHOT_BYTES = 256 * 1024
_IDENTITY = {"instance_id", "pid", "birth_time"}
_WORKER = _IDENTITY | {"state", "observed_from"}
_EVENT = {"version", "sequence", "type", "origin", "received_monotonic_ns"}
_ATTEMPT = {"index", "attempt_id"}
_TASK_STATES: dict[str, tuple[str, ...]] = {
    "task_submitted": (),
    "task_started": ("SUCCEEDED", "FAILED", "UNKNOWN"),
    "submission_unknown": ("UNKNOWN",),
    "task_not_started": ("NOT_STARTED",),
    "task_unknown": ("UNKNOWN",),
    "task_result_received": ("SUCCEEDED", "FAILED"),
    "task_succeeded": ("SUCCEEDED",),
    "task_failed": ("FAILED",),
}
_RESULT_EVENTS = {"task_result_received", "task_succeeded", "task_failed"}
_CHILD_PAYLOAD = _IDENTITY | {"worker_monotonic_ns"}
_EVENT_PAYLOADS: dict[str, set[str]] = {
    **dict.fromkeys(_TASK_STATES, _ATTEMPT),
    **dict.fromkeys(CHILD_EVENTS, _CHILD_PAYLOAD),
    "task_started": _CHILD_PAYLOAD | _ATTEMPT,
    "worker_exited": _IDENTITY | {"exit_code"},
    "execution_terminal": {"run_state", "error_code"},
    "execution_started": set(),
    "cancel_requested": set(),
}
_COUNTERS = {
    "dropped_event_count_lower_bound",
    "event_window_evictions",
    "transport_dropped_lower_bound",
}


def _validate_header(snapshot: dict[str, object], receipt: ExecutionResult) -> None:
    expected: dict[str, object] = {
        "schema_version": 1,
        "mode": "operational-worker-telemetry-v1",
        "run_id": receipt.execution_id,
        "manifest_sha256": receipt.manifest_sha256,
        "requested_worker_cap": receipt.workers,
        "run_state": receipt.state,
        "cancel_requested": receipt.cancel_requested,
        "error_code": receipt.error_code,
    }
    fields = (
        expected.keys()
        | _COUNTERS
        | {
            "snapshot_sequence",
            "observed_at",
            "observed_workers",
            "counts",
            "recent_events",
            "coverage",
            "transport_loss_count_unquantified",
        }
    )
    if set(snapshot) != fields or any(
        type(snapshot[key]) is not type(value) or snapshot[key] != value
        for key, value in expected.items()
    ):
        raise ValueError("Telemetry receipt identity mismatch")
    counts = _object(snapshot["counts"])
    if {key: _integer(value) for key, value in counts.items()} != receipt.counts:
        raise ValueError("Telemetry count mismatch")
    observed_at = snapshot["observed_at"]
    if not isinstance(observed_at, str) or datetime.fromisoformat(
        observed_at
    ).utcoffset() != timedelta(0):
        raise ValueError("Invalid telemetry observation time")


def _validate_workers(value: object, limit: int) -> dict[str, dict[str, object]]:
    raw = _array(value)
    if len(raw) > limit:
        raise ValueError("Excessive worker count")
    workers: dict[str, dict[str, object]] = {}
    for candidate in raw:
        worker = _object(candidate)
        state = worker["state"]
        fields = _WORKER | {"exit_code"} if state == "EXITED" else _WORKER
        if set(worker) != fields or state not in ("OBSERVED", "EXITED"):
            raise ValueError("Invalid worker fields")
        identity, pid, birth = (
            worker["instance_id"],
            worker["pid"],
            worker["birth_time"],
        )
        if (
            not isinstance(identity, str)
            or not fullmatch("[0-9a-f]{32}", identity)
            or identity in workers
            or _integer(pid) == 0
            or (
                birth is not None
                and (
                    not isinstance(birth, (int, float))
                    or isinstance(birth, bool)
                    or not isfinite(birth)
                    or birth <= 0
                )
            )
            or worker["observed_from"]
            not in (*CHILD_EVENTS, "task_result_received", "worker_exited")
            or (state == "EXITED" and type(worker["exit_code"]) is not int)
            or (state == "OBSERVED" and worker["observed_from"] == "worker_exited")
        ):
            raise ValueError("Invalid worker observation")
        workers[identity] = worker
    return workers


def _validate_attempt(
    event: dict[str, object], kind: str, receipt: ExecutionResult
) -> None:
    index = _integer(event["index"])
    states = _TASK_STATES[kind]
    if (
        index >= len(receipt.items)
        or event["attempt_id"] != f"{receipt.execution_id}:{index}"
        or (states and receipt.items[index].state not in states)
    ):
        raise ValueError("Invalid task attempt")


def _validate_event_worker(
    event: dict[str, object], workers: dict[str, dict[str, object]]
) -> None:
    identity = event["instance_id"]
    if not isinstance(identity, str) or identity not in workers:
        raise ValueError("Unrecognized event worker")
    worker = workers[identity]
    if any(
        type(event[key]) is not type(worker[key]) or event[key] != worker[key]
        for key in _IDENTITY
    ):
        raise ValueError("Mismatched event worker")
    if "exit_code" in event and (
        type(event["exit_code"]) is not int
        or event["exit_code"] != worker.get("exit_code")
    ):
        raise ValueError("Mismatched worker exit")


def _validate_event_payload(
    event: dict[str, object],
    kind: str,
    receipt: ExecutionResult,
    workers: dict[str, dict[str, object]],
) -> None:
    fields = _EVENT | _EVENT_PAYLOADS[kind]
    if kind in _RESULT_EVENTS and _IDENTITY & event.keys():
        fields |= _IDENTITY
    if event.keys() != fields:
        raise ValueError("Invalid telemetry event fields")
    if "index" in event:
        _validate_attempt(event, kind, receipt)
    if "instance_id" in event:
        _validate_event_worker(event, workers)
    if "worker_monotonic_ns" in event:
        _integer(event["worker_monotonic_ns"])
    if kind == "execution_terminal" and (
        event["run_state"] != receipt.state or event["error_code"] != receipt.error_code
    ):
        raise ValueError("Mismatched terminal observation")


def _validate_events(
    raw: list[object],
    sequence: int,
    receipt: ExecutionResult,
    workers: dict[str, dict[str, object]],
) -> None:
    if not 1 <= len(raw) <= min(EVENT_WINDOW, sequence):
        raise ValueError("Invalid retained event count")
    previous_time = 0
    seen: set[tuple[str, object]] = set()
    attempt_workers: dict[object, object] = {}
    for number, value in enumerate(raw, sequence - len(raw) + 1):
        event = _object(value)
        kind = event["type"]
        if not isinstance(kind, str) or kind not in _EVENT_PAYLOADS:
            raise ValueError("Unknown telemetry event")
        expected_origin = "child" if kind in CHILD_EVENTS else "parent"
        observed = _integer(event["received_monotonic_ns"])
        if (
            _integer(event["version"]) != 1
            or _integer(event["sequence"]) != number
            or observed < previous_time
            or event["origin"] != expected_origin
            or (kind == "execution_started") != (number == 1)
            or (kind == "execution_terminal") != (number == sequence)
            or (kind == "cancel_requested" and not receipt.cancel_requested)
        ):
            raise ValueError("Invalid telemetry event")
        _validate_event_payload(event, kind, receipt, workers)
        index, worker = event.get("index"), event.get("instance_id")
        key = (kind, index if index is not None else worker)
        if key in seen:
            raise ValueError("Duplicate telemetry event")
        seen.add(key)
        if (
            index is not None
            and worker is not None
            and attempt_workers.setdefault(index, worker) != worker
        ):
            raise ValueError("Conflicting task worker observations")
        previous_time = observed


def _validate_coverage(snapshot: dict[str, object], evicted: int) -> None:
    unquantified = snapshot["transport_loss_count_unquantified"]
    dropped = _integer(snapshot["transport_dropped_lower_bound"])
    coverage = (
        "best_effort_with_gaps"
        if evicted or dropped or unquantified
        else "best_effort_unverified_delivery"
    )
    if (
        type(unquantified) is not bool
        or snapshot["coverage"] != coverage
        or _integer(snapshot["event_window_evictions"]) != evicted
        or _integer(snapshot["dropped_event_count_lower_bound"]) != evicted + dropped
    ):
        raise ValueError("Invalid telemetry coverage")


def inspect_telemetry(directory: Path) -> dict[str, object]:
    """Read and validate terminal telemetry from a trusted local run directory.

    Args:
        directory: Existing run output directory selected by the local operator.

    Returns:
        The bounded, path-free observation artifact.

    Raises:
        ProcessingError: A valid terminal receipt or telemetry artifact is unavailable.
    """
    try:
        root = dataset_root(directory)
        receipt = inspect_execution(root)
        if receipt.state == "UNCONFIRMED":
            raise ValueError("Terminal receipt unavailable")
        snapshot = _read(root, "telemetry.json", MAX_SNAPSHOT_BYTES)
        _validate_header(snapshot, receipt)
        workers = _validate_workers(snapshot["observed_workers"], receipt.workers)
        events = _array(snapshot["recent_events"])
        sequence = _integer(snapshot["snapshot_sequence"])
        _validate_events(events, sequence, receipt, workers)
        _validate_coverage(snapshot, sequence - len(events))
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
        OverflowError,
    ) as error:
        raise ProcessingError(
            "invalid_path", "Operational telemetry unavailable"
        ) from error
    return snapshot

# Copyright (c) 2026 Hans Valerie
"""Fixed-run telemetry evidence, transport failure and real spawn coverage."""

import json
import multiprocessing
import os
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from typing import NoReturn
from unittest.mock import Mock

import pytest

from tachyron.execution import inspect_execution, run_local, runner
from tachyron.execution.storage import _array, _object
from tachyron.execution.telemetry_storage import inspect_telemetry
from tachyron.processing import (
    ImageOutcome,
    ManifestEntry,
    ProcessingError,
    ResourceLimits,
    TransformConfig,
    process_image,
)
from tachyron.workers import WorkerExecutionError, operational
from tachyron.workers import telemetry as worker_telemetry
from tachyron.workers.telemetry import WorkerTelemetry, _Worker
from tests.benchmark_helpers import make_manifest
from tests.test_workers import exit_in_worker

_CONFIG = TransformConfig(2, 2)


def _events(snapshot: dict[str, object]) -> list[dict[str, object]]:
    return [_object(event) for event in _array(snapshot["recent_events"])]


def _kinds(snapshot: dict[str, object]) -> list[object]:
    return [event["type"] for event in _events(snapshot)]


def coordinated_image(
    root: Path,
    entry: ManifestEntry,
    output: Path,
    config: TransformConfig,
    limits: ResourceLimits,
) -> ImageOutcome:
    """Require two actual child participants without relying on scheduling speed."""
    (output / f"participant-{os.getpid()}").touch()
    deadline = monotonic() + 15
    while len(list(output.glob("participant-*"))) < 2:
        if monotonic() >= deadline:
            raise RuntimeError("Only one child participated")
        sleep(0.01)
    return process_image(root, entry, output, config, limits)


def abrupt_exit(
    root: Path,
    entry: ManifestEntry,
    output: Path,
    config: TransformConfig,
    limits: ResourceLimits,
) -> NoReturn:
    """Terminate the process, rather than reporting an exception via a future."""
    os._exit(23)


def exit_holding_telemetry_lock(
    root: Path,
    entry: ManifestEntry,
    output: Path,
    config: TransformConfig,
    limits: ResourceLimits,
) -> NoReturn:
    """Leave a process-shared lock owned by a killed child."""
    channel = worker_telemetry._child.channel
    assert channel is not None
    assert channel.lock.acquire(timeout=5)
    os._exit(24)


def test_bounded_history_and_transport_loss_are_not_accounting() -> None:
    collector = WorkerTelemetry("run", "digest", 8, window=3, queue_capacity=1)
    worker = _Worker(bytes(16), 1234, 12.5)
    collector.channel.send("task_started", worker, 10, index=0)
    collector.channel.send("task_started", worker, 11, index=1)
    collector.drain()
    for index in range(10_000):
        collector.task("task_submitted", index)
    collector.request_cancel()
    counts = {"succeeded": 0, "failed": 0, "unknown": 1, "not_started": 9999}
    snapshot = collector.terminal_snapshot(
        "CANCELLED", counts, cancel_requested=True, error_code=None
    )
    assert snapshot["snapshot_sequence"] == 10_004
    assert snapshot["event_window_evictions"] == 10_001
    assert snapshot["dropped_event_count_lower_bound"] == 10_002
    assert snapshot["transport_dropped_lower_bound"] == 1
    assert snapshot["coverage"] == "best_effort_with_gaps"
    assert snapshot["counts"] == counts
    assert _kinds(snapshot) == [
        "task_submitted",
        "cancel_requested",
        "execution_terminal",
    ]


def test_busy_transport_skips_observation_and_recovers_after_unlock() -> None:
    collector = WorkerTelemetry("run", "digest", 1)
    channel = collector.channel
    worker = _Worker(bytes(16), 1234, None)
    sender = Thread(
        target=channel.send,
        args=("task_started", worker, 10),
        kwargs={"index": 0},
        daemon=True,
    )
    with channel.lock:
        sender.start()
        sender.join(timeout=5)
        returned_while_locked = not sender.is_alive()
    sender.join(timeout=5)
    assert returned_while_locked
    channel.send("task_started", worker, 11, index=1)
    collector.drain()
    counts = {"succeeded": 2, "failed": 0, "unknown": 0, "not_started": 0}
    snapshot = collector.terminal_snapshot(
        "COMPLETED", counts, cancel_requested=False, error_code=None
    )
    assert snapshot["counts"] == counts
    assert snapshot["coverage"] == "best_effort_with_gaps"
    assert snapshot["transport_loss_count_unquantified"] is True
    assert snapshot["transport_dropped_lower_bound"] == 0
    assert snapshot["event_window_evictions"] == 0
    assert [
        event["index"] for event in _events(snapshot) if event["type"] == "task_started"
    ] == [1]


def test_child_signal_does_not_assume_submission_or_success() -> None:
    collector = WorkerTelemetry("run", "digest", 2)
    collector.channel.send("task_started", _Worker(bytes(16), 1234, None), 10, index=1)
    collector.drain()
    snapshot = collector.terminal_snapshot(
        "FAILED",
        {"succeeded": 0, "failed": 0, "unknown": 1, "not_started": 0},
        cancel_requested=False,
        error_code="worker_failure",
    )
    assert _kinds(snapshot) == [
        "execution_started",
        "task_started",
        "execution_terminal",
    ]
    start = _events(snapshot)[1]
    assert start["origin"] == "child"
    assert start["worker_monotonic_ns"] == 10
    assert start["attempt_id"] == "run:1"
    assert len(_array(snapshot["observed_workers"])) == 1


def test_cancelled_before_start_never_creates_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    output = tmp_path / "output"
    factory = Mock(side_effect=AssertionError("pool must never start"))
    monkeypatch.setattr(operational, "ProcessPoolExecutor", factory)
    result = run_local(
        manifest.root, output, _CONFIG, 8, cancelled=lambda: True, telemetry=True
    )
    factory.assert_not_called()
    assert result.state == "CANCELLED"
    snapshot = inspect_telemetry(output)
    assert snapshot["observed_workers"] == []
    assert snapshot["requested_worker_cap"] == 8
    assert _object(snapshot["counts"])["not_started"] == len(manifest.entries)
    assert _kinds(snapshot) == [
        "execution_started",
        "cancel_requested",
        "execution_terminal",
    ]


@pytest.mark.spawn
def test_real_worker_attempts_and_telemetry_off_compatibility(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    before = {p.pid for p in multiprocessing.active_children()}
    output = tmp_path / "output"
    result = run_local(manifest.root, output, _CONFIG, 1, telemetry=True)
    off = run_local(manifest.root, tmp_path / "off", _CONFIG, 1)
    assert result.state == off.state == "COMPLETED"
    assert result.items == off.items
    assert inspect_execution(output) == result
    assert not (tmp_path / "off" / "telemetry.json").exists()
    with pytest.raises(ProcessingError, match="Operational telemetry unavailable"):
        inspect_telemetry(tmp_path / "off")
    snapshot = inspect_telemetry(output)
    assert snapshot["run_id"] == result.execution_id
    assert snapshot["manifest_sha256"] == result.manifest_sha256
    assert snapshot["counts"] == result.counts
    (worker,) = [_object(row) for row in _array(snapshot["observed_workers"])]
    assert worker["state"] == "EXITED"
    assert worker["exit_code"] == 0
    assert worker["pid"] != os.getpid()
    kinds = _kinds(snapshot)
    assert {"worker_started", "worker_ready", "task_started", "worker_exited"} <= set(
        kinds
    )
    for index in range(len(manifest.entries)):
        events = [event for event in _events(snapshot) if event.get("index") == index]
        assert {event["attempt_id"] for event in events} == {
            f"{result.execution_id}:{index}"
        }
        assert events[0]["type"] == "task_submitted"
        end = next(event for event in events if event["type"] == "task_succeeded")
        start = next(
            (event for event in events if event["type"] == "task_started"), None
        )
        if start is None:
            assert snapshot["coverage"] == "best_effort_with_gaps"
        else:
            assert start["origin"] == "child"
            assert start["instance_id"] == end["instance_id"] == worker["instance_id"]
    assert {p.pid for p in multiprocessing.active_children()} <= before


@pytest.mark.spawn
def test_multiple_real_workers_and_per_image_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = make_manifest(tmp_path / "inputs", corrupt=True)
    output = tmp_path / "output"
    monkeypatch.setattr(operational, "process_image", coordinated_image)
    result = run_local(manifest.root, output, _CONFIG, 2, telemetry=True)
    snapshot = inspect_telemetry(output)
    assert result.state == "FAILED"
    assert result.error_code == "image_failure"
    assert snapshot["counts"] == result.counts
    workers = [_object(row) for row in _array(snapshot["observed_workers"])]
    assert len(workers) == 2
    assert len({worker["pid"] for worker in workers}) == 2
    assert len({worker["instance_id"] for worker in workers}) == 2
    assert all(worker["exit_code"] == 0 for worker in workers)
    assert {"task_failed", "task_succeeded"} <= set(_kinds(snapshot))
    attributed = {
        event["instance_id"]
        for event in _events(snapshot)
        if event["type"] in ("task_failed", "task_succeeded")
    }
    assert attributed == {worker["instance_id"] for worker in workers}

    event = next(
        event for event in _events(snapshot) if event["type"] == "task_succeeded"
    )
    other = next(
        worker for worker in workers if worker["instance_id"] != event["instance_id"]
    )
    event.update(
        {field: other[field] for field in ("instance_id", "pid", "birth_time")}
    )
    (output / "telemetry.json").write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(ProcessingError, match="Operational telemetry unavailable"):
        inspect_telemetry(output)


@pytest.mark.spawn
@pytest.mark.parametrize(
    ("operation", "exit_code", "transport_lost"),
    [
        (exit_in_worker, 0, False),
        (abrupt_exit, 23, False),
        (exit_holding_telemetry_lock, 24, True),
    ],
)
def test_abrupt_and_exception_failures_keep_unknown_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: object,
    exit_code: int,
    transport_lost: bool,
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    output = tmp_path / "output"
    before = {p.pid for p in multiprocessing.active_children()}
    monkeypatch.setattr(operational, "process_image", operation)
    result = run_local(manifest.root, output, _CONFIG, 1, telemetry=True)
    snapshot = inspect_telemetry(output)
    assert result.state == "FAILED"
    assert result.error_code == "worker_failure"
    assert result.counts["unknown"] > 0
    assert result.counts["succeeded"] == result.counts["failed"] == 0
    assert snapshot["counts"] == result.counts
    if transport_lost:
        assert snapshot["transport_loss_count_unquantified"] is True
        assert snapshot["coverage"] == "best_effort_with_gaps"
    assert "task_succeeded" not in _kinds(snapshot)
    assert "task_failed" not in _kinds(snapshot)
    unknown = [event for event in _events(snapshot) if event["type"] == "task_unknown"]
    submitted = {
        event["attempt_id"]
        for event in _events(snapshot)
        if event["type"] == "task_submitted"
    }
    assert {event["attempt_id"] for event in unknown} <= submitted
    assert len(unknown) == result.counts["unknown"]
    assert all("instance_id" not in event for event in unknown)
    (worker_exit,) = [
        event for event in _events(snapshot) if event["type"] == "worker_exited"
    ]
    assert worker_exit["exit_code"] == exit_code
    assert "attempt_id" not in worker_exit
    assert {p.pid for p in multiprocessing.active_children()} <= before


@pytest.mark.spawn
def test_artifact_validation_rejects_malformed_and_misattributed_events(
    tmp_path: Path,
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    output = tmp_path / "output"
    run_local(manifest.root, output, _CONFIG, 1, telemetry=True)
    target = output / "telemetry.json"
    original = target.read_text(encoding="utf-8")
    mutations: tuple[tuple[str, object], ...] = (
        ("run_id", "different-run"),
        ("cancel_requested", True),
        ("snapshot_sequence", 0),
        ("recent_events", ()),
        ("dropped_event_count_lower_bound", 100),
        ("observed_workers", (None,)),
    )
    for key, value in mutations:
        snapshot = _object(json.loads(original))
        snapshot[key] = value
        target.write_text(json.dumps(snapshot), encoding="utf-8")
        with pytest.raises(ProcessingError, match="Operational telemetry unavailable"):
            inspect_telemetry(output)
    for kind, key, value in (
        ("task_succeeded", "attempt_id", "wrong-attempt"),
        ("task_succeeded", "index", True),
        ("task_succeeded", "path", "/private/file"),
        ("worker_exited", "pid", True),
        ("worker_exited", "exit_code", 9),
        ("execution_terminal", "run_state", "FAILED"),
        ("execution_terminal", "error_code", "worker_failure"),
    ):
        snapshot = _object(json.loads(original))
        event = next(row for row in _events(snapshot) if row["type"] == kind)
        event[key] = value
        target.write_text(json.dumps(snapshot), encoding="utf-8")
        with pytest.raises(ProcessingError, match="Operational telemetry unavailable"):
            inspect_telemetry(output)


def test_telemetry_initialization_failure_preserves_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    output = tmp_path / "output"
    monkeypatch.setattr(
        runner, "WorkerTelemetry", Mock(side_effect=OSError("private detail"))
    )
    result = run_local(
        manifest.root, output, _CONFIG, 1, telemetry=True, cancelled=lambda: True
    )
    assert inspect_execution(output) == result
    assert result.state == "CANCELLED"
    assert not (output / "telemetry.json").exists()
    assert "could not be initialized" in caplog.text
    assert "private detail" not in caplog.text


def test_telemetry_publication_failure_does_not_change_terminal_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    output = tmp_path / "output"
    publish = runner.publish_bytes

    def reject_telemetry(directory: Path, name: str, data: bytes) -> None:
        if name == "telemetry.json":
            raise OSError("private detail")
        publish(directory, name, data)

    monkeypatch.setattr(runner, "publish_bytes", reject_telemetry)
    result = run_local(
        manifest.root, output, _CONFIG, 1, telemetry=True, cancelled=lambda: True
    )
    assert inspect_execution(output) == result
    assert result.state == "CANCELLED"
    assert not (output / "telemetry.json").exists()
    assert "could not be published" in caplog.text
    assert "private detail" not in caplog.text


@pytest.mark.spawn
def test_cancellation_after_dispatch_preserves_observed_worker_outcomes(
    tmp_path: Path,
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    content = (manifest.root / manifest.entries[0].relative_path).read_bytes()
    for index in range(10):
        (manifest.root / f"extra-{index}.png").write_bytes(content)
    output = tmp_path / "output"
    result = run_local(
        manifest.root,
        output,
        _CONFIG,
        1,
        telemetry=True,
        cancelled=(output / "records" / "00000.json").exists,
    )
    snapshot = inspect_telemetry(output)
    assert result.state == "CANCELLED"
    assert result.counts["succeeded"] >= 1
    assert result.counts["not_started"] > 0
    assert result.counts["unknown"] == 0
    assert snapshot["counts"] == result.counts
    assert _kinds(snapshot).count("cancel_requested") == 1
    assert _kinds(snapshot)[-1] == "execution_terminal"


@pytest.mark.spawn
def test_receipt_failure_records_return_without_acknowledgement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    output = tmp_path / "output"
    publish = Mock(side_effect=OSError("private receipt failure"))
    monkeypatch.setattr(runner, "publish_outcome", publish)
    result = run_local(manifest.root, output, _CONFIG, 1, telemetry=True)
    snapshot = inspect_telemetry(output)
    assert result.error_code == "receipt_failure"
    assert result.counts["succeeded"] >= 1
    assert snapshot["counts"] == result.counts
    assert "task_result_received" in _kinds(snapshot)
    assert "task_succeeded" not in _kinds(snapshot)
    publish.assert_called_once()


@pytest.mark.spawn
def test_unconfirmed_shutdown_cannot_publish_terminal_telemetry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    output = tmp_path / "output"
    shutdown = operational._shutdown_pool

    def lose_confirmation(pool: operational.ProcessPoolExecutor) -> NoReturn:
        shutdown(pool)
        raise OSError("lost shutdown confirmation")

    monkeypatch.setattr(operational, "_shutdown_pool", lose_confirmation)
    with pytest.raises(WorkerExecutionError, match="shutdown is unconfirmed"):
        run_local(manifest.root, output, _CONFIG, 1, telemetry=True)
    assert inspect_execution(output).state == "UNCONFIRMED"
    assert not (output / "telemetry.json").exists()

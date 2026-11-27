# Copyright (c) 2026 Hans Valerie
"""Real operational API executions, local request trust and polling reconciliation."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import partial
from pathlib import Path
from threading import Barrier, Event
from time import monotonic, sleep
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from tachyron.api import create_app
from tachyron.execution import (
    adaptive,
    inspect_adaptive,
    inspect_execution,
    run_adaptive,
    runner,
)
from tachyron.execution.calibration import CalibrationPolicy
from tachyron.execution.live import TERMINAL, LiveSnapshot, LiveTelemetry
from tachyron.execution.resources import Resources
from tachyron.jobs import coordinator, operations
from tachyron.jobs.coordinator import Coordinator
from tachyron.jobs.models import Job
from tachyron.processing import (
    ImageOutcome,
    ManifestEntry,
    ResourceLimits,
    TransformConfig,
    process_image,
)
from tachyron.settings import Settings
from tachyron.workers import operational
from tests.benchmark_helpers import make_manifest
from tests.job_helpers import BODY, local_client
from tests.test_adaptive_execution import Clock, controlled, image_dataset
from tests.test_execution_telemetry import abrupt_exit

__all__ = ["controlled"]

_MANUAL: dict[str, object] = {
    "dataset_id": "tiny",
    "mode": "manual",
    "workers": 1,
    "width": 2,
    "height": 2,
}
_ADAPTIVE: dict[str, object] = {
    "dataset_id": "tiny",
    "mode": "adaptive",
    "width": 2,
    "height": 2,
}
_SNAPSHOT = TypeAdapter(LiveSnapshot)
_OPERATIONS = "/api/operations"


def _settings(tmp_path: Path, count: int = 3) -> Settings:
    root = (
        make_manifest(tmp_path / "input").root
        if count == 3
        else image_dataset(tmp_path / "input", count)
    )
    return Settings({"tiny": root}, tmp_path / "history")


def _submit(client: TestClient, body: dict[str, object]) -> tuple[str, LiveSnapshot]:
    response = client.post(_OPERATIONS, json=body)
    assert response.status_code == 202, response.text
    assert response.headers["cache-control"] == "no-store"
    accepted = _SNAPSHOT.validate_json(response.content)
    location = response.headers["location"]
    assert location == f"{_OPERATIONS}/{accepted.run_id}"
    assert accepted.state == "QUEUED"
    return location, accepted


def _poll(client: TestClient, path: str, after: int | None = None) -> LiveSnapshot:
    response = client.get(path, params={"after": after} if after is not None else None)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return _SNAPSHOT.validate_json(response.content)


def _wait(
    client: TestClient, path: str, until: Callable[[LiveSnapshot], bool]
) -> LiveSnapshot:
    deadline = monotonic() + 75
    while monotonic() < deadline:
        snapshot = _poll(client, path)
        if until(snapshot):
            return snapshot
        sleep(0.02)
    pytest.fail("Operational snapshot did not reach the expected state")


def held_image(
    root: Path,
    entry: ManifestEntry,
    output: Path,
    config: TransformConfig,
    limits: ResourceLimits,
) -> ImageOutcome:
    """Hold genuine spawned work until the test releases the output namespace."""
    (output / "worker-entered").touch()
    deadline = monotonic() + 30
    while not (output / "worker-release").exists():
        if monotonic() >= deadline:
            raise RuntimeError("Test worker release timed out")
        sleep(0.01)
    return process_image(root, entry, output, config, limits)


def _state_is_terminal(snapshot: LiveSnapshot) -> bool:
    return snapshot.state in TERMINAL


def test_operational_request_trust_and_validation(tmp_path: Path) -> None:
    with local_client(create_app(_settings(tmp_path))) as client:
        invalid_bodies: tuple[dict[str, object], ...] = (
            {**_MANUAL, "workers": True},
            {**_MANUAL, "workers": 3},
            {**_MANUAL, "output": "/private/output"},
            {**_MANUAL, "code": "private"},
            {**_ADAPTIVE, "workers": 2},
            {**_MANUAL, "dataset_id": "../private"},
        )
        for body in invalid_bodies:
            response = client.post(_OPERATIONS, json=body)
            assert response.status_code == 422
            assert response.json() == {"detail": "invalid_request"}
        assert (
            client.post(
                _OPERATIONS, json={**_MANUAL, "dataset_id": "unlisted"}
            ).status_code
            == 404
        )
        path = f"{_OPERATIONS}/{uuid4()}"
        assert client.get(path).json() == {"detail": "operation_unavailable"}
        assert client.get(path, headers={"Host": "hostile.local"}).status_code == 403
        assert (
            client.get(path, headers={"Origin": "https://hostile.example"}).status_code
            == 403
        )
        assert (
            client.get(path, headers={"Sec-Fetch-Site": "cross-site"}).status_code
            == 403
        )
        assert client.get(path, headers={"X-Tachyron-Request": ""}).status_code == 403
        assert (
            client.post(
                _OPERATIONS, json=_MANUAL, headers={"X-Tachyron-Request": ""}
            ).status_code
            == 403
        )
        assert client.get(path, params={"after": -1}).status_code == 422


@pytest.mark.parametrize(
    ("max_seconds", "status", "workers", "fallback"),
    [(60.0, "SELECTED", 2, False), (1.0, "INCONCLUSIVE", 1, True)],
)
def test_adaptive_api_reports_the_measured_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    controlled: Clock,
    max_seconds: float,
    status: str,
    workers: int,
    fallback: bool,
) -> None:
    settings = _settings(tmp_path, 512)
    monkeypatch.setattr(operations, "perf_counter", lambda: controlled.now)
    monkeypatch.setattr(
        operations,
        "_run_adaptive_manifest",
        partial(
            adaptive._run_adaptive_manifest,
            policy=CalibrationPolicy(
                sample_files=16,
                max_seconds=max_seconds,
            ),
        ),
    )
    with local_client(create_app(settings)) as client:
        path, accepted = _submit(client, _ADAPTIVE)
        final = _wait(client, path, _state_is_terminal)
    assert final.state == "COMPLETED"
    assert final.selection is not None
    assert final.selection["status"] == status
    assert final.selection["workers"] == workers
    assert final.selection["fallback"] is fallback
    assert final.calibration_trials_finished == controlled.trial_count
    assert final.calibration_trials_finished > 0
    assert final.pool is not None
    assert final.pool.trial is None
    assert final.pool.requested_workers == workers
    directory = settings.data_dir / "operations" / accepted.run_id
    receipt = inspect_execution(directory / "execution")
    assert final.final_execution_id == receipt.execution_id
    assert final.progress == {**receipt.counts, "total": 512, "pending": 0}
    assert final.progress["succeeded"] == 512
    assert inspect_adaptive(directory)["execution_id"] == receipt.execution_id
    selection = next(
        event for event in final.events if event["type"] == "selection_decided"
    )
    assert selection["phase"] == "calibration"
    assert selection["status"] == status
    assert selection["fallback"] is fallback


def test_operation_never_overwrites_an_existing_output_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    run_id = UUID("6477e209-306c-4ef6-9649-f83d940ac61d")
    work = settings.data_dir / "operations" / str(run_id)
    work.mkdir(parents=True)
    marker = work / "execution.json"
    marker.write_bytes(b"unowned prior evidence")
    monkeypatch.setattr(coordinator, "uuid4", Mock(return_value=run_id))
    with local_client(create_app(settings)) as client:
        path, _ = _submit(client, _MANUAL)
        finished = _wait(client, path, _state_is_terminal)
    assert finished.state == "FAILED"
    assert finished.error_code == "operation_failure"
    assert finished.pool is None
    assert finished.progress["not_started"] == 3
    assert marker.read_bytes() == b"unowned prior evidence"
    assert list(work.iterdir()) == [marker]


@pytest.mark.spawn
def test_operation_uses_frozen_registered_image_identity(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path / "input")
    settings = Settings({"tiny": manifest.root}, tmp_path / "history")
    with local_client(create_app(settings)) as client:
        changed, replacement = manifest.entries[:2]
        (manifest.root / changed.relative_path).write_bytes(
            (manifest.root / replacement.relative_path).read_bytes()
        )
        path, accepted = _submit(client, _MANUAL)
        finished = _wait(client, path, _state_is_terminal)
    receipt = inspect_execution(settings.data_dir / "operations" / accepted.run_id)
    assert accepted.manifest_sha256 == receipt.manifest_sha256 == manifest.sha256
    assert finished.state == "FAILED"
    assert finished.progress["failed"] == 1
    assert finished.progress["succeeded"] == 2
    assert receipt.items[0].outcome is not None
    assert receipt.items[0].outcome.error_code == "input_changed"


@pytest.mark.spawn
def test_manual_polling_is_bounded_and_independent_of_slow_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, 80)
    monkeypatch.setattr(operational, "process_image", held_image)
    captured, release_reader = Event(), Event()
    original_snapshot = LiveTelemetry.snapshot

    def slow_response(live: LiveTelemetry, after: int | None = None) -> LiveSnapshot:
        snapshot = original_snapshot(live, after)
        if after == 1:
            captured.set()
            assert release_reader.wait(30)
        return snapshot

    monkeypatch.setattr(LiveTelemetry, "snapshot", slow_response)
    with local_client(create_app(settings)) as client, ThreadPoolExecutor(2) as readers:
        barrier = Barrier(2)

        def submit_together() -> tuple[int, str]:
            barrier.wait(timeout=10)
            response = client.post(_OPERATIONS, json=_MANUAL)
            return response.status_code, response.headers.get("location", "")

        submissions = [readers.submit(submit_together) for _ in range(2)]
        results = sorted(future.result(timeout=20) for future in submissions)
        assert [status for status, _ in results] == [202, 409]
        path = results[0][1]
        output = settings.data_dir / "operations" / path.rsplit("/", 1)[1]
        try:
            active = _wait(client, path, lambda _: (output / "worker-entered").exists())
            assert active.state == "RUNNING"
            assert active.pool is not None
            assert active.pool.requested_workers == 1
            assert active.progress["pending"] == 80
            assert client.post("/api/jobs", json=BODY).json() == {
                "detail": "workload_busy"
            }
            slow = readers.submit(_poll, client, path, 1)
            assert captured.wait(10)
            (output / "worker-release").touch()
            terminal = _wait(client, path, _state_is_terminal)
        finally:
            if output.is_dir():
                (output / "worker-release").touch()
            release_reader.set()
        assert slow.result(timeout=5).progress == active.progress
        assert terminal.state == "COMPLETED"
        receipt = inspect_execution(output)
        assert terminal.final_execution_id == receipt.execution_id
        assert terminal.progress == {**receipt.counts, "total": 80, "pending": 0}
        assert terminal.pool is not None
        assert terminal.pool.observed_workers[0]["state"] == "EXITED"
        assert terminal.event_window_evictions > 0
        assert len(terminal.events) == 256
        assert _poll(client, path, 1).resync_required
        assert _poll(client, path, terminal.snapshot_sequence + 1).resync_required
        assert _poll(client, path, terminal.snapshot_sequence).events == []
        suffix = _poll(client, path, terminal.snapshot_sequence - 1)
        assert [event["type"] for event in suffix.events] == ["operation_terminal"]
        assert not suffix.resync_required
        assert client.get("/api/benchmarks").json() == []

        next_path, next_job = _submit(client, _ADAPTIVE)
        fallback = _wait(client, next_path, lambda view: view.selection is not None)
        next_output = settings.data_dir / "operations" / next_job.run_id / "execution"
        try:
            assert fallback.selection is not None
            assert fallback.selection["fallback"] is True
            assert next_job.run_id != terminal.run_id
            assert _poll(client, path).run_id == terminal.run_id
            assert _poll(client, next_path, terminal.snapshot_sequence).resync_required
        finally:
            _wait(
                client,
                next_path,
                lambda view: (
                    _state_is_terminal(view)
                    or (next_output / "worker-entered").exists()
                ),
            )
            if next_output.is_dir():
                (next_output / "worker-release").touch()
        assert _wait(client, next_path, _state_is_terminal).state == "COMPLETED"
    with local_client(create_app(settings)) as restarted:
        assert restarted.get(path).json() == {"detail": "operation_unavailable"}
        assert receipt == inspect_execution(output)


@pytest.mark.spawn
def test_cancellation_drains_actual_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, 12)
    monkeypatch.setattr(operational, "process_image", held_image)
    with local_client(create_app(settings)) as client:
        path, accepted = _submit(client, _MANUAL)
        output = settings.data_dir / "operations" / accepted.run_id
        try:
            _wait(client, path, lambda _: (output / "worker-entered").exists())
            response = client.post(f"{path}/cancel")
            assert response.status_code == 202
            draining = _SNAPSHOT.validate_json(response.content)
            assert draining.cancel_requested
            assert draining.state == "RUNNING"
        finally:
            if output.is_dir():
                (output / "worker-release").touch()
        finished = _wait(client, path, _state_is_terminal)
        assert finished.state == "CANCELLED"
        assert finished.progress["pending"] == 0
        assert finished.progress["unknown"] == 0
        assert finished.progress["not_started"] > 0
        assert finished.progress == {
            **inspect_execution(output).counts,
            "total": 12,
            "pending": 0,
        }


@pytest.mark.spawn
def test_crash_leaves_unknown_attempts_and_observed_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(operational, "process_image", abrupt_exit)
    with local_client(create_app(settings)) as client:
        path, accepted = _submit(client, _MANUAL)
        finished = _wait(client, path, _state_is_terminal)
    assert finished.state == "FAILED"
    assert finished.error_code == "worker_failure"
    assert finished.progress["unknown"] > 0
    assert finished.pool is not None
    assert finished.pool.observed_workers[0]["exit_code"] == 23
    assert finished.progress == {
        **inspect_execution(settings.data_dir / "operations" / accepted.run_id).counts,
        "total": 3,
        "pending": 0,
    }


@pytest.mark.spawn
@pytest.mark.parametrize("crash_trial", [False, True])
def test_adaptive_trials_stream_genuine_observations_and_reconcile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_trial: bool,
) -> None:
    settings = _settings(tmp_path, 288)
    if crash_trial:
        monkeypatch.setattr(operational, "process_image", abrupt_exit)
    monkeypatch.setattr(
        operations,
        "_run_adaptive_manifest",
        partial(
            adaptive._run_adaptive_manifest, policy=CalibrationPolicy(sample_files=16)
        ),
    )
    observe = adaptive.observe_resources

    def two_candidates(
        config: TransformConfig, pixels: int, compressed_bytes: int
    ) -> Resources:
        return replace(observe(config, pixels, compressed_bytes), candidates=(1, 2))

    monkeypatch.setattr(adaptive, "observe_resources", two_candidates)
    with local_client(create_app(settings)) as client:
        path, accepted = _submit(client, _ADAPTIVE)
        final, events = _adaptive_events(client, path, accepted.snapshot_sequence)
    directory = settings.data_dir / "operations" / accepted.run_id
    assert final.calibration_trials_finished > 0
    assert final.selection is not None
    assert final.selection["status"] in ("SELECTED", "INCONCLUSIVE")
    trial_events = [event for event in events if event["phase"] == "calibration"]
    assert "calibration_started" in {event["type"] for event in trial_events}
    assert "selection_decided" in {event["type"] for event in trial_events}
    if crash_trial:
        assert final.state == "FAILED"
        assert final.error_code == "worker_failure"
        assert final.final_execution_id is None
        assert final.progress["not_started"] == 288
        assert final.pool is not None
        assert final.pool.progress["unknown"] > 0
        assert any(
            event["type"] == "worker_exited" and event["exit_code"] == 23
            for event in trial_events
        )
        assert inspect_adaptive(directory)["execution_id"] is None
        return
    assert final.state == "COMPLETED"
    child_starts = [event for event in trial_events if event["type"] == "task_started"]
    assert child_starts
    assert all(
        event["execution_id"] != final.final_execution_id for event in child_starts
    )
    assert all(event["origin"] == "child" for event in child_starts)
    assert inspect_adaptive(directory)["execution_id"] == final.final_execution_id
    assert final.progress == {
        **inspect_execution(directory / "execution").counts,
        "total": 288,
        "pending": 0,
    }


def _adaptive_events(
    client: TestClient, path: str, cursor: int
) -> tuple[LiveSnapshot, list[dict[str, object]]]:
    events: list[dict[str, object]] = []
    deadline = monotonic() + 75
    while monotonic() < deadline:
        view = _poll(client, path, cursor)
        events.extend(view.events)
        cursor = view.snapshot_sequence
        if view.phase == "calibration":
            assert view.progress["succeeded"] == 0
            assert view.progress["failed"] == 0
            if view.pool is not None:
                assert view.pool.trial is not None
        if view.state in TERMINAL:
            return view, events
        sleep(0.015)
    pytest.fail("Adaptive operation did not reach a terminal state")


@pytest.mark.spawn
def test_unavailable_transport_keeps_receipt_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(runner, "WorkerTelemetry", Mock(side_effect=OSError("private")))
    with local_client(create_app(settings)) as client:
        path, accepted = _submit(client, _MANUAL)
        finished = _wait(client, path, _state_is_terminal)
    assert finished.state == "COMPLETED"
    assert finished.transport_unavailable
    assert finished.coverage == "unavailable"
    assert finished.pool is not None
    assert finished.pool.observed_workers == []
    kinds = {event["type"] for event in finished.events}
    assert "worker_observations_unavailable" in kinds
    assert "task_started" not in kinds
    output = settings.data_dir / "operations" / accepted.run_id
    assert finished.progress == {
        **inspect_execution(output).counts,
        "total": 3,
        "pending": 0,
    }


@pytest.mark.spawn
def test_unconfirmed_shutdown_closes_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shutdown = operational._shutdown_pool

    def lose_confirmation(pool: operational.ProcessPoolExecutor) -> None:
        shutdown(pool)
        raise OSError("private")

    monkeypatch.setattr(operational, "_shutdown_pool", lose_confirmation)
    with local_client(create_app(_settings(tmp_path))) as client:
        path, _ = _submit(client, _MANUAL)
        finished = _wait(client, path, _state_is_terminal)
        assert finished.state == "UNCONFIRMED"
        assert finished.error_code == "execution_unconfirmed"
        assert finished.transport_loss_count_unquantified
        denied = client.post(_OPERATIONS, json=_MANUAL)
        assert denied.status_code == 503
        assert denied.json() == {"detail": "coordinator_unavailable"}
        assert _poll(client, path).state == "UNCONFIRMED"


def test_queued_cancellation_never_starts_a_pool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = Event()
    next_job = Coordinator._next_job

    def gated_next_job(
        manager: Coordinator,
    ) -> operations.OperationalJob | tuple[Job, Event] | None:
        if not ready.wait(10):
            raise RuntimeError("Test coordinator was not released")
        return next_job(manager)

    monkeypatch.setattr(Coordinator, "_next_job", gated_next_job)
    settings = _settings(tmp_path)
    with local_client(create_app(settings)) as client:
        try:
            path, accepted = _submit(client, _MANUAL)
            response = client.post(f"{path}/cancel")
            assert response.status_code == 202
            result = _SNAPSHOT.validate_json(response.content)
            assert result.state == "CANCELLED"
            assert result.cancel_requested
            assert result.progress["not_started"] == 3
            assert result.pool is None
            assert not (settings.data_dir / "operations" / accepted.run_id).exists()
        finally:
            ready.set()


@pytest.mark.spawn
def test_adaptive_cli_artifact_uses_the_same_workflow_snapshot(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path / "input")
    output = tmp_path / "output"
    result = run_adaptive(manifest.root, output, TransformConfig(2, 2), telemetry=True)
    view = _SNAPSHOT.validate_json((output / "adaptive-telemetry.json").read_bytes())
    assert view.run_id == result.calibration_id
    assert view.state == result.state == "COMPLETED"
    assert view.selection is not None
    assert view.selection["fallback"] is True
    evidence = inspect_adaptive(output)
    assert evidence["execution_id"] == view.final_execution_id
    assert evidence["mode"] == "adaptive-calibration-v4"


def test_old_job_snapshots_are_evicted_without_reusing_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        operations.OperationalJob,
        "execute",
        Mock(return_value=("FAILED", "operation_failure")),
    )
    with local_client(create_app(_settings(tmp_path))) as client:
        paths: list[str] = []
        for _ in range(operations.RETAINED_OPERATIONS + 1):
            path, _ = _submit(client, _MANUAL)
            assert _wait(client, path, _state_is_terminal).progress["not_started"] == 3
            paths.append(path)
        assert len(set(paths)) == len(paths)
        assert client.get(paths[0]).json() == {"detail": "operation_unavailable"}
        assert _poll(client, paths[1]).state == "FAILED"

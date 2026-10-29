# Copyright (c) 2026 Hans Valerie
"""History migration, coordinator ownership, failed admission and teardown."""

import asyncio
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep
from typing import cast
from unittest.mock import Mock
from uuid import uuid4

import pytest

from tachyron.api import create_app
from tachyron.benchmarking import runner
from tachyron.benchmarking.exports import publish_exports
from tachyron.jobs import coordinator
from tachyron.jobs.coordinator import AdmissionError, Coordinator, Settings, stamp
from tachyron.jobs.models import Job, Metrics, RunRequest
from tachyron.persistence import HistoryStore
from tachyron.processing import (
    BatchResult,
    DatasetManifest,
    ResourceLimits,
    TransformConfig,
)
from tests.benchmark_helpers import example_experiment, make_manifest, sequential_pool
from tests.job_helpers import BODY, local_client, terminal


def saved_job(**updates: object) -> Job:
    """Generate a durable document without inventing measured production results."""
    return Job(
        id=str(uuid4()),
        request=RunRequest(dataset_id="tiny"),
        manifest_sha256="a" * 64,
        input_count=10,
        created_at=stamp(),
        total_batches=24,
    ).model_copy(update=updates)


def test_v0_migration_v1_reopen_and_recovery_preserve_results(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    store = HistoryStore(path)
    completed = saved_job(state="COMPLETED", ended_at=stamp())
    failed = saved_job(state="FAILED", error_code="image_failure", ended_at=stamp())
    active = saved_job(state="RUNNING", completed_batches=1, succeeded_images=10)
    queued = saved_job()
    metrics = Metrics.from_experiment(example_experiment())
    try:
        store.save(completed, metrics, key="completed")
        store.save(failed)
        store.save(active)
        store.save(queued)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            HistoryStore(path)
        assert store.get(active.id) == active
    finally:
        store.close()
    reopened = HistoryStore(path)
    try:
        reopened.recover(stamp())
        assert reopened.get(completed.id) == completed
        assert reopened.result(completed.id) == metrics
        assert reopened.by_key("completed") == completed
        assert reopened.get(failed.id) == failed
        for identifier in (active.id, queued.id):
            recovered = reopened.get(identifier)
            assert recovered is not None
            assert recovered.state == "FAILED"
            assert recovered.error_code == "server_interrupted"
            assert recovered.ended_at is not None
        recovered_active = reopened.get(active.id)
        assert recovered_active is not None
        assert (
            recovered_active.completed_batches,
            recovered_active.succeeded_images,
        ) == (1, 10)
        assert len(reopened.history(50, 0)) == 4
    finally:
        reopened.close()
    with closing(sqlite3.connect(path)) as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 1


@pytest.mark.parametrize("version", [0, 2])
def test_unknown_schema_is_rejected_without_modifying_history(
    tmp_path: Path, version: int
) -> None:
    path = tmp_path / "history.sqlite3"
    with closing(sqlite3.connect(path)) as database:
        database.execute("CREATE TABLE precious (value TEXT)")
        database.execute("INSERT INTO precious VALUES ('keep')")
        database.execute(f"PRAGMA user_version={version}")
        database.commit()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unsupported history schema"):
        HistoryStore(path)
    assert path.read_bytes() == before


def test_failed_admission_rolls_back_queue_and_can_be_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = make_manifest(tmp_path / "input")
    settings = Settings({"tiny": manifest.root}, tmp_path / "history")
    monkeypatch.setattr(runner, "run_parallel", sequential_pool)
    with local_client(create_app(settings)) as client:
        manager = cast("Coordinator", client.app_state["coordinator"])
        original = manager.store.save
        with monkeypatch.context() as patch:
            patch.setattr(
                manager.store,
                "save",
                Mock(side_effect=sqlite3.OperationalError("private storage details")),
            )
            response = client.post("/api/jobs", json=BODY)
        assert response.status_code == 503
        assert response.json() == {"detail": "history_unavailable"}
        assert manager._signals == {}
        assert not manager._queue
        assert manager.store.save == original
        retry = client.post("/api/jobs", json=BODY)
        assert terminal(client, retry.json()["id"]).state == "COMPLETED"


def test_storage_fault_stops_consumer_and_reports_unavailable_until_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = make_manifest(tmp_path / "input")
    settings = Settings({"tiny": manifest.root}, tmp_path / "history")
    with local_client(create_app(settings)) as client:
        manager = cast("Coordinator", client.app_state["coordinator"])
        save = manager.store.save

        def fail_running(
            job: Job, result: Metrics | None = None, *, key: str | None = None
        ) -> None:
            if job.state == "RUNNING":
                raise sqlite3.OperationalError("private storage details")
            save(job, result, key=key)

        with monkeypatch.context() as patch:
            patch.setattr(manager.store, "save", fail_running)
            accepted = client.post("/api/jobs", json=BODY)
            identifier = accepted.json()["id"]
            assert manager._thread is not None
            manager._thread.join(timeout=5)
            assert not manager._thread.is_alive()
            assert client.get(f"/api/jobs/{identifier}").status_code == 503
            assert client.post(f"/api/jobs/{identifier}/cancel").status_code == 503
            assert client.post("/api/jobs", json=BODY).status_code == 503
            assert client.get("/health").status_code == 200


def test_shutdown_cancels_waiting_work_and_drains_the_active_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = make_manifest(tmp_path / "input")
    settings = Settings({"tiny": manifest.root}, tmp_path / "history", queue_capacity=1)
    entered, release = Event(), Event()

    def blocked(
        workload: DatasetManifest,
        directory: Path,
        transform: TransformConfig,
        workers: int,
    ) -> BatchResult:
        entered.set()
        if not release.wait(10):
            raise RuntimeError("test release timed out")
        return sequential_pool(workload, directory, transform, workers)

    monkeypatch.setattr(runner, "run_parallel", blocked)
    manager = Coordinator(settings)
    manager.start()
    active = manager.submit(RunRequest.model_validate(BODY))
    assert entered.wait(5)
    waiting = manager.submit(RunRequest.model_validate(BODY))
    shutdown = Thread(target=manager.close)
    shutdown.start()
    try:
        deadline = monotonic() + 5
        while not manager.get(active.id).cancel_requested and monotonic() < deadline:
            sleep(0.01)
        assert manager.get(active.id).cancel_requested
        assert shutdown.is_alive()
        with pytest.raises(AdmissionError, match="coordinator_unavailable"):
            manager.submit(RunRequest.model_validate(BODY))
    finally:
        release.set()
        shutdown.join(timeout=10)
    assert not shutdown.is_alive()
    store = HistoryStore(settings.data_dir / "history.sqlite3")
    try:
        for identifier in (active.id, waiting.id):
            job = store.get(identifier)
            assert job is not None
            assert job.state == "CANCELLED"
        assert store.result(active.id) is not None
        assert store.result(waiting.id) is None
    finally:
        store.close()


def test_partial_startup_failure_releases_sqlite_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(data_dir=tmp_path / "history")
    monkeypatch.setattr(
        coordinator,
        "Thread",
        Mock(return_value=Mock(start=Mock(side_effect=RuntimeError("cannot start")))),
    )
    with pytest.raises(RuntimeError, match="cannot start"):
        Coordinator(settings).start()
    store = HistoryStore(settings.data_dir / "history.sqlite3")
    store.close()


@pytest.mark.parametrize("started", [False, True])
@pytest.mark.parametrize("failure", [KeyboardInterrupt, RuntimeError])
def test_failed_startup_stops_a_real_consumer_before_closing_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    started: bool,
    failure: type[BaseException],
) -> None:
    settings = Settings(data_dir=tmp_path / "history")
    manager = Coordinator(settings)
    consumers: list[Thread] = []
    original = failure("controlled thread startup failure")

    def make_thread(*, target: Callable[[], None], name: str) -> Thread:
        thread = Thread(target=target, name=name)
        start = thread.start

        def fail_start() -> None:
            if started:
                start()
            raise original

        monkeypatch.setattr(thread, "start", fail_start)
        consumers.append(thread)
        return thread

    monkeypatch.setattr(coordinator, "Thread", make_thread)
    try:
        with pytest.raises(
            failure, match="controlled thread startup failure"
        ) as caught:
            manager.start()
        assert caught.value is original
        assert not consumers[0].is_alive()
        assert manager._closing
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            manager.store.get("absent")
        reopened = HistoryStore(settings.data_dir / "history.sqlite3")
        reopened.close()
    finally:
        # Keep a regression on the old implementation from leaking a real thread.
        with manager._condition:
            manager._closing = True
            manager._condition.notify_all()
        if consumers and consumers[0].ident is not None:
            consumers[0].join(timeout=5)


def test_output_collision_cannot_import_preexisting_experiment_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = make_manifest(tmp_path / "input")
    monkeypatch.setattr(runner, "run_parallel", sequential_pool)
    settings = Settings({"tiny": manifest.root}, tmp_path / "history")
    with local_client(create_app(settings)) as client:
        manager = cast("Coordinator", client.app_state["coordinator"])
        with manager._condition:
            accepted = manager.submit(RunRequest.model_validate(BODY))
            directory = manager.directory / accepted.id
            directory.mkdir()
            publish_exports(example_experiment(), directory)
            before = {path.name: path.read_bytes() for path in directory.iterdir()}
        failed = terminal(client, accepted.id)
        assert failed.state == "FAILED"
        assert failed.error_code == "execution_failure"
        assert (
            failed.completed_batches
            == failed.succeeded_images
            == failed.failed_images
            == 0
        )
        assert client.get(f"/api/benchmarks/{failed.id}").json()["result"] is None
        assert client.get("/api/benchmarks").json()[0]["summaries"] is None
        assert client.get(f"/api/jobs/{failed.id}/metrics").status_code == 409
        assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


def test_recovery_rejects_unrelated_evidence_even_inside_an_owned_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = make_manifest(tmp_path / "input")
    settings = Settings({"tiny": manifest.root}, tmp_path / "history")

    def fail_with_foreign_evidence(
        workload: DatasetManifest,
        directory: Path,
        transform: TransformConfig,
        config: object,
        **controls: object,
    ) -> None:
        directory.mkdir()
        publish_exports(example_experiment(), directory)
        raise OSError("controlled failure with unrelated evidence")

    monkeypatch.setattr(coordinator, "run_experiment", fail_with_foreign_evidence)
    with local_client(create_app(settings)) as client:
        failed = terminal(client, client.post("/api/jobs", json=BODY).json()["id"])
        assert failed.state == "FAILED"
        assert (
            failed.completed_batches
            == failed.succeeded_images
            == failed.failed_images
            == 0
        )
        assert client.get(f"/api/benchmarks/{failed.id}").json()["result"] is None


@pytest.mark.parametrize("nested", [True, False])
def test_history_dataset_overlap_fails_before_writing(
    tmp_path: Path, nested: bool
) -> None:
    manifest = make_manifest(tmp_path / "input")
    data_dir = manifest.root / "history" if nested else tmp_path
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    with pytest.raises(ValueError, match="overlap"):
        Coordinator(Settings({"tiny": manifest.root}, data_dir)).start()
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


def test_discovery_reports_unavailable_and_enforces_existing_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = make_manifest(tmp_path / "input")
    settings = Settings(
        {"tiny": manifest.root, "missing": tmp_path / "private-missing"},
        tmp_path / "history",
    )
    with local_client(create_app(settings)) as client:
        info = client.get("/api/datasets")
        assert "private-missing" not in info.text
        assert info.json()[0]["error_code"] == "dataset_unavailable"
        assert info.json()[1]["manifest_sha256"] == manifest.sha256
        assert info.json()[1]["input_count"] == 3
        manager = cast("Coordinator", client.app_state["coordinator"])
        limited = DatasetManifest(
            manifest.root,
            manifest.entries,
            manifest.sha256,
            ResourceLimits(max_output_pixels=1),
        )
        monkeypatch.setitem(manager._manifests, "tiny", limited)
        assert client.post("/api/jobs", json=BODY).json() == {
            "detail": "resource_limit"
        }
        assert manager._signals == {}


@pytest.mark.spawn
def test_owner_crash_releases_lock_and_restart_retains_real_partial_progress(
    tmp_path: Path,
) -> None:
    manifest = make_manifest(tmp_path / "input")
    script = """
import os, sys
from pathlib import Path
from tachyron.benchmarking import runner
from tachyron.jobs.coordinator import Coordinator, Settings
from tachyron.jobs.models import RunRequest
from tachyron.processing import run_sequential
calls = 0
def execute(manifest, directory, transform, workers):
    global calls
    calls += 1
    if calls == 2:
        os._exit(23)
    return run_sequential(manifest, directory, transform)
runner.run_parallel = execute
root = Path(sys.argv[1])
manager = Coordinator(Settings({'tiny': root / 'input'}, root / 'history'))
manager.start()
manager.submit(RunRequest(dataset_id='tiny', width=2, height=2, tiny=True, repetitions=1))
manager._thread.join(15)
sys.exit(2)
"""
    result = subprocess.run(  # noqa: S603 -- Fixed isolated test program; only its temp directory varies.
        [sys.executable, "-I", "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 23, result.stderr
    with local_client(
        create_app(Settings({"tiny": manifest.root}, tmp_path / "history"))
    ) as client:
        recovered = client.get("/api/benchmarks").json()[0]["job"]
        assert recovered["state"] == "FAILED"
        assert recovered["error_code"] == "server_interrupted"
        assert (recovered["completed_batches"], recovered["succeeded_images"]) == (1, 3)
        assert client.get(f"/api/jobs/{recovered['id']}/metrics").status_code == 409
        assert client.get(f"/api/benchmarks/{recovered['id']}").json()["result"] is None


def test_lifespan_cancellation_waits_for_shutdown_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(data_dir=tmp_path / "history")
    entered, release = Event(), Event()
    original = Coordinator.close

    def slow_close(manager: Coordinator) -> None:
        entered.set()
        if not release.wait(10):
            raise RuntimeError("test release timed out")
        original(manager)

    monkeypatch.setattr(Coordinator, "close", slow_close)
    app = create_app(settings)

    async def exercise() -> None:
        ready = asyncio.Event()

        async def own_lifespan() -> None:
            async with app.router.lifespan_context(app):
                ready.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(own_lifespan())
        await ready.wait()
        task.cancel()
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    store = HistoryStore(settings.data_dir / "history.sqlite3")
    store.close()


@pytest.mark.parametrize(
    "configured", ["[]", '{"tiny": 1}', '{"bad/id": "input"}', "{" + " " * 16_384]
)
def test_invalid_operator_registry_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
) -> None:
    monkeypatch.setenv("TACHYRON_DATASETS", configured)
    with pytest.raises((TypeError, ValueError)):
        Settings.from_environment()


def test_environment_registry_and_queue_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TACHYRON_DATASETS", '{"tiny": "datasets/tiny"}')
    monkeypatch.setenv("TACHYRON_DATA_DIR", str(tmp_path))
    settings = Settings.from_environment()
    assert settings.datasets == {"tiny": Path("datasets/tiny")}
    assert settings.data_dir == tmp_path
    for capacity in (0, 17, True):
        with pytest.raises(ValueError, match="capacity"):
            Settings(queue_capacity=capacity)
    with pytest.raises(ValueError, match="32"):
        Settings({f"data-{number}": tmp_path for number in range(33)})

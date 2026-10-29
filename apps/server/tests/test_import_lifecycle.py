# Copyright (c) 2026 Hans Valerie
"""Import ownership at startup, cancellation, shutdown, and publication boundaries."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from starlette.types import Message, Scope

from tachyron.api import create_app
from tachyron.imports.models import DatasetImportError, Operation
from tachyron.imports.routes import cancel as cancel_route
from tachyron.imports.routes import owned_thread
from tachyron.imports.service import Session
from tachyron.imports.storage import Catalog
from tachyron.jobs.coordinator import AdmissionError, Coordinator
from tachyron.jobs.models import RunRequest
from tests.test_dataset_imports import payload, png, settings


def test_cancel_between_receipt_commit_and_session_attachment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created, attach, started, proceed, cancelling = (
        Event(),
        Event(),
        Event(),
        Event(),
        Event(),
    )
    initialize, start = Session.__init__, Session.start

    def before_attach(
        session: Session, catalog: Catalog, identifier: str, digest: str, boundary: str
    ) -> None:
        initialize(session, catalog, identifier, digest, boundary)
        created.set()
        assert attach.wait(10)

    def before_start(session: Session) -> None:
        started.set()
        assert proceed.wait(10)
        start(session)

    monkeypatch.setattr(Session, "__init__", before_attach)
    monkeypatch.setattr(Session, "start", before_start)
    body, headers, identifier = payload({"a.png": png()})
    with (
        TestClient(
            create_app(settings(tmp_path)), base_url="http://127.0.0.1"
        ) as client,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        manager = cast("Coordinator", client.app_state["coordinator"])
        request = executor.submit(
            client.post, "/api/dataset-imports", content=body, headers=headers
        )

        def cancel() -> Operation:
            cancelling.set()
            return cancel_route(UUID(identifier), manager, Response())

        try:
            assert created.wait(10)
            cancellation = executor.submit(cancel)
            assert cancelling.wait(5)
            try:
                early = cancellation.result(timeout=0.2)
            except TimeoutError:
                early = None
            attach.set()
            assert started.wait(10)
            result = cancellation.result(timeout=10)
            assert result.cancel_requested is True
            assert early is None
        finally:
            attach.set()
            proceed.set()
            request.result(timeout=15)
        assert manager.catalog.get(identifier).state == "CANCELLED"


def test_repeated_asgi_cancellation_never_abandons_mutating_thread() -> None:
    entered, release, completed = Event(), Event(), Event()

    def mutation() -> None:
        entered.set()
        assert release.wait(5)
        completed.set()

    async def interrupted() -> None:
        task = asyncio.create_task(owned_thread(mutation))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert completed.is_set()

    asyncio.run(interrupted())


def test_cancellation_during_helper_startup_is_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()
    exchange = Session.exchange

    def hold_startup(
        session: Session, command: bytes, expected: str
    ) -> dict[str, object]:
        result = exchange(session, command, expected)
        if command == b"go":
            entered.set()
            if not release.wait(10):
                raise RuntimeError("Startup test did not release helper")
        return result

    monkeypatch.setattr(Session, "exchange", hold_startup)
    body, headers, identifier = payload({"a.png": png()})
    with (
        TestClient(
            create_app(settings(tmp_path)), base_url="http://127.0.0.1"
        ) as client,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        request = executor.submit(
            client.post, "/api/dataset-imports", content=body, headers=headers
        )
        try:
            assert entered.wait(10)
            cancellation = client.post(
                f"/api/dataset-imports/{identifier}/cancel",
                headers={"X-Tachyron-Request": "1"},
            )
            assert cancellation.status_code == 202
            assert cancellation.json()["cancel_requested"] is True
        finally:
            release.set()
            request.result(timeout=15)
        receipt = client.get(f"/api/dataset-imports/{identifier}").json()
        assert receipt["state"] == "CANCELLED"
        assert client.get("/api/datasets").json() == []


def test_unconfirmed_helper_termination_closes_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held: list[Session] = []
    stop = Session.stop

    def cannot_stop(session: Session) -> None:
        held.append(session)
        raise DatasetImportError("worker_unavailable", 503)

    body, headers, identifier = payload({"a.png": png()})
    with TestClient(
        create_app(settings(tmp_path)),
        base_url="http://127.0.0.1",
        raise_server_exceptions=False,
    ) as client:
        try:
            with monkeypatch.context() as fault:
                fault.setattr(Session, "stop", cannot_stop)
                response = client.post(
                    "/api/dataset-imports", content=body, headers=headers
                )
            assert response.status_code == 503
            receipt = client.get(f"/api/dataset-imports/{identifier}").json()
            assert receipt["state"] == "READY"
            rejected = client.post(
                "/api/jobs",
                json={"dataset_id": receipt["dataset"]["dataset_id"]},
                headers={"X-Tachyron-Request": "1"},
            )
            assert rejected.status_code == 503
            assert rejected.json() == {"detail": "coordinator_unavailable"}
        finally:
            for session in held:
                stop(session)


def request_scope(manager: Coordinator, headers: dict[str, str]) -> Scope:
    """Inject the real lifespan owner into a direct ASGI connection."""
    return {
        "type": "http",
        "http_version": "1.1",
        "asgi": {"version": "3.0"},
        "method": "POST",
        "scheme": "http",
        "path": "/api/dataset-imports",
        "raw_path": b"/api/dataset-imports",
        "query_string": b"",
        "headers": [
            (b"host", b"127.0.0.1"),
            *((k.lower().encode(), v.encode()) for k, v in headers.items()),
        ],
        "state": {"coordinator": manager},
        "server": ("127.0.0.1", 80),
        "client": ("127.0.0.1", 1234),
    }


@pytest.mark.parametrize("failure", ["partial", "after-eof", "lost-response"])
def test_disconnect_boundary(tmp_path: Path, failure: str) -> None:
    configuration = settings(tmp_path)
    manager = Coordinator(configuration)
    manager.start()
    body, headers, identifier = payload({"a.png": png()})

    async def connected() -> None:
        calls = 0

        async def receive() -> Message:
            nonlocal calls
            calls += 1
            if calls == 1:
                return {
                    "type": "http.request",
                    "body": body[:80] if failure == "partial" else body,
                    "more_body": failure == "partial",
                }
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            if failure == "lost-response" and message["type"] == "http.response.start":
                raise OSError("Client connection lost after commit")

        app = create_app(configuration)
        if failure == "lost-response":
            with pytest.raises(OSError, match="connection lost"):
                await app(request_scope(manager, headers), receive, send)
        else:
            await app(request_scope(manager, headers), receive, send)
        assert calls == (2 if failure == "partial" else 1)

    try:
        asyncio.run(connected())
        receipt = manager.catalog.get(identifier)
        assert receipt.state == ("FAILED" if failure == "partial" else "READY")
        assert len(manager.catalog.datasets()) == (0 if failure == "partial" else 1)
        assert manager.import_session is None
    finally:
        manager.close()
    restarted = Coordinator(configuration)
    restarted.start()
    try:
        assert restarted.catalog.get(identifier) == receipt
        assert len(restarted.catalog.datasets()) == (0 if failure == "partial" else 1)
    finally:
        restarted.close()


@pytest.mark.parametrize("action", ["cancel", "shutdown"])
def test_stalled_receive_observes_cancellation_and_shutdown(
    tmp_path: Path, action: str
) -> None:
    configuration = settings(tmp_path)
    manager = Coordinator(configuration)
    manager.start()
    _, headers, identifier = payload({"a.png": png()})

    async def connected() -> None:
        stalled = asyncio.Event()

        async def receive() -> Message:
            stalled.set()
            await asyncio.Event().wait()
            raise RuntimeError("Unreachable receive")

        async def send(_message: Message) -> None:
            pass

        task = asyncio.create_task(
            create_app(configuration)(request_scope(manager, headers), receive, send)
        )
        await asyncio.wait_for(stalled.wait(), 10)
        if action == "shutdown":
            await asyncio.wait_for(asyncio.to_thread(manager.close), 5)
        else:
            session = manager.import_session
            assert session is not None
            assert session.cancel().cancel_requested
        await asyncio.wait_for(task, 5)

    try:
        asyncio.run(connected())
        if action == "cancel":
            assert manager.catalog.get(identifier).state == "CANCELLED"
            assert manager.import_session is None
    finally:
        if action == "cancel":
            manager.close()
    with TestClient(create_app(configuration), base_url="http://127.0.0.1") as client:
        assert (
            client.get(f"/api/dataset-imports/{identifier}").json()["state"]
            == "CANCELLED"
        )


def test_concurrent_keys_and_benchmark_admission_share_one_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()
    original = Session.start

    def hold(session: Session) -> None:
        original(session)
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(Session, "start", hold)
    body, headers, identifier = payload({"a.png": png()})
    with (
        TestClient(
            create_app(settings(tmp_path)), base_url="http://127.0.0.1"
        ) as client,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        task = executor.submit(
            client.post, "/api/dataset-imports", content=body, headers=headers
        )
        try:
            assert entered.wait(10)
            replay = client.post("/api/dataset-imports", content=b"", headers=headers)
            assert replay.status_code == 200
            assert replay.json()["state"] == "RECEIVING"
            conflicting = client.post(
                "/api/dataset-imports",
                content=b"",
                headers={**headers, "X-Tachyron-Metadata-SHA256": "0" * 64},
            )
            assert conflicting.status_code == 409
            other, other_headers, _ = payload({"b.png": png()})
            assert (
                client.post(
                    "/api/dataset-imports", content=other, headers=other_headers
                ).status_code
                == 429
            )
            job = client.post(
                "/api/jobs",
                json={"dataset_id": "anything"},
                headers={"X-Tachyron-Request": "1"},
            )
            assert job.status_code == 409
            assert job.json()["detail"] == "import_in_progress"
        finally:
            release.set()
            result = task.result(timeout=15)
        assert result.status_code == 201
        receipt = client.get(f"/api/dataset-imports/{identifier}").json()
        assert receipt["state"] == "READY"


def test_atomic_job_import_admission_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration = settings(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.png").write_bytes(png())
    manager = Coordinator(
        configuration.__class__(
            datasets={"tiny": source},
            data_dir=configuration.data_dir,
            dataset_dir=configuration.dataset_dir,
        )
    )
    # Keep the accepted job queued, so admission itself is tested without a benchmark.
    monkeypatch.setattr(manager, "_work", lambda: None)
    manager.start()
    manager._healthy = True
    barrier = Barrier(2)

    def admit(kind: str) -> str:
        barrier.wait(timeout=5)
        try:
            if kind == "job":
                manager.submit(RunRequest(dataset_id="tiny", tiny=True, repetitions=1))
            else:
                manager.begin_import(str(uuid4()), "0" * 64, "test")
        except (AdmissionError, DatasetImportError) as error:
            return error.code
        return kind

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            job, upload = (
                executor.submit(admit, "job"),
                executor.submit(admit, "import"),
            )
            outcomes = {job.result(timeout=10), upload.result(timeout=10)}
        assert outcomes in ({"job", "workload_busy"}, {"import", "import_in_progress"})
    finally:
        if manager.import_session is not None:
            session = cast("Session", manager.import_session)
            session.cancel()
            manager.catalog.finish(session.identifier, "test_cancel")
            session.close()
            manager.release_import(session)
        manager.close()

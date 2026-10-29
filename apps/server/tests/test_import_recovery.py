# Copyright (c) 2026 Hans Valerie
"""Private filesystem ownership, verified helper recovery and atomic publication."""

import multiprocessing
from pathlib import Path
from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import psutil
import pytest

from tachyron.benchmarking.models import BenchmarkError
from tachyron.benchmarking.ownership import experiment_lock
from tachyron.imports import storage
from tachyron.imports.models import DatasetImportError, ImportLimits
from tachyron.imports.service import Session
from tachyron.imports.storage import Catalog
from tests.test_dataset_imports import payload, png


@pytest.mark.parametrize("stage", ["receiving", "validated", "ready"])
def test_recovery_reaps_only_verified_helper_and_retains_ready(
    tmp_path: Path, stage: str
) -> None:
    body, headers, identifier = payload({"nested/a.png": png()})
    catalog = Catalog(tmp_path / "managed", ImportLimits())
    session = Session(
        catalog, identifier, headers["X-Tachyron-Metadata-SHA256"], "test"
    )
    try:
        session.start()
        session.feed(body)
        with pytest.raises(BenchmarkError, match="experiment lock"), experiment_lock():
            pytest.fail("The helper did not retain the account workload lock")
        if stage != "receiving":
            document = session.validate()
            if stage == "ready":
                session.publish(document)
        process = session.process
        assert process is not None
        assert process.is_alive()
        catalog.close()  # Simulate lost parent ownership with the child still alive.
        recovered = Catalog(tmp_path / "managed", ImportLimits())
        try:
            recovered.recover()
            process.join(timeout=5)
            assert not process.is_alive()
            assert process.exitcode is not None
            receipt = recovered.get(identifier)
            assert receipt.state == ("READY" if stage == "ready" else "FAILED")
            assert len(recovered.datasets()) == (1 if stage == "ready" else 0)
            directory = recovered.objects / identifier
            assert directory.exists() == (stage == "ready")
            if stage == "ready":
                assert (directory / "files/nested/a.png").read_bytes() == png()
            assert (
                recovered.connection.execute("SELECT count(*) FROM work").fetchone()[0]
                == 0
            )
        finally:
            recovered.close()
        with experiment_lock():
            pass
    finally:
        session.stop()
        catalog.close()


@pytest.mark.parametrize("denied", [False, True])
def test_unconfirmed_helper_exit_retains_receipt_and_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, denied: bool
) -> None:
    catalog = Catalog(tmp_path / "managed", ImportLimits())
    identifier = str(uuid4())
    nonce = catalog.create(identifier, "0" * 64)
    catalog.helper(identifier, 1234, 1.0)
    directory = catalog.objects / identifier
    directory.mkdir()
    (directory / ".owner").write_text(nonce, encoding="ascii")
    (directory / "precious").write_bytes(b"retain")
    process = Mock(spec=psutil.Process)
    process.create_time.return_value = 1.0
    process.is_running.return_value = True
    process.status.return_value = psutil.STATUS_RUNNING
    if denied:
        process.status.side_effect = psutil.AccessDenied(1234)
    clock = iter([0.0, 6.0])
    monkeypatch.setattr(storage, "monotonic", lambda: next(clock))
    monkeypatch.setattr(storage.psutil, "Process", Mock(return_value=process))
    try:
        with pytest.raises(
            DatasetImportError, match="ownership_unconfirmed"
        ) as failure:
            catalog.recover()
        assert failure.value.status == 503
        process.kill.assert_not_called()
        assert catalog.get(identifier).state == "RECEIVING"
        assert (directory / "precious").read_bytes() == b"retain"
        assert (
            catalog.connection.execute("SELECT count(*) FROM work").fetchone()[0] == 1
        )
    finally:
        catalog.close()


def unrelated_process() -> None:
    """A real spawn process must not become killable just because its PID was stored."""
    Event().wait(30)


def test_recovery_never_kills_unverified_process(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "managed", ImportLimits())
    identifier = str(uuid4())
    catalog.create(identifier, "0" * 64)
    process = multiprocessing.get_context("spawn").Process(target=unrelated_process)
    process.start()
    try:
        assert process.pid is not None
        catalog.helper(
            identifier, process.pid, psutil.Process(process.pid).create_time()
        )
        directory = catalog.objects / identifier
        directory.mkdir()
        (directory / ".owner").write_text("unproven owner", encoding="ascii")
        (directory / "unknown").write_text("retain", encoding="ascii")
        with pytest.raises(DatasetImportError, match="ownership_unconfirmed"):
            catalog.recover()
        assert process.is_alive()
        assert (directory / "unknown").read_text(encoding="ascii") == "retain"
        assert catalog.get(identifier).state == "RECEIVING"
    finally:
        process.terminate()
        process.join(timeout=5)
        process.close()
        catalog.close()


def test_publication_failure_rolls_back_registration_and_preserves_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body, headers, identifier = payload({"a.png": png()})
    catalog = Catalog(tmp_path / "managed", ImportLimits())
    session = Session(
        catalog, identifier, headers["X-Tachyron-Metadata-SHA256"], "test"
    )
    try:
        session.start()
        session.feed(body)
        document = session.validate()

        def fail_save(_operation: object) -> None:
            raise OSError("Storage unavailable after registration insert")

        with monkeypatch.context() as fault:
            fault.setattr(catalog, "_save", fail_save)
            with pytest.raises(OSError, match="Storage unavailable"):
                session.publish(document)
        assert catalog.datasets() == []
        assert catalog.get(identifier).state == "VALIDATING"
        catalog.finish(identifier, "test_failure")
        assert catalog.get(identifier).state == "FAILED"
    finally:
        session.close()
        catalog.close()


def test_cancellation_at_publication_cannot_become_ready(tmp_path: Path) -> None:
    body, headers, identifier = payload({"a.png": png()})
    catalog = Catalog(tmp_path / "managed", ImportLimits())
    session = Session(
        catalog, identifier, headers["X-Tachyron-Metadata-SHA256"], "test"
    )
    try:
        session.start()
        session.feed(body)
        document = session.validate()
        session.cancel()
        with pytest.raises(DatasetImportError, match="import_cancelled"):
            session.publish(document)
        terminal = catalog.finish(identifier, "import_cancelled")
        assert terminal is not None
        assert terminal.state == "CANCELLED"
        assert catalog.datasets() == []
    finally:
        session.close()
        catalog.close()

# Copyright (c) 2026 Hans Valerie
"""Characterize connection shutdown, owned-process escalation and retry boundaries."""

from contextlib import closing
from multiprocessing.context import SpawnProcess
from pathlib import Path
from unittest.mock import Mock, call
from uuid import uuid4

import pytest

from tachyron.imports.models import DatasetImportError, ImportLimits
from tachyron.imports.service import Session
from tachyron.imports.storage import Catalog
from tachyron.imports.worker import Channel


@pytest.mark.parametrize(
    ("alive", "methods", "waits", "failed"),
    [
        ((False, False, False), ["join", "close"], [0.2], False),
        ((True, False, False), ["join", "terminate", "join", "close"], [0.2, 5], False),
        (
            (True, True, False),
            ["join", "terminate", "join", "kill", "join", "close"],
            [0.2, 5, 5],
            False,
        ),
        (
            (True, True, True),
            ["join", "terminate", "join", "kill", "join"],
            [0.2, 5, 5],
            True,
        ),
    ],
)
def test_stop_escalates_in_order_and_retains_unconfirmed_owner(
    tmp_path: Path,
    alive: tuple[bool, bool, bool],
    methods: list[str],
    waits: list[float],
    failed: bool,
) -> None:
    with closing(Catalog(tmp_path / "managed", ImportLimits())) as catalog:
        session = Session(catalog, str(uuid4()), "0" * 64, "test")
        process = Mock(spec=SpawnProcess, pid=1234)
        process.is_alive.side_effect = alive
        session.process = process
        if failed:
            with pytest.raises(DatasetImportError, match="worker_unavailable") as error:
                session.stop()
            assert error.value.status == 503
            assert session.process is process
        else:
            session.stop()
            assert session.process is None
        assert [
            item[0] for item in process.method_calls if item[0] != "is_alive"
        ] == methods
        assert process.join.call_args_list == [call(timeout=value) for value in waits]
        if failed:
            process.is_alive.side_effect = None
            process.is_alive.return_value = False
            session.stop()
        session.stop()
        process.close.assert_called_once_with()


def test_unstarted_process_is_closed_without_joining_or_signalling(
    tmp_path: Path,
) -> None:
    with closing(Catalog(tmp_path / "managed", ImportLimits())) as catalog:
        session = Session(catalog, str(uuid4()), "0" * 64, "test")
        process = Mock(spec=SpawnProcess, pid=None)
        session.process = process
        session.stop()
        assert process.method_calls == [call.close()]
        assert session.process is None


@pytest.mark.parametrize(
    ("validated", "broken"), [(False, False), (True, False), (True, True)]
)
def test_release_requires_complete_validation_and_connection_closes_once(
    tmp_path: Path, validated: bool, broken: bool
) -> None:
    with closing(Catalog(tmp_path / "managed", ImportLimits())) as catalog:
        session = Session(catalog, str(uuid4()), "0" * 64, "test")
        channel = Mock(spec=Channel)
        if broken:
            channel.send_bytes.side_effect = BrokenPipeError
        session.connection = channel
        session._validated = validated
        session.stop()
        session.stop()
        assert session.connection is None
        assert session.process is None
        assert channel.method_calls == (
            [call.send_bytes(b"release")] if validated else []
        ) + [call.close()]


def test_connection_close_failure_keeps_process_owned_and_allows_retry(
    tmp_path: Path,
) -> None:
    with closing(Catalog(tmp_path / "managed", ImportLimits())) as catalog:
        session = Session(catalog, str(uuid4()), "0" * 64, "test")
        channel = Mock(spec=Channel)
        channel.close.side_effect = OSError("controlled close failure")
        process = Mock(spec=SpawnProcess, pid=None)
        session.connection = channel
        session.process = process
        with pytest.raises(OSError, match="controlled close failure"):
            session.stop()
        assert session.connection is channel
        assert session.process is process
        assert process.method_calls == []
        channel.close.side_effect = None
        session.stop()
        assert session.connection is None
        assert session.process is None
        process.close.assert_called_once_with()


@pytest.mark.parametrize("stage", ["join", "terminate", "kill", "close"])
def test_process_shutdown_failure_retains_the_exact_owned_process(
    tmp_path: Path, stage: str
) -> None:
    with closing(Catalog(tmp_path / "managed", ImportLimits())) as catalog:
        session = Session(catalog, str(uuid4()), "0" * 64, "test")
        process = Mock(spec=SpawnProcess, pid=1234)
        process.is_alive.return_value = stage != "close"
        getattr(process, stage).side_effect = OSError("controlled process failure")
        session.process = process
        with pytest.raises(OSError, match="controlled process failure"):
            session.stop()
        assert session.process is process
        if stage != "close":
            process.close.assert_not_called()

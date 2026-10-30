# Copyright (c) 2026 Hans Valerie
"""Controlled scheduling boundaries; real spawn is covered by local execution tests."""

from collections.abc import Callable, Iterable
from concurrent.futures import Future
from pathlib import Path
from threading import Event
from typing import Literal
from unittest.mock import Mock

import pytest

from tachyron.execution import inspect_execution, run_local
from tachyron.processing import (
    ImageOutcome,
    ManifestEntry,
    ResourceLimits,
    TransformConfig,
)
from tachyron.workers import WorkerExecutionError, operational
from tests.benchmark_helpers import make_manifest


class ControlledPool:
    """Model two dispatched calls and a cancellable pending window without sleeps."""

    def __init__(self) -> None:
        self._processes: dict[int, object] = {}
        self._executor_manager_thread = None
        self.calls: dict[Future[ImageOutcome], Callable[[], ImageOutcome]] = {}
        self.closed = False
        self.peak = 0

    def submit(
        self,
        function: Callable[
            [Path, ManifestEntry, Path, TransformConfig, ResourceLimits], ImageOutcome
        ],
        root: Path,
        entry: ManifestEntry,
        output: Path,
        config: TransformConfig,
        limits: ResourceLimits,
    ) -> Future[ImageOutcome]:
        future = Future[ImageOutcome]()
        if len(self.calls) < 2:
            future.set_running_or_notify_cancel()
        self.calls[future] = lambda: function(root, entry, output, config, limits)
        return future

    def complete(
        self,
        pending: Iterable[Future[ImageOutcome]],
        *,
        timeout: float,
        return_when: Literal["FIRST_COMPLETED"],
    ) -> tuple[set[Future[ImageOutcome]], set[Future[ImageOutcome]]]:
        values = list(pending)
        self.peak = max(self.peak, len(values))
        first = values[0]
        if not first.running():
            first.set_running_or_notify_cancel()
        first.set_result(self.calls[first]())
        return {first}, set(values[1:])

    def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
        for future, work in self.calls.items():
            if future.running():
                future.set_result(work())
            elif not future.done():
                future.cancel()
        self.closed = True


def test_cancellation_drains_dispatched_work_and_proves_unstarted_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    pool = ControlledPool()
    cancelled = Event()
    monkeypatch.setattr(operational, "ProcessPoolExecutor", Mock(return_value=pool))

    def complete(
        pending: Iterable[Future[ImageOutcome]],
        *,
        timeout: float,
        return_when: Literal["FIRST_COMPLETED"],
    ) -> tuple[set[Future[ImageOutcome]], set[Future[ImageOutcome]]]:
        cancelled.set()
        return pool.complete(pending, timeout=timeout, return_when=return_when)

    monkeypatch.setattr(operational, "wait", complete)
    result = run_local(
        manifest.root,
        tmp_path / "output",
        TransformConfig(2, 2),
        2,
        cancelled=cancelled.is_set,
    )
    assert result.state == "CANCELLED"
    assert result.counts == {
        "succeeded": 2,
        "failed": 0,
        "unknown": 0,
        "not_started": 1,
    }
    assert pool.peak <= 4
    assert pool.closed
    assert inspect_execution(tmp_path / "output") == result


def test_uncertain_submission_is_unknown_and_never_inferred_from_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    pool = ControlledPool()
    original = pool.submit

    def submit(
        function: Callable[
            [Path, ManifestEntry, Path, TransformConfig, ResourceLimits], ImageOutcome
        ],
        root: Path,
        entry: ManifestEntry,
        output: Path,
        config: TransformConfig,
        limits: ResourceLimits,
    ) -> Future[ImageOutcome]:
        original(function, root, entry, output, config, limits)
        raise RuntimeError("private startup failure after possible dispatch")

    monkeypatch.setattr(pool, "submit", submit)
    monkeypatch.setattr(operational, "ProcessPoolExecutor", Mock(return_value=pool))
    result = run_local(manifest.root, tmp_path / "output", TransformConfig(2, 2), 1)
    assert result.state == "FAILED"
    assert result.counts == {
        "succeeded": 0,
        "failed": 0,
        "unknown": 1,
        "not_started": 2,
    }
    assert len(list((tmp_path / "output").glob("*.png"))) == 1
    assert inspect_execution(tmp_path / "output") == result
    assert pool.closed


def test_startup_failure_before_submission_accounts_all_as_unstarted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    monkeypatch.setattr(
        operational,
        "ProcessPoolExecutor",
        Mock(side_effect=OSError("private startup failure")),
    )
    result = run_local(manifest.root, tmp_path / "output", TransformConfig(2, 2), 1)
    assert result.state == "FAILED"
    assert result.counts == {
        "succeeded": 0,
        "failed": 0,
        "unknown": 0,
        "not_started": 3,
    }


def test_shutdown_failure_prevents_terminal_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    pool = ControlledPool()
    monkeypatch.setattr(operational, "ProcessPoolExecutor", Mock(return_value=pool))
    monkeypatch.setattr(operational, "wait", pool.complete)
    monkeypatch.setattr(
        operational,
        "_shutdown_pool",
        Mock(side_effect=OSError("private shutdown failure")),
    )
    with pytest.raises(WorkerExecutionError, match="unconfirmed"):
        run_local(manifest.root, tmp_path / "output", TransformConfig(2, 2), 1)
    assert not (tmp_path / "output" / "execution.json").exists()
    assert inspect_execution(tmp_path / "output").state == "UNCONFIRMED"


def test_parent_interrupt_remains_cooperative_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    pool = ControlledPool()
    monkeypatch.setattr(operational, "ProcessPoolExecutor", Mock(return_value=pool))
    interrupted = False

    def interrupt_once(
        pending: Iterable[Future[ImageOutcome]],
        *,
        timeout: float,
        return_when: Literal["FIRST_COMPLETED"],
    ) -> tuple[set[Future[ImageOutcome]], set[Future[ImageOutcome]]]:
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return pool.complete(pending, timeout=timeout, return_when=return_when)

    monkeypatch.setattr(operational, "wait", interrupt_once)
    result = run_local(manifest.root, tmp_path / "output", TransformConfig(2, 2), 2)
    assert result.state == "CANCELLED"
    assert result.cancel_requested
    assert result.error_code is None
    assert pool.closed
    assert inspect_execution(tmp_path / "output") == result

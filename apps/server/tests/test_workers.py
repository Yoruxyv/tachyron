# Copyright (c) 2026 Hans Valerie
"""Real spawn parity plus deterministic scheduling and pool-failure regressions."""

import hashlib
import json
import multiprocessing
import os
import signal
import subprocess
import sys
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from multiprocessing.process import BaseProcess
from pathlib import Path
from threading import Thread
from typing import NoReturn, cast
from unittest.mock import Mock

import pytest
from PIL import Image

from tachyron import workers
from tachyron.processing import (
    ImageOutcome,
    ManifestEntry,
    ProcessingError,
    ResourceLimits,
    TransformConfig,
    build_manifest,
    process_image,
    run_sequential,
)
from tachyron.workers import WorkerExecutionError, run_parallel
from tachyron.workers._startup import defer_startup_interrupt


def make_dataset(root: Path, count: int = 9) -> None:
    """Generate tiny, distinct PNGs without a binary dataset dependency."""
    root.mkdir()
    for index in range(count):
        with Image.new("RGB", (4, 3), (index * 13 % 256, 64, 192)) as image:
            image.save(root / f"{index:02}.png")


def record_process(
    root: Path,
    entry: ManifestEntry,
    output: Path,
    config: TransformConfig,
    limits: ResourceLimits,
) -> ImageOutcome:
    """Record the real child PID, then use the exact production transformation."""
    (output / f"pid-{entry.relative_path}.txt").write_text(
        str(os.getpid()), encoding="utf-8"
    )
    return process_image(root, entry, output, config, limits)


def raise_in_worker(
    root: Path,
    entry: ManifestEntry,
    output: Path,
    config: TransformConfig,
    limits: ResourceLimits,
) -> NoReturn:
    """Raise an unexpected exception across the real process boundary."""
    raise RuntimeError("private worker detail")


def exit_in_worker(
    root: Path,
    entry: ManifestEntry,
    output: Path,
    config: TransformConfig,
    limits: ResourceLimits,
) -> NoReturn:
    """A task's SystemExit must not become a successful parent CLI exit."""
    raise SystemExit(0)


def collide_one_output(
    root: Path,
    entry: ManifestEntry,
    output: Path,
    config: TransformConfig,
    limits: ResourceLimits,
) -> ImageOutcome:
    """Reproduce a destination collision while retaining another valid task."""
    if entry.relative_path == "00.png":
        name = hashlib.sha256(entry.relative_path.encode()).hexdigest() + ".png"
        with (output / name).open("xb") as stream:
            stream.write(b"keep-existing")
    return process_image(root, entry, output, config, limits)


def crash_in_worker(
    root: Path,
    entry: ManifestEntry,
    output: Path,
    config: TransformConfig,
    limits: ResourceLimits,
) -> NoReturn:
    """Simulate abrupt death while the worker owns an unpublished temporary file."""
    (output / ".tachyron-crash.tmp").write_bytes(b"partial")
    os._exit(23)


def fail_initializer() -> NoReturn:
    """Reproduce a genuine process initializer failure."""
    raise RuntimeError("private initialization detail")


@pytest.mark.spawn
@pytest.mark.parametrize("count", [1, 2, 4, 8])
def test_spawn_pool_matches_sequential_and_joins_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    dataset = tmp_path / "input"
    make_dataset(dataset)
    (dataset / "bad.png").write_bytes(b"corrupted")
    manifest = build_manifest(dataset)
    config = TransformConfig(3, 2)
    sequential = run_sequential(manifest, tmp_path / "sequential", config)
    before = {child.pid for child in multiprocessing.active_children()}
    monkeypatch.setattr(workers, "process_image", record_process)
    result = run_parallel(manifest, tmp_path / "parallel", config, count)
    assert result.outcomes == sequential.outcomes
    assert (result.succeeded, result.failed) == (9, 1)
    assert len(result.outcomes) == len(manifest.entries)
    assert [outcome.relative_path for outcome in result.outcomes] == [
        entry.relative_path for entry in manifest.entries
    ]
    for outcome in result.outcomes:
        if outcome.output_name is not None:
            assert (result.output_dir / outcome.output_name).read_bytes() == (
                sequential.output_dir / outcome.output_name
            ).read_bytes()
    identifiers = [
        int(path.read_text()) for path in result.output_dir.glob("pid-*.txt")
    ]
    assert len(identifiers) == len(manifest.entries)
    assert os.getpid() not in identifiers
    assert 1 <= len(set(identifiers)) <= count
    assert {child.pid for child in multiprocessing.active_children()} <= before
    assert not list(result.output_dir.glob(".tachyron-*.tmp"))
    report = json.loads((result.output_dir / "run.json").read_text(encoding="utf-8"))
    assert report["execution"] == {
        "mode": "process_pool",
        "requested_workers": count,
        "start_method": "spawn",
        "max_in_flight": 2 * count,
    }
    assert "execution" not in sequential.to_dict()
    assert build_manifest(dataset) == manifest


@pytest.mark.parametrize("count", [0, -1, 3, 16, True, False, 1.5, "2"])
def test_invalid_worker_count_has_no_output_or_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: object
) -> None:
    dataset = tmp_path / "input"
    make_dataset(dataset, 1)
    manifest = build_manifest(dataset)
    constructor = Mock(
        side_effect=AssertionError("invalid request must not start a pool")
    )
    monkeypatch.setattr(workers, "ProcessPoolExecutor", constructor)
    with pytest.raises((ValueError, TypeError)):
        run_parallel(
            manifest, tmp_path / "run", TransformConfig(), cast("int", count)
        )  # Deliberately validate untyped caller input.
    assert not (tmp_path / "run").exists()
    constructor.assert_not_called()


def test_resource_limits_reject_before_pool_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "input"
    make_dataset(dataset, 1)
    manifest = build_manifest(dataset, ResourceLimits(max_output_pixels=4))
    constructor = Mock()
    monkeypatch.setattr(workers, "ProcessPoolExecutor", constructor)
    with pytest.raises(ProcessingError, match="output pixel limit"):
        run_parallel(manifest, tmp_path / "run", TransformConfig(3, 2), 2)
    constructor.assert_not_called()
    assert not (tmp_path / "run").exists()


class ControlledPool:
    """A concrete test double controlling submission, consumption, and failures."""

    def __init__(self) -> None:
        self._processes: dict[int, BaseProcess] = {}
        self._executor_manager_thread: Thread | None = None
        self.pending: list[Future[ImageOutcome]] = []
        self.submitted: list[str] = []
        self.completion: list[str] = []
        self.peak = 0
        self.shutdown = Mock()
        self.submit_failure: BaseException | None = None
        self.wait_failure: BaseException | None = None
        self.future_failure: Exception | None = None

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
        if self.submit_failure is not None and len(self.submitted) == 1:
            raise self.submit_failure
        future: Future[ImageOutcome] = Future()
        if self.future_failure is not None:
            future.set_exception(self.future_failure)
        else:
            future.set_result(function(root, entry, output, config, limits))
        self.pending.append(future)
        self.submitted.append(entry.relative_path)
        self.peak = max(self.peak, len(self.pending))
        return future

    def wait_last(
        self, futures: object, *, return_when: str
    ) -> tuple[set[Future[ImageOutcome]], set[Future[ImageOutcome]]]:
        if self.wait_failure is not None:
            raise self.wait_failure
        future = self.pending.pop()
        if self.future_failure is None:
            self.completion.append(future.result().relative_path)
        return {future}, set(self.pending)


def install_pool(monkeypatch: pytest.MonkeyPatch, pool: ControlledPool) -> Mock:
    """Use controlled completion without introducing a production executor hook."""
    constructor = Mock(return_value=pool)
    monkeypatch.setattr(workers, "ProcessPoolExecutor", constructor)
    monkeypatch.setattr(workers, "wait", pool.wait_last)
    return constructor


def test_bounded_out_of_order_results_are_finalized_in_manifest_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "input"
    make_dataset(dataset, 9)
    manifest = build_manifest(dataset)
    pool = ControlledPool()
    constructor = install_pool(monkeypatch, pool)
    result = run_parallel(manifest, tmp_path / "run", TransformConfig(2, 2), 2)
    expected = [entry.relative_path for entry in manifest.entries]
    assert pool.submitted == expected
    assert pool.completion != expected
    assert pool.peak == 4
    assert [outcome.relative_path for outcome in result.outcomes] == expected
    assert len({outcome.output_name for outcome in result.outcomes}) == 9
    assert result.succeeded == 9
    pool.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
    options = constructor.call_args.kwargs
    assert options["max_workers"] == 2
    assert options["mp_context"].get_start_method() == "spawn"


@pytest.mark.parametrize("phase", ["construct", "submit", "wait", "result"])
def test_pool_failure_never_publishes_complete_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    dataset = tmp_path / "input"
    make_dataset(dataset, 4)
    manifest = build_manifest(dataset)
    pool = ControlledPool()
    constructor = install_pool(monkeypatch, pool)
    if phase == "construct":
        constructor.side_effect = OSError("private startup detail")
    elif phase == "submit":
        pool.submit_failure = OSError("private submission detail")
    elif phase == "wait":
        pool.wait_failure = RuntimeError("private wait detail")
    else:
        pool.future_failure = RuntimeError("private future detail")
    handler = signal.getsignal(signal.SIGINT)
    with pytest.raises(
        WorkerExecutionError, match="Parallel execution failed"
    ) as caught:
        run_parallel(manifest, tmp_path / "run", TransformConfig(2, 2), 2)
    assert caught.value.__cause__ is not None
    assert "private" not in str(caught.value)
    assert not (tmp_path / "run/run.json").exists()
    assert signal.getsignal(signal.SIGINT) == handler
    if phase == "construct":
        pool.shutdown.assert_not_called()
    else:
        pool.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
    assert len(pool.submitted) == len(set(pool.submitted))


def test_interrupt_drains_pool_and_preserves_completed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "input"
    make_dataset(dataset, 4)
    manifest = build_manifest(dataset)
    pool = ControlledPool()
    pool.wait_failure = KeyboardInterrupt()
    install_pool(monkeypatch, pool)
    handler = signal.getsignal(signal.SIGINT)
    with pytest.raises(KeyboardInterrupt):
        run_parallel(manifest, tmp_path / "run", TransformConfig(2, 2), 1)
    pool.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
    assert signal.getsignal(signal.SIGINT) == handler
    assert len(list((tmp_path / "run").glob("*.png"))) == 2
    assert not (tmp_path / "run/run.json").exists()


@pytest.mark.spawn
@pytest.mark.parametrize(
    "failure", ["exception", "crash", "initializer", "system_exit"]
)
def test_real_spawn_failures_release_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    dataset = tmp_path / "input"
    make_dataset(dataset, 2)
    before = {child.pid for child in multiprocessing.active_children()}
    if failure == "initializer":
        monkeypatch.setattr(workers, "_initialize_worker", fail_initializer)
    elif failure == "system_exit":
        monkeypatch.setattr(workers, "process_image", exit_in_worker)
    else:
        monkeypatch.setattr(
            workers,
            "process_image",
            raise_in_worker if failure == "exception" else crash_in_worker,
        )
    with pytest.raises(WorkerExecutionError):
        run_parallel(
            build_manifest(dataset), tmp_path / "run", TransformConfig(2, 2), 1
        )
    assert {child.pid for child in multiprocessing.active_children()} <= before
    assert not list((tmp_path / "run").glob(".tachyron-*.tmp"))
    assert not (tmp_path / "run/run.json").exists()


def test_explicit_oversubscription_warns_without_changing_requested_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    dataset = tmp_path / "input"
    make_dataset(dataset, 1)
    monkeypatch.setattr(os, "cpu_count", lambda: 1)
    pool = ControlledPool()
    constructor = install_pool(monkeypatch, pool)
    result = run_parallel(
        build_manifest(dataset), tmp_path / "run", TransformConfig(2, 2), 8
    )
    assert result.succeeded == 1
    assert constructor.call_args.kwargs["max_workers"] == 8
    assert "logical CPU" in caplog.text


@pytest.mark.spawn
def test_real_interrupt_waits_for_running_tasks_and_releases_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "input"
    make_dataset(dataset, 6)
    manifest = build_manifest(dataset)
    before = {child.pid for child in multiprocessing.active_children()}
    handler = signal.getsignal(signal.SIGINT)

    def interrupt_after_one_result(
        futures: Iterable[Future[ImageOutcome]], *, return_when: str
    ) -> NoReturn:
        outcome = next(iter(futures)).result(timeout=30)
        assert outcome.success
        raise KeyboardInterrupt

    monkeypatch.setattr(workers, "wait", interrupt_after_one_result)
    with pytest.raises(KeyboardInterrupt):
        run_parallel(manifest, tmp_path / "run", TransformConfig(2, 2), 2)
    assert {child.pid for child in multiprocessing.active_children()} <= before
    assert signal.getsignal(signal.SIGINT) == handler
    assert list((tmp_path / "run").glob("*.png"))
    assert not (tmp_path / "run/run.json").exists()
    assert not list((tmp_path / "run").glob(".tachyron-*.tmp"))
    assert build_manifest(dataset) == manifest


@pytest.mark.spawn
def test_pool_preserves_input_change_and_missing_file_failures(tmp_path: Path) -> None:
    dataset = tmp_path / "input"
    make_dataset(dataset, 3)
    manifest = build_manifest(dataset)
    with Image.new("RGB", (4, 3), "blue") as changed:
        changed.save(dataset / "00.png")
    (dataset / "01.png").unlink()
    result = run_parallel(manifest, tmp_path / "run", TransformConfig(2, 2), 2)
    assert (result.succeeded, result.failed) == (1, 2)
    assert [item.error_code for item in result.outcomes] == [
        "input_changed",
        "io_error",
        None,
    ]


@pytest.mark.spawn
def test_pool_collision_is_accounted_without_overwriting_or_aborting_other_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "input"
    make_dataset(dataset, 2)
    monkeypatch.setattr(workers, "process_image", collide_one_output)
    result = run_parallel(
        build_manifest(dataset), tmp_path / "run", TransformConfig(2, 2), 2
    )
    assert (result.succeeded, result.failed) == (1, 1)
    assert result.outcomes[0].error_code == "output_collision"
    assert result.outcomes[1].success
    name = hashlib.sha256(b"00.png").hexdigest() + ".png"
    assert (result.output_dir / name).read_bytes() == b"keep-existing"


@pytest.mark.spawn
def test_guarded_cli_spawns_real_children(tmp_path: Path) -> None:
    dataset = tmp_path / "input"
    make_dataset(dataset, 3)
    result = subprocess.run(  # noqa: S603 -- Fixed installed module and generated paths, no shell.
        [
            sys.executable,
            "-I",
            "-m",
            "tachyron.cli",
            str(dataset),
            "--output",
            str(tmp_path / "run"),
            "--width",
            "2",
            "--height",
            "2",
            "--workers",
            "2",
        ],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert (report["succeeded"], report["failed"]) == (3, 0)
    assert report["execution"]["requested_workers"] == 2
    assert report["execution"]["start_method"] == "spawn"


def test_import_does_not_construct_a_pool_or_import_fastapi() -> None:
    script = "import concurrent.futures, sys; from unittest.mock import Mock; concurrent.futures.ProcessPoolExecutor = Mock(side_effect=AssertionError('pool created at import')); import tachyron.workers; assert 'fastapi' not in sys.modules"
    result = subprocess.run(  # noqa: S603 -- Trusted fixed import probe, no shell.
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_wrong_worker_identity_is_fatal_without_a_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "input"
    make_dataset(dataset, 1)
    pool = ControlledPool()
    install_pool(monkeypatch, pool)
    monkeypatch.setattr(
        workers, "process_image", Mock(return_value=ImageOutcome("wrong.png"))
    )
    with pytest.raises(WorkerExecutionError):
        run_parallel(
            build_manifest(dataset), tmp_path / "run", TransformConfig(2, 2), 1
        )
    pool.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
    assert not (tmp_path / "run/run.json").exists()


@pytest.mark.parametrize("target", ["existing", "input/nested"])
def test_unsafe_pool_outputs_reject_before_pool_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    dataset = tmp_path / "input"
    make_dataset(dataset, 1)
    (tmp_path / "existing").mkdir()
    sentinel = tmp_path / "existing/keep.txt"
    sentinel.write_bytes(b"keep")
    constructor = Mock()
    monkeypatch.setattr(workers, "ProcessPoolExecutor", constructor)
    with pytest.raises(ProcessingError):
        run_parallel(build_manifest(dataset), tmp_path / target, TransformConfig(), 1)
    constructor.assert_not_called()
    assert sentinel.read_bytes() == b"keep"
    assert not (dataset / "nested").exists()


@pytest.mark.spawn
@pytest.mark.parametrize(
    ("stage", "kind"),
    [
        ("before_manager", "interrupt"),
        ("before_manager", "os_error"),
        ("before_thread_start", "interrupt"),
        ("before_thread_start", "os_error"),
        ("during_registration", "interrupt"),
    ],
)
def test_partial_startup_reaps_only_owned_children_and_can_recover(
    tmp_path: Path, kind: str, stage: str
) -> None:
    probe = Path(__file__).with_name("worker_startup_probe.py")
    result = subprocess.run(  # noqa: S603 -- Trusted checked-in probe and isolated generated paths, no shell.
        [sys.executable, "-B", str(probe), str(tmp_path), kind, stage],
        capture_output=True,
        text=True,
        check=False,
        timeout=70,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["initialized_child_alive_at_fault"] == [True]
    assert evidence["owned_alive_after_runner"] == [False]
    assert all(code is not None for code in evidence["owned_exitcodes_after_runner"])
    if stage == "during_registration":
        assert evidence["owned_exitcodes_after_runner"] == [0]
    assert evidence["original_error_preserved"] is True
    assert evidence["raised"] == (
        "KeyboardInterrupt" if kind == "interrupt" else "WorkerExecutionError"
    )
    assert evidence["report_exists"] is False
    assert evidence["unrelated_still_alive"] is True
    assert evidence["recovery_succeeded"] == 1
    assert evidence["recovery_parity"] is True


@pytest.mark.parametrize("operation_fails", [False, True])
def test_deferred_interrupt_restores_handler_and_preserves_operation_error(
    operation_fails: bool,
) -> None:
    previous = signal.getsignal(signal.SIGINT)
    expected = OSError if operation_fails else KeyboardInterrupt

    def submit() -> None:
        with defer_startup_interrupt():
            signal.raise_signal(signal.SIGINT)
            # SIGINT must not interrupt process registration/bookkeeping here.
            assert signal.getsignal(signal.SIGINT) != previous
            if operation_fails:
                raise OSError("original submission error")

    with pytest.raises(expected):
        submit()
    assert signal.getsignal(signal.SIGINT) == previous

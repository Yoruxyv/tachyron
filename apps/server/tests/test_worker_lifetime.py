# Copyright (c) 2026 Hans Valerie
"""Real spawn regression for controller loss with an active worker and descendant."""

import json
import multiprocessing
import os
from collections.abc import Callable
from functools import partial
from multiprocessing.process import BaseProcess
from multiprocessing.synchronize import Event
from pathlib import Path
from time import monotonic, sleep

import psutil
import pytest
from PIL import Image

from tachyron.execution import inspect_execution, run_local
from tachyron.processing import (
    ImageOutcome,
    ManifestEntry,
    ResourceLimits,
    TransformConfig,
    process_image,
)
from tachyron.workers import operational
from tachyron.workers._lifetime import worker_initializer


def _process_with_descendant(
    marker: Path,
    root: Path,
    entry: ManifestEntry,
    output: Path,
    config: TransformConfig,
    limits: ResourceLimits,
) -> ImageOutcome:
    if entry.relative_path == "b.png":
        child = multiprocessing.get_context("spawn").Process(target=sleep, args=(35,))
        try:
            child.start()
            temporary = marker.with_suffix(".tmp")
            temporary.write_text(json.dumps([os.getpid(), child.pid]), encoding="utf-8")
            temporary.replace(marker)
            child.join(30)
        finally:
            _stop_probe(child)
    return process_image(root, entry, output, config, limits)


def _run_controller(root: Path, output: Path, marker: Path) -> None:
    # Replace only the test task. The real runner, pool initializer and receipts stay in use.
    operational.process_image = partial(_process_with_descendant, marker)
    run_local(root, output, TransformConfig(2, 2), 1, telemetry=True)


def _live(process: psutil.Process) -> bool:
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _wait_for_acknowledgement(controller: BaseProcess, marker: Path, ack: Path) -> None:
    deadline = monotonic() + 15
    while controller.is_alive() and monotonic() < deadline:
        if marker.is_file() and ack.is_file():
            return
        sleep(0.02)
    pytest.fail("The controller never acknowledged an image with a live descendant")


def _wait_for_exit(processes: tuple[psutil.Process, ...]) -> None:
    deadline = monotonic() + 8
    while monotonic() < deadline and any(_live(process) for process in processes):
        sleep(0.02)


def _stop_probe(process: BaseProcess) -> None:
    if process.pid is not None:
        if process.is_alive():
            process.kill()
        process.join(8)
        process.close()


@pytest.mark.spawn
def test_forced_controller_exit_stops_owned_worker_tree(tmp_path: Path) -> None:
    root = tmp_path / "input"
    root.mkdir()
    for name in ("a.png", "b.png", "c.png"):
        with Image.new("RGB", (3, 2), "red") as image:
            image.save(root / name)

    output = tmp_path / "interrupted"
    marker = tmp_path / "descendant.json"
    context = multiprocessing.get_context("spawn")
    controller = context.Process(target=_run_controller, args=(root, output, marker))
    unrelated = context.Process(target=sleep, args=(40,))
    owned: dict[int, psutil.Process] = {}

    try:
        unrelated.start()
        controller.start()
        assert controller.pid is not None
        controller_identity = psutil.Process(controller.pid)
        _wait_for_acknowledgement(controller, marker, output / "records/00000.json")
        owned.update(
            (child.pid, child) for child in controller_identity.children(recursive=True)
        )
        worker_pid, descendant_pid = json.loads(marker.read_text(encoding="utf-8"))
        workers = owned[worker_pid], owned[descendant_pid]
        assert all(_live(process) for process in workers)
        assert workers[1].parent() == workers[0]
        assert not (output / "execution.json").exists()

        controller.kill()
        controller.join(8)
        assert not controller.is_alive()
        _wait_for_exit(workers)
        assert not any(_live(process) for process in workers)
        assert unrelated.is_alive()

        result = inspect_execution(output)
        assert result.state == "UNCONFIRMED"
        assert result.processing_seconds is None
        assert result.counts == {
            "succeeded": 1,
            "failed": 0,
            "unknown": 2,
            "not_started": 0,
        }
        outcome = result.items[0].outcome
        assert outcome is not None
        assert outcome.output_name is not None
        with Image.open(output / outcome.output_name) as image:
            assert image.size == (2, 2)
            image.verify()
    finally:
        _stop_probe(controller)
        # Only identities captured from this test's live controller can be cleaned up here.
        for child in owned.values():
            if _live(child):
                child.kill()
        _wait_for_exit(tuple(owned.values()))
        _stop_probe(unrelated)

    recovered = run_local(root, tmp_path / "recovered", TransformConfig(2, 2), 1)
    assert recovered.state == "COMPLETED"
    assert recovered.counts["succeeded"] == 3


def _delay_enrollment(
    ready: Event, release: Event, initialize: Callable[[], None]
) -> None:
    ready.set()
    if release.wait(20):
        initialize()


def _start_unenrolled_worker(ready: Event, release: Event) -> None:
    context = multiprocessing.get_context("spawn")
    with worker_initializer(partial(sleep, 25)) as initialize:
        worker = context.Process(
            target=_delay_enrollment, args=(ready, release, initialize)
        )
        try:
            worker.start()
            worker.join(45)
        finally:
            _stop_probe(worker)


@pytest.mark.spawn
def test_worker_exits_if_controller_dies_before_enrollment() -> None:
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    controller = context.Process(target=_start_unenrolled_worker, args=(ready, release))
    owned: tuple[psutil.Process, ...] = ()
    try:
        controller.start()
        assert controller.pid is not None
        identity = psutil.Process(controller.pid)
        assert ready.wait(15)
        owned = tuple(identity.children())
        assert len(owned) == 1
        assert _live(owned[0])

        controller.kill()
        controller.join(8)
        assert not controller.is_alive()
        release.set()
        _wait_for_exit(owned)
        assert not _live(owned[0])
    finally:
        _stop_probe(controller)
        for worker in owned:
            if _live(worker):
                worker.kill()
        _wait_for_exit(owned)

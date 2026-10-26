# Copyright (c) 2026 Hans Valerie
"""Bounded real-process startup fault injection, executed in an isolated Python.

The two private hooks locate CPython's audited gap deterministically. They are
test-only; a watchdog inside each probe worker and explicit finally cleanup keep
an unfixed implementation from leaving idle children behind or hanging CI.
"""

import json
import multiprocessing
import os
import signal
import sys
import threading
from concurrent.futures import ProcessPoolExecutor
from multiprocessing.process import BaseProcess
from multiprocessing.synchronize import Event
from pathlib import Path
from typing import cast
from unittest.mock import patch

from PIL import Image

from tachyron import workers
from tachyron.processing import TransformConfig, build_manifest, run_sequential
from tachyron.workers import WorkerExecutionError


def initialize_probe(ready: Event) -> None:
    """Signal genuine child readiness and bound its life if the parent regresses."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    def safety_exit() -> None:
        threading.Event().wait(25)
        os._exit(91)

    threading.Thread(target=safety_exit, daemon=True).start()
    ready.set()


def unrelated_child(ready: Event, release: Event) -> None:
    """Remain alive independently until the probe releases this unrelated child."""
    ready.set()
    release.wait(25)


def registered_children(pool: ProcessPoolExecutor) -> tuple[BaseProcess, ...]:
    """Capture exact ownership before ordinary shutdown can discard its registry."""
    # The audited CPython registry is used only to inject/check this regression.
    processes = cast("dict[int, BaseProcess]", getattr(pool, "_processes", None))
    return tuple(processes.values())


def stop_probe_child(process: BaseProcess) -> None:
    """Bound fallback cleanup even when the application fails to reap its child."""
    if process.is_alive():
        process.kill()
    process.join(5)
    if process.is_alive():
        raise AssertionError("Probe fallback could not stop its child")
    process.close()


def main() -> None:
    """Exercise one fault with safe fallback cleanup and print portable evidence."""
    root = Path(sys.argv[1])
    kind, stage = sys.argv[2:4]
    context = multiprocessing.get_context("spawn")
    ready, unrelated_ready, release = context.Event(), context.Event(), context.Event()
    unrelated = context.Process(target=unrelated_child, args=(unrelated_ready, release))
    owned: list[BaseProcess] = []
    live_at_failure: list[bool] = []
    holder: list[ProcessPoolExecutor] = []
    original_start = threading.Thread.start
    original_process_start = BaseProcess.start
    input_dir = root / "input"
    input_dir.mkdir()
    with Image.new("RGB", (3, 2), "red") as image:
        image.save(input_dir / "a.png")
    manifest = build_manifest(input_dir)

    def factory(**options: object) -> ProcessPoolExecutor:
        # Only the initializer is replaced; task execution and spawn are real.
        pool = ProcessPoolExecutor(
            max_workers=1,
            mp_context=context,
            initializer=initialize_probe,
            initargs=(ready,),
        )
        holder.append(pool)
        return pool

    def fail_startup(pool: ProcessPoolExecutor) -> None:
        owned.extend(registered_children(pool))
        if not ready.wait(10):
            raise AssertionError("Probe child never initialized")
        live_at_failure.extend(process.is_alive() for process in owned)
        if kind == "interrupt":
            raise KeyboardInterrupt
        raise OSError("startup-regression-evidence")

    def fail_manager_start(thread: threading.Thread) -> None:
        if (
            type(thread).__module__ == "concurrent.futures.process"
            and type(thread).__name__ == "_ExecutorManagerThread"
        ):
            fail_startup(holder[0])
        original_start(thread)

    def interrupt_registration(process: BaseProcess) -> None:
        original_process_start(process)
        owned.append(process)
        if not ready.wait(10):
            raise AssertionError("Probe child never initialized")
        live_at_failure.append(process.is_alive())
        signal.raise_signal(signal.SIGINT)

    unrelated.start()
    try:
        if not unrelated_ready.wait(10):
            raise AssertionError("Unrelated child never initialized")
        raised = "none"
        original_error = False
        if stage == "during_registration":
            target = patch.object(BaseProcess, "start", interrupt_registration)
        elif stage == "before_manager":
            target = patch.object(
                ProcessPoolExecutor, "_start_executor_manager_thread", fail_startup
            )
        else:
            target = patch.object(threading.Thread, "start", fail_manager_start)
        with patch.object(workers, "ProcessPoolExecutor", factory), target:
            try:
                workers.run_parallel(
                    manifest, root / "failed-run", TransformConfig(2, 2), 1
                )
            except KeyboardInterrupt:
                raised = "KeyboardInterrupt"
                original_error = True
            except WorkerExecutionError as error:
                raised = "WorkerExecutionError"
                original_error = (
                    isinstance(error.__cause__, OSError)
                    and str(error.__cause__) == "startup-regression-evidence"
                )
        evidence: dict[str, object] = {
            "fault": kind,
            "stage": stage,
            "raised": raised,
            "initialized_child_alive_at_fault": live_at_failure,
            "owned_alive_after_runner": [process.is_alive() for process in owned],
            "owned_exitcodes_after_runner": [process.exitcode for process in owned],
            "original_error_preserved": original_error,
            "report_exists": (root / "failed-run/run.json").exists(),
            "unrelated_still_alive": unrelated.is_alive(),
        }
    finally:
        for process in owned:
            stop_probe_child(process)
        release.set()
        unrelated.join(5)
        stop_probe_child(unrelated)
    sequential = run_sequential(manifest, root / "sequential", TransformConfig(2, 2))
    recovered = workers.run_parallel(
        manifest, root / "recovered-run", TransformConfig(2, 2), 1
    )
    evidence["recovery_succeeded"] = recovered.succeeded
    evidence["recovery_parity"] = recovered.outcomes == sequential.outcomes and all(
        (recovered.output_dir / outcome.output_name).read_bytes()
        == (sequential.output_dir / outcome.output_name).read_bytes()
        for outcome in recovered.outcomes
        if outcome.output_name is not None
    )
    sys.stdout.write(json.dumps(evidence, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()

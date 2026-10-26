# Copyright (c) 2026 Hans Valerie
"""Isolated CPython 3.11/3.13 recovery for an executor without a started manager.

Public executor shutdown drops its process registry without reaping children if
the first submission fails before the manager starts. Only the two private state
fields below are used; process termination and reaping use public APIs.
"""

import signal
import threading
from collections.abc import Generator
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from multiprocessing.process import BaseProcess
from types import FrameType
from typing import Protocol, cast


class _ExecutorState(Protocol):
    _processes: dict[int, BaseProcess] | None
    _executor_manager_thread: threading.Thread | None


def check_startup_support(pool: ProcessPoolExecutor) -> None:
    """Reject an unknown executor layout before submitting or starting children."""
    if (
        not isinstance(getattr(pool, "_processes", None), dict)
        or getattr(pool, "_executor_manager_thread", object()) is not None
    ):
        raise RuntimeError("Executor startup recovery is unsupported on this runtime")


def reap_unmanaged_children(pool: ProcessPoolExecutor) -> None:
    """Stop only registered children when startup never produced a running manager.

    Call with SIGINT suppressed and before public shutdown discards the registry.
    A started manager retains full responsibility for graceful task shutdown.
    """
    # These fields have the same ownership/lifecycle in CPython 3.11 and 3.13.
    state = cast("_ExecutorState", pool)
    manager = state._executor_manager_thread
    if manager is not None and manager.ident is not None:
        return
    for process in tuple((state._processes or {}).values()):
        if process.is_alive():
            process.kill()
        process.join()
    # Public shutdown otherwise tries to join a thread which never started.
    state._executor_manager_thread = None


@contextmanager
def defer_startup_interrupt() -> Generator[None]:
    """Deliver Ctrl+C after child registration, preserving an operation's own error.

    This protects short constructor/submission bookkeeping, not task execution.
    Background callers and ignored/default OS handlers retain their behavior.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGINT)
    if not callable(previous):
        yield
        return
    pending: list[tuple[int, FrameType | None]] = []

    def defer(signum: int, frame: FrameType | None) -> None:
        pending.append((signum, frame))

    signal.signal(signal.SIGINT, defer)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)
    if pending:
        previous(*pending[-1])

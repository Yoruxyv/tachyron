# Copyright (c) 2026 Hans Valerie
"""Tie spawn workers to their controller without searching the process table."""

import multiprocessing
import os
import signal
import sys
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from functools import partial
from multiprocessing.connection import wait

if sys.platform == "win32":
    from tachyron.workers._windows_job import join_worker_job, worker_job

    def _initialize_windows_worker(name: str, initialize: Callable[[], None]) -> None:
        join_worker_job(name)
        initialize()
else:

    def _watch_parent(sentinel: int) -> None:
        wait([sentinel])
        # The live session leader keeps this process-group identity from being reused.
        os.killpg(os.getpgrp(), signal.SIGKILL)

    def _initialize_posix_worker(initialize: Callable[[], None]) -> None:
        parent = multiprocessing.parent_process()
        if parent is None:
            raise RuntimeError("Spawned worker has no controller sentinel")
        os.setsid()
        threading.Thread(
            target=_watch_parent, args=(parent.sentinel,), daemon=True
        ).start()
        initialize()


@contextmanager
def worker_initializer(
    initialize: Callable[[], None],
) -> Generator[Callable[[], None]]:
    """Supply a spawn initializer. Keep this context open through pool shutdown."""
    if sys.platform == "win32":
        with worker_job() as name:
            yield partial(_initialize_windows_worker, name, initialize)
    else:
        yield partial(_initialize_posix_worker, initialize)

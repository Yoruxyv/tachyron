# Copyright (c) 2026 Hans Valerie
"""Cooperative OS-account benchmark exclusion without stale-lock deletion."""

import errno
import hashlib
import os
import stat
import sys
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import psutil

from tachyron.benchmarking.models import BenchmarkError
from tachyron.processing.dataset import reject_link

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


@contextmanager
def experiment_lock() -> Generator[None]:
    """Admit one cooperating benchmark per account/temp directory, across processes.

    The OS releases the lock after crashes. The file is never unlinked: replacing
    an inode while another caller holds it would admit two benchmark owners.
    """
    account = hashlib.sha256(psutil.Process().username().encode()).hexdigest()[:24]
    path = Path(tempfile.gettempdir()) / f"tachyron-benchmark-{account}.lock"
    flags = (
        os.O_CREAT
        | os.O_RDWR
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "r+b") as stream:
        info = os.fstat(stream.fileno())
        reject_link(info)
        if not stat.S_ISREG(info.st_mode):
            raise BenchmarkError("Benchmark lock must be a regular file")
        try:
            if sys.platform == "win32":
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                if info.st_uid != os.getuid():
                    raise BenchmarkError("Benchmark lock belongs to another account")
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise BenchmarkError(
                    "Another benchmark owns this account's experiment lock"
                ) from error
            raise
        try:
            yield
        finally:
            if sys.platform == "win32":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

# Copyright (c) 2026 Hans Valerie
"""Non-inherited Windows job handles for one worker pool at a time."""

import ctypes
import sys
from collections.abc import Generator
from contextlib import contextmanager
from ctypes import wintypes
from typing import cast
from uuid import uuid4

if sys.platform == "win32":
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JOB_OBJECT_ASSIGN_PROCESS = 0x0001
    _ERROR_ALREADY_EXISTS = 183

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", ctypes.c_uint64 * 6),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _create_job = _kernel.CreateJobObjectW
    _create_job.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _create_job.restype = wintypes.HANDLE
    _set_job = _kernel.SetInformationJobObject
    _set_job.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _set_job.restype = wintypes.BOOL
    _open_job = _kernel.OpenJobObjectW
    _open_job.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    _open_job.restype = wintypes.HANDLE
    _assign = _kernel.AssignProcessToJobObject
    _assign.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _assign.restype = wintypes.BOOL
    _current_process = _kernel.GetCurrentProcess
    _current_process.argtypes = []
    _current_process.restype = wintypes.HANDLE
    _close_handle = _kernel.CloseHandle
    _close_handle.argtypes = [wintypes.HANDLE]
    _close_handle.restype = wintypes.BOOL

    def _close(handle: int) -> None:
        if not _close_handle(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    @contextmanager
    def worker_job() -> Generator[str]:
        """Own the only long-lived handle to a private kill-on-close job."""
        name = f"Local\\tachyron-worker-{uuid4().hex}"
        ctypes.set_last_error(0)
        handle = cast("int | None", _create_job(None, name))
        error = ctypes.get_last_error()
        if handle is None:
            raise ctypes.WinError(error)
        try:
            if error == _ERROR_ALREADY_EXISTS:
                raise ctypes.WinError(error)
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = (
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            if not _set_job(
                handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            yield name
        finally:
            _close(handle)

    def join_worker_job(name: str) -> None:
        """Enroll this worker and release its temporary handle before running work."""
        handle = cast("int | None", _open_job(_JOB_OBJECT_ASSIGN_PROCESS, False, name))
        if handle is None:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not _assign(handle, _current_process()):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _close(handle)

"""Windows Job Object helpers — real process-tree isolation on Windows.

POSIX already gets a robust process-tree kill via ``start_new_session=True`` +
``os.killpg`` (see deps.py). Windows only had ``taskkill /F /T``, which walks
PID-reported parent/child links and can miss a re-parented or detached
process. A Job Object groups every process ever assigned to it (regardless of
reparenting) and, when terminated, kills them ALL atomically — the same
guarantee POSIX process groups already provide.

ctypes-only (no pywin32 dependency) so this keeps working even if the ``mcp``
SDK's transitive pywin32 install ever changes. Every function degrades to a
no-op/False/None on non-Windows or on any Win32 API failure — Job Objects are
a best-effort hardening layer, never a hard requirement for a command to run.
"""
from __future__ import annotations

import sys
from typing import Optional

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # Explicit restype/argtypes are REQUIRED here: ctypes defaults an
    # undeclared function to a 32-bit c_int return, which would silently
    # truncate a 64-bit HANDLE on 64-bit Windows and corrupt every handle
    # this module hands back.
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_void_p),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _JobObjectExtendedLimitInformation = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _PROCESS_ALL_ACCESS = 0x1F0FFF


def create_job_object() -> Optional[int]:
    """Create a Job Object with KILL_ON_JOB_CLOSE. Returns the handle, or
    ``None`` on non-Windows or on any failure (caller falls back to the
    existing taskkill-based tree-kill)."""
    if not _IS_WINDOWS:
        return None
    try:
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = _kernel32.SetInformationJobObject(
            handle, _JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            _kernel32.CloseHandle(handle)
            return None
        return handle
    except Exception:  # noqa: BLE001 - a hardening layer must never be fatal
        return None


def assign_process(job_handle: Optional[int], pid: int) -> bool:
    """Add process ``pid`` to the job so it (and anything it spawns) is
    killed together when the job is terminated."""
    if not _IS_WINDOWS or not job_handle:
        return False
    try:
        proc_handle = _kernel32.OpenProcess(_PROCESS_ALL_ACCESS, False, pid)
        if not proc_handle:
            return False
        try:
            return bool(_kernel32.AssignProcessToJobObject(job_handle, proc_handle))
        finally:
            _kernel32.CloseHandle(proc_handle)
    except Exception:  # noqa: BLE001
        return False


def terminate_job(job_handle: Optional[int]) -> bool:
    """Kill every process ever assigned to the job, atomically, then close
    the handle. This is what makes Job Objects stronger than ``taskkill /T``
    — it catches processes that got reparented/detached, which taskkill's
    PID-tree walk can miss."""
    if not _IS_WINDOWS or not job_handle:
        return False
    try:
        ok = _kernel32.TerminateJobObject(job_handle, 1)
        _kernel32.CloseHandle(job_handle)
        return bool(ok)
    except Exception:  # noqa: BLE001
        return False

"""Win32 Job Object sandbox for bash command execution.

Uses ctypes to call Win32 kernel APIs directly — no third-party dependencies.
This module is importable on all platforms but only functional on Windows.
All public functions return success/failure and never raise exceptions.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import OsSandboxConfig

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("anteroom.security")

IS_WINDOWS = sys.platform == "win32"

# Win32 constants
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
_JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
_JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000

_JOBOBJECTEXTENDEDLIMITINFORMATION = 9
_PROCESS_ALL_ACCESS = 0x001FFFFF
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


if IS_WINDOWS:
    import ctypes.wintypes

    class IO_COUNTERS(ctypes.Structure):  # noqa: N801
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", ctypes.wintypes.LARGE_INTEGER),
            ("LimitFlags", ctypes.wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.wintypes.DWORD),
            ("SchedulingClass", ctypes.wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]


def create_job_object(config: OsSandboxConfig) -> int | None:
    """Create a Win32 Job Object with resource limits from config.

    Returns the job handle on success, None on failure.
    """
    if not IS_WINDOWS:
        return None

    try:
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            logger.warning("CreateJobObjectW failed: %s", ctypes.get_last_error())  # type: ignore[attr-defined]
            return None

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        # Memory limit
        if config.max_memory_mb > 0:
            flags |= _JOB_OBJECT_LIMIT_PROCESS_MEMORY
            info.ProcessMemoryLimit = config.max_memory_mb * 1024 * 1024

        # Process count limit
        if config.max_processes > 0:
            flags |= _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = config.max_processes

        # CPU time limit (in 100-nanosecond intervals)
        if config.cpu_time_limit is not None and config.cpu_time_limit > 0:
            flags |= _JOB_OBJECT_LIMIT_PROCESS_TIME
            info.BasicLimitInformation.PerProcessUserTimeLimit = config.cpu_time_limit * 10_000_000

        # Prevent child processes from escaping the Job
        # Clear breakaway flags to ensure children inherit the Job
        flags &= ~(_JOB_OBJECT_LIMIT_BREAKAWAY_OK | _JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK)

        info.BasicLimitInformation.LimitFlags = flags

        success = _kernel32.SetInformationJobObject(
            handle,
            _JOBOBJECTEXTENDEDLIMITINFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not success:
            logger.warning("SetInformationJobObject failed: %s", ctypes.get_last_error())  # type: ignore[attr-defined]
            _kernel32.CloseHandle(handle)
            return None

        security_logger.debug(
            "Job Object created: memory=%dMB, processes=%d, cpu=%s",
            config.max_memory_mb,
            config.max_processes,
            f"{config.cpu_time_limit}s" if config.cpu_time_limit else "unlimited",
        )
        return int(handle)

    except OSError:
        logger.warning("Job Object creation failed", exc_info=True)
        return None


def assign_process(job_handle: int, pid: int) -> bool:
    """Assign a process to a Job Object by PID.

    Returns True on success, False on failure.
    """
    if not IS_WINDOWS:
        return False

    try:
        proc_handle = _kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not proc_handle:
            logger.warning("OpenProcess(%d) failed: %s", pid, ctypes.get_last_error())  # type: ignore[attr-defined]
            return False

        try:
            success = _kernel32.AssignProcessToJobObject(job_handle, proc_handle)
            if not success:
                logger.warning("AssignProcessToJobObject failed: %s", ctypes.get_last_error())  # type: ignore[attr-defined]
                return False
            return True
        finally:
            _kernel32.CloseHandle(proc_handle)

    except OSError:
        logger.warning("Process assignment to Job Object failed", exc_info=True)
        return False


def terminate_job(job_handle: int) -> None:
    """Terminate all processes in a Job Object."""
    if not IS_WINDOWS:
        return

    try:
        _kernel32.TerminateJobObject(job_handle, 1)
    except OSError:
        logger.warning("TerminateJobObject failed", exc_info=True)


def close_job(job_handle: int) -> None:
    """Close a Job Object handle. With KILL_ON_JOB_CLOSE, this kills remaining processes."""
    if not IS_WINDOWS:
        return

    try:
        _kernel32.CloseHandle(job_handle)
    except OSError:
        logger.warning("CloseHandle (Job Object) failed", exc_info=True)


def setup_job_for_process(config: OsSandboxConfig, pid: int) -> int | None:
    """Create a Job Object and assign a process to it. Convenience wrapper.

    Returns the job handle on success, None on failure. On failure, the
    caller should proceed without OS-level sandboxing (graceful degradation).
    """
    job_handle = create_job_object(config)
    if job_handle is None:
        return None

    if not assign_process(job_handle, pid):
        close_job(job_handle)
        return None

    security_logger.info("Process %d assigned to Job Object sandbox", pid)
    return job_handle

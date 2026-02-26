"""Tests for Win32 Job Object sandbox.

All Win32 API calls are mocked — tests run on all platforms.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from anteroom.config import OsSandboxConfig
from anteroom.tools.sandbox_win32 import (
    _JOB_OBJECT_LIMIT_ACTIVE_PROCESS,
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    _JOB_OBJECT_LIMIT_PROCESS_MEMORY,
    _JOB_OBJECT_LIMIT_PROCESS_TIME,
    IS_WINDOWS,
    assign_process,
    close_job,
    create_job_object,
    setup_job_for_process,
    terminate_job,
)

# --- OsSandboxConfig validation ---


class TestOsSandboxConfig:
    def test_defaults(self):
        cfg = OsSandboxConfig()
        assert cfg.enabled is None
        assert cfg.max_memory_mb == 512
        assert cfg.max_processes == 10
        assert cfg.cpu_time_limit is None

    def test_memory_clamped_to_min(self):
        cfg = OsSandboxConfig(max_memory_mb=10)
        assert cfg.max_memory_mb == 64

    def test_memory_valid_unchanged(self):
        cfg = OsSandboxConfig(max_memory_mb=1024)
        assert cfg.max_memory_mb == 1024

    def test_processes_clamped_to_min(self):
        cfg = OsSandboxConfig(max_processes=0)
        assert cfg.max_processes == 1

    def test_processes_clamped_to_max(self):
        cfg = OsSandboxConfig(max_processes=5000)
        assert cfg.max_processes == 1000

    def test_cpu_time_clamped_to_min(self):
        cfg = OsSandboxConfig(cpu_time_limit=0)
        assert cfg.cpu_time_limit == 1

    def test_cpu_time_none_unchanged(self):
        cfg = OsSandboxConfig(cpu_time_limit=None)
        assert cfg.cpu_time_limit is None

    def test_is_enabled_none_auto_detects(self):
        cfg = OsSandboxConfig(enabled=None)
        assert cfg.is_enabled == (sys.platform == "win32")

    def test_is_enabled_true(self):
        cfg = OsSandboxConfig(enabled=True)
        assert cfg.is_enabled is True

    def test_is_enabled_false(self):
        cfg = OsSandboxConfig(enabled=False)
        assert cfg.is_enabled is False


# --- Non-Windows early returns ---


@pytest.mark.skipif(IS_WINDOWS, reason="Tests non-Windows fallback paths")
class TestNonWindowsFallback:
    def test_create_job_object_returns_none(self):
        cfg = OsSandboxConfig()
        assert create_job_object(cfg) is None

    def test_assign_process_returns_false(self):
        assert assign_process(12345, 999) is False

    def test_terminate_job_noop(self):
        terminate_job(12345)  # should not raise

    def test_close_job_noop(self):
        close_job(12345)  # should not raise

    def test_setup_job_for_process_returns_none(self):
        cfg = OsSandboxConfig()
        assert setup_job_for_process(cfg, 999) is None


# --- Mocked Win32 API tests ---


@pytest.fixture
def mock_kernel32():
    """Mock the kernel32 module for cross-platform testing."""
    kernel = MagicMock()
    kernel.CreateJobObjectW.return_value = 42  # fake handle
    kernel.SetInformationJobObject.return_value = True
    kernel.OpenProcess.return_value = 100  # fake proc handle
    kernel.AssignProcessToJobObject.return_value = True
    kernel.CloseHandle.return_value = True
    kernel.TerminateJobObject.return_value = True
    return kernel


@pytest.mark.skipif(not IS_WINDOWS, reason="Requires Windows ctypes structures")
class TestCreateJobObjectWindows:
    def test_creates_job_with_memory_limit(self, mock_kernel32):
        cfg = OsSandboxConfig(max_memory_mb=256)
        with patch("anteroom.tools.sandbox_win32._kernel32", mock_kernel32):
            handle = create_job_object(cfg)
        assert handle == 42
        mock_kernel32.CreateJobObjectW.assert_called_once()
        mock_kernel32.SetInformationJobObject.assert_called_once()

    def test_creates_job_with_process_limit(self, mock_kernel32):
        cfg = OsSandboxConfig(max_processes=5)
        with patch("anteroom.tools.sandbox_win32._kernel32", mock_kernel32):
            handle = create_job_object(cfg)
        assert handle == 42

    def test_creates_job_with_cpu_time(self, mock_kernel32):
        cfg = OsSandboxConfig(cpu_time_limit=30)
        with patch("anteroom.tools.sandbox_win32._kernel32", mock_kernel32):
            handle = create_job_object(cfg)
        assert handle == 42

    def test_returns_none_on_create_failure(self, mock_kernel32):
        mock_kernel32.CreateJobObjectW.return_value = 0
        cfg = OsSandboxConfig()
        with patch("anteroom.tools.sandbox_win32._kernel32", mock_kernel32):
            assert create_job_object(cfg) is None

    def test_returns_none_on_set_info_failure(self, mock_kernel32):
        mock_kernel32.SetInformationJobObject.return_value = False
        cfg = OsSandboxConfig()
        with patch("anteroom.tools.sandbox_win32._kernel32", mock_kernel32):
            assert create_job_object(cfg) is None
        mock_kernel32.CloseHandle.assert_called_once_with(42)


@pytest.mark.skipif(not IS_WINDOWS, reason="Requires Windows ctypes structures")
class TestAssignProcessWindows:
    def test_assigns_process(self, mock_kernel32):
        with patch("anteroom.tools.sandbox_win32._kernel32", mock_kernel32):
            assert assign_process(42, 1234) is True
        mock_kernel32.OpenProcess.assert_called_once()
        mock_kernel32.AssignProcessToJobObject.assert_called_once_with(42, 100)
        mock_kernel32.CloseHandle.assert_called_once_with(100)

    def test_returns_false_on_open_failure(self, mock_kernel32):
        mock_kernel32.OpenProcess.return_value = 0
        with patch("anteroom.tools.sandbox_win32._kernel32", mock_kernel32):
            assert assign_process(42, 1234) is False

    def test_returns_false_on_assign_failure(self, mock_kernel32):
        mock_kernel32.AssignProcessToJobObject.return_value = False
        with patch("anteroom.tools.sandbox_win32._kernel32", mock_kernel32):
            assert assign_process(42, 1234) is False
        # Process handle still closed
        mock_kernel32.CloseHandle.assert_called_once_with(100)


@pytest.mark.skipif(not IS_WINDOWS, reason="Requires Windows ctypes structures")
class TestTerminateAndCloseWindows:
    def test_terminate_job(self, mock_kernel32):
        with patch("anteroom.tools.sandbox_win32._kernel32", mock_kernel32):
            terminate_job(42)
        mock_kernel32.TerminateJobObject.assert_called_once_with(42, 1)

    def test_close_job(self, mock_kernel32):
        with patch("anteroom.tools.sandbox_win32._kernel32", mock_kernel32):
            close_job(42)
        mock_kernel32.CloseHandle.assert_called_once_with(42)


@pytest.mark.skipif(not IS_WINDOWS, reason="Requires Windows ctypes structures")
class TestSetupJobForProcess:
    def test_success(self, mock_kernel32):
        cfg = OsSandboxConfig(max_memory_mb=256, max_processes=5)
        with patch("anteroom.tools.sandbox_win32._kernel32", mock_kernel32):
            handle = setup_job_for_process(cfg, 1234)
        assert handle == 42

    def test_returns_none_on_create_failure(self, mock_kernel32):
        mock_kernel32.CreateJobObjectW.return_value = 0
        cfg = OsSandboxConfig()
        with patch("anteroom.tools.sandbox_win32._kernel32", mock_kernel32):
            assert setup_job_for_process(cfg, 1234) is None

    def test_returns_none_on_assign_failure(self, mock_kernel32):
        mock_kernel32.OpenProcess.return_value = 0
        cfg = OsSandboxConfig()
        with patch("anteroom.tools.sandbox_win32._kernel32", mock_kernel32):
            assert setup_job_for_process(cfg, 1234) is None
        # Job handle cleaned up
        mock_kernel32.CloseHandle.assert_called_once_with(42)


# --- Limit flag composition tests (platform-independent via struct inspection) ---


class TestLimitFlagComposition:
    """Verify the correct Win32 flags are set based on config."""

    def test_kill_on_close_always_set(self):
        # Kill-on-close is mandatory for safety
        assert _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE == 0x00002000

    def test_memory_flag(self):
        assert _JOB_OBJECT_LIMIT_PROCESS_MEMORY == 0x00000100

    def test_active_process_flag(self):
        assert _JOB_OBJECT_LIMIT_ACTIVE_PROCESS == 0x00000008

    def test_process_time_flag(self):
        assert _JOB_OBJECT_LIMIT_PROCESS_TIME == 0x00000002

    def test_combined_flags(self):
        flags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | _JOB_OBJECT_LIMIT_PROCESS_TIME
        )
        assert flags == 0x0000210A

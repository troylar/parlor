"""Tests for the shared COM lifecycle manager."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from anteroom.tools.office_com import ComAppManager


class TestComAppManager:
    def test_get_app_creates_and_caches(self):
        manager = ComAppManager()
        mock_app = MagicMock()
        mock_dispatch = MagicMock(return_value=mock_app)

        with patch("anteroom.tools.office_com._win32com_client") as mock_client:
            mock_client.Dispatch = mock_dispatch
            app1 = manager.get_app("Word.Application")
            app2 = manager.get_app("Word.Application")

        assert app1 is app2
        mock_dispatch.assert_called_once_with("Word.Application")
        assert mock_app.Visible is False
        assert mock_app.DisplayAlerts is False

    def test_get_app_powerpoint_visible_minimized(self):
        """PowerPoint refuses Visible=False; must be visible + minimized."""
        manager = ComAppManager()
        mock_app = MagicMock()

        with patch("anteroom.tools.office_com._win32com_client") as mock_client:
            mock_client.Dispatch = MagicMock(return_value=mock_app)
            manager.get_app("PowerPoint.Application")

        assert mock_app.Visible is True
        assert mock_app.WindowState == 2  # ppWindowMinimized
        assert mock_app.DisplayAlerts is False

    def test_get_app_reconnects_on_stale_connection(self):
        """If the cached COM app is disconnected, get_app evicts and recreates it."""
        manager = ComAppManager()
        stale_app = MagicMock()
        # Simulate RPC disconnect: accessing .Name raises
        type(stale_app).Name = property(lambda self: (_ for _ in ()).throw(Exception("RPC server is unavailable")))
        fresh_app = MagicMock()

        with patch("anteroom.tools.office_com._win32com_client") as mock_client:
            mock_client.Dispatch = MagicMock(side_effect=[stale_app, fresh_app])
            # First call creates and caches stale_app
            app1 = manager.get_app("Word.Application")
            assert app1 is stale_app

            # Second call detects stale connection and reconnects
            app2 = manager.get_app("Word.Application")
            assert app2 is fresh_app
            assert mock_client.Dispatch.call_count == 2

    def test_get_app_different_prog_ids(self):
        manager = ComAppManager()
        mock_word = MagicMock()
        mock_excel = MagicMock()

        with patch("anteroom.tools.office_com._win32com_client") as mock_client:
            mock_client.Dispatch = MagicMock(side_effect=[mock_word, mock_excel])
            word = manager.get_app("Word.Application")
            excel = manager.get_app("Excel.Application")

        assert word is not excel

    def test_quit_all_calls_quit_on_each_without_executor(self):
        """quit_all without an executor (no run_com called) quits apps directly."""
        manager = ComAppManager()
        mock_word = MagicMock()
        mock_excel = MagicMock()
        apps = [mock_word, mock_excel]

        with patch("anteroom.tools.office_com._win32com_client") as mock_client:
            mock_client.Dispatch = MagicMock(side_effect=apps)
            manager.get_app("Word.Application")
            manager.get_app("Excel.Application")

        manager.quit_all()
        mock_word.Quit.assert_called_once()
        mock_excel.Quit.assert_called_once()
        assert len(manager._apps) == 0

    def test_quit_all_handles_exceptions(self):
        manager = ComAppManager()
        mock_app = MagicMock()
        mock_app.Quit.side_effect = Exception("COM error")

        with patch("anteroom.tools.office_com._win32com_client") as mock_client:
            mock_client.Dispatch = MagicMock(return_value=mock_app)
            manager.get_app("Word.Application")

        manager.quit_all()
        assert len(manager._apps) == 0

    @pytest.mark.asyncio
    async def test_run_com_initializes_com_on_worker_thread(self):
        """CoInitialize is called once via the executor initializer, not per-call."""
        manager = ComAppManager()
        mock_pythoncom = MagicMock()
        result_value = {"test": True}

        def work():
            return result_value

        with patch("anteroom.tools.office_com._pythoncom", mock_pythoncom):
            result = await manager.run_com(work)
            # Second call reuses the same thread — no additional CoInitialize
            result2 = await manager.run_com(work)

        assert result is result_value
        assert result2 is result_value
        # CoInitialize called once by the thread initializer
        mock_pythoncom.CoInitialize.assert_called_once()

        # Clean up executor
        if manager._executor:
            manager._executor.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_run_com_propagates_exception(self):
        manager = ComAppManager()
        mock_pythoncom = MagicMock()

        def failing_work():
            raise ValueError("boom")

        with patch("anteroom.tools.office_com._pythoncom", mock_pythoncom):
            with pytest.raises(ValueError, match="boom"):
                await manager.run_com(failing_work)

        # CoInitialize called by initializer; no per-call CoUninitialize
        mock_pythoncom.CoInitialize.assert_called_once()
        mock_pythoncom.CoUninitialize.assert_not_called()

        if manager._executor:
            manager._executor.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_run_com_passes_args_and_kwargs(self):
        manager = ComAppManager()
        mock_pythoncom = MagicMock()
        calls: list[tuple] = []

        def work(a, b, key=None):
            calls.append((a, b, key))
            return "done"

        with patch("anteroom.tools.office_com._pythoncom", mock_pythoncom):
            result = await manager.run_com(work, 1, 2, key="val")

        assert result == "done"
        assert calls == [(1, 2, "val")]

        if manager._executor:
            manager._executor.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_run_com_reuses_same_thread(self):
        """Verify all run_com calls execute on the same thread (persistent worker)."""
        import threading

        manager = ComAppManager()
        mock_pythoncom = MagicMock()
        thread_ids: list[int] = []

        def record_thread():
            thread_ids.append(threading.current_thread().ident)
            return True

        with patch("anteroom.tools.office_com._pythoncom", mock_pythoncom):
            await manager.run_com(record_thread)
            await manager.run_com(record_thread)
            await manager.run_com(record_thread)

        assert len(thread_ids) == 3
        assert thread_ids[0] == thread_ids[1] == thread_ids[2]

        if manager._executor:
            manager._executor.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_quit_all_with_executor_runs_on_worker_thread(self):
        """quit_all with an active executor submits quit to the worker thread."""
        manager = ComAppManager()
        mock_pythoncom = MagicMock()
        mock_app = MagicMock()

        with patch("anteroom.tools.office_com._pythoncom", mock_pythoncom):
            with patch("anteroom.tools.office_com._win32com_client") as mock_client:
                mock_client.Dispatch = MagicMock(return_value=mock_app)

                # Start the executor by running something
                await manager.run_com(manager.get_app, "Word.Application")

            # Now quit_all should submit to the executor
            manager.quit_all()

        mock_app.Quit.assert_called_once()
        assert len(manager._apps) == 0
        assert manager._executor is None
        mock_pythoncom.CoUninitialize.assert_called_once()


class TestGetManager:
    def test_returns_singleton(self):
        from anteroom.tools.office_com import get_manager

        with patch("anteroom.tools.office_com._manager", None):
            m1 = get_manager()
            m2 = get_manager()
            assert m1 is m2


class TestComAvailableFlag:
    def test_not_available_on_non_windows(self):
        with patch.object(sys, "platform", "linux"):
            # Re-evaluating the flag would require reimporting, but we can
            # verify the current state matches the platform
            if sys.platform != "win32":
                from anteroom.tools.office_com import COM_AVAILABLE

                assert COM_AVAILABLE is False or sys.platform == "win32"

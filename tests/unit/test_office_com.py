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
        mock_app.Visible = False
        mock_app.DisplayAlerts = False

    def test_get_app_different_prog_ids(self):
        manager = ComAppManager()
        mock_word = MagicMock()
        mock_excel = MagicMock()

        with patch("anteroom.tools.office_com._win32com_client") as mock_client:
            mock_client.Dispatch = MagicMock(side_effect=[mock_word, mock_excel])
            word = manager.get_app("Word.Application")
            excel = manager.get_app("Excel.Application")

        assert word is not excel

    def test_quit_all_calls_quit_on_each(self):
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
    async def test_run_com_calls_coinitialize(self):
        manager = ComAppManager()
        mock_pythoncom = MagicMock()
        result_value = {"test": True}

        def work():
            return result_value

        with patch("anteroom.tools.office_com._pythoncom", mock_pythoncom):
            result = await manager.run_com(work)

        assert result is result_value
        mock_pythoncom.CoInitialize.assert_called_once()
        mock_pythoncom.CoUninitialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_com_uninitializes_on_exception(self):
        manager = ComAppManager()
        mock_pythoncom = MagicMock()

        def failing_work():
            raise ValueError("boom")

        with patch("anteroom.tools.office_com._pythoncom", mock_pythoncom):
            with pytest.raises(ValueError, match="boom"):
                await manager.run_com(failing_work)

        mock_pythoncom.CoInitialize.assert_called_once()
        mock_pythoncom.CoUninitialize.assert_called_once()

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

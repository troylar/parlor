"""Shared COM lifecycle manager for Office tools on Windows.

Manages Word, Excel, and PowerPoint Application objects via win32com.
Only activates on Windows with pywin32 installed.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

COM_AVAILABLE = False
_win32com_client: Any = None
_pythoncom: Any = None

if sys.platform == "win32":
    try:
        import pythoncom as _pythoncom_mod
        import win32com.client as _win32com_mod

        _win32com_client = _win32com_mod
        _pythoncom = _pythoncom_mod
        COM_AVAILABLE = True
    except ImportError:
        pass

T = TypeVar("T")


class ComAppManager:
    """Manages cached COM Application objects for Office apps.

    COM Application objects (Word, Excel, PowerPoint) are expensive to launch.
    This manager caches them for reuse and handles cleanup on shutdown.

    All COM calls must happen on a thread with CoInitialize called. Use
    ``run_com()`` to execute a callable on a properly initialized thread.
    """

    def __init__(self) -> None:
        self._apps: dict[str, Any] = {}

    def get_app(self, prog_id: str) -> Any:
        """Get or create a cached COM Application object.

        Must be called from a COM-initialized thread (inside ``run_com``).

        Args:
            prog_id: COM ProgID, e.g. "Word.Application", "Excel.Application",
                     "PowerPoint.Application".
        """
        if prog_id not in self._apps:
            app = _win32com_client.Dispatch(prog_id)
            app.Visible = False
            app.DisplayAlerts = False
            self._apps[prog_id] = app
        return self._apps[prog_id]

    def quit_all(self) -> None:
        """Quit all cached COM Application objects.

        Must be called from a COM-initialized thread (inside ``run_com``).
        """
        for prog_id, app in list(self._apps.items()):
            try:
                app.Quit()
            except Exception:
                logger.debug("Failed to quit COM app %s", prog_id)
            del self._apps[prog_id]

    async def run_com(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Run a callable on a thread with COM initialized.

        COM is apartment-threaded — each thread must call CoInitialize before
        using COM objects and CoUninitialize when done. This wraps that lifecycle
        around the provided callable via asyncio.to_thread.
        """

        def _wrapper() -> T:
            _pythoncom.CoInitialize()
            try:
                return fn(*args, **kwargs)
            finally:
                _pythoncom.CoUninitialize()

        return await asyncio.to_thread(_wrapper)


_manager: ComAppManager | None = None


def get_manager() -> ComAppManager:
    """Get the singleton ComAppManager instance."""
    global _manager
    if _manager is None:
        _manager = ComAppManager()
    return _manager

"""Shared COM lifecycle manager for Office tools on Windows.

Manages Word, Excel, and PowerPoint Application objects via win32com.
Only activates on Windows with pywin32 installed.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
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


def _com_thread_init() -> None:
    """Initialize COM on the persistent worker thread."""
    _pythoncom.CoInitialize()


class ComAppManager:
    """Manages cached COM Application objects for Office apps.

    COM Application objects (Word, Excel, PowerPoint) are expensive to launch.
    This manager caches them for reuse and handles cleanup on shutdown.

    All COM calls run on a single persistent thread with CoInitialize called
    once at startup. This ensures cached COM objects remain connected across
    multiple ``run_com()`` calls.
    """

    def __init__(self) -> None:
        self._apps: dict[str, Any] = {}
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None

    def _get_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1,
                initializer=_com_thread_init,
            )
        return self._executor

    def _create_app(self, prog_id: str) -> Any:
        """Create a new COM Application object for the given ProgID."""
        app = _win32com_client.Dispatch(prog_id)
        if prog_id == "PowerPoint.Application":
            # PowerPoint refuses to automate with Visible=False
            # (-2147352567). Set visible and minimize instead.
            app.Visible = True
            app.WindowState = 2  # ppWindowMinimized
        else:
            app.Visible = False
        app.DisplayAlerts = False
        return app

    def get_app(self, prog_id: str) -> Any:
        """Get or create a cached COM Application object.

        Must be called from the COM worker thread (inside ``run_com``).
        Automatically reconnects if the cached object is stale (e.g.
        the Office process was closed or the RPC connection dropped).

        Args:
            prog_id: COM ProgID, e.g. "Word.Application", "Excel.Application",
                     "PowerPoint.Application".
        """
        if prog_id in self._apps:
            try:
                # Lightweight health check — access a property to verify
                # the COM connection is still alive.
                self._apps[prog_id].Name  # noqa: B018
            except Exception:
                logger.info("COM app %s disconnected, reconnecting", prog_id)
                del self._apps[prog_id]
        if prog_id not in self._apps:
            self._apps[prog_id] = self._create_app(prog_id)
        return self._apps[prog_id]

    def quit_all(self) -> None:
        """Quit all cached COM Application objects and shut down the worker thread."""
        if self._executor is not None:
            # Run quit on the COM thread where the objects live
            def _quit_apps() -> None:
                for prog_id, app in list(self._apps.items()):
                    try:
                        app.Quit()
                    except Exception:
                        logger.debug("Failed to quit COM app %s", prog_id)
                    del self._apps[prog_id]
                _pythoncom.CoUninitialize()

            try:
                self._executor.submit(_quit_apps).result(timeout=10)
            except Exception:
                logger.debug("Failed to quit COM apps on worker thread")
                self._apps.clear()
            self._executor.shutdown(wait=False)
            self._executor = None
        else:
            # No executor — just clear the cache (e.g. in tests)
            for prog_id, app in list(self._apps.items()):
                try:
                    app.Quit()
                except Exception:
                    logger.debug("Failed to quit COM app %s", prog_id)
                del self._apps[prog_id]

    async def run_com(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Run a callable on the persistent COM worker thread.

        Uses a single-thread executor so COM objects created by ``get_app()``
        remain in the same apartment and stay connected across calls.
        """
        executor = self._get_executor()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(executor, lambda: fn(*args, **kwargs))


_manager: ComAppManager | None = None


def get_manager() -> ComAppManager:
    """Get the singleton ComAppManager instance."""
    global _manager
    if _manager is None:
        _manager = ComAppManager()
    return _manager

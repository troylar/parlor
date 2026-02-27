"""Mtime-based space file watcher for hot-reload.

Polls a single space YAML file and triggers a callback when the file
changes.  Invalid YAML is rejected — the previous valid config remains.
Interval is configurable via team config (``space_refresh_interval``).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL: float = 5.0


class SpaceFileWatcher:
    """Watch a space YAML file for changes via mtime polling.

    Parameters
    ----------
    path:
        Space file to monitor.
    on_change:
        Async or sync callback invoked with the parsed dict when
        a valid change is detected.
    interval:
        Polling interval in seconds (default 5, team-overridable via
        ``space_refresh_interval``).
    """

    def __init__(
        self,
        path: Path,
        on_change: Callable[[dict[str, Any]], Any],
        *,
        interval: float = _DEFAULT_INTERVAL,
    ) -> None:
        self._path = path.resolve()
        self._on_change = on_change
        self._interval = max(1.0, interval)
        self._mtime: float | None = None
        self._task: asyncio.Task[None] | None = None

        if self._path.exists():
            self._mtime = self._path.stat().st_mtime

    async def start(self) -> None:
        """Start the background polling loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop the background polling loop."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self._check()
            except Exception:
                logger.exception("Error checking space file %s", self._path)

    async def _check(self) -> None:
        try:
            current_mtime = self._path.stat().st_mtime
        except OSError:
            return

        if self._mtime is not None and current_mtime == self._mtime:
            return

        try:
            content = self._path.read_text(encoding="utf-8")
            raw = yaml.safe_load(content)
        except OSError:
            return
        except Exception:
            logger.warning("Space file %s has invalid YAML — ignoring change", self._path)
            return

        if not isinstance(raw, dict):
            logger.warning("Space file %s is not a dict — ignoring change", self._path)
            return

        name = raw.get("name", "")
        if not name:
            logger.warning("Space file %s missing name — ignoring change", self._path)
            return

        self._mtime = current_mtime
        logger.info("Space file changed: %s", self._path)

        try:
            result = self._on_change(raw)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception("Space file change callback failed for %s", self._path)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def interval(self) -> float:
        return self._interval

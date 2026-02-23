"""Mtime-based config file watcher for live reload.

Checks config files periodically and triggers a callback when any file
has been modified since the last check.  Invalid configs are rejected
(logged as warnings) — the previous valid config remains active.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable

import yaml

from .config_validator import validate_config

logger = logging.getLogger(__name__)


class ConfigWatcher:
    """Watch one or more config files for changes via mtime polling.

    Parameters
    ----------
    paths:
        Config file paths to monitor.  Non-existent paths are silently
        skipped until they appear.
    on_change:
        Async callback invoked with ``(path, raw_dict)`` when a valid
        config change is detected.
    interval:
        Polling interval in seconds (default 5).
    """

    def __init__(
        self,
        paths: list[Path],
        on_change: Callable[[Path, dict[str, Any]], Any],
        *,
        interval: float = 5.0,
    ) -> None:
        self._paths = [p.resolve() for p in paths]
        self._on_change = on_change
        self._interval = interval
        self._mtimes: dict[Path, float] = {}
        self._task: asyncio.Task[None] | None = None

        # Snapshot current mtimes
        for p in self._paths:
            if p.exists():
                self._mtimes[p] = p.stat().st_mtime

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
            for p in self._paths:
                try:
                    await self._check_file(p)
                except Exception:
                    logger.exception("Error checking config file %s", p)

    async def _check_file(self, path: Path) -> None:
        if not path.exists():
            return

        current_mtime = path.stat().st_mtime
        previous_mtime = self._mtimes.get(path)

        if previous_mtime is not None and current_mtime == previous_mtime:
            return

        # File changed — validate before accepting
        try:
            content = path.read_text(encoding="utf-8")
            raw = yaml.safe_load(content)
        except Exception:
            logger.warning("Config file %s has invalid YAML — ignoring change", path)
            return

        if not isinstance(raw, dict):
            logger.warning("Config file %s is not a dict — ignoring change", path)
            return

        result = validate_config(raw)
        if not result.is_valid:
            errors = [e for e in result.errors if e.severity == "error"]
            logger.warning(
                "Config file %s has validation errors — ignoring change: %s",
                path,
                "; ".join(str(e) for e in errors),
            )
            return

        # Warnings are OK — accept the change
        for w in result.errors:
            if w.severity == "warning":
                logger.info("Config reload warning for %s: %s", path, w)

        self._mtimes[path] = current_mtime
        logger.info("Config file changed: %s", path)

        callback_result = self._on_change(path, raw)
        if asyncio.iscoroutine(callback_result):
            await callback_result

    @property
    def watching(self) -> list[Path]:
        """Return the list of paths being watched."""
        return list(self._paths)

    def add_path(self, path: Path) -> None:
        """Add a new path to watch."""
        resolved = path.resolve()
        if resolved not in self._paths:
            self._paths.append(resolved)
            if resolved.exists():
                self._mtimes[resolved] = resolved.stat().st_mtime

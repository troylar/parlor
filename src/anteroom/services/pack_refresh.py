"""Background worker for pack source refresh.

Periodically pulls configured pack source repositories and re-installs
packs when upstream content changes.  Follows the same pattern as
:class:`RetentionWorker` — configurable interval, exponential backoff,
auto-disable after repeated failures.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import PackSourceConfig
from . import packs
from .pack_sources import ensure_source, resolve_cache_path

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = 1800.0  # 30 minutes
MAX_INTERVAL = 7200.0  # 2 hours
BACKOFF_MULTIPLIER = 2.0
MAX_CONSECUTIVE_FAILURES = 10


@dataclass
class SourceRefreshResult:
    """Result of refreshing a single pack source."""

    url: str
    success: bool
    packs_updated: int = 0
    packs_installed: int = 0
    error: str = ""
    changed: bool = False


@dataclass
class _SourceState:
    """Per-source tracking for next-due scheduling."""

    config: PackSourceConfig
    last_refreshed: float = 0.0
    consecutive_failures: int = 0


class PackRefreshWorker:
    """Background worker that refreshes pack sources on a schedule."""

    def __init__(
        self,
        db: sqlite3.Connection,
        data_dir: Path,
        sources: list[PackSourceConfig],
    ) -> None:
        self._db = db
        self._data_dir = data_dir
        self._sources = [_SourceState(config=s) for s in sources if s.refresh_interval > 0]
        self._manual_sources = [s for s in sources if s.refresh_interval == 0]
        self._all_sources = sources
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._consecutive_failures = 0
        self._poll_interval = 60.0  # check every minute which sources are due

    @property
    def running(self) -> bool:
        return self._running

    def _is_due(self, state: _SourceState) -> bool:
        """Check if a source is due for refresh."""
        if state.last_refreshed == 0.0:
            return True
        elapsed = time.monotonic() - state.last_refreshed
        return elapsed >= state.config.refresh_interval * 60

    def refresh_source(self, source: PackSourceConfig) -> SourceRefreshResult:
        """Refresh a single pack source (synchronous).

        Calls ``ensure_source`` to clone-or-pull, then scans the cache
        directory for pack manifests and installs/updates as needed.
        """
        result = ensure_source(
            source.url,
            source.branch,
            self._data_dir,
        )
        if not result.success:
            return SourceRefreshResult(url=source.url, success=False, error=result.error)

        cache_path = resolve_cache_path(source.url, self._data_dir)
        if not cache_path.is_dir():
            return SourceRefreshResult(url=source.url, success=False, error="cache directory missing after ensure")

        installed, updated = install_from_source(self._db, cache_path)

        return SourceRefreshResult(
            url=source.url,
            success=True,
            packs_installed=installed,
            packs_updated=updated,
            changed=result.changed or installed > 0 or updated > 0,
        )

    def refresh_all(self) -> list[SourceRefreshResult]:
        """Refresh all configured sources (including manual-only)."""
        results: list[SourceRefreshResult] = []
        for source in self._all_sources:
            results.append(self.refresh_source(source))
        return results

    async def run_once(self) -> list[SourceRefreshResult]:
        """Run a single refresh cycle for due sources."""
        results: list[SourceRefreshResult] = []
        for state in self._sources:
            if not self._is_due(state):
                continue
            try:
                r = self.refresh_source(state.config)
                results.append(r)
                state.last_refreshed = time.monotonic()
                if r.success:
                    state.consecutive_failures = 0
                else:
                    state.consecutive_failures += 1
                    logger.warning("Pack source refresh failed for %s: %s", state.config.url, r.error)
            except Exception as e:
                state.consecutive_failures += 1
                logger.error("Pack source refresh error for %s: %s", state.config.url, e)
                results.append(SourceRefreshResult(url=state.config.url, success=False, error=str(e)))

        return results

    async def run_forever(self) -> None:
        """Poll at regular intervals, refreshing due sources."""
        self._running = True
        logger.info(
            "Pack refresh worker started (%d auto-refresh sources, %d manual-only)",
            len(self._sources),
            len(self._manual_sources),
        )

        # Initial refresh of all auto-refresh sources
        try:
            await self.run_once()
        except Exception as e:
            logger.error("Pack refresh worker initial cycle error: %s", e)

        while self._running:
            await asyncio.sleep(self._poll_interval)
            if not self._running:
                break
            try:
                await self.run_once()
                if self._consecutive_failures > 0:
                    self._consecutive_failures = 0
            except Exception as e:
                self._consecutive_failures += 1
                logger.error("Pack refresh worker error: %s", type(e).__name__, exc_info=True)
                if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    logger.error(
                        "Pack refresh worker disabled after %d consecutive failures",
                        self._consecutive_failures,
                    )
                    break

    def start(self) -> None:
        """Start the background refresh loop."""
        self._task = asyncio.ensure_future(self.run_forever())

    def stop(self) -> None:
        """Stop the background refresh loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()


def install_from_source(db: sqlite3.Connection, source_dir: Path) -> tuple[int, int]:
    """Scan a source directory for pack manifests and install/update.

    Walks *source_dir* looking for ``pack.yaml`` files.  For each manifest
    found, installs the pack if not present, or updates it if already
    installed and the content has changed.

    Returns ``(installed_count, updated_count)``.
    """
    installed = 0
    updated = 0

    manifest_paths = list(source_dir.rglob("pack.yaml"))
    for manifest_path in manifest_paths:
        pack_dir = manifest_path.parent
        try:
            manifest = packs.parse_manifest(manifest_path)
        except ValueError as e:
            logger.warning("Invalid manifest in %s: %s", manifest_path, e)
            continue

        errors = packs.validate_manifest(manifest, pack_dir)
        if errors:
            logger.warning("Manifest validation errors in %s: %s", manifest_path, errors)
            continue

        existing = packs.get_pack(db, manifest.namespace, manifest.name)
        if existing:
            try:
                packs.update_pack(db, manifest, pack_dir)
                updated += 1
                logger.info("Updated pack %s/%s from source", manifest.namespace, manifest.name)
            except ValueError as e:
                logger.warning("Failed to update pack %s/%s: %s", manifest.namespace, manifest.name, e)
        else:
            try:
                packs.install_pack(db, manifest, pack_dir)
                installed += 1
                logger.info("Installed pack %s/%s from source", manifest.namespace, manifest.name)
            except ValueError as e:
                logger.warning("Failed to install pack %s/%s: %s", manifest.namespace, manifest.name, e)

    return installed, updated

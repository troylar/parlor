"""Background worker for pack source refresh.

Periodically pulls configured pack source repositories and re-installs
packs when upstream content changes.  Follows the same pattern as
:class:`RetentionWorker` — configurable interval, exponential backoff,
auto-disable after repeated failures.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..db import ThreadSafeConnection
import time
from dataclasses import dataclass, field
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
    packs_attached: int = 0
    error: str = ""
    changed: bool = False
    changed_pack_ids: list[str] = field(default_factory=list)


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
        db: ThreadSafeConnection,
        data_dir: Path,
        sources: list[PackSourceConfig],
        on_packs_changed: Callable[[], None] | None = None,
        event_loop: asyncio.AbstractEventLoop | None = None,
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
        self._on_packs_changed = on_packs_changed
        self._event_loop = event_loop
        self._last_changed_pack_ids: list[str] = []

    @property
    def running(self) -> bool:
        return self._running

    def _is_due(self, state: _SourceState) -> bool:
        """Check if a source is due for refresh.

        Applies exponential backoff when consecutive failures occur, up to
        ``MAX_INTERVAL``.
        """
        if state.last_refreshed == 0.0:
            return True
        base_interval = state.config.refresh_interval * 60
        if state.consecutive_failures > 0:
            interval = min(
                base_interval * (BACKOFF_MULTIPLIER**state.consecutive_failures),
                MAX_INTERVAL,
            )
        else:
            interval = base_interval
        elapsed = time.monotonic() - state.last_refreshed
        return elapsed >= interval

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

        ifs = install_from_source(
            self._db,
            cache_path,
            auto_attach=source.auto_attach,
            priority=source.priority,
        )

        return SourceRefreshResult(
            url=source.url,
            success=True,
            packs_installed=ifs.installed,
            packs_updated=ifs.updated,
            packs_attached=ifs.attached,
            changed=result.changed or ifs.installed > 0 or ifs.updated > 0,
            changed_pack_ids=ifs.changed_pack_ids,
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
                r = await asyncio.to_thread(self.refresh_source, state.config)
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

        if self._on_packs_changed and any(
            r.packs_installed > 0 or r.packs_updated > 0 or r.packs_attached > 0 for r in results
        ):
            all_changed: list[str] = []
            for r in results:
                all_changed.extend(r.changed_pack_ids)
            self._last_changed_pack_ids = all_changed
            if self._event_loop:
                self._event_loop.call_soon_threadsafe(self._on_packs_changed)
            else:
                self._on_packs_changed()

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


@dataclass
class InstallFromSourceResult:
    """Result of scanning a source directory for pack manifests."""

    installed: int = 0
    updated: int = 0
    attached: int = 0
    changed_pack_ids: list[str] = field(default_factory=list)


def install_from_source(
    db: ThreadSafeConnection,
    source_dir: Path,
    *,
    auto_attach: bool = False,
    priority: int = 50,
) -> InstallFromSourceResult:
    """Scan a source directory for pack manifests and install/update.

    Walks *source_dir* looking for ``pack.yaml`` files.  For each manifest
    found, installs the pack if not present, or updates it if already
    installed and the content has changed.

    When *auto_attach* is ``True``, newly installed packs are automatically
    attached at global scope with the given *priority*.  Updates skip
    attachment (attachment persists across updates).

    Returns an :class:`InstallFromSourceResult` with counts and changed pack IDs.
    """
    from .pack_attachments import attach_pack, list_attachments

    result = InstallFromSourceResult()

    # Pre-fetch attached pack IDs for idempotent attach checks
    attached_ids: set[str] = set()
    if auto_attach:
        for att in list_attachments(db):
            attached_ids.add(att["pack_id"])

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

        existing = packs.get_pack_by_source_path(db, str(pack_dir))
        if existing:
            if existing.get("version") == manifest.version:
                logger.debug(
                    "Pack %s/%s already at v%s, skipping",
                    manifest.namespace,
                    manifest.name,
                    manifest.version,
                )
                continue
            try:
                pack_result = packs.update_pack(db, manifest, pack_dir)
                result.updated += 1
                result.changed_pack_ids.append(pack_result["id"])
                logger.info("Updated pack %s/%s from source", manifest.namespace, manifest.name)
            except ValueError as e:
                logger.warning("Failed to update pack %s/%s: %s", manifest.namespace, manifest.name, e)
        else:
            try:
                pack_result = packs.install_pack(db, manifest, pack_dir)
                result.installed += 1
                result.changed_pack_ids.append(pack_result["id"])
                logger.info("Installed pack %s/%s from source", manifest.namespace, manifest.name)
                # Auto-attach newly installed packs
                if auto_attach and pack_result["id"] not in attached_ids:
                    try:
                        attach_pack(db, pack_result["id"], priority=priority)
                        result.attached += 1
                        attached_ids.add(pack_result["id"])
                        logger.info(
                            "Auto-attached pack %s/%s at priority %d",
                            manifest.namespace,
                            manifest.name,
                            priority,
                        )
                    except ValueError as attach_err:
                        logger.warning(
                            "Auto-attach failed for %s/%s: %s",
                            manifest.namespace,
                            manifest.name,
                            attach_err,
                        )
            except ValueError as e:
                logger.warning("Failed to install pack %s/%s: %s", manifest.namespace, manifest.name, e)

    return result

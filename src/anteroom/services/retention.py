"""Background worker for data retention policy enforcement.

Periodically purges conversations older than the configured retention period,
along with their messages, tool calls, embeddings, and attachment files.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = 3600.0  # 1 hour
MAX_INTERVAL = 7200.0  # 2 hours
BACKOFF_MULTIPLIER = 2.0
MAX_CONSECUTIVE_FAILURES = 5

# Conversation IDs should be UUID4 format; reject anything else before path operations
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def purge_conversations_before(
    db: object,
    cutoff: datetime,
    data_dir: Path,
    *,
    purge_attachments: bool = True,
    dry_run: bool = False,
) -> int:
    """Delete conversations last updated before *cutoff*.

    CASCADE handles messages, tool_calls, and embeddings.
    Attachment files are deleted from disk when *purge_attachments* is True.

    Returns the count of conversations purged.
    """
    import sqlite3

    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")

    conn: sqlite3.Connection = db  # type: ignore[assignment]
    rows = conn.execute(
        "SELECT id FROM conversations WHERE updated_at < ?",
        (cutoff_str,),
    ).fetchall()

    if not rows:
        return 0

    count = 0
    for row in rows:
        cid = row["id"] if hasattr(row, "keys") else row[0]
        if dry_run:
            count += 1
            continue

        if purge_attachments and _UUID_RE.match(cid):
            attachments_dir = data_dir / "attachments" / cid
            if attachments_dir.exists():
                shutil.rmtree(attachments_dir)

        conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))
        count += 1

    if not dry_run and count:
        conn.commit()

    return count


def purge_orphaned_attachments(data_dir: Path, db: object, *, dry_run: bool = False) -> int:
    """Delete attachment directories with no corresponding conversation.

    Returns the count of orphaned directories removed.
    """
    import sqlite3

    attachments_root = data_dir / "attachments"
    if not attachments_root.exists():
        return 0

    conn: sqlite3.Connection = db  # type: ignore[assignment]
    count = 0
    for entry in attachments_root.iterdir():
        if not entry.is_dir():
            continue
        if not _UUID_RE.match(entry.name):
            continue
        row = conn.execute("SELECT 1 FROM conversations WHERE id = ?", (entry.name,)).fetchone()
        if row is None:
            if not dry_run:
                shutil.rmtree(entry)
            count += 1

    return count


class RetentionWorker:
    """Background worker that enforces data retention policies."""

    def __init__(
        self,
        db: object,
        data_dir: Path,
        retention_days: int,
        *,
        check_interval: int = 3600,
        purge_attachments: bool = True,
    ) -> None:
        self._db = db
        self._data_dir = data_dir
        self._retention_days = retention_days
        self._check_interval = max(60, check_interval)
        self._purge_attachments = purge_attachments
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._consecutive_failures = 0
        self._current_interval = float(self._check_interval)

    @property
    def retention_days(self) -> int:
        return self._retention_days

    @property
    def running(self) -> bool:
        return self._running

    def _reset_backoff(self) -> None:
        self._consecutive_failures = 0
        self._current_interval = float(self._check_interval)

    def _apply_backoff(self) -> None:
        self._consecutive_failures += 1
        self._current_interval = min(
            float(self._check_interval) * (BACKOFF_MULTIPLIER**self._consecutive_failures),
            MAX_INTERVAL,
        )
        logger.warning(
            "Retention worker: %d consecutive failures, next interval %.0fs",
            self._consecutive_failures,
            self._current_interval,
        )

    async def run_once(self) -> int:
        """Run a single retention cycle. Returns total items purged."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        count = purge_conversations_before(
            self._db,
            cutoff,
            self._data_dir,
            purge_attachments=self._purge_attachments,
        )
        if count:
            logger.info("Retention: purged %d conversation(s) older than %d days", count, self._retention_days)

        orphaned = purge_orphaned_attachments(self._data_dir, self._db)
        if orphaned:
            logger.info("Retention: removed %d orphaned attachment dir(s)", orphaned)

        return count + orphaned

    async def run_forever(self) -> None:
        """Poll at regular intervals, enforcing retention policy."""
        self._running = True
        logger.info(
            "Retention worker started (retention_days=%d, interval=%ds)",
            self._retention_days,
            self._check_interval,
        )
        while self._running:
            try:
                await self.run_once()
                if self._consecutive_failures > 0:
                    self._reset_backoff()
            except Exception as e:
                logger.error("Retention worker error: %s", type(e).__name__, exc_info=True)
                self._apply_backoff()
                if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    logger.error(
                        "Retention worker disabled after %d consecutive failures",
                        self._consecutive_failures,
                    )
                    break
            await asyncio.sleep(self._current_interval)

    def start(self) -> None:
        """Start the background retention loop."""
        self._task = asyncio.ensure_future(self.run_forever())

    def stop(self) -> None:
        """Stop the background retention loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

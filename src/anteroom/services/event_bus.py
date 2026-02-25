"""In-process async event bus with SQLite-backed cross-process polling.

Single-process flow (instant):
    publish() → put_nowait() into subscriber queues

Multi-process flow (polled):
    publish() → INSERT into change_log table
    background poller → SELECT new rows → publish to local subscribers (skipping own process_id)

This allows N Anteroom server processes sharing the same SQLite DB to see each other's
events with ~1-2s latency, without exposing any ports.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..db import DatabaseManager

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 1.5
CLEANUP_INTERVAL_SECONDS = 300  # 5 minutes
CLEANUP_MAX_AGE_SECONDS = 600  # keep rows for 10 minutes


class EventBus:
    """Pub/sub event bus using asyncio.Queue per subscriber.

    Channels follow the pattern:
    - ``conversation:{id}`` for per-conversation events (messages, streaming)
    - ``global:{db_name}`` for database-wide events (conversation CRUD, title changes)

    Thread-safety: all operations run within the asyncio event loop.
    The bus supports N concurrent subscribers per channel.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._process_id: str = uuid.uuid4().hex
        self._db_manager: DatabaseManager | None = None
        self._poll_task: asyncio.Task | None = None
        self._last_seen_ids: dict[str, int] = {}  # db_name → last change_log id
        self._cleanup_counter = 0

    @property
    def process_id(self) -> str:
        return self._process_id

    def subscribe(self, channel: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        if channel not in self._subscribers:
            self._subscribers[channel] = set()
        self._subscribers[channel].add(queue)
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subs = self._subscribers.get(channel)
        if subs:
            subs.discard(queue)
            if not subs:
                del self._subscribers[channel]

    async def publish(self, channel: str, event: dict[str, Any]) -> None:
        """Publish event to local subscribers and persist to change_log for cross-process polling."""
        # Local delivery (instant)
        subs = self._subscribers.get(channel)
        if subs:
            for queue in list(subs):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning("Event bus: queue full on channel %s, dropping event", channel)

        # Persist to DB for cross-process delivery
        self._persist_event(channel, event)

    def _persist_event(self, channel: str, event: dict[str, Any]) -> None:
        """Write event to change_log table. Non-blocking, best-effort."""
        if not self._db_manager:
            return

        event_type = event.get("type", "unknown")
        payload = json.dumps(event.get("data", {}))

        # Determine which DB this channel belongs to
        db_name = self._channel_to_db_name(channel)
        try:
            db = self._db_manager.get(db_name)
            db.execute(
                "INSERT INTO change_log (process_id, channel, event_type, payload) VALUES (?, ?, ?, ?)",
                (self._process_id, channel, event_type, payload),
            )
            db.commit()
        except Exception:
            logger.debug("Failed to persist event to change_log", exc_info=True)

    def _channel_to_db_name(self, channel: str) -> str:
        """Extract db name from channel. ``global:team-alpha`` → ``team-alpha``."""
        if channel.startswith("global:"):
            return channel.split(":", 1)[1]
        # conversation channels: look up which DB — fall back to personal
        return "personal"

    # --- Polling ---

    def start_polling(self, db_manager: DatabaseManager) -> None:
        """Begin background polling of change_log across all databases."""
        self._db_manager = db_manager
        # Seed last_seen_ids to current max so we don't replay old events on startup
        for db_info in db_manager.list_databases():
            name = db_info["name"]
            try:
                db = db_manager.get(name)
                row = db.execute_fetchone("SELECT MAX(id) as max_id FROM change_log")
                self._last_seen_ids[name] = (row["max_id"] or 0) if row else 0
            except Exception:
                self._last_seen_ids[name] = 0
        self._poll_task = asyncio.ensure_future(self._poll_loop())
        logger.info("Event bus polling started (process_id=%s)", self._process_id[:8])

    def stop_polling(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            self._poll_task = None

    async def _poll_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                await self._poll_all_databases()
                self._cleanup_counter += 1
                if self._cleanup_counter >= int(CLEANUP_INTERVAL_SECONDS / POLL_INTERVAL_SECONDS):
                    self._cleanup_counter = 0
                    self._cleanup_old_events()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Event bus poll loop crashed")

    async def _poll_all_databases(self) -> None:
        if not self._db_manager:
            return
        for db_info in self._db_manager.list_databases():
            name = db_info["name"]
            last_id = self._last_seen_ids.get(name, 0)
            try:
                db = self._db_manager.get(name)
                rows = db.execute_fetchall(
                    "SELECT id, process_id, channel, event_type, payload FROM change_log WHERE id > ? ORDER BY id",
                    (last_id,),
                )
                for row in rows:
                    self._last_seen_ids[name] = row["id"]
                    # Skip events from our own process — we already delivered them locally
                    if row["process_id"] == self._process_id:
                        continue
                    event = {
                        "type": row["event_type"],
                        "data": json.loads(row["payload"]),
                    }
                    channel = row["channel"]
                    subs = self._subscribers.get(channel)
                    if subs:
                        for queue in list(subs):
                            try:
                                queue.put_nowait(event)
                            except asyncio.QueueFull:
                                pass
            except Exception:
                logger.debug("Poll error for db '%s'", name, exc_info=True)

    def _cleanup_old_events(self) -> None:
        """Delete change_log rows older than CLEANUP_MAX_AGE_SECONDS."""
        if not self._db_manager:
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=CLEANUP_MAX_AGE_SECONDS)).strftime("%Y-%m-%dT%H:%M:%S")
        for db_info in self._db_manager.list_databases():
            try:
                db = self._db_manager.get(db_info["name"])
                db.execute("DELETE FROM change_log WHERE created_at < ?", (cutoff,))
                db.commit()
            except Exception:
                logger.debug("Cleanup error for db '%s'", db_info["name"], exc_info=True)

    def subscriber_count(self, channel: str) -> int:
        return len(self._subscribers.get(channel, set()))

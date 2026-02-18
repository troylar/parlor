"""Background worker for generating message embeddings."""

from __future__ import annotations

import asyncio
import hashlib
import logging

from . import storage
from .embeddings import EmbeddingPermanentError, EmbeddingService, EmbeddingTransientError

logger = logging.getLogger(__name__)

MIN_CONTENT_LENGTH = 10
DEFAULT_INTERVAL = 30.0
MAX_INTERVAL = 300.0
BACKOFF_MULTIPLIER = 2.0
MAX_CONSECUTIVE_FAILURES = 10


class EmbeddingWorker:
    def __init__(self, db: object, embedding_service: EmbeddingService, batch_size: int = 50) -> None:
        self._db = db
        self._service = embedding_service
        self._batch_size = batch_size
        self._running = False
        self._disabled = False
        self._disabled_reason: str | None = None
        self._consecutive_failures = 0
        self._current_interval = DEFAULT_INTERVAL
        self._base_interval = DEFAULT_INTERVAL
        self._task: asyncio.Task[None] | None = None

    @property
    def disabled(self) -> bool:
        return self._disabled

    @property
    def disabled_reason(self) -> str | None:
        return self._disabled_reason

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def current_interval(self) -> float:
        return self._current_interval

    def _reset_backoff(self) -> None:
        self._consecutive_failures = 0
        self._current_interval = self._base_interval

    def _apply_backoff(self) -> None:
        self._consecutive_failures += 1
        self._current_interval = min(
            self._base_interval * (BACKOFF_MULTIPLIER**self._consecutive_failures),
            MAX_INTERVAL,
        )
        logger.warning(
            "Embedding worker: %d consecutive failures, next interval %.0fs",
            self._consecutive_failures,
            self._current_interval,
        )
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self._disabled = True
            self._disabled_reason = f"Auto-disabled after {self._consecutive_failures} consecutive failures"
            logger.error("Embedding worker auto-disabled: %s", self._disabled_reason)

    def _disable_permanent(self, reason: str) -> None:
        self._disabled = True
        self._disabled_reason = reason
        logger.error("Embedding worker permanently disabled: %s", reason)

    async def process_pending(self) -> int:
        """Process unembedded messages. Returns count of messages embedded."""
        messages = storage.get_unembedded_messages(self._db, limit=self._batch_size)
        if not messages:
            return 0

        # Filter out short messages
        eligible = [m for m in messages if len(m.get("content", "")) >= MIN_CONTENT_LENGTH]
        if not eligible:
            return 0

        texts = [m["content"] for m in eligible]
        embeddings = await self._service.embed_batch(texts, batch_size=self._batch_size)

        count = 0
        for msg, embedding in zip(eligible, embeddings):
            if embedding is None:
                continue
            content_hash = hashlib.sha256(msg["content"].encode()).hexdigest()
            try:
                storage.store_embedding(
                    self._db,
                    msg["id"],
                    msg["conversation_id"],
                    embedding,
                    content_hash,
                )
                count += 1
            except Exception as e:
                logger.error("Failed to store embedding for message %s: %s", msg["id"], type(e).__name__)

        if count:
            logger.info("Embedded %d messages", count)
        return count

    async def embed_message(self, message_id: str, content: str, conversation_id: str) -> None:
        """Embed a single message (called inline after message creation)."""
        if len(content) < MIN_CONTENT_LENGTH:
            return

        content_hash = hashlib.sha256(content.encode()).hexdigest()
        try:
            embedding = await self._service.embed(content)
        except (EmbeddingPermanentError, EmbeddingTransientError):
            logger.warning("Embedding failed for message %s, will be retried by worker", message_id)
            return
        if embedding is None:
            return

        try:
            storage.store_embedding(self._db, message_id, conversation_id, embedding, content_hash)
        except Exception as e:
            logger.error("Failed to store embedding for message %s: %s", message_id, type(e).__name__)

    async def run_forever(self, interval: float = 30.0) -> None:
        """Poll for unembedded messages at a regular interval with exponential backoff."""
        self._running = True
        self._base_interval = interval
        self._current_interval = interval
        logger.info("Embedding worker started (interval=%.0fs)", interval)
        while self._running:
            if self._disabled:
                logger.debug("Embedding worker is disabled: %s", self._disabled_reason)
                await asyncio.sleep(MAX_INTERVAL)
                continue
            try:
                await self.process_pending()
                if self._consecutive_failures > 0:
                    self._reset_backoff()
            except EmbeddingPermanentError as e:
                self._disable_permanent(f"Permanent API error: {e} (status={e.status_code})")
            except EmbeddingTransientError:
                self._apply_backoff()
            except Exception as e:
                logger.error("Embedding worker unexpected error: %s", type(e).__name__)
                self._apply_backoff()
            await asyncio.sleep(self._current_interval)

    def start(self, interval: float = 30.0) -> None:
        """Start the background polling loop."""
        self._task = asyncio.ensure_future(self.run_forever(interval))

    def stop(self) -> None:
        """Stop the background polling loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

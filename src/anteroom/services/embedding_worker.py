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
MAX_STORE_RETRIES = 3


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
        self._store_failures: dict[str, int] = {}

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
        """Process unembedded messages and source chunks. Returns total count embedded."""
        count = await self._process_pending_messages()
        count += await self._process_pending_source_chunks()
        return count

    async def _process_pending_messages(self) -> int:
        """Process unembedded messages. Returns count of messages embedded."""
        messages = storage.get_unembedded_messages(self._db, limit=self._batch_size)
        if not messages:
            return 0

        # Mark short messages as skipped so they are never re-queried
        eligible = []
        for m in messages:
            if len(m.get("content", "")) < MIN_CONTENT_LENGTH:
                content_hash = hashlib.sha256(m.get("content", "").encode()).hexdigest()
                try:
                    storage.mark_embedding_skipped(
                        self._db, m["id"], m["conversation_id"], content_hash, status="skipped"
                    )
                except Exception:
                    logger.debug("Failed to mark short message %s as skipped", m["id"], exc_info=True)
            else:
                eligible.append(m)

        if not eligible:
            return 0

        texts = [m["content"] for m in eligible]
        embeddings = await self._service.embed_batch(texts, batch_size=self._batch_size)

        count = 0
        for msg, embedding in zip(eligible, embeddings):
            content_hash = hashlib.sha256(msg["content"].encode()).hexdigest()
            if embedding is None:
                logger.warning("Embedding returned None for message %s, marking as skipped", msg["id"])
                try:
                    storage.mark_embedding_skipped(
                        self._db, msg["id"], msg["conversation_id"], content_hash, status="failed"
                    )
                except Exception:
                    logger.debug("Failed to mark message %s as skipped", msg["id"], exc_info=True)
                continue
            try:
                storage.store_embedding(
                    self._db,
                    msg["id"],
                    msg["conversation_id"],
                    embedding,
                    content_hash,
                )
                count += 1
                self._store_failures.pop(msg["id"], None)
            except Exception as e:
                fails = self._store_failures.get(msg["id"], 0) + 1
                self._store_failures[msg["id"]] = fails
                if fails >= MAX_STORE_RETRIES:
                    logger.error(
                        "Failed to store embedding for message %s %d times, marking as failed: %s",
                        msg["id"],
                        fails,
                        type(e).__name__,
                    )
                    try:
                        storage.mark_embedding_skipped(
                            self._db, msg["id"], msg["conversation_id"], content_hash, status="failed"
                        )
                    except Exception:
                        logger.debug("Failed to mark message %s as failed", msg["id"], exc_info=True)
                    self._store_failures.pop(msg["id"], None)
                else:
                    logger.error(
                        "Failed to store embedding for message %s (%d/%d): %s",
                        msg["id"],
                        fails,
                        MAX_STORE_RETRIES,
                        type(e).__name__,
                    )

        if count:
            logger.info("Embedded %d messages", count)
        return count

    async def _process_pending_source_chunks(self) -> int:
        """Process unembedded source chunks. Returns count of chunks embedded."""
        chunks = storage.get_unembedded_source_chunks(self._db, limit=self._batch_size)
        if not chunks:
            return 0

        eligible = []
        for c in chunks:
            if len(c.get("content", "")) < MIN_CONTENT_LENGTH:
                try:
                    storage.mark_source_chunk_embedding_skipped(
                        self._db, c["id"], c["source_id"], c["content_hash"], status="skipped"
                    )
                except Exception:
                    logger.debug("Failed to mark short chunk %s as skipped", c["id"], exc_info=True)
            else:
                eligible.append(c)

        if not eligible:
            return 0

        texts = [c["content"] for c in eligible]
        embeddings = await self._service.embed_batch(texts, batch_size=self._batch_size)

        count = 0
        for chunk, embedding in zip(eligible, embeddings):
            if embedding is None:
                logger.warning("Embedding returned None for source chunk %s, marking as skipped", chunk["id"])
                try:
                    storage.mark_source_chunk_embedding_skipped(
                        self._db, chunk["id"], chunk["source_id"], chunk["content_hash"], status="failed"
                    )
                except Exception:
                    logger.debug("Failed to mark chunk %s as skipped", chunk["id"], exc_info=True)
                continue
            try:
                storage.store_source_chunk_embedding(
                    self._db,
                    chunk["id"],
                    chunk["source_id"],
                    embedding,
                    chunk["content_hash"],
                )
                count += 1
                self._store_failures.pop(chunk["id"], None)
            except Exception as e:
                fails = self._store_failures.get(chunk["id"], 0) + 1
                self._store_failures[chunk["id"]] = fails
                if fails >= MAX_STORE_RETRIES:
                    logger.error(
                        "Failed to store embedding for chunk %s %d times, marking as failed: %s",
                        chunk["id"],
                        fails,
                        type(e).__name__,
                    )
                    try:
                        storage.mark_source_chunk_embedding_skipped(
                            self._db, chunk["id"], chunk["source_id"], chunk["content_hash"], status="failed"
                        )
                    except Exception:
                        logger.debug("Failed to mark chunk %s as failed", chunk["id"], exc_info=True)
                    self._store_failures.pop(chunk["id"], None)
                else:
                    logger.error(
                        "Failed to store embedding for chunk %s (%d/%d): %s",
                        chunk["id"],
                        fails,
                        MAX_STORE_RETRIES,
                        type(e).__name__,
                    )

        if count:
            logger.info("Embedded %d source chunks", count)
        return count

    async def embed_source(self, source_id: str) -> int:
        """Embed all chunks of a source inline. Returns count of chunks embedded."""
        chunks = storage.list_source_chunks(self._db, source_id)
        if not chunks:
            return 0

        eligible = [c for c in chunks if len(c.get("content", "")) >= MIN_CONTENT_LENGTH]
        if not eligible:
            return 0

        texts = [c["content"] for c in eligible]
        try:
            embeddings = await self._service.embed_batch(texts, batch_size=self._batch_size)
        except (EmbeddingPermanentError, EmbeddingTransientError):
            logger.warning("Embedding failed for source %s, will be retried by worker", source_id)
            return 0

        count = 0
        for chunk, embedding in zip(eligible, embeddings):
            if embedding is None:
                continue
            try:
                storage.store_source_chunk_embedding(
                    self._db,
                    chunk["id"],
                    source_id,
                    embedding,
                    chunk["content_hash"],
                )
                count += 1
            except Exception as e:
                logger.error("Failed to store embedding for source chunk %s: %s", chunk["id"], type(e).__name__)

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

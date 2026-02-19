"""Tests for the embedding worker."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anteroom.services.embedding_worker import EmbeddingWorker


class TestEmbeddingWorker:
    def _make_worker(self, db=None, service=None):
        db = db or MagicMock()
        service = service or AsyncMock()
        return EmbeddingWorker(db, service, batch_size=10)

    @pytest.mark.asyncio
    async def test_process_pending_embeds_messages(self) -> None:
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[[0.1, 0.2], [0.3, 0.4]])

        messages = [
            {"id": "m1", "conversation_id": "c1", "content": "Hello, this is a test message.", "role": "user"},
            {"id": "m2", "conversation_id": "c1", "content": "This is another test message.", "role": "assistant"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=messages)
            mock_storage.store_embedding = MagicMock()

            worker = self._make_worker(service=service)
            count = await worker.process_pending()

        assert count == 2
        assert mock_storage.store_embedding.call_count == 2

    @pytest.mark.asyncio
    async def test_process_pending_skips_short_messages(self) -> None:
        service = AsyncMock()

        messages = [
            {"id": "m1", "conversation_id": "c1", "content": "short", "role": "user"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=messages)

            worker = self._make_worker(service=service)
            count = await worker.process_pending()

        assert count == 0
        service.embed_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_pending_returns_zero_when_no_messages(self) -> None:
        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=[])

            worker = self._make_worker()
            count = await worker.process_pending()

        assert count == 0

    @pytest.mark.asyncio
    async def test_process_pending_handles_none_embeddings(self) -> None:
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[None, [0.1, 0.2]])

        messages = [
            {"id": "m1", "conversation_id": "c1", "content": "First test message content", "role": "user"},
            {"id": "m2", "conversation_id": "c1", "content": "Second test message content", "role": "assistant"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=messages)
            mock_storage.store_embedding = MagicMock()

            worker = self._make_worker(service=service)
            count = await worker.process_pending()

        assert count == 1
        mock_storage.store_embedding.assert_called_once()

    @pytest.mark.asyncio
    async def test_embed_message_single(self) -> None:
        service = AsyncMock()
        service.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.store_embedding = MagicMock()

            worker = self._make_worker(service=service)
            await worker.embed_message("m1", "This is a test message for embedding.", "c1")

        service.embed.assert_called_once_with("This is a test message for embedding.")
        mock_storage.store_embedding.assert_called_once()
        call_args = mock_storage.store_embedding.call_args
        # store_embedding is called with positional args: (db, message_id, conv_id, embedding, hash)
        assert call_args[0][1] == "m1"

    @pytest.mark.asyncio
    async def test_embed_message_skips_short(self) -> None:
        service = AsyncMock()

        with patch("anteroom.services.embedding_worker.storage"):
            worker = self._make_worker(service=service)
            await worker.embed_message("m1", "hi", "c1")

        service.embed.assert_not_called()

    @pytest.mark.asyncio
    async def test_embed_message_handles_none_result(self) -> None:
        service = AsyncMock()
        service.embed = AsyncMock(return_value=None)

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            worker = self._make_worker(service=service)
            await worker.embed_message("m1", "This is a test message for embedding.", "c1")

        mock_storage.store_embedding.assert_not_called()

    @pytest.mark.asyncio
    async def test_embed_message_uses_content_hash(self) -> None:
        service = AsyncMock()
        service.embed = AsyncMock(return_value=[0.1])
        content = "This is a test message for content hash."
        expected_hash = hashlib.sha256(content.encode()).hexdigest()

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.store_embedding = MagicMock()

            worker = self._make_worker(service=service)
            await worker.embed_message("m1", content, "c1")

        call_args = mock_storage.store_embedding.call_args
        # Check the content_hash positional or keyword arg
        all_args = list(call_args[0]) + list(call_args[1].values())
        assert expected_hash in all_args

    @pytest.mark.asyncio
    async def test_process_pending_continues_after_store_failure(self) -> None:
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[[0.1, 0.2], [0.3, 0.4]])

        messages = [
            {"id": "m1", "conversation_id": "c1", "content": "First test message content here", "role": "user"},
            {"id": "m2", "conversation_id": "c1", "content": "Second test message content here", "role": "assistant"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=messages)
            # First store_embedding raises, second succeeds
            mock_storage.store_embedding = MagicMock(side_effect=[Exception("DB error"), None])

            worker = self._make_worker(service=service)
            count = await worker.process_pending()

        # Should have counted only the successful one
        assert count == 1

    @pytest.mark.asyncio
    async def test_embed_message_handles_store_error(self) -> None:
        service = AsyncMock()
        service.embed = AsyncMock(return_value=[0.1, 0.2])

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.store_embedding = MagicMock(side_effect=Exception("DB error"))

            worker = self._make_worker(service=service)
            # Should not raise
            await worker.embed_message("m1", "This is long enough to embed", "c1")

    @pytest.mark.asyncio
    async def test_run_forever_processes_and_stops(self) -> None:
        import asyncio

        service = AsyncMock()
        worker = self._make_worker(service=service)

        call_count = 0

        async def mock_process():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                worker.stop()
            return 0

        worker.process_pending = mock_process

        # run_forever with a tiny interval, should exit after stop()
        await asyncio.wait_for(worker.run_forever(interval=0.01), timeout=2.0)
        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_content_hash_is_deterministic(self) -> None:
        service = AsyncMock()
        service.embed = AsyncMock(return_value=[0.1])
        content = "This is a deterministic hash test."

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.store_embedding = MagicMock()

            worker = self._make_worker(service=service)
            await worker.embed_message("m1", content, "c1")
            await worker.embed_message("m2", content, "c2")

        # Both calls should use the same hash
        hash1 = mock_storage.store_embedding.call_args_list[0][0][4]
        hash2 = mock_storage.store_embedding.call_args_list[1][0][4]
        assert hash1 == hash2

    def test_stop_sets_flag(self) -> None:
        worker = self._make_worker()
        worker._running = True
        worker.stop()
        assert not worker._running

    @pytest.mark.asyncio
    async def test_embed_message_handles_transient_error(self) -> None:
        from anteroom.services.embeddings import EmbeddingTransientError

        service = AsyncMock()
        service.embed = AsyncMock(side_effect=EmbeddingTransientError("rate limited", status_code=429))

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            worker = self._make_worker(service=service)
            await worker.embed_message("m1", "This is a test message for embedding.", "c1")

        mock_storage.store_embedding.assert_not_called()

    @pytest.mark.asyncio
    async def test_embed_message_handles_permanent_error(self) -> None:
        from anteroom.services.embeddings import EmbeddingPermanentError

        service = AsyncMock()
        service.embed = AsyncMock(side_effect=EmbeddingPermanentError("model not found", status_code=404))

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            worker = self._make_worker(service=service)
            await worker.embed_message("m1", "This is a test message for embedding.", "c1")

        mock_storage.store_embedding.assert_not_called()


class TestEmbeddingWorkerBackoff:
    def _make_worker(self, db=None, service=None):
        db = db or MagicMock()
        service = service or AsyncMock()
        return EmbeddingWorker(db, service, batch_size=10)

    def test_initial_state(self) -> None:
        worker = self._make_worker()
        assert not worker.disabled
        assert worker.disabled_reason is None
        assert worker.consecutive_failures == 0
        assert worker.current_interval == 30.0

    def test_apply_backoff_increases_interval(self) -> None:
        worker = self._make_worker()
        worker._base_interval = 30.0
        worker._apply_backoff()
        assert worker.consecutive_failures == 1
        assert worker.current_interval == 60.0
        worker._apply_backoff()
        assert worker.consecutive_failures == 2
        assert worker.current_interval == 120.0

    def test_backoff_caps_at_max_interval(self) -> None:
        worker = self._make_worker()
        worker._base_interval = 30.0
        for _ in range(20):
            worker._apply_backoff()
        assert worker.current_interval == 300.0

    def test_reset_backoff_clears_state(self) -> None:
        worker = self._make_worker()
        worker._base_interval = 30.0
        worker._apply_backoff()
        worker._apply_backoff()
        assert worker.consecutive_failures == 2
        worker._reset_backoff()
        assert worker.consecutive_failures == 0
        assert worker.current_interval == 30.0

    def test_auto_disable_after_max_failures(self) -> None:
        from anteroom.services.embedding_worker import MAX_CONSECUTIVE_FAILURES

        worker = self._make_worker()
        worker._base_interval = 30.0
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            worker._apply_backoff()
        assert worker.disabled
        assert "Auto-disabled" in (worker.disabled_reason or "")

    def test_disable_permanent(self) -> None:
        worker = self._make_worker()
        worker._disable_permanent("model not found")
        assert worker.disabled
        assert worker.disabled_reason == "model not found"

    @pytest.mark.asyncio
    async def test_run_forever_backoff_on_transient_error(self) -> None:
        import asyncio

        from anteroom.services.embeddings import EmbeddingTransientError

        worker = self._make_worker()
        call_count = 0

        async def mock_process():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                worker.stop()
                raise EmbeddingTransientError("rate limited", status_code=429)
            raise EmbeddingTransientError("rate limited", status_code=429)

        worker.process_pending = mock_process

        await asyncio.wait_for(worker.run_forever(interval=0.01), timeout=5.0)
        assert call_count >= 3
        assert worker.consecutive_failures == 3

    @pytest.mark.asyncio
    async def test_run_forever_disables_on_permanent_error(self) -> None:
        import asyncio

        from anteroom.services.embeddings import EmbeddingPermanentError

        worker = self._make_worker()
        call_count = 0

        async def mock_process():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise EmbeddingPermanentError("model not found", status_code=404)
            # Should not reach here — worker should be disabled
            worker.stop()
            return 0

        worker.process_pending = mock_process

        # Worker disables on permanent error, then sleeps at MAX_INTERVAL.
        # We cancel after a short delay to verify disabled state.
        task = asyncio.create_task(worker.run_forever(interval=0.01))
        await asyncio.sleep(0.05)
        worker.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert worker.disabled
        assert "Permanent" in (worker.disabled_reason or "")

    @pytest.mark.asyncio
    async def test_run_forever_resets_backoff_on_success(self) -> None:
        import asyncio

        from anteroom.services.embeddings import EmbeddingTransientError

        worker = self._make_worker()
        call_count = 0

        async def mock_process():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise EmbeddingTransientError("rate limited", status_code=429)
            if call_count == 2:
                return 0  # successful poll (no errors), resets backoff
            worker.stop()
            return 0

        worker.process_pending = mock_process

        await asyncio.wait_for(worker.run_forever(interval=0.01), timeout=5.0)
        assert worker.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_embed_batch_auth_retry_failure_raises_permanent(self) -> None:
        from openai import AuthenticationError

        from anteroom.services.embeddings import EmbeddingPermanentError, EmbeddingService

        client = AsyncMock()
        fresh_client = AsyncMock()
        # Both original and refreshed client fail with auth error
        client.embeddings.create = AsyncMock(
            side_effect=AuthenticationError(message="invalid", response=MagicMock(status_code=401), body=None)
        )
        fresh_client.embeddings.create = AsyncMock(
            side_effect=AuthenticationError(message="still invalid", response=MagicMock(status_code=401), body=None)
        )
        client.base_url = "https://api.test/v1"

        service = EmbeddingService(client, dimensions=1)
        provider = MagicMock()
        provider.refresh = MagicMock()
        provider.get_token = MagicMock(return_value="new-token")
        service._set_token_provider(provider)

        with patch("anteroom.services.embeddings.AsyncOpenAI", return_value=fresh_client):
            with pytest.raises(EmbeddingPermanentError) as exc_info:
                await service.embed_batch(["hello world test"])
        assert exc_info.value.status_code == 401


class TestEmbeddingWorkerSkipTracking:
    """Regression tests for #185 — embedding worker retrying unembeddable messages forever."""

    def _make_worker(self, db=None, service=None):
        db = db or MagicMock()
        service = service or AsyncMock()
        return EmbeddingWorker(db, service, batch_size=10)

    @pytest.mark.asyncio
    async def test_short_messages_marked_as_skipped(self) -> None:
        """Gap 1: Short messages (<10 chars) should get a sentinel row on first encounter."""
        service = AsyncMock()
        messages = [
            {"id": "m1", "conversation_id": "c1", "content": "hi", "role": "user"},
            {"id": "m2", "conversation_id": "c1", "content": "ok", "role": "user"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=messages)
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=[])
            mock_storage.mark_embedding_skipped = MagicMock()

            worker = self._make_worker(service=service)
            count = await worker.process_pending()

        assert count == 0
        assert mock_storage.mark_embedding_skipped.call_count == 2
        # Verify status is 'skipped'
        for call in mock_storage.mark_embedding_skipped.call_args_list:
            assert (
                call[1].get("status", call[0][4] if len(call[0]) > 4 else None) == "skipped"
                or call.kwargs.get("status") == "skipped"
            )
        service.embed_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_embeddings_marked_as_failed(self) -> None:
        """Gap 3: When embed_batch returns None for an item, mark it as failed."""
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[None, [0.1, 0.2]])

        messages = [
            {"id": "m1", "conversation_id": "c1", "content": "First test message content", "role": "user"},
            {"id": "m2", "conversation_id": "c1", "content": "Second test message content", "role": "assistant"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=messages)
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=[])
            mock_storage.store_embedding = MagicMock()
            mock_storage.mark_embedding_skipped = MagicMock()

            worker = self._make_worker(service=service)
            count = await worker.process_pending()

        assert count == 1
        mock_storage.store_embedding.assert_called_once()
        # m1 got None embedding → marked as failed
        mock_storage.mark_embedding_skipped.assert_called_once()
        skip_call = mock_storage.mark_embedding_skipped.call_args
        assert skip_call[0][1] == "m1"  # message_id

    @pytest.mark.asyncio
    async def test_all_none_embeddings_returns_zero(self) -> None:
        """All None batch results should mark all as failed and return 0."""
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[None, None])

        messages = [
            {"id": "m1", "conversation_id": "c1", "content": "First test message content", "role": "user"},
            {"id": "m2", "conversation_id": "c1", "content": "Second test message content", "role": "user"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=messages)
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=[])
            mock_storage.store_embedding = MagicMock()
            mock_storage.mark_embedding_skipped = MagicMock()

            worker = self._make_worker(service=service)
            count = await worker.process_pending()

        assert count == 0
        mock_storage.store_embedding.assert_not_called()
        assert mock_storage.mark_embedding_skipped.call_count == 2

    @pytest.mark.asyncio
    async def test_store_failure_marks_as_failed_after_retries(self) -> None:
        """Gap 2: store_embedding failures should mark as failed after MAX_STORE_RETRIES."""
        from anteroom.services.embedding_worker import MAX_STORE_RETRIES

        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[[0.1, 0.2]])

        messages = [
            {"id": "m1", "conversation_id": "c1", "content": "A message that fails to store", "role": "user"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=messages)
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=[])
            mock_storage.store_embedding = MagicMock(side_effect=Exception("DB locked"))
            mock_storage.mark_embedding_skipped = MagicMock()

            worker = self._make_worker(service=service)

            # Process multiple times to hit the retry limit
            for _ in range(MAX_STORE_RETRIES):
                await worker._process_pending_messages()

        # After MAX_STORE_RETRIES failures, should be marked as failed
        mock_storage.mark_embedding_skipped.assert_called_once()
        skip_call = mock_storage.mark_embedding_skipped.call_args
        assert skip_call[0][1] == "m1"

    @pytest.mark.asyncio
    async def test_store_failure_below_limit_does_not_mark(self) -> None:
        """store_embedding failures below the retry limit should NOT mark as failed."""
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[[0.1, 0.2]])

        messages = [
            {"id": "m1", "conversation_id": "c1", "content": "A message that fails to store", "role": "user"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=messages)
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=[])
            mock_storage.store_embedding = MagicMock(side_effect=Exception("DB locked"))
            mock_storage.mark_embedding_skipped = MagicMock()

            worker = self._make_worker(service=service)
            # Only one attempt — below limit
            await worker._process_pending_messages()

        mock_storage.mark_embedding_skipped.assert_not_called()
        assert worker._store_failures["m1"] == 1

    @pytest.mark.asyncio
    async def test_mixed_batch_short_valid_none(self) -> None:
        """Mixed batch: short + valid + None embedding → correct handling for each."""
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[[0.1, 0.2], None])

        messages = [
            {"id": "m1", "conversation_id": "c1", "content": "hi", "role": "user"},  # short
            {"id": "m2", "conversation_id": "c1", "content": "This is a valid message for embedding", "role": "user"},
            {"id": "m3", "conversation_id": "c1", "content": "Another valid message for embedding", "role": "user"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=messages)
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=[])
            mock_storage.store_embedding = MagicMock()
            mock_storage.mark_embedding_skipped = MagicMock()

            worker = self._make_worker(service=service)
            count = await worker.process_pending()

        # m1 short → skipped, m2 valid → embedded, m3 None → failed
        assert count == 1
        mock_storage.store_embedding.assert_called_once()
        # mark_embedding_skipped called twice: once for m1 (short), once for m3 (None)
        assert mock_storage.mark_embedding_skipped.call_count == 2

    @pytest.mark.asyncio
    async def test_source_chunks_short_marked_as_skipped(self) -> None:
        """Source chunks that are too short should also get sentinel rows."""
        service = AsyncMock()
        chunks = [
            {"id": "sc1", "source_id": "s1", "content": "hi", "content_hash": "abc"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=[])
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=chunks)
            mock_storage.mark_source_chunk_embedding_skipped = MagicMock()

            worker = self._make_worker(service=service)
            count = await worker.process_pending()

        assert count == 0
        mock_storage.mark_source_chunk_embedding_skipped.assert_called_once()

    @pytest.mark.asyncio
    async def test_source_chunks_none_embedding_marked_as_failed(self) -> None:
        """Source chunks with None embedding result should be marked as failed."""
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[None])

        chunks = [
            {"id": "sc1", "source_id": "s1", "content": "A valid length source chunk content", "content_hash": "abc"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=[])
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=chunks)
            mock_storage.store_source_chunk_embedding = MagicMock()
            mock_storage.mark_source_chunk_embedding_skipped = MagicMock()

            worker = self._make_worker(service=service)
            count = await worker.process_pending()

        assert count == 0
        mock_storage.mark_source_chunk_embedding_skipped.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_success_clears_failure_counter(self) -> None:
        """A successful store_embedding should clear the failure counter for that message."""
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[[0.1, 0.2]])

        messages = [
            {"id": "m1", "conversation_id": "c1", "content": "A message that eventually stores", "role": "user"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=messages)
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=[])

            worker = self._make_worker(service=service)
            # Simulate prior failures
            worker._store_failures["m1"] = 2

            mock_storage.store_embedding = MagicMock()  # succeeds
            await worker._process_pending_messages()

        assert "m1" not in worker._store_failures

    @pytest.mark.asyncio
    async def test_mark_skipped_failure_on_short_message_continues(self) -> None:
        """If mark_embedding_skipped raises for a short message, processing continues."""
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[[0.1, 0.2]])

        messages = [
            {"id": "m1", "conversation_id": "c1", "content": "hi", "role": "user"},  # short, mark fails
            {"id": "m2", "conversation_id": "c1", "content": "This is a valid long message", "role": "user"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=messages)
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=[])
            mock_storage.mark_embedding_skipped = MagicMock(side_effect=Exception("DB error"))
            mock_storage.store_embedding = MagicMock()

            worker = self._make_worker(service=service)
            count = await worker.process_pending()

        # m2 should still be embedded despite m1 mark failure
        assert count == 1
        mock_storage.store_embedding.assert_called_once()

    @pytest.mark.asyncio
    async def test_mark_failed_exception_on_none_embedding_continues(self) -> None:
        """If mark_embedding_skipped raises for a None embedding, loop continues."""
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[None, [0.1, 0.2]])

        messages = [
            {"id": "m1", "conversation_id": "c1", "content": "First valid length message", "role": "user"},
            {"id": "m2", "conversation_id": "c1", "content": "Second valid length message", "role": "user"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=messages)
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=[])
            mock_storage.mark_embedding_skipped = MagicMock(side_effect=Exception("DB error"))
            mock_storage.store_embedding = MagicMock()

            worker = self._make_worker(service=service)
            count = await worker.process_pending()

        # m2 should still be embedded despite m1 mark failure
        assert count == 1
        mock_storage.store_embedding.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_retry_exhausted_mark_failed_exception(self) -> None:
        """If mark_embedding_skipped raises after retry exhaustion, message is still cleaned from tracker."""
        from anteroom.services.embedding_worker import MAX_STORE_RETRIES

        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[[0.1, 0.2]])

        messages = [
            {"id": "m1", "conversation_id": "c1", "content": "A message that always fails to store", "role": "user"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=messages)
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=[])
            mock_storage.store_embedding = MagicMock(side_effect=Exception("DB locked"))
            mock_storage.mark_embedding_skipped = MagicMock(side_effect=Exception("Also fails"))

            worker = self._make_worker(service=service)

            for _ in range(MAX_STORE_RETRIES):
                await worker._process_pending_messages()

        # Message should be cleaned from tracker even though marking as failed also failed
        assert "m1" not in worker._store_failures

    @pytest.mark.asyncio
    async def test_chunks_mark_skipped_failure_continues(self) -> None:
        """If mark_source_chunk_embedding_skipped raises for short chunk, processing continues."""
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[[0.1, 0.2]])

        chunks = [
            {"id": "sc1", "source_id": "s1", "content": "hi", "content_hash": "abc"},
            {"id": "sc2", "source_id": "s1", "content": "Valid length source chunk content", "content_hash": "def"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=[])
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=chunks)
            mock_storage.mark_source_chunk_embedding_skipped = MagicMock(side_effect=Exception("DB error"))
            mock_storage.store_source_chunk_embedding = MagicMock()

            worker = self._make_worker(service=service)
            count = await worker.process_pending()

        assert count == 1
        mock_storage.store_source_chunk_embedding.assert_called_once()

    @pytest.mark.asyncio
    async def test_chunks_mark_failed_exception_on_none_continues(self) -> None:
        """If mark_source_chunk_embedding_skipped raises for None chunk, loop continues."""
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[None, [0.1, 0.2]])

        chunks = [
            {"id": "sc1", "source_id": "s1", "content": "Valid chunk content first", "content_hash": "abc"},
            {"id": "sc2", "source_id": "s1", "content": "Valid chunk content second", "content_hash": "def"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=[])
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=chunks)
            mock_storage.mark_source_chunk_embedding_skipped = MagicMock(side_effect=Exception("DB error"))
            mock_storage.store_source_chunk_embedding = MagicMock()

            worker = self._make_worker(service=service)
            count = await worker.process_pending()

        assert count == 1
        mock_storage.store_source_chunk_embedding.assert_called_once()

    @pytest.mark.asyncio
    async def test_chunks_store_retry_exhausted_mark_failed_exception(self) -> None:
        """If mark_source_chunk_embedding_skipped raises after retry exhaustion, chunk cleaned from tracker."""
        from anteroom.services.embedding_worker import MAX_STORE_RETRIES

        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[[0.1, 0.2]])

        chunks = [
            {"id": "sc1", "source_id": "s1", "content": "Valid chunk that fails to store", "content_hash": "abc"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=[])
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=chunks)
            mock_storage.store_source_chunk_embedding = MagicMock(side_effect=Exception("DB locked"))
            mock_storage.mark_source_chunk_embedding_skipped = MagicMock(side_effect=Exception("Also fails"))

            worker = self._make_worker(service=service)

            for _ in range(MAX_STORE_RETRIES):
                await worker._process_pending_source_chunks()

        assert "sc1" not in worker._store_failures

    @pytest.mark.asyncio
    async def test_chunks_store_success_clears_failure_counter(self) -> None:
        """Successful chunk store clears the failure counter for that chunk."""
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[[0.1, 0.2]])

        chunks = [
            {"id": "sc1", "source_id": "s1", "content": "Valid chunk that succeeds now", "content_hash": "abc"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=[])
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=chunks)
            mock_storage.store_source_chunk_embedding = MagicMock()

            worker = self._make_worker(service=service)
            worker._store_failures["sc1"] = 2
            await worker._process_pending_source_chunks()

        assert "sc1" not in worker._store_failures

    @pytest.mark.asyncio
    async def test_boundary_exactly_min_content_length(self) -> None:
        """Message with exactly MIN_CONTENT_LENGTH chars should be embedded, not skipped."""
        from anteroom.services.embedding_worker import MIN_CONTENT_LENGTH

        service = AsyncMock()
        content = "a" * MIN_CONTENT_LENGTH  # exactly 10 chars
        service.embed_batch = AsyncMock(return_value=[[0.1, 0.2]])

        messages = [
            {"id": "m1", "conversation_id": "c1", "content": content, "role": "user"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=messages)
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=[])
            mock_storage.store_embedding = MagicMock()
            mock_storage.mark_embedding_skipped = MagicMock()

            worker = self._make_worker(service=service)
            count = await worker.process_pending()

        assert count == 1
        mock_storage.store_embedding.assert_called_once()
        mock_storage.mark_embedding_skipped.assert_not_called()

    @pytest.mark.asyncio
    async def test_boundary_one_below_min_content_length(self) -> None:
        """Message with MIN_CONTENT_LENGTH - 1 chars should be skipped."""
        from anteroom.services.embedding_worker import MIN_CONTENT_LENGTH

        service = AsyncMock()
        content = "a" * (MIN_CONTENT_LENGTH - 1)  # 9 chars

        messages = [
            {"id": "m1", "conversation_id": "c1", "content": content, "role": "user"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=messages)
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=[])
            mock_storage.mark_embedding_skipped = MagicMock()

            worker = self._make_worker(service=service)
            count = await worker.process_pending()

        assert count == 0
        mock_storage.mark_embedding_skipped.assert_called_once()
        service.embed_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_content_message_skipped(self) -> None:
        """Message with empty string content should be skipped."""
        service = AsyncMock()
        messages = [
            {"id": "m1", "conversation_id": "c1", "content": "", "role": "user"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=messages)
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=[])
            mock_storage.mark_embedding_skipped = MagicMock()

            worker = self._make_worker(service=service)
            count = await worker.process_pending()

        assert count == 0
        mock_storage.mark_embedding_skipped.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_content_key_treated_as_short(self) -> None:
        """Message missing 'content' key should be treated as short and skipped."""
        service = AsyncMock()
        messages = [
            {"id": "m1", "conversation_id": "c1", "role": "user"},  # no content key
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=messages)
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=[])
            mock_storage.mark_embedding_skipped = MagicMock()

            worker = self._make_worker(service=service)
            count = await worker.process_pending()

        assert count == 0
        mock_storage.mark_embedding_skipped.assert_called_once()


class TestEmbedSource:
    """Tests for embed_source() method."""

    def _make_worker(self, db=None, service=None):
        db = db or MagicMock()
        service = service or AsyncMock()
        return EmbeddingWorker(db, service, batch_size=10)

    @pytest.mark.asyncio
    async def test_embed_source_no_chunks_returns_zero(self) -> None:
        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.list_source_chunks = MagicMock(return_value=[])

            worker = self._make_worker()
            count = await worker.embed_source("s1")

        assert count == 0

    @pytest.mark.asyncio
    async def test_embed_source_all_short_returns_zero(self) -> None:
        chunks = [
            {"id": "sc1", "source_id": "s1", "content": "hi", "content_hash": "abc"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.list_source_chunks = MagicMock(return_value=chunks)

            worker = self._make_worker()
            count = await worker.embed_source("s1")

        assert count == 0

    @pytest.mark.asyncio
    async def test_embed_source_handles_transient_error(self) -> None:
        from anteroom.services.embeddings import EmbeddingTransientError

        service = AsyncMock()
        service.embed_batch = AsyncMock(side_effect=EmbeddingTransientError("rate limited", status_code=429))

        chunks = [
            {"id": "sc1", "source_id": "s1", "content": "Valid length source chunk", "content_hash": "abc"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.list_source_chunks = MagicMock(return_value=chunks)

            worker = self._make_worker(service=service)
            count = await worker.embed_source("s1")

        assert count == 0

    @pytest.mark.asyncio
    async def test_embed_source_handles_permanent_error(self) -> None:
        from anteroom.services.embeddings import EmbeddingPermanentError

        service = AsyncMock()
        service.embed_batch = AsyncMock(side_effect=EmbeddingPermanentError("bad model", status_code=404))

        chunks = [
            {"id": "sc1", "source_id": "s1", "content": "Valid length source chunk", "content_hash": "abc"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.list_source_chunks = MagicMock(return_value=chunks)

            worker = self._make_worker(service=service)
            count = await worker.embed_source("s1")

        assert count == 0

    @pytest.mark.asyncio
    async def test_embed_source_skips_none_embeddings(self) -> None:
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[[0.1], None, [0.3]])

        chunks = [
            {"id": "sc1", "source_id": "s1", "content": "First valid chunk content", "content_hash": "a"},
            {"id": "sc2", "source_id": "s1", "content": "Second valid chunk content", "content_hash": "b"},
            {"id": "sc3", "source_id": "s1", "content": "Third valid chunk content", "content_hash": "c"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.list_source_chunks = MagicMock(return_value=chunks)
            mock_storage.store_source_chunk_embedding = MagicMock()

            worker = self._make_worker(service=service)
            count = await worker.embed_source("s1")

        assert count == 2
        assert mock_storage.store_source_chunk_embedding.call_count == 2

    @pytest.mark.asyncio
    async def test_embed_source_continues_after_store_error(self) -> None:
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[[0.1], [0.2]])

        chunks = [
            {"id": "sc1", "source_id": "s1", "content": "First valid chunk content", "content_hash": "a"},
            {"id": "sc2", "source_id": "s1", "content": "Second valid chunk content", "content_hash": "b"},
        ]

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.list_source_chunks = MagicMock(return_value=chunks)
            mock_storage.store_source_chunk_embedding = MagicMock(side_effect=[Exception("DB error"), None])

            worker = self._make_worker(service=service)
            count = await worker.embed_source("s1")

        # Only the second one should count
        assert count == 1


class TestEmbeddingWorkerLifecycle:
    """Tests for start/stop and run_forever edge cases."""

    def _make_worker(self, db=None, service=None):
        db = db or MagicMock()
        service = service or AsyncMock()
        return EmbeddingWorker(db, service, batch_size=10)

    @pytest.mark.asyncio
    async def test_run_forever_unexpected_exception_applies_backoff(self) -> None:
        import asyncio

        worker = self._make_worker()
        call_count = 0
        backoff_seen = False

        async def mock_process():
            nonlocal call_count, backoff_seen
            call_count += 1
            if call_count >= 2:
                # Check backoff state BEFORE it gets reset by a successful return
                backoff_seen = worker.consecutive_failures >= 1
                worker.stop()
                return 0
            raise RuntimeError("Unexpected crash")

        worker.process_pending = mock_process

        await asyncio.wait_for(worker.run_forever(interval=0.01), timeout=5.0)
        assert call_count >= 2
        # Backoff was applied after the RuntimeError, before reset on success
        assert backoff_seen

    @pytest.mark.asyncio
    async def test_start_creates_task(self) -> None:
        import asyncio

        worker = self._make_worker()

        async def mock_run_forever(interval):
            worker._running = True
            while worker._running:
                await asyncio.sleep(0.01)

        worker.run_forever = mock_run_forever
        worker.start(interval=0.01)

        assert worker._task is not None
        assert isinstance(worker._task, asyncio.Task)

        # Clean up
        worker.stop()
        worker._task.cancel()
        try:
            await worker._task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_stop_cancels_running_task(self) -> None:
        import asyncio

        worker = self._make_worker()

        async def mock_run_forever(interval):
            worker._running = True
            while worker._running:
                await asyncio.sleep(0.01)

        worker.run_forever = mock_run_forever
        worker.start(interval=0.01)
        await asyncio.sleep(0.02)

        worker.stop()
        assert not worker._running
        # Yield to event loop so cancellation propagates
        try:
            await worker._task
        except asyncio.CancelledError:
            pass
        assert worker._task.cancelled() or worker._task.done()

    @pytest.mark.asyncio
    async def test_process_pending_both_empty_returns_zero(self) -> None:
        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=[])
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=[])

            worker = self._make_worker()
            count = await worker.process_pending()

        assert count == 0

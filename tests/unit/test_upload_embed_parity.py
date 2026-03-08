"""Tests for upload → embed → save_all parity and mid-session recovery (#834)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anteroom.services.embedding_worker import EmbeddingWorker


def _make_worker(
    db: MagicMock | None = None,
    service: AsyncMock | None = None,
    vec_manager: MagicMock | None = None,
) -> EmbeddingWorker:
    db = db or MagicMock()
    service = service or AsyncMock()
    return EmbeddingWorker(db, service, batch_size=10, vec_manager=vec_manager)


class TestEmbedSourceSaveAll:
    """embed_source() must flush the vector index to disk after embedding."""

    @pytest.mark.asyncio
    async def test_embed_source_calls_save_all_on_success(self) -> None:
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[[0.1, 0.2]])
        vec_manager = MagicMock()
        vec_manager.source_chunks = MagicMock()

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.list_source_chunks = MagicMock(
                return_value=[{"id": "c1", "content": "Long enough content for embed", "content_hash": "h1"}]
            )
            mock_storage.store_source_chunk_embedding = MagicMock()

            worker = _make_worker(service=service, vec_manager=vec_manager)
            count = await worker.embed_source("src-1")

        assert count == 1
        vec_manager.save_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_embed_source_skips_save_all_when_no_chunks_embedded(self) -> None:
        service = AsyncMock()
        vec_manager = MagicMock()
        vec_manager.source_chunks = MagicMock()

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.list_source_chunks = MagicMock(return_value=[])

            worker = _make_worker(service=service, vec_manager=vec_manager)
            count = await worker.embed_source("src-1")

        assert count == 0
        vec_manager.save_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_embed_source_skips_save_all_when_no_vec_manager(self) -> None:
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[[0.1, 0.2]])

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.list_source_chunks = MagicMock(
                return_value=[{"id": "c1", "content": "Long enough content for embed", "content_hash": "h1"}]
            )
            mock_storage.store_source_chunk_embedding = MagicMock()

            worker = _make_worker(service=service, vec_manager=None)
            count = await worker.embed_source("src-1")

        assert count == 1

    @pytest.mark.asyncio
    async def test_embed_source_save_all_failure_does_not_raise(self) -> None:
        service = AsyncMock()
        service.embed_batch = AsyncMock(return_value=[[0.1, 0.2]])
        vec_manager = MagicMock()
        vec_manager.source_chunks = MagicMock()
        vec_manager.save_all.side_effect = OSError("disk full")

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.list_source_chunks = MagicMock(
                return_value=[{"id": "c1", "content": "Long enough content for embed", "content_hash": "h1"}]
            )
            mock_storage.store_source_chunk_embedding = MagicMock()

            worker = _make_worker(service=service, vec_manager=vec_manager)
            count = await worker.embed_source("src-1")

        assert count == 1


class TestRepairStaleEmbeddings:
    """Mid-session recovery resets stale 'embedded' rows to 'pending'."""

    def test_repair_resets_missing_chunks_to_pending(self) -> None:
        db = MagicMock()
        db.execute_fetchall = MagicMock(return_value=[{"chunk_id": "c1"}, {"chunk_id": "c2"}, {"chunk_id": "c3"}])
        vec_manager = MagicMock()
        source_chunks_index = MagicMock()
        source_chunks_index.contains = MagicMock(side_effect=lambda k: k != "c2")
        vec_manager.source_chunks = source_chunks_index

        worker = _make_worker(db=db, vec_manager=vec_manager)
        worker._repair_stale_embeddings()

        db.execute.assert_called_once()
        sql = db.execute.call_args[0][0]
        params = db.execute.call_args[0][1]
        assert "SET status = 'pending'" in sql
        assert params == ("c2",)
        db.commit.assert_called_once()

    def test_repair_noop_when_all_present(self) -> None:
        db = MagicMock()
        db.execute_fetchall = MagicMock(return_value=[{"chunk_id": "c1"}, {"chunk_id": "c2"}])
        vec_manager = MagicMock()
        source_chunks_index = MagicMock()
        source_chunks_index.contains = MagicMock(return_value=True)
        vec_manager.source_chunks = source_chunks_index

        worker = _make_worker(db=db, vec_manager=vec_manager)
        worker._repair_stale_embeddings()

        db.execute.assert_not_called()

    def test_repair_noop_when_no_vec_manager(self) -> None:
        db = MagicMock()

        worker = _make_worker(db=db, vec_manager=None)
        worker._repair_stale_embeddings()

        db.execute_fetchall.assert_not_called()

    def test_repair_noop_when_no_embedded_rows(self) -> None:
        db = MagicMock()
        db.execute_fetchall = MagicMock(return_value=[])
        vec_manager = MagicMock()
        vec_manager.source_chunks = MagicMock()

        worker = _make_worker(db=db, vec_manager=vec_manager)
        worker._repair_stale_embeddings()

        db.execute.assert_not_called()

    def test_repair_handles_exception_gracefully(self) -> None:
        db = MagicMock()
        db.execute_fetchall = MagicMock(side_effect=Exception("db error"))
        vec_manager = MagicMock()
        vec_manager.source_chunks = MagicMock()

        worker = _make_worker(db=db, vec_manager=vec_manager)
        worker._repair_stale_embeddings()  # should not raise

    def test_repair_cursor_advances_across_calls(self) -> None:
        """Cursor sweeps through all rows, not just the first page."""
        db = MagicMock()
        # First call: return full page (2 rows at limit=2) — all present
        # Second call: return partial page (1 row) — missing from index
        # Third call: empty — cursor resets
        call_count = [0]

        def _fetchall(sql: str, params: tuple[int, ...]) -> list[dict[str, str]]:
            call_count[0] += 1
            if call_count[0] == 1:
                assert params == (2, 0), f"First call should have offset=0, got {params}"
                return [{"chunk_id": "c1"}, {"chunk_id": "c2"}]
            elif call_count[0] == 2:
                assert params == (2, 2), f"Second call should have offset=2, got {params}"
                return [{"chunk_id": "c3"}]
            else:
                return []

        db.execute_fetchall = MagicMock(side_effect=_fetchall)
        vec_manager = MagicMock()
        source_chunks_index = MagicMock()
        # c1, c2 are present; c3 is missing
        source_chunks_index.contains = MagicMock(side_effect=lambda k: k != "c3")
        vec_manager.source_chunks = source_chunks_index

        worker = _make_worker(db=db, vec_manager=vec_manager)

        # First sweep page: all present, no update, cursor advances
        worker._repair_stale_embeddings(limit=2)
        assert worker._repair_offset == 2
        db.execute.assert_not_called()

        # Second sweep page: c3 missing, reset to pending, cursor wraps
        worker._repair_stale_embeddings(limit=2)
        assert worker._repair_offset == 0  # partial page, cursor resets
        db.execute.assert_called_once()
        sql = db.execute.call_args[0][0]
        params = db.execute.call_args[0][1]
        assert "SET status = 'pending'" in sql
        assert params == ("c3",)

    def test_repair_cursor_resets_on_empty_result(self) -> None:
        """Cursor resets to 0 when no rows are returned (full sweep complete)."""
        db = MagicMock()
        db.execute_fetchall = MagicMock(return_value=[])
        vec_manager = MagicMock()
        vec_manager.source_chunks = MagicMock()

        worker = _make_worker(db=db, vec_manager=vec_manager)
        worker._repair_offset = 200  # simulate mid-sweep

        worker._repair_stale_embeddings()

        assert worker._repair_offset == 0  # cursor reset

    @pytest.mark.asyncio
    async def test_process_pending_triggers_repair_every_10th_cycle(self) -> None:
        """_repair_stale_embeddings runs on every 10th process_pending cycle."""
        db = MagicMock()
        db.execute_fetchall = MagicMock(return_value=[])
        vec_manager = MagicMock()
        vec_manager.source_chunks = MagicMock()

        worker = _make_worker(db=db, vec_manager=vec_manager)

        with patch("anteroom.services.embedding_worker.storage") as mock_storage:
            mock_storage.get_unembedded_messages = MagicMock(return_value=[])
            mock_storage.get_unembedded_source_chunks = MagicMock(return_value=[])

            # Run 9 cycles — repair should NOT fire
            for _ in range(9):
                await worker.process_pending()
            db.execute_fetchall.assert_not_called()

            # 10th cycle — repair SHOULD fire
            await worker.process_pending()
            db.execute_fetchall.assert_called_once()


class TestCliUploadEmbedWiring:
    """CLI /upload must call _embed_after_upload() after saving — the core parity fix for #834.

    These tests exercise the actual extracted function from repl.py, not
    EmbeddingWorker in isolation. If _embed_after_upload() is removed or
    stops calling embed_source(), these tests fail.
    """

    @pytest.mark.asyncio
    async def test_embed_after_upload_calls_embed_source(self) -> None:
        """_embed_after_upload() gets a worker and calls embed_source()."""
        from anteroom.cli.repl import _embed_after_upload

        mock_worker = AsyncMock()
        mock_worker.embed_source = AsyncMock(return_value=3)

        async def get_worker() -> AsyncMock:
            return mock_worker

        result = await _embed_after_upload(get_worker, "src-42")

        assert result == 3
        mock_worker.embed_source.assert_called_once_with("src-42")

    @pytest.mark.asyncio
    async def test_embed_after_upload_returns_none_when_no_worker(self) -> None:
        """When embedding service is unavailable, returns None (upload not blocked)."""
        from anteroom.cli.repl import _embed_after_upload

        async def get_worker() -> None:
            return None

        result = await _embed_after_upload(get_worker, "src-42")

        assert result is None

    @pytest.mark.asyncio
    async def test_embed_after_upload_handles_embed_failure(self) -> None:
        """embed_source() exception is caught — upload succeeds without embedding."""
        from anteroom.cli.repl import _embed_after_upload

        mock_worker = AsyncMock()
        mock_worker.embed_source = AsyncMock(side_effect=RuntimeError("model crashed"))

        async def get_worker() -> AsyncMock:
            return mock_worker

        result = await _embed_after_upload(get_worker, "src-42")

        assert result is None
        mock_worker.embed_source.assert_called_once_with("src-42")

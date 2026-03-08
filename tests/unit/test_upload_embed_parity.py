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

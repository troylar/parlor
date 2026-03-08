"""Tests for cross-encoder reranking integration in the RAG pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anteroom.config import RagConfig, RerankerConfig
from anteroom.services.rag import RetrievedChunk, _rerank_chunks, retrieve_context


def _make_chunk(content: str, distance: float, source_type: str = "message", **kwargs: object) -> RetrievedChunk:
    return RetrievedChunk(
        content=content,
        source_type=source_type,
        source_label=kwargs.get("source_label", "test"),  # type: ignore[arg-type]
        distance=distance,
        conversation_id=kwargs.get("conversation_id"),  # type: ignore[arg-type]
        message_id=kwargs.get("message_id"),  # type: ignore[arg-type]
        source_id=kwargs.get("source_id"),  # type: ignore[arg-type]
        chunk_id=kwargs.get("chunk_id"),  # type: ignore[arg-type]
        conversation_type=kwargs.get("conversation_type"),  # type: ignore[arg-type]
    )


class TestRerankChunks:
    """Tests for the _rerank_chunks helper."""

    @pytest.mark.asyncio
    async def test_reranks_and_reorders(self) -> None:
        chunks = [
            _make_chunk("doc A", 0.1),
            _make_chunk("doc B", 0.2),
            _make_chunk("doc C", 0.3),
        ]
        reranker = AsyncMock()
        # Reverse order: C best, A worst
        reranker.rerank.return_value = [(2, 0.9), (1, 0.7), (0, 0.3)]
        cfg = RerankerConfig(top_k=3, score_threshold=0.0)

        result = await _rerank_chunks("query", chunks, reranker, cfg)

        assert len(result) == 3
        assert result[0].content == "doc C"
        assert result[1].content == "doc B"
        assert result[2].content == "doc A"

    @pytest.mark.asyncio
    async def test_score_threshold_filters(self) -> None:
        chunks = [_make_chunk("doc A", 0.1), _make_chunk("doc B", 0.2)]
        reranker = AsyncMock()
        reranker.rerank.return_value = [(0, 0.8), (1, 0.1)]
        cfg = RerankerConfig(top_k=5, score_threshold=0.5)

        result = await _rerank_chunks("query", chunks, reranker, cfg)

        assert len(result) == 1
        assert result[0].content == "doc A"

    @pytest.mark.asyncio
    async def test_all_filtered_returns_original(self) -> None:
        chunks = [_make_chunk("doc A", 0.1)]
        reranker = AsyncMock()
        reranker.rerank.return_value = [(0, 0.01)]
        cfg = RerankerConfig(top_k=5, score_threshold=0.5)

        result = await _rerank_chunks("query", chunks, reranker, cfg)

        # Falls back to original when all filtered
        assert len(result) == 1
        assert result[0].content == "doc A"

    @pytest.mark.asyncio
    async def test_reranker_failure_returns_original(self) -> None:
        chunks = [_make_chunk("doc A", 0.1), _make_chunk("doc B", 0.2)]
        reranker = AsyncMock()
        reranker.rerank.side_effect = RuntimeError("model crashed")
        cfg = RerankerConfig(top_k=5, score_threshold=0.0)

        result = await _rerank_chunks("query", chunks, reranker, cfg)

        assert result == chunks  # graceful fallback

    @pytest.mark.asyncio
    async def test_distance_replaced_with_reranker_score(self) -> None:
        chunks = [_make_chunk("doc A", 0.99)]
        reranker = AsyncMock()
        reranker.rerank.return_value = [(0, 0.85)]
        cfg = RerankerConfig(top_k=5, score_threshold=0.0)

        result = await _rerank_chunks("query", chunks, reranker, cfg)

        assert result[0].distance == pytest.approx(0.15, abs=0.001)

    @pytest.mark.asyncio
    async def test_preserves_chunk_metadata(self) -> None:
        chunks = [
            _make_chunk(
                "doc A",
                0.1,
                source_type="source_chunk",
                source_label="My Source",
                source_id="src-1",
                chunk_id="chunk-1",
            )
        ]
        reranker = AsyncMock()
        reranker.rerank.return_value = [(0, 0.8)]
        cfg = RerankerConfig(top_k=5, score_threshold=0.0)

        result = await _rerank_chunks("query", chunks, reranker, cfg)

        assert result[0].source_type == "source_chunk"
        assert result[0].source_id == "src-1"
        assert result[0].chunk_id == "chunk-1"


class TestRetrieveContextWithReranker:
    """Tests that retrieve_context passes reranker through correctly."""

    @pytest.mark.asyncio
    async def test_reranker_not_called_when_none(self) -> None:
        """Reranker is skipped when reranker_service is None."""
        config = RagConfig(enabled=True)
        embedding_svc = AsyncMock()
        embedding_svc.embed.return_value = [0.1] * 384

        with patch("anteroom.services.rag.storage") as mock_storage:
            mock_storage.search_similar_messages.return_value = [
                {"message_id": "m1", "conversation_id": "c1", "content": "hello", "distance": 0.1}
            ]
            mock_storage.search_similar_source_chunks.return_value = []
            mock_storage.get_conversation.return_value = {"title": "Test Conv"}

            db = MagicMock()
            result = await retrieve_context(
                "test query text",
                db,
                embedding_svc,
                config,
                reranker_service=None,
                reranker_config=None,
            )
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_reranker_called_when_provided(self) -> None:
        """Reranker is called when both service and config are provided."""
        config = RagConfig(enabled=True)
        reranker_config = RerankerConfig(enabled=True, top_k=5, score_threshold=0.0)
        embedding_svc = AsyncMock()
        embedding_svc.embed.return_value = [0.1] * 384
        reranker_svc = AsyncMock()
        reranker_svc.rerank.return_value = [(0, 0.8)]

        with patch("anteroom.services.rag.storage") as mock_storage:
            mock_storage.search_similar_messages.return_value = [
                {"message_id": "m1", "conversation_id": "c1", "content": "hello", "distance": 0.1}
            ]
            mock_storage.search_similar_source_chunks.return_value = []
            mock_storage.get_conversation.return_value = {"title": "Test Conv"}

            db = MagicMock()
            result = await retrieve_context(
                "test query text",
                db,
                embedding_svc,
                config,
                reranker_service=reranker_svc,
                reranker_config=reranker_config,
            )
            reranker_svc.rerank.assert_called_once()
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_reranker_disabled_not_called(self) -> None:
        """Reranker with enabled=False is not called."""
        config = RagConfig(enabled=True)
        reranker_config = RerankerConfig(enabled=False)
        embedding_svc = AsyncMock()
        embedding_svc.embed.return_value = [0.1] * 384
        reranker_svc = AsyncMock()

        with patch("anteroom.services.rag.storage") as mock_storage:
            mock_storage.search_similar_messages.return_value = [
                {"message_id": "m1", "conversation_id": "c1", "content": "hello", "distance": 0.1}
            ]
            mock_storage.search_similar_source_chunks.return_value = []
            mock_storage.get_conversation.return_value = {"title": "Test Conv"}

            db = MagicMock()
            await retrieve_context(
                "test query text",
                db,
                embedding_svc,
                config,
                reranker_service=reranker_svc,
                reranker_config=reranker_config,
            )
            reranker_svc.rerank.assert_not_called()

    @pytest.mark.asyncio
    async def test_candidate_multiplier_widens_search(self) -> None:
        """When reranker is active, retrieval limit is multiplied."""
        config = RagConfig(enabled=True, max_chunks=5)
        reranker_config = RerankerConfig(enabled=True, candidate_multiplier=3, top_k=5, score_threshold=0.0)
        embedding_svc = AsyncMock()
        embedding_svc.embed.return_value = [0.1] * 384
        reranker_svc = AsyncMock()
        reranker_svc.rerank.return_value = []

        with patch("anteroom.services.rag.storage") as mock_storage:
            mock_storage.search_similar_messages.return_value = []
            mock_storage.search_similar_source_chunks.return_value = []

            db = MagicMock()
            await retrieve_context(
                "test query text",
                db,
                embedding_svc,
                config,
                reranker_service=reranker_svc,
                reranker_config=reranker_config,
            )
            # Should search with limit=15 (5 * 3)
            call_kwargs = mock_storage.search_similar_messages.call_args
            assert call_kwargs.kwargs.get("limit", call_kwargs[1].get("limit")) == 15

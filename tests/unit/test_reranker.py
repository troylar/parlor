"""Tests for cross-encoder reranker service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from anteroom.config import AppConfig, RerankerConfig
from anteroom.services.reranker import LocalRerankerService, create_reranker_service

# ---------------------------------------------------------------------------
# LocalRerankerService
# ---------------------------------------------------------------------------


class TestLocalRerankerService:
    """Tests for the local fastembed-based reranker."""

    @pytest.mark.asyncio
    async def test_rerank_empty_documents(self) -> None:
        svc = LocalRerankerService()
        result = await svc.rerank("query", [])
        assert result == []

    @pytest.mark.asyncio
    async def test_rerank_returns_sorted_indices(self) -> None:
        svc = LocalRerankerService()
        mock_model = MagicMock()
        # Simulate scores: doc2 best, doc0 next, doc1 worst
        mock_model.rerank.return_value = [0.3, 0.1, 0.9]
        svc._cross_encoder = mock_model

        result = await svc.rerank("test query", ["doc0", "doc1", "doc2"])
        # Should be sorted by score descending: (2, 0.9), (0, 0.3), (1, 0.1)
        assert len(result) == 3
        assert result[0] == (2, 0.9)
        assert result[1] == (0, 0.3)
        assert result[2] == (1, 0.1)

    @pytest.mark.asyncio
    async def test_rerank_top_k(self) -> None:
        svc = LocalRerankerService()
        mock_model = MagicMock()
        mock_model.rerank.return_value = [0.3, 0.1, 0.9, 0.5]
        svc._cross_encoder = mock_model

        result = await svc.rerank("query", ["a", "b", "c", "d"], top_k=2)
        assert len(result) == 2
        assert result[0][0] == 2  # highest score
        assert result[1][0] == 3  # second highest

    @pytest.mark.asyncio
    async def test_rerank_transient_error_on_failure(self) -> None:
        from anteroom.services.embeddings import EmbeddingTransientError

        svc = LocalRerankerService()
        mock_model = MagicMock()
        mock_model.rerank.side_effect = RuntimeError("boom")
        svc._cross_encoder = mock_model

        with pytest.raises(EmbeddingTransientError, match="Local reranking failed"):
            await svc.rerank("query", ["doc"])

    @pytest.mark.asyncio
    async def test_rerank_memory_error(self) -> None:
        from anteroom.services.embeddings import EmbeddingTransientError

        svc = LocalRerankerService()
        mock_model = MagicMock()
        mock_model.rerank.side_effect = MemoryError("oom")
        svc._cross_encoder = mock_model

        with pytest.raises(EmbeddingTransientError, match="Out of memory"):
            await svc.rerank("query", ["doc"])

    def test_ensure_model_import_error(self) -> None:
        from anteroom.services.embeddings import EmbeddingPermanentError

        svc = LocalRerankerService()
        with patch.dict("sys.modules", {"fastembed": None}):
            with pytest.raises(EmbeddingPermanentError, match="fastembed is not installed"):
                svc._ensure_model()

    def test_ensure_model_network_error(self) -> None:
        from anteroom.services.embeddings import EmbeddingPermanentError

        svc = LocalRerankerService()
        mock_fastembed = MagicMock()
        mock_fastembed.TextCrossEncoder.side_effect = RuntimeError("connection timeout")
        with patch.dict("sys.modules", {"fastembed": mock_fastembed}):
            svc._cross_encoder = None
            with pytest.raises(EmbeddingPermanentError, match="Failed to download"):
                svc._ensure_model()

    def test_ensure_model_generic_error(self) -> None:
        from anteroom.services.embeddings import EmbeddingPermanentError

        svc = LocalRerankerService()
        mock_fastembed = MagicMock()
        mock_fastembed.TextCrossEncoder.side_effect = RuntimeError("invalid model format")
        with patch.dict("sys.modules", {"fastembed": mock_fastembed}):
            svc._cross_encoder = None
            with pytest.raises(EmbeddingPermanentError, match="Failed to load cross-encoder"):
                svc._ensure_model()

    def test_ensure_model_caches(self) -> None:
        svc = LocalRerankerService()
        sentinel = MagicMock()
        svc._cross_encoder = sentinel
        assert svc._ensure_model() is sentinel

    @pytest.mark.asyncio
    async def test_probe_success(self) -> None:
        svc = LocalRerankerService()
        mock_model = MagicMock()
        mock_model.rerank.return_value = [0.5]
        svc._cross_encoder = mock_model

        assert await svc.probe() is True

    @pytest.mark.asyncio
    async def test_probe_failure(self) -> None:
        svc = LocalRerankerService()
        mock_model = MagicMock()
        mock_model.rerank.side_effect = RuntimeError("fail")
        svc._cross_encoder = mock_model

        assert await svc.probe() is False

    def test_model_property(self) -> None:
        svc = LocalRerankerService(model_name="test-model")
        assert svc.model == "test-model"

    def test_cache_dir_stored(self) -> None:
        svc = LocalRerankerService(cache_dir="/tmp/models")
        assert svc._cache_dir == "/tmp/models"

    def test_cache_dir_default_empty(self) -> None:
        svc = LocalRerankerService()
        assert svc._cache_dir == ""

    def test_cache_dir_passed_to_text_cross_encoder(self) -> None:
        mock_fastembed = MagicMock()
        mock_tce_class = MagicMock()
        mock_fastembed.TextCrossEncoder = mock_tce_class
        svc = LocalRerankerService(model_name="test-model", cache_dir="/custom/cache")
        with patch.dict("sys.modules", {"fastembed": mock_fastembed}):
            svc._ensure_model()
        mock_tce_class.assert_called_once_with(
            model_name="test-model", cache_dir="/custom/cache", local_files_only=True
        )

    def test_cache_dir_not_passed_when_empty(self) -> None:
        mock_fastembed = MagicMock()
        mock_tce_class = MagicMock()
        mock_fastembed.TextCrossEncoder = mock_tce_class
        svc = LocalRerankerService(model_name="test-model", cache_dir="")
        with patch.dict("sys.modules", {"fastembed": mock_fastembed}):
            svc._ensure_model()
        mock_tce_class.assert_called_once_with(model_name="test-model")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreateRerankerService:
    """Tests for the reranker factory function."""

    def _make_config(self, **reranker_kwargs: object) -> AppConfig:
        """Build a minimal AppConfig with custom reranker settings."""
        from anteroom.config import AIConfig

        reranker = RerankerConfig(**reranker_kwargs)  # type: ignore[arg-type]
        return AppConfig(ai=AIConfig(base_url="http://test", api_key="test"), reranker=reranker)

    def test_disabled_returns_none(self) -> None:
        config = self._make_config(enabled=False)
        assert create_reranker_service(config) is None

    def test_auto_detect_creates_service(self) -> None:
        config = self._make_config(enabled=None)
        svc = create_reranker_service(config)
        assert isinstance(svc, LocalRerankerService)

    def test_explicit_enabled_creates_service(self) -> None:
        config = self._make_config(enabled=True)
        svc = create_reranker_service(config)
        assert isinstance(svc, LocalRerankerService)

    def test_unsupported_provider_returns_none(self) -> None:
        config = self._make_config(enabled=True, provider="api")
        assert create_reranker_service(config) is None

    def test_custom_model_name(self) -> None:
        config = self._make_config(enabled=True, model="custom/model")
        svc = create_reranker_service(config)
        assert svc is not None
        assert svc.model == "custom/model"

    def test_cache_dir_passed_through(self) -> None:
        config = self._make_config(enabled=True, cache_dir="/vendored/models")
        svc = create_reranker_service(config)
        assert svc is not None
        assert svc._cache_dir == "/vendored/models"

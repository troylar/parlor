"""Tests for embedding service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from anteroom.services.embeddings import (
    EmbeddingService,
    LocalEmbeddingService,
    create_embedding_service,
    get_effective_dimensions,
    get_local_model_dimensions,
)


def _make_embedding_response(embeddings: list[list[float]]) -> MagicMock:
    """Build a mock embeddings API response."""
    response = MagicMock()
    response.data = []
    for i, emb in enumerate(embeddings):
        item = MagicMock()
        item.index = i
        item.embedding = emb
        response.data.append(item)
    return response


class TestEmbeddingService:
    @pytest.mark.asyncio
    async def test_embed_calls_api(self) -> None:
        client = AsyncMock()
        client.embeddings.create = AsyncMock(return_value=_make_embedding_response([[0.1, 0.2, 0.3]]))
        service = EmbeddingService(client, model="test-model", dimensions=3)

        result = await service.embed("hello world")

        assert result == [0.1, 0.2, 0.3]
        client.embeddings.create.assert_called_once_with(
            model="test-model",
            input="hello world",
            dimensions=3,
        )

    @pytest.mark.asyncio
    async def test_embed_returns_none_on_empty_text(self) -> None:
        client = AsyncMock()
        service = EmbeddingService(client)

        assert await service.embed("") is None
        assert await service.embed("   ") is None
        client.embeddings.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_embed_raises_transient_on_rate_limit(self) -> None:
        from openai import RateLimitError

        from anteroom.services.embeddings import EmbeddingTransientError

        client = AsyncMock()
        client.embeddings.create = AsyncMock(
            side_effect=RateLimitError(message="rate limited", response=MagicMock(status_code=429), body=None)
        )
        service = EmbeddingService(client)

        with pytest.raises(EmbeddingTransientError) as exc_info:
            await service.embed("hello world test")
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_embed_raises_permanent_on_not_found(self) -> None:
        from openai import NotFoundError

        from anteroom.services.embeddings import EmbeddingPermanentError

        client = AsyncMock()
        client.embeddings.create = AsyncMock(
            side_effect=NotFoundError(message="not found", response=MagicMock(status_code=404), body=None)
        )
        service = EmbeddingService(client)

        with pytest.raises(EmbeddingPermanentError) as exc_info:
            await service.embed("hello world test")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_embed_batch(self) -> None:
        client = AsyncMock()
        client.embeddings.create = AsyncMock(return_value=_make_embedding_response([[0.1, 0.2], [0.3, 0.4]]))
        service = EmbeddingService(client, model="test-model", dimensions=2)

        results = await service.embed_batch(["hello", "world"], batch_size=10)

        assert len(results) == 2
        assert results[0] == [0.1, 0.2]
        assert results[1] == [0.3, 0.4]

    @pytest.mark.asyncio
    async def test_embed_batch_raises_transient_on_error(self) -> None:
        from openai import APIConnectionError

        from anteroom.services.embeddings import EmbeddingTransientError

        client = AsyncMock()
        client.embeddings.create = AsyncMock(side_effect=APIConnectionError(request=MagicMock()))
        service = EmbeddingService(client, dimensions=2)

        with pytest.raises(EmbeddingTransientError):
            await service.embed_batch(["hello", "world"])

    @pytest.mark.asyncio
    async def test_embed_batch_raises_permanent_on_not_found(self) -> None:
        from openai import NotFoundError

        from anteroom.services.embeddings import EmbeddingPermanentError

        client = AsyncMock()
        client.embeddings.create = AsyncMock(
            side_effect=NotFoundError(message="not found", response=MagicMock(status_code=404), body=None)
        )
        service = EmbeddingService(client, dimensions=2)

        with pytest.raises(EmbeddingPermanentError):
            await service.embed_batch(["hello", "world"])

    @pytest.mark.asyncio
    async def test_embed_batch_multiple_batches(self) -> None:
        client = AsyncMock()
        call_count = 0

        async def mock_create(**kwargs):
            nonlocal call_count
            batch = kwargs["input"]
            resp = _make_embedding_response([[float(call_count)] for _ in batch])
            call_count += 1
            return resp

        client.embeddings.create = mock_create
        service = EmbeddingService(client, dimensions=1)

        results = await service.embed_batch(["a", "b", "c"], batch_size=2)
        assert len(results) == 3
        assert call_count == 2  # 2 batches: [a,b] and [c]

    def test_model_and_dimensions_properties(self) -> None:
        client = AsyncMock()
        service = EmbeddingService(client, model="custom-model", dimensions=768)
        assert service.model == "custom-model"
        assert service.dimensions == 768


class TestEmbeddingServiceTokenRefresh:
    @pytest.mark.asyncio
    async def test_embed_refreshes_token_on_auth_error(self) -> None:
        from openai import AuthenticationError

        client = AsyncMock()
        fresh_client = AsyncMock()
        fresh_client.embeddings.create = AsyncMock(return_value=_make_embedding_response([[0.1, 0.2]]))

        # First call raises auth error, second succeeds after refresh
        client.embeddings.create = AsyncMock(
            side_effect=AuthenticationError(message="invalid", response=MagicMock(status_code=401), body=None)
        )
        client.base_url = "https://api.test/v1"

        service = EmbeddingService(client, dimensions=2)

        # Set up a mock token provider
        provider = MagicMock()
        provider.refresh = MagicMock()
        provider.get_token = MagicMock(return_value="new-token")
        service._set_token_provider(provider)

        # Patch AsyncOpenAI to return fresh_client on re-creation
        with patch("anteroom.services.embeddings.AsyncOpenAI", return_value=fresh_client):
            result = await service.embed("hello world")

        assert result == [0.1, 0.2]
        provider.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_embed_raises_permanent_when_refresh_fails(self) -> None:
        from openai import AuthenticationError

        from anteroom.services.embeddings import EmbeddingPermanentError
        from anteroom.services.token_provider import TokenProviderError

        client = AsyncMock()
        client.embeddings.create = AsyncMock(
            side_effect=AuthenticationError(message="invalid", response=MagicMock(status_code=401), body=None)
        )
        client.base_url = "https://api.test/v1"

        service = EmbeddingService(client, dimensions=2)

        provider = MagicMock()
        provider.refresh = MagicMock(side_effect=TokenProviderError("failed"))
        service._set_token_provider(provider)

        with pytest.raises(EmbeddingPermanentError) as exc_info:
            await service.embed("hello world")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_embed_batch_refreshes_token_on_auth_error(self) -> None:
        from openai import AuthenticationError

        client = AsyncMock()
        fresh_client = AsyncMock()
        fresh_client.embeddings.create = AsyncMock(return_value=_make_embedding_response([[0.1], [0.2]]))

        client.embeddings.create = AsyncMock(
            side_effect=AuthenticationError(message="invalid", response=MagicMock(status_code=401), body=None)
        )
        client.base_url = "https://api.test/v1"

        service = EmbeddingService(client, dimensions=1)

        provider = MagicMock()
        provider.refresh = MagicMock()
        provider.get_token = MagicMock(return_value="new-token")
        service._set_token_provider(provider)

        with patch("anteroom.services.embeddings.AsyncOpenAI", return_value=fresh_client):
            results = await service.embed_batch(["hello", "world"])

        assert results == [[0.1], [0.2]]

    @pytest.mark.asyncio
    async def test_embed_truncates_long_text(self) -> None:
        from anteroom.services.embeddings import MAX_INPUT_TOKENS

        client = AsyncMock()
        client.embeddings.create = AsyncMock(return_value=_make_embedding_response([[0.1]]))
        service = EmbeddingService(client, dimensions=1)

        long_text = "a" * (MAX_INPUT_TOKENS * 4 + 1000)
        await service.embed(long_text)

        call_args = client.embeddings.create.call_args
        assert len(call_args.kwargs["input"]) == MAX_INPUT_TOKENS * 4


class TestCreateEmbeddingService:
    def test_returns_none_when_disabled(self) -> None:
        from anteroom.config import AIConfig, AppConfig, EmbeddingsConfig

        config = AppConfig(
            ai=AIConfig(base_url="https://api.test", api_key="sk-test"),
            embeddings=EmbeddingsConfig(enabled=False),
        )
        assert create_embedding_service(config) is None

    def test_returns_none_when_api_provider_no_key(self) -> None:
        from anteroom.config import AIConfig, AppConfig, EmbeddingsConfig

        config = AppConfig(
            ai=AIConfig(base_url="https://api.test", api_key=""),
            embeddings=EmbeddingsConfig(enabled=True, provider="api", api_key=""),
        )
        assert create_embedding_service(config) is None

    def test_creates_api_service_with_ai_config(self) -> None:
        from anteroom.config import AIConfig, AppConfig, EmbeddingsConfig

        config = AppConfig(
            ai=AIConfig(base_url="https://api.test", api_key="sk-test"),
            embeddings=EmbeddingsConfig(enabled=True, provider="api"),
        )
        service = create_embedding_service(config)
        assert service is not None
        assert isinstance(service, EmbeddingService)
        assert service.model == "text-embedding-3-small"
        assert service.dimensions == 1536

    def test_creates_api_service_with_override_config(self) -> None:
        from anteroom.config import AIConfig, AppConfig, EmbeddingsConfig

        config = AppConfig(
            ai=AIConfig(base_url="https://api.test", api_key="sk-test"),
            embeddings=EmbeddingsConfig(
                enabled=True,
                provider="api",
                base_url="https://embeddings.test",
                api_key="sk-embed",
                model="custom-embed",
                dimensions=768,
            ),
        )
        service = create_embedding_service(config)
        assert service is not None
        assert isinstance(service, EmbeddingService)
        assert service.model == "custom-embed"
        assert service.dimensions == 768

    def test_creates_local_service_by_default(self) -> None:
        from anteroom.config import AIConfig, AppConfig, EmbeddingsConfig

        config = AppConfig(
            ai=AIConfig(base_url="https://api.test", api_key="sk-test"),
            embeddings=EmbeddingsConfig(enabled=True),
        )
        service = create_embedding_service(config)
        assert service is not None
        assert isinstance(service, LocalEmbeddingService)
        assert service.model == "BAAI/bge-small-en-v1.5"
        assert service.dimensions == 384

    def test_creates_local_service_with_custom_model(self) -> None:
        from anteroom.config import AIConfig, AppConfig, EmbeddingsConfig

        config = AppConfig(
            ai=AIConfig(base_url="https://api.test", api_key=""),
            embeddings=EmbeddingsConfig(
                enabled=True,
                provider="local",
                local_model="BAAI/bge-base-en-v1.5",
            ),
        )
        service = create_embedding_service(config)
        assert service is not None
        assert isinstance(service, LocalEmbeddingService)
        assert service.model == "BAAI/bge-base-en-v1.5"
        assert service.dimensions == 768

    def test_local_service_no_api_key_needed(self) -> None:
        from anteroom.config import AIConfig, AppConfig, EmbeddingsConfig

        config = AppConfig(
            ai=AIConfig(base_url="", api_key=""),
            embeddings=EmbeddingsConfig(enabled=True, provider="local"),
        )
        service = create_embedding_service(config)
        assert service is not None
        assert isinstance(service, LocalEmbeddingService)


class TestLocalEmbeddingService:
    def test_model_and_dimensions_properties(self) -> None:
        service = LocalEmbeddingService(model_name="BAAI/bge-small-en-v1.5")
        assert service.model == "BAAI/bge-small-en-v1.5"
        assert service.dimensions == 384

    def test_explicit_dimensions_override(self) -> None:
        service = LocalEmbeddingService(model_name="BAAI/bge-small-en-v1.5", dimensions=512)
        assert service.dimensions == 512

    @pytest.mark.asyncio
    async def test_embed_returns_none_on_empty_text(self) -> None:
        service = LocalEmbeddingService()
        assert await service.embed("") is None
        assert await service.embed("   ") is None

    @pytest.mark.asyncio
    async def test_embed_calls_fastembed(self) -> None:
        service = LocalEmbeddingService(model_name="test-model", dimensions=3)
        mock_model = MagicMock()
        mock_model.embed = MagicMock(return_value=[np.array([0.1, 0.2, 0.3])])
        service._embedding_model = mock_model

        result = await service.embed("hello world")

        assert result == [0.1, 0.2, 0.3]
        mock_model.embed.assert_called_once()

    @pytest.mark.asyncio
    async def test_embed_batch(self) -> None:
        service = LocalEmbeddingService(model_name="test-model", dimensions=2)
        mock_model = MagicMock()
        mock_model.embed = MagicMock(return_value=[np.array([0.1, 0.2]), np.array([0.3, 0.4])])
        service._embedding_model = mock_model

        results = await service.embed_batch(["hello", "world"])

        assert len(results) == 2
        assert results[0] == [0.1, 0.2]
        assert results[1] == [0.3, 0.4]

    @pytest.mark.asyncio
    async def test_embed_batch_skips_empty(self) -> None:
        service = LocalEmbeddingService(model_name="test-model", dimensions=2)
        mock_model = MagicMock()
        mock_model.embed = MagicMock(return_value=[np.array([0.1, 0.2]), np.array([0.3, 0.4])])
        service._embedding_model = mock_model

        results = await service.embed_batch(["hello", "", "world"])

        assert len(results) == 3
        assert results[0] is not None
        assert results[1] is None
        assert results[2] is not None

    @pytest.mark.asyncio
    async def test_embed_raises_permanent_when_fastembed_missing(self) -> None:
        from anteroom.services.embeddings import EmbeddingPermanentError

        service = LocalEmbeddingService()
        with patch.dict("sys.modules", {"fastembed": None}):
            with patch("builtins.__import__", side_effect=ImportError("No module named 'fastembed'")):
                with pytest.raises(EmbeddingPermanentError, match="fastembed is not installed"):
                    await service.embed("hello")

    @pytest.mark.asyncio
    async def test_embed_raises_transient_on_runtime_error(self) -> None:
        from anteroom.services.embeddings import EmbeddingTransientError

        service = LocalEmbeddingService()
        mock_model = MagicMock()
        mock_model.embed = MagicMock(side_effect=RuntimeError("ONNX crash"))
        service._embedding_model = mock_model

        with pytest.raises(EmbeddingTransientError, match="Local embedding failed"):
            await service.embed("hello")

    @pytest.mark.asyncio
    async def test_embed_truncates_long_text(self) -> None:
        from anteroom.services.embeddings import MAX_INPUT_TOKENS

        service = LocalEmbeddingService(dimensions=2)
        mock_model = MagicMock()
        mock_model.embed = MagicMock(return_value=[np.array([0.1, 0.2])])
        service._embedding_model = mock_model

        long_text = "a" * (MAX_INPUT_TOKENS * 4 + 1000)
        await service.embed(long_text)

        call_args = mock_model.embed.call_args[0][0]
        assert len(call_args[0]) == MAX_INPUT_TOKENS * 4


class TestGetLocalModelDimensions:
    def test_known_model(self) -> None:
        assert get_local_model_dimensions("BAAI/bge-small-en-v1.5") == 384
        assert get_local_model_dimensions("BAAI/bge-base-en-v1.5") == 768
        assert get_local_model_dimensions("BAAI/bge-large-en-v1.5") == 1024

    def test_unknown_model_defaults_384(self) -> None:
        assert get_local_model_dimensions("some/unknown-model") == 384


class TestGetEffectiveDimensions:
    def test_explicit_dimensions(self) -> None:
        from anteroom.config import AIConfig, AppConfig, EmbeddingsConfig

        config = AppConfig(
            ai=AIConfig(base_url="https://api.test", api_key="sk-test"),
            embeddings=EmbeddingsConfig(dimensions=512),
        )
        assert get_effective_dimensions(config) == 512

    def test_local_auto_detect(self) -> None:
        from anteroom.config import AIConfig, AppConfig, EmbeddingsConfig

        config = AppConfig(
            ai=AIConfig(base_url="https://api.test", api_key="sk-test"),
            embeddings=EmbeddingsConfig(provider="local", dimensions=0, local_model="BAAI/bge-base-en-v1.5"),
        )
        assert get_effective_dimensions(config) == 768

    def test_api_defaults_to_1536(self) -> None:
        from anteroom.config import AIConfig, AppConfig, EmbeddingsConfig

        config = AppConfig(
            ai=AIConfig(base_url="https://api.test", api_key="sk-test"),
            embeddings=EmbeddingsConfig(provider="api", dimensions=0),
        )
        assert get_effective_dimensions(config) == 1536

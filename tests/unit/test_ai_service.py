"""Tests for AIService error handling and client configuration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from anteroom.config import AIConfig
from anteroom.services.ai_service import AIService


def _make_config(**overrides) -> AIConfig:
    defaults = {
        "base_url": "http://localhost:11434/v1",
        "api_key": "test-key",
        "model": "gpt-4",
        "request_timeout": 120,
        "verify_ssl": True,
    }
    defaults.update(overrides)
    return AIConfig(**defaults)


class TestClientConfiguration:
    def test_default_timeout_applied(self):
        """httpx client must be built with the configured request_timeout as read timeout."""
        config = _make_config(request_timeout=60)
        with patch("anteroom.services.ai_service.AsyncOpenAI") as mock_openai:
            AIService(config)
            call_kwargs = mock_openai.call_args[1]
            http_client = call_kwargs["http_client"]
            assert isinstance(http_client, httpx.AsyncClient)
            assert http_client.timeout.read == 60.0

    def test_connect_timeout_is_10s(self):
        """Connect timeout must be fixed at 10s regardless of request_timeout."""
        config = _make_config(request_timeout=300)
        with patch("anteroom.services.ai_service.AsyncOpenAI") as mock_openai:
            AIService(config)
            http_client = mock_openai.call_args[1]["http_client"]
            assert http_client.timeout.connect == 10.0

    def test_verify_ssl_true_by_default(self):
        """SSL verification must be enabled by default — AsyncClient built with verify=True."""
        config = _make_config(verify_ssl=True)
        with patch("anteroom.services.ai_service.httpx.AsyncClient") as mock_client_cls:
            with patch("anteroom.services.ai_service.AsyncOpenAI"):
                AIService(config)
            _, kwargs = mock_client_cls.call_args
            assert "verify" in kwargs, "verify must be explicitly passed to AsyncClient"
            assert kwargs["verify"] is True

    def test_verify_ssl_false_when_configured(self):
        """SSL verification must be disabled when verify_ssl: false is explicitly set."""
        config = _make_config(verify_ssl=False)
        with patch("anteroom.services.ai_service.httpx.AsyncClient") as mock_client_cls:
            with patch("anteroom.services.ai_service.AsyncOpenAI"):
                AIService(config)
            _, kwargs = mock_client_cls.call_args
            assert kwargs.get("verify") is False


class TestTimeoutErrorHandling:
    @pytest.mark.asyncio
    async def test_timeout_yields_error_event(self):
        """APITimeoutError must be caught and yield a timeout error event with helpful message."""
        from openai import APITimeoutError

        config = _make_config(request_timeout=30)
        service = AIService.__new__(AIService)
        service.config = config
        service._token_provider = None

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=APITimeoutError(request=MagicMock()))
        service.client = mock_client

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        assert len(events) == 1
        assert events[0]["event"] == "error"
        assert events[0]["data"]["code"] == "timeout"
        assert "30s" in events[0]["data"]["message"]
        assert "request_timeout" in events[0]["data"]["message"]

    @pytest.mark.asyncio
    async def test_client_rebuilt_after_stream_chat_timeout(self):
        """_build_client must be called after APITimeoutError in stream_chat to reset the connection pool."""
        from openai import APITimeoutError

        config = _make_config(request_timeout=30)
        service = AIService.__new__(AIService)
        service.config = config
        service._token_provider = None

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=APITimeoutError(request=MagicMock()))
        service.client = mock_client

        with patch.object(service, "_build_client") as mock_build:
            async for _ in service.stream_chat([{"role": "user", "content": "hi"}]):
                pass
            assert mock_build.call_count == 1

    @pytest.mark.asyncio
    async def test_generate_title_rebuilds_client_on_timeout(self):
        """_build_client must be called after APITimeoutError in generate_title."""
        from openai import APITimeoutError

        config = _make_config(request_timeout=30)
        service = AIService.__new__(AIService)
        service.config = config
        service._token_provider = None

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=APITimeoutError(request=MagicMock()))
        service.client = mock_client

        with patch.object(service, "_build_client") as mock_build:
            result = await service.generate_title("hello")
            assert result == "New Conversation"
            assert mock_build.call_count == 1

    @pytest.mark.asyncio
    async def test_validate_connection_rebuilds_client_on_timeout(self):
        """_build_client must be called after APITimeoutError in validate_connection."""
        from openai import APITimeoutError

        config = _make_config(request_timeout=30)
        service = AIService.__new__(AIService)
        service.config = config
        service._token_provider = None

        mock_client = MagicMock()
        mock_client.models.list = AsyncMock(side_effect=APITimeoutError(request=MagicMock()))
        service.client = mock_client

        with patch.object(service, "_build_client") as mock_build:
            ok, msg, models = await service.validate_connection()
            assert ok is False
            assert "timed out" in msg.lower()
            assert mock_build.call_count == 1


def _make_service(config: AIConfig | None = None) -> AIService:
    """Create an AIService with a mock client, bypassing __init__."""
    service = AIService.__new__(AIService)
    service.config = config or _make_config()
    service._token_provider = None
    service.client = MagicMock()
    return service


class TestConnectionErrorHandling:
    @pytest.mark.asyncio
    async def test_stream_chat_yields_connection_error_event(self):
        """APIConnectionError in stream_chat must yield an error event with base_url."""
        from openai import APIConnectionError

        service = _make_service(_make_config(base_url="http://bad-host:1234/v1"))
        service.client.chat.completions.create = AsyncMock(side_effect=APIConnectionError(request=MagicMock()))

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        assert len(events) == 1
        assert events[0]["event"] == "error"
        assert events[0]["data"]["code"] == "connection_error"
        assert "bad-host:1234" in events[0]["data"]["message"]

    @pytest.mark.asyncio
    async def test_stream_chat_rebuilds_client_on_connection_error(self):
        """_build_client must be called after APIConnectionError in stream_chat."""
        from openai import APIConnectionError

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(side_effect=APIConnectionError(request=MagicMock()))

        with patch.object(service, "_build_client") as mock_build:
            async for _ in service.stream_chat([{"role": "user", "content": "hi"}]):
                pass
            assert mock_build.call_count == 1

    @pytest.mark.asyncio
    async def test_validate_connection_returns_connection_error(self):
        """APIConnectionError in validate_connection must return helpful message with base_url."""
        from openai import APIConnectionError

        service = _make_service(_make_config(base_url="http://dead-server:8080/v1"))
        service.client.models.list = AsyncMock(side_effect=APIConnectionError(request=MagicMock()))

        with patch.object(service, "_build_client") as mock_build:
            ok, msg, models = await service.validate_connection()
            assert ok is False
            assert "dead-server:8080" in msg
            assert models == []
            assert mock_build.call_count == 1

    @pytest.mark.asyncio
    async def test_generate_title_returns_fallback_on_connection_error(self):
        """APIConnectionError in generate_title must return 'New Conversation'."""
        from openai import APIConnectionError

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(side_effect=APIConnectionError(request=MagicMock()))

        with patch.object(service, "_build_client") as mock_build:
            result = await service.generate_title("hello")
            assert result == "New Conversation"
            assert mock_build.call_count == 1


class TestAuthErrorHandling:
    @pytest.mark.asyncio
    async def test_auth_error_yields_auth_failed_event(self):
        """AuthenticationError without token provider must yield auth_failed error event."""
        from openai import AuthenticationError

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(
            side_effect=AuthenticationError(message="Invalid API key", response=MagicMock(status_code=401), body={})
        )

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        assert len(events) == 1
        assert events[0]["event"] == "error"
        assert events[0]["data"]["code"] == "auth_failed"
        assert "API key" in events[0]["data"]["message"]

    @pytest.mark.asyncio
    async def test_auth_error_retries_after_token_refresh(self):
        """AuthenticationError with a token provider must attempt refresh and retry."""
        from openai import AuthenticationError

        service = _make_service()
        service._token_provider = MagicMock()

        # First call raises auth error, second call succeeds with a done event
        async def fake_stream():
            yield MagicMock(choices=[MagicMock(delta=MagicMock(content=None, tool_calls=None), finish_reason="stop")])

        service.client.chat.completions.create = AsyncMock(
            side_effect=[
                AuthenticationError(message="Invalid API key", response=MagicMock(status_code=401), body={}),
                fake_stream(),
            ]
        )

        with patch.object(service, "_build_client"):
            events = []
            async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
                events.append(event)

        assert any(e["event"] == "done" for e in events)

    @pytest.mark.asyncio
    async def test_validate_connection_auth_error(self):
        """AuthenticationError in validate_connection must return auth failure message."""
        from openai import AuthenticationError

        service = _make_service()
        service.client.models.list = AsyncMock(
            side_effect=AuthenticationError(message="Invalid API key", response=MagicMock(status_code=401), body={})
        )

        ok, msg, models = await service.validate_connection()
        assert ok is False
        assert "authentication" in msg.lower() or "api key" in msg.lower()


class TestRateLimitErrorHandling:
    @pytest.mark.asyncio
    async def test_rate_limit_yields_rate_limit_event(self):
        """RateLimitError must yield a rate_limit error event."""
        from openai import RateLimitError

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(
            side_effect=RateLimitError(message="Rate limit exceeded", response=MagicMock(status_code=429), body={})
        )

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        assert len(events) == 1
        assert events[0]["event"] == "error"
        assert events[0]["data"]["code"] == "rate_limit"


class TestBadRequestErrorHandling:
    @pytest.mark.asyncio
    async def test_context_length_exceeded_yields_correct_code(self):
        """BadRequestError with context_length_exceeded must yield the correct error code."""
        from openai import BadRequestError

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(
            side_effect=BadRequestError(
                message="context_length_exceeded",
                response=MagicMock(status_code=400),
                body={"error": {"code": "context_length_exceeded"}},
            )
        )

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        assert len(events) == 1
        assert events[0]["event"] == "error"
        assert events[0]["data"]["code"] == "context_length_exceeded"

    @pytest.mark.asyncio
    async def test_other_bad_request_yields_error_message(self):
        """BadRequestError without context_length_exceeded must yield the error message."""
        from openai import BadRequestError

        service = _make_service()
        service.client.chat.completions.create = AsyncMock(
            side_effect=BadRequestError(
                message="Invalid request parameters",
                response=MagicMock(status_code=400),
                body={"error": {"code": "invalid_request"}},
            )
        )

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        assert len(events) == 1
        assert events[0]["event"] == "error"
        assert "Invalid request parameters" in events[0]["data"]["message"]


class TestGenericExceptionHandling:
    @pytest.mark.asyncio
    async def test_generic_exception_yields_internal_error(self):
        """Unexpected exceptions must yield a generic internal error (no details leaked)."""
        service = _make_service()
        service.client.chat.completions.create = AsyncMock(side_effect=RuntimeError("something unexpected"))

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "hi"}]):
            events.append(event)

        assert len(events) == 1
        assert events[0]["event"] == "error"
        assert events[0]["data"]["message"] == "An internal error occurred"
        assert "something unexpected" not in events[0]["data"].get("message", "")

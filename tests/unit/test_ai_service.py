"""Tests for AIService timeout handling and client configuration."""

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

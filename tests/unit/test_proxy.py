"""Tests for the OpenAI-compatible proxy router."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.routers.proxy import router


def _make_app(*, mock_service: MagicMock | None = None) -> FastAPI:
    """Create a minimal FastAPI app with the proxy router."""
    app = FastAPI()
    app.include_router(router, prefix="/v1")

    config = MagicMock()
    config.ai.model = "gpt-4"
    app.state.config = config
    app.state.proxy_ai_service = mock_service

    return app


def _make_mock_service(*, response: MagicMock | None = None, error: Exception | None = None) -> MagicMock:
    """Create a mock AIService with pre-configured completions behavior."""
    service = MagicMock()
    if error:
        service.client.chat.completions.create = AsyncMock(side_effect=error)
    elif response:
        service.client.chat.completions.create = AsyncMock(return_value=response)
    return service


class TestListModels:
    def test_returns_configured_model(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == "gpt-4"
        assert data["data"][0]["object"] == "model"


class TestChatCompletions:
    def test_rejects_non_json_content_type(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/v1/chat/completions", content="hello", headers={"content-type": "text/plain"})
        assert resp.status_code == 415

    def test_rejects_invalid_json(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            content="not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

    def test_rejects_missing_messages(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4"},
        )
        assert resp.status_code == 400
        assert "messages" in resp.json()["error"]["message"]

    def test_rejects_empty_messages(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4", "messages": []},
        )
        assert resp.status_code == 400

    def test_rejects_non_object_body(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json=["not", "an", "object"],
        )
        assert resp.status_code == 400
        assert "JSON object" in resp.json()["error"]["message"]

    def test_rejects_message_without_role(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"content": "Hi"}]},
        )
        assert resp.status_code == 400
        assert "role" in resp.json()["error"]["message"]

    def test_rejects_non_dict_message_item(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": ["not a dict"]},
        )
        assert resp.status_code == 400
        assert "messages[0]" in resp.json()["error"]["message"]

    def test_non_streaming_success(self) -> None:
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "Hello!"}, "index": 0}],
        }
        service = _make_mock_service(response=mock_response)
        app = _make_app(mock_service=service)

        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hi"}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "chatcmpl-123"
        assert data["choices"][0]["message"]["content"] == "Hello!"

    def test_uses_configured_model_as_default(self) -> None:
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {"id": "chatcmpl-123", "choices": []}
        service = _make_mock_service(response=mock_response)
        app = _make_app(mock_service=service)

        client = TestClient(app)
        client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hi"}]},
        )

        call_kwargs = service.client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "gpt-4"

    def test_allows_model_override(self) -> None:
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {"id": "chatcmpl-123", "choices": []}
        service = _make_mock_service(response=mock_response)
        app = _make_app(mock_service=service)

        client = TestClient(app)
        client.post(
            "/v1/chat/completions",
            json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Hi"}]},
        )

        call_kwargs = service.client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "gpt-3.5-turbo"

    def test_forwards_optional_parameters(self) -> None:
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {"id": "chatcmpl-123", "choices": []}
        service = _make_mock_service(response=mock_response)
        app = _make_app(mock_service=service)

        client = TestClient(app)
        client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "Hi"}],
                "temperature": 0.7,
                "max_tokens": 100,
                "top_p": 0.9,
            },
        )

        call_kwargs = service.client.chat.completions.create.call_args[1]
        assert call_kwargs["temperature"] == 0.7
        assert call_kwargs["max_tokens"] == 100
        assert call_kwargs["top_p"] == 0.9

    def test_upstream_error_returns_502(self) -> None:
        service = _make_mock_service(error=Exception("upstream down"))
        app = _make_app(mock_service=service)

        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hi"}]},
        )
        assert resp.status_code == 502
        assert resp.json()["error"]["message"] == "Upstream API error"

    def test_streaming_returns_sse(self) -> None:
        chunk1 = MagicMock()
        chunk1.model_dump.return_value = {
            "id": "chatcmpl-123",
            "object": "chat.completion.chunk",
            "choices": [{"delta": {"content": "Hi"}, "index": 0}],
        }

        async def mock_stream():
            yield chunk1

        service = MagicMock()
        service.client.chat.completions.create = AsyncMock(return_value=mock_stream())
        app = _make_app(mock_service=service)

        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hi"}], "stream": True},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = resp.text
        assert "data: " in body
        assert "chatcmpl-123" in body
        assert "data: [DONE]" in body

    def test_streaming_error_emits_error_chunk(self) -> None:
        async def failing_stream():
            raise RuntimeError("connection lost")
            yield  # noqa: F811 — unreachable but needed to make this an async generator

        service = MagicMock()
        service.client.chat.completions.create = AsyncMock(return_value=failing_stream())
        app = _make_app(mock_service=service)

        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hi"}], "stream": True},
        )
        assert resp.status_code == 200
        body = resp.text
        assert "Upstream stream error" in body
        assert "data: [DONE]" in body


class TestProxyConfig:
    def test_proxy_config_defaults(self) -> None:
        from anteroom.config import ProxyConfig

        config = ProxyConfig()
        assert config.enabled is False
        assert config.allowed_origins == []

    def test_proxy_config_in_app_config(self) -> None:
        from anteroom.config import AIConfig, AppConfig, ProxyConfig

        ai = AIConfig(base_url="http://localhost:8080/v1", api_key="test")
        app_config = AppConfig(ai=ai, proxy=ProxyConfig(enabled=True))
        assert app_config.proxy.enabled is True

    def test_proxy_config_parsed_from_yaml(self) -> None:
        import tempfile
        from pathlib import Path

        from anteroom.config import load_config

        yaml_content = """
ai:
  base_url: http://localhost:8080/v1
  api_key: test-key
proxy:
  enabled: true
  allowed_origins:
    - http://localhost:3000
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            config, _ = load_config(Path(f.name))

        assert config.proxy.enabled is True
        assert config.proxy.allowed_origins == ["http://localhost:3000"]

    def test_proxy_config_env_override(self) -> None:
        import os
        import tempfile
        from pathlib import Path

        from anteroom.config import load_config

        yaml_content = """
ai:
  base_url: http://localhost:8080/v1
  api_key: test-key
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            old = os.environ.get("AI_CHAT_PROXY_ENABLED")
            try:
                os.environ["AI_CHAT_PROXY_ENABLED"] = "true"
                config, _ = load_config(Path(f.name))
            finally:
                if old is None:
                    os.environ.pop("AI_CHAT_PROXY_ENABLED", None)
                else:
                    os.environ["AI_CHAT_PROXY_ENABLED"] = old

        assert config.proxy.enabled is True

    def test_proxy_config_rejects_wildcard_origin(self) -> None:
        import tempfile
        from pathlib import Path

        from anteroom.config import load_config

        yaml_content = """
ai:
  base_url: http://localhost:8080/v1
  api_key: test-key
proxy:
  enabled: true
  allowed_origins:
    - "*"
    - http://localhost:3000
    - not-a-url
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            config, _ = load_config(Path(f.name))

        assert config.proxy.allowed_origins == ["http://localhost:3000"]

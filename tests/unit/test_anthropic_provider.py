"""Tests for AnthropicService provider."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anteroom.config import AIConfig

# Skip tests that need the anthropic package when it's not installed.
# Pure conversion function tests don't need it; service tests do.
try:
    import anthropic  # noqa: F401

    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

requires_anthropic = pytest.mark.skipif(not HAS_ANTHROPIC, reason="anthropic package not installed")


def _make_config(**overrides) -> AIConfig:
    defaults = {
        "base_url": "https://api.anthropic.com",
        "api_key": "test-key",
        "model": "claude-sonnet-4-20250514",
        "provider": "anthropic",
        "max_output_tokens": 4096,
        "request_timeout": 120,
        "retry_max_attempts": 0,
    }
    defaults.update(overrides)
    return AIConfig(**defaults)


class _AsyncEventIterator:
    """Helper that creates a proper async iterator from a list of events."""

    def __init__(self, events):
        self._events = list(events)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._index]
        self._index += 1
        return event


# ---------------------------------------------------------------------------
# Message conversion tests
# ---------------------------------------------------------------------------


class TestConvertMessages:
    def test_system_extracted(self):
        from anteroom.services.anthropic_provider import _convert_messages

        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
        system, anthropic_msgs = _convert_messages(msgs)
        assert system == "You are helpful."
        assert len(anthropic_msgs) == 1
        assert anthropic_msgs[0]["role"] == "user"

    def test_multiple_system_joined(self):
        from anteroom.services.anthropic_provider import _convert_messages

        msgs = [
            {"role": "system", "content": "Part 1"},
            {"role": "system", "content": "Part 2"},
            {"role": "user", "content": "Hi"},
        ]
        system, _ = _convert_messages(msgs)
        assert system == "Part 1\n\nPart 2"

    def test_assistant_with_tool_calls(self):
        from anteroom.services.anthropic_provider import _convert_messages

        msgs = [
            {
                "role": "assistant",
                "content": "Let me search.",
                "tool_calls": [
                    {
                        "id": "tc_1",
                        "function": {"name": "grep", "arguments": '{"pattern": "foo"}'},
                    }
                ],
            }
        ]
        _, anthropic_msgs = _convert_messages(msgs)
        assert len(anthropic_msgs) == 1
        blocks = anthropic_msgs[0]["content"]
        assert len(blocks) == 2
        assert blocks[0]["type"] == "text"
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["id"] == "tc_1"
        assert blocks[1]["input"] == {"pattern": "foo"}

    def test_tool_result_grouped(self):
        from anteroom.services.anthropic_provider import _convert_messages

        msgs = [
            {"role": "tool", "tool_call_id": "tc_1", "content": "result 1"},
            {"role": "tool", "tool_call_id": "tc_2", "content": "result 2"},
        ]
        _, anthropic_msgs = _convert_messages(msgs)
        assert len(anthropic_msgs) == 1
        assert anthropic_msgs[0]["role"] == "user"
        assert len(anthropic_msgs[0]["content"]) == 2

    def test_tool_result_after_user_message_replaces_content(self):
        """Tool results after a user message replace the string content with a list (merges into same user turn)."""
        from anteroom.services.anthropic_provider import _convert_messages

        msgs = [
            {"role": "user", "content": "Do something"},
            {"role": "tool", "tool_call_id": "tc_1", "content": "done"},
        ]
        _, anthropic_msgs = _convert_messages(msgs)
        # Tool result is merged into the existing user message (string → list replacement)
        assert len(anthropic_msgs) == 1
        assert anthropic_msgs[0]["role"] == "user"
        assert anthropic_msgs[0]["content"][0]["type"] == "tool_result"

    def test_user_message_content_preserved(self):
        from anteroom.services.anthropic_provider import _convert_messages

        msgs = [{"role": "user", "content": "Hello world"}]
        _, anthropic_msgs = _convert_messages(msgs)
        assert anthropic_msgs[0]["content"] == "Hello world"

    def test_assistant_no_content(self):
        from anteroom.services.anthropic_provider import _convert_messages

        msgs = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "tc_1", "function": {"name": "bash", "arguments": '{"cmd": "ls"}'}},
                ],
            }
        ]
        _, anthropic_msgs = _convert_messages(msgs)
        assert len(anthropic_msgs) == 1
        blocks = anthropic_msgs[0]["content"]
        assert len(blocks) == 1
        assert blocks[0]["type"] == "tool_use"


# ---------------------------------------------------------------------------
# Tool conversion tests
# ---------------------------------------------------------------------------


class TestConvertTools:
    def test_openai_to_anthropic(self):
        from anteroom.services.anthropic_provider import _convert_tools

        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ]
        result = _convert_tools(openai_tools)
        assert len(result) == 1
        assert result[0]["name"] == "read_file"
        assert result[0]["description"] == "Read a file"
        assert result[0]["input_schema"]["type"] == "object"

    def test_non_function_skipped(self):
        from anteroom.services.anthropic_provider import _convert_tools

        tools = [{"type": "retrieval"}]
        assert _convert_tools(tools) == []


# ---------------------------------------------------------------------------
# Service construction tests
# ---------------------------------------------------------------------------


class TestServiceConstruction:
    @patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", False)
    def test_raises_without_anthropic_package(self):
        from anteroom.services.anthropic_provider import AnthropicService

        config = _make_config()
        with pytest.raises(ImportError, match="anthropic package is not installed"):
            AnthropicService(config)

    @requires_anthropic
    @patch("anteroom.services.anthropic_provider.anthropic")
    @patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True)
    def test_strips_v1_suffix(self, mock_anthropic):
        from anteroom.services.anthropic_provider import AnthropicService

        config = _make_config(base_url="https://api.example.com/v1")
        AnthropicService(config)
        call_kwargs = mock_anthropic.AsyncAnthropic.call_args[1]
        assert call_kwargs["base_url"] == "https://api.example.com"

    @requires_anthropic
    @patch("anteroom.services.anthropic_provider.anthropic")
    @patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True)
    def test_default_base_url_passes_none(self, mock_anthropic):
        from anteroom.services.anthropic_provider import AnthropicService

        config = _make_config(base_url="https://api.anthropic.com")
        AnthropicService(config)
        call_kwargs = mock_anthropic.AsyncAnthropic.call_args[1]
        assert call_kwargs["base_url"] is None


# ---------------------------------------------------------------------------
# stream_chat tests
# ---------------------------------------------------------------------------


def _make_mock_stream_context(events):
    """Create a mock that works as `async with stream as response: async for event in response:`."""
    event_iter = _AsyncEventIterator(events)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=event_iter)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


@requires_anthropic
class TestStreamChat:
    @pytest.fixture
    def mock_service(self):
        with (
            patch("anteroom.services.anthropic_provider.anthropic") as mock_anthropic,
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
        ):
            config = _make_config()
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)
            yield svc, mock_anthropic

    @pytest.mark.asyncio
    async def test_text_streaming(self, mock_service):
        svc, _ = mock_service

        text_delta = MagicMock()
        text_delta.type = "content_block_delta"
        text_delta.delta = MagicMock()
        text_delta.delta.type = "text_delta"
        text_delta.delta.text = "Hello"

        msg_delta = MagicMock()
        msg_delta.type = "message_delta"
        msg_delta.delta = MagicMock()
        msg_delta.delta.stop_reason = "end_turn"
        msg_delta.usage = MagicMock()
        msg_delta.usage.output_tokens = 5

        svc.client.messages.stream = MagicMock(return_value=_make_mock_stream_context([text_delta, msg_delta]))

        events = []
        async for event in svc.stream_chat([{"role": "user", "content": "Hi"}]):
            events.append(event)

        event_types = [e["event"] for e in events]
        assert "phase" in event_types
        assert "token" in event_types
        assert "done" in event_types

    @pytest.mark.asyncio
    async def test_tool_call_streaming(self, mock_service):
        svc, _ = mock_service

        block_start = MagicMock()
        block_start.type = "content_block_start"
        block_start.content_block = MagicMock()
        block_start.content_block.type = "tool_use"
        block_start.content_block.id = "tool_1"
        block_start.content_block.name = "read_file"

        json_delta = MagicMock()
        json_delta.type = "content_block_delta"
        json_delta.delta = MagicMock()
        json_delta.delta.type = "input_json_delta"
        json_delta.delta.partial_json = '{"path": "/tmp/test"}'

        msg_delta = MagicMock()
        msg_delta.type = "message_delta"
        msg_delta.delta = MagicMock()
        msg_delta.delta.stop_reason = "tool_use"
        msg_delta.usage = MagicMock()
        msg_delta.usage.output_tokens = 10

        svc.client.messages.stream = MagicMock(
            return_value=_make_mock_stream_context([block_start, json_delta, msg_delta])
        )

        events = []
        async for event in svc.stream_chat(
            [{"role": "user", "content": "Read a file"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read a file",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                }
            ],
        ):
            events.append(event)

        tool_calls = [e for e in events if e["event"] == "tool_call"]
        assert len(tool_calls) == 1
        assert tool_calls[0]["data"]["function_name"] == "read_file"
        assert tool_calls[0]["data"]["arguments"] == {"path": "/tmp/test"}

    @pytest.mark.asyncio
    async def test_cancel_before_stream(self, mock_service):
        svc, _ = mock_service

        cancel = asyncio.Event()
        cancel.set()

        events = []
        async for event in svc.stream_chat([{"role": "user", "content": "Hi"}], cancel_event=cancel):
            events.append(event)

        assert events == []

    @pytest.mark.asyncio
    async def test_message_start_captures_usage(self, mock_service):
        svc, _ = mock_service

        msg_start = MagicMock()
        msg_start.type = "message_start"
        msg_start.message = MagicMock()
        msg_start.message.usage = MagicMock()
        msg_start.message.usage.input_tokens = 100

        msg_delta = MagicMock()
        msg_delta.type = "message_delta"
        msg_delta.delta = MagicMock()
        msg_delta.delta.stop_reason = "end_turn"
        msg_delta.usage = MagicMock()
        msg_delta.usage.output_tokens = 50

        svc.client.messages.stream = MagicMock(return_value=_make_mock_stream_context([msg_start, msg_delta]))

        events = []
        async for event in svc.stream_chat([{"role": "user", "content": "Hi"}]):
            events.append(event)

        usage_events = [e for e in events if e["event"] == "usage"]
        assert len(usage_events) == 1
        assert usage_events[0]["data"]["prompt_tokens"] == 100
        assert usage_events[0]["data"]["completion_tokens"] == 50


# ---------------------------------------------------------------------------
# generate_title tests
# ---------------------------------------------------------------------------


@requires_anthropic
class TestGenerateTitle:
    @pytest.mark.asyncio
    async def test_returns_title(self):
        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
        ):
            config = _make_config()
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)

            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="Project Setup Help")]
            svc.client.messages.create = AsyncMock(return_value=mock_response)

            title = await svc.generate_title("Help me set up my project")
            assert title == "Project Setup Help"

    @pytest.mark.asyncio
    async def test_returns_default_on_error(self):
        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
        ):
            config = _make_config()
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)
            svc.client.messages.create = AsyncMock(side_effect=Exception("API error"))

            title = await svc.generate_title("Hello")
            assert title == "New Conversation"


# ---------------------------------------------------------------------------
# validate_connection tests
# ---------------------------------------------------------------------------


@requires_anthropic
class TestValidateConnection:
    @pytest.mark.asyncio
    async def test_success(self):
        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
        ):
            config = _make_config()
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)

            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="Hi")]
            svc.client.messages.create = AsyncMock(return_value=mock_response)

            ok, msg, models = await svc.validate_connection()
            assert ok is True
            assert "Connected" in msg
            assert config.model in models


# ---------------------------------------------------------------------------
# complete() tests
# ---------------------------------------------------------------------------


@requires_anthropic
class TestComplete:
    @pytest.mark.asyncio
    async def test_returns_text(self):
        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
        ):
            config = _make_config()
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)

            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="Summary of conversation")]
            svc.client.messages.create = AsyncMock(return_value=mock_response)

            result = await svc.complete([{"role": "user", "content": "Summarize this"}])
            assert result == "Summary of conversation"

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
        ):
            config = _make_config()
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)
            svc.client.messages.create = AsyncMock(side_effect=Exception("fail"))

            result = await svc.complete([{"role": "user", "content": "test"}])
            assert result is None


# ---------------------------------------------------------------------------
# Factory routing tests
# ---------------------------------------------------------------------------


class TestCreateAiServiceFactory:
    @requires_anthropic
    @patch("anteroom.services.anthropic_provider.anthropic")
    @patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True)
    def test_anthropic_provider_selected(self, mock_anthropic):
        from anteroom.services.ai_service import create_ai_service
        from anteroom.services.anthropic_provider import AnthropicService

        config = _make_config(provider="anthropic")
        svc = create_ai_service(config)
        assert isinstance(svc, AnthropicService)

    def test_openai_provider_default(self):
        from anteroom.services.ai_service import AIService, create_ai_service

        config = _make_config(provider="openai", base_url="http://localhost:11434/v1")
        with patch("anteroom.services.ai_service.AsyncOpenAI"):
            svc = create_ai_service(config)
        assert isinstance(svc, AIService)


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


@requires_anthropic
class TestValidateConnectionErrors:
    @pytest.mark.asyncio
    async def test_generic_error(self):
        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
        ):
            config = _make_config()
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)
            svc.client.messages.create = AsyncMock(side_effect=Exception("connection refused"))

            ok, msg, models = await svc.validate_connection()
            assert ok is False
            assert models == []

    @pytest.mark.asyncio
    async def test_empty_response_content(self):
        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
        ):
            config = _make_config()
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)
            mock_response = MagicMock()
            mock_response.content = []
            svc.client.messages.create = AsyncMock(return_value=mock_response)

            ok, msg, models = await svc.validate_connection()
            assert ok is True


@requires_anthropic
class TestStreamChatKwargs:
    @pytest.mark.asyncio
    async def test_temperature_and_top_p_forwarded(self):
        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
        ):
            config = _make_config(temperature=0.7, top_p=0.9)
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)

            msg_delta = MagicMock()
            msg_delta.type = "message_delta"
            msg_delta.delta = MagicMock()
            msg_delta.delta.stop_reason = "end_turn"
            msg_delta.usage = MagicMock()
            msg_delta.usage.output_tokens = 1

            svc.client.messages.stream = MagicMock(return_value=_make_mock_stream_context([msg_delta]))

            events = []
            async for event in svc.stream_chat([{"role": "user", "content": "Hi"}]):
                events.append(event)

            call_kwargs = svc.client.messages.stream.call_args[1]
            assert call_kwargs["temperature"] == 0.7
            assert call_kwargs["top_p"] == 0.9

    @pytest.mark.asyncio
    async def test_tools_converted_and_passed(self):
        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
        ):
            config = _make_config()
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)

            msg_delta = MagicMock()
            msg_delta.type = "message_delta"
            msg_delta.delta = MagicMock()
            msg_delta.delta.stop_reason = "end_turn"
            msg_delta.usage = MagicMock()
            msg_delta.usage.output_tokens = 1

            svc.client.messages.stream = MagicMock(return_value=_make_mock_stream_context([msg_delta]))

            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "description": "Run a command",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]

            events = []
            async for event in svc.stream_chat([{"role": "user", "content": "Hi"}], tools=tools):
                events.append(event)

            call_kwargs = svc.client.messages.stream.call_args[1]
            assert "tools" in call_kwargs
            assert call_kwargs["tools"][0]["name"] == "bash"


@requires_anthropic
class TestTokenRefresh:
    def test_try_refresh_without_provider_returns_false(self):
        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
        ):
            config = _make_config()
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)
            assert svc._try_refresh_token() is False

    def test_resolve_api_key_from_config(self):
        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
        ):
            config = _make_config(api_key="my-key")
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)
            assert svc._resolve_api_key() == "my-key"


@requires_anthropic
class TestCompleteEdgeCases:
    @pytest.mark.asyncio
    async def test_complete_empty_response_returns_none(self):
        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
        ):
            config = _make_config()
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)
            mock_response = MagicMock()
            mock_response.content = []
            svc.client.messages.create = AsyncMock(return_value=mock_response)

            result = await svc.complete([{"role": "user", "content": "test"}])
            assert result is None

    @pytest.mark.asyncio
    async def test_complete_passes_max_tokens(self):
        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
        ):
            config = _make_config()
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="ok")]
            svc.client.messages.create = AsyncMock(return_value=mock_response)

            await svc.complete([{"role": "user", "content": "test"}], max_completion_tokens=500)
            call_kwargs = svc.client.messages.create.call_args[1]
            assert call_kwargs["max_tokens"] == 500


@requires_anthropic
class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_generic_error_yields_error_event(self):
        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
        ):
            config = _make_config()
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)

            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("unexpected"))
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            svc.client.messages.stream = MagicMock(return_value=mock_ctx)

            events = []
            async for event in svc.stream_chat([{"role": "user", "content": "Hi"}]):
                events.append(event)

            error_events = [e for e in events if e["event"] == "error"]
            assert len(error_events) == 1
            assert error_events[0]["data"]["retryable"] is False

    @pytest.mark.asyncio
    async def test_stream_ends_without_stop_yields_done(self, mock_service=None):
        """If the stream ends without an explicit stop_reason, we still get a done event."""
        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
        ):
            config = _make_config()
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)

            text_delta = MagicMock()
            text_delta.type = "content_block_delta"
            text_delta.delta = MagicMock()
            text_delta.delta.type = "text_delta"
            text_delta.delta.text = "Hello"

            svc.client.messages.stream = MagicMock(return_value=_make_mock_stream_context([text_delta]))

            events = []
            async for event in svc.stream_chat([{"role": "user", "content": "Hi"}]):
                events.append(event)

            event_types = [e["event"] for e in events]
            assert "done" in event_types

    @pytest.mark.asyncio
    async def test_extra_system_prompt_prepended(self):
        """extra_system_prompt is prepended to the system prompt."""
        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
        ):
            config = _make_config()
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)

            msg_delta = MagicMock()
            msg_delta.type = "message_delta"
            msg_delta.delta = MagicMock()
            msg_delta.delta.stop_reason = "end_turn"
            msg_delta.usage = MagicMock()
            msg_delta.usage.output_tokens = 1

            svc.client.messages.stream = MagicMock(return_value=_make_mock_stream_context([msg_delta]))

            events = []
            async for event in svc.stream_chat(
                [{"role": "user", "content": "Hi"}],
                extra_system_prompt="Be concise.",
            ):
                events.append(event)

            call_kwargs = svc.client.messages.stream.call_args[1]
            assert "Be concise." in call_kwargs["system"]

    @pytest.mark.asyncio
    async def test_bad_request_context_length(self):
        """AnthropicBadRequestError with context-related message yields context_length_exceeded."""
        import httpx
        from anthropic import BadRequestError as RealBadRequestError

        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
            patch(
                "anteroom.services.anthropic_provider.AnthropicBadRequestError",
                RealBadRequestError,
            ),
        ):
            config = _make_config()
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)

            exc = RealBadRequestError(
                message="prompt is too long for the model context window",
                response=httpx.Response(400, request=httpx.Request("POST", "https://test")),
                body={"type": "error", "error": {"type": "invalid_request_error", "message": "prompt is too long"}},
            )

            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(side_effect=exc)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            svc.client.messages.stream = MagicMock(return_value=mock_ctx)

            events = []
            async for event in svc.stream_chat([{"role": "user", "content": "Hi"}]):
                events.append(event)

            error_events = [e for e in events if e["event"] == "error"]
            assert len(error_events) == 1
            assert error_events[0]["data"]["code"] == "context_length_exceeded"

    @pytest.mark.asyncio
    async def test_bad_request_too_many_tools(self):
        """AnthropicBadRequestError with too-many-tools message yields too_many_tools."""
        import httpx
        from anthropic import BadRequestError as RealBadRequestError

        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
            patch(
                "anteroom.services.anthropic_provider.AnthropicBadRequestError",
                RealBadRequestError,
            ),
        ):
            config = _make_config()
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)

            exc = RealBadRequestError(
                message="too many tool definitions provided",
                response=httpx.Response(400, request=httpx.Request("POST", "https://test")),
                body={"type": "error", "error": {"type": "invalid_request_error", "message": "too many tools"}},
            )

            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(side_effect=exc)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            svc.client.messages.stream = MagicMock(return_value=mock_ctx)

            events = []
            async for event in svc.stream_chat([{"role": "user", "content": "Hi"}]):
                events.append(event)

            error_events = [e for e in events if e["event"] == "error"]
            assert len(error_events) == 1
            assert error_events[0]["data"]["code"] == "too_many_tools"

    @pytest.mark.asyncio
    async def test_bad_request_generic_surfaces_sanitized_message(self):
        """Generic AnthropicBadRequestError must yield sanitized provider message."""
        import httpx
        from anthropic import BadRequestError as RealBadRequestError

        with (
            patch("anteroom.services.anthropic_provider.anthropic"),
            patch("anteroom.services.anthropic_provider.HAS_ANTHROPIC", True),
            patch(
                "anteroom.services.anthropic_provider.AnthropicBadRequestError",
                RealBadRequestError,
            ),
        ):
            config = _make_config()
            from anteroom.services.anthropic_provider import AnthropicService

            svc = AnthropicService(config)

            exc = RealBadRequestError(
                message="The model was unable to complete inference due to an internal error",
                response=httpx.Response(400, request=httpx.Request("POST", "https://test")),
                body={
                    "type": "error",
                    "error": {
                        "type": "server_error",
                        "message": "The model was unable to complete inference due to an internal error",
                    },
                },
            )

            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(side_effect=exc)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            svc.client.messages.stream = MagicMock(return_value=mock_ctx)

            events = []
            async for event in svc.stream_chat([{"role": "user", "content": "Hi"}]):
                events.append(event)

            error_events = [e for e in events if e["event"] == "error"]
            assert len(error_events) == 1
            assert "unable to complete inference" in error_events[0]["data"]["message"]
            assert error_events[0]["data"]["code"] == "bad_request"

"""Tests for canvas streaming event pipeline: ai_service -> agent_loop -> chat SSE."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anteroom.routers.chat import _CANVAS_STREAMING_TOOLS, _extract_streaming_content
from anteroom.services.agent_loop import run_agent_loop

# --- Helpers ---


@dataclass
class FakeDelta:
    content: str | None = None
    tool_calls: list[Any] | None = None


@dataclass
class FakeToolCallFunction:
    name: str | None = None
    arguments: str | None = None


@dataclass
class FakeToolCallDelta:
    index: int = 0
    id: str | None = None
    function: FakeToolCallFunction | None = None


@dataclass
class FakeChoice:
    delta: FakeDelta | None = None
    finish_reason: str | None = None


@dataclass
class FakeChunk:
    choices: list[FakeChoice] | None = None


# --- ai_service tool_call_args_delta tests ---


class TestAiServiceToolCallArgsDelta:
    """Test that ai_service emits tool_call_args_delta events during streaming."""

    @pytest.mark.asyncio
    async def test_emits_args_delta_for_each_chunk(self) -> None:
        from anteroom.services.ai_service import AIService

        chunks = [
            FakeChunk(
                choices=[
                    FakeChoice(
                        delta=FakeDelta(
                            tool_calls=[
                                FakeToolCallDelta(
                                    index=0,
                                    id="call_1",
                                    function=FakeToolCallFunction(name="create_canvas"),
                                ),
                            ]
                        )
                    )
                ]
            ),
            FakeChunk(
                choices=[
                    FakeChoice(
                        delta=FakeDelta(
                            tool_calls=[
                                FakeToolCallDelta(
                                    index=0,
                                    function=FakeToolCallFunction(arguments='{"title":'),
                                ),
                            ]
                        )
                    )
                ]
            ),
            FakeChunk(
                choices=[
                    FakeChoice(
                        delta=FakeDelta(
                            tool_calls=[
                                FakeToolCallDelta(
                                    index=0,
                                    function=FakeToolCallFunction(arguments=' "Doc", '),
                                ),
                            ]
                        )
                    )
                ]
            ),
            FakeChunk(
                choices=[
                    FakeChoice(
                        delta=FakeDelta(
                            tool_calls=[
                                FakeToolCallDelta(
                                    index=0,
                                    function=FakeToolCallFunction(arguments='"content": "Hello"}'),
                                ),
                            ]
                        )
                    )
                ]
            ),
            FakeChunk(choices=[FakeChoice(delta=FakeDelta(), finish_reason="tool_calls")]),
        ]

        async def fake_stream():
            for c in chunks:
                yield c

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=fake_stream())

        config = MagicMock()
        config.model = "test-model"
        config.system_prompt = None
        config.retry_max_attempts = 0
        config.first_token_timeout = 30

        with patch.object(AIService, "_build_client"):
            service = AIService(config)
        service.client = mock_client

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "test"}]):
            events.append(event)

        delta_events = [e for e in events if e["event"] == "tool_call_args_delta"]
        assert len(delta_events) == 3
        assert delta_events[0]["data"]["tool_name"] == "create_canvas"
        assert delta_events[0]["data"]["delta"] == '{"title":'
        assert delta_events[1]["data"]["delta"] == ' "Doc", '
        assert delta_events[2]["data"]["delta"] == '"content": "Hello"}'

    @pytest.mark.asyncio
    async def test_no_delta_without_arguments(self) -> None:
        from anteroom.services.ai_service import AIService

        chunks = [
            FakeChunk(
                choices=[
                    FakeChoice(
                        delta=FakeDelta(
                            tool_calls=[
                                FakeToolCallDelta(
                                    index=0,
                                    id="call_1",
                                    function=FakeToolCallFunction(name="create_canvas"),
                                ),
                            ]
                        )
                    )
                ]
            ),
            FakeChunk(choices=[FakeChoice(delta=FakeDelta(), finish_reason="stop")]),
        ]

        async def fake_stream():
            for c in chunks:
                yield c

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=fake_stream())

        config = MagicMock()
        config.model = "test-model"
        config.system_prompt = None
        config.retry_max_attempts = 0
        config.first_token_timeout = 30

        with patch.object(AIService, "_build_client"):
            service = AIService(config)
        service.client = mock_client

        events = []
        async for event in service.stream_chat([{"role": "user", "content": "test"}]):
            events.append(event)

        delta_events = [e for e in events if e["event"] == "tool_call_args_delta"]
        assert len(delta_events) == 0


# --- agent_loop forwarding tests ---


class TestAgentLoopForwardsArgsDelta:
    """Test that agent_loop forwards tool_call_args_delta events from ai_service."""

    @pytest.mark.asyncio
    async def test_forwards_tool_call_args_delta(self) -> None:
        call_count = 0

        async def fake_stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield {
                    "event": "tool_call_args_delta",
                    "data": {"index": 0, "tool_name": "create_canvas", "delta": '{"content": "'},
                }
                yield {
                    "event": "tool_call_args_delta",
                    "data": {"index": 0, "tool_name": "create_canvas", "delta": "Hello"},
                }
                yield {
                    "event": "tool_call",
                    "data": {
                        "id": "call_1",
                        "function_name": "create_canvas",
                        "arguments": {"content": "Hello", "title": "T"},
                    },
                }
            else:
                yield {"event": "done", "data": {}}

        mock_ai = MagicMock()
        mock_ai.stream_chat = fake_stream_chat

        async def tool_executor(name, args):
            return {"status": "created", "id": "canvas-1"}

        events = []
        async for event in run_agent_loop(
            ai_service=mock_ai,
            messages=[{"role": "user", "content": "test"}],
            tool_executor=tool_executor,
            tools_openai=[{"type": "function", "function": {"name": "create_canvas"}}],
        ):
            events.append(event)

        delta_events = [e for e in events if e.kind == "tool_call_args_delta"]
        assert len(delta_events) == 2
        assert delta_events[0].data["tool_name"] == "create_canvas"
        assert delta_events[0].data["delta"] == '{"content": "'
        assert delta_events[1].data["delta"] == "Hello"

    @pytest.mark.asyncio
    async def test_non_canvas_delta_still_forwarded(self) -> None:
        call_count = 0

        async def fake_stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield {
                    "event": "tool_call_args_delta",
                    "data": {"index": 0, "tool_name": "read_file", "delta": '{"path":'},
                }
                yield {
                    "event": "tool_call",
                    "data": {
                        "id": "call_1",
                        "function_name": "read_file",
                        "arguments": {"path": "/tmp/x"},
                    },
                }
            else:
                yield {"event": "done", "data": {}}

        mock_ai = MagicMock()
        mock_ai.stream_chat = fake_stream_chat

        async def tool_executor(name, args):
            return {"content": "file data"}

        events = []
        async for event in run_agent_loop(
            ai_service=mock_ai,
            messages=[{"role": "user", "content": "test"}],
            tool_executor=tool_executor,
            tools_openai=[{"type": "function", "function": {"name": "read_file"}}],
        ):
            events.append(event)

        delta_events = [e for e in events if e.kind == "tool_call_args_delta"]
        assert len(delta_events) == 1
        assert delta_events[0].data["tool_name"] == "read_file"

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_with_args_delta(self) -> None:
        call_count = 0

        async def fake_stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First tool call
                yield {
                    "event": "tool_call_args_delta",
                    "data": {"index": 0, "tool_name": "create_canvas", "delta": '{"title":'},
                }
                yield {
                    "event": "tool_call_args_delta",
                    "data": {"index": 0, "tool_name": "create_canvas", "delta": ' "Doc1"'},
                }
                # Second tool call
                yield {
                    "event": "tool_call_args_delta",
                    "data": {"index": 1, "tool_name": "read_file", "delta": '{"path":'},
                }
                yield {
                    "event": "tool_call_args_delta",
                    "data": {"index": 1, "tool_name": "read_file", "delta": ' "/tmp/x"}'},
                }
                # Both tool calls complete
                yield {
                    "event": "tool_call",
                    "data": {
                        "id": "call_1",
                        "function_name": "create_canvas",
                        "arguments": {"title": "Doc1", "content": ""},
                    },
                }
                yield {
                    "event": "tool_call",
                    "data": {
                        "id": "call_2",
                        "function_name": "read_file",
                        "arguments": {"path": "/tmp/x"},
                    },
                }
            else:
                yield {"event": "done", "data": {}}

        mock_ai = MagicMock()
        mock_ai.stream_chat = fake_stream_chat

        async def tool_executor(name, args):
            if name == "create_canvas":
                return {"status": "created", "id": "canvas-1"}
            return {"content": "file data"}

        events = []
        async for event in run_agent_loop(
            ai_service=mock_ai,
            messages=[{"role": "user", "content": "test"}],
            tool_executor=tool_executor,
            tools_openai=[
                {"type": "function", "function": {"name": "create_canvas"}},
                {"type": "function", "function": {"name": "read_file"}},
            ],
        ):
            events.append(event)

        delta_events = [e for e in events if e.kind == "tool_call_args_delta"]
        assert len(delta_events) == 4

        # Verify index 0 events
        idx_0_events = [e for e in delta_events if e.data["index"] == 0]
        assert len(idx_0_events) == 2
        assert idx_0_events[0].data["tool_name"] == "create_canvas"
        assert idx_0_events[0].data["delta"] == '{"title":'
        assert idx_0_events[1].data["delta"] == ' "Doc1"'

        # Verify index 1 events
        idx_1_events = [e for e in delta_events if e.data["index"] == 1]
        assert len(idx_1_events) == 2
        assert idx_1_events[0].data["tool_name"] == "read_file"
        assert idx_1_events[0].data["delta"] == '{"path":'
        assert idx_1_events[1].data["delta"] == ' "/tmp/x"}'


# --- Message queue tests ---


class TestAgentLoopMessageQueue:
    """Test message queue handling in agent_loop."""

    @pytest.mark.asyncio
    async def test_processes_queued_message_after_done(self) -> None:
        call_count = 0

        async def fake_stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield {"event": "token", "data": {"content": "Response to first message"}}
                yield {"event": "done", "data": {}}
            elif call_count == 2:
                yield {"event": "token", "data": {"content": "Response to queued message"}}
                yield {"event": "done", "data": {}}

        mock_ai = MagicMock()
        mock_ai.stream_chat = fake_stream_chat

        async def tool_executor(name, args):
            return {}

        # Create queue with a pending message
        message_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await message_queue.put({"role": "user", "content": "queued message"})

        events = []
        async for event in run_agent_loop(
            ai_service=mock_ai,
            messages=[{"role": "user", "content": "first message"}],
            tool_executor=tool_executor,
            tools_openai=[],
            message_queue=message_queue,
        ):
            events.append(event)

        # Verify we got a queued_message event
        queued_events = [e for e in events if e.kind == "queued_message"]
        assert len(queued_events) == 1
        assert queued_events[0].data["content"] == "queued message"

        # Verify stream_chat was called twice
        assert call_count == 2

        # Verify both done events were emitted
        done_events = [e for e in events if e.kind == "done"]
        assert len(done_events) == 2

    @pytest.mark.asyncio
    async def test_returns_when_queue_empty(self) -> None:
        call_count = 0

        async def fake_stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            nonlocal call_count
            call_count += 1
            yield {"event": "token", "data": {"content": "Response"}}
            yield {"event": "done", "data": {}}

        mock_ai = MagicMock()
        mock_ai.stream_chat = fake_stream_chat

        async def tool_executor(name, args):
            return {}

        # Create empty queue
        message_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        events = []
        async for event in run_agent_loop(
            ai_service=mock_ai,
            messages=[{"role": "user", "content": "test"}],
            tool_executor=tool_executor,
            tools_openai=[],
            message_queue=message_queue,
        ):
            events.append(event)

        # Verify stream_chat was only called once
        assert call_count == 1

        # Verify no queued_message events
        queued_events = [e for e in events if e.kind == "queued_message"]
        assert len(queued_events) == 0

        # Verify single done event
        done_events = [e for e in events if e.kind == "done"]
        assert len(done_events) == 1


# --- Canvas SSE event emission tests ---


class TestCanvasSSEEventEmission:
    """Test the canvas streaming SSE event logic in the chat event generator."""

    def test_canvas_streaming_tools_constant(self) -> None:
        assert "create_canvas" in _CANVAS_STREAMING_TOOLS
        assert "update_canvas" in _CANVAS_STREAMING_TOOLS
        assert "patch_canvas" not in _CANVAS_STREAMING_TOOLS

    def test_extract_content_produces_incrementing_deltas(self) -> None:
        """Simulate the accumulation pattern used in chat.py event_generator."""
        chunks = [
            '{"title": "T", ',
            '"content": "He',
            "llo",
            " world",
            '"}',
        ]

        accum = ""
        sent = 0
        deltas: list[str] = []
        stream_started = False

        for chunk in chunks:
            accum += chunk
            content = _extract_streaming_content(accum)
            if content is not None and len(content) > sent:
                if not stream_started:
                    stream_started = True
                delta = content[sent:]
                sent = len(content)
                deltas.append(delta)

        assert stream_started
        assert "".join(deltas) == "Hello world"

    def test_no_stream_for_non_canvas_tool(self) -> None:
        tool_name = "read_file"
        assert tool_name not in _CANVAS_STREAMING_TOOLS

    def test_extract_returns_none_before_content_key_for_title_only(self) -> None:
        accum = '{"title": "My Document"'
        assert _extract_streaming_content(accum) is None

    def test_stream_start_emitted_once(self) -> None:
        """canvas_stream_start should only be emitted once per tool call."""
        chunks = ['"content": "a', "b", "c"]
        accum = "{"
        started_count = 0
        sent = 0

        for chunk in chunks:
            accum += chunk
            content = _extract_streaming_content(accum)
            if content is not None and len(content) > sent:
                if sent == 0:
                    started_count += 1
                sent = len(content)

        assert started_count == 1


class TestCanvasSSEEventStructure:
    """Test the canvas SSE event structure and state management."""

    def test_canvas_tool_args_not_accumulated_for_non_canvas_tool(self) -> None:
        """Non-canvas tools like read_file don't have a 'content' key to stream."""
        # Simulate tool_call_args_delta for read_file
        accum = '{"path": "/tmp/x"}'
        content = _extract_streaming_content(accum)
        assert content is None

    def test_canvas_stream_state_cleared_on_new_tool_call(self) -> None:
        """Simulate the state clearing that happens on tool_call_start event."""
        # First tool call accumulation
        canvas_args_accum = {0: ""}
        canvas_content_sent = {0: 0}
        canvas_stream_started: set[int] = set()

        # Accumulate some content for index 0
        canvas_args_accum[0] = '{"title": "Doc", "content": "Hello"'
        content = _extract_streaming_content(canvas_args_accum[0])
        assert content == "Hello"
        canvas_content_sent[0] = len(content)
        canvas_stream_started.add(0)

        # Simulate tool_call_start event - clear state
        canvas_args_accum.clear()
        canvas_content_sent.clear()
        canvas_stream_started.clear()

        # New tool call starts fresh
        assert len(canvas_args_accum) == 0
        assert len(canvas_content_sent) == 0
        assert len(canvas_stream_started) == 0

        # New accumulation starts from zero
        canvas_args_accum[0] = '{"title": "Doc2", "content": "World"'
        content = _extract_streaming_content(canvas_args_accum[0])
        assert content == "World"
        prev_len = canvas_content_sent.get(0, 0)
        assert prev_len == 0
        assert len(content) > prev_len


# --- Context Recovery Tests ---


class TestTruncateLargeToolOutputs:
    """Test _truncate_large_tool_outputs function from agent_loop."""

    def test_truncates_oversized_tool_output(self) -> None:
        from anteroom.services.agent_loop import _truncate_large_tool_outputs

        # Create a tool output message with 3000+ chars
        large_content = "x" * 3500
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {"name": "grep", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_123",
                "content": large_content,
            },
        ]

        result = _truncate_large_tool_outputs(messages)

        assert result is True
        assert len(messages[1]["content"]) < len(large_content)
        assert "TRUNCATED" in messages[1]["content"]
        assert "3,500 chars" in messages[1]["content"]

    def test_leaves_small_tool_output_alone(self) -> None:
        from anteroom.services.agent_loop import _truncate_large_tool_outputs

        small_content = "x" * 1500
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_456",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_456",
                "content": small_content,
            },
        ]

        original_content = messages[1]["content"]
        result = _truncate_large_tool_outputs(messages)

        assert result is False
        assert messages[1]["content"] == original_content

    def test_includes_tool_name_in_truncation_notice(self) -> None:
        from anteroom.services.agent_loop import _truncate_large_tool_outputs

        large_content = "y" * 2500
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_789",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_789",
                "content": large_content,
            },
        ]

        result = _truncate_large_tool_outputs(messages)

        assert result is True
        assert "'bash'" in messages[1]["content"]
        assert "TRUNCATED" in messages[1]["content"]

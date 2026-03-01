"""Tests for consecutive text-only loop limit in agent_loop (#679)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from anteroom.services.agent_loop import AgentEvent, run_agent_loop

# -- Helpers --


def _make_stream_events(
    content: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build a list of stream events that ai_service.stream_chat would yield."""
    events: list[dict[str, Any]] = []
    if content:
        events.append({"event": "token", "data": {"content": content}})
    for tc in tool_calls or []:
        events.append({"event": "tool_call", "data": tc})
    events.append({"event": "done", "data": {}})
    return events


def _mock_ai_service(*rounds: list[dict[str, Any]]) -> AsyncMock:
    """Create a mock AIService that yields different stream events per call."""
    service = AsyncMock()
    call_count = 0

    async def _stream_chat(
        messages: Any, tools: Any = None, cancel_event: Any = None, extra_system_prompt: Any = None
    ) -> Any:
        nonlocal call_count
        idx = min(call_count, len(rounds) - 1)
        call_count += 1
        for event in rounds[idx]:
            yield event

    service.stream_chat = _stream_chat
    return service


def _tc(tool_id: str, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": tool_id, "function_name": name, "arguments": args or {}}


async def _collect_events(gen: Any) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    async for e in gen:
        events.append(e)
    return events


class TestConsecutiveTextOnlyLimit:
    """Tests for max_consecutive_text_only parameter."""

    @pytest.mark.asyncio
    async def test_stops_after_consecutive_text_only_turns(self) -> None:
        """Loop stops with error after exceeding max_consecutive_text_only."""
        # AI always returns text-only (no tool calls), with queued messages to keep looping
        text_round = _make_stream_events(content="proceeding...")
        ai = _mock_ai_service(text_round)

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        # Pre-load enough messages to trigger multiple text-only turns
        for i in range(10):
            await queue.put({"role": "user", "content": f"msg {i}"})

        messages: list[dict[str, Any]] = [{"role": "user", "content": "start"}]

        async def _executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
            return {"output": "ok"}

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai,
                messages=messages,
                tool_executor=_executor,
                tools_openai=None,
                message_queue=queue,
                max_consecutive_text_only=3,
            )
        )

        error_events = [e for e in events if e.kind == "error"]
        assert len(error_events) == 1
        assert "consecutive text-only" in error_events[0].data["message"]

    @pytest.mark.asyncio
    async def test_counter_resets_after_tool_call(self) -> None:
        """Text-only counter resets when a tool call occurs."""
        text_round = _make_stream_events(content="thinking...")
        tool_round = _make_stream_events(
            content="using tool",
            tool_calls=[_tc("tc1", "read_file", {"path": "/tmp/x"})],
        )
        # Sequence: text, text, tool (resets), text, done
        # With max_consecutive_text_only=2, this should NOT error because
        # the tool call resets the counter and only 1 text-only follows
        final_text = _make_stream_events(content="done!")
        ai = _mock_ai_service(text_round, text_round, tool_round, final_text)

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        # 2 queued messages: first triggers text round 2, second triggers tool round
        # After tool round resets counter, the final text-only round returns normally
        # because the queue is empty.
        for i in range(2):
            await queue.put({"role": "user", "content": f"msg {i}"})

        messages: list[dict[str, Any]] = [{"role": "user", "content": "start"}]

        async def _executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
            return {"output": "ok"}

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai,
                messages=messages,
                tool_executor=_executor,
                tools_openai=[{"type": "function", "function": {"name": "read_file"}}],
                message_queue=queue,
                max_consecutive_text_only=2,
            )
        )

        error_events = [e for e in events if e.kind == "error"]
        assert len(error_events) == 0

    @pytest.mark.asyncio
    async def test_disabled_with_zero(self) -> None:
        """Setting max_consecutive_text_only=0 disables the limit."""
        text_round = _make_stream_events(content="proceeding...")
        ai = _mock_ai_service(text_round)

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for i in range(6):
            await queue.put({"role": "user", "content": f"msg {i}"})

        messages: list[dict[str, Any]] = [{"role": "user", "content": "start"}]

        async def _executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
            return {"output": "ok"}

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai,
                messages=messages,
                tool_executor=_executor,
                tools_openai=None,
                message_queue=queue,
                max_iterations=8,
                max_consecutive_text_only=0,
            )
        )

        # Should hit max_iterations, not the text-only limit
        error_events = [e for e in events if e.kind == "error"]
        # Either no error (ran out of queue) or max iterations error
        text_only_errors = [e for e in error_events if "consecutive text-only" in e.data.get("message", "")]
        assert len(text_only_errors) == 0

    @pytest.mark.asyncio
    async def test_single_text_only_response_no_queue(self) -> None:
        """A single text-only response without queue returns normally."""
        text_round = _make_stream_events(content="Hello!")
        ai = _mock_ai_service(text_round)

        messages: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]

        async def _executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
            return {"output": "ok"}

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai,
                messages=messages,
                tool_executor=_executor,
                tools_openai=None,
                max_consecutive_text_only=3,
            )
        )

        error_events = [e for e in events if e.kind == "error"]
        assert len(error_events) == 0
        done_events = [e for e in events if e.kind == "done"]
        assert len(done_events) == 1

    @pytest.mark.asyncio
    async def test_message_queue_counts_toward_limit(self) -> None:
        """Each queued message that triggers a text-only response counts toward the limit."""
        text_round = _make_stream_events(content="proceeding...")
        ai = _mock_ai_service(text_round)

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        # Put exactly enough messages to trigger: initial + 3 queued = 4 text-only turns
        # With limit of 3, the 4th should trigger the error
        for i in range(5):
            await queue.put({"role": "user", "content": f"msg {i}"})

        messages: list[dict[str, Any]] = [{"role": "user", "content": "start"}]

        async def _executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
            return {"output": "ok"}

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai,
                messages=messages,
                tool_executor=_executor,
                tools_openai=None,
                message_queue=queue,
                max_consecutive_text_only=3,
            )
        )

        error_events = [e for e in events if e.kind == "error"]
        assert len(error_events) == 1
        assert "4" in error_events[0].data["message"]  # 4th text-only turn triggers error

    @pytest.mark.asyncio
    async def test_default_limit_is_three(self) -> None:
        """Default max_consecutive_text_only is 3."""
        text_round = _make_stream_events(content="proceeding...")
        ai = _mock_ai_service(text_round)

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for i in range(10):
            await queue.put({"role": "user", "content": f"msg {i}"})

        messages: list[dict[str, Any]] = [{"role": "user", "content": "start"}]

        async def _executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
            return {"output": "ok"}

        # Don't pass max_consecutive_text_only — should use default of 3
        events = await _collect_events(
            run_agent_loop(
                ai_service=ai,
                messages=messages,
                tool_executor=_executor,
                tools_openai=None,
                message_queue=queue,
            )
        )

        error_events = [e for e in events if e.kind == "error"]
        assert len(error_events) == 1
        assert "consecutive text-only" in error_events[0].data["message"]

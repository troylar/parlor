"""Tests for intra-response line repetition detection in agent_loop (#691)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from anteroom.services.agent_loop import AgentEvent, run_agent_loop


def _make_stream_events(
    content: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if content:
        events.append({"event": "token", "data": {"content": content}})
    for tc in tool_calls or []:
        events.append({"event": "tool_call", "data": tc})
    events.append({"event": "done", "data": {}})
    return events


def _mock_ai_service(*rounds: list[dict[str, Any]]) -> AsyncMock:
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


async def _collect_events(gen: Any) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    async for e in gen:
        events.append(e)
    return events


async def _executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"output": "ok"}


class TestLineRepeatDetection:
    """Tests for max_line_repeats parameter (intra-response repetition)."""

    @pytest.mark.asyncio
    async def test_stops_on_repeated_lines(self) -> None:
        """Detects and stops when same line repeats >= max_line_repeats times."""
        repeated = "Proceeding with the reset...\n" * 7
        stream = _make_stream_events(content=repeated)
        ai = _mock_ai_service(stream)

        messages: list[dict[str, Any]] = [{"role": "user", "content": "start"}]

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai,
                messages=messages,
                tool_executor=_executor,
                tools_openai=None,
                max_consecutive_text_only=0,
                max_line_repeats=5,
            )
        )

        error_events = [e for e in events if e.kind == "error"]
        assert len(error_events) == 1
        assert "Repetitive output" in error_events[0].data["message"]
        assert "7" in error_events[0].data["message"]

    @pytest.mark.asyncio
    async def test_no_false_positive_on_varied_lines(self) -> None:
        """Normal multi-line responses are not flagged."""
        content = "\n".join(f"Step {i}: doing something different" for i in range(20))
        stream = _make_stream_events(content=content)
        ai = _mock_ai_service(stream)

        messages: list[dict[str, Any]] = [{"role": "user", "content": "start"}]

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai,
                messages=messages,
                tool_executor=_executor,
                tools_openai=None,
                max_line_repeats=5,
            )
        )

        error_events = [e for e in events if e.kind == "error"]
        assert len(error_events) == 0

    @pytest.mark.asyncio
    async def test_below_threshold_not_triggered(self) -> None:
        """Repetition below the threshold is allowed."""
        repeated = "Proceeding...\n" * 4  # 4 repeats, threshold is 5
        stream = _make_stream_events(content=repeated)
        ai = _mock_ai_service(stream)

        messages: list[dict[str, Any]] = [{"role": "user", "content": "start"}]

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai,
                messages=messages,
                tool_executor=_executor,
                tools_openai=None,
                max_line_repeats=5,
            )
        )

        error_events = [e for e in events if e.kind == "error"]
        assert len(error_events) == 0

    @pytest.mark.asyncio
    async def test_disabled_with_zero(self) -> None:
        """Setting max_line_repeats=0 disables detection."""
        repeated = "Proceeding...\n" * 20
        stream = _make_stream_events(content=repeated)
        ai = _mock_ai_service(stream)

        messages: list[dict[str, Any]] = [{"role": "user", "content": "start"}]

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai,
                messages=messages,
                tool_executor=_executor,
                tools_openai=None,
                max_line_repeats=0,
            )
        )

        repetition_errors = [e for e in events if e.kind == "error" and "Repetitive" in e.data.get("message", "")]
        assert len(repetition_errors) == 0

    @pytest.mark.asyncio
    async def test_strips_whitespace_for_comparison(self) -> None:
        """Lines with varying whitespace are still detected as identical."""
        lines = [
            "  Proceeding with the reset...  ",
            "Proceeding with the reset...",
            "  Proceeding with the reset...\t",
            "Proceeding with the reset...  ",
            "Proceeding with the reset...",
        ]
        content = "\n".join(lines)
        stream = _make_stream_events(content=content)
        ai = _mock_ai_service(stream)

        messages: list[dict[str, Any]] = [{"role": "user", "content": "start"}]

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai,
                messages=messages,
                tool_executor=_executor,
                tools_openai=None,
                max_line_repeats=5,
            )
        )

        error_events = [e for e in events if e.kind == "error"]
        assert len(error_events) == 1
        assert "Repetitive output" in error_events[0].data["message"]

    @pytest.mark.asyncio
    async def test_empty_lines_ignored(self) -> None:
        """Blank lines between repeated content don't break detection."""
        content = "Proceeding...\n\nProceeding...\n\nProceeding...\n\nProceeding...\n\nProceeding..."
        stream = _make_stream_events(content=content)
        ai = _mock_ai_service(stream)

        messages: list[dict[str, Any]] = [{"role": "user", "content": "start"}]

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai,
                messages=messages,
                tool_executor=_executor,
                tools_openai=None,
                max_line_repeats=5,
            )
        )

        error_events = [e for e in events if e.kind == "error"]
        assert len(error_events) == 1

    @pytest.mark.asyncio
    async def test_default_threshold_is_five(self) -> None:
        """Default max_line_repeats is 5."""
        repeated = "Proceeding...\n" * 6
        stream = _make_stream_events(content=repeated)
        ai = _mock_ai_service(stream)

        messages: list[dict[str, Any]] = [{"role": "user", "content": "start"}]

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai,
                messages=messages,
                tool_executor=_executor,
                tools_openai=None,
                # don't pass max_line_repeats — should use default of 5
            )
        )

        error_events = [e for e in events if e.kind == "error" and "Repetitive" in e.data.get("message", "")]
        assert len(error_events) == 1

    @pytest.mark.asyncio
    async def test_tool_call_response_skips_check(self) -> None:
        """Responses with tool calls don't get repetition-checked (tool_calls_pending is truthy)."""
        repeated = "Proceeding...\n" * 10
        stream = _make_stream_events(
            content=repeated,
            tool_calls=[{"id": "tc1", "function_name": "read_file", "arguments": {"path": "/tmp/x"}}],
        )
        ai = _mock_ai_service(stream)

        messages: list[dict[str, Any]] = [{"role": "user", "content": "start"}]

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai,
                messages=messages,
                tool_executor=_executor,
                tools_openai=[{"type": "function", "function": {"name": "read_file"}}],
                max_line_repeats=5,
            )
        )

        repetition_errors = [e for e in events if e.kind == "error" and "Repetitive" in e.data.get("message", "")]
        assert len(repetition_errors) == 0

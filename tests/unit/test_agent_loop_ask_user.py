"""Tests for ask_user tool integration with the agent loop (#299)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from anteroom.services.agent_loop import AgentEvent, run_agent_loop

# -- Helpers (same patterns as test_prompt_queue.py) --


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


def _tc(tool_id: str, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": tool_id, "function_name": name, "arguments": args or {}}


def _mock_ai_service(*rounds: list[dict[str, Any]]) -> AsyncMock:
    service = AsyncMock()
    call_count = 0

    async def _stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
        nonlocal call_count
        idx = min(call_count, len(rounds) - 1)
        call_count += 1
        for event in rounds[idx]:
            yield event

    service.stream_chat = _stream_chat
    return service


async def _collect_events(gen) -> list[AgentEvent]:
    events = []
    async for e in gen:
        events.append(e)
    return events


class TestAskUserInAgentLoop:
    @pytest.mark.asyncio
    async def test_ask_user_pauses_and_resumes(self) -> None:
        """AI calls ask_user, loop pauses for callback, answer returned, loop continues."""
        # Round 1: AI calls ask_user
        # Round 2: AI responds with the answer incorporated
        round1 = _make_stream_events(tool_calls=[_tc("tc1", "ask_user", {"question": "Which DB?"})])
        round2 = _make_stream_events(content="Using PostgreSQL as requested.")

        service = _mock_ai_service(round1, round2)

        async def tool_executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "ask_user":
                # Simulate the callback injection that tool_executor does
                async def mock_callback(q: str) -> str:
                    return "PostgreSQL"

                from anteroom.tools.ask_user import handle

                return await handle(**args, _ask_callback=mock_callback)
            return {"output": "ok"}

        events = await _collect_events(
            run_agent_loop(
                ai_service=service,
                messages=[{"role": "user", "content": "set up the database"}],
                tool_executor=tool_executor,
                tools_openai=[{"type": "function", "function": {"name": "ask_user"}}],
            )
        )

        kinds = [e.kind for e in events]
        # Should see: thinking, tool_call_start, tool_call_end, thinking, token, assistant_message, done
        assert "tool_call_start" in kinds
        assert "tool_call_end" in kinds
        assert "assistant_message" in kinds
        assert "done" in kinds

        # Verify the tool result contains the answer
        tool_end = next(e for e in events if e.kind == "tool_call_end")
        assert tool_end.data["output"]["answer"] == "PostgreSQL"

    @pytest.mark.asyncio
    async def test_ask_user_no_callback_continues(self) -> None:
        """When no callback is provided, ask_user returns an error and the loop continues."""
        round1 = _make_stream_events(tool_calls=[_tc("tc1", "ask_user", {"question": "Which DB?"})])
        round2 = _make_stream_events(content="I'll use SQLite as default.")

        service = _mock_ai_service(round1, round2)

        async def tool_executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "ask_user":
                from anteroom.tools.ask_user import handle

                return await handle(**args)  # no _ask_callback
            return {"output": "ok"}

        events = await _collect_events(
            run_agent_loop(
                ai_service=service,
                messages=[{"role": "user", "content": "set up the database"}],
                tool_executor=tool_executor,
                tools_openai=[{"type": "function", "function": {"name": "ask_user"}}],
            )
        )

        kinds = [e.kind for e in events]
        assert "tool_call_end" in kinds
        assert "done" in kinds

        # Tool result should be an error
        tool_end = next(e for e in events if e.kind == "tool_call_end")
        assert "error" in tool_end.data["output"]

    @pytest.mark.asyncio
    async def test_ask_user_callback_cancelled(self) -> None:
        """When callback raises EOFError (user cancels), loop continues with empty answer."""
        round1 = _make_stream_events(tool_calls=[_tc("tc1", "ask_user", {"question": "Which DB?"})])
        round2 = _make_stream_events(content="Proceeding with defaults.")

        service = _mock_ai_service(round1, round2)

        async def tool_executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "ask_user":

                async def cancel_callback(q: str) -> str:
                    raise EOFError()

                from anteroom.tools.ask_user import handle

                return await handle(**args, _ask_callback=cancel_callback)
            return {"output": "ok"}

        events = await _collect_events(
            run_agent_loop(
                ai_service=service,
                messages=[{"role": "user", "content": "set up the database"}],
                tool_executor=tool_executor,
                tools_openai=[{"type": "function", "function": {"name": "ask_user"}}],
            )
        )

        tool_end = next(e for e in events if e.kind == "tool_call_end")
        assert tool_end.data["output"]["answer"] == ""
        assert "done" in [e.kind for e in events]

    @pytest.mark.asyncio
    async def test_ask_user_with_other_tools(self) -> None:
        """ask_user can be called alongside other tools in the same turn."""
        round1 = _make_stream_events(
            tool_calls=[
                _tc("tc1", "read_file", {"path": "/tmp/test.txt"}),
                _tc("tc2", "ask_user", {"question": "Keep going?"}),
            ]
        )
        round2 = _make_stream_events(content="Done.")

        service = _mock_ai_service(round1, round2)

        async def tool_executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "ask_user":

                async def cb(q: str) -> str:
                    return "yes"

                from anteroom.tools.ask_user import handle

                return await handle(**args, _ask_callback=cb)
            return {"output": f"{name} result"}

        events = await _collect_events(
            run_agent_loop(
                ai_service=service,
                messages=[{"role": "user", "content": "process files"}],
                tool_executor=tool_executor,
                tools_openai=[
                    {"type": "function", "function": {"name": "read_file"}},
                    {"type": "function", "function": {"name": "ask_user"}},
                ],
            )
        )

        tool_ends = [e for e in events if e.kind == "tool_call_end"]
        assert len(tool_ends) == 2
        assert "done" in [e.kind for e in events]

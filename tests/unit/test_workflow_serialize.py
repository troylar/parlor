"""Tests for serialized tool execution mode and pause signal in agent_loop."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from anteroom.services.agent_loop import AgentEvent, run_agent_loop
from anteroom.services.ai_service import AIService


def _make_ai_service() -> AIService:
    service = AIService.__new__(AIService)
    service.config = type(
        "C",
        (),
        {
            "base_url": "http://localhost/v1",
            "api_key": "test",
            "model": "gpt-4",
            "request_timeout": 120,
            "verify_ssl": True,
            "max_output_tokens": 4096,
        },
    )()
    service._token_provider = None
    service.client = AsyncMock()
    return service


def _tc(tool_id: str, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": tool_id, "function_name": name, "arguments": args or {}}


def _mock_ai_with_tool_calls(tool_calls: list[dict[str, Any]], final_text: str = "Done") -> AIService:
    """Create an AI service that returns tool calls on first round, text on second."""
    service = _make_ai_service()
    call_count = 0

    async def fake_stream(messages: Any, **kwargs: Any):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            for tc in tool_calls:
                yield {
                    "event": "tool_call",
                    "data": _tc(tc["id"], tc["name"], tc.get("args")),
                }
            yield {"event": "done", "data": {}}
        else:
            yield {"event": "token", "data": {"content": final_text}}
            yield {"event": "done", "data": {}}

    service.stream_chat = fake_stream
    return service


async def _collect(gen) -> list[AgentEvent]:
    events = []
    async for e in gen:
        events.append(e)
    return events


def _make_tool_executor(results: dict[str, Any] | None = None) -> AsyncMock:
    """Create a tool executor that returns configurable results per tool name."""
    default_results = results or {}

    async def executor(name: str, args: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return default_results.get(name, {"result": f"{name} done"})

    return executor


# ---------------------------------------------------------------------------
# Serialized mode: sequential execution
# ---------------------------------------------------------------------------


class TestSerializedMode:
    @pytest.mark.asyncio
    async def test_serialized_executes_sequentially(self) -> None:
        """Tools execute in definition order, not completion order."""
        execution_order: list[str] = []

        async def ordered_executor(name: str, args: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            execution_order.append(name)
            return {"result": f"{name} done"}

        ai = _mock_ai_with_tool_calls(
            [
                {"id": "tc1", "name": "tool_a"},
                {"id": "tc2", "name": "tool_b"},
                {"id": "tc3", "name": "tool_c"},
            ]
        )
        events = await _collect(
            run_agent_loop(
                ai_service=ai,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=ordered_executor,
                tools_openai=[{"type": "function", "function": {"name": "tool_a"}}],
                serialize_tools=True,
                max_iterations=2,
            )
        )
        assert execution_order == ["tool_a", "tool_b", "tool_c"]
        end_events = [e for e in events if e.kind == "tool_call_end"]
        assert [e.data["tool_name"] for e in end_events] == ["tool_a", "tool_b", "tool_c"]

    @pytest.mark.asyncio
    async def test_default_mode_unchanged(self) -> None:
        """Default (serialize_tools=False) still works — no regression."""
        ai = _mock_ai_with_tool_calls([{"id": "tc1", "name": "tool_a"}])
        events = await _collect(
            run_agent_loop(
                ai_service=ai,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=_make_tool_executor(),
                tools_openai=[{"type": "function", "function": {"name": "tool_a"}}],
                max_iterations=2,
            )
        )
        end_events = [e for e in events if e.kind == "tool_call_end"]
        assert len(end_events) == 1
        assert end_events[0].data["tool_name"] == "tool_a"

    @pytest.mark.asyncio
    async def test_serialized_appends_results_to_messages(self) -> None:
        """Serialized mode appends tool results to messages like parallel mode."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": "test"}]
        ai = _mock_ai_with_tool_calls([{"id": "tc1", "name": "tool_a"}])
        await _collect(
            run_agent_loop(
                ai_service=ai,
                messages=messages,
                tool_executor=_make_tool_executor(),
                tools_openai=[{"type": "function", "function": {"name": "tool_a"}}],
                serialize_tools=True,
                max_iterations=2,
            )
        )
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "tc1"


# ---------------------------------------------------------------------------
# Pause signal
# ---------------------------------------------------------------------------


class TestPauseSignal:
    @pytest.mark.asyncio
    async def test_pause_yields_workflow_pause_and_exits(self) -> None:
        """When pause_signal is set, loop yields workflow_pause and returns."""
        pause = asyncio.Event()

        async def pausing_executor(name: str, args: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            pause.set()
            return {"error": "Operation denied by user", "_approval_decision": "denied"}

        ai = _mock_ai_with_tool_calls([{"id": "tc1", "name": "write_file", "args": {"path": "/x"}}])
        events = await _collect(
            run_agent_loop(
                ai_service=ai,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=pausing_executor,
                tools_openai=[{"type": "function", "function": {"name": "write_file"}}],
                serialize_tools=True,
                pause_signal=pause,
                max_iterations=2,
            )
        )
        pause_events = [e for e in events if e.kind == "workflow_pause"]
        assert len(pause_events) == 1
        assert pause_events[0].data["tool_name"] == "write_file"
        assert pause_events[0].data["reason"] == "approval_required"

    @pytest.mark.asyncio
    async def test_pause_does_not_append_denied_result(self) -> None:
        """Paused tool's result is NOT appended to messages."""
        pause = asyncio.Event()
        messages: list[dict[str, Any]] = [{"role": "user", "content": "test"}]

        async def pausing_executor(name: str, args: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            pause.set()
            return {"error": "denied", "_approval_decision": "denied"}

        ai = _mock_ai_with_tool_calls([{"id": "tc1", "name": "write_file"}])
        await _collect(
            run_agent_loop(
                ai_service=ai,
                messages=messages,
                tool_executor=pausing_executor,
                tools_openai=[{"type": "function", "function": {"name": "write_file"}}],
                serialize_tools=True,
                pause_signal=pause,
                max_iterations=2,
            )
        )
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 0

    @pytest.mark.asyncio
    async def test_pause_assistant_message_in_history(self) -> None:
        """Assistant message with tool calls IS in messages (appended before tool exec)."""
        pause = asyncio.Event()
        messages: list[dict[str, Any]] = [{"role": "user", "content": "test"}]

        async def pausing_executor(name: str, args: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            pause.set()
            return {"error": "denied"}

        ai = _mock_ai_with_tool_calls([{"id": "tc1", "name": "write_file"}])
        await _collect(
            run_agent_loop(
                ai_service=ai,
                messages=messages,
                tool_executor=pausing_executor,
                tools_openai=[{"type": "function", "function": {"name": "write_file"}}],
                serialize_tools=True,
                pause_signal=pause,
                max_iterations=2,
            )
        )
        assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
        assert len(assistant_msgs) == 1

    @pytest.mark.asyncio
    async def test_pause_after_first_tool_skips_remaining(self) -> None:
        """With 3 tool calls, pause after first leaves 2nd and 3rd unexecuted."""
        pause = asyncio.Event()
        executed: list[str] = []

        async def tracking_executor(name: str, args: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            executed.append(name)
            if name == "tool_a":
                pause.set()
                return {"error": "denied"}
            return {"result": "ok"}

        ai = _mock_ai_with_tool_calls(
            [
                {"id": "tc1", "name": "tool_a"},
                {"id": "tc2", "name": "tool_b"},
                {"id": "tc3", "name": "tool_c"},
            ]
        )
        events = await _collect(
            run_agent_loop(
                ai_service=ai,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=tracking_executor,
                tools_openai=[{"type": "function", "function": {"name": "tool_a"}}],
                serialize_tools=True,
                pause_signal=pause,
                max_iterations=2,
            )
        )
        assert executed == ["tool_a"]
        assert any(e.kind == "workflow_pause" for e in events)
        assert not any(e.kind == "tool_call_end" and e.data["tool_name"] == "tool_b" for e in events)

    @pytest.mark.asyncio
    async def test_no_pause_signal_full_execution(self) -> None:
        """serialize_tools=True with no pause_signal runs all tools."""
        ai = _mock_ai_with_tool_calls(
            [
                {"id": "tc1", "name": "tool_a"},
                {"id": "tc2", "name": "tool_b"},
            ]
        )
        events = await _collect(
            run_agent_loop(
                ai_service=ai,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=_make_tool_executor(),
                tools_openai=[{"type": "function", "function": {"name": "tool_a"}}],
                serialize_tools=True,
                max_iterations=2,
            )
        )
        end_events = [e for e in events if e.kind == "tool_call_end"]
        assert len(end_events) == 2
        assert not any(e.kind == "workflow_pause" for e in events)

    @pytest.mark.asyncio
    async def test_pause_signal_ignored_in_parallel_mode(self) -> None:
        """pause_signal is ignored when serialize_tools=False (default)."""
        pause = asyncio.Event()
        pause.set()

        ai = _mock_ai_with_tool_calls([{"id": "tc1", "name": "tool_a"}])
        events = await _collect(
            run_agent_loop(
                ai_service=ai,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=_make_tool_executor(),
                tools_openai=[{"type": "function", "function": {"name": "tool_a"}}],
                serialize_tools=False,
                pause_signal=pause,
                max_iterations=2,
            )
        )
        assert not any(e.kind == "workflow_pause" for e in events)
        assert any(e.kind == "tool_call_end" for e in events)

    @pytest.mark.asyncio
    async def test_cancel_event_works_in_serialized_mode(self) -> None:
        """Cancel event still pre-empts tool execution in serialized mode."""
        cancel = asyncio.Event()
        cancel.set()

        ai = _mock_ai_with_tool_calls([{"id": "tc1", "name": "tool_a"}])
        events = await _collect(
            run_agent_loop(
                ai_service=ai,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=_make_tool_executor(),
                tools_openai=[{"type": "function", "function": {"name": "tool_a"}}],
                serialize_tools=True,
                cancel_event=cancel,
                max_iterations=2,
            )
        )
        end_events = [e for e in events if e.kind == "tool_call_end"]
        assert all(e.data.get("status") == "cancelled" for e in end_events)

    @pytest.mark.asyncio
    async def test_workflow_pause_event_data(self) -> None:
        """workflow_pause event contains tool_call_id, tool_name, tool_args, reason."""
        pause = asyncio.Event()

        async def pausing_executor(name: str, args: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            pause.set()
            return {"error": "denied"}

        ai = _mock_ai_with_tool_calls([{"id": "tc1", "name": "write_file", "args": {"path": "/src/foo.py"}}])
        events = await _collect(
            run_agent_loop(
                ai_service=ai,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=pausing_executor,
                tools_openai=[{"type": "function", "function": {"name": "write_file"}}],
                serialize_tools=True,
                pause_signal=pause,
                max_iterations=2,
            )
        )
        pause_events = [e for e in events if e.kind == "workflow_pause"]
        assert len(pause_events) == 1
        data = pause_events[0].data
        assert data["tool_call_id"] == "tc1"
        assert data["tool_name"] == "write_file"
        assert data["reason"] == "approval_required"

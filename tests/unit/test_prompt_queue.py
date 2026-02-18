"""Tests for parallel tool execution and message queue in agent_loop."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from anteroom.services.agent_loop import AgentEvent, _build_compaction_history, _execute_tool, run_agent_loop

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

    async def _stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
        nonlocal call_count
        idx = min(call_count, len(rounds) - 1)
        call_count += 1
        for event in rounds[idx]:
            yield event

    service.stream_chat = _stream_chat
    return service


def _tc(tool_id: str, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Shorthand to build a tool call dict."""
    return {"id": tool_id, "function_name": name, "arguments": args or {}}


async def _collect_events(gen) -> list[AgentEvent]:
    """Drain an async generator into a list."""
    events = []
    async for e in gen:
        events.append(e)
    return events


# =============================================================================
# _execute_tool unit tests
# =============================================================================


class TestExecuteTool:
    @pytest.mark.asyncio
    async def test_success_no_cancel_event(self):
        """Success path with cancel_event=None."""

        async def executor(name: str, args: dict) -> dict:
            return {"output": "hello"}

        tc = _tc("tc1", "test_tool", {"a": 1})
        result_tc, result, status = await _execute_tool(tc, executor, None)
        assert result_tc is tc
        assert result == {"output": "hello"}
        assert status == "success"

    @pytest.mark.asyncio
    async def test_success_with_cancel_event_not_set(self):
        """Execution completes before cancel event — should return success."""
        cancel = asyncio.Event()

        async def executor(name: str, args: dict) -> dict:
            return {"done": True}

        tc = _tc("tc1", "fast_tool")
        result_tc, result, status = await _execute_tool(tc, executor, cancel)
        assert status == "success"
        assert result == {"done": True}

    @pytest.mark.asyncio
    async def test_cancelled_by_event(self):
        """Cancel event fires before execution completes — should return cancelled."""
        cancel = asyncio.Event()

        async def executor(name: str, args: dict) -> dict:
            await asyncio.sleep(10)
            return {}

        async def _cancel_soon():
            await asyncio.sleep(0.02)
            cancel.set()

        asyncio.create_task(_cancel_soon())
        tc = _tc("tc1", "slow_tool")
        result_tc, result, status = await _execute_tool(tc, executor, cancel)
        assert status == "cancelled"
        assert result == {"error": "Cancelled by user"}

    @pytest.mark.asyncio
    async def test_error_returns_error_status(self):
        """Exception in executor is caught and returned as error."""

        async def executor(name: str, args: dict) -> dict:
            raise RuntimeError("boom")

        tc = _tc("tc1", "bad_tool")
        result_tc, result, status = await _execute_tool(tc, executor, None)
        assert status == "error"
        assert "boom" in result["error"]

    @pytest.mark.asyncio
    async def test_error_with_cancel_event(self):
        """Exception with cancel_event provided — error should still be caught."""
        cancel = asyncio.Event()

        async def executor(name: str, args: dict) -> dict:
            raise ValueError("invalid input")

        tc = _tc("tc1", "err_tool")
        _, result, status = await _execute_tool(tc, executor, cancel)
        assert status == "error"
        assert "invalid input" in result["error"]

    @pytest.mark.asyncio
    async def test_preserves_tool_call_identity(self):
        """Returned tc object is the same object passed in (identity check)."""

        async def executor(name: str, args: dict) -> dict:
            return {}

        tc = _tc("tc1", "tool", {"key": "value"})
        returned_tc, _, _ = await _execute_tool(tc, executor, None)
        assert returned_tc is tc
        assert returned_tc["arguments"] == {"key": "value"}


# =============================================================================
# Parallel tool execution tests
# =============================================================================


class TestParallelToolExecution:
    @pytest.mark.asyncio
    async def test_three_tools_completion_order(self):
        """Three tools with staggered delays complete in fastest-first order."""
        delays = {"fast": 0.01, "medium": 0.05, "slow": 0.1}

        async def tool_executor(name: str, args: dict) -> dict:
            await asyncio.sleep(delays[name])
            return {"result": name}

        tool_calls = [_tc(f"tc_{n}", n) for n in ["slow", "medium", "fast"]]
        ai_service = _mock_ai_service(
            _make_stream_events(tool_calls=tool_calls),
            _make_stream_events(content="Done"),
        )

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=tool_executor,
                tools_openai=[],
            )
        )

        end_events = [e for e in events if e.kind == "tool_call_end"]
        assert len(end_events) == 3
        assert all(e.data["status"] == "success" for e in end_events)

        end_names = [e.data["tool_name"] for e in end_events]
        assert end_names.index("fast") < end_names.index("slow")

    @pytest.mark.asyncio
    async def test_parallel_is_faster_than_sequential(self):
        """Parallel execution should complete in ~max(delays) not sum(delays)."""

        async def tool_executor(name: str, args: dict) -> dict:
            await asyncio.sleep(0.05)
            return {}

        # 5 tools at 50ms each: sequential = 250ms, parallel ≈ 50ms
        tool_calls = [_tc(f"tc_{i}", f"tool_{i}") for i in range(5)]
        ai_service = _mock_ai_service(
            _make_stream_events(tool_calls=tool_calls),
            _make_stream_events(content="Done"),
        )

        start = time.monotonic()
        await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=tool_executor,
                tools_openai=[],
            )
        )
        elapsed = time.monotonic() - start

        # Should be well under 250ms (sequential). Allow generous margin for CI.
        assert elapsed < 0.2, f"Parallel execution took {elapsed:.3f}s, expected < 0.2s"

    @pytest.mark.asyncio
    async def test_single_tool_works(self):
        """Single tool call still works with the parallel code path."""

        async def tool_executor(name: str, args: dict) -> dict:
            return {"result": "only"}

        ai_service = _mock_ai_service(
            _make_stream_events(tool_calls=[_tc("tc1", "solo_tool")]),
            _make_stream_events(content="Done"),
        )

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=tool_executor,
                tools_openai=[],
            )
        )

        end_events = [e for e in events if e.kind == "tool_call_end"]
        assert len(end_events) == 1
        assert end_events[0].data["status"] == "success"
        assert end_events[0].data["output"] == {"result": "only"}

    @pytest.mark.asyncio
    async def test_cancel_all_tools_midway(self):
        """Cancel event fires while all tools are running — all report cancelled."""
        cancel = asyncio.Event()

        async def slow_executor(name: str, args: dict) -> dict:
            await asyncio.sleep(10)
            return {}

        tool_calls = [_tc("tc_1", "tool1"), _tc("tc_2", "tool2")]
        ai_service = _mock_ai_service(_make_stream_events(tool_calls=tool_calls))

        asyncio.get_event_loop().call_later(0.05, cancel.set)

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=slow_executor,
                tools_openai=[],
                cancel_event=cancel,
            )
        )

        end_events = [e for e in events if e.kind == "tool_call_end"]
        assert len(end_events) == 2
        assert all(e.data["status"] == "cancelled" for e in end_events)
        # Should end with a done event
        assert events[-1].kind == "done"

    @pytest.mark.asyncio
    async def test_cancel_before_tool_dispatch(self):
        """Cancel already set before tools start — all report cancelled immediately."""
        cancel = asyncio.Event()
        cancel.set()  # Pre-cancelled

        executor_called = False

        async def executor(name: str, args: dict) -> dict:
            nonlocal executor_called
            executor_called = True
            return {}

        tool_calls = [_tc("tc_1", "a"), _tc("tc_2", "b"), _tc("tc_3", "c")]
        ai_service = _mock_ai_service(_make_stream_events(tool_calls=tool_calls))

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=executor,
                tools_openai=[],
                cancel_event=cancel,
            )
        )

        # Executor should NOT have been called
        assert not executor_called
        end_events = [e for e in events if e.kind == "tool_call_end"]
        assert len(end_events) == 3
        assert all(e.data["status"] == "cancelled" for e in end_events)

    @pytest.mark.asyncio
    async def test_error_isolation_one_fails(self):
        """One tool errors, others succeed — error doesn't affect siblings."""

        async def tool_executor(name: str, args: dict) -> dict:
            if name == "bad":
                raise ValueError("Tool failed")
            return {"result": name}

        tool_calls = [_tc("tc_good", "good"), _tc("tc_bad", "bad")]
        ai_service = _mock_ai_service(
            _make_stream_events(tool_calls=tool_calls),
            _make_stream_events(content="Recovered"),
        )

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=tool_executor,
                tools_openai=[],
            )
        )

        end_events = [e for e in events if e.kind == "tool_call_end"]
        statuses = {e.data["tool_name"]: e.data["status"] for e in end_events}
        assert statuses["good"] == "success"
        assert statuses["bad"] == "error"
        assert "Tool failed" in end_events[1 if end_events[1].data["tool_name"] == "bad" else 0].data["output"]["error"]

    @pytest.mark.asyncio
    async def test_all_tool_results_in_messages(self):
        """After parallel execution, all tool role messages are appended to messages list."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": "test"}]

        async def tool_executor(name: str, args: dict) -> dict:
            return {"val": name}

        tool_calls = [_tc(f"tc_{i}", f"tool_{i}") for i in range(4)]
        ai_service = _mock_ai_service(
            _make_stream_events(tool_calls=tool_calls),
            _make_stream_events(content="Done"),
        )

        await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=messages,
                tool_executor=tool_executor,
                tools_openai=[],
            )
        )

        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 4
        # Each tool_call_id should be unique
        ids = {m["tool_call_id"] for m in tool_msgs}
        assert len(ids) == 4
        # Each should have valid JSON content
        for m in tool_msgs:
            parsed = json.loads(m["content"])
            assert "val" in parsed

    @pytest.mark.asyncio
    async def test_assistant_message_saved_before_tool_execution(self):
        """Assistant message with tool_calls should be in messages before tool results."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": "test"}]

        async def tool_executor(name: str, args: dict) -> dict:
            return {}

        tool_calls = [_tc("tc1", "my_tool", {"x": 1})]
        ai_service = _mock_ai_service(
            _make_stream_events(content="Let me run that", tool_calls=tool_calls),
            _make_stream_events(content="Done"),
        )

        await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=messages,
                tool_executor=tool_executor,
                tools_openai=[],
            )
        )

        # Find the assistant message with tool_calls
        assistant_with_tc = [m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")]
        assert len(assistant_with_tc) == 1
        assert assistant_with_tc[0]["content"] == "Let me run that"
        assert assistant_with_tc[0]["tool_calls"][0]["function"]["name"] == "my_tool"

        # The tool result should come after the assistant message
        assistant_idx = messages.index(assistant_with_tc[0])
        tool_msg = [m for m in messages if m.get("role") == "tool"][0]
        tool_idx = messages.index(tool_msg)
        assert tool_idx > assistant_idx

    @pytest.mark.asyncio
    async def test_event_ordering_starts_before_ends(self):
        """All tool_call_start events are emitted before any tool_call_end events."""

        async def tool_executor(name: str, args: dict) -> dict:
            await asyncio.sleep(0.01)
            return {}

        tool_calls = [_tc(f"tc_{i}", f"tool_{i}") for i in range(3)]
        ai_service = _mock_ai_service(
            _make_stream_events(tool_calls=tool_calls),
            _make_stream_events(content="Done"),
        )

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=tool_executor,
                tools_openai=[],
            )
        )

        start_indices = [i for i, e in enumerate(events) if e.kind == "tool_call_start"]
        end_indices = [i for i, e in enumerate(events) if e.kind == "tool_call_end"]
        assert len(start_indices) == 3
        assert len(end_indices) == 3
        # All starts should come before all ends (starts emitted during streaming,
        # ends emitted during execution)
        assert max(start_indices) < min(end_indices)

    @pytest.mark.asyncio
    async def test_thinking_event_emitted_for_second_iteration(self):
        """A thinking event is emitted before every API call, including the first and second iterations."""

        async def tool_executor(name: str, args: dict) -> dict:
            return {}

        ai_service = _mock_ai_service(
            _make_stream_events(tool_calls=[_tc("tc1", "tool1")]),
            _make_stream_events(content="Final"),
        )

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=tool_executor,
                tools_openai=[],
            )
        )

        thinking_events = [e for e in events if e.kind == "thinking"]
        assert len(thinking_events) == 2  # Emitted before each API call (iterations 1 and 2)

    @pytest.mark.asyncio
    async def test_cancel_after_tools_complete_yields_done(self):
        """If cancel is set right after tools complete, loop yields done and returns."""
        cancel = asyncio.Event()

        async def tool_executor(name: str, args: dict) -> dict:
            cancel.set()  # Cancel right after this tool completes
            return {"result": "ok"}

        tool_calls = [_tc("tc1", "trigger_cancel")]
        ai_service = _mock_ai_service(
            _make_stream_events(tool_calls=tool_calls),
        )

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=tool_executor,
                tools_openai=[],
                cancel_event=cancel,
            )
        )

        kinds = [e.kind for e in events]
        assert "done" in kinds
        # Should NOT have a second API call (no thinking event after the done)
        done_idx = kinds.index("done")
        assert "thinking" not in kinds[done_idx:]

    @pytest.mark.asyncio
    async def test_empty_assistant_content_with_tool_calls(self):
        """Tools requested with no text content — empty content should be handled."""

        async def tool_executor(name: str, args: dict) -> dict:
            return {}

        # No content, only tool calls
        ai_service = _mock_ai_service(
            _make_stream_events(content="", tool_calls=[_tc("tc1", "tool1")]),
            _make_stream_events(content="Result"),
        )

        messages: list[dict[str, Any]] = [{"role": "user", "content": "test"}]
        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=messages,
                tool_executor=tool_executor,
                tools_openai=[],
            )
        )

        # Assistant message should still be saved (with empty content)
        assistant_msgs = [e for e in events if e.kind == "assistant_message"]
        assert len(assistant_msgs) == 2
        assert assistant_msgs[0].data["content"] == ""

        # Messages list should have the assistant entry with tool_calls
        assistant_with_tc = [m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")]
        assert len(assistant_with_tc) == 1
        assert assistant_with_tc[0]["content"] == ""


# =============================================================================
# Message queue tests
# =============================================================================


class TestMessageQueue:
    @pytest.mark.asyncio
    async def test_queue_processes_after_done(self):
        """Queued message causes the loop to continue instead of returning."""
        call_count = 0

        async def _stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            nonlocal call_count
            call_count += 1
            yield {"event": "token", "data": {"content": f"Response {call_count}"}}
            yield {"event": "done", "data": {}}

        ai_service = AsyncMock()
        ai_service.stream_chat = _stream_chat

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await queue.put({"role": "user", "content": "Follow-up"})

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "Initial"}],
                tool_executor=AsyncMock(return_value={}),
                tools_openai=[],
                message_queue=queue,
            )
        )

        kinds = [e.kind for e in events]
        assert "queued_message" in kinds
        assert kinds.count("done") == 2
        assert kinds.count("assistant_message") == 2

    @pytest.mark.asyncio
    async def test_queue_empty_returns_normally(self):
        """Empty queue — agent loop returns after done as usual."""
        ai_service = _mock_ai_service(_make_stream_events(content="Hello"))
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=AsyncMock(return_value={}),
                tools_openai=[],
                message_queue=queue,
            )
        )

        kinds = [e.kind for e in events]
        assert kinds.count("done") == 1
        assert "queued_message" not in kinds

    @pytest.mark.asyncio
    async def test_no_queue_param_backward_compat(self):
        """message_queue=None (default) — loop returns after done, no queue check."""
        ai_service = _mock_ai_service(_make_stream_events(content="Hello"))

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=AsyncMock(return_value={}),
                tools_openai=[],
                # message_queue not passed — defaults to None
            )
        )

        kinds = [e.kind for e in events]
        assert kinds.count("done") == 1
        assert "queued_message" not in kinds

    @pytest.mark.asyncio
    async def test_multiple_messages_fifo_order(self):
        """Three queued messages are processed in FIFO order."""
        seen_contents: list[str] = []

        async def _stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            last_user = [m for m in messages if m["role"] == "user"][-1]
            seen_contents.append(last_user["content"])
            yield {"event": "token", "data": {"content": "reply"}}
            yield {"event": "done", "data": {}}

        ai_service = AsyncMock()
        ai_service.stream_chat = _stream_chat

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await queue.put({"role": "user", "content": "Second"})
        await queue.put({"role": "user", "content": "Third"})
        await queue.put({"role": "user", "content": "Fourth"})

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "First"}],
                tool_executor=AsyncMock(return_value={}),
                tools_openai=[],
                message_queue=queue,
            )
        )

        assert seen_contents == ["First", "Second", "Third", "Fourth"]
        assert [e.kind for e in events].count("done") == 4
        assert [e.kind for e in events].count("queued_message") == 3

    @pytest.mark.asyncio
    async def test_queued_message_appended_to_messages_list(self):
        """Queued message content is appended to the messages list for context."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": "Initial"}]

        async def _stream_chat(messages_arg, tools=None, cancel_event=None, extra_system_prompt=None):
            yield {"event": "token", "data": {"content": "reply"}}
            yield {"event": "done", "data": {}}

        ai_service = AsyncMock()
        ai_service.stream_chat = _stream_chat

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await queue.put({"role": "user", "content": "Follow-up question"})

        await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=messages,
                tool_executor=AsyncMock(return_value={}),
                tools_openai=[],
                message_queue=queue,
            )
        )

        user_msgs = [m for m in messages if m["role"] == "user"]
        assert len(user_msgs) == 2
        assert user_msgs[1]["content"] == "Follow-up question"

    @pytest.mark.asyncio
    async def test_queued_message_event_data(self):
        """queued_message event contains the actual message data."""

        async def _stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            yield {"event": "token", "data": {"content": "reply"}}
            yield {"event": "done", "data": {}}

        ai_service = AsyncMock()
        ai_service.stream_chat = _stream_chat

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await queue.put({"role": "user", "content": "my queued msg"})

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=AsyncMock(return_value={}),
                tools_openai=[],
                message_queue=queue,
            )
        )

        qm_events = [e for e in events if e.kind == "queued_message"]
        assert len(qm_events) == 1
        assert qm_events[0].data == {"role": "user", "content": "my queued msg"}

    @pytest.mark.asyncio
    async def test_queue_after_tool_execution(self):
        """Queue is checked after done, even when the current turn had tool calls."""
        call_count = 0

        async def _stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: return tool calls
                yield {"event": "tool_call", "data": _tc("tc1", "read_file", {"path": "foo.py"})}
                yield {"event": "done", "data": {}}
            elif call_count == 2:
                # Second call (after tool execution): text response, then done triggers queue
                yield {"event": "token", "data": {"content": "File contents"}}
                yield {"event": "done", "data": {}}
            else:
                # Third call: processing queued message
                yield {"event": "token", "data": {"content": "Queued response"}}
                yield {"event": "done", "data": {}}

        ai_service = AsyncMock()
        ai_service.stream_chat = _stream_chat

        async def tool_executor(name: str, args: dict) -> dict:
            return {"content": "file data"}

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await queue.put({"role": "user", "content": "Now analyze it"})

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "Read foo.py"}],
                tool_executor=tool_executor,
                tools_openai=[{"type": "function", "function": {"name": "read_file"}}],
                message_queue=queue,
            )
        )

        kinds = [e.kind for e in events]
        assert "tool_call_start" in kinds
        assert "tool_call_end" in kinds
        assert "queued_message" in kinds
        # done events: after text response (triggers queue), after queued message response
        assert kinds.count("done") == 2

    @pytest.mark.asyncio
    async def test_queue_not_checked_during_tool_loop(self):
        """Queue is only checked when no tool_calls are pending (text-only response)."""
        call_count = 0

        async def _stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                # Keep returning tool calls — queue should not be checked
                yield {"event": "tool_call", "data": _tc(f"tc{call_count}", f"tool{call_count}")}
                yield {"event": "done", "data": {}}
            else:
                yield {"event": "token", "data": {"content": "Final"}}
                yield {"event": "done", "data": {}}

        ai_service = AsyncMock()
        ai_service.stream_chat = _stream_chat

        async def tool_executor(name: str, args: dict) -> dict:
            return {}

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await queue.put({"role": "user", "content": "Queued"})

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=tool_executor,
                tools_openai=[],
                message_queue=queue,
            )
        )

        kinds = [e.kind for e in events]
        # queued_message should only appear after the final text-only done
        first_queued_idx = kinds.index("queued_message")
        # There should be tool_call_end events before it
        assert "tool_call_end" in kinds[:first_queued_idx]

    @pytest.mark.asyncio
    async def test_queue_with_max_iterations_limit(self):
        """Queue messages consume iterations — max_iterations is respected."""

        async def _stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            yield {"event": "token", "data": {"content": "reply"}}
            yield {"event": "done", "data": {}}

        ai_service = AsyncMock()
        ai_service.stream_chat = _stream_chat

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        # Queue more messages than max_iterations allows
        for i in range(10):
            await queue.put({"role": "user", "content": f"msg {i}"})

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "start"}],
                tool_executor=AsyncMock(return_value={}),
                tools_openai=[],
                message_queue=queue,
                max_iterations=3,  # Only 3 iterations allowed
            )
        )

        kinds = [e.kind for e in events]
        # Each text response uses 1 iteration. Queue check is free (happens after done).
        # 3 iterations = 3 text responses = 3 done events.
        # After each done, queue is checked: 3 queued_message events consumed,
        # but the 4th iteration (from 3rd queued message) would be iteration 4 > max 3.
        # Actually: iteration 1->done->queue, iteration 2->done->queue, iteration 3->done->queue
        # The queue check itself doesn't increment iterations, only the `continue` does.
        # So we get: 3 done events, but the loop continues after each, consuming 3 queued msgs.
        # Iteration 4 would be from the 3rd queue message, but that's iteration 4 > 3.
        # Wait — the `continue` goes back to `while iteration < max_iterations` and increments.
        # iteration=1: initial -> done -> queue msg 0 (queued_message, continue)
        # iteration=2: msg 0 -> done -> queue msg 1 (queued_message, continue)
        # iteration=3: msg 1 -> done -> queue msg 2 (queued_message, continue)
        # iteration=4: exceeds max_iterations=3, falls through to error
        # But wait: after queued_message + continue, iteration increments at top of loop.
        # So 3 iterations produce 3 done + 3 queued_message events, then error on iteration 4.
        assert kinds.count("done") == 3
        assert kinds.count("queued_message") == 3
        assert "error" in kinds  # Max iterations reached
        # Queue should still have remaining messages
        assert not queue.empty()

    @pytest.mark.asyncio
    async def test_event_sequence_with_queue(self):
        """Verify exact event sequence: token, assistant_message, done, queued_message, token, ..."""

        async def _stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            yield {"event": "token", "data": {"content": "reply"}}
            yield {"event": "done", "data": {}}

        ai_service = AsyncMock()
        ai_service.stream_chat = _stream_chat

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await queue.put({"role": "user", "content": "follow-up"})

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=AsyncMock(return_value={}),
                tools_openai=[],
                message_queue=queue,
            )
        )

        kinds = [e.kind for e in events]
        assert kinds == [
            "thinking",  # iteration 1 — before first API call
            "token",
            "assistant_message",
            "done",
            "queued_message",
            "thinking",  # iteration 2 — after queued message appended
            "token",
            "assistant_message",
            "done",
        ]

    @pytest.mark.asyncio
    async def test_queue_with_cancel_stops_processing(self):
        """If cancel is set during queued message processing, loop ends."""
        call_count = 0
        cancel = asyncio.Event()

        async def _stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield {"event": "token", "data": {"content": "first reply"}}
                yield {"event": "done", "data": {}}
            else:
                # Second call for queued msg — return tool calls, then cancel
                yield {
                    "event": "tool_call",
                    "data": _tc("tc_q", "some_tool"),
                }
                yield {"event": "done", "data": {}}

        ai_service = AsyncMock()
        ai_service.stream_chat = _stream_chat

        async def tool_executor(name: str, args: dict) -> dict:
            cancel.set()  # Cancel during tool execution of queued message
            return {}

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await queue.put({"role": "user", "content": "queued"})
        await queue.put({"role": "user", "content": "should not process"})

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "initial"}],
                tool_executor=tool_executor,
                tools_openai=[],
                message_queue=queue,
                cancel_event=cancel,
            )
        )

        # Second queued message should NOT be processed
        kinds = [e.kind for e in events]
        assert kinds.count("queued_message") == 1
        assert not queue.empty()  # "should not process" is still in queue


# =============================================================================
# Mixed scenarios (parallel tools + queue interactions)
# =============================================================================


class TestMixedScenarios:
    @pytest.mark.asyncio
    async def test_parallel_tools_then_queue(self):
        """Full flow: parallel tools execute, then queued message is picked up."""
        call_count = 0

        async def _stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield {"event": "tool_call", "data": _tc("tc_a", "tool_a")}
                yield {"event": "tool_call", "data": _tc("tc_b", "tool_b")}
                yield {"event": "done", "data": {}}
            elif call_count == 2:
                yield {"event": "token", "data": {"content": "Tools done"}}
                yield {"event": "done", "data": {}}
            else:
                yield {"event": "token", "data": {"content": "Queued reply"}}
                yield {"event": "done", "data": {}}

        ai_service = AsyncMock()
        ai_service.stream_chat = _stream_chat

        async def tool_executor(name: str, args: dict) -> dict:
            await asyncio.sleep(0.01)
            return {"executed": name}

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await queue.put({"role": "user", "content": "Now do this"})

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "Run tools"}],
                tool_executor=tool_executor,
                tools_openai=[],
                message_queue=queue,
            )
        )

        kinds = [e.kind for e in events]
        assert kinds.count("tool_call_start") == 2
        assert kinds.count("tool_call_end") == 2
        assert "queued_message" in kinds
        # done events: after text response (triggers queue check), after queued response
        assert kinds.count("done") == 2

    @pytest.mark.asyncio
    async def test_no_content_no_tools_yields_done(self):
        """AI returns empty content and no tools — should still yield done."""
        ai_service = _mock_ai_service(_make_stream_events(content="", tool_calls=[]))

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=AsyncMock(return_value={}),
                tools_openai=[],
            )
        )

        kinds = [e.kind for e in events]
        assert "done" in kinds
        # No assistant_message since content is empty
        assert "assistant_message" not in kinds

    @pytest.mark.asyncio
    async def test_max_iterations_with_tools_exhausts(self):
        """If AI keeps requesting tools, max_iterations is enforced."""

        async def _stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            yield {"event": "tool_call", "data": _tc("tc1", "infinite_tool")}
            yield {"event": "done", "data": {}}

        ai_service = AsyncMock()
        ai_service.stream_chat = _stream_chat

        async def tool_executor(name: str, args: dict) -> dict:
            return {}

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "test"}],
                tool_executor=tool_executor,
                tools_openai=[],
                max_iterations=3,
            )
        )

        kinds = [e.kind for e in events]
        assert "error" in kinds
        error_event = [e for e in events if e.kind == "error"][0]
        assert "Max iterations" in error_event.data["message"]
        assert kinds.count("tool_call_end") == 3


# =============================================================================
# Thinking event on first iteration (#153)
# =============================================================================


class TestThinkingEventFirstIteration:
    @pytest.mark.asyncio
    async def test_thinking_emitted_on_iteration_one(self):
        """thinking event must be emitted before the first API response, not just on iteration 2+."""
        ai_service = _mock_ai_service(_make_stream_events(content="Hello"))

        async def tool_executor(name: str, args: dict) -> dict:
            return {}

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "hi"}],
                tool_executor=tool_executor,
                tools_openai=[],
            )
        )

        kinds = [e.kind for e in events]
        assert kinds[0] == "thinking", f"First event should be 'thinking', got {kinds[0]!r}"

    @pytest.mark.asyncio
    async def test_thinking_emitted_on_every_iteration(self):
        """thinking event emitted before each API call, including the first."""
        ai_service = _mock_ai_service(
            _make_stream_events(tool_calls=[_tc("tc1", "some_tool")]),
            _make_stream_events(content="Done"),
        )

        async def tool_executor(name: str, args: dict) -> dict:
            return {"ok": True}

        events = await _collect_events(
            run_agent_loop(
                ai_service=ai_service,
                messages=[{"role": "user", "content": "go"}],
                tool_executor=tool_executor,
                tools_openai=[],
            )
        )

        thinking_events = [e for e in events if e.kind == "thinking"]
        # Two iterations: one before the tool-call API call, one before the follow-up
        assert len(thinking_events) == 2


# =============================================================================
# _build_compaction_history (#153)
# =============================================================================


class TestBuildCompactionHistory:
    def test_includes_tool_result_success(self):
        """Successful tool results must appear as 'tool_result: <name> → SUCCESS'."""
        messages = [
            {
                "role": "assistant",
                "content": "Writing file.",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "write_file", "arguments": '{"path":"foo.py"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "tc1", "content": '{"written": true}'},
        ]
        history = _build_compaction_history(messages)
        assert "tool_result: write_file → SUCCESS" in history

    def test_includes_tool_result_error(self):
        """Failed tool results must appear as 'tool_result: <name> → ERROR'."""
        messages = [
            {
                "role": "assistant",
                "content": "Running command.",
                "tool_calls": [
                    {"id": "tc2", "type": "function", "function": {"name": "bash", "arguments": '{"command":"ls"}'}}
                ],
            },
            {"role": "tool", "tool_call_id": "tc2", "content": '{"error": "permission denied"}'},
        ]
        history = _build_compaction_history(messages)
        assert "tool_result: bash → ERROR" in history
        assert "permission denied" in history

    def test_tool_call_includes_args_preview(self):
        """Tool call lines must include an argument preview, not just the tool name."""
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc3",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "/etc/hosts"}'},
                    }
                ],
            },
        ]
        history = _build_compaction_history(messages)
        assert "read_file(" in history
        assert "path" in history

    def test_user_and_assistant_text_preserved(self):
        """User and assistant text messages must appear in the history."""
        messages = [
            {"role": "user", "content": "Please implement feature X"},
            {"role": "assistant", "content": "I will implement feature X now."},
        ]
        history = _build_compaction_history(messages)
        assert "Please implement feature X" in history
        assert "I will implement feature X now." in history

    def test_long_content_truncated(self):
        """Long content must be truncated so the summary stays compact."""
        long_content = "A" * 1000
        messages = [{"role": "user", "content": long_content}]
        history = _build_compaction_history(messages)
        assert len(history) < 700  # well below 1000

    def test_tool_result_name_resolved_from_assistant_message(self):
        """Tool result name must be resolved from the preceding assistant tool_calls, not left as 'unknown'."""
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "abc", "type": "function", "function": {"name": "glob_files", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "abc", "content": '["file1.py", "file2.py"]'},
        ]
        history = _build_compaction_history(messages)
        assert "glob_files" in history
        assert "unknown" not in history

    def test_tool_result_content_none_does_not_raise(self):
        """content=None on a tool message must not raise TypeError."""
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "t1", "content": None},
        ]
        history = _build_compaction_history(messages)
        assert "bash" in history
        assert "SUCCESS" in history

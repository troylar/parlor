"""Tests for agent loop narration cadence enforcement."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from anteroom.services.agent_loop import AgentEvent, run_agent_loop

# -- Helpers (same pattern as test_prompt_queue.py) --


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


async def _collect_events(gen) -> list[AgentEvent]:
    events = []
    async for e in gen:
        events.append(e)
    return events


class TestNarrationCadence:
    @pytest.mark.asyncio
    async def test_narration_fires_at_cadence(self):
        """After narration_cadence tool calls complete, a token event with narration text must appear."""
        call_count = 0
        # Round 1: 2 tool calls; Round 2 (narration): text token; Round 3: final text response
        rounds = [
            _make_stream_events(tool_calls=[_tc("t1", "bash"), _tc("t2", "read_file")]),
            _make_stream_events(content="I've run bash and read a file. Moving on to write the output."),
            _make_stream_events(content="All done."),
        ]

        service = AsyncMock()

        async def _stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            nonlocal call_count
            idx = min(call_count, len(rounds) - 1)
            call_count += 1
            for event in rounds[idx]:
                yield event

        service.stream_chat = _stream_chat

        async def tool_executor(name: str, args: dict) -> dict:
            return {"output": "ok"}

        events = await _collect_events(
            run_agent_loop(
                ai_service=service,
                messages=[{"role": "user", "content": "do stuff"}],
                tool_executor=tool_executor,
                tools_openai=[],
                narration_cadence=2,
            )
        )

        token_contents = [e.data["content"] for e in events if e.kind == "token"]
        assert any("bash" in t or "file" in t or "Moving on" in t for t in token_contents), (
            f"Expected narration token in event stream. Got tokens: {token_contents}"
        )
        # Narration call should have fired (call_count == 3: tools, narration, final)
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_narration_disabled_at_cadence_zero(self):
        """When narration_cadence=0, no extra API call should be made for narration."""
        call_count = 0
        rounds = [
            _make_stream_events(tool_calls=[_tc("t1", "bash"), _tc("t2", "bash")]),
            _make_stream_events(content="Done."),
        ]

        service = AsyncMock()

        async def _stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            nonlocal call_count
            idx = min(call_count, len(rounds) - 1)
            call_count += 1
            for event in rounds[idx]:
                yield event

        service.stream_chat = _stream_chat

        async def tool_executor(name: str, args: dict) -> dict:
            return {"output": "ok"}

        await _collect_events(
            run_agent_loop(
                ai_service=service,
                messages=[{"role": "user", "content": "do stuff"}],
                tool_executor=tool_executor,
                tools_openai=[],
                narration_cadence=0,
            )
        )

        # Only 2 calls: tool round + final response; no narration call
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_ephemeral_narration_prompt_not_in_history(self):
        """The injected narration user message must not remain in message history after narration."""
        captured_messages: list[list[dict[str, Any]]] = []
        call_count = 0
        rounds = [
            _make_stream_events(tool_calls=[_tc("t1", "bash")]),
            _make_stream_events(content="Progress update here."),
            _make_stream_events(content="Final answer."),
        ]

        service = AsyncMock()

        async def _stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            nonlocal call_count
            captured_messages.append([m.copy() for m in messages])
            idx = min(call_count, len(rounds) - 1)
            call_count += 1
            for event in rounds[idx]:
                yield event

        service.stream_chat = _stream_chat

        async def tool_executor(name: str, args: dict) -> dict:
            return {"output": "ok"}

        messages: list[dict[str, Any]] = [{"role": "user", "content": "do stuff"}]
        await _collect_events(
            run_agent_loop(
                ai_service=service,
                messages=messages,
                tool_executor=tool_executor,
                tools_openai=[],
                narration_cadence=1,
            )
        )

        # The final messages list must not contain any narration prompts
        narration_prompts = [m for m in messages if "summarize your progress" in m.get("content", "")]
        assert narration_prompts == [], f"Narration prompt was not removed from message history: {narration_prompts}"

    @pytest.mark.asyncio
    async def test_narration_exception_does_not_propagate_and_prompt_removed(self):
        """If the narration stream_chat call raises, the loop must continue and the prompt must be removed."""
        call_count = 0

        service = AsyncMock()

        async def _stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: 1 tool call (triggers cadence at narration_cadence=1)
                yield {"event": "tool_call", "data": _tc("t1", "bash")}
                yield {"event": "done", "data": {}}
            elif call_count == 2:
                # Second call: narration — raises unexpectedly
                raise RuntimeError("narration API error")
            else:
                # Third call: final response after exception recovery
                yield {"event": "token", "data": {"content": "Finished."}}
                yield {"event": "done", "data": {}}

        service.stream_chat = _stream_chat

        async def tool_executor(name: str, args: dict) -> dict:
            return {"output": "ok"}

        messages: list[dict[str, Any]] = [{"role": "user", "content": "do stuff"}]
        events = await _collect_events(
            run_agent_loop(
                ai_service=service,
                messages=messages,
                tool_executor=tool_executor,
                tools_openai=[],
                narration_cadence=1,
            )
        )

        # Loop must complete (done event present)
        assert any(e.kind == "done" for e in events), "Loop did not complete after narration exception"
        # Narration prompt must not remain in history
        narration_prompts = [m for m in messages if "summarize your progress" in m.get("content", "")]
        assert narration_prompts == [], f"Narration prompt leaked into history after exception: {narration_prompts}"

    @pytest.mark.asyncio
    async def test_narration_fires_on_cross_iteration_accumulation(self):
        """Cadence counter must accumulate across iterations (e.g. cadence=3, 2+1 tools across 2 rounds)."""
        call_count = 0
        rounds = [
            # Round 1: 2 tool calls (total=2, cadence=3 → no narration yet)
            _make_stream_events(tool_calls=[_tc("t1", "bash"), _tc("t2", "read_file")]),
            # Round 2: 1 more tool call (total=3, cadence=3 → narration fires)
            _make_stream_events(tool_calls=[_tc("t3", "write_file")]),
            # Round 3: narration response
            _make_stream_events(content="Found 2 files, writing results now."),
            # Round 4: final response
            _make_stream_events(content="Done."),
        ]

        service = AsyncMock()

        async def _stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            nonlocal call_count
            idx = min(call_count, len(rounds) - 1)
            call_count += 1
            for event in rounds[idx]:
                yield event

        service.stream_chat = _stream_chat

        async def tool_executor(name: str, args: dict) -> dict:
            return {"output": "ok"}

        events = await _collect_events(
            run_agent_loop(
                ai_service=service,
                messages=[{"role": "user", "content": "do stuff"}],
                tool_executor=tool_executor,
                tools_openai=[],
                narration_cadence=3,
            )
        )

        # 4 API calls: round1(tools), round2(tools), round3(narration), round4(final)
        assert call_count == 4, f"Expected 4 API calls (2 tool rounds + 1 narration + 1 final), got {call_count}"
        token_contents = [e.data["content"] for e in events if e.kind == "token"]
        assert any("Found" in t or "writing" in t for t in token_contents), (
            f"Expected narration token from cross-iteration cadence. Got: {token_contents}"
        )

    @pytest.mark.asyncio
    async def test_narration_skipped_when_cancelled(self):
        """Narration must not fire when cancel_event is set (#253)."""
        import asyncio

        call_count = 0
        # Round 1: 2 tool calls (hits cadence=2), Round 2: final text
        rounds = [
            _make_stream_events(tool_calls=[_tc("t1", "bash"), _tc("t2", "read_file")]),
            _make_stream_events(content="Done."),
        ]

        async def stream_chat(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            nonlocal call_count
            idx = call_count
            call_count += 1
            for evt in rounds[idx]:
                yield evt

        service = AsyncMock()
        service.stream_chat = stream_chat

        cancel = asyncio.Event()

        async def tool_executor(name, args):
            # Set cancel after tools run — before narration would fire
            cancel.set()
            return {"output": "ok"}

        events = await _collect_events(
            run_agent_loop(
                ai_service=service,
                messages=[{"role": "user", "content": "do stuff"}],
                tool_executor=tool_executor,
                tools_openai=[],
                narration_cadence=2,
                cancel_event=cancel,
            )
        )

        # Should be 1 API call (tool round), NOT 2 (no narration round)
        assert call_count == 1, f"Expected 1 API call (cancel should skip narration), got {call_count}"
        # Should end with a done event (from cancel path)
        done_events = [e for e in events if e.kind == "done"]
        assert len(done_events) >= 1

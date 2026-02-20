"""Tests for phase event forwarding in the agent loop (#203)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from anteroom.config import AIConfig
from anteroom.services.agent_loop import AgentEvent, run_agent_loop
from anteroom.services.ai_service import AIService


def _make_config(**overrides: Any) -> AIConfig:
    defaults = {
        "base_url": "http://localhost:11434/v1",
        "api_key": "test-key",
        "model": "gpt-4",
        "request_timeout": 120,
        "verify_ssl": True,
    }
    defaults.update(overrides)
    return AIConfig(**defaults)


def _make_ai_service() -> AIService:
    service = AIService.__new__(AIService)
    service.config = _make_config()
    service._token_provider = None
    service.client = MagicMock()
    return service


class TestAgentLoopPhaseForwarding:
    """Tests for phase event forwarding through the agent loop."""

    @pytest.mark.asyncio
    async def test_phase_events_forwarded_from_stream_chat(self) -> None:
        """Agent loop must forward phase events from ai_service.stream_chat."""
        ai_service = _make_ai_service()

        async def fake_stream_chat(messages: Any, **kwargs: Any):
            yield {"event": "phase", "data": {"phase": "connecting"}}
            yield {"event": "phase", "data": {"phase": "waiting"}}
            yield {"event": "token", "data": {"content": "hello"}}
            yield {"event": "done", "data": {}}

        ai_service.stream_chat = fake_stream_chat

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "hi"}],
            tool_executor=AsyncMock(),
            tools_openai=None,
        ):
            events.append(event)

        phase_events = [e for e in events if e.kind == "phase"]
        assert len(phase_events) == 2
        assert phase_events[0].data["phase"] == "connecting"
        assert phase_events[1].data["phase"] == "waiting"

    @pytest.mark.asyncio
    async def test_phase_events_order_preserved(self) -> None:
        """Phase events must appear before token events in the output."""
        ai_service = _make_ai_service()

        async def fake_stream_chat(messages: Any, **kwargs: Any):
            yield {"event": "phase", "data": {"phase": "connecting"}}
            yield {"event": "phase", "data": {"phase": "waiting"}}
            yield {"event": "token", "data": {"content": "hi"}}
            yield {"event": "done", "data": {}}

        ai_service.stream_chat = fake_stream_chat

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "test"}],
            tool_executor=AsyncMock(),
            tools_openai=None,
        ):
            events.append(event)

        kinds = [e.kind for e in events]
        # thinking comes first (from the agent loop), then phase events, then token
        thinking_idx = kinds.index("thinking")
        connecting_idx = kinds.index("phase")
        token_idx = kinds.index("token")
        assert thinking_idx < connecting_idx < token_idx

    @pytest.mark.asyncio
    async def test_phase_events_forwarded_with_tool_calls(self) -> None:
        """Phase events must be forwarded even when tool calls are present."""
        ai_service = _make_ai_service()
        call_count = 0

        async def fake_stream_chat(messages: Any, **kwargs: Any):
            nonlocal call_count
            call_count += 1
            yield {"event": "phase", "data": {"phase": "connecting"}}
            yield {"event": "phase", "data": {"phase": "waiting"}}
            if call_count == 1:
                yield {
                    "event": "tool_call",
                    "data": {"id": "call_1", "function_name": "bash", "arguments": {"command": "ls"}},
                }
            else:
                yield {"event": "token", "data": {"content": "done"}}
                yield {"event": "done", "data": {}}

        ai_service.stream_chat = fake_stream_chat

        async def fake_tool_executor(name: str, args: dict) -> dict:
            return {"stdout": "file.txt"}

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "list files"}],
            tool_executor=fake_tool_executor,
            tools_openai=[{"type": "function", "function": {"name": "bash"}}],
        ):
            events.append(event)

        phase_events = [e for e in events if e.kind == "phase"]
        # Two iterations = 4 phase events (2 per iteration)
        assert len(phase_events) == 4
        assert all(e.data["phase"] in ("connecting", "waiting") for e in phase_events)

    @pytest.mark.asyncio
    async def test_phase_events_not_stored_in_messages(self) -> None:
        """Phase events are display-only and must NOT be stored in the messages list."""
        ai_service = _make_ai_service()

        async def fake_stream_chat(messages: Any, **kwargs: Any):
            yield {"event": "phase", "data": {"phase": "connecting"}}
            yield {"event": "phase", "data": {"phase": "waiting"}}
            yield {"event": "token", "data": {"content": "hello"}}
            yield {"event": "done", "data": {}}

        ai_service.stream_chat = fake_stream_chat

        messages: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
        async for _ in run_agent_loop(
            ai_service=ai_service,
            messages=messages,
            tool_executor=AsyncMock(),
            tools_openai=None,
        ):
            pass

        # Messages should only contain the original user message
        # (no phase-related messages stored)
        for msg in messages:
            assert "phase" not in str(msg.get("content", "")).lower() or msg["role"] == "user"

"""Tests for auto-plan suggestion event emission in the agent loop (#265)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

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


class TestAutoPlanSuggestEvent:
    """Tests for auto_plan_suggest event emission when tool call threshold is crossed."""

    @pytest.mark.asyncio
    async def test_emitted_when_threshold_crossed(self) -> None:
        """auto_plan_suggest fires when total_tool_calls crosses the threshold."""
        ai_service = _make_ai_service()
        call_count = 0

        async def fake_stream_chat(messages: Any, **kwargs: Any):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                yield {
                    "event": "tool_call",
                    "data": {"id": f"call_{call_count}", "function_name": "bash", "arguments": {"command": "ls"}},
                }
                yield {"event": "done", "data": {}}
            else:
                yield {"event": "token", "data": {"content": "done"}}
                yield {"event": "done", "data": {}}

        ai_service.stream_chat = fake_stream_chat

        async def fake_tool_executor(name: str, args: dict) -> dict:
            return {"stdout": "ok"}

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "do something"}],
            tool_executor=fake_tool_executor,
            tools_openai=[{"type": "function", "function": {"name": "bash", "parameters": {}}}],
            auto_plan_threshold=2,
        ):
            events.append(event)

        suggest_events = [e for e in events if e.kind == "auto_plan_suggest"]
        assert len(suggest_events) == 1
        assert suggest_events[0].data["tool_calls"] >= 2

    @pytest.mark.asyncio
    async def test_not_emitted_when_threshold_zero(self) -> None:
        """auto_plan_threshold=0 disables the feature — no event emitted."""
        ai_service = _make_ai_service()
        call_count = 0

        async def fake_stream_chat(messages: Any, **kwargs: Any):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                yield {
                    "event": "tool_call",
                    "data": {"id": f"call_{call_count}", "function_name": "bash", "arguments": {"command": "ls"}},
                }
                yield {"event": "done", "data": {}}
            else:
                yield {"event": "token", "data": {"content": "done"}}
                yield {"event": "done", "data": {}}

        ai_service.stream_chat = fake_stream_chat

        async def fake_tool_executor(name: str, args: dict) -> dict:
            return {"stdout": "ok"}

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "do something"}],
            tool_executor=fake_tool_executor,
            tools_openai=[{"type": "function", "function": {"name": "bash", "parameters": {}}}],
            auto_plan_threshold=0,
        ):
            events.append(event)

        suggest_events = [e for e in events if e.kind == "auto_plan_suggest"]
        assert len(suggest_events) == 0

    @pytest.mark.asyncio
    async def test_one_shot_not_recurring(self) -> None:
        """Event fires only once even when tool calls continue past the threshold."""
        ai_service = _make_ai_service()
        call_count = 0

        async def fake_stream_chat(messages: Any, **kwargs: Any):
            nonlocal call_count
            call_count += 1
            if call_count <= 5:
                yield {
                    "event": "tool_call",
                    "data": {"id": f"call_{call_count}", "function_name": "bash", "arguments": {"command": "ls"}},
                }
                yield {"event": "done", "data": {}}
            else:
                yield {"event": "token", "data": {"content": "done"}}
                yield {"event": "done", "data": {}}

        ai_service.stream_chat = fake_stream_chat

        async def fake_tool_executor(name: str, args: dict) -> dict:
            return {"stdout": "ok"}

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "do something"}],
            tool_executor=fake_tool_executor,
            tools_openai=[{"type": "function", "function": {"name": "bash", "parameters": {}}}],
            auto_plan_threshold=2,
        ):
            events.append(event)

        suggest_events = [e for e in events if e.kind == "auto_plan_suggest"]
        assert len(suggest_events) == 1

    @pytest.mark.asyncio
    async def test_not_emitted_before_threshold(self) -> None:
        """No event when tool calls stay below the threshold."""
        ai_service = _make_ai_service()
        call_count = 0

        async def fake_stream_chat(messages: Any, **kwargs: Any):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield {
                    "event": "tool_call",
                    "data": {"id": "call_1", "function_name": "bash", "arguments": {"command": "ls"}},
                }
                yield {"event": "done", "data": {}}
            else:
                yield {"event": "token", "data": {"content": "done"}}
                yield {"event": "done", "data": {}}

        ai_service.stream_chat = fake_stream_chat

        async def fake_tool_executor(name: str, args: dict) -> dict:
            return {"stdout": "ok"}

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "do something"}],
            tool_executor=fake_tool_executor,
            tools_openai=[{"type": "function", "function": {"name": "bash", "parameters": {}}}],
            auto_plan_threshold=5,
        ):
            events.append(event)

        suggest_events = [e for e in events if e.kind == "auto_plan_suggest"]
        assert len(suggest_events) == 0

    @pytest.mark.asyncio
    async def test_event_data_contains_tool_call_count(self) -> None:
        """The event data includes the actual tool call count."""
        ai_service = _make_ai_service()
        call_count = 0

        async def fake_stream_chat(messages: Any, **kwargs: Any):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                yield {
                    "event": "tool_call",
                    "data": {"id": f"call_{call_count}", "function_name": "bash", "arguments": {"command": "ls"}},
                }
                yield {"event": "done", "data": {}}
            else:
                yield {"event": "token", "data": {"content": "done"}}
                yield {"event": "done", "data": {}}

        ai_service.stream_chat = fake_stream_chat

        async def fake_tool_executor(name: str, args: dict) -> dict:
            return {"stdout": "ok"}

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "do something"}],
            tool_executor=fake_tool_executor,
            tools_openai=[{"type": "function", "function": {"name": "bash", "parameters": {}}}],
            auto_plan_threshold=3,
        ):
            events.append(event)

        suggest_events = [e for e in events if e.kind == "auto_plan_suggest"]
        assert len(suggest_events) == 1
        assert suggest_events[0].data["tool_calls"] == 3

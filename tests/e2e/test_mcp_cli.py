"""MCP integration e2e tests via the CLI/programmatic path.

Tests McpManager directly (no REPL) and agent_loop with mocked AI + real MCP.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator
from unittest.mock import MagicMock

import pytest

from anteroom.services.agent_loop import AgentEvent, run_agent_loop
from anteroom.services.mcp_manager import McpManager
from tests.e2e.conftest import (
    MCP_EVERYTHING_SERVER,
    MCP_TIME_SERVER,
    requires_mcp,
    requires_npx,
    requires_uvx,
)

pytestmark = [pytest.mark.e2e, requires_mcp]


class TestMcpCliConnection:
    """Test McpManager startup and tool discovery."""

    @requires_uvx
    @pytest.mark.asyncio
    async def test_mcp_manager_connects_time_server(self) -> None:
        manager = McpManager([MCP_TIME_SERVER])
        try:
            await manager.startup()
            statuses = manager.get_server_statuses()
            assert "time" in statuses
            assert statuses["time"]["status"] == "connected", f"Time server status: {statuses['time']}"
            assert statuses["time"]["tool_count"] >= 1
        finally:
            await manager.shutdown()

    @requires_npx
    @pytest.mark.asyncio
    async def test_mcp_manager_connects_everything_server(self) -> None:
        manager = McpManager([MCP_EVERYTHING_SERVER])
        try:
            await manager.startup()
            statuses = manager.get_server_statuses()
            assert "everything" in statuses
            assert statuses["everything"]["status"] == "connected", f"Everything server: {statuses['everything']}"
            assert statuses["everything"]["tool_count"] >= 1
        finally:
            await manager.shutdown()

    @requires_uvx
    @pytest.mark.asyncio
    async def test_openai_tools_format(self) -> None:
        manager = McpManager([MCP_TIME_SERVER])
        try:
            await manager.startup()
            tools = manager.get_openai_tools()
            assert tools is not None
            assert len(tools) >= 1

            for tool in tools:
                assert tool["type"] == "function"
                assert "function" in tool
                func = tool["function"]
                assert "name" in func
                assert "description" in func
                assert "parameters" in func
        finally:
            await manager.shutdown()


class TestMcpCliToolExecution:
    """Test direct tool calls and agent_loop integration."""

    @requires_uvx
    @pytest.mark.asyncio
    async def test_call_get_current_time(self) -> None:
        manager = McpManager([MCP_TIME_SERVER])
        try:
            await manager.startup()
            result = await manager.call_tool("get_current_time", {"timezone": "UTC"})
            assert "content" in result or "result" in result, f"Unexpected result shape: {result}"
            text = result.get("content", result.get("result", ""))
            assert len(text) > 0, "Expected non-empty time result"
        finally:
            await manager.shutdown()

    @requires_npx
    @pytest.mark.asyncio
    async def test_call_everything_echo(self) -> None:
        manager = McpManager([MCP_EVERYTHING_SERVER])
        try:
            await manager.startup()
            result = await manager.call_tool("echo", {"message": "hello from cli test"})
            assert "content" in result or "result" in result, f"Unexpected result shape: {result}"
            text = result.get("content", result.get("result", ""))
            assert "hello from cli test" in text.lower(), f"Expected echo content in result: {text}"
        finally:
            await manager.shutdown()

    @requires_uvx
    @pytest.mark.asyncio
    async def test_agent_loop_with_mcp_tool(self) -> None:
        manager = McpManager([MCP_TIME_SERVER])
        try:
            await manager.startup()

            call_count = 0

            async def mock_stream(
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
                cancel_event: Any = None,
                extra_system_prompt: str | None = None,
            ) -> AsyncGenerator[dict[str, Any], None]:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    yield {
                        "event": "tool_call",
                        "data": {
                            "id": "call_agent_test",
                            "function_name": "get_current_time",
                            "arguments": {"timezone": "UTC"},
                        },
                    }
                    yield {"event": "done", "data": {}}
                else:
                    yield {"event": "token", "data": {"content": "The time is now."}}
                    yield {"event": "done", "data": {}}

            ai_service = MagicMock()
            ai_service.stream_chat = mock_stream

            async def tool_executor(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                return await manager.call_tool(tool_name, arguments)

            tools_openai = manager.get_openai_tools()
            messages: list[dict[str, Any]] = [{"role": "user", "content": "What time is it?"}]

            collected: list[AgentEvent] = []
            async for event in run_agent_loop(
                ai_service=ai_service,
                messages=messages,
                tool_executor=tool_executor,
                tools_openai=tools_openai,
            ):
                collected.append(event)

            kinds = [e.kind for e in collected]
            assert "tool_call_start" in kinds, f"Expected tool_call_start in {kinds}"
            assert "tool_call_end" in kinds, f"Expected tool_call_end in {kinds}"
            assert "done" in kinds, f"Expected done in {kinds}"

            end_events = [e for e in collected if e.kind == "tool_call_end"]
            assert len(end_events) >= 1
            assert end_events[0].data["status"] == "success"
            output = end_events[0].data["output"]
            assert "content" in output or "result" in output

        finally:
            await manager.shutdown()

    @requires_npx
    @pytest.mark.asyncio
    async def test_agent_loop_with_everything_add(self) -> None:
        manager = McpManager([MCP_EVERYTHING_SERVER])
        try:
            await manager.startup()

            all_tools = manager.get_all_tools()
            tool_names = [t["name"] for t in all_tools]
            if "add" not in tool_names:
                pytest.skip("'add' tool not available in everything server")

            call_count = 0

            async def mock_stream(
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
                cancel_event: Any = None,
                extra_system_prompt: str | None = None,
            ) -> AsyncGenerator[dict[str, Any], None]:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    yield {
                        "event": "tool_call",
                        "data": {
                            "id": "call_add_test",
                            "function_name": "add",
                            "arguments": {"a": 2, "b": 3},
                        },
                    }
                    yield {"event": "done", "data": {}}
                else:
                    yield {"event": "token", "data": {"content": "The answer is 5."}}
                    yield {"event": "done", "data": {}}

            ai_service = MagicMock()
            ai_service.stream_chat = mock_stream

            async def tool_executor(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                return await manager.call_tool(tool_name, arguments)

            tools_openai = manager.get_openai_tools()
            messages: list[dict[str, Any]] = [{"role": "user", "content": "What is 2 + 3?"}]

            collected: list[AgentEvent] = []
            async for event in run_agent_loop(
                ai_service=ai_service,
                messages=messages,
                tool_executor=tool_executor,
                tools_openai=tools_openai,
            ):
                collected.append(event)

            end_events = [e for e in collected if e.kind == "tool_call_end"]
            assert len(end_events) >= 1
            assert end_events[0].data["status"] == "success"

        finally:
            await manager.shutdown()

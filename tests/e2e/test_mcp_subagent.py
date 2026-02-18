"""E2e tests for MCP tools inside sub-agents (#100).

Wires a real McpManager + ToolRegistry + subagent.handle() to verify
MCP tools flow end-to-end into child agent contexts.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator
from unittest.mock import MagicMock, patch

import pytest

from anteroom.config import SafetyConfig
from anteroom.services.agent_loop import AgentEvent
from anteroom.services.mcp_manager import McpManager
from anteroom.tools import ToolRegistry, register_default_tools
from anteroom.tools.subagent import SubagentLimiter, handle
from tests.e2e.conftest import (
    MCP_TIME_SERVER,
    requires_uvx,
)

pytestmark = [pytest.mark.e2e, requires_uvx]


def _mock_ai() -> MagicMock:
    mock = MagicMock()
    mock.config = MagicMock()
    mock.config.model = "gpt-4"
    mock._token_provider = None
    return mock


class TestSubagentMcpToolList:
    """Verify MCP tool definitions are visible to sub-agents."""

    @pytest.mark.asyncio
    async def test_mcp_tools_appear_in_child_tool_list(self) -> None:
        """Sub-agent should receive MCP tool definitions alongside built-in tools."""
        manager = McpManager([MCP_TIME_SERVER])
        try:
            await manager.startup()

            registry = ToolRegistry()
            register_default_tools(registry)

            captured_tools: list[dict] = []

            async def mock_agent_loop(**kwargs: Any) -> AsyncGenerator[AgentEvent, None]:
                captured_tools.extend(kwargs.get("tools_openai", []))
                yield AgentEvent(kind="done", data={})

            with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
                with patch("anteroom.tools.subagent.AIService"):
                    await handle(
                        prompt="What time is it?",
                        _ai_service=_mock_ai(),
                        _tool_registry=registry,
                        _mcp_manager=manager,
                        _depth=0,
                        _limiter=SubagentLimiter(),
                    )

            tool_names = [t["function"]["name"] for t in captured_tools]
            # Built-in tools should be present
            assert "read_file" in tool_names
            assert "run_agent" in tool_names
            # MCP tool from the time server should also be present
            assert "get_current_time" in tool_names

        finally:
            await manager.shutdown()

    @pytest.mark.asyncio
    async def test_mcp_tool_count_matches_manager(self) -> None:
        """Number of MCP tools in child list should match what the manager reports."""
        manager = McpManager([MCP_TIME_SERVER])
        try:
            await manager.startup()

            registry = ToolRegistry()
            register_default_tools(registry)
            builtin_count = len(registry.get_openai_tools())
            mcp_count = len(manager.get_openai_tools() or [])

            captured_tools: list[dict] = []

            async def mock_agent_loop(**kwargs: Any) -> AsyncGenerator[AgentEvent, None]:
                captured_tools.extend(kwargs.get("tools_openai", []))
                yield AgentEvent(kind="done", data={})

            with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
                with patch("anteroom.tools.subagent.AIService"):
                    await handle(
                        prompt="test",
                        _ai_service=_mock_ai(),
                        _tool_registry=registry,
                        _mcp_manager=manager,
                        _depth=0,
                        _limiter=SubagentLimiter(),
                    )

            assert len(captured_tools) == builtin_count + mcp_count

        finally:
            await manager.shutdown()


class TestSubagentMcpToolExecution:
    """Verify sub-agents can actually call MCP tools via a real server."""

    @pytest.mark.asyncio
    async def test_subagent_calls_mcp_tool_successfully(self) -> None:
        """Child executor should route MCP tool calls to the real MCP server."""
        manager = McpManager([MCP_TIME_SERVER])
        try:
            await manager.startup()

            registry = ToolRegistry()
            register_default_tools(registry)
            registry.set_safety_config(SafetyConfig(approval_mode="auto"))

            mcp_results: list[dict] = []

            async def mock_agent_loop(**kwargs: Any) -> AsyncGenerator[AgentEvent, None]:
                executor = kwargs["tool_executor"]
                # Call the real MCP tool through the child executor
                result = await executor("get_current_time", {"timezone": "UTC"})
                mcp_results.append(result)
                yield AgentEvent(kind="done", data={})

            with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
                with patch("anteroom.tools.subagent.AIService"):
                    result = await handle(
                        prompt="What time is it in UTC?",
                        _ai_service=_mock_ai(),
                        _tool_registry=registry,
                        _mcp_manager=manager,
                        _depth=0,
                        _limiter=SubagentLimiter(),
                    )

            # The handle() result should not have an error
            assert "error" not in result, f"Unexpected error: {result.get('error')}"

            # The MCP tool should have returned real time data
            assert len(mcp_results) == 1
            assert "content" in mcp_results[0] or "result" in mcp_results[0]
            text = mcp_results[0].get("content", mcp_results[0].get("result", ""))
            assert len(text) > 0, "Expected non-empty time result from MCP server"

        finally:
            await manager.shutdown()

    @pytest.mark.asyncio
    async def test_subagent_mcp_tool_tracked_in_tool_calls(self) -> None:
        """MCP tool calls from sub-agents should appear in tool_calls_made."""
        manager = McpManager([MCP_TIME_SERVER])
        try:
            await manager.startup()

            registry = ToolRegistry()
            register_default_tools(registry)
            registry.set_safety_config(SafetyConfig(approval_mode="auto"))

            async def mock_agent_loop(**kwargs: Any) -> AsyncGenerator[AgentEvent, None]:
                executor = kwargs["tool_executor"]
                await executor("get_current_time", {"timezone": "UTC"})
                yield AgentEvent(
                    kind="tool_call_start",
                    data={"tool_name": "get_current_time", "id": "call_1", "arguments": {}},
                )
                yield AgentEvent(kind="done", data={})

            with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
                with patch("anteroom.tools.subagent.AIService"):
                    result = await handle(
                        prompt="What time is it?",
                        _ai_service=_mock_ai(),
                        _tool_registry=registry,
                        _mcp_manager=manager,
                        _depth=0,
                        _limiter=SubagentLimiter(),
                    )

            assert "get_current_time" in result["tool_calls_made"]

        finally:
            await manager.shutdown()

    @pytest.mark.asyncio
    async def test_subagent_builtin_and_mcp_tools_coexist(self) -> None:
        """Sub-agent should be able to call both built-in and MCP tools in one session."""
        manager = McpManager([MCP_TIME_SERVER])
        try:
            await manager.startup()

            registry = ToolRegistry()
            register_default_tools(registry)
            registry.set_safety_config(SafetyConfig(approval_mode="auto"))

            call_results: dict[str, Any] = {}

            async def mock_agent_loop(**kwargs: Any) -> AsyncGenerator[AgentEvent, None]:
                executor = kwargs["tool_executor"]
                # Call a built-in tool
                builtin_result = await executor("glob_files", {"pattern": "*.py", "path": "/tmp"})
                call_results["builtin"] = builtin_result
                # Call an MCP tool
                mcp_result = await executor("get_current_time", {"timezone": "UTC"})
                call_results["mcp"] = mcp_result
                yield AgentEvent(kind="done", data={})

            with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
                with patch("anteroom.tools.subagent.AIService"):
                    result = await handle(
                        prompt="List py files and get the time",
                        _ai_service=_mock_ai(),
                        _tool_registry=registry,
                        _mcp_manager=manager,
                        _depth=0,
                        _limiter=SubagentLimiter(),
                    )

            assert "error" not in result
            # Built-in tool should have returned something (glob result)
            assert "builtin" in call_results
            # MCP tool should have returned time data
            assert "mcp" in call_results
            mcp_text = call_results["mcp"].get("content", call_results["mcp"].get("result", ""))
            assert len(mcp_text) > 0

        finally:
            await manager.shutdown()


class TestSubagentMcpSafetyGate:
    """Verify safety gates work for MCP tools inside sub-agents with real registry."""

    @pytest.mark.asyncio
    async def test_mcp_tool_denied_by_config(self) -> None:
        """MCP tool on denied_tools list should be hard-blocked in sub-agent."""
        manager = McpManager([MCP_TIME_SERVER])
        try:
            await manager.startup()

            registry = ToolRegistry()
            register_default_tools(registry)
            registry.set_safety_config(
                SafetyConfig(
                    approval_mode="auto",
                    denied_tools=["get_current_time"],
                )
            )

            denied_results: list[dict] = []

            async def mock_agent_loop(**kwargs: Any) -> AsyncGenerator[AgentEvent, None]:
                executor = kwargs["tool_executor"]
                result = await executor("get_current_time", {"timezone": "UTC"})
                denied_results.append(result)
                yield AgentEvent(kind="done", data={})

            with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
                with patch("anteroom.tools.subagent.AIService"):
                    await handle(
                        prompt="What time is it?",
                        _ai_service=_mock_ai(),
                        _tool_registry=registry,
                        _mcp_manager=manager,
                        _depth=0,
                        _limiter=SubagentLimiter(),
                    )

            assert len(denied_results) == 1
            assert "blocked" in denied_results[0].get("error", "").lower() or "safety_blocked" in denied_results[0]

        finally:
            await manager.shutdown()

    @pytest.mark.asyncio
    async def test_mcp_tool_approved_by_callback(self) -> None:
        """MCP tool requiring approval should succeed when callback approves."""
        manager = McpManager([MCP_TIME_SERVER])
        try:
            await manager.startup()

            registry = ToolRegistry()
            register_default_tools(registry)
            registry.set_safety_config(SafetyConfig(approval_mode="ask"))

            approval_count = 0

            async def auto_approve(verdict: Any) -> bool:
                nonlocal approval_count
                approval_count += 1
                return True

            approved_results: list[dict] = []

            async def mock_agent_loop(**kwargs: Any) -> AsyncGenerator[AgentEvent, None]:
                executor = kwargs["tool_executor"]
                result = await executor("get_current_time", {"timezone": "UTC"})
                approved_results.append(result)
                yield AgentEvent(kind="done", data={})

            with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
                with patch("anteroom.tools.subagent.AIService"):
                    await handle(
                        prompt="What time is it?",
                        _ai_service=_mock_ai(),
                        _tool_registry=registry,
                        _mcp_manager=manager,
                        _confirm_callback=auto_approve,
                        _depth=0,
                        _limiter=SubagentLimiter(),
                    )

            assert approval_count >= 1
            assert len(approved_results) == 1
            text = approved_results[0].get("content", approved_results[0].get("result", ""))
            assert len(text) > 0, "Expected real time data after approval"

        finally:
            await manager.shutdown()


class TestSubagentMcpPropagation:
    """Verify MCP manager propagates to nested sub-agents."""

    @pytest.mark.asyncio
    async def test_nested_subagent_receives_mcp_manager(self) -> None:
        """When child calls run_agent, _mcp_manager should be in the injected args."""
        manager = McpManager([MCP_TIME_SERVER])
        try:
            await manager.startup()

            registry = ToolRegistry()
            register_default_tools(registry)

            captured_mcp_refs: list[Any] = []

            async def mock_call_tool(name: str, args: dict, confirm_callback: Any = None) -> dict:
                if name == "run_agent":
                    captured_mcp_refs.append(args.get("_mcp_manager"))
                return {"output": "ok"}

            registry.call_tool = mock_call_tool  # type: ignore[assignment]

            async def mock_agent_loop(**kwargs: Any) -> AsyncGenerator[AgentEvent, None]:
                executor = kwargs["tool_executor"]
                await executor("run_agent", {"prompt": "nested task"})
                yield AgentEvent(kind="done", data={})

            with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
                with patch("anteroom.tools.subagent.AIService"):
                    await handle(
                        prompt="parent task",
                        _ai_service=_mock_ai(),
                        _tool_registry=registry,
                        _mcp_manager=manager,
                        _depth=0,
                        _agent_id="agent-1",
                        _limiter=SubagentLimiter(),
                    )

            assert len(captured_mcp_refs) == 1
            assert captured_mcp_refs[0] is manager

        finally:
            await manager.shutdown()

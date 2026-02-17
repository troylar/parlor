"""MCP CLI approval flow e2e tests.

Mirrors test_mcp_approval.py but exercises the CLI's tool_executor pattern:
- ToolRegistry.check_safety() for tier-based approval
- Mocked confirm callback (replacing interactive prompt_toolkit input)
- Real McpManager + real MCP server tool execution
- agent_loop integration with safety gates

Tests all approval outcomes:
1. Auto mode — MCP tool executes without confirm callback
2. Approve once — confirm returns True, tool executes
3. Deny — confirm returns False, tool returns error
4. Session scope — confirm grants session permission, subsequent calls auto-approve
5. Always scope — confirm grants session + persists to config
6. Hard-denied tools — denied_tools config blocks without callback
7. Unknown tool — raises ValueError
"""

from __future__ import annotations

from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anteroom.config import SafetyConfig
from anteroom.services.agent_loop import AgentEvent, run_agent_loop
from anteroom.services.mcp_manager import McpManager
from anteroom.tools import ToolRegistry
from anteroom.tools.safety import SafetyVerdict
from tests.e2e.conftest import MCP_TIME_SERVER, mock_tool_call_stream, requires_mcp, requires_uvx

pytestmark = [pytest.mark.e2e, requires_mcp, requires_uvx]


def _build_cli_tool_executor(
    tool_registry: ToolRegistry,
    mcp_manager: McpManager,
    confirm_callback: Any,
) -> Any:
    """Build a tool_executor closure that mirrors the CLI REPL's pattern.

    This replicates src/anteroom/cli/repl.py:500-513 exactly, substituting
    the interactive _confirm_destructive with a mocked callback.
    """

    async def tool_executor(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_registry.has_tool(tool_name):
            return await tool_registry.call_tool(tool_name, arguments)
        if mcp_manager:
            verdict = tool_registry.check_safety(tool_name, arguments)
            if verdict and verdict.needs_approval:
                if verdict.hard_denied:
                    return {"error": f"Tool '{tool_name}' is blocked by configuration", "safety_blocked": True}
                confirmed = await confirm_callback(verdict)
                if not confirmed:
                    return {"error": "Operation denied by user", "exit_code": -1}
            return await mcp_manager.call_tool(tool_name, arguments)
        raise ValueError(f"Unknown tool: {tool_name}")

    return tool_executor


@pytest.fixture()
async def mcp_manager_with_time() -> AsyncGenerator[McpManager, None]:
    """Start a real McpManager with the time server."""
    manager = McpManager([MCP_TIME_SERVER])
    await manager.startup()
    yield manager
    await manager.shutdown()


class TestCliAutoMode:
    """In auto mode, MCP tools execute without any approval check."""

    @pytest.mark.asyncio
    async def test_auto_mode_no_approval_needed(self, mcp_manager_with_time: McpManager) -> None:
        registry = ToolRegistry()
        registry.set_safety_config(SafetyConfig(approval_mode="auto"))

        confirm = AsyncMock(return_value=True)
        executor = _build_cli_tool_executor(registry, mcp_manager_with_time, confirm)

        result = await executor("get_current_time", {"timezone": "UTC"})
        assert "content" in result or "result" in result
        confirm.assert_not_called()


class TestCliApproveOnce:
    """Approve once — confirm returns True, tool executes, next call still needs approval."""

    @pytest.mark.asyncio
    async def test_approve_once_executes_tool(self, mcp_manager_with_time: McpManager) -> None:
        registry = ToolRegistry()
        registry.set_safety_config(SafetyConfig(approval_mode="ask_for_writes"))

        confirm = AsyncMock(return_value=True)
        executor = _build_cli_tool_executor(registry, mcp_manager_with_time, confirm)

        result = await executor("get_current_time", {"timezone": "UTC"})

        assert "error" not in result
        text = result.get("content", result.get("result", ""))
        assert len(text) > 0
        confirm.assert_called_once()

        # Verify the SafetyVerdict was passed to the callback
        verdict: SafetyVerdict = confirm.call_args[0][0]
        assert verdict.tool_name == "get_current_time"
        assert verdict.needs_approval is True

    @pytest.mark.asyncio
    async def test_approve_once_does_not_persist(self, mcp_manager_with_time: McpManager) -> None:
        """After approving once, the next call should still require approval."""
        registry = ToolRegistry()
        registry.set_safety_config(SafetyConfig(approval_mode="ask_for_writes"))

        confirm = AsyncMock(return_value=True)
        executor = _build_cli_tool_executor(registry, mcp_manager_with_time, confirm)

        await executor("get_current_time", {"timezone": "UTC"})
        assert confirm.call_count == 1

        # Second call should also require approval
        await executor("get_current_time", {"timezone": "America/New_York"})
        assert confirm.call_count == 2


class TestCliDeny:
    """Deny — confirm returns False, tool returns error."""

    @pytest.mark.asyncio
    async def test_deny_returns_error(self, mcp_manager_with_time: McpManager) -> None:
        registry = ToolRegistry()
        registry.set_safety_config(SafetyConfig(approval_mode="ask_for_writes"))

        confirm = AsyncMock(return_value=False)
        executor = _build_cli_tool_executor(registry, mcp_manager_with_time, confirm)

        result = await executor("get_current_time", {"timezone": "UTC"})

        assert "error" in result
        assert "denied" in result["error"].lower()
        confirm.assert_called_once()

    @pytest.mark.asyncio
    async def test_deny_does_not_execute_tool(self, mcp_manager_with_time: McpManager) -> None:
        """When denied, the MCP tool should not be called at all."""
        registry = ToolRegistry()
        registry.set_safety_config(SafetyConfig(approval_mode="ask_for_writes"))

        confirm = AsyncMock(return_value=False)

        with patch.object(mcp_manager_with_time, "call_tool", wraps=mcp_manager_with_time.call_tool) as spy:
            executor = _build_cli_tool_executor(registry, mcp_manager_with_time, confirm)
            await executor("get_current_time", {"timezone": "UTC"})
            spy.assert_not_called()


class TestCliSessionScope:
    """Session scope — confirm grants session permission, subsequent calls auto-approve."""

    @pytest.mark.asyncio
    async def test_session_scope_first_call_approved(self, mcp_manager_with_time: McpManager) -> None:
        registry = ToolRegistry()
        registry.set_safety_config(SafetyConfig(approval_mode="ask_for_writes"))

        async def confirm_with_session_grant(verdict: SafetyVerdict) -> bool:
            registry.grant_session_permission(verdict.tool_name)
            return True

        executor = _build_cli_tool_executor(registry, mcp_manager_with_time, confirm_with_session_grant)

        result = await executor("get_current_time", {"timezone": "UTC"})
        assert "error" not in result
        assert registry.check_safety("get_current_time", {}) is None, "Expected session permission to bypass approval"

    @pytest.mark.asyncio
    async def test_session_scope_subsequent_auto_approved(self, mcp_manager_with_time: McpManager) -> None:
        """After session grant, check_safety returns None so no confirm callback is invoked."""
        registry = ToolRegistry()
        registry.set_safety_config(SafetyConfig(approval_mode="ask_for_writes"))

        call_count = 0

        async def confirm_with_session_grant(verdict: SafetyVerdict) -> bool:
            nonlocal call_count
            call_count += 1
            registry.grant_session_permission(verdict.tool_name)
            return True

        executor = _build_cli_tool_executor(registry, mcp_manager_with_time, confirm_with_session_grant)

        # First call — requires approval
        await executor("get_current_time", {"timezone": "UTC"})
        assert call_count == 1

        # Second call — session permission should bypass approval
        result = await executor("get_current_time", {"timezone": "America/New_York"})
        assert call_count == 1  # confirm not called again
        assert "error" not in result


class TestCliAlwaysScope:
    """Always scope — grants session permission + persists to config."""

    @pytest.mark.asyncio
    async def test_always_scope_grants_and_persists(self, mcp_manager_with_time: McpManager) -> None:
        registry = ToolRegistry()
        registry.set_safety_config(SafetyConfig(approval_mode="ask_for_writes"))

        persist_mock = MagicMock()

        async def confirm_with_always_grant(verdict: SafetyVerdict) -> bool:
            registry.grant_session_permission(verdict.tool_name)
            persist_mock(verdict.tool_name)
            return True

        executor = _build_cli_tool_executor(registry, mcp_manager_with_time, confirm_with_always_grant)

        result = await executor("get_current_time", {"timezone": "UTC"})
        assert "error" not in result
        assert registry.check_safety("get_current_time", {}) is None, "Expected session permission to bypass approval"
        persist_mock.assert_called_once_with("get_current_time")

    @pytest.mark.asyncio
    async def test_always_scope_subsequent_auto_approved(self, mcp_manager_with_time: McpManager) -> None:
        """After always grant, subsequent calls skip approval."""
        registry = ToolRegistry()
        registry.set_safety_config(SafetyConfig(approval_mode="ask_for_writes"))

        call_count = 0

        async def confirm_with_always_grant(verdict: SafetyVerdict) -> bool:
            nonlocal call_count
            call_count += 1
            registry.grant_session_permission(verdict.tool_name)
            return True

        executor = _build_cli_tool_executor(registry, mcp_manager_with_time, confirm_with_always_grant)

        await executor("get_current_time", {"timezone": "UTC"})
        result = await executor("get_current_time", {"timezone": "Europe/London"})
        assert call_count == 1
        assert "error" not in result


class TestCliHardDenied:
    """Hard-denied tools — denied_tools config blocks without invoking the callback."""

    @pytest.mark.asyncio
    async def test_denied_tool_blocked(self, mcp_manager_with_time: McpManager) -> None:
        registry = ToolRegistry()
        registry.set_safety_config(
            SafetyConfig(
                approval_mode="ask_for_writes",
                denied_tools=["get_current_time"],
            )
        )

        confirm = AsyncMock(return_value=True)
        executor = _build_cli_tool_executor(registry, mcp_manager_with_time, confirm)

        result = await executor("get_current_time", {"timezone": "UTC"})

        assert "error" in result
        assert result.get("safety_blocked") is True
        assert "blocked by configuration" in result["error"]
        confirm.assert_not_called()


class TestCliUnknownTool:
    """Unknown tool — not in registry and no MCP manager raises ValueError."""

    @pytest.mark.asyncio
    async def test_unknown_tool_raises_error(self) -> None:
        registry = ToolRegistry()
        registry.set_safety_config(SafetyConfig(approval_mode="ask_for_writes"))

        confirm = AsyncMock(return_value=True)
        executor = _build_cli_tool_executor(registry, None, confirm)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="Unknown tool"):
            await executor("nonexistent_tool", {})


class TestCliAgentLoopWithApproval:
    """Integration: run_agent_loop with the CLI tool_executor pattern + safety gates."""

    @pytest.mark.asyncio
    async def test_agent_loop_approve_once(self, mcp_manager_with_time: McpManager) -> None:
        """Agent loop with an MCP tool call that requires approval (approved)."""
        registry = ToolRegistry()
        registry.set_safety_config(SafetyConfig(approval_mode="ask_for_writes"))

        confirm = AsyncMock(return_value=True)
        executor = _build_cli_tool_executor(registry, mcp_manager_with_time, confirm)

        ai_service = MagicMock()
        ai_service.stream_chat = mock_tool_call_stream(
            tool_name="get_current_time",
            arguments={"timezone": "UTC"},
        )

        messages: list[dict[str, Any]] = [{"role": "user", "content": "What time is it?"}]
        tools_openai = mcp_manager_with_time.get_openai_tools()

        collected: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=messages,
            tool_executor=executor,
            tools_openai=tools_openai,
        ):
            collected.append(event)

        kinds = [e.kind for e in collected]
        assert "tool_call_start" in kinds
        assert "tool_call_end" in kinds
        assert "done" in kinds

        end_events = [e for e in collected if e.kind == "tool_call_end"]
        assert end_events[0].data["status"] == "success"
        confirm.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_loop_deny(self, mcp_manager_with_time: McpManager) -> None:
        """Agent loop with an MCP tool call that is denied."""
        registry = ToolRegistry()
        registry.set_safety_config(SafetyConfig(approval_mode="ask_for_writes"))

        confirm = AsyncMock(return_value=False)
        executor = _build_cli_tool_executor(registry, mcp_manager_with_time, confirm)

        ai_service = MagicMock()
        ai_service.stream_chat = mock_tool_call_stream(
            tool_name="get_current_time",
            arguments={"timezone": "UTC"},
        )

        messages: list[dict[str, Any]] = [{"role": "user", "content": "What time is it?"}]
        tools_openai = mcp_manager_with_time.get_openai_tools()

        collected: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=messages,
            tool_executor=executor,
            tools_openai=tools_openai,
        ):
            collected.append(event)

        kinds = [e.kind for e in collected]
        assert "tool_call_end" in kinds

        end_events = [e for e in collected if e.kind == "tool_call_end"]
        output = end_events[0].data.get("output", {})
        assert "error" in output
        confirm.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_loop_session_scope_second_call_no_prompt(self, mcp_manager_with_time: McpManager) -> None:
        """After session grant in agent_loop, a second tool call auto-approves."""
        registry = ToolRegistry()
        registry.set_safety_config(SafetyConfig(approval_mode="ask_for_writes"))

        call_count = 0

        async def confirm_with_session(verdict: SafetyVerdict) -> bool:
            nonlocal call_count
            call_count += 1
            registry.grant_session_permission(verdict.tool_name)
            return True

        executor = _build_cli_tool_executor(registry, mcp_manager_with_time, confirm_with_session)

        # First agent_loop run — requires approval
        ai_service = MagicMock()
        ai_service.stream_chat = mock_tool_call_stream(
            tool_name="get_current_time",
            arguments={"timezone": "UTC"},
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": "Time?"}]
        tools_openai = mcp_manager_with_time.get_openai_tools()

        async for _ in run_agent_loop(
            ai_service=ai_service,
            messages=messages,
            tool_executor=executor,
            tools_openai=tools_openai,
        ):
            pass

        assert call_count == 1

        # Second agent_loop run — should auto-approve
        ai_service2 = MagicMock()
        ai_service2.stream_chat = mock_tool_call_stream(
            tool_name="get_current_time",
            arguments={"timezone": "America/Chicago"},
        )
        messages2: list[dict[str, Any]] = [{"role": "user", "content": "Time in Chicago?"}]

        collected: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service2,
            messages=messages2,
            tool_executor=executor,
            tools_openai=tools_openai,
        ):
            collected.append(event)

        assert call_count == 1  # confirm not called again
        end_events = [e for e in collected if e.kind == "tool_call_end"]
        assert end_events[0].data["status"] == "success"

"""Tests for MCP manager per-server lifecycle."""

from __future__ import annotations

import asyncio
import sys
from contextlib import AsyncExitStack
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anteroom.config import McpServerConfig
from anteroom.services.mcp_manager import McpManager


class TestMcpManagerInit:
    def test_configs_stored_as_dict(self) -> None:
        configs = [
            McpServerConfig(name="server-a", transport="stdio", command="echo"),
            McpServerConfig(name="server-b", transport="stdio", command="cat"),
        ]
        mgr = McpManager(configs)
        assert "server-a" in mgr._configs
        assert "server-b" in mgr._configs

    def test_empty_configs(self) -> None:
        mgr = McpManager([])
        assert mgr._configs == {}


class TestMcpManagerLifecycle:
    @pytest.fixture()
    def manager(self) -> McpManager:
        configs = [
            McpServerConfig(name="test-server", transport="stdio", command="echo"),
        ]
        return McpManager(configs)

    @pytest.mark.asyncio()
    async def test_startup_without_mcp_sdk(self, manager: McpManager) -> None:
        with patch.dict("sys.modules", {"mcp": None}):
            await manager.startup()
        assert manager.get_all_tools() == []

    @pytest.mark.asyncio()
    async def test_get_server_statuses_default(self, manager: McpManager) -> None:
        statuses = manager.get_server_statuses()
        assert "test-server" in statuses
        assert statuses["test-server"]["status"] == "disconnected"
        assert statuses["test-server"]["transport"] == "stdio"

    @pytest.mark.asyncio()
    async def test_disconnect_unknown_server_raises(self, manager: McpManager) -> None:
        with pytest.raises(ValueError, match="Unknown MCP server"):
            await manager.disconnect_server("nonexistent")

    @pytest.mark.asyncio()
    async def test_connect_unknown_server_raises(self, manager: McpManager) -> None:
        with pytest.raises(ValueError, match="Unknown MCP server"):
            await manager.connect_server("nonexistent")

    @pytest.mark.asyncio()
    async def test_get_openai_tools_empty(self, manager: McpManager) -> None:
        assert manager.get_openai_tools() is None

    @pytest.mark.asyncio()
    async def test_get_all_tools_empty(self, manager: McpManager) -> None:
        assert manager.get_all_tools() == []

    @pytest.mark.asyncio()
    async def test_shutdown_empty(self, manager: McpManager) -> None:
        await manager.shutdown()
        assert manager._sessions == {}
        assert manager._server_tools == {}

    def test_get_tool_server_name_unknown(self, manager: McpManager) -> None:
        assert manager.get_tool_server_name("nonexistent") == "unknown"


class TestMcpManagerToolMap:
    def test_rebuild_tool_map(self) -> None:
        mgr = McpManager([])
        mgr._server_tools = {
            "server-a": [
                {"name": "tool1", "server_name": "server-a", "description": "", "input_schema": {}},
                {"name": "tool2", "server_name": "server-a", "description": "", "input_schema": {}},
            ],
            "server-b": [
                {"name": "tool3", "server_name": "server-b", "description": "", "input_schema": {}},
            ],
        }
        mgr._rebuild_tool_map()
        assert mgr._tool_to_server == {"tool1": "server-a", "tool2": "server-a", "tool3": "server-b"}

    def test_get_all_tools_flattens(self) -> None:
        mgr = McpManager([])
        mgr._server_tools = {
            "server-a": [
                {"name": "tool1", "server_name": "server-a", "description": "d1", "input_schema": {}},
            ],
            "server-b": [
                {"name": "tool2", "server_name": "server-b", "description": "d2", "input_schema": {}},
            ],
        }
        tools = mgr.get_all_tools()
        assert len(tools) == 2
        names = {t["name"] for t in tools}
        assert names == {"tool1", "tool2"}

    def test_get_openai_tools_format(self) -> None:
        mgr = McpManager([])
        mgr._server_tools = {
            "server-a": [
                {
                    "name": "my_tool",
                    "server_name": "server-a",
                    "description": "Does stuff",
                    "input_schema": {"type": "object"},
                },
            ],
        }
        mgr._rebuild_tool_map()
        openai_tools = mgr.get_openai_tools()
        assert openai_tools is not None
        assert len(openai_tools) == 1
        assert openai_tools[0]["type"] == "function"
        assert openai_tools[0]["function"]["name"] == "my_tool"
        assert openai_tools[0]["function"]["description"] == "Does stuff"

    def test_disconnect_removes_tools(self) -> None:
        configs = [
            McpServerConfig(name="server-a", transport="stdio", command="echo"),
        ]
        mgr = McpManager(configs)
        mgr._server_tools = {
            "server-a": [
                {"name": "tool1", "server_name": "server-a", "description": "", "input_schema": {}},
            ],
        }
        mgr._tool_to_server = {"tool1": "server-a"}

        # Simulate disconnect's cleanup (without the async stack close)
        mgr._sessions.pop("server-a", None)
        mgr._server_tools.pop("server-a", None)
        mgr._rebuild_tool_map()

        assert mgr.get_all_tools() == []
        assert mgr._tool_to_server == {}


class TestMcpManagerCallTool:
    """Tests for call_tool error handling and argument passthrough."""

    @pytest.mark.asyncio()
    async def test_multiline_args_accepted(self) -> None:
        """MCP tool args with newlines (e.g. Jira descriptions) must not be rejected."""
        mgr = McpManager([])
        mgr._tool_to_server = {"update_issue": "jira"}

        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.content = [AsyncMock(text="OK")]
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        mgr._sessions = {"jira": mock_session}

        result = await mgr.call_tool(
            "update_issue",
            {
                "description": "Line 1\nLine 2\nLine 3\r\nLine 4",
                "summary": "Test with special chars: $100 & (parens) {braces}",
            },
        )
        assert result["content"] == "OK"
        mock_session.call_tool.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_tool_not_found_lists_connected_servers(self) -> None:
        """Error for missing tool should list connected servers."""
        mgr = McpManager([])
        mgr._sessions = {"jira": AsyncMock(), "slack": AsyncMock()}

        with pytest.raises(ValueError, match="MCP tool 'nonexistent' not available") as exc_info:
            await mgr.call_tool("nonexistent", {})

        msg = str(exc_info.value)
        assert "jira" in msg
        assert "slack" in msg

    @pytest.mark.asyncio()
    async def test_tool_not_found_no_servers(self) -> None:
        """Error for missing tool with no connected servers shows (none)."""
        mgr = McpManager([])
        mgr._sessions = {}

        with pytest.raises(ValueError, match=r"\(none\)"):
            await mgr.call_tool("anything", {})

    @pytest.mark.asyncio()
    async def test_server_error_includes_context(self) -> None:
        """Server-side errors should include server name and tool name."""
        mgr = McpManager([])
        mgr._tool_to_server = {"broken_tool": "my-server"}

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(side_effect=RuntimeError("connection reset"))
        mgr._sessions = {"my-server": mock_session}

        with pytest.raises(ValueError, match="MCP server 'my-server'") as exc_info:
            await mgr.call_tool("broken_tool", {"arg": "value"})

        msg = str(exc_info.value)
        assert "broken_tool" in msg
        assert "connection reset" not in msg  # raw exception not exposed to caller

    @pytest.mark.asyncio()
    async def test_server_error_chains_original(self) -> None:
        """Server-side errors should chain the original exception."""
        mgr = McpManager([])
        mgr._tool_to_server = {"my_tool": "srv"}

        original = RuntimeError("original error")
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(side_effect=original)
        mgr._sessions = {"srv": mock_session}

        with pytest.raises(ValueError) as exc_info:
            await mgr.call_tool("my_tool", {})

        assert exc_info.value.__cause__ is original


class TestMcpManagerFailedConnection:
    """Tests for MCP connection failures — stack must be cleaned up."""

    @pytest.mark.asyncio()
    async def test_connect_one_exception_closes_stack(self) -> None:
        """When _connect_one raises, the AsyncExitStack must be closed."""
        config = McpServerConfig(name="bad-server", transport="stdio", command="echo")
        mgr = McpManager([config])

        mock_stack = AsyncMock(spec=AsyncExitStack)
        mock_stack.enter_async_context = AsyncMock(side_effect=ConnectionError("server crashed"))

        with (
            patch("anteroom.services.mcp_manager.AsyncExitStack", return_value=mock_stack),
            patch("anteroom.services.mcp_manager.shutil.which", return_value="/usr/bin/echo"),
        ):
            await mgr._connect_one(config)

        # Stack must have been closed to prevent leaked task groups
        mock_stack.aclose.assert_awaited_once()
        # Server should be marked as error
        assert mgr._server_status["bad-server"]["status"] == "error"
        assert "server crashed" in mgr._server_status["bad-server"]["error_message"]
        # No session or tools left behind
        assert "bad-server" not in mgr._sessions
        assert "bad-server" not in mgr._exit_stacks

    @pytest.mark.asyncio()
    @pytest.mark.skipif(sys.version_info < (3, 11), reason="ExceptionGroup requires 3.11+")
    async def test_connect_one_exception_group_closes_stack(self) -> None:
        """ExceptionGroup from a TaskGroup must also close the stack."""
        config = McpServerConfig(name="bad-server", transport="stdio", command="echo")
        mgr = McpManager([config])

        exc_group = ExceptionGroup("task group failed", [RuntimeError("subtask died")])  # noqa: F821
        mock_stack = AsyncMock(spec=AsyncExitStack)
        mock_stack.enter_async_context = AsyncMock(side_effect=exc_group)

        with (
            patch("anteroom.services.mcp_manager.AsyncExitStack", return_value=mock_stack),
            patch("anteroom.services.mcp_manager.shutil.which", return_value="/usr/bin/echo"),
        ):
            await mgr._connect_one(config)

        mock_stack.aclose.assert_awaited_once()
        assert mgr._server_status["bad-server"]["status"] == "error"

    @pytest.mark.asyncio()
    async def test_startup_continues_after_one_server_fails(self) -> None:
        """If one server fails to connect, the others should still connect."""
        configs = [
            McpServerConfig(name="good-server", transport="stdio", command="echo"),
            McpServerConfig(name="bad-server", transport="stdio", command="echo"),
        ]
        mgr = McpManager(configs)

        call_count = 0

        async def fake_connect_one(config: McpServerConfig, status_callback: Any = None) -> None:
            nonlocal call_count
            call_count += 1
            if config.name == "bad-server":
                mgr._server_status[config.name] = {
                    "status": "error",
                    "tool_count": 0,
                    "error_message": "connection refused",
                }
            else:
                mgr._server_status[config.name] = {
                    "status": "connected",
                    "tool_count": 3,
                }

        with patch.object(mgr, "_connect_one", side_effect=fake_connect_one):
            with patch.dict("sys.modules", {"mcp": MagicMock(), "mcp.client.stdio": MagicMock()}):
                await mgr.startup()

        assert call_count == 2
        assert mgr._server_status["good-server"]["status"] == "connected"
        assert mgr._server_status["bad-server"]["status"] == "error"

    @pytest.mark.asyncio()
    async def test_connect_one_stack_aclose_failure_still_sets_error(self) -> None:
        """Even if stack.aclose() itself fails, server status should be set."""
        config = McpServerConfig(name="messy-server", transport="stdio", command="echo")
        mgr = McpManager([config])

        mock_stack = AsyncMock(spec=AsyncExitStack)
        mock_stack.enter_async_context = AsyncMock(side_effect=OSError("broken pipe"))
        mock_stack.aclose = AsyncMock(side_effect=RuntimeError("cleanup exploded"))

        with (
            patch("anteroom.services.mcp_manager.AsyncExitStack", return_value=mock_stack),
            patch("anteroom.services.mcp_manager.shutil.which", return_value="/usr/bin/echo"),
        ):
            await mgr._connect_one(config)

        # Despite aclose failing, status should still be set
        assert mgr._server_status["messy-server"]["status"] == "error"
        assert "broken pipe" in mgr._server_status["messy-server"]["error_message"]

    @pytest.mark.asyncio()
    async def test_connect_one_invalid_transport_does_not_leak_stack(self) -> None:
        """Invalid transport config should return early without leaking."""
        config = McpServerConfig(name="bad-transport", transport="invalid", command="")
        mgr = McpManager([config])

        await mgr._connect_one(config)

        assert mgr._server_status["bad-transport"]["status"] == "error"
        assert "Invalid transport" in mgr._server_status["bad-transport"]["error_message"]
        assert "bad-transport" not in mgr._exit_stacks

    @pytest.mark.asyncio()
    async def test_failed_server_has_no_tools(self) -> None:
        """A failed server should contribute zero tools."""
        configs = [
            McpServerConfig(name="dead-server", transport="stdio", command="echo"),
        ]
        mgr = McpManager(configs)

        mock_stack = AsyncMock(spec=AsyncExitStack)
        mock_stack.enter_async_context = AsyncMock(side_effect=ConnectionRefusedError("refused"))

        with (
            patch("anteroom.services.mcp_manager.AsyncExitStack", return_value=mock_stack),
            patch("anteroom.services.mcp_manager.shutil.which", return_value="/usr/bin/echo"),
        ):
            await mgr._connect_one(configs[0])

        assert mgr.get_all_tools() == []
        assert mgr.get_openai_tools() is None
        assert mgr.get_tool_server_name("any_tool") == "unknown"

    @pytest.mark.asyncio()
    async def test_error_message_includes_exception_type(self) -> None:
        """Error message should include the exception class name for diagnostics."""
        config = McpServerConfig(name="typed-error", transport="stdio", command="echo")
        mgr = McpManager([config])

        mock_stack = AsyncMock(spec=AsyncExitStack)
        mock_stack.enter_async_context = AsyncMock(side_effect=TimeoutError("timed out after 30s"))

        with (
            patch("anteroom.services.mcp_manager.AsyncExitStack", return_value=mock_stack),
            patch("anteroom.services.mcp_manager.shutil.which", return_value="/usr/bin/echo"),
        ):
            await mgr._connect_one(config)

        err = mgr._server_status["typed-error"]["error_message"]
        assert "TimeoutError" in err
        assert "timed out after 30s" in err

    @pytest.mark.asyncio()
    async def test_command_not_found_shows_path(self) -> None:
        """FileNotFoundError should mention PATH when command is missing."""
        config = McpServerConfig(name="missing-cmd", transport="stdio", command="nonexistent-mcp-server")
        mgr = McpManager([config])

        with patch("anteroom.services.mcp_manager.shutil.which", return_value=None):
            await mgr._connect_one(config)

        err = mgr._server_status["missing-cmd"]["error_message"]
        assert "not found on PATH" in err
        assert "nonexistent-mcp-server" in err

    def test_describe_config_stdio(self) -> None:
        """_describe_config should include command and args."""
        config = McpServerConfig(name="jira", transport="stdio", command="npx", args=["-y", "jira-mcp-server"])
        mgr = McpManager([config])
        desc = mgr._describe_config(config)
        assert "jira" in desc
        assert "stdio" in desc
        assert "npx -y jira-mcp-server" in desc

    def test_describe_config_sse(self) -> None:
        """_describe_config should include URL for SSE."""
        config = McpServerConfig(name="remote", transport="sse", url="https://mcp.example.com/sse")
        mgr = McpManager([config])
        desc = mgr._describe_config(config)
        assert "sse" in desc
        assert "https://mcp.example.com/sse" in desc

    def test_describe_config_with_env(self) -> None:
        """_describe_config should list env key names (not values)."""
        config = McpServerConfig(
            name="secret", transport="stdio", command="cmd", env={"API_KEY": "sk-123", "TOKEN": "abc"}
        )
        mgr = McpManager([config])
        desc = mgr._describe_config(config)
        assert "API_KEY" in desc
        assert "TOKEN" in desc
        assert "sk-123" not in desc  # values must not leak

    @pytest.mark.asyncio()
    async def test_connection_timeout(self) -> None:
        """Server that hangs should be killed after timeout."""
        config = McpServerConfig(name="slow-server", transport="stdio", command="echo", timeout=0.1)
        mgr = McpManager([config])

        async def hang_forever(_config: McpServerConfig) -> None:
            await asyncio.sleep(999)

        with patch.object(mgr, "_do_connect", side_effect=hang_forever):
            await mgr._connect_one(config)

        assert mgr._server_status["slow-server"]["status"] == "error"
        assert "timed out" in mgr._server_status["slow-server"]["error_message"].lower()

    @pytest.mark.asyncio()
    async def test_custom_timeout_from_config(self) -> None:
        """Timeout value should come from config."""
        config = McpServerConfig(name="custom", transport="stdio", command="echo", timeout=60.0)
        mgr = McpManager([config])
        assert mgr._configs["custom"].timeout == 60.0

    def test_default_timeout(self) -> None:
        """Default timeout should be 30s."""
        config = McpServerConfig(name="default", transport="stdio", command="echo")
        assert config.timeout == 30.0

    def test_describe_config_includes_timeout(self) -> None:
        """_describe_config should show timeout."""
        config = McpServerConfig(name="t", transport="stdio", command="echo", timeout=15.0)
        mgr = McpManager([config])
        desc = mgr._describe_config(config)
        assert "timeout=15.0s" in desc


class TestMcpManagerErrorClassification:
    """Tests for exc_info suppression on McpError vs full traceback for unexpected errors."""

    @pytest.mark.asyncio()
    async def test_mcp_error_suppresses_traceback(self) -> None:
        """McpError should be logged without exc_info so no raw traceback appears."""
        try:
            from mcp import McpError
            from mcp.types import ErrorData
        except ImportError:
            pytest.skip("mcp SDK not installed")

        config = McpServerConfig(name="mcp-err-server", transport="stdio", command="echo")
        mgr = McpManager([config])
        logged_kwargs: list[dict] = []

        import logging

        mcp_error = McpError(ErrorData(code=-32000, message="server rejected handshake"))

        with (
            patch("anteroom.services.mcp_manager.shutil.which", return_value="/usr/bin/echo"),
            patch("anteroom.services.mcp_manager.AsyncExitStack") as mock_stack_cls,
            patch.object(
                logging.getLogger("anteroom.services.mcp_manager"),
                "warning",
                side_effect=lambda *a, **kw: logged_kwargs.append(kw),
            ),
        ):
            mock_stack = AsyncMock()
            mock_stack_cls.return_value = mock_stack
            mock_stack.enter_async_context.side_effect = mcp_error
            mock_stack.aclose = AsyncMock()
            await mgr._do_connect(config)

        error_calls = [kw for kw in logged_kwargs if "exc_info" in kw]
        assert error_calls, "Expected at least one logger.warning call with exc_info kwarg"
        assert error_calls[-1]["exc_info"] is False, "McpError should suppress traceback (exc_info=False)"

    @pytest.mark.asyncio()
    async def test_unexpected_error_keeps_traceback(self) -> None:
        """Non-McpError exceptions should be logged with exc_info=True."""
        config = McpServerConfig(name="unexpected-server", transport="stdio", command="echo")
        mgr = McpManager([config])
        logged_kwargs: list[dict] = []

        import logging

        with (
            patch("anteroom.services.mcp_manager.shutil.which", return_value="/usr/bin/echo"),
            patch("anteroom.services.mcp_manager.AsyncExitStack") as mock_stack_cls,
            patch.object(
                logging.getLogger("anteroom.services.mcp_manager"),
                "warning",
                side_effect=lambda *a, **kw: logged_kwargs.append(kw),
            ),
        ):
            mock_stack = AsyncMock()
            mock_stack_cls.return_value = mock_stack
            mock_stack.enter_async_context.side_effect = RuntimeError("segfault in subprocess")
            mock_stack.aclose = AsyncMock()
            await mgr._do_connect(config)

        error_calls = [kw for kw in logged_kwargs if "exc_info" in kw]
        assert error_calls, "Expected at least one logger.warning call with exc_info kwarg"
        assert error_calls[-1]["exc_info"] is True, "Unexpected errors should include traceback (exc_info=True)"

    @pytest.mark.asyncio()
    async def test_cancelled_error_reraised_after_stack_cleanup(self) -> None:
        """CancelledError must propagate out of _do_connect so wait_for works."""
        config = McpServerConfig(name="cancelled-server", transport="stdio", command="echo")
        mgr = McpManager([config])
        stack_closed = False

        with (
            patch("anteroom.services.mcp_manager.shutil.which", return_value="/usr/bin/echo"),
            patch("anteroom.services.mcp_manager.AsyncExitStack") as mock_stack_cls,
        ):
            mock_stack = AsyncMock()

            async def close_and_track():
                nonlocal stack_closed
                stack_closed = True

            mock_stack.aclose = close_and_track
            mock_stack_cls.return_value = mock_stack
            mock_stack.enter_async_context.side_effect = asyncio.CancelledError()

            with pytest.raises(asyncio.CancelledError):
                await mgr._do_connect(config)

        assert stack_closed, "Stack should be closed even when CancelledError is re-raised"
        # Server status should NOT be set to error — cancellation is not a connection failure
        assert "cancelled-server" not in mgr._server_status


class TestMcpManagerShutdown:
    """Tests for shutdown handling of BaseException and ExceptionGroup."""

    @pytest.mark.asyncio()
    async def test_shutdown_closes_active_stacks(self) -> None:
        """Shutdown should close all active exit stacks."""
        mgr = McpManager([])
        stack_a = AsyncMock(spec=AsyncExitStack)
        stack_b = AsyncMock(spec=AsyncExitStack)
        mgr._exit_stacks = {"server-a": stack_a, "server-b": stack_b}
        mgr._sessions = {"server-a": AsyncMock(), "server-b": AsyncMock()}

        await mgr.shutdown()

        stack_a.aclose.assert_awaited_once()
        stack_b.aclose.assert_awaited_once()
        assert mgr._exit_stacks == {}
        assert mgr._sessions == {}

    @pytest.mark.asyncio()
    async def test_shutdown_handles_base_exception_from_stack(self) -> None:
        """Shutdown must catch BaseException (including ExceptionGroup) from stack.aclose()."""
        mgr = McpManager([])
        stack = AsyncMock(spec=AsyncExitStack)
        stack.aclose = AsyncMock(side_effect=BaseException("task group teardown"))
        mgr._exit_stacks = {"server": stack}
        mgr._sessions = {"server": AsyncMock()}

        await mgr.shutdown()

        assert mgr._exit_stacks == {}
        assert mgr._sessions == {}

    @pytest.mark.asyncio()
    @pytest.mark.skipif(sys.version_info < (3, 11), reason="ExceptionGroup requires 3.11+")
    async def test_shutdown_handles_exception_group(self) -> None:
        """Shutdown must survive ExceptionGroup from TaskGroup teardown."""
        mgr = McpManager([])
        exc_group = ExceptionGroup("task group failed", [RuntimeError("subtask died")])  # noqa: F821
        stack = AsyncMock(spec=AsyncExitStack)
        stack.aclose = AsyncMock(side_effect=exc_group)
        mgr._exit_stacks = {"server": stack}
        mgr._sessions = {"server": AsyncMock()}

        await mgr.shutdown()

        assert mgr._exit_stacks == {}
        assert mgr._sessions == {}

    @pytest.mark.asyncio()
    async def test_shutdown_timeout_prevents_hang(self) -> None:
        """Shutdown should complete within its internal 5s timeout, not hang forever."""
        mgr = McpManager([])
        entered_hang = asyncio.Event()

        async def hang_forever() -> None:
            entered_hang.set()
            await asyncio.sleep(999)

        stack = AsyncMock(spec=AsyncExitStack)
        stack.aclose = hang_forever
        mgr._exit_stacks = {"stuck-server": stack}
        mgr._sessions = {"stuck-server": AsyncMock()}

        import time

        start = time.monotonic()
        await mgr.shutdown()
        elapsed = time.monotonic() - start

        assert mgr._exit_stacks == {}
        assert entered_hang.is_set(), "hang_forever should have been entered"
        assert elapsed < 8.0, f"Shutdown took {elapsed:.1f}s — internal timeout not working"

    @pytest.mark.asyncio()
    async def test_disconnect_handles_base_exception_from_stack(self) -> None:
        """disconnect_server must catch BaseException from stack.aclose()."""
        config = McpServerConfig(name="server", transport="stdio", command="echo")
        mgr = McpManager([config])
        stack = AsyncMock(spec=AsyncExitStack)
        stack.aclose = AsyncMock(side_effect=BaseException("task group teardown"))
        mgr._exit_stacks = {"server": stack}
        mgr._sessions = {"server": AsyncMock()}

        await mgr.disconnect_server("server")

        assert mgr._server_status["server"]["status"] == "disconnected"
        assert "server" not in mgr._exit_stacks

    @pytest.mark.asyncio()
    @pytest.mark.skipif(sys.version_info < (3, 11), reason="ExceptionGroup requires 3.11+")
    async def test_disconnect_handles_exception_group(self) -> None:
        """disconnect_server must survive ExceptionGroup from TaskGroup teardown."""
        config = McpServerConfig(name="server", transport="stdio", command="echo")
        mgr = McpManager([config])
        exc_group = ExceptionGroup("task group failed", [RuntimeError("subtask died")])  # noqa: F821
        stack = AsyncMock(spec=AsyncExitStack)
        stack.aclose = AsyncMock(side_effect=exc_group)
        mgr._exit_stacks = {"server": stack}
        mgr._sessions = {"server": AsyncMock()}

        await mgr.disconnect_server("server")

        assert mgr._server_status["server"]["status"] == "disconnected"
        assert "server" not in mgr._exit_stacks

    @pytest.mark.asyncio()
    async def test_shutdown_logs_errors_at_debug_not_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Shutdown errors should be logged at DEBUG, not WARNING, to avoid noisy tracebacks on Ctrl+C."""
        import logging

        mgr = McpManager([])
        stack = AsyncMock(spec=AsyncExitStack)
        stack.aclose = AsyncMock(side_effect=RuntimeError("Attempted to exit a cancel scope"))
        mgr._exit_stacks = {"clickup": stack}
        mgr._sessions = {"clickup": AsyncMock()}

        with caplog.at_level(logging.DEBUG, logger="anteroom.services.mcp_manager"):
            await mgr.shutdown()

        shutdown_records = [r for r in caplog.records if "clickup" in r.message and "shutdown" in r.message]
        assert shutdown_records, "Expected a log record mentioning 'clickup' and 'shutdown'"
        assert all(r.levelno == logging.DEBUG for r in shutdown_records), (
            f"Expected DEBUG level, got: {[r.levelname for r in shutdown_records]}"
        )
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING and "clickup" in r.message]
        assert not warning_records, f"Unexpected WARNING+ records: {warning_records}"

    @pytest.mark.asyncio()
    async def test_shutdown_suppresses_cancelled_error(self) -> None:
        """CancelledError during shutdown (common on Ctrl+C) should not propagate."""
        mgr = McpManager([])
        stack = AsyncMock(spec=AsyncExitStack)
        stack.aclose = AsyncMock(side_effect=asyncio.CancelledError())
        mgr._exit_stacks = {"time": stack}
        mgr._sessions = {"time": AsyncMock()}

        await mgr.shutdown()

        stack.aclose.assert_awaited_once()
        assert mgr._exit_stacks == {}

    @pytest.mark.asyncio()
    @pytest.mark.skipif(
        sys.version_info < (3, 12),
        reason="asyncio.wait_for propagates KeyboardInterrupt at the event loop level on <3.12",
    )
    async def test_shutdown_reraises_keyboard_interrupt_after_cleanup(self) -> None:
        """KeyboardInterrupt during shutdown should be re-raised after all stacks are cleaned."""
        mgr = McpManager([])

        # Use a real async function rather than AsyncMock side_effect for
        # KeyboardInterrupt — AsyncMock's side_effect handling for BaseException
        # subclasses leaks through asyncio.wait_for on Python <3.12.
        stack_a_closed = False
        stack_b_closed = False

        async def _aclose_raises_ki() -> None:
            nonlocal stack_a_closed
            stack_a_closed = True
            raise KeyboardInterrupt()

        async def _aclose_ok() -> None:
            nonlocal stack_b_closed
            stack_b_closed = True

        stack_a = AsyncMock(spec=AsyncExitStack)
        stack_a.aclose = _aclose_raises_ki
        stack_b = AsyncMock(spec=AsyncExitStack)
        stack_b.aclose = _aclose_ok
        mgr._exit_stacks = {"server-a": stack_a, "server-b": stack_b}
        mgr._sessions = {"server-a": AsyncMock(), "server-b": AsyncMock()}

        with pytest.raises(KeyboardInterrupt):
            await mgr.shutdown()

        # Both stacks must have been closed before re-raising
        assert stack_a_closed
        assert stack_b_closed
        assert mgr._exit_stacks == {}
        assert mgr._sessions == {}


def _make_tool(name: str, server: str) -> dict[str, Any]:
    return {"name": name, "server_name": server, "description": f"desc-{name}", "input_schema": {}}


class TestMcpToolFiltering:
    """Tests for per-server tool include/exclude filtering."""

    def _make_manager(self, *configs: McpServerConfig) -> McpManager:
        mgr = McpManager(list(configs))
        return mgr

    def test_no_filter_returns_all_tools(self) -> None:
        cfg = McpServerConfig(name="jira", transport="stdio", command="echo")
        mgr = self._make_manager(cfg)
        mgr._server_tools = {"jira": [_make_tool("search", "jira"), _make_tool("create", "jira")]}
        mgr._rebuild_tool_map()
        assert len(mgr.get_all_tools()) == 2

    def test_include_filters_to_allowlist(self) -> None:
        cfg = McpServerConfig(name="jira", transport="stdio", command="echo", tools_include=["search", "get_issue"])
        mgr = self._make_manager(cfg)
        mgr._server_tools = {
            "jira": [_make_tool("search", "jira"), _make_tool("create", "jira"), _make_tool("get_issue", "jira")],
        }
        mgr._rebuild_tool_map()
        names = {t["name"] for t in mgr.get_all_tools()}
        assert names == {"search", "get_issue"}

    def test_exclude_filters_out_blocklist(self) -> None:
        cfg = McpServerConfig(name="jira", transport="stdio", command="echo", tools_exclude=["delete", "bulk_delete"])
        mgr = self._make_manager(cfg)
        mgr._server_tools = {
            "jira": [_make_tool("search", "jira"), _make_tool("delete", "jira"), _make_tool("bulk_delete", "jira")],
        }
        mgr._rebuild_tool_map()
        names = {t["name"] for t in mgr.get_all_tools()}
        assert names == {"search"}

    def test_include_glob_pattern(self) -> None:
        cfg = McpServerConfig(name="jira", transport="stdio", command="echo", tools_include=["get_*", "search_*"])
        mgr = self._make_manager(cfg)
        mgr._server_tools = {
            "jira": [
                _make_tool("get_issue", "jira"),
                _make_tool("get_comment", "jira"),
                _make_tool("search_issues", "jira"),
                _make_tool("create_issue", "jira"),
                _make_tool("delete_issue", "jira"),
            ],
        }
        mgr._rebuild_tool_map()
        names = {t["name"] for t in mgr.get_all_tools()}
        assert names == {"get_issue", "get_comment", "search_issues"}

    def test_exclude_glob_pattern(self) -> None:
        cfg = McpServerConfig(name="jira", transport="stdio", command="echo", tools_exclude=["bulk_*", "admin_*"])
        mgr = self._make_manager(cfg)
        mgr._server_tools = {
            "jira": [
                _make_tool("search", "jira"),
                _make_tool("bulk_create", "jira"),
                _make_tool("bulk_delete", "jira"),
                _make_tool("admin_settings", "jira"),
            ],
        }
        mgr._rebuild_tool_map()
        names = {t["name"] for t in mgr.get_all_tools()}
        assert names == {"search"}

    def test_filtered_tool_not_in_tool_map(self) -> None:
        cfg = McpServerConfig(name="jira", transport="stdio", command="echo", tools_include=["search"])
        mgr = self._make_manager(cfg)
        mgr._server_tools = {"jira": [_make_tool("search", "jira"), _make_tool("delete", "jira")]}
        mgr._rebuild_tool_map()
        assert mgr.get_tool_server_name("search") == "jira"
        assert mgr.get_tool_server_name("delete") == "unknown"

    def test_get_openai_tools_respects_filter(self) -> None:
        cfg = McpServerConfig(name="jira", transport="stdio", command="echo", tools_include=["search"])
        mgr = self._make_manager(cfg)
        mgr._server_tools = {"jira": [_make_tool("search", "jira"), _make_tool("delete", "jira")]}
        mgr._rebuild_tool_map()
        openai_tools = mgr.get_openai_tools()
        assert openai_tools is not None
        assert len(openai_tools) == 1
        assert openai_tools[0]["function"]["name"] == "search"

    def test_multi_server_independent_filters(self) -> None:
        cfg_a = McpServerConfig(name="jira", transport="stdio", command="echo", tools_include=["search"])
        cfg_b = McpServerConfig(name="confluence", transport="stdio", command="echo", tools_exclude=["delete_*"])
        mgr = self._make_manager(cfg_a, cfg_b)
        mgr._server_tools = {
            "jira": [_make_tool("search", "jira"), _make_tool("create", "jira")],
            "confluence": [_make_tool("read_page", "confluence"), _make_tool("delete_page", "confluence")],
        }
        mgr._rebuild_tool_map()
        names = {t["name"] for t in mgr.get_all_tools()}
        assert names == {"search", "read_page"}

    @pytest.mark.asyncio()
    async def test_call_tool_blocked_for_filtered_tool(self) -> None:
        cfg = McpServerConfig(name="jira", transport="stdio", command="echo", tools_include=["search"])
        mgr = self._make_manager(cfg)
        mgr._server_tools = {"jira": [_make_tool("search", "jira"), _make_tool("delete", "jira")]}
        mgr._sessions = {"jira": AsyncMock()}
        mgr._rebuild_tool_map()
        with pytest.raises(ValueError, match="filtered out"):
            await mgr.call_tool("delete", {})

    def test_empty_include_means_no_filter(self) -> None:
        cfg = McpServerConfig(name="jira", transport="stdio", command="echo", tools_include=[])
        mgr = self._make_manager(cfg)
        mgr._server_tools = {"jira": [_make_tool("search", "jira"), _make_tool("create", "jira")]}
        mgr._rebuild_tool_map()
        assert len(mgr.get_all_tools()) == 2

    def test_empty_exclude_means_no_filter(self) -> None:
        cfg = McpServerConfig(name="jira", transport="stdio", command="echo", tools_exclude=[])
        mgr = self._make_manager(cfg)
        mgr._server_tools = {"jira": [_make_tool("search", "jira"), _make_tool("create", "jira")]}
        mgr._rebuild_tool_map()
        assert len(mgr.get_all_tools()) == 2

    @pytest.mark.asyncio()
    async def test_tool_warning_threshold_fires(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        cfg = McpServerConfig(name="jira", transport="stdio", command="echo")
        mgr = McpManager([cfg], tool_warning_threshold=3)
        mgr._server_tools = {"jira": [_make_tool(f"tool{i}", "jira") for i in range(5)]}
        mgr._rebuild_tool_map()
        mgr._server_status = {"jira": {"status": "connected", "tool_count": 5}}

        async def noop(_config: McpServerConfig) -> None:
            pass

        with (
            caplog.at_level(logging.WARNING, logger="anteroom.services.mcp_manager"),
            patch.object(mgr, "_connect_one", side_effect=noop),
        ):
            await mgr.startup()

        warning_records = [r for r in caplog.records if "exceeds threshold" in r.message]
        assert warning_records, "Expected tool threshold warning"
        assert "5" in warning_records[0].message
        assert "3" in warning_records[0].message

    @pytest.mark.asyncio()
    async def test_tool_warning_threshold_disabled(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        cfg = McpServerConfig(name="jira", transport="stdio", command="echo")
        mgr = McpManager([cfg], tool_warning_threshold=0)
        mgr._server_tools = {"jira": [_make_tool(f"tool{i}", "jira") for i in range(50)]}
        mgr._rebuild_tool_map()
        mgr._server_status = {"jira": {"status": "connected", "tool_count": 50}}

        async def noop(_config: McpServerConfig) -> None:
            pass

        with (
            caplog.at_level(logging.WARNING, logger="anteroom.services.mcp_manager"),
            patch.object(mgr, "_connect_one", side_effect=noop),
        ):
            await mgr.startup()

        warning_records = [r for r in caplog.records if "exceeds threshold" in r.message]
        assert not warning_records, "Should not warn when threshold is 0 (disabled)"

    @pytest.mark.asyncio()
    async def test_tool_warning_threshold_not_exceeded(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        cfg = McpServerConfig(name="jira", transport="stdio", command="echo")
        mgr = McpManager([cfg], tool_warning_threshold=10)
        mgr._server_tools = {"jira": [_make_tool(f"tool{i}", "jira") for i in range(5)]}
        mgr._rebuild_tool_map()
        mgr._server_status = {"jira": {"status": "connected", "tool_count": 5}}

        async def noop(_config: McpServerConfig) -> None:
            pass

        with (
            caplog.at_level(logging.WARNING, logger="anteroom.services.mcp_manager"),
            patch.object(mgr, "_connect_one", side_effect=noop),
        ):
            await mgr.startup()

        warning_records = [r for r in caplog.records if "exceeds threshold" in r.message]
        assert not warning_records, "Should not warn when under threshold"

    def test_include_no_matches_returns_empty(self) -> None:
        cfg = McpServerConfig(name="jira", transport="stdio", command="echo", tools_include=["nonexistent_*"])
        mgr = self._make_manager(cfg)
        mgr._server_tools = {"jira": [_make_tool("search", "jira"), _make_tool("create", "jira")]}
        mgr._rebuild_tool_map()
        assert mgr.get_all_tools() == []
        assert mgr.get_openai_tools() is None


# Additional tests for coverage of missed lines (#689)


class TestValidateSseUrl:
    """Tests for _validate_sse_url covering lines 37-59."""

    def test_valid_https_url_passes(self) -> None:
        from anteroom.services.mcp_manager import _validate_sse_url

        # Should not raise — external reachable hostname
        # We need a URL that won't be SSRF-blocked; use a known public IP
        # but since DNS is live, use an IP directly instead
        _validate_sse_url("https://93.184.216.34/sse")  # example.com IP

    def test_invalid_scheme_raises(self) -> None:
        from anteroom.services.mcp_manager import _validate_sse_url

        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            _validate_sse_url("ftp://example.com/sse")

    def test_localhost_hostname_blocked(self) -> None:
        from anteroom.services.mcp_manager import _validate_sse_url

        with pytest.raises(ValueError, match="Blocked internal hostname"):
            _validate_sse_url("http://localhost/sse")

    def test_gcp_metadata_hostname_blocked(self) -> None:
        from anteroom.services.mcp_manager import _validate_sse_url

        with pytest.raises(ValueError, match="Blocked internal hostname"):
            _validate_sse_url("http://metadata.google.internal/sse")

    def test_loopback_ip_blocked(self) -> None:
        from anteroom.services.mcp_manager import _validate_sse_url

        with pytest.raises(ValueError, match="Blocked internal IP"):
            _validate_sse_url("http://127.0.0.1/sse")

    def test_private_ip_10_x_blocked(self) -> None:
        from anteroom.services.mcp_manager import _validate_sse_url

        with pytest.raises(ValueError, match="Blocked internal IP"):
            _validate_sse_url("http://10.0.0.1/sse")

    def test_private_ip_192_168_blocked(self) -> None:
        from anteroom.services.mcp_manager import _validate_sse_url

        with pytest.raises(ValueError, match="Blocked internal IP"):
            _validate_sse_url("http://192.168.1.1/sse")

    def test_private_ip_172_16_blocked(self) -> None:
        from anteroom.services.mcp_manager import _validate_sse_url

        with pytest.raises(ValueError, match="Blocked internal IP"):
            _validate_sse_url("http://172.16.0.1/sse")

    def test_link_local_ip_blocked(self) -> None:
        from anteroom.services.mcp_manager import _validate_sse_url

        with pytest.raises(ValueError, match="Blocked internal IP"):
            _validate_sse_url("http://169.254.169.254/sse")

    def test_ipv6_loopback_blocked(self) -> None:
        from anteroom.services.mcp_manager import _validate_sse_url

        with pytest.raises(ValueError, match="Blocked internal IP"):
            _validate_sse_url("http://[::1]/sse")

    def test_unresolvable_hostname_raises(self) -> None:
        from anteroom.services.mcp_manager import _validate_sse_url

        with pytest.raises(ValueError, match="Cannot resolve hostname"):
            _validate_sse_url("http://this-hostname-definitely-does-not-exist-xyzzy.invalid/sse")

    def test_hostname_resolving_to_private_ip_blocked(self) -> None:
        """Hostname that resolves to a private IP must be blocked (lines 51-57)."""
        import socket

        from anteroom.services.mcp_manager import _validate_sse_url

        private_ip = "10.0.0.1"
        with patch("anteroom.services.mcp_manager.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (private_ip, 0))]
            with pytest.raises(ValueError, match="resolves to blocked IP"):
                _validate_sse_url("http://internal-service.example.com/sse")


class TestValidateCommand:
    """Tests for _validate_command covering lines 64-66."""

    def test_existing_command_passes(self) -> None:
        from anteroom.services.mcp_manager import _validate_command

        with patch("anteroom.services.mcp_manager.shutil.which", return_value="/usr/bin/echo"):
            _validate_command("echo")  # should not raise

    def test_missing_command_raises(self) -> None:
        from anteroom.services.mcp_manager import _validate_command

        with patch("anteroom.services.mcp_manager.shutil.which", return_value=None):
            with pytest.raises(ValueError, match="MCP command not found on PATH"):
                _validate_command("nonexistent-mcp-tool")


class TestIsReadyProperty:
    """Tests for is_ready property covering lines 83-85."""

    def test_is_ready_true_when_no_configs(self) -> None:
        mgr = McpManager([])
        assert mgr.is_ready is True

    def test_is_ready_false_when_not_all_resolved(self) -> None:
        configs = [
            McpServerConfig(name="server-a", transport="stdio", command="echo"),
            McpServerConfig(name="server-b", transport="stdio", command="echo"),
        ]
        mgr = McpManager(configs)
        # Only one server has resolved status
        mgr._server_status = {"server-a": {"status": "connected", "tool_count": 0}}
        assert mgr.is_ready is False

    def test_is_ready_true_when_all_resolved(self) -> None:
        configs = [
            McpServerConfig(name="server-a", transport="stdio", command="echo"),
            McpServerConfig(name="server-b", transport="stdio", command="echo"),
        ]
        mgr = McpManager(configs)
        mgr._server_status = {
            "server-a": {"status": "connected", "tool_count": 2},
            "server-b": {"status": "error", "tool_count": 0},
        }
        assert mgr.is_ready is True


class TestStartupEmptyConfigs:
    """Tests for startup early-return with no configs (line 89)."""

    @pytest.mark.asyncio()
    async def test_startup_empty_configs_returns_immediately(self) -> None:
        mgr = McpManager([])
        # Patch MCP import to ensure we don't accidentally exercise connection code
        with patch.dict("sys.modules", {"mcp": MagicMock(), "mcp.client.stdio": MagicMock()}):
            await mgr.startup()
        assert mgr._server_status == {}
        assert mgr.is_ready is True


class TestStartupStatusCallback:
    """Tests for status_callback invocations during startup (lines 102-103, 182)."""

    @pytest.mark.asyncio()
    async def test_startup_calls_connecting_status_callback(self) -> None:
        """startup() must call status_callback with 'connecting' for each server (lines 102-103)."""
        configs = [
            McpServerConfig(name="server-a", transport="stdio", command="echo"),
            McpServerConfig(name="server-b", transport="stdio", command="echo"),
        ]
        mgr = McpManager(configs)
        received: list[tuple[str, dict]] = []

        async def fake_connect_one(config: McpServerConfig, status_callback: Any = None) -> None:
            mgr._server_status[config.name] = {"status": "connected", "tool_count": 0}

        with (
            patch.object(mgr, "_connect_one", side_effect=fake_connect_one),
            patch.dict("sys.modules", {"mcp": MagicMock(), "mcp.client.stdio": MagicMock()}),
        ):
            await mgr.startup(status_callback=lambda name, status: received.append((name, status)))

        connecting_calls = [(n, s) for n, s in received if s.get("status") == "connecting"]
        assert len(connecting_calls) == 2
        names = {n for n, _ in connecting_calls}
        assert names == {"server-a", "server-b"}

    @pytest.mark.asyncio()
    async def test_connect_one_calls_status_callback_on_completion(self) -> None:
        """_connect_one must call status_callback with final status (line 182)."""
        config = McpServerConfig(name="srv", transport="stdio", command="echo")
        mgr = McpManager([config])
        callback_calls: list[tuple[str, dict]] = []

        async def fake_do_connect(_config: McpServerConfig) -> None:
            mgr._server_status[_config.name] = {"status": "connected", "tool_count": 1}

        with patch.object(mgr, "_do_connect", side_effect=fake_do_connect):
            await mgr._connect_one(config, status_callback=lambda n, s: callback_calls.append((n, s)))

        assert len(callback_calls) == 1
        name, status = callback_calls[0]
        assert name == "srv"
        assert status["status"] == "connected"

    @pytest.mark.asyncio()
    async def test_connect_one_timeout_calls_status_callback(self) -> None:
        """status_callback must be called even when connection times out (line 182)."""
        config = McpServerConfig(name="slow", transport="stdio", command="echo", timeout=0.05)
        mgr = McpManager([config])
        callback_calls: list[tuple[str, dict]] = []

        async def hang_forever(_config: McpServerConfig) -> None:
            await asyncio.sleep(999)

        with patch.object(mgr, "_do_connect", side_effect=hang_forever):
            await mgr._connect_one(config, status_callback=lambda n, s: callback_calls.append((n, s)))

        assert len(callback_calls) == 1
        name, status = callback_calls[0]
        assert name == "slow"
        assert status["status"] == "error"


class TestDoConnectImportError:
    """Tests for _do_connect MCP SDK ImportError path (lines 191-199)."""

    @pytest.mark.asyncio()
    async def test_do_connect_without_mcp_sdk_sets_error_status(self) -> None:
        """_do_connect with no mcp package sets error status and returns (lines 191-199)."""
        config = McpServerConfig(name="no-sdk", transport="stdio", command="echo")
        mgr = McpManager([config])

        with patch.dict("sys.modules", {"mcp": None, "mcp.client.stdio": None}):
            await mgr._do_connect(config)

        assert mgr._server_status["no-sdk"]["status"] == "error"
        assert "not installed" in mgr._server_status["no-sdk"]["error_message"].lower()
        assert "no-sdk" not in mgr._sessions


class TestDoConnectSseTransport:
    """Tests for SSE transport branch in _do_connect (lines 231-250)."""

    @pytest.mark.asyncio()
    async def test_sse_client_import_error_sets_error_status(self) -> None:
        """When sse_client can't be imported, status must be set to error (lines 231-241)."""
        config = McpServerConfig(name="sse-server", transport="sse", url="https://93.184.216.34/sse")
        mgr = McpManager([config])

        mcp_mock = MagicMock()
        with patch.dict(
            "sys.modules",
            {"mcp": mcp_mock, "mcp.client.stdio": MagicMock(), "mcp.client.sse": None},
        ):
            await mgr._do_connect(config)

        assert mgr._server_status["sse-server"]["status"] == "error"
        assert "sse" in mgr._server_status["sse-server"]["error_message"].lower()

    @pytest.mark.asyncio()
    async def test_sse_url_validation_blocks_private_ip(self) -> None:
        """_do_connect for SSE must reject private IPs via _validate_sse_url (line 243)."""
        config = McpServerConfig(name="ssrf-attempt", transport="sse", url="https://mcp.example.com/sse")
        mgr = McpManager([config])

        mcp_mock = MagicMock()

        # Make McpError a real class so isinstance() works during error handling
        class FakeMcpError(Exception):
            pass

        mcp_mock.McpError = FakeMcpError
        sse_mod_mock = MagicMock()

        with (
            patch.dict(
                "sys.modules",
                {"mcp": mcp_mock, "mcp.client.stdio": MagicMock(), "mcp.client.sse": sse_mod_mock},
            ),
            patch(
                "anteroom.services.mcp_manager._validate_sse_url",
                side_effect=ValueError("Blocked internal IP: 10.0.0.1"),
            ),
        ):
            await mgr._do_connect(config)

        assert mgr._server_status["ssrf-attempt"]["status"] == "error"
        assert "Blocked internal IP" in mgr._server_status["ssrf-attempt"]["error_message"]

    @pytest.mark.asyncio()
    async def test_sse_connect_success(self) -> None:
        """SSE transport happy path: session initialized, tools listed (lines 244-250)."""
        config = McpServerConfig(name="sse-ok", transport="sse", url="https://93.184.216.34/sse")
        mgr = McpManager([config])

        mock_tool = MagicMock()
        mock_tool.name = "sse_tool"
        mock_tool.description = "An SSE tool"
        mock_tool.inputSchema = {"type": "object"}

        mock_session = AsyncMock()
        mock_tools_result = MagicMock()
        mock_tools_result.tools = [mock_tool]
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=mock_tools_result)

        mock_stack = AsyncMock(spec=AsyncExitStack)
        mock_stack.enter_async_context = AsyncMock(side_effect=[(MagicMock(), MagicMock()), mock_session])
        mock_stack.callback = MagicMock()
        mock_stack.aclose = AsyncMock()

        mcp_mock = MagicMock()
        sse_mod_mock = MagicMock()
        sse_mod_mock.sse_client = MagicMock()

        with (
            patch.dict(
                "sys.modules",
                {"mcp": mcp_mock, "mcp.client.stdio": MagicMock(), "mcp.client.sse": sse_mod_mock},
            ),
            patch("anteroom.services.mcp_manager.AsyncExitStack", return_value=mock_stack),
            patch("anteroom.services.mcp_manager._validate_sse_url"),
        ):
            await mgr._do_connect(config)

        assert mgr._server_status["sse-ok"]["status"] == "connected"
        assert mgr._server_tools["sse-ok"][0]["name"] == "sse_tool"


class TestDoConnectStdioSuccessPath:
    """Tests for successful stdio connect path (lines 224-228, 266-301)."""

    @pytest.mark.asyncio()
    async def test_stdio_connect_success_sets_connected_status(self) -> None:
        """Successful stdio connection populates session, tools, and connected status (lines 266-301)."""
        config = McpServerConfig(name="stdio-ok", transport="stdio", command="echo")
        mgr = McpManager([config])

        mock_tool = MagicMock()
        mock_tool.name = "my_tool"
        mock_tool.description = "Does things"
        mock_tool.inputSchema = {"type": "object", "properties": {}}

        mock_session = AsyncMock()
        mock_tools_result = MagicMock()
        mock_tools_result.tools = [mock_tool]
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=mock_tools_result)

        mock_stack = AsyncMock(spec=AsyncExitStack)
        # First enter_async_context returns (read, write) streams, second returns session
        mock_stack.enter_async_context = AsyncMock(side_effect=[(MagicMock(), MagicMock()), mock_session])
        mock_stack.callback = MagicMock()
        mock_stack.aclose = AsyncMock()

        mcp_mock = MagicMock()
        mcp_mock.ClientSession = MagicMock()
        mcp_mock.StdioServerParameters = MagicMock()

        with (
            patch.dict(
                "sys.modules",
                {"mcp": mcp_mock, "mcp.client.stdio": MagicMock()},
            ),
            patch("anteroom.services.mcp_manager.AsyncExitStack", return_value=mock_stack),
            patch("anteroom.services.mcp_manager.shutil.which", return_value="/usr/bin/echo"),
        ):
            await mgr._do_connect(config)

        assert mgr._server_status["stdio-ok"]["status"] == "connected"
        assert mgr._server_status["stdio-ok"]["tool_count"] == 1
        assert mgr._server_tools["stdio-ok"][0]["name"] == "my_tool"
        assert mgr._sessions["stdio-ok"] is mock_session

    @pytest.mark.asyncio()
    async def test_stdio_connect_with_filtered_tools_logs_filtered_count(self) -> None:
        """When tools are filtered, status reflects allowed count (lines 292-299)."""
        config = McpServerConfig(
            name="filtered-ok",
            transport="stdio",
            command="echo",
            tools_include=["allowed_tool"],
        )
        mgr = McpManager([config])

        mock_tool_allowed = MagicMock()
        mock_tool_allowed.name = "allowed_tool"
        mock_tool_allowed.description = "Allowed"
        mock_tool_allowed.inputSchema = {}

        mock_tool_blocked = MagicMock()
        mock_tool_blocked.name = "blocked_tool"
        mock_tool_blocked.description = "Blocked"
        mock_tool_blocked.inputSchema = {}

        mock_session = AsyncMock()
        mock_tools_result = MagicMock()
        mock_tools_result.tools = [mock_tool_allowed, mock_tool_blocked]
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=mock_tools_result)

        mock_stack = AsyncMock(spec=AsyncExitStack)
        mock_stack.enter_async_context = AsyncMock(side_effect=[(MagicMock(), MagicMock()), mock_session])
        mock_stack.callback = MagicMock()
        mock_stack.aclose = AsyncMock()

        mcp_mock = MagicMock()

        with (
            patch.dict("sys.modules", {"mcp": mcp_mock, "mcp.client.stdio": MagicMock()}),
            patch("anteroom.services.mcp_manager.AsyncExitStack", return_value=mock_stack),
            patch("anteroom.services.mcp_manager.shutil.which", return_value="/usr/bin/echo"),
        ):
            await mgr._do_connect(config)

        # 1 allowed out of 2 total
        assert mgr._server_status["filtered-ok"]["tool_count"] == 1
        assert mgr._server_status["filtered-ok"]["total_tool_count"] == 2

    @pytest.mark.asyncio()
    async def test_stdio_connect_tool_with_no_input_schema_attribute(self) -> None:
        """Tool without inputSchema attribute uses empty dict fallback (line 277)."""
        config = McpServerConfig(name="no-schema", transport="stdio", command="echo")
        mgr = McpManager([config])

        mock_tool = MagicMock(spec=["name", "description"])  # no inputSchema
        mock_tool.name = "simple_tool"
        mock_tool.description = "No schema"

        mock_session = AsyncMock()
        mock_tools_result = MagicMock()
        mock_tools_result.tools = [mock_tool]
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=mock_tools_result)

        mock_stack = AsyncMock(spec=AsyncExitStack)
        mock_stack.enter_async_context = AsyncMock(side_effect=[(MagicMock(), MagicMock()), mock_session])
        mock_stack.callback = MagicMock()
        mock_stack.aclose = AsyncMock()

        mcp_mock = MagicMock()

        with (
            patch.dict("sys.modules", {"mcp": mcp_mock, "mcp.client.stdio": MagicMock()}),
            patch("anteroom.services.mcp_manager.AsyncExitStack", return_value=mock_stack),
            patch("anteroom.services.mcp_manager.shutil.which", return_value="/usr/bin/echo"),
        ):
            await mgr._do_connect(config)

        assert mgr._server_tools["no-schema"][0]["input_schema"] == {}

    @pytest.mark.asyncio()
    async def test_stdio_connect_many_tools_truncates_log_names(self) -> None:
        """More than 10 tools should have '...' in the log (lines 298, 305)."""
        config = McpServerConfig(name="many-tools", transport="stdio", command="echo")
        mgr = McpManager([config])

        tools = []
        for i in range(15):
            t = MagicMock()
            t.name = f"tool_{i}"
            t.description = f"Tool {i}"
            t.inputSchema = {}
            tools.append(t)

        mock_session = AsyncMock()
        mock_tools_result = MagicMock()
        mock_tools_result.tools = tools
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=mock_tools_result)

        mock_stack = AsyncMock(spec=AsyncExitStack)
        mock_stack.enter_async_context = AsyncMock(side_effect=[(MagicMock(), MagicMock()), mock_session])
        mock_stack.callback = MagicMock()
        mock_stack.aclose = AsyncMock()

        mcp_mock = MagicMock()

        with (
            patch.dict("sys.modules", {"mcp": mcp_mock, "mcp.client.stdio": MagicMock()}),
            patch("anteroom.services.mcp_manager.AsyncExitStack", return_value=mock_stack),
            patch("anteroom.services.mcp_manager.shutil.which", return_value="/usr/bin/echo"),
        ):
            await mgr._do_connect(config)

        assert mgr._server_status["many-tools"]["tool_count"] == 15


class TestMcpErrorImportFailureDuringErrorHandling:
    """Tests for the McpError import fallback in error handling (lines 329-330)."""

    @pytest.mark.asyncio()
    async def test_error_handling_when_mcp_error_import_fails(self) -> None:
        """If 'from mcp import McpError' fails, error must still be recorded (lines 327-330)."""
        config = McpServerConfig(name="no-mcp-error", transport="stdio", command="echo")
        mgr = McpManager([config])

        # Patch shutil.which to find the command, then AsyncExitStack to raise on enter
        mock_stack = AsyncMock(spec=AsyncExitStack)
        mock_stack.enter_async_context = AsyncMock(side_effect=OSError("broken pipe"))
        mock_stack.aclose = AsyncMock()

        # mcp module exists for first import but McpError import attempt fails
        mcp_mock = MagicMock()
        del mcp_mock.McpError  # make McpError unavailable

        with (
            patch.dict("sys.modules", {"mcp": mcp_mock, "mcp.client.stdio": MagicMock()}),
            patch("anteroom.services.mcp_manager.AsyncExitStack", return_value=mock_stack),
            patch("anteroom.services.mcp_manager.shutil.which", return_value="/usr/bin/echo"),
        ):
            await mgr._do_connect(config)

        assert mgr._server_status["no-mcp-error"]["status"] == "error"
        assert "OSError" in mgr._server_status["no-mcp-error"]["error_message"]


class TestToolCollisionWarning:
    """Tests for tool name collision warning in _rebuild_tool_map (line 367)."""

    def test_tool_name_collision_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        mgr = McpManager([])
        mgr._server_tools = {
            "server-a": [_make_tool("shared_tool", "server-a")],
            "server-b": [_make_tool("shared_tool", "server-b")],
        }

        with caplog.at_level(logging.WARNING, logger="anteroom.services.mcp_manager"):
            mgr._rebuild_tool_map()

        collision_records = [r for r in caplog.records if "collision" in r.message.lower()]
        assert collision_records, "Expected a tool name collision warning"
        assert "shared_tool" in collision_records[0].message

    def test_tool_collision_last_server_wins(self) -> None:
        mgr = McpManager([])
        # Server iteration order: server-a first, then server-b (dicts are insertion-ordered in Python 3.7+)
        mgr._server_tools = {
            "server-a": [_make_tool("shared_tool", "server-a")],
            "server-b": [_make_tool("shared_tool", "server-b")],
        }
        mgr._rebuild_tool_map()
        # server-b overwrites server-a
        assert mgr._tool_to_server["shared_tool"] == "server-b"


class TestConnectServerWithExistingSession:
    """Tests for connect_server reconnection path (lines 381-386)."""

    @pytest.mark.asyncio()
    async def test_connect_server_disconnects_first_if_already_connected(self) -> None:
        """connect_server must disconnect before reconnecting if session exists (lines 381-382)."""
        config = McpServerConfig(name="srv", transport="stdio", command="echo")
        mgr = McpManager([config])
        mgr._sessions["srv"] = AsyncMock()

        disconnect_called = False
        connect_called = False

        async def fake_disconnect(name: str) -> None:
            nonlocal disconnect_called
            disconnect_called = True
            mgr._sessions.pop(name, None)
            mgr._server_status[name] = {"status": "disconnected", "tool_count": 0}

        async def fake_connect_one(cfg: McpServerConfig, status_callback: Any = None) -> None:
            nonlocal connect_called
            connect_called = True
            mgr._server_status[cfg.name] = {"status": "connected", "tool_count": 0}

        with (
            patch.object(mgr, "disconnect_server", side_effect=fake_disconnect),
            patch.object(mgr, "_connect_one", side_effect=fake_connect_one),
        ):
            await mgr.connect_server("srv")

        assert disconnect_called, "disconnect_server should have been called first"
        assert connect_called, "_connect_one should have been called after disconnect"
        assert "srv" not in mgr._disabled

    @pytest.mark.asyncio()
    async def test_connect_server_no_existing_session_skips_disconnect(self) -> None:
        """connect_server without existing session should not call disconnect (line 381)."""
        config = McpServerConfig(name="fresh", transport="stdio", command="echo")
        mgr = McpManager([config])
        # No existing session

        disconnect_called = False

        async def fake_disconnect(name: str) -> None:
            nonlocal disconnect_called
            disconnect_called = True

        async def fake_connect_one(cfg: McpServerConfig, status_callback: Any = None) -> None:
            mgr._server_status[cfg.name] = {"status": "connected", "tool_count": 0}

        with (
            patch.object(mgr, "disconnect_server", side_effect=fake_disconnect),
            patch.object(mgr, "_connect_one", side_effect=fake_connect_one),
        ):
            await mgr.connect_server("fresh")

        assert not disconnect_called, "disconnect_server should NOT be called for a fresh server"

    @pytest.mark.asyncio()
    async def test_connect_server_clears_disabled_flag(self) -> None:
        """connect_server must clear the _disabled flag (line 384)."""
        config = McpServerConfig(name="re-enable", transport="stdio", command="echo")
        mgr = McpManager([config])
        mgr._disabled.add("re-enable")

        async def fake_connect_one(cfg: McpServerConfig, status_callback: Any = None) -> None:
            mgr._server_status[cfg.name] = {"status": "connected", "tool_count": 0}

        with patch.object(mgr, "_connect_one", side_effect=fake_connect_one):
            await mgr.connect_server("re-enable")

        assert "re-enable" not in mgr._disabled


class TestReconnectServer:
    """Tests for reconnect_server (line 413)."""

    @pytest.mark.asyncio()
    async def test_reconnect_server_delegates_to_connect_server(self) -> None:
        """reconnect_server is a thin wrapper around connect_server (line 413)."""
        config = McpServerConfig(name="srv", transport="stdio", command="echo")
        mgr = McpManager([config])

        connect_server_called_with: list[str] = []

        async def fake_connect_server(name: str) -> None:
            connect_server_called_with.append(name)

        with patch.object(mgr, "connect_server", side_effect=fake_connect_server):
            await mgr.reconnect_server("srv")

        assert connect_server_called_with == ["srv"]


class TestCallToolResultParsing:
    """Tests for call_tool result content parsing (lines 463-466, 473)."""

    @pytest.mark.asyncio()
    async def test_call_tool_result_with_data_attribute(self) -> None:
        """Items with .data (not .text) should be converted to str (line 463-464)."""
        mgr = McpManager([])
        mgr._tool_to_server = {"image_tool": "vision-server"}

        item = MagicMock(spec=["data"])  # has .data, no .text
        item.data = b"\x89PNG binary"

        mock_result = MagicMock()
        mock_result.content = [item]

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        mgr._sessions = {"vision-server": mock_session}

        result = await mgr.call_tool("image_tool", {})
        assert "content" in result
        assert str(b"\x89PNG binary") in result["content"]

    @pytest.mark.asyncio()
    async def test_call_tool_result_with_plain_item(self) -> None:
        """Items with neither .text nor .data should fall through to str(item) (line 465-466)."""
        mgr = McpManager([])
        mgr._tool_to_server = {"plain_tool": "plain-server"}

        class PlainItem:
            def __str__(self) -> str:
                return "plain string result"

        mock_result = MagicMock()
        mock_result.content = [PlainItem()]

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        mgr._sessions = {"plain-server": mock_session}

        result = await mgr.call_tool("plain_tool", {})
        assert "content" in result
        assert "plain string result" in result["content"]

    @pytest.mark.asyncio()
    async def test_call_tool_result_without_content_attribute(self) -> None:
        """Result without .content falls back to str(result) (line 473)."""
        mgr = McpManager([])
        mgr._tool_to_server = {"raw_tool": "raw-server"}

        class RawResult:
            def __str__(self) -> str:
                return "raw result string"

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=RawResult())
        mgr._sessions = {"raw-server": mock_session}

        result = await mgr.call_tool("raw_tool", {})
        assert "result" in result
        assert "raw result string" in result["result"]

    @pytest.mark.asyncio()
    async def test_call_tool_context_trust_from_config(self) -> None:
        """call_tool should include _context_trust from the server config (lines 455-456)."""
        config = McpServerConfig(name="trusted-server", transport="stdio", command="echo", trust_level="trusted")
        mgr = McpManager([config])
        mgr._tool_to_server = {"trusted_tool": "trusted-server"}

        mock_result = MagicMock()
        mock_result.content = [MagicMock(text="trusted response")]

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        mgr._sessions = {"trusted-server": mock_session}

        result = await mgr.call_tool("trusted_tool", {})
        assert result["_context_trust"] == "trusted"
        assert result["_context_origin"] == "mcp:trusted-server"

    @pytest.mark.asyncio()
    async def test_call_tool_default_trust_level_when_no_config(self) -> None:
        """call_tool uses 'untrusted' when server config is missing (lines 455-456)."""
        mgr = McpManager([])
        mgr._tool_to_server = {"ghost_tool": "ghost-server"}
        # No config for ghost-server

        mock_result = MagicMock()
        mock_result.content = [MagicMock(text="response")]

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        mgr._sessions = {"ghost-server": mock_session}

        result = await mgr.call_tool("ghost_tool", {})
        assert result["_context_trust"] == "untrusted"


class TestToolWarningThresholdWithFilters:
    """Tests for tool warning threshold with active filters (line 136)."""

    @pytest.mark.asyncio()
    async def test_tool_warning_with_active_filter_changes_hint(self, caplog: pytest.LogCaptureFixture) -> None:
        """When any server has an active filter and threshold is exceeded, the hint changes (line 136)."""
        import logging

        cfg = McpServerConfig(
            name="jira",
            transport="stdio",
            command="echo",
            tools_include=["tool0", "tool1", "tool2", "tool3", "tool4"],
        )
        mgr = McpManager([cfg], tool_warning_threshold=3)
        mgr._server_tools = {"jira": [_make_tool(f"tool{i}", "jira") for i in range(5)]}
        mgr._rebuild_tool_map()
        mgr._server_status = {"jira": {"status": "connected", "tool_count": 5}}

        async def noop(_config: McpServerConfig) -> None:
            pass

        with (
            caplog.at_level(logging.WARNING, logger="anteroom.services.mcp_manager"),
            patch.object(mgr, "_connect_one", side_effect=noop),
            patch.dict("sys.modules", {"mcp": MagicMock(), "mcp.client.stdio": MagicMock()}),
        ):
            await mgr.startup()

        warning_records = [r for r in caplog.records if "exceeds threshold" in r.message]
        assert warning_records, "Expected tool threshold warning"
        assert "not be restrictive enough" in warning_records[0].message

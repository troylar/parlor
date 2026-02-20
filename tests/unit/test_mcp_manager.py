"""Tests for MCP manager per-server lifecycle."""

from __future__ import annotations

import asyncio
import sys
from contextlib import AsyncExitStack
from unittest.mock import AsyncMock, patch

import pytest

from anteroom.config import McpServerConfig
from anteroom.services.mcp_manager import McpManager, _validate_tool_args


class TestValidateToolArgs:
    def test_rejects_shell_metacharacters(self) -> None:
        with pytest.raises(ValueError, match="disallowed characters"):
            _validate_tool_args({"cmd": "ls; rm -rf /"})

    def test_accepts_clean_args(self) -> None:
        _validate_tool_args({"path": "/home/user/file.txt", "count": "5"})

    def test_accepts_non_string_values(self) -> None:
        _validate_tool_args({"count": 5, "flag": True, "items": ["a", "b"]})


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

        async def fake_connect_one(config: McpServerConfig) -> None:
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
                "error",
                side_effect=lambda *a, **kw: logged_kwargs.append(kw),
            ),
        ):
            mock_stack = AsyncMock()
            mock_stack_cls.return_value = mock_stack
            mock_stack.enter_async_context.side_effect = mcp_error
            mock_stack.aclose = AsyncMock()
            await mgr._do_connect(config)

        error_calls = [kw for kw in logged_kwargs if "exc_info" in kw]
        assert error_calls, "Expected at least one logger.error call with exc_info kwarg"
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
                "error",
                side_effect=lambda *a, **kw: logged_kwargs.append(kw),
            ),
        ):
            mock_stack = AsyncMock()
            mock_stack_cls.return_value = mock_stack
            mock_stack.enter_async_context.side_effect = RuntimeError("segfault in subprocess")
            mock_stack.aclose = AsyncMock()
            await mgr._do_connect(config)

        error_calls = [kw for kw in logged_kwargs if "exc_info" in kw]
        assert error_calls, "Expected at least one logger.error call with exc_info kwarg"
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

"""Tests for parallel MCP startup and toolbar formatting (#383)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from anteroom.config import McpServerConfig
from anteroom.services.mcp_manager import McpManager


class TestIsReady:
    def test_ready_when_no_configs(self) -> None:
        mgr = McpManager([])
        assert mgr.is_ready is True

    def test_not_ready_when_servers_pending(self) -> None:
        configs = [McpServerConfig(name="s1", transport="stdio", command="echo")]
        mgr = McpManager(configs)
        assert mgr.is_ready is False

    def test_ready_when_all_servers_resolved(self) -> None:
        configs = [McpServerConfig(name="s1", transport="stdio", command="echo")]
        mgr = McpManager(configs)
        mgr._server_status["s1"] = {"status": "connected", "tool_count": 3}
        assert mgr.is_ready is True

    def test_not_ready_when_some_servers_pending(self) -> None:
        configs = [
            McpServerConfig(name="s1", transport="stdio", command="echo"),
            McpServerConfig(name="s2", transport="stdio", command="cat"),
        ]
        mgr = McpManager(configs)
        mgr._server_status["s1"] = {"status": "connected", "tool_count": 3}
        # s2 has no status yet
        assert mgr.is_ready is False


class TestParallelStartup:
    @pytest.mark.asyncio()
    async def test_startup_without_mcp_sdk_skips(self) -> None:
        configs = [McpServerConfig(name="s1", transport="stdio", command="echo")]
        mgr = McpManager(configs)
        with patch.dict("sys.modules", {"mcp": None}):
            await mgr.startup()
        assert mgr.get_all_tools() == []

    @pytest.mark.asyncio()
    async def test_startup_calls_connect_one_for_each_server(self) -> None:
        configs = [
            McpServerConfig(name="s1", transport="stdio", command="echo"),
            McpServerConfig(name="s2", transport="stdio", command="cat"),
        ]
        mgr = McpManager(configs)
        connect_calls: list[str] = []

        async def fake_connect(config: McpServerConfig, status_callback: Any = None) -> None:
            connect_calls.append(config.name)
            mgr._server_status[config.name] = {"status": "connected", "tool_count": 0}
            if status_callback:
                status_callback(config.name, mgr._server_status[config.name])

        with patch.object(mgr, "_connect_one", side_effect=fake_connect):
            with patch.dict("sys.modules", {"mcp": MagicMock(), "mcp.client.stdio": MagicMock()}):
                await mgr.startup()

        assert set(connect_calls) == {"s1", "s2"}

    @pytest.mark.asyncio()
    async def test_status_callback_called_with_connecting(self) -> None:
        configs = [McpServerConfig(name="s1", transport="stdio", command="echo")]
        mgr = McpManager(configs)
        callback_calls: list[tuple[str, dict[str, Any]]] = []

        def cb(name: str, status: dict[str, Any]) -> None:
            callback_calls.append((name, dict(status)))

        async def fake_connect(config: McpServerConfig, status_callback: Any = None) -> None:
            mgr._server_status[config.name] = {"status": "connected", "tool_count": 2}
            if status_callback:
                status_callback(config.name, mgr._server_status[config.name])

        with patch.object(mgr, "_connect_one", side_effect=fake_connect):
            with patch.dict("sys.modules", {"mcp": MagicMock(), "mcp.client.stdio": MagicMock()}):
                await mgr.startup(status_callback=cb)

        # Should have "connecting" from startup(), then "connected" from _connect_one
        names = [c[0] for c in callback_calls]
        assert "s1" in names
        statuses = [c[1]["status"] for c in callback_calls]
        assert "connecting" in statuses
        assert "connected" in statuses

    @pytest.mark.asyncio()
    async def test_rebuild_tool_map_called_once_after_gather(self) -> None:
        configs = [
            McpServerConfig(name="s1", transport="stdio", command="echo"),
            McpServerConfig(name="s2", transport="stdio", command="cat"),
        ]
        mgr = McpManager(configs)
        rebuild_calls = 0
        original_rebuild = mgr._rebuild_tool_map

        def counting_rebuild() -> None:
            nonlocal rebuild_calls
            rebuild_calls += 1
            original_rebuild()

        async def fake_connect(config: McpServerConfig, status_callback: Any = None) -> None:
            mgr._server_status[config.name] = {"status": "connected", "tool_count": 0}

        with patch.object(mgr, "_connect_one", side_effect=fake_connect):
            with patch.object(mgr, "_rebuild_tool_map", side_effect=counting_rebuild):
                with patch.dict("sys.modules", {"mcp": MagicMock(), "mcp.client.stdio": MagicMock()}):
                    await mgr.startup()

        assert rebuild_calls == 1

    @pytest.mark.asyncio()
    async def test_startup_handles_connection_error_gracefully(self) -> None:
        configs = [
            McpServerConfig(name="good", transport="stdio", command="echo"),
            McpServerConfig(name="bad", transport="stdio", command="fail"),
        ]
        mgr = McpManager(configs)

        async def fake_connect(config: McpServerConfig, status_callback: Any = None) -> None:
            if config.name == "bad":
                raise ConnectionError("Server unavailable")
            mgr._server_status[config.name] = {"status": "connected", "tool_count": 1}

        with patch.object(mgr, "_connect_one", side_effect=fake_connect):
            with patch.dict("sys.modules", {"mcp": MagicMock(), "mcp.client.stdio": MagicMock()}):
                # Should not raise — gather uses return_exceptions=True
                await mgr.startup()

    @pytest.mark.asyncio()
    async def test_empty_startup_returns_immediately(self) -> None:
        mgr = McpManager([])
        await mgr.startup()
        assert mgr.is_ready is True


class TestFormatMcpToolbar:
    def test_empty_statuses_returns_none(self) -> None:
        from anteroom.cli.renderer import format_mcp_toolbar

        assert format_mcp_toolbar({}) is None

    def test_all_resolved_returns_none(self) -> None:
        from anteroom.cli.renderer import format_mcp_toolbar

        statuses = {
            "server-a": {"status": "connected", "tool_count": 5},
            "server-b": {"status": "error", "error_message": "timeout"},
        }
        assert format_mcp_toolbar(statuses) is None

    def test_connecting_shows_toolbar(self) -> None:
        from anteroom.cli.renderer import format_mcp_toolbar

        statuses = {
            "server-a": {"status": "connecting", "tool_count": 0},
        }
        result = format_mcp_toolbar(statuses)
        assert result is not None
        text = "".join(part[1] for part in result)
        assert "MCP:" in text
        assert "server-a" in text

    def test_mixed_statuses(self) -> None:
        from anteroom.cli.renderer import format_mcp_toolbar

        statuses = {
            "fast-server": {"status": "connected", "tool_count": 3},
            "slow-server": {"status": "connecting", "tool_count": 0},
        }
        result = format_mcp_toolbar(statuses)
        assert result is not None
        text = "".join(part[1] for part in result)
        assert "fast-server" in text
        assert "slow-server" in text
        assert "3 tools" in text

    def test_error_status_shows_message(self) -> None:
        from anteroom.cli.renderer import format_mcp_toolbar

        statuses = {
            "bad-server": {"status": "error", "error_message": "connection refused"},
            "pending": {"status": "connecting", "tool_count": 0},
        }
        result = format_mcp_toolbar(statuses)
        assert result is not None
        text = "".join(part[1] for part in result)
        assert "bad-server" in text
        assert "connection refused" in text

    def test_long_error_message_truncated(self) -> None:
        from anteroom.cli.renderer import format_mcp_toolbar

        statuses = {
            "bad": {"status": "error", "error_message": "x" * 100},
            "pending": {"status": "connecting", "tool_count": 0},
        }
        result = format_mcp_toolbar(statuses)
        assert result is not None
        text = "".join(part[1] for part in result)
        assert "..." in text

    def test_styles_used_correctly(self) -> None:
        from anteroom.cli.renderer import format_mcp_toolbar

        statuses = {
            "connected-srv": {"status": "connected", "tool_count": 2},
            "connecting-srv": {"status": "connecting", "tool_count": 0},
            "error-srv": {"status": "error", "error_message": "fail"},
        }
        result = format_mcp_toolbar(statuses)
        assert result is not None
        styles = {part[0] for part in result}
        assert "class:mcp-connected" in styles
        assert "class:mcp-connecting" in styles
        assert "class:mcp-error" in styles

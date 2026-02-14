"""Tests for MCP manager per-server lifecycle."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from parlor.config import McpServerConfig
from parlor.services.mcp_manager import McpManager, _validate_tool_args


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

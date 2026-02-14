"""MCP client lifecycle and tool routing manager."""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import shutil
import socket
from contextlib import AsyncExitStack
from typing import Any
from urllib.parse import urlparse

from ..config import McpServerConfig

logger = logging.getLogger(__name__)

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

_SHELL_META_RE = re.compile(r"[;&|`$(){}!<>\n\r]")


def _validate_sse_url(url: str) -> None:
    """Block SSE URLs pointing to internal/metadata endpoints (with DNS resolution)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
    hostname = parsed.hostname or ""
    if hostname in ("localhost", "metadata.google.internal"):
        raise ValueError(f"Blocked internal hostname: {hostname}")
    try:
        addr = ipaddress.ip_address(hostname)
        for network in _BLOCKED_NETWORKS:
            if addr in network:
                raise ValueError(f"Blocked internal IP: {hostname}")
    except ValueError as e:
        if "Blocked" in str(e):
            raise
        try:
            infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
            for _family, _type, _proto, _canon, sockaddr in infos:
                resolved_addr = ipaddress.ip_address(sockaddr[0])
                for network in _BLOCKED_NETWORKS:
                    if resolved_addr in network:
                        raise ValueError(f"Hostname '{hostname}' resolves to blocked IP: {sockaddr[0]}")
        except socket.gaierror:
            raise ValueError(f"Cannot resolve hostname: {hostname}")


def _validate_tool_args(arguments: dict[str, Any]) -> None:
    """Reject tool arguments containing shell metacharacters in string values."""
    for key, value in arguments.items():
        if isinstance(value, str) and _SHELL_META_RE.search(value):
            raise ValueError(f"Tool argument '{key}' contains disallowed characters")


def _validate_command(command: str) -> None:
    """Validate MCP command exists on PATH."""
    resolved = shutil.which(command)
    if resolved is None:
        raise ValueError(f"MCP command not found on PATH: {command}")


class McpManager:
    def __init__(self, server_configs: list[McpServerConfig]) -> None:
        self._configs = server_configs
        self._exit_stack = AsyncExitStack()
        self._sessions: dict[str, Any] = {}
        self._tools: list[dict[str, Any]] = []
        self._tool_to_server: dict[str, str] = {}
        self._server_status: dict[str, dict[str, Any]] = {}

    async def startup(self) -> None:
        if not self._configs:
            return

        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            logger.warning("MCP SDK not installed, skipping MCP server connections")
            return

        for config in self._configs:
            try:
                if config.transport == "stdio" and config.command:
                    _validate_command(config.command)
                    server_params = StdioServerParameters(
                        command=config.command,
                        args=config.args,
                        env={**os.environ, **config.env} if config.env else None,
                    )
                    stdio_transport = await self._exit_stack.enter_async_context(stdio_client(server_params))
                    read_stream, write_stream = stdio_transport
                    session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
                    await session.initialize()
                    self._sessions[config.name] = session

                    tools_result = await session.list_tools()
                    tool_count = 0
                    for tool in tools_result.tools:
                        if tool.name in self._tool_to_server:
                            logger.warning(
                                "Tool name collision: '%s' from server '%s' shadows existing tool from '%s'",
                                tool.name,
                                config.name,
                                self._tool_to_server[tool.name],
                            )
                        tool_entry = {
                            "name": tool.name,
                            "server_name": config.name,
                            "description": tool.description or "",
                            "input_schema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
                        }
                        self._tools.append(tool_entry)
                        self._tool_to_server[tool.name] = config.name
                        tool_count += 1

                    self._server_status[config.name] = {
                        "status": "connected",
                        "tool_count": tool_count,
                    }
                    logger.info(f"MCP server '{config.name}' connected with {tool_count} tools")

                elif config.transport == "sse" and config.url:
                    try:
                        from mcp.client.sse import sse_client

                        _validate_sse_url(config.url)
                        sse_transport = await self._exit_stack.enter_async_context(sse_client(config.url))
                        read_stream, write_stream = sse_transport
                        session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
                        await session.initialize()
                        self._sessions[config.name] = session

                        tools_result = await session.list_tools()
                        tool_count = 0
                        for tool in tools_result.tools:
                            if tool.name in self._tool_to_server:
                                logger.warning(
                                    "Tool name collision: '%s' from server '%s' shadows existing tool from '%s'",
                                    tool.name,
                                    config.name,
                                    self._tool_to_server[tool.name],
                                )
                            tool_entry = {
                                "name": tool.name,
                                "server_name": config.name,
                                "description": tool.description or "",
                                "input_schema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
                            }
                            self._tools.append(tool_entry)
                            self._tool_to_server[tool.name] = config.name
                            tool_count += 1

                        self._server_status[config.name] = {
                            "status": "connected",
                            "tool_count": tool_count,
                        }
                        logger.info(f"MCP SSE server '{config.name}' connected with {tool_count} tools")

                    except ImportError:
                        logger.warning(f"SSE client not available for MCP server '{config.name}'")
                        self._server_status[config.name] = {
                            "status": "error",
                            "tool_count": 0,
                        }

            except Exception as e:
                logger.warning(f"Failed to connect to MCP server '{config.name}': {e}")
                self._server_status[config.name] = {
                    "status": "error",
                    "tool_count": 0,
                }

    def get_openai_tools(self) -> list[dict[str, Any]] | None:
        if not self._tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            }
            for tool in self._tools
        ]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        server_name = self._tool_to_server.get(tool_name)
        if not server_name or server_name not in self._sessions:
            raise ValueError(f"Tool '{tool_name}' not found in any connected MCP server")

        _validate_tool_args(arguments)

        session = self._sessions[server_name]
        result = await session.call_tool(tool_name, arguments)

        if hasattr(result, "content"):
            contents = []
            for item in result.content:
                if hasattr(item, "text"):
                    contents.append(item.text)
                elif hasattr(item, "data"):
                    contents.append(str(item.data))
                else:
                    contents.append(str(item))
            return {"content": "\n".join(contents)}

        return {"result": str(result)}

    def get_tool_server_name(self, tool_name: str) -> str:
        return self._tool_to_server.get(tool_name, "unknown")

    def get_all_tools(self) -> list[dict[str, Any]]:
        return self._tools

    def get_server_statuses(self) -> dict[str, dict[str, Any]]:
        result = {}
        for config in self._configs:
            status = self._server_status.get(config.name, {"status": "disconnected", "tool_count": 0})
            result[config.name] = {
                "name": config.name,
                "transport": config.transport,
                **status,
            }
        return result

    async def shutdown(self) -> None:
        await self._exit_stack.aclose()
        self._sessions.clear()
        self._tools.clear()
        self._tool_to_server.clear()

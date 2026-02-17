"""MCP integration e2e tests via the web UI chat API.

These tests start a real Anteroom server with real MCP servers (mcp-server-time,
@modelcontextprotocol/server-everything) but mock the AI service to avoid needing
API keys. The MCP tool execution pipeline is fully real.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import httpx
import pytest

from tests.e2e.conftest import (
    mock_tool_call_stream,
    parse_sse_events,
    requires_mcp,
    requires_npx,
    requires_uvx,
)

pytestmark = [pytest.mark.e2e, requires_mcp]


class TestMcpServerConnection:
    """Verify MCP servers start and report as connected."""

    @requires_uvx
    def test_time_server_connected(self, mcp_api_client: httpx.Client) -> None:
        resp = mcp_api_client.get("/api/config")
        resp.raise_for_status()
        data = resp.json()
        servers = {s["name"]: s for s in data.get("mcp_servers", [])}
        assert "time" in servers, f"Expected 'time' server in {list(servers)}"
        assert servers["time"]["status"] == "connected"
        assert servers["time"]["tool_count"] >= 1

    @requires_npx
    def test_everything_server_connected(self, mcp_api_client: httpx.Client) -> None:
        resp = mcp_api_client.get("/api/config")
        resp.raise_for_status()
        data = resp.json()
        servers = {s["name"]: s for s in data.get("mcp_servers", [])}
        assert "everything" in servers, f"Expected 'everything' server in {list(servers)}"
        assert servers["everything"]["status"] == "connected"
        assert servers["everything"]["tool_count"] >= 1

    @requires_uvx
    def test_get_current_time_in_tool_list(self, mcp_api_client: httpx.Client) -> None:
        resp = mcp_api_client.get("/api/mcp/tools")
        resp.raise_for_status()
        tools = resp.json()
        tool_names = {t["name"] for t in tools}
        assert "get_current_time" in tool_names, f"Expected 'get_current_time' in {tool_names}"


class TestMcpToolExecution:
    """Execute MCP tools through the chat SSE pipeline with mocked AI."""

    @requires_uvx
    def test_time_tool_via_chat(self, mcp_api_client: httpx.Client, mcp_conversation_id: str) -> None:
        stream_fn = mock_tool_call_stream(
            tool_name="get_current_time",
            arguments={"timezone": "UTC"},
            tool_call_id=f"call_{uuid.uuid4().hex[:12]}",
        )

        with patch("anteroom.services.ai_service.AIService.stream_chat", side_effect=stream_fn):
            resp = mcp_api_client.post(
                f"/api/conversations/{mcp_conversation_id}/chat",
                json={"message": "What time is it?"},
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            )
        assert resp.status_code == 200

        events = parse_sse_events(resp)
        event_types = [e["event"] for e in events]

        assert "tool_call_start" in event_types, f"Expected tool_call_start in {event_types}"
        assert "tool_call_end" in event_types, f"Expected tool_call_end in {event_types}"

        end_events = [e for e in events if e["event"] == "tool_call_end"]
        assert len(end_events) >= 1
        tool_result = end_events[0]["data"]
        assert tool_result["status"] == "success", f"Tool failed: {tool_result}"
        output = tool_result.get("output", {})
        assert "content" in output or "result" in output, f"Unexpected output shape: {output}"

    @requires_npx
    def test_everything_echo_tool_via_chat(self, mcp_api_client: httpx.Client, mcp_conversation_id: str) -> None:
        stream_fn = mock_tool_call_stream(
            tool_name="echo",
            arguments={"message": "hello from e2e test"},
            tool_call_id=f"call_{uuid.uuid4().hex[:12]}",
        )

        with patch("anteroom.services.ai_service.AIService.stream_chat", side_effect=stream_fn):
            resp = mcp_api_client.post(
                f"/api/conversations/{mcp_conversation_id}/chat",
                json={"message": "Echo this message"},
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            )
        assert resp.status_code == 200

        events = parse_sse_events(resp)
        end_events = [e for e in events if e["event"] == "tool_call_end"]
        assert len(end_events) >= 1
        tool_result = end_events[0]["data"]
        assert tool_result["status"] == "success", f"Echo tool failed: {tool_result}"

    @requires_uvx
    def test_tool_result_stored_in_db(self, mcp_api_client: httpx.Client, mcp_conversation_id: str) -> None:
        call_id = f"call_{uuid.uuid4().hex[:12]}"
        stream_fn = mock_tool_call_stream(
            tool_name="get_current_time",
            arguments={"timezone": "UTC"},
            tool_call_id=call_id,
        )

        with patch("anteroom.services.ai_service.AIService.stream_chat", side_effect=stream_fn):
            resp = mcp_api_client.post(
                f"/api/conversations/{mcp_conversation_id}/chat",
                json={"message": "Time check for audit"},
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            )
        assert resp.status_code == 200

        conv_resp = mcp_api_client.get(f"/api/conversations/{mcp_conversation_id}")
        conv_resp.raise_for_status()
        conv_data = conv_resp.json()
        messages = conv_data.get("messages", [])

        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        assert len(assistant_msgs) >= 1, "Expected at least one assistant message stored"

    @requires_uvx
    def test_mcp_tool_auto_approved(self, mcp_api_client: httpx.Client, mcp_conversation_id: str) -> None:
        stream_fn = mock_tool_call_stream(
            tool_name="get_current_time",
            arguments={"timezone": "UTC"},
            tool_call_id=f"call_{uuid.uuid4().hex[:12]}",
        )

        with patch("anteroom.services.ai_service.AIService.stream_chat", side_effect=stream_fn):
            resp = mcp_api_client.post(
                f"/api/conversations/{mcp_conversation_id}/chat",
                json={"message": "Time please"},
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            )
        assert resp.status_code == 200

        events = parse_sse_events(resp)
        event_types = [e["event"] for e in events]
        assert "approval_required" not in event_types, "Auto mode should not require approval"


class TestMcpErrorHandling:
    """Verify graceful error handling for MCP tool failures."""

    def test_nonexistent_tool_returns_error(self, mcp_api_client: httpx.Client, mcp_conversation_id: str) -> None:
        stream_fn = mock_tool_call_stream(
            tool_name="nonexistent_tool_xyz",
            arguments={},
            tool_call_id=f"call_{uuid.uuid4().hex[:12]}",
        )

        with patch("anteroom.services.ai_service.AIService.stream_chat", side_effect=stream_fn):
            resp = mcp_api_client.post(
                f"/api/conversations/{mcp_conversation_id}/chat",
                json={"message": "Call a fake tool"},
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            )
        assert resp.status_code == 200

        events = parse_sse_events(resp)
        end_events = [e for e in events if e["event"] == "tool_call_end"]
        assert len(end_events) >= 1
        tool_result = end_events[0]["data"]
        assert tool_result["status"] == "error", f"Expected error status, got: {tool_result}"

    @requires_uvx
    def test_tool_with_invalid_args(self, mcp_api_client: httpx.Client, mcp_conversation_id: str) -> None:
        stream_fn = mock_tool_call_stream(
            tool_name="get_current_time",
            arguments={"timezone": "INVALID/NOT_A_REAL_TIMEZONE_12345"},
            tool_call_id=f"call_{uuid.uuid4().hex[:12]}",
        )

        with patch("anteroom.services.ai_service.AIService.stream_chat", side_effect=stream_fn):
            resp = mcp_api_client.post(
                f"/api/conversations/{mcp_conversation_id}/chat",
                json={"message": "What time in nowhere?"},
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            )
        assert resp.status_code == 200

        events = parse_sse_events(resp)
        end_events = [e for e in events if e["event"] == "tool_call_end"]
        assert len(end_events) >= 1
        tool_result = end_events[0]["data"]
        assert tool_result["status"] == "error", f"Expected error for invalid timezone, got: {tool_result}"

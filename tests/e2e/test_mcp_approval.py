"""MCP approval flow e2e tests.

Tests all four approval outcomes:
1. Approve once — tool executes, next call still requires approval
2. Deny — tool returns error
3. Approve for session — subsequent calls to the same tool auto-approve
4. Approve always — persists to config (tested via in-memory session grant)

Strategy: The server runs in a background thread within the same process, so
we poll app.state.pending_approvals directly to discover approval IDs, then
respond via the HTTP API.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from tests.e2e.conftest import (
    mock_tool_call_stream,
    parse_sse_events,
    requires_mcp,
    requires_uvx,
)

pytestmark = [pytest.mark.e2e, requires_mcp, requires_uvx]


def _poll_pending_approval(
    app: Any,
    timeout: float = 15.0,
    poll_interval: float = 0.1,
) -> str | None:
    """Poll app.state.pending_approvals until an entry appears. Returns the approval_id."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = getattr(app.state, "pending_approvals", {})
        if pending:
            return next(iter(pending.keys()))
        time.sleep(poll_interval)
    return None


def _do_chat(
    client: httpx.Client,
    conversation_id: str,
    message: str,
    stream_fn: object,
    result: dict,
) -> None:
    """Background thread: send a chat request with mocked AI."""
    try:
        with patch("anteroom.services.ai_service.AIService.stream_chat", side_effect=stream_fn):
            resp = client.post(
                f"/api/conversations/{conversation_id}/chat",
                json={"message": message},
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            )
        result["response"] = resp
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"


def _respond_to_approval(
    client: httpx.Client,
    approval_id: str,
    approved: bool,
    scope: str = "once",
) -> httpx.Response:
    """Send approval/denial response via the REST API."""
    return client.post(
        f"/api/approvals/{approval_id}/respond",
        json={"approved": approved, "scope": scope},
        headers={"Content-Type": "application/json"},
    )


class TestMcpApprovalApproveOnce:
    """Test: approve once — tool executes, but next call still requires approval."""

    def test_approve_once_executes_tool(
        self,
        mcp_approval_client: httpx.Client,
        mcp_approval_conversation_id: str,
        mcp_approval_app: Any,
    ) -> None:
        call_id = f"call_{uuid.uuid4().hex[:12]}"
        stream_fn = mock_tool_call_stream(
            tool_name="get_current_time",
            arguments={"timezone": "UTC"},
            tool_call_id=call_id,
        )

        chat_result: dict = {}
        chat_thread = threading.Thread(
            target=_do_chat,
            args=(mcp_approval_client, mcp_approval_conversation_id, "Time once?", stream_fn, chat_result),
            daemon=True,
        )
        chat_thread.start()

        approval_id = _poll_pending_approval(mcp_approval_app)
        assert approval_id is not None, "No pending approval appeared"

        resp = _respond_to_approval(mcp_approval_client, approval_id, approved=True, scope="once")
        assert resp.status_code == 200
        assert resp.json()["approved"] is True
        assert resp.json()["scope"] == "once"

        chat_thread.join(timeout=30)
        assert "response" in chat_result, f"Chat failed: {chat_result}"

        events = parse_sse_events(chat_result["response"])
        event_types = [e["event"] for e in events]
        assert "tool_call_start" in event_types
        assert "tool_call_end" in event_types

        end_events = [e for e in events if e["event"] == "tool_call_end"]
        assert end_events[0]["data"]["status"] == "success"
        output = end_events[0]["data"].get("output", {})
        assert "content" in output or "result" in output

    def test_approve_once_does_not_persist(
        self,
        mcp_approval_client: httpx.Client,
        mcp_approval_conversation_id: str,
        mcp_approval_app: Any,
    ) -> None:
        """After 'once' approval, the next call to the same tool should still require approval."""
        call_id = f"call_{uuid.uuid4().hex[:12]}"
        stream_fn = mock_tool_call_stream(
            tool_name="get_current_time",
            arguments={"timezone": "UTC"},
            tool_call_id=call_id,
        )

        chat_result: dict = {}
        chat_thread = threading.Thread(
            target=_do_chat,
            args=(mcp_approval_client, mcp_approval_conversation_id, "Time once again?", stream_fn, chat_result),
            daemon=True,
        )
        chat_thread.start()

        # If approval is NOT required, the chat would complete without blocking.
        # If it IS required, we'll find a pending approval.
        approval_id = _poll_pending_approval(mcp_approval_app, timeout=5)

        if approval_id is not None:
            # Good — approval was required again. Approve it to unblock.
            _respond_to_approval(mcp_approval_client, approval_id, approved=True, scope="once")

        chat_thread.join(timeout=30)
        assert "response" in chat_result

        # The key assertion: approval WAS required (approval_id was found)
        assert approval_id is not None, "Expected approval to be required again after 'once' scope"


class TestMcpApprovalDeny:
    """Test: deny — tool execution is blocked."""

    def test_deny_returns_error(
        self,
        mcp_approval_client: httpx.Client,
        mcp_approval_conversation_id: str,
        mcp_approval_app: Any,
    ) -> None:
        call_id = f"call_{uuid.uuid4().hex[:12]}"
        stream_fn = mock_tool_call_stream(
            tool_name="get_current_time",
            arguments={"timezone": "UTC"},
            tool_call_id=call_id,
        )

        chat_result: dict = {}
        chat_thread = threading.Thread(
            target=_do_chat,
            args=(mcp_approval_client, mcp_approval_conversation_id, "Time denied?", stream_fn, chat_result),
            daemon=True,
        )
        chat_thread.start()

        approval_id = _poll_pending_approval(mcp_approval_app)
        assert approval_id is not None, "No pending approval appeared"

        resp = _respond_to_approval(mcp_approval_client, approval_id, approved=False, scope="once")
        assert resp.status_code == 200
        assert resp.json()["approved"] is False

        chat_thread.join(timeout=30)
        assert "response" in chat_result, f"Chat failed: {chat_result}"

        events = parse_sse_events(chat_result["response"])
        end_events = [e for e in events if e["event"] == "tool_call_end"]
        assert len(end_events) >= 1

        output = end_events[0]["data"].get("output", {})
        assert "error" in output or "denied" in str(output).lower(), f"Expected denial in output: {output}"


class TestMcpApprovalSessionScope:
    """Test: approve for session — subsequent calls auto-approve."""

    def test_session_scope_first_call_approved(
        self,
        mcp_approval_client: httpx.Client,
        mcp_approval_conversation_id: str,
        mcp_approval_app: Any,
    ) -> None:
        call_id = f"call_{uuid.uuid4().hex[:12]}"
        stream_fn = mock_tool_call_stream(
            tool_name="get_current_time",
            arguments={"timezone": "America/New_York"},
            tool_call_id=call_id,
        )

        chat_result: dict = {}
        chat_thread = threading.Thread(
            target=_do_chat,
            args=(mcp_approval_client, mcp_approval_conversation_id, "Session grant", stream_fn, chat_result),
            daemon=True,
        )
        chat_thread.start()

        approval_id = _poll_pending_approval(mcp_approval_app)
        assert approval_id is not None

        resp = _respond_to_approval(mcp_approval_client, approval_id, approved=True, scope="session")
        assert resp.status_code == 200
        assert resp.json()["scope"] == "session"

        chat_thread.join(timeout=30)
        assert "response" in chat_result

        events = parse_sse_events(chat_result["response"])
        end_events = [e for e in events if e["event"] == "tool_call_end"]
        assert len(end_events) >= 1
        assert end_events[0]["data"]["status"] == "success"

    def test_session_scope_subsequent_call_auto_approved(
        self,
        mcp_approval_client: httpx.Client,
        mcp_approval_conversation_id: str,
        mcp_approval_app: Any,
    ) -> None:
        """After session grant, the same tool should execute without approval prompt."""
        call_id = f"call_{uuid.uuid4().hex[:12]}"
        stream_fn = mock_tool_call_stream(
            tool_name="get_current_time",
            arguments={"timezone": "Europe/London"},
            tool_call_id=call_id,
        )

        # This should complete without blocking (no approval needed)
        with patch("anteroom.services.ai_service.AIService.stream_chat", side_effect=stream_fn):
            resp = mcp_approval_client.post(
                f"/api/conversations/{mcp_approval_conversation_id}/chat",
                json={"message": "Time no approval"},
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            )
        assert resp.status_code == 200

        events = parse_sse_events(resp)
        event_types = [e["event"] for e in events]
        assert "tool_call_end" in event_types

        end_events = [e for e in events if e["event"] == "tool_call_end"]
        assert end_events[0]["data"]["status"] == "success"


class TestMcpApprovalAlwaysScope:
    """Test: approve always — persists to session permissions (and config in real use)."""

    def test_always_scope_grants_persistent_access(
        self,
        mcp_approval_client: httpx.Client,
        mcp_approval_conversation_id: str,
        mcp_approval_app: Any,
    ) -> None:
        """Use a different MCP tool to test 'always' scope independently.

        We use get_current_time with a distinct conversation to avoid interference
        from session-scope grants in earlier tests. The 'always' scope triggers
        write_allowed_tool (which we mock to prevent config file writes), but
        the session permission is granted immediately.
        """
        # Clear any existing session permissions for this tool to test cleanly
        tool_registry = mcp_approval_app.state.tool_registry
        tool_registry._session_allowed.discard("get_current_time")

        call_id = f"call_{uuid.uuid4().hex[:12]}"
        stream_fn = mock_tool_call_stream(
            tool_name="get_current_time",
            arguments={"timezone": "Asia/Tokyo"},
            tool_call_id=call_id,
        )

        chat_result: dict = {}
        chat_thread = threading.Thread(
            target=_do_chat,
            args=(mcp_approval_client, mcp_approval_conversation_id, "Always scope", stream_fn, chat_result),
            daemon=True,
        )
        chat_thread.start()

        approval_id = _poll_pending_approval(mcp_approval_app)
        assert approval_id is not None

        # Patch write_allowed_tool to prevent actual config file writes
        with patch("anteroom.config.write_allowed_tool"):
            resp = _respond_to_approval(mcp_approval_client, approval_id, approved=True, scope="always")
        assert resp.status_code == 200
        assert resp.json()["scope"] == "always"

        chat_thread.join(timeout=30)
        assert "response" in chat_result

        events = parse_sse_events(chat_result["response"])
        end_events = [e for e in events if e["event"] == "tool_call_end"]
        assert len(end_events) >= 1
        assert end_events[0]["data"]["status"] == "success"

        # Verify the session permission was granted
        assert "get_current_time" in tool_registry._session_allowed

    def test_always_scope_subsequent_call_auto_approved(
        self,
        mcp_approval_client: httpx.Client,
        mcp_approval_conversation_id: str,
    ) -> None:
        """After 'always' grant, subsequent calls should auto-approve."""
        call_id = f"call_{uuid.uuid4().hex[:12]}"
        stream_fn = mock_tool_call_stream(
            tool_name="get_current_time",
            arguments={"timezone": "Pacific/Auckland"},
            tool_call_id=call_id,
        )

        with patch("anteroom.services.ai_service.AIService.stream_chat", side_effect=stream_fn):
            resp = mcp_approval_client.post(
                f"/api/conversations/{mcp_approval_conversation_id}/chat",
                json={"message": "Auto after always"},
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            )
        assert resp.status_code == 200

        events = parse_sse_events(resp)
        end_events = [e for e in events if e["event"] == "tool_call_end"]
        assert len(end_events) >= 1
        assert end_events[0]["data"]["status"] == "success"


class TestMcpApprovalEdgeCases:
    """Edge cases for the approval system."""

    def test_expired_approval_returns_404(self, mcp_approval_client: httpx.Client) -> None:
        resp = mcp_approval_client.post(
            "/api/approvals/nonexistent_id_12345/respond",
            json={"approved": True, "scope": "once"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 404

    def test_wrong_content_type_rejected(self, mcp_approval_client: httpx.Client) -> None:
        resp = mcp_approval_client.post(
            "/api/approvals/some_valid_id/respond",
            content="approved=true",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code in (415, 422)

    def test_double_respond_returns_404(
        self,
        mcp_approval_client: httpx.Client,
        mcp_approval_conversation_id: str,
        mcp_approval_app: Any,
    ) -> None:
        """Responding twice to the same approval should fail on the second attempt."""
        # Clear session permission so approval is required again
        tool_registry = mcp_approval_app.state.tool_registry
        tool_registry._session_allowed.discard("get_current_time")

        call_id = f"call_{uuid.uuid4().hex[:12]}"
        stream_fn = mock_tool_call_stream(
            tool_name="get_current_time",
            arguments={"timezone": "UTC"},
            tool_call_id=call_id,
        )

        chat_result: dict = {}
        chat_thread = threading.Thread(
            target=_do_chat,
            args=(mcp_approval_client, mcp_approval_conversation_id, "Double respond", stream_fn, chat_result),
            daemon=True,
        )
        chat_thread.start()

        approval_id = _poll_pending_approval(mcp_approval_app)
        assert approval_id is not None

        # First response succeeds
        resp1 = _respond_to_approval(mcp_approval_client, approval_id, approved=True, scope="once")
        assert resp1.status_code == 200

        # Second response should fail (approval already consumed via atomic pop)
        resp2 = _respond_to_approval(mcp_approval_client, approval_id, approved=True, scope="once")
        assert resp2.status_code == 404

        chat_thread.join(timeout=30)

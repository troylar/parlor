"""E2E regression: web approval flow unchanged after serialize_tools branch.

Uses the same architecture as test_mcp_approval.py: real server in a
background thread, mock AI that triggers a tool call requiring approval,
poll pending_approvals, respond via API, assert the SSE stream contains
tool_call_start/tool_call_end with no workflow_pause events.

This proves the default (serialize_tools=False) web approval path is
unchanged after the serialized branch was added to agent_loop.py.
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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = getattr(app.state, "pending_approvals", {})
        if pending:
            return list(pending.keys())[0]
        time.sleep(poll_interval)
    return None


def _do_chat(
    client: httpx.Client,
    conversation_id: str,
    message: str,
    stream_fn: object,
    result: dict,
) -> None:
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
    return client.post(
        f"/api/approvals/{approval_id}/respond",
        json={"approved": approved, "scope": scope},
        headers={"Content-Type": "application/json"},
    )


class TestWebApprovalRegressionAfterSerializeTools:
    """Real server E2E: web approval flow unchanged after serialize_tools branch.

    These tests exercise the full web approval path — mock AI triggers a tool
    call, the server pauses for approval, we respond via the HTTP API, and
    the SSE stream completes with tool_call_start/tool_call_end. The key
    regression assertion is that workflow_pause never appears.
    """

    def test_approve_once_still_works_no_workflow_pause(
        self,
        mcp_approval_client: httpx.Client,
        mcp_approval_conversation_id: str,
        mcp_approval_app: Any,
    ) -> None:
        """Normal approve-once flow produces tool_call_end, not workflow_pause."""
        call_id = f"call_{uuid.uuid4().hex[:12]}"
        stream_fn = mock_tool_call_stream(
            tool_name="get_current_time",
            arguments={"timezone": "UTC"},
            tool_call_id=call_id,
        )

        chat_result: dict = {}
        chat_thread = threading.Thread(
            target=_do_chat,
            args=(mcp_approval_client, mcp_approval_conversation_id, "Regression test", stream_fn, chat_result),
            daemon=True,
        )
        chat_thread.start()

        approval_id = _poll_pending_approval(mcp_approval_app)
        assert approval_id is not None, "No pending approval appeared"

        resp = _respond_to_approval(mcp_approval_client, approval_id, approved=True, scope="once")
        assert resp.status_code == 200

        chat_thread.join(timeout=30)
        assert "response" in chat_result, f"Chat failed: {chat_result}"

        events = parse_sse_events(chat_result["response"])
        event_types = {e["event"] for e in events}

        assert "tool_call_start" in event_types
        assert "tool_call_end" in event_types
        assert "workflow_pause" not in event_types

        end_events = [e for e in events if e["event"] == "tool_call_end"]
        assert end_events[0]["data"]["status"] == "success"

    def test_deny_still_works_no_workflow_pause(
        self,
        mcp_approval_client: httpx.Client,
        mcp_approval_conversation_id: str,
        mcp_approval_app: Any,
    ) -> None:
        """Normal deny flow produces tool_call_end with error, not workflow_pause."""
        # Ensure approval is required
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
            args=(mcp_approval_client, mcp_approval_conversation_id, "Deny regression", stream_fn, chat_result),
            daemon=True,
        )
        chat_thread.start()

        approval_id = _poll_pending_approval(mcp_approval_app)
        assert approval_id is not None, "No pending approval appeared"

        resp = _respond_to_approval(mcp_approval_client, approval_id, approved=False)
        assert resp.status_code == 200

        chat_thread.join(timeout=30)
        assert "response" in chat_result, f"Chat failed: {chat_result}"

        events = parse_sse_events(chat_result["response"])
        event_types = {e["event"] for e in events}

        assert "workflow_pause" not in event_types
        end_events = [e for e in events if e["event"] == "tool_call_end"]
        assert len(end_events) >= 1
        output = end_events[0]["data"].get("output", {})
        assert "error" in output or "denied" in str(output).lower()

    def test_session_grant_still_works_no_workflow_pause(
        self,
        mcp_approval_client: httpx.Client,
        mcp_approval_conversation_id: str,
        mcp_approval_app: Any,
    ) -> None:
        """Session-scoped auto-approve still works, no workflow_pause leak."""
        tool_registry = mcp_approval_app.state.tool_registry
        tool_registry.grant_session_permission("get_current_time")

        call_id = f"call_{uuid.uuid4().hex[:12]}"
        stream_fn = mock_tool_call_stream(
            tool_name="get_current_time",
            arguments={"timezone": "Europe/London"},
            tool_call_id=call_id,
        )

        with patch("anteroom.services.ai_service.AIService.stream_chat", side_effect=stream_fn):
            resp = mcp_approval_client.post(
                f"/api/conversations/{mcp_approval_conversation_id}/chat",
                json={"message": "Auto-approve regression"},
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            )
        assert resp.status_code == 200

        events = parse_sse_events(resp)
        event_types = {e["event"] for e in events}

        assert "tool_call_end" in event_types
        assert "workflow_pause" not in event_types
        end_events = [e for e in events if e["event"] == "tool_call_end"]
        assert end_events[0]["data"]["status"] == "success"

"""Shared-core Focus & Fold coverage through the real web SSE pipeline."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import httpx
import pytest

from tests.e2e.conftest import mock_tool_call_stream, parse_sse_events

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.filterwarnings("ignore:.*websockets\\.legacy is deprecated.*:DeprecationWarning"),
    pytest.mark.filterwarnings(
        "ignore:.*websockets\\.server\\.WebSocketServerProtocol is deprecated.*:DeprecationWarning"
    ),
]

class TestFocusFoldAgentLoopWeb:
    """Verify the real chat SSE route emits grouped tool-batch events from agent_loop."""

    @staticmethod
    def _mock_two_tool_batches() -> object:
        call_count = 0

        async def _stream(
            messages: list[dict[str, object]],
            tools: list[dict[str, object]] | None = None,
            cancel_event: object | None = None,
            extra_system_prompt: str | None = None,
        ):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield {
                    "event": "tool_call",
                    "data": {
                        "id": "call_batch_one",
                        "function_name": "read_file",
                        "arguments": {"path": "src/anteroom/cli/renderer.py", "limit": 1},
                    },
                }
                yield {"event": "done", "data": {}}
            elif call_count == 2:
                yield {
                    "event": "tool_call",
                    "data": {
                        "id": "call_batch_two",
                        "function_name": "grep",
                        "arguments": {"path": "src/anteroom/cli", "pattern": "FoldGroup"},
                    },
                }
                yield {"event": "done", "data": {}}
            else:
                yield {"event": "token", "data": {"content": "Final answer after two batches."}}
                yield {"event": "done", "data": {}}

        return _stream

    def test_agent_loop_emits_fold_batch_events_via_web_chat(
        self,
        api_client: httpx.Client,
        conversation_id: str,
    ) -> None:
        stream_fn = mock_tool_call_stream(
            tool_name="read_file",
            arguments={"path": "src/anteroom/cli/renderer.py", "limit": 1},
            tool_call_id=f"call_{uuid.uuid4().hex[:12]}",
            follow_up_text="Summary after the file read.",
        )

        with patch("anteroom.services.ai_service.AIService.stream_chat", side_effect=stream_fn):
            resp = api_client.post(
                f"/api/conversations/{conversation_id}/chat",
                json={"message": "Read a file and summarize it."},
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            )

        assert resp.status_code == 200

        events = parse_sse_events(resp)
        event_types = [e["event"] for e in events]

        assert "tool_batch_start" in event_types, event_types
        assert "tool_call_start" in event_types, event_types
        assert "tool_call_end" in event_types, event_types
        assert "tool_batch_end" in event_types, event_types
        assert "token" in event_types, event_types
        assert "done" in event_types, event_types

        batch_start_idx = event_types.index("tool_batch_start")
        tool_start_idx = event_types.index("tool_call_start")
        tool_end_idx = event_types.index("tool_call_end")
        batch_end_idx = event_types.index("tool_batch_end")
        token_idx = event_types.index("token")
        done_idx = event_types.index("done")

        assert tool_start_idx < batch_start_idx < tool_end_idx < batch_end_idx < token_idx < done_idx

        batch_end = next(e for e in events if e["event"] == "tool_batch_end")
        assert batch_end["data"]["call_count"] == 1
        assert isinstance(batch_end["data"]["elapsed_seconds"], float)

        tool_end = next(e for e in events if e["event"] == "tool_call_end")
        assert tool_end["data"]["status"] == "success"
        assert "Rich-based terminal output" in tool_end["data"]["output"].get("content", "")

        detail = api_client.get(f"/api/conversations/{conversation_id}")
        detail.raise_for_status()
        assistant_messages = [m for m in detail.json().get("messages", []) if m["role"] == "assistant"]
        assert assistant_messages, "Expected assistant messages to be stored after the fold turn"
        assert "Summary after the file read." in assistant_messages[-1]["content"]

    def test_agent_loop_closes_batch_before_followup_after_tool_error(
        self,
        api_client: httpx.Client,
        conversation_id: str,
    ) -> None:
        stream_fn = mock_tool_call_stream(
            tool_name="missing_tool_for_focus_fold",
            arguments={"path": "missing.py"},
            tool_call_id=f"call_{uuid.uuid4().hex[:12]}",
            follow_up_text="The read failed, so I stopped there.",
        )

        with patch("anteroom.services.ai_service.AIService.stream_chat", side_effect=stream_fn):
            resp = api_client.post(
                f"/api/conversations/{conversation_id}/chat",
                json={"message": "Try a tool that will fail."},
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            )

        assert resp.status_code == 200

        events = parse_sse_events(resp)
        event_types = [e["event"] for e in events]

        assert "tool_batch_start" in event_types, event_types
        assert "tool_call_end" in event_types, event_types
        assert "tool_batch_end" in event_types, event_types
        assert "token" in event_types, event_types
        assert "done" in event_types, event_types

        tool_end_idx = event_types.index("tool_call_end")
        batch_end_idx = event_types.index("tool_batch_end")
        token_idx = event_types.index("token")
        done_idx = event_types.index("done")

        assert tool_end_idx < batch_end_idx < token_idx < done_idx

        tool_end = next(e for e in events if e["event"] == "tool_call_end")
        assert tool_end["data"]["status"] == "error"
        assert "Unknown tool" in tool_end["data"]["output"].get("error", "")

        detail = api_client.get(f"/api/conversations/{conversation_id}")
        detail.raise_for_status()
        assistant_messages = [m for m in detail.json().get("messages", []) if m["role"] == "assistant"]
        assert assistant_messages
        assert "The read failed, so I stopped there." in assistant_messages[-1]["content"]

    def test_agent_loop_emits_two_distinct_tool_batches_via_web_chat(
        self,
        api_client: httpx.Client,
        conversation_id: str,
    ) -> None:
        with patch("anteroom.services.ai_service.AIService.stream_chat", side_effect=self._mock_two_tool_batches()):
            resp = api_client.post(
                f"/api/conversations/{conversation_id}/chat",
                json={"message": "Run two tools before answering."},
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            )

        assert resp.status_code == 200

        events = parse_sse_events(resp)
        batch_starts = [e for e in events if e["event"] == "tool_batch_start"]
        batch_ends = [e for e in events if e["event"] == "tool_batch_end"]
        tool_ends = [e for e in events if e["event"] == "tool_call_end"]
        tokens = [e for e in events if e["event"] == "token"]

        assert len(batch_starts) == 2
        assert len(batch_ends) == 2
        assert len(tool_ends) == 2
        assert any("Final answer after two batches." in e["data"]["content"] for e in tokens)

        event_types = [e["event"] for e in events]
        first_batch_end_idx = event_types.index("tool_batch_end")
        second_batch_start_idx = event_types.index("tool_batch_start", first_batch_end_idx + 1)
        second_batch_end_idx = event_types.index("tool_batch_end", second_batch_start_idx + 1)
        token_idx = event_types.index("token")

        assert first_batch_end_idx < second_batch_start_idx < second_batch_end_idx < token_idx

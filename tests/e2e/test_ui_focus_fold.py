"""Browser UX coverage for Focus & Fold ordering and grouped batch rendering."""

from __future__ import annotations

import json
from importlib.util import find_spec
from unittest.mock import patch

import pytest

from tests.e2e.conftest import mock_tool_call_stream

HAS_PLAYWRIGHT = find_spec("playwright.sync_api") is not None and find_spec("pytest_playwright") is not None

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed"),
]


def _sse(events: list[tuple[str, dict]]) -> str:
    return "".join(f"event: {kind}\ndata: {json.dumps(data)}\n\n" for kind, data in events)


class TestFocusFoldWeb:
    def test_real_backend_fold_turn_keeps_text_chronological(self, page_with_conversation) -> None:
        """Browser should render real backend fold events in chronological order."""
        page, conv_id = page_with_conversation
        stream_fn = mock_tool_call_stream(
            tool_name="read_file",
            arguments={"path": "src/anteroom/cli/renderer.py", "limit": 1},
            follow_up_text="Follow-up after the real tool batch.",
        )

        with patch("anteroom.services.ai_service.AIService.stream_chat", side_effect=stream_fn):
            page.fill("#message-input", "Read a file and summarize it.")
            page.click("#btn-send")
            page.wait_for_selector(".message.assistant .tool-batch", timeout=5000)
            page.wait_for_function(
                """() => {
                    const segments = document.querySelectorAll('.message.assistant:last-of-type .assistant-text-segment');
                    return Array.from(segments).some((el) => (el.textContent || '').includes('Follow-up after the real tool batch.'));
                }""",
                timeout=5000,
            )

        order = page.evaluate(
            """() => {
                const content = document.querySelector('.message.assistant:last-of-type .message-content');
                return Array.from(content.children).map((el) => ({
                    className: el.className,
                    text: (el.textContent || '').trim(),
                }));
            }"""
        )

        assert len(order) >= 3
        assert "assistant-text-segment" in order[0]["className"]
        assert "tool-batch" in order[1]["className"]
        assert "Tools (1 call" in order[1]["text"]
        assert "assistant-text-segment" in order[2]["className"]
        assert "Follow-up after the real tool batch." in order[2]["text"]

    def test_tool_batch_stays_between_pre_and_post_tool_text(self, page_with_conversation) -> None:
        """Assistant prose should remain chronologically interleaved around tool batches."""
        page, conv_id = page_with_conversation
        body = _sse(
            [
                ("thinking", {}),
                ("token", {"content": "Intro before tools. "}),
                ("tool_batch_start", {"call_count": 1}),
                (
                    "tool_call_start",
                    {"id": "call1", "tool_name": "read_file", "input": {"path": "src/app.py"}},
                ),
                (
                    "tool_call_end",
                    {"id": "call1", "status": "success", "output": {"content": "ok"}},
                ),
                ("tool_batch_end", {"call_count": 1, "elapsed_seconds": 0.3}),
                ("token", {"content": "Follow-up after tools."}),
                ("done", {}),
            ]
        )

        page.route(
            f"**/api/conversations/{conv_id}/chat",
            lambda route: route.fulfill(
                status=200,
                content_type="text/event-stream",
                body=body,
                headers={"Cache-Control": "no-cache"},
            ),
        )

        page.fill("#message-input", "exercise focus fold")
        page.click("#btn-send")
        page.wait_for_selector(".message.assistant .tool-batch", timeout=5000)

        order = page.evaluate(
            """() => {
                const content = document.querySelector('.message.assistant:last-of-type .message-content');
                return Array.from(content.children).map((el) => ({
                    className: el.className,
                    text: (el.textContent || '').trim(),
                }));
            }"""
        )

        assert len(order) >= 3
        assert "assistant-text-segment" in order[0]["className"]
        assert "Intro before tools." in order[0]["text"]
        assert "tool-batch" in order[1]["className"]
        assert "Tools (1 call, 0.3s)" in order[1]["text"]
        assert "assistant-text-segment" in order[2]["className"]
        assert "Follow-up after tools." in order[2]["text"]

    def test_multiple_tool_batches_remain_chronological(self, page_with_conversation) -> None:
        """Multiple batches in one turn should preserve text/tool ordering across both boundaries."""
        page, conv_id = page_with_conversation
        body = _sse(
            [
                ("token", {"content": "Before batch one. "}),
                ("tool_batch_start", {"call_count": 1}),
                (
                    "tool_call_start",
                    {"id": "call1", "tool_name": "read_file", "input": {"path": "src/app.py"}},
                ),
                ("tool_call_end", {"id": "call1", "status": "success", "output": {"content": "ok"}}),
                ("tool_batch_end", {"call_count": 1, "elapsed_seconds": 0.2}),
                ("token", {"content": "Between batches. "}),
                ("tool_batch_start", {"call_count": 1}),
                (
                    "tool_call_start",
                    {"id": "call2", "tool_name": "grep", "input": {"path": "src", "pattern": "FoldGroup"}},
                ),
                ("tool_call_end", {"id": "call2", "status": "success", "output": {"stdout": "match"}}),
                ("tool_batch_end", {"call_count": 1, "elapsed_seconds": 0.2}),
                ("token", {"content": "After batch two."}),
                ("done", {}),
            ]
        )

        page.route(
            f"**/api/conversations/{conv_id}/chat",
            lambda route: route.fulfill(
                status=200,
                content_type="text/event-stream",
                body=body,
                headers={"Cache-Control": "no-cache"},
            ),
        )

        page.fill("#message-input", "exercise multi batch focus fold")
        page.click("#btn-send")
        page.wait_for_selector(".message.assistant .tool-batch", timeout=5000)

        order = page.evaluate(
            """() => {
                const content = document.querySelector('.message.assistant:last-of-type .message-content');
                return Array.from(content.children).map((el) => ({
                    className: el.className,
                    text: (el.textContent || '').trim(),
                }));
            }"""
        )

        assert len(order) >= 5
        assert "Before batch one." in order[0]["text"]
        assert "tool-batch" in order[1]["className"]
        assert "Between batches." in order[2]["text"]
        assert "tool-batch" in order[3]["className"]
        assert "After batch two." in order[4]["text"]

    def test_tool_batch_is_collapsed_by_default_and_expandable(self, page_with_conversation) -> None:
        """The grouped batch should default collapsed and toggle open from the summary."""
        page, conv_id = page_with_conversation
        body = _sse(
            [
                ("tool_batch_start", {"call_count": 1}),
                (
                    "tool_call_start",
                    {"id": "call1", "tool_name": "read_file", "input": {"path": "src/app.py"}},
                ),
                (
                    "tool_call_end",
                    {"id": "call1", "status": "success", "output": {"content": "ok"}},
                ),
                ("tool_batch_end", {"call_count": 1, "elapsed_seconds": 0.3}),
                ("done", {}),
            ]
        )
        page.route(
            f"**/api/conversations/{conv_id}/chat",
            lambda route: route.fulfill(
                status=200,
                content_type="text/event-stream",
                body=body,
                headers={"Cache-Control": "no-cache"},
            ),
        )

        page.fill("#message-input", "exercise collapsed fold")
        page.click("#btn-send")
        batch = page.locator(".message.assistant:last-of-type .tool-batch")
        batch.wait_for(timeout=5000)
        assert batch.evaluate("el => el.open") is False
        page.click(".message.assistant:last-of-type .tool-batch > summary")
        page.wait_for_timeout(150)
        assert batch.evaluate("el => el.open") is True

    def test_failed_batch_surfaces_error_summary(self, page_with_conversation) -> None:
        """A batch interrupted by an error should retain an error-labelled summary."""
        page, conv_id = page_with_conversation
        body = _sse(
            [
                ("tool_batch_start", {"call_count": 1}),
                (
                    "tool_call_start",
                    {"id": "call1", "tool_name": "read_file", "input": {"path": "missing.py"}},
                ),
                ("error", {"message": "Tool failed"}),
            ]
        )
        page.route(
            f"**/api/conversations/{conv_id}/chat",
            lambda route: route.fulfill(
                status=200,
                content_type="text/event-stream",
                body=body,
                headers={"Cache-Control": "no-cache"},
            ),
        )

        page.fill("#message-input", "exercise failed fold")
        page.click("#btn-send")
        batch = page.locator(".message.assistant:last-of-type .tool-batch")
        batch.wait_for(timeout=5000)
        summary_text = page.locator(".message.assistant:last-of-type .tool-batch > summary").inner_text()
        assert "error" in summary_text.lower()
        assert page.locator(".message.assistant:last-of-type .error-message").is_visible()

    def test_real_backend_tool_error_keeps_batch_summary_before_followup(self, page_with_conversation) -> None:
        """Real backend tool failures should still leave the fold batch ahead of follow-up text."""
        page, _conv_id = page_with_conversation

        async def failing_stream(messages, tools=None, cancel_event=None, extra_system_prompt=None):
            yield {
                "event": "tool_call",
                "data": {
                    "id": "call_error",
                    "function_name": "missing_tool_for_focus_fold",
                    "arguments": {"path": "missing.py"},
                },
            }
            yield {"event": "done", "data": {}}
            yield {"event": "token", "data": {"content": "The tool failed, so I stopped there."}}
            yield {"event": "done", "data": {}}

        with patch("anteroom.services.ai_service.AIService.stream_chat", side_effect=failing_stream):
            page.fill("#message-input", "Run a tool that will fail.")
            page.click("#btn-send")
            page.wait_for_function(
                """() => {
                    const content = document.querySelector('.message.assistant:last-of-type .message-content');
                    return content && Array.from(content.children).some((el) => (el.textContent || '').includes('The tool failed, so I stopped there.'));
                }""",
                timeout=5000,
            )

        order = page.evaluate(
            """() => {
                const content = document.querySelector('.message.assistant:last-of-type .message-content');
                return Array.from(content.children).map((el) => ({
                    className: el.className,
                    text: (el.textContent || '').trim(),
                }));
            }"""
        )

        assert any("tool-batch" in child["className"] and "error" in child["text"].lower() for child in order)
        assert order[-1]["text"].endswith("The tool failed, so I stopped there.")

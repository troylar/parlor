"""Shared-core Focus & Fold coverage through the CLI/programmatic agent loop path."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from anteroom.services.agent_loop import AgentEvent, run_agent_loop
from anteroom.tools import ToolRegistry, register_default_tools
from tests.e2e.conftest import mock_tool_call_stream


@pytest.mark.e2e
class TestFocusFoldAgentLoopCli:
    """Verify the real CLI tool stack receives grouped batch events from agent_loop."""

    @staticmethod
    def _mock_two_tool_batches() -> object:
        call_count = 0

        async def _stream(
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            cancel_event: Any = None,
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

    @pytest.mark.asyncio
    async def test_agent_loop_emits_fold_batch_events_with_real_read_tool(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]

        registry = ToolRegistry()
        register_default_tools(registry, working_dir=str(repo_root))

        ai_service = MagicMock()
        ai_service.stream_chat = mock_tool_call_stream(
            tool_name="read_file",
            arguments={"path": "src/anteroom/cli/renderer.py", "limit": 1},
            follow_up_text="Summary after the file read.",
        )

        messages: list[dict[str, Any]] = [{"role": "user", "content": "Read renderer.py and summarize it."}]
        events: list[AgentEvent] = []

        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=messages,
            tool_executor=registry.call_tool,
            tools_openai=registry.get_openai_tools(),
            max_iterations=4,
        ):
            events.append(event)

        kinds = [event.kind for event in events]
        assert "tool_batch_start" in kinds, kinds
        assert "tool_call_start" in kinds, kinds
        assert "tool_call_end" in kinds, kinds
        assert "tool_batch_end" in kinds, kinds
        assert "assistant_message" in kinds, kinds
        assert kinds[-1] == "done"

        batch_start_idx = kinds.index("tool_batch_start")
        tool_start_idx = kinds.index("tool_call_start")
        tool_end_idx = kinds.index("tool_call_end")
        batch_end_idx = kinds.index("tool_batch_end")
        final_assistant_idx = max(i for i, kind in enumerate(kinds) if kind == "assistant_message")

        assert tool_start_idx < batch_start_idx < tool_end_idx < batch_end_idx < final_assistant_idx

        batch_end = events[batch_end_idx]
        assert batch_end.data["call_count"] == 1
        assert isinstance(batch_end.data["elapsed_seconds"], float)

        tool_end = next(event for event in events if event.kind == "tool_call_end")
        assert tool_end.data["status"] == "success"
        assert "Rich-based terminal output" in tool_end.data["output"].get("content", "")

        final_assistant = events[final_assistant_idx]
        assert "Summary after the file read." in final_assistant.data["content"]

    @pytest.mark.asyncio
    async def test_agent_loop_closes_batch_before_followup_after_tool_error(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]

        registry = ToolRegistry()
        register_default_tools(registry, working_dir=str(repo_root))

        ai_service = MagicMock()
        ai_service.stream_chat = mock_tool_call_stream(
            tool_name="missing_tool_for_focus_fold",
            arguments={"path": "missing.py"},
            follow_up_text="The read failed, so I stopped there.",
        )

        messages: list[dict[str, Any]] = [{"role": "user", "content": "Try a tool that will fail."}]
        events: list[AgentEvent] = []

        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=messages,
            tool_executor=registry.call_tool,
            tools_openai=registry.get_openai_tools(),
            max_iterations=4,
        ):
            events.append(event)

        kinds = [event.kind for event in events]
        assert "tool_batch_start" in kinds, kinds
        assert "tool_call_end" in kinds, kinds
        assert "tool_batch_end" in kinds, kinds
        assert kinds[-1] == "done"

        tool_end_idx = kinds.index("tool_call_end")
        batch_end_idx = kinds.index("tool_batch_end")
        final_assistant_idx = max(i for i, kind in enumerate(kinds) if kind == "assistant_message")

        assert tool_end_idx < batch_end_idx < final_assistant_idx

        tool_end = next(event for event in events if event.kind == "tool_call_end")
        assert tool_end.data["status"] == "error"
        assert "Unknown built-in tool" in tool_end.data["output"].get("error", "")

        final_assistant = events[final_assistant_idx]
        assert "The read failed, so I stopped there." in final_assistant.data["content"]

    @pytest.mark.asyncio
    async def test_agent_loop_emits_two_distinct_tool_batches(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]

        registry = ToolRegistry()
        register_default_tools(registry, working_dir=str(repo_root))

        ai_service = MagicMock()
        ai_service.stream_chat = self._mock_two_tool_batches()

        messages: list[dict[str, Any]] = [{"role": "user", "content": "Run two tools before answering."}]
        events: list[AgentEvent] = []

        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=messages,
            tool_executor=registry.call_tool,
            tools_openai=registry.get_openai_tools(),
            max_iterations=5,
        ):
            events.append(event)

        batch_starts = [event for event in events if event.kind == "tool_batch_start"]
        batch_ends = [event for event in events if event.kind == "tool_batch_end"]
        tool_ends = [event for event in events if event.kind == "tool_call_end"]
        assistant_messages = [event for event in events if event.kind == "assistant_message"]

        assert len(batch_starts) == 2
        assert len(batch_ends) == 2
        assert len(tool_ends) == 2
        assert "Final answer after two batches." in assistant_messages[-1].data["content"]

        kinds = [event.kind for event in events]
        first_batch_end_idx = kinds.index("tool_batch_end")
        second_batch_start_idx = kinds.index("tool_batch_start", first_batch_end_idx + 1)
        second_batch_end_idx = kinds.index("tool_batch_end", second_batch_start_idx + 1)
        final_assistant_idx = max(i for i, kind in enumerate(kinds) if kind == "assistant_message")

        assert first_batch_end_idx < second_batch_start_idx < second_batch_end_idx < final_assistant_idx

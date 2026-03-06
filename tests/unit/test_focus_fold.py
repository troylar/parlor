"""Tests for Focus & Fold: turn-level tool batch grouping in CLI renderer."""

from __future__ import annotations

import os
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import anteroom.cli.renderer as renderer
from anteroom.cli.renderer import FoldGroup, Verbosity


class TestFoldGroup:
    """FoldGroup stores batch metadata and compact summaries."""

    def test_stores_call_count(self) -> None:
        g = FoldGroup(call_count=1, elapsed_seconds=0.0, summaries=[], tool_types=[])
        assert g.call_count == 1

    def test_stores_elapsed(self) -> None:
        g = FoldGroup(call_count=3, elapsed_seconds=2.5, summaries=[], tool_types=[])
        assert g.elapsed_seconds == 2.5

    def test_stores_summaries(self) -> None:
        g = FoldGroup(
            call_count=1,
            elapsed_seconds=0.0,
            summaries=["\u2713 Reading test.py"],
            tool_types=["read"],
        )
        assert len(g.summaries) == 1

    def test_stores_tool_types(self) -> None:
        g = FoldGroup(
            call_count=2,
            elapsed_seconds=0.0,
            summaries=[],
            tool_types=["read", "glob"],
        )
        assert g.tool_types == ["read", "glob"]


class TestBuildFoldNarrative:
    """Test _build_fold_narrative narrative output."""

    def test_single_read(self) -> None:
        result = renderer._build_fold_narrative(["\u2713 Reading config.py"], 0.5)
        assert result == "Read config.py 0.5s"

    def test_multiple_reads(self) -> None:
        summaries = ["\u2713 Reading a.py", "\u2713 Reading b.py", "\u2713 Reading c.py"]
        result = renderer._build_fold_narrative(summaries, 1.5)
        assert "Read" in result
        assert "a.py" in result
        assert "b.py" in result
        assert "c.py" in result
        assert "1.5s" in result

    def test_many_reads_truncates(self) -> None:
        summaries = [f"\u2713 Reading file{i}.py" for i in range(6)]
        result = renderer._build_fold_narrative(summaries, 0.5)
        assert "and 2 more" in result

    def test_mixed_types(self) -> None:
        summaries = ["\u2713 Reading a.py", "\u2713 Finding tests/*"]
        result = renderer._build_fold_narrative(summaries, 0.5)
        assert "read" in result or "Read" in result
        assert "0.5s" in result

    def test_glob_files(self) -> None:
        summaries = [
            "\u2713 Finding tests/*",
            "\u2713 Finding docs/*",
            "\u2713 Finding src/*",
        ]
        result = renderer._build_fold_narrative(summaries, 0.0)
        assert "Found" in result
        assert "tests/*" in result

    def test_no_elapsed_when_fast(self) -> None:
        result = renderer._build_fold_narrative(["\u2713 Reading a.py"], 0.05)
        assert "0.0s" not in result
        assert "0.1s" not in result

    def test_bash_commands(self) -> None:
        result = renderer._build_fold_narrative(["\u2713 bash git status"], 0.3)
        assert "Ran" in result
        assert "git status" in result

    def test_empty_summaries(self) -> None:
        result = renderer._build_fold_narrative([], 0.5)
        assert "done" in result


class TestFoldTypeLabel:
    """Test _fold_type_label mapping."""

    def test_known_tools(self) -> None:
        assert renderer._fold_type_label("read_file") == "read"
        assert renderer._fold_type_label("glob_files") == "glob"
        assert renderer._fold_type_label("write_file") == "write"
        assert renderer._fold_type_label("bash") == "bash"

    def test_unknown_tool(self) -> None:
        assert renderer._fold_type_label("custom_tool") == "custom tool"

    def test_mcp_tool(self) -> None:
        assert renderer._fold_type_label("search_docs") == "search docs"


class TestToolBatchRendering:
    """Test render_tool_batch_start / render_tool_batch_end."""

    def setup_method(self) -> None:
        renderer._fold_groups.clear()
        renderer._fold_batch_summaries.clear()
        renderer._fold_batch_types.clear()
        renderer._fold_batch_active = False
        renderer._fold_batch_total = 0
        renderer._fold_batch_done = 0
        renderer._fold_batch_current = ""
        renderer._fold_last_expanded = False
        renderer._dedup_key = ""
        renderer._dedup_count = 0
        renderer._dedup_first_summary = ""
        renderer._dedup_summary = ""
        renderer._tool_batch_active = False
        renderer._verbosity = Verbosity.COMPACT

    def test_batch_start_sets_active(self) -> None:
        renderer.render_tool_batch_start(3)
        assert renderer._fold_batch_active is True
        assert renderer._fold_batch_total == 3
        assert renderer._fold_batch_done == 0

    def test_batch_end_creates_fold_group(self) -> None:
        with patch("anteroom.cli.renderer.console"):
            renderer.render_tool_batch_start(2)
            renderer.render_tool_batch_end(2, 1.5)

        assert len(renderer._fold_groups) == 1
        assert renderer._fold_groups[0].call_count == 2
        assert renderer._fold_groups[0].elapsed_seconds == 1.5
        assert renderer._fold_batch_active is False

    def test_batch_end_prints_narrative_summary(self) -> None:
        with patch("anteroom.cli.renderer.console") as mock_console:
            renderer.render_tool_batch_start(3)
            # Simulate 3 reads being recorded
            renderer._fold_batch_summaries.extend(
                [
                    "\u2713 Reading a.py",
                    "\u2713 Reading b.py",
                    "\u2713 Reading c.py",
                ]
            )
            renderer._fold_batch_types.extend(["read", "read", "read"])
            renderer.render_tool_batch_end(3, 2.0)

        printed = str(mock_console.print.call_args_list)
        assert "Read" in printed
        assert "2.0s" in printed

    def test_batch_end_noop_without_start(self) -> None:
        with patch("anteroom.cli.renderer.console") as mock_console:
            renderer.render_tool_batch_end(1, 0.5)
        mock_console.print.assert_not_called()
        assert len(renderer._fold_groups) == 0

    def test_multiple_batches(self) -> None:
        with patch("anteroom.cli.renderer.console"):
            renderer.render_tool_batch_start(1)
            renderer.render_tool_batch_end(1, 0.1)
            renderer.render_tool_batch_start(2)
            renderer.render_tool_batch_end(2, 3.0)

        assert len(renderer._fold_groups) == 2


class TestFoldPrint:
    """Test _fold_print suppresses output during active batch."""

    def setup_method(self) -> None:
        renderer._fold_batch_active = False

    def test_fold_print_no_suppress_when_inactive(self) -> None:
        with patch("anteroom.cli.renderer.console") as mock_console:
            renderer._fold_print("hello")
        mock_console.print.assert_called_once_with("hello")

    def test_fold_print_suppresses_when_active(self) -> None:
        renderer._fold_batch_active = True
        with patch("anteroom.cli.renderer.console") as mock_console:
            renderer._fold_print("suppressed line")
        mock_console.print.assert_not_called()


class TestRecordFoldSummary:
    """Test _record_fold_summary collects summaries and updates counter."""

    def setup_method(self) -> None:
        renderer._fold_batch_active = True
        renderer._fold_batch_total = 3
        renderer._fold_batch_done = 0
        renderer._fold_batch_summaries.clear()
        renderer._fold_batch_types.clear()
        renderer._fold_batch_current = ""

    def test_records_success_summary(self) -> None:
        renderer._record_fold_summary("read_file", "Reading test.py", "success", 0.5)
        assert len(renderer._fold_batch_summaries) == 1
        assert "\u2713" in renderer._fold_batch_summaries[0]
        assert "Reading test.py" in renderer._fold_batch_summaries[0]

    def test_records_failure_summary(self) -> None:
        renderer._record_fold_summary("write_file", "Writing bad.py", "error", 0.1)
        assert "\u2717" in renderer._fold_batch_summaries[0]

    def test_increments_done_counter(self) -> None:
        renderer._record_fold_summary("read_file", "Reading a.py", "success", 0.0)
        assert renderer._fold_batch_done == 1
        renderer._record_fold_summary("read_file", "Reading b.py", "success", 0.0)
        assert renderer._fold_batch_done == 2

    def test_records_tool_type(self) -> None:
        renderer._record_fold_summary("read_file", "Reading test.py", "success", 0.0)
        assert renderer._fold_batch_types == ["read"]

    def test_updates_current_tool(self) -> None:
        renderer._record_fold_summary("glob_files", "Listing src/", "success", 0.0)
        assert renderer._fold_batch_current == "Listing src/"

    def test_includes_elapsed_when_significant(self) -> None:
        renderer._record_fold_summary("read_file", "Reading test.py", "success", 1.5)
        assert "1.5s" in renderer._fold_batch_summaries[0]

    def test_omits_elapsed_when_fast(self) -> None:
        renderer._record_fold_summary("read_file", "Reading test.py", "success", 0.05)
        assert "0.0s" not in renderer._fold_batch_summaries[0]
        assert "0.1s" not in renderer._fold_batch_summaries[0]


class TestToggleLastFold:
    """Test toggle_last_fold one-shot expand."""

    def setup_method(self) -> None:
        renderer._fold_groups.clear()
        renderer._fold_last_expanded = False

    def test_toggle_noop_when_no_groups(self) -> None:
        with patch("anteroom.cli.renderer.console") as mock_console:
            renderer.toggle_last_fold()
        mock_console.print.assert_not_called()

    def test_toggle_expands_group(self) -> None:
        summaries = ["\u2713 Reading a.py", "\u2713 Reading b.py"]
        group = FoldGroup(call_count=2, elapsed_seconds=1.0, summaries=summaries, tool_types=["read", "read"])
        renderer._fold_groups.append(group)

        with patch("anteroom.cli.renderer.console") as mock_console:
            renderer.toggle_last_fold()

        assert renderer._fold_last_expanded is True
        # 1 header line + 2 detail lines = 3
        assert mock_console.print.call_count == 3

    def test_second_toggle_is_noop(self) -> None:
        summaries = ["\u2713 Reading a.py"]
        group = FoldGroup(call_count=1, elapsed_seconds=0.0, summaries=summaries, tool_types=["read"])
        renderer._fold_groups.append(group)

        with patch("anteroom.cli.renderer.console") as mock_console:
            renderer.toggle_last_fold()  # first: prints
            first_count = mock_console.print.call_count
            renderer.toggle_last_fold()  # second: no-op

        assert mock_console.print.call_count == first_count

    def test_new_batch_resets_expand_flag(self) -> None:
        summaries = ["\u2713 Reading a.py"]
        group = FoldGroup(call_count=1, elapsed_seconds=0.0, summaries=summaries, tool_types=["read"])
        renderer._fold_groups.append(group)
        renderer._fold_last_expanded = True

        renderer.render_tool_batch_start(2)
        assert renderer._fold_last_expanded is False


class TestFlushBufferedTextDuringBatch:
    """Verify flush_buffered_text is suppressed during fold batch."""

    def setup_method(self) -> None:
        renderer._fold_batch_active = False
        renderer._streaming_buffer = []
        renderer._tool_batch_active = False

    def test_flush_suppressed_during_fold_batch(self) -> None:
        renderer._fold_batch_active = True
        renderer._streaming_buffer = ["raw json tool data"]
        with patch("anteroom.cli.renderer._stdout_console") as mock_stdout:
            renderer.flush_buffered_text()
        mock_stdout.print.assert_not_called()
        # Buffer preserved so render_response_end() can flush it
        assert renderer._streaming_buffer == ["raw json tool data"]

    def test_flush_works_normally_outside_batch(self) -> None:
        renderer._streaming_buffer = ["hello world"]
        with patch("anteroom.cli.renderer._stdout_console") as mock_stdout:
            renderer.flush_buffered_text()
        mock_stdout.print.assert_called_once()


class TestBetweenBatchesSuppression:
    """Text and thinking are suppressed between consecutive fold batches."""

    def setup_method(self) -> None:
        renderer._fold_batch_active = False
        renderer._fold_between_batches = False
        renderer._fold_suppress_thinking = False
        renderer._fold_batch_summaries.clear()
        renderer._fold_batch_types.clear()
        renderer._fold_groups.clear()
        renderer._streaming_buffer = []
        renderer._tool_batch_active = False

    def test_between_batches_flag_set_on_batch_end(self) -> None:
        with patch("anteroom.cli.renderer.console"):
            renderer.render_tool_batch_start(1)
            renderer.render_tool_batch_end(1, 0.1)
        assert renderer._fold_between_batches is True

    def test_between_batches_cleared_on_next_batch_start(self) -> None:
        renderer._fold_between_batches = True
        renderer.render_tool_batch_start(2)
        assert renderer._fold_between_batches is False

    def test_flush_suppressed_between_batches(self) -> None:
        renderer._fold_between_batches = True
        renderer._streaming_buffer = ["raw tool json data"]
        with patch("anteroom.cli.renderer._stdout_console") as mock_stdout:
            renderer.flush_buffered_text()
        mock_stdout.print.assert_not_called()
        # Buffer preserved so render_response_end() can flush it
        assert renderer._streaming_buffer == ["raw tool json data"]

    def test_between_batches_cleared_on_response_end(self) -> None:
        renderer._fold_between_batches = True
        renderer._streaming_buffer = ["final text"]
        with patch("anteroom.cli.renderer._stdout_console"):
            renderer.render_response_end()
        assert renderer._fold_between_batches is False


class TestToolCallEndDuringBatch:
    """Verify render_tool_call_end records summary during active batch."""

    def setup_method(self) -> None:
        renderer._fold_batch_active = False
        renderer._fold_batch_summaries.clear()
        renderer._fold_batch_types.clear()
        renderer._fold_batch_total = 0
        renderer._fold_batch_done = 0
        renderer._fold_batch_current = ""
        renderer._fold_groups.clear()
        renderer._dedup_key = ""
        renderer._dedup_count = 0
        renderer._dedup_first_summary = ""
        renderer._dedup_summary = ""
        renderer._tool_batch_active = False
        renderer._tool_start = 0
        renderer._current_turn_tools.clear()
        renderer._verbosity = Verbosity.COMPACT

    @patch("anteroom.cli.renderer.stop_tool_ticker_sync")
    @patch("anteroom.cli.renderer.console")
    def test_tool_call_end_recorded_in_batch(self, mock_console: MagicMock, mock_stop: MagicMock) -> None:
        renderer._fold_batch_active = True
        renderer._fold_batch_total = 1
        renderer._tool_start = time.monotonic()
        renderer._current_turn_tools.append(
            {
                "tool_name": "read_file",
                "arguments": {"path": "test.py"},
                "summary": "Reading test.py",
                "status": "running",
                "output": None,
                "start_time": time.monotonic(),
            }
        )

        renderer.render_tool_call_end("read_file", "success", {"content": "hello"})

        assert len(renderer._fold_batch_summaries) == 1
        assert renderer._fold_batch_done == 1
        assert "Reading test.py" in renderer._fold_batch_summaries[0]
        assert renderer._fold_batch_types == ["read"]

    @patch("anteroom.cli.renderer.stop_tool_ticker_sync")
    @patch("anteroom.cli.renderer.console")
    def test_each_tool_gets_own_summary_in_batch(self, mock_console: MagicMock, mock_stop: MagicMock) -> None:
        """During batch, each tool gets its own summary line (no dedup)."""
        renderer._fold_batch_active = True
        renderer._fold_batch_total = 3
        renderer._tool_dedup_enabled = True

        now = time.monotonic()
        for i in range(3):
            renderer._current_turn_tools.append(
                {
                    "tool_name": "read_file",
                    "arguments": {"path": f"file{i}.py"},
                    "summary": f"Reading file{i}.py",
                    "status": "running",
                    "output": None,
                    "start_time": now,
                }
            )
            renderer.render_tool_call_end("read_file", "success", {"content": "ok"})

        assert len(renderer._fold_batch_summaries) == 3
        assert renderer._fold_batch_done == 3
        assert renderer._fold_batch_types == ["read", "read", "read"]


class TestToolCallStartDuringBatch:
    """Verify render_tool_call_start updates ticker and returns early during batch."""

    def setup_method(self) -> None:
        renderer._fold_batch_active = True
        renderer._fold_batch_total = 3
        renderer._fold_batch_done = 0
        renderer._fold_batch_current = ""
        renderer._tool_batch_active = False
        renderer._tool_start = 0
        renderer._current_turn_tools.clear()
        renderer._streaming_buffer = []
        renderer._verbosity = Verbosity.COMPACT

    @patch("anteroom.cli.renderer.start_tool_ticker")
    @patch("anteroom.cli.renderer.console")
    def test_does_not_start_tool_ticker(self, mock_console: MagicMock, mock_ticker: MagicMock) -> None:
        renderer.render_tool_call_start("read_file", {"path": "test.py"})
        mock_ticker.assert_not_called()

    @patch("anteroom.cli.renderer.console")
    def test_updates_current_summary(self, mock_console: MagicMock) -> None:
        renderer.render_tool_call_start("read_file", {"path": "test.py"})
        assert "test.py" in renderer._fold_batch_current


class TestFoldSuppressThinking:
    """Verify start_thinking is suppressed after a fold batch."""

    def setup_method(self) -> None:
        renderer._fold_suppress_thinking = False
        renderer._fold_batch_active = False
        renderer._fold_batch_summaries.clear()
        renderer._fold_batch_types.clear()
        renderer._fold_groups.clear()
        renderer._thinking_start = 0

    def test_batch_end_sets_suppress_flag(self) -> None:
        with patch("anteroom.cli.renderer.console"):
            renderer.render_tool_batch_start(1)
            renderer.render_tool_batch_end(1, 0.1)
        assert renderer._fold_suppress_thinking is True

    @patch("anteroom.cli.renderer._write_thinking_line")
    @patch("anteroom.cli.renderer.console")
    def test_suppressed_thinking_skips_display(self, mock_console: MagicMock, mock_write: MagicMock) -> None:
        renderer._fold_suppress_thinking = True
        renderer._repl_mode = True
        renderer.start_thinking()
        renderer._repl_mode = False
        # Should NOT have written a thinking line
        mock_write.assert_not_called()
        # But timing state should be reset
        assert renderer._thinking_start > 0
        assert renderer._fold_suppress_thinking is False

    @patch("anteroom.cli.renderer._write_thinking_line")
    @patch("anteroom.cli.renderer.console")
    def test_between_batches_suppresses_thinking(self, mock_console: MagicMock, mock_write: MagicMock) -> None:
        renderer._fold_between_batches = True
        renderer._fold_suppress_thinking = False
        renderer._repl_mode = True
        renderer.start_thinking()
        renderer._repl_mode = False
        mock_write.assert_not_called()
        assert renderer._thinking_start > 0


class TestBatchStartStopsToolTicker:
    """render_tool_batch_start must stop any tool ticker from a preceding tool_call_start."""

    def setup_method(self) -> None:
        renderer._fold_batch_active = False
        renderer._fold_between_batches = False
        renderer._fold_batch_summaries.clear()
        renderer._fold_batch_types.clear()
        renderer._fold_groups.clear()

    @patch("anteroom.cli.renderer.stop_tool_ticker_sync")
    def test_batch_start_stops_tool_ticker(self, mock_stop: MagicMock) -> None:
        renderer.render_tool_batch_start(2)
        mock_stop.assert_called_once()


class TestStopThinkingIdempotent:
    """stop_thinking is a no-op when thinking isn't active (prevents stale timer)."""

    def setup_method(self) -> None:
        renderer._thinking_start = 0
        renderer._spinner = None
        renderer._thinking_ticker_task = None

    @pytest.mark.asyncio
    @patch("anteroom.cli.renderer._write_thinking_line")
    async def test_double_stop_is_noop(self, mock_write: MagicMock) -> None:
        elapsed = await renderer.stop_thinking()
        assert elapsed == 0.0
        mock_write.assert_not_called()

    def test_sync_double_stop_is_noop(self) -> None:
        elapsed = renderer.stop_thinking_sync()
        assert elapsed == 0.0


class TestAgentLoopBatchEvents:
    """Test that agent loop emits tool_batch_start and tool_batch_end events."""

    @pytest.mark.asyncio
    async def test_batch_events_emitted(self) -> None:
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop

        call_count = 0

        async def mock_stream_chat(messages: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield {
                    "event": "tool_call",
                    "data": {"id": "tc1", "function_name": "read_file", "arguments": {"path": "test.py"}},
                }
            else:
                yield {"event": "token", "data": {"content": "Done."}}

        ai_service = MagicMock()
        ai_service.stream_chat = mock_stream_chat

        async def tool_executor(name: str, args: dict[str, Any], **kw: Any) -> tuple[dict[str, Any], str]:
            return {"content": "file content"}, "success"

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "read test.py"}],
            tools_openai=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
            tool_executor=tool_executor,
            max_iterations=2,
        ):
            events.append(event)

        kinds = [e.kind for e in events]
        assert "tool_batch_start" in kinds
        assert "tool_batch_end" in kinds

        batch_start_idx = kinds.index("tool_batch_start")
        tool_end_idx = kinds.index("tool_call_end")
        batch_end_idx = kinds.index("tool_batch_end")
        assert batch_start_idx < tool_end_idx
        assert tool_end_idx < batch_end_idx

        batch_end_event = events[batch_end_idx]
        assert batch_end_event.data["call_count"] == 1
        assert "elapsed_seconds" in batch_end_event.data


class TestJoinTargets:
    """Direct tests for _join_targets helper."""

    def test_single_target(self) -> None:
        assert renderer._join_targets(["a.py"]) == "a.py"

    def test_two_targets(self) -> None:
        assert renderer._join_targets(["a.py", "b.py"]) == "a.py and b.py"

    def test_three_targets_oxford_comma(self) -> None:
        result = renderer._join_targets(["a.py", "b.py", "c.py"])
        assert result == "a.py, b.py, and c.py"

    def test_four_targets_at_max(self) -> None:
        result = renderer._join_targets(["a", "b", "c", "d"])
        assert result == "a, b, c, and d"

    def test_five_targets_truncates(self) -> None:
        result = renderer._join_targets(["a", "b", "c", "d", "e"])
        assert result == "a, b, c, d, and 1 more"

    def test_many_targets_truncates(self) -> None:
        result = renderer._join_targets([f"f{i}" for i in range(10)])
        assert "and 6 more" in result

    def test_custom_max_shown(self) -> None:
        result = renderer._join_targets(["a", "b", "c"], max_shown=2)
        assert result == "a, b, and 1 more"


class TestUpdateFoldTicker:
    """Test _update_fold_ticker ANSI output."""

    def setup_method(self) -> None:
        renderer._fold_batch_done = 2
        renderer._fold_batch_total = 5
        renderer._fold_batch_current = "Reading test.py"

    def test_noop_when_not_repl(self) -> None:
        renderer._repl_mode = False
        mock_stdout = MagicMock()
        renderer._stdout = mock_stdout
        renderer._stdout_is_tty = True
        renderer._update_fold_ticker()
        mock_stdout.write.assert_not_called()
        renderer._repl_mode = False

    def test_noop_when_no_stdout(self) -> None:
        renderer._repl_mode = True
        renderer._stdout = None
        renderer._stdout_is_tty = True
        renderer._update_fold_ticker()
        # No exception = pass

    def test_noop_when_not_tty(self) -> None:
        renderer._repl_mode = True
        mock_stdout = MagicMock()
        renderer._stdout = mock_stdout
        renderer._stdout_is_tty = False
        renderer._update_fold_ticker()
        mock_stdout.write.assert_not_called()

    def test_writes_progress_with_tool_name(self) -> None:
        renderer._repl_mode = True
        mock_stdout = MagicMock()
        renderer._stdout = mock_stdout
        renderer._stdout_is_tty = True
        renderer._update_fold_ticker()
        output = mock_stdout.write.call_args[0][0]
        assert "2/5" in output
        assert "Reading test.py" in output
        mock_stdout.flush.assert_called_once()
        renderer._repl_mode = False

    def test_writes_progress_without_tool_name(self) -> None:
        renderer._repl_mode = True
        renderer._fold_batch_current = ""
        mock_stdout = MagicMock()
        renderer._stdout = mock_stdout
        renderer._stdout_is_tty = True
        renderer._update_fold_ticker()
        output = mock_stdout.write.call_args[0][0]
        assert "2/5" in output
        assert "Reading" not in output
        renderer._repl_mode = False


class TestBuildFoldNarrativeExtended:
    """Additional verb coverage for _build_fold_narrative."""

    def test_write_file_verb(self) -> None:
        result = renderer._build_fold_narrative(["\u2713 Writing output.py"], 0.5)
        assert "Wrote" in result
        assert "output.py" in result

    def test_edit_file_verb(self) -> None:
        result = renderer._build_fold_narrative(["\u2713 Editing config.py"], 0.3)
        assert "Edited" in result
        assert "config.py" in result

    def test_search_verb(self) -> None:
        result = renderer._build_fold_narrative(["\u2713 Searching for 'error'"], 0.2)
        assert "Searched" in result

    def test_sub_agent_verb(self) -> None:
        result = renderer._build_fold_narrative(["\u2713 Sub-agent: analyze code"], 0.5)
        assert "Ran sub-agent:" in result

    def test_listing_verb(self) -> None:
        result = renderer._build_fold_narrative(["\u2713 Listing tests/", "\u2713 Listing docs/"], 0.1)
        assert "Listed" in result
        assert "tests/" in result
        assert "docs/" in result

    def test_mixed_read_and_write(self) -> None:
        summaries = ["\u2713 Reading a.py", "\u2713 Writing b.py"]
        result = renderer._build_fold_narrative(summaries, 0.5)
        # Mixed types produce grouped summary
        assert "read" in result.lower() or "wrote" in result.lower()

    def test_mixed_many_items_per_group(self) -> None:
        summaries = [f"\u2713 Reading f{i}.py" for i in range(5)]
        summaries += [f"\u2713 Writing o{i}.py" for i in range(3)]
        result = renderer._build_fold_narrative(summaries, 1.0)
        assert "5 items" in result or "read" in result.lower()

    def test_failure_icon_stripped(self) -> None:
        result = renderer._build_fold_narrative(["\u2717 Reading bad.py"], 0.1)
        assert "Read" in result
        assert "bad.py" in result

    def test_elapsed_stripped_from_summaries(self) -> None:
        result = renderer._build_fold_narrative(["\u2713 Reading slow.py 1.5s"], 2.0)
        assert "Read" in result
        assert "slow.py" in result
        # Per-tool elapsed should be stripped; only total elapsed shown
        assert "2.0s" in result


class TestFoldTypeLabelComplete:
    """All known tool mappings in _TOOL_TYPE_LABELS."""

    def test_all_known_labels(self) -> None:
        expected = {
            "read_file": "read",
            "glob_files": "glob",
            "grep": "grep",
            "write_file": "write",
            "edit_file": "edit",
            "bash": "bash",
            "create_canvas": "canvas",
            "update_canvas": "canvas",
            "patch_canvas": "canvas",
            "run_agent": "agent",
            "ask_user": "ask",
        }
        for tool, label in expected.items():
            assert renderer._fold_type_label(tool) == label, f"{tool} should map to {label}"

    def test_underscores_to_spaces(self) -> None:
        assert renderer._fold_type_label("my_custom_tool") == "my custom tool"


class TestClearTurnHistoryResetsFoldState:
    """clear_turn_history must reset all fold state to prevent leaks across turns."""

    def setup_method(self) -> None:
        # Set up dirty fold state as if a batch was interrupted
        renderer._fold_batch_active = True
        renderer._fold_between_batches = True
        renderer._fold_suppress_thinking = True
        renderer._fold_last_expanded = True
        renderer._fold_groups.append(FoldGroup(call_count=1, elapsed_seconds=0.1, summaries=["x"], tool_types=["read"]))

    def test_clears_fold_batch_active(self) -> None:
        renderer.clear_turn_history()
        assert renderer._fold_batch_active is False

    def test_clears_between_batches(self) -> None:
        renderer.clear_turn_history()
        assert renderer._fold_between_batches is False

    def test_clears_suppress_thinking(self) -> None:
        renderer.clear_turn_history()
        assert renderer._fold_suppress_thinking is False

    def test_clears_last_expanded(self) -> None:
        renderer.clear_turn_history()
        assert renderer._fold_last_expanded is False

    def test_clears_fold_groups(self) -> None:
        renderer.clear_turn_history()
        assert len(renderer._fold_groups) == 0

    def test_clears_streaming_buffer(self) -> None:
        renderer._streaming_buffer = ["leftover"]
        renderer.clear_turn_history()
        assert renderer._streaming_buffer == []


class TestCancellationClearsFoldState:
    """stop_thinking and stop_thinking_sync must clear fold flags on cancel."""

    def setup_method(self) -> None:
        renderer._fold_batch_active = True
        renderer._fold_between_batches = True
        renderer._fold_suppress_thinking = True
        renderer._thinking_start = time.monotonic()
        renderer._spinner = None
        renderer._thinking_ticker_task = None

    @pytest.mark.asyncio
    @patch("anteroom.cli.renderer._write_thinking_line")
    async def test_stop_thinking_clears_fold_state(self, mock_write: MagicMock) -> None:
        await renderer.stop_thinking()
        assert renderer._fold_batch_active is False
        assert renderer._fold_between_batches is False
        assert renderer._fold_suppress_thinking is False

    def test_stop_thinking_sync_clears_fold_state(self) -> None:
        renderer.stop_thinking_sync()
        assert renderer._fold_batch_active is False
        assert renderer._fold_between_batches is False
        assert renderer._fold_suppress_thinking is False

    @pytest.mark.asyncio
    @patch("anteroom.cli.renderer._write_thinking_line")
    async def test_stop_thinking_noop_still_works(self, mock_write: MagicMock) -> None:
        """The no-op guard doesn't prevent fold cleanup on subsequent real stops."""
        renderer._thinking_start = 0
        renderer._spinner = None
        renderer._thinking_ticker_task = None
        # This is a no-op — fold state should remain dirty
        elapsed = await renderer.stop_thinking()
        assert elapsed == 0.0
        # Fold state NOT cleared by no-op (no thinking was active)
        assert renderer._fold_batch_active is True


class TestSuppressedThinkingClearsThroughputWindow:
    """The suppressed thinking path should clear _throughput_window."""

    def setup_method(self) -> None:
        renderer._fold_suppress_thinking = True
        renderer._thinking_start = 0
        renderer._spinner = None
        renderer._thinking_ticker_task = None

    @patch("anteroom.cli.renderer._write_thinking_line")
    @patch("anteroom.cli.renderer.console")
    def test_throughput_window_cleared(self, mock_console: MagicMock, mock_write: MagicMock) -> None:
        renderer._throughput_window.append((time.monotonic(), 100))
        renderer._repl_mode = True
        renderer.start_thinking()
        renderer._repl_mode = False
        assert len(renderer._throughput_window) == 0


class TestToggleLastFoldExtended:
    """Additional edge cases for toggle_last_fold."""

    def setup_method(self) -> None:
        renderer._fold_groups.clear()
        renderer._fold_last_expanded = False

    def test_expands_only_last_group(self) -> None:
        """With multiple groups, only the last is expanded."""
        group1 = FoldGroup(1, 0.1, ["\u2713 Reading a.py"], ["read"])
        group2 = FoldGroup(1, 0.2, ["\u2713 Writing b.py"], ["write"])
        renderer._fold_groups.extend([group1, group2])

        with patch("anteroom.cli.renderer.console") as mock_console:
            renderer.toggle_last_fold()

        # 1 header + 1 detail line from group2, not group1
        assert mock_console.print.call_count == 2
        printed = str(mock_console.print.call_args_list[1])
        assert "Writing b.py" in printed


class TestCollapseInputToolbarRow:
    """Explicit tests for _collapse_long_input +1 toolbar row and unified fd."""

    def _make_lines(self, count: int) -> str:
        return "\n".join(f"line {i}" for i in range(count))

    def test_cursor_movement_includes_toolbar_row(self) -> None:
        """With 20 lines, cursor moves up 21 (20 lines + 1 toolbar)."""
        from anteroom.cli.repl import _collapse_long_input

        long_input = self._make_lines(20)
        with (
            patch("anteroom.cli.repl.renderer") as mock_renderer,
            patch("anteroom.cli.repl.sys") as mock_sys,
            patch("anteroom.cli.repl.shutil") as mock_shutil,
        ):
            mock_sys.stdout.isatty.return_value = True
            mock_shutil.get_terminal_size.return_value = os.terminal_size((120, 24))
            _collapse_long_input(long_input)

            output = mock_renderer._stdout.write.call_args[0][0]
            # 20 lines + 1 toolbar = 21 rows
            assert "\033[21A" in output

    def test_no_console_print_calls(self) -> None:
        """After #779 fix, all output goes through _stdout.write, not console.print."""
        from anteroom.cli.repl import _collapse_long_input

        long_input = self._make_lines(20)
        with (
            patch("anteroom.cli.repl.renderer") as mock_renderer,
            patch("anteroom.cli.repl.sys") as mock_sys,
            patch("anteroom.cli.repl.shutil") as mock_shutil,
        ):
            mock_sys.stdout.isatty.return_value = True
            mock_shutil.get_terminal_size.return_value = os.terminal_size((120, 24))
            _collapse_long_input(long_input)

            mock_renderer.console.print.assert_not_called()

    def test_single_atomic_write(self) -> None:
        """All output is a single write call (cursor + content)."""
        from anteroom.cli.repl import _collapse_long_input

        long_input = self._make_lines(15)
        with (
            patch("anteroom.cli.repl.renderer") as mock_renderer,
            patch("anteroom.cli.repl.sys") as mock_sys,
            patch("anteroom.cli.repl.shutil") as mock_shutil,
        ):
            mock_sys.stdout.isatty.return_value = True
            mock_shutil.get_terminal_size.return_value = os.terminal_size((120, 24))
            _collapse_long_input(long_input)

            assert mock_renderer._stdout.write.call_count == 1

    def test_wrapped_lines_increase_row_count(self) -> None:
        """A line wider than terminal width wraps, increasing cursor-up count."""
        from anteroom.cli.repl import _collapse_long_input

        # 11 lines: line 0 is 200 chars (wraps to ~2 rows at 120 cols), rest are short
        lines = ["x" * 200] + [f"line {i}" for i in range(1, 11)]
        long_input = "\n".join(lines)
        with (
            patch("anteroom.cli.repl.renderer") as mock_renderer,
            patch("anteroom.cli.repl.sys") as mock_sys,
            patch("anteroom.cli.repl.shutil") as mock_shutil,
        ):
            mock_sys.stdout.isatty.return_value = True
            mock_shutil.get_terminal_size.return_value = os.terminal_size((120, 24))
            _collapse_long_input(long_input)

            output = mock_renderer._stdout.write.call_args[0][0]
            # usable = 120 - 2 = 118. Line 0: ceil(200/118)=2 rows. 10 short lines = 10 rows.
            # Total = 2 + 10 + 1 (toolbar) = 13
            assert "\033[13A" in output

    def test_empty_lines_count_as_one_row(self) -> None:
        """Empty lines in paste each count as 1 terminal row."""
        from anteroom.cli.repl import _collapse_long_input

        lines = ["first"] + [""] * 10 + ["last"]
        long_input = "\n".join(lines)
        with (
            patch("anteroom.cli.repl.renderer") as mock_renderer,
            patch("anteroom.cli.repl.sys") as mock_sys,
            patch("anteroom.cli.repl.shutil") as mock_shutil,
        ):
            mock_sys.stdout.isatty.return_value = True
            mock_shutil.get_terminal_size.return_value = os.terminal_size((120, 24))
            _collapse_long_input(long_input)

            output = mock_renderer._stdout.write.call_args[0][0]
            # 12 lines + 1 toolbar = 13
            assert "\033[13A" in output


class TestBatchSummariesClearedAfterGroup:
    """render_tool_batch_end clears working lists after creating FoldGroup."""

    def setup_method(self) -> None:
        renderer._fold_batch_active = False
        renderer._fold_batch_summaries.clear()
        renderer._fold_batch_types.clear()
        renderer._fold_groups.clear()
        renderer._dedup_key = ""
        renderer._dedup_count = 0
        renderer._tool_batch_active = False

    def test_summaries_cleared_after_end(self) -> None:
        with patch("anteroom.cli.renderer.console"):
            renderer.render_tool_batch_start(2)
            renderer._fold_batch_summaries.extend(["\u2713 Reading a.py", "\u2713 Reading b.py"])
            renderer._fold_batch_types.extend(["read", "read"])
            renderer.render_tool_batch_end(2, 0.5)

        assert len(renderer._fold_batch_summaries) == 0
        assert len(renderer._fold_batch_types) == 0
        # But the group preserved the data
        assert len(renderer._fold_groups[0].summaries) == 2


class TestIdenticalToolCallDetector:
    """Agent loop stops when the same tool+args is called consecutively."""

    @pytest.mark.asyncio
    async def test_stops_on_repeated_identical_calls(self) -> None:
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop

        call_count = 0

        async def mock_stream_chat(messages: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            # Always emit the same tool call
            yield {
                "event": "tool_call",
                "data": {"id": f"tc{call_count}", "function_name": "bash", "arguments": {"command": "echo hi"}},
            }

        ai_service = MagicMock()
        ai_service.stream_chat = mock_stream_chat

        async def tool_executor(name: str, args: dict[str, Any], **kw: Any) -> tuple[dict[str, Any], str]:
            return {"content": "hi"}, "success"

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "echo hi"}],
            tools_openai=[{"type": "function", "function": {"name": "bash", "parameters": {}}}],
            tool_executor=tool_executor,
            max_iterations=10,
            max_identical_tool_repeats=3,
        ):
            events.append(event)

        error_events = [e for e in events if e.kind == "error"]
        assert len(error_events) == 1
        assert "Repetitive tool calls" in error_events[0].data["message"]
        assert call_count == 3  # stopped after 3 identical calls

    @pytest.mark.asyncio
    async def test_resets_on_different_args(self) -> None:
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop

        call_count = 0

        async def mock_stream_chat(messages: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            # Different arguments each time
            yield {
                "event": "tool_call",
                "data": {
                    "id": f"tc{call_count}",
                    "function_name": "read_file",
                    "arguments": {"path": f"file{call_count}.py"},
                },
            }
            if call_count >= 5:
                yield {"event": "token", "data": {"content": "Done."}}

        ai_service = MagicMock()
        ai_service.stream_chat = mock_stream_chat

        async def tool_executor(name: str, args: dict[str, Any], **kw: Any) -> tuple[dict[str, Any], str]:
            return {"content": "content"}, "success"

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "read files"}],
            tools_openai=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
            tool_executor=tool_executor,
            max_iterations=6,
            max_identical_tool_repeats=3,
        ):
            events.append(event)

        error_events = [e for e in events if e.kind == "error"]
        # Only max-iterations error, no repetition error
        repetition_errors = [e for e in error_events if "Repetitive tool calls" in e.data.get("message", "")]
        assert len(repetition_errors) == 0

    @pytest.mark.asyncio
    async def test_disabled_when_zero(self) -> None:
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop

        call_count = 0

        async def mock_stream_chat(messages: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                yield {
                    "event": "tool_call",
                    "data": {"id": f"tc{call_count}", "function_name": "bash", "arguments": {"command": "echo hi"}},
                }
            else:
                yield {"event": "token", "data": {"content": "Done."}}

        ai_service = MagicMock()
        ai_service.stream_chat = mock_stream_chat

        async def tool_executor(name: str, args: dict[str, Any], **kw: Any) -> tuple[dict[str, Any], str]:
            return {"content": "hi"}, "success"

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "echo hi"}],
            tools_openai=[{"type": "function", "function": {"name": "bash", "parameters": {}}}],
            tool_executor=tool_executor,
            max_iterations=6,
            max_identical_tool_repeats=0,  # disabled
        ):
            events.append(event)

        error_events = [e for e in events if e.kind == "error"]
        assert len(error_events) == 0  # no error — detection disabled

    @pytest.mark.asyncio
    async def test_same_args_different_key_order(self) -> None:
        """Args with different key order should be treated as identical (sort_keys)."""
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop

        call_count = 0

        async def mock_stream_chat(messages: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            # Alternate key order, but same values
            if call_count % 2 == 1:
                args = {"path": "/tmp/a.py", "content": "hello"}
            else:
                args = {"content": "hello", "path": "/tmp/a.py"}
            yield {
                "event": "tool_call",
                "data": {"id": f"tc{call_count}", "function_name": "write_file", "arguments": args},
            }

        ai_service = MagicMock()
        ai_service.stream_chat = mock_stream_chat

        async def tool_executor(name: str, args: dict[str, Any], **kw: Any) -> tuple[dict[str, Any], str]:
            return {"content": "ok"}, "success"

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "write file"}],
            tools_openai=[{"type": "function", "function": {"name": "write_file", "parameters": {}}}],
            tool_executor=tool_executor,
            max_iterations=5,
            max_identical_tool_repeats=3,
        ):
            events.append(event)

        error_events = [e for e in events if e.kind == "error"]
        repetition_errors = [e for e in error_events if "Repetitive tool calls" in e.data.get("message", "")]
        assert len(repetition_errors) == 1
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_multi_tool_batch_order_independent(self) -> None:
        """A batch with [read A, grep B] is identical to [grep B, read A]."""
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop

        call_count = 0

        async def mock_stream_chat(messages: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 1:
                # Order: read then grep
                yield {
                    "event": "tool_call",
                    "data": {"id": f"tc{call_count}a", "function_name": "read_file", "arguments": {"path": "a.py"}},
                }
                yield {
                    "event": "tool_call",
                    "data": {"id": f"tc{call_count}b", "function_name": "grep", "arguments": {"pattern": "foo"}},
                }
            else:
                # Order: grep then read (same tools+args, different order)
                yield {
                    "event": "tool_call",
                    "data": {"id": f"tc{call_count}a", "function_name": "grep", "arguments": {"pattern": "foo"}},
                }
                yield {
                    "event": "tool_call",
                    "data": {"id": f"tc{call_count}b", "function_name": "read_file", "arguments": {"path": "a.py"}},
                }

        ai_service = MagicMock()
        ai_service.stream_chat = mock_stream_chat

        async def tool_executor(name: str, args: dict[str, Any], **kw: Any) -> tuple[dict[str, Any], str]:
            return {"content": "result"}, "success"

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "search"}],
            tools_openai=[
                {"type": "function", "function": {"name": "read_file", "parameters": {}}},
                {"type": "function", "function": {"name": "grep", "parameters": {}}},
            ],
            tool_executor=tool_executor,
            max_iterations=5,
            max_identical_tool_repeats=3,
        ):
            events.append(event)

        error_events = [e for e in events if e.kind == "error"]
        repetition_errors = [e for e in error_events if "Repetitive tool calls" in e.data.get("message", "")]
        assert len(repetition_errors) == 1
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_exact_threshold_boundary(self) -> None:
        """At max_identical_tool_repeats=N, error fires on Nth call, not N-1 or N+1."""
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop

        call_count = 0
        threshold = 4

        async def mock_stream_chat(messages: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            yield {
                "event": "tool_call",
                "data": {"id": f"tc{call_count}", "function_name": "bash", "arguments": {"command": "ls"}},
            }

        ai_service = MagicMock()
        ai_service.stream_chat = mock_stream_chat

        async def tool_executor(name: str, args: dict[str, Any], **kw: Any) -> tuple[dict[str, Any], str]:
            return {"content": "file.py"}, "success"

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "list"}],
            tools_openai=[{"type": "function", "function": {"name": "bash", "parameters": {}}}],
            tool_executor=tool_executor,
            max_iterations=10,
            max_identical_tool_repeats=threshold,
        ):
            events.append(event)

        assert call_count == threshold
        error_events = [e for e in events if e.kind == "error"]
        assert len(error_events) == 1

    @pytest.mark.asyncio
    async def test_interleaved_different_call_resets_counter(self) -> None:
        """A different call between identical calls resets the repeat counter."""
        from anteroom.services.agent_loop import AgentEvent, run_agent_loop

        call_count = 0

        async def mock_stream_chat(messages: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            # Pattern: A, A, B, A, A, done
            if call_count in (1, 2, 4, 5):
                yield {
                    "event": "tool_call",
                    "data": {"id": f"tc{call_count}", "function_name": "bash", "arguments": {"command": "echo hi"}},
                }
            elif call_count == 3:
                yield {
                    "event": "tool_call",
                    "data": {"id": f"tc{call_count}", "function_name": "bash", "arguments": {"command": "echo bye"}},
                }
            else:
                yield {"event": "token", "data": {"content": "Done."}}

        ai_service = MagicMock()
        ai_service.stream_chat = mock_stream_chat

        async def tool_executor(name: str, args: dict[str, Any], **kw: Any) -> tuple[dict[str, Any], str]:
            return {"content": "output"}, "success"

        events: list[AgentEvent] = []
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=[{"role": "user", "content": "test"}],
            tools_openai=[{"type": "function", "function": {"name": "bash", "parameters": {}}}],
            tool_executor=tool_executor,
            max_iterations=8,
            max_identical_tool_repeats=3,
        ):
            events.append(event)

        # No repetition error — never 3 consecutive identical calls
        repetition_errors = [
            e for e in events if e.kind == "error" and "Repetitive tool calls" in e.data.get("message", "")
        ]
        assert len(repetition_errors) == 0

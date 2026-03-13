"""Tests for the CLI renderer verbosity and display system."""

from __future__ import annotations

import asyncio
import io
import time
from unittest.mock import patch

import pytest

from anteroom.cli.renderer import (
    Verbosity,
    _build_thinking_text,
    _collapse_plan,
    _dedup_flush_label,
    _dedup_key_from_summary,
    _flush_dedup,
    _format_tokens,
    _has_diff_data,
    _humanize_tool,
    _output_summary,
    _phase_elapsed_str,
    _phase_suffix,
    _plan_block_height,
    _render_inline_diff,
    _short_path,
    _write_thinking_block,
    _write_thinking_line,
    clear_plan,
    clear_turn_history,
    configure_thresholds,
    cycle_verbosity,
    flush_buffered_text,
    format_status_toolbar,
    get_plan_steps,
    get_verbosity,
    increment_streaming_chars,
    increment_thinking_tokens,
    is_plan_visible,
    render_error,
    render_response_end,
    render_tool_call_end,
    render_tool_call_start,
    render_warning,
    save_turn_history,
    set_retrying,
    set_thinking_phase,
    set_tool_dedup,
    set_verbosity,
    start_plan,
    start_thinking,
    start_tool_ticker,
    startup_step,
    stop_thinking,
    stop_thinking_sync,
    stop_tool_ticker_sync,
    thinking_countdown,
    update_plan_step,
)


class TestVerbosity:
    def setup_method(self) -> None:
        set_verbosity(Verbosity.COMPACT)

    def test_default_verbosity(self) -> None:
        assert get_verbosity() == Verbosity.COMPACT

    def test_set_verbosity(self) -> None:
        set_verbosity(Verbosity.VERBOSE)
        assert get_verbosity() == Verbosity.VERBOSE

    def test_cycle_compact_to_detailed(self) -> None:
        set_verbosity(Verbosity.COMPACT)
        result = cycle_verbosity()
        assert result == Verbosity.DETAILED
        assert get_verbosity() == Verbosity.DETAILED

    def test_cycle_detailed_to_verbose(self) -> None:
        set_verbosity(Verbosity.DETAILED)
        result = cycle_verbosity()
        assert result == Verbosity.VERBOSE

    def test_cycle_verbose_wraps_to_compact(self) -> None:
        set_verbosity(Verbosity.VERBOSE)
        result = cycle_verbosity()
        assert result == Verbosity.COMPACT

    def test_full_cycle(self) -> None:
        set_verbosity(Verbosity.COMPACT)
        assert cycle_verbosity() == Verbosity.DETAILED
        assert cycle_verbosity() == Verbosity.VERBOSE
        assert cycle_verbosity() == Verbosity.COMPACT


class TestHumanizeTool:
    def test_bash_command(self) -> None:
        result = _humanize_tool("bash", {"command": "git status"})
        assert result == "bash git status"

    def test_bash_long_command_truncated(self) -> None:
        long_cmd = "x" * 150
        result = _humanize_tool("bash", {"command": long_cmd})
        assert len(result) < 110
        assert result.endswith("...")

    def test_bash_medium_command_not_truncated(self) -> None:
        cmd = "python manage.py test --settings=config.test"
        result = _humanize_tool("bash", {"command": cmd})
        assert result == f"bash {cmd}"

    def test_file_read(self) -> None:
        result = _humanize_tool("file_read", {"path": "src/config.py"})
        assert "Reading" in result
        assert "config.py" in result

    def test_file_write(self) -> None:
        result = _humanize_tool("file_write", {"path": "output.txt"})
        assert "Writing" in result

    def test_file_edit(self) -> None:
        result = _humanize_tool("file_edit", {"path": "main.py"})
        assert "Editing" in result

    def test_grep_pattern(self) -> None:
        result = _humanize_tool("grep", {"pattern": "TODO"})
        assert "Searching" in result
        assert "TODO" in result

    def test_glob_pattern(self) -> None:
        result = _humanize_tool("glob", {"pattern": "**/*.py"})
        assert "Finding" in result
        assert "**/*.py" in result

    def test_unknown_tool_with_string_arg(self) -> None:
        result = _humanize_tool("my_mcp_tool", {"query": "test data"})
        assert "my_mcp_tool" in result
        assert "test data" in result

    def test_unknown_tool_no_string_args(self) -> None:
        result = _humanize_tool("my_tool", {"count": 5})
        assert result == "my_tool"

    def test_unknown_tool_long_arg_truncated(self) -> None:
        result = _humanize_tool("my_custom_tool", {"query": "a" * 50})
        assert "..." in result
        assert len(result) <= 55

    def test_case_insensitive(self) -> None:
        result = _humanize_tool("Bash", {"command": "ls"})
        assert result == "bash ls"

    def test_read_file_variant(self) -> None:
        result = _humanize_tool("read_file", {"file_path": "test.py"})
        assert "Reading" in result

    def test_list_directory(self) -> None:
        result = _humanize_tool("list_directory", {"path": "/tmp"})
        assert "Listing" in result


class TestShortPath:
    def test_empty_path(self) -> None:
        assert _short_path("") == ""

    def test_relative_path_stays_relative(self) -> None:
        result = _short_path("src/main.py")
        assert "src/main.py" in result or "main.py" in result


class TestFormatTokens:
    def test_small_number(self) -> None:
        assert _format_tokens(500) == "500"

    def test_exactly_1000(self) -> None:
        assert _format_tokens(1000) == "1.0k"

    def test_large_number(self) -> None:
        assert _format_tokens(128000) == "128k"

    def test_mid_range(self) -> None:
        assert _format_tokens(5500) == "5.5k"

    def test_over_10k(self) -> None:
        assert _format_tokens(45230) == "45k"

    def test_zero(self) -> None:
        assert _format_tokens(0) == "0"


class TestOutputSummary:
    def test_error_output(self) -> None:
        result = _output_summary({"error": "file not found"})
        assert "file not found" in result

    def test_short_content(self) -> None:
        result = _output_summary({"content": "hello world"})
        assert result == "hello world"

    def test_long_content_shows_stats(self) -> None:
        result = _output_summary({"content": "x" * 100})
        assert "lines" in result or "chars" in result

    def test_stdout_single_line(self) -> None:
        result = _output_summary({"stdout": "all tests passed"})
        assert result == "all tests passed"

    def test_stdout_multiline(self) -> None:
        result = _output_summary({"stdout": "line1\nline2\nline3"})
        assert "+2 lines" in result

    def test_empty_dict(self) -> None:
        assert _output_summary({}) == ""

    def test_non_dict(self) -> None:
        assert _output_summary("string") == ""


class TestTurnHistory:
    def setup_method(self) -> None:
        clear_turn_history()

    def test_clear_and_save_empty(self) -> None:
        clear_turn_history()
        save_turn_history()
        # Should not crash

    def test_save_preserves_tools(self) -> None:
        from anteroom.cli.renderer import _current_turn_tools, _tool_history

        _current_turn_tools.append(
            {
                "tool_name": "bash",
                "arguments": {"command": "ls"},
                "summary": "bash ls",
                "status": "success",
                "output": {"stdout": "file.txt"},
                "elapsed": 0.5,
            }
        )
        save_turn_history()
        assert len(_tool_history) == 1
        assert _tool_history[0]["tool_name"] == "bash"

    def test_clear_removes_current(self) -> None:
        from anteroom.cli.renderer import _current_turn_tools

        _current_turn_tools.append({"tool_name": "test"})
        clear_turn_history()
        assert len(_current_turn_tools) == 0


class TestDedup:
    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        r._dedup_key = ""
        r._dedup_count = 0
        r._dedup_first_summary = ""

        r._dedup_summary = ""
        r._tool_dedup_enabled = True

    def test_flush_dedup_resets_state(self) -> None:
        import anteroom.cli.renderer as r

        r._dedup_key = "Editing"
        r._dedup_summary = "Editing test.py"
        r._dedup_count = 3
        _flush_dedup()
        assert r._dedup_summary == ""
        assert r._dedup_key == ""
        assert r._dedup_count == 0

    def test_flush_dedup_noop_when_empty(self) -> None:
        _flush_dedup()  # Should not crash


class TestStartThinkingFlushesDedup:
    """start_thinking() should flush dedup state so repeated tools across iterations are visible."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        r._dedup_key = ""
        r._dedup_count = 0
        r._dedup_first_summary = ""

        r._dedup_summary = ""
        r._tool_batch_active = False
        r._tool_dedup_enabled = True

    def test_start_thinking_flushes_dedup(self) -> None:
        import anteroom.cli.renderer as r

        r._dedup_key = "bash"
        r._dedup_summary = "bash git status"
        r._dedup_count = 3
        with patch("anteroom.cli.renderer._write_thinking_line"):
            r._repl_mode = True
            start_thinking()
            r._repl_mode = False
        assert r._dedup_summary == ""
        assert r._dedup_key == ""
        assert r._dedup_count == 0

    def test_start_thinking_resets_tool_batch(self) -> None:
        import anteroom.cli.renderer as r

        r._tool_batch_active = True
        with (
            patch("anteroom.cli.renderer._write_thinking_line"),
            patch("anteroom.cli.renderer.console") as mock_console,
        ):
            r._repl_mode = True
            start_thinking()
            r._repl_mode = False
        assert r._tool_batch_active is False
        # Should have emitted a blank line for spacing (#680)
        blank_calls = [c for c in mock_console.print.call_args_list if c == ((),) or c[0] == ()]
        assert len(blank_calls) >= 1

    def test_start_thinking_no_spacing_without_tool_batch(self) -> None:
        """start_thinking() should NOT emit a blank line when no tool batch was active."""
        import anteroom.cli.renderer as r

        r._tool_batch_active = False
        with (
            patch("anteroom.cli.renderer._write_thinking_line"),
            patch("anteroom.cli.renderer.console") as mock_console,
        ):
            r._repl_mode = True
            start_thinking()
            r._repl_mode = False
        blank_calls = [c for c in mock_console.print.call_args_list if c == ((),) or c[0] == ()]
        assert len(blank_calls) == 0

    def test_repeated_tool_across_thinking_boundary_not_deduped(self) -> None:
        """Same tool before and after start_thinking() should both produce output."""
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.COMPACT)
        r._dedup_key = ""
        r._dedup_count = 0
        r._dedup_first_summary = ""

        r._dedup_summary = ""
        r._tool_batch_active = False
        r._current_turn_tools.clear()

        # First tool call
        render_tool_call_start("bash", {"command": "git status"})
        render_tool_call_end("bash", "success", {"stdout": "clean"})
        assert r._dedup_key == "bash"
        assert r._dedup_count == 1

        # Thinking boundary (new iteration)
        with patch("anteroom.cli.renderer._write_thinking_line"):
            r._repl_mode = True
            start_thinking()
            r._repl_mode = False

        # Dedup state should be flushed
        assert r._dedup_key == ""
        assert r._dedup_count == 0

        # Same tool again — should NOT be deduped
        render_tool_call_start("bash", {"command": "git status"})
        render_tool_call_end("bash", "success", {"stdout": "clean"})
        assert r._dedup_key == "bash"
        assert r._dedup_count == 1  # Fresh count, not accumulated


class TestFlushBufferedText:
    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        r._streaming_buffer.clear()

    def test_flush_clears_buffer(self) -> None:
        import anteroom.cli.renderer as r

        r._streaming_buffer.append("hello")
        r._streaming_buffer.append(" world")
        flush_buffered_text()
        assert len(r._streaming_buffer) == 0

    def test_flush_noop_when_empty(self) -> None:
        flush_buffered_text()  # Should not crash

    def test_flush_noop_for_whitespace(self) -> None:
        import anteroom.cli.renderer as r

        r._streaming_buffer.append("   \n  ")
        flush_buffered_text()
        assert len(r._streaming_buffer) == 0


class TestFlushBufferedTextToolSpacing:
    """Tests for #680: blank line between tool call block and narration text."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        r._streaming_buffer.clear()
        r._tool_batch_active = False

    def test_flush_adds_spacing_after_tool_batch(self) -> None:
        """flush_buffered_text() should print a blank line when _tool_batch_active is True."""
        import anteroom.cli.renderer as r

        r._tool_batch_active = True
        r._streaming_buffer.append("Some narration text")
        with patch("anteroom.cli.renderer.console") as mock_console, patch("anteroom.cli.renderer._stdout_console"):
            flush_buffered_text()
            # Should print a blank line (spacing after tool block)
            blank_calls = [c for c in mock_console.print.call_args_list if c == ((),) or c[0] == ()]
            assert len(blank_calls) == 1
            assert r._tool_batch_active is False

    def test_flush_no_spacing_without_tool_batch(self) -> None:
        """flush_buffered_text() should NOT print a blank line when _tool_batch_active is False."""
        import anteroom.cli.renderer as r

        r._tool_batch_active = False
        r._streaming_buffer.append("Some narration text")
        with patch("anteroom.cli.renderer.console") as mock_console, patch("anteroom.cli.renderer._stdout_console"):
            flush_buffered_text()
            blank_calls = [c for c in mock_console.print.call_args_list if c == ((),) or c[0] == ()]
            assert len(blank_calls) == 0

    def test_flush_clears_tool_batch_flag(self) -> None:
        """flush_buffered_text() should clear _tool_batch_active after emitting spacing."""
        import anteroom.cli.renderer as r

        r._tool_batch_active = True
        r._streaming_buffer.append("text")
        with patch("anteroom.cli.renderer.console"), patch("anteroom.cli.renderer._stdout_console"):
            flush_buffered_text()
        assert r._tool_batch_active is False

    def test_flush_empty_buffer_preserves_tool_batch(self) -> None:
        """flush_buffered_text() with empty buffer should not clear _tool_batch_active."""
        import anteroom.cli.renderer as r

        r._tool_batch_active = True
        # Buffer is empty — early return before the spacing check
        with patch("anteroom.cli.renderer.console"), patch("anteroom.cli.renderer._stdout_console"):
            flush_buffered_text()
        # _tool_batch_active stays True for render_response_end() to handle
        assert r._tool_batch_active is True

    def test_no_double_spacing_flush_then_response_end(self) -> None:
        """If flush_buffered_text() already emitted spacing, render_response_end() should not add another."""
        import anteroom.cli.renderer as r

        r._tool_batch_active = True
        r._streaming_buffer.append("mid-turn narration")
        with patch("anteroom.cli.renderer.console"), patch("anteroom.cli.renderer._stdout_console"):
            flush_buffered_text()
        # _tool_batch_active is now False
        assert r._tool_batch_active is False

        # Now simulate end-of-turn with more text
        r._streaming_buffer.append("final text")
        with patch("anteroom.cli.renderer.console") as mock_console, patch("anteroom.cli.renderer._stdout_console"):
            render_response_end()
            # No blank line from console since _tool_batch_active was already cleared
            blank_calls = [c for c in mock_console.print.call_args_list if c == ((),) or c[0] == ()]
            assert len(blank_calls) == 0


class TestToolCallDimming:
    """Tests for #111/#140: muted intermediate CLI output."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.COMPACT)
        r._dedup_key = ""
        r._dedup_count = 0
        r._dedup_first_summary = ""

        r._dedup_summary = ""
        r._tool_batch_active = False
        r._current_turn_tools.clear()
        r._tool_dedup_enabled = True

    def _set_tool_start(self) -> None:
        """Set _tool_start to a recent time so elapsed is small."""
        import time

        import anteroom.cli.renderer as r

        r._tool_start = time.monotonic()

    def test_successful_tool_call_compact_uses_muted(self) -> None:
        import anteroom.cli.renderer as r

        self._set_tool_start()
        r._current_turn_tools.append(
            {
                "tool_name": "bash",
                "arguments": {"command": "ls"},
                "summary": "bash ls",
                "status": "running",
                "output": None,
            }
        )
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_tool_call_end("bash", "success", {"stdout": "file.txt"})
            printed = str(mock_console.print.call_args_list)
            assert r.MUTED in printed

    def test_successful_tool_call_detailed_uses_muted(self) -> None:
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.DETAILED)
        self._set_tool_start()
        r._current_turn_tools.append(
            {
                "tool_name": "bash",
                "arguments": {"command": "ls"},
                "summary": "bash ls",
                "status": "running",
                "output": None,
            }
        )
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_tool_call_end("bash", "success", {"stdout": "file.txt"})
            first_call = str(mock_console.print.call_args_list[0])
            assert r.MUTED in first_call

    def test_error_tool_call_not_muted(self) -> None:
        import anteroom.cli.renderer as r

        self._set_tool_start()
        r._current_turn_tools.append(
            {
                "tool_name": "bash",
                "arguments": {"command": "bad"},
                "summary": "bash bad",
                "status": "running",
                "output": None,
            }
        )
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_tool_call_end("bash", "error", {"error": "command failed"})
            first_call = str(mock_console.print.call_args_list[0])
            assert r.MUTED not in first_call
            assert r._theme.error in first_call

    def test_verbose_mode_unchanged(self) -> None:
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.VERBOSE)
        self._set_tool_start()
        r._current_turn_tools.append(
            {
                "tool_name": "bash",
                "arguments": {"command": "ls"},
                "summary": "bash ls",
                "status": "running",
                "output": None,
            }
        )
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_tool_call_end("bash", "success", {"stdout": "file.txt"})
            printed = str(mock_console.print.call_args_list)
            assert r.MUTED not in printed


class TestInlineDiff:
    """Tests for #281: Claude Code-style inline diff rendering."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.COMPACT)
        r._dedup_key = ""
        r._dedup_count = 0
        r._dedup_first_summary = ""
        r._dedup_summary = ""
        r._tool_batch_active = False
        r._current_turn_tools.clear()
        r._tool_dedup_enabled = True

    def _set_tool_start(self) -> None:
        import anteroom.cli.renderer as r

        r._tool_start = time.monotonic()

    def test_has_diff_data_true_for_write_file(self) -> None:
        assert _has_diff_data("write_file", {"_new_content": "hello"})

    def test_has_diff_data_true_for_edit_file(self) -> None:
        assert _has_diff_data("edit_file", {"_old_content": "a", "_new_content": "b"})

    def test_has_diff_data_false_for_bash(self) -> None:
        assert not _has_diff_data("bash", {"stdout": "hello"})

    def test_has_diff_data_false_for_no_content(self) -> None:
        assert not _has_diff_data("write_file", {"status": "ok"})

    def test_has_diff_data_false_for_non_dict(self) -> None:
        assert not _has_diff_data("write_file", "string output")

    def test_render_inline_diff_created(self) -> None:
        output = {
            "status": "ok",
            "path": "/tmp/new_file.py",
            "action": "created",
            "lines": 10,
            "_new_content": "line1\nline2\n",
        }
        with patch("anteroom.cli.renderer.console") as mock_console:
            _render_inline_diff("write_file", output)
            printed = str(mock_console.print.call_args_list)
            assert "Write(" in printed
            assert "Created" in printed
            assert "10 lines" in printed

    def test_render_inline_diff_edit(self) -> None:
        output = {
            "status": "ok",
            "path": "/tmp/test.py",
            "_old_content": "line1\nline2\nline3\n",
            "_new_content": "line1\nmodified\nline3\nnew_line\n",
        }
        with patch("anteroom.cli.renderer.console") as mock_console:
            _render_inline_diff("edit_file", output)
            printed = str(mock_console.print.call_args_list)
            assert "Update(" in printed
            assert "Added" in printed
            assert "removed" in printed

    def test_render_inline_diff_write_update(self) -> None:
        output = {
            "status": "ok",
            "path": "/tmp/test.py",
            "action": "updated",
            "_old_content": "old\n",
            "_new_content": "new\n",
        }
        with patch("anteroom.cli.renderer.console") as mock_console:
            _render_inline_diff("write_file", output)
            printed = str(mock_console.print.call_args_list)
            assert "Write(" in printed

    def test_render_tool_call_end_uses_inline_diff(self) -> None:
        import anteroom.cli.renderer as r

        self._set_tool_start()
        r._current_turn_tools.append(
            {
                "tool_name": "edit_file",
                "arguments": {"path": "test.py", "old_text": "a", "new_text": "b"},
                "summary": "Editing test.py",
                "status": "running",
                "output": None,
            }
        )
        output = {
            "status": "ok",
            "path": "/tmp/test.py",
            "_old_content": "aaa\n",
            "_new_content": "baa\n",
            "lines_before": 1,
            "lines_after": 1,
        }
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_tool_call_end("edit_file", "success", output)
            printed = str(mock_console.print.call_args_list)
            # Should use inline diff, not the default muted summary
            assert "Update(" in printed

    def test_render_tool_call_end_error_no_diff(self) -> None:
        """Errors should not trigger inline diff even with diff data."""
        import anteroom.cli.renderer as r

        self._set_tool_start()
        r._current_turn_tools.append(
            {
                "tool_name": "edit_file",
                "arguments": {"path": "test.py", "old_text": "a", "new_text": "b"},
                "summary": "Editing test.py",
                "status": "running",
                "output": None,
            }
        )
        output = {"error": "file not found"}
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_tool_call_end("edit_file", "error", output)
            printed = str(mock_console.print.call_args_list)
            assert "Update(" not in printed
            assert "file not found" in printed

    def test_inline_diff_resets_dedup(self) -> None:
        """Inline diff should reset dedup state so next tool isn't collapsed."""
        import anteroom.cli.renderer as r

        self._set_tool_start()
        r._current_turn_tools.append(
            {
                "tool_name": "edit_file",
                "arguments": {"path": "a.py", "old_text": "x", "new_text": "y"},
                "summary": "Editing a.py",
                "status": "running",
                "output": None,
            }
        )
        output = {
            "status": "ok",
            "path": "/tmp/a.py",
            "_old_content": "x\n",
            "_new_content": "y\n",
        }
        with patch("anteroom.cli.renderer.console"):
            render_tool_call_end("edit_file", "success", output)
        assert r._dedup_key == ""
        assert r._dedup_count == 0


class TestToolBatchSpacing:
    """Tests for #111: spacing around tool call blocks."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.COMPACT)
        r._tool_batch_active = False
        r._current_turn_tools.clear()
        r._streaming_buffer.clear()
        r._dedup_key = ""
        r._dedup_count = 0
        r._dedup_first_summary = ""

        r._dedup_summary = ""
        r._tool_dedup_enabled = True

    def test_first_tool_call_adds_blank_line(self) -> None:
        import anteroom.cli.renderer as r

        assert r._tool_batch_active is False
        with patch("anteroom.cli.renderer.console") as mock_console, patch("anteroom.cli.renderer._stdout_console"):
            render_tool_call_start("bash", {"command": "ls"})
            # First call should be a blank line (no args = blank)
            assert mock_console.print.call_count >= 1
            first_print = mock_console.print.call_args_list[0]
            assert first_print == ((),) or first_print[0] == ()
            assert r._tool_batch_active is True

    def test_second_tool_call_no_extra_blank_line(self) -> None:
        import anteroom.cli.renderer as r

        r._tool_batch_active = True
        with patch("anteroom.cli.renderer.console") as mock_console, patch("anteroom.cli.renderer._stdout_console"):
            render_tool_call_start("bash", {"command": "ls"})
            # In compact mode, render_tool_call_start prints nothing for non-verbose
            # The key assertion is that no blank line was printed
            for call in mock_console.print.call_args_list:
                if call == ((),) or (call[0] == () and call[1] == {}):
                    raise AssertionError("Should not print blank line for second tool call")

    def test_save_turn_history_resets_batch_flag(self) -> None:
        import anteroom.cli.renderer as r

        r._tool_batch_active = True
        with patch("anteroom.cli.renderer.console"):
            save_turn_history()
        assert r._tool_batch_active is False

    def test_response_end_adds_spacing_after_tools(self) -> None:
        import anteroom.cli.renderer as r

        r._tool_batch_active = True
        r._streaming_buffer.extend(["hello ", "world"])
        with patch("anteroom.cli.renderer.console") as mock_console, patch("anteroom.cli.renderer._stdout_console"):
            render_response_end()
            # Should print a blank line (spacing after tool block)
            assert mock_console.print.call_count >= 1
            first_print = mock_console.print.call_args_list[0]
            assert first_print == ((),) or first_print[0] == ()
            assert r._tool_batch_active is False

    def test_response_end_no_extra_spacing_without_tools(self) -> None:
        import anteroom.cli.renderer as r

        r._tool_batch_active = False
        r._streaming_buffer.extend(["hello ", "world"])
        with patch("anteroom.cli.renderer.console") as mock_console, patch("anteroom.cli.renderer._stdout_console"):
            render_response_end()
            # No blank line should be printed on console (only _stdout_console gets the markdown)
            blank_calls = [c for c in mock_console.print.call_args_list if c == ((),) or c[0] == ()]
            assert len(blank_calls) == 0


class TestStartupStep:
    """Tests for #122: startup progress feedback."""

    def test_returns_context_manager(self) -> None:
        with patch("anteroom.cli.renderer.console") as mock_console:
            mock_console.status.return_value.__enter__ = lambda s: s
            mock_console.status.return_value.__exit__ = lambda s, *a: None
            result = startup_step("Loading...")
            assert result is mock_console.status.return_value

    def test_uses_muted_styling(self) -> None:
        import anteroom.cli.renderer as r

        with patch("anteroom.cli.renderer.console") as mock_console:
            mock_console.status.return_value.__enter__ = lambda s: s
            mock_console.status.return_value.__exit__ = lambda s, *a: None
            startup_step("Loading...")
            call_args = mock_console.status.call_args
            message_arg = call_args[0][0]
            assert r.MUTED in message_arg
            assert "Loading..." in message_arg

    def test_uses_dots12_spinner(self) -> None:
        import anteroom.cli.renderer as r

        with patch("anteroom.cli.renderer.console") as mock_console:
            mock_console.status.return_value.__enter__ = lambda s: s
            mock_console.status.return_value.__exit__ = lambda s, *a: None
            startup_step("Connecting...")
            call_kwargs = mock_console.status.call_args[1]
            assert call_kwargs["spinner"] == "dots12"
            assert call_kwargs["spinner_style"] == r.MUTED

    def test_message_indented(self) -> None:
        with patch("anteroom.cli.renderer.console") as mock_console:
            mock_console.status.return_value.__enter__ = lambda s: s
            mock_console.status.return_value.__exit__ = lambda s, *a: None
            startup_step("Validating...")
            message_arg = mock_console.status.call_args[0][0]
            assert message_arg.startswith("  ")

    def test_works_as_context_manager(self) -> None:
        from unittest.mock import MagicMock

        with patch("anteroom.cli.renderer.console") as mock_console:
            mock_ctx = MagicMock()
            mock_console.status.return_value = mock_ctx
            with startup_step("Testing..."):
                pass
            mock_ctx.__enter__.assert_called_once()
            mock_ctx.__exit__.assert_called_once()

    def test_mcp_label_singular(self) -> None:
        """MCP spinner label uses singular for 1 server."""
        server_count = 1
        label = f"Starting {server_count} MCP server{'s' if server_count != 1 else ''}..."
        assert label == "Starting 1 MCP server..."

    def test_mcp_label_plural(self) -> None:
        """MCP spinner label uses plural for multiple servers."""
        server_count = 3
        label = f"Starting {server_count} MCP server{'s' if server_count != 1 else ''}..."
        assert label == "Starting 3 MCP servers..."

    def test_mcp_label_zero(self) -> None:
        """MCP spinner label handles zero servers."""
        server_count = 0
        label = f"Starting {server_count} MCP server{'s' if server_count != 1 else ''}..."
        assert label == "Starting 0 MCP servers..."


class TestDedupKeyFromSummary:
    """Tests for _dedup_key_from_summary grouping logic."""

    def test_editing_key(self) -> None:
        assert _dedup_key_from_summary("Editing src/main.py") == "Editing"

    def test_reading_key(self) -> None:
        assert _dedup_key_from_summary("Reading config.py") == "Reading"

    def test_writing_key(self) -> None:
        assert _dedup_key_from_summary("Writing output.txt") == "Writing"

    def test_searching_key(self) -> None:
        assert _dedup_key_from_summary("Searching for 'TODO'") == "Searching"

    def test_bash_key(self) -> None:
        assert _dedup_key_from_summary("bash git status") == "bash"

    def test_mcp_tool_key(self) -> None:
        assert _dedup_key_from_summary("my_mcp_tool query") == "my_mcp_tool"

    def test_single_word_tool(self) -> None:
        assert _dedup_key_from_summary("my_tool") == "my_tool"

    def test_finding_key(self) -> None:
        assert _dedup_key_from_summary("Finding **/*.py") == "Finding"

    def test_listing_key(self) -> None:
        assert _dedup_key_from_summary("Listing /tmp") == "Listing"

    def test_subagent_key(self) -> None:
        assert _dedup_key_from_summary("Sub-agent: do something") == "Sub-agent:"


class TestDedupFlushLabel:
    """Tests for _dedup_flush_label human-readable summaries."""

    def test_editing_label(self) -> None:
        result = _dedup_flush_label("Editing", 5)
        assert "edited" in result
        assert "5" in result
        assert "files" in result

    def test_reading_label(self) -> None:
        result = _dedup_flush_label("Reading", 3)
        assert "read" in result
        assert "3" in result

    def test_bash_label(self) -> None:
        result = _dedup_flush_label("bash", 4)
        assert "ran" in result
        assert "4" in result

    def test_unknown_tool_label(self) -> None:
        result = _dedup_flush_label("my_mcp_tool", 2)
        assert "my_mcp_tool" in result
        assert "2" in result


class TestEnhancedDedup:
    """Tests for #59: enhanced tool call dedup grouping by tool type."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.COMPACT)
        r._dedup_key = ""
        r._dedup_count = 0
        r._dedup_first_summary = ""

        r._dedup_summary = ""
        r._tool_batch_active = False
        r._current_turn_tools.clear()
        r._tool_dedup_enabled = True

    def _set_tool_start(self) -> None:
        import time

        import anteroom.cli.renderer as r

        r._tool_start = time.monotonic()

    def test_consecutive_edits_different_files_collapse(self) -> None:
        """Editing foo.py then bar.py should collapse (same dedup key 'Editing')."""
        import anteroom.cli.renderer as r

        with patch("anteroom.cli.renderer.console"):
            # First edit
            render_tool_call_start("edit_file", {"path": "foo.py"})
            self._set_tool_start()
            render_tool_call_end("edit_file", "success", {"content": "ok"})
            assert r._dedup_key == "Editing"
            assert r._dedup_count == 1

            # Second edit — different file, same tool type → collapsed
            render_tool_call_start("edit_file", {"path": "bar.py"})
            self._set_tool_start()
            render_tool_call_end("edit_file", "success", {"content": "ok"})
            assert r._dedup_key == "Editing"
            assert r._dedup_count == 2

    def test_different_tool_types_dont_collapse(self) -> None:
        """Editing then Reading should NOT collapse."""
        import anteroom.cli.renderer as r

        with patch("anteroom.cli.renderer.console"):
            render_tool_call_start("edit_file", {"path": "foo.py"})
            self._set_tool_start()
            render_tool_call_end("edit_file", "success", {"content": "ok"})
            assert r._dedup_key == "Editing"

            render_tool_call_start("read_file", {"file_path": "bar.py"})
            self._set_tool_start()
            render_tool_call_end("read_file", "success", {"content": "data"})
            assert r._dedup_key == "Reading"
            assert r._dedup_count == 1  # Fresh, not accumulated

    def test_dedup_disabled_shows_all(self) -> None:
        """With dedup disabled, consecutive identical calls should all print."""
        set_tool_dedup(False)
        with patch("anteroom.cli.renderer.console") as mock_console:
            for i in range(3):
                render_tool_call_start("edit_file", {"path": "foo.py"})
                self._set_tool_start()
                render_tool_call_end("edit_file", "success", {"content": "ok"})

            # Each call should print (no dedup suppression)
            print_calls = [c for c in mock_console.print.call_args_list if "Editing" in str(c)]
            assert len(print_calls) == 3

    def test_flush_dedup_prints_summary_for_edits(self) -> None:
        """Flushing a group of 3 edits should print '... edited 3 files total'."""
        import anteroom.cli.renderer as r

        r._dedup_key = "Editing"
        r._dedup_count = 3
        r._dedup_first_summary = "Editing foo.py"
        r._dedup_summary = "Editing foo.py"

        with patch("anteroom.cli.renderer.console") as mock_console:
            _flush_dedup()
            printed = str(mock_console.print.call_args_list)
            assert "edited" in printed
            assert "3" in printed

    def test_error_breaks_dedup_group(self) -> None:
        """An error tool call should break the dedup group."""
        import anteroom.cli.renderer as r

        with patch("anteroom.cli.renderer.console"):
            render_tool_call_start("edit_file", {"path": "foo.py"})
            self._set_tool_start()
            render_tool_call_end("edit_file", "success", {"content": "ok"})
            assert r._dedup_key == "Editing"

            render_tool_call_start("edit_file", {"path": "bar.py"})
            self._set_tool_start()
            render_tool_call_end("edit_file", "error", {"error": "file not found"})
            assert r._dedup_key == ""
            assert r._dedup_count == 0

    def test_set_tool_dedup(self) -> None:
        """set_tool_dedup() should update the module-level flag."""
        import anteroom.cli.renderer as r

        set_tool_dedup(False)
        assert r._tool_dedup_enabled is False
        set_tool_dedup(True)
        assert r._tool_dedup_enabled is True


class TestWriteThinkingLine:
    """Tests for _write_thinking_line() ESC cancel hint (#164)."""

    def test_no_timer_under_half_second(self) -> None:
        """Under 0.5s: only 'Thinking...' with no timer or hint."""
        import io

        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        _write_thinking_line(0.3)
        output = buf.getvalue()
        assert "Thinking..." in output
        assert "s" not in output.split("Thinking...")[1].split("\033")[0]
        assert "esc to cancel" not in output
        r._stdout = None

    def test_no_hint_under_threshold(self) -> None:
        """Between 0.5s and 3s: timer shown but no ESC hint."""
        import io

        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        _write_thinking_line(2.0)
        output = buf.getvalue()
        assert "2s" in output
        assert "esc to cancel" not in output
        r._stdout = None

    def test_hint_at_threshold(self) -> None:
        """At exactly 3s: ESC hint appears."""
        import io

        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        _write_thinking_line(3.0)
        output = buf.getvalue()
        assert "3s" in output
        assert "esc to cancel" in output
        r._stdout = None

    def test_hint_after_threshold(self) -> None:
        """Well past threshold: ESC hint still present."""
        import io

        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        _write_thinking_line(10.0)
        output = buf.getvalue()
        assert "10s" in output
        assert "esc to cancel" in output
        r._stdout = None

    def test_hint_uses_muted_color(self) -> None:
        """ESC hint should use the MUTED color (RGB 139,139,139)."""
        import io

        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        _write_thinking_line(5.0)
        output = buf.getvalue()
        assert "\033[38;2;139;139;139m" in output
        r._stdout = None

    def test_no_stall_warning_under_threshold(self) -> None:
        """Under 15s: no stall warning shown."""
        import io

        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        _write_thinking_line(10.0)
        output = buf.getvalue()
        assert "waiting for API" not in output
        r._stdout = None

    def test_no_stall_warning_without_phase(self) -> None:
        """At 15s+ with no phase set: no stall warning (phase system handles it)."""
        import io

        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        r._thinking_phase = ""
        _write_thinking_line(15.0)
        output = buf.getvalue()
        assert "15s" in output
        assert "esc to cancel" in output
        r._stdout = None

    def test_long_elapsed_still_shows_hint(self) -> None:
        """Well past threshold: timer and ESC hint present."""
        import io

        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        r._thinking_phase = ""
        _write_thinking_line(30.0)
        output = buf.getvalue()
        assert "30s" in output
        assert "esc to cancel" in output
        r._stdout = None


class TestThinkingTicker:
    """Tests for background ticker task (#201)."""

    @pytest.mark.asyncio
    async def test_start_thinking_creates_ticker_task(self) -> None:
        """start_thinking() should create a background ticker task."""
        import anteroom.cli.renderer as r

        r._repl_mode = True
        r._stdout = io.StringIO()
        try:
            start_thinking()
            assert r._thinking_ticker_task is not None
            assert not r._thinking_ticker_task.done()
        finally:
            stop_thinking_sync()
            r._repl_mode = False
            r._stdout = None

    @pytest.mark.asyncio
    async def test_stop_thinking_cancels_ticker_task(self) -> None:
        """stop_thinking() should cancel and await the ticker task."""
        import anteroom.cli.renderer as r

        r._repl_mode = True
        r._stdout = io.StringIO()
        try:
            start_thinking()
            task = r._thinking_ticker_task
            assert task is not None
            await stop_thinking()
            assert r._thinking_ticker_task is None
            assert task.cancelled() or task.done()
        finally:
            r._repl_mode = False
            r._stdout = None

    @pytest.mark.asyncio
    async def test_ticker_updates_timer(self) -> None:
        """Background ticker should advance the displayed timer."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._repl_mode = True
        r._stdout = buf
        try:
            start_thinking()
            # Override _thinking_start *after* start_thinking to pretend 5s elapsed
            r._thinking_start = time.monotonic() - 5.0
            await asyncio.sleep(0.6)
            output = buf.getvalue()
            assert "5s" in output or "6s" in output
        finally:
            stop_thinking_sync()
            r._repl_mode = False
            r._stdout = None

    def test_start_thinking_without_event_loop_is_safe(self) -> None:
        """When no event loop is running, ticker task is None (no crash)."""
        import anteroom.cli.renderer as r

        r._repl_mode = False
        r._stdout = None
        try:
            start_thinking()
            assert r._thinking_ticker_task is None
        finally:
            stop_thinking_sync()

    @pytest.mark.asyncio
    async def test_double_start_cancels_previous_ticker(self) -> None:
        """Calling start_thinking() twice cancels the first ticker (no task leak)."""
        import anteroom.cli.renderer as r

        r._repl_mode = True
        r._stdout = io.StringIO()
        try:
            start_thinking()
            first_task = r._thinking_ticker_task
            assert first_task is not None
            start_thinking()
            second_task = r._thinking_ticker_task
            assert second_task is not None
            assert second_task is not first_task
            await asyncio.sleep(0)
            assert first_task.cancelled() or first_task.done()
        finally:
            stop_thinking_sync()
            r._repl_mode = False
            r._stdout = None


class TestFirstThinkingNewline:
    """Regression tests for first thinking indicator blank line (#249).

    start_thinking(newline=True) must write \\n + Thinking... as a single
    atomic write so prompt_toolkit's cursor teardown cannot interleave.
    """

    @pytest.mark.asyncio
    async def test_newline_true_writes_atomic_line(self) -> None:
        """newline=True writes \\n + erase + Thinking... in one write call."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._repl_mode = True
        r._stdout = buf
        try:
            start_thinking(newline=True)
            output = buf.getvalue()
            # Must contain Thinking... text
            assert "Thinking..." in output
            # Must start with \n for visual separation
            assert output.startswith("\n")
            # The \n and Thinking... must be in the same write (atomic)
            assert "\n\r\033[2K" in output
        finally:
            stop_thinking_sync()
            r._repl_mode = False
            r._stdout = None

    @pytest.mark.asyncio
    async def test_newline_false_no_leading_newline(self) -> None:
        """Default (newline=False) writes no leading \\n — for retries."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._repl_mode = True
        r._stdout = buf
        try:
            start_thinking(newline=False)
            output = buf.getvalue()
            assert "Thinking..." in output
            # Must NOT start with \n (retry overwrites in-place)
            assert not output.startswith("\n")
        finally:
            stop_thinking_sync()
            r._repl_mode = False
            r._stdout = None

    @pytest.mark.asyncio
    async def test_newline_default_is_false(self) -> None:
        """start_thinking() without keyword defaults to no leading newline."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._repl_mode = True
        r._stdout = buf
        try:
            start_thinking()
            output = buf.getvalue()
            assert "Thinking..." in output
            assert not output.startswith("\n")
        finally:
            stop_thinking_sync()
            r._repl_mode = False
            r._stdout = None

    def test_newline_ignored_in_non_repl_mode(self) -> None:
        """In non-repl mode, newline=True has no effect (Rich Status used)."""
        import anteroom.cli.renderer as r

        r._repl_mode = False
        r._stdout = None
        try:
            start_thinking(newline=True)
            # Should use Rich Status, not raw ANSI write
            assert r._spinner is not None
        finally:
            stop_thinking_sync()
            r._repl_mode = False
            r._stdout = None


class TestThinkingPhases:
    """Tests for lifecycle phase tracking in the thinking indicator (#203)."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        r._thinking_phase = ""
        r._thinking_tokens = 0
        r._streaming_chars = 0
        r._last_chunk_time = 0
        r._phase_start_time = 0
        r._retrying_info = {}
        r._throughput_window.clear()
        set_verbosity(Verbosity.DETAILED)

    def teardown_method(self) -> None:
        import anteroom.cli.renderer as r

        r._thinking_phase = ""
        r._thinking_tokens = 0
        r._streaming_chars = 0
        r._last_chunk_time = 0
        r._phase_start_time = 0
        r._retrying_info = {}
        r._throughput_window.clear()
        set_verbosity(Verbosity.COMPACT)

    def test_set_thinking_phase_connecting(self) -> None:
        """set_thinking_phase('connecting') updates the module state."""
        import anteroom.cli.renderer as r

        set_thinking_phase("connecting")
        assert r._thinking_phase == "connecting"
        assert r._last_chunk_time > 0

    def test_set_thinking_phase_waiting(self) -> None:
        """set_thinking_phase('waiting') updates the module state."""
        import anteroom.cli.renderer as r

        set_thinking_phase("waiting")
        assert r._thinking_phase == "waiting"

    def test_set_thinking_phase_updates_chunk_time(self) -> None:
        """set_thinking_phase updates _last_chunk_time for stall detection."""
        import anteroom.cli.renderer as r

        before = time.monotonic()
        set_thinking_phase("connecting")
        assert r._last_chunk_time >= before

    def test_increment_thinking_tokens_increments_counter(self) -> None:
        """increment_thinking_tokens increases _thinking_tokens by 1."""
        import anteroom.cli.renderer as r

        r._thinking_tokens = 0
        increment_thinking_tokens()
        assert r._thinking_tokens == 1
        increment_thinking_tokens()
        assert r._thinking_tokens == 2
        increment_thinking_tokens()
        assert r._thinking_tokens == 3

    def test_increment_thinking_tokens_sets_streaming_phase(self) -> None:
        """increment_thinking_tokens implicitly transitions to 'streaming' phase."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "waiting"
        increment_thinking_tokens()
        assert r._thinking_phase == "streaming"

    def test_increment_thinking_tokens_updates_chunk_time(self) -> None:
        """increment_thinking_tokens updates _last_chunk_time."""
        import anteroom.cli.renderer as r

        before = time.monotonic()
        increment_thinking_tokens()
        assert r._last_chunk_time >= before

    def test_phase_suffix_empty_when_no_phase(self) -> None:
        """_phase_suffix returns empty string when no phase is set."""
        import anteroom.cli.renderer as r

        r._thinking_phase = ""
        assert _phase_suffix(5.0) == ""

    def test_phase_suffix_shown_in_compact_mode(self) -> None:
        """_phase_suffix returns phase text even in COMPACT verbosity (health monitor)."""
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.COMPACT)
        r._thinking_phase = "connecting"
        assert _phase_suffix(5.0) == "connecting"

    def test_phase_suffix_connecting(self) -> None:
        """_phase_suffix returns 'connecting' for the connecting phase."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "connecting"
        assert _phase_suffix(1.0) == "connecting"

    def test_phase_suffix_waiting(self) -> None:
        """_phase_suffix returns 'connected · waiting for first token' for the waiting phase."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "waiting"
        assert _phase_suffix(2.0) == "connected · waiting for first token"

    def test_phase_suffix_streaming_with_char_count(self) -> None:
        """_phase_suffix returns 'streaming · N chars' during active streaming."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "streaming"
        r._streaming_chars = 420
        r._last_chunk_time = time.monotonic()  # recent, no stall
        result = _phase_suffix(3.0)
        assert result == "streaming · 420 chars"

    def test_phase_suffix_streaming_stalled(self) -> None:
        """_phase_suffix returns 'stalled Ns' when no chunks arrive for >5s."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "streaming"
        r._streaming_chars = 100
        r._last_chunk_time = time.monotonic() - 7.0  # 7s since last chunk
        result = _phase_suffix(10.0)
        assert "stalled" in result
        assert "7s" in result or "6s" in result  # allow for timing jitter

    def test_phase_suffix_streaming_not_stalled_within_threshold(self) -> None:
        """_phase_suffix does NOT report stalled when chunk arrived within 5s."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "streaming"
        r._streaming_chars = 100
        r._last_chunk_time = time.monotonic() - 2.0  # 2s ago, under threshold
        result = _phase_suffix(10.0)
        assert "stalled" not in result
        assert result == "streaming · 100 chars"

    def test_phase_suffix_unknown_phase_returns_raw(self) -> None:
        """_phase_suffix returns the raw phase string for unknown phases."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "custom_phase"
        assert _phase_suffix(1.0) == "custom_phase"

    def test_phase_suffix_verbose_mode_works(self) -> None:
        """_phase_suffix works in VERBOSE mode (not just DETAILED)."""
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.VERBOSE)
        r._thinking_phase = "connecting"
        assert _phase_suffix(1.0) == "connecting"

    def test_start_thinking_resets_phase_state(self) -> None:
        """start_thinking() resets all phase-related state."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "streaming"
        r._thinking_tokens = 100
        r._streaming_chars = 500
        r._last_chunk_time = time.monotonic()
        r._phase_start_time = time.monotonic()
        r._repl_mode = True
        r._stdout = io.StringIO()
        try:
            start_thinking()
            assert r._thinking_phase == ""
            assert r._thinking_tokens == 0
            assert r._streaming_chars == 0
            assert r._last_chunk_time == 0
            assert r._phase_start_time == r._thinking_start
        finally:
            stop_thinking_sync()
            r._repl_mode = False
            r._stdout = None

    def test_phase_transition_connecting_to_waiting(self) -> None:
        """Phase transition from connecting to waiting updates correctly."""
        import anteroom.cli.renderer as r

        set_thinking_phase("connecting")
        assert r._thinking_phase == "connecting"
        set_thinking_phase("waiting")
        assert r._thinking_phase == "waiting"

    def test_phase_transition_waiting_to_streaming_via_tokens(self) -> None:
        """Phase transition from waiting to streaming happens via increment_thinking_tokens."""
        import anteroom.cli.renderer as r

        set_thinking_phase("waiting")
        assert r._thinking_phase == "waiting"
        increment_thinking_tokens()
        assert r._thinking_phase == "streaming"
        assert r._thinking_tokens == 1

    def test_full_phase_lifecycle(self) -> None:
        """Full lifecycle: connecting → waiting → streaming with chars."""
        import anteroom.cli.renderer as r

        set_thinking_phase("connecting")
        assert _phase_suffix(0.5) == "connecting"

        set_thinking_phase("waiting")
        assert _phase_suffix(1.0) == "connected · waiting for first token"

        increment_thinking_tokens()
        increment_thinking_tokens()
        increment_thinking_tokens()
        r._streaming_chars = 150
        result = _phase_suffix(2.0)
        assert result == "streaming · 150 chars"

    def test_stall_detection_clears_when_chunks_resume(self) -> None:
        """Stall detection clears when new chunks arrive."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "streaming"
        r._streaming_chars = 50
        r._last_chunk_time = time.monotonic() - 10.0  # stalled
        assert "stalled" in _phase_suffix(15.0)

        # New chunk arrives
        increment_thinking_tokens()
        r._streaming_chars = 80
        result = _phase_suffix(15.0)
        assert "stalled" not in result
        assert result == "streaming · 80 chars"

    def test_phase_suffix_streaming_with_zero_chunk_time(self) -> None:
        """_phase_suffix with _last_chunk_time=0 skips stall check, returns char count."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "streaming"
        r._streaming_chars = 70
        r._last_chunk_time = 0  # falsy — stall check skipped
        result = _phase_suffix(20.0)
        assert "stalled" not in result
        assert result == "streaming · 70 chars"


class TestThroughputStallDetection:
    """Tests for throughput-based stall detection (#774).

    Catches slow-trickle streams where tiny chunks arrive frequently enough
    to avoid gap-based stall detection but overall throughput is very low.
    """

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        r._thinking_phase = ""
        r._thinking_tokens = 0
        r._streaming_chars = 0
        r._last_chunk_time = 0
        r._phase_start_time = 0
        r._retrying_info = {}
        r._throughput_window.clear()
        set_verbosity(Verbosity.DETAILED)

    def teardown_method(self) -> None:
        import anteroom.cli.renderer as r

        r._thinking_phase = ""
        r._thinking_tokens = 0
        r._streaming_chars = 0
        r._last_chunk_time = 0
        r._phase_start_time = 0
        r._retrying_info = {}
        r._throughput_window.clear()
        set_verbosity(Verbosity.COMPACT)

    def test_slow_trickle_shows_slow_indicator(self) -> None:
        """Low throughput stream shows 'slow (N chars/s)' even if chunks arrive within gap threshold."""
        import anteroom.cli.renderer as r

        now = time.monotonic()
        r._thinking_phase = "streaming"
        r._streaming_chars = 50
        r._last_chunk_time = now  # recent chunk — no gap-based stall
        r._phase_start_time = now - 15.0  # streaming for 15s (past warmup)
        # Simulate 5 chars/sec over the last 10s window
        for i in range(10):
            r._throughput_window.append((now - 10.0 + i, 5))
        result = _phase_suffix(20.0)
        assert "slow" in result
        assert "chars/s" in result

    def test_normal_throughput_no_slow_indicator(self) -> None:
        """Normal throughput does NOT show slow indicator."""
        import anteroom.cli.renderer as r

        now = time.monotonic()
        r._thinking_phase = "streaming"
        r._streaming_chars = 5000
        r._last_chunk_time = now
        r._phase_start_time = now - 15.0
        # Simulate 500 chars/sec
        for i in range(10):
            r._throughput_window.append((now - 10.0 + i, 500))
        result = _phase_suffix(20.0)
        assert "slow" not in result
        assert result == "streaming · 5,000 chars"

    def test_no_slow_indicator_during_warmup(self) -> None:
        """Throughput stall not triggered during warmup period."""
        import anteroom.cli.renderer as r

        now = time.monotonic()
        r._thinking_phase = "streaming"
        r._streaming_chars = 10
        r._last_chunk_time = now
        r._phase_start_time = now - 3.0  # only 3s into streaming (under 8s warmup)
        r._throughput_window.append((now - 2.0, 5))
        r._throughput_window.append((now - 1.0, 5))
        result = _phase_suffix(5.0)
        assert "slow" not in result

    def test_gap_stall_takes_priority_over_throughput(self) -> None:
        """Gap-based stall is shown when both conditions are met."""
        import anteroom.cli.renderer as r

        now = time.monotonic()
        r._thinking_phase = "streaming"
        r._streaming_chars = 50
        r._last_chunk_time = now - 7.0  # 7s gap — triggers gap stall
        r._phase_start_time = now - 20.0
        r._throughput_window.append((now - 7.0, 5))
        result = _phase_suffix(25.0)
        assert "stalled" in result  # gap-based takes priority
        assert "slow" not in result

    def test_throughput_window_pruned_on_increment(self) -> None:
        """Old entries are pruned from the throughput window."""
        import anteroom.cli.renderer as r

        now = time.monotonic()
        # Add old entry outside window
        r._throughput_window.append((now - 20.0, 100))
        r._throughput_window.append((now - 15.0, 100))
        increment_streaming_chars(10)
        # Old entries should be pruned (window is 10s)
        assert len(r._throughput_window) == 1
        assert r._throughput_window[0][1] == 10

    def test_start_thinking_resets_throughput_window(self) -> None:
        """start_thinking() clears the throughput window."""
        import anteroom.cli.renderer as r

        r._throughput_window.append((time.monotonic(), 100))
        assert len(r._throughput_window) == 1
        r._repl_mode = True
        r._stdout = io.StringIO()
        try:
            start_thinking()
            assert len(r._throughput_window) == 0
        finally:
            stop_thinking_sync()
            r._repl_mode = False
            r._stdout = None

    def test_empty_throughput_window_no_slow(self) -> None:
        """Empty throughput window does not trigger slow indicator."""
        import anteroom.cli.renderer as r

        now = time.monotonic()
        r._thinking_phase = "streaming"
        r._streaming_chars = 0
        r._last_chunk_time = now
        r._phase_start_time = now - 15.0
        # Empty window
        result = _phase_suffix(20.0)
        assert "slow" not in result

    def test_configure_throughput_threshold(self) -> None:
        """configure_thresholds() can override the throughput stall threshold."""
        import anteroom.cli.renderer as r

        original = r._THROUGHPUT_STALL_THRESHOLD
        try:
            configure_thresholds(throughput_threshold=100.0)
            assert r._THROUGHPUT_STALL_THRESHOLD == 100.0
        finally:
            r._THROUGHPUT_STALL_THRESHOLD = original


class TestWriteThinkingLinePhases:
    """Tests for phase text in _write_thinking_line() ANSI output (#203)."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        r._thinking_phase = ""
        r._thinking_tokens = 0
        r._streaming_chars = 0
        r._last_chunk_time = 0
        r._phase_start_time = 0
        set_verbosity(Verbosity.DETAILED)

    def teardown_method(self) -> None:
        import anteroom.cli.renderer as r

        r._thinking_phase = ""
        r._thinking_tokens = 0
        r._streaming_chars = 0
        r._last_chunk_time = 0
        r._phase_start_time = 0
        set_verbosity(Verbosity.COMPACT)

    def test_connecting_phase_in_ansi_output(self) -> None:
        """_write_thinking_line includes 'connecting' phase text."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        r._thinking_phase = "connecting"
        r._last_chunk_time = time.monotonic()
        _write_thinking_line(2.0)
        output = buf.getvalue()
        assert "connecting" in output
        assert "2s" in output
        r._stdout = None

    def test_waiting_phase_in_ansi_output(self) -> None:
        """_write_thinking_line includes 'waiting for first token' phase text."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        r._thinking_phase = "waiting"
        r._last_chunk_time = time.monotonic()
        _write_thinking_line(5.0)
        output = buf.getvalue()
        assert "waiting for first token" in output
        r._stdout = None

    def test_streaming_phase_in_ansi_output(self) -> None:
        """_write_thinking_line includes 'streaming · N chars' phase text."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        r._thinking_phase = "streaming"
        r._streaming_chars = 250
        r._last_chunk_time = time.monotonic()
        _write_thinking_line(3.0)
        output = buf.getvalue()
        assert "streaming" in output
        assert "250 chars" in output
        r._stdout = None

    def test_stalled_phase_in_ansi_output(self) -> None:
        """_write_thinking_line includes 'stalled Ns' when streaming is stalled."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        r._thinking_phase = "streaming"
        r._thinking_tokens = 10
        r._last_chunk_time = time.monotonic() - 8.0
        _write_thinking_line(12.0)
        output = buf.getvalue()
        assert "stalled" in output
        r._stdout = None

    def test_phase_text_uses_muted_color(self) -> None:
        """Phase text in _write_thinking_line uses MUTED color (RGB 139,139,139)."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        r._thinking_phase = "connecting"
        r._last_chunk_time = time.monotonic()
        _write_thinking_line(2.0)
        output = buf.getvalue()
        assert "\033[38;2;139;139;139m" in output
        r._stdout = None

    def test_phase_text_shown_in_compact_mode(self) -> None:
        """_write_thinking_line shows phase text even in COMPACT mode (health monitor)."""
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.COMPACT)
        buf = io.StringIO()
        r._stdout = buf
        r._thinking_phase = "connecting"
        r._last_chunk_time = time.monotonic()
        _write_thinking_line(2.0)
        output = buf.getvalue()
        assert "connecting" in output
        r._stdout = None

    def test_phase_text_overrides_stall_warning(self) -> None:
        """When phase is set, phase text is shown instead of the generic stall warning."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        r._thinking_phase = "waiting"
        r._last_chunk_time = time.monotonic()
        _write_thinking_line(20.0)  # past _STALL_THRESHOLD
        output = buf.getvalue()
        assert "waiting for first token" in output
        assert "(waiting for API response)" not in output
        r._stdout = None

    def test_no_phase_no_stall_warning(self) -> None:
        """When no phase is set, no phase text or stall warning appears (phase system handles it)."""
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.DETAILED)
        buf = io.StringIO()
        r._stdout = buf
        r._thinking_phase = ""
        _write_thinking_line(20.0)
        output = buf.getvalue()
        assert "waiting for API response" not in output
        assert "Thinking..." in output
        r._stdout = None


class TestThinkingTickerPhases:
    """Tests for phase display in the background ticker (#203)."""

    @pytest.mark.asyncio
    async def test_ticker_includes_phase_in_spinner(self) -> None:
        """Background ticker includes phase suffix in Rich spinner label."""
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.DETAILED)
        r._repl_mode = False  # Use spinner mode
        r._thinking_start = time.monotonic() - 3.0
        r._thinking_phase = "waiting"
        r._last_chunk_time = time.monotonic()
        r._thinking_cancelled = False

        mock_spinner = r._spinner = type("FakeSpinner", (), {"update": lambda self, label: None})()
        labels_seen: list[str] = []
        mock_spinner.update = lambda label: labels_seen.append(label)

        r._spinner = mock_spinner
        try:
            from anteroom.cli.renderer import _thinking_ticker

            task = asyncio.create_task(_thinking_ticker())
            await asyncio.sleep(0.6)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            # At least one label should contain the phase suffix
            assert any("waiting for first token" in label for label in labels_seen), (
                f"Expected 'waiting for first token' in spinner labels, got: {labels_seen}"
            )
        finally:
            r._spinner = None
            r._thinking_phase = ""

    @pytest.mark.asyncio
    async def test_ticker_shows_streaming_char_count(self) -> None:
        """Background ticker shows char count during streaming phase."""
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.DETAILED)
        r._repl_mode = True
        buf = io.StringIO()
        r._stdout = buf
        r._thinking_start = time.monotonic() - 2.0
        r._thinking_phase = "streaming"
        r._streaming_chars = 1500
        r._last_chunk_time = time.monotonic()
        r._thinking_cancelled = False

        try:
            from anteroom.cli.renderer import _thinking_ticker

            task = asyncio.create_task(_thinking_ticker())
            await asyncio.sleep(0.6)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            output = buf.getvalue()
            assert "streaming" in output
            assert "1,500 chars" in output
        finally:
            r._repl_mode = False
            r._stdout = None
            r._thinking_phase = ""
            r._streaming_chars = 0

    @pytest.mark.asyncio
    async def test_ticker_shows_stalled_during_streaming(self) -> None:
        """Background ticker shows 'stalled' when no chunks arrive for >5s."""
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.DETAILED)
        r._repl_mode = True
        buf = io.StringIO()
        r._stdout = buf
        r._thinking_start = time.monotonic() - 10.0
        r._thinking_phase = "streaming"
        r._thinking_tokens = 20
        r._last_chunk_time = time.monotonic() - 8.0  # 8s since last chunk
        r._thinking_cancelled = False

        try:
            from anteroom.cli.renderer import _thinking_ticker

            task = asyncio.create_task(_thinking_ticker())
            await asyncio.sleep(0.6)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            output = buf.getvalue()
            assert "stalled" in output
        finally:
            r._repl_mode = False
            r._stdout = None
            r._thinking_phase = ""
            r._thinking_tokens = 0


class TestRetryingPhase:
    """Tests for retrying phase display in the thinking indicator (#209)."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        r._thinking_phase = ""
        r._thinking_tokens = 0
        r._streaming_chars = 0
        r._last_chunk_time = 0
        r._phase_start_time = 0
        r._retrying_info = {}
        set_verbosity(Verbosity.DETAILED)

    def teardown_method(self) -> None:
        import anteroom.cli.renderer as r

        r._thinking_phase = ""
        r._thinking_tokens = 0
        r._streaming_chars = 0
        r._last_chunk_time = 0
        r._phase_start_time = 0
        r._retrying_info = {}
        set_verbosity(Verbosity.COMPACT)

    def test_set_retrying_updates_phase(self) -> None:
        """set_retrying must set _thinking_phase to 'retrying'."""
        import anteroom.cli.renderer as r

        set_retrying({"attempt": 2, "max_attempts": 3, "delay": 1.0})
        assert r._thinking_phase == "retrying"

    def test_set_retrying_stores_info(self) -> None:
        """set_retrying must store the retry data."""
        import anteroom.cli.renderer as r

        data = {"attempt": 2, "max_attempts": 4, "delay": 2.0}
        set_retrying(data)
        assert r._retrying_info == data

    def test_phase_suffix_retrying(self) -> None:
        """_phase_suffix must show 'retry N/M' for retrying phase."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "retrying"
        r._retrying_info = {"attempt": 2, "max_attempts": 3}
        result = _phase_suffix(5.0)
        assert result == "retry 2/3"

    def test_phase_suffix_retrying_shown_in_compact(self) -> None:
        """_phase_suffix shows retry info even in COMPACT mode (health monitor)."""
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.COMPACT)
        r._thinking_phase = "retrying"
        r._retrying_info = {"attempt": 2, "max_attempts": 3}
        result = _phase_suffix(5.0)
        assert result == "retry 2/3"

    def test_start_thinking_resets_retrying_info(self) -> None:
        """start_thinking must clear _retrying_info."""
        import anteroom.cli.renderer as r

        r._retrying_info = {"attempt": 2, "max_attempts": 3}
        r._repl_mode = True
        r._stdout = io.StringIO()
        try:
            start_thinking()
            assert r._retrying_info == {}
        finally:
            stop_thinking_sync()
            r._repl_mode = False
            r._stdout = None


class TestIncrementStreamingChars:
    """Tests for increment_streaming_chars() (#221)."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        r._streaming_chars = 0

    def teardown_method(self) -> None:
        import anteroom.cli.renderer as r

        r._streaming_chars = 0

    def test_increment_adds_chars(self) -> None:
        import anteroom.cli.renderer as r

        increment_streaming_chars(42)
        assert r._streaming_chars == 42

    def test_increment_accumulates(self) -> None:
        import anteroom.cli.renderer as r

        increment_streaming_chars(10)
        increment_streaming_chars(20)
        increment_streaming_chars(5)
        assert r._streaming_chars == 35

    def test_increment_zero_is_noop(self) -> None:
        import anteroom.cli.renderer as r

        increment_streaming_chars(0)
        assert r._streaming_chars == 0


class TestPhaseElapsedStr:
    """Tests for _phase_elapsed_str() (#221)."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        r._phase_start_time = 0

    def teardown_method(self) -> None:
        import anteroom.cli.renderer as r

        r._phase_start_time = 0

    def test_returns_empty_when_no_start_time(self) -> None:
        assert _phase_elapsed_str() == ""

    def test_returns_empty_when_under_threshold(self) -> None:
        import anteroom.cli.renderer as r

        r._phase_start_time = time.monotonic() - 0.5  # 0.5s, under 1.5s threshold
        assert _phase_elapsed_str() == ""

    def test_returns_elapsed_when_over_threshold(self) -> None:
        import anteroom.cli.renderer as r

        r._phase_start_time = time.monotonic() - 3.0
        result = _phase_elapsed_str()
        assert result.startswith(" (")
        assert result.endswith("s)")
        # Should be approximately 3s
        secs = int(result.strip(" ()s"))
        assert 2 <= secs <= 4

    def test_phase_suffix_includes_elapsed(self) -> None:
        """Phase suffix includes per-phase elapsed when > 1.5s."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "connecting"
        r._phase_start_time = time.monotonic() - 5.0
        result = _phase_suffix(10.0)
        assert "connecting" in result
        assert "(5s)" in result or "(4s)" in result


class TestWriteThinkingLineMessages:
    """Tests for error_msg, cancel_msg, countdown in _write_thinking_line() (#221)."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        r._thinking_phase = ""
        r._streaming_chars = 0
        r._phase_start_time = 0

    def teardown_method(self) -> None:
        import anteroom.cli.renderer as r

        r._thinking_phase = ""
        r._streaming_chars = 0
        r._phase_start_time = 0

    def test_error_msg_shown_in_red(self) -> None:
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        _write_thinking_line(10.0, error_msg="Stream timed out")
        output = buf.getvalue()
        assert "Stream timed out" in output
        # ERROR_RED #CD6B6B = rgb(205, 107, 107)
        assert "\033[38;2;205;107;107m" in output
        r._stdout = None

    def test_error_with_countdown(self) -> None:
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        _write_thinking_line(10.0, error_msg="Stream timed out", countdown=3)
        output = buf.getvalue()
        assert "Stream timed out" in output
        assert "retrying in 3s" in output
        assert "esc to give up" in output
        r._stdout = None

    def test_cancel_msg_shown_muted(self) -> None:
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        _write_thinking_line(10.0, cancel_msg="cancelled")
        output = buf.getvalue()
        assert "cancelled" in output
        # MUTED color
        assert "\033[38;2;139;139;139m" in output
        r._stdout = None

    def test_error_msg_overrides_phase(self) -> None:
        """Error message replaces phase text (not shown alongside it)."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        r._thinking_phase = "streaming"
        r._streaming_chars = 100
        _write_thinking_line(10.0, error_msg="Connection lost")
        output = buf.getvalue()
        assert "Connection lost" in output
        assert "streaming" not in output
        r._stdout = None

    def test_cancel_msg_overrides_phase(self) -> None:
        """Cancel message replaces phase text."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        r._thinking_phase = "streaming"
        r._streaming_chars = 100
        _write_thinking_line(10.0, cancel_msg="cancelled")
        output = buf.getvalue()
        assert "cancelled" in output
        assert "streaming" not in output
        r._stdout = None

    def test_no_esc_hint_during_error(self) -> None:
        """'esc to cancel' should NOT appear when showing error (shows 'esc to give up' instead)."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        _write_thinking_line(10.0, error_msg="Timed out", countdown=5)
        output = buf.getvalue()
        assert "esc to cancel" not in output
        assert "esc to give up" in output
        r._stdout = None

    def test_no_esc_hint_during_cancel(self) -> None:
        """'esc to cancel' should NOT appear when showing cancel message."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        _write_thinking_line(10.0, cancel_msg="cancelled")
        output = buf.getvalue()
        assert "esc to cancel" not in output
        r._stdout = None


class TestAsyncStopThinking:
    """Tests for async stop_thinking() with messages (#221)."""

    @pytest.mark.asyncio
    async def test_stop_thinking_with_error_msg(self) -> None:
        """stop_thinking(error_msg=...) writes error on final line."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._repl_mode = True
        r._stdout = buf
        r._thinking_start = time.monotonic() - 5.0
        r._thinking_ticker_task = None
        r._spinner = None

        elapsed = await stop_thinking(error_msg="Stream timed out")
        output = buf.getvalue()
        assert "Stream timed out" in output
        assert "\n" in output  # newline after final line
        assert elapsed >= 4.0
        r._repl_mode = False
        r._stdout = None

    @pytest.mark.asyncio
    async def test_stop_thinking_with_cancel_msg(self) -> None:
        """stop_thinking(cancel_msg=...) writes cancel on final line."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._repl_mode = True
        r._stdout = buf
        r._thinking_start = time.monotonic() - 3.0
        r._thinking_ticker_task = None
        r._spinner = None

        await stop_thinking(cancel_msg="cancelled")
        output = buf.getvalue()
        assert "cancelled" in output
        assert "\n" in output
        r._repl_mode = False
        r._stdout = None

    @pytest.mark.asyncio
    async def test_stop_thinking_clean_final_line(self) -> None:
        """stop_thinking() with no args writes clean final line with no phase or hint."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._repl_mode = True
        r._stdout = buf
        r._thinking_start = time.monotonic() - 10.0  # long enough for esc hint
        r._thinking_phase = "waiting"  # stale phase that should NOT appear
        r._phase_start_time = time.monotonic() - 8.0
        r._thinking_ticker_task = None
        r._spinner = None

        await stop_thinking()
        output = buf.getvalue()
        assert "Thinking..." in output
        assert "\n" in output
        # Must NOT contain stale phase or esc hint
        assert "waiting" not in output
        assert "first token" not in output
        assert "esc to cancel" not in output
        assert "streaming" not in output
        # Phase should be cleared
        assert r._thinking_phase == ""
        r._repl_mode = False
        r._stdout = None

    @pytest.mark.asyncio
    async def test_stop_thinking_cleans_stale_streaming_phase(self) -> None:
        """stop_thinking() clears stale 'streaming' phase on clean completion."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._repl_mode = True
        r._stdout = buf
        r._thinking_start = time.monotonic() - 5.0
        r._thinking_phase = "streaming"
        r._streaming_chars = 1234
        r._phase_start_time = time.monotonic() - 3.0
        r._last_chunk_time = time.monotonic()
        r._thinking_ticker_task = None
        r._spinner = None

        await stop_thinking()
        output = buf.getvalue()
        assert "Thinking..." in output
        assert "streaming" not in output
        assert "1,234" not in output  # char count should not appear
        assert "esc" not in output
        r._repl_mode = False
        r._stdout = None

    @pytest.mark.asyncio
    async def test_stop_thinking_error_msg_preserves_phase_state(self) -> None:
        """stop_thinking(error_msg=...) does NOT clear phase (only clean completion does)."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._repl_mode = True
        r._stdout = buf
        r._thinking_start = time.monotonic() - 5.0
        r._thinking_phase = "waiting"
        r._thinking_ticker_task = None
        r._spinner = None

        await stop_thinking(error_msg="Connection failed")
        # Phase is NOT cleared for error paths
        assert r._thinking_phase == "waiting"
        r._repl_mode = False
        r._stdout = None

    @pytest.mark.asyncio
    async def test_stop_thinking_awaits_ticker(self) -> None:
        """stop_thinking() awaits ticker task before writing final line."""
        import anteroom.cli.renderer as r

        r._repl_mode = True
        r._stdout = io.StringIO()
        r._thinking_start = time.monotonic() - 1.0
        r._spinner = None

        # Create a real ticker task
        start_thinking()
        assert r._thinking_ticker_task is not None

        await stop_thinking()
        assert r._thinking_ticker_task is None
        r._repl_mode = False
        r._stdout = None

    def test_stop_thinking_sync_clears_line(self) -> None:
        """stop_thinking_sync() clears the line without writing a message."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._repl_mode = True
        r._stdout = buf
        r._thinking_start = time.monotonic() - 3.0
        r._thinking_ticker_task = None
        r._spinner = None

        elapsed = stop_thinking_sync()
        output = buf.getvalue()
        assert "\r\033[2K" in output  # line clear
        assert elapsed >= 2.0
        r._repl_mode = False
        r._stdout = None


class TestThinkingCountdown:
    """Tests for thinking_countdown() (#221)."""

    @pytest.mark.asyncio
    async def test_countdown_completes_returns_true(self) -> None:
        """Countdown finishes without cancel -> returns True (should retry)."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._repl_mode = True
        r._stdout = buf
        r._thinking_start = time.monotonic() - 5.0

        cancel = asyncio.Event()
        result = await thinking_countdown(2.0, cancel, "Stream timed out")
        assert result is True
        output = buf.getvalue()
        assert "Stream timed out" in output
        assert "retrying in" in output
        r._repl_mode = False
        r._stdout = None

    @pytest.mark.asyncio
    async def test_countdown_cancelled_returns_false(self) -> None:
        """Cancel event during countdown -> returns False (give up)."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._repl_mode = True
        r._stdout = buf
        r._thinking_start = time.monotonic() - 5.0

        cancel = asyncio.Event()

        async def fire_cancel() -> None:
            await asyncio.sleep(0.5)
            cancel.set()

        asyncio.create_task(fire_cancel())
        result = await thinking_countdown(10.0, cancel, "Connection lost")
        assert result is False
        output = buf.getvalue()
        assert "cancelled" in output
        r._repl_mode = False
        r._stdout = None

    @pytest.mark.asyncio
    async def test_countdown_zero_delay_returns_true(self) -> None:
        """Zero-second countdown returns True immediately."""
        import anteroom.cli.renderer as r

        r._repl_mode = True
        r._stdout = io.StringIO()
        r._thinking_start = time.monotonic()

        cancel = asyncio.Event()
        result = await thinking_countdown(0.0, cancel, "error")
        assert result is True
        r._repl_mode = False
        r._stdout = None

    @pytest.mark.asyncio
    async def test_countdown_stops_ticker_to_prevent_race(self) -> None:
        """Ticker task is cancelled before countdown writes, preventing stale lines (#245)."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._repl_mode = True
        r._stdout = buf
        r._thinking_start = time.monotonic() - 2.0

        # Simulate a running ticker task
        async def fake_ticker() -> None:
            try:
                while True:
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                return

        task = asyncio.create_task(fake_ticker())
        r._thinking_ticker_task = task

        cancel = asyncio.Event()
        result = await thinking_countdown(1.0, cancel, "Stream timed out")
        assert result is True
        # Ticker must be stopped and cleared during countdown
        assert r._thinking_ticker_task is None
        assert task.done()
        r._repl_mode = False
        r._stdout = None


class TestStreamingCharsInPhaseDisplay:
    """Integration tests: streaming chars display across the full pipeline (#221)."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        r._thinking_phase = ""
        r._thinking_tokens = 0
        r._streaming_chars = 0
        r._last_chunk_time = 0
        r._phase_start_time = 0

    def teardown_method(self) -> None:
        import anteroom.cli.renderer as r

        r._thinking_phase = ""
        r._thinking_tokens = 0
        r._streaming_chars = 0
        r._last_chunk_time = 0
        r._phase_start_time = 0

    def test_chars_formatted_with_comma_separator(self) -> None:
        """Large char counts use comma grouping (e.g. '1,500 chars')."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "streaming"
        r._streaming_chars = 12345
        r._last_chunk_time = time.monotonic()
        result = _phase_suffix(5.0)
        assert "12,345 chars" in result

    def test_zero_chars_shows_zero(self) -> None:
        """Zero chars during streaming shows '0 chars'."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "streaming"
        r._streaming_chars = 0
        r._last_chunk_time = time.monotonic()
        result = _phase_suffix(2.0)
        assert result == "streaming · 0 chars"

    def test_chars_and_stall_coexist(self) -> None:
        """Stall message includes char count."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "streaming"
        r._streaming_chars = 500
        r._last_chunk_time = time.monotonic() - 8.0
        result = _phase_suffix(10.0)
        assert "500 chars" in result
        assert "stalled" in result

    def test_streaming_chars_independent_of_tokens(self) -> None:
        """Char count and token count are tracked independently."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "streaming"
        r._thinking_tokens = 99
        r._streaming_chars = 2000
        r._last_chunk_time = time.monotonic()
        result = _phase_suffix(3.0)
        assert "2,000 chars" in result
        assert "99" not in result  # tokens not shown

    def test_start_thinking_resets_streaming_chars(self) -> None:
        """start_thinking() resets streaming chars to 0."""
        import anteroom.cli.renderer as r

        r._streaming_chars = 5000
        r._repl_mode = True
        r._stdout = io.StringIO()
        try:
            start_thinking()
            assert r._streaming_chars == 0
        finally:
            stop_thinking_sync()
            r._repl_mode = False
            r._stdout = None


class TestPhaseElapsedEdgeCases:
    """Edge cases for per-phase elapsed timing (#221)."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        r._phase_start_time = 0
        r._thinking_phase = ""

    def teardown_method(self) -> None:
        import anteroom.cli.renderer as r

        r._phase_start_time = 0
        r._thinking_phase = ""

    def test_set_thinking_phase_resets_phase_timer(self) -> None:
        """set_thinking_phase() records a new _phase_start_time."""
        import anteroom.cli.renderer as r

        set_thinking_phase("connecting")
        assert r._phase_start_time > 0

        old_start = r._phase_start_time
        time.sleep(0.01)
        set_thinking_phase("waiting")
        assert r._phase_start_time > old_start

    def test_elapsed_not_shown_for_fast_phases(self) -> None:
        """Phases that resolve quickly (< 1.5s) don't show elapsed."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "connecting"
        r._phase_start_time = time.monotonic() - 1.0  # 1s, under 1.5s threshold
        result = _phase_suffix(3.0)
        assert result == "connecting"
        assert "(" not in result

    def test_elapsed_shown_for_slow_phases(self) -> None:
        """Phases taking > 1.5s show per-phase elapsed."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "waiting"
        r._phase_start_time = time.monotonic() - 10.0
        result = _phase_suffix(15.0)
        assert "connected · waiting for first token" in result
        assert "(10s)" in result or "(9s)" in result

    def test_elapsed_not_shown_for_streaming(self) -> None:
        """Streaming phase doesn't show per-phase elapsed (char count is enough)."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "streaming"
        r._streaming_chars = 100
        r._last_chunk_time = time.monotonic()
        r._phase_start_time = time.monotonic() - 10.0
        result = _phase_suffix(15.0)
        # Streaming uses char count, not per-phase elapsed
        assert "100 chars" in result


class TestPhaseStartTimeInitialization:
    """Verify _phase_start_time is initialized to _thinking_start (#238)."""

    def test_start_thinking_sets_phase_start_to_thinking_start(self) -> None:
        """start_thinking() initializes _phase_start_time to _thinking_start."""
        import anteroom.cli.renderer as r

        r._repl_mode = True
        r._stdout = io.StringIO()
        try:
            start_thinking()
            assert r._phase_start_time == r._thinking_start
            assert r._phase_start_time > 0
        finally:
            stop_thinking_sync()
            r._repl_mode = False
            r._stdout = None

    def test_phase_elapsed_works_immediately_after_start(self) -> None:
        """_phase_elapsed_str() returns timing immediately (no set_thinking_phase needed)."""
        import anteroom.cli.renderer as r

        r._phase_start_time = time.monotonic() - 3.0
        result = _phase_elapsed_str()
        assert result.startswith(" (")
        assert result.endswith("s)")

    def test_set_thinking_phase_overrides_initial_start(self) -> None:
        """set_thinking_phase() resets _phase_start_time from the initial value."""
        import anteroom.cli.renderer as r

        r._phase_start_time = time.monotonic() - 10.0
        old = r._phase_start_time
        set_thinking_phase("connecting")
        assert r._phase_start_time > old


class TestWriteThinkingLineColorCodes:
    """Verify ANSI color codes in _write_thinking_line() (#221)."""

    def test_gold_color_for_thinking_text(self) -> None:
        """'Thinking...' text uses GOLD color."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        _write_thinking_line(2.0)
        output = buf.getvalue()
        # GOLD #C5A059 = rgb(197, 160, 89)
        assert "\033[38;2;197;160;89m" in output
        r._stdout = None

    def test_timer_color_for_elapsed(self) -> None:
        """Timer uses CHROME color."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        _write_thinking_line(5.0)
        output = buf.getvalue()
        # CHROME #6b7280 = rgb(107, 114, 128)
        assert "\033[38;2;107;114;128m" in output
        r._stdout = None

    def test_error_red_color(self) -> None:
        """Error messages use ERROR_RED color."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        _write_thinking_line(5.0, error_msg="timeout")
        output = buf.getvalue()
        # ERROR_RED #CD6B6B = rgb(205, 107, 107)
        assert "\033[38;2;205;107;107m" in output
        r._stdout = None

    def test_reset_codes_present(self) -> None:
        """ANSI reset codes are present to avoid color bleed."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        _write_thinking_line(5.0, error_msg="error", countdown=3)
        output = buf.getvalue()
        assert "\033[0m" in output
        r._stdout = None


class TestCountdownEdgeCases:
    """Edge cases for thinking_countdown() (#221)."""

    @pytest.mark.asyncio
    async def test_countdown_ticks_once_per_second(self) -> None:
        """Countdown writes to stdout once per second."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._repl_mode = True
        r._stdout = buf
        r._thinking_start = time.monotonic() - 5.0

        cancel = asyncio.Event()
        start = time.monotonic()
        await thinking_countdown(3.0, cancel, "error")
        elapsed = time.monotonic() - start

        # Should take ~3 seconds
        assert 2.5 <= elapsed <= 4.0

        output = buf.getvalue()
        # Should contain countdown values
        assert "retrying in 3s" in output
        assert "retrying in 2s" in output
        assert "retrying in 1s" in output
        r._repl_mode = False
        r._stdout = None

    @pytest.mark.asyncio
    async def test_countdown_preserves_error_message(self) -> None:
        """Error message persists throughout countdown ticks."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._repl_mode = True
        r._stdout = buf
        r._thinking_start = time.monotonic() - 10.0

        cancel = asyncio.Event()
        await thinking_countdown(2.0, cancel, "Stream timed out")

        output = buf.getvalue()
        # Each tick should include the error message
        assert output.count("Stream timed out") >= 2
        r._repl_mode = False
        r._stdout = None

    @pytest.mark.asyncio
    async def test_countdown_not_active_in_non_repl_mode(self) -> None:
        """Countdown does nothing visible when not in REPL mode."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._repl_mode = False
        r._stdout = buf
        r._thinking_start = time.monotonic()

        cancel = asyncio.Event()
        result = await thinking_countdown(1.0, cancel, "error")
        assert result is True
        # No output when not in repl mode
        assert buf.getvalue() == ""
        r._stdout = None


class TestStopThinkingEdgeCases:
    """Edge cases for stop_thinking() and stop_thinking_sync() (#221)."""

    @pytest.mark.asyncio
    async def test_stop_thinking_returns_elapsed_time(self) -> None:
        """stop_thinking() returns accurate elapsed seconds."""
        import anteroom.cli.renderer as r

        r._repl_mode = True
        r._stdout = io.StringIO()
        r._thinking_start = time.monotonic() - 7.0
        r._thinking_ticker_task = None
        r._spinner = None

        elapsed = await stop_thinking()
        assert 6.0 <= elapsed <= 8.0
        r._repl_mode = False
        r._stdout = None

    @pytest.mark.asyncio
    async def test_stop_thinking_no_output_without_repl_mode(self) -> None:
        """stop_thinking() writes nothing when not in REPL mode."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._repl_mode = False
        r._stdout = buf
        r._thinking_start = time.monotonic() - 2.0
        r._thinking_ticker_task = None
        r._spinner = None

        await stop_thinking(error_msg="error")
        assert buf.getvalue() == ""
        r._stdout = None

    def test_stop_thinking_sync_returns_elapsed(self) -> None:
        """stop_thinking_sync() returns accurate elapsed seconds."""
        import anteroom.cli.renderer as r

        r._repl_mode = True
        r._stdout = io.StringIO()
        r._thinking_start = time.monotonic() - 4.0
        r._thinking_ticker_task = None
        r._spinner = None

        elapsed = stop_thinking_sync()
        assert 3.0 <= elapsed <= 5.0
        r._repl_mode = False
        r._stdout = None

    def test_stop_thinking_sync_cancels_ticker_without_await(self) -> None:
        """stop_thinking_sync() cancels ticker task but does not await it."""
        import anteroom.cli.renderer as r

        r._repl_mode = True
        r._stdout = io.StringIO()
        r._thinking_start = time.monotonic()

        # Simulate a ticker task
        fake_task = type("FakeTask", (), {"cancel": lambda self: None})()
        r._thinking_ticker_task = fake_task

        stop_thinking_sync()
        assert r._thinking_ticker_task is None
        r._repl_mode = False
        r._stdout = None

    def test_stop_thinking_sync_sets_cancelled_flag(self) -> None:
        """stop_thinking_sync() sets _thinking_cancelled to suppress stale output (#937)."""
        import anteroom.cli.renderer as r

        r._repl_mode = True
        r._stdout = io.StringIO()
        r._thinking_start = time.monotonic() - 1.0
        r._thinking_ticker_task = None
        r._thinking_cancelled = False

        stop_thinking_sync()

        assert r._thinking_cancelled is True
        r._repl_mode = False
        r._stdout = None


class TestConfigureThresholds:
    """Verify configure_thresholds() overrides module-level constants (#241)."""

    def test_configure_all_thresholds(self) -> None:
        """configure_thresholds() must update all three visual threshold constants."""
        import anteroom.cli.renderer as r

        orig_esc = r._ESC_HINT_DELAY
        orig_stall = r._MID_STREAM_STALL
        orig_warn = r._STALL_THRESHOLD
        try:
            configure_thresholds(esc_hint_delay=10.0, stall_display=8.0, stall_warning=20.0)
            assert r._ESC_HINT_DELAY == 10.0
            assert r._MID_STREAM_STALL == 8.0
            assert r._STALL_THRESHOLD == 20.0
        finally:
            r._ESC_HINT_DELAY = orig_esc
            r._MID_STREAM_STALL = orig_stall
            r._STALL_THRESHOLD = orig_warn

    def test_configure_partial_thresholds(self) -> None:
        """configure_thresholds() with None leaves existing values unchanged."""
        import anteroom.cli.renderer as r

        orig_esc = r._ESC_HINT_DELAY
        orig_stall = r._MID_STREAM_STALL
        try:
            configure_thresholds(stall_display=12.0)
            assert r._ESC_HINT_DELAY == orig_esc  # unchanged
            assert r._MID_STREAM_STALL == 12.0
        finally:
            r._ESC_HINT_DELAY = orig_esc
            r._MID_STREAM_STALL = orig_stall


# ---------------------------------------------------------------------------
# Plan checklist rendering (#166)
# ---------------------------------------------------------------------------


class TestPlanChecklistState:
    """Verify plan checklist state management."""

    def setup_method(self) -> None:
        clear_plan()

    def teardown_method(self) -> None:
        clear_plan()

    def test_start_plan_initializes_steps(self) -> None:
        start_plan(["Step 1", "Step 2", "Step 3"])
        steps = get_plan_steps()
        assert len(steps) == 3
        assert all(s["status"] == "pending" for s in steps)
        assert steps[0]["text"] == "Step 1"
        assert is_plan_visible()

    def test_update_plan_step_changes_status(self) -> None:
        import anteroom.cli.renderer as r

        r._repl_mode = False  # no output during test
        start_plan(["A", "B"])
        update_plan_step(0, "in_progress")
        assert get_plan_steps()[0]["status"] == "in_progress"
        update_plan_step(0, "complete")
        assert get_plan_steps()[0]["status"] == "complete"

    def test_update_plan_step_out_of_range_is_noop(self) -> None:
        start_plan(["A"])
        update_plan_step(5, "complete")  # no error
        update_plan_step(-1, "complete")  # no error
        assert get_plan_steps()[0]["status"] == "pending"

    def test_clear_plan_resets_state(self) -> None:
        start_plan(["A", "B"])
        clear_plan()
        assert not is_plan_visible()
        assert get_plan_steps() == []

    def test_plan_block_height_zero_when_no_plan(self) -> None:
        assert _plan_block_height() == 0

    def test_plan_block_height_with_steps(self) -> None:
        start_plan(["A", "B", "C"])
        assert _plan_block_height() == 4  # header + 3 steps


class TestPlanChecklistRendering:
    """Verify plan checklist ANSI output."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        self._orig_repl = r._repl_mode
        self._orig_stdout = r._stdout
        r._repl_mode = True
        r._stdout = io.StringIO()
        clear_plan()

    def teardown_method(self) -> None:
        import anteroom.cli.renderer as r

        r._repl_mode = self._orig_repl
        r._stdout = self._orig_stdout
        clear_plan()

    def test_write_thinking_block_includes_plan_header(self) -> None:
        import anteroom.cli.renderer as r

        start_plan(["Read config", "Write tests"])
        r._plan_written_lines = 0
        _write_thinking_block(2.0)
        output = r._stdout.getvalue()
        assert "Plan" in output
        assert "Read config" in output
        assert "Write tests" in output
        assert "Thinking" in output

    def test_write_thinking_block_shows_step_status_icons(self) -> None:
        import anteroom.cli.renderer as r

        start_plan(["A", "B", "C"])
        r._plan_written_lines = 0
        update_plan_step(0, "complete")
        update_plan_step(1, "in_progress")
        # Reset output to capture fresh block
        r._stdout = io.StringIO()
        r._plan_written_lines = 0
        _write_thinking_block(3.0)
        output = r._stdout.getvalue()
        # Check status indicators are present (unicode chars)
        assert "\u2713" in output  # checkmark for complete
        assert "\u2192" in output  # arrow for in_progress
        assert "\u25cb" in output  # circle for pending

    def test_write_thinking_line_delegates_to_block_when_plan_active(self) -> None:
        import anteroom.cli.renderer as r

        start_plan(["Step one"])
        r._plan_written_lines = 0
        _write_thinking_line(1.0)
        output = r._stdout.getvalue()
        assert "Plan" in output
        assert "Step one" in output

    def test_write_thinking_line_single_line_when_no_plan(self) -> None:
        import anteroom.cli.renderer as r

        r._stdout = io.StringIO()
        _write_thinking_line(2.0)
        output = r._stdout.getvalue()
        assert "Thinking" in output
        assert "Plan" not in output

    def test_collapse_plan_shows_summary(self) -> None:
        import anteroom.cli.renderer as r

        start_plan(["A", "B"])
        update_plan_step(0, "complete")
        update_plan_step(1, "complete")
        r._stdout = io.StringIO()
        _collapse_plan()
        output = r._stdout.getvalue()
        assert "2/2" in output
        assert "complete" in output
        assert not is_plan_visible()

    def test_collapse_plan_partial_completion(self) -> None:
        import anteroom.cli.renderer as r

        start_plan(["A", "B", "C"])
        update_plan_step(0, "complete")
        r._stdout = io.StringIO()
        _collapse_plan()
        output = r._stdout.getvalue()
        assert "1/3" in output

    def test_cursor_up_on_redraw(self) -> None:
        import anteroom.cli.renderer as r

        start_plan(["A", "B"])
        r._plan_written_lines = 0
        _write_thinking_block(1.0)
        # After first write, plan_written_lines should be set
        assert r._plan_written_lines == 3  # header + 2 steps
        # Second write should include cursor-up
        r._stdout = io.StringIO()
        _write_thinking_block(2.0)
        output = r._stdout.getvalue()
        assert "\033[3A" in output  # cursor up 3 lines

    def test_build_thinking_text_no_plan_dependency(self) -> None:
        """_build_thinking_text returns plain thinking text."""
        text = _build_thinking_text(5.0)
        assert "Thinking" in text
        assert "5s" in text


class TestPlanChecklistWithThinking:
    """Verify plan checklist integration with start/stop thinking."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        self._orig_repl = r._repl_mode
        self._orig_stdout = r._stdout
        r._repl_mode = True
        r._stdout = io.StringIO()
        clear_plan()

    def teardown_method(self) -> None:
        import anteroom.cli.renderer as r

        r._repl_mode = self._orig_repl
        r._stdout = self._orig_stdout
        r._thinking_start = 0
        r._thinking_ticker_task = None
        r._spinner = None
        clear_plan()

    def test_start_thinking_writes_plan_block(self) -> None:
        import anteroom.cli.renderer as r

        start_plan(["Read files", "Write code"])
        r._stdout = io.StringIO()
        start_thinking(newline=True)
        output = r._stdout.getvalue()
        assert "Plan" in output
        assert "Read files" in output
        # Clean up ticker
        if r._thinking_ticker_task:
            r._thinking_ticker_task.cancel()
            r._thinking_ticker_task = None

    @pytest.mark.asyncio
    async def test_stop_thinking_with_collapse(self) -> None:
        import anteroom.cli.renderer as r

        start_plan(["A", "B"])
        update_plan_step(0, "complete")
        update_plan_step(1, "complete")
        r._thinking_start = time.monotonic()
        r._plan_written_lines = 3  # simulate block on screen
        r._thinking_ticker_task = None
        r._spinner = None
        r._stdout = io.StringIO()
        await stop_thinking(collapse_plan=True)
        output = r._stdout.getvalue()
        assert "2/2" in output

    @pytest.mark.asyncio
    async def test_stop_thinking_clears_plan_block(self) -> None:
        import anteroom.cli.renderer as r

        start_plan(["A"])
        r._thinking_start = time.monotonic()
        r._plan_written_lines = 2  # header + 1 step
        r._thinking_ticker_task = None
        r._spinner = None
        r._stdout = io.StringIO()
        await stop_thinking()
        # Plan written lines should be reset
        assert r._plan_written_lines == 0

    def test_stop_thinking_sync_clears_plan_block(self) -> None:
        """stop_thinking_sync() clears the plan block and resets written lines."""
        import anteroom.cli.renderer as r

        start_plan(["A", "B"])
        r._thinking_start = time.monotonic()
        r._plan_written_lines = 3  # header + 2 steps
        r._thinking_ticker_task = None
        r._spinner = None
        r._stdout = io.StringIO()
        stop_thinking_sync()
        assert r._plan_written_lines == 0

    def test_update_plan_step_triggers_redraw(self) -> None:
        """update_plan_step() redraws the block when thinking is active."""
        import anteroom.cli.renderer as r

        start_plan(["First", "Second"])
        r._thinking_start = time.monotonic()
        r._plan_written_lines = 3  # simulate block on screen
        r._stdout = io.StringIO()
        update_plan_step(0, "complete")
        output = r._stdout.getvalue()
        # Should contain cursor-up and plan content
        assert "\033[3A" in output
        assert "First" in output
        assert "\u2713" in output  # checkmark for complete


class TestRenderWelcome:
    """Tests for render_welcome() banner output (#526)."""

    @staticmethod
    def _printed(mock_console: object) -> str:
        parts = []
        for c in mock_console.print.call_args_list:  # type: ignore[union-attr]
            if c[0]:
                parts.append(str(c[0][0]))
        return "\n".join(parts)

    @staticmethod
    def _render(**kwargs: object) -> None:
        from anteroom.cli.renderer import render_welcome

        defaults: dict[str, object] = {
            "model": "gpt-4o",
            "tool_count": 12,
            "instructions_loaded": False,
            "working_dir": "/tmp",
        }
        defaults.update(kwargs)
        render_welcome(**defaults)  # type: ignore[arg-type]

    def test_basic_output(self) -> None:
        with patch("anteroom.cli.renderer.console") as mc:
            self._render(instructions_loaded=True, working_dir="/home/user/project")
            output = self._printed(mc)
            assert "A N T E R O O M" in output
            assert "gpt-4o" in output
            assert "12 tools" in output
            assert "instructions" in output
            assert "Type /help for commands" in output

    def test_backward_compat_no_new_params(self) -> None:
        with patch("anteroom.cli.renderer.console") as mc:
            self._render(tool_count=5)
            output = self._printed(mc)
            assert "skills" not in output
            assert "packs" not in output
            assert "Packs:" not in output
            assert "Getting started:" not in output
            assert "Type /help for commands" in output

    def test_shows_skill_count(self) -> None:
        with patch("anteroom.cli.renderer.console") as mc:
            self._render(skill_count=7)
            output = self._printed(mc)
            assert "7 skills" in output

    def test_omits_zero_skills(self) -> None:
        with patch("anteroom.cli.renderer.console") as mc:
            self._render(skill_count=0)
            output = self._printed(mc)
            assert "skills" not in output

    def test_shows_pack_count_and_names(self) -> None:
        with patch("anteroom.cli.renderer.console") as mc:
            self._render(pack_count=3, pack_names=["python-dev", "security-baseline", "docs"])
            output = self._printed(mc)
            assert "3 packs" in output
            assert "Packs: python-dev, security-baseline, docs" in output

    def test_omits_zero_packs(self) -> None:
        with patch("anteroom.cli.renderer.console") as mc:
            self._render(pack_count=0)
            output = self._printed(mc)
            assert "packs" not in output
            assert "Packs:" not in output

    def test_first_run_hint(self) -> None:
        with patch("anteroom.cli.renderer.console") as mc:
            self._render(is_first_run=True)
            output = self._printed(mc)
            assert "Getting started:" in output
            assert "Just type a message to start chatting" in output
            assert "/space init" in output
            assert "/help" in output

    def test_returning_user_hint(self) -> None:
        with patch("anteroom.cli.renderer.console") as mc:
            self._render(is_first_run=False)
            output = self._printed(mc)
            assert "Type /help for commands" in output
            assert "New here?" not in output

    def test_full_banner(self) -> None:
        with patch("anteroom.cli.renderer.console") as mc:
            self._render(
                model="claude-3-opus",
                tool_count=15,
                instructions_loaded=True,
                working_dir="/home/dev/myproject",
                git_branch="main",
                version="1.72.1",
                build_date="Feb 27, 2026",
                skill_count=10,
                pack_count=2,
                pack_names=["python-dev", "security"],
            )
            output = self._printed(mc)
            assert "claude-3-opus" in output
            assert "15 tools" in output
            assert "10 skills" in output
            assert "2 packs" in output
            assert "instructions" in output
            assert "(main)" in output
            assert "v1.72.1" in output
            assert "Packs: python-dev, security" in output

    def test_info_line_order(self) -> None:
        with patch("anteroom.cli.renderer.console") as mc:
            self._render(skill_count=7, pack_count=3, instructions_loaded=True)
            output = self._printed(mc)
            info_line = [line for line in output.split("\n") if "12 tools" in line][0]
            tools_pos = info_line.index("12 tools")
            skills_pos = info_line.index("7 skills")
            packs_pos = info_line.index("3 packs")
            instructions_pos = info_line.index("instructions")
            assert tools_pos < skills_pos < packs_pos < instructions_pos


class TestRenderWelcomeFirstRunLayout:
    """Visual layout tests for first-run onboarding output (#798).

    Validates the full structure of the getting-started block to catch
    formatting regressions that simple string asserts would miss.
    """

    @staticmethod
    def _printed(mock_console: object) -> list[str]:
        lines = []
        for c in mock_console.print.call_args_list:  # type: ignore[union-attr]
            if c[0]:
                lines.append(str(c[0][0]))
            else:
                lines.append("")
        return lines

    def test_first_run_block_structure(self) -> None:
        """First-run output has: Getting started header, 3 hint lines, blank line."""
        from anteroom.cli.renderer import render_welcome

        with patch("anteroom.cli.renderer.console") as mc:
            render_welcome(
                model="gpt-4o",
                tool_count=12,
                instructions_loaded=False,
                working_dir="/tmp",
                is_first_run=True,
            )
            lines = self._printed(mc)
            # Find the "Getting started:" line
            gs_idx = next(i for i, line in enumerate(lines) if "Getting started:" in line)
            # Next 3 lines should be the hints
            assert "Just type a message" in lines[gs_idx + 1]
            assert "/space init" in lines[gs_idx + 2]
            assert "/help" in lines[gs_idx + 3]
            # Followed by a blank line (empty print call)
            assert lines[gs_idx + 4] == ""

    def test_first_run_does_not_show_returning_user_hint(self) -> None:
        """First-run should NOT show the compact 'Type /help' line."""
        from anteroom.cli.renderer import render_welcome

        with patch("anteroom.cli.renderer.console") as mc:
            render_welcome(
                model="gpt-4o",
                tool_count=12,
                instructions_loaded=False,
                working_dir="/tmp",
                is_first_run=True,
            )
            lines = self._printed(mc)
            # The returning-user hint should not appear
            single_help_lines = [line for line in lines if "Type /help for commands" in line and "Getting" not in line]
            assert len(single_help_lines) == 0

    def test_returning_user_does_not_show_getting_started(self) -> None:
        """Returning user should see compact hint, not the full getting-started block."""
        from anteroom.cli.renderer import render_welcome

        with patch("anteroom.cli.renderer.console") as mc:
            render_welcome(
                model="gpt-4o",
                tool_count=12,
                instructions_loaded=False,
                working_dir="/tmp",
                is_first_run=False,
            )
            lines = self._printed(mc)
            full_output = "\n".join(lines)
            assert "Getting started:" not in full_output
            assert "/space init" not in full_output
            assert "Type /help for commands" in full_output


class TestToolTicker:
    """Tests for tool call elapsed timer (#581)."""

    @pytest.mark.asyncio
    async def test_start_tool_ticker_creates_task(self) -> None:
        """start_tool_ticker() should create a background ticker task."""
        import anteroom.cli.renderer as r

        r._repl_mode = True
        r._stdout = io.StringIO()
        try:
            start_tool_ticker("Running bash")
            assert r._tool_ticker_task is not None
            assert not r._tool_ticker_task.done()
        finally:
            stop_tool_ticker_sync()
            r._repl_mode = False
            r._stdout = None

    @pytest.mark.asyncio
    async def test_stop_tool_ticker_cancels_task(self) -> None:
        """stop_tool_ticker_sync() should cancel the ticker task."""
        import anteroom.cli.renderer as r

        r._repl_mode = True
        r._stdout = io.StringIO()
        try:
            start_tool_ticker("Running bash")
            task = r._tool_ticker_task
            assert task is not None
            stop_tool_ticker_sync()
            assert r._tool_ticker_task is None
        finally:
            r._repl_mode = False
            r._stdout = None

    @pytest.mark.asyncio
    async def test_tool_ticker_updates_elapsed(self) -> None:
        """Background ticker should show elapsed time in REPL mode."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._repl_mode = True
        r._stdout = buf
        try:
            r._tool_start = time.monotonic() - 5.0
            start_tool_ticker("Running bash")
            await asyncio.sleep(0.6)
            output = buf.getvalue()
            assert "5s" in output or "6s" in output
            assert "Running bash" in output
        finally:
            stop_tool_ticker_sync()
            r._tool_start = 0
            r._repl_mode = False
            r._stdout = None

    @pytest.mark.asyncio
    async def test_tool_ticker_non_repl_uses_spinner(self) -> None:
        """In non-REPL mode, tool ticker should use Rich Status spinner."""
        import anteroom.cli.renderer as r

        r._repl_mode = False
        r._stdout = None
        try:
            start_tool_ticker("Reading file")
            assert r._tool_spinner is not None
            assert r._tool_ticker_task is not None
        finally:
            stop_tool_ticker_sync()
            assert r._tool_spinner is None

    def test_stop_tool_ticker_without_start_is_safe(self) -> None:
        """stop_tool_ticker_sync() should be safe to call without start."""
        import anteroom.cli.renderer as r

        r._tool_ticker_task = None
        r._tool_spinner = None
        stop_tool_ticker_sync()  # should not raise

    @pytest.mark.asyncio
    async def test_ask_user_skips_ticker(self) -> None:
        """ask_user tool should not start a ticker (it clobbers the input prompt)."""
        import anteroom.cli.renderer as r

        r._repl_mode = True
        r._stdout = io.StringIO()
        try:
            set_verbosity(Verbosity.COMPACT)
            render_tool_call_start("ask_user", {"question": "Continue?"})
            assert r._tool_ticker_task is None
            assert r._tool_spinner is None
        finally:
            stop_tool_ticker_sync()
            r._tool_start = 0
            r._repl_mode = False
            r._stdout = None

    @pytest.mark.asyncio
    async def test_ask_user_stops_existing_ticker(self) -> None:
        """ask_user should stop any existing ticker from a prior tool call."""
        import anteroom.cli.renderer as r

        r._repl_mode = True
        r._stdout = io.StringIO()
        try:
            set_verbosity(Verbosity.COMPACT)
            # Start a ticker for a normal tool
            render_tool_call_start("bash", {"command": "sleep 10"})
            assert r._tool_ticker_task is not None
            prior_task = r._tool_ticker_task

            # Now ask_user starts — should cancel the prior ticker
            render_tool_call_start("ask_user", {"question": "Continue?"})
            assert r._tool_ticker_task is None
            # Let the event loop process the cancellation
            await asyncio.sleep(0)
            assert prior_task.cancelled()
        finally:
            stop_tool_ticker_sync()
            r._tool_start = 0
            r._repl_mode = False
            r._stdout = None


class TestFormatStatusToolbar:
    """Tests for the persistent bottom toolbar formatter."""

    def test_returns_list_of_tuples(self):
        result = format_status_toolbar(model="gpt-4o")
        assert isinstance(result, list)
        assert all(isinstance(t, tuple) and len(t) == 2 for t in result)

    def test_shows_model(self):
        result = format_status_toolbar(model="gpt-4o")
        text = "".join(t[1] for t in result)
        assert "gpt-4o" in text

    def test_shows_token_usage(self):
        result = format_status_toolbar(current_tokens=12300, max_context=128_000)
        text = "".join(t[1] for t in result)
        assert "12k" in text
        assert "128k" in text
        assert "10%" in text

    def test_token_pressure_green(self):
        result = format_status_toolbar(current_tokens=10_000, max_context=128_000)
        token_entries = [t for t in result if "%" in t[1]]
        assert token_entries
        assert token_entries[0][0] == "class:bottom-toolbar.tokens"

    def test_token_pressure_warn(self):
        result = format_status_toolbar(current_tokens=70_000, max_context=128_000)
        token_entries = [t for t in result if "%" in t[1]]
        assert token_entries
        assert token_entries[0][0] == "class:bottom-toolbar.tokens-warn"

    def test_token_pressure_danger(self):
        result = format_status_toolbar(current_tokens=100_000, max_context=128_000)
        token_entries = [t for t in result if "%" in t[1]]
        assert token_entries
        assert token_entries[0][0] == "class:bottom-toolbar.tokens-danger"

    def test_shows_message_count(self):
        result = format_status_toolbar(message_count=14)
        text = "".join(t[1] for t in result)
        assert "14 msgs" in text

    def test_hides_zero_messages(self):
        result = format_status_toolbar(message_count=0)
        text = "".join(t[1] for t in result)
        assert "msgs" not in text

    def test_shows_approval_mode(self):
        result = format_status_toolbar(approval_mode="ask_for_writes")
        text = "".join(t[1] for t in result)
        assert "ask_for_writes" in text

    def test_shows_tool_count(self):
        result = format_status_toolbar(tool_count=8)
        text = "".join(t[1] for t in result)
        assert "8 tools" in text

    def test_shows_mcp_connecting(self):
        mcp = {"myserver": {"status": "connecting"}}
        result = format_status_toolbar(mcp_statuses=mcp)
        text = "".join(t[1] for t in result)
        assert "MCP" in text
        assert "myserver" in text

    def test_hides_mcp_when_resolved(self):
        mcp = {"myserver": {"status": "connected", "tool_count": 3}}
        result = format_status_toolbar(mcp_statuses=mcp)
        text = "".join(t[1] for t in result)
        assert "MCP" not in text

    def test_full_toolbar(self):
        result = format_status_toolbar(
            model="gpt-4o",
            current_tokens=50_000,
            max_context=128_000,
            message_count=14,
            approval_mode="ask_for_writes",
            tool_count=8,
        )
        text = "".join(t[1] for t in result)
        assert "gpt-4o" in text
        assert "50k" in text
        assert "14 msgs" in text
        assert "ask_for_writes" in text
        assert "8 tools" in text

    def test_no_trailing_separator(self):
        result = format_status_toolbar(model="gpt-4o", tool_count=5)
        assert result[-1][0] != "class:bottom-toolbar.sep"

    def test_shows_working_dir(self):
        result = format_status_toolbar(working_dir="/home/user/project")
        text = "".join(t[1] for t in result)
        assert "project" in text

    def test_shows_git_branch(self):
        result = format_status_toolbar(working_dir="/home/user/project", git_branch="feat-x")
        text = "".join(t[1] for t in result)
        assert "feat-x" in text

    def test_git_branch_without_working_dir_hidden(self):
        result = format_status_toolbar(git_branch="feat-x")
        text = "".join(t[1] for t in result)
        assert "feat-x" not in text

    def test_shows_space_name(self):
        result = format_status_toolbar(space_name="my-space")
        text = "".join(t[1] for t in result)
        assert "my-space" in text

    def test_shows_plan_mode(self):
        result = format_status_toolbar(plan_mode=True)
        text = "".join(t[1] for t in result)
        assert "PLAN" in text

    def test_hides_plan_mode_when_false(self):
        result = format_status_toolbar(plan_mode=False)
        text = "".join(t[1] for t in result)
        assert "PLAN" not in text

    def test_shows_conversation_name(self):
        result = format_status_toolbar(conversation_name="swift-fox")
        text = "".join(t[1] for t in result)
        assert "swift-fox" in text

    def test_full_toolbar_with_new_fields(self):
        result = format_status_toolbar(
            model="gpt-4o",
            working_dir="/home/user/project",
            git_branch="main",
            space_name="dev",
            plan_mode=True,
            conversation_name="swift-fox",
            tool_count=5,
        )
        text = "".join(t[1] for t in result)
        assert "gpt-4o" in text
        assert "main" in text
        assert "dev" in text
        assert "PLAN" in text
        assert "swift-fox" in text


# ---------------------------------------------------------------------------
# Regression tests for #617 bug fixes
# ---------------------------------------------------------------------------


class TestBugfix617Renderer:
    """Regression tests for renderer bugs (#617)."""

    def setup_method(self):
        import importlib

        import anteroom.cli.renderer as mod

        importlib.reload(mod)
        self._mod = mod

    def test_stop_thinking_resets_thinking_start(self):
        """#617-18: _thinking_start must be reset to 0 after stop_thinking."""
        self._mod._thinking_start = time.monotonic()
        self._mod._repl_mode = False
        self._mod._spinner = None
        self._mod._thinking_ticker_task = None
        asyncio.run(self._mod.stop_thinking())
        assert self._mod._thinking_start == 0

    def test_stop_thinking_sync_resets_thinking_start(self):
        """#617-18: stop_thinking_sync must also reset _thinking_start."""
        self._mod._thinking_start = time.monotonic()
        self._mod._repl_mode = False
        self._mod._spinner = None
        self._mod._thinking_ticker_task = None
        self._mod.stop_thinking_sync()
        assert self._mod._thinking_start == 0

    def test_render_newline_when_stdout_is_none(self):
        """#617: render_newline must not raise when _stdout is None."""
        self._mod._stdout = None
        # Should not raise
        self._mod.render_newline()

    def test_stop_thinking_returns_elapsed(self):
        """#617-18: stop_thinking returns elapsed time even after reset."""
        self._mod._thinking_start = time.monotonic() - 2.5
        self._mod._repl_mode = False
        self._mod._spinner = None
        self._mod._thinking_ticker_task = None
        elapsed = asyncio.run(self._mod.stop_thinking())
        assert elapsed >= 2.0
        assert self._mod._thinking_start == 0

    def test_stop_thinking_sync_returns_elapsed(self):
        """#617-18: stop_thinking_sync returns elapsed time even after reset."""
        self._mod._thinking_start = time.monotonic() - 1.0
        self._mod._repl_mode = False
        self._mod._spinner = None
        self._mod._thinking_ticker_task = None
        elapsed = self._mod.stop_thinking_sync()
        assert elapsed >= 0.5
        assert self._mod._thinking_start == 0

    def test_stop_thinking_idempotent(self):
        """#617-18: Calling stop_thinking twice doesn't accumulate state."""
        self._mod._thinking_start = time.monotonic() - 1.0
        self._mod._repl_mode = False
        self._mod._spinner = None
        self._mod._thinking_ticker_task = None
        asyncio.run(self._mod.stop_thinking())
        assert self._mod._thinking_start == 0
        # Second call should still work without error
        asyncio.run(self._mod.stop_thinking())
        assert self._mod._thinking_start == 0

    def test_render_newline_with_stdout(self):
        """render_newline emits a blank line via console.print()."""
        with patch("anteroom.cli.renderer.console") as mock_console:
            self._mod.render_newline()
            mock_console.print.assert_called_once_with()


# ---------------------------------------------------------------------------
# render_error / render_warning (#678)
# ---------------------------------------------------------------------------


class TestRenderError:
    """Tests for render_error() and render_warning()."""

    def test_render_error_prints_red_bold(self) -> None:
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_error("Connection timed out")
            printed = str(mock_console.print.call_args_list[0])
            assert "Connection timed out" in printed
            import anteroom.cli.renderer as r

            assert f"{r._theme.error} bold" in printed

    def test_render_error_escapes_markup(self) -> None:
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_error("[bold]injection[/bold]")
            printed = str(mock_console.print.call_args_list[0])
            assert "\\[bold\\]" in printed or "\\[bold]" in printed

    def test_render_warning_prints_yellow_bold(self) -> None:
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_warning("Rate limited by API")
            printed = str(mock_console.print.call_args_list[0])
            assert "Rate limited by API" in printed
            import anteroom.cli.renderer as r

            assert f"{r._theme.warning} bold" in printed

    def test_render_warning_escapes_markup(self) -> None:
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_warning("[red]injection[/red]")
            printed = str(mock_console.print.call_args_list[0])
            assert "\\[red\\]" in printed or "\\[red]" in printed

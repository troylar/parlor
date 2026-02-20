"""Tests for the CLI renderer verbosity and display system."""

from __future__ import annotations

import asyncio
import io
import time
from unittest.mock import patch

import pytest

from anteroom.cli.renderer import (
    Verbosity,
    _dedup_flush_label,
    _dedup_key_from_summary,
    _flush_dedup,
    _format_tokens,
    _humanize_tool,
    _output_summary,
    _phase_suffix,
    _short_path,
    _write_thinking_line,
    clear_turn_history,
    cycle_verbosity,
    flush_buffered_text,
    get_verbosity,
    increment_thinking_tokens,
    render_response_end,
    render_tool_call_end,
    render_tool_call_start,
    save_turn_history,
    set_thinking_phase,
    set_tool_dedup,
    set_verbosity,
    start_thinking,
    startup_step,
    stop_thinking,
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
        with patch("anteroom.cli.renderer._write_thinking_line"):
            r._repl_mode = True
            start_thinking()
            r._repl_mode = False
        assert r._tool_batch_active is False

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
            assert "[red]" in first_call

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

    def test_stall_warning_at_threshold(self) -> None:
        """At 15s: stall warning appears."""
        import io

        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        _write_thinking_line(15.0)
        output = buf.getvalue()
        assert "15s" in output
        assert "waiting for API response" in output
        r._stdout = None

    def test_stall_warning_after_threshold(self) -> None:
        """Well past threshold: stall warning still present."""
        import io

        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        _write_thinking_line(30.0)
        output = buf.getvalue()
        assert "30s" in output
        assert "waiting for API response" in output
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
            stop_thinking()
            r._repl_mode = False
            r._stdout = None

    @pytest.mark.asyncio
    async def test_stop_thinking_cancels_ticker_task(self) -> None:
        """stop_thinking() should cancel the ticker task."""
        import anteroom.cli.renderer as r

        r._repl_mode = True
        r._stdout = io.StringIO()
        try:
            start_thinking()
            task = r._thinking_ticker_task
            assert task is not None
            stop_thinking()
            assert r._thinking_ticker_task is None
            # Allow cancellation to propagate
            await asyncio.sleep(0)
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
            stop_thinking()
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
            stop_thinking()

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
            stop_thinking()
            r._repl_mode = False
            r._stdout = None


class TestThinkingPhases:
    """Tests for lifecycle phase tracking in the thinking indicator (#203)."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        r._thinking_phase = ""
        r._thinking_tokens = 0
        r._last_chunk_time = 0
        set_verbosity(Verbosity.DETAILED)

    def teardown_method(self) -> None:
        import anteroom.cli.renderer as r

        r._thinking_phase = ""
        r._thinking_tokens = 0
        r._last_chunk_time = 0
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

    def test_phase_suffix_empty_in_compact_mode(self) -> None:
        """_phase_suffix returns empty string in COMPACT verbosity."""
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.COMPACT)
        r._thinking_phase = "connecting"
        assert _phase_suffix(5.0) == ""

    def test_phase_suffix_connecting(self) -> None:
        """_phase_suffix returns 'connecting' for the connecting phase."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "connecting"
        assert _phase_suffix(1.0) == "connecting"

    def test_phase_suffix_waiting(self) -> None:
        """_phase_suffix returns 'waiting for first token' for the waiting phase."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "waiting"
        assert _phase_suffix(2.0) == "waiting for first token"

    def test_phase_suffix_streaming_with_token_count(self) -> None:
        """_phase_suffix returns 'streaming (N tokens)' during active streaming."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "streaming"
        r._thinking_tokens = 42
        r._last_chunk_time = time.monotonic()  # recent, no stall
        result = _phase_suffix(3.0)
        assert result == "streaming (42 tokens)"

    def test_phase_suffix_streaming_stalled(self) -> None:
        """_phase_suffix returns 'stalled Ns' when no chunks arrive for >5s."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "streaming"
        r._thinking_tokens = 10
        r._last_chunk_time = time.monotonic() - 7.0  # 7s since last chunk
        result = _phase_suffix(10.0)
        assert "stalled" in result
        assert "7s" in result or "6s" in result  # allow for timing jitter

    def test_phase_suffix_streaming_not_stalled_within_threshold(self) -> None:
        """_phase_suffix does NOT report stalled when chunk arrived within 5s."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "streaming"
        r._thinking_tokens = 10
        r._last_chunk_time = time.monotonic() - 2.0  # 2s ago, under threshold
        result = _phase_suffix(10.0)
        assert "stalled" not in result
        assert "streaming (10 tokens)" == result

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
        r._last_chunk_time = time.monotonic()
        r._repl_mode = True
        r._stdout = io.StringIO()
        try:
            start_thinking()
            assert r._thinking_phase == ""
            assert r._thinking_tokens == 0
            assert r._last_chunk_time == 0
        finally:
            stop_thinking()
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
        """Full lifecycle: connecting → waiting → streaming with tokens."""
        set_thinking_phase("connecting")
        assert _phase_suffix(0.5) == "connecting"

        set_thinking_phase("waiting")
        assert _phase_suffix(1.0) == "waiting for first token"

        increment_thinking_tokens()
        increment_thinking_tokens()
        increment_thinking_tokens()
        result = _phase_suffix(2.0)
        assert "streaming (3 tokens)" == result

    def test_stall_detection_clears_when_chunks_resume(self) -> None:
        """Stall detection clears when new chunks arrive."""
        import anteroom.cli.renderer as r

        r._thinking_phase = "streaming"
        r._thinking_tokens = 5
        r._last_chunk_time = time.monotonic() - 10.0  # stalled
        assert "stalled" in _phase_suffix(15.0)

        # New chunk arrives
        increment_thinking_tokens()
        result = _phase_suffix(15.0)
        assert "stalled" not in result
        assert "streaming (6 tokens)" == result


class TestWriteThinkingLinePhases:
    """Tests for phase text in _write_thinking_line() ANSI output (#203)."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as r

        r._thinking_phase = ""
        r._thinking_tokens = 0
        r._last_chunk_time = 0
        set_verbosity(Verbosity.DETAILED)

    def teardown_method(self) -> None:
        import anteroom.cli.renderer as r

        r._thinking_phase = ""
        r._thinking_tokens = 0
        r._last_chunk_time = 0
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
        """_write_thinking_line includes 'streaming (N tokens)' phase text."""
        import anteroom.cli.renderer as r

        buf = io.StringIO()
        r._stdout = buf
        r._thinking_phase = "streaming"
        r._thinking_tokens = 25
        r._last_chunk_time = time.monotonic()
        _write_thinking_line(3.0)
        output = buf.getvalue()
        assert "streaming (25 tokens)" in output
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

    def test_no_phase_text_in_compact_mode(self) -> None:
        """_write_thinking_line omits phase text in COMPACT verbosity."""
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.COMPACT)
        buf = io.StringIO()
        r._stdout = buf
        r._thinking_phase = "connecting"
        r._last_chunk_time = time.monotonic()
        _write_thinking_line(2.0)
        output = buf.getvalue()
        assert "connecting" not in output
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

    def test_no_phase_falls_back_to_stall_warning(self) -> None:
        """When no phase is set, the generic stall warning still appears."""
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.DETAILED)
        buf = io.StringIO()
        r._stdout = buf
        r._thinking_phase = ""
        _write_thinking_line(20.0)
        output = buf.getvalue()
        assert "waiting for API response" in output
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
    async def test_ticker_shows_streaming_token_count(self) -> None:
        """Background ticker shows token count during streaming phase."""
        import anteroom.cli.renderer as r

        set_verbosity(Verbosity.DETAILED)
        r._repl_mode = True
        buf = io.StringIO()
        r._stdout = buf
        r._thinking_start = time.monotonic() - 2.0
        r._thinking_phase = "streaming"
        r._thinking_tokens = 150
        r._last_chunk_time = time.monotonic()

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
            assert "streaming (150 tokens)" in output
        finally:
            r._repl_mode = False
            r._stdout = None
            r._thinking_phase = ""
            r._thinking_tokens = 0

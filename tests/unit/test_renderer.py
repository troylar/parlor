"""Tests for the CLI renderer verbosity and display system."""

from __future__ import annotations

from anteroom.cli.renderer import (
    Verbosity,
    _flush_dedup,
    _format_tokens,
    _humanize_tool,
    _output_summary,
    _short_path,
    clear_turn_history,
    cycle_verbosity,
    flush_buffered_text,
    get_verbosity,
    save_turn_history,
    set_verbosity,
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

        r._dedup_summary = ""
        r._dedup_count = 0

    def test_flush_dedup_resets_state(self) -> None:
        import anteroom.cli.renderer as r

        r._dedup_summary = "Reading test.py"
        r._dedup_count = 3
        _flush_dedup()
        assert r._dedup_summary == ""
        assert r._dedup_count == 0

    def test_flush_dedup_noop_when_empty(self) -> None:
        _flush_dedup()  # Should not crash


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

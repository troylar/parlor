"""Tests for paste detection and input collapsing in the CLI REPL."""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

from anteroom.cli.repl import _MAX_PASTE_DISPLAY_LINES, _PASTE_THRESHOLD, _collapse_long_input, _is_paste

_TERM_SIZE = os.terminal_size((80, 24))


class TestIsPaste:
    def test_recent_change_is_paste(self) -> None:
        """Buffer change < 50ms ago → paste."""
        just_now = time.monotonic()
        assert _is_paste(just_now) is True

    def test_old_change_is_not_paste(self) -> None:
        """Buffer change > 50ms ago → normal typing."""
        long_ago = time.monotonic() - 0.2  # 200ms ago
        assert _is_paste(long_ago) is False

    def test_exactly_at_threshold_is_not_paste(self) -> None:
        """Buffer change exactly at threshold boundary → not paste."""
        at_threshold = time.monotonic() - _PASTE_THRESHOLD - 0.001
        assert _is_paste(at_threshold) is False

    def test_custom_threshold(self) -> None:
        """Custom threshold is respected."""
        recent = time.monotonic() - 0.02  # 20ms ago
        assert _is_paste(recent, threshold=0.01) is False
        assert _is_paste(recent, threshold=0.05) is True

    def test_zero_timestamp_is_not_paste(self) -> None:
        """Initial state (0.0) should never count as paste."""
        assert _is_paste(0.0) is False


class TestPasteKeybindingBehavior:
    """Simulate the Enter key handler logic with mocked events."""

    def _make_event(self) -> MagicMock:
        event = MagicMock()
        event.current_buffer = MagicMock()
        return event

    def test_enter_during_paste_inserts_newline(self) -> None:
        """When Enter arrives during a paste burst, insert newline."""
        event = self._make_event()
        last_change = [time.monotonic()]  # just changed

        # Simulate the Enter handler logic
        if _is_paste(last_change[0]):
            event.current_buffer.insert_text("\n")
        else:
            event.current_buffer.validate_and_handle()

        event.current_buffer.insert_text.assert_called_once_with("\n")
        event.current_buffer.validate_and_handle.assert_not_called()

    def test_enter_after_delay_submits(self) -> None:
        """When Enter arrives well after last change, submit."""
        event = self._make_event()
        last_change = [time.monotonic() - 0.2]  # 200ms ago

        if _is_paste(last_change[0]):
            event.current_buffer.insert_text("\n")
        else:
            event.current_buffer.validate_and_handle()

        event.current_buffer.validate_and_handle.assert_called_once()
        event.current_buffer.insert_text.assert_not_called()

    def test_rapid_multiple_newlines_all_insert(self) -> None:
        """Simulate pasting 3 lines: each newline should insert, not submit."""
        event = self._make_event()
        submitted = False

        for _ in range(3):
            last_change = time.monotonic()  # simulate rapid buffer change
            if _is_paste(last_change):
                event.current_buffer.insert_text("\n")
            else:
                submitted = True

        assert not submitted
        assert event.current_buffer.insert_text.call_count == 3

    def test_enter_after_paste_settles_submits(self) -> None:
        """After paste is done and user waits, Enter submits."""
        event = self._make_event()

        # Simulate: paste happened, then user reviews for 500ms, then presses Enter
        last_change = [time.monotonic() - 0.5]

        if _is_paste(last_change[0]):
            event.current_buffer.insert_text("\n")
        else:
            event.current_buffer.validate_and_handle()

        event.current_buffer.validate_and_handle.assert_called_once()


class TestCollapseLongInput:
    """Tests for _collapse_long_input() output rendering."""

    def _make_lines(self, count: int) -> str:
        return "\n".join(f"line {i}" for i in range(count))

    def test_short_input_no_output(self) -> None:
        """Input with <= _MAX_PASTE_DISPLAY_LINES produces no output."""
        short = self._make_lines(_MAX_PASTE_DISPLAY_LINES)
        with (
            patch("anteroom.cli.repl.renderer") as mock_renderer,
            patch("anteroom.cli.repl.sys") as mock_sys,
        ):
            mock_sys.stdout.isatty.return_value = True
            _collapse_long_input(short)
            mock_renderer.console.print.assert_not_called()
            mock_renderer._stdout.write.assert_not_called()

    def test_long_input_collapses(self) -> None:
        """Input with many lines writes collapsed output via renderer."""
        long_input = self._make_lines(20)
        with (
            patch("anteroom.cli.repl.renderer") as mock_renderer,
            patch("anteroom.cli.repl.sys") as mock_sys,
            patch("anteroom.cli.repl.shutil") as mock_shutil,
        ):
            mock_sys.stdout.isatty.return_value = True
            mock_shutil.get_terminal_size.return_value = _TERM_SIZE
            _collapse_long_input(long_input)

            # Cursor movement via _stdout (real fd)
            assert mock_renderer._stdout.write.call_count == 1
            cursor_call = mock_renderer._stdout.write.call_args[0][0]
            assert "\033[" in cursor_call  # ANSI cursor movement
            assert "\033[J" in cursor_call  # clear to end

            # 3 shown lines + 1 hidden count = 4 console.print calls
            assert mock_renderer.console.print.call_count == 4

            # First call has the prompt character
            first_call = mock_renderer.console.print.call_args_list[0][0][0]
            assert "❯" in first_call

            # Last call has the hidden line count
            last_call = mock_renderer.console.print.call_args_list[3][0][0]
            assert "17 more lines" in last_call

            mock_renderer._stdout.flush.assert_called_once()

    def test_user_content_escaped(self) -> None:
        """Pasted content with Rich markup is escaped in output."""
        lines = ["[bold red]danger[/]"] + [f"line {i}" for i in range(19)]
        raw = "\n".join(lines)
        with (
            patch("anteroom.cli.repl.renderer") as mock_renderer,
            patch("anteroom.cli.repl.sys") as mock_sys,
            patch("anteroom.cli.repl.shutil") as mock_shutil,
        ):
            mock_sys.stdout.isatty.return_value = True
            mock_shutil.get_terminal_size.return_value = _TERM_SIZE
            _collapse_long_input(raw)

            first_call = mock_renderer.console.print.call_args_list[0][0][0]
            # The raw markup should be escaped — Rich escape() turns [ into \[
            assert "[bold red]" not in first_call or "\\[bold red]" in first_call

    def test_not_a_tty_no_output(self) -> None:
        """When stdout is not a TTY, function returns early."""
        long_input = self._make_lines(20)
        with (
            patch("anteroom.cli.repl.renderer") as mock_renderer,
            patch("anteroom.cli.repl.sys") as mock_sys,
        ):
            mock_sys.stdout.isatty.return_value = False
            _collapse_long_input(long_input)
            mock_renderer.console.print.assert_not_called()
            mock_renderer._stdout.write.assert_not_called()

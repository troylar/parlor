"""Tests for paste detection in the CLI REPL."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from anteroom.cli.repl import _PASTE_THRESHOLD, _is_paste


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

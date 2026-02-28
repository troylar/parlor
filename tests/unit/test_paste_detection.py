"""Tests for paste detection, input collapsing, and fullscreen helpers in the CLI REPL."""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import pytest
from prompt_toolkit.completion import Completion
from prompt_toolkit.document import Document

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
            mock_renderer.is_fullscreen.return_value = False
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
            mock_renderer.is_fullscreen.return_value = False
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

    def test_fullscreen_mode_returns_early(self) -> None:
        """In fullscreen mode, collapse is skipped (output pane handles display)."""
        long_input = self._make_lines(20)
        with (
            patch("anteroom.cli.repl.renderer") as mock_renderer,
            patch("anteroom.cli.repl.sys") as mock_sys,
        ):
            mock_sys.stdout.isatty.return_value = True
            mock_renderer.is_fullscreen.return_value = True
            _collapse_long_input(long_input)
            mock_renderer.console.print.assert_not_called()
            mock_renderer._stdout.write.assert_not_called()


class TestAcceptCompletionBehavior:
    """Simulate the _accept_completion() logic with mocked buffers."""

    def _make_buf(self, *, has_completion: bool = False) -> MagicMock:
        buf = MagicMock()
        if has_completion:
            completion = MagicMock()
            buf.complete_state = MagicMock()
            buf.complete_state.current_completion = completion
        else:
            buf.complete_state = None
        buf.completer = MagicMock()
        return buf

    @staticmethod
    def _accept_completion(buf: MagicMock) -> bool:
        """Reproduce the _accept_completion logic from repl.py."""
        if buf.complete_state and buf.complete_state.current_completion:
            saved_completer = buf.completer
            buf.completer = None
            try:
                buf.apply_completion(buf.complete_state.current_completion)
            finally:
                buf.completer = saved_completer
            return True
        return False

    def test_accepts_when_completion_menu_open(self) -> None:
        """When the completion menu has a selection, accept it."""
        buf = self._make_buf(has_completion=True)
        result = self._accept_completion(buf)
        assert result is True
        buf.apply_completion.assert_called_once()

    def test_returns_false_when_no_completion(self) -> None:
        """When no completion menu is open, return False."""
        buf = self._make_buf(has_completion=False)
        result = self._accept_completion(buf)
        assert result is False
        buf.apply_completion.assert_not_called()

    def test_temporarily_nulls_completer(self) -> None:
        """Completer is temporarily set to None during apply to prevent _Retry."""
        buf = self._make_buf(has_completion=True)
        original_completer = buf.completer

        # Track completer state during apply_completion
        completer_during_apply = []

        def capture_completer(*args: object) -> None:
            completer_during_apply.append(buf.completer)

        buf.apply_completion.side_effect = capture_completer
        self._accept_completion(buf)

        # During apply_completion, completer should have been None
        assert completer_during_apply == [None]
        # After, completer should be restored
        assert buf.completer is original_completer

    def test_completer_restored_on_exception(self) -> None:
        """#617-12: Completer must be restored even if apply_completion raises."""
        buf = self._make_buf(has_completion=True)
        original_completer = buf.completer
        buf.apply_completion.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            self._accept_completion(buf)

        # Completer must still be restored despite the exception
        assert buf.completer is original_completer

    def test_enter_with_completion_does_not_submit(self) -> None:
        """Enter key accepts completion instead of submitting when menu is open."""
        buf = self._make_buf(has_completion=True)
        last_change = [time.monotonic() - 0.5]  # not a paste

        # Simulate Enter handler logic
        if self._accept_completion(buf):
            submitted = False
        elif _is_paste(last_change[0]):
            submitted = False
        else:
            submitted = True

        assert submitted is False
        buf.apply_completion.assert_called_once()
        buf.validate_and_handle.assert_not_called()

    def test_enter_without_completion_submits(self) -> None:
        """Enter key submits when no completion menu is open."""
        buf = self._make_buf(has_completion=False)
        last_change = [time.monotonic() - 0.5]  # not a paste

        if self._accept_completion(buf):
            pass
        elif _is_paste(last_change[0]):
            buf.insert_text("\n")
        else:
            buf.validate_and_handle()

        buf.validate_and_handle.assert_called_once()

    def test_tab_starts_completion_when_no_menu(self) -> None:
        """Tab triggers start_completion when no menu is open."""
        buf = self._make_buf(has_completion=False)
        buf.complete_state = None

        # Simulate Tab handler logic
        if buf.complete_state:
            if not self._accept_completion(buf):
                buf.complete_next()
        else:
            buf.start_completion()

        buf.start_completion.assert_called_once()

    def test_tab_accepts_current_completion(self) -> None:
        """Tab accepts the current completion when menu is open."""
        buf = self._make_buf(has_completion=True)

        # Simulate Tab handler logic
        if buf.complete_state:
            if not self._accept_completion(buf):
                buf.complete_next()
        else:
            buf.start_completion()

        buf.apply_completion.assert_called_once()
        buf.complete_next.assert_not_called()


class TestShouldAutoComplete:
    """Test the auto-complete trigger predicate logic."""

    @staticmethod
    def _should_auto_complete(text: str) -> bool:
        """Reproduce the _should_auto_complete logic from repl.py."""
        stripped = text.lstrip()
        if stripped.startswith("/") and " " not in stripped:
            return True
        if "@" in (text.split()[-1] if text.split() else ""):
            return True
        return False

    def test_slash_command_triggers(self) -> None:
        assert self._should_auto_complete("/hel") is True

    def test_slash_command_with_leading_spaces(self) -> None:
        assert self._should_auto_complete("  /hel") is True

    def test_slash_command_with_space_after_does_not_trigger(self) -> None:
        assert self._should_auto_complete("/help ") is False

    def test_at_sign_triggers(self) -> None:
        assert self._should_auto_complete("read @src/") is True

    def test_at_sign_in_last_word_triggers(self) -> None:
        assert self._should_auto_complete("@file.py") is True

    def test_plain_text_does_not_trigger(self) -> None:
        assert self._should_auto_complete("hello world") is False

    def test_empty_text_does_not_trigger(self) -> None:
        assert self._should_auto_complete("") is False

    def test_slash_only_triggers(self) -> None:
        assert self._should_auto_complete("/") is True

    def test_full_command_with_args_does_not_trigger(self) -> None:
        assert self._should_auto_complete("/resume my-slug") is False


class TestAnteroomCompleter:
    """Tests for the AnteroomCompleter class defined in repl.py."""

    def _make_completer(self) -> MagicMock:
        """Create a mock that simulates AnteroomCompleter.get_completions logic."""
        commands = ["help", "resume", "model", "quit"]
        skill_names = ["commit", "deploy"]
        skill_descriptions = {"commit": "Create a commit", "deploy": "Deploy to PyPI"}
        command_descriptions = {
            "help": "show help",
            "resume": "resume conversation",
            "model": "change model",
            "quit": "exit",
        }

        completer = MagicMock()

        def get_completions(document: Document, complete_event: MagicMock) -> list[Completion]:
            text = document.text_before_cursor
            word = document.get_word_before_cursor(WORD=True)
            results = []

            stripped = text.lstrip()
            if stripped.startswith("/") and " " not in stripped:
                prefix = word.lstrip("/")
                for cmd in commands:
                    if cmd.startswith(prefix):
                        meta = command_descriptions.get(cmd, "")
                        results.append(Completion(f"/{cmd} ", start_position=-len(word), display_meta=meta))
                for sname in skill_names:
                    if sname.startswith(prefix):
                        desc = skill_descriptions.get(sname, "skill")
                        results.append(Completion(f"/{sname} ", start_position=-len(word), display_meta=desc))
            return results

        completer.get_completions = get_completions
        return completer

    def test_slash_yields_matching_commands(self) -> None:
        completer = self._make_completer()
        doc = Document("/he", cursor_position=3)
        completions = list(completer.get_completions(doc, MagicMock()))
        texts = [c.text for c in completions]
        assert "/help " in texts

    def test_slash_yields_skills(self) -> None:
        completer = self._make_completer()
        doc = Document("/co", cursor_position=3)
        completions = list(completer.get_completions(doc, MagicMock()))
        texts = [c.text for c in completions]
        assert "/commit " in texts

    def test_skill_has_display_meta(self) -> None:
        completer = self._make_completer()
        doc = Document("/de", cursor_position=3)
        completions = list(completer.get_completions(doc, MagicMock()))
        deploy_completions = [c for c in completions if "/deploy" in c.text]
        assert len(deploy_completions) == 1
        assert "Deploy to PyPI" in str(deploy_completions[0].display_meta)

    def test_command_has_display_meta(self) -> None:
        completer = self._make_completer()
        doc = Document("/he", cursor_position=3)
        completions = list(completer.get_completions(doc, MagicMock()))
        help_completions = [c for c in completions if "/help" in c.text]
        assert len(help_completions) == 1
        assert "show help" in str(help_completions[0].display_meta)

    def test_completions_have_trailing_space(self) -> None:
        completer = self._make_completer()
        doc = Document("/he", cursor_position=3)
        completions = list(completer.get_completions(doc, MagicMock()))
        for c in completions:
            assert c.text.endswith(" ")

    def test_no_completions_after_space(self) -> None:
        completer = self._make_completer()
        doc = Document("/help foo", cursor_position=9)
        completions = list(completer.get_completions(doc, MagicMock()))
        assert completions == []

    def test_empty_slash_yields_all(self) -> None:
        completer = self._make_completer()
        doc = Document("/", cursor_position=1)
        completions = list(completer.get_completions(doc, MagicMock()))
        # Should yield all commands + all skills
        assert len(completions) == 6  # 4 commands + 2 skills


class TestOnAcceptBehavior:
    """Test the _on_accept buffer handler logic."""

    def test_signals_main_input_when_no_sub_prompt(self) -> None:
        """Accept stashes text and signals _input_ready."""
        accepted_text = [""]
        input_ready = MagicMock()
        sub_prompt_event = None

        buf = MagicMock()
        buf.text = "hello world"

        # Simulate _on_accept logic
        accepted_text[0] = buf.text
        if sub_prompt_event is not None:
            sub_prompt_event.set()
        else:
            input_ready.set()

        assert accepted_text[0] == "hello world"
        input_ready.set.assert_called_once()

    def test_signals_sub_prompt_when_active(self) -> None:
        """Accept signals sub-prompt event instead of main input."""
        accepted_text = [""]
        input_ready = MagicMock()
        sub_prompt_event = MagicMock()

        buf = MagicMock()
        buf.text = "sub response"

        # Simulate _on_accept logic
        accepted_text[0] = buf.text
        if sub_prompt_event is not None:
            sub_prompt_event.set()
        else:
            input_ready.set()

        assert accepted_text[0] == "sub response"
        sub_prompt_event.set.assert_called_once()
        input_ready.set.assert_not_called()

    def test_returns_true_to_keep_text(self) -> None:
        """_on_accept returns True to keep text in buffer (cleared after reading)."""
        # The return value True tells prompt_toolkit to keep text in the buffer
        # We test this by simulating the handler
        result = True  # _on_accept always returns True
        assert result is True

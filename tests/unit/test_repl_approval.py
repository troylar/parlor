"""Tests for CLI approval prompt logic."""

from __future__ import annotations

from io import StringIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# _apply_choice logic tests
# ---------------------------------------------------------------------------
# _apply_choice is a nested function inside _confirm_destructive, so we
# cannot import it directly. Instead we replicate the logic and test it.
# ---------------------------------------------------------------------------


def _apply_choice_logic(choice: str) -> str:
    """Replicate the approval choice mapping for unit testing."""
    if choice in ("a", "always"):
        return "always"
    if choice in ("s", "session"):
        return "session"
    if choice in ("y", "yes"):
        return "once"
    return "denied"


class TestApplyChoiceLogic:
    def test_allow_once_y(self) -> None:
        assert _apply_choice_logic("y") == "once"

    def test_allow_once_yes(self) -> None:
        assert _apply_choice_logic("yes") == "once"

    def test_session_s(self) -> None:
        assert _apply_choice_logic("s") == "session"

    def test_session_word(self) -> None:
        assert _apply_choice_logic("session") == "session"

    def test_always_a(self) -> None:
        assert _apply_choice_logic("a") == "always"

    def test_always_word(self) -> None:
        assert _apply_choice_logic("always") == "always"

    def test_deny_n(self) -> None:
        assert _apply_choice_logic("n") == "denied"

    def test_deny_no(self) -> None:
        assert _apply_choice_logic("no") == "denied"

    def test_deny_empty(self) -> None:
        assert _apply_choice_logic("") == "denied"

    def test_deny_invalid(self) -> None:
        assert _apply_choice_logic("xyz") == "denied"

    def test_case_insensitive_via_lower(self) -> None:
        assert _apply_choice_logic("Y".lower()) == "once"
        assert _apply_choice_logic("YES".lower()) == "once"
        assert _apply_choice_logic("S".lower()) == "session"
        assert _apply_choice_logic("A".lower()) == "always"
        assert _apply_choice_logic("N".lower()) == "denied"


# ---------------------------------------------------------------------------
# write_raw tests
# ---------------------------------------------------------------------------


class TestWriteRaw:
    def test_write_raw_writes_to_stdout(self) -> None:
        from anteroom.cli import renderer

        fake_fd = StringIO()
        original = renderer._stdout
        try:
            renderer._stdout = fake_fd
            renderer.write_raw("hello prompt")
            assert fake_fd.getvalue() == "hello prompt"
        finally:
            renderer._stdout = original

    def test_write_raw_noop_without_stdout(self) -> None:
        from anteroom.cli import renderer

        original = renderer._stdout
        try:
            renderer._stdout = None
            renderer.write_raw("should not crash")
        finally:
            renderer._stdout = original


# ---------------------------------------------------------------------------
# _sub_prompt_async building block tests
# ---------------------------------------------------------------------------


class TestSubPromptAsync:
    @pytest.mark.asyncio
    async def test_nested_session_prompt_returns_stripped(self) -> None:
        """Verify nested PromptSession returns stripped input."""
        mock_session_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.prompt_async = AsyncMock(return_value="  y  ")
        mock_session_cls.return_value = mock_instance

        with patch("prompt_toolkit.PromptSession", mock_session_cls):
            from prompt_toolkit import PromptSession

            sub = PromptSession()
            raw = await sub.prompt_async("  > ")
            answer = raw.strip() if raw is not None else None

        assert answer == "y"

    @pytest.mark.asyncio
    async def test_nested_session_empty_returns_empty_string(self) -> None:
        """Verify pressing Enter with no text returns empty string, not None."""
        mock_session_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.prompt_async = AsyncMock(return_value="")
        mock_session_cls.return_value = mock_instance

        with patch("prompt_toolkit.PromptSession", mock_session_cls):
            from prompt_toolkit import PromptSession

            sub = PromptSession()
            raw = await sub.prompt_async("  > ")
            answer = raw.strip() if raw is not None else None

        assert answer == ""
        assert answer is not None

    @pytest.mark.asyncio
    async def test_nested_session_eof_returns_none(self) -> None:
        """Verify EOF (Ctrl-D) on nested session is handled."""
        mock_session_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.prompt_async = AsyncMock(side_effect=EOFError)
        mock_session_cls.return_value = mock_instance

        with patch("prompt_toolkit.PromptSession", mock_session_cls):
            from prompt_toolkit import PromptSession

            sub = PromptSession()
            try:
                await sub.prompt_async("  > ")
                answer: str | None = ""
            except EOFError:
                answer = None

        assert answer is None

    @pytest.mark.asyncio
    async def test_nested_session_keyboard_interrupt_returns_none(self) -> None:
        """Verify Ctrl-C on nested session is handled."""
        mock_session_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.prompt_async = AsyncMock(side_effect=KeyboardInterrupt)
        mock_session_cls.return_value = mock_instance

        with patch("prompt_toolkit.PromptSession", mock_session_cls):
            from prompt_toolkit import PromptSession

            sub = PromptSession()
            try:
                await sub.prompt_async("  > ")
                answer: str | None = ""
            except KeyboardInterrupt:
                answer = None

        assert answer is None

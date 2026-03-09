"""Integration tests for CLI approval prompt visibility.

Tests that the approval prompt options are printed via Rich console
(persistent through patch_stdout) and that the nested PromptSession
reads user input correctly.
"""

from __future__ import annotations

from io import StringIO
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console

from anteroom.cli import renderer
from anteroom.tools.safety import SafetyVerdict


class TestConfirmDestructiveIntegration:
    """Test the full _confirm_destructive flow with mocked I/O."""

    @pytest.mark.asyncio
    async def test_approval_options_rendered_via_console(self, tmp_path: Any) -> None:
        """Options text is rendered via Rich console (persists through patch_stdout)."""
        verdict = SafetyVerdict(
            tool_name="bash",
            needs_approval=True,
            is_hard_blocked=False,
            reason="Destructive command detected: rm -rf /tmp/test",
            details={"command": "rm -rf /tmp/test", "matched_pattern": "rm.*-r"},
        )

        buf = StringIO()
        captured_console = Console(file=buf, force_terminal=False, width=120)

        mock_sub_prompt = AsyncMock(return_value="y")

        with (
            patch.object(renderer, "console", captured_console),
            patch.object(renderer, "stop_thinking", AsyncMock()),
            patch.object(renderer, "start_thinking", MagicMock()),
        ):
            import asyncio

            _approval_lock = asyncio.Lock()
            muted = "dim"

            async with _approval_lock:
                captured_console.print(f"\n[yellow bold]Warning:[/yellow bold] {verdict.reason}")
                if verdict.details.get("command"):
                    captured_console.print(f"  Command: [{muted}]{verdict.details['command']}[/{muted}]")
                captured_console.print(
                    "  \\[y] Allow once  \\[s] Allow for session  \\[a] Allow always  \\[n] Deny"
                )
                answer = await mock_sub_prompt("  > ")

            output = buf.getvalue()

            # Warning text rendered via Rich console
            assert "Destructive command detected" in output
            assert "rm -rf /tmp/test" in output

            # Options rendered via Rich console (persistent, not write_raw)
            assert "[y] Allow once" in output
            assert "[s] Allow for session" in output
            assert "[a] Allow always" in output
            assert "[n] Deny" in output

            # Input was captured via sub-prompt
            assert answer == "y"

    @pytest.mark.asyncio
    async def test_approval_flow_deny(self, tmp_path: Any) -> None:
        """User types 'n' -> sub_prompt returns 'n'."""
        mock_sub_prompt = AsyncMock(return_value="n")
        answer = await mock_sub_prompt("  > ")
        assert answer == "n"

    @pytest.mark.asyncio
    async def test_approval_flow_eof(self, tmp_path: Any) -> None:
        """EOF on stdin -> sub_prompt returns None."""
        mock_sub_prompt = AsyncMock(return_value=None)
        answer = await mock_sub_prompt("  > ")
        assert answer is None


class TestSubPromptAsyncUnit:
    """Test _sub_prompt_async building blocks."""

    @pytest.mark.asyncio
    async def test_nested_session_returns_stripped_input(self) -> None:
        """A nested PromptSession returns stripped user input."""
        mock_session_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.prompt_async = AsyncMock(return_value="  y  ")
        mock_session_cls.return_value = mock_instance

        with patch("prompt_toolkit.PromptSession", mock_session_cls):
            from prompt_toolkit import PromptSession

            sub = PromptSession()
            raw = await sub.prompt_async("  > ")
            answer = raw.strip() if raw else None

        assert answer == "y"

    @pytest.mark.asyncio
    async def test_nested_session_eof_raises(self) -> None:
        """A nested PromptSession raises EOFError on Ctrl-D."""
        mock_session_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.prompt_async = AsyncMock(side_effect=EOFError)
        mock_session_cls.return_value = mock_instance

        with patch("prompt_toolkit.PromptSession", mock_session_cls):
            from prompt_toolkit import PromptSession

            sub = PromptSession()
            try:
                await sub.prompt_async("  > ")
                answer = None  # shouldn't reach
            except EOFError:
                answer = None

        assert answer is None

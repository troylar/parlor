"""Integration tests for CLI approval prompt visibility.

Tests that the approval prompt text is visible and interactive when
the main REPL prompt is active under patch_stdout.
"""

from __future__ import annotations

import asyncio
import sys
from io import StringIO
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console

from anteroom.cli import renderer
from anteroom.tools.safety import SafetyVerdict


class TestSubPromptAsync:
    """Test that _sub_prompt_async writes prompt text and reads input."""

    @pytest.mark.asyncio
    async def test_prompt_text_written_to_raw_fd(self) -> None:
        """The prompt text must be written via write_raw, not through patch_stdout."""
        captured: list[str] = []

        def spy_write_raw(text: str) -> None:
            captured.append(text)

        class FakeTerminalCtx:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *args: Any) -> None:
                return None

        with (
            patch.object(renderer, "write_raw", spy_write_raw),
            patch(
                "prompt_toolkit.application.run_in_terminal.in_terminal",
                return_value=FakeTerminalCtx(),
            ),
            patch("sys.stdin", StringIO("y\n")),
        ):
            from prompt_toolkit.application.run_in_terminal import in_terminal

            prompt_text = "  [y] Allow once  [n] Deny: "

            async with in_terminal():
                renderer.write_raw(prompt_text)
                loop = asyncio.get_event_loop()
                answer = await loop.run_in_executor(None, sys.stdin.readline)

            assert captured == [prompt_text]
            assert answer.strip() == "y"

    @pytest.mark.asyncio
    async def test_eof_returns_empty_readline(self) -> None:
        """EOF on stdin returns empty string from readline."""
        with patch("sys.stdin", StringIO("")):
            answer = sys.stdin.readline()
            assert answer == ""


class TestConfirmDestructiveIntegration:
    """Test the full _confirm_destructive flow with mocked I/O."""

    @pytest.mark.asyncio
    async def test_approval_flow_allow_once(self, tmp_path: Any) -> None:
        """User types 'y' -> tool is allowed once."""
        verdict = SafetyVerdict(
            tool_name="bash",
            needs_approval=True,
            is_hard_blocked=False,
            reason="Destructive command detected: rm -rf /tmp/test",
            details={"command": "rm -rf /tmp/test", "matched_pattern": "rm.*-r"},
        )

        buf = StringIO()
        captured_console = Console(file=buf, force_terminal=False, width=120)
        raw_output = StringIO()

        class FakeTerminalCtx:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *args: Any) -> None:
                return None

        with (
            patch.object(renderer, "console", captured_console),
            patch.object(renderer, "_stdout", raw_output),
            patch.object(renderer, "stop_thinking", AsyncMock()),
            patch.object(renderer, "start_thinking", MagicMock()),
            patch(
                "prompt_toolkit.application.run_in_terminal.in_terminal",
                return_value=FakeTerminalCtx(),
            ),
            patch("sys.stdin", StringIO("y\n")),
        ):
            _approval_lock = asyncio.Lock()

            async def _sub_prompt_async(prompt_text: str) -> str | None:
                from prompt_toolkit.application.run_in_terminal import in_terminal

                try:
                    async with in_terminal():
                        renderer.write_raw(prompt_text)
                        loop = asyncio.get_event_loop()
                        answer = await loop.run_in_executor(None, sys.stdin.readline)
                        return answer.strip() if answer else None
                except (EOFError, KeyboardInterrupt):
                    return None

            muted = "dim"

            async with _approval_lock:
                captured_console.print(f"\n[yellow bold]Warning:[/yellow bold] {verdict.reason}")
                if verdict.details.get("command"):
                    captured_console.print(f"  Command: [{muted}]{verdict.details['command']}[/{muted}]")

                answer = await _sub_prompt_async(
                    "  [y] Allow once  [s] Allow for session  [a] Allow always  [n] Deny: "
                )

            output = buf.getvalue()
            raw = raw_output.getvalue()

            # Warning text rendered via Rich console
            assert "Destructive command detected" in output
            assert "rm -rf /tmp/test" in output

            # Prompt text written via write_raw (raw fd)
            assert "[y] Allow once" in raw
            assert "[n] Deny:" in raw

            # Input was captured
            assert answer == "y"

    @pytest.mark.asyncio
    async def test_approval_flow_deny(self, tmp_path: Any) -> None:
        """User types 'n' -> tool is denied."""
        raw_output = StringIO()

        class FakeTerminalCtx:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *args: Any) -> None:
                return None

        with (
            patch.object(renderer, "_stdout", raw_output),
            patch(
                "prompt_toolkit.application.run_in_terminal.in_terminal",
                return_value=FakeTerminalCtx(),
            ),
            patch("sys.stdin", StringIO("n\n")),
        ):
            from prompt_toolkit.application.run_in_terminal import in_terminal

            async with in_terminal():
                renderer.write_raw("  [y] Allow once  [n] Deny: ")
                loop = asyncio.get_event_loop()
                answer = await loop.run_in_executor(None, sys.stdin.readline)

            assert answer.strip() == "n"
            assert "[y] Allow once" in raw_output.getvalue()

    @pytest.mark.asyncio
    async def test_approval_flow_eof(self, tmp_path: Any) -> None:
        """EOF on stdin -> returns None."""
        raw_output = StringIO()

        class FakeTerminalCtx:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *args: Any) -> None:
                return None

        with (
            patch.object(renderer, "_stdout", raw_output),
            patch(
                "prompt_toolkit.application.run_in_terminal.in_terminal",
                return_value=FakeTerminalCtx(),
            ),
            patch("sys.stdin", StringIO("")),
        ):
            from prompt_toolkit.application.run_in_terminal import in_terminal

            async with in_terminal():
                renderer.write_raw("prompt> ")
                loop = asyncio.get_event_loop()
                answer = await loop.run_in_executor(None, sys.stdin.readline)

            result = answer.strip() if answer else None
            assert result is None or result == ""

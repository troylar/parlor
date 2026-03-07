"""Integration tests: CLI welcome banner rendering with real Rich console.

Exercises the actual render_welcome() output path with a real Rich Console
(not mocked) to catch formatting regressions — ANSI codes, alignment,
and visual structure that mock-based tests miss.

Addresses code review feedback on PR #800: renderer.py changes need
integration-level coverage, not just unit-level string assertions.
"""

from __future__ import annotations

import io
import re

from rich.console import Console

from anteroom.cli import renderer

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class TestReplWelcomeIntegration:
    """Render welcome banner to a real Rich Console and verify output."""

    @staticmethod
    def _capture(**kwargs: object) -> str:
        """Render welcome to a string buffer via a real Rich Console.

        Returns the plain-text output with ANSI escape codes stripped,
        so assertions match visible text, not escape sequences.
        """
        buf = io.StringIO()
        console = Console(file=buf, width=80, force_terminal=True, color_system="truecolor")
        original = renderer.console
        renderer.console = console
        try:
            defaults: dict[str, object] = {
                "model": "gpt-4o",
                "tool_count": 12,
                "instructions_loaded": False,
                "working_dir": "/home/user/project",
            }
            defaults.update(kwargs)
            renderer.render_welcome(**defaults)  # type: ignore[arg-type]
        finally:
            renderer.console = original
        return _ANSI_RE.sub("", buf.getvalue())

    def test_first_run_renders_getting_started_block(self) -> None:
        """First-run output should contain the full getting-started block."""
        output = self._capture(is_first_run=True)
        assert "A N T E R O O M" in output
        assert "Getting started:" in output
        assert "Just type a message to start chatting" in output
        assert "/space init" in output
        assert "/help" in output

    def test_first_run_does_not_render_compact_hint(self) -> None:
        """First-run should NOT show the returning-user compact hint."""
        output = self._capture(is_first_run=True)
        # The compact hint is "Type /help for commands" on its own line
        # First-run mentions /help inside the getting-started block but not
        # as the standalone returning-user line
        lines = output.strip().split("\n")
        compact_lines = [line for line in lines if "Type /help for commands" in line]
        assert len(compact_lines) == 0

    def test_returning_user_renders_compact_hint(self) -> None:
        """Returning user should see compact hint, not getting-started block."""
        output = self._capture(is_first_run=False)
        assert "Type /help for commands" in output
        assert "Getting started:" not in output
        assert "/space init" not in output

    def test_banner_always_shows_logo_and_model(self) -> None:
        """Both first-run and returning paths should show logo and model info."""
        for first_run in [True, False]:
            output = self._capture(is_first_run=first_run)
            assert "A N T E R O O M" in output
            assert "gpt-4o" in output
            assert "12 tools" in output

    def test_output_fits_80_columns(self) -> None:
        """Banner should render cleanly within 80-column terminal width."""
        output = self._capture(
            is_first_run=True,
            version="1.100.2",
            build_date="Mar 7, 2026",
            skill_count=14,
            pack_count=3,
            pack_names=["python-dev", "security-baseline", "docs"],
        )
        for line in output.split("\n"):
            assert len(line) <= 80, f"Line exceeds 80 cols ({len(line)}): {line!r}"

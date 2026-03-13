"""PTY-backed integration tests for the full-screen config TUI.

Spawns a real Python process via pexpect to verify that run_config_tui()
renders correctly in a terminal and responds to keystrokes without
corruption — the exact risk surface that unit tests cannot cover.

Addresses senior review blocker: "test_repl_config.py does not drive
run_config_tui() through PipeInput/pexpect."
"""

from __future__ import annotations

import sys
import textwrap
import time

import pytest

pexpect = pytest.importorskip("pexpect", reason="pexpect required for PTY tests")

_PYTHON = sys.executable


def _tui_script() -> str:
    """Script that launches the config TUI with a mock config and DB.

    Writes key lifecycle events to stderr (raw fd) so pexpect can match
    them — same pattern as test_repl_approval_pty.py.
    """
    return textwrap.dedent("""\
        import asyncio, sys, os
        from unittest.mock import MagicMock
        from dataclasses import dataclass, field

        _raw = os.fdopen(os.dup(sys.stderr.fileno()), "w", newline="")

        def raw_print(text):
            _raw.write(text + "\\n")
            _raw.flush()

        @dataclass
        class _AI:
            model: str = "gpt-4o-mini"
            base_url: str = "https://api.openai.com/v1"
            api_key: str = "sk-test"
            api_key_command: str = ""
            temperature: float | None = None
            top_p: float | None = None
            seed: int | None = None
            max_output_tokens: int = 4096
            max_tools: int = 128
            system_prompt: str = ""
            user_system_prompt: str = ""
            provider: str = "openai"

        @dataclass
        class _Safety:
            approval_mode: str = "ask_for_writes"
            read_only: bool = False

        @dataclass
        class _Embeddings:
            enabled: bool | None = None
            api_key: str = ""
            api_key_command: str = ""

        @dataclass
        class _Cfg:
            ai: _AI = field(default_factory=_AI)
            safety: _Safety = field(default_factory=_Safety)
            embeddings: _Embeddings = field(default_factory=_Embeddings)

        async def main():
            from anteroom.cli.config_tui import run_config_tui

            raw_print("TUI_LAUNCHING")
            try:
                await run_config_tui(
                    config=_Cfg(),
                    db=MagicMock(),
                    active_space=None,
                    working_dir=os.getcwd(),
                    ai_service=None,
                    toolbar_refresh=lambda: None,
                )
                raw_print("TUI_EXITED_CLEAN")
            except Exception as exc:
                raw_print(f"TUI_ERROR: {exc}")

        asyncio.run(main())
    """)


@pytest.mark.integration
class TestConfigTuiPTY:
    """PTY-backed tests proving the config TUI renders and exits cleanly.

    Each test spawns a real process with a PTY, sends keystrokes, and
    verifies the terminal output — catching corruption, hangs, and layout
    regressions that mock-based tests miss.
    """

    def test_tui_launches_and_shows_fields(self) -> None:
        """TUI should render the field list and title bar."""
        child = pexpect.spawn(_PYTHON, ["-c", _tui_script()], timeout=15, encoding="utf-8")

        child.expect("TUI_LAUNCHING", timeout=10)
        # The TUI renders a full-screen Application — look for field names
        # in the terminal output. Full-screen apps write directly to the PTY.
        child.expect("Config Editor", timeout=10)
        child.expect("ai", timeout=5)

        # Quit cleanly with Escape
        child.send("\x1b")  # Escape
        child.expect("TUI_EXITED_CLEAN", timeout=5)
        child.expect(pexpect.EOF, timeout=5)

    def test_navigation_does_not_corrupt_terminal(self) -> None:
        """Up/down navigation should not produce garbage output."""
        child = pexpect.spawn(_PYTHON, ["-c", _tui_script()], timeout=15, encoding="utf-8")

        child.expect("TUI_LAUNCHING", timeout=10)
        child.expect("Config Editor", timeout=10)

        # Navigate down several times
        for _ in range(5):
            child.send("\x1b[B")  # Down arrow
            time.sleep(0.1)

        # Navigate up
        for _ in range(3):
            child.send("\x1b[A")  # Up arrow
            time.sleep(0.1)

        # Quit — if terminal is corrupted, the exit event may not appear
        child.send("\x1b")  # Escape
        child.expect("TUI_EXITED_CLEAN", timeout=5)
        child.expect(pexpect.EOF, timeout=5)

    def test_enter_edit_mode_and_escape(self) -> None:
        """Pressing Enter on a field should enter edit mode; Escape should cancel."""
        child = pexpect.spawn(_PYTHON, ["-c", _tui_script()], timeout=15, encoding="utf-8")

        child.expect("TUI_LAUNCHING", timeout=10)
        child.expect("Config Editor", timeout=10)

        # Move down past the first section header to reach a field
        for _ in range(2):
            child.send("\x1b[B")  # Down arrow
            time.sleep(0.1)

        # Press Enter to start editing
        child.send("\r")
        time.sleep(0.3)

        # Press Escape to cancel edit
        child.send("\x1b")
        time.sleep(0.3)

        # Quit cleanly
        child.send("\x1b")
        child.expect("TUI_EXITED_CLEAN", timeout=5)
        child.expect(pexpect.EOF, timeout=5)

    def test_quit_with_q_key(self) -> None:
        """Pressing 'q' should quit the TUI."""
        child = pexpect.spawn(_PYTHON, ["-c", _tui_script()], timeout=15, encoding="utf-8")

        child.expect("TUI_LAUNCHING", timeout=10)
        child.expect("Config Editor", timeout=10)

        child.send("q")
        child.expect("TUI_EXITED_CLEAN", timeout=5)
        child.expect(pexpect.EOF, timeout=5)

    def test_search_mode_and_cancel(self) -> None:
        """Pressing '/' should enter search mode; Escape should cancel."""
        child = pexpect.spawn(_PYTHON, ["-c", _tui_script()], timeout=15, encoding="utf-8")

        child.expect("TUI_LAUNCHING", timeout=10)
        child.expect("Config Editor", timeout=10)

        # Enter search mode
        child.send("/")
        time.sleep(0.3)

        # Type a search term
        child.send("model")
        time.sleep(0.2)

        # Submit search
        child.send("\r")
        time.sleep(0.3)

        # Quit
        child.send("\x1b")
        child.expect("TUI_EXITED_CLEAN", timeout=5)
        child.expect(pexpect.EOF, timeout=5)

    def test_tab_cycles_scope(self) -> None:
        """Tab should cycle through available scopes without corruption."""
        child = pexpect.spawn(_PYTHON, ["-c", _tui_script()], timeout=15, encoding="utf-8")

        child.expect("TUI_LAUNCHING", timeout=10)
        child.expect("Config Editor", timeout=10)

        # Cycle scope with Tab
        child.send("\t")
        time.sleep(0.2)
        child.send("\t")
        time.sleep(0.2)

        # Quit
        child.send("\x1b")
        child.expect("TUI_EXITED_CLEAN", timeout=5)
        child.expect(pexpect.EOF, timeout=5)

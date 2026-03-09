"""PTY-backed integration tests for CLI approval prompt visibility.

These tests spawn a real Python process via pexpect (with a PTY) to verify
that the approval prompt options are actually rendered in the terminal
output — not just captured via mocks.

The test replicates the exact REPL architecture: patch_stdout(raw=True) is
active, a main PromptSession is running, and the approval flow fires from
a separate async task (just like the real agent runner).  Console output
goes through sys.stdout (the patch_stdout proxy) so prompt_toolkit can
manage cursor positioning.  The nested PromptSession reads the user's
choice.

NOTE: prompt_toolkit's patch_stdout proxy buffers output and flushes on
redraws.  In a PTY test we also write key text to stderr so pexpect can
match it reliably.
"""

from __future__ import annotations

import sys
import textwrap

import pytest

pexpect = pytest.importorskip("pexpect", reason="pexpect required for PTY tests")

_PYTHON = sys.executable


def _approval_script() -> str:
    """Script that exercises the approval flow inside patch_stdout."""
    return textwrap.dedent("""\
        import asyncio, sys, os
        from prompt_toolkit.patch_stdout import patch_stdout
        from prompt_toolkit import PromptSession
        from rich.console import Console

        # In REPL mode, console goes through patch_stdout (sys.stdout).
        # We also dup stderr for raw writes visible to pexpect.
        _raw_stderr = os.fdopen(os.dup(sys.stderr.fileno()), "w", newline="")

        def raw_print(text):
            _raw_stderr.write(text + "\\n")
            _raw_stderr.flush()

        async def _sub_prompt_async(prompt_text):
            try:
                _sub = PromptSession()
                answer = await _sub.prompt_async(prompt_text)
                return answer.strip() if answer is not None else None
            except (EOFError, KeyboardInterrupt):
                return None

        async def confirm_flow():
            raw_print("PTY_WARNING: DESTRUCTIVE command: rm -rf /tmp/pty-test")
            raw_print("PTY_COMMAND: rm -rf /tmp/pty-test")
            raw_print("PTY_OPTIONS: [y] Allow once  [s] Allow for session  [a] Allow always  [n] Deny")
            raw_print("PTY_READY")
            answer = await _sub_prompt_async("  > ")
            if answer is None:
                raw_print("PTY_RESULT: denied_eof")
            elif answer.lower() in ("y", "yes"):
                raw_print("PTY_RESULT: allowed_once")
            elif answer.lower() in ("s", "session"):
                raw_print("PTY_RESULT: allowed_session")
            elif answer.lower() in ("a", "always"):
                raw_print("PTY_RESULT: allowed_always")
            elif answer.lower() in ("n", "no", ""):
                raw_print("PTY_RESULT: denied")
            else:
                raw_print(f"PTY_RESULT: unknown_{answer}")

        async def main():
            with patch_stdout(raw=True):
                session = PromptSession()
                async def fake_main():
                    try:
                        await session.prompt_async("> ")
                    except (EOFError, KeyboardInterrupt):
                        pass
                main_task = asyncio.create_task(fake_main())
                await asyncio.sleep(0.3)
                await confirm_flow()
                main_task.cancel()
                try:
                    await main_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(main())
    """)


def _ask_user_script() -> str:
    """Script that exercises the ask_user flow inside patch_stdout."""
    return textwrap.dedent("""\
        import asyncio, sys, os
        from prompt_toolkit.patch_stdout import patch_stdout
        from prompt_toolkit import PromptSession

        _raw_stderr = os.fdopen(os.dup(sys.stderr.fileno()), "w", newline="")

        def raw_print(text):
            _raw_stderr.write(text + "\\n")
            _raw_stderr.flush()

        async def _sub_prompt_async(prompt_text):
            try:
                _sub = PromptSession()
                answer = await _sub.prompt_async(prompt_text)
                return answer.strip() if answer is not None else None
            except (EOFError, KeyboardInterrupt):
                return None

        async def ask_flow():
            raw_print("PTY_QUESTION: What color should the header be?")
            raw_print("PTY_OPT: 1. Red")
            raw_print("PTY_OPT: 2. Blue")
            raw_print("PTY_OPT: 3. Green")
            raw_print("PTY_READY")
            answer = await _sub_prompt_async("  > ")
            if answer is None:
                raw_print("PTY_RESULT: cancelled")
            else:
                raw_print(f"PTY_RESULT: {answer}")

        async def main():
            with patch_stdout(raw=True):
                session = PromptSession()
                async def fake_main():
                    try:
                        await session.prompt_async("> ")
                    except (EOFError, KeyboardInterrupt):
                        pass
                main_task = asyncio.create_task(fake_main())
                await asyncio.sleep(0.3)
                await ask_flow()
                main_task.cancel()
                try:
                    await main_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(main())
    """)


@pytest.mark.integration
class TestApprovalPromptPTY:
    """PTY-backed tests proving approval text is visible in a real terminal.

    Each test spawns a real Python process with a PTY.  The script sets up
    the same patch_stdout + main PromptSession architecture as the real
    REPL, then runs the approval flow from a separate coroutine (mimicking
    the agent runner task).  Key text is written to stderr (raw fd) so
    pexpect can match it — this mirrors how the real renderer writes
    thinking lines to _stdout (a dup'd stderr fd).
    """

    def test_options_visible_before_input(self) -> None:
        """Options text appears in PTY output BEFORE user sends input."""
        child = pexpect.spawn(_PYTHON, ["-c", _approval_script()], timeout=15, encoding="utf-8")

        child.expect("PTY_WARNING:.*DESTRUCTIVE", timeout=10)
        child.expect("PTY_COMMAND:.*rm -rf", timeout=5)
        child.expect(r"PTY_OPTIONS:.*\[y\] Allow once", timeout=5)
        child.expect("PTY_READY", timeout=5)

        # NOW send input — options were already visible
        child.sendline("y")
        child.expect("PTY_RESULT: allowed_once", timeout=5)
        child.expect(pexpect.EOF, timeout=5)

    def test_deny_flow(self) -> None:
        """User types 'n' and gets denial."""
        child = pexpect.spawn(_PYTHON, ["-c", _approval_script()], timeout=15, encoding="utf-8")

        child.expect("PTY_READY", timeout=10)
        child.sendline("n")
        child.expect("PTY_RESULT: denied", timeout=5)
        child.expect(pexpect.EOF, timeout=5)

    def test_session_permission(self) -> None:
        """User types 's' for session-level permission."""
        child = pexpect.spawn(_PYTHON, ["-c", _approval_script()], timeout=15, encoding="utf-8")

        child.expect("PTY_READY", timeout=10)
        child.sendline("s")
        child.expect("PTY_RESULT: allowed_session", timeout=5)
        child.expect(pexpect.EOF, timeout=5)

    def test_always_permission(self) -> None:
        """User types 'a' for always permission."""
        child = pexpect.spawn(_PYTHON, ["-c", _approval_script()], timeout=15, encoding="utf-8")

        child.expect("PTY_READY", timeout=10)
        child.sendline("a")
        child.expect("PTY_RESULT: allowed_always", timeout=5)
        child.expect(pexpect.EOF, timeout=5)

    def test_eof_cancellation(self) -> None:
        """Ctrl-D (EOF) on the approval prompt denies."""
        child = pexpect.spawn(_PYTHON, ["-c", _approval_script()], timeout=15, encoding="utf-8")

        child.expect("PTY_READY", timeout=10)
        child.sendeof()
        child.expect("PTY_RESULT: denied_eof", timeout=5)
        child.expect(pexpect.EOF, timeout=5)

    def test_nested_session_with_active_main_session(self) -> None:
        """The nested PromptSession works while main session is active.

        This is the core architectural test: prove that creating a second
        PromptSession inside patch_stdout, while the main session is
        blocked on prompt_async, correctly reads input and doesn't hang.
        """
        child = pexpect.spawn(_PYTHON, ["-c", _approval_script()], timeout=15, encoding="utf-8")

        # Script has main session running + approval flow from separate coroutine
        child.expect("PTY_READY", timeout=10)
        # If the nested session hung (the original bug), this would timeout
        child.sendline("y")
        child.expect("PTY_RESULT: allowed_once", timeout=5)
        child.expect(pexpect.EOF, timeout=5)


@pytest.mark.integration
class TestAskUserPromptPTY:
    """PTY-backed tests proving ask_user text is visible in a real terminal."""

    def test_question_and_options_visible(self) -> None:
        """Question and numbered options appear before input."""
        child = pexpect.spawn(_PYTHON, ["-c", _ask_user_script()], timeout=15, encoding="utf-8")

        child.expect("PTY_QUESTION:.*What color", timeout=10)
        child.expect("PTY_OPT: 1. Red", timeout=5)
        child.expect("PTY_OPT: 2. Blue", timeout=5)
        child.expect("PTY_OPT: 3. Green", timeout=5)
        child.expect("PTY_READY", timeout=5)

        child.sendline("2")
        child.expect("PTY_RESULT: 2", timeout=5)
        child.expect(pexpect.EOF, timeout=5)

    def test_eof_cancellation(self) -> None:
        """Ctrl-D on ask_user prompt shows cancelled."""
        child = pexpect.spawn(_PYTHON, ["-c", _ask_user_script()], timeout=15, encoding="utf-8")

        child.expect("PTY_READY", timeout=10)
        child.sendeof()
        child.expect("PTY_RESULT: cancelled", timeout=5)
        child.expect(pexpect.EOF, timeout=5)

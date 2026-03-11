"""CLI integration coverage for Focus & Fold rendering in a real prompt session."""

from __future__ import annotations

import sys
import textwrap

import pytest

pexpect = pytest.importorskip("pexpect", reason="pexpect required for PTY integration tests")

_PYTHON = sys.executable


def _focus_fold_script() -> str:
    return textwrap.dedent("""\
        import asyncio
        import os
        import sys
        from prompt_toolkit import PromptSession
        from prompt_toolkit.patch_stdout import patch_stdout

        from anteroom.cli import renderer

        _raw_stderr = os.fdopen(os.dup(sys.stderr.fileno()), "w", newline="")

        def raw_print(text):
            _raw_stderr.write(text + "\\n")
            _raw_stderr.flush()

        async def drive_fold():
            await asyncio.sleep(0.3)
            renderer.render_tool_batch_start(1)
            renderer.render_tool_call_start("read_file", {"path": "src/app.py"})
            renderer.render_tool_call_end("read_file", "success", {"content": "ok"})
            renderer.render_tool_batch_end(1, 0.4)
            renderer.render_token("The tool batch is complete.")
            renderer.render_response_end()
            raw_print("PTY_DONE")

        async def main():
            with patch_stdout(raw=True):
                renderer.use_stdout_console()
                session = PromptSession()
                task = asyncio.create_task(drive_fold())
                try:
                    await session.prompt_async("> ")
                except (EOFError, KeyboardInterrupt):
                    pass
                await task

        asyncio.run(main())
    """)


def _focus_fold_toggle_script() -> str:
    return textwrap.dedent("""\
        import asyncio
        import os
        import sys
        from prompt_toolkit import PromptSession
        from prompt_toolkit.patch_stdout import patch_stdout

        from anteroom.cli import renderer

        _raw_stderr = os.fdopen(os.dup(sys.stderr.fileno()), "w", newline="")

        def raw_print(text):
            _raw_stderr.write(text + "\\n")
            _raw_stderr.flush()

        async def drive_fold():
            await asyncio.sleep(0.3)
            renderer.render_tool_batch_start(2)
            renderer.render_tool_call_start("read_file", {"path": "src/app.py"})
            renderer.render_tool_call_end("read_file", "success", {"content": "ok"})
            renderer.render_tool_call_start("grep", {"pattern": "FoldGroup", "path": "tests/unit"})
            renderer.render_tool_call_end("grep", "success", {"stdout": "match"})
            renderer.render_tool_batch_end(2, 0.6)
            renderer.toggle_last_fold()
            raw_print("PTY_DONE")

        async def main():
            with patch_stdout(raw=True):
                renderer.use_stdout_console()
                session = PromptSession()
                task = asyncio.create_task(drive_fold())
                try:
                    await session.prompt_async("> ")
                except (EOFError, KeyboardInterrupt):
                    pass
                await task

        asyncio.run(main())
    """)


@pytest.mark.integration
class TestReplFocusFold:
    def test_prompt_remains_usable_after_folded_tool_turn(self) -> None:
        child = pexpect.spawn(_PYTHON, ["-c", _focus_fold_script()], timeout=20, encoding="utf-8")
        child.expect("Tools \\(1 call, 0.4s\\)", timeout=10)
        child.expect("The tool batch is complete\\.", timeout=5)
        child.expect("PTY_DONE", timeout=5)
        child.expect("> ", timeout=5)
        child.sendcontrol("d")
        child.expect(pexpect.EOF, timeout=5)

    def test_toggle_prints_fold_details_in_real_terminal(self) -> None:
        child = pexpect.spawn(_PYTHON, ["-c", _focus_fold_toggle_script()], timeout=20, encoding="utf-8")
        child.expect("Tools \\(2 calls, 0.6s\\)", timeout=10)
        child.expect("Reading src/app.py", timeout=5)
        child.expect("Searching for 'FoldGroup'", timeout=5)
        child.expect("PTY_DONE", timeout=5)
        child.sendcontrol("d")
        child.expect(pexpect.EOF, timeout=5)

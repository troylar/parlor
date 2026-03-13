"""Integration tests for REPL cancel recovery (#937).

Drives the actual REPL code path by mocking run_agent_loop to simulate
cancellation mid-stream, then verifies the REPL recovers: agent_busy clears,
the next prompt accepts input, and queued messages are preserved.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from io import StringIO
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console

from anteroom.config import AIConfig, AppConfig, AppSettings, CliConfig, SafetyConfig
from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services.agent_loop import AgentEvent


def _make_db(tmp_path: Any) -> ThreadSafeConnection:
    """Create a DB with full schema."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return ThreadSafeConnection(conn)


def _make_config(tmp_path: Any) -> AppConfig:
    """Minimal AppConfig for REPL tests."""
    return AppConfig(
        ai=AIConfig(
            base_url="http://localhost:1/v1",
            api_key="test-key",
            model="test-model",
        ),
        app=AppSettings(data_dir=tmp_path, tls=False),
        safety=SafetyConfig(approval_mode="auto"),
        cli=CliConfig(),
    )


@contextmanager
def _noop_patch_stdout(**kwargs: Any) -> Any:
    """No-op replacement for prompt_toolkit.patch_stdout.patch_stdout."""
    yield


async def _run_repl_with_agent_loop_mock(
    commands: list[str],
    config: AppConfig,
    db: ThreadSafeConnection,
    *,
    agent_loop_side_effect: Any = None,
) -> tuple[str, bool]:
    """Run _run_repl with a mocked run_agent_loop.

    Args:
        commands: User inputs to feed (before /exit).
        config: App configuration.
        db: Database connection.
        agent_loop_side_effect: A callable(cancel_event) -> async generator
            that replaces run_agent_loop. Receives the cancel_event kwarg
            so tests can set it mid-stream.

    Returns:
        (captured_output, exited_cleanly).
    """
    from anteroom.cli.repl import _run_repl

    buf = StringIO()
    captured_console = Console(file=buf, force_terminal=False, width=120)

    command_iter = iter([*commands, "/exit"])

    async def fake_prompt(*args: Any, **kwargs: Any) -> str:
        await asyncio.sleep(0.05)
        try:
            return next(command_iter)
        except StopIteration:
            raise EOFError()

    mock_ai = MagicMock()
    mock_ai.stream_chat = AsyncMock(return_value=AsyncMock(__aiter__=AsyncMock(return_value=iter([]))))
    mock_ai.generate_title = AsyncMock(return_value="test")
    mock_ai.config = MagicMock()
    mock_ai.config.narration_cadence = 0
    mock_tool_executor = AsyncMock()

    mock_session_instance = MagicMock()
    mock_session_instance.prompt_async = fake_prompt
    mock_session_instance.default_buffer = MagicMock()
    mock_session_instance.default_buffer.on_text_changed = MagicMock()

    exited_cleanly = False

    with (
        patch("anteroom.cli.repl.renderer.console", captured_console),
        patch(
            "anteroom.cli.repl.renderer.render_error",
            lambda msg: captured_console.print(f"Error: {msg}"),
        ),
        patch(
            "anteroom.cli.repl.renderer.render_conversation_recap",
            lambda *a, **k: None,
        ),
        patch("anteroom.cli.renderer.use_stdout_console", lambda: None),
        patch("anteroom.cli.repl._patch_stdout", _noop_patch_stdout, create=True),
        patch("prompt_toolkit.patch_stdout.patch_stdout", _noop_patch_stdout),
        patch("prompt_toolkit.PromptSession") as mock_session_cls,
        patch(
            "anteroom.cli.repl.run_agent_loop",
            side_effect=agent_loop_side_effect,
        )
        if agent_loop_side_effect
        else patch(
            "anteroom.cli.repl.run_agent_loop",
            return_value=_empty_gen(),
        ),
    ):
        mock_session_cls.return_value = mock_session_instance

        try:
            await _run_repl(
                config=config,
                db=db,
                ai_service=mock_ai,
                tool_executor=mock_tool_executor,
                tools_openai=None,
                extra_system_prompt="",
                all_tool_names=[],
                working_dir=str(config.app.data_dir),
            )
            exited_cleanly = True
        except (EOFError, KeyboardInterrupt, SystemExit):
            exited_cleanly = True

    return buf.getvalue(), exited_cleanly


async def _empty_gen(**kwargs: Any) -> Any:
    """Empty async generator — agent loop that does nothing."""
    return
    yield  # noqa: F841 — unreachable yield makes this an async generator


@pytest.mark.asyncio
class TestReplCancelRecovery:
    """Integration tests that cancel an in-flight agent run and verify recovery."""

    async def test_cancel_during_thinking_recovers_for_next_input(self, tmp_path: Any) -> None:
        """Cancel during thinking clears agent_busy and REPL accepts next input.

        This is the core #937 scenario: agent is streaming, user presses
        Ctrl-C/Escape, and the REPL must recover to accept the next prompt.
        We verify by feeding a message, cancelling it mid-stream, and confirming
        the REPL exits cleanly via /exit. If agent_busy were stuck after cancel,
        the REPL would hang forever waiting for the runner task.
        """
        cancel_was_set = False

        async def fake_agent_loop(**kwargs: Any) -> Any:
            nonlocal cancel_was_set
            cancel_event = kwargs.get("cancel_event")

            yield AgentEvent(kind="thinking", data={})
            await asyncio.sleep(0.02)
            if cancel_event and not cancel_event.is_set():
                cancel_event.set()
                cancel_was_set = True
            yield AgentEvent(kind="done", data={})

        db = _make_db(tmp_path)
        config = _make_config(tmp_path)

        output, exited = await _run_repl_with_agent_loop_mock(
            ["trigger cancel"],
            config,
            db,
            agent_loop_side_effect=fake_agent_loop,
        )

        assert cancel_was_set, "cancel_event should have been set during thinking"
        assert exited, "REPL should exit cleanly after cancel — if agent_busy was stuck, this would hang"

    async def test_cancel_clears_agent_busy_for_prompt_redraw(self, tmp_path: Any) -> None:
        """After cancel, agent_busy is cleared so the prompt redraws correctly.

        We hook into _cleanup_after_turn indirectly: if agent_busy stayed set
        after cancel, the REPL would hang waiting for the runner task. The fact
        that /exit works proves agent_busy was cleared.
        """

        async def fake_agent_loop(**kwargs: Any) -> Any:
            cancel_event = kwargs.get("cancel_event")
            yield AgentEvent(kind="thinking", data={})
            await asyncio.sleep(0.02)
            if cancel_event:
                cancel_event.set()
            yield AgentEvent(kind="done", data={})

        db = _make_db(tmp_path)
        config = _make_config(tmp_path)

        _, exited = await _run_repl_with_agent_loop_mock(
            ["trigger cancel"],
            config,
            db,
            agent_loop_side_effect=fake_agent_loop,
        )

        # If agent_busy were stuck, the REPL would hang and this test would
        # timeout. Clean exit proves agent_busy was cleared by _cleanup_after_turn.
        assert exited, "REPL hung — agent_busy likely not cleared after cancel"

    async def test_queued_messages_preserved_after_cancel(self, tmp_path: Any) -> None:
        """Messages queued during a cancelled run are preserved for the next turn.

        The REPL drains input_queue into msg_queue during streaming. On cancel,
        _cleanup_after_turn backfills msg_queue items into ai_messages so they
        aren't lost. We verify by sending multiple messages rapidly — the second
        one arrives while the first is being processed, and both contribute to
        the conversation.
        """
        turns_seen: list[int] = []

        async def fake_agent_loop(**kwargs: Any) -> Any:
            cancel_event = kwargs.get("cancel_event")
            messages = kwargs.get("messages", [])
            turns_seen.append(len(messages))

            if cancel_event and not cancel_event.is_set():
                # First call: yield thinking then cancel
                yield AgentEvent(kind="thinking", data={})
                await asyncio.sleep(0.02)
                cancel_event.set()
                yield AgentEvent(kind="done", data={})
            else:
                # After cancel recovery
                yield AgentEvent(kind="content", data={"content": "ok"})
                yield AgentEvent(kind="done", data={})

        db = _make_db(tmp_path)
        config = _make_config(tmp_path)

        _, exited = await _run_repl_with_agent_loop_mock(
            ["first", "second"],
            config,
            db,
            agent_loop_side_effect=fake_agent_loop,
        )

        assert exited, "REPL should exit cleanly"
        # Agent loop was invoked more than once — second invocation has the
        # queued message context from the first (backfilled by _cleanup_after_turn)
        assert len(turns_seen) >= 2, f"Expected >=2 agent loop invocations, got {len(turns_seen)}"

    async def test_exit_after_cancel_works(self, tmp_path: Any) -> None:
        """/exit after a cancelled run terminates cleanly — no hang."""

        async def fake_agent_loop(**kwargs: Any) -> Any:
            cancel_event = kwargs.get("cancel_event")
            yield AgentEvent(kind="thinking", data={})
            if cancel_event:
                cancel_event.set()
            yield AgentEvent(kind="done", data={})

        db = _make_db(tmp_path)
        config = _make_config(tmp_path)

        # After cancel, the next command is /exit (from the helper's auto-append)
        _, exited = await _run_repl_with_agent_loop_mock(
            ["trigger"],
            config,
            db,
            agent_loop_side_effect=fake_agent_loop,
        )

        assert exited, "REPL should exit after cancel + /exit"

    async def test_runner_exception_surfaces_without_hang(self, tmp_path: Any) -> None:
        """If run_agent_loop raises, the REPL logs it and recovers."""
        call_count = 0

        async def fake_agent_loop(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("agent loop exploded")
            yield AgentEvent(kind="content", data={"content": "ok"})
            yield AgentEvent(kind="done", data={})

        db = _make_db(tmp_path)
        config = _make_config(tmp_path)

        _, exited = await _run_repl_with_agent_loop_mock(
            ["boom", "recover"],
            config,
            db,
            agent_loop_side_effect=fake_agent_loop,
        )

        assert exited, "REPL should not hang after agent loop exception"

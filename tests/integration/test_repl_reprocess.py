"""Integration tests for the CLI /reprocess command via the real REPL.

Drives the actual REPL code path in src/anteroom/cli/repl.py by mocking
PromptSession.prompt_async to feed commands, and capturing renderer.console
output to verify /reprocess behavior in the terminal.
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
from anteroom.services.storage import create_source


def _make_db(tmp_path: Any) -> ThreadSafeConnection:
    """Create a DB with full schema."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return ThreadSafeConnection(conn)


def _seed_space(db: ThreadSafeConnection, space_id: str = "sp1", name: str = "testspace") -> dict:
    """Insert a space row."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO spaces (id, name, source_file, source_hash, last_loaded_at, created_at, updated_at) "
        "VALUES (?, ?, ?, '', '', ?, ?)",
        (space_id, name, "/s.yaml", now, now),
    )
    db.commit()
    return {"id": space_id, "name": name}


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


async def _run_repl_with_commands(
    commands: list[str],
    config: AppConfig,
    db: ThreadSafeConnection,
    space: dict[str, Any],
) -> str:
    """Run _run_repl with mocked PromptSession feeding specific commands.

    Returns the captured console output as a string.
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
    mock_ai.stream_chat = AsyncMock()
    mock_tool_executor = AsyncMock()

    mock_session_instance = MagicMock()
    mock_session_instance.prompt_async = fake_prompt
    mock_session_instance.default_buffer = MagicMock()
    mock_session_instance.default_buffer.on_text_changed = MagicMock()

    with (
        patch("anteroom.cli.repl.renderer.console", captured_console),
        patch("anteroom.cli.repl.renderer.render_error", lambda msg: captured_console.print(f"Error: {msg}")),
        patch("anteroom.cli.repl.renderer.render_conversation_recap", lambda *a, **k: None),
        patch("anteroom.cli.renderer.use_stdout_console", lambda: None),
        patch("anteroom.cli.repl._patch_stdout", _noop_patch_stdout, create=True),
        patch("prompt_toolkit.patch_stdout.patch_stdout", _noop_patch_stdout),
        patch("prompt_toolkit.PromptSession") as mock_session_cls,
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
                space=space,
            )
        except (EOFError, KeyboardInterrupt, SystemExit):
            pass

    return buf.getvalue()


@pytest.mark.asyncio
class TestReplReprocess:
    """Drive the real REPL and verify /reprocess command output."""

    async def test_reprocess_no_args_shows_usage(self, tmp_path: Any) -> None:
        """/reprocess with no argument prints usage instructions."""
        db = _make_db(tmp_path)
        sp = _seed_space(db)
        config = _make_config(tmp_path)

        output = await _run_repl_with_commands(
            ["/reprocess"],
            config,
            db,
            sp,
        )

        assert "Usage:" in output, f"Expected usage message, got: {output}"
        assert "/reprocess" in output

    async def test_reprocess_nonexistent_source(self, tmp_path: Any) -> None:
        """/reprocess with a fake ID prints 'Source not found.'."""
        db = _make_db(tmp_path)
        sp = _seed_space(db)
        config = _make_config(tmp_path)

        output = await _run_repl_with_commands(
            ["/reprocess some-fake-id"],
            config,
            db,
            sp,
        )

        assert "Source not found" in output, f"Expected 'Source not found', got: {output}"

    async def test_reprocess_existing_text_source(self, tmp_path: Any) -> None:
        """/reprocess on a text source with content shows 'Reprocessed' and chunk count."""
        db = _make_db(tmp_path)
        sp = _seed_space(db)
        src, _ = create_source(db, source_type="text", title="My Notes", content="Some important notes here.")
        config = _make_config(tmp_path)

        output = await _run_repl_with_commands(
            [f"/reprocess {src['id']}"],
            config,
            db,
            sp,
        )

        assert "Reprocessed" in output, f"Expected 'Reprocessed' message, got: {output}"
        assert "chunk(s)" in output, f"Expected chunk count in output, got: {output}"

    async def test_reprocess_all_no_sources_needing_reprocess(self, tmp_path: Any) -> None:
        """/reprocess all with only text sources (which have content) prints 'No sources need reprocessing.'."""
        db = _make_db(tmp_path)
        sp = _seed_space(db)
        create_source(db, source_type="text", title="Doc A", content="Content A")
        create_source(db, source_type="text", title="Doc B", content="Content B")
        config = _make_config(tmp_path)

        output = await _run_repl_with_commands(
            ["/reprocess all"],
            config,
            db,
            sp,
        )

        assert "No sources need reprocessing" in output, f"Expected no-op message, got: {output}"

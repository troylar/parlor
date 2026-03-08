"""Integration tests for CLI /space link-source disambiguation via the real REPL.

Drives the actual REPL code path in src/anteroom/cli/repl.py by mocking
PromptSession.prompt_async to feed commands, and capturing renderer.console
output to verify disambiguation messages appear in the terminal.

This tests the real REPL loop — not a duplicated matching function.
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
        # Yield control so _agent_runner can process between commands
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
        # Prevent use_stdout_console from overwriting our captured_console
        patch("anteroom.cli.renderer.use_stdout_console", lambda: None),
        # Replace patch_stdout with a no-op context manager
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
class TestReplDisambiguationFlow:
    """Drive the real REPL and verify disambiguation output."""

    async def test_link_source_duplicate_title_shows_disambiguation(self, tmp_path: Any) -> None:
        """When two sources share the same title, the REPL prints candidates with IDs."""
        db = _make_db(tmp_path)
        sp = _seed_space(db)
        s1, _ = create_source(db, source_type="text", title="Quarterly Report", content="v1")
        s2, _ = create_source(db, source_type="text", title="Quarterly Report", content="v2")
        config = _make_config(tmp_path)

        output = await _run_repl_with_commands(
            ["/space link-source Quarterly Report"],
            config,
            db,
            sp,
        )

        assert "Multiple sources named" in output, f"Expected disambiguation message, got: {output}"
        assert s1["id"][:8] in output, f"Expected truncated ID {s1['id'][:8]} in output"
        assert s2["id"][:8] in output, f"Expected truncated ID {s2['id'][:8]} in output"
        assert "Use the source ID to disambiguate" in output

    async def test_link_source_by_id_after_disambiguation(self, tmp_path: Any) -> None:
        """After disambiguation, using the source ID links successfully."""
        db = _make_db(tmp_path)
        sp = _seed_space(db)
        s1, _ = create_source(db, source_type="text", title="Quarterly Report", content="v1")
        create_source(db, source_type="text", title="Quarterly Report", content="v2")
        config = _make_config(tmp_path)

        output = await _run_repl_with_commands(
            [f"/space link-source {s1['id']}"],
            config,
            db,
            sp,
        )

        assert "Linked" in output, f"Expected link confirmation, got: {output}"
        assert "Quarterly Report" in output

    async def test_link_source_partial_match_multiple_shows_candidates(self, tmp_path: Any) -> None:
        """Partial title matching multiple sources shows disambiguation."""
        db = _make_db(tmp_path)
        sp = _seed_space(db)
        create_source(db, source_type="text", title="Q1 Report", content="c1")
        create_source(db, source_type="text", title="Q2 Report", content="c2")
        create_source(db, source_type="text", title="Budget Plan", content="c3")
        config = _make_config(tmp_path)

        output = await _run_repl_with_commands(
            ["/space link-source report"],
            config,
            db,
            sp,
        )

        assert "Multiple sources match" in output, f"Expected partial disambiguation, got: {output}"
        assert "Be more specific or use the source ID" in output

    async def test_link_source_unique_title_links_directly(self, tmp_path: Any) -> None:
        """A unique title match links without disambiguation."""
        db = _make_db(tmp_path)
        sp = _seed_space(db)
        create_source(db, source_type="text", title="Unique Doc", content="c1")
        create_source(db, source_type="text", title="Other Doc", content="c2")
        config = _make_config(tmp_path)

        output = await _run_repl_with_commands(
            ["/space link-source Unique Doc"],
            config,
            db,
            sp,
        )

        assert "Linked" in output, f"Expected link confirmation, got: {output}"
        assert "Multiple" not in output, "Should not show disambiguation"

    async def test_link_source_not_found(self, tmp_path: Any) -> None:
        """Non-matching query shows 'not found' error."""
        db = _make_db(tmp_path)
        sp = _seed_space(db)
        create_source(db, source_type="text", title="Alpha", content="c1")
        config = _make_config(tmp_path)

        output = await _run_repl_with_commands(
            ["/space link-source nonexistent"],
            config,
            db,
            sp,
        )

        assert "not found" in output.lower(), f"Expected 'not found', got: {output}"

    async def test_unlink_source_duplicate_title_shows_disambiguation(self, tmp_path: Any) -> None:
        """Unlink with duplicate titles also shows disambiguation."""
        from anteroom.services.storage import link_source_to_space

        db = _make_db(tmp_path)
        sp = _seed_space(db)
        s1, _ = create_source(db, source_type="text", title="Status Report", content="v1")
        s2, _ = create_source(db, source_type="text", title="Status Report", content="v2")
        link_source_to_space(db, sp["id"], source_id=s1["id"])
        link_source_to_space(db, sp["id"], source_id=s2["id"])
        config = _make_config(tmp_path)

        output = await _run_repl_with_commands(
            ["/space unlink-source Status Report"],
            config,
            db,
            sp,
        )

        assert "Multiple sources named" in output, f"Expected disambiguation, got: {output}"
        assert s1["id"][:8] in output
        assert s2["id"][:8] in output

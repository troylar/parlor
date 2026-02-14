"""Tests for database initialization."""

from __future__ import annotations

import sqlite3

from parlor.db import _FTS_SCHEMA, _FTS_TRIGGERS, _SCHEMA


def _init_in_memory() -> sqlite3.Connection:
    """Create an in-memory database with the full schema applied."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    try:
        conn.executescript(_FTS_SCHEMA)
        conn.executescript(_FTS_TRIGGERS)
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


class TestInitDb:
    def test_creates_conversations_table(self) -> None:
        conn = _init_in_memory()
        assert "conversations" in _table_names(conn)

    def test_creates_messages_table(self) -> None:
        conn = _init_in_memory()
        assert "messages" in _table_names(conn)

    def test_creates_attachments_table(self) -> None:
        conn = _init_in_memory()
        assert "attachments" in _table_names(conn)

    def test_creates_tool_calls_table(self) -> None:
        conn = _init_in_memory()
        assert "tool_calls" in _table_names(conn)

    def test_creates_conversations_fts(self) -> None:
        conn = _init_in_memory()
        assert "conversations_fts" in _table_names(conn)

    def test_creates_messages_fts_triggers(self) -> None:
        """Verify FTS triggers exist after init."""
        conn = _init_in_memory()
        triggers = conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
        trigger_names = {r[0] for r in triggers}
        assert "fts_conversations_insert" in trigger_names
        assert "fts_conversations_delete" in trigger_names
        assert "fts_messages_insert" in trigger_names

    def test_foreign_keys_enabled(self) -> None:
        conn = _init_in_memory()
        fk = conn.execute("PRAGMA foreign_keys").fetchone()
        assert fk[0] == 1

    def test_idempotent(self) -> None:
        """Running init twice on the same connection should not raise."""
        conn = _init_in_memory()
        conn.executescript(_SCHEMA)
        try:
            conn.executescript(_FTS_SCHEMA)
            conn.executescript(_FTS_TRIGGERS)
        except sqlite3.OperationalError:
            pass
        conn.commit()
        assert "conversations" in _table_names(conn)

    def test_conversations_columns(self) -> None:
        conn = _init_in_memory()
        info = conn.execute("PRAGMA table_info(conversations)").fetchall()
        col_names = {r[1] for r in info}
        assert col_names == {"id", "title", "model", "project_id", "folder_id", "created_at", "updated_at"}

    def test_messages_columns(self) -> None:
        conn = _init_in_memory()
        info = conn.execute("PRAGMA table_info(messages)").fetchall()
        col_names = {r[1] for r in info}
        assert col_names == {"id", "conversation_id", "role", "content", "created_at", "position"}

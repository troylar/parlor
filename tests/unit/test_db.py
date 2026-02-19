"""Tests for database initialization."""

from __future__ import annotations

import sqlite3

import pytest

from anteroom.db import _FTS_SCHEMA, _FTS_TRIGGERS, _SCHEMA, _VEC_METADATA_SCHEMA, _make_vec_schema, has_vec_support


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
        assert col_names == {
            "id",
            "title",
            "type",
            "model",
            "project_id",
            "folder_id",
            "user_id",
            "user_display_name",
            "created_at",
            "updated_at",
        }

    def test_messages_columns(self) -> None:
        conn = _init_in_memory()
        info = conn.execute("PRAGMA table_info(messages)").fetchall()
        col_names = {r[1] for r in info}
        assert col_names == {
            "id",
            "conversation_id",
            "role",
            "content",
            "user_id",
            "user_display_name",
            "created_at",
            "position",
        }

    def test_creates_users_table(self) -> None:
        conn = _init_in_memory()
        assert "users" in _table_names(conn)

    def test_users_table_columns(self) -> None:
        conn = _init_in_memory()
        info = conn.execute("PRAGMA table_info(users)").fetchall()
        col_names = {r[1] for r in info}
        assert col_names == {"user_id", "display_name", "public_key", "created_at", "updated_at"}

    def test_projects_have_user_columns(self) -> None:
        conn = _init_in_memory()
        info = conn.execute("PRAGMA table_info(projects)").fetchall()
        col_names = {r[1] for r in info}
        assert "user_id" in col_names
        assert "user_display_name" in col_names

    def test_folders_have_user_columns(self) -> None:
        conn = _init_in_memory()
        info = conn.execute("PRAGMA table_info(folders)").fetchall()
        col_names = {r[1] for r in info}
        assert "user_id" in col_names
        assert "user_display_name" in col_names

    def test_tags_have_user_columns(self) -> None:
        conn = _init_in_memory()
        info = conn.execute("PRAGMA table_info(tags)").fetchall()
        col_names = {r[1] for r in info}
        assert "user_id" in col_names
        assert "user_display_name" in col_names


class TestMigrations:
    """Test that migrations add user_id/user_display_name to pre-existing tables."""

    def _init_legacy_db(self) -> sqlite3.Connection:
        """Create a DB with the old schema (no user columns, no users table)."""
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                instructions TEXT NOT NULL DEFAULT '',
                model TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS folders (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                parent_id TEXT DEFAULT NULL,
                project_id TEXT DEFAULT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                collapsed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tags (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                color TEXT NOT NULL DEFAULT '#3b82f6',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                model TEXT DEFAULT NULL,
                project_id TEXT DEFAULT NULL,
                folder_id TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                position INTEGER NOT NULL
            );
        """)
        conn.commit()
        return conn

    def test_migration_adds_users_table(self) -> None:
        from anteroom.db import _run_migrations

        conn = self._init_legacy_db()
        _run_migrations(conn)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "users" in tables

    def test_migration_adds_user_columns_to_conversations(self) -> None:
        from anteroom.db import _run_migrations

        conn = self._init_legacy_db()
        _run_migrations(conn)
        info = conn.execute("PRAGMA table_info(conversations)").fetchall()
        col_names = {r[1] for r in info}
        assert "user_id" in col_names
        assert "user_display_name" in col_names

    def test_migration_adds_user_columns_to_messages(self) -> None:
        from anteroom.db import _run_migrations

        conn = self._init_legacy_db()
        _run_migrations(conn)
        info = conn.execute("PRAGMA table_info(messages)").fetchall()
        col_names = {r[1] for r in info}
        assert "user_id" in col_names
        assert "user_display_name" in col_names

    def test_migration_adds_user_columns_to_all_entity_tables(self) -> None:
        from anteroom.db import _run_migrations

        conn = self._init_legacy_db()
        _run_migrations(conn)
        for table in ("conversations", "messages", "projects", "folders", "tags"):
            info = conn.execute(f"PRAGMA table_info({table})").fetchall()
            col_names = {r[1] for r in info}
            assert "user_id" in col_names, f"user_id missing from {table}"
            assert "user_display_name" in col_names, f"user_display_name missing from {table}"

    def test_migration_idempotent(self) -> None:
        from anteroom.db import _run_migrations

        conn = self._init_legacy_db()
        _run_migrations(conn)
        _run_migrations(conn)
        info = conn.execute("PRAGMA table_info(conversations)").fetchall()
        col_names = [r[1] for r in info]
        assert col_names.count("user_id") == 1

    def test_migration_adds_type_column_to_conversations(self) -> None:
        from anteroom.db import _run_migrations

        conn = self._init_legacy_db()
        _run_migrations(conn)
        info = conn.execute("PRAGMA table_info(conversations)").fetchall()
        col_names = {r[1] for r in info}
        assert "type" in col_names

    def test_migration_type_column_defaults_to_chat(self) -> None:
        from anteroom.db import _run_migrations

        conn = self._init_legacy_db()
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at)"
            " VALUES ('test-1', 'Test', '2024-01-01', '2024-01-01')"
        )
        conn.commit()
        _run_migrations(conn)
        row = conn.execute("SELECT type FROM conversations WHERE id = 'test-1'").fetchone()
        assert row[0] == "chat"

    def test_migration_creates_message_embeddings_table(self) -> None:
        from anteroom.db import _run_migrations

        conn = self._init_legacy_db()
        _run_migrations(conn)
        tables = _table_names(conn)
        assert "message_embeddings" in tables

    def test_migration_creates_message_embeddings_columns(self) -> None:
        from anteroom.db import _run_migrations

        conn = self._init_legacy_db()
        _run_migrations(conn)
        info = conn.execute("PRAGMA table_info(message_embeddings)").fetchall()
        col_names = {r[1] for r in info}
        assert "message_id" in col_names
        assert "conversation_id" in col_names
        assert "content_hash" in col_names
        assert "created_at" in col_names


class TestVecSupport:
    def test_message_embeddings_table_created(self) -> None:
        conn = _init_in_memory()
        try:
            conn.executescript(_VEC_METADATA_SCHEMA)
        except sqlite3.OperationalError:
            pass
        conn.commit()
        tables = _table_names(conn)
        assert "message_embeddings" in tables

    def test_has_vec_support_false_without_extension(self) -> None:
        conn = sqlite3.connect(":memory:")
        assert has_vec_support(conn) is False
        conn.close()

    def test_has_vec_support_true_with_extension(self) -> None:
        try:
            import sqlite_vec

            conn = sqlite3.connect(":memory:")
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            assert has_vec_support(conn) is True
            conn.close()
        except (ImportError, Exception):
            pytest.skip("sqlite-vec not available")

    def test_vec_messages_table_created_with_extension(self) -> None:
        try:
            import sqlite_vec

            conn = sqlite3.connect(":memory:")
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            conn.executescript(_make_vec_schema())
            conn.commit()
            # Check that vec_messages exists (virtual tables show up in sqlite_master)
            rows = conn.execute("SELECT name FROM sqlite_master WHERE name = 'vec_messages'").fetchall()
            assert len(rows) == 1
            conn.close()
        except (ImportError, Exception):
            pytest.skip("sqlite-vec not available")

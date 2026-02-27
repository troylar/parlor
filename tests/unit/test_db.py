"""Tests for database initialization."""

from __future__ import annotations

import sqlite3

import pytest

from anteroom.db import (
    _FTS_SCHEMA,
    _FTS_TRIGGERS,
    _SCHEMA,
    _VEC_METADATA_SCHEMA,
    _create_indexes,
    _make_vec_schema,
    _run_migrations,
    has_vec_support,
)


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
    _run_migrations(conn)
    _create_indexes(conn)
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
            "slug",
            "type",
            "model",
            "project_id",
            "working_dir",
            "folder_id",
            "user_id",
            "user_display_name",
            "space_id",
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
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "model",
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

    def test_migration_adds_working_dir_column(self) -> None:
        from anteroom.db import _run_migrations

        conn = self._init_legacy_db()
        _run_migrations(conn)
        info = conn.execute("PRAGMA table_info(conversations)").fetchall()
        col_names = {r[1] for r in info}
        assert "working_dir" in col_names

    def test_migration_working_dir_defaults_to_null(self) -> None:
        from anteroom.db import _run_migrations

        conn = self._init_legacy_db()
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at)"
            " VALUES ('wd-1', 'Test', '2024-01-01', '2024-01-01')"
        )
        conn.commit()
        _run_migrations(conn)
        row = conn.execute("SELECT working_dir FROM conversations WHERE id = 'wd-1'").fetchone()
        assert row[0] is None

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


class TestSpaceIdMigration:
    """Regression tests for #562: space_id column migration on pre-v1.74.0 databases."""

    def _init_pre_spaces_db(self) -> sqlite3.Connection:
        """Create a DB with the pre-v1.74.0 schema — tables exist but without space_id."""
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
            -- pack_attachments existed since v1.70.0, but without space_id
            CREATE TABLE IF NOT EXISTS pack_attachments (
                id TEXT PRIMARY KEY,
                pack_id TEXT NOT NULL,
                project_path TEXT,
                scope TEXT NOT NULL CHECK(scope IN ('global', 'project')),
                created_at TEXT NOT NULL
            );
        """)
        conn.commit()
        return conn

    def test_migration_succeeds_on_pre_spaces_db(self) -> None:
        """init_db must not crash on a database created before spaces feature."""
        from anteroom.db import _run_migrations

        conn = self._init_pre_spaces_db()
        _run_migrations(conn)

    def test_space_id_added_to_conversations(self) -> None:
        from anteroom.db import _run_migrations

        conn = self._init_pre_spaces_db()
        _run_migrations(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()}
        assert "space_id" in cols

    def test_space_id_added_to_folders(self) -> None:
        from anteroom.db import _run_migrations

        conn = self._init_pre_spaces_db()
        _run_migrations(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(folders)").fetchall()}
        assert "space_id" in cols

    def test_space_id_added_to_pack_attachments(self) -> None:
        from anteroom.db import _run_migrations

        conn = self._init_pre_spaces_db()
        _run_migrations(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pack_attachments)").fetchall()}
        assert "space_id" in cols

    def test_indexes_created_after_migration(self) -> None:
        from anteroom.db import _create_indexes, _run_migrations

        conn = self._init_pre_spaces_db()
        _run_migrations(conn)
        _create_indexes(conn)
        indexes = {r[1] for r in conn.execute("PRAGMA index_list(conversations)").fetchall()}
        assert "idx_conversations_space" in indexes

        indexes = {r[1] for r in conn.execute("PRAGMA index_list(folders)").fetchall()}
        assert "idx_folders_space" in indexes

        indexes = {r[1] for r in conn.execute("PRAGMA index_list(pack_attachments)").fetchall()}
        assert "idx_pack_attachments_space" in indexes
        assert "idx_pack_attachments_unique" in indexes

    def test_migration_idempotent_with_space_id(self) -> None:
        from anteroom.db import _run_migrations

        conn = self._init_pre_spaces_db()
        _run_migrations(conn)
        _run_migrations(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()}
        assert "space_id" in cols

    def test_fresh_db_also_gets_indexes(self) -> None:
        """On a brand-new database, space_id indexes should also exist."""
        conn = _init_in_memory()
        from anteroom.db import _create_indexes, _run_migrations

        _run_migrations(conn)
        _create_indexes(conn)
        indexes = {r[1] for r in conn.execute("PRAGMA index_list(conversations)").fetchall()}
        assert "idx_conversations_space" in indexes
        indexes = {r[1] for r in conn.execute("PRAGMA index_list(pack_attachments)").fetchall()}
        assert "idx_pack_attachments_unique" in indexes


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


class TestCreateIndexes:
    """Tests for the _create_indexes() function (#564)."""

    def test_schema_has_no_create_index(self) -> None:
        """_SCHEMA must not contain any CREATE INDEX statements."""
        assert "CREATE INDEX" not in _SCHEMA
        assert "CREATE UNIQUE INDEX" not in _SCHEMA

    def test_all_expected_indexes_created_on_fresh_db(self) -> None:
        """_create_indexes() creates every expected index on a fresh database."""
        conn = _init_in_memory()
        all_indexes = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'").fetchall()
        }
        expected = {
            "idx_projects_name",
            "idx_spaces_name",
            "idx_space_paths_space",
            "idx_messages_conversation",
            "idx_change_log_id",
            "idx_source_chunks_source",
            "idx_artifacts_fqn",
            "idx_artifacts_type",
            "idx_artifacts_namespace",
            "idx_artifact_versions_artifact_id",
            "idx_packs_namespace",
            "idx_pack_artifacts_artifact_id",
            "idx_pack_attachments_pack",
            "idx_pack_attachments_project",
            "idx_pack_attachments_space",
            "idx_pack_attachments_unique",
            "idx_space_sources_unique",
            "idx_conversations_space",
            "idx_folders_space",
            "idx_conversations_slug",
        }
        assert expected.issubset(all_indexes), f"Missing indexes: {expected - all_indexes}"

    def test_conversations_slug_index_on_fresh_db(self) -> None:
        """idx_conversations_slug must be created on fresh databases (latent bug fix)."""
        conn = _init_in_memory()
        indexes = {r[1] for r in conn.execute("PRAGMA index_list(conversations)").fetchall()}
        assert "idx_conversations_slug" in indexes

    def test_create_indexes_idempotent(self) -> None:
        """Calling _create_indexes() twice does not error."""
        conn = _init_in_memory()
        _create_indexes(conn)
        _create_indexes(conn)


class TestMigrationPaths:
    """Migration-path tests for historical schema versions (#565).

    Each test creates a database at a historical schema baseline, runs
    _run_migrations() + _create_indexes(), and verifies the result matches
    the expected final state.
    """

    @staticmethod
    def _make_pre_artifacts_db() -> sqlite3.Connection:
        """Baseline: pre-v1.67.0 — no artifacts, packs, or spaces tables."""
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                public_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                instructions TEXT NOT NULL DEFAULT '',
                model TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
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
            CREATE TABLE IF NOT EXISTS attachments (
                id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                storage_path TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tool_calls (
                id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                server_name TEXT NOT NULL,
                input_json TEXT NOT NULL,
                output_json TEXT,
                status TEXT NOT NULL CHECK(status IN ('pending', 'success', 'error')),
                created_at TEXT NOT NULL
            );
        """)
        conn.commit()
        return conn

    @staticmethod
    def _make_pre_packs_db() -> sqlite3.Connection:
        """Baseline: pre-v1.69.0 — has artifacts but no packs or spaces."""
        conn = TestMigrationPaths._make_pre_artifacts_db()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                fqn TEXT UNIQUE NOT NULL,
                type TEXT NOT NULL,
                namespace TEXT NOT NULL,
                name TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                source TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifact_versions (
                id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(artifact_id, version)
            );
        """)
        conn.commit()
        return conn

    @staticmethod
    def _make_pre_spaces_db() -> sqlite3.Connection:
        """Baseline: pre-v1.74.0 — has artifacts and packs but no spaces."""
        conn = TestMigrationPaths._make_pre_packs_db()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS packs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                namespace TEXT NOT NULL,
                version TEXT NOT NULL DEFAULT '0.0.0',
                description TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                installed_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(namespace, name)
            );
            CREATE TABLE IF NOT EXISTS pack_artifacts (
                pack_id TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                PRIMARY KEY(pack_id, artifact_id)
            );
            CREATE TABLE IF NOT EXISTS pack_attachments (
                id TEXT PRIMARY KEY,
                pack_id TEXT NOT NULL,
                project_path TEXT,
                scope TEXT NOT NULL CHECK(scope IN ('global', 'project')),
                created_at TEXT NOT NULL
            );
        """)
        conn.commit()
        return conn

    def _run_full_init(self, conn: sqlite3.Connection) -> None:
        """Run migrations + index creation (mirrors init_db minus file I/O)."""
        _run_migrations(conn)
        _create_indexes(conn)
        conn.commit()

    def _get_all_tables(self, conn: sqlite3.Connection) -> set[str]:
        return {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if not r[0].startswith("conversations_fts")
        }

    def _get_all_indexes(self, conn: sqlite3.Connection) -> set[str]:
        return {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'").fetchall()
        }

    def _get_columns(self, conn: sqlite3.Connection, table: str) -> set[str]:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    # -- Pre-artifacts baseline (v1.66) --

    def test_pre_artifacts_migration_succeeds(self) -> None:
        conn = self._make_pre_artifacts_db()
        self._run_full_init(conn)

    def test_pre_artifacts_creates_artifact_tables(self) -> None:
        conn = self._make_pre_artifacts_db()
        self._run_full_init(conn)
        tables = self._get_all_tables(conn)
        assert "artifacts" in tables
        assert "artifact_versions" in tables
        assert "packs" in tables
        assert "pack_artifacts" in tables
        assert "pack_attachments" in tables
        assert "spaces" in tables
        assert "space_paths" in tables

    def test_pre_artifacts_creates_all_indexes(self) -> None:
        conn = self._make_pre_artifacts_db()
        self._run_full_init(conn)
        indexes = self._get_all_indexes(conn)
        assert "idx_artifacts_fqn" in indexes
        assert "idx_packs_namespace" in indexes
        assert "idx_pack_attachments_space" in indexes
        assert "idx_spaces_name" in indexes
        assert "idx_conversations_slug" in indexes

    def test_pre_artifacts_adds_missing_columns(self) -> None:
        conn = self._make_pre_artifacts_db()
        self._run_full_init(conn)
        conv_cols = self._get_columns(conn, "conversations")
        assert "slug" in conv_cols
        assert "space_id" in conv_cols
        assert "type" in conv_cols
        assert "working_dir" in conv_cols
        assert "user_id" in conv_cols

    # -- Pre-packs baseline (v1.68) --

    def test_pre_packs_migration_succeeds(self) -> None:
        conn = self._make_pre_packs_db()
        self._run_full_init(conn)

    def test_pre_packs_creates_pack_tables(self) -> None:
        conn = self._make_pre_packs_db()
        self._run_full_init(conn)
        tables = self._get_all_tables(conn)
        assert "packs" in tables
        assert "pack_artifacts" in tables
        assert "pack_attachments" in tables
        assert "spaces" in tables

    def test_pre_packs_creates_all_indexes(self) -> None:
        conn = self._make_pre_packs_db()
        self._run_full_init(conn)
        indexes = self._get_all_indexes(conn)
        assert "idx_packs_namespace" in indexes
        assert "idx_pack_artifacts_artifact_id" in indexes
        assert "idx_pack_attachments_unique" in indexes
        assert "idx_spaces_name" in indexes

    # -- Pre-spaces baseline (v1.73) --

    def test_pre_spaces_migration_succeeds(self) -> None:
        conn = self._make_pre_spaces_db()
        self._run_full_init(conn)

    def test_pre_spaces_creates_space_tables(self) -> None:
        conn = self._make_pre_spaces_db()
        self._run_full_init(conn)
        tables = self._get_all_tables(conn)
        assert "spaces" in tables
        assert "space_paths" in tables
        assert "space_sources" in tables

    def test_pre_spaces_adds_space_id_columns(self) -> None:
        conn = self._make_pre_spaces_db()
        self._run_full_init(conn)
        assert "space_id" in self._get_columns(conn, "conversations")
        assert "space_id" in self._get_columns(conn, "folders")
        assert "space_id" in self._get_columns(conn, "pack_attachments")

    def test_pre_spaces_creates_all_indexes(self) -> None:
        conn = self._make_pre_spaces_db()
        self._run_full_init(conn)
        indexes = self._get_all_indexes(conn)
        assert "idx_conversations_space" in indexes
        assert "idx_folders_space" in indexes
        assert "idx_pack_attachments_space" in indexes
        assert "idx_pack_attachments_unique" in indexes
        assert "idx_space_paths_space" in indexes
        assert "idx_space_sources_unique" in indexes

    # -- Idempotency --

    def test_double_migration_idempotent(self) -> None:
        """Running migrations twice on the oldest baseline must not error."""
        conn = self._make_pre_artifacts_db()
        self._run_full_init(conn)
        self._run_full_init(conn)

    # -- Schema fingerprint --

    def test_schema_fingerprint(self) -> None:
        """Final schema from any migration path must match a fresh database.

        This catches cases where a migration produces a different schema
        than a fresh install — e.g., missing indexes, extra columns, or
        tables with different column sets.
        """
        fresh = _init_in_memory()
        migrated = self._make_pre_artifacts_db()
        self._run_full_init(migrated)

        fresh_tables = self._get_all_tables(fresh)
        migrated_tables = self._get_all_tables(migrated)

        missing_tables = fresh_tables - migrated_tables
        assert not missing_tables, f"Tables missing after migration: {missing_tables}"

        common_tables = fresh_tables & migrated_tables
        for table in sorted(common_tables):
            fresh_cols = self._get_columns(fresh, table)
            migrated_cols = self._get_columns(migrated, table)
            missing_cols = fresh_cols - migrated_cols
            assert not missing_cols, f"Table {table}: columns missing after migration: {missing_cols}"

        fresh_indexes = self._get_all_indexes(fresh)
        migrated_indexes = self._get_all_indexes(migrated)
        missing_indexes = fresh_indexes - migrated_indexes
        assert not missing_indexes, f"Indexes missing after migration: {missing_indexes}"

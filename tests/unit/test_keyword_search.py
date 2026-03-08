"""Tests for FTS5 keyword search functions (storage.py)."""

from __future__ import annotations

import sqlite3

from anteroom.db import _FTS_SCHEMA, _FTS_TRIGGERS, _SCHEMA
from anteroom.services.storage import (
    search_keyword_messages,
    search_keyword_source_chunks,
)


class _FakeThreadSafe:
    """Minimal ThreadSafeConnection-like wrapper for testing."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, parameters: tuple = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, parameters)

    def execute_fetchone(self, sql: str, parameters: tuple = ()) -> sqlite3.Row | None:
        return self._conn.execute(sql, parameters).fetchone()

    def execute_fetchall(self, sql: str, parameters: tuple = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, parameters).fetchall()

    def commit(self) -> None:
        self._conn.commit()

    class _TxContext:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn

        def __enter__(self) -> sqlite3.Connection:
            return self._conn

        def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
            if exc_type:
                self._conn.rollback()
            else:
                self._conn.commit()
            return False

    def transaction(self) -> _TxContext:
        return self._TxContext(self._conn)


def _init_db() -> _FakeThreadSafe:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    try:
        conn.executescript(_FTS_SCHEMA)
        conn.executescript(_FTS_TRIGGERS)
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return _FakeThreadSafe(conn)


def _seed_conversation(db: _FakeThreadSafe, conv_id: str, space_id: str | None = None) -> None:
    now = "2025-01-01T00:00:00Z"
    db.execute(
        "INSERT INTO conversations (id, title, type, space_id, created_at, updated_at) VALUES (?, ?, 'chat', ?, ?, ?)",
        (conv_id, f"Conv {conv_id}", space_id, now, now),
    )
    db.commit()


_MSG_POS = 0


def _seed_message(db: _FakeThreadSafe, msg_id: str, conv_id: str, content: str) -> None:
    global _MSG_POS  # noqa: PLW0603
    _MSG_POS += 1
    now = "2025-01-01T00:00:00Z"
    db.execute(
        "INSERT INTO messages (id, conversation_id, role, content, created_at, position)"
        " VALUES (?, ?, 'assistant', ?, ?, ?)",
        (msg_id, conv_id, content, now, _MSG_POS),
    )
    db.commit()


def _seed_source(db: _FakeThreadSafe, source_id: str, title: str = "Source") -> None:
    now = "2025-01-01T00:00:00Z"
    db.execute(
        "INSERT INTO sources (id, type, title, created_at, updated_at) VALUES (?, 'text', ?, ?, ?)",
        (source_id, title, now, now),
    )
    db.commit()


def _seed_chunk(db: _FakeThreadSafe, chunk_id: str, source_id: str, content: str, idx: int = 0) -> None:
    import hashlib

    content_hash = hashlib.sha256(content.encode()).hexdigest()
    now = "2025-01-01T00:00:00Z"
    db.execute(
        "INSERT INTO source_chunks (id, source_id, chunk_index, content, content_hash, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (chunk_id, source_id, idx, content, content_hash, now),
    )
    db.commit()


def _seed_space_source(db: _FakeThreadSafe, space_id: str, source_id: str) -> None:
    now = "2025-01-01T00:00:00Z"
    db.execute(
        "INSERT INTO space_sources (space_id, source_id, created_at) VALUES (?, ?, ?)",
        (space_id, source_id, now),
    )
    db.commit()


class TestSearchKeywordMessages:
    def test_basic_match(self) -> None:
        db = _init_db()
        _seed_conversation(db, "c1")
        _seed_message(db, "m1", "c1", "Use pytest for testing Python applications")

        results = search_keyword_messages(db, "pytest")
        assert len(results) >= 1
        assert results[0]["message_id"] == "m1"
        assert results[0]["content"] == "Use pytest for testing Python applications"

    def test_no_match(self) -> None:
        db = _init_db()
        _seed_conversation(db, "c1")
        _seed_message(db, "m1", "c1", "Hello world")

        results = search_keyword_messages(db, "kubernetes deployment")
        assert len(results) == 0

    def test_short_query_returns_empty(self) -> None:
        db = _init_db()
        results = search_keyword_messages(db, "a")
        assert results == []

    def test_empty_query_returns_empty(self) -> None:
        db = _init_db()
        results = search_keyword_messages(db, "")
        assert results == []

    def test_space_scoped(self) -> None:
        db = _init_db()
        now = "2025-01-01T00:00:00Z"
        db.execute(
            "INSERT INTO spaces (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("sp1", "backend", now, now),
        )
        db.commit()
        _seed_conversation(db, "c1", space_id="sp1")
        _seed_conversation(db, "c2", space_id=None)
        _seed_message(db, "m1", "c1", "backend pytest framework")
        _seed_message(db, "m2", "c2", "frontend pytest framework")

        results = search_keyword_messages(db, "pytest", space_id="sp1")
        ids = [r["message_id"] for r in results]
        assert "m1" in ids
        assert "m2" not in ids

    def test_result_format(self) -> None:
        db = _init_db()
        _seed_conversation(db, "c1")
        _seed_message(db, "m1", "c1", "Use parameterized queries for SQL safety")

        results = search_keyword_messages(db, "parameterized")
        assert len(results) >= 1
        r = results[0]
        assert "message_id" in r
        assert "conversation_id" in r
        assert "content" in r
        assert "distance" in r
        assert "fts_rank" in r


class TestSearchKeywordSourceChunks:
    def test_basic_match(self) -> None:
        db = _init_db()
        _seed_source(db, "s1")
        _seed_chunk(db, "ch1", "s1", "Always use type hints in Python functions")

        results = search_keyword_source_chunks(db, "type hints")
        assert len(results) >= 1
        assert results[0]["chunk_id"] == "ch1"

    def test_no_match(self) -> None:
        db = _init_db()
        _seed_source(db, "s1")
        _seed_chunk(db, "ch1", "s1", "Hello world")

        results = search_keyword_source_chunks(db, "kubernetes deployment")
        assert len(results) == 0

    def test_space_scoped(self) -> None:
        db = _init_db()
        now = "2025-01-01T00:00:00Z"
        db.execute(
            "INSERT INTO spaces (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("sp1", "backend", now, now),
        )
        db.commit()
        _seed_source(db, "s1")
        _seed_source(db, "s2")
        _seed_chunk(db, "ch1", "s1", "backend parameterized queries")
        _seed_chunk(db, "ch2", "s2", "frontend parameterized queries")
        _seed_space_source(db, "sp1", "s1")

        results = search_keyword_source_chunks(db, "parameterized", space_id="sp1")
        ids = [r["chunk_id"] for r in results]
        assert "ch1" in ids
        assert "ch2" not in ids

    def test_result_format(self) -> None:
        db = _init_db()
        _seed_source(db, "s1")
        _seed_chunk(db, "ch1", "s1", "Use Black for formatting code style")

        results = search_keyword_source_chunks(db, "formatting")
        assert len(results) >= 1
        r = results[0]
        assert "chunk_id" in r
        assert "source_id" in r
        assert "content" in r
        assert "distance" in r
        assert "fts_rank" in r

    def test_short_query_returns_empty(self) -> None:
        db = _init_db()
        results = search_keyword_source_chunks(db, "x")
        assert results == []

    def test_space_scoped_no_limit_starvation(self) -> None:
        """SQL-level space filtering must not starve results when out-of-space chunks fill the limit."""
        db = _init_db()
        now = "2025-01-01T00:00:00Z"
        db.execute(
            "INSERT INTO spaces (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("sp1", "backend", now, now),
        )
        db.commit()

        # Create one in-space source with a matching chunk
        _seed_source(db, "s-in")
        _seed_chunk(db, "ch-in", "s-in", "deployment orchestration kubernetes")
        _seed_space_source(db, "sp1", "s-in")

        # Create many out-of-space sources that also match the query
        for i in range(15):
            sid = f"s-out-{i}"
            _seed_source(db, sid)
            _seed_chunk(db, f"ch-out-{i}", sid, f"deployment orchestration kubernetes variant {i}")

        # With limit=10, post-filter approach would miss the in-space chunk
        results = search_keyword_source_chunks(db, "deployment", limit=10, space_id="sp1")
        ids = [r["chunk_id"] for r in results]
        assert "ch-in" in ids


class TestFtsUpdateTrigger:
    def test_message_update_reflected_in_fts(self) -> None:
        """FTS index must stay in sync when message content is updated."""
        db = _init_db()
        _seed_conversation(db, "c1")
        _seed_message(db, "m1", "c1", "original content about pytest")

        results = search_keyword_messages(db, "pytest")
        assert len(results) == 1

        db.execute("UPDATE messages SET content = 'updated content about ruff' WHERE id = 'm1'")
        db.commit()

        assert search_keyword_messages(db, "pytest") == []
        results = search_keyword_messages(db, "ruff")
        assert len(results) == 1
        assert results[0]["message_id"] == "m1"

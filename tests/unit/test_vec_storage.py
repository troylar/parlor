"""Tests for vector storage functions."""

from __future__ import annotations

import sqlite3

import pytest

from anteroom.db import _SCHEMA, _VEC_METADATA_SCHEMA, _make_vec_schema, has_vec_support
from anteroom.services.storage import (
    create_conversation,
    create_message,
    delete_embeddings_for_conversation,
    get_embedding_stats,
    get_unembedded_messages,
    search_similar_messages,
    store_embedding,
)


def _vec_available() -> bool:
    """Check if sqlite-vec is available in this environment."""
    try:
        import sqlite_vec

        conn = sqlite3.connect(":memory:")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.close()
        return True
    except Exception:
        return False


VEC_AVAILABLE = _vec_available()


class _FakeThreadSafe:
    """Minimal ThreadSafeConnection-like wrapper for testing."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, sql, parameters=()):
        return self._conn.execute(sql, parameters)

    def execute_fetchone(self, sql, parameters=()):
        return self._conn.execute(sql, parameters).fetchone()

    def execute_fetchall(self, sql, parameters=()):
        return self._conn.execute(sql, parameters).fetchall()

    def commit(self):
        self._conn.commit()

    class _TxContext:
        def __init__(self, conn):
            self._conn = conn

        def __enter__(self):
            return self._conn

        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type:
                self._conn.rollback()
            else:
                self._conn.commit()
            return False

    def transaction(self):
        return self._TxContext(self._conn)


def _init_vec_db() -> _FakeThreadSafe:
    """Create an in-memory db with vec support."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)

    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception:
        pass

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    try:
        conn.executescript(_VEC_METADATA_SCHEMA)
    except sqlite3.OperationalError:
        pass
    try:
        conn.executescript(_make_vec_schema(1536))
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return _FakeThreadSafe(conn)


def _seed_messages(db: _FakeThreadSafe, conv_id: str, count: int = 3) -> list[dict]:
    """Create a conversation and some messages."""
    create_conversation(db, title="Test Conv")
    # Need to manually insert since create_conversation generates its own ID
    now = "2025-01-01T00:00:00Z"
    db.execute(
        "INSERT OR IGNORE INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (conv_id, "Test Conv", now, now),
    )
    db.commit()

    msgs = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        content = f"Test message number {i} with enough content to be embedded"
        msg = create_message(db, conv_id, role, content)
        msgs.append(msg)
    return msgs


@pytest.mark.skipif(not VEC_AVAILABLE, reason="sqlite-vec not available")
class TestStoreEmbedding:
    def test_stores_in_both_tables(self) -> None:
        db = _init_vec_db()
        msgs = _seed_messages(db, "conv-1", count=1)
        msg = msgs[0]
        embedding = [0.1] * 1536

        store_embedding(db, msg["id"], "conv-1", embedding, "hash123")

        # Check metadata table
        row = db.execute_fetchone("SELECT * FROM message_embeddings WHERE message_id = ?", (msg["id"],))
        assert row is not None
        assert dict(row)["content_hash"] == "hash123"

        # Check vec table
        vec_row = db.execute_fetchone("SELECT * FROM vec_messages WHERE message_id = ?", (msg["id"],))
        assert vec_row is not None

    def test_replaces_existing_embedding(self) -> None:
        db = _init_vec_db()
        msgs = _seed_messages(db, "conv-1", count=1)
        msg = msgs[0]

        store_embedding(db, msg["id"], "conv-1", [0.1] * 1536, "hash1")
        store_embedding(db, msg["id"], "conv-1", [0.2] * 1536, "hash2")

        row = db.execute_fetchone("SELECT * FROM message_embeddings WHERE message_id = ?", (msg["id"],))
        assert dict(row)["content_hash"] == "hash2"


@pytest.mark.skipif(not VEC_AVAILABLE, reason="sqlite-vec not available")
class TestSearchSimilarMessages:
    def test_returns_nearest_neighbors(self) -> None:
        db = _init_vec_db()
        msgs = _seed_messages(db, "conv-1", count=3)

        for i, msg in enumerate(msgs):
            emb = [0.0] * 1536
            emb[i] = 1.0
            store_embedding(db, msg["id"], "conv-1", emb, f"hash{i}")

        query = [0.0] * 1536
        query[0] = 1.0
        results = search_similar_messages(db, query, limit=2)

        assert len(results) >= 1
        assert results[0]["message_id"] == msgs[0]["id"]

    def test_filters_by_conversation(self) -> None:
        db = _init_vec_db()
        msgs1 = _seed_messages(db, "conv-1", count=1)
        # Create second conversation
        db.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("conv-2", "Other Conv", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
        )
        db.commit()
        msg2 = create_message(db, "conv-2", "user", "Different conversation message content here")

        emb = [0.1] * 1536
        store_embedding(db, msgs1[0]["id"], "conv-1", emb, "h1")
        store_embedding(db, msg2["id"], "conv-2", emb, "h2")

        results = search_similar_messages(db, emb, limit=10, conversation_id="conv-1")
        conv_ids = {r["conversation_id"] for r in results}
        assert conv_ids == {"conv-1"}

    def test_returns_empty_when_no_data(self) -> None:
        db = _init_vec_db()
        results = search_similar_messages(db, [0.1] * 1536)
        assert results == []


class TestGetUnembeddedMessages:
    def test_returns_messages_without_embeddings(self) -> None:
        db = _init_vec_db()
        _seed_messages(db, "conv-1", count=3)

        unembedded = get_unembedded_messages(db)
        assert len(unembedded) == 3

    @pytest.mark.skipif(not VEC_AVAILABLE, reason="sqlite-vec not available")
    def test_excludes_embedded_messages(self) -> None:
        db = _init_vec_db()
        msgs = _seed_messages(db, "conv-1", count=3)

        store_embedding(db, msgs[0]["id"], "conv-1", [0.1] * 1536, "hash0")

        unembedded = get_unembedded_messages(db)
        ids = {m["id"] for m in unembedded}
        assert msgs[0]["id"] not in ids
        assert len(unembedded) == 2


@pytest.mark.skipif(not VEC_AVAILABLE, reason="sqlite-vec not available")
class TestDeleteEmbeddingsForConversation:
    def test_deletes_all_embeddings(self) -> None:
        db = _init_vec_db()
        msgs = _seed_messages(db, "conv-1", count=2)

        for msg in msgs:
            store_embedding(db, msg["id"], "conv-1", [0.1] * 1536, "hash")

        delete_embeddings_for_conversation(db, "conv-1")

        meta_count = db.execute_fetchone(
            "SELECT COUNT(*) FROM message_embeddings WHERE conversation_id = ?", ("conv-1",)
        )
        assert meta_count[0] == 0

        vec_count = db.execute_fetchone("SELECT COUNT(*) FROM vec_messages WHERE conversation_id = ?", ("conv-1",))
        assert vec_count[0] == 0


class TestGetEmbeddingStats:
    def test_returns_accurate_counts(self) -> None:
        db = _init_vec_db()
        _seed_messages(db, "conv-1", count=3)

        stats = get_embedding_stats(db)
        assert stats["total_messages"] == 3
        assert stats["embedded_messages"] == 0
        assert stats["pending_messages"] == 3

    @pytest.mark.skipif(not VEC_AVAILABLE, reason="sqlite-vec not available")
    def test_counts_after_embedding(self) -> None:
        db = _init_vec_db()
        msgs = _seed_messages(db, "conv-1", count=3)

        store_embedding(db, msgs[0]["id"], "conv-1", [0.1] * 1536, "hash")

        stats = get_embedding_stats(db)
        assert stats["total_messages"] == 3
        assert stats["embedded_messages"] == 1
        assert stats["pending_messages"] == 2


class TestValidateEmbedding:
    def test_rejects_empty_embedding(self) -> None:
        from anteroom.services.storage import _validate_embedding

        with pytest.raises(ValueError, match="1-4096 dimensions"):
            _validate_embedding([])

    def test_rejects_oversized_embedding(self) -> None:
        from anteroom.services.storage import _validate_embedding

        with pytest.raises(ValueError, match="1-4096 dimensions"):
            _validate_embedding([0.1] * 5000)

    def test_rejects_nan(self) -> None:
        from anteroom.services.storage import _validate_embedding

        with pytest.raises(ValueError, match="not a finite number"):
            _validate_embedding([float("nan"), 0.1, 0.2])

    def test_rejects_inf(self) -> None:
        from anteroom.services.storage import _validate_embedding

        with pytest.raises(ValueError, match="not a finite number"):
            _validate_embedding([0.1, float("inf")])

    def test_rejects_negative_inf(self) -> None:
        from anteroom.services.storage import _validate_embedding

        with pytest.raises(ValueError, match="not a finite number"):
            _validate_embedding([float("-inf"), 0.1])

    def test_accepts_valid_embedding(self) -> None:
        from anteroom.services.storage import _validate_embedding

        result = _validate_embedding([0.1, 0.2, 0.3])
        assert isinstance(result, bytes)
        assert len(result) == 3 * 4  # 3 floats * 4 bytes each

    def test_accepts_integers(self) -> None:
        from anteroom.services.storage import _validate_embedding

        result = _validate_embedding([1, 2, 3])
        assert isinstance(result, bytes)


@pytest.mark.skipif(not VEC_AVAILABLE, reason="sqlite-vec not available")
class TestSearchLimitCapping:
    def test_limit_capped_at_max(self) -> None:
        db = _init_vec_db()
        msgs = _seed_messages(db, "conv-1", count=1)
        store_embedding(db, msgs[0]["id"], "conv-1", [0.1] * 1536, "hash")

        # Should not error even with huge limit
        results = search_similar_messages(db, [0.1] * 1536, limit=999999)
        assert isinstance(results, list)

    def test_negative_limit_becomes_one(self) -> None:
        db = _init_vec_db()
        msgs = _seed_messages(db, "conv-1", count=1)
        store_embedding(db, msgs[0]["id"], "conv-1", [0.1] * 1536, "hash")

        results = search_similar_messages(db, [0.1] * 1536, limit=-5)
        assert isinstance(results, list)


class TestHasVecSupport:
    def test_returns_correct_value(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            import sqlite_vec

            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            assert has_vec_support(conn) is True
        except Exception:
            assert has_vec_support(conn) is False
        conn.close()

    def test_false_without_extension(self) -> None:
        conn = sqlite3.connect(":memory:")
        assert has_vec_support(conn) is False
        conn.close()


class TestGracefulDegradation:
    def test_store_embedding_noop_without_vec(self) -> None:
        """store_embedding should silently do nothing when vec is not loaded."""
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        try:
            conn.executescript(_VEC_METADATA_SCHEMA)
        except sqlite3.OperationalError:
            pass
        conn.commit()
        db = _FakeThreadSafe(conn)

        # Should not raise
        store_embedding(db, "msg-1", "conv-1", [0.1] * 1536, "hash")

    def test_search_returns_empty_without_vec(self) -> None:
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
        db = _FakeThreadSafe(conn)

        results = search_similar_messages(db, [0.1] * 1536)
        assert results == []

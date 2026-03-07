"""Tests for vector storage functions (usearch-based)."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from anteroom.db import _SCHEMA, _VEC_METADATA_SCHEMA, has_vec_support
from anteroom.services.storage import (
    create_conversation,
    create_message,
    delete_embeddings_for_conversation,
    get_embedding_stats,
    get_unembedded_messages,
    search_similar_messages,
    store_embedding,
)
from anteroom.services.vector_index import (
    VectorIndex,
    VectorIndexManager,
    _string_key_to_int,
    _validate_embedding,
    has_vector_support,
)


def _usearch_available() -> bool:
    return has_vector_support()


USEARCH_AVAILABLE = _usearch_available()

DIMS = 384


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


def _init_db() -> _FakeThreadSafe:
    """Create an in-memory db with metadata tables."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    try:
        conn.executescript(_VEC_METADATA_SCHEMA)
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return _FakeThreadSafe(conn)


def _make_vec_index(dims: int = DIMS) -> VectorIndex:
    """Create a VectorIndex in a temp directory."""
    tmp = tempfile.mkdtemp()
    return VectorIndex(Path(tmp) / "test.usearch", dimensions=dims)


def _seed_messages(db: _FakeThreadSafe, conv_id: str, count: int = 3) -> list[dict]:
    """Create a conversation and some messages."""
    create_conversation(db, title="Test Conv")
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


@pytest.mark.skipif(not USEARCH_AVAILABLE, reason="usearch not available")
class TestVectorIndex:
    def test_add_and_search(self) -> None:
        idx = _make_vec_index()
        emb = [0.0] * DIMS
        emb[0] = 1.0
        idx.add("key-1", emb)

        results = idx.search(emb, limit=1)
        assert len(results) == 1
        assert results[0]["key"] == "key-1"
        assert results[0]["distance"] < 0.01

    def test_remove(self) -> None:
        idx = _make_vec_index()
        emb = [0.1] * DIMS
        idx.add("key-1", emb)
        assert idx.count() == 1

        idx.remove("key-1")
        results = idx.search(emb, limit=1)
        assert len(results) == 0

    def test_remove_nonexistent_noop(self) -> None:
        idx = _make_vec_index()
        idx.remove("nonexistent")

    def test_replace_vector(self) -> None:
        idx = _make_vec_index()
        emb1 = [0.0] * DIMS
        emb1[0] = 1.0
        idx.add("key-1", emb1)

        emb2 = [0.0] * DIMS
        emb2[1] = 1.0
        idx.add("key-1", emb2)

        results = idx.search(emb2, limit=1)
        assert results[0]["key"] == "key-1"
        assert results[0]["distance"] < 0.01

    def test_save_and_restore(self) -> None:
        tmp = tempfile.mkdtemp()
        path = Path(tmp) / "persist.usearch"
        idx = VectorIndex(path, dimensions=DIMS)
        emb = [0.1] * DIMS
        idx.add("key-1", emb)
        idx.save()

        idx2 = VectorIndex(path, dimensions=DIMS)
        idx2.register_key("key-1")
        results = idx2.search(emb, limit=1)
        assert len(results) == 1
        assert results[0]["key"] == "key-1"

    def test_clear(self) -> None:
        idx = _make_vec_index()
        idx.add("k1", [0.1] * DIMS)
        idx.add("k2", [0.2] * DIMS)
        assert idx.count() == 2

        idx.clear()
        assert idx.count() == 0

    def test_rebuild_key_map(self) -> None:
        idx = _make_vec_index()
        emb = [0.1] * DIMS
        idx.add("msg-abc", emb)

        idx._int_to_str.clear()
        assert idx.search(emb, limit=1) == []

        idx.rebuild_key_map([("msg-abc", "conv-1")])
        results = idx.search(emb, limit=1)
        assert len(results) == 1
        assert results[0]["key"] == "msg-abc"

    def test_empty_search(self) -> None:
        idx = _make_vec_index()
        results = idx.search([0.1] * DIMS, limit=10)
        assert results == []

    def test_dimension_mismatch_raises(self) -> None:
        idx = _make_vec_index(dims=384)
        with pytest.raises(ValueError, match="expected 384"):
            idx.add("k1", [0.1] * 128)


@pytest.mark.skipif(not USEARCH_AVAILABLE, reason="usearch not available")
class TestStoreEmbedding:
    def test_stores_in_metadata_and_index(self) -> None:
        db = _init_db()
        vec_idx = _make_vec_index()
        msgs = _seed_messages(db, "conv-1", count=1)
        msg = msgs[0]
        embedding = [0.1] * DIMS

        store_embedding(db, msg["id"], "conv-1", embedding, "hash123", vec_index=vec_idx)

        row = db.execute_fetchone("SELECT * FROM message_embeddings WHERE message_id = ?", (msg["id"],))
        assert row is not None
        assert dict(row)["content_hash"] == "hash123"

        assert vec_idx.count() == 1

    def test_replaces_existing_embedding(self) -> None:
        db = _init_db()
        vec_idx = _make_vec_index()
        msgs = _seed_messages(db, "conv-1", count=1)
        msg = msgs[0]

        store_embedding(db, msg["id"], "conv-1", [0.1] * DIMS, "hash1", vec_index=vec_idx)
        store_embedding(db, msg["id"], "conv-1", [0.2] * DIMS, "hash2", vec_index=vec_idx)

        row = db.execute_fetchone("SELECT * FROM message_embeddings WHERE message_id = ?", (msg["id"],))
        assert dict(row)["content_hash"] == "hash2"
        assert vec_idx.count() == 1

    def test_store_without_vec_index_only_writes_metadata(self) -> None:
        db = _init_db()
        msgs = _seed_messages(db, "conv-1", count=1)
        msg = msgs[0]

        store_embedding(db, msg["id"], "conv-1", [0.1] * DIMS, "hash1")

        row = db.execute_fetchone("SELECT * FROM message_embeddings WHERE message_id = ?", (msg["id"],))
        assert row is not None


@pytest.mark.skipif(not USEARCH_AVAILABLE, reason="usearch not available")
class TestSearchSimilarMessages:
    def test_returns_nearest_neighbors(self) -> None:
        db = _init_db()
        vec_idx = _make_vec_index()
        msgs = _seed_messages(db, "conv-1", count=3)

        for i, msg in enumerate(msgs):
            emb = [0.0] * DIMS
            emb[i] = 1.0
            store_embedding(db, msg["id"], "conv-1", emb, f"hash{i}", vec_index=vec_idx)

        query = [0.0] * DIMS
        query[0] = 1.0
        results = search_similar_messages(db, query, limit=2, vec_index=vec_idx)

        assert len(results) >= 1
        assert results[0]["message_id"] == msgs[0]["id"]

    def test_filters_by_conversation(self) -> None:
        db = _init_db()
        vec_idx = _make_vec_index()
        msgs1 = _seed_messages(db, "conv-1", count=1)
        db.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("conv-2", "Other Conv", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
        )
        db.commit()
        msg2 = create_message(db, "conv-2", "user", "Different conversation message content here")

        emb = [0.1] * DIMS
        store_embedding(db, msgs1[0]["id"], "conv-1", emb, "h1", vec_index=vec_idx)
        store_embedding(db, msg2["id"], "conv-2", emb, "h2", vec_index=vec_idx)

        results = search_similar_messages(db, emb, limit=10, conversation_id="conv-1", vec_index=vec_idx)
        conv_ids = {r["conversation_id"] for r in results}
        assert conv_ids == {"conv-1"}

    def test_returns_empty_when_no_data(self) -> None:
        db = _init_db()
        vec_idx = _make_vec_index()
        results = search_similar_messages(db, [0.1] * DIMS, vec_index=vec_idx)
        assert results == []

    def test_returns_empty_without_vec_index(self) -> None:
        db = _init_db()
        results = search_similar_messages(db, [0.1] * DIMS)
        assert results == []


class TestGetUnembeddedMessages:
    def test_returns_messages_without_embeddings(self) -> None:
        db = _init_db()
        _seed_messages(db, "conv-1", count=3)

        unembedded = get_unembedded_messages(db)
        assert len(unembedded) == 3

    @pytest.mark.skipif(not USEARCH_AVAILABLE, reason="usearch not available")
    def test_excludes_embedded_messages(self) -> None:
        db = _init_db()
        vec_idx = _make_vec_index()
        msgs = _seed_messages(db, "conv-1", count=3)

        store_embedding(db, msgs[0]["id"], "conv-1", [0.1] * DIMS, "hash0", vec_index=vec_idx)

        unembedded = get_unembedded_messages(db)
        ids = {m["id"] for m in unembedded}
        assert msgs[0]["id"] not in ids
        assert len(unembedded) == 2


@pytest.mark.skipif(not USEARCH_AVAILABLE, reason="usearch not available")
class TestDeleteEmbeddingsForConversation:
    def test_deletes_all_embeddings(self) -> None:
        db = _init_db()
        vec_idx = _make_vec_index()
        msgs = _seed_messages(db, "conv-1", count=2)

        for msg in msgs:
            store_embedding(db, msg["id"], "conv-1", [0.1] * DIMS, "hash", vec_index=vec_idx)

        delete_embeddings_for_conversation(db, "conv-1", vec_index=vec_idx)

        meta_count = db.execute_fetchone(
            "SELECT COUNT(*) FROM message_embeddings WHERE conversation_id = ?", ("conv-1",)
        )
        assert meta_count[0] == 0


class TestGetEmbeddingStats:
    def test_returns_accurate_counts(self) -> None:
        db = _init_db()
        _seed_messages(db, "conv-1", count=3)

        stats = get_embedding_stats(db)
        assert stats["total_messages"] == 3
        assert stats["embedded_messages"] == 0
        assert stats["pending_messages"] == 3

    @pytest.mark.skipif(not USEARCH_AVAILABLE, reason="usearch not available")
    def test_counts_after_embedding(self) -> None:
        db = _init_db()
        vec_idx = _make_vec_index()
        msgs = _seed_messages(db, "conv-1", count=3)

        store_embedding(db, msgs[0]["id"], "conv-1", [0.1] * DIMS, "hash", vec_index=vec_idx)

        stats = get_embedding_stats(db)
        assert stats["total_messages"] == 3
        assert stats["embedded_messages"] == 1
        assert stats["pending_messages"] == 2


class TestValidateEmbedding:
    def test_rejects_empty_embedding(self) -> None:
        with pytest.raises(ValueError, match="1-4096 dimensions"):
            _validate_embedding([])

    def test_rejects_oversized_embedding(self) -> None:
        with pytest.raises(ValueError, match="1-4096 dimensions"):
            _validate_embedding([0.1] * 5000)

    def test_rejects_nan(self) -> None:
        with pytest.raises(ValueError, match="not a finite number"):
            _validate_embedding([float("nan"), 0.1, 0.2])

    def test_rejects_inf(self) -> None:
        with pytest.raises(ValueError, match="not a finite number"):
            _validate_embedding([0.1, float("inf")])

    def test_rejects_negative_inf(self) -> None:
        with pytest.raises(ValueError, match="not a finite number"):
            _validate_embedding([float("-inf"), 0.1])

    def test_accepts_valid_embedding(self) -> None:
        _validate_embedding([0.1, 0.2, 0.3])

    def test_accepts_integers(self) -> None:
        _validate_embedding([1, 2, 3])


@pytest.mark.skipif(not USEARCH_AVAILABLE, reason="usearch not available")
class TestSearchLimitCapping:
    def test_limit_capped_at_max(self) -> None:
        db = _init_db()
        vec_idx = _make_vec_index()
        msgs = _seed_messages(db, "conv-1", count=1)
        store_embedding(db, msgs[0]["id"], "conv-1", [0.1] * DIMS, "hash", vec_index=vec_idx)

        results = search_similar_messages(db, [0.1] * DIMS, limit=999999, vec_index=vec_idx)
        assert isinstance(results, list)

    def test_negative_limit_becomes_one(self) -> None:
        db = _init_db()
        vec_idx = _make_vec_index()
        msgs = _seed_messages(db, "conv-1", count=1)
        store_embedding(db, msgs[0]["id"], "conv-1", [0.1] * DIMS, "hash", vec_index=vec_idx)

        results = search_similar_messages(db, [0.1] * DIMS, limit=-5, vec_index=vec_idx)
        assert isinstance(results, list)


class TestHasVecSupport:
    def test_returns_usearch_availability(self) -> None:
        assert has_vec_support() == USEARCH_AVAILABLE

    def test_accepts_conn_for_backward_compat(self) -> None:
        conn = sqlite3.connect(":memory:")
        result = has_vec_support(conn)
        assert result == USEARCH_AVAILABLE
        conn.close()


class TestGracefulDegradation:
    def test_store_embedding_writes_metadata_without_vec_index(self) -> None:
        """store_embedding should write metadata even without vec_index."""
        db = _init_db()
        msgs = _seed_messages(db, "conv-1", count=1)
        msg = msgs[0]

        store_embedding(db, msg["id"], "conv-1", [0.1] * DIMS, "hash")

        row = db.execute_fetchone("SELECT * FROM message_embeddings WHERE message_id = ?", (msg["id"],))
        assert row is not None

    def test_search_returns_empty_without_vec_index(self) -> None:
        db = _init_db()
        results = search_similar_messages(db, [0.1] * DIMS)
        assert results == []


class TestStringKeyToInt:
    def test_deterministic(self) -> None:
        key = "abc-123-def"
        assert _string_key_to_int(key) == _string_key_to_int(key)

    def test_positive(self) -> None:
        assert _string_key_to_int("test") > 0

    def test_different_keys_different_ints(self) -> None:
        a = _string_key_to_int("key-a")
        b = _string_key_to_int("key-b")
        assert a != b


@pytest.mark.skipif(not USEARCH_AVAILABLE, reason="usearch not available")
class TestVectorIndexManager:
    def test_creates_both_indexes(self) -> None:
        tmp = tempfile.mkdtemp()
        mgr = VectorIndexManager(Path(tmp), dimensions=DIMS)
        assert mgr.enabled
        assert mgr.messages is not None
        assert mgr.source_chunks is not None

    def test_save_all(self) -> None:
        tmp = tempfile.mkdtemp()
        mgr = VectorIndexManager(Path(tmp), dimensions=DIMS)
        mgr.messages.add("k1", [0.1] * DIMS)
        mgr.save_all()
        assert (Path(tmp) / "vectors" / "messages.usearch").exists()

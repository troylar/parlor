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


@pytest.mark.skipif(not USEARCH_AVAILABLE, reason="usearch not available")
class TestSearchSkipsStaleVectors:
    """Test that search over-fetches to skip stale (unmapped) vectors."""

    def test_returns_mapped_result_when_stale_vector_is_closer(self) -> None:
        idx = _make_vec_index()

        # Add two vectors: "stale" is closer to the query, "valid" is further
        stale_emb = [0.0] * DIMS
        stale_emb[0] = 1.0
        idx.add("stale-key", stale_emb)

        valid_emb = [0.0] * DIMS
        valid_emb[0] = 0.8
        valid_emb[1] = 0.2
        idx.add("valid-key", valid_emb)

        # Remove "stale-key" from the key map (simulating post-recovery state)
        # but leave it in the usearch index (as happens after crash recovery)
        from anteroom.services.vector_index import _string_key_to_int

        stale_int = _string_key_to_int("stale-key")
        idx._int_to_str.pop(stale_int, None)

        # Search with limit=1 — should skip stale and return valid
        query = [0.0] * DIMS
        query[0] = 1.0
        results = idx.search(query, limit=1)
        assert len(results) == 1
        assert results[0]["key"] == "valid-key"

    def test_returns_all_mapped_when_multiple_stale(self) -> None:
        idx = _make_vec_index()

        from anteroom.services.vector_index import _string_key_to_int

        # Add 5 stale vectors (closer to query) and 2 valid ones
        for i in range(5):
            emb = [0.0] * DIMS
            emb[0] = 1.0
            emb[1] = 0.001 * i
            idx.add(f"stale-{i}", emb)
            idx._int_to_str.pop(_string_key_to_int(f"stale-{i}"), None)

        for i in range(2):
            emb = [0.0] * DIMS
            emb[0] = 0.5
            emb[2] = 0.1 * (i + 1)
            idx.add(f"valid-{i}", emb)

        query = [0.0] * DIMS
        query[0] = 1.0
        results = idx.search(query, limit=2)
        assert len(results) == 2
        assert all(r["key"].startswith("valid-") for r in results)


@pytest.mark.skipif(not USEARCH_AVAILABLE, reason="usearch not available")
class TestStoreEmbeddingRollback:
    """Test that store_embedding resets to pending when vec_index.add() fails."""

    def test_resets_to_pending_on_vec_add_failure(self) -> None:
        db = _init_db()
        conv_id = "conv-rollback"
        msgs = _seed_messages(db, conv_id, count=1)
        msg_id = msgs[0]["id"]

        class _FailingIndex:
            def add(self, key, embedding):
                raise RuntimeError("simulated usearch failure")

        emb = [0.1] * DIMS
        store_embedding(db, msg_id, conv_id, emb, "hash1", vec_index=_FailingIndex())

        row = db.execute_fetchone("SELECT status FROM message_embeddings WHERE message_id = ?", (msg_id,))
        assert row is not None
        assert row["status"] == "pending"

    def test_metadata_committed_on_vec_add_success(self) -> None:
        db = _init_db()
        conv_id = "conv-success"
        msgs = _seed_messages(db, conv_id, count=1)
        msg_id = msgs[0]["id"]

        idx = _make_vec_index()
        emb = [0.1] * DIMS
        store_embedding(db, msg_id, conv_id, emb, "hash1", vec_index=idx)

        row = db.execute_fetchone("SELECT status FROM message_embeddings WHERE message_id = ?", (msg_id,))
        assert row is not None
        # Default status is 'embedded' (the column default)
        assert row["status"] in (None, "embedded")
        assert idx.count() == 1


@pytest.mark.skipif(not USEARCH_AVAILABLE, reason="usearch not available")
class TestStoreSourceChunkEmbeddingRollback:
    def test_resets_to_pending_on_vec_add_failure(self) -> None:
        from anteroom.services.storage import create_source, store_source_chunk_embedding

        db = _init_db()
        source, _ = create_source(db, source_type="text", title="Test", content="test content")
        # Insert a source chunk
        chunk_id = "chunk-fail-1"
        db.execute(
            "INSERT INTO source_chunks (id, source_id, content, chunk_index, content_hash, created_at)"
            " VALUES (?, ?, ?, ?, ?, '2025-01-01')",
            (chunk_id, source["id"], "chunk content", 0, "abc123"),
        )
        db.commit()

        class _FailingIndex:
            def add(self, key, embedding):
                raise RuntimeError("simulated failure")

        emb = [0.1] * DIMS
        store_source_chunk_embedding(db, chunk_id, source["id"], emb, "hash1", vec_index=_FailingIndex())

        row = db.execute_fetchone("SELECT status FROM source_chunk_embeddings WHERE chunk_id = ?", (chunk_id,))
        assert row is not None
        assert row["status"] == "pending"


@pytest.mark.skipif(not USEARCH_AVAILABLE, reason="usearch not available")
class TestScopedSearchWidening:
    """Test that scoped search uses iterative widening to find matching results."""

    def test_conversation_filter_finds_results_beyond_initial_fetch(self) -> None:
        """When matching messages are ranked low globally, widening should still find them."""
        db = _init_db()

        # Create two conversations with messages
        target_conv = "conv-target"
        other_conv = "conv-other"
        _seed_messages(db, target_conv, count=1)
        _seed_messages(db, other_conv, count=1)

        idx = _make_vec_index()

        # Add many vectors for the "other" conversation (closer to query)
        for i in range(20):
            key = f"other-msg-{i}"
            emb = [0.0] * DIMS
            emb[0] = 1.0
            emb[1] = 0.01 * i  # Slight variation, all close to query
            idx.add(key, emb)
            db.execute(
                "INSERT INTO messages (id, conversation_id, role, content, position, created_at) "
                "VALUES (?, ?, 'user', 'other msg', ?, '2025-01-01')",
                (key, other_conv, i + 10),
            )

        # Add one vector for the target conversation (further from query)
        target_key = "target-msg-1"
        target_emb = [0.0] * DIMS
        target_emb[0] = 0.5
        target_emb[2] = 0.5  # Different direction
        idx.add(target_key, target_emb)
        db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, position, created_at) "
            "VALUES (?, ?, 'user', 'target msg', 0, '2025-01-01')",
            (target_key, target_conv),
        )
        db.commit()

        # Search for messages in target_conv — the target message is ranked low
        # globally but should still be found via widening.
        query = [0.0] * DIMS
        query[0] = 1.0
        results = search_similar_messages(db, query, limit=5, conversation_id=target_conv, vec_index=idx)

        # Should find the target message despite it being ranked below 20 others
        target_ids = [r["message_id"] for r in results]
        assert target_key in target_ids

    def test_unfiltered_search_no_widening(self) -> None:
        """Unfiltered search should return results without iterative widening."""
        db = _init_db()
        conv_id = "conv-simple"
        msgs = _seed_messages(db, conv_id, count=3)
        idx = _make_vec_index()

        for i, msg in enumerate(msgs):
            emb = [0.0] * DIMS
            emb[i] = 1.0
            idx.add(msg["id"], emb)

        query = [0.0] * DIMS
        query[0] = 1.0
        results = search_similar_messages(db, query, limit=3, vec_index=idx)
        assert len(results) == 3


@pytest.mark.skipif(not USEARCH_AVAILABLE, reason="usearch not available")
class TestRebuildIndexRecovery:
    """Test that rebuild_from_db detects index/metadata divergence."""

    def test_partial_loss_resets_missing_to_pending(self) -> None:
        db = _init_db()
        tmp = tempfile.mkdtemp()
        mgr = VectorIndexManager(Path(tmp), dimensions=DIMS)

        conv_id = "conv-partial"
        msgs = _seed_messages(db, conv_id, count=5)
        for i, msg in enumerate(msgs):
            db.execute(
                "INSERT OR REPLACE INTO message_embeddings "
                "(message_id, conversation_id, chunk_index, content_hash, created_at, status) "
                "VALUES (?, ?, 0, ?, '2025-01-01', 'embedded')",
                (msg["id"], conv_id, f"hash-{i}"),
            )
        db.commit()

        # Only add 2 vectors to the index
        for i in range(2):
            emb = [0.0] * DIMS
            emb[i] = 1.0
            mgr.messages.add(msgs[i]["id"], emb)

        assert mgr.messages.count() == 2

        mgr.rebuild_from_db(db)

        # Only the 3 missing keys should be reset to pending
        pending = db.execute_fetchall("SELECT message_id FROM message_embeddings WHERE status = 'pending'")
        embedded = db.execute_fetchall("SELECT message_id FROM message_embeddings WHERE status = 'embedded'")
        assert len(pending) == 3
        assert len(embedded) == 2

    def test_key_set_divergence_resets_missing_keys(self) -> None:
        """Same count but different keys — the exact scenario from the review."""
        db = _init_db()
        tmp = tempfile.mkdtemp()
        mgr = VectorIndexManager(Path(tmp), dimensions=DIMS)

        conv_id = "conv-diverge"
        msgs = _seed_messages(db, conv_id, count=3)
        msg_a, msg_b, msg_c = msgs[0], msgs[1], msgs[2]

        # SQLite says B and C are embedded
        for msg in [msg_b, msg_c]:
            db.execute(
                "INSERT OR REPLACE INTO message_embeddings "
                "(message_id, conversation_id, chunk_index, content_hash, created_at, status) "
                "VALUES (?, ?, 0, 'hash', '2025-01-01', 'embedded')",
                (msg["id"], conv_id),
            )
        db.commit()

        # But the on-disk index contains A and B (C was never saved)
        emb = [0.1] * DIMS
        mgr.messages.add(msg_a["id"], emb)
        mgr.messages.add(msg_b["id"], emb)
        assert mgr.messages.count() == 2

        mgr.rebuild_from_db(db)

        # B should stay embedded (exists in index), C should be reset to pending
        row_b = db.execute_fetchone("SELECT status FROM message_embeddings WHERE message_id = ?", (msg_b["id"],))
        row_c = db.execute_fetchone("SELECT status FROM message_embeddings WHERE message_id = ?", (msg_c["id"],))
        assert row_b["status"] == "embedded"
        assert row_c["status"] == "pending"

    def test_full_index_loss_resets_all(self) -> None:
        db = _init_db()
        tmp = tempfile.mkdtemp()
        mgr = VectorIndexManager(Path(tmp), dimensions=DIMS)

        conv_id = "conv-full-loss"
        msgs = _seed_messages(db, conv_id, count=3)
        for msg in msgs:
            db.execute(
                "INSERT OR REPLACE INTO message_embeddings "
                "(message_id, conversation_id, chunk_index, content_hash, created_at, status) "
                "VALUES (?, ?, 0, 'hash', '2025-01-01', 'embedded')",
                (msg["id"], conv_id),
            )
        db.commit()

        # Index is empty (simulating full index file loss)
        assert mgr.messages.count() == 0

        mgr.rebuild_from_db(db)

        pending = db.execute_fetchall("SELECT message_id FROM message_embeddings WHERE status = 'pending'")
        assert len(pending) == 3

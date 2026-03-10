"""Tests for message metadata persistence (issue #822)."""

from __future__ import annotations

import sqlite3

import pytest

from anteroom.db import _FTS_SCHEMA, _FTS_TRIGGERS, _SCHEMA, ThreadSafeConnection, _run_migrations
from anteroom.services.storage import (
    copy_conversation_to_db,
    create_conversation,
    create_message,
    fork_conversation,
    list_messages,
    update_message_metadata,
)


@pytest.fixture()
def db() -> ThreadSafeConnection:
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
    return ThreadSafeConnection(conn)


class TestCreateMessageMetadata:
    def test_create_message_without_metadata(self, db: ThreadSafeConnection) -> None:
        conv = create_conversation(db, title="test")
        msg = create_message(db, conv["id"], "assistant", "hello")
        assert msg["metadata"] is None

    def test_create_message_with_metadata(self, db: ThreadSafeConnection) -> None:
        conv = create_conversation(db, title="test")
        meta = {"rag_sources": [{"label": "doc.pdf", "type": "source_chunk", "source_id": "s1"}]}
        msg = create_message(db, conv["id"], "assistant", "hello", metadata=meta)
        assert msg["metadata"] == meta

    def test_metadata_persisted_to_db(self, db: ThreadSafeConnection) -> None:
        conv = create_conversation(db, title="test")
        meta = {"rag_sources": [{"label": "notes.md", "type": "source_chunk", "source_id": "s2"}]}
        create_message(db, conv["id"], "assistant", "hello", metadata=meta)
        messages = list_messages(db, conv["id"])
        assert len(messages) == 1
        assert messages[0]["metadata"] == meta

    def test_metadata_none_when_not_provided(self, db: ThreadSafeConnection) -> None:
        conv = create_conversation(db, title="test")
        create_message(db, conv["id"], "user", "hi")
        messages = list_messages(db, conv["id"])
        assert messages[0]["metadata"] is None


class TestUpdateMessageMetadata:
    def test_update_metadata_on_existing_message(self, db: ThreadSafeConnection) -> None:
        conv = create_conversation(db, title="test")
        msg = create_message(db, conv["id"], "assistant", "hello")
        meta = {"rag_sources": [{"label": "doc.pdf", "type": "source_chunk", "source_id": "s1"}]}
        update_message_metadata(db, msg["id"], meta)
        messages = list_messages(db, conv["id"])
        assert messages[0]["metadata"] == meta

    def test_update_metadata_replaces_existing(self, db: ThreadSafeConnection) -> None:
        conv = create_conversation(db, title="test")
        msg = create_message(
            db,
            conv["id"],
            "assistant",
            "hello",
            metadata={"rag_sources": [{"label": "old.pdf", "type": "source_chunk", "source_id": "s1"}]},
        )
        new_meta = {"rag_sources": [{"label": "new.pdf", "type": "source_chunk", "source_id": "s2"}]}
        update_message_metadata(db, msg["id"], new_meta)
        messages = list_messages(db, conv["id"])
        assert messages[0]["metadata"] == new_meta


class TestListMessagesMetadata:
    def test_deserializes_json_metadata(self, db: ThreadSafeConnection) -> None:
        conv = create_conversation(db, title="test")
        meta = {"rag_sources": [{"label": "a", "type": "source_chunk", "source_id": "x"}]}
        create_message(db, conv["id"], "assistant", "hi", metadata=meta)
        messages = list_messages(db, conv["id"])
        assert isinstance(messages[0]["metadata"], dict)
        assert messages[0]["metadata"]["rag_sources"][0]["label"] == "a"

    def test_invalid_json_metadata_returns_none(self, db: ThreadSafeConnection) -> None:
        conv = create_conversation(db, title="test")
        msg = create_message(db, conv["id"], "assistant", "hi")
        # Directly write invalid JSON to the metadata column
        db.execute("UPDATE messages SET metadata = ? WHERE id = ?", ("{bad json", msg["id"]))
        messages = list_messages(db, conv["id"])
        assert messages[0]["metadata"] is None

    def test_mixed_metadata_messages(self, db: ThreadSafeConnection) -> None:
        conv = create_conversation(db, title="test")
        create_message(db, conv["id"], "user", "question")
        meta = {"rag_sources": [{"label": "doc", "type": "source_chunk", "source_id": "s1"}]}
        create_message(db, conv["id"], "assistant", "answer", metadata=meta)
        create_message(db, conv["id"], "user", "followup")
        messages = list_messages(db, conv["id"])
        assert messages[0]["metadata"] is None
        assert messages[1]["metadata"] == meta
        assert messages[2]["metadata"] is None


class TestForkConversationMetadata:
    def test_fork_preserves_metadata(self, db: ThreadSafeConnection) -> None:
        conv = create_conversation(db, title="test")
        create_message(db, conv["id"], "user", "hi")
        meta = {"rag_sources": [{"label": "doc.pdf", "type": "source_chunk", "source_id": "s1"}]}
        create_message(db, conv["id"], "assistant", "hello", metadata=meta)
        forked = fork_conversation(db, conv["id"], up_to_position=1)
        forked_msgs = list_messages(db, forked["id"])
        assert len(forked_msgs) == 2
        assert forked_msgs[1]["metadata"] == meta

    def test_fork_preserves_null_metadata(self, db: ThreadSafeConnection) -> None:
        conv = create_conversation(db, title="test")
        create_message(db, conv["id"], "user", "hi")
        forked = fork_conversation(db, conv["id"], up_to_position=0)
        forked_msgs = list_messages(db, forked["id"])
        assert forked_msgs[0]["metadata"] is None


class TestCopyConversationMetadata:
    def test_copy_preserves_metadata(self, db: ThreadSafeConnection) -> None:
        conv = create_conversation(db, title="test")
        create_message(db, conv["id"], "user", "hi")
        meta = {"rag_sources": [{"label": "report.pdf", "type": "source_chunk", "source_id": "s3"}]}
        create_message(db, conv["id"], "assistant", "hello", metadata=meta)

        target_conn = sqlite3.connect(":memory:", check_same_thread=False)
        target_conn.row_factory = sqlite3.Row
        target_conn.execute("PRAGMA foreign_keys=ON")
        target_conn.executescript(_SCHEMA)
        try:
            target_conn.executescript(_FTS_SCHEMA)
            target_conn.executescript(_FTS_TRIGGERS)
        except sqlite3.OperationalError:
            pass
        target_conn.commit()
        target_db = ThreadSafeConnection(target_conn)

        copied = copy_conversation_to_db(db, target_db, conv["id"])
        assert copied is not None
        copied_msgs = list_messages(target_db, copied["id"])
        assert len(copied_msgs) == 2
        assert copied_msgs[0]["metadata"] is None
        assert copied_msgs[1]["metadata"] == meta

    def test_copy_preserves_null_metadata(self, db: ThreadSafeConnection) -> None:
        conv = create_conversation(db, title="test")
        create_message(db, conv["id"], "user", "hi")

        target_conn = sqlite3.connect(":memory:", check_same_thread=False)
        target_conn.row_factory = sqlite3.Row
        target_conn.execute("PRAGMA foreign_keys=ON")
        target_conn.executescript(_SCHEMA)
        try:
            target_conn.executescript(_FTS_SCHEMA)
            target_conn.executescript(_FTS_TRIGGERS)
        except sqlite3.OperationalError:
            pass
        target_conn.commit()
        target_db = ThreadSafeConnection(target_conn)

        copied = copy_conversation_to_db(db, target_db, conv["id"])
        assert copied is not None
        copied_msgs = list_messages(target_db, copied["id"])
        assert copied_msgs[0]["metadata"] is None


class TestFkRepairPreservesMetadata:
    def test_messages_repaired_includes_metadata_column(self, db: ThreadSafeConnection) -> None:
        """Regression: messages_repaired table must include the metadata column.

        The broken-FK repair path rebuilds the messages table via CREATE TABLE
        messages_repaired ... INSERT INTO messages_repaired SELECT * FROM messages.
        If messages_repaired is missing the metadata column, this fails with
        'table messages_repaired has N columns but N+1 values were supplied'.
        """
        conv = create_conversation(db, title="test")
        meta = {"rag_sources": [{"label": "doc.pdf", "type": "source_chunk", "source_id": "s1"}]}
        create_message(db, conv["id"], "assistant", "hello", metadata=meta)

        # Simulate the broken-FK state by rewriting the messages DDL to reference
        # a bogus table name, which triggers the repair path in _run_migrations.
        raw = db._conn
        ddl_row = raw.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='messages'").fetchone()
        original_ddl = ddl_row[0] if isinstance(ddl_row, tuple) else ddl_row["sql"]

        # Inject "_conversations_old" into the FK to trigger repair detection
        broken_ddl = original_ddl.replace('REFERENCES "conversations"', 'REFERENCES "_conversations_old"')
        if broken_ddl == original_ddl:
            broken_ddl = original_ddl.replace("REFERENCES conversations", "REFERENCES _conversations_old")

        # Only proceed if we successfully injected the broken FK
        if broken_ddl != original_ddl:
            raw.execute("PRAGMA foreign_keys=OFF")
            raw.execute("ALTER TABLE messages RENAME TO _messages_backup")
            raw.execute(broken_ddl)
            raw.execute("INSERT INTO messages SELECT * FROM _messages_backup")
            raw.execute("DROP TABLE _messages_backup")
            raw.execute("PRAGMA foreign_keys=ON")
            raw.commit()

            # Run migrations — should repair without error
            _run_migrations(raw, 384)

            # Verify metadata survived the repair
            messages = list_messages(db, conv["id"])
            assert len(messages) == 1
            assert messages[0]["metadata"] == meta

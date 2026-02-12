"""Tests for the storage service (CRUD operations)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from parlor.db import _FTS_SCHEMA, _FTS_TRIGGERS, _SCHEMA
from parlor.services.storage import (
    create_conversation,
    create_message,
    create_tool_call,
    delete_conversation,
    get_conversation,
    list_conversations,
    list_messages,
    list_tool_calls,
    update_conversation_title,
    update_tool_call,
)


@pytest.fixture()
def db() -> sqlite3.Connection:
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
    return conn


class TestConversations:
    def test_create_conversation_returns_dict(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Hello")
        assert conv["title"] == "Hello"
        assert "id" in conv
        assert "created_at" in conv
        assert "updated_at" in conv

    def test_get_conversation(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Test")
        fetched = get_conversation(db, conv["id"])
        assert fetched is not None
        assert fetched["id"] == conv["id"]
        assert fetched["title"] == "Test"

    def test_get_conversation_missing(self, db: sqlite3.Connection) -> None:
        result = get_conversation(db, "nonexistent-id")
        assert result is None

    def test_list_conversations_empty(self, db: sqlite3.Connection) -> None:
        result = list_conversations(db)
        assert result == []

    def test_list_conversations_returns_all(self, db: sqlite3.Connection) -> None:
        create_conversation(db, title="First")
        create_conversation(db, title="Second")
        result = list_conversations(db)
        assert len(result) == 2

    def test_list_conversations_includes_message_count(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Counted")
        create_message(db, conv["id"], "user", "hi")
        create_message(db, conv["id"], "assistant", "hello")
        result = list_conversations(db)
        assert result[0]["message_count"] == 2

    def test_list_conversations_ordered_by_updated_at(self, db: sqlite3.Connection) -> None:
        c1 = create_conversation(db, title="Older")
        create_conversation(db, title="Newer")
        create_message(db, c1["id"], "user", "bump")
        result = list_conversations(db)
        assert result[0]["id"] == c1["id"]

    def test_update_conversation_title(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Original")
        updated = update_conversation_title(db, conv["id"], "Renamed")
        assert updated is not None
        assert updated["title"] == "Renamed"

    def test_delete_conversation(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        conv = create_conversation(db, title="Doomed")
        result = delete_conversation(db, conv["id"], tmp_path)
        assert result is True
        assert get_conversation(db, conv["id"]) is None

    def test_delete_conversation_missing(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        result = delete_conversation(db, "no-such-id", tmp_path)
        assert result is False

    def test_delete_conversation_cascades_messages(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        conv = create_conversation(db, title="Cascade")
        create_message(db, conv["id"], "user", "hello")
        delete_conversation(db, conv["id"], tmp_path)
        msgs = db.execute("SELECT * FROM messages WHERE conversation_id = ?", (conv["id"],)).fetchall()
        assert len(msgs) == 0

    def test_delete_conversation_cascades_tool_calls(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        conv = create_conversation(db, title="TC Cascade")
        msg = create_message(db, conv["id"], "assistant", "calling tool")
        create_tool_call(db, msg["id"], "my_tool", "server1", {"arg": "val"})
        delete_conversation(db, conv["id"], tmp_path)
        tcs = db.execute("SELECT * FROM tool_calls WHERE message_id = ?", (msg["id"],)).fetchall()
        assert len(tcs) == 0


class TestMessages:
    def test_create_message(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Msgs")
        msg = create_message(db, conv["id"], "user", "hello")
        assert msg["role"] == "user"
        assert msg["content"] == "hello"
        assert msg["position"] == 0

    def test_create_message_increments_position(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Pos")
        m1 = create_message(db, conv["id"], "user", "first")
        m2 = create_message(db, conv["id"], "assistant", "second")
        assert m1["position"] == 0
        assert m2["position"] == 1

    def test_list_messages_ordered_by_position(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Order")
        create_message(db, conv["id"], "user", "a")
        create_message(db, conv["id"], "assistant", "b")
        create_message(db, conv["id"], "user", "c")
        msgs = list_messages(db, conv["id"])
        assert [m["content"] for m in msgs] == ["a", "b", "c"]

    def test_list_messages_empty(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Empty")
        msgs = list_messages(db, conv["id"])
        assert msgs == []

    def test_list_messages_includes_attachments_and_tool_calls(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Full")
        msg = create_message(db, conv["id"], "assistant", "response")
        create_tool_call(db, msg["id"], "tool", "srv", {"k": "v"})
        msgs = list_messages(db, conv["id"])
        assert "attachments" in msgs[0]
        assert "tool_calls" in msgs[0]
        assert len(msgs[0]["tool_calls"]) == 1

    def test_create_message_updates_conversation_updated_at(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Updated")
        original_updated = conv["updated_at"]
        create_message(db, conv["id"], "user", "bump")
        refreshed = get_conversation(db, conv["id"])
        assert refreshed is not None
        assert refreshed["updated_at"] >= original_updated


class TestToolCalls:
    def test_create_tool_call(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Tools")
        msg = create_message(db, conv["id"], "assistant", "calling")
        tc = create_tool_call(db, msg["id"], "search", "search_server", {"query": "test"})
        assert tc["tool_name"] == "search"
        assert tc["status"] == "pending"
        assert tc["input"] == {"query": "test"}
        assert tc["output"] is None

    def test_update_tool_call(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Tools")
        msg = create_message(db, conv["id"], "assistant", "calling")
        tc = create_tool_call(db, msg["id"], "search", "srv", {"q": "x"})
        update_tool_call(db, tc["id"], {"result": "found"}, "success")
        tcs = list_tool_calls(db, msg["id"])
        assert len(tcs) == 1
        assert tcs[0]["status"] == "success"
        assert tcs[0]["output"] == {"result": "found"}

    def test_list_tool_calls_empty(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="NoTools")
        msg = create_message(db, conv["id"], "user", "plain")
        tcs = list_tool_calls(db, msg["id"])
        assert tcs == []

    def test_create_tool_call_with_custom_id(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="CustomId")
        msg = create_message(db, conv["id"], "assistant", "calling")
        tc = create_tool_call(db, msg["id"], "tool", "srv", {}, tool_call_id="custom-123")
        assert tc["id"] == "custom-123"


class TestSearchConversations:
    def test_search_by_title(self, db: sqlite3.Connection) -> None:
        create_conversation(db, title="Python tutorial")
        create_conversation(db, title="Rust handbook")
        results = list_conversations(db, search="Python")
        assert len(results) == 1
        assert results[0]["title"] == "Python tutorial"

    def test_search_by_message_content(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Generic chat")
        create_message(db, conv["id"], "user", "Tell me about quantum computing")
        results = list_conversations(db, search="quantum")
        assert len(results) == 1
        assert results[0]["id"] == conv["id"]

    def test_search_no_results(self, db: sqlite3.Connection) -> None:
        create_conversation(db, title="Something")
        results = list_conversations(db, search="zzzznotfound")
        assert len(results) == 0

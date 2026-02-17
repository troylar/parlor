"""Unit tests for canvas storage CRUD operations."""

from __future__ import annotations

import sqlite3

import pytest

from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services.storage import (
    create_canvas,
    create_conversation,
    delete_canvas,
    get_canvas,
    get_canvas_for_conversation,
    update_canvas,
)


@pytest.fixture()
def db() -> ThreadSafeConnection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return ThreadSafeConnection(conn)


@pytest.fixture()
def conv_id(db: ThreadSafeConnection) -> str:
    conv = create_conversation(db, title="Test Conv")
    return conv["id"]


class TestCreateCanvas:
    def test_create_returns_canvas_dict(self, db: ThreadSafeConnection, conv_id: str) -> None:
        canvas = create_canvas(db, conv_id, title="My Canvas", content="# Hello")
        assert canvas["id"]
        assert canvas["conversation_id"] == conv_id
        assert canvas["title"] == "My Canvas"
        assert canvas["content"] == "# Hello"
        assert canvas["version"] == 1
        assert canvas["created_at"]
        assert canvas["updated_at"]

    def test_create_with_defaults(self, db: ThreadSafeConnection, conv_id: str) -> None:
        canvas = create_canvas(db, conv_id)
        assert canvas["title"] == "Untitled"
        assert canvas["content"] == ""
        assert canvas["language"] is None

    def test_create_with_language(self, db: ThreadSafeConnection, conv_id: str) -> None:
        canvas = create_canvas(db, conv_id, title="Code", content="x = 1", language="python")
        assert canvas["language"] == "python"

    def test_create_with_user_identity(self, db: ThreadSafeConnection, conv_id: str) -> None:
        canvas = create_canvas(
            db,
            conv_id,
            title="T",
            user_id="user-1",
            user_display_name="Troy",
        )
        assert canvas["user_id"] == "user-1"
        assert canvas["user_display_name"] == "Troy"

    def test_create_persists_to_db(self, db: ThreadSafeConnection, conv_id: str) -> None:
        canvas = create_canvas(db, conv_id, title="Persisted", content="data")
        fetched = get_canvas(db, canvas["id"])
        assert fetched is not None
        assert fetched["title"] == "Persisted"
        assert fetched["content"] == "data"


class TestGetCanvas:
    def test_get_existing(self, db: ThreadSafeConnection, conv_id: str) -> None:
        canvas = create_canvas(db, conv_id, title="Find Me")
        result = get_canvas(db, canvas["id"])
        assert result is not None
        assert result["title"] == "Find Me"

    def test_get_nonexistent(self, db: ThreadSafeConnection) -> None:
        result = get_canvas(db, "nonexistent-id")
        assert result is None


class TestGetCanvasForConversation:
    def test_returns_canvas_for_conversation(self, db: ThreadSafeConnection, conv_id: str) -> None:
        create_canvas(db, conv_id, title="Conv Canvas")
        result = get_canvas_for_conversation(db, conv_id)
        assert result is not None
        assert result["title"] == "Conv Canvas"

    def test_returns_none_when_no_canvas(self, db: ThreadSafeConnection, conv_id: str) -> None:
        result = get_canvas_for_conversation(db, conv_id)
        assert result is None

    def test_returns_most_recent(self, db: ThreadSafeConnection, conv_id: str) -> None:
        create_canvas(db, conv_id, title="First")
        first = get_canvas_for_conversation(db, conv_id)
        assert first is not None
        assert first["title"] == "First"


class TestUpdateCanvas:
    def test_update_content(self, db: ThreadSafeConnection, conv_id: str) -> None:
        canvas = create_canvas(db, conv_id, title="T", content="old")
        updated = update_canvas(db, canvas["id"], content="new")
        assert updated is not None
        assert updated["content"] == "new"
        assert updated["title"] == "T"

    def test_update_title(self, db: ThreadSafeConnection, conv_id: str) -> None:
        canvas = create_canvas(db, conv_id, title="Old Title", content="C")
        updated = update_canvas(db, canvas["id"], title="New Title")
        assert updated is not None
        assert updated["title"] == "New Title"
        assert updated["content"] == "C"

    def test_update_both(self, db: ThreadSafeConnection, conv_id: str) -> None:
        canvas = create_canvas(db, conv_id, title="T", content="C")
        updated = update_canvas(db, canvas["id"], content="new C", title="new T")
        assert updated is not None
        assert updated["content"] == "new C"
        assert updated["title"] == "new T"

    def test_update_increments_version(self, db: ThreadSafeConnection, conv_id: str) -> None:
        canvas = create_canvas(db, conv_id, title="T")
        assert canvas["version"] == 1
        v2 = update_canvas(db, canvas["id"], content="v2")
        assert v2 is not None
        assert v2["version"] == 2
        v3 = update_canvas(db, canvas["id"], content="v3")
        assert v3 is not None
        assert v3["version"] == 3

    def test_update_changes_updated_at(self, db: ThreadSafeConnection, conv_id: str) -> None:
        canvas = create_canvas(db, conv_id, title="T")
        updated = update_canvas(db, canvas["id"], content="changed")
        assert updated is not None
        assert updated["updated_at"] >= canvas["updated_at"]

    def test_update_nonexistent_returns_none(self, db: ThreadSafeConnection) -> None:
        result = update_canvas(db, "nonexistent-id", content="data")
        assert result is None

    def test_update_none_content_preserves_original(self, db: ThreadSafeConnection, conv_id: str) -> None:
        canvas = create_canvas(db, conv_id, title="T", content="keep this")
        updated = update_canvas(db, canvas["id"], title="New Title")
        assert updated is not None
        assert updated["content"] == "keep this"

    def test_atomic_version_increment(self, db: ThreadSafeConnection, conv_id: str) -> None:
        """Version uses SQL version + 1 (atomic), not Python read-modify-write."""
        canvas = create_canvas(db, conv_id, title="T")
        update_canvas(db, canvas["id"], content="a")
        update_canvas(db, canvas["id"], content="b")
        update_canvas(db, canvas["id"], content="c")
        final = get_canvas(db, canvas["id"])
        assert final is not None
        assert final["version"] == 4

    def test_update_canvas_neither_content_nor_title(self, db: ThreadSafeConnection, conv_id: str) -> None:
        canvas = create_canvas(db, conv_id, title="Original Title", content="Original Content")
        original_version = canvas["version"]
        original_updated_at = canvas["updated_at"]
        updated = update_canvas(db, canvas["id"], content=None, title=None)
        assert updated is not None
        assert updated["version"] == original_version
        assert updated["updated_at"] == original_updated_at
        assert updated["content"] == "Original Content"
        assert updated["title"] == "Original Title"

    def test_update_canvas_title_only(self, db: ThreadSafeConnection, conv_id: str) -> None:
        canvas = create_canvas(db, conv_id, title="Old Title", content="Keep This Content")
        updated = update_canvas(db, canvas["id"], title="New Title")
        assert updated is not None
        assert updated["title"] == "New Title"
        assert updated["content"] == "Keep This Content"
        assert updated["version"] == 2

    def test_get_canvas_for_conversation_multiple_canvases(self, db: ThreadSafeConnection, conv_id: str) -> None:
        create_canvas(db, conv_id, title="First Canvas")
        import time

        time.sleep(0.01)
        second = create_canvas(db, conv_id, title="Second Canvas")
        update_canvas(db, second["id"], content="Updated second")
        result = get_canvas_for_conversation(db, conv_id)
        assert result is not None
        assert result["id"] == second["id"]
        assert result["title"] == "Second Canvas"


class TestDeleteCanvas:
    def test_delete_existing(self, db: ThreadSafeConnection, conv_id: str) -> None:
        canvas = create_canvas(db, conv_id, title="Delete Me")
        assert delete_canvas(db, canvas["id"]) is True
        assert get_canvas(db, canvas["id"]) is None

    def test_delete_nonexistent(self, db: ThreadSafeConnection) -> None:
        assert delete_canvas(db, "nonexistent-id") is False

    def test_fk_cascade_on_conversation_delete(self, db: ThreadSafeConnection, conv_id: str) -> None:
        """Deleting the conversation should cascade-delete its canvas."""
        canvas = create_canvas(db, conv_id, title="Cascade")
        db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        db.commit()
        assert get_canvas(db, canvas["id"]) is None

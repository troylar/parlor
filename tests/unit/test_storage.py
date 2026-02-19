"""Tests for the storage service (CRUD operations)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from anteroom.db import _FTS_SCHEMA, _FTS_TRIGGERS, _SCHEMA, ThreadSafeConnection
from anteroom.services.storage import (
    add_tag_to_conversation,
    copy_conversation_to_db,
    create_conversation,
    create_folder,
    create_message,
    create_tag,
    create_tool_call,
    delete_conversation,
    delete_folder,
    delete_message,
    delete_messages_after_position,
    delete_tag,
    fork_conversation,
    get_conversation,
    get_conversation_tags,
    list_conversations,
    list_folders,
    list_messages,
    list_tags,
    list_tool_calls,
    move_conversation_to_folder,
    register_user,
    remove_tag_from_conversation,
    replace_document_content,
    save_attachment,
    update_conversation_title,
    update_conversation_type,
    update_folder,
    update_message_content,
    update_tag,
    update_tool_call,
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

    def test_create_conversation_default_type_is_chat(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Chat")
        assert conv["type"] == "chat"

    def test_create_conversation_with_note_type(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="My Notes", conversation_type="note")
        assert conv["type"] == "note"
        fetched = get_conversation(db, conv["id"])
        assert fetched is not None
        assert fetched["type"] == "note"

    def test_create_conversation_with_document_type(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="My Doc", conversation_type="document")
        assert conv["type"] == "document"

    def test_create_conversation_invalid_type_raises(self, db: sqlite3.Connection) -> None:
        with pytest.raises(ValueError, match="Invalid conversation type"):
            create_conversation(db, title="Bad", conversation_type="invalid")

    def test_list_conversations_returns_type(self, db: sqlite3.Connection) -> None:
        create_conversation(db, title="Chat", conversation_type="chat")
        create_conversation(db, title="Note", conversation_type="note")
        result = list_conversations(db)
        types = {c["type"] for c in result}
        assert types == {"chat", "note"}

    def test_list_conversations_filter_by_type(self, db: sqlite3.Connection) -> None:
        create_conversation(db, title="Chat 1", conversation_type="chat")
        create_conversation(db, title="Note 1", conversation_type="note")
        create_conversation(db, title="Doc 1", conversation_type="document")
        notes = list_conversations(db, conversation_type="note")
        assert len(notes) == 1
        assert notes[0]["type"] == "note"

    def test_update_conversation_type(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Will Change")
        updated = update_conversation_type(db, conv["id"], "note")
        assert updated is not None
        assert updated["type"] == "note"

    def test_update_conversation_type_invalid_raises(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Bad Update")
        with pytest.raises(ValueError, match="Invalid conversation type"):
            update_conversation_type(db, conv["id"], "invalid")

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


class TestForkConversation:
    def test_fork_copies_messages_up_to_position(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Original")
        create_message(db, conv["id"], "user", "msg0")
        create_message(db, conv["id"], "assistant", "msg1")
        create_message(db, conv["id"], "user", "msg2")
        create_message(db, conv["id"], "assistant", "msg3")

        forked = fork_conversation(db, conv["id"], 1)
        assert forked["title"] == "Original (fork)"
        assert forked["id"] != conv["id"]

        msgs = list_messages(db, forked["id"])
        assert len(msgs) == 2
        assert msgs[0]["content"] == "msg0"
        assert msgs[1]["content"] == "msg1"

    def test_fork_preserves_positions(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Positions")
        create_message(db, conv["id"], "user", "a")
        create_message(db, conv["id"], "assistant", "b")
        create_message(db, conv["id"], "user", "c")

        forked = fork_conversation(db, conv["id"], 2)
        msgs = list_messages(db, forked["id"])
        assert [m["position"] for m in msgs] == [0, 1, 2]

    def test_fork_copies_tool_calls(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Tools")
        create_message(db, conv["id"], "user", "hi")
        msg = create_message(db, conv["id"], "assistant", "calling")
        create_tool_call(db, msg["id"], "tool1", "srv", {"k": "v"})

        forked = fork_conversation(db, conv["id"], 1)
        msgs = list_messages(db, forked["id"])
        assert len(msgs[1]["tool_calls"]) == 1
        assert msgs[1]["tool_calls"][0]["tool_name"] == "tool1"

    def test_fork_new_message_ids(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="IDs")
        m0 = create_message(db, conv["id"], "user", "hi")

        forked = fork_conversation(db, conv["id"], 0)
        msgs = list_messages(db, forked["id"])
        assert msgs[0]["id"] != m0["id"]

    def test_fork_nonexistent_conversation(self, db: sqlite3.Connection) -> None:
        with pytest.raises(ValueError, match="Conversation not found"):
            fork_conversation(db, "no-such-id", 0)

    def test_fork_inherits_project_and_model(self, db: sqlite3.Connection) -> None:
        from anteroom.services.storage import create_project, update_conversation_model

        project = create_project(db, "TestProj")
        conv = create_conversation(db, title="WithProject", project_id=project["id"])
        update_conversation_model(db, conv["id"], "gpt-4o")
        create_message(db, conv["id"], "user", "hi")

        forked = fork_conversation(db, conv["id"], 0)
        assert forked["project_id"] == project["id"]
        assert forked["model"] == "gpt-4o"


class TestUpdateMessageContent:
    def test_update_message_content(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Edit")
        msg = create_message(db, conv["id"], "user", "original")
        updated = update_message_content(db, conv["id"], msg["id"], "edited")
        assert updated is not None
        assert updated["content"] == "edited"

    def test_update_message_wrong_conversation(self, db: sqlite3.Connection) -> None:
        conv1 = create_conversation(db, title="C1")
        conv2 = create_conversation(db, title="C2")
        msg = create_message(db, conv1["id"], "user", "hello")
        result = update_message_content(db, conv2["id"], msg["id"], "edited")
        assert result is None

    def test_update_message_nonexistent(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Edit")
        result = update_message_content(db, conv["id"], "no-such-id", "edited")
        assert result is None


class TestDeleteMessagesAfterPosition:
    def test_delete_messages_after_position(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Delete")
        create_message(db, conv["id"], "user", "keep0")
        create_message(db, conv["id"], "assistant", "keep1")
        create_message(db, conv["id"], "user", "remove2")
        create_message(db, conv["id"], "assistant", "remove3")

        count = delete_messages_after_position(db, conv["id"], 1)
        assert count == 2
        msgs = list_messages(db, conv["id"])
        assert len(msgs) == 2
        assert [m["content"] for m in msgs] == ["keep0", "keep1"]

    def test_delete_messages_after_last_position(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="NoDelete")
        create_message(db, conv["id"], "user", "only")
        count = delete_messages_after_position(db, conv["id"], 0)
        assert count == 0
        msgs = list_messages(db, conv["id"])
        assert len(msgs) == 1

    def test_delete_cascades_tool_calls(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Cascade")
        create_message(db, conv["id"], "user", "hi")
        msg = create_message(db, conv["id"], "assistant", "calling")
        create_tool_call(db, msg["id"], "tool1", "srv", {"x": 1})

        delete_messages_after_position(db, conv["id"], 0)
        tcs = list_tool_calls(db, msg["id"])
        assert len(tcs) == 0


class TestFolders:
    def test_create_folder(self, db: sqlite3.Connection) -> None:
        folder = create_folder(db, "Work")
        assert folder["name"] == "Work"
        assert folder["id"]
        assert folder["position"] == 0
        assert folder["collapsed"] is False
        assert folder["project_id"] is None
        assert folder["parent_id"] is None

    def test_create_subfolder(self, db: sqlite3.Connection) -> None:
        parent = create_folder(db, "Parent")
        child = create_folder(db, "Child", parent_id=parent["id"])
        assert child["parent_id"] == parent["id"]
        assert child["position"] == 0

    def test_nested_subfolders(self, db: sqlite3.Connection) -> None:
        root = create_folder(db, "Root")
        mid = create_folder(db, "Mid", parent_id=root["id"])
        leaf = create_folder(db, "Leaf", parent_id=mid["id"])
        assert leaf["parent_id"] == mid["id"]
        assert mid["parent_id"] == root["id"]

    def test_delete_folder_cascades_children(self, db: sqlite3.Connection) -> None:
        root = create_folder(db, "Root")
        child = create_folder(db, "Child", parent_id=root["id"])
        grandchild = create_folder(db, "Grandchild", parent_id=child["id"])
        conv = create_conversation(db, "In grandchild")
        move_conversation_to_folder(db, conv["id"], grandchild["id"])
        delete_folder(db, root["id"])
        assert list_folders(db) == []
        updated_conv = get_conversation(db, conv["id"])
        assert updated_conv["folder_id"] is None

    def test_create_folder_with_project(self, db: sqlite3.Connection) -> None:
        from anteroom.services.storage import create_project

        proj = create_project(db, "Test Project")
        folder = create_folder(db, "Research", project_id=proj["id"])
        assert folder["project_id"] == proj["id"]

    def test_create_folders_auto_increment_position(self, db: sqlite3.Connection) -> None:
        f1 = create_folder(db, "First")
        f2 = create_folder(db, "Second")
        f3 = create_folder(db, "Third")
        assert f1["position"] == 0
        assert f2["position"] == 1
        assert f3["position"] == 2

    def test_list_folders_empty(self, db: sqlite3.Connection) -> None:
        assert list_folders(db) == []

    def test_list_folders_ordered_by_position(self, db: sqlite3.Connection) -> None:
        create_folder(db, "A")
        create_folder(db, "B")
        create_folder(db, "C")
        folders = list_folders(db)
        assert len(folders) == 3
        assert [f["name"] for f in folders] == ["A", "B", "C"]

    def test_list_folders_filtered_by_project(self, db: sqlite3.Connection) -> None:
        from anteroom.services.storage import create_project

        proj = create_project(db, "P1")
        create_folder(db, "In project", project_id=proj["id"])
        create_folder(db, "No project")
        assert len(list_folders(db, project_id=proj["id"])) == 1
        assert len(list_folders(db)) == 2

    def test_update_folder_name(self, db: sqlite3.Connection) -> None:
        folder = create_folder(db, "Old Name")
        updated = update_folder(db, folder["id"], name="New Name")
        assert updated is not None
        assert updated["name"] == "New Name"

    def test_update_folder_collapsed(self, db: sqlite3.Connection) -> None:
        folder = create_folder(db, "Collapsible")
        updated = update_folder(db, folder["id"], collapsed=True)
        assert updated is not None
        assert updated["collapsed"] is True

    def test_update_folder_position(self, db: sqlite3.Connection) -> None:
        folder = create_folder(db, "Movable")
        updated = update_folder(db, folder["id"], position=5)
        assert updated is not None
        assert updated["position"] == 5

    def test_update_folder_not_found(self, db: sqlite3.Connection) -> None:
        assert update_folder(db, "nonexistent-id", name="X") is None

    def test_delete_folder(self, db: sqlite3.Connection) -> None:
        folder = create_folder(db, "Doomed")
        assert delete_folder(db, folder["id"]) is True
        assert list_folders(db) == []

    def test_delete_folder_not_found(self, db: sqlite3.Connection) -> None:
        assert delete_folder(db, "nonexistent-id") is False

    def test_delete_folder_unlinks_conversations(self, db: sqlite3.Connection) -> None:
        folder = create_folder(db, "Has Convos")
        conv = create_conversation(db)
        move_conversation_to_folder(db, conv["id"], folder["id"])
        c = get_conversation(db, conv["id"])
        assert c is not None
        assert c["folder_id"] == folder["id"]

        delete_folder(db, folder["id"])
        c = get_conversation(db, conv["id"])
        assert c is not None
        assert c["folder_id"] is None

    def test_move_conversation_to_folder(self, db: sqlite3.Connection) -> None:
        folder = create_folder(db, "Target")
        conv = create_conversation(db)
        move_conversation_to_folder(db, conv["id"], folder["id"])
        c = get_conversation(db, conv["id"])
        assert c is not None
        assert c["folder_id"] == folder["id"]

    def test_move_conversation_to_no_folder(self, db: sqlite3.Connection) -> None:
        folder = create_folder(db, "Source")
        conv = create_conversation(db)
        move_conversation_to_folder(db, conv["id"], folder["id"])
        move_conversation_to_folder(db, conv["id"], None)
        c = get_conversation(db, conv["id"])
        assert c is not None
        assert c["folder_id"] is None

    def test_list_conversations_includes_folder_id(self, db: sqlite3.Connection) -> None:
        folder = create_folder(db, "Folder")
        conv = create_conversation(db)
        move_conversation_to_folder(db, conv["id"], folder["id"])
        convs = list_conversations(db)
        assert len(convs) == 1
        assert convs[0]["folder_id"] == folder["id"]


class TestTags:
    def test_create_tag(self, db: sqlite3.Connection) -> None:
        tag = create_tag(db, "important", "#ff0000")
        assert tag["name"] == "important"
        assert tag["color"] == "#ff0000"
        assert tag["id"]

    def test_create_tag_default_color(self, db: sqlite3.Connection) -> None:
        tag = create_tag(db, "general")
        assert tag["color"] == "#3b82f6"

    def test_list_tags(self, db: sqlite3.Connection) -> None:
        create_tag(db, "b-tag")
        create_tag(db, "a-tag")
        tags = list_tags(db)
        assert len(tags) == 2
        assert tags[0]["name"] == "a-tag"
        assert tags[1]["name"] == "b-tag"

    def test_update_tag(self, db: sqlite3.Connection) -> None:
        tag = create_tag(db, "old", "#000000")
        updated = update_tag(db, tag["id"], name="new", color="#ffffff")
        assert updated is not None
        assert updated["name"] == "new"
        assert updated["color"] == "#ffffff"

    def test_update_tag_not_found(self, db: sqlite3.Connection) -> None:
        assert update_tag(db, "nonexistent") is None

    def test_delete_tag(self, db: sqlite3.Connection) -> None:
        tag = create_tag(db, "doomed")
        assert delete_tag(db, tag["id"]) is True
        assert list_tags(db) == []

    def test_delete_tag_not_found(self, db: sqlite3.Connection) -> None:
        assert delete_tag(db, "nonexistent") is False

    def test_add_tag_to_conversation(self, db: sqlite3.Connection) -> None:
        tag = create_tag(db, "important")
        conv = create_conversation(db)
        add_tag_to_conversation(db, conv["id"], tag["id"])
        tags = get_conversation_tags(db, conv["id"])
        assert len(tags) == 1
        assert tags[0]["name"] == "important"

    def test_remove_tag_from_conversation(self, db: sqlite3.Connection) -> None:
        tag = create_tag(db, "temp")
        conv = create_conversation(db)
        add_tag_to_conversation(db, conv["id"], tag["id"])
        remove_tag_from_conversation(db, conv["id"], tag["id"])
        assert get_conversation_tags(db, conv["id"]) == []

    def test_conversation_multiple_tags(self, db: sqlite3.Connection) -> None:
        t1 = create_tag(db, "alpha")
        t2 = create_tag(db, "beta")
        conv = create_conversation(db)
        add_tag_to_conversation(db, conv["id"], t1["id"])
        add_tag_to_conversation(db, conv["id"], t2["id"])
        tags = get_conversation_tags(db, conv["id"])
        assert len(tags) == 2

    def test_delete_tag_removes_from_conversations(self, db: sqlite3.Connection) -> None:
        tag = create_tag(db, "doomed")
        conv = create_conversation(db)
        add_tag_to_conversation(db, conv["id"], tag["id"])
        delete_tag(db, tag["id"])
        assert get_conversation_tags(db, conv["id"]) == []

    def test_list_conversations_includes_tags(self, db: sqlite3.Connection) -> None:
        tag = create_tag(db, "flagged", "#ff0000")
        conv = create_conversation(db)
        add_tag_to_conversation(db, conv["id"], tag["id"])
        convs = list_conversations(db)
        assert len(convs) == 1
        assert len(convs[0]["tags"]) == 1
        assert convs[0]["tags"][0]["name"] == "flagged"


class TestCopyConversationToDb:
    @pytest.fixture()
    def target_db(self) -> ThreadSafeConnection:
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

    def test_copy_conversation(self, db, target_db) -> None:
        conv = create_conversation(db, title="Test Copy")
        create_message(db, conv["id"], "user", "Hello")
        create_message(db, conv["id"], "assistant", "Hi there")

        copied = copy_conversation_to_db(db, target_db, conv["id"])
        assert copied is not None
        assert copied["title"] == "Test Copy"
        assert copied["id"] != conv["id"]

        msgs = list_messages(target_db, copied["id"])
        assert len(msgs) == 2
        assert msgs[0]["content"] == "Hello"
        assert msgs[1]["content"] == "Hi there"

    def test_copy_preserves_positions(self, db, target_db) -> None:
        conv = create_conversation(db, title="Positions")
        create_message(db, conv["id"], "user", "First")
        create_message(db, conv["id"], "assistant", "Second")
        create_message(db, conv["id"], "user", "Third")

        copied = copy_conversation_to_db(db, target_db, conv["id"])
        assert copied is not None
        msgs = list_messages(target_db, copied["id"])
        assert [m["position"] for m in msgs] == [0, 1, 2]

    def test_copy_copies_tool_calls(self, db, target_db) -> None:
        conv = create_conversation(db)
        msg = create_message(db, conv["id"], "assistant", "tool usage")
        create_tool_call(db, msg["id"], "search", "mcp-server", {"query": "test"})

        copied = copy_conversation_to_db(db, target_db, conv["id"])
        assert copied is not None
        msgs = list_messages(target_db, copied["id"])
        assert len(msgs) == 1
        assert len(msgs[0]["tool_calls"]) == 1
        assert msgs[0]["tool_calls"][0]["tool_name"] == "search"

    def test_copy_nonexistent_conversation(self, db, target_db) -> None:
        result = copy_conversation_to_db(db, target_db, "nonexistent-id")
        assert result is None

    def test_copy_does_not_modify_source(self, db, target_db) -> None:
        conv = create_conversation(db, title="Original")
        create_message(db, conv["id"], "user", "Hello")

        copy_conversation_to_db(db, target_db, conv["id"])

        original = get_conversation(db, conv["id"])
        assert original is not None
        assert original["title"] == "Original"
        source_msgs = list_messages(db, conv["id"])
        assert len(source_msgs) == 1


class TestDatabaseManager:
    def test_add_and_get(self, tmp_path) -> None:
        from anteroom.db import DatabaseManager

        mgr = DatabaseManager()
        mgr.add("personal", tmp_path / "personal.db")
        mgr.add("shared", tmp_path / "shared.db")

        assert mgr.get("personal") is not None
        assert mgr.get("shared") is not None
        assert mgr.personal is not None

    def test_get_default_returns_personal(self, tmp_path) -> None:
        from anteroom.db import DatabaseManager

        mgr = DatabaseManager()
        mgr.add("personal", tmp_path / "personal.db")
        assert mgr.get() is mgr.personal

    def test_get_unknown_raises(self, tmp_path) -> None:
        from anteroom.db import DatabaseManager

        mgr = DatabaseManager()
        mgr.add("personal", tmp_path / "personal.db")
        with pytest.raises(KeyError):
            mgr.get("unknown")

    def test_list_databases(self, tmp_path) -> None:
        from anteroom.db import DatabaseManager

        mgr = DatabaseManager()
        mgr.add("personal", tmp_path / "personal.db")
        mgr.add("shared", tmp_path / "shared.db")

        dbs = mgr.list_databases()
        assert len(dbs) == 2
        names = [d["name"] for d in dbs]
        assert "personal" in names
        assert "shared" in names

    def test_remove(self, tmp_path) -> None:
        from anteroom.db import DatabaseManager

        mgr = DatabaseManager()
        mgr.add("personal", tmp_path / "personal.db")
        mgr.add("shared", tmp_path / "shared.db")
        mgr.remove("shared")

        dbs = mgr.list_databases()
        assert len(dbs) == 1
        assert dbs[0]["name"] == "personal"
        with pytest.raises(KeyError):
            mgr.get("shared")

    def test_close_all(self, tmp_path) -> None:
        from anteroom.db import DatabaseManager

        mgr = DatabaseManager()
        mgr.add("personal", tmp_path / "personal.db")
        mgr.add("shared", tmp_path / "shared.db")
        mgr.close_all()
        assert mgr.list_databases() == []


class TestUserIdentityInStorage:
    def test_create_conversation_with_identity(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Hello", user_id="u1", user_display_name="Alice")
        assert conv["title"] == "Hello"
        row = db.execute_fetchone("SELECT user_id, user_display_name FROM conversations WHERE id = ?", (conv["id"],))
        assert row is not None
        assert row["user_id"] == "u1"
        assert row["user_display_name"] == "Alice"

    def test_create_conversation_without_identity(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="No ID")
        row = db.execute_fetchone("SELECT user_id, user_display_name FROM conversations WHERE id = ?", (conv["id"],))
        assert row is not None
        assert row["user_id"] is None
        assert row["user_display_name"] is None

    def test_create_message_with_identity(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Msgs")
        msg = create_message(db, conv["id"], "user", "hi", user_id="u1", user_display_name="Alice")
        assert msg["content"] == "hi"
        row = db.execute_fetchone("SELECT user_id, user_display_name FROM messages WHERE id = ?", (msg["id"],))
        assert row is not None
        assert row["user_id"] == "u1"
        assert row["user_display_name"] == "Alice"

    def test_create_message_without_identity(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Msgs")
        msg = create_message(db, conv["id"], "user", "hi")
        row = db.execute_fetchone("SELECT user_id, user_display_name FROM messages WHERE id = ?", (msg["id"],))
        assert row is not None
        assert row["user_id"] is None
        assert row["user_display_name"] is None

    def test_create_folder_with_identity(self, db: sqlite3.Connection) -> None:
        folder = create_folder(db, "Work", user_id="u1", user_display_name="Alice")
        row = db.execute_fetchone("SELECT user_id, user_display_name FROM folders WHERE id = ?", (folder["id"],))
        assert row is not None
        assert row["user_id"] == "u1"
        assert row["user_display_name"] == "Alice"

    def test_create_tag_with_identity(self, db: sqlite3.Connection) -> None:
        tag = create_tag(db, "important", user_id="u1", user_display_name="Alice")
        row = db.execute_fetchone("SELECT user_id, user_display_name FROM tags WHERE id = ?", (tag["id"],))
        assert row is not None
        assert row["user_id"] == "u1"
        assert row["user_display_name"] == "Alice"

    def test_create_project_with_identity(self, db: sqlite3.Connection) -> None:
        from anteroom.services.storage import create_project

        proj = create_project(db, "Test", user_id="u1", user_display_name="Alice")
        row = db.execute_fetchone("SELECT user_id, user_display_name FROM projects WHERE id = ?", (proj["id"],))
        assert row is not None
        assert row["user_id"] == "u1"
        assert row["user_display_name"] == "Alice"

    def test_fork_preserves_user_identity(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Original", user_id="u1", user_display_name="Alice")
        create_message(db, conv["id"], "user", "msg0", user_id="u1", user_display_name="Alice")
        create_message(db, conv["id"], "assistant", "msg1")

        forked = fork_conversation(db, conv["id"], 1)
        forked_msgs = list_messages(db, forked["id"])
        row0 = db.execute_fetchone(
            "SELECT user_id, user_display_name FROM messages WHERE id = ?", (forked_msgs[0]["id"],)
        )
        assert row0["user_id"] == "u1"
        assert row0["user_display_name"] == "Alice"

    def test_copy_conversation_preserves_user_identity(self, db) -> None:
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

        conv = create_conversation(db, title="Copy", user_id="u1", user_display_name="Alice")
        create_message(db, conv["id"], "user", "hi", user_id="u1", user_display_name="Alice")

        copied = copy_conversation_to_db(db, target_db, conv["id"])
        assert copied is not None
        msgs = list_messages(target_db, copied["id"])
        row = target_db.execute_fetchone(
            "SELECT user_id, user_display_name FROM messages WHERE id = ?", (msgs[0]["id"],)
        )
        assert row["user_id"] == "u1"
        assert row["user_display_name"] == "Alice"


class TestConversationTypeSQLInjection:
    """Security: verify type field cannot be used for SQL injection."""

    def test_type_filter_with_sql_injection_ignores_filter(self, db: sqlite3.Connection) -> None:
        """Invalid type is not in VALID_CONVERSATION_TYPES, so filter is ignored (returns all).

        This is safe because the type is never interpolated into SQL — it's checked
        against an allowlist first, and only valid types reach the parameterized query.
        """
        create_conversation(db, title="Safe", conversation_type="chat")
        result = list_conversations(db, conversation_type="chat' OR '1'='1")
        # Invalid type is silently ignored: returns unfiltered results
        assert len(result) == 1
        assert result[0]["type"] == "chat"

    def test_type_filter_with_union_injection_ignores_filter(self, db: sqlite3.Connection) -> None:
        create_conversation(db, title="Safe", conversation_type="chat")
        result = list_conversations(db, conversation_type="chat' UNION SELECT * FROM messages--")
        assert len(result) == 1  # ignored, returns all

    def test_type_filter_with_semicolon_injection_ignores_filter(self, db: sqlite3.Connection) -> None:
        create_conversation(db, title="Safe", conversation_type="chat")
        result = list_conversations(db, conversation_type="chat; DROP TABLE conversations;")
        assert len(result) == 1  # ignored, returns all
        # Verify table still exists
        rows = db.execute_fetchall("SELECT COUNT(*) as cnt FROM conversations")
        assert rows[0]["cnt"] == 1

    def test_create_conversation_type_injection(self, db: sqlite3.Connection) -> None:
        with pytest.raises(ValueError, match="Invalid conversation type"):
            create_conversation(db, title="Bad", conversation_type="chat'; DROP TABLE conversations;--")

    def test_update_type_injection(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Target")
        with pytest.raises(ValueError, match="Invalid conversation type"):
            update_conversation_type(db, conv["id"], "note'; DROP TABLE messages;--")

    def test_valid_types_are_exactly_three(self, db: sqlite3.Connection) -> None:
        from anteroom.services.storage import VALID_CONVERSATION_TYPES

        assert VALID_CONVERSATION_TYPES == {"chat", "note", "document"}

    def test_invalid_type_filter_ignored_returns_all(self, db: sqlite3.Connection) -> None:
        """Invalid type filter is silently ignored, returning unfiltered results."""
        create_conversation(db, title="Chat 1", conversation_type="chat")
        result = list_conversations(db, conversation_type="not_valid")
        assert len(result) == 1  # filter ignored, returns all

    def test_valid_type_filter_works_correctly(self, db: sqlite3.Connection) -> None:
        create_conversation(db, title="Chat", conversation_type="chat")
        create_conversation(db, title="Note", conversation_type="note")
        create_conversation(db, title="Doc", conversation_type="document")
        for t in ("chat", "note", "document"):
            result = list_conversations(db, conversation_type=t)
            assert len(result) == 1
            assert result[0]["type"] == t


class TestFTSSearchSanitization:
    """Security: verify FTS query sanitization prevents injection."""

    def test_search_with_fts_special_chars(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Test conversation")
        create_message(db, conv["id"], "user", "Hello world")
        results = list_conversations(db, search='hello" OR "')
        assert isinstance(results, list)

    def test_search_with_asterisk(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Test")
        create_message(db, conv["id"], "user", "Hello world")
        results = list_conversations(db, search="hello*")
        assert isinstance(results, list)

    def test_search_with_parentheses(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Test")
        create_message(db, conv["id"], "user", "Hello world")
        results = list_conversations(db, search="hello) OR (1=1")
        assert isinstance(results, list)

    def test_search_with_near_operator(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Test")
        create_message(db, conv["id"], "user", "Hello beautiful world")
        results = list_conversations(db, search="NEAR(hello world)")
        assert isinstance(results, list)


class TestRegisterUser:
    def test_register_new_user(self, db: sqlite3.Connection) -> None:
        register_user(db, "u1", "Alice", "pub-key-pem")
        row = db.execute_fetchone("SELECT * FROM users WHERE user_id = ?", ("u1",))
        assert row is not None
        assert row["display_name"] == "Alice"
        assert row["public_key"] == "pub-key-pem"
        assert row["created_at"]
        assert row["updated_at"]

    def test_register_user_upsert_updates(self, db: sqlite3.Connection) -> None:
        register_user(db, "u1", "Alice", "pub-key-1")
        register_user(db, "u1", "Alice Updated", "pub-key-2")
        row = db.execute_fetchone("SELECT * FROM users WHERE user_id = ?", ("u1",))
        assert row is not None
        assert row["display_name"] == "Alice Updated"
        assert row["public_key"] == "pub-key-2"

    def test_register_multiple_users(self, db: sqlite3.Connection) -> None:
        register_user(db, "u1", "Alice", "pub1")
        register_user(db, "u2", "Bob", "pub2")
        rows = db.execute_fetchall("SELECT * FROM users ORDER BY user_id")
        assert len(rows) == 2
        assert rows[0]["user_id"] == "u1"
        assert rows[1]["user_id"] == "u2"

    def test_register_user_preserves_created_at(self, db: sqlite3.Connection) -> None:
        register_user(db, "u1", "Alice", "pub1")
        row1 = db.execute_fetchone("SELECT created_at FROM users WHERE user_id = ?", ("u1",))
        created_at = row1["created_at"]
        register_user(db, "u1", "Alice v2", "pub2")
        row2 = db.execute_fetchone("SELECT created_at FROM users WHERE user_id = ?", ("u1",))
        assert row2["created_at"] == created_at


class TestDeleteMessage:
    def test_delete_message_removes_it(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Del")
        m1 = create_message(db, conv["id"], "user", "keep")
        m2 = create_message(db, conv["id"], "user", "remove")
        result = delete_message(db, conv["id"], m2["id"])
        assert result is True
        msgs = list_messages(db, conv["id"])
        assert len(msgs) == 1
        assert msgs[0]["id"] == m1["id"]

    def test_delete_message_wrong_conversation(self, db: sqlite3.Connection) -> None:
        conv1 = create_conversation(db, title="Conv1")
        conv2 = create_conversation(db, title="Conv2")
        msg = create_message(db, conv1["id"], "user", "hello")
        result = delete_message(db, conv2["id"], msg["id"])
        assert result is False
        msgs = list_messages(db, conv1["id"])
        assert len(msgs) == 1

    def test_delete_message_nonexistent(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Test")
        result = delete_message(db, conv["id"], "nonexistent-id")
        assert result is False

    def test_delete_message_updates_conversation_timestamp(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Timestamp")
        msg = create_message(db, conv["id"], "user", "hello")
        before = get_conversation(db, conv["id"])
        assert before is not None
        delete_message(db, conv["id"], msg["id"])
        after = get_conversation(db, conv["id"])
        assert after is not None
        assert after["updated_at"] >= before["updated_at"]


class TestReplaceDocumentContent:
    def test_replace_document_content_works(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Doc", conversation_type="document")
        create_message(db, conv["id"], "user", "old content 1")
        create_message(db, conv["id"], "user", "old content 2")
        msg = replace_document_content(db, conv["id"], "new full content")
        assert msg["content"] == "new full content"
        assert msg["position"] == 0
        msgs = list_messages(db, conv["id"])
        assert len(msgs) == 1
        assert msgs[0]["content"] == "new full content"

    def test_replace_document_content_on_empty(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Empty Doc", conversation_type="document")
        msg = replace_document_content(db, conv["id"], "first content")
        assert msg["content"] == "first content"
        msgs = list_messages(db, conv["id"])
        assert len(msgs) == 1

    def test_replace_document_content_updates_timestamp(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Doc TS", conversation_type="document")
        before = get_conversation(db, conv["id"])
        assert before is not None
        replace_document_content(db, conv["id"], "content")
        after = get_conversation(db, conv["id"])
        assert after is not None
        assert after["updated_at"] >= before["updated_at"]

    def test_replace_document_content_preserves_identity(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Doc ID", conversation_type="document")
        msg = replace_document_content(db, conv["id"], "content", user_id="u1", user_display_name="Alice")
        assert msg["user_id"] == "u1"
        assert msg["user_display_name"] == "Alice"

    def test_replace_document_content_cascades_tool_calls(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Doc TC", conversation_type="document")
        msg = create_message(db, conv["id"], "user", "old content")
        create_tool_call(db, msg["id"], "read_file", "local", {"path": "/tmp"})
        tcs = list_tool_calls(db, msg["id"])
        assert len(tcs) == 1
        replace_document_content(db, conv["id"], "new content")
        tcs_after = list_tool_calls(db, msg["id"])
        assert len(tcs_after) == 0

    def test_replace_document_content_multiple_replaces(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Multi Replace", conversation_type="document")
        replace_document_content(db, conv["id"], "v1")
        replace_document_content(db, conv["id"], "v2")
        msg = replace_document_content(db, conv["id"], "v3")
        assert msg["content"] == "v3"
        msgs = list_messages(db, conv["id"])
        assert len(msgs) == 1
        assert msgs[0]["content"] == "v3"


class TestDeleteMessageCascade:
    def test_delete_message_cascades_tool_calls(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Cascade TC")
        msg = create_message(db, conv["id"], "assistant", "I used a tool")
        create_tool_call(db, msg["id"], "bash", "local", {"command": "ls"})
        assert len(list_tool_calls(db, msg["id"])) == 1
        delete_message(db, conv["id"], msg["id"])
        assert len(list_tool_calls(db, msg["id"])) == 0

    def test_delete_message_preserves_other_messages(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Preserve")
        m1 = create_message(db, conv["id"], "user", "first")
        m2 = create_message(db, conv["id"], "assistant", "second")
        m3 = create_message(db, conv["id"], "user", "third")
        delete_message(db, conv["id"], m2["id"])
        msgs = list_messages(db, conv["id"])
        assert len(msgs) == 2
        assert msgs[0]["id"] == m1["id"]
        assert msgs[1]["id"] == m3["id"]

    def test_delete_only_message(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Single")
        msg = create_message(db, conv["id"], "user", "only one")
        result = delete_message(db, conv["id"], msg["id"])
        assert result is True
        msgs = list_messages(db, conv["id"])
        assert len(msgs) == 0

    def test_delete_message_with_multiple_tool_calls(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="Multi TC")
        msg = create_message(db, conv["id"], "assistant", "I ran tools")
        create_tool_call(db, msg["id"], "bash", "local", {"command": "ls"})
        create_tool_call(db, msg["id"], "read_file", "local", {"path": "/tmp"})
        create_tool_call(db, msg["id"], "glob_files", "local", {"pattern": "*.py"})
        assert len(list_tool_calls(db, msg["id"])) == 3
        delete_message(db, conv["id"], msg["id"])
        assert len(list_tool_calls(db, msg["id"])) == 0

    def test_delete_message_sql_injection_in_id(self, db: sqlite3.Connection) -> None:
        conv = create_conversation(db, title="SQLi")
        create_message(db, conv["id"], "user", "safe")
        result = delete_message(db, conv["id"], "'; DROP TABLE messages;--")
        assert result is False
        msgs = list_messages(db, conv["id"])
        assert len(msgs) == 1


class TestSaveAttachment:
    def test_save_text_attachment(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        conv = create_conversation(db, title="Test")
        msg = create_message(db, conv["id"], "user", "See attached")
        result = save_attachment(db, msg["id"], conv["id"], "notes.txt", "text/plain", b"Hello", tmp_path)
        assert result["filename"] == "notes.txt"
        assert result["mime_type"] == "text/plain"
        assert result["size_bytes"] == 5

    def test_save_markdown_attachment(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        conv = create_conversation(db, title="Test")
        msg = create_message(db, conv["id"], "user", "See attached")
        result = save_attachment(db, msg["id"], conv["id"], "readme.md", "text/markdown", b"# Hello", tmp_path)
        assert result["filename"] == "readme.md"

    def test_save_json_attachment(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        conv = create_conversation(db, title="Test")
        msg = create_message(db, conv["id"], "user", "See attached")
        result = save_attachment(db, msg["id"], conv["id"], "data.json", "application/json", b"{}", tmp_path)
        assert result["filename"] == "data.json"

    def test_save_docx_attachment(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        conv = create_conversation(db, title="Test")
        msg = create_message(db, conv["id"], "user", "See attached")
        zip_header = b"PK\x03\x04" + b"\x00" * 26
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        result = save_attachment(db, msg["id"], conv["id"], "doc.docx", mime, zip_header, tmp_path)
        assert result["filename"] == "doc.docx"

    def test_reject_unsupported_type(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        conv = create_conversation(db, title="Test")
        msg = create_message(db, conv["id"], "user", "See attached")
        with pytest.raises(ValueError, match="Unsupported file type"):
            save_attachment(db, msg["id"], conv["id"], "bad.exe", "application/x-executable", b"MZ", tmp_path)

    def test_reject_oversized_file(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        conv = create_conversation(db, title="Test")
        msg = create_message(db, conv["id"], "user", "See attached")
        with pytest.raises(ValueError, match="maximum size"):
            save_attachment(db, msg["id"], conv["id"], "big.txt", "text/plain", b"x" * (11 * 1024 * 1024), tmp_path)

    def test_octet_stream_text_passes(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        conv = create_conversation(db, title="Test")
        msg = create_message(db, conv["id"], "user", "See attached")
        result = save_attachment(
            db, msg["id"], conv["id"], "file.md", "application/octet-stream", b"# Markdown content", tmp_path
        )
        assert result["filename"] == "file.md"

    def test_octet_stream_binary_rejected(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        conv = create_conversation(db, title="Test")
        msg = create_message(db, conv["id"], "user", "See attached")
        binary_data = bytes(range(256)) * 10
        with pytest.raises(ValueError, match="Cannot verify file content type"):
            save_attachment(db, msg["id"], conv["id"], "file.md", "application/octet-stream", binary_data, tmp_path)

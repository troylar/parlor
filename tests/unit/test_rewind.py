"""Tests for the conversation rewind service and endpoint."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from parlor.db import _FTS_SCHEMA, _FTS_TRIGGERS, _SCHEMA, ThreadSafeConnection
from parlor.models import RewindRequest
from parlor.routers.conversations import rewind_conversation
from parlor.services.rewind import collect_file_paths
from parlor.services.rewind import rewind_conversation as rewind_service
from parlor.services.storage import (
    create_conversation,
    create_message,
    create_tool_call,
    list_messages,
    list_tool_calls,
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


def _make_request(db: ThreadSafeConnection, data_dir: Path | None = None) -> SimpleNamespace:
    """Build a fake Request with app.state for the endpoint."""
    config = SimpleNamespace(app=SimpleNamespace(data_dir=data_dir))
    state = SimpleNamespace(db=db, config=config)
    app = SimpleNamespace(state=state)
    request = SimpleNamespace(app=app, query_params={})
    return request


class TestRewindService:
    """Tests for the shared rewind service layer."""

    async def test_rewind_deletes_messages(self, db, tmp_path):
        conv = create_conversation(db, title="Test")
        create_message(db, conv["id"], "user", "msg0")
        create_message(db, conv["id"], "assistant", "msg1")
        create_message(db, conv["id"], "user", "msg2")
        create_message(db, conv["id"], "assistant", "msg3")

        result = await rewind_service(db, conv["id"], to_position=1, data_dir=tmp_path)

        assert result.deleted_messages == 2
        assert result.reverted_files == []
        assert result.skipped_files == []
        msgs = list_messages(db, conv["id"])
        assert len(msgs) == 2
        assert [m["content"] for m in msgs] == ["msg0", "msg1"]

    async def test_undo_files_reverts_git(self, db, tmp_path):
        conv = create_conversation(db, title="Test")
        create_message(db, conv["id"], "user", "msg0")
        msg1 = create_message(db, conv["id"], "assistant", "wrote a file")
        create_tool_call(db, msg1["id"], "write_file", "builtin", {"path": "/tmp/test.py", "content": "hello"})

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with (
            patch("parlor.services.rewind.check_git_repo", return_value=True),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec,
        ):
            result = await rewind_service(db, conv["id"], to_position=0, undo_files=True, data_dir=tmp_path)

        assert result.deleted_messages == 1
        assert len(result.reverted_files) == 1
        assert "/tmp/test.py" in result.reverted_files[0]
        assert result.skipped_files == []
        mock_exec.assert_called_once()

    async def test_undo_files_skips_on_git_failure(self, db, tmp_path):
        conv = create_conversation(db, title="Test")
        create_message(db, conv["id"], "user", "msg0")
        msg1 = create_message(db, conv["id"], "assistant", "wrote a file")
        create_tool_call(db, msg1["id"], "write_file", "builtin", {"path": "/tmp/test.py", "content": "hello"})

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error: pathspec did not match"))

        with (
            patch("parlor.services.rewind.check_git_repo", return_value=True),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        ):
            result = await rewind_service(db, conv["id"], to_position=0, undo_files=True, data_dir=tmp_path)

        assert result.deleted_messages == 1
        assert result.reverted_files == []
        assert len(result.skipped_files) == 1
        assert "pathspec" in result.skipped_files[0]

    async def test_undo_files_skips_non_git(self, db, tmp_path):
        conv = create_conversation(db, title="Test")
        create_message(db, conv["id"], "user", "msg0")
        msg1 = create_message(db, conv["id"], "assistant", "wrote a file")
        create_tool_call(db, msg1["id"], "write_file", "builtin", {"path": "/tmp/test.py", "content": "hello"})

        with patch("parlor.services.rewind.check_git_repo", return_value=False):
            result = await rewind_service(db, conv["id"], to_position=0, undo_files=True, data_dir=tmp_path)

        assert result.deleted_messages == 1
        assert result.reverted_files == []
        assert len(result.skipped_files) == 1
        assert "not a git repo" in result.skipped_files[0]

    async def test_no_file_tools(self, db, tmp_path):
        """Rewind with undo_files=True but no file-modifying tool calls."""
        conv = create_conversation(db, title="Test")
        create_message(db, conv["id"], "user", "msg0")
        msg1 = create_message(db, conv["id"], "assistant", "used a tool")
        create_tool_call(db, msg1["id"], "bash", "builtin", {"command": "ls"})

        result = await rewind_service(db, conv["id"], to_position=0, undo_files=True, data_dir=tmp_path)

        assert result.deleted_messages == 1
        assert result.reverted_files == []
        assert result.skipped_files == []

    async def test_deduplicates_file_paths(self, db, tmp_path):
        """Same file modified by multiple tool calls should only be reverted once."""
        conv = create_conversation(db, title="Test")
        create_message(db, conv["id"], "user", "msg0")
        msg1 = create_message(db, conv["id"], "assistant", "edited twice")
        create_tool_call(db, msg1["id"], "write_file", "builtin", {"path": "/tmp/test.py", "content": "v1"})
        create_tool_call(
            db,
            msg1["id"],
            "edit_file",
            "builtin",
            {"path": "/tmp/test.py", "old_string": "v1", "new_string": "v2"},
        )

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with (
            patch("parlor.services.rewind.check_git_repo", return_value=True),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec,
        ):
            result = await rewind_service(db, conv["id"], to_position=0, undo_files=True, data_dir=tmp_path)

        assert len(result.reverted_files) == 1
        mock_exec.assert_called_once()

    async def test_cascades_tool_calls(self, db, tmp_path):
        """Tool calls on deleted messages should be cleaned up."""
        conv = create_conversation(db, title="Test")
        create_message(db, conv["id"], "user", "msg0")
        msg1 = create_message(db, conv["id"], "assistant", "msg1")
        create_tool_call(db, msg1["id"], "write_file", "builtin", {"path": "/tmp/f.py", "content": "x"})

        await rewind_service(db, conv["id"], to_position=0, data_dir=tmp_path)

        tcs = list_tool_calls(db, msg1["id"])
        assert len(tcs) == 0

    def test_collect_file_paths(self, db):
        """collect_file_paths extracts paths from write_file and edit_file only."""
        conv = create_conversation(db, title="Test")
        msg = create_message(db, conv["id"], "assistant", "tools")
        create_tool_call(db, msg["id"], "write_file", "builtin", {"path": "/a.py", "content": "x"})
        create_tool_call(db, msg["id"], "edit_file", "builtin", {"path": "/b.py", "old_string": "x", "new_string": "y"})
        create_tool_call(db, msg["id"], "bash", "builtin", {"command": "echo hi"})
        create_tool_call(db, msg["id"], "read_file", "builtin", {"path": "/c.py"})

        paths = collect_file_paths(db, [msg["id"]])
        assert paths == {"/a.py", "/b.py"}


class TestRewindEndpoint:
    """Tests for the HTTP endpoint wrapper around the rewind service."""

    async def test_rewind_endpoint(self, db, tmp_path):
        conv = create_conversation(db, title="Test")
        create_message(db, conv["id"], "user", "msg0")
        create_message(db, conv["id"], "assistant", "msg1")

        request = _make_request(db, tmp_path)
        body = RewindRequest(to_position=0, undo_files=False)

        result = await rewind_conversation(conv["id"], body, request)

        assert result.deleted_messages == 1
        msgs = list_messages(db, conv["id"])
        assert len(msgs) == 1

    async def test_rewind_invalid_position(self, db, tmp_path):
        conv = create_conversation(db, title="Test")
        create_message(db, conv["id"], "user", "msg0")

        request = _make_request(db, tmp_path)
        body = RewindRequest(to_position=99, undo_files=False)

        with pytest.raises(Exception, match="Invalid position"):
            await rewind_conversation(conv["id"], body, request)

    async def test_rewind_nonexistent_conversation(self, db, tmp_path):
        request = _make_request(db, tmp_path)
        body = RewindRequest(to_position=0, undo_files=False)

        with pytest.raises(Exception, match="Conversation not found"):
            await rewind_conversation("00000000-0000-0000-0000-000000000000", body, request)

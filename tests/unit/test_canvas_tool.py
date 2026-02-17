"""Unit tests for canvas tool handlers (create_canvas, update_canvas)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from anteroom.tools.canvas import (
    CANVAS_CREATE_DEFINITION,
    CANVAS_PATCH_DEFINITION,
    CANVAS_UPDATE_DEFINITION,
    MAX_CANVAS_CONTENT,
    handle_create_canvas,
    handle_patch_canvas,
    handle_update_canvas,
)


@pytest.fixture()
def mock_storage():
    """Patch the storage module that canvas tools import lazily."""
    mock = MagicMock()
    # Ensure the services package has a storage attribute for the lazy import
    with patch.dict(sys.modules, {"anteroom.services.storage": mock}):
        # Also make it importable via `from anteroom.services import storage`
        import anteroom.services

        original = getattr(anteroom.services, "storage", None)
        anteroom.services.storage = mock
        yield mock
        if original is not None:
            anteroom.services.storage = original
        elif hasattr(anteroom.services, "storage"):
            delattr(anteroom.services, "storage")


class TestCanvasDefinitions:
    def test_create_definition_has_required_fields(self) -> None:
        assert CANVAS_CREATE_DEFINITION["name"] == "create_canvas"
        params = CANVAS_CREATE_DEFINITION["parameters"]
        assert "title" in params["properties"]
        assert "content" in params["properties"]
        assert params["required"] == ["title", "content"]

    def test_update_definition_has_required_fields(self) -> None:
        assert CANVAS_UPDATE_DEFINITION["name"] == "update_canvas"
        params = CANVAS_UPDATE_DEFINITION["parameters"]
        assert "content" in params["properties"]
        assert params["required"] == ["content"]

    def test_patch_definition_has_required_fields(self) -> None:
        assert CANVAS_PATCH_DEFINITION["name"] == "patch_canvas"
        params = CANVAS_PATCH_DEFINITION["parameters"]
        assert "edits" in params["properties"]
        assert params["required"] == ["edits"]
        items = params["properties"]["edits"]["items"]
        assert items["required"] == ["search", "replace"]


class TestHandleCreateCanvas:
    @pytest.mark.asyncio()
    async def test_missing_conversation_id(self) -> None:
        result = await handle_create_canvas(title="T", content="C", _conversation_id=None, _db=MagicMock())
        assert "error" in result
        assert "context" in result["error"].lower()

    @pytest.mark.asyncio()
    async def test_missing_db(self) -> None:
        result = await handle_create_canvas(title="T", content="C", _conversation_id="conv-1", _db=None)
        assert "error" in result

    @pytest.mark.asyncio()
    async def test_conversation_not_found(self, mock_storage: MagicMock) -> None:
        mock_storage.get_conversation.return_value = None
        result = await handle_create_canvas(
            title="T",
            content="C",
            _conversation_id="conv-1",
            _db=MagicMock(),
        )
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio()
    async def test_duplicate_canvas_returns_error(self, mock_storage: MagicMock) -> None:
        mock_storage.get_canvas_for_conversation.return_value = {"id": "existing"}
        result = await handle_create_canvas(
            title="T",
            content="C",
            _conversation_id="conv-1",
            _db=MagicMock(),
        )
        assert "error" in result
        assert "already exists" in result["error"]

    @pytest.mark.asyncio()
    async def test_create_success(self, mock_storage: MagicMock) -> None:
        mock_db = MagicMock()
        mock_storage.get_canvas_for_conversation.return_value = None
        mock_storage.create_canvas.return_value = {
            "id": "canvas-1",
            "title": "My Canvas",
            "content": "# Hello",
            "language": "python",
            "version": 1,
        }
        result = await handle_create_canvas(
            title="My Canvas",
            content="# Hello",
            language="python",
            _conversation_id="conv-1",
            _db=mock_db,
            _user_id="user-1",
            _user_display_name="Troy",
        )
        assert result["status"] == "created"
        assert result["id"] == "canvas-1"
        assert result["title"] == "My Canvas"
        assert result["language"] == "python"
        mock_storage.create_canvas.assert_called_once_with(
            mock_db,
            "conv-1",
            title="My Canvas",
            content="# Hello",
            language="python",
            user_id="user-1",
            user_display_name="Troy",
        )

    @pytest.mark.asyncio()
    async def test_create_rejects_oversized_content(self, mock_storage: MagicMock) -> None:
        result = await handle_create_canvas(
            title="T",
            content="x" * (MAX_CANVAS_CONTENT + 1),
            _conversation_id="conv-1",
            _db=MagicMock(),
        )
        assert "error" in result
        assert "too large" in result["error"].lower()
        mock_storage.create_canvas.assert_not_called()

    @pytest.mark.asyncio()
    async def test_create_accepts_max_content(self, mock_storage: MagicMock) -> None:
        mock_storage.get_canvas_for_conversation.return_value = None
        mock_storage.create_canvas.return_value = {
            "id": "canvas-1",
            "title": "T",
            "content": "x" * MAX_CANVAS_CONTENT,
            "version": 1,
        }
        result = await handle_create_canvas(
            title="T",
            content="x" * MAX_CANVAS_CONTENT,
            _conversation_id="conv-1",
            _db=MagicMock(),
        )
        assert result["status"] == "created"

    @pytest.mark.asyncio()
    async def test_create_without_language(self, mock_storage: MagicMock) -> None:
        mock_storage.get_canvas_for_conversation.return_value = None
        mock_storage.create_canvas.return_value = {
            "id": "canvas-1",
            "title": "Notes",
            "content": "text",
            "version": 1,
        }
        result = await handle_create_canvas(
            title="Notes",
            content="text",
            _conversation_id="conv-1",
            _db=MagicMock(),
        )
        assert result["status"] == "created"
        assert result.get("language") is None


class TestHandleUpdateCanvas:
    @pytest.mark.asyncio()
    async def test_missing_conversation_id(self) -> None:
        result = await handle_update_canvas(content="C", _conversation_id=None, _db=MagicMock())
        assert "error" in result

    @pytest.mark.asyncio()
    async def test_missing_db(self) -> None:
        result = await handle_update_canvas(content="C", _conversation_id="conv-1", _db=None)
        assert "error" in result

    @pytest.mark.asyncio()
    async def test_conversation_not_found(self, mock_storage: MagicMock) -> None:
        mock_storage.get_conversation.return_value = None
        result = await handle_update_canvas(
            content="C",
            _conversation_id="conv-1",
            _db=MagicMock(),
        )
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio()
    async def test_update_rejects_oversized_content(self, mock_storage: MagicMock) -> None:
        result = await handle_update_canvas(
            content="x" * (MAX_CANVAS_CONTENT + 1),
            _conversation_id="conv-1",
            _db=MagicMock(),
        )
        assert "error" in result
        assert "too large" in result["error"].lower()
        mock_storage.update_canvas.assert_not_called()

    @pytest.mark.asyncio()
    async def test_no_canvas_returns_error(self, mock_storage: MagicMock) -> None:
        mock_storage.get_canvas_for_conversation.return_value = None
        result = await handle_update_canvas(
            content="new content",
            _conversation_id="conv-1",
            _db=MagicMock(),
        )
        assert "error" in result
        assert "No canvas" in result["error"]

    @pytest.mark.asyncio()
    async def test_update_success(self, mock_storage: MagicMock) -> None:
        mock_db = MagicMock()
        mock_storage.get_canvas_for_conversation.return_value = {"id": "canvas-1"}
        mock_storage.update_canvas.return_value = {
            "id": "canvas-1",
            "title": "Updated",
            "version": 2,
        }
        result = await handle_update_canvas(
            content="new content",
            title="Updated",
            _conversation_id="conv-1",
            _db=mock_db,
        )
        assert result["status"] == "updated"
        assert result["id"] == "canvas-1"
        assert result["version"] == 2
        mock_storage.update_canvas.assert_called_once_with(
            mock_db,
            "canvas-1",
            content="new content",
            title="Updated",
        )

    @pytest.mark.asyncio()
    async def test_update_fails_returns_error(self, mock_storage: MagicMock) -> None:
        mock_storage.get_canvas_for_conversation.return_value = {"id": "canvas-1"}
        mock_storage.update_canvas.return_value = None
        result = await handle_update_canvas(
            content="new content",
            _conversation_id="conv-1",
            _db=MagicMock(),
        )
        assert "error" in result
        assert "Failed" in result["error"]

    @pytest.mark.asyncio()
    async def test_update_content_only(self, mock_storage: MagicMock) -> None:
        mock_db = MagicMock()
        mock_storage.get_canvas_for_conversation.return_value = {"id": "canvas-1"}
        mock_storage.update_canvas.return_value = {
            "id": "canvas-1",
            "title": "Original Title",
            "version": 3,
        }
        result = await handle_update_canvas(
            content="just content update",
            _conversation_id="conv-1",
            _db=mock_db,
        )
        assert result["status"] == "updated"
        mock_storage.update_canvas.assert_called_once_with(
            mock_db,
            "canvas-1",
            content="just content update",
            title=None,
        )


class TestPatchCanvas:
    @pytest.mark.asyncio()
    async def test_missing_conversation_id(self) -> None:
        result = await handle_patch_canvas(
            edits=[{"search": "a", "replace": "b"}], _conversation_id=None, _db=MagicMock()
        )
        assert "error" in result
        assert "context" in result["error"].lower()

    @pytest.mark.asyncio()
    async def test_missing_db(self) -> None:
        result = await handle_patch_canvas(edits=[{"search": "a", "replace": "b"}], _conversation_id="conv-1", _db=None)
        assert "error" in result

    @pytest.mark.asyncio()
    async def test_conversation_not_found(self, mock_storage: MagicMock) -> None:
        mock_storage.get_conversation.return_value = None
        result = await handle_patch_canvas(
            edits=[{"search": "a", "replace": "b"}],
            _conversation_id="conv-1",
            _db=MagicMock(),
        )
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio()
    async def test_empty_edits(self, mock_storage: MagicMock) -> None:
        mock_storage.get_conversation.return_value = {"id": "conv-1"}
        result = await handle_patch_canvas(edits=[], _conversation_id="conv-1", _db=MagicMock())
        assert "error" in result
        assert "No edits" in result["error"]

    @pytest.mark.asyncio()
    async def test_too_many_edits(self, mock_storage: MagicMock) -> None:
        mock_storage.get_conversation.return_value = {"id": "conv-1"}
        edits = [{"search": f"s{i}", "replace": f"r{i}"} for i in range(51)]
        result = await handle_patch_canvas(edits=edits, _conversation_id="conv-1", _db=MagicMock())
        assert "error" in result
        assert "Too many" in result["error"]

    @pytest.mark.asyncio()
    async def test_no_canvas_returns_error(self, mock_storage: MagicMock) -> None:
        mock_storage.get_canvas_for_conversation.return_value = None
        result = await handle_patch_canvas(
            edits=[{"search": "a", "replace": "b"}],
            _conversation_id="conv-1",
            _db=MagicMock(),
        )
        assert "error" in result
        assert "No canvas" in result["error"]

    @pytest.mark.asyncio()
    async def test_single_edit_success(self, mock_storage: MagicMock) -> None:
        mock_db = MagicMock()
        mock_storage.get_canvas_for_conversation.return_value = {
            "id": "canvas-1",
            "content": "Hello World",
        }
        mock_storage.update_canvas.return_value = {
            "id": "canvas-1",
            "title": "My Canvas",
            "version": 2,
        }
        result = await handle_patch_canvas(
            edits=[{"search": "World", "replace": "Earth"}],
            _conversation_id="conv-1",
            _db=mock_db,
        )
        assert result["status"] == "patched"
        assert result["edits_applied"] == 1
        assert result["version"] == 2
        assert "patches" not in result
        mock_storage.update_canvas.assert_called_once_with(mock_db, "canvas-1", content="Hello Earth")

    @pytest.mark.asyncio()
    async def test_multiple_sequential_edits(self, mock_storage: MagicMock) -> None:
        mock_db = MagicMock()
        mock_storage.get_canvas_for_conversation.return_value = {
            "id": "canvas-1",
            "content": "foo bar baz",
        }
        mock_storage.update_canvas.return_value = {
            "id": "canvas-1",
            "title": "T",
            "version": 3,
        }
        result = await handle_patch_canvas(
            edits=[
                {"search": "foo", "replace": "FOO"},
                {"search": "baz", "replace": "BAZ"},
            ],
            _conversation_id="conv-1",
            _db=mock_db,
        )
        assert result["status"] == "patched"
        assert result["edits_applied"] == 2
        mock_storage.update_canvas.assert_called_once_with(mock_db, "canvas-1", content="FOO bar BAZ")

    @pytest.mark.asyncio()
    async def test_search_not_found(self, mock_storage: MagicMock) -> None:
        mock_storage.get_canvas_for_conversation.return_value = {
            "id": "canvas-1",
            "content": "Hello World",
        }
        result = await handle_patch_canvas(
            edits=[{"search": "Missing", "replace": "X"}],
            _conversation_id="conv-1",
            _db=MagicMock(),
        )
        assert "error" in result
        assert "not found" in result["error"]
        assert result["edit_index"] == 0
        assert result["failed_edit"]["search"] == "Missing"

    @pytest.mark.asyncio()
    async def test_ambiguous_search(self, mock_storage: MagicMock) -> None:
        mock_storage.get_canvas_for_conversation.return_value = {
            "id": "canvas-1",
            "content": "aaa bbb aaa",
        }
        result = await handle_patch_canvas(
            edits=[{"search": "aaa", "replace": "ccc"}],
            _conversation_id="conv-1",
            _db=MagicMock(),
        )
        assert "error" in result
        assert "ambiguous" in result["error"].lower()
        assert result["edit_index"] == 0

    @pytest.mark.asyncio()
    async def test_version_incremented(self, mock_storage: MagicMock) -> None:
        mock_storage.get_canvas_for_conversation.return_value = {
            "id": "canvas-1",
            "content": "old text",
        }
        mock_storage.update_canvas.return_value = {
            "id": "canvas-1",
            "title": "T",
            "version": 5,
        }
        result = await handle_patch_canvas(
            edits=[{"search": "old", "replace": "new"}],
            _conversation_id="conv-1",
            _db=MagicMock(),
        )
        assert result["version"] == 5

    @pytest.mark.asyncio()
    async def test_empty_search_string(self, mock_storage: MagicMock) -> None:
        mock_storage.get_canvas_for_conversation.return_value = {
            "id": "canvas-1",
            "content": "some content",
        }
        result = await handle_patch_canvas(
            edits=[{"search": "", "replace": "X"}],
            _conversation_id="conv-1",
            _db=MagicMock(),
        )
        assert "error" in result
        assert result["edit_index"] == 0

    @pytest.mark.asyncio()
    async def test_patch_rejects_oversized_result(self, mock_storage: MagicMock) -> None:
        """Patch that expands content beyond MAX_CANVAS_CONTENT is rejected."""
        base = "x" * (MAX_CANVAS_CONTENT - 5)
        mock_storage.get_canvas_for_conversation.return_value = {
            "id": "canvas-1",
            "content": base + "SHORT",
        }
        result = await handle_patch_canvas(
            edits=[{"search": "SHORT", "replace": "y" * 100}],
            _conversation_id="conv-1",
            _db=MagicMock(),
        )
        assert "error" in result
        assert "size limit" in result["error"].lower()

    @pytest.mark.asyncio()
    async def test_patch_storage_failure_after_apply(self, mock_storage: MagicMock) -> None:
        """Storage returns None after patches applied — should report error."""
        mock_storage.get_canvas_for_conversation.return_value = {
            "id": "canvas-1",
            "content": "Hello World",
        }
        mock_storage.update_canvas.return_value = None
        result = await handle_patch_canvas(
            edits=[{"search": "World", "replace": "Earth"}],
            _conversation_id="conv-1",
            _db=MagicMock(),
        )
        assert "error" in result
        assert "Failed" in result["error"]
        mock_storage.update_canvas.assert_called_once()

"""Tests for the DOCX tool."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from anteroom.tools.office_docx import _MAX_CONTENT_BLOCKS, AVAILABLE, DEFINITION, handle, set_working_dir

_needs_docx = pytest.mark.skipif(not AVAILABLE, reason="requires python-docx: pip install anteroom[office]")


@pytest.fixture(autouse=True)
def _set_working_dir(tmp_path):
    set_working_dir(str(tmp_path))
    yield


class TestDefinition:
    def test_name(self):
        assert DEFINITION["name"] == "docx"

    def test_required_params(self):
        assert "action" in DEFINITION["parameters"]["required"]
        assert "path" in DEFINITION["parameters"]["required"]


@_needs_docx
class TestCreate:
    @pytest.mark.asyncio
    async def test_create_simple(self, tmp_path):
        result = await handle(
            action="create",
            path="test.docx",
            content_blocks=[
                {"type": "heading", "text": "Title", "level": 1},
                {"type": "paragraph", "text": "Hello world"},
            ],
        )
        assert "error" not in result
        assert result["blocks_written"] == 2
        assert (tmp_path / "test.docx").exists()

    @pytest.mark.asyncio
    async def test_create_with_table(self, tmp_path):
        result = await handle(
            action="create",
            path="tables.docx",
            content_blocks=[
                {"type": "table", "rows": [["A", "B"], ["1", "2"]]},
            ],
        )
        assert "error" not in result
        assert (tmp_path / "tables.docx").exists()

    @pytest.mark.asyncio
    async def test_create_no_blocks(self):
        result = await handle(action="create", path="empty.docx")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_create_too_many_blocks(self):
        blocks = [{"type": "paragraph", "text": f"p{i}"} for i in range(_MAX_CONTENT_BLOCKS + 1)]
        result = await handle(action="create", path="big.docx", content_blocks=blocks)
        assert "error" in result
        assert "Too many" in result["error"]

    @pytest.mark.asyncio
    async def test_create_subdirectory(self, tmp_path):
        result = await handle(
            action="create",
            path="sub/dir/test.docx",
            content_blocks=[{"type": "paragraph", "text": "nested"}],
        )
        assert "error" not in result
        assert (tmp_path / "sub" / "dir" / "test.docx").exists()


@_needs_docx
class TestRead:
    @pytest.mark.asyncio
    async def test_read_simple(self, tmp_path):
        await handle(
            action="create",
            path="read_me.docx",
            content_blocks=[
                {"type": "heading", "text": "Title", "level": 1},
                {"type": "paragraph", "text": "Body text"},
            ],
        )
        result = await handle(action="read", path="read_me.docx")
        assert "error" not in result
        assert "Title" in result["content"]
        assert "Body text" in result["content"]

    @pytest.mark.asyncio
    async def test_read_with_table(self, tmp_path):
        await handle(
            action="create",
            path="table.docx",
            content_blocks=[
                {"type": "table", "rows": [["Name", "Age"], ["Alice", "30"]]},
            ],
        )
        result = await handle(action="read", path="table.docx")
        assert "error" not in result
        assert "Alice" in result["content"]

    @pytest.mark.asyncio
    async def test_read_not_found(self):
        result = await handle(action="read", path="missing.docx")
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_read_corrupt_file(self, tmp_path):
        corrupt = tmp_path / "corrupt.docx"
        corrupt.write_bytes(b"not a docx file")
        result = await handle(action="read", path="corrupt.docx")
        assert "error" in result


@_needs_docx
class TestEdit:
    @pytest.mark.asyncio
    async def test_edit_replace(self, tmp_path):
        await handle(
            action="create",
            path="edit_me.docx",
            content_blocks=[{"type": "paragraph", "text": "Hello world"}],
        )
        result = await handle(
            action="edit",
            path="edit_me.docx",
            replacements=[{"old": "Hello", "new": "Goodbye"}],
        )
        assert "error" not in result
        assert result["replacements_made"] >= 1

        read_result = await handle(action="read", path="edit_me.docx")
        assert "Goodbye" in read_result["content"]

    @pytest.mark.asyncio
    async def test_edit_append(self, tmp_path):
        await handle(
            action="create",
            path="append.docx",
            content_blocks=[{"type": "paragraph", "text": "Original"}],
        )
        result = await handle(
            action="edit",
            path="append.docx",
            content_blocks=[{"type": "paragraph", "text": "Appended"}],
        )
        assert "error" not in result
        assert result["blocks_appended"] == 1

    @pytest.mark.asyncio
    async def test_edit_not_found(self):
        result = await handle(
            action="edit",
            path="missing.docx",
            replacements=[{"old": "a", "new": "b"}],
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_edit_no_operations(self, tmp_path):
        await handle(
            action="create",
            path="noop.docx",
            content_blocks=[{"type": "paragraph", "text": "text"}],
        )
        result = await handle(action="edit", path="noop.docx")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_edit_too_many_blocks(self, tmp_path):
        await handle(
            action="create",
            path="big.docx",
            content_blocks=[{"type": "paragraph", "text": "text"}],
        )
        blocks = [{"type": "paragraph", "text": f"p{i}"} for i in range(_MAX_CONTENT_BLOCKS + 1)]
        result = await handle(action="edit", path="big.docx", content_blocks=blocks)
        assert "error" in result


@_needs_docx
class TestPathValidation:
    @pytest.mark.asyncio
    async def test_blocked_system_path_rejected(self):
        result = await handle(
            action="create",
            path="/etc/shadow",
            content_blocks=[{"type": "paragraph", "text": "x"}],
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_null_bytes_rejected(self):
        result = await handle(action="read", path="test\x00.docx")
        assert "error" in result


@_needs_docx
class TestUnknownAction:
    @pytest.mark.asyncio
    async def test_unknown_action(self):
        result = await handle(action="delete", path="test.docx")
        assert "error" in result
        assert "Unknown action" in result["error"]


class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_unavailable(self):
        with patch("anteroom.tools.office_docx.AVAILABLE", False):
            result = await handle(action="read", path="test.docx")
            assert "error" in result
            assert "pip install" in result["error"]

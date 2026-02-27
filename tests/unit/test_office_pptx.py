"""Tests for the PPTX tool."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from anteroom.tools.office_pptx import _MAX_SLIDES, AVAILABLE, DEFINITION, handle, set_working_dir

_needs_pptx = pytest.mark.skipif(not AVAILABLE, reason="requires python-pptx: pip install anteroom[office]")


@pytest.fixture(autouse=True)
def _set_working_dir(tmp_path):
    set_working_dir(str(tmp_path))
    yield


class TestDefinition:
    def test_name(self):
        assert DEFINITION["name"] == "pptx"

    def test_required_params(self):
        assert "action" in DEFINITION["parameters"]["required"]
        assert "path" in DEFINITION["parameters"]["required"]


@_needs_pptx
class TestCreate:
    @pytest.mark.asyncio
    async def test_create_simple(self, tmp_path):
        result = await handle(
            action="create",
            path="test.pptx",
            slides=[
                {"title": "Slide 1", "content": "Hello world"},
                {"title": "Slide 2", "bullets": ["Point A", "Point B"]},
            ],
        )
        assert "error" not in result
        assert result["slides_created"] == 2
        assert (tmp_path / "test.pptx").exists()

    @pytest.mark.asyncio
    async def test_create_with_notes(self, tmp_path):
        result = await handle(
            action="create",
            path="notes.pptx",
            slides=[{"title": "Talk", "content": "Main point", "notes": "Speaker notes here"}],
        )
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_create_no_slides(self):
        result = await handle(action="create", path="empty.pptx")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_create_too_many_slides(self):
        slides = [{"title": f"Slide {i}"} for i in range(_MAX_SLIDES + 1)]
        result = await handle(action="create", path="big.pptx", slides=slides)
        assert "error" in result
        assert "Too many" in result["error"]


@_needs_pptx
class TestRead:
    @pytest.mark.asyncio
    async def test_read_simple(self, tmp_path):
        await handle(
            action="create",
            path="read_me.pptx",
            slides=[
                {"title": "Title Slide", "content": "Body text"},
            ],
        )
        result = await handle(action="read", path="read_me.pptx")
        assert "error" not in result
        assert "Title Slide" in result["content"]
        assert result["slides"] == 1

    @pytest.mark.asyncio
    async def test_read_with_notes(self, tmp_path):
        await handle(
            action="create",
            path="notes.pptx",
            slides=[{"title": "Talk", "notes": "My notes"}],
        )
        result = await handle(action="read", path="notes.pptx")
        assert "error" not in result
        assert "My notes" in result["content"]

    @pytest.mark.asyncio
    async def test_read_not_found(self):
        result = await handle(action="read", path="missing.pptx")
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_read_corrupt_file(self, tmp_path):
        corrupt = tmp_path / "corrupt.pptx"
        corrupt.write_bytes(b"not a pptx file")
        result = await handle(action="read", path="corrupt.pptx")
        assert "error" in result


@_needs_pptx
class TestEdit:
    @pytest.mark.asyncio
    async def test_edit_replace(self, tmp_path):
        await handle(
            action="create",
            path="edit_me.pptx",
            slides=[{"title": "Hello world", "content": "Hello content"}],
        )
        result = await handle(
            action="edit",
            path="edit_me.pptx",
            replacements=[{"old": "Hello", "new": "Goodbye"}],
        )
        assert "error" not in result
        assert result["replacements_made"] >= 1

    @pytest.mark.asyncio
    async def test_edit_append_slides(self, tmp_path):
        await handle(
            action="create",
            path="append.pptx",
            slides=[{"title": "Original"}],
        )
        result = await handle(
            action="edit",
            path="append.pptx",
            slides=[{"title": "Appended"}],
        )
        assert "error" not in result
        assert result["slides_appended"] == 1

    @pytest.mark.asyncio
    async def test_edit_not_found(self):
        result = await handle(
            action="edit",
            path="missing.pptx",
            replacements=[{"old": "a", "new": "b"}],
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_edit_no_operations(self, tmp_path):
        await handle(
            action="create",
            path="noop.pptx",
            slides=[{"title": "Slide"}],
        )
        result = await handle(action="edit", path="noop.pptx")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_edit_too_many_slides(self, tmp_path):
        await handle(
            action="create",
            path="big.pptx",
            slides=[{"title": "Slide"}],
        )
        slides = [{"title": f"S{i}"} for i in range(_MAX_SLIDES + 1)]
        result = await handle(action="edit", path="big.pptx", slides=slides)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_edit_exceeds_total_limit(self, tmp_path):
        # Create with many slides, then try to append more
        initial = [{"title": f"S{i}"} for i in range(_MAX_SLIDES - 1)]
        await handle(action="create", path="nearly_full.pptx", slides=initial)
        result = await handle(
            action="edit",
            path="nearly_full.pptx",
            slides=[{"title": "Extra1"}, {"title": "Extra2"}, {"title": "Extra3"}],
        )
        assert "error" in result
        assert "exceed" in result["error"].lower()


@_needs_pptx
class TestPathValidation:
    @pytest.mark.asyncio
    async def test_blocked_system_path_rejected(self):
        result = await handle(
            action="create",
            path="/etc/shadow",
            slides=[{"title": "x"}],
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_null_bytes_rejected(self):
        result = await handle(action="read", path="test\x00.pptx")
        assert "error" in result


@_needs_pptx
class TestUnknownAction:
    @pytest.mark.asyncio
    async def test_unknown_action(self):
        result = await handle(action="delete", path="test.pptx")
        assert "error" in result
        assert "Unknown action" in result["error"]


class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_unavailable(self):
        with patch("anteroom.tools.office_pptx.AVAILABLE", False):
            result = await handle(action="read", path="test.pptx")
            assert "error" in result
            assert "pip install" in result["error"]

"""Tests for the XLSX tool."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from anteroom.tools.office_xlsx import _MAX_ROWS, AVAILABLE, DEFINITION, handle, set_working_dir

_needs_openpyxl = pytest.mark.skipif(not AVAILABLE, reason="requires openpyxl: pip install anteroom[office]")


@pytest.fixture(autouse=True)
def _set_working_dir(tmp_path):
    set_working_dir(str(tmp_path))
    yield


class TestDefinition:
    def test_name(self):
        assert DEFINITION["name"] == "xlsx"

    def test_required_params(self):
        assert "action" in DEFINITION["parameters"]["required"]
        assert "path" in DEFINITION["parameters"]["required"]


@_needs_openpyxl
class TestCreate:
    @pytest.mark.asyncio
    async def test_create_simple(self, tmp_path):
        result = await handle(
            action="create",
            path="test.xlsx",
            sheets=[{"name": "Data", "headers": ["Name", "Age"], "rows": [["Alice", 30], ["Bob", 25]]}],
        )
        assert "error" not in result
        assert result["sheets_created"] == 1
        assert result["total_rows"] == 3  # 1 header + 2 data
        assert (tmp_path / "test.xlsx").exists()

    @pytest.mark.asyncio
    async def test_create_multiple_sheets(self, tmp_path):
        result = await handle(
            action="create",
            path="multi.xlsx",
            sheets=[
                {"name": "Sheet1", "rows": [["a", "b"]]},
                {"name": "Sheet2", "rows": [["c", "d"]]},
            ],
        )
        assert "error" not in result
        assert result["sheets_created"] == 2

    @pytest.mark.asyncio
    async def test_create_no_sheets(self):
        result = await handle(action="create", path="empty.xlsx")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_create_too_many_rows(self):
        rows = [[i] for i in range(_MAX_ROWS + 1)]
        result = await handle(action="create", path="big.xlsx", sheets=[{"name": "Big", "rows": rows}])
        assert "error" in result
        assert "Too many rows" in result["error"]


@_needs_openpyxl
class TestRead:
    @pytest.mark.asyncio
    async def test_read_simple(self, tmp_path):
        await handle(
            action="create",
            path="read_me.xlsx",
            sheets=[{"name": "Data", "rows": [["Alice", 30], ["Bob", 25]]}],
        )
        result = await handle(action="read", path="read_me.xlsx")
        assert "error" not in result
        assert "Alice" in result["content"]
        assert result["rows_read"] == 2

    @pytest.mark.asyncio
    async def test_read_specific_sheet(self, tmp_path):
        await handle(
            action="create",
            path="multi.xlsx",
            sheets=[
                {"name": "First", "rows": [["a"]]},
                {"name": "Second", "rows": [["b"]]},
            ],
        )
        result = await handle(action="read", path="multi.xlsx", sheet_name="Second")
        assert "error" not in result
        assert "b" in result["content"]

    @pytest.mark.asyncio
    async def test_read_missing_sheet(self, tmp_path):
        await handle(
            action="create",
            path="test.xlsx",
            sheets=[{"name": "Data", "rows": [["x"]]}],
        )
        result = await handle(action="read", path="test.xlsx", sheet_name="NonExistent")
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_read_not_found(self):
        result = await handle(action="read", path="missing.xlsx")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_read_corrupt_file(self, tmp_path):
        corrupt = tmp_path / "corrupt.xlsx"
        corrupt.write_bytes(b"not an xlsx file")
        result = await handle(action="read", path="corrupt.xlsx")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_read_with_range(self, tmp_path):
        await handle(
            action="create",
            path="range.xlsx",
            sheets=[{"name": "Data", "rows": [["a", "b", "c"], ["1", "2", "3"], ["4", "5", "6"]]}],
        )
        result = await handle(action="read", path="range.xlsx", cell_range="A1:B2")
        assert "error" not in result
        assert result["rows_read"] == 2


@_needs_openpyxl
class TestEdit:
    @pytest.mark.asyncio
    async def test_edit_cells(self, tmp_path):
        await handle(
            action="create",
            path="edit_me.xlsx",
            sheets=[{"name": "Data", "rows": [["old_value"]]}],
        )
        result = await handle(
            action="edit",
            path="edit_me.xlsx",
            updates=[{"cell": "A1", "value": "new_value"}],
        )
        assert "error" not in result
        assert result["cells_updated"] == 1

    @pytest.mark.asyncio
    async def test_edit_append_rows(self, tmp_path):
        await handle(
            action="create",
            path="append.xlsx",
            sheets=[{"name": "Data", "rows": [["a"]]}],
        )
        result = await handle(
            action="edit",
            path="append.xlsx",
            append_rows=[["b"], ["c"]],
        )
        assert "error" not in result
        assert result["rows_appended"] == 2

    @pytest.mark.asyncio
    async def test_edit_add_sheet(self, tmp_path):
        await handle(
            action="create",
            path="sheets.xlsx",
            sheets=[{"name": "Original", "rows": [["x"]]}],
        )
        result = await handle(
            action="edit",
            path="sheets.xlsx",
            add_sheets=[{"name": "NewSheet", "rows": [["y"]]}],
        )
        assert "error" not in result
        assert result["sheets_added"] == 1

    @pytest.mark.asyncio
    async def test_edit_not_found(self):
        result = await handle(
            action="edit",
            path="missing.xlsx",
            updates=[{"cell": "A1", "value": "x"}],
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_edit_no_operations(self, tmp_path):
        await handle(
            action="create",
            path="noop.xlsx",
            sheets=[{"name": "Data", "rows": [["x"]]}],
        )
        result = await handle(action="edit", path="noop.xlsx")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_edit_too_many_append_rows(self, tmp_path):
        await handle(
            action="create",
            path="big.xlsx",
            sheets=[{"name": "Data", "rows": [["x"]]}],
        )
        rows = [[i] for i in range(_MAX_ROWS + 1)]
        result = await handle(action="edit", path="big.xlsx", append_rows=rows)
        assert "error" in result


@_needs_openpyxl
class TestPathValidation:
    @pytest.mark.asyncio
    async def test_blocked_system_path_rejected(self):
        result = await handle(
            action="create",
            path="/etc/shadow",
            sheets=[{"name": "Sheet1", "rows": [["x"]]}],
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_null_bytes_rejected(self):
        result = await handle(action="read", path="test\x00.xlsx")
        assert "error" in result


@_needs_openpyxl
class TestUnknownAction:
    @pytest.mark.asyncio
    async def test_unknown_action(self):
        result = await handle(action="delete", path="test.xlsx")
        assert "error" in result
        assert "Unknown action" in result["error"]


class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_unavailable(self):
        with patch("anteroom.tools.office_xlsx.AVAILABLE", False):
            result = await handle(action="read", path="test.xlsx")
            assert "error" in result
            assert "pip install" in result["error"]

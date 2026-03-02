"""Tests for tools/grep.py (#689)."""

from __future__ import annotations

from pathlib import Path

import pytest

from anteroom.tools.grep import _search_file, handle, set_working_dir


@pytest.fixture()
def tmp_tree(tmp_path: Path) -> Path:
    (tmp_path / "hello.py").write_text("def hello():\n    return 'world'\n\ndef goodbye():\n    return 'bye'\n")
    (tmp_path / "data.txt").write_text("line one\nline two\nline three\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.py").write_text("import os\nimport sys\n")
    set_working_dir(str(tmp_path))
    return tmp_path


class TestSearchFile:
    def test_basic_match(self, tmp_tree: Path) -> None:
        import re

        regex = re.compile("hello")
        results = _search_file(tmp_tree / "hello.py", regex, context=0)
        assert len(results) == 1
        assert results[0]["line_number"] == 1

    def test_context_lines(self, tmp_tree: Path) -> None:
        import re

        regex = re.compile("line two")
        results = _search_file(tmp_tree / "data.txt", regex, context=1)
        assert len(results) == 1
        content = results[0]["content"]
        assert "line one" in content
        assert "line three" in content

    def test_no_match(self, tmp_tree: Path) -> None:
        import re

        regex = re.compile("nonexistent")
        results = _search_file(tmp_tree / "hello.py", regex, context=0)
        assert results == []

    def test_missing_file(self, tmp_path: Path) -> None:
        import re

        regex = re.compile("x")
        results = _search_file(tmp_path / "gone.txt", regex, context=0)
        assert results == []


class TestHandleGrep:
    @pytest.mark.asyncio
    async def test_search_single_file(self, tmp_tree: Path) -> None:
        result = await handle(pattern="hello", path=str(tmp_tree / "hello.py"))
        assert result["total_matches"] == 1

    @pytest.mark.asyncio
    async def test_search_directory(self, tmp_tree: Path) -> None:
        result = await handle(pattern="import", path=str(tmp_tree))
        assert result["total_matches"] >= 1

    @pytest.mark.asyncio
    async def test_glob_filter(self, tmp_tree: Path) -> None:
        result = await handle(pattern="import", path=str(tmp_tree), glob="**/*.py")
        assert result["total_matches"] >= 1

    @pytest.mark.asyncio
    async def test_case_insensitive(self, tmp_tree: Path) -> None:
        result = await handle(pattern="HELLO", path=str(tmp_tree / "hello.py"), case_insensitive=True)
        assert result["total_matches"] == 1

    @pytest.mark.asyncio
    async def test_case_sensitive_no_match(self, tmp_tree: Path) -> None:
        result = await handle(pattern="HELLO", path=str(tmp_tree / "hello.py"), case_insensitive=False)
        assert result["total_matches"] == 0

    @pytest.mark.asyncio
    async def test_invalid_regex(self) -> None:
        result = await handle(pattern="[invalid")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_path_not_found(self, tmp_path: Path) -> None:
        result = await handle(pattern="x", path=str(tmp_path / "nonexistent"))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_null_byte_glob(self, tmp_tree: Path) -> None:
        result = await handle(pattern="x", path=str(tmp_tree), glob="*\x00*")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_default_working_dir(self, tmp_tree: Path) -> None:
        result = await handle(pattern="hello")
        assert "total_matches" in result or "error" not in result

    @pytest.mark.asyncio
    async def test_context_in_directory_search(self, tmp_tree: Path) -> None:
        result = await handle(pattern="line two", path=str(tmp_tree), context=1)
        assert result["total_matches"] >= 1
        assert "line one" in result["content"]

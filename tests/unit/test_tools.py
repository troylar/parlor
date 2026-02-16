"""Tests for built-in tools."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from anteroom.tools import ToolRegistry, register_default_tools
from anteroom.tools.security import sanitize_command, validate_path


class TestToolRegistry:
    def test_register_and_has_tool(self) -> None:
        reg = ToolRegistry()

        async def handler(**kwargs):
            return {"ok": True}

        reg.register("test_tool", handler, {"name": "test_tool", "description": "test"})
        assert reg.has_tool("test_tool")
        assert not reg.has_tool("nonexistent")

    def test_list_tools(self) -> None:
        reg = ToolRegistry()

        async def handler(**kwargs):
            return {"ok": True}

        reg.register("alpha", handler, {"name": "alpha", "description": ""})
        reg.register("beta", handler, {"name": "beta", "description": ""})
        names = reg.list_tools()
        assert "alpha" in names
        assert "beta" in names

    def test_get_openai_tools(self) -> None:
        reg = ToolRegistry()

        async def handler(**kwargs):
            return {"ok": True}

        defn = {
            "name": "my_tool",
            "description": "A test tool",
            "parameters": {"type": "object", "properties": {}},
        }
        reg.register("my_tool", handler, defn)
        tools = reg.get_openai_tools()
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "my_tool"
        assert tools[0]["function"]["description"] == "A test tool"

    @pytest.mark.asyncio
    async def test_call_tool(self) -> None:
        reg = ToolRegistry()

        async def handler(x: int = 0, **kwargs):
            return {"result": x * 2}

        reg.register("double", handler, {"name": "double", "description": ""})
        result = await reg.call_tool("double", {"x": 5})
        assert result == {"result": 10}

    @pytest.mark.asyncio
    async def test_call_unknown_tool(self) -> None:
        reg = ToolRegistry()
        with pytest.raises(ValueError, match="Unknown built-in tool"):
            await reg.call_tool("nope", {})

    def test_register_default_tools(self) -> None:
        reg = ToolRegistry()
        register_default_tools(reg, working_dir="/tmp")
        names = reg.list_tools()
        assert "read_file" in names
        assert "write_file" in names
        assert "edit_file" in names
        assert "bash" in names
        assert "glob_files" in names
        assert "grep" in names

    @pytest.mark.asyncio
    async def test_destructive_command_confirmation_denied(self) -> None:
        reg = ToolRegistry()
        register_default_tools(reg, working_dir="/tmp")

        async def deny(msg: str) -> bool:
            return False

        reg.set_confirm_callback(deny)
        result = await reg.call_tool("bash", {"command": "rm -rf /some/dir"})
        assert "cancelled" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_destructive_command_confirmation_allowed(self) -> None:
        reg = ToolRegistry()
        register_default_tools(reg, working_dir="/tmp")

        async def allow(msg: str) -> bool:
            return True

        reg.set_confirm_callback(allow)
        result = await reg.call_tool("bash", {"command": "rm nonexistent_file_12345"})
        # Should actually execute (and likely fail with "No such file")
        assert "cancelled" not in result.get("error", "")


class TestValidatePath:
    def test_valid_relative_path(self) -> None:
        resolved, error = validate_path("file.txt", "/tmp")
        assert error is None
        assert resolved.endswith("file.txt")

    def test_null_bytes_rejected(self) -> None:
        resolved, error = validate_path("file\x00.txt", "/tmp")
        assert error is not None
        assert "null" in error.lower()

    def test_blocked_path(self) -> None:
        resolved, error = validate_path("/etc/shadow", "/tmp")
        assert error is not None
        assert "denied" in error.lower()

    def test_blocked_prefix(self) -> None:
        resolved, error = validate_path("/proc/self/status", "/tmp")
        assert error is not None
        assert "denied" in error.lower()

    def test_absolute_path(self) -> None:
        with tempfile.NamedTemporaryFile() as f:
            resolved, error = validate_path(f.name, "/tmp")
            assert error is None
            assert resolved == os.path.realpath(f.name)


class TestSanitizeCommand:
    def test_valid_command(self) -> None:
        cmd, error = sanitize_command("ls -la")
        assert error is None
        assert cmd == "ls -la"

    def test_null_bytes_rejected(self) -> None:
        cmd, error = sanitize_command("ls\x00 -la")
        assert error is not None

    def test_blocked_rm_rf(self) -> None:
        cmd, error = sanitize_command("rm -rf /")
        assert error is not None
        assert "Blocked" in error

    def test_blocked_fork_bomb(self) -> None:
        cmd, error = sanitize_command(":(){:|:&};:")
        assert error is not None


class TestReadFileTool:
    @pytest.mark.asyncio
    async def test_read_file(self) -> None:
        from anteroom.tools import read

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("line one\nline two\nline three\n")
            f.flush()
            path = f.name

        try:
            read.set_working_dir(os.path.dirname(path))
            result = await read.handle(path=path)
            assert "content" in result
            assert "line one" in result["content"]
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_read_file_with_offset(self) -> None:
        from anteroom.tools import read

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("line one\nline two\nline three\n")
            f.flush()
            path = f.name

        try:
            read.set_working_dir(os.path.dirname(path))
            result = await read.handle(path=path, offset=2, limit=1)
            assert "content" in result
            assert "line two" in result["content"]
            assert "line one" not in result["content"]
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_read_nonexistent(self) -> None:
        from anteroom.tools import read

        read.set_working_dir("/tmp")
        result = await read.handle(path="/tmp/nonexistent_12345.txt")
        assert "error" in result


class TestWriteFileTool:
    @pytest.mark.asyncio
    async def test_write_file(self) -> None:
        from anteroom.tools import write

        with tempfile.TemporaryDirectory() as tmpdir:
            write.set_working_dir(tmpdir)
            path = os.path.join(tmpdir, "test_output.txt")
            result = await write.handle(path=path, content="hello world")
            assert result.get("status") == "ok"
            assert Path(path).read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_write_creates_dirs(self) -> None:
        from anteroom.tools import write

        with tempfile.TemporaryDirectory() as tmpdir:
            write.set_working_dir(tmpdir)
            path = os.path.join(tmpdir, "sub", "dir", "file.txt")
            result = await write.handle(path=path, content="nested")
            assert result.get("status") == "ok"
            assert Path(path).read_text() == "nested"


class TestEditFileTool:
    @pytest.mark.asyncio
    async def test_edit_file(self) -> None:
        from anteroom.tools import edit

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello world")
            path = f.name

        try:
            edit.set_working_dir(os.path.dirname(path))
            result = await edit.handle(path=path, old_text="hello", new_text="goodbye")
            assert result.get("status") == "ok"
            assert Path(path).read_text() == "goodbye world"
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_edit_not_found(self) -> None:
        from anteroom.tools import edit

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello world")
            path = f.name

        try:
            edit.set_working_dir(os.path.dirname(path))
            result = await edit.handle(path=path, old_text="nonexistent", new_text="replacement")
            assert "error" in result
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_edit_ambiguous(self) -> None:
        from anteroom.tools import edit

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("aaa bbb aaa")
            path = f.name

        try:
            edit.set_working_dir(os.path.dirname(path))
            result = await edit.handle(path=path, old_text="aaa", new_text="ccc")
            assert "error" in result
        finally:
            os.unlink(path)


class TestBashTool:
    @pytest.mark.asyncio
    async def test_bash_echo(self) -> None:
        from anteroom.tools import bash

        bash.set_working_dir("/tmp")
        result = await bash.handle(command="echo hello")
        assert result.get("stdout", "").strip() == "hello"
        assert result.get("exit_code") == 0

    @pytest.mark.asyncio
    async def test_bash_timeout(self) -> None:
        from anteroom.tools import bash

        bash.set_working_dir("/tmp")
        result = await bash.handle(command="sleep 10", timeout=1)
        assert "timed out" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_bash_exit_code(self) -> None:
        from anteroom.tools import bash

        bash.set_working_dir("/tmp")
        result = await bash.handle(command="exit 42")
        assert result.get("exit_code") == 42


class TestGlobTool:
    @pytest.mark.asyncio
    async def test_glob_files(self) -> None:
        from anteroom.tools import glob_tool

        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "a.txt").touch()
            Path(tmpdir, "b.py").touch()
            Path(tmpdir, "c.txt").touch()

            glob_tool.set_working_dir(tmpdir)
            result = await glob_tool.handle(pattern="*.txt")
            assert "files" in result
            filenames = [os.path.basename(f) for f in result["files"]]
            assert "a.txt" in filenames
            assert "c.txt" in filenames
            assert "b.py" not in filenames


class TestGrepTool:
    @pytest.mark.asyncio
    async def test_grep_files(self) -> None:
        from anteroom.tools import grep

        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "test.py").write_text("def hello():\n    return 42\n")
            Path(tmpdir, "other.py").write_text("x = 1\ny = 2\n")

            grep.set_working_dir(tmpdir)
            result = await grep.handle(pattern="hello")
            assert result.get("total_matches", 0) > 0
            assert "hello" in result.get("content", "")

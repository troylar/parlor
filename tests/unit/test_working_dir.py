"""Tests for working directory persistence and restoration."""

from __future__ import annotations

from unittest.mock import MagicMock

from anteroom.cli.repl import _restore_working_dir


class TestRestoreWorkingDir:
    def test_returns_current_when_no_stored_dir(self) -> None:
        conv: dict = {"id": "abc", "title": "Test"}
        registry = MagicMock()
        result = _restore_working_dir(conv, registry, "/current")
        assert result == "/current"

    def test_returns_current_when_stored_dir_is_none(self) -> None:
        conv: dict = {"id": "abc", "working_dir": None}
        registry = MagicMock()
        result = _restore_working_dir(conv, registry, "/current")
        assert result == "/current"

    def test_returns_current_when_stored_dir_missing(self) -> None:
        conv: dict = {"id": "abc", "working_dir": "/nonexistent/path/xyz"}
        registry = MagicMock()
        result = _restore_working_dir(conv, registry, "/current")
        assert result == "/current"

    def test_restores_stored_dir_when_exists(self, tmp_path: object) -> None:
        stored = str(tmp_path)
        conv: dict = {"id": "abc", "working_dir": stored}
        registry = MagicMock()

        result = _restore_working_dir(conv, registry, "/current")

        assert result == stored
        assert registry._working_dir == stored

    def test_rescopes_tool_modules(self, tmp_path: object) -> None:
        stored = str(tmp_path)
        conv: dict = {"id": "abc", "working_dir": stored}
        registry = MagicMock()

        from anteroom.tools import bash, edit, glob_tool, grep, read, write

        original_dirs = {}
        for mod in [read, write, edit, bash, glob_tool, grep]:
            original_dirs[mod] = getattr(mod, "_working_dir", None)

        try:
            _restore_working_dir(conv, registry, "/current")

            for mod in [read, write, edit, bash, glob_tool, grep]:
                assert mod._working_dir == stored, f"{mod.__name__} not re-scoped"
        finally:
            for mod, orig in original_dirs.items():
                if orig is not None:
                    mod.set_working_dir(orig)

    def test_does_not_rescope_when_dir_missing(self) -> None:
        conv: dict = {"id": "abc", "working_dir": "/nonexistent/path/xyz"}
        registry = MagicMock()

        from anteroom.tools import read

        original = read._working_dir

        _restore_working_dir(conv, registry, "/current")

        assert read._working_dir == original

    def test_blocks_sensitive_system_dirs(self) -> None:
        for path in ["/proc", "/sys", "/dev", "/proc/self", "/sys/class/net"]:
            conv: dict = {"id": "abc", "working_dir": path}
            registry = MagicMock()
            result = _restore_working_dir(conv, registry, "/current")
            assert result == "/current", f"Should block {path}"

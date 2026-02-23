"""Tests for the unified walk-up discovery module."""

from __future__ import annotations

import tempfile
from pathlib import Path

from anteroom.services.discovery import (
    find_all_project_dirs,
    find_project_dir,
    walk_up_for_dir,
    walk_up_for_file,
)


def _r(p: str | Path) -> Path:
    """Resolve path (handles macOS /var -> /private/var symlink)."""
    return Path(p).resolve()


class TestWalkUpForFile:
    def test_finds_file_in_start_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = _r(tmpdir) / "config.yaml"
            target.write_text("test")
            result = walk_up_for_file(("config.yaml",), tmpdir, stop_at_home=False)
            assert result == target

    def test_finds_file_in_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = _r(tmpdir) / "config.yaml"
            target.write_text("test")
            child = _r(tmpdir) / "subdir"
            child.mkdir()
            result = walk_up_for_file(("config.yaml",), str(child), stop_at_home=False)
            assert result == target

    def test_returns_none_when_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = walk_up_for_file(("nonexistent.yaml",), tmpdir, stop_at_home=False)
            assert result is None

    def test_priority_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            first = _r(tmpdir) / ".anteroom.md"
            first.write_text("first")
            second = _r(tmpdir) / "ANTEROOM.md"
            second.write_text("second")
            result = walk_up_for_file((".anteroom.md", "ANTEROOM.md"), tmpdir, stop_at_home=False)
            assert result == first

    def test_walks_multiple_levels(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = _r(tmpdir) / "target.txt"
            target.write_text("found")
            deep = _r(tmpdir) / "a" / "b" / "c"
            deep.mkdir(parents=True)
            result = walk_up_for_file(("target.txt",), str(deep), stop_at_home=False)
            assert result == target


class TestWalkUpForDir:
    def test_finds_dir_in_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = _r(tmpdir) / ".anteroom" / "skills"
            target.mkdir(parents=True)
            result = walk_up_for_dir((".anteroom/skills",), tmpdir, stop_at_home=False)
            assert result == target

    def test_finds_dir_in_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = _r(tmpdir) / ".anteroom" / "skills"
            target.mkdir(parents=True)
            child = _r(tmpdir) / "src" / "module"
            child.mkdir(parents=True)
            result = walk_up_for_dir((".anteroom/skills",), str(child), stop_at_home=False)
            assert result == target

    def test_returns_none_when_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = walk_up_for_dir((".anteroom/skills",), tmpdir, stop_at_home=False)
            assert result is None

    def test_claude_dir_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = _r(tmpdir) / ".claude" / "skills"
            target.mkdir(parents=True)
            result = walk_up_for_dir((".anteroom/skills", ".claude/skills"), tmpdir, stop_at_home=False)
            assert result == target

    def test_anteroom_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            anteroom = _r(tmpdir) / ".anteroom" / "skills"
            anteroom.mkdir(parents=True)
            claude = _r(tmpdir) / ".claude" / "skills"
            claude.mkdir(parents=True)
            result = walk_up_for_dir((".anteroom/skills", ".claude/skills"), tmpdir, stop_at_home=False)
            assert result == anteroom


class TestFindProjectDir:
    def test_finds_anteroom_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = _r(tmpdir) / ".anteroom" / "rules"
            target.mkdir(parents=True)
            result = find_project_dir("rules", tmpdir)
            assert result == target

    def test_finds_claude_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = _r(tmpdir) / ".claude" / "rules"
            target.mkdir(parents=True)
            result = find_project_dir("rules", tmpdir)
            assert result == target

    def test_returns_none_when_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = find_project_dir("rules", tmpdir)
            assert result is None


class TestFindAllProjectDirs:
    def test_collects_multiple_levels(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            outer = _r(tmpdir) / ".anteroom" / "rules"
            outer.mkdir(parents=True)
            inner_dir = _r(tmpdir) / "project"
            inner_dir.mkdir()
            inner = inner_dir / ".claude" / "rules"
            inner.mkdir(parents=True)

            result = find_all_project_dirs("rules", str(inner_dir), stop_at_home=False)
            assert len(result) == 2
            assert result[0] == inner
            assert result[1] == outer

    def test_empty_when_none_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = find_all_project_dirs("rules", tmpdir, stop_at_home=False)
            assert result == []

    def test_one_per_level(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(_r(tmpdir) / ".anteroom" / "rules").mkdir(parents=True)
            Path(_r(tmpdir) / ".claude" / "rules").mkdir(parents=True)
            result = find_all_project_dirs("rules", tmpdir, stop_at_home=False)
            assert len(result) == 1
            assert ".anteroom" in str(result[0])

"""Tests for anteroom.tools.path_utils — cross-platform path resolution."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from anteroom.tools.path_utils import safe_resolve, safe_resolve_pathlib


class TestSafeResolve:
    def test_collapses_dotdot(self, tmp_path: Path) -> None:
        child = tmp_path / "a" / "b"
        child.mkdir(parents=True)
        path_with_dotdot = str(child / ".." / "b")
        result = safe_resolve(path_with_dotdot)
        assert result == str(child)

    def test_absolute_path_passthrough(self, tmp_path: Path) -> None:
        target = tmp_path / "file.txt"
        target.touch()
        result = safe_resolve(str(target))
        assert os.path.isabs(result)
        assert result.endswith("file.txt")

    def test_relative_path_made_absolute(self) -> None:
        result = safe_resolve("some/relative/path")
        assert os.path.isabs(result)

    def test_normalizes_separators(self, tmp_path: Path) -> None:
        result = safe_resolve(str(tmp_path / "a" / "b"))
        assert "//" not in result or result.startswith("//")  # UNC paths may start with //

    @patch("anteroom.tools.path_utils._IS_WINDOWS", True)
    def test_windows_uses_abspath_not_realpath(self, tmp_path: Path) -> None:
        target = str(tmp_path / "test.txt")
        with (
            patch("anteroom.tools.path_utils.os.path.abspath", return_value=target) as mock_abs,
            patch("anteroom.tools.path_utils.os.path.normpath", return_value=target) as mock_norm,
            patch("anteroom.tools.path_utils.os.path.realpath") as mock_real,
        ):
            safe_resolve(target)
            mock_abs.assert_called_once_with(target)
            mock_norm.assert_called_once_with(target)
            mock_real.assert_not_called()

    @patch("anteroom.tools.path_utils._IS_WINDOWS", False)
    def test_posix_uses_realpath(self, tmp_path: Path) -> None:
        target = str(tmp_path / "test.txt")
        with patch("anteroom.tools.path_utils.os.path.realpath", return_value=target) as mock_real:
            result = safe_resolve(target)
            mock_real.assert_called_once_with(target)
            assert result == target

    @patch("anteroom.tools.path_utils._IS_WINDOWS", True)
    def test_windows_preserves_drive_letter(self) -> None:
        with (
            patch("anteroom.tools.path_utils.os.path.abspath", return_value=r"X:\test\file.txt"),
            patch("anteroom.tools.path_utils.os.path.normpath", return_value=r"X:\test\file.txt"),
        ):
            result = safe_resolve(r"X:\test\file.txt")
            assert result == r"X:\test\file.txt"

    @patch("anteroom.tools.path_utils._IS_WINDOWS", True)
    def test_windows_dotdot_traversal_blocked(self) -> None:
        with (
            patch("anteroom.tools.path_utils.os.path.abspath", return_value=r"X:\test\..\secret"),
            patch("anteroom.tools.path_utils.os.path.normpath", return_value=r"X:\secret"),
        ):
            result = safe_resolve(r"X:\test\..\secret")
            assert result == r"X:\secret"
            assert ".." not in result


class TestSafeResolvePathlib:
    def test_returns_path_object(self, tmp_path: Path) -> None:
        result = safe_resolve_pathlib(tmp_path)
        assert isinstance(result, Path)

    def test_resolves_same_as_safe_resolve(self, tmp_path: Path) -> None:
        result = safe_resolve_pathlib(tmp_path)
        assert str(result) == safe_resolve(str(tmp_path))

    def test_collapses_dotdot(self, tmp_path: Path) -> None:
        child = tmp_path / "a" / "b"
        child.mkdir(parents=True)
        path_with_dotdot = child / ".." / "b"
        result = safe_resolve_pathlib(path_with_dotdot)
        assert result == child

    @patch("anteroom.tools.path_utils._IS_WINDOWS", True)
    def test_windows_pathlib_uses_abspath(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        with (
            patch("anteroom.tools.path_utils.os.path.abspath", return_value=str(target)),
            patch("anteroom.tools.path_utils.os.path.normpath", return_value=str(target)),
            patch("anteroom.tools.path_utils.os.path.realpath") as mock_real,
        ):
            safe_resolve_pathlib(target)
            mock_real.assert_not_called()


class TestSecurityIntegration:
    """Verify that safe_resolve still supports security-critical path checks."""

    def test_dotdot_collapses_to_parent(self, tmp_path: Path) -> None:
        result = safe_resolve(str(tmp_path / "child" / ".." / ".." / "etc" / "passwd"))
        assert ".." not in result
        assert "etc" in result or "passwd" in result

    def test_is_relative_to_works_after_resolve(self, tmp_path: Path) -> None:
        child = tmp_path / "subdir" / "file.txt"
        child.parent.mkdir(parents=True)
        child.touch()
        resolved_base = safe_resolve_pathlib(tmp_path)
        resolved_child = safe_resolve_pathlib(child)
        assert resolved_child.is_relative_to(resolved_base)

    def test_escape_detected_after_resolve(self, tmp_path: Path) -> None:
        base = tmp_path / "sandbox"
        base.mkdir()
        escaped = tmp_path / "sandbox" / ".." / "outside"
        resolved_base = safe_resolve_pathlib(base)
        resolved_escaped = safe_resolve_pathlib(escaped)
        assert not resolved_escaped.is_relative_to(resolved_base)

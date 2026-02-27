"""Tests for Windows-specific bash tool rewrites (#583)."""

from __future__ import annotations

import os
from unittest.mock import patch

from anteroom.tools.bash import _resolve_python_binary, _rewrite_python_c_for_windows


class TestRewritePythonCForWindows:
    def test_multiline_rewritten_to_temp_file(self):
        cmd = 'python -c "import sys\nprint(sys.version)"'
        rewritten, tmp_path = _rewrite_python_c_for_windows(cmd)
        assert tmp_path is not None
        assert tmp_path.endswith(".py")
        assert "python" in rewritten
        assert tmp_path in rewritten
        # Verify temp file content
        with open(tmp_path) as f:
            content = f.read()
        assert "import sys" in content
        assert "print(sys.version)" in content
        os.unlink(tmp_path)

    def test_single_line_not_rewritten(self):
        cmd = 'python -c "print(42)"'
        rewritten, tmp_path = _rewrite_python_c_for_windows(cmd)
        assert tmp_path is None
        assert rewritten == cmd

    def test_non_python_command_not_rewritten(self):
        cmd = "echo hello"
        rewritten, tmp_path = _rewrite_python_c_for_windows(cmd)
        assert tmp_path is None
        assert rewritten == cmd

    def test_python3_multiline_rewritten(self):
        cmd = "python3 -c 'import os\nprint(os.getcwd())'"
        rewritten, tmp_path = _rewrite_python_c_for_windows(cmd)
        assert tmp_path is not None
        assert "python3" in rewritten
        os.unlink(tmp_path)

    def test_preserves_python_binary_name(self):
        cmd = 'python3 -c "line1\nline2"'
        rewritten, tmp_path = _rewrite_python_c_for_windows(cmd)
        assert tmp_path is not None
        assert rewritten.startswith("python3")
        os.unlink(tmp_path)


class TestResolvePythonBinary:
    @patch("sys.platform", "win32")
    @patch("shutil.which")
    def test_rewrites_python3_to_python_on_windows(self, mock_which):
        mock_which.side_effect = lambda x: None if x == "python3" else "/usr/bin/python"
        result = _resolve_python_binary("python3 script.py")
        assert result == "python script.py"

    @patch("sys.platform", "win32")
    @patch("shutil.which")
    def test_keeps_python3_when_available(self, mock_which):
        mock_which.return_value = "/usr/bin/python3"
        result = _resolve_python_binary("python3 script.py")
        assert result == "python3 script.py"

    @patch("sys.platform", "darwin")
    def test_no_rewrite_on_non_windows(self):
        result = _resolve_python_binary("python3 script.py")
        assert result == "python3 script.py"

    @patch("sys.platform", "win32")
    def test_non_python_command_unchanged(self):
        result = _resolve_python_binary("echo hello")
        assert result == "echo hello"

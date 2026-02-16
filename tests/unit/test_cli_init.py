"""Tests for anteroom init and --version."""

from __future__ import annotations

import subprocess
import sys


class TestVersion:
    def test_version_flag(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "anteroom", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "aroom" in result.stdout.lower()


class TestInit:
    def test_init_creates_config(self, tmp_path) -> None:
        config_path = tmp_path / "config.yaml"
        import stat
        from unittest.mock import patch

        import yaml

        from anteroom.__main__ import _run_init

        inputs = iter(
            [
                "https://api.openai.com/v1",
                "sk-test-key",
                "gpt-4",
                "",
            ]
        )
        with (
            patch("anteroom.__main__._get_config_path", return_value=config_path),
            patch("builtins.input", side_effect=lambda _: next(inputs)),
        ):
            _run_init()

        assert config_path.exists()
        data = yaml.safe_load(config_path.read_text())
        assert data["ai"]["base_url"] == "https://api.openai.com/v1"
        assert data["ai"]["api_key"] == "sk-test-key"
        assert data["ai"]["model"] == "gpt-4"

        # Check restricted permissions
        mode = config_path.stat().st_mode
        assert mode & stat.S_IROTH == 0
        assert mode & stat.S_IWOTH == 0

    def test_init_respects_cancel(self, tmp_path) -> None:
        config_path = tmp_path / "config.yaml"
        from unittest.mock import patch

        from anteroom.__main__ import _run_init

        with (
            patch("anteroom.__main__._get_config_path", return_value=config_path),
            patch("builtins.input", side_effect=EOFError),
        ):
            _run_init()

        assert not config_path.exists()

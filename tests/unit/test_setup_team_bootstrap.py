"""Tests for team config bootstrap in the init wizard."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml

from anteroom.cli.setup import bootstrap_team_config


def _r(p: str | Path) -> Path:
    return Path(p).resolve()


class TestBootstrapTeamConfig:
    def _make_team(self, tmpdir: Path, data: dict | None = None) -> Path:
        team = tmpdir / "team.yaml"
        team.write_text(yaml.dump(data or {"ai": {"base_url": "http://team:8080"}}))
        return team

    @patch("anteroom.cli.setup.console")
    def test_saves_team_path_in_config_data(self, _console) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            team = self._make_team(base)
            cfg = base / "config.yaml"
            config_data: dict = {"ai": {"model": "gpt-4"}}

            result = bootstrap_team_config(str(team), config_data, cfg)

            assert result is True
            assert config_data["team_config_path"] == str(team.resolve())

    @patch("anteroom.cli.setup.console")
    def test_trusts_team_config(self, _console) -> None:
        from anteroom.services.trust import check_trust, compute_content_hash

        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            team = self._make_team(base)
            cfg = base / "config.yaml"

            bootstrap_team_config(str(team), {}, cfg)

            content = team.read_text(encoding="utf-8")
            content_hash = compute_content_hash(content)
            status = check_trust(str(team.resolve()), content_hash, data_dir=base)
            assert status == "trusted"

    @patch("anteroom.cli.setup.console")
    def test_missing_file_returns_false(self, _console) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            cfg = base / "config.yaml"
            config_data: dict = {}

            result = bootstrap_team_config(str(base / "nope.yaml"), config_data, cfg)

            assert result is False
            assert "team_config_path" not in config_data

    @patch("anteroom.cli.setup.console")
    def test_prompts_for_required_keys(self, _console) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            team = self._make_team(
                base,
                {
                    "ai": {"base_url": "http://team:8080"},
                    "required": [
                        {"path": "custom.setting", "description": "A custom value"},
                    ],
                },
            )
            cfg = base / "config.yaml"

            with patch("anteroom.services.required_keys.prompt_for_missing_keys") as mock_prompt:
                mock_prompt.return_value = True
                bootstrap_team_config(str(team), {}, cfg)

                mock_prompt.assert_called_once()
                missing = mock_prompt.call_args[0][0]
                assert len(missing) == 1
                assert missing[0]["path"] == "custom.setting"

    @patch("anteroom.cli.setup.console")
    def test_skips_prompt_when_keys_satisfied(self, _console) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            team = self._make_team(
                base,
                {
                    "required": [{"path": "ai.model", "description": "Model"}],
                },
            )
            cfg = base / "config.yaml"
            config_data = {"ai": {"model": "gpt-4"}}

            with patch("anteroom.services.required_keys.prompt_for_missing_keys") as mock_prompt:
                bootstrap_team_config(str(team), config_data, cfg)
                mock_prompt.assert_not_called()

    @patch("anteroom.cli.setup.console")
    def test_no_required_section(self, _console) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            team = self._make_team(base, {"ai": {"model": "llama3"}})
            cfg = base / "config.yaml"

            result = bootstrap_team_config(str(team), {}, cfg)
            assert result is True

    @patch("anteroom.cli.setup.console")
    def test_invalid_required_entries_filtered(self, _console) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            team = self._make_team(
                base,
                {
                    "required": [
                        {"path": "ai.api_key", "description": "Valid"},
                        "just a string",
                        {"no_path": True},
                    ],
                },
            )
            cfg = base / "config.yaml"

            with patch("anteroom.services.required_keys.prompt_for_missing_keys") as mock_prompt:
                mock_prompt.return_value = True
                bootstrap_team_config(str(team), {}, cfg)

                missing = mock_prompt.call_args[0][0]
                assert len(missing) == 1
                assert missing[0]["path"] == "ai.api_key"

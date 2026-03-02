"""Tests for project-scoped configuration.

Covers discover_project_config, load_project_config, and _prompt_trust.
Related to issue #689 (test coverage >= 80%).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from anteroom.services.project_config import _prompt_trust, discover_project_config, load_project_config


def _r(p: str | Path) -> Path:
    return Path(p).resolve()


def _trust_config(path: Path, data_dir: Path) -> None:
    """Pre-trust a config file in the trust store."""
    from anteroom.services.trust import compute_content_hash, save_trust_decision

    content = path.read_text(encoding="utf-8")
    content_hash = compute_content_hash(content)
    save_trust_decision(str(path.resolve()), content_hash, recursive=False, data_dir=data_dir)


# ---------------------------------------------------------------------------
# discover_project_config
# ---------------------------------------------------------------------------


class TestDiscoverProjectConfig:
    def test_finds_anteroom_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _r(tmpdir) / ".anteroom" / "config.yaml"
            cfg.parent.mkdir(parents=True)
            cfg.write_text("ai:\n  model: gpt-4\n")
            result = discover_project_config(tmpdir)
            assert result == cfg

    def test_finds_claude_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _r(tmpdir) / ".claude" / "config.yaml"
            cfg.parent.mkdir(parents=True)
            cfg.write_text("ai:\n  model: gpt-4\n")
            result = discover_project_config(tmpdir)
            assert result == cfg

    def test_finds_parlor_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".parlor" / "config.yaml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("ai:\n  model: gpt-4\n")
        result = discover_project_config(tmp_path)
        assert result == cfg

    def test_anteroom_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            anteroom = _r(tmpdir) / ".anteroom" / "config.yaml"
            anteroom.parent.mkdir(parents=True)
            anteroom.write_text("ai:\n  model: anteroom\n")
            claude = _r(tmpdir) / ".claude" / "config.yaml"
            claude.parent.mkdir(parents=True)
            claude.write_text("ai:\n  model: claude\n")
            result = discover_project_config(tmpdir)
            assert result == anteroom

    def test_walks_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _r(tmpdir) / ".anteroom" / "config.yaml"
            cfg.parent.mkdir(parents=True)
            cfg.write_text("ai:\n  model: gpt-4\n")
            child = _r(tmpdir) / "src" / "module"
            child.mkdir(parents=True)
            result = discover_project_config(str(child))
            assert result == cfg

    def test_returns_none_when_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = discover_project_config(tmpdir)
            assert result is None

    def test_default_start_is_cwd(self) -> None:
        # Should not raise even when called with no args
        result = discover_project_config()
        # Result may or may not be None depending on the repo layout; just verify it's Path or None
        assert result is None or isinstance(result, Path)


# ---------------------------------------------------------------------------
# load_project_config
# ---------------------------------------------------------------------------


class TestLoadProjectConfig:
    def _setup(self, tmpdir: str) -> tuple[Path, Path]:
        """Create data dir and project config dir, return (data_dir, proj_dir)."""
        data_dir = _r(tmpdir) / "data"
        data_dir.mkdir(parents=True)
        proj_dir = _r(tmpdir) / "project" / ".anteroom"
        proj_dir.mkdir(parents=True)
        return data_dir, proj_dir

    def test_loads_valid_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text("ai:\n  model: llama3\n")
            _trust_config(cfg, data_dir)

            raw, required = load_project_config(cfg, data_dir)
            assert raw["ai"]["model"] == "llama3"
            assert required == []

    def test_extracts_required_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text(
                yaml.dump(
                    {
                        "required": [
                            {"path": "ai.api_key", "description": "Your API key"},
                            {"path": "ai.base_url", "description": "API endpoint"},
                        ],
                    }
                )
            )
            _trust_config(cfg, data_dir)

            raw, required = load_project_config(cfg, data_dir)
            assert len(required) == 2
            assert required[0]["path"] == "ai.api_key"
            assert required[0]["description"] == "Your API key"
            assert "required" not in raw

    def test_skips_untrusted_non_interactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text("ai:\n  model: llama3\n")

            raw, required = load_project_config(cfg, data_dir, interactive=False)
            assert raw == {}
            assert required == []

    def test_skips_invalid_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text("not: valid: yaml: [[[")
            _trust_config(cfg, data_dir)

            raw, required = load_project_config(cfg, data_dir)
            assert raw == {}
            assert required == []

    def test_skips_non_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text("- just\n- a\n- list\n")
            _trust_config(cfg, data_dir)

            raw, required = load_project_config(cfg, data_dir)
            assert raw == {}

    def test_invalid_required_entries_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text(
                yaml.dump(
                    {
                        "required": [
                            {"path": "ai.api_key", "description": "Valid"},
                            "just a string",
                            {"no_path_key": True},
                        ],
                    }
                )
            )
            _trust_config(cfg, data_dir)

            raw, required = load_project_config(cfg, data_dir)
            assert len(required) == 1
            assert required[0]["path"] == "ai.api_key"

    def test_oserror_reading_file_returns_empty(self, tmp_path: Path) -> None:
        """OSError when reading file returns ({}, [])."""
        nonexistent = tmp_path / "nonexistent" / "config.yaml"
        raw, required = load_project_config(nonexistent)
        assert raw == {}
        assert required == []

    def test_required_not_a_list_treated_as_empty(self) -> None:
        """If `required` is not a list (e.g. a string), it should be treated as empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text(yaml.dump({"required": "not-a-list", "ai": {"model": "gpt-4"}}))
            _trust_config(cfg, data_dir)

            raw, required = load_project_config(cfg, data_dir)
            assert required == []
            assert raw["ai"]["model"] == "gpt-4"

    def test_validation_warnings_are_logged(self, tmp_path: Path) -> None:
        """Config with validation warnings should still load; warnings should be logged."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        proj_dir = tmp_path / "project" / ".anteroom"
        proj_dir.mkdir(parents=True)
        cfg = proj_dir / "config.yaml"
        # Use an unknown top-level key to trigger a validation warning
        cfg.write_text(yaml.dump({"unknown_key_xyz": "value", "ai": {"model": "gpt-4"}}))
        _trust_config(cfg, data_dir)

        mock_result = MagicMock()
        mock_result.has_warnings = True
        warning_error = MagicMock()
        warning_error.severity = "warning"
        warning_error.path = "unknown_key_xyz"
        warning_error.message = "unknown key"
        mock_result.errors = [warning_error]

        with patch("anteroom.services.config_validator.validate_config", return_value=mock_result):
            import logging

            with patch.object(logging.getLogger("anteroom.services.project_config"), "warning") as mock_warn:
                raw, _ = load_project_config(cfg, data_dir)
                mock_warn.assert_called()

    def test_untrusted_interactive_user_accepts(self) -> None:
        """Interactive mode: untrusted file + user says yes => loads config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text("ai:\n  model: accepted\n")
            # Do NOT pre-trust — status will be "untrusted"

            with patch("anteroom.services.project_config._prompt_trust", return_value=True):
                raw, required = load_project_config(cfg, data_dir, interactive=True)

            assert raw.get("ai", {}).get("model") == "accepted"
            assert required == []

    def test_untrusted_interactive_user_declines(self) -> None:
        """Interactive mode: untrusted file + user says no => returns ({}, [])."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text("ai:\n  model: declined\n")

            with patch("anteroom.services.project_config._prompt_trust", return_value=False):
                raw, required = load_project_config(cfg, data_dir, interactive=True)

            assert raw == {}
            assert required == []

    def test_changed_non_interactive_returns_empty(self) -> None:
        """Non-interactive mode: changed (hash mismatch) file => returns ({}, [])."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text("ai:\n  model: original\n")
            _trust_config(cfg, data_dir)

            # Modify content so hash no longer matches — status becomes "changed"
            cfg.write_text("ai:\n  model: modified\n")

            raw, required = load_project_config(cfg, data_dir, interactive=False)
            assert raw == {}
            assert required == []

    def test_changed_interactive_user_accepts(self) -> None:
        """Interactive mode: changed file + user accepts => loads updated config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text("ai:\n  model: original\n")
            _trust_config(cfg, data_dir)

            cfg.write_text("ai:\n  model: updated\n")

            with patch("anteroom.services.project_config._prompt_trust", return_value=True):
                raw, required = load_project_config(cfg, data_dir, interactive=True)

            assert raw.get("ai", {}).get("model") == "updated"

    def test_changed_interactive_user_declines(self) -> None:
        """Interactive mode: changed file + user declines => returns ({}, [])."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text("ai:\n  model: original\n")
            _trust_config(cfg, data_dir)

            cfg.write_text("ai:\n  model: updated\n")

            with patch("anteroom.services.project_config._prompt_trust", return_value=False):
                raw, required = load_project_config(cfg, data_dir, interactive=True)

            assert raw == {}
            assert required == []

    def test_required_entry_without_description_defaults_to_empty_string(self) -> None:
        """Required entry with only 'path' key gets empty description."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text(yaml.dump({"required": [{"path": "ai.api_key"}]}))
            _trust_config(cfg, data_dir)

            raw, required = load_project_config(cfg, data_dir)
            assert len(required) == 1
            assert required[0]["path"] == "ai.api_key"
            assert required[0]["description"] == ""


# ---------------------------------------------------------------------------
# _prompt_trust
# ---------------------------------------------------------------------------


class TestPromptTrust:
    def test_returns_false_when_not_a_tty(self, tmp_path: Path) -> None:
        """Non-tty stdin always returns False."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("")
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            result = _prompt_trust(cfg, is_changed=False)
        assert result is False

    def test_returns_false_when_not_a_tty_changed(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text("")
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            result = _prompt_trust(cfg, is_changed=True)
        assert result is False

    def test_returns_true_on_yes_answer_new_file(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        with patch("sys.stdin") as mock_stdin, patch("builtins.input", return_value="y"):
            mock_stdin.isatty.return_value = True
            result = _prompt_trust(cfg, is_changed=False)
        assert result is True

    def test_returns_true_on_yes_full_word(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        with patch("sys.stdin") as mock_stdin, patch("builtins.input", return_value="yes"):
            mock_stdin.isatty.return_value = True
            result = _prompt_trust(cfg, is_changed=False)
        assert result is True

    def test_returns_false_on_no_answer(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        with patch("sys.stdin") as mock_stdin, patch("builtins.input", return_value="n"):
            mock_stdin.isatty.return_value = True
            result = _prompt_trust(cfg, is_changed=False)
        assert result is False

    def test_returns_false_on_empty_answer(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        with patch("sys.stdin") as mock_stdin, patch("builtins.input", return_value=""):
            mock_stdin.isatty.return_value = True
            result = _prompt_trust(cfg, is_changed=False)
        assert result is False

    def test_prompt_message_for_changed_file(self, tmp_path: Path) -> None:
        """Changed file uses different prompt message."""
        cfg = tmp_path / "config.yaml"
        captured_prompt: list[str] = []

        def fake_input(msg: str) -> str:
            captured_prompt.append(msg)
            return "y"

        with patch("sys.stdin") as mock_stdin, patch("builtins.input", side_effect=fake_input):
            mock_stdin.isatty.return_value = True
            result = _prompt_trust(cfg, is_changed=True)

        assert result is True
        assert "changed" in captured_prompt[0].lower() or "updated" in captured_prompt[0].lower()

    def test_prompt_message_for_new_file(self, tmp_path: Path) -> None:
        """New (untrusted) file uses found/trust prompt message."""
        cfg = tmp_path / "config.yaml"
        captured_prompt: list[str] = []

        def fake_input(msg: str) -> str:
            captured_prompt.append(msg)
            return "y"

        with patch("sys.stdin") as mock_stdin, patch("builtins.input", side_effect=fake_input):
            mock_stdin.isatty.return_value = True
            result = _prompt_trust(cfg, is_changed=False)

        assert result is True
        assert "trust" in captured_prompt[0].lower() or "found" in captured_prompt[0].lower()

    def test_returns_false_on_eoferror(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        with patch("sys.stdin") as mock_stdin, patch("builtins.input", side_effect=EOFError):
            mock_stdin.isatty.return_value = True
            result = _prompt_trust(cfg, is_changed=False)
        assert result is False

    def test_returns_false_on_keyboard_interrupt(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        with patch("sys.stdin") as mock_stdin, patch("builtins.input", side_effect=KeyboardInterrupt):
            mock_stdin.isatty.return_value = True
            result = _prompt_trust(cfg, is_changed=True)
        assert result is False

    def test_case_insensitive_yes(self, tmp_path: Path) -> None:
        """Uppercase Y/YES should also be accepted."""
        cfg = tmp_path / "config.yaml"
        for answer in ("Y", "YES", "Yes"):
            with patch("sys.stdin") as mock_stdin, patch("builtins.input", return_value=answer):
                mock_stdin.isatty.return_value = True
                result = _prompt_trust(cfg, is_changed=False)
            assert result is True, f"Expected True for answer {answer!r}"


# ---------------------------------------------------------------------------
# Integration: load_config using project_config_path
# ---------------------------------------------------------------------------


class TestProjectConfigIntegration:
    """Integration tests: load_config uses path.parent as the trust store data_dir."""

    def test_project_config_overlays_personal(self) -> None:
        from anteroom.config import load_config

        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)

            personal = base / "config.yaml"
            personal.write_text(
                yaml.dump(
                    {
                        "ai": {"base_url": "http://personal:8080", "api_key": "personal-key", "model": "gpt-4"},
                    }
                )
            )

            proj_dir = base / "project" / ".anteroom"
            proj_dir.mkdir(parents=True)
            proj_cfg = proj_dir / "config.yaml"
            proj_cfg.write_text(
                yaml.dump(
                    {
                        "ai": {"model": "llama3"},
                    }
                )
            )
            _trust_config(proj_cfg, base)

            cfg, _ = load_config(
                personal,
                project_config_path=proj_cfg,
            )
            assert cfg.ai.model == "llama3"
            assert cfg.ai.base_url == "http://personal:8080"

    def test_team_enforcement_overrides_project(self) -> None:
        from anteroom.config import load_config
        from anteroom.services.trust import compute_content_hash, save_trust_decision

        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)

            team_path = base / "team.yaml"
            team_content = yaml.dump(
                {
                    "ai": {"base_url": "http://team:8080", "api_key": "team-key"},
                    "enforce": ["ai.base_url"],
                }
            )
            team_path.write_text(team_content)
            team_hash = compute_content_hash(team_content)
            save_trust_decision(str(team_path), team_hash, recursive=False, data_dir=base)

            personal = base / "config.yaml"
            personal.write_text(
                yaml.dump(
                    {
                        "ai": {"base_url": "http://personal:8080", "api_key": "personal-key"},
                    }
                )
            )

            proj_dir = base / "project" / ".anteroom"
            proj_dir.mkdir(parents=True)
            proj_cfg = proj_dir / "config.yaml"
            proj_cfg.write_text(
                yaml.dump(
                    {
                        "ai": {"base_url": "http://project:9090", "model": "llama3"},
                    }
                )
            )
            _trust_config(proj_cfg, base)

            cfg, enforced = load_config(
                personal,
                team_config_path=team_path,
                project_config_path=proj_cfg,
                interactive=False,
            )
            assert cfg.ai.base_url == "http://team:8080"
            assert cfg.ai.model == "llama3"
            assert "ai.base_url" in enforced

    def test_mcp_servers_from_project(self) -> None:
        from anteroom.config import load_config

        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)

            personal = base / "config.yaml"
            personal.write_text(
                yaml.dump(
                    {
                        "ai": {"base_url": "http://localhost:8080", "api_key": "key"},
                        "mcp_servers": [
                            {"name": "global-fs", "transport": "stdio", "command": "npx"},
                        ],
                    }
                )
            )

            proj_dir = base / "project" / ".anteroom"
            proj_dir.mkdir(parents=True)
            proj_cfg = proj_dir / "config.yaml"
            proj_cfg.write_text(
                yaml.dump(
                    {
                        "mcp_servers": [
                            {"name": "project-db", "transport": "stdio", "command": "db-tool"},
                        ],
                    }
                )
            )
            _trust_config(proj_cfg, base)

            cfg, _ = load_config(
                personal,
                project_config_path=proj_cfg,
            )
            names = [s.name for s in cfg.mcp_servers]
            assert "project-db" in names

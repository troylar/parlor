"""Tests for team configuration discovery, loading, merging, and enforcement."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from anteroom.services.team_config import (
    _MISSING,
    _SAFE_DOT_PATH,
    _resolve_dot_path,
    _set_dot_path,
    _walk_up_for_team_config,
    apply_enforcement,
    deep_merge,
    discover_team_config,
    load_team_config,
)


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# discover_team_config
# ---------------------------------------------------------------------------


class TestDiscoverTeamConfig:
    def test_cli_path_takes_priority(self, tmp_path: Path) -> None:
        team_file = tmp_path / "team.yaml"
        _write_yaml(team_file, {"ai": {"base_url": "https://team.example.com"}})

        result = discover_team_config(cli_path=str(team_file))
        assert result == team_file.resolve()

    def test_cli_path_missing_returns_none(self, tmp_path: Path) -> None:
        result = discover_team_config(cli_path=str(tmp_path / "nope.yaml"))
        assert result is None

    def test_env_path_used_when_no_cli(self, tmp_path: Path) -> None:
        team_file = tmp_path / "team.yaml"
        _write_yaml(team_file, {"ai": {"base_url": "https://env.example.com"}})

        result = discover_team_config(env_path=str(team_file))
        assert result == team_file.resolve()

    def test_env_path_missing_returns_none(self, tmp_path: Path) -> None:
        result = discover_team_config(env_path="/nonexistent/team.yaml")
        assert result is None

    def test_personal_path_used_when_no_env(self, tmp_path: Path) -> None:
        team_file = tmp_path / "team.yaml"
        _write_yaml(team_file, {"ai": {"base_url": "https://personal.example.com"}})

        result = discover_team_config(personal_path=str(team_file))
        assert result == team_file.resolve()

    def test_personal_path_missing_returns_none(self, tmp_path: Path) -> None:
        result = discover_team_config(personal_path="/nonexistent/team.yaml")
        assert result is None

    def test_priority_order_cli_over_env(self, tmp_path: Path) -> None:
        cli_file = tmp_path / "cli.yaml"
        env_file = tmp_path / "env.yaml"
        _write_yaml(cli_file, {"ai": {"base_url": "cli"}})
        _write_yaml(env_file, {"ai": {"base_url": "env"}})

        result = discover_team_config(cli_path=str(cli_file), env_path=str(env_file))
        assert result == cli_file.resolve()

    def test_no_config_found_returns_none(self, tmp_path: Path) -> None:
        result = discover_team_config(cwd=str(tmp_path))
        assert result is None


# ---------------------------------------------------------------------------
# _walk_up_for_team_config
# ---------------------------------------------------------------------------


class TestWalkUpDiscovery:
    def test_finds_anteroom_dir_team_yaml(self, tmp_path: Path) -> None:
        team_dir = tmp_path / ".anteroom"
        team_dir.mkdir()
        team_file = team_dir / "team.yaml"
        _write_yaml(team_file, {"ai": {"base_url": "found"}})

        result = _walk_up_for_team_config(tmp_path)
        assert result == team_file

    def test_finds_flat_team_yaml(self, tmp_path: Path) -> None:
        team_file = tmp_path / "anteroom.team.yaml"
        _write_yaml(team_file, {"ai": {"base_url": "found"}})

        result = _walk_up_for_team_config(tmp_path)
        assert result == team_file

    def test_prefers_anteroom_dir_over_flat(self, tmp_path: Path) -> None:
        dir_file = tmp_path / ".anteroom" / "team.yaml"
        dir_file.parent.mkdir()
        _write_yaml(dir_file, {"ai": {"base_url": "dir"}})
        flat_file = tmp_path / "anteroom.team.yaml"
        _write_yaml(flat_file, {"ai": {"base_url": "flat"}})

        result = _walk_up_for_team_config(tmp_path)
        assert result == dir_file

    def test_walks_up_to_parent(self, tmp_path: Path) -> None:
        child = tmp_path / "project" / "src"
        child.mkdir(parents=True)
        team_file = tmp_path / "anteroom.team.yaml"
        _write_yaml(team_file, {"ai": {"base_url": "parent"}})

        result = _walk_up_for_team_config(child)
        assert result == team_file

    def test_returns_none_when_nothing_found(self, tmp_path: Path) -> None:
        result = _walk_up_for_team_config(tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# deep_merge
# ---------------------------------------------------------------------------


class TestDeepMerge:
    def test_scalar_overlay_wins(self) -> None:
        base = {"a": 1, "b": 2}
        overlay = {"b": 3, "c": 4}
        result = deep_merge(base, overlay)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_dicts_merged(self) -> None:
        base = {"ai": {"base_url": "team", "model": "gpt-4"}}
        overlay = {"ai": {"model": "gpt-4o"}}
        result = deep_merge(base, overlay)
        assert result == {"ai": {"base_url": "team", "model": "gpt-4o"}}

    def test_lists_replaced_not_appended(self) -> None:
        base = {"safety": {"denied_tools": ["bash", "rm"]}}
        overlay = {"safety": {"denied_tools": ["bash"]}}
        result = deep_merge(base, overlay)
        assert result == {"safety": {"denied_tools": ["bash"]}}

    def test_empty_overlay(self) -> None:
        base = {"a": 1}
        result = deep_merge(base, {})
        assert result == {"a": 1}

    def test_empty_base(self) -> None:
        overlay = {"a": 1}
        result = deep_merge({}, overlay)
        assert result == {"a": 1}

    def test_does_not_mutate_inputs(self) -> None:
        base = {"ai": {"model": "gpt-4"}}
        overlay = {"ai": {"model": "gpt-4o"}}
        deep_merge(base, overlay)
        assert base == {"ai": {"model": "gpt-4"}}
        assert overlay == {"ai": {"model": "gpt-4o"}}

    def test_deeply_nested(self) -> None:
        base = {"a": {"b": {"c": 1, "d": 2}}}
        overlay = {"a": {"b": {"d": 3, "e": 4}}}
        result = deep_merge(base, overlay)
        assert result == {"a": {"b": {"c": 1, "d": 3, "e": 4}}}


# ---------------------------------------------------------------------------
# _resolve_dot_path / _set_dot_path
# ---------------------------------------------------------------------------


class TestDotPath:
    def test_resolve_simple(self) -> None:
        raw = {"ai": {"base_url": "https://example.com"}}
        assert _resolve_dot_path(raw, "ai.base_url") == "https://example.com"

    def test_resolve_top_level(self) -> None:
        raw = {"mcp_tool_warning_threshold": 40}
        assert _resolve_dot_path(raw, "mcp_tool_warning_threshold") == 40

    def test_resolve_missing_returns_sentinel(self) -> None:
        raw = {"ai": {"model": "gpt-4"}}
        result = _resolve_dot_path(raw, "ai.nonexistent")
        assert isinstance(result, type(_MISSING))

    def test_resolve_partial_path_returns_sentinel(self) -> None:
        raw = {"ai": "not_a_dict"}
        result = _resolve_dot_path(raw, "ai.base_url")
        assert isinstance(result, type(_MISSING))

    def test_set_simple(self) -> None:
        raw: dict = {"ai": {"model": "gpt-4"}}
        _set_dot_path(raw, "ai.model", "gpt-4o")
        assert raw["ai"]["model"] == "gpt-4o"

    def test_set_creates_intermediate_dicts(self) -> None:
        raw: dict = {}
        _set_dot_path(raw, "ai.base_url", "https://new.example.com")
        assert raw == {"ai": {"base_url": "https://new.example.com"}}


# ---------------------------------------------------------------------------
# apply_enforcement
# ---------------------------------------------------------------------------


class TestApplyEnforcement:
    def test_enforces_overridden_value(self) -> None:
        team_raw = {"ai": {"base_url": "https://team.example.com"}}
        merged = {"ai": {"base_url": "https://personal.example.com", "model": "gpt-4"}}
        result = apply_enforcement(merged, team_raw, ["ai.base_url"])
        assert result["ai"]["base_url"] == "https://team.example.com"
        assert result["ai"]["model"] == "gpt-4"

    def test_no_op_when_values_match(self) -> None:
        team_raw = {"ai": {"base_url": "https://team.example.com"}}
        merged = {"ai": {"base_url": "https://team.example.com"}}
        result = apply_enforcement(merged, team_raw, ["ai.base_url"])
        assert result["ai"]["base_url"] == "https://team.example.com"

    def test_skips_missing_enforce_path(self) -> None:
        team_raw = {"ai": {"model": "gpt-4"}}
        merged = {"ai": {"model": "gpt-4o", "base_url": "https://example.com"}}
        result = apply_enforcement(merged, team_raw, ["ai.nonexistent"])
        assert result["ai"]["model"] == "gpt-4o"

    def test_empty_enforce_list(self) -> None:
        team_raw = {"ai": {"base_url": "team"}}
        merged = {"ai": {"base_url": "personal"}}
        result = apply_enforcement(merged, team_raw, [])
        assert result["ai"]["base_url"] == "personal"

    def test_enforces_list_value(self) -> None:
        team_raw = {"safety": {"denied_tools": ["bash", "rm"]}}
        merged = {"safety": {"denied_tools": ["bash"]}}
        result = apply_enforcement(merged, team_raw, ["safety.denied_tools"])
        assert result["safety"]["denied_tools"] == ["bash", "rm"]

    def test_enforces_multiple_fields(self) -> None:
        team_raw = {"ai": {"base_url": "team", "model": "team-model"}}
        merged = {"ai": {"base_url": "personal", "model": "personal-model"}}
        result = apply_enforcement(merged, team_raw, ["ai.base_url", "ai.model"])
        assert result["ai"]["base_url"] == "team"
        assert result["ai"]["model"] == "team-model"


# ---------------------------------------------------------------------------
# load_team_config
# ---------------------------------------------------------------------------


class TestLoadTeamConfig:
    def test_loads_trusted_file(self, tmp_path: Path) -> None:
        team_file = tmp_path / "team.yaml"
        _write_yaml(
            team_file,
            {
                "ai": {"base_url": "https://team.example.com"},
                "enforce": ["ai.base_url"],
            },
        )

        with patch("anteroom.services.trust.check_trust", return_value="trusted"):
            raw, enforced = load_team_config(team_file, tmp_path)

        assert raw["ai"]["base_url"] == "https://team.example.com"
        assert enforced == ["ai.base_url"]
        assert "enforce" not in raw

    def test_untrusted_file_returns_empty_noninteractive(self, tmp_path: Path) -> None:
        team_file = tmp_path / "team.yaml"
        _write_yaml(team_file, {"ai": {"base_url": "https://team.example.com"}})

        with patch("anteroom.services.trust.check_trust", return_value="untrusted"):
            raw, enforced = load_team_config(team_file, tmp_path, interactive=False)

        assert raw == {}
        assert enforced == []

    def test_changed_file_returns_empty_noninteractive(self, tmp_path: Path) -> None:
        team_file = tmp_path / "team.yaml"
        _write_yaml(team_file, {"ai": {"base_url": "https://team.example.com"}})

        with patch("anteroom.services.trust.check_trust", return_value="changed"):
            raw, enforced = load_team_config(team_file, tmp_path, interactive=False)

        assert raw == {}
        assert enforced == []

    def test_untrusted_file_interactive_accept(self, tmp_path: Path) -> None:
        team_file = tmp_path / "team.yaml"
        _write_yaml(team_file, {"ai": {"base_url": "https://team.example.com"}})

        with (
            patch("anteroom.services.trust.check_trust", return_value="untrusted"),
            patch("sys.stdin") as mock_stdin,
            patch("builtins.input", return_value="y"),
            patch("anteroom.services.trust.save_trust_decision"),
        ):
            mock_stdin.isatty.return_value = True
            raw, enforced = load_team_config(team_file, tmp_path, interactive=True)

        assert raw["ai"]["base_url"] == "https://team.example.com"

    def test_untrusted_file_interactive_decline(self, tmp_path: Path) -> None:
        team_file = tmp_path / "team.yaml"
        _write_yaml(team_file, {"ai": {"base_url": "https://team.example.com"}})

        with (
            patch("anteroom.services.trust.check_trust", return_value="untrusted"),
            patch("sys.stdin") as mock_stdin,
            patch("builtins.input", return_value="n"),
        ):
            mock_stdin.isatty.return_value = True
            raw, enforced = load_team_config(team_file, tmp_path, interactive=True)

        assert raw == {}
        assert enforced == []

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.yaml"
        raw, enforced = load_team_config(missing, tmp_path)
        assert raw == {}
        assert enforced == []

    def test_invalid_yaml_returns_empty(self, tmp_path: Path) -> None:
        team_file = tmp_path / "team.yaml"
        team_file.write_text("{\n  invalid:\n    - [unclosed", encoding="utf-8")

        with patch("anteroom.services.trust.check_trust", return_value="trusted"):
            raw, enforced = load_team_config(team_file, tmp_path)

        assert raw == {}
        assert enforced == []

    def test_non_dict_yaml_returns_empty(self, tmp_path: Path) -> None:
        team_file = tmp_path / "team.yaml"
        team_file.write_text("- just\n- a\n- list\n", encoding="utf-8")

        with patch("anteroom.services.trust.check_trust", return_value="trusted"):
            raw, enforced = load_team_config(team_file, tmp_path)

        assert raw == {}
        assert enforced == []

    def test_enforce_not_list_ignored(self, tmp_path: Path) -> None:
        team_file = tmp_path / "team.yaml"
        _write_yaml(
            team_file,
            {
                "ai": {"base_url": "https://team.example.com"},
                "enforce": "not_a_list",
            },
        )

        with patch("anteroom.services.trust.check_trust", return_value="trusted"):
            raw, enforced = load_team_config(team_file, tmp_path)

        assert enforced == []


# ---------------------------------------------------------------------------
# Integration: load_config with team config
# ---------------------------------------------------------------------------


class TestTeamConfigIntegration:
    def _write_config(self, path: Path, data: dict) -> Path:
        config_file = path / "config.yaml"
        _write_yaml(config_file, data)
        return config_file

    def test_team_config_merged_into_personal(self, tmp_path: Path) -> None:
        from anteroom.config import load_config

        # Personal config
        config_file = self._write_config(
            tmp_path,
            {
                "ai": {"base_url": "https://personal.example.com", "api_key": "sk-personal", "model": "gpt-4o"},
            },
        )

        # Team config
        team_file = tmp_path / "team.yaml"
        _write_yaml(
            team_file,
            {
                "ai": {"base_url": "https://team.example.com"},
                "safety": {"approval_mode": "ask"},
            },
        )

        with patch("anteroom.services.trust.check_trust", return_value="trusted"):
            config, enforced = load_config(config_file, team_config_path=team_file)

        # Personal wins for non-enforced (model stays gpt-4o, base_url overridden by personal)
        assert config.ai.base_url == "https://personal.example.com"
        assert config.ai.model == "gpt-4o"
        # Team provides safety.approval_mode (not in personal config)
        assert config.safety.approval_mode == "ask"

    def test_enforced_fields_override_personal(self, tmp_path: Path) -> None:
        from anteroom.config import load_config

        config_file = self._write_config(
            tmp_path,
            {
                "ai": {"base_url": "https://personal.example.com", "api_key": "sk-personal"},
            },
        )

        team_file = tmp_path / "team.yaml"
        _write_yaml(
            team_file,
            {
                "ai": {"base_url": "https://team.example.com"},
                "enforce": ["ai.base_url"],
            },
        )

        with patch("anteroom.services.trust.check_trust", return_value="trusted"):
            config, enforced = load_config(config_file, team_config_path=team_file)

        assert config.ai.base_url == "https://team.example.com"
        assert enforced == ["ai.base_url"]

    def test_no_team_config_returns_empty_enforced(self, tmp_path: Path) -> None:
        from anteroom.config import load_config

        config_file = self._write_config(
            tmp_path,
            {
                "ai": {"base_url": "https://example.com", "api_key": "sk-test"},
            },
        )

        config, enforced = load_config(config_file)
        assert enforced == []
        assert config.ai.base_url == "https://example.com"

    def test_team_config_via_personal_field(self, tmp_path: Path) -> None:
        from anteroom.config import load_config

        team_file = tmp_path / "team.yaml"
        _write_yaml(
            team_file,
            {
                "ai": {"base_url": "https://team.example.com"},
                "enforce": ["ai.base_url"],
            },
        )

        config_file = self._write_config(
            tmp_path,
            {
                "ai": {"base_url": "https://personal.example.com", "api_key": "sk-personal"},
                "team_config_path": str(team_file),
            },
        )

        with patch("anteroom.services.trust.check_trust", return_value="trusted"):
            config, enforced = load_config(config_file)

        assert config.ai.base_url == "https://team.example.com"

    def test_team_config_via_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from anteroom.config import load_config

        team_file = tmp_path / "team.yaml"
        _write_yaml(
            team_file,
            {
                "ai": {"base_url": "https://team-env.example.com"},
                "enforce": ["ai.base_url"],
            },
        )

        config_file = self._write_config(
            tmp_path,
            {
                "ai": {"base_url": "https://personal.example.com", "api_key": "sk-personal"},
            },
        )

        monkeypatch.setenv("AI_CHAT_TEAM_CONFIG", str(team_file))

        with patch("anteroom.services.trust.check_trust", return_value="trusted"):
            config, enforced = load_config(config_file)

        assert config.ai.base_url == "https://team-env.example.com"


# ---------------------------------------------------------------------------
# Dot-path validation
# ---------------------------------------------------------------------------


class TestDotPathValidation:
    def test_valid_single_segment(self) -> None:
        assert _SAFE_DOT_PATH.match("model")

    def test_valid_two_segments(self) -> None:
        assert _SAFE_DOT_PATH.match("ai.base_url")

    def test_valid_four_segments(self) -> None:
        assert _SAFE_DOT_PATH.match("a.b.c.d")

    def test_rejects_five_segments(self) -> None:
        assert not _SAFE_DOT_PATH.match("a.b.c.d.e")

    def test_rejects_uppercase(self) -> None:
        assert not _SAFE_DOT_PATH.match("AI.base_url")

    def test_rejects_special_chars(self) -> None:
        assert not _SAFE_DOT_PATH.match("ai.base-url")
        assert not _SAFE_DOT_PATH.match("ai.base url")
        assert not _SAFE_DOT_PATH.match("ai.base/url")

    def test_rejects_empty(self) -> None:
        assert not _SAFE_DOT_PATH.match("")

    def test_rejects_leading_dot(self) -> None:
        assert not _SAFE_DOT_PATH.match(".ai.model")

    def test_load_filters_invalid_enforce_paths(self, tmp_path: Path) -> None:
        team_file = tmp_path / "team.yaml"
        _write_yaml(
            team_file,
            {
                "ai": {"base_url": "https://team.example.com"},
                "enforce": ["ai.base_url", "INVALID.PATH", "a.b.c.d.e.f", "valid_key"],
            },
        )

        with patch("anteroom.services.trust.check_trust", return_value="trusted"):
            raw, enforced = load_team_config(team_file, tmp_path)

        assert enforced == ["ai.base_url", "valid_key"]

    def test_apply_enforcement_skips_invalid_path(self) -> None:
        team_raw = {"ai": {"base_url": "team"}}
        merged = {"ai": {"base_url": "personal"}}
        # Inject an invalid path that bypassed load-time validation
        result = apply_enforcement(merged, team_raw, ["INVALID"])
        assert result["ai"]["base_url"] == "personal"


# ---------------------------------------------------------------------------
# Walk-up depth cap
# ---------------------------------------------------------------------------


class TestWalkUpDepthCap:
    def test_stops_at_home_directory(self, tmp_path: Path) -> None:
        # Place a team config above $HOME — it should NOT be found
        with patch("anteroom.services.team_config.Path.home", return_value=tmp_path / "home"):
            child = tmp_path / "home" / "project"
            child.mkdir(parents=True)
            # Place config at tmp_path (above "home")
            team_file = tmp_path / "anteroom.team.yaml"
            _write_yaml(team_file, {"ai": {"model": "gpt-4"}})

            result = _walk_up_for_team_config(child)
            assert result is None

    def test_finds_config_at_home(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        team_file = home / "anteroom.team.yaml"
        _write_yaml(team_file, {"ai": {"model": "gpt-4"}})

        with patch("anteroom.services.team_config.Path.home", return_value=home):
            child = home / "project"
            child.mkdir()
            result = _walk_up_for_team_config(child)
            assert result == team_file

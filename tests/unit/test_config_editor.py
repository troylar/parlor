"""Tests for the scoped config editor service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from anteroom.services.config_editor import (
    _SENSITIVE_FIELDS,
    ConfigFieldInfo,
    _delete_nested,
    _set_nested,
    apply_field_to_config,
    build_full_source_map,
    check_write_allowed,
    collect_env_overrides,
    get_field,
    list_settable_fields,
    reset_personal_field,
    reset_project_field,
    reset_space_field,
    validate_field_value,
    write_personal_field,
    write_project_field,
    write_space_field,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _write_space_yaml(path: Path, name: str = "test", config: dict | None = None) -> None:
    data: dict[str, Any] = {"name": name, "version": "1"}
    if config:
        data["config"] = config
    _write_yaml(path, data)


# ---------------------------------------------------------------------------
# list_settable_fields
# ---------------------------------------------------------------------------


class TestListSettableFields:
    def test_returns_nonempty_list(self) -> None:
        fields = list_settable_fields()
        assert len(fields) > 0
        assert all(isinstance(f, ConfigFieldInfo) for f in fields)

    def test_excludes_sensitive_by_default(self) -> None:
        fields = list_settable_fields()
        paths = {f.dot_path for f in fields}
        for sensitive in _SENSITIVE_FIELDS:
            assert sensitive not in paths

    def test_includes_sensitive_when_requested(self) -> None:
        fields = list_settable_fields(include_sensitive=True)
        paths = {f.dot_path for f in fields}
        assert "ai.api_key" in paths

    def test_typed_fields_have_correct_types(self) -> None:
        fields = list_settable_fields()
        by_path = {f.dot_path: f for f in fields}

        assert by_path["ai.request_timeout"].field_type == "int"
        assert by_path["ai.request_timeout"].min_val == 10
        assert by_path["ai.request_timeout"].max_val == 600

        assert by_path["ai.temperature"].field_type == "float"
        assert by_path["safety.approval_mode"].field_type == "enum"
        assert by_path["safety.approval_mode"].allowed_values is not None
        assert "auto" in by_path["safety.approval_mode"].allowed_values

    def test_bool_fields_detected(self) -> None:
        fields = list_settable_fields()
        by_path = {f.dot_path: f for f in fields}
        assert by_path["ai.verify_ssl"].field_type == "bool"
        assert by_path["safety.read_only"].field_type == "bool"

    def test_list_fields_detected(self) -> None:
        fields = list_settable_fields()
        by_path = {f.dot_path: f for f in fields}
        assert by_path["safety.allowed_tools"].field_type == "list"

    def test_sorted_by_dot_path(self) -> None:
        fields = list_settable_fields()
        paths = [f.dot_path for f in fields]
        assert paths == sorted(paths)


# ---------------------------------------------------------------------------
# build_full_source_map
# ---------------------------------------------------------------------------


class TestBuildFullSourceMap:
    def test_empty_inputs(self) -> None:
        result = build_full_source_map()
        assert result == {}

    def test_single_layer(self) -> None:
        result = build_full_source_map(personal_raw={"ai": {"model": "gpt-4o"}})
        assert result["ai.model"] == "personal"

    def test_precedence_order(self) -> None:
        result = build_full_source_map(
            team_raw={"ai": {"model": "team-model"}},
            personal_raw={"ai": {"model": "personal-model"}},
        )
        assert result["ai.model"] == "personal"

    def test_all_layers(self) -> None:
        result = build_full_source_map(
            team_raw={"ai": {"model": "t"}},
            pack_raw={"ai": {"model": "pk"}},
            personal_raw={"ai": {"model": "p"}},
            space_raw={"ai": {"model": "s"}},
            project_raw={"ai": {"model": "pr"}},
            env_overrides={"ai": {"model": "e"}},
        )
        assert result["ai.model"] == "env var"

    def test_different_keys_from_different_layers(self) -> None:
        result = build_full_source_map(
            team_raw={"safety": {"approval_mode": "ask"}},
            personal_raw={"ai": {"model": "gpt-4o"}},
        )
        assert result["safety.approval_mode"] == "team"
        assert result["ai.model"] == "personal"

    def test_pack_layer_tracked(self) -> None:
        result = build_full_source_map(
            pack_raw={"ai": {"temperature": 0.5}},
        )
        assert result["ai.temperature"] == "pack"


# ---------------------------------------------------------------------------
# get_field
# ---------------------------------------------------------------------------


class TestGetField:
    def _make_config(self) -> Any:
        @dataclass
        class AI:
            model: str = "gpt-4o"
            base_url: str = "http://localhost"

        @dataclass
        class Safety:
            approval_mode: str = "ask_for_writes"

        @dataclass
        class Cfg:
            ai: AI = None  # type: ignore[assignment]
            safety: Safety = None  # type: ignore[assignment]

        return Cfg(ai=AI(), safety=Safety())

    def test_basic_read(self) -> None:
        config = self._make_config()
        source_map = {"ai.model": "personal"}
        result = get_field(config, "ai.model", source_map, [])
        assert result.effective_value == "gpt-4o"
        assert result.source_layer == "personal"
        assert result.is_enforced is False

    def test_enforced_field(self) -> None:
        config = self._make_config()
        source_map = {"ai.model": "personal"}
        result = get_field(config, "ai.model", source_map, ["ai.model"])
        assert result.source_layer == "team (enforced)"
        assert result.is_enforced is True

    def test_default_source(self) -> None:
        config = self._make_config()
        result = get_field(config, "ai.model", {}, [])
        assert result.source_layer == "default"

    def test_layer_values(self) -> None:
        config = self._make_config()
        layer_raws = {
            "team": {"ai": {"model": "team-model"}},
            "personal": {"ai": {"model": "gpt-4o"}},
        }
        result = get_field(config, "ai.model", {}, [], layer_raws=layer_raws)
        assert result.layer_values["team"] == "team-model"
        assert result.layer_values["personal"] == "gpt-4o"

    def test_invalid_dot_path_raises(self) -> None:
        config = self._make_config()
        with pytest.raises(ValueError, match="Invalid config path"):
            get_field(config, "../etc/passwd", {}, [])

    def test_field_info_populated(self) -> None:
        config = self._make_config()
        result = get_field(config, "ai.model", {}, [])
        assert result.field_info is not None
        assert result.field_info.dot_path == "ai.model"


# ---------------------------------------------------------------------------
# validate_field_value
# ---------------------------------------------------------------------------


class TestValidateFieldValue:
    def test_int_valid(self) -> None:
        val, errs = validate_field_value("ai.request_timeout", "60")
        assert val == 60
        assert errs == []

    def test_int_out_of_range(self) -> None:
        val, errs = validate_field_value("ai.request_timeout", "5")
        assert val == 5
        assert len(errs) == 1
        assert "below minimum" in errs[0]

    def test_int_invalid(self) -> None:
        val, errs = validate_field_value("ai.request_timeout", "abc")
        assert val is None
        assert len(errs) == 1

    def test_float_valid(self) -> None:
        val, errs = validate_field_value("ai.temperature", "0.7")
        assert val == 0.7
        assert errs == []

    def test_bool_true(self) -> None:
        val, errs = validate_field_value("ai.verify_ssl", "true")
        assert val is True
        assert errs == []

    def test_bool_false(self) -> None:
        val, errs = validate_field_value("safety.read_only", "no")
        assert val is False
        assert errs == []

    def test_bool_invalid(self) -> None:
        val, errs = validate_field_value("ai.verify_ssl", "maybe")
        assert val is None
        assert len(errs) == 1

    def test_enum_valid(self) -> None:
        val, errs = validate_field_value("safety.approval_mode", "auto")
        assert val == "auto"
        assert errs == []

    def test_enum_invalid(self) -> None:
        val, errs = validate_field_value("safety.approval_mode", "yolo")
        assert val is None
        assert len(errs) == 1
        assert "Must be one of" in errs[0]

    def test_list_comma_separated(self) -> None:
        val, errs = validate_field_value("safety.allowed_tools", "bash, write_file, read_file")
        assert val == ["bash", "write_file", "read_file"]
        assert errs == []

    def test_unknown_field_accepts_string(self) -> None:
        val, errs = validate_field_value("ai.model", "gpt-4o")
        assert val == "gpt-4o"
        assert errs == []

    def test_invalid_dot_path(self) -> None:
        val, errs = validate_field_value("../../etc/passwd", "value")
        assert val is None
        assert len(errs) == 1


# ---------------------------------------------------------------------------
# check_write_allowed
# ---------------------------------------------------------------------------


class TestCheckWriteAllowed:
    def test_normal_field_allowed(self) -> None:
        ok, reason = check_write_allowed("ai.model", [])
        assert ok is True
        assert reason is None

    def test_enforced_field_blocked(self) -> None:
        ok, reason = check_write_allowed("ai.model", ["ai.model"])
        assert ok is False
        assert "enforced" in reason  # type: ignore[operator]

    def test_sensitive_field_blocked(self) -> None:
        ok, reason = check_write_allowed("ai.api_key", [])
        assert ok is False
        assert "sensitive" in reason  # type: ignore[operator]

    def test_sensitive_field_allowed_when_flag(self) -> None:
        ok, reason = check_write_allowed("ai.api_key", [], allow_sensitive=True)
        assert ok is True

    def test_invalid_path_blocked(self) -> None:
        ok, reason = check_write_allowed("../etc/passwd", [])
        assert ok is False


# ---------------------------------------------------------------------------
# _set_nested / _delete_nested
# ---------------------------------------------------------------------------


class TestNestedHelpers:
    def test_set_creates_intermediate(self) -> None:
        d: dict[str, Any] = {}
        _set_nested(d, "ai.model", "gpt-4o")
        assert d == {"ai": {"model": "gpt-4o"}}

    def test_set_preserves_siblings(self) -> None:
        d: dict[str, Any] = {"ai": {"base_url": "http://localhost"}}
        _set_nested(d, "ai.model", "gpt-4o")
        assert d["ai"]["base_url"] == "http://localhost"
        assert d["ai"]["model"] == "gpt-4o"

    def test_delete_existing(self) -> None:
        d: dict[str, Any] = {"ai": {"model": "gpt-4o", "base_url": "http://localhost"}}
        assert _delete_nested(d, "ai.model") is True
        assert "model" not in d["ai"]
        assert d["ai"]["base_url"] == "http://localhost"

    def test_delete_nonexistent(self) -> None:
        d: dict[str, Any] = {"ai": {}}
        assert _delete_nested(d, "ai.model") is False


# ---------------------------------------------------------------------------
# write_personal_field
# ---------------------------------------------------------------------------


class TestWritePersonalField:
    def test_writes_to_existing_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        _write_yaml(config_path, {"ai": {"base_url": "http://localhost"}})

        result = write_personal_field("ai.model", "gpt-4o", config_path=config_path)
        assert result == config_path

        raw = _read_yaml(config_path)
        assert raw["ai"]["model"] == "gpt-4o"
        assert raw["ai"]["base_url"] == "http://localhost"  # preserved

    def test_creates_config_if_missing(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        write_personal_field("ai.model", "gpt-4o", config_path=config_path)

        raw = _read_yaml(config_path)
        assert raw["ai"]["model"] == "gpt-4o"

    def test_nested_field(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        write_personal_field("safety.subagent.max_concurrent", 10, config_path=config_path)

        raw = _read_yaml(config_path)
        assert raw["safety"]["subagent"]["max_concurrent"] == 10

    def test_file_permissions(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        write_personal_field("ai.model", "gpt-4o", config_path=config_path)

        mode = config_path.stat().st_mode
        assert mode & 0o777 == 0o600


# ---------------------------------------------------------------------------
# write_space_field
# ---------------------------------------------------------------------------


class TestWriteSpaceField:
    def test_writes_config_to_space_yaml(self, tmp_path: Path) -> None:
        space_path = tmp_path / "space.yaml"
        _write_space_yaml(space_path)

        write_space_field("ai.model", "gpt-4o", space_path)

        raw = _read_yaml(space_path)
        assert raw["config"]["ai"]["model"] == "gpt-4o"
        assert raw["name"] == "test"  # preserved

    def test_preserves_existing_config(self, tmp_path: Path) -> None:
        space_path = tmp_path / "space.yaml"
        _write_space_yaml(space_path, config={"safety": {"approval_mode": "ask"}})

        write_space_field("ai.model", "gpt-4o", space_path)

        raw = _read_yaml(space_path)
        assert raw["config"]["ai"]["model"] == "gpt-4o"
        assert raw["config"]["safety"]["approval_mode"] == "ask"

    def test_updates_trust_hash(self, tmp_path: Path) -> None:
        space_path = tmp_path / "space.yaml"
        _write_space_yaml(space_path)

        with patch("anteroom.services.trust.save_trust_decision") as mock_trust:
            write_space_field("ai.model", "gpt-4o", space_path)
            mock_trust.assert_called_once()

    def test_syncs_db_model_for_ai_model(self, tmp_path: Path) -> None:
        space_path = tmp_path / "space.yaml"
        _write_space_yaml(space_path)

        mock_db = object()
        with patch("anteroom.services.space_storage.update_space") as mock_update:
            write_space_field("ai.model", "gpt-4o", space_path, db=mock_db, space_id="s1")
            mock_update.assert_called_once_with(mock_db, "s1", model="gpt-4o")

    def test_syncs_db_model_for_bare_model(self, tmp_path: Path) -> None:
        space_path = tmp_path / "space.yaml"
        _write_space_yaml(space_path)

        mock_db = object()
        with patch("anteroom.services.space_storage.update_space") as mock_update:
            write_space_field("model", "gpt-4o", space_path, db=mock_db, space_id="s1")
            mock_update.assert_called_once_with(mock_db, "s1", model="gpt-4o")

    def test_no_db_sync_for_other_fields(self, tmp_path: Path) -> None:
        space_path = tmp_path / "space.yaml"
        _write_space_yaml(space_path)

        mock_db = object()
        with patch("anteroom.services.space_storage.update_space") as mock_update:
            write_space_field("ai.temperature", 0.5, space_path, db=mock_db, space_id="s1")
            mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# write_project_field
# ---------------------------------------------------------------------------


class TestWriteProjectField:
    def test_creates_anteroom_config(self, tmp_path: Path) -> None:
        with patch("anteroom.services.project_config.discover_project_config", return_value=None):
            result = write_project_field("ai.model", "gpt-4o", working_dir=tmp_path, data_dir=tmp_path)

        expected = tmp_path / ".anteroom" / "config.yaml"
        assert result == expected
        raw = _read_yaml(expected)
        assert raw["ai"]["model"] == "gpt-4o"

    def test_writes_to_discovered_claude_config(self, tmp_path: Path) -> None:
        claude_config = tmp_path / ".claude" / "config.yaml"
        _write_yaml(claude_config, {"ai": {"base_url": "http://existing"}})

        with patch("anteroom.services.project_config.discover_project_config", return_value=claude_config):
            result = write_project_field("ai.model", "gpt-4o", working_dir=tmp_path, data_dir=tmp_path)

        assert result == claude_config  # Wrote to .claude, NOT .anteroom
        raw = _read_yaml(claude_config)
        assert raw["ai"]["model"] == "gpt-4o"
        assert raw["ai"]["base_url"] == "http://existing"  # preserved

    def test_writes_to_discovered_parlor_config(self, tmp_path: Path) -> None:
        parlor_config = tmp_path / ".parlor" / "config.yaml"
        _write_yaml(parlor_config, {})

        with patch("anteroom.services.project_config.discover_project_config", return_value=parlor_config):
            result = write_project_field("ai.model", "gpt-4o", working_dir=tmp_path, data_dir=tmp_path)

        assert result == parlor_config

    def test_updates_trust_hash(self, tmp_path: Path) -> None:
        with (
            patch("anteroom.services.project_config.discover_project_config", return_value=None),
            patch("anteroom.services.trust.save_trust_decision") as mock_trust,
        ):
            write_project_field("ai.model", "gpt-4o", working_dir=tmp_path, data_dir=tmp_path)
            mock_trust.assert_called_once()


# ---------------------------------------------------------------------------
# reset_*_field
# ---------------------------------------------------------------------------


class TestResetFields:
    def test_reset_personal(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        _write_yaml(config_path, {"ai": {"model": "gpt-4o", "base_url": "http://x"}})

        assert reset_personal_field("ai.model", config_path=config_path) is True

        raw = _read_yaml(config_path)
        assert "model" not in raw["ai"]
        assert raw["ai"]["base_url"] == "http://x"

    def test_reset_personal_nonexistent(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        _write_yaml(config_path, {"ai": {}})
        assert reset_personal_field("ai.model", config_path=config_path) is False

    def test_reset_space(self, tmp_path: Path) -> None:
        space_path = tmp_path / "space.yaml"
        _write_space_yaml(space_path, config={"ai": {"model": "gpt-4o", "base_url": "http://x"}})

        assert reset_space_field("ai.model", space_path) is True

        raw = _read_yaml(space_path)
        assert "model" not in raw["config"]["ai"]

    def test_reset_project(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".anteroom" / "config.yaml"
        _write_yaml(config_path, {"ai": {"model": "gpt-4o"}})

        with patch("anteroom.services.project_config.discover_project_config", return_value=config_path):
            assert reset_project_field("ai.model", working_dir=tmp_path, data_dir=tmp_path) is True

    def test_reset_project_no_config(self, tmp_path: Path) -> None:
        with patch("anteroom.services.project_config.discover_project_config", return_value=None):
            assert reset_project_field("ai.model", working_dir=tmp_path) is False


# ---------------------------------------------------------------------------
# apply_field_to_config
# ---------------------------------------------------------------------------


class TestApplyFieldToConfig:
    def test_sets_nested_attr(self) -> None:
        @dataclass
        class AI:
            model: str = "old"

        @dataclass
        class Cfg:
            ai: AI = None  # type: ignore[assignment]

        config = Cfg(ai=AI())
        apply_field_to_config(config, "ai.model", "new")
        assert config.ai.model == "new"


# ---------------------------------------------------------------------------
# collect_env_overrides
# ---------------------------------------------------------------------------


class TestCollectEnvOverrides:
    def _clean_env(self) -> dict[str, str]:
        """Return env dict with all AI_CHAT_* vars removed."""
        return {k: v for k, v in os.environ.items() if not k.startswith("AI_CHAT_")}

    def test_collects_set_vars(self) -> None:
        clean = self._clean_env()
        clean["AI_CHAT_MODEL"] = "env-model"
        with patch.dict("os.environ", clean, clear=True):
            result = collect_env_overrides()
        assert result == {"ai": {"model": "env-model"}}

    def test_empty_when_no_vars(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = collect_env_overrides()
        assert result == {}

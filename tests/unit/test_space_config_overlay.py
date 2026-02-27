"""Tests for space config overlay merging into load_config."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml


def _write_config(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _base_config(**overrides: object) -> dict:
    cfg = {
        "ai": {
            "base_url": "http://localhost:11434/v1",
            "api_key": "test-key",
            "model": "gpt-4",
        },
    }
    for k, v in overrides.items():
        cfg[k] = v
    return cfg


def test_space_config_overlay_merges_between_personal_and_project(tmp_path: Path) -> None:
    """Space config overlay is merged on top of personal config."""
    from anteroom.config import load_config

    personal = _base_config()
    personal["ai"]["model"] = "personal-model"
    config_path = _write_config(tmp_path, personal)

    space_overlay = {"ai": {"model": "space-model"}}

    cfg, _ = load_config(config_path, space_config=space_overlay)
    assert cfg.ai.model == "space-model"


def test_space_config_overlay_overridden_by_project(tmp_path: Path) -> None:
    """Project config takes precedence over space config."""
    from anteroom.config import load_config

    personal = _base_config()
    config_path = _write_config(tmp_path, personal)

    space_overlay = {"ai": {"model": "space-model"}}

    proj_data = {"ai": {"model": "project-model"}}
    proj_path = tmp_path / "project_config.yaml"
    proj_path.write_text(yaml.dump(proj_data), encoding="utf-8")

    with patch("anteroom.services.project_config.load_project_config") as mock_load:
        mock_load.return_value = (proj_data, [])
        cfg, _ = load_config(
            config_path,
            space_config=space_overlay,
            project_config_path=proj_path,
        )
    assert cfg.ai.model == "project-model"


def test_space_config_overlay_respects_team_enforcement(tmp_path: Path) -> None:
    """Team-enforced fields override space config overlay."""
    from unittest.mock import patch as mock_patch

    from anteroom.config import load_config

    personal = _base_config()
    config_path = _write_config(tmp_path, personal)

    team_data = {"ai": {"model": "team-enforced-model"}, "enforce": ["ai.model"]}
    team_path = tmp_path / "team_config.yaml"
    team_path.write_text(yaml.dump(team_data), encoding="utf-8")

    space_overlay = {"ai": {"model": "space-model"}}

    with mock_patch("anteroom.services.trust.check_trust", return_value="trusted"):
        cfg, enforced = load_config(
            config_path,
            team_config_path=team_path,
            space_config=space_overlay,
        )
    assert cfg.ai.model == "team-enforced-model"
    assert "ai.model" in enforced


def test_space_config_overlay_none_is_noop(tmp_path: Path) -> None:
    """Passing space_config=None doesn't change behavior."""
    from anteroom.config import load_config

    personal = _base_config()
    personal["ai"]["model"] = "personal-model"
    config_path = _write_config(tmp_path, personal)

    cfg, _ = load_config(config_path, space_config=None)
    assert cfg.ai.model == "personal-model"


def test_space_config_overlay_empty_dict_is_noop(tmp_path: Path) -> None:
    """Passing space_config={} doesn't change behavior."""
    from anteroom.config import load_config

    personal = _base_config()
    personal["ai"]["model"] = "personal-model"
    config_path = _write_config(tmp_path, personal)

    cfg, _ = load_config(config_path, space_config={})
    assert cfg.ai.model == "personal-model"


def test_get_space_config_overlay_happy_path(tmp_path: Path) -> None:
    """get_space_config_overlay returns the config dict from a space file."""
    from anteroom.services.spaces import get_space_config_overlay

    space_file = tmp_path / "test.yaml"
    space_file.write_text(
        yaml.dump({"name": "testspace", "config": {"ai": {"model": "space-model"}}}),
        encoding="utf-8",
    )

    overlay = get_space_config_overlay(space_file)
    assert overlay == {"ai": {"model": "space-model"}}


def test_get_space_config_overlay_no_config_section(tmp_path: Path) -> None:
    """get_space_config_overlay returns empty dict when no config section."""
    from anteroom.services.spaces import get_space_config_overlay

    space_file = tmp_path / "test.yaml"
    space_file.write_text(yaml.dump({"name": "testspace"}), encoding="utf-8")

    overlay = get_space_config_overlay(space_file)
    assert overlay == {}


def test_get_space_config_overlay_missing_file(tmp_path: Path) -> None:
    """get_space_config_overlay returns empty dict for missing file."""
    from anteroom.services.spaces import get_space_config_overlay

    overlay = get_space_config_overlay(tmp_path / "nonexistent.yaml")
    assert overlay == {}

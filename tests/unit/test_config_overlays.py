"""Tests for pack config overlay collection, merging, and conflict detection."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services.config_overlays import (
    check_enforced_field_violations,
    collect_pack_overlays,
    detect_overlay_conflicts,
    flatten_to_dot_paths,
    merge_pack_overlays,
    track_config_sources,
)

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def db() -> ThreadSafeConnection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return ThreadSafeConnection(conn)


def _insert_pack(
    db: ThreadSafeConnection,
    pack_id: str | None = None,
    namespace: str = "test",
    name: str = "my-pack",
    version: str = "1.0.0",
) -> str:
    pid = pack_id or uuid.uuid4().hex
    db.execute(
        "INSERT INTO packs (id, namespace, name, version, description,"
        " source_path, installed_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (pid, namespace, name, version, "A test pack", ""),
    )
    db.commit()
    return pid


def _insert_config_overlay(
    db: ThreadSafeConnection,
    pack_id: str,
    fqn: str,
    content: dict[str, Any],
) -> str:
    art_id = uuid.uuid4().hex
    yaml_content = yaml.dump(content)
    db.execute(
        "INSERT INTO artifacts (id, fqn, type, namespace, name, source, content,"
        " content_hash, created_at, updated_at)"
        " VALUES (?, ?, 'config_overlay', ?, ?, 'built_in', ?, ?,"
        " datetime('now'), datetime('now'))",
        (art_id, fqn, fqn.split("/")[0].lstrip("@"), fqn.split("/")[-1], yaml_content, f"hash-{art_id}"),
    )
    db.execute(
        "INSERT INTO pack_artifacts (pack_id, artifact_id) VALUES (?, ?)",
        (pack_id, art_id),
    )
    db.commit()
    return art_id


# ---------------------------------------------------------------------------
# flatten_to_dot_paths
# ---------------------------------------------------------------------------


class TestFlattenToDotPaths:
    def test_flat_dict(self) -> None:
        assert flatten_to_dot_paths({"a": 1, "b": 2}) == {"a": 1, "b": 2}

    def test_nested_dict(self) -> None:
        result = flatten_to_dot_paths({"ai": {"model": "gpt-4", "temperature": 0.7}})
        assert result == {"ai.model": "gpt-4", "ai.temperature": 0.7}

    def test_deeply_nested(self) -> None:
        result = flatten_to_dot_paths({"safety": {"bash": {"network": "block"}}})
        assert result == {"safety.bash.network": "block"}

    def test_empty_dict(self) -> None:
        assert flatten_to_dot_paths({}) == {}

    def test_mixed_types(self) -> None:
        result = flatten_to_dot_paths({"a": [1, 2], "b": {"c": True}})
        assert result == {"a": [1, 2], "b.c": True}


# ---------------------------------------------------------------------------
# collect_pack_overlays
# ---------------------------------------------------------------------------


class TestCollectPackOverlays:
    def test_no_pack_ids_returns_empty(self, db: ThreadSafeConnection) -> None:
        assert collect_pack_overlays(db, []) == []

    def test_pack_without_overlays_returns_empty(self, db: ThreadSafeConnection) -> None:
        pid = _insert_pack(db)
        assert collect_pack_overlays(db, [pid]) == []

    def test_single_overlay(self, db: ThreadSafeConnection) -> None:
        pid = _insert_pack(db)
        overlay_data = {"ai": {"model": "gpt-4o"}}
        _insert_config_overlay(db, pid, "@test/config_overlay/prod", overlay_data)

        result = collect_pack_overlays(db, [pid])
        assert len(result) == 1
        label, data = result[0]
        assert label == "test/my-pack"
        assert data == overlay_data

    def test_multiple_overlays_from_one_pack(self, db: ThreadSafeConnection) -> None:
        pid = _insert_pack(db)
        _insert_config_overlay(db, pid, "@test/config_overlay/a", {"ai": {"model": "gpt-4o"}})
        _insert_config_overlay(db, pid, "@test/config_overlay/b", {"safety": {"approval_mode": "ask"}})

        result = collect_pack_overlays(db, [pid])
        assert len(result) == 2

    def test_overlays_from_multiple_packs(self, db: ThreadSafeConnection) -> None:
        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_config_overlay(db, pid1, "@ns1/config_overlay/x", {"ai": {"model": "gpt-4o"}})
        _insert_config_overlay(db, pid2, "@ns2/config_overlay/y", {"safety": {"approval_mode": "ask"}})

        result = collect_pack_overlays(db, [pid1, pid2])
        assert len(result) == 2
        labels = {r[0] for r in result}
        assert labels == {"ns1/pack-a", "ns2/pack-b"}

    def test_invalid_yaml_skipped(self, db: ThreadSafeConnection) -> None:
        pid = _insert_pack(db)
        art_id = uuid.uuid4().hex
        db.execute(
            "INSERT INTO artifacts (id, fqn, type, namespace, name, source, content,"
            " content_hash, created_at, updated_at)"
            " VALUES (?, '@test/config_overlay/bad', 'config_overlay', 'test', 'bad',"
            " 'built_in', '{{invalid yaml', ?, datetime('now'), datetime('now'))",
            (art_id, f"hash-{art_id}"),
        )
        db.execute("INSERT INTO pack_artifacts (pack_id, artifact_id) VALUES (?, ?)", (pid, art_id))
        db.commit()

        result = collect_pack_overlays(db, [pid])
        assert result == []

    def test_nonexistent_pack_id_skipped(self, db: ThreadSafeConnection) -> None:
        result = collect_pack_overlays(db, ["nonexistent"])
        assert result == []


# ---------------------------------------------------------------------------
# merge_pack_overlays
# ---------------------------------------------------------------------------


class TestMergePackOverlays:
    def test_empty_list(self) -> None:
        assert merge_pack_overlays([]) == {}

    def test_single_overlay(self) -> None:
        result = merge_pack_overlays([("pack-a", {"ai": {"model": "gpt-4o"}})])
        assert result == {"ai": {"model": "gpt-4o"}}

    def test_non_overlapping_keys(self) -> None:
        overlays = [
            ("pack-a", {"ai": {"model": "gpt-4o"}}),
            ("pack-b", {"safety": {"approval_mode": "ask"}}),
        ]
        result = merge_pack_overlays(overlays)
        assert result == {"ai": {"model": "gpt-4o"}, "safety": {"approval_mode": "ask"}}

    def test_overlapping_nested_keys_merge(self) -> None:
        overlays = [
            ("pack-a", {"ai": {"model": "gpt-4o"}}),
            ("pack-b", {"ai": {"temperature": 0.7}}),
        ]
        result = merge_pack_overlays(overlays)
        assert result == {"ai": {"model": "gpt-4o", "temperature": 0.7}}


# ---------------------------------------------------------------------------
# detect_overlay_conflicts
# ---------------------------------------------------------------------------


class TestDetectOverlayConflicts:
    def test_no_conflicts(self) -> None:
        existing = [("pack-a", {"ai": {"model": "gpt-4o"}})]
        new = ("pack-b", {"safety": {"approval_mode": "ask"}})
        assert detect_overlay_conflicts(existing, new) == []

    def test_conflicting_leaf_key(self) -> None:
        existing = [("pack-a", {"ai": {"model": "gpt-4o"}})]
        new = ("pack-b", {"ai": {"model": "claude-3"}})
        conflicts = detect_overlay_conflicts(existing, new)
        assert len(conflicts) == 1
        assert "ai.model" in conflicts[0]
        assert "pack-a" in conflicts[0]
        assert "pack-b" in conflicts[0]

    def test_multiple_conflicts(self) -> None:
        existing = [("pack-a", {"ai": {"model": "gpt-4o", "temperature": 0.7}})]
        new = ("pack-b", {"ai": {"model": "claude-3", "temperature": 0.9}})
        conflicts = detect_overlay_conflicts(existing, new)
        assert len(conflicts) == 2

    def test_no_existing_overlays(self) -> None:
        new = ("pack-b", {"ai": {"model": "claude-3"}})
        assert detect_overlay_conflicts([], new) == []

    def test_nested_no_conflict(self) -> None:
        existing = [("pack-a", {"ai": {"model": "gpt-4o"}})]
        new = ("pack-b", {"ai": {"temperature": 0.7}})
        assert detect_overlay_conflicts(existing, new) == []

    def test_conflict_across_multiple_existing(self) -> None:
        existing = [
            ("pack-a", {"ai": {"model": "gpt-4o"}}),
            ("pack-c", {"safety": {"approval_mode": "ask"}}),
        ]
        new = ("pack-b", {"ai": {"model": "claude-3"}, "safety": {"approval_mode": "auto"}})
        conflicts = detect_overlay_conflicts(existing, new)
        assert len(conflicts) == 2


# ---------------------------------------------------------------------------
# check_enforced_field_violations
# ---------------------------------------------------------------------------


class TestCheckEnforcedFieldViolations:
    def test_no_violations(self) -> None:
        overlay = {"ai": {"temperature": 0.7}}
        assert check_enforced_field_violations(overlay, ["ai.model"]) == []

    def test_violation_detected(self) -> None:
        overlay = {"ai": {"model": "gpt-4o"}}
        violations = check_enforced_field_violations(overlay, ["ai.model"])
        assert violations == ["ai.model"]

    def test_multiple_violations(self) -> None:
        overlay = {"ai": {"model": "gpt-4o", "base_url": "http://new"}}
        violations = check_enforced_field_violations(overlay, ["ai.model", "ai.base_url"])
        assert violations == ["ai.base_url", "ai.model"]

    def test_empty_enforced(self) -> None:
        overlay = {"ai": {"model": "gpt-4o"}}
        assert check_enforced_field_violations(overlay, []) == []


# ---------------------------------------------------------------------------
# track_config_sources
# ---------------------------------------------------------------------------


class TestTrackConfigSources:
    def test_single_layer(self) -> None:
        layers = [("personal", {"ai": {"model": "gpt-4"}})]
        result = track_config_sources(layers)
        assert result == {"ai.model": "personal"}

    def test_later_layer_overwrites(self) -> None:
        layers = [
            ("team", {"ai": {"model": "team-model"}}),
            ("personal", {"ai": {"model": "personal-model"}}),
        ]
        result = track_config_sources(layers)
        assert result["ai.model"] == "personal"

    def test_multiple_keys(self) -> None:
        layers = [
            ("team", {"ai": {"model": "team-model"}}),
            ("pack", {"safety": {"approval_mode": "ask"}}),
        ]
        result = track_config_sources(layers)
        assert result == {"ai.model": "team", "safety.approval_mode": "pack"}

    def test_empty_layers(self) -> None:
        assert track_config_sources([]) == {}


# ---------------------------------------------------------------------------
# Integration: load_config with pack_config
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _base_config(**overrides: object) -> dict:
    cfg: dict[str, Any] = {
        "ai": {
            "base_url": "http://localhost:11434/v1",
            "api_key": "test-key",
            "model": "gpt-4",
        },
    }
    for k, v in overrides.items():
        cfg[k] = v
    return cfg


class TestLoadConfigWithPackOverlay:
    def test_pack_overlay_applies_for_unset_keys(self, tmp_path: Path) -> None:
        from anteroom.config import load_config

        personal = _base_config()
        config_path = _write_config(tmp_path, personal)

        # Pack sets a key that personal doesn't explicitly set
        pack_overlay = {"ai": {"temperature": 0.42}}
        cfg, _ = load_config(config_path, pack_config=pack_overlay)
        assert cfg.ai.temperature == 0.42

    def test_pack_overlay_applies_when_personal_sets_same_key(self, tmp_path: Path) -> None:
        from anteroom.config import load_config

        personal = _base_config()
        personal["ai"]["model"] = "personal-model"
        config_path = _write_config(tmp_path, personal)

        # Pack also sets model — personal should win (personal > packs)
        pack_overlay = {"ai": {"model": "pack-model"}}
        cfg, _ = load_config(config_path, pack_config=pack_overlay)
        assert cfg.ai.model == "personal-model"

    def test_pack_none_is_noop(self, tmp_path: Path) -> None:
        from anteroom.config import load_config

        personal = _base_config()
        personal["ai"]["model"] = "personal-model"
        config_path = _write_config(tmp_path, personal)

        cfg, _ = load_config(config_path, pack_config=None)
        assert cfg.ai.model == "personal-model"

    def test_pack_empty_dict_is_noop(self, tmp_path: Path) -> None:
        from anteroom.config import load_config

        personal = _base_config()
        personal["ai"]["model"] = "personal-model"
        config_path = _write_config(tmp_path, personal)

        cfg, _ = load_config(config_path, pack_config={})
        assert cfg.ai.model == "personal-model"

    def test_team_enforcement_overrides_pack(self, tmp_path: Path) -> None:
        from anteroom.config import load_config

        personal = _base_config()
        config_path = _write_config(tmp_path, personal)

        team_data = {"ai": {"model": "team-enforced-model"}, "enforce": ["ai.model"]}
        team_path = tmp_path / "team_config.yaml"
        team_path.write_text(yaml.dump(team_data), encoding="utf-8")

        pack_overlay = {"ai": {"model": "pack-model"}}

        with patch("anteroom.services.trust.check_trust", return_value="trusted"):
            cfg, enforced = load_config(
                config_path,
                team_config_path=team_path,
                pack_config=pack_overlay,
            )
        assert cfg.ai.model == "team-enforced-model"
        assert "ai.model" in enforced

    def test_space_overrides_pack(self, tmp_path: Path) -> None:
        from anteroom.config import load_config

        personal = _base_config()
        config_path = _write_config(tmp_path, personal)

        pack_overlay = {"ai": {"model": "pack-model"}}
        space_overlay = {"ai": {"model": "space-model"}}

        cfg, _ = load_config(config_path, pack_config=pack_overlay, space_config=space_overlay)
        assert cfg.ai.model == "space-model"

    def test_pack_overlay_survives_empty_team_config(self, tmp_path: Path) -> None:
        """Pack overlays must apply even when team_config_path exists but file is empty."""
        from anteroom.config import load_config

        personal = _base_config()
        config_path = _write_config(tmp_path, personal)

        # Empty team config file — load_team_config returns ({}, [])
        team_path = tmp_path / "team_config.yaml"
        team_path.write_text("", encoding="utf-8")

        pack_overlay = {"ai": {"temperature": 0.42}}

        with patch("anteroom.services.trust.check_trust", return_value="trusted"):
            cfg, _ = load_config(
                config_path,
                team_config_path=team_path,
                pack_config=pack_overlay,
            )
        assert cfg.ai.temperature == 0.42


# ---------------------------------------------------------------------------
# Integration: attach_pack conflict detection
# ---------------------------------------------------------------------------


class TestAttachPackConflictDetection:
    def test_attach_no_overlay_succeeds(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack

        pid = _insert_pack(db)
        result = attach_pack(db, pid)
        assert result["pack_id"] == pid

    def test_attach_with_overlay_no_conflict_succeeds(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_config_overlay(db, pid1, "@ns1/config_overlay/x", {"ai": {"model": "gpt-4o"}})
        attach_pack(db, pid1)

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_config_overlay(db, pid2, "@ns2/config_overlay/y", {"safety": {"approval_mode": "ask"}})
        result = attach_pack(db, pid2)
        assert result["pack_id"] == pid2

    def test_attach_with_overlay_conflict_raises(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_config_overlay(db, pid1, "@ns1/config_overlay/x", {"ai": {"model": "gpt-4o"}})
        attach_pack(db, pid1)

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_config_overlay(db, pid2, "@ns2/config_overlay/y", {"ai": {"model": "claude-3"}})

        with pytest.raises(ValueError, match="Config overlay conflict"):
            attach_pack(db, pid2)

    def test_attach_skip_conflict_check(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_config_overlay(db, pid1, "@ns1/config_overlay/x", {"ai": {"model": "gpt-4o"}})
        attach_pack(db, pid1)

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_config_overlay(db, pid2, "@ns2/config_overlay/y", {"ai": {"model": "claude-3"}})

        result = attach_pack(db, pid2, check_overlay_conflicts=False)
        assert result["pack_id"] == pid2

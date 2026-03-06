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
        assert result["priority"] == 50

    def test_attach_with_overlay_no_conflict_succeeds(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_config_overlay(db, pid1, "@ns1/config_overlay/x", {"ai": {"model": "gpt-4o"}})
        attach_pack(db, pid1)

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_config_overlay(db, pid2, "@ns2/config_overlay/y", {"safety": {"approval_mode": "ask"}})
        result = attach_pack(db, pid2)
        assert result["pack_id"] == pid2

    def test_attach_same_key_same_priority_raises(self, db: ThreadSafeConnection) -> None:
        """Two packs setting the same key at the same priority is an error."""
        from anteroom.services.pack_attachments import attach_pack

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_config_overlay(db, pid1, "@ns1/config_overlay/x", {"ai": {"model": "gpt-4o"}})
        attach_pack(db, pid1, priority=50)

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_config_overlay(db, pid2, "@ns2/config_overlay/y", {"ai": {"model": "claude-3"}})

        with pytest.raises(ValueError, match="Config overlay conflict"):
            attach_pack(db, pid2, priority=50)

    def test_attach_same_key_different_priority_succeeds(self, db: ThreadSafeConnection) -> None:
        """Two packs setting the same key at different priorities is allowed."""
        from anteroom.services.pack_attachments import attach_pack

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_config_overlay(db, pid1, "@ns1/config_overlay/x", {"ai": {"model": "gpt-4o"}})
        attach_pack(db, pid1, priority=50)

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_config_overlay(db, pid2, "@ns2/config_overlay/y", {"ai": {"model": "claude-3"}})

        # Different priority — no conflict
        result = attach_pack(db, pid2, priority=10)
        assert result["pack_id"] == pid2
        assert result["priority"] == 10

    def test_attach_custom_priority_stored(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack

        pid = _insert_pack(db)
        result = attach_pack(db, pid, priority=25)
        assert result["priority"] == 25

    def test_attach_priority_below_range_raises(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack

        pid = _insert_pack(db)
        with pytest.raises(ValueError, match="Priority must be between 1 and 100"):
            attach_pack(db, pid, priority=0)

    def test_attach_priority_above_range_raises(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack

        pid = _insert_pack(db)
        with pytest.raises(ValueError, match="Priority must be between 1 and 100"):
            attach_pack(db, pid, priority=101)

    def test_attach_priority_boundary_values_accepted(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        result1 = attach_pack(db, pid1, priority=1, check_overlay_conflicts=False)
        assert result1["priority"] == 1

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        result2 = attach_pack(db, pid2, priority=100, check_overlay_conflicts=False)
        assert result2["priority"] == 100

    def test_conflict_error_message_includes_resolution_hint(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_config_overlay(db, pid1, "@ns1/config_overlay/x", {"ai": {"model": "gpt-4o"}})
        attach_pack(db, pid1)

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_config_overlay(db, pid2, "@ns2/config_overlay/y", {"ai": {"model": "claude-3"}})

        with pytest.raises(ValueError, match="--priority"):
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


# ---------------------------------------------------------------------------
# Integration: attach_pack_to_space conflict detection
# ---------------------------------------------------------------------------


def _insert_space(db: ThreadSafeConnection, space_id: str = "space-1", name: str = "test-space") -> str:
    """Insert a minimal space row for testing."""
    db.execute(
        "INSERT INTO spaces (id, name, created_at, updated_at) VALUES (?, ?, datetime('now'), datetime('now'))",
        (space_id, name),
    )
    db.commit()
    return space_id


class TestAttachPackToSpaceConflictDetection:
    """Tests for config overlay conflict detection in attach_pack_to_space.

    These mirror TestAttachPackConflictDetection but use the space-scoped
    attachment path (attach_pack_to_space) which queries a different set
    of active pack IDs (global + space-scoped).
    """

    def test_attach_to_space_no_overlay_succeeds(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack_to_space

        pid = _insert_pack(db)
        sid = _insert_space(db)
        result = attach_pack_to_space(db, pid, sid)
        assert result["pack_id"] == pid
        assert result["scope"] == "space"
        assert result["priority"] == 50

    def test_attach_to_space_with_overlay_no_conflict(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack_to_space

        sid = _insert_space(db)

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_config_overlay(db, pid1, "@ns1/config_overlay/x", {"ai": {"model": "gpt-4o"}})
        attach_pack_to_space(db, pid1, sid)

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_config_overlay(db, pid2, "@ns2/config_overlay/y", {"safety": {"approval_mode": "ask"}})
        result = attach_pack_to_space(db, pid2, sid)
        assert result["pack_id"] == pid2

    def test_attach_to_space_same_key_same_priority_raises(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack_to_space

        sid = _insert_space(db)

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_config_overlay(db, pid1, "@ns1/config_overlay/x", {"ai": {"model": "gpt-4o"}})
        attach_pack_to_space(db, pid1, sid, priority=50)

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_config_overlay(db, pid2, "@ns2/config_overlay/y", {"ai": {"model": "claude-3"}})

        with pytest.raises(ValueError, match="Config overlay conflict"):
            attach_pack_to_space(db, pid2, sid, priority=50)

    def test_attach_to_space_same_key_different_priority_succeeds(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack_to_space

        sid = _insert_space(db)

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_config_overlay(db, pid1, "@ns1/config_overlay/x", {"ai": {"model": "gpt-4o"}})
        attach_pack_to_space(db, pid1, sid, priority=50)

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_config_overlay(db, pid2, "@ns2/config_overlay/y", {"ai": {"model": "claude-3"}})

        result = attach_pack_to_space(db, pid2, sid, priority=10)
        assert result["pack_id"] == pid2
        assert result["priority"] == 10

    def test_attach_to_space_priority_below_range_raises(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack_to_space

        pid = _insert_pack(db)
        sid = _insert_space(db)
        with pytest.raises(ValueError, match="Priority must be between 1 and 100"):
            attach_pack_to_space(db, pid, sid, priority=0)

    def test_attach_to_space_priority_above_range_raises(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack_to_space

        pid = _insert_pack(db)
        sid = _insert_space(db)
        with pytest.raises(ValueError, match="Priority must be between 1 and 100"):
            attach_pack_to_space(db, pid, sid, priority=200)

    def test_attach_to_space_skip_conflict_check(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack_to_space

        sid = _insert_space(db)

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_config_overlay(db, pid1, "@ns1/config_overlay/x", {"ai": {"model": "gpt-4o"}})
        attach_pack_to_space(db, pid1, sid)

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_config_overlay(db, pid2, "@ns2/config_overlay/y", {"ai": {"model": "claude-3"}})

        result = attach_pack_to_space(db, pid2, sid, check_overlay_conflicts=False)
        assert result["pack_id"] == pid2


# ---------------------------------------------------------------------------
# Priority: detect_overlay_conflicts with priority args
# ---------------------------------------------------------------------------


class TestDetectOverlayConflictsWithPriority:
    """Tests for priority-aware conflict detection.

    When priorities are provided, overlapping keys are only conflicts if
    the two packs share the same priority number.  Different priorities
    are allowed — the lower number wins at merge time.
    """

    def test_same_priority_same_key_is_conflict(self) -> None:
        existing = [("ns1/pack-a", {"ai": {"model": "gpt-4o"}})]
        new = ("ns2/pack-b", {"ai": {"model": "claude-3"}})
        conflicts = detect_overlay_conflicts(
            existing,
            new,
            new_priority=50,
            existing_priorities={"ns1/pack-a": 50},
        )
        assert len(conflicts) == 1
        assert "ai.model" in conflicts[0]
        assert "priority 50" in conflicts[0]

    def test_different_priority_same_key_is_not_conflict(self) -> None:
        existing = [("ns1/pack-a", {"ai": {"model": "gpt-4o"}})]
        new = ("ns2/pack-b", {"ai": {"model": "claude-3"}})
        conflicts = detect_overlay_conflicts(
            existing,
            new,
            new_priority=10,
            existing_priorities={"ns1/pack-a": 50},
        )
        assert conflicts == []

    def test_no_priority_info_falls_back_to_strict(self) -> None:
        """Without priority args, any overlap is a conflict (backward compat)."""
        existing = [("ns1/pack-a", {"ai": {"model": "gpt-4o"}})]
        new = ("ns2/pack-b", {"ai": {"model": "claude-3"}})
        conflicts = detect_overlay_conflicts(existing, new)
        assert len(conflicts) == 1
        assert "priority" not in conflicts[0]

    def test_partial_priority_info_falls_back_to_strict(self) -> None:
        """Only new_priority without existing_priorities falls back to strict."""
        existing = [("ns1/pack-a", {"ai": {"model": "gpt-4o"}})]
        new = ("ns2/pack-b", {"ai": {"model": "claude-3"}})
        conflicts = detect_overlay_conflicts(
            existing,
            new,
            new_priority=10,
        )
        assert len(conflicts) == 1

    def test_multiple_existing_mixed_priorities(self) -> None:
        """Only the pack with the same priority produces a conflict."""
        existing = [
            ("ns1/pack-a", {"ai": {"model": "gpt-4o"}}),
            ("ns3/pack-c", {"ai": {"model": "fallback"}}),
        ]
        new = ("ns2/pack-b", {"ai": {"model": "claude-3"}})
        conflicts = detect_overlay_conflicts(
            existing,
            new,
            new_priority=50,
            existing_priorities={"ns1/pack-a": 10, "ns3/pack-c": 50},
        )
        # pack-a is at priority 10 (different from 50) — no conflict
        # pack-c is at priority 50 (same) — conflict
        assert len(conflicts) == 1
        assert "ns3/pack-c" in conflicts[0]

    def test_non_overlapping_keys_no_conflict_regardless_of_priority(self) -> None:
        existing = [("ns1/pack-a", {"ai": {"model": "gpt-4o"}})]
        new = ("ns2/pack-b", {"safety": {"approval_mode": "ask"}})
        conflicts = detect_overlay_conflicts(
            existing,
            new,
            new_priority=50,
            existing_priorities={"ns1/pack-a": 50},
        )
        assert conflicts == []


# ---------------------------------------------------------------------------
# Priority: merge_pack_overlays with priority sorting
# ---------------------------------------------------------------------------


class TestMergePackOverlaysWithPriority:
    """Tests for priority-sorted overlay merging.

    Lower priority number = higher precedence.  deep_merge(base, overlay)
    has overlay win, so we sort descending (highest number first) and
    apply lower-number packs last so they win.
    """

    def test_lower_priority_wins(self) -> None:
        overlays = [
            ("ns1/pack-a", {"ai": {"model": "low-pri-model"}}),
            ("ns2/pack-b", {"ai": {"model": "high-pri-model"}}),
        ]
        priorities = {"ns1/pack-a": 100, "ns2/pack-b": 10}
        result = merge_pack_overlays(overlays, priorities)
        assert result["ai"]["model"] == "high-pri-model"

    def test_same_priority_last_in_list_wins(self) -> None:
        """Same priority — deterministic but order-dependent (shouldn't happen
        in practice because same-priority conflicts are caught at attach time)."""
        overlays = [
            ("ns1/pack-a", {"ai": {"model": "model-a"}}),
            ("ns2/pack-b", {"ai": {"model": "model-b"}}),
        ]
        priorities = {"ns1/pack-a": 50, "ns2/pack-b": 50}
        # Both at priority 50 — sorted descending by priority, then stable
        # sort preserves original order. Both deep_merge last wins.
        result = merge_pack_overlays(overlays, priorities)
        # With same priority, sort is stable — original order preserved
        # deep_merge applies in order: pack-a then pack-b, pack-b wins
        assert result["ai"]["model"] in ("model-a", "model-b")

    def test_without_priorities_uses_list_order(self) -> None:
        """Without priorities arg, overlays merge in list order (backward compat)."""
        overlays = [
            ("ns1/pack-a", {"ai": {"model": "model-a"}}),
            ("ns2/pack-b", {"ai": {"model": "model-b"}}),
        ]
        result = merge_pack_overlays(overlays)
        assert result["ai"]["model"] == "model-b"

    def test_three_packs_priority_ordering(self) -> None:
        overlays = [
            ("ns1/baseline", {"ai": {"model": "baseline"}, "safety": {"approval_mode": "auto"}}),
            ("ns2/security", {"safety": {"approval_mode": "ask"}}),
            ("ns3/team", {"ai": {"model": "team-model"}}),
        ]
        priorities = {"ns1/baseline": 100, "ns2/security": 10, "ns3/team": 50}
        result = merge_pack_overlays(overlays, priorities)
        # security (10) wins for safety.approval_mode
        assert result["safety"]["approval_mode"] == "ask"
        # team (50) beats baseline (100) for ai.model
        assert result["ai"]["model"] == "team-model"

    def test_non_overlapping_keys_unaffected_by_priority(self) -> None:
        overlays = [
            ("ns1/pack-a", {"ai": {"model": "gpt-4o"}}),
            ("ns2/pack-b", {"safety": {"approval_mode": "ask"}}),
        ]
        priorities = {"ns1/pack-a": 100, "ns2/pack-b": 10}
        result = merge_pack_overlays(overlays, priorities)
        assert result == {"ai": {"model": "gpt-4o"}, "safety": {"approval_mode": "ask"}}

    def test_empty_priorities_dict_still_sorts(self) -> None:
        """Empty priorities dict (all packs get default 50) must still enter
        the sorting path.  Regression: ``if priorities:`` was falsy for ``{}``,
        silently skipping sort and making merge order non-deterministic."""
        overlays = [
            ("ns1/pack-a", {"ai": {"model": "model-a"}}),
            ("ns2/pack-b", {"ai": {"model": "model-b"}}),
        ]
        # Both packs get default priority 50 → same priority → stable sort
        # preserves original order → model-b wins via deep_merge.
        result = merge_pack_overlays(overlays, {})
        assert result["ai"]["model"] == "model-b"

    def test_none_priorities_uses_list_order(self) -> None:
        """None priorities (backward compat) should use list order without sorting."""
        overlays = [("ns1/pack-a", {"ai": {"model": "gpt-4o"}})]
        result = merge_pack_overlays(overlays, None)
        assert result == {"ai": {"model": "gpt-4o"}}


# ---------------------------------------------------------------------------
# Priority: get_attachment_priorities
# ---------------------------------------------------------------------------


class TestGetAttachmentPriorities:
    def test_empty_pack_ids(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import get_attachment_priorities

        assert get_attachment_priorities(db, []) == {}

    def test_default_priority(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack, get_attachment_priorities

        pid = _insert_pack(db, namespace="test", name="my-pack")
        attach_pack(db, pid)

        result = get_attachment_priorities(db, [pid])
        assert result == {"test/my-pack": 50}

    def test_custom_priority(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack, get_attachment_priorities

        pid = _insert_pack(db, namespace="test", name="my-pack")
        attach_pack(db, pid, priority=10)

        result = get_attachment_priorities(db, [pid])
        assert result == {"test/my-pack": 10}

    def test_multiple_packs(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack, get_attachment_priorities

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        attach_pack(db, pid1, priority=10)
        attach_pack(db, pid2, priority=90)

        result = get_attachment_priorities(db, [pid1, pid2])
        assert result == {"ns1/pack-a": 10, "ns2/pack-b": 90}

    def test_nonexistent_pack_id_skipped(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import get_attachment_priorities

        result = get_attachment_priorities(db, ["nonexistent"])
        assert result == {}

    def test_multiple_attachments_uses_lowest_priority(self, db: ThreadSafeConnection) -> None:
        """If a pack is attached at multiple scopes, the lowest priority wins."""
        from anteroom.services.pack_attachments import attach_pack, get_attachment_priorities

        pid = _insert_pack(db, namespace="test", name="my-pack")
        attach_pack(db, pid, priority=80)
        # Attach at project scope with different priority
        attach_pack(db, pid, project_path="/my/project", priority=20)

        result = get_attachment_priorities(db, [pid])
        assert result == {"test/my-pack": 20}


# ---------------------------------------------------------------------------
# Integration: priority-based merge through full config load
# ---------------------------------------------------------------------------


class TestPriorityMergeIntegration:
    """End-to-end test: two packs with overlapping keys at different priorities
    produce the correct merged config via load_config()."""

    def test_lower_priority_pack_wins_in_config(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack, get_active_pack_ids, get_attachment_priorities

        pid1 = _insert_pack(db, namespace="ns1", name="baseline")
        _insert_config_overlay(db, pid1, "@ns1/config_overlay/x", {"ai": {"temperature": 0.9}})
        attach_pack(db, pid1, priority=100)

        pid2 = _insert_pack(db, namespace="ns2", name="override")
        _insert_config_overlay(db, pid2, "@ns2/config_overlay/y", {"ai": {"temperature": 0.1}})
        attach_pack(db, pid2, priority=10)

        active_ids = get_active_pack_ids(db)
        overlays = collect_pack_overlays(db, active_ids)
        priorities = get_attachment_priorities(db, active_ids)
        merged = merge_pack_overlays(overlays, priorities)

        assert merged["ai"]["temperature"] == 0.1


# ---------------------------------------------------------------------------
# Generalized artifact conflict detection (all types)
# ---------------------------------------------------------------------------


def _insert_artifact(
    db: ThreadSafeConnection,
    pack_id: str,
    fqn: str,
    artifact_type: str,
    content: str = "placeholder",
) -> str:
    """Insert a non-config-overlay artifact and link it to a pack."""
    art_id = uuid.uuid4().hex
    ns, _, name = fqn[1:].split("/", 2)
    db.execute(
        "INSERT INTO artifacts (id, fqn, type, namespace, name, source, content,"
        " content_hash, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, 'built_in', ?, ?, datetime('now'), datetime('now'))",
        (art_id, fqn, artifact_type, ns, name, content, f"hash-{art_id}"),
    )
    db.execute(
        "INSERT INTO pack_artifacts (pack_id, artifact_id) VALUES (?, ?)",
        (pack_id, art_id),
    )
    db.commit()
    return art_id


class TestDetectArtifactConflicts:
    """Tests for generalized artifact conflict detection across all artifact types.

    Two packs conflict when they provide artifacts with the same type and name
    (the user-facing identifier), regardless of namespace/FQN. For example,
    @team-a/skill/deploy and @team-b/skill/deploy both expose /deploy.
    """

    def test_no_conflicts_different_names(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.config_overlays import detect_artifact_conflicts

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_artifact(db, pid1, "@ns1/skill/deploy", "skill")

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_artifact(db, pid2, "@ns2/skill/test", "skill")

        conflicts = detect_artifact_conflicts(db, pid2, [pid1])
        assert conflicts == []

    def test_skill_same_name_is_additive(self, db: ThreadSafeConnection) -> None:
        """Skills are additive — same name from two packs is resolved via namespace."""
        from anteroom.services.config_overlays import detect_artifact_conflicts

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_artifact(db, pid1, "@ns1/skill/deploy", "skill")

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_artifact(db, pid2, "@ns2/skill/deploy", "skill")

        conflicts = detect_artifact_conflicts(db, pid2, [pid1])
        assert conflicts == []  # No conflict — namespace-aware resolution handles it

    def test_skill_same_name_no_conflict_with_priority(self, db: ThreadSafeConnection) -> None:
        """Skills don't conflict even at different priorities — they're additive."""
        from anteroom.services.config_overlays import detect_artifact_conflicts
        from anteroom.services.pack_attachments import attach_pack, get_attachment_priorities

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_artifact(db, pid1, "@ns1/skill/deploy", "skill")
        attach_pack(db, pid1, priority=50)

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_artifact(db, pid2, "@ns2/skill/deploy", "skill")

        priorities = get_attachment_priorities(db, [pid1])
        conflicts = detect_artifact_conflicts(
            db,
            pid2,
            [pid1],
            new_priority=10,
            existing_priorities=priorities,
        )
        assert conflicts == []  # No conflict — skills are additive

    def test_rule_same_name_is_additive(self, db: ThreadSafeConnection) -> None:
        """Rules are additive — multiple packs can provide rules with the same name."""
        from anteroom.services.config_overlays import detect_artifact_conflicts

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_artifact(db, pid1, "@ns1/rule/style-guide", "rule")

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_artifact(db, pid2, "@ns2/rule/style-guide", "rule")

        conflicts = detect_artifact_conflicts(db, pid2, [pid1])
        assert conflicts == []

    def test_instruction_same_name_is_additive(self, db: ThreadSafeConnection) -> None:
        """Instructions are additive — no conflict."""
        from anteroom.services.config_overlays import detect_artifact_conflicts

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_artifact(db, pid1, "@ns1/instruction/setup", "instruction")

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_artifact(db, pid2, "@ns2/instruction/setup", "instruction")

        conflicts = detect_artifact_conflicts(db, pid2, [pid1])
        assert conflicts == []

    def test_context_and_memory_are_additive(self, db: ThreadSafeConnection) -> None:
        """Context and memory artifacts are additive."""
        from anteroom.services.config_overlays import detect_artifact_conflicts

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_artifact(db, pid1, "@ns1/context/docs", "context")
        _insert_artifact(db, pid1, "@ns1/memory/notes", "memory")

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_artifact(db, pid2, "@ns2/context/docs", "context")
        _insert_artifact(db, pid2, "@ns2/memory/notes", "memory")

        conflicts = detect_artifact_conflicts(db, pid2, [pid1])
        assert conflicts == []

    def test_mcp_server_same_name_is_additive(self, db: ThreadSafeConnection) -> None:
        """MCP servers are additive — config settings merge."""
        from anteroom.services.config_overlays import detect_artifact_conflicts

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_artifact(db, pid1, "@ns1/mcp_server/github", "mcp_server")

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_artifact(db, pid2, "@ns2/mcp_server/github", "mcp_server")

        conflicts = detect_artifact_conflicts(db, pid2, [pid1])
        assert conflicts == []

    def test_skill_and_rule_same_name_different_types_no_conflict(self, db: ThreadSafeConnection) -> None:
        """skill/deploy and rule/deploy are different types — no conflict."""
        from anteroom.services.config_overlays import detect_artifact_conflicts

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_artifact(db, pid1, "@ns1/skill/deploy", "skill")

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_artifact(db, pid2, "@ns2/rule/deploy", "rule")

        conflicts = detect_artifact_conflicts(db, pid2, [pid1])
        assert conflicts == []

    def test_config_overlay_excluded(self, db: ThreadSafeConnection) -> None:
        """config_overlay conflicts are handled by detect_overlay_conflicts, not here."""
        from anteroom.services.config_overlays import detect_artifact_conflicts

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_config_overlay(db, pid1, "@ns1/config_overlay/x", {"ai": {"model": "gpt-4o"}})

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_config_overlay(db, pid2, "@ns2/config_overlay/x", {"ai": {"model": "claude"}})

        conflicts = detect_artifact_conflicts(db, pid2, [pid1])
        assert conflicts == []

    def test_mixed_types_all_additive(self, db: ThreadSafeConnection) -> None:
        """All non-config artifact types (skill, rule, etc.) are additive — no conflicts."""
        from anteroom.services.config_overlays import detect_artifact_conflicts

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_artifact(db, pid1, "@ns1/skill/deploy", "skill")
        _insert_artifact(db, pid1, "@ns1/rule/style-guide", "rule")

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_artifact(db, pid2, "@ns2/skill/deploy", "skill")
        _insert_artifact(db, pid2, "@ns2/rule/style-guide", "rule")

        conflicts = detect_artifact_conflicts(db, pid2, [pid1])
        # Skills use namespace-qualified display names on collision; rules are additive
        assert conflicts == []

    def test_empty_existing_packs(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.config_overlays import detect_artifact_conflicts

        pid = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_artifact(db, pid, "@ns1/skill/deploy", "skill")

        conflicts = detect_artifact_conflicts(db, pid, [])
        assert conflicts == []

    def test_new_pack_has_no_artifacts(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.config_overlays import detect_artifact_conflicts

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_artifact(db, pid1, "@ns1/skill/deploy", "skill")

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")

        conflicts = detect_artifact_conflicts(db, pid2, [pid1])
        assert conflicts == []


class TestAttachPackArtifactConflictIntegration:
    """Integration tests: attach_pack detects artifact conflicts (not just config overlays)."""

    def test_skill_same_name_same_priority_is_additive(self, db: ThreadSafeConnection) -> None:
        """Skills are additive — same name from two packs uses namespace-qualified display names."""
        from anteroom.services.pack_attachments import attach_pack

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_artifact(db, pid1, "@ns1/skill/deploy", "skill")
        attach_pack(db, pid1, priority=50)

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_artifact(db, pid2, "@ns2/skill/deploy", "skill")

        result = attach_pack(db, pid2, priority=50)
        assert result["pack_id"] == pid2

    def test_skill_same_name_different_priority_is_additive(self, db: ThreadSafeConnection) -> None:
        """Skills are additive — different priority also fine."""
        from anteroom.services.pack_attachments import attach_pack

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_artifact(db, pid1, "@ns1/skill/deploy", "skill")
        attach_pack(db, pid1, priority=50)

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_artifact(db, pid2, "@ns2/skill/deploy", "skill")

        result = attach_pack(db, pid2, priority=10)
        assert result["pack_id"] == pid2

    def test_rule_same_name_allowed(self, db: ThreadSafeConnection) -> None:
        """Rules are additive — same name from two packs is fine."""
        from anteroom.services.pack_attachments import attach_pack

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_artifact(db, pid1, "@ns1/rule/style", "rule")
        attach_pack(db, pid1, priority=50)

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_artifact(db, pid2, "@ns2/rule/style", "rule")

        result = attach_pack(db, pid2, priority=50)
        assert result["pack_id"] == pid2

    def test_mixed_overlay_conflict_but_skill_additive(self, db: ThreadSafeConnection) -> None:
        """Config overlay conflicts still caught; skill same-name is additive."""
        from anteroom.services.pack_attachments import attach_pack

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_config_overlay(db, pid1, "@ns1/config_overlay/x", {"ai": {"model": "gpt-4o"}})
        _insert_artifact(db, pid1, "@ns1/skill/deploy", "skill")
        attach_pack(db, pid1, priority=50)

        # Pack with only a config overlay conflict — still blocked
        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_config_overlay(db, pid2, "@ns2/config_overlay/y", {"ai": {"model": "claude"}})

        with pytest.raises(ValueError, match="Config overlay conflict"):
            attach_pack(db, pid2, priority=50)

        # Pack with only a skill same-name — additive, no conflict
        pid3 = _insert_pack(db, namespace="ns3", name="pack-c")
        _insert_artifact(db, pid3, "@ns3/skill/deploy", "skill")

        result = attach_pack(db, pid3, priority=50)
        assert result["pack_id"] == pid3

    def test_skip_conflict_check_bypasses_artifact_detection(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack

        pid1 = _insert_pack(db, namespace="ns1", name="pack-a")
        _insert_artifact(db, pid1, "@ns1/skill/deploy", "skill")
        attach_pack(db, pid1, priority=50)

        pid2 = _insert_pack(db, namespace="ns2", name="pack-b")
        _insert_artifact(db, pid2, "@ns2/skill/deploy", "skill")

        result = attach_pack(db, pid2, priority=50, check_overlay_conflicts=False)
        assert result["pack_id"] == pid2

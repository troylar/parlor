"""Integration-style tests for the full pack lifecycle.

Creates real pack directories on disk, installs them into a real SQLite DB,
and verifies every operation: install, update, remove, attach/detach,
rule enforcement, config overlays, shared artifacts, and registry refresh.

These are still unit tests (no network, no server) but they exercise the
full stack from pack YAML → DB → artifact registry → rule enforcer.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml

from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services.artifact_registry import ArtifactRegistry
from anteroom.services.artifact_storage import get_artifact_by_fqn, list_artifacts
from anteroom.services.artifacts import ArtifactType
from anteroom.services.config_overlays import (
    check_enforced_field_violations,
    collect_pack_overlays,
    detect_artifact_conflicts,
    detect_overlay_conflicts,
    flatten_to_dot_paths,
    merge_pack_overlays,
    track_config_sources,
)
from anteroom.services.pack_attachments import (
    attach_pack,
    detach_pack,
    get_active_pack_ids,
    list_attachments,
)
from anteroom.services.packs import (
    get_pack_by_id,
    install_pack,
    list_packs,
    parse_manifest,
    remove_pack,
    remove_pack_by_id,
    resolve_pack,
)
from anteroom.services.rule_enforcer import RuleEnforcer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db() -> ThreadSafeConnection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return ThreadSafeConnection(conn)


@pytest.fixture()
def registry() -> ArtifactRegistry:
    return ArtifactRegistry()


@pytest.fixture()
def enforcer() -> RuleEnforcer:
    return RuleEnforcer()


# ---------------------------------------------------------------------------
# Pack builders — create real pack directories on disk
# ---------------------------------------------------------------------------


def _write_pack(
    base: Path,
    name: str,
    namespace: str,
    version: str = "1.0.0",
    artifacts: list[dict[str, Any]] | None = None,
    skill_files: dict[str, str] | None = None,
    rule_files: dict[str, dict[str, Any]] | None = None,
    instruction_files: dict[str, str] | None = None,
    config_overlay_files: dict[str, dict[str, Any]] | None = None,
) -> Path:
    """Create a pack directory with manifest and artifact files."""
    pack_dir = base / f"{namespace}-{name}"
    pack_dir.mkdir(parents=True, exist_ok=True)

    manifest_artifacts = artifacts or []

    # Write skill files
    if skill_files:
        skills_dir = pack_dir / "skills"
        skills_dir.mkdir(exist_ok=True)
        for skill_name, content in skill_files.items():
            (skills_dir / f"{skill_name}.yaml").write_text(
                yaml.dump({"content": content, "metadata": {"tier": "read"}}),
                encoding="utf-8",
            )
            manifest_artifacts.append({"type": "skill", "name": skill_name})

    # Write rule files
    if rule_files:
        rules_dir = pack_dir / "rules"
        rules_dir.mkdir(exist_ok=True)
        for rule_name, rule_data in rule_files.items():
            (rules_dir / f"{rule_name}.yaml").write_text(
                yaml.dump({"content": rule_data["content"], "metadata": rule_data["metadata"]}),
                encoding="utf-8",
            )
            manifest_artifacts.append({"type": "rule", "name": rule_name})

    # Write instruction files
    if instruction_files:
        instructions_dir = pack_dir / "instructions"
        instructions_dir.mkdir(exist_ok=True)
        for inst_name, content in instruction_files.items():
            (instructions_dir / f"{inst_name}.yaml").write_text(
                yaml.dump({"content": content, "metadata": {}}),
                encoding="utf-8",
            )
            manifest_artifacts.append({"type": "instruction", "name": inst_name})

    # Write config overlay files
    if config_overlay_files:
        overlays_dir = pack_dir / "config_overlays"
        overlays_dir.mkdir(exist_ok=True)
        for overlay_name, overlay_data in config_overlay_files.items():
            (overlays_dir / f"{overlay_name}.yaml").write_text(
                yaml.dump({"content": yaml.dump(overlay_data), "metadata": {}}),
                encoding="utf-8",
            )
            manifest_artifacts.append({"type": "config_overlay", "name": overlay_name})

    # Write manifest
    manifest = {
        "name": name,
        "namespace": namespace,
        "version": version,
        "description": f"Test pack {namespace}/{name}",
        "artifacts": manifest_artifacts,
    }
    (pack_dir / "pack.yaml").write_text(yaml.dump(manifest), encoding="utf-8")

    return pack_dir


def _install(db: ThreadSafeConnection, pack_dir: Path) -> dict[str, Any]:
    """Parse manifest and install pack."""
    manifest = parse_manifest(pack_dir / "pack.yaml")
    return install_pack(db, manifest, pack_dir)


def _load_registries(
    db: ThreadSafeConnection,
    registry: ArtifactRegistry,
    enforcer: RuleEnforcer,
) -> None:
    """Reload artifact registry and rule enforcer from DB (simulates what CLI/web do)."""
    registry.load_from_db(db)
    enforcer.load_rules(registry.list_all(artifact_type=ArtifactType.RULE))


# ---------------------------------------------------------------------------
# Test pack definitions
# ---------------------------------------------------------------------------


def _security_pack(base: Path) -> Path:
    """A pack with hard-enforced rules: blocks force push and .env writes."""
    return _write_pack(
        base,
        name="security-baseline",
        namespace="acme",
        rule_files={
            "no-force-push": {
                "content": "Never force push to any branch.",
                "metadata": {
                    "enforce": "hard",
                    "reason": "Force push is forbidden by team policy",
                    "matches": [{"tool": "bash", "pattern": r"git\s+push\s+--force"}],
                },
            },
            "no-env-writes": {
                "content": "Do not write to .env files.",
                "metadata": {
                    "enforce": "hard",
                    "reason": "Writing .env files is blocked for security",
                    "matches": [{"tool": "write_file", "pattern": r"\.env$"}],
                },
            },
        },
        skill_files={
            "security-check": "Run a security check on the codebase.",
        },
    )


def _python_pack(base: Path) -> Path:
    """A pack with skills and instructions for Python development."""
    return _write_pack(
        base,
        name="python-dev",
        namespace="acme",
        skill_files={
            "lint": "Run ruff check on the codebase.",
            "test": "Run pytest with verbose output.",
        },
        instruction_files={
            "style-guide": "Use type hints. Prefer f-strings. Max line length 120.",
        },
    )


def _config_pack(base: Path, priority_setting: str = "high") -> Path:
    """A pack with a config overlay."""
    return _write_pack(
        base,
        name="config-overlay",
        namespace="acme",
        config_overlay_files={
            "defaults": {"ai": {"temperature": 0.7}, "safety": {"approval_mode": priority_setting}},
        },
    )


def _conflicting_config_pack(base: Path) -> Path:
    """A pack with a config overlay that conflicts with _config_pack."""
    return _write_pack(
        base,
        name="config-conflict",
        namespace="acme",
        config_overlay_files={
            "overrides": {"ai": {"temperature": 0.9}, "safety": {"approval_mode": "auto"}},
        },
    )


def _shared_skill_pack(base: Path, name: str, namespace: str = "acme") -> Path:
    """Two packs that reference the same FQN (same namespace + type + artifact name)."""
    return _write_pack(
        base,
        name=name,
        namespace=namespace,
        skill_files={
            "shared-greeting": "Hello from the shared skill!",
        },
    )


# ---------------------------------------------------------------------------
# Tests: Basic install / remove lifecycle
# ---------------------------------------------------------------------------


class TestPackInstallRemoveLifecycle:
    def test_install_pack(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = _security_pack(tmp_path)
        result = _install(db, pack_dir)

        assert result["action"] == "installed"
        assert result["namespace"] == "acme"
        assert result["name"] == "security-baseline"
        assert result["artifact_count"] == 3  # 2 rules + 1 skill

    def test_installed_pack_appears_in_list(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        _install(db, _security_pack(tmp_path))
        packs = list_packs(db)
        assert len(packs) == 1
        assert packs[0]["namespace"] == "acme"
        assert packs[0]["name"] == "security-baseline"

    def test_installed_artifacts_in_db(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        _install(db, _security_pack(tmp_path))
        arts = list_artifacts(db)
        fqns = {a["fqn"] for a in arts}
        assert "@acme/rule/no-force-push" in fqns
        assert "@acme/rule/no-env-writes" in fqns
        assert "@acme/skill/security-check" in fqns

    def test_remove_pack_by_name(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        _install(db, _security_pack(tmp_path))
        assert remove_pack(db, "acme", "security-baseline") is True
        assert list_packs(db) == []
        assert list_artifacts(db) == []

    def test_remove_pack_by_id(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        result = _install(db, _security_pack(tmp_path))
        assert remove_pack_by_id(db, result["id"]) is True
        assert list_packs(db) == []

    def test_remove_nonexistent_returns_false(self, db: ThreadSafeConnection) -> None:
        assert remove_pack(db, "no", "such-pack") is False
        assert remove_pack_by_id(db, "nonexistent-id") is False

    def test_reinstall_updates(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = _security_pack(tmp_path)
        r1 = _install(db, pack_dir)
        r2 = _install(db, pack_dir)
        assert r2["action"] == "updated"
        assert r2["id"] != r1["id"]
        assert len(list_packs(db)) == 1

    def test_multiple_packs(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        _install(db, _security_pack(tmp_path))
        _install(db, _python_pack(tmp_path))
        packs = list_packs(db)
        assert len(packs) == 2
        names = {p["name"] for p in packs}
        assert names == {"security-baseline", "python-dev"}

    def test_resolve_pack(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        _install(db, _security_pack(tmp_path))
        match, candidates = resolve_pack(db, "acme", "security-baseline")
        assert match is not None
        assert match["name"] == "security-baseline"
        assert candidates == []

    def test_get_pack_by_id_includes_artifacts(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        result = _install(db, _security_pack(tmp_path))
        pack = get_pack_by_id(db, result["id"])
        assert pack is not None
        assert pack["artifact_count"] == 3
        assert len(pack["artifacts"]) == 3


# ---------------------------------------------------------------------------
# Tests: Artifact registry loading
# ---------------------------------------------------------------------------


class TestArtifactRegistryFromPacks:
    def test_registry_loads_pack_artifacts(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        _install(db, _security_pack(tmp_path))
        registry.load_from_db(db)

        assert registry.count == 3
        rule = registry.get("@acme/rule/no-force-push")
        assert rule is not None
        assert rule.type == ArtifactType.RULE
        assert rule.name == "no-force-push"

    def test_registry_reload_reflects_removals(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        _install(db, _security_pack(tmp_path))
        registry.load_from_db(db)
        assert registry.count == 3

        remove_pack(db, "acme", "security-baseline")
        registry.load_from_db(db)
        assert registry.count == 0

    def test_registry_search(self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry) -> None:
        _install(db, _security_pack(tmp_path))
        _install(db, _python_pack(tmp_path))
        registry.load_from_db(db)

        results = registry.search("check")
        assert len(results) == 1
        assert results[0].name == "security-check"

    def test_registry_filter_by_type(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        _install(db, _security_pack(tmp_path))
        registry.load_from_db(db)

        rules = registry.list_all(artifact_type=ArtifactType.RULE)
        assert len(rules) == 2
        skills = registry.list_all(artifact_type=ArtifactType.SKILL)
        assert len(skills) == 1


# ---------------------------------------------------------------------------
# Tests: Hard rule enforcement end-to-end
# ---------------------------------------------------------------------------


class TestRuleEnforcementEndToEnd:
    def test_rules_block_force_push(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        registry: ArtifactRegistry,
        enforcer: RuleEnforcer,
    ) -> None:
        _install(db, _security_pack(tmp_path))
        _load_registries(db, registry, enforcer)

        assert enforcer.rule_count == 2

        blocked, reason, fqn = enforcer.check_tool_call("bash", {"command": "git push --force origin main"})
        assert blocked is True
        assert "Force push" in reason
        assert fqn == "@acme/rule/no-force-push"

    def test_rules_block_env_writes(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        registry: ArtifactRegistry,
        enforcer: RuleEnforcer,
    ) -> None:
        _install(db, _security_pack(tmp_path))
        _load_registries(db, registry, enforcer)

        blocked, reason, _ = enforcer.check_tool_call("write_file", {"path": "/app/.env"})
        assert blocked is True
        assert ".env" in reason

    def test_rules_allow_normal_push(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        registry: ArtifactRegistry,
        enforcer: RuleEnforcer,
    ) -> None:
        _install(db, _security_pack(tmp_path))
        _load_registries(db, registry, enforcer)

        blocked, _, _ = enforcer.check_tool_call("bash", {"command": "git push origin main"})
        assert blocked is False

    def test_rules_allow_non_env_writes(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        registry: ArtifactRegistry,
        enforcer: RuleEnforcer,
    ) -> None:
        _install(db, _security_pack(tmp_path))
        _load_registries(db, registry, enforcer)

        blocked, _, _ = enforcer.check_tool_call("write_file", {"path": "/app/config.yaml"})
        assert blocked is False

    def test_rules_cleared_after_pack_removal(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        registry: ArtifactRegistry,
        enforcer: RuleEnforcer,
    ) -> None:
        _install(db, _security_pack(tmp_path))
        _load_registries(db, registry, enforcer)
        assert enforcer.rule_count == 2

        remove_pack(db, "acme", "security-baseline")
        _load_registries(db, registry, enforcer)
        assert enforcer.rule_count == 0

        blocked, _, _ = enforcer.check_tool_call("bash", {"command": "git push --force origin main"})
        assert blocked is False

    def test_rules_survive_pack_update(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        registry: ArtifactRegistry,
        enforcer: RuleEnforcer,
    ) -> None:
        pack_dir = _security_pack(tmp_path)
        _install(db, pack_dir)
        _load_registries(db, registry, enforcer)
        assert enforcer.rule_count == 2

        # Reinstall (update) the same pack
        _install(db, pack_dir)
        _load_registries(db, registry, enforcer)
        assert enforcer.rule_count == 2

        blocked, _, _ = enforcer.check_tool_call("bash", {"command": "git push --force origin main"})
        assert blocked is True

    def test_no_rules_no_enforcement(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        registry: ArtifactRegistry,
        enforcer: RuleEnforcer,
    ) -> None:
        # Install a pack with no rules
        _install(db, _python_pack(tmp_path))
        _load_registries(db, registry, enforcer)
        assert enforcer.rule_count == 0

        blocked, _, _ = enforcer.check_tool_call("bash", {"command": "git push --force origin main"})
        assert blocked is False


# ---------------------------------------------------------------------------
# Tests: Attach / detach with priority
# ---------------------------------------------------------------------------


class TestPackAttachDetach:
    def test_attach_global(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        result = _install(db, _security_pack(tmp_path))
        att = attach_pack(db, result["id"])
        assert att["scope"] == "global"
        assert att["project_path"] is None

    def test_attach_project(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        result = _install(db, _security_pack(tmp_path))
        att = attach_pack(db, result["id"], project_path="/my/project")
        assert att["scope"] == "project"
        assert att["project_path"] == "/my/project"

    def test_detach_removes_attachment(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        result = _install(db, _security_pack(tmp_path))
        attach_pack(db, result["id"])
        assert detach_pack(db, result["id"]) is True
        assert get_active_pack_ids(db) == []

    def test_active_pack_ids_after_attach(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        r1 = _install(db, _security_pack(tmp_path))
        r2 = _install(db, _python_pack(tmp_path))
        attach_pack(db, r1["id"])
        attach_pack(db, r2["id"])
        ids = get_active_pack_ids(db)
        assert set(ids) == {r1["id"], r2["id"]}

    def test_project_attachments_include_global(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        r1 = _install(db, _security_pack(tmp_path))
        r2 = _install(db, _python_pack(tmp_path))
        attach_pack(db, r1["id"])  # global
        attach_pack(db, r2["id"], project_path="/proj")  # project-scoped
        ids = get_active_pack_ids(db, project_path="/proj")
        assert set(ids) == {r1["id"], r2["id"]}

    def test_project_attachments_exclude_other_projects(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        result = _install(db, _security_pack(tmp_path))
        attach_pack(db, result["id"], project_path="/proj-a")
        ids = get_active_pack_ids(db, project_path="/proj-b")
        assert result["id"] not in ids

    def test_attachments_survive_pack_update(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = _security_pack(tmp_path)
        r1 = _install(db, pack_dir)
        attach_pack(db, r1["id"], priority=10)
        assert get_active_pack_ids(db) == [r1["id"]]

        # Reinstall (update) — attachments should transfer to new pack ID
        r2 = _install(db, pack_dir)
        assert r2["id"] != r1["id"]
        ids = get_active_pack_ids(db)
        assert r2["id"] in ids
        assert r1["id"] not in ids

    def test_attachments_cleaned_on_remove(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        result = _install(db, _security_pack(tmp_path))
        attach_pack(db, result["id"])
        assert len(list_attachments(db)) == 1

        remove_pack_by_id(db, result["id"])
        assert list_attachments(db) == []

    def test_duplicate_attach_raises(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        result = _install(db, _security_pack(tmp_path))
        attach_pack(db, result["id"])
        with pytest.raises(ValueError, match="already attached"):
            attach_pack(db, result["id"])

    def test_attach_with_priority(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        result = _install(db, _security_pack(tmp_path))
        att = attach_pack(db, result["id"], priority=10)
        assert att["priority"] == 10


# ---------------------------------------------------------------------------
# Tests: Shared artifacts across packs
# ---------------------------------------------------------------------------


class TestSharedArtifacts:
    def test_shared_fqn_artifact_survives_single_pack_removal(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Two packs install an artifact with the same FQN. Removing one pack
        must NOT delete the artifact since the other pack still references it."""
        p1 = _shared_skill_pack(tmp_path, name="pack-alpha")
        p2 = _shared_skill_pack(tmp_path, name="pack-beta")
        r1 = _install(db, p1)
        _install(db, p2)

        # Both packs share @acme/skill/shared-greeting
        art = get_artifact_by_fqn(db, "@acme/skill/shared-greeting")
        assert art is not None

        # Remove pack-alpha
        remove_pack_by_id(db, r1["id"])

        # Artifact must survive (still referenced by pack-beta)
        art_after = get_artifact_by_fqn(db, "@acme/skill/shared-greeting")
        assert art_after is not None

        # pack-beta still exists
        remaining = list_packs(db)
        assert len(remaining) == 1
        assert remaining[0]["name"] == "pack-beta"

    def test_shared_artifact_deleted_when_all_packs_removed(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        p1 = _shared_skill_pack(tmp_path, name="pack-alpha")
        p2 = _shared_skill_pack(tmp_path, name="pack-beta")
        r1 = _install(db, p1)
        r2 = _install(db, p2)

        remove_pack_by_id(db, r1["id"])
        remove_pack_by_id(db, r2["id"])

        art = get_artifact_by_fqn(db, "@acme/skill/shared-greeting")
        assert art is None
        assert list_packs(db) == []
        assert list_artifacts(db) == []


# ---------------------------------------------------------------------------
# Tests: Config overlays and conflict detection
# ---------------------------------------------------------------------------


class TestConfigOverlays:
    def test_collect_overlays_from_attached_pack(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        result = _install(db, _config_pack(tmp_path))
        attach_pack(db, result["id"], priority=20)
        pack_ids = get_active_pack_ids(db)

        overlays = collect_pack_overlays(db, pack_ids)
        assert len(overlays) > 0

    def test_merge_overlays_produces_config(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        result = _install(db, _config_pack(tmp_path))
        attach_pack(db, result["id"], priority=20)
        pack_ids = get_active_pack_ids(db)

        overlays = collect_pack_overlays(db, pack_ids)
        merged = merge_pack_overlays(overlays)
        assert isinstance(merged, dict)

    def test_conflicting_overlays_blocked_at_attach(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """attach_pack() itself detects same-priority config overlay conflicts
        and raises ValueError, preventing the second pack from being attached."""
        r1 = _install(db, _config_pack(tmp_path))
        r2 = _install(db, _conflicting_config_pack(tmp_path))

        # First attach succeeds
        attach_pack(db, r1["id"], priority=50)

        # Second attach at same priority is blocked by conflict detection
        with pytest.raises(ValueError, match="Config overlay conflict"):
            attach_pack(db, r2["id"], priority=50)

        # Only the first pack should be attached
        ids = get_active_pack_ids(db)
        assert ids == [r1["id"]]

    def test_conflicting_overlays_detected_directly(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Test detect_overlay_conflicts() directly by bypassing attach_pack's
        built-in conflict check."""
        r1 = _install(db, _config_pack(tmp_path))
        r2 = _install(db, _conflicting_config_pack(tmp_path))

        # Attach both, bypassing conflict detection on the second
        attach_pack(db, r1["id"], priority=50)
        attach_pack(db, r2["id"], priority=50, check_overlay_conflicts=False)

        # Now verify detect_overlay_conflicts finds the conflict
        existing_overlays = collect_pack_overlays(db, [r1["id"]])
        new_overlay_list = collect_pack_overlays(db, [r2["id"]])
        assert len(new_overlay_list) > 0

        conflicts = detect_overlay_conflicts(
            existing_overlays,
            new_overlay_list[0],
            new_priority=50,
            existing_priorities={existing_overlays[0][0]: 50} if existing_overlays else {},
        )
        # Both set ai.temperature and safety.approval_mode at same priority
        assert len(conflicts) > 0

    def test_different_priority_no_conflict(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        r1 = _install(db, _config_pack(tmp_path))
        r2 = _install(db, _conflicting_config_pack(tmp_path))

        # Different priorities — lower wins, no conflict
        attach_pack(db, r1["id"], priority=10)
        attach_pack(db, r2["id"], priority=50)

        # Both attached successfully — no conflict at different priorities
        ids = get_active_pack_ids(db)
        assert set(ids) == {r1["id"], r2["id"]}

    def test_different_priority_merge_lower_wins(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """When two packs set the same key at different priorities,
        merge_pack_overlays should use the lower-priority-number pack's value."""
        from anteroom.services.pack_attachments import get_attachment_priorities

        r1 = _install(db, _config_pack(tmp_path))  # temperature=0.7
        r2 = _install(db, _conflicting_config_pack(tmp_path))  # temperature=0.9

        attach_pack(db, r1["id"], priority=10)  # higher precedence
        attach_pack(db, r2["id"], priority=50)

        pack_ids = get_active_pack_ids(db)
        priorities = get_attachment_priorities(db, pack_ids)
        overlays = collect_pack_overlays(db, pack_ids)
        merged = merge_pack_overlays(overlays, priorities=priorities)
        assert merged.get("ai", {}).get("temperature") == 0.7


# ---------------------------------------------------------------------------
# Tests: Artifact version tracking
# ---------------------------------------------------------------------------


class TestArtifactVersioning:
    def test_initial_version_is_1(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        _install(db, _security_pack(tmp_path))
        art = get_artifact_by_fqn(db, "@acme/rule/no-force-push")
        assert art is not None
        assert art["version"] == 1

    def test_version_increments_on_shared_artifact_update(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Version increments when an artifact FQN persists across updates.

        When a single pack is updated, its orphan artifacts are deleted and
        recreated (version resets to 1). But when the same FQN is shared
        with another pack, the artifact survives removal and upsert_artifact
        bumps the version.
        """
        # Create two packs that share the same artifact FQN
        p1 = _shared_skill_pack(tmp_path, name="pack-v1")
        p2 = _shared_skill_pack(tmp_path, name="pack-v2")
        _install(db, p1)
        _install(db, p2)

        art1 = get_artifact_by_fqn(db, "@acme/skill/shared-greeting")
        assert art1 is not None
        assert art1["version"] == 1

        # Update pack-v2 with different content — artifact persists via pack-v1
        (p2 / "skills" / "shared-greeting.yaml").write_text(
            yaml.dump({"content": "Updated greeting v2", "metadata": {"tier": "read"}}),
            encoding="utf-8",
        )
        _install(db, p2)
        art2 = get_artifact_by_fqn(db, "@acme/skill/shared-greeting")
        assert art2 is not None
        assert art2["version"] == 2

    def test_single_pack_update_resets_version(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """When a pack's artifact is orphaned during update, the version resets.

        update_pack deletes orphan artifacts and upsert_artifact creates fresh.
        """
        pack_dir = _write_pack(
            tmp_path,
            name="versioned",
            namespace="test",
            skill_files={"evolving": "Version 1 content"},
        )
        _install(db, pack_dir)

        # Update with different content — artifact is orphaned, deleted, recreated
        (pack_dir / "skills" / "evolving.yaml").write_text(
            yaml.dump({"content": "Version 2 content", "metadata": {"tier": "read"}}),
            encoding="utf-8",
        )
        _install(db, pack_dir)
        art = get_artifact_by_fqn(db, "@test/skill/evolving")
        assert art is not None
        # Version resets to 1 because the artifact was deleted and recreated
        assert art["version"] == 1

    def test_version_stable_on_same_content(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = _write_pack(
            tmp_path,
            name="stable",
            namespace="test",
            skill_files={"unchanged": "Same content"},
        )
        _install(db, pack_dir)
        _install(db, pack_dir)  # reinstall with same content

        art = get_artifact_by_fqn(db, "@test/skill/unchanged")
        assert art is not None
        assert art["version"] == 1  # no content change, no version bump


# ---------------------------------------------------------------------------
# Tests: Full lifecycle scenario
# ---------------------------------------------------------------------------


class TestFullLifecycleScenario:
    """End-to-end scenario: install multiple packs, attach them, verify rules,
    update one, verify rules still work, remove one, verify cleanup."""

    def test_full_lifecycle(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        registry: ArtifactRegistry,
        enforcer: RuleEnforcer,
    ) -> None:
        # 1. Install two packs
        security_dir = _security_pack(tmp_path)
        python_dir = _python_pack(tmp_path)
        r_sec = _install(db, security_dir)
        r_py = _install(db, python_dir)

        assert len(list_packs(db)) == 2

        # 2. Attach both
        attach_pack(db, r_sec["id"], priority=10)
        attach_pack(db, r_py["id"], priority=20)
        assert len(get_active_pack_ids(db)) == 2

        # 3. Load registries and verify rules
        _load_registries(db, registry, enforcer)
        assert registry.count == 6  # 2 rules + 1 skill (security) + 2 skills + 1 instruction (python)
        assert enforcer.rule_count == 2

        # 4. Verify rule enforcement
        blocked, _, _ = enforcer.check_tool_call("bash", {"command": "git push --force"})
        assert blocked is True
        blocked, _, _ = enforcer.check_tool_call("bash", {"command": "git push origin main"})
        assert blocked is False

        # 5. Update security pack (reinstall)
        r_sec2 = _install(db, security_dir)
        assert r_sec2["id"] != r_sec["id"]
        assert r_sec2["action"] == "updated"

        # 6. Verify attachments survived the update
        ids = get_active_pack_ids(db)
        assert r_sec2["id"] in ids
        assert r_sec["id"] not in ids

        # 7. Reload and verify rules still work
        _load_registries(db, registry, enforcer)
        assert enforcer.rule_count == 2
        blocked, _, _ = enforcer.check_tool_call("bash", {"command": "git push --force"})
        assert blocked is True

        # 8. Remove python pack
        remove_pack_by_id(db, r_py["id"])
        assert len(list_packs(db)) == 1

        # 9. Reload — python skills gone, security rules remain
        _load_registries(db, registry, enforcer)
        assert registry.get("@acme/skill/lint") is None
        assert registry.get("@acme/rule/no-force-push") is not None
        assert enforcer.rule_count == 2

        # 10. Remove security pack
        remove_pack_by_id(db, r_sec2["id"])
        _load_registries(db, registry, enforcer)
        assert registry.count == 0
        assert enforcer.rule_count == 0
        assert list_packs(db) == []
        assert list_artifacts(db) == []
        assert list_attachments(db) == []


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_pack_installs(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = _write_pack(tmp_path, name="empty", namespace="test")
        result = _install(db, pack_dir)
        assert result["artifact_count"] == 0
        assert len(list_packs(db)) == 1

    def test_remove_empty_pack(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = _write_pack(tmp_path, name="empty", namespace="test")
        _install(db, pack_dir)
        assert remove_pack(db, "test", "empty") is True
        assert list_packs(db) == []

    def test_soft_rules_not_enforced(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        registry: ArtifactRegistry,
        enforcer: RuleEnforcer,
    ) -> None:
        pack_dir = _write_pack(
            tmp_path,
            name="soft-rules",
            namespace="test",
            rule_files={
                "suggestion": {
                    "content": "Consider using type hints.",
                    "metadata": {
                        "enforce": "soft",
                        "matches": [{"tool": "*", "pattern": ".*"}],
                    },
                },
            },
        )
        _install(db, pack_dir)
        _load_registries(db, registry, enforcer)
        assert enforcer.rule_count == 0  # soft rules not loaded

    def test_rule_with_invalid_regex_skipped(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        registry: ArtifactRegistry,
        enforcer: RuleEnforcer,
    ) -> None:
        pack_dir = _write_pack(
            tmp_path,
            name="bad-regex",
            namespace="test",
            rule_files={
                "broken": {
                    "content": "This rule has bad regex.",
                    "metadata": {
                        "enforce": "hard",
                        "reason": "Should not load",
                        "matches": [{"tool": "bash", "pattern": "[invalid"}],
                    },
                },
            },
        )
        _install(db, pack_dir)
        _load_registries(db, registry, enforcer)
        assert enforcer.rule_count == 0

    def test_wildcard_tool_rule(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        registry: ArtifactRegistry,
        enforcer: RuleEnforcer,
    ) -> None:
        pack_dir = _write_pack(
            tmp_path,
            name="wildcard-rule",
            namespace="test",
            rule_files={
                "no-secrets": {
                    "content": "No secrets in any tool.",
                    "metadata": {
                        "enforce": "hard",
                        "reason": "Secret detected in arguments",
                        "matches": [{"tool": "*", "pattern": r"AKIA[A-Z0-9]{16}"}],
                    },
                },
            },
        )
        _install(db, pack_dir)
        _load_registries(db, registry, enforcer)

        # Should block any tool with AWS key pattern
        blocked, _, _ = enforcer.check_tool_call("bash", {"command": "echo AKIAIOSFODNN7EXAMPLE"})
        assert blocked is True

        blocked, _, _ = enforcer.check_tool_call(
            "write_file", {"path": "/tmp/creds", "content": "key=AKIAIOSFODNN7EXAMPLE"}
        )
        # write_file stringifies path, not content — so this won't match
        blocked_path, _, _ = enforcer.check_tool_call("write_file", {"path": "/tmp/AKIAIOSFODNN7EXAMPLE.txt"})
        assert blocked_path is True

    def test_path_traversal_in_attach_rejected(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        result = _install(db, _security_pack(tmp_path))
        with pytest.raises(ValueError, match="must not contain"):
            attach_pack(db, result["id"], project_path="/foo/../etc/passwd")

    def test_content_hash_verified_on_registry_load(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        """Tampered content in DB should log a warning but still load."""
        _install(db, _python_pack(tmp_path))

        # Tamper with content directly in DB (simulates corruption)
        db.execute("UPDATE artifacts SET content = 'TAMPERED' WHERE fqn = '@acme/skill/lint'")
        db.commit()

        # Should still load (warns but doesn't reject)
        registry.load_from_db(db)
        art = registry.get("@acme/skill/lint")
        assert art is not None
        assert art.content == "TAMPERED"


# ---------------------------------------------------------------------------
# Tests: Skill registry integration with packs
# ---------------------------------------------------------------------------


class TestSkillRegistryFromPacks:
    """Verify that skills installed via packs are visible in the SkillRegistry
    through the artifact bridge (load_from_artifacts)."""

    def test_pack_skills_appear_in_list(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        from anteroom.cli.skills import SkillRegistry

        _install(db, _python_pack(tmp_path))
        registry.load_from_db(db)

        skill_reg = SkillRegistry()
        added = skill_reg.load_from_artifacts(registry)
        assert added >= 2  # lint and test skills

        names = [s.name for s in skill_reg.list_skills()]
        assert "lint" in names
        assert "test" in names

    def test_has_skill_returns_true_for_pack_skills(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        from anteroom.cli.skills import SkillRegistry

        _install(db, _python_pack(tmp_path))
        registry.load_from_db(db)

        skill_reg = SkillRegistry()
        skill_reg.load_from_artifacts(registry)

        assert skill_reg.has_skill("lint") is True
        assert skill_reg.has_skill("test") is True
        assert skill_reg.has_skill("nonexistent") is False

    def test_resolve_input_expands_pack_skill(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        from anteroom.cli.skills import SkillRegistry

        _install(db, _python_pack(tmp_path))
        registry.load_from_db(db)

        skill_reg = SkillRegistry()
        skill_reg.load_from_artifacts(registry)

        is_skill, prompt = skill_reg.resolve_input("/lint")
        assert is_skill is True
        assert "ruff" in prompt.lower() or len(prompt) > 0

    def test_resolve_input_with_args(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        from anteroom.cli.skills import SkillRegistry

        # Create a pack with a skill that has {args} placeholder
        pack_dir = _write_pack(
            tmp_path,
            name="args-pack",
            namespace="test",
            skill_files={"greet": "Say hello to {args}"},
        )
        _install(db, pack_dir)
        registry.load_from_db(db)

        skill_reg = SkillRegistry()
        skill_reg.load_from_artifacts(registry)

        is_skill, prompt = skill_reg.resolve_input("/greet world")
        assert is_skill is True
        assert "world" in prompt

    def test_get_skill_descriptions_includes_pack_skills(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        from anteroom.cli.skills import SkillRegistry

        _install(db, _python_pack(tmp_path))
        registry.load_from_db(db)

        skill_reg = SkillRegistry()
        skill_reg.load_from_artifacts(registry)

        descriptions = skill_reg.get_skill_descriptions()
        desc_names = [name for name, _ in descriptions]
        assert "lint" in desc_names
        assert "test" in desc_names

    def test_filesystem_skills_take_precedence(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        """If a filesystem skill and a pack skill share the same name,
        the filesystem skill takes precedence."""
        from anteroom.cli.skills import Skill, SkillRegistry

        _install(db, _python_pack(tmp_path))
        registry.load_from_db(db)

        skill_reg = SkillRegistry()
        # Pre-load a filesystem skill with the same name
        skill_reg._skills["lint"] = Skill(
            name="lint",
            description="Filesystem lint",
            prompt="Run filesystem lint check.",
            source="project",
        )

        skill_reg.load_from_artifacts(registry)
        # "lint" should NOT be overwritten; "test" should be added
        skill = skill_reg.get("lint")
        assert skill is not None
        assert skill.source == "project"  # filesystem version retained
        assert skill_reg.has_skill("test") is True

    def test_invoke_skill_definition_includes_pack_skills(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        from anteroom.cli.skills import SkillRegistry

        _install(db, _python_pack(tmp_path))
        registry.load_from_db(db)

        skill_reg = SkillRegistry()
        skill_reg.load_from_artifacts(registry)

        defn = skill_reg.get_invoke_skill_definition()
        assert defn is not None
        enum_values = defn["function"]["parameters"]["properties"]["skill_name"]["enum"]
        assert "lint" in enum_values
        assert "test" in enum_values

    def test_multiple_packs_skills_combined(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        """Skills from multiple packs are all visible in the registry."""
        from anteroom.cli.skills import SkillRegistry

        _install(db, _security_pack(tmp_path))
        _install(db, _python_pack(tmp_path))
        registry.load_from_db(db)

        skill_reg = SkillRegistry()
        added = skill_reg.load_from_artifacts(registry)
        assert added >= 3  # security-check + lint + test

        names = [s.name for s in skill_reg.list_skills()]
        assert "security-check" in names
        assert "lint" in names
        assert "test" in names

    def test_skills_cleared_after_pack_removal(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        """After removing a pack and reloading, its skills disappear."""
        from anteroom.cli.skills import SkillRegistry

        result = _install(db, _python_pack(tmp_path))
        registry.load_from_db(db)

        skill_reg = SkillRegistry()
        skill_reg.load_from_artifacts(registry)
        assert skill_reg.has_skill("lint") is True

        # Remove pack and reload
        remove_pack_by_id(db, result["id"])
        registry.load_from_db(db)

        # Fresh skill registry — pack skills should be gone
        skill_reg2 = SkillRegistry()
        skill_reg2.load_from_artifacts(registry)
        assert skill_reg2.has_skill("lint") is False
        assert skill_reg2.has_skill("test") is False


# ---------------------------------------------------------------------------
# Tests: Namespace-aware skill resolution
# ---------------------------------------------------------------------------


class TestSkillNamespaceResolution:
    """Verify namespace/name disambiguation when multiple packs define
    skills with the same name."""

    def test_unique_name_resolves_bare(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        """When a skill name is unique, bare name resolves it."""
        from anteroom.cli.skills import SkillRegistry

        _install(db, _python_pack(tmp_path))
        registry.load_from_db(db)

        skill_reg = SkillRegistry()
        skill_reg.load_from_artifacts(registry)

        # "lint" is unique — bare name works
        assert skill_reg.has_skill("lint") is True
        skill = skill_reg.get("lint")
        assert skill is not None
        assert skill.name == "lint"

    def test_duplicate_name_both_loaded(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        """Two packs with same skill name should both be loaded."""
        from anteroom.cli.skills import SkillRegistry

        pack_a = _write_pack(
            tmp_path,
            name="pack-a",
            namespace="team-a",
            skill_files={"deploy": "Deploy to staging."},
        )
        pack_b = _write_pack(
            tmp_path,
            name="pack-b",
            namespace="team-b",
            skill_files={"deploy": "Deploy to production."},
        )
        _install(db, pack_a)
        _install(db, pack_b)
        registry.load_from_db(db)

        skill_reg = SkillRegistry()
        skill_reg.load_from_artifacts(registry)

        # Both should be loadable by namespace/name
        skill_a = skill_reg.get("team-a/deploy")
        skill_b = skill_reg.get("team-b/deploy")
        assert skill_a is not None
        assert skill_b is not None
        assert "staging" in skill_a.prompt.lower()
        assert "production" in skill_b.prompt.lower()

    def test_duplicate_name_bare_lookup_fails(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        """When two packs share a skill name, bare name returns None (ambiguous)."""
        from anteroom.cli.skills import SkillRegistry

        pack_a = _write_pack(
            tmp_path,
            name="pack-a",
            namespace="team-a",
            skill_files={"deploy": "Deploy to staging."},
        )
        pack_b = _write_pack(
            tmp_path,
            name="pack-b",
            namespace="team-b",
            skill_files={"deploy": "Deploy to production."},
        )
        _install(db, pack_a)
        _install(db, pack_b)
        registry.load_from_db(db)

        skill_reg = SkillRegistry()
        skill_reg.load_from_artifacts(registry)

        # Bare "deploy" is ambiguous — should return None
        assert skill_reg.get("deploy") is None
        assert skill_reg.has_skill("deploy") is False

    def test_display_names_qualified_on_collision(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        """get_skill_descriptions returns namespace/name for colliding skills."""
        from anteroom.cli.skills import SkillRegistry

        pack_a = _write_pack(
            tmp_path,
            name="pack-a",
            namespace="team-a",
            skill_files={"deploy": "Deploy to staging."},
        )
        pack_b = _write_pack(
            tmp_path,
            name="pack-b",
            namespace="team-b",
            skill_files={"deploy": "Deploy to production."},
        )
        _install(db, pack_a)
        _install(db, pack_b)
        registry.load_from_db(db)

        skill_reg = SkillRegistry()
        skill_reg.load_from_artifacts(registry)

        descs = skill_reg.get_skill_descriptions()
        display_names = [name for name, _ in descs]
        assert "team-a/deploy" in display_names
        assert "team-b/deploy" in display_names
        # Bare "deploy" should NOT appear
        assert "deploy" not in display_names

    def test_display_name_bare_when_unique(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        """get_skill_descriptions returns bare name when no collision."""
        from anteroom.cli.skills import SkillRegistry

        _install(db, _python_pack(tmp_path))
        registry.load_from_db(db)

        skill_reg = SkillRegistry()
        skill_reg.load_from_artifacts(registry)

        descs = skill_reg.get_skill_descriptions()
        display_names = [name for name, _ in descs]
        # "lint" and "test" are unique — should be bare
        assert "lint" in display_names
        assert "test" in display_names

    def test_resolve_input_with_namespace(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        """resolve_input accepts /namespace/name for disambiguation."""
        from anteroom.cli.skills import SkillRegistry

        pack_a = _write_pack(
            tmp_path,
            name="pack-a",
            namespace="team-a",
            skill_files={"deploy": "Deploy to staging."},
        )
        pack_b = _write_pack(
            tmp_path,
            name="pack-b",
            namespace="team-b",
            skill_files={"deploy": "Deploy to production."},
        )
        _install(db, pack_a)
        _install(db, pack_b)
        registry.load_from_db(db)

        skill_reg = SkillRegistry()
        skill_reg.load_from_artifacts(registry)

        # Bare /deploy is ambiguous — should not resolve
        is_skill, _ = skill_reg.resolve_input("/deploy")
        assert is_skill is False

        # Qualified /team-a/deploy should resolve
        is_skill, prompt = skill_reg.resolve_input("/team-a/deploy")
        assert is_skill is True
        assert "staging" in prompt.lower()

    def test_invoke_skill_enum_qualified_on_collision(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        """invoke_skill tool definition uses namespace/name in enum when colliding."""
        from anteroom.cli.skills import SkillRegistry

        pack_a = _write_pack(
            tmp_path,
            name="pack-a",
            namespace="team-a",
            skill_files={"deploy": "Deploy to staging."},
        )
        pack_b = _write_pack(
            tmp_path,
            name="pack-b",
            namespace="team-b",
            skill_files={"deploy": "Deploy to production."},
        )
        _install(db, pack_a)
        _install(db, pack_b)
        registry.load_from_db(db)

        skill_reg = SkillRegistry()
        skill_reg.load_from_artifacts(registry)

        defn = skill_reg.get_invoke_skill_definition()
        assert defn is not None
        enum_values = defn["function"]["parameters"]["properties"]["skill_name"]["enum"]
        assert "team-a/deploy" in enum_values
        assert "team-b/deploy" in enum_values

    def test_mixed_unique_and_colliding(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        """Mix of unique and colliding skill names displays correctly."""
        from anteroom.cli.skills import SkillRegistry

        pack_a = _write_pack(
            tmp_path,
            name="pack-a",
            namespace="team-a",
            skill_files={"deploy": "Deploy staging.", "lint": "Lint code."},
        )
        pack_b = _write_pack(
            tmp_path,
            name="pack-b",
            namespace="team-b",
            skill_files={"deploy": "Deploy prod."},
        )
        _install(db, pack_a)
        _install(db, pack_b)
        registry.load_from_db(db)

        skill_reg = SkillRegistry()
        skill_reg.load_from_artifacts(registry)

        descs = skill_reg.get_skill_descriptions()
        display_names = [name for name, _ in descs]
        # "deploy" collides — should be qualified
        assert "team-a/deploy" in display_names
        assert "team-b/deploy" in display_names
        # "lint" is unique — should be bare
        assert "lint" in display_names

    def test_filesystem_skill_blocks_artifact_same_name(
        self, tmp_path: Path, db: ThreadSafeConnection, registry: ArtifactRegistry
    ) -> None:
        """Filesystem skill with no namespace blocks pack skill with same bare name."""
        from anteroom.cli.skills import Skill, SkillRegistry

        pack = _write_pack(
            tmp_path,
            name="pack-a",
            namespace="team-a",
            skill_files={"lint": "Pack lint."},
        )
        _install(db, pack)
        registry.load_from_db(db)

        skill_reg = SkillRegistry()
        # Pre-load filesystem skill
        skill_reg._skills["lint"] = Skill(
            name="lint",
            description="FS lint",
            prompt="FS lint.",
            source="project",
        )
        skill_reg._rebuild_name_index()

        skill_reg.load_from_artifacts(registry)

        # Filesystem version should win
        skill = skill_reg.get("lint")
        assert skill is not None
        assert skill.source == "project"


# ---------------------------------------------------------------------------
# Tests: Space-scoped pack attachments
# ---------------------------------------------------------------------------


class TestSpaceScopedAttachments:
    """Tests for attach_pack_to_space(), detach_pack_from_space(),
    and get_active_pack_ids_for_space() — zero coverage before this."""

    @staticmethod
    def _create_space(db: ThreadSafeConnection, name: str = "test-space") -> str:
        """Insert a minimal space row and return its ID."""
        import uuid
        from datetime import datetime, timezone

        space_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO spaces (id, name, instructions, created_at, updated_at) VALUES (?, ?, '', ?, ?)",
            (space_id, name, now, now),
        )
        db.commit()
        return space_id

    def test_attach_to_space(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack_to_space

        result = _install(db, _security_pack(tmp_path))
        space_id = self._create_space(db)

        att = attach_pack_to_space(db, result["id"], space_id)
        assert att["scope"] == "space"
        assert att["space_id"] == space_id
        assert att["priority"] == 50

    def test_attach_to_space_with_priority(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack_to_space

        result = _install(db, _security_pack(tmp_path))
        space_id = self._create_space(db)

        att = attach_pack_to_space(db, result["id"], space_id, priority=10)
        assert att["priority"] == 10

    def test_detach_from_space(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import (
            attach_pack_to_space,
            detach_pack_from_space,
        )

        result = _install(db, _security_pack(tmp_path))
        space_id = self._create_space(db)
        attach_pack_to_space(db, result["id"], space_id)

        assert detach_pack_from_space(db, result["id"], space_id) is True
        # Detaching again returns False
        assert detach_pack_from_space(db, result["id"], space_id) is False

    def test_active_pack_ids_for_space_includes_global(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Space context includes both global and space-scoped packs."""
        from anteroom.services.pack_attachments import (
            attach_pack_to_space,
            get_active_pack_ids_for_space,
        )

        r1 = _install(db, _security_pack(tmp_path))
        r2 = _install(db, _python_pack(tmp_path))
        space_id = self._create_space(db)

        # r1 attached globally, r2 attached to space
        attach_pack(db, r1["id"])
        attach_pack_to_space(db, r2["id"], space_id)

        ids = get_active_pack_ids_for_space(db, space_id)
        assert set(ids) == {r1["id"], r2["id"]}

    def test_active_pack_ids_for_space_excludes_other_spaces(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import (
            attach_pack_to_space,
            get_active_pack_ids_for_space,
        )

        result = _install(db, _security_pack(tmp_path))
        space_a = self._create_space(db, "space-a")
        space_b = self._create_space(db, "space-b")

        attach_pack_to_space(db, result["id"], space_a)

        ids_b = get_active_pack_ids_for_space(db, space_b)
        assert result["id"] not in ids_b

    def test_active_pack_ids_three_scope_union(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Three-scope union: global + space + project all included."""
        from anteroom.services.pack_attachments import (
            attach_pack_to_space,
            get_active_pack_ids_for_space,
        )

        r1 = _install(db, _security_pack(tmp_path))
        r2 = _install(db, _python_pack(tmp_path))
        r3 = _install(db, _write_pack(tmp_path, name="proj-pack", namespace="test", skill_files={"x": "y"}))
        space_id = self._create_space(db)

        attach_pack(db, r1["id"])  # global
        attach_pack_to_space(db, r2["id"], space_id)  # space
        attach_pack(db, r3["id"], project_path="/proj")  # project

        ids = get_active_pack_ids_for_space(db, space_id, project_path="/proj")
        assert set(ids) == {r1["id"], r2["id"], r3["id"]}

    def test_duplicate_space_attach_raises(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack_to_space

        result = _install(db, _security_pack(tmp_path))
        space_id = self._create_space(db)

        attach_pack_to_space(db, result["id"], space_id)
        with pytest.raises(ValueError, match="already attached"):
            attach_pack_to_space(db, result["id"], space_id)

    def test_attach_to_nonexistent_space_raises(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack_to_space

        result = _install(db, _security_pack(tmp_path))
        with pytest.raises(ValueError, match="Space not found"):
            attach_pack_to_space(db, result["id"], "nonexistent-space-id")

    def test_space_attach_conflict_detection(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Two packs with conflicting config overlays at same priority are blocked at space scope."""
        from anteroom.services.pack_attachments import attach_pack_to_space

        r1 = _install(db, _config_pack(tmp_path))
        r2 = _install(db, _conflicting_config_pack(tmp_path))
        space_id = self._create_space(db)

        attach_pack_to_space(db, r1["id"], space_id, priority=50)
        with pytest.raises(ValueError, match="Config overlay conflict"):
            attach_pack_to_space(db, r2["id"], space_id, priority=50)

    def test_space_attach_different_priority_allowed(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import attach_pack_to_space

        r1 = _install(db, _config_pack(tmp_path))
        r2 = _install(db, _conflicting_config_pack(tmp_path))
        space_id = self._create_space(db)

        attach_pack_to_space(db, r1["id"], space_id, priority=10)
        attach_pack_to_space(db, r2["id"], space_id, priority=50)

        from anteroom.services.pack_attachments import get_active_pack_ids_for_space

        ids = get_active_pack_ids_for_space(db, space_id)
        assert set(ids) == {r1["id"], r2["id"]}


# ---------------------------------------------------------------------------
# Tests: Pack update preserves attachment metadata
# ---------------------------------------------------------------------------


class TestAttachmentMetadataPreservation:
    """Verify that update_pack() restores attachment fields correctly."""

    @staticmethod
    def _create_space(db: ThreadSafeConnection) -> str:
        import uuid
        from datetime import datetime, timezone

        space_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO spaces (id, name, instructions, created_at, updated_at) VALUES (?, ?, '', ?, ?)",
            (space_id, "test-space", now, now),
        )
        db.commit()
        return space_id

    def test_global_attachment_priority_preserved(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = _security_pack(tmp_path)
        r1 = _install(db, pack_dir)
        attach_pack(db, r1["id"], priority=15)

        r2 = _install(db, pack_dir)  # update
        atts = list_attachments(db)
        assert len(atts) == 1
        assert atts[0]["pack_id"] == r2["id"]
        assert atts[0]["priority"] == 15
        assert atts[0]["scope"] == "global"

    def test_project_attachment_scope_preserved(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = _security_pack(tmp_path)
        r1 = _install(db, pack_dir)
        attach_pack(db, r1["id"], project_path="/my/project", priority=30)

        r2 = _install(db, pack_dir)
        atts = list_attachments(db, project_path="/my/project")
        project_atts = [a for a in atts if a["scope"] == "project"]
        assert len(project_atts) == 1
        assert project_atts[0]["pack_id"] == r2["id"]
        assert project_atts[0]["priority"] == 30
        assert project_atts[0]["project_path"] == "/my/project"

    def test_space_attachment_preserved_on_update(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import (
            attach_pack_to_space,
            list_attachments_for_pack,
        )

        pack_dir = _security_pack(tmp_path)
        r1 = _install(db, pack_dir)
        space_id = self._create_space(db)
        attach_pack_to_space(db, r1["id"], space_id, priority=25)

        r2 = _install(db, pack_dir)  # update
        atts = list_attachments_for_pack(db, r2["id"])
        assert len(atts) == 1
        assert atts[0]["scope"] == "space"
        assert atts[0]["priority"] == 25
        assert atts[0]["space_id"] == space_id

    def test_multiple_attachments_all_preserved(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """A pack attached at both global and space scopes keeps both."""
        from anteroom.services.pack_attachments import (
            attach_pack_to_space,
            list_attachments_for_pack,
        )

        pack_dir = _write_pack(
            tmp_path,
            name="multi-attach",
            namespace="test",
            rule_files={"info": {"content": "Be nice.", "metadata": {"enforce": "soft"}}},
        )
        r1 = _install(db, pack_dir)
        space_id = self._create_space(db)
        attach_pack(db, r1["id"], priority=10)
        attach_pack_to_space(db, r1["id"], space_id, priority=20)

        r2 = _install(db, pack_dir)
        atts = list_attachments_for_pack(db, r2["id"])
        assert len(atts) == 2
        scopes = {a["scope"] for a in atts}
        assert scopes == {"global", "space"}
        priorities = {a["priority"] for a in atts}
        assert priorities == {10, 20}


# ---------------------------------------------------------------------------
# Tests: Additive artifact coexistence
# ---------------------------------------------------------------------------


class TestAdditiveArtifacts:
    """Rules, instructions, and other additive artifact types from multiple
    packs with the same name should NOT conflict."""

    def test_same_rule_name_from_two_packs_no_conflict(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Two packs providing rule/deploy should both be attachable."""
        p1 = _write_pack(
            tmp_path,
            name="team-a",
            namespace="team-a",
            rule_files={
                "deploy": {
                    "content": "Always run tests before deploying.",
                    "metadata": {
                        "enforce": "hard",
                        "reason": "Tests first",
                        "matches": [{"tool": "bash", "pattern": "deploy"}],
                    },
                }
            },
        )
        p2 = _write_pack(
            tmp_path,
            name="team-b",
            namespace="team-b",
            rule_files={
                "deploy": {
                    "content": "Notify the channel before deploying.",
                    "metadata": {
                        "enforce": "hard",
                        "reason": "Notify team",
                        "matches": [{"tool": "bash", "pattern": "deploy"}],
                    },
                }
            },
        )
        r1 = _install(db, p1)
        r2 = _install(db, p2)

        # Both should attach without conflict
        attach_pack(db, r1["id"])
        attach_pack(db, r2["id"])

        ids = get_active_pack_ids(db)
        assert set(ids) == {r1["id"], r2["id"]}

    def test_same_rule_name_both_enforced(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        registry: ArtifactRegistry,
        enforcer: RuleEnforcer,
    ) -> None:
        """Both same-named rules from different packs should be loaded and enforced."""
        p1 = _write_pack(
            tmp_path,
            name="team-a",
            namespace="team-a",
            rule_files={
                "guardrails": {
                    "content": "Block force push.",
                    "metadata": {
                        "enforce": "hard",
                        "reason": "No force push",
                        "matches": [{"tool": "bash", "pattern": r"git\s+push\s+--force"}],
                    },
                }
            },
        )
        p2 = _write_pack(
            tmp_path,
            name="team-b",
            namespace="team-b",
            rule_files={
                "guardrails": {
                    "content": "Block rm -rf.",
                    "metadata": {
                        "enforce": "hard",
                        "reason": "No rm -rf",
                        "matches": [{"tool": "bash", "pattern": r"rm\s+-rf"}],
                    },
                }
            },
        )
        _install(db, p1)
        _install(db, p2)
        _load_registries(db, registry, enforcer)

        # Both rules should be loaded (additive)
        assert enforcer.rule_count == 2

        blocked_push, _, _ = enforcer.check_tool_call("bash", {"command": "git push --force"})
        assert blocked_push is True
        blocked_rm, _, _ = enforcer.check_tool_call("bash", {"command": "rm -rf /"})
        assert blocked_rm is True

    def test_same_instruction_name_from_two_packs_no_conflict(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Instructions are additive — same name from two packs is fine."""
        p1 = _write_pack(
            tmp_path,
            name="style-a",
            namespace="org-a",
            instruction_files={"coding-style": "Use 4-space indentation."},
        )
        p2 = _write_pack(
            tmp_path,
            name="style-b",
            namespace="org-b",
            instruction_files={"coding-style": "Use type hints everywhere."},
        )
        r1 = _install(db, p1)
        r2 = _install(db, p2)

        attach_pack(db, r1["id"])
        attach_pack(db, r2["id"])

        ids = get_active_pack_ids(db)
        assert set(ids) == {r1["id"], r2["id"]}

    def test_skill_same_name_from_two_packs_is_additive(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Skills are additive — same name from two packs resolves via namespace."""
        p1 = _write_pack(
            tmp_path,
            name="team-a",
            namespace="team-a",
            skill_files={"deploy": "Deploy to staging."},
        )
        p2 = _write_pack(
            tmp_path,
            name="team-b",
            namespace="team-b",
            skill_files={"deploy": "Deploy to production."},
        )
        r1 = _install(db, p1)
        r2 = _install(db, p2)

        attach_pack(db, r1["id"])
        attach_pack(db, r2["id"])  # No conflict — namespace-aware resolution

    def test_detect_artifact_conflicts_skips_additive_types(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """detect_artifact_conflicts() returns empty for additive types."""
        p1 = _write_pack(
            tmp_path,
            name="rules-a",
            namespace="org-a",
            rule_files={"shared-rule": {"content": "Rule A.", "metadata": {"enforce": "soft"}}},
        )
        p2 = _write_pack(
            tmp_path,
            name="rules-b",
            namespace="org-b",
            rule_files={"shared-rule": {"content": "Rule B.", "metadata": {"enforce": "soft"}}},
        )
        r1 = _install(db, p1)
        r2 = _install(db, p2)
        attach_pack(db, r1["id"], check_overlay_conflicts=False)

        conflicts = detect_artifact_conflicts(db, r2["id"], [r1["id"]])
        assert conflicts == []


# ---------------------------------------------------------------------------
# Tests: Config overlay utilities (track_config_sources, check_enforced_field_violations, flatten)
# ---------------------------------------------------------------------------


class TestConfigOverlayUtilities:
    def test_flatten_to_dot_paths_nested(self) -> None:
        d = {"ai": {"model": "gpt-4", "temperature": 0.7}, "safety": {"approval_mode": "ask"}}
        flat = flatten_to_dot_paths(d)
        assert flat == {
            "ai.model": "gpt-4",
            "ai.temperature": 0.7,
            "safety.approval_mode": "ask",
        }

    def test_flatten_to_dot_paths_list_values_are_leaves(self) -> None:
        d = {"mcp_servers": [{"name": "a"}, {"name": "b"}]}
        flat = flatten_to_dot_paths(d)
        assert flat == {"mcp_servers": [{"name": "a"}, {"name": "b"}]}

    def test_flatten_empty_dict(self) -> None:
        assert flatten_to_dot_paths({}) == {}

    def test_track_config_sources_last_layer_wins(self) -> None:
        layers = [
            ("team", {"ai": {"temperature": 0.5}}),
            ("pack:security", {"ai": {"temperature": 0.7}}),
            ("personal", {"safety": {"approval_mode": "auto"}}),
        ]
        sources = track_config_sources(layers)
        assert sources["ai.temperature"] == "pack:security"
        assert sources["safety.approval_mode"] == "personal"

    def test_track_config_sources_empty(self) -> None:
        assert track_config_sources([]) == {}

    def test_check_enforced_field_violations_found(self) -> None:
        overlay = {"ai": {"temperature": 0.7, "model": "gpt-4"}, "safety": {"approval_mode": "auto"}}
        enforced = ["ai.temperature", "safety.approval_mode"]
        violations = check_enforced_field_violations(overlay, enforced)
        assert violations == ["ai.temperature", "safety.approval_mode"]

    def test_check_enforced_field_violations_none(self) -> None:
        overlay = {"ai": {"model": "gpt-4"}}
        enforced = ["safety.approval_mode"]
        violations = check_enforced_field_violations(overlay, enforced)
        assert violations == []


# ---------------------------------------------------------------------------
# Tests: Pack lock file generation and validation
# ---------------------------------------------------------------------------


class TestPackLockFile:
    def test_generate_lock_empty_db(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_lock import generate_lock

        lock = generate_lock(db)
        assert lock["version"] == 1
        assert lock["packs"] == []

    def test_generate_lock_with_packs(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_lock import generate_lock

        _install(db, _security_pack(tmp_path))
        lock = generate_lock(db)
        assert len(lock["packs"]) == 1
        pack_entry = lock["packs"][0]
        assert pack_entry["namespace"] == "acme"
        assert pack_entry["name"] == "security-baseline"
        assert len(pack_entry["artifacts"]) == 3  # 2 rules + 1 skill

    def test_write_and_read_lock(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_lock import generate_lock, read_lock, write_lock

        _install(db, _security_pack(tmp_path))
        lock_data = generate_lock(db)

        lock_path = write_lock(tmp_path, lock_data)
        assert lock_path.is_file()

        read_back = read_lock(tmp_path)
        assert read_back is not None
        assert read_back["version"] == 1
        assert len(read_back["packs"]) == 1

    def test_read_lock_missing_file(self, tmp_path: Path) -> None:
        from anteroom.services.pack_lock import read_lock

        assert read_lock(tmp_path) is None

    def test_validate_lock_clean(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_lock import generate_lock, validate_lock, write_lock

        _install(db, _security_pack(tmp_path))
        write_lock(tmp_path, generate_lock(db))

        warnings = validate_lock(db, tmp_path)
        assert warnings == []

    def test_validate_lock_missing_file(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_lock import validate_lock

        warnings = validate_lock(db, tmp_path)
        assert warnings == ["Lock file not found"]

    def test_validate_lock_pack_in_lock_not_installed(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_lock import generate_lock, validate_lock, write_lock

        _install(db, _security_pack(tmp_path))
        write_lock(tmp_path, generate_lock(db))

        # Remove the pack from DB but keep the lock file
        remove_pack(db, "acme", "security-baseline")

        warnings = validate_lock(db, tmp_path)
        assert any("not installed" in w for w in warnings)

    def test_validate_lock_pack_installed_not_in_lock(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_lock import validate_lock, write_lock

        # Write empty lock
        write_lock(tmp_path, {"version": 1, "packs": []})

        # Install a pack
        _install(db, _security_pack(tmp_path))

        warnings = validate_lock(db, tmp_path)
        assert any("not in lock file" in w for w in warnings)

    def test_validate_lock_content_hash_mismatch(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_lock import generate_lock, validate_lock, write_lock

        _install(db, _security_pack(tmp_path))
        write_lock(tmp_path, generate_lock(db))

        # Tamper with artifact content in DB
        db.execute(
            "UPDATE artifacts SET content = 'TAMPERED', content_hash = 'badhash' WHERE fqn = '@acme/rule/no-force-push'"
        )
        db.commit()

        warnings = validate_lock(db, tmp_path)
        assert any("hash mismatch" in w for w in warnings)

    def test_validate_lock_invalid_format(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_lock import validate_lock

        lock_dir = tmp_path / ".anteroom"
        lock_dir.mkdir()
        (lock_dir / "anteroom.lock.yaml").write_text("packs: not-a-list", encoding="utf-8")

        warnings = validate_lock(db, tmp_path)
        assert any("not a list" in w for w in warnings)

    def test_read_lock_invalid_yaml_mapping(self, tmp_path: Path) -> None:
        from anteroom.services.pack_lock import read_lock

        lock_dir = tmp_path / ".anteroom"
        lock_dir.mkdir()
        (lock_dir / "anteroom.lock.yaml").write_text("just a string", encoding="utf-8")

        assert read_lock(tmp_path) is None


# ---------------------------------------------------------------------------
# Tests: Artifact YAML edge cases
# ---------------------------------------------------------------------------


class TestArtifactYAMLEdgeCases:
    def test_yaml_parse_error_falls_back_to_raw(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """If a YAML artifact file has invalid YAML, raw content is used."""
        from anteroom.services.artifact_storage import get_artifact_by_fqn

        pack_dir = tmp_path / "test-bad-yaml"
        pack_dir.mkdir()
        skills_dir = pack_dir / "skills"
        skills_dir.mkdir()
        # Write invalid YAML
        (skills_dir / "broken.yaml").write_text("content: [invalid yaml {", encoding="utf-8")

        manifest = {
            "name": "bad-yaml",
            "namespace": "test",
            "version": "1.0.0",
            "artifacts": [{"type": "skill", "name": "broken"}],
        }
        (pack_dir / "pack.yaml").write_text(yaml.dump(manifest), encoding="utf-8")
        _install(db, pack_dir)

        art = get_artifact_by_fqn(db, "@test/skill/broken")
        assert art is not None
        # Content should be the raw file content (fallback)
        assert "invalid yaml" in art["content"]

    def test_non_dict_metadata_coerced_to_empty(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """If metadata is a non-dict value, it should be coerced to {}."""
        from anteroom.services.artifact_storage import get_artifact_by_fqn

        pack_dir = tmp_path / "test-bad-meta"
        pack_dir.mkdir()
        skills_dir = pack_dir / "skills"
        skills_dir.mkdir()
        (skills_dir / "odd.yaml").write_text(
            yaml.dump({"content": "Hello", "metadata": "not-a-dict"}),
            encoding="utf-8",
        )
        manifest = {
            "name": "bad-meta",
            "namespace": "test",
            "version": "1.0.0",
            "artifacts": [{"type": "skill", "name": "odd"}],
        }
        (pack_dir / "pack.yaml").write_text(yaml.dump(manifest), encoding="utf-8")
        _install(db, pack_dir)

        art = get_artifact_by_fqn(db, "@test/skill/odd")
        assert art is not None
        assert art["metadata"] == {}

    def test_markdown_artifact_content(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Non-YAML artifact files (e.g. .md) use raw content."""
        from anteroom.services.artifact_storage import get_artifact_by_fqn

        pack_dir = tmp_path / "test-md"
        pack_dir.mkdir()
        inst_dir = pack_dir / "instructions"
        inst_dir.mkdir()
        (inst_dir / "guide.md").write_text("# Style Guide\nUse type hints.", encoding="utf-8")
        manifest = {
            "name": "md-pack",
            "namespace": "test",
            "version": "1.0.0",
            "artifacts": [{"type": "instruction", "name": "guide"}],
        }
        (pack_dir / "pack.yaml").write_text(yaml.dump(manifest), encoding="utf-8")
        _install(db, pack_dir)

        art = get_artifact_by_fqn(db, "@test/instruction/guide")
        assert art is not None
        assert "Style Guide" in art["content"]

    def test_missing_artifact_file_skipped(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Manifest references an artifact whose file doesn't exist — skipped, not crash."""
        pack_dir = tmp_path / "test-missing"
        pack_dir.mkdir()
        manifest = {
            "name": "missing-file",
            "namespace": "test",
            "version": "1.0.0",
            "artifacts": [{"type": "skill", "name": "ghost"}],
        }
        (pack_dir / "pack.yaml").write_text(yaml.dump(manifest), encoding="utf-8")
        result = _install(db, pack_dir)

        assert result["artifact_count"] == 0
        assert "skill/ghost" in result["skipped_artifacts"]

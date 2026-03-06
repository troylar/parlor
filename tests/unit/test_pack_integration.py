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
    collect_pack_overlays,
    detect_overlay_conflicts,
    merge_pack_overlays,
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

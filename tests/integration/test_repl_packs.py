"""Integration tests: REPL pack/rule/skill initialization chain.

Simulates the REPL's bootstrap path (repl.py lines 1346-1362) to verify that
packs installed in the DB are correctly wired through to the tool registry's
rule enforcer and the skill registry.

This is the integration gap identified during PR #786 review — the REPL init
path was never tested end-to-end.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from anteroom.cli.skills import SkillRegistry
from anteroom.config import SafetyConfig
from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services.artifact_registry import ArtifactRegistry
from anteroom.services.artifact_storage import upsert_artifact
from anteroom.services.artifacts import ArtifactSource, ArtifactType
from anteroom.services.packs import install_pack, parse_manifest
from anteroom.services.rule_enforcer import RuleEnforcer
from anteroom.tools import ToolRegistry, register_default_tools


@pytest.fixture()
def db() -> ThreadSafeConnection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return ThreadSafeConnection(conn)


@pytest.fixture()
def packs_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "src" / "anteroom" / "packs"


class TestReplRuleEnforcerChain:
    """Verify the full REPL init chain: DB → ArtifactRegistry → RuleEnforcer → ToolRegistry."""

    def test_hard_rule_from_pack_blocks_tool_call(self, db: ThreadSafeConnection) -> None:
        """Install a pack with a hard rule, then simulate the REPL init and verify blocking."""
        # 1. Insert a hard-enforced rule artifact directly (simulating a pack with hard rules)
        upsert_artifact(
            db,
            fqn="@test-team/rule/no-force-push",
            artifact_type="rule",
            namespace="test-team",
            name="no-force-push",
            content="Never force push to shared branches.",
            source=ArtifactSource.TEAM,
            metadata={
                "enforce": "hard",
                "reason": "Force pushing destroys shared history",
                "matches": [{"tool": "bash", "pattern": r"git\s+push\s+--force"}],
            },
        )

        # 2. Simulate REPL init (repl.py lines 1346-1362)
        artifact_registry = ArtifactRegistry()
        artifact_registry.load_from_db(db)

        rule_enforcer = RuleEnforcer()
        rule_enforcer.load_rules(artifact_registry.list_all(artifact_type=ArtifactType.RULE))

        tool_registry = ToolRegistry()
        register_default_tools(tool_registry, working_dir="/tmp")
        tool_registry.set_safety_config(SafetyConfig(), working_dir="/tmp")
        tool_registry.set_rule_enforcer(rule_enforcer)

        # 3. Verify: force push is blocked
        verdict = tool_registry.check_safety("bash", {"command": "git push --force origin main"})
        assert verdict is not None
        assert verdict.hard_denied is True
        assert "no-force-push" in verdict.reason
        assert "Force pushing destroys shared history" in verdict.reason

        # 4. Verify: safe commands still allowed
        safe_verdict = tool_registry.check_safety("bash", {"command": "git status"})
        # In default approval mode (ask_for_writes), bash is EXECUTE tier
        # which means it needs approval, but it's NOT hard_denied
        if safe_verdict is not None:
            assert safe_verdict.hard_denied is False

    def test_no_hard_rules_no_blocking(self, db: ThreadSafeConnection) -> None:
        """Soft rules should not cause blocking via the rule enforcer."""
        upsert_artifact(
            db,
            fqn="@test-team/rule/soft-guideline",
            artifact_type="rule",
            namespace="test-team",
            name="soft-guideline",
            content="Prefer descriptive variable names.",
            source=ArtifactSource.TEAM,
            metadata={"enforce": "soft"},
        )

        artifact_registry = ArtifactRegistry()
        artifact_registry.load_from_db(db)

        rule_enforcer = RuleEnforcer()
        rule_enforcer.load_rules(artifact_registry.list_all(artifact_type=ArtifactType.RULE))
        assert rule_enforcer.rule_count == 0  # Soft rules not loaded

        tool_registry = ToolRegistry()
        register_default_tools(tool_registry, working_dir="/tmp")
        tool_registry.set_safety_config(SafetyConfig(approval_mode="auto"), working_dir="/tmp")
        tool_registry.set_rule_enforcer(rule_enforcer)

        verdict = tool_registry.check_safety("bash", {"command": "git push --force"})
        assert verdict is None  # Auto mode, no hard rules → no verdict

    def test_rule_reload_after_pack_attach(self, db: ThreadSafeConnection) -> None:
        """Simulate pack attach → rule reload path (repl.py line 2667-2669)."""
        # Start with no rules
        artifact_registry = ArtifactRegistry()
        artifact_registry.load_from_db(db)

        rule_enforcer = RuleEnforcer()
        rule_enforcer.load_rules(artifact_registry.list_all(artifact_type=ArtifactType.RULE))
        assert rule_enforcer.rule_count == 0

        tool_registry = ToolRegistry()
        register_default_tools(tool_registry, working_dir="/tmp")
        tool_registry.set_safety_config(SafetyConfig(), working_dir="/tmp")
        tool_registry.set_rule_enforcer(rule_enforcer)

        # Force push should not be blocked yet
        verdict = tool_registry.check_safety("bash", {"command": "git push --force"})
        if verdict is not None:
            assert verdict.hard_denied is False

        # Now add a hard rule (simulating pack attach)
        upsert_artifact(
            db,
            fqn="@ops/rule/no-force-push",
            artifact_type="rule",
            namespace="ops",
            name="no-force-push",
            content="Block force push",
            source=ArtifactSource.TEAM,
            metadata={
                "enforce": "hard",
                "reason": "Policy violation",
                "matches": [{"tool": "bash", "pattern": r"git\s+push\s+--force"}],
            },
        )

        # Simulate REPL's reload path (repl.py line 2667-2669)
        artifact_registry.load_from_db(db)
        enforcer = getattr(tool_registry, "_rule_enforcer", None)
        assert enforcer is not None
        enforcer.load_rules(artifact_registry.list_all(artifact_type=ArtifactType.RULE))

        # Now force push should be blocked
        verdict2 = tool_registry.check_safety("bash", {"command": "git push --force"})
        assert verdict2 is not None
        assert verdict2.hard_denied is True


class TestReplSkillRegistryChain:
    """Verify the REPL init chain: DB → ArtifactRegistry → SkillRegistry."""

    def test_skills_from_pack_appear_in_registry(self, db: ThreadSafeConnection, packs_root: Path) -> None:
        """Install an example pack and verify its skills load into the skill registry."""
        pack_dir = packs_root / "writing-assistant"
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        # Simulate REPL init
        artifact_registry = ArtifactRegistry()
        artifact_registry.load_from_db(db)

        skill_registry = SkillRegistry()
        skill_registry.load()
        added = skill_registry.load_from_artifacts(artifact_registry)

        assert added >= 3  # summarize, rewrite, proofread
        assert skill_registry.has_skill("summarize")
        assert skill_registry.has_skill("rewrite")
        assert skill_registry.has_skill("proofread")

    def test_namespace_collision_shows_qualified_names(self, db: ThreadSafeConnection) -> None:
        """Two packs with same skill name → display names are namespace-qualified."""
        # Insert two skills with the same name from different namespaces
        for ns in ("team-alpha", "team-beta"):
            upsert_artifact(
                db,
                fqn=f"@{ns}/skill/deploy",
                artifact_type="skill",
                namespace=ns,
                name="deploy",
                content=f"name: deploy\ndescription: Deploy from {ns}\nprompt: Run deploy for {ns}",
                source=ArtifactSource.TEAM,
            )

        artifact_registry = ArtifactRegistry()
        artifact_registry.load_from_db(db)

        skill_registry = SkillRegistry()
        skill_registry.load()
        skill_registry.load_from_artifacts(artifact_registry)

        # Bare name should be ambiguous
        assert skill_registry.get("deploy") is None

        # Qualified names should resolve
        assert skill_registry.get("team-alpha/deploy") is not None
        assert skill_registry.get("team-beta/deploy") is not None

        # Display names should be qualified
        descs = skill_registry.get_skill_descriptions()
        deploy_names = [n for n, _ in descs if "deploy" in n]
        assert "team-alpha/deploy" in deploy_names
        assert "team-beta/deploy" in deploy_names

        # invoke_skill definition should use qualified names in enum
        defn = skill_registry.get_invoke_skill_definition()
        assert defn is not None
        enum_vals = defn["function"]["parameters"]["properties"]["skill_name"]["enum"]
        assert "team-alpha/deploy" in enum_vals
        assert "team-beta/deploy" in enum_vals

    def test_skill_reload_after_pack_change(self, db: ThreadSafeConnection) -> None:
        """Simulate pack install → skill reload path."""
        artifact_registry = ArtifactRegistry()
        artifact_registry.load_from_db(db)

        skill_registry = SkillRegistry()
        skill_registry.load()
        skill_registry.load_from_artifacts(artifact_registry)

        assert not skill_registry.has_skill("custom-check")

        # Add a skill artifact (simulating pack install)
        upsert_artifact(
            db,
            fqn="@devops/skill/custom-check",
            artifact_type="skill",
            namespace="devops",
            name="custom-check",
            content="name: custom-check\ndescription: Run checks\nprompt: Run all checks",
            source=ArtifactSource.TEAM,
        )

        # Reload (simulating REPL's /pack install → reload path)
        artifact_registry.load_from_db(db)
        skill_registry.load()
        skill_registry.load_from_artifacts(artifact_registry)

        assert skill_registry.has_skill("custom-check")

    def test_stale_artifact_skills_cleared_on_reload(self, db: ThreadSafeConnection) -> None:
        """Detaching a pack should remove its skills from the registry on reload."""
        # Add a skill artifact
        upsert_artifact(
            db,
            fqn="@devops/skill/stale-skill",
            artifact_type="skill",
            namespace="devops",
            name="stale-skill",
            content="name: stale-skill\ndescription: Will be removed\nprompt: Do something",
            source=ArtifactSource.TEAM,
        )

        artifact_registry = ArtifactRegistry()
        artifact_registry.load_from_db(db)

        skill_registry = SkillRegistry()
        skill_registry.load()
        skill_registry.load_from_artifacts(artifact_registry)

        assert skill_registry.has_skill("stale-skill")

        # Remove the artifact from the DB (simulating pack detach/remove)
        db.execute("DELETE FROM artifacts WHERE fqn = ?", ("@devops/skill/stale-skill",))
        db.commit()

        # Reload — stale skill should be gone
        artifact_registry.load_from_db(db)
        skill_registry.load_from_artifacts(artifact_registry)

        assert not skill_registry.has_skill("stale-skill")


class TestReplFullPackInstallChain:
    """End-to-end: install example pack from disk → verify rules and skills load."""

    def test_code_review_pack_full_chain(self, db: ThreadSafeConnection, packs_root: Path) -> None:
        """Install code-review pack, verify 2 rules + 2 skills loaded."""
        pack_dir = packs_root / "code-review"
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        # Bootstrap registries (REPL init path)
        artifact_registry = ArtifactRegistry()
        artifact_registry.load_from_db(db)

        skill_registry = SkillRegistry()
        skill_registry.load()
        skill_added = skill_registry.load_from_artifacts(artifact_registry)

        rule_enforcer = RuleEnforcer()
        rule_enforcer.load_rules(artifact_registry.list_all(artifact_type=ArtifactType.RULE))

        tool_registry = ToolRegistry()
        register_default_tools(tool_registry, working_dir="/tmp")
        tool_registry.set_safety_config(SafetyConfig(), working_dir="/tmp")
        tool_registry.set_rule_enforcer(rule_enforcer)

        # Verify skills loaded
        assert skill_added >= 2
        assert skill_registry.has_skill("review") or skill_registry.has_skill("anteroom/review")
        assert skill_registry.has_skill("changelog") or skill_registry.has_skill("anteroom/changelog")

        # Verify rules loaded (code-review rules are soft, so rule_count = 0)
        # This is correct — code-review rules don't have enforce: hard
        assert rule_enforcer.rule_count == 0

    def test_strict_safety_pack_config_overlay(self, db: ThreadSafeConnection, packs_root: Path) -> None:
        """Install strict-safety pack, verify config overlay artifact exists."""
        pack_dir = packs_root / "strict-safety"
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        artifact_registry = ArtifactRegistry()
        artifact_registry.load_from_db(db)

        overlays = artifact_registry.list_all(artifact_type=ArtifactType.CONFIG_OVERLAY)
        assert len(overlays) >= 1
        overlay_names = [a.name for a in overlays]
        assert "strict-defaults" in overlay_names

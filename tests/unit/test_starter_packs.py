"""Tests for services/starter_packs.py."""

from __future__ import annotations

import json
import sqlite3

import pytest

from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services.artifacts import Artifact, ArtifactSource, ArtifactType
from anteroom.services.packs import _read_artifact_content
from anteroom.services.rule_enforcer import parse_rule
from anteroom.services.starter_packs import (
    get_built_in_pack_path,
    install_starter_packs,
    list_all_built_in_packs,
    list_example_packs,
    list_starter_packs,
)


@pytest.fixture()
def db() -> ThreadSafeConnection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return ThreadSafeConnection(conn)


class TestListStarterPacks:
    def test_returns_all_available(self) -> None:
        result = list_starter_packs()
        assert len(result) == 2
        names = {p["name"] for p in result}
        assert "python-dev" in names
        assert "security-baseline" in names

    def test_each_has_required_fields(self) -> None:
        for pack in list_starter_packs():
            assert pack["name"]
            assert pack["namespace"] == "anteroom"
            assert pack["version"]
            assert pack["description"]


class TestListExamplePacks:
    def test_returns_all_example_packs(self) -> None:
        result = list_example_packs()
        assert len(result) == 3
        names = {p["name"] for p in result}
        assert names == {"code-review", "writing-assistant", "strict-safety"}

    def test_each_has_required_fields(self) -> None:
        for pack in list_example_packs():
            assert pack["name"]
            assert pack["namespace"]
            assert pack["version"]
            assert pack["description"]


class TestListAllBuiltInPacks:
    def test_returns_starter_plus_example(self) -> None:
        result = list_all_built_in_packs()
        assert len(result) == 5  # 2 starter + 3 example
        names = {p["name"] for p in result}
        assert "python-dev" in names
        assert "code-review" in names


class TestGetBuiltInPackPath:
    def test_returns_path_for_valid_pack(self) -> None:
        path = get_built_in_pack_path("python-dev")
        assert path is not None
        assert (path / "pack.yaml").is_file()

    def test_returns_none_for_nonexistent(self) -> None:
        assert get_built_in_pack_path("nonexistent") is None

    def test_blocks_path_traversal(self) -> None:
        assert get_built_in_pack_path("../../etc") is None

    def test_blocks_absolute_traversal(self) -> None:
        assert get_built_in_pack_path("../../../tmp") is None


class TestInstallStarterPacks:
    def test_installs_all(self, db: ThreadSafeConnection) -> None:
        results = install_starter_packs(db)
        installed = [r for r in results if r["status"] == "installed"]
        assert len(installed) == 2

        # Verify in DB
        packs = db.execute("SELECT * FROM packs").fetchall()
        assert len(packs) == 2

        artifacts = db.execute("SELECT * FROM artifacts WHERE source = 'built_in'").fetchall()
        assert len(artifacts) > 0

    def test_idempotent_skips_same_version(self, db: ThreadSafeConnection) -> None:
        install_starter_packs(db)
        results = install_starter_packs(db)
        skipped = [r for r in results if r["status"] == "skipped"]
        assert len(skipped) == 2

    def test_updates_on_version_change(self, db: ThreadSafeConnection) -> None:
        install_starter_packs(db)

        # Manually change version in DB
        db.execute("UPDATE packs SET version = '0.0.1' WHERE name = 'python-dev'")
        db.commit()

        results = install_starter_packs(db)
        updated = [r for r in results if r["status"] == "updated"]
        assert len(updated) == 1
        assert updated[0]["name"] == "python-dev"

    def test_install_specific_names(self, db: ThreadSafeConnection) -> None:
        results = install_starter_packs(db, names=["python-dev"])
        assert len(results) == 1
        assert results[0]["name"] == "python-dev"
        assert results[0]["status"] == "installed"

        packs = db.execute("SELECT * FROM packs").fetchall()
        assert len(packs) == 1

    def test_install_nonexistent_name(self, db: ThreadSafeConnection) -> None:
        results = install_starter_packs(db, names=["nonexistent-pack"])
        assert len(results) == 1
        assert results[0]["status"] == "error"

    def test_artifacts_have_built_in_source(self, db: ThreadSafeConnection) -> None:
        install_starter_packs(db)
        artifacts = db.execute("SELECT * FROM artifacts").fetchall()
        for art in artifacts:
            assert art["source"] == "built_in"

    def test_pack_artifacts_linked(self, db: ThreadSafeConnection) -> None:
        install_starter_packs(db)
        links = db.execute("SELECT * FROM pack_artifacts").fetchall()
        assert len(links) > 0

        # Each link should reference a valid pack and artifact
        for link in links:
            pack = db.execute("SELECT id FROM packs WHERE id = ?", (link["pack_id"],)).fetchone()
            assert pack is not None
            art = db.execute("SELECT id FROM artifacts WHERE id = ?", (link["artifact_id"],)).fetchone()
            assert art is not None


class TestSecurityBaselineRuleEnforcement:
    """Verify security-baseline pack rules have proper metadata for hard enforcement."""

    _RULE_NAMES = ("no-eval", "parameterized-queries", "no-hardcoded-secrets")

    def test_rule_files_are_yaml(self) -> None:
        pack_path = get_built_in_pack_path("security-baseline")
        assert pack_path is not None
        for name in self._RULE_NAMES:
            yaml_file = pack_path / "rules" / f"{name}.yaml"
            assert yaml_file.is_file(), f"Expected YAML rule file: {yaml_file}"

    def test_rule_files_have_metadata(self) -> None:
        pack_path = get_built_in_pack_path("security-baseline")
        assert pack_path is not None
        for name in self._RULE_NAMES:
            yaml_file = pack_path / "rules" / f"{name}.yaml"
            content, metadata = _read_artifact_content(yaml_file)
            assert metadata, f"Rule {name} has empty metadata"
            assert metadata.get("enforce") == "hard", f"Rule {name} is not hard-enforced"
            assert metadata.get("matches"), f"Rule {name} has no match patterns"
            assert metadata.get("reason"), f"Rule {name} has no reason"

    def test_rule_files_parse_as_hard_rules(self) -> None:
        pack_path = get_built_in_pack_path("security-baseline")
        assert pack_path is not None
        for name in self._RULE_NAMES:
            yaml_file = pack_path / "rules" / f"{name}.yaml"
            content, metadata = _read_artifact_content(yaml_file)
            artifact = Artifact(
                fqn=f"@anteroom/rule/{name}",
                type=ArtifactType.RULE,
                namespace="anteroom",
                name=name,
                content=content,
                source=ArtifactSource.BUILT_IN,
                metadata=metadata,
            )
            parsed = parse_rule(artifact)
            assert parsed is not None, f"Rule {name} failed to parse as hard rule"
            assert len(parsed.matches) > 0, f"Rule {name} has no valid match patterns"

    def test_installed_rules_have_metadata(self, db: ThreadSafeConnection) -> None:
        install_starter_packs(db, names=["security-baseline"])
        for name in self._RULE_NAMES:
            row = db.execute(
                "SELECT metadata FROM artifacts WHERE name = ? AND namespace = 'anteroom'",
                (name,),
            ).fetchone()
            assert row is not None, f"Rule {name} not found in DB"
            meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
            assert meta.get("enforce") == "hard", f"Rule {name} metadata missing enforce:hard in DB"

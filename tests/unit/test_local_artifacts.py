"""Tests for services/local_artifacts.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services.local_artifacts import (
    _LOCAL_DIR,
    discover_local_artifacts,
    load_local_artifacts,
    scaffold_local_artifact,
)


@pytest.fixture()
def db() -> ThreadSafeConnection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return ThreadSafeConnection(conn)


class TestDiscoverLocalArtifacts:
    def test_empty_dir(self, tmp_path: Path) -> None:
        assert discover_local_artifacts(tmp_path) == []

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        assert discover_local_artifacts(tmp_path / "nope") == []

    def test_discovers_rules(self, tmp_path: Path) -> None:
        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "my-rule.md").write_text("# My Rule\nDo stuff.\n")
        result = discover_local_artifacts(tmp_path)
        assert len(result) == 1
        assert result[0]["type"] == "rule"
        assert result[0]["name"] == "my-rule"
        assert result[0]["namespace"] == "local"
        assert "@local/rule/my-rule" == result[0]["fqn"]

    def test_discovers_skills(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        skills.mkdir()
        (skills / "greet.yaml").write_text("name: greet\ncontent: say hi\n")
        result = discover_local_artifacts(tmp_path)
        assert len(result) == 1
        assert result[0]["type"] == "skill"
        assert result[0]["content"] == "say hi"

    def test_skips_wrong_extension(self, tmp_path: Path) -> None:
        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "readme.py").write_text("not a rule")
        assert discover_local_artifacts(tmp_path) == []

    def test_skips_directories(self, tmp_path: Path) -> None:
        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "subdir").mkdir()
        assert discover_local_artifacts(tmp_path) == []

    def test_multiple_types(self, tmp_path: Path) -> None:
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "r1.md").write_text("rule 1")
        (tmp_path / "skills").mkdir()
        (tmp_path / "skills" / "s1.yaml").write_text("content: skill 1\n")
        (tmp_path / "instructions").mkdir()
        (tmp_path / "instructions" / "i1.md").write_text("instruction 1")
        result = discover_local_artifacts(tmp_path)
        types = {a["type"] for a in result}
        assert types == {"rule", "skill", "instruction"}


class TestLoadLocalArtifacts:
    def test_loads_global(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        local = tmp_path / _LOCAL_DIR
        rules = local / "rules"
        rules.mkdir(parents=True)
        (rules / "no-eval.md").write_text("Don't use eval()")
        count = load_local_artifacts(db, tmp_path)
        assert count == 1
        row = db.execute("SELECT * FROM artifacts WHERE name = 'no-eval'").fetchone()
        assert row is not None
        assert row["source"] == "local"

    def test_loads_project(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        proj = tmp_path / "myproject"
        proj.mkdir()
        local = proj / ".anteroom" / _LOCAL_DIR / "rules"
        local.mkdir(parents=True)
        (local / "proj-rule.md").write_text("Project rule")
        count = load_local_artifacts(db, tmp_path, project_dir=proj)
        assert count == 1

    def test_loads_both_global_and_project(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        # Global
        g = tmp_path / _LOCAL_DIR / "rules"
        g.mkdir(parents=True)
        (g / "global.md").write_text("global")
        # Project
        proj = tmp_path / "proj"
        proj.mkdir()
        p = proj / ".anteroom" / _LOCAL_DIR / "rules"
        p.mkdir(parents=True)
        (p / "local.md").write_text("local")
        count = load_local_artifacts(db, tmp_path, project_dir=proj)
        assert count == 2

    def test_returns_zero_when_no_artifacts(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        assert load_local_artifacts(db, tmp_path) == 0


class TestScaffoldLocalArtifact:
    def test_creates_rule(self, tmp_path: Path) -> None:
        path = scaffold_local_artifact("rule", "my-rule", tmp_path)
        assert path.exists()
        assert path.name == "my-rule.md"
        content = path.read_text()
        assert "my-rule" in content

    def test_creates_skill(self, tmp_path: Path) -> None:
        path = scaffold_local_artifact("skill", "my-skill", tmp_path)
        assert path.exists()
        assert path.name == "my-skill.yaml"

    def test_creates_in_project_dir(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        proj.mkdir()
        path = scaffold_local_artifact("rule", "r1", tmp_path, project=True, project_dir=proj)
        assert ".anteroom" in str(path)
        assert path.exists()

    def test_rejects_path_traversal_name(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Invalid artifact name"):
            scaffold_local_artifact("rule", "../../evil", tmp_path)

    def test_rejects_slash_in_name(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Invalid artifact name"):
            scaffold_local_artifact("rule", "foo/bar", tmp_path)

    def test_invalid_type_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Invalid artifact type"):
            scaffold_local_artifact("bogus", "test", tmp_path)

    def test_duplicate_raises(self, tmp_path: Path) -> None:
        scaffold_local_artifact("rule", "dup", tmp_path)
        with pytest.raises(ValueError, match="already exists"):
            scaffold_local_artifact("rule", "dup", tmp_path)

    def test_project_without_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="project_dir required"):
            scaffold_local_artifact("rule", "r1", tmp_path, project=True)

"""Tests for anteroom.services.packs — pack management."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services.packs import (
    ManifestArtifact,
    _read_artifact_content,
    _resolve_artifact_file,
    get_pack,
    install_pack,
    list_packs,
    load_project_packs,
    parse_manifest,
    remove_pack,
    update_pack,
    validate_manifest,
)


@pytest.fixture()
def db() -> ThreadSafeConnection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return ThreadSafeConnection(conn)


def _write_manifest(path: Path, data: dict) -> Path:
    manifest_path = path / "pack.yaml"
    with open(manifest_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)
    return manifest_path


def _create_pack_dir(tmp_path: Path, name: str = "test-pack", namespace: str = "test-ns") -> Path:
    """Create a minimal valid pack directory with one skill artifact."""
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "skills").mkdir()
    (pack_dir / "skills" / "greet.yaml").write_text("content: Hello!\nmetadata:\n  tier: read\n", encoding="utf-8")
    _write_manifest(
        pack_dir,
        {
            "name": name,
            "namespace": namespace,
            "version": "1.0.0",
            "description": "A test pack",
            "artifacts": [
                {"type": "skill", "name": "greet"},
            ],
        },
    )
    return pack_dir


class TestParseManifest:
    def test_valid_manifest(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            {
                "name": "my-pack",
                "namespace": "my-ns",
                "version": "2.1.0",
                "description": "A great pack",
                "artifacts": [
                    {"type": "skill", "name": "greet"},
                    {"type": "rule", "name": "no-eval"},
                ],
            },
        )
        m = parse_manifest(tmp_path / "pack.yaml")
        assert m.name == "my-pack"
        assert m.namespace == "my-ns"
        assert m.version == "2.1.0"
        assert m.description == "A great pack"
        assert len(m.artifacts) == 2
        assert m.artifacts[0].type == "skill"
        assert m.artifacts[0].name == "greet"

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Manifest not found"):
            parse_manifest(tmp_path / "pack.yaml")

    def test_missing_name(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"namespace": "ns", "artifacts": []})
        with pytest.raises(ValueError, match="missing required field: name"):
            parse_manifest(tmp_path / "pack.yaml")

    def test_missing_namespace(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"name": "p", "artifacts": []})
        with pytest.raises(ValueError, match="missing required field: namespace"):
            parse_manifest(tmp_path / "pack.yaml")

    def test_invalid_name_format(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"name": "../evil", "namespace": "ns", "artifacts": []})
        with pytest.raises(ValueError, match="Invalid pack name"):
            parse_manifest(tmp_path / "pack.yaml")

    def test_invalid_namespace_format(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"name": "p", "namespace": "../../etc", "artifacts": []})
        with pytest.raises(ValueError, match="Invalid namespace"):
            parse_manifest(tmp_path / "pack.yaml")

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "pack.yaml").write_text("not a mapping", encoding="utf-8")
        with pytest.raises(ValueError, match="must be a YAML mapping"):
            parse_manifest(tmp_path / "pack.yaml")

    def test_artifacts_not_list(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"name": "p", "namespace": "ns", "artifacts": "bad"})
        with pytest.raises(ValueError, match="must be a list"):
            parse_manifest(tmp_path / "pack.yaml")

    def test_invalid_artifact_type(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            {
                "name": "p",
                "namespace": "ns",
                "artifacts": [{"type": "invalid", "name": "x"}],
            },
        )
        with pytest.raises(ValueError, match="invalid type"):
            parse_manifest(tmp_path / "pack.yaml")

    def test_artifact_missing_name(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            {
                "name": "p",
                "namespace": "ns",
                "artifacts": [{"type": "skill"}],
            },
        )
        with pytest.raises(ValueError, match="missing required field 'name'"):
            parse_manifest(tmp_path / "pack.yaml")

    def test_artifact_entry_not_mapping(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            {
                "name": "p",
                "namespace": "ns",
                "artifacts": ["just a string"],
            },
        )
        with pytest.raises(ValueError, match="must be a mapping"):
            parse_manifest(tmp_path / "pack.yaml")

    def test_default_version(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"name": "p", "namespace": "ns", "artifacts": []})
        m = parse_manifest(tmp_path / "pack.yaml")
        assert m.version == "0.0.0"

    def test_custom_file_field(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            {
                "name": "p",
                "namespace": "ns",
                "artifacts": [{"type": "rule", "name": "x", "file": "rules/x.md"}],
            },
        )
        m = parse_manifest(tmp_path / "pack.yaml")
        assert m.artifacts[0].file == "rules/x.md"


class TestValidateManifest:
    def test_valid_pack(self, tmp_path: Path) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        errors = validate_manifest(manifest, pack_dir)
        assert errors == []

    def test_missing_artifact_file(self, tmp_path: Path) -> None:
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        _write_manifest(
            pack_dir,
            {
                "name": "p",
                "namespace": "ns",
                "artifacts": [{"type": "skill", "name": "missing"}],
            },
        )
        manifest = parse_manifest(pack_dir / "pack.yaml")
        errors = validate_manifest(manifest, pack_dir)
        assert len(errors) == 1
        assert "Missing artifact file" in errors[0]

    def test_custom_file_path(self, tmp_path: Path) -> None:
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        (pack_dir / "my-rule.md").write_text("# Rule", encoding="utf-8")
        _write_manifest(
            pack_dir,
            {
                "name": "p",
                "namespace": "ns",
                "artifacts": [{"type": "rule", "name": "r", "file": "my-rule.md"}],
            },
        )
        manifest = parse_manifest(pack_dir / "pack.yaml")
        errors = validate_manifest(manifest, pack_dir)
        assert errors == []

    def test_path_traversal_blocked(self, tmp_path: Path) -> None:
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        _write_manifest(
            pack_dir,
            {
                "name": "p",
                "namespace": "ns",
                "artifacts": [{"type": "skill", "name": "evil", "file": "../../../etc/passwd"}],
            },
        )
        manifest = parse_manifest(pack_dir / "pack.yaml")
        errors = validate_manifest(manifest, pack_dir)
        assert len(errors) == 1
        assert "Path traversal" in errors[0]


class TestResolveArtifactFile:
    def test_yaml_extension(self, tmp_path: Path) -> None:
        (tmp_path / "skills").mkdir()
        (tmp_path / "skills" / "greet.yaml").write_text("content: hi")
        art = ManifestArtifact(type="skill", name="greet")
        result = _resolve_artifact_file(art, tmp_path)
        assert result is not None
        assert result.name == "greet.yaml"

    def test_md_extension(self, tmp_path: Path) -> None:
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "no-eval.md").write_text("# No eval")
        art = ManifestArtifact(type="rule", name="no-eval")
        result = _resolve_artifact_file(art, tmp_path)
        assert result is not None
        assert result.name == "no-eval.md"

    def test_custom_file(self, tmp_path: Path) -> None:
        (tmp_path / "custom.txt").write_text("custom")
        art = ManifestArtifact(type="rule", name="r", file="custom.txt")
        result = _resolve_artifact_file(art, tmp_path)
        assert result is not None

    def test_not_found(self, tmp_path: Path) -> None:
        art = ManifestArtifact(type="skill", name="missing")
        result = _resolve_artifact_file(art, tmp_path)
        assert result is None

    def test_path_traversal_blocked(self, tmp_path: Path) -> None:
        art = ManifestArtifact(type="skill", name="evil", file="../../../etc/passwd")
        result = _resolve_artifact_file(art, tmp_path)
        assert result is None


class TestReadArtifactContent:
    def test_yaml_file(self, tmp_path: Path) -> None:
        p = tmp_path / "skill.yaml"
        p.write_text("content: Hello!\nmetadata:\n  tier: read\n")
        content, metadata = _read_artifact_content(p)
        assert content == "Hello!"
        assert metadata == {"tier": "read"}

    def test_markdown_file(self, tmp_path: Path) -> None:
        p = tmp_path / "rule.md"
        p.write_text("# No eval\nDo not use eval().")
        content, metadata = _read_artifact_content(p)
        assert "No eval" in content
        assert metadata == {}

    def test_yaml_without_content_key(self, tmp_path: Path) -> None:
        p = tmp_path / "plain.yaml"
        p.write_text("just: a mapping\n")
        content, metadata = _read_artifact_content(p)
        # Falls back to raw content since there's no "content" key
        assert "just: a mapping" in content


class TestInstallPack:
    def test_install_basic(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        result = install_pack(db, manifest, pack_dir)

        assert result["name"] == "test-pack"
        assert result["namespace"] == "test-ns"
        assert result["version"] == "1.0.0"
        assert result["artifact_count"] == 1

    def test_install_creates_db_rows(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        # Pack row
        row = db.execute("SELECT * FROM packs WHERE name = 'test-pack'").fetchone()
        assert row is not None
        assert dict(row)["namespace"] == "test-ns"

        # Artifact row
        art = db.execute("SELECT * FROM artifacts WHERE fqn = '@test-ns/skill/greet'").fetchone()
        assert art is not None

        # Junction row
        pa = db.execute("SELECT * FROM pack_artifacts").fetchone()
        assert pa is not None

    def test_install_duplicate_raises(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        with pytest.raises(ValueError, match="already installed"):
            install_pack(db, manifest, pack_dir)

    def test_install_with_project_dir(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir, project_dir=project_dir)

        dest = project_dir / ".anteroom" / "packs" / "test-ns" / "test-pack"
        assert dest.is_dir()
        assert (dest / "pack.yaml").is_file()

    def test_install_skips_missing_artifact_file(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        _write_manifest(
            pack_dir,
            {
                "name": "p",
                "namespace": "ns",
                "version": "1.0.0",
                "artifacts": [{"type": "skill", "name": "missing"}],
            },
        )
        manifest = parse_manifest(pack_dir / "pack.yaml")
        result = install_pack(db, manifest, pack_dir)
        assert result["artifact_count"] == 0

    def test_install_multiple_artifacts(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        (pack_dir / "skills").mkdir()
        (pack_dir / "rules").mkdir()
        (pack_dir / "skills" / "greet.yaml").write_text("content: hi\n")
        (pack_dir / "rules" / "no-eval.md").write_text("# No eval\n")
        _write_manifest(
            pack_dir,
            {
                "name": "multi",
                "namespace": "ns",
                "version": "1.0.0",
                "artifacts": [
                    {"type": "skill", "name": "greet"},
                    {"type": "rule", "name": "no-eval"},
                ],
            },
        )
        manifest = parse_manifest(pack_dir / "pack.yaml")
        result = install_pack(db, manifest, pack_dir)
        assert result["artifact_count"] == 2


class TestRemovePack:
    def test_remove_existing(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        removed = remove_pack(db, "test-ns", "test-pack")
        assert removed is True

        # Pack gone
        assert db.execute("SELECT * FROM packs WHERE name = 'test-pack'").fetchone() is None
        # Artifact gone (orphaned)
        assert db.execute("SELECT * FROM artifacts WHERE fqn = '@test-ns/skill/greet'").fetchone() is None

    def test_remove_nonexistent(self, db: ThreadSafeConnection) -> None:
        assert remove_pack(db, "no", "such") is False

    def test_shared_artifact_survives(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """An artifact shared between two packs should survive removal of one pack."""
        # Install pack A with artifact 'greet'
        pack_a = _create_pack_dir(tmp_path / "a", name="pack-a", namespace="ns")
        manifest_a = parse_manifest(pack_a / "pack.yaml")
        install_pack(db, manifest_a, pack_a)

        # Install pack B referencing the same artifact FQN (different pack, same content)
        pack_b = tmp_path / "b" / "pack"
        pack_b.mkdir(parents=True)
        (pack_b / "skills").mkdir()
        (pack_b / "skills" / "greet.yaml").write_text("content: Hello!\n")
        _write_manifest(
            pack_b,
            {
                "name": "pack-b",
                "namespace": "ns",
                "version": "1.0.0",
                "artifacts": [{"type": "skill", "name": "greet"}],
            },
        )
        manifest_b = parse_manifest(pack_b / "pack.yaml")
        install_pack(db, manifest_b, pack_b)

        # Remove pack A — artifact should survive because pack B still references it
        remove_pack(db, "ns", "pack-a")
        art = db.execute("SELECT * FROM artifacts WHERE fqn = '@ns/skill/greet'").fetchone()
        assert art is not None

        # Remove pack B — artifact should now be deleted
        remove_pack(db, "ns", "pack-b")
        art = db.execute("SELECT * FROM artifacts WHERE fqn = '@ns/skill/greet'").fetchone()
        assert art is None


class TestUpdatePack:
    def test_update_replaces(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        # Modify version in manifest
        _write_manifest(
            pack_dir,
            {
                "name": "test-pack",
                "namespace": "test-ns",
                "version": "2.0.0",
                "description": "Updated",
                "artifacts": [{"type": "skill", "name": "greet"}],
            },
        )
        new_manifest = parse_manifest(pack_dir / "pack.yaml")
        result = update_pack(db, new_manifest, pack_dir)
        assert result["version"] == "2.0.0"

        # Only one pack row
        rows = db.execute("SELECT * FROM packs").fetchall()
        assert len(rows) == 1

    def test_update_not_installed(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        with pytest.raises(ValueError, match="not installed"):
            update_pack(db, manifest, pack_dir)


class TestListPacks:
    def test_empty(self, db: ThreadSafeConnection) -> None:
        assert list_packs(db) == []

    def test_with_packs(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        packs = list_packs(db)
        assert len(packs) == 1
        assert packs[0]["name"] == "test-pack"
        assert packs[0]["artifact_count"] == 1


class TestGetPack:
    def test_found(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        p = get_pack(db, "test-ns", "test-pack")
        assert p is not None
        assert p["name"] == "test-pack"
        assert len(p["artifacts"]) == 1
        assert p["artifacts"][0]["fqn"] == "@test-ns/skill/greet"

    def test_not_found(self, db: ThreadSafeConnection) -> None:
        assert get_pack(db, "no", "such") is None


class TestLoadProjectPacks:
    def test_loads_from_project_dir(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        project_dir = tmp_path / "project"
        packs_root = project_dir / ".anteroom" / "packs" / "test-ns" / "my-pack"
        packs_root.mkdir(parents=True)
        (packs_root / "skills").mkdir()
        (packs_root / "skills" / "hello.yaml").write_text("content: Hello!\n")
        _write_manifest(
            packs_root,
            {
                "name": "my-pack",
                "namespace": "test-ns",
                "version": "1.0.0",
                "artifacts": [{"type": "skill", "name": "hello"}],
            },
        )

        results = load_project_packs(db, project_dir)
        assert len(results) == 1
        assert results[0]["name"] == "my-pack"

    def test_skips_already_installed(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        # Pre-install the pack
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        # Set up project dir with same pack
        project_dir = tmp_path / "project"
        packs_root = project_dir / ".anteroom" / "packs" / "test-ns" / "test-pack"
        packs_root.mkdir(parents=True)
        (packs_root / "skills").mkdir()
        (packs_root / "skills" / "greet.yaml").write_text("content: Hello!\n")
        _write_manifest(
            packs_root,
            {
                "name": "test-pack",
                "namespace": "test-ns",
                "version": "1.0.0",
                "artifacts": [{"type": "skill", "name": "greet"}],
            },
        )

        results = load_project_packs(db, project_dir)
        assert len(results) == 0

    def test_no_packs_dir(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        assert load_project_packs(db, tmp_path) == []

    def test_invalid_manifest_skipped(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        project_dir = tmp_path / "project"
        packs_root = project_dir / ".anteroom" / "packs" / "ns" / "bad"
        packs_root.mkdir(parents=True)
        (packs_root / "pack.yaml").write_text("not a mapping")

        results = load_project_packs(db, project_dir)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Bug fix: _read_artifact_content YAML crash (#522)
# ---------------------------------------------------------------------------


class TestReadArtifactContentYamlError:
    def test_invalid_yaml_returns_raw(self, tmp_path: Path) -> None:
        """Invalid YAML should fall back to raw content instead of crashing."""
        p = tmp_path / "bad.yaml"
        p.write_text("content: [\ninvalid yaml {{{\n")
        content, metadata = _read_artifact_content(p)
        assert "invalid yaml" in content
        assert metadata == {}

    def test_yaml_with_tabs_returns_raw(self, tmp_path: Path) -> None:
        """YAML with tab indentation (common error) should degrade gracefully."""
        p = tmp_path / "tabbed.yaml"
        p.write_text("key:\n\t- invalid tab indent\n")
        content, metadata = _read_artifact_content(p)
        assert "invalid tab indent" in content
        assert metadata == {}


# ---------------------------------------------------------------------------
# Bug fix: install_pack skipped_artifacts reporting (#522)
# ---------------------------------------------------------------------------


class TestInstallPackSkippedArtifacts:
    def test_skipped_artifacts_in_result(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """install_pack should report which artifacts were skipped."""
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        (pack_dir / "skills").mkdir()
        (pack_dir / "skills" / "found.yaml").write_text("content: hi\n")
        # 'missing' artifact has no file
        _write_manifest(
            pack_dir,
            {
                "name": "p",
                "namespace": "ns",
                "version": "1.0.0",
                "artifacts": [
                    {"type": "skill", "name": "found"},
                    {"type": "skill", "name": "missing"},
                ],
            },
        )
        manifest = parse_manifest(pack_dir / "pack.yaml")
        result = install_pack(db, manifest, pack_dir)
        assert result["artifact_count"] == 1
        assert "skill/missing" in result["skipped_artifacts"]
        assert len(result["skipped_artifacts"]) == 1

    def test_no_skipped_artifacts(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """When all artifacts resolve, skipped_artifacts should be empty."""
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        result = install_pack(db, manifest, pack_dir)
        assert result["skipped_artifacts"] == []

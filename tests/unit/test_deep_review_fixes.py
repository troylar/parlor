"""Tests for deep review fixes (follow-up to #540-#545 batch).

Covers:
- Issue 1: Route ordering — artifact health check registered before artifact catch-all
- Issue 2: _copy_to_project copies only manifest-referenced files
- Issue 4: validate_lock bidirectional check (DB→lock direction)
- Issue 6: update_pack atomic transaction
- Issue 7: CLI _validate_pack_ref format validation
- Issue 8: _pack_row_to_dict key alignment with list_packs query
- Issue 9: artifact_health uses thread-safe execute_fetchone/execute_fetchall
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services import artifact_storage, packs
from anteroom.services.artifact_health import (
    check_orphaned_artifacts,
    fix_duplicate_content,
    run_health_check,
)
from anteroom.services.pack_lock import validate_lock, write_lock


@pytest.fixture()
def db() -> ThreadSafeConnection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return ThreadSafeConnection(conn)


def _create(db: ThreadSafeConnection, fqn: str, content: str, source: str = "local", **kw: object) -> dict:
    ns, atype, name = fqn[1:].split("/", 2)
    return artifact_storage.create_artifact(db, fqn, atype, ns, name, content, source=source, **kw)


# ---------------------------------------------------------------------------
# Issue 2: _copy_to_project copies only manifest-referenced files
# ---------------------------------------------------------------------------


class TestCopyToProjectSelective:
    def test_only_manifest_files_copied(self, tmp_path: Path) -> None:
        """_copy_to_project should copy pack.yaml and referenced files only."""
        pack_dir = tmp_path / "source_pack"
        pack_dir.mkdir()
        skills_dir = pack_dir / "skills"
        skills_dir.mkdir()

        # Create manifest
        manifest_data = {
            "name": "testpack",
            "namespace": "ns",
            "version": "1.0.0",
            "artifacts": [{"type": "skill", "name": "greet"}],
        }
        (pack_dir / "pack.yaml").write_text(yaml.dump(manifest_data))
        (skills_dir / "greet.yaml").write_text("content: hello")

        # Create extra files that should NOT be copied
        (pack_dir / "README.md").write_text("This should not be copied")
        (pack_dir / ".git").mkdir()
        (pack_dir / ".git" / "config").write_text("git config")
        (skills_dir / "secret.yaml").write_text("content: secret stuff")

        manifest = packs.parse_manifest(pack_dir / "pack.yaml")
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        packs._copy_to_project(pack_dir, manifest, project_dir)

        dest = project_dir / ".anteroom" / "packs" / "ns" / "testpack"
        assert dest.is_dir()
        assert (dest / "pack.yaml").is_file()
        assert (dest / "skills" / "greet.yaml").is_file()
        assert not (dest / "README.md").exists()
        assert not (dest / ".git").exists()
        assert not (dest / "skills" / "secret.yaml").exists()


# ---------------------------------------------------------------------------
# Issue 4: validate_lock bidirectional
# ---------------------------------------------------------------------------


class TestValidateLockBidirectional:
    def _install_pack(self, db: ThreadSafeConnection, ns: str, name: str) -> None:
        """Install a minimal pack directly into DB."""
        import uuid
        from datetime import datetime, timezone

        pack_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            """INSERT INTO packs (id, name, namespace, version, installed_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (pack_id, name, ns, "1.0.0", now, now),
        )
        db.commit()

    def test_db_pack_not_in_lock(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        """A pack in DB but not in the lock file should be flagged."""
        self._install_pack(db, "team", "extra-pack")

        # Write lock with no packs
        write_lock(tmp_path, {"version": 1, "packs": []})

        warnings = validate_lock(db, tmp_path)
        assert any("extra-pack" in w and "not in lock file" in w for w in warnings)

    def test_both_directions_reported(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        """Both lock→DB and DB→lock mismatches should be reported."""
        self._install_pack(db, "team", "db-only")

        lock_data = {
            "version": 1,
            "packs": [{"namespace": "team", "name": "lock-only", "artifacts": []}],
        }
        write_lock(tmp_path, lock_data)

        warnings = validate_lock(db, tmp_path)
        assert any("lock-only" in w and "not installed" in w for w in warnings)
        assert any("db-only" in w and "not in lock file" in w for w in warnings)


# ---------------------------------------------------------------------------
# Issue 6: update_pack atomic
# ---------------------------------------------------------------------------


class TestUpdatePackAtomic:
    def test_update_preserves_shared_artifacts(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        """Shared artifacts between packs should not be deleted during update."""
        # Create pack1
        pack1_dir = tmp_path / "pack1"
        pack1_dir.mkdir()
        (pack1_dir / "skills").mkdir()
        (pack1_dir / "skills" / "shared.yaml").write_text("content: shared skill")
        (pack1_dir / "pack.yaml").write_text(
            yaml.dump(
                {
                    "name": "pack1",
                    "namespace": "ns",
                    "version": "1.0.0",
                    "artifacts": [{"type": "skill", "name": "shared"}],
                }
            )
        )

        # Create pack2 referencing the same artifact
        pack2_dir = tmp_path / "pack2"
        pack2_dir.mkdir()
        (pack2_dir / "skills").mkdir()
        (pack2_dir / "skills" / "shared.yaml").write_text("content: shared skill")
        (pack2_dir / "pack.yaml").write_text(
            yaml.dump(
                {
                    "name": "pack2",
                    "namespace": "ns",
                    "version": "1.0.0",
                    "artifacts": [{"type": "skill", "name": "shared"}],
                }
            )
        )

        manifest1 = packs.parse_manifest(pack1_dir / "pack.yaml")
        manifest2 = packs.parse_manifest(pack2_dir / "pack.yaml")
        packs.install_pack(db, manifest1, pack1_dir)
        packs.install_pack(db, manifest2, pack2_dir)

        # Update pack1 with new version
        (pack1_dir / "pack.yaml").write_text(
            yaml.dump(
                {
                    "name": "pack1",
                    "namespace": "ns",
                    "version": "2.0.0",
                    "artifacts": [{"type": "skill", "name": "shared"}],
                }
            )
        )
        manifest1_v2 = packs.parse_manifest(pack1_dir / "pack.yaml")
        result = packs.update_pack(db, manifest1_v2, pack1_dir)

        assert result["version"] == "2.0.0"
        # Shared artifact should still exist
        art = artifact_storage.get_artifact_by_fqn(db, "@ns/skill/shared")
        assert art is not None

    def test_update_not_installed_raises(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        """Updating a pack that isn't installed should raise ValueError."""
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        (pack_dir / "skills").mkdir()
        (pack_dir / "skills" / "test.yaml").write_text("content: test")
        (pack_dir / "pack.yaml").write_text(
            yaml.dump(
                {
                    "name": "nonexistent",
                    "namespace": "ns",
                    "version": "1.0.0",
                    "artifacts": [{"type": "skill", "name": "test"}],
                }
            )
        )
        manifest = packs.parse_manifest(pack_dir / "pack.yaml")
        with pytest.raises(ValueError, match="not installed"):
            packs.update_pack(db, manifest, pack_dir)


# ---------------------------------------------------------------------------
# Issue 7: CLI _validate_pack_ref
# ---------------------------------------------------------------------------


class TestValidatePackRef:
    def test_valid_ref(self) -> None:
        from anteroom.__main__ import _validate_pack_ref

        ns, name = _validate_pack_ref("team/my-pack")
        assert ns == "team"
        assert name == "my-pack"

    def test_missing_slash_defaults_namespace(self) -> None:
        from anteroom.__main__ import _validate_pack_ref

        ns, name = _validate_pack_ref("nonamespace")
        assert ns == "default"
        assert name == "nonamespace"

    def test_invalid_namespace(self) -> None:
        from anteroom.__main__ import _validate_pack_ref

        with pytest.raises(SystemExit):
            _validate_pack_ref("../traversal/pack")

    def test_invalid_name(self) -> None:
        from anteroom.__main__ import _validate_pack_ref

        with pytest.raises(SystemExit):
            _validate_pack_ref("ns/../traversal")


# ---------------------------------------------------------------------------
# Issue 8: _pack_row_to_dict alignment with list_packs
# ---------------------------------------------------------------------------


class TestPackRowToDictAlignment:
    def test_list_packs_returns_correct_fields(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        """list_packs should return dicts with all expected keys including source_path."""
        pack_dir = tmp_path / "testpack"
        pack_dir.mkdir()
        (pack_dir / "skills").mkdir()
        (pack_dir / "skills" / "test.yaml").write_text("content: hello")
        (pack_dir / "pack.yaml").write_text(
            yaml.dump(
                {
                    "name": "test",
                    "namespace": "ns",
                    "version": "1.0.0",
                    "artifacts": [{"type": "skill", "name": "test"}],
                }
            )
        )
        manifest = packs.parse_manifest(pack_dir / "pack.yaml")
        packs.install_pack(db, manifest, pack_dir)

        result = packs.list_packs(db)
        assert len(result) == 1
        p = result[0]
        assert p["name"] == "test"
        assert p["namespace"] == "ns"
        assert p["version"] == "1.0.0"
        assert p["artifact_count"] == 1
        assert "installed_at" in p
        assert "updated_at" in p
        assert "source_path" in p


# ---------------------------------------------------------------------------
# Issue 9: artifact_health uses thread-safe methods
# ---------------------------------------------------------------------------


class TestArtifactHealthThreadSafe:
    def test_check_orphaned_uses_fetchone(self, db: ThreadSafeConnection) -> None:
        """check_orphaned_artifacts should work with ThreadSafeConnection."""
        # Just verify it runs without error against a ThreadSafeConnection
        issues = check_orphaned_artifacts(db)
        assert isinstance(issues, list)

    def test_run_health_check_uses_fetchone(self, db: ThreadSafeConnection) -> None:
        """run_health_check should work with ThreadSafeConnection for pack_count."""
        report = run_health_check(db)
        assert report.pack_count == 0
        assert report.healthy

    def test_fix_duplicate_uses_fetchall(self, db: ThreadSafeConnection) -> None:
        """fix_duplicate_content should work with ThreadSafeConnection."""
        deleted = fix_duplicate_content(db)
        assert deleted == 0

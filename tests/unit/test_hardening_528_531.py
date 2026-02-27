"""Tests for hardening fixes #528-#531."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services.artifacts import validate_fqn
from anteroom.services.pack_sources import _sanitize_git_stderr, _sanitize_url
from anteroom.services.packs import (
    ManifestArtifact,
    _resolve_artifact_file,
    parse_manifest,
    validate_manifest,
)


def _write_manifest(path, data):
    import yaml
    manifest_path = path / "pack.yaml"
    with open(manifest_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)
    return manifest_path


@pytest.fixture()
def db() -> ThreadSafeConnection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return ThreadSafeConnection(conn)


# --- FQN regex edge cases (#530) ---


class TestFqnRegexEdgeCases:
    """Validate hardened FQN regex rejects edge cases."""

    def test_namespace_leading_hyphen_rejected(self) -> None:
        assert validate_fqn("@-bad/skill/name") is False

    def test_namespace_leading_dot_rejected(self) -> None:
        assert validate_fqn("@.bad/skill/name") is False

    def test_namespace_dots_valid(self) -> None:
        assert validate_fqn("@my.team/skill/greet") is True

    def test_namespace_too_long(self) -> None:
        assert validate_fqn(f"@{'a' * 64}/skill/greet") is False

    def test_namespace_max_length(self) -> None:
        assert validate_fqn(f"@{'a' * 63}/skill/greet") is True

    def test_name_too_long(self) -> None:
        assert validate_fqn(f"@ns/skill/{'a' * 64}") is False

    def test_name_max_length(self) -> None:
        assert validate_fqn(f"@ns/skill/{'a' * 63}") is True

    def test_type_with_digit(self) -> None:
        assert validate_fqn("@ns/skill2/greet") is True

    def test_name_leading_underscore_valid(self) -> None:
        assert validate_fqn("@ns/skill/_hidden") is True

    def test_name_leading_dot_rejected(self) -> None:
        assert validate_fqn("@ns/skill/.hidden") is False


# --- Transaction atomicity: commit=False (#529) ---


class TestCommitFalse:
    """Verify commit=False prevents auto-commit in CRUD functions."""

    def test_create_no_commit(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.artifact_storage import create_artifact

        create_artifact(
            db, "@ns/skill/x", "skill", "ns", "x", "content", commit=False
        )
        db._conn.rollback()
        row = db.execute_fetchone(
            "SELECT * FROM artifacts WHERE fqn = ?", ("@ns/skill/x",)
        )
        assert row is None

    def test_create_with_commit(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.artifact_storage import create_artifact

        create_artifact(
            db, "@ns/skill/y", "skill", "ns", "y", "content", commit=True
        )
        db._conn.rollback()
        row = db.execute_fetchone(
            "SELECT * FROM artifacts WHERE fqn = ?", ("@ns/skill/y",)
        )
        assert row is not None

    def test_update_no_commit(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.artifact_storage import create_artifact, update_artifact

        art = create_artifact(
            db, "@ns/skill/u", "skill", "ns", "u", "v1", commit=True
        )
        update_artifact(db, art["id"], content="v2", commit=False)
        db._conn.rollback()
        row = db.execute_fetchone(
            "SELECT content FROM artifacts WHERE id = ?", (art["id"],)
        )
        assert row["content"] == "v1"

    def test_delete_no_commit(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.artifact_storage import create_artifact, delete_artifact

        art = create_artifact(
            db, "@ns/skill/d", "skill", "ns", "d", "content", commit=True
        )
        delete_artifact(db, art["id"], commit=False)
        db._conn.rollback()
        row = db.execute_fetchone(
            "SELECT * FROM artifacts WHERE id = ?", (art["id"],)
        )
        assert row is not None

    def test_upsert_no_commit(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.artifact_storage import upsert_artifact

        upsert_artifact(
            db, "@ns/skill/up", "skill", "ns", "up", "content", commit=False
        )
        db._conn.rollback()
        row = db.execute_fetchone(
            "SELECT * FROM artifacts WHERE fqn = ?", ("@ns/skill/up",)
        )
        assert row is None

    def test_transaction_rollback(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.artifact_storage import create_artifact

        with pytest.raises(RuntimeError):
            with db.transaction():
                create_artifact(
                    db, "@ns/skill/txn", "skill", "ns", "txn", "data", commit=False
                )
                raise RuntimeError("force rollback")

        row = db.execute_fetchone(
            "SELECT * FROM artifacts WHERE fqn = ?", ("@ns/skill/txn",)
        )
        assert row is None


# --- Symlink rejection tests (#531) ---


class TestSymlinkRejection:
    """Validate that symlinks are rejected in pack manifests and resolution."""

    def test_validate_manifest_rejects_symlink_explicit_file(
        self, tmp_path: Path
    ) -> None:
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        skills_dir = pack_dir / "skills"
        skills_dir.mkdir()
        target = tmp_path / "secret.txt"
        target.write_text("secret data")
        link = skills_dir / "evil.yaml"
        link.symlink_to(target)
        _write_manifest(
            pack_dir,
            {
                "name": "p",
                "namespace": "ns",
                "artifacts": [
                    {"type": "skill", "name": "evil", "file": "skills/evil.yaml"}
                ],
            },
        )
        manifest = parse_manifest(pack_dir / "pack.yaml")
        errors = validate_manifest(manifest, pack_dir)
        assert any("ymlink" in e for e in errors)

    def test_validate_manifest_rejects_symlink_auto_resolved(
        self, tmp_path: Path
    ) -> None:
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        skills_dir = pack_dir / "skills"
        skills_dir.mkdir()
        target = tmp_path / "secret.txt"
        target.write_text("secret data")
        link = skills_dir / "evil.yaml"
        link.symlink_to(target)
        _write_manifest(
            pack_dir,
            {
                "name": "p",
                "namespace": "ns",
                "artifacts": [{"type": "skill", "name": "evil"}],
            },
        )
        manifest = parse_manifest(pack_dir / "pack.yaml")
        errors = validate_manifest(manifest, pack_dir)
        assert any("ymlink" in e for e in errors)

    def test_resolve_artifact_file_rejects_symlink(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        target = tmp_path / "real.yaml"
        target.write_text("content: real")
        link = skills_dir / "evil.yaml"
        link.symlink_to(target)
        art = ManifestArtifact(type="skill", name="evil")
        result = _resolve_artifact_file(art, tmp_path)
        assert result is None

    def test_resolve_artifact_file_rejects_explicit_symlink(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "real.txt"
        target.write_text("real content")
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        art = ManifestArtifact(type="rule", name="r", file="link.txt")
        result = _resolve_artifact_file(art, tmp_path)
        assert result is None


# --- Credential sanitization tests (#531) ---


class TestSanitizeUrl:
    """Verify _sanitize_url strips credentials from various URL schemes."""

    def test_https_credentials(self) -> None:
        url = "https://user:token@github.com/org/repo.git"
        sanitized = _sanitize_url(url)
        assert "token" not in sanitized
        assert "***@" in sanitized

    def test_ssh_credentials(self) -> None:
        url = "ssh://deploy:secret@git.internal.com/repo.git"
        sanitized = _sanitize_url(url)
        assert "secret" not in sanitized
        assert "***@" in sanitized

    def test_git_scheme_credentials(self) -> None:
        url = "git://user:pass@git.example.com/repo.git"
        sanitized = _sanitize_url(url)
        assert "pass" not in sanitized
        assert "***@" in sanitized

    def test_no_credentials_unchanged(self) -> None:
        url = "https://github.com/org/repo.git"
        assert _sanitize_url(url) == url

    def test_git_at_shorthand_unchanged(self) -> None:
        url = "git@github.com:org/repo.git"
        assert _sanitize_url(url) == url


class TestSanitizeGitStderrExtended:
    """Extended tests for _sanitize_git_stderr covering ssh/git schemes."""

    def test_strips_ssh_credentials(self) -> None:
        stderr = (
            "fatal: could not read from ssh://deploy:secret@git.corp.com/repo.git"
        )
        sanitized = _sanitize_git_stderr(stderr)
        assert "secret" not in sanitized
        assert "***@" in sanitized

    def test_strips_git_scheme_credentials(self) -> None:
        stderr = (
            "fatal: could not read from git://user:pass@git.example.com/repo.git"
        )
        sanitized = _sanitize_git_stderr(stderr)
        assert "pass" not in sanitized
        assert "***@" in sanitized

    def test_strips_username_prompt(self) -> None:
        stderr = "Username for 'https://github.com': "
        sanitized = _sanitize_git_stderr(stderr)
        assert "github.com" not in sanitized
        assert "***" in sanitized

"""Tests for project-scoped pack attachments (#874).

Covers path normalization helpers, boundary-safe ancestor matching in
get_active_pack_ids / list_attachments / list_artifacts, and the
project_path parameter plumbing through the artifact registry.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services.artifact_storage import create_artifact, list_artifacts
from anteroom.services.pack_attachments import (
    _normalize_for_comparison,
    _normalize_project_path,
    attach_pack,
    get_active_pack_ids,
    get_active_pack_ids_for_space,
    list_attachments,
)


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
    pack_id: str = "pack-1",
    namespace: str = "test",
    name: str = "my-pack",
    version: str = "1.0.0",
) -> str:
    db.execute(
        "INSERT INTO packs (id, namespace, name, version, description,"
        " source_path, installed_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (pack_id, namespace, name, version, "A test pack", ""),
    )
    db.commit()
    return pack_id


def _insert_artifact(
    db: ThreadSafeConnection,
    fqn: str = "@test/skill/my-skill",
    artifact_type: str = "skill",
    namespace: str = "test",
    name: str = "my-skill",
) -> str:
    result = create_artifact(
        db,
        fqn=fqn,
        artifact_type=artifact_type,
        namespace=namespace,
        name=name,
        content="test content",
        source="local",
    )
    return result["id"]


def _link_artifact_to_pack(
    db: ThreadSafeConnection,
    pack_id: str,
    artifact_id: str,
) -> None:
    db.execute(
        "INSERT INTO pack_artifacts (pack_id, artifact_id) VALUES (?, ?)",
        (pack_id, artifact_id),
    )
    db.commit()


# ---------------------------------------------------------------------------
# _normalize_project_path
# ---------------------------------------------------------------------------


class TestNormalizeProjectPath:
    def test_unix_absolute_path(self) -> None:
        with patch("os.path.realpath", return_value="/home/user/project"):
            result = _normalize_project_path("/home/user/project")
        assert result == "/home/user/project"

    def test_strips_trailing_slash(self) -> None:
        with patch("os.path.realpath", return_value="/home/user/project/"):
            result = _normalize_project_path("/home/user/project/")
        assert result == "/home/user/project"

    def test_preserves_unix_root(self) -> None:
        with patch("os.path.realpath", return_value="/"):
            result = _normalize_project_path("/")
        assert result == "/"

    def test_preserves_windows_drive_root(self) -> None:
        with patch("os.path.realpath", return_value="C:/"):
            with patch("os.sep", "/"):
                result = _normalize_project_path("C:/")
        assert result == "C:/"

    def test_normalizes_backslashes_on_windows(self) -> None:
        with patch("os.path.realpath", return_value="C:\\Users\\dev\\project"):
            with patch("os.sep", "\\"):
                result = _normalize_project_path("C:\\Users\\dev\\project")
        assert result == "C:/Users/dev/project"


# ---------------------------------------------------------------------------
# _normalize_for_comparison
# ---------------------------------------------------------------------------


class TestNormalizeForComparison:
    def test_replaces_backslashes(self) -> None:
        assert _normalize_for_comparison("C:\\Users\\dev") == "C:/Users/dev"

    def test_strips_trailing_slash(self) -> None:
        assert _normalize_for_comparison("/home/user/") == "/home/user"

    def test_preserves_unix_root(self) -> None:
        assert _normalize_for_comparison("/") == "/"

    def test_preserves_windows_root(self) -> None:
        assert _normalize_for_comparison("C:/") == "C:/"

    def test_forward_slashes_unchanged(self) -> None:
        assert _normalize_for_comparison("/a/b/c") == "/a/b/c"


# ---------------------------------------------------------------------------
# attach_pack stores normalized path
# ---------------------------------------------------------------------------


class TestAttachPackNormalization:
    def test_attach_stores_normalized_path(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        with patch(
            "anteroom.services.pack_attachments.os.path.realpath",
            return_value="/home/user/project",
        ):
            result = attach_pack(
                db,
                "pack-1",
                project_path="/home/user/project/",
            )
        assert result["project_path"] == "/home/user/project"


# ---------------------------------------------------------------------------
# get_active_pack_ids — exact match and ancestor matching
# ---------------------------------------------------------------------------


class TestGetActivePackIdsProjectScope:
    def test_exact_path_match(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        attach_pack(db, "pack-1", project_path="/repo")
        ids = get_active_pack_ids(db, project_path="/repo")
        assert "pack-1" in ids

    def test_subdirectory_ancestor_match(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        attach_pack(db, "pack-1", project_path="/repo")
        ids = get_active_pack_ids(db, project_path="/repo/src/lib")
        assert "pack-1" in ids

    def test_sibling_prefix_excluded(self, db: ThreadSafeConnection) -> None:
        """Boundary safety: /repo must NOT match /repo2."""
        _insert_pack(db)
        attach_pack(db, "pack-1", project_path="/repo")
        ids = get_active_pack_ids(db, project_path="/repo2")
        assert "pack-1" not in ids

    def test_global_always_included(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db, pack_id="global-pack", name="global")
        _insert_pack(db, pack_id="proj-pack", name="proj")
        attach_pack(db, "global-pack")
        attach_pack(db, "proj-pack", project_path="/repo")
        ids = get_active_pack_ids(db, project_path="/repo")
        assert "global-pack" in ids
        assert "proj-pack" in ids

    def test_no_project_path_excludes_project_packs(
        self,
        db: ThreadSafeConnection,
    ) -> None:
        _insert_pack(db)
        attach_pack(db, "pack-1", project_path="/repo")
        ids = get_active_pack_ids(db)
        assert "pack-1" not in ids


# ---------------------------------------------------------------------------
# list_attachments — ancestor matching
# ---------------------------------------------------------------------------


class TestListAttachmentsProjectScope:
    def test_subdirectory_shows_ancestor_attachment(
        self,
        db: ThreadSafeConnection,
    ) -> None:
        _insert_pack(db)
        attach_pack(db, "pack-1", project_path="/repo")
        result = list_attachments(db, project_path="/repo/sub")
        assert len(result) == 1
        assert result[0]["pack_id"] == "pack-1"

    def test_sibling_excluded(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        attach_pack(db, "pack-1", project_path="/repo")
        result = list_attachments(db, project_path="/repo2")
        assert len(result) == 0


# ---------------------------------------------------------------------------
# get_active_pack_ids_for_space — three-scope union with ancestor matching
# ---------------------------------------------------------------------------


class TestGetActivePackIdsForSpaceProjectScope:
    def test_includes_project_ancestor(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        db.execute(
            "INSERT INTO spaces (id, name, created_at, updated_at)"
            " VALUES ('sp1', 'test-space', datetime('now'), datetime('now'))",
        )
        db.commit()
        attach_pack(db, "pack-1", project_path="/repo")
        ids = get_active_pack_ids_for_space(
            db,
            "sp1",
            project_path="/repo/sub",
        )
        assert "pack-1" in ids

    def test_excludes_sibling_prefix(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        db.execute(
            "INSERT INTO spaces (id, name, created_at, updated_at)"
            " VALUES ('sp1', 'test-space', datetime('now'), datetime('now'))",
        )
        db.commit()
        attach_pack(db, "pack-1", project_path="/repo")
        ids = get_active_pack_ids_for_space(
            db,
            "sp1",
            project_path="/repo2",
        )
        assert "pack-1" not in ids


# ---------------------------------------------------------------------------
# list_artifacts with attached_only + project_path
# ---------------------------------------------------------------------------


class TestListArtifactsProjectScope:
    def test_includes_project_scoped_artifact(
        self,
        db: ThreadSafeConnection,
    ) -> None:
        _insert_pack(db)
        art_id = _insert_artifact(db)
        _link_artifact_to_pack(db, "pack-1", art_id)
        attach_pack(db, "pack-1", project_path="/repo")

        results = list_artifacts(
            db,
            attached_only=True,
            project_path="/repo",
        )
        fqns = [r["fqn"] for r in results]
        assert "@test/skill/my-skill" in fqns

    def test_includes_project_ancestor_artifact(
        self,
        db: ThreadSafeConnection,
    ) -> None:
        _insert_pack(db)
        art_id = _insert_artifact(db)
        _link_artifact_to_pack(db, "pack-1", art_id)
        attach_pack(db, "pack-1", project_path="/repo")

        results = list_artifacts(
            db,
            attached_only=True,
            project_path="/repo/deep/sub",
        )
        fqns = [r["fqn"] for r in results]
        assert "@test/skill/my-skill" in fqns

    def test_excludes_project_artifact_when_no_project_path(
        self,
        db: ThreadSafeConnection,
    ) -> None:
        _insert_pack(db)
        art_id = _insert_artifact(db)
        _link_artifact_to_pack(db, "pack-1", art_id)
        attach_pack(db, "pack-1", project_path="/repo")

        results = list_artifacts(db, attached_only=True)
        fqns = [r["fqn"] for r in results]
        assert "@test/skill/my-skill" not in fqns

    def test_excludes_project_artifact_with_sibling_path(
        self,
        db: ThreadSafeConnection,
    ) -> None:
        _insert_pack(db)
        art_id = _insert_artifact(db)
        _link_artifact_to_pack(db, "pack-1", art_id)
        attach_pack(db, "pack-1", project_path="/repo")

        results = list_artifacts(
            db,
            attached_only=True,
            project_path="/repo2",
        )
        fqns = [r["fqn"] for r in results]
        assert "@test/skill/my-skill" not in fqns

    def test_standalone_artifact_always_visible(
        self,
        db: ThreadSafeConnection,
    ) -> None:
        """Artifacts not linked to any pack are always included."""
        _insert_artifact(db)
        results = list_artifacts(db, attached_only=True)
        fqns = [r["fqn"] for r in results]
        assert "@test/skill/my-skill" in fqns

    def test_global_attachment_visible_without_project_path(
        self,
        db: ThreadSafeConnection,
    ) -> None:
        _insert_pack(db)
        art_id = _insert_artifact(db)
        _link_artifact_to_pack(db, "pack-1", art_id)
        attach_pack(db, "pack-1")

        results = list_artifacts(db, attached_only=True)
        fqns = [r["fqn"] for r in results]
        assert "@test/skill/my-skill" in fqns


# ---------------------------------------------------------------------------
# ArtifactRegistry.load_from_db with project_path
# ---------------------------------------------------------------------------


class TestArtifactRegistryProjectPath:
    def test_load_from_db_passes_project_path(
        self,
        db: ThreadSafeConnection,
    ) -> None:
        from anteroom.services.artifact_registry import ArtifactRegistry

        _insert_pack(db)
        art_id = _insert_artifact(db)
        _link_artifact_to_pack(db, "pack-1", art_id)
        attach_pack(db, "pack-1", project_path="/repo")

        registry = ArtifactRegistry()
        registry.load_from_db(db, project_path="/repo")
        assert registry.get("@test/skill/my-skill") is not None

    def test_load_from_db_without_project_path_excludes(
        self,
        db: ThreadSafeConnection,
    ) -> None:
        from anteroom.services.artifact_registry import ArtifactRegistry

        _insert_pack(db)
        art_id = _insert_artifact(db)
        _link_artifact_to_pack(db, "pack-1", art_id)
        attach_pack(db, "pack-1", project_path="/repo")

        registry = ArtifactRegistry()
        registry.load_from_db(db)
        assert registry.get("@test/skill/my-skill") is None

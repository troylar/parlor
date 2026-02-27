"""Tests for services/pack_attachments.py."""

from __future__ import annotations

import sqlite3

import pytest

from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services.pack_attachments import (
    attach_pack,
    detach_pack,
    get_active_pack_ids,
    list_attachments,
    resolve_pack_id,
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


class TestAttachPack:
    def test_attach_global(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        result = attach_pack(db, "pack-1")
        assert result["pack_id"] == "pack-1"
        assert result["scope"] == "global"
        assert result["project_path"] is None
        assert result["id"]

    def test_attach_project(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        result = attach_pack(db, "pack-1", project_path="/my/project")
        assert result["scope"] == "project"
        assert result["project_path"] == "/my/project"

    def test_attach_nonexistent_pack_raises(self, db: ThreadSafeConnection) -> None:
        with pytest.raises(ValueError, match="Pack not found"):
            attach_pack(db, "no-such-pack")

    def test_attach_duplicate_raises(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        attach_pack(db, "pack-1")
        with pytest.raises(ValueError, match="already attached"):
            attach_pack(db, "pack-1")

    def test_attach_same_pack_different_scopes(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        attach_pack(db, "pack-1")
        result = attach_pack(db, "pack-1", project_path="/proj")
        assert result["scope"] == "project"


class TestDetachPack:
    def test_detach_existing(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        attach_pack(db, "pack-1")
        assert detach_pack(db, "pack-1") is True

    def test_detach_nonexistent_returns_false(self, db: ThreadSafeConnection) -> None:
        assert detach_pack(db, "no-such-pack") is False

    def test_detach_wrong_scope_returns_false(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        attach_pack(db, "pack-1")
        assert detach_pack(db, "pack-1", project_path="/other") is False

    def test_detach_project_leaves_global(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        attach_pack(db, "pack-1")
        attach_pack(db, "pack-1", project_path="/proj")
        detach_pack(db, "pack-1", project_path="/proj")
        ids = get_active_pack_ids(db)
        assert "pack-1" in ids


class TestListAttachments:
    def test_empty(self, db: ThreadSafeConnection) -> None:
        assert list_attachments(db) == []

    def test_global_only(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        attach_pack(db, "pack-1")
        result = list_attachments(db)
        assert len(result) == 1
        assert result[0]["namespace"] == "test"
        assert result[0]["name"] == "my-pack"

    def test_project_includes_global(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        attach_pack(db, "pack-1")
        _insert_pack(db, pack_id="pack-2", name="other-pack")
        attach_pack(db, "pack-2", project_path="/proj")
        result = list_attachments(db, project_path="/proj")
        assert len(result) == 2

    def test_project_excludes_other_projects(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        attach_pack(db, "pack-1", project_path="/proj-a")
        result = list_attachments(db, project_path="/proj-b")
        assert len(result) == 0


class TestGetActivePackIds:
    def test_empty(self, db: ThreadSafeConnection) -> None:
        assert get_active_pack_ids(db) == []

    def test_global(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        attach_pack(db, "pack-1")
        ids = get_active_pack_ids(db)
        assert ids == ["pack-1"]

    def test_project_merges_global(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        _insert_pack(db, pack_id="pack-2", name="other-pack")
        attach_pack(db, "pack-1")
        attach_pack(db, "pack-2", project_path="/proj")
        ids = get_active_pack_ids(db, project_path="/proj")
        assert set(ids) == {"pack-1", "pack-2"}

    def test_no_duplicates(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        attach_pack(db, "pack-1")
        attach_pack(db, "pack-1", project_path="/proj")
        ids = get_active_pack_ids(db, project_path="/proj")
        assert ids.count("pack-1") == 1


class TestResolvePackId:
    def test_found(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        assert resolve_pack_id(db, "test", "my-pack") == "pack-1"

    def test_not_found(self, db: ThreadSafeConnection) -> None:
        assert resolve_pack_id(db, "test", "nope") is None


class TestProjectPathValidation:
    def test_rejects_traversal(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        with pytest.raises(ValueError, match="must not contain"):
            attach_pack(db, "pack-1", project_path="/foo/../etc")


class TestCascadeDelete:
    def test_deleting_pack_removes_attachments(self, db: ThreadSafeConnection) -> None:
        _insert_pack(db)
        attach_pack(db, "pack-1")
        db.execute("DELETE FROM packs WHERE id = 'pack-1'")
        db.commit()
        rows = db.execute("SELECT * FROM pack_attachments").fetchall()
        assert len(rows) == 0

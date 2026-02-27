"""Tests for space-scoped pack attachments."""

from __future__ import annotations

import sqlite3

import pytest

from anteroom.services.pack_attachments import (
    attach_pack_to_space,
    detach_pack_from_space,
    get_active_pack_ids_for_space,
)


def _make_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute(
        "CREATE TABLE spaces (id TEXT PRIMARY KEY, name TEXT, file_path TEXT, "
        "file_hash TEXT DEFAULT '', last_loaded_at TEXT, created_at TEXT, updated_at TEXT)"
    )
    db.execute(
        "CREATE TABLE packs (id TEXT PRIMARY KEY, name TEXT, namespace TEXT, "
        "version TEXT DEFAULT '0.0.0', description TEXT DEFAULT '', "
        "source_path TEXT DEFAULT '', installed_at TEXT, updated_at TEXT, UNIQUE(namespace, name))"
    )
    db.execute(
        "CREATE TABLE pack_attachments ("
        "id TEXT PRIMARY KEY, pack_id TEXT NOT NULL, project_path TEXT, "
        "space_id TEXT DEFAULT NULL, "
        "scope TEXT NOT NULL CHECK(scope IN ('global', 'project', 'space')), "
        "created_at TEXT, UNIQUE(pack_id, project_path), "
        "FOREIGN KEY(pack_id) REFERENCES packs(id) ON DELETE CASCADE, "
        "FOREIGN KEY(space_id) REFERENCES spaces(id) ON DELETE CASCADE)"
    )
    db.execute("INSERT INTO spaces VALUES ('sp1', 'myspace', '/tmp/s.yaml', '', '', '', '')")
    db.execute("INSERT INTO packs VALUES ('pk1', 'core', 'ns', '1.0.0', '', '', '', '')")
    db.execute("INSERT INTO packs VALUES ('pk2', 'extra', 'ns', '1.0.0', '', '', '', '')")
    db.execute("INSERT INTO packs VALUES ('pk3', 'proj-only', 'ns', '1.0.0', '', '', '', '')")
    db.commit()
    return db


def test_attach_pack_to_space_happy_path() -> None:
    db = _make_db()
    result = attach_pack_to_space(db, "pk1", "sp1")
    assert result["pack_id"] == "pk1"
    assert result["space_id"] == "sp1"
    assert result["scope"] == "space"
    assert result["id"]


def test_attach_pack_to_space_duplicate_raises() -> None:
    db = _make_db()
    attach_pack_to_space(db, "pk1", "sp1")
    with pytest.raises(ValueError, match="already attached"):
        attach_pack_to_space(db, "pk1", "sp1")


def test_attach_pack_to_space_not_found_raises() -> None:
    db = _make_db()
    with pytest.raises(ValueError, match="Pack not found"):
        attach_pack_to_space(db, "nonexistent", "sp1")


def test_detach_pack_from_space_happy_path() -> None:
    db = _make_db()
    attach_pack_to_space(db, "pk1", "sp1")
    assert detach_pack_from_space(db, "pk1", "sp1") is True


def test_detach_pack_from_space_not_found() -> None:
    db = _make_db()
    assert detach_pack_from_space(db, "pk1", "sp1") is False


def test_active_ids_include_global_and_space() -> None:
    db = _make_db()
    # Global attachment
    db.execute("INSERT INTO pack_attachments (id, pack_id, scope, created_at) VALUES ('a1', 'pk1', 'global', '')")
    # Space attachment
    attach_pack_to_space(db, "pk2", "sp1")
    db.commit()

    ids = get_active_pack_ids_for_space(db, "sp1")
    assert "pk1" in ids
    assert "pk2" in ids


def test_active_ids_three_scope_union() -> None:
    db = _make_db()
    # Global
    db.execute("INSERT INTO pack_attachments (id, pack_id, scope, created_at) VALUES ('a1', 'pk1', 'global', '')")
    # Space
    attach_pack_to_space(db, "pk2", "sp1")
    # Project
    db.execute(
        "INSERT INTO pack_attachments (id, pack_id, project_path, scope, created_at) "
        "VALUES ('a3', 'pk3', '/proj', 'project', '')"
    )
    db.commit()

    ids = get_active_pack_ids_for_space(db, "sp1", project_path="/proj")
    assert set(ids) == {"pk1", "pk2", "pk3"}


def test_active_ids_without_project_excludes_project_packs() -> None:
    db = _make_db()
    # Global
    db.execute("INSERT INTO pack_attachments (id, pack_id, scope, created_at) VALUES ('a1', 'pk1', 'global', '')")
    # Project-only
    db.execute(
        "INSERT INTO pack_attachments (id, pack_id, project_path, scope, created_at) "
        "VALUES ('a3', 'pk3', '/proj', 'project', '')"
    )
    db.commit()

    ids = get_active_pack_ids_for_space(db, "sp1")
    assert "pk1" in ids
    assert "pk3" not in ids

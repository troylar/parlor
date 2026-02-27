"""Unit tests for services/space_storage.py — Space DB CRUD."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from anteroom.db import _FTS_SCHEMA, _FTS_TRIGGERS, _SCHEMA, _create_indexes
from anteroom.services.space_storage import (
    count_space_conversations,
    create_space,
    delete_space,
    discover_space_file,
    get_space,
    get_space_by_name,
    get_space_paths,
    get_spaces_by_name,
    list_spaces,
    resolve_space,
    resolve_space_by_cwd,
    sync_space_paths,
    update_space,
)


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    _create_indexes(conn)
    try:
        conn.executescript(_FTS_SCHEMA)
        conn.executescript(_FTS_TRIGGERS)
    except sqlite3.OperationalError:
        pass
    return conn


class TestCreateSpace:
    def test_create_returns_dict(self) -> None:
        db = _make_db()
        s = create_space(db, "myspace", "/path/to/space.yaml", "abc123")
        assert s["name"] == "myspace"
        assert s["file_path"] == "/path/to/space.yaml"
        assert s["file_hash"] == "abc123"
        assert "id" in s

    def test_create_duplicate_name_allowed(self) -> None:
        db = _make_db()
        s1 = create_space(db, "dup", "/path.yaml")
        s2 = create_space(db, "dup", "/other.yaml")
        assert s1["id"] != s2["id"]
        assert s1["name"] == s2["name"]


class TestGetSpace:
    def test_get_by_id(self) -> None:
        db = _make_db()
        s = create_space(db, "myspace", "/path.yaml")
        got = get_space(db, s["id"])
        assert got is not None
        assert got["name"] == "myspace"

    def test_get_missing(self) -> None:
        db = _make_db()
        assert get_space(db, "nonexistent") is None

    def test_get_by_name(self) -> None:
        db = _make_db()
        create_space(db, "findme", "/path.yaml")
        got = get_space_by_name(db, "findme")
        assert got is not None
        assert got["name"] == "findme"


class TestListSpaces:
    def test_list_empty(self) -> None:
        db = _make_db()
        assert list_spaces(db) == []

    def test_list_multiple(self) -> None:
        db = _make_db()
        create_space(db, "b-space", "/b.yaml")
        create_space(db, "a-space", "/a.yaml")
        spaces = list_spaces(db)
        assert len(spaces) == 2
        assert spaces[0]["name"] == "a-space"  # sorted by name


class TestUpdateSpace:
    def test_update_file_hash(self) -> None:
        db = _make_db()
        s = create_space(db, "myspace", "/path.yaml", "old")
        updated = update_space(db, s["id"], file_hash="new")
        assert updated is not None
        assert updated["file_hash"] == "new"

    def test_update_bad_column(self) -> None:
        import pytest

        db = _make_db()
        s = create_space(db, "myspace", "/path.yaml")
        with pytest.raises(ValueError, match="Cannot update"):
            update_space(db, s["id"], name="bad")

    def test_update_empty_noop(self) -> None:
        db = _make_db()
        s = create_space(db, "myspace", "/path.yaml")
        got = update_space(db, s["id"])
        assert got is not None
        assert got["name"] == "myspace"


class TestDeleteSpace:
    def test_delete(self) -> None:
        db = _make_db()
        s = create_space(db, "myspace", "/path.yaml")
        assert delete_space(db, s["id"]) is True
        assert get_space(db, s["id"]) is None

    def test_delete_missing(self) -> None:
        db = _make_db()
        assert delete_space(db, "nonexistent") is False


class TestSpacePaths:
    def test_sync_and_get(self) -> None:
        db = _make_db()
        s = create_space(db, "myspace", "/path.yaml")
        sync_space_paths(
            db,
            s["id"],
            [
                {"repo_url": "https://github.com/org/repo.git", "local_path": "/tmp/repo"},
            ],
        )
        paths = get_space_paths(db, s["id"])
        assert len(paths) == 1
        assert paths[0]["repo_url"] == "https://github.com/org/repo.git"

    def test_sync_replaces(self) -> None:
        db = _make_db()
        s = create_space(db, "myspace", "/path.yaml")
        sync_space_paths(db, s["id"], [{"repo_url": "a", "local_path": "/a"}])
        sync_space_paths(db, s["id"], [{"repo_url": "b", "local_path": "/b"}])
        paths = get_space_paths(db, s["id"])
        assert len(paths) == 1
        assert paths[0]["repo_url"] == "b"


class TestConversationSpace:
    def test_count(self) -> None:
        db = _make_db()
        s = create_space(db, "myspace", "/path.yaml")
        assert count_space_conversations(db, s["id"]) == 0

    def test_resolve_by_cwd(self) -> None:
        db = _make_db()
        s = create_space(db, "myspace", "/path.yaml")
        sync_space_paths(db, s["id"], [{"repo_url": "https://github.com/org/repo.git", "local_path": "/tmp/repo"}])
        resolved = resolve_space_by_cwd(db, "/tmp/repo")
        assert resolved is not None
        assert resolved["name"] == "myspace"

    def test_resolve_by_cwd_walks_up_parents(self) -> None:
        db = _make_db()
        s = create_space(db, "myspace", "/path.yaml")
        sync_space_paths(db, s["id"], [{"repo_url": "", "local_path": "/tmp/repo"}])
        resolved = resolve_space_by_cwd(db, "/tmp/repo/src/subdir/nested")
        assert resolved is not None
        assert resolved["name"] == "myspace"

    def test_resolve_by_cwd_no_match(self) -> None:
        db = _make_db()
        assert resolve_space_by_cwd(db, "/nonexistent") is None


class TestDiscoverSpaceFile:
    def test_finds_space_yaml(self, tmp_path: Path) -> None:
        d = tmp_path / ".anteroom"
        d.mkdir()
        f = d / "space.yaml"
        f.write_text("name: test\n")
        assert discover_space_file(str(tmp_path)) == f

    def test_finds_named_yaml(self, tmp_path: Path) -> None:
        d = tmp_path / ".anteroom"
        d.mkdir()
        f = d / "my-project.yaml"
        f.write_text("name: my-project\n")
        assert discover_space_file(str(tmp_path)) == f

    def test_walks_up(self, tmp_path: Path) -> None:
        d = tmp_path / ".anteroom"
        d.mkdir()
        f = d / "space.yaml"
        f.write_text("name: test\n")
        nested = tmp_path / "src" / "subdir"
        nested.mkdir(parents=True)
        assert discover_space_file(str(nested)) == f

    def test_skips_local_yaml(self, tmp_path: Path) -> None:
        d = tmp_path / ".anteroom"
        d.mkdir()
        (d / "test.local.yaml").write_text("name: test\n")
        assert discover_space_file(str(tmp_path)) is None

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        assert discover_space_file(str(tmp_path)) is None

    def test_prefers_space_yaml(self, tmp_path: Path) -> None:
        d = tmp_path / ".anteroom"
        d.mkdir()
        canonical = d / "space.yaml"
        canonical.write_text("name: canonical\n")
        (d / "other.yaml").write_text("name: other\n")
        assert discover_space_file(str(tmp_path)) == canonical

    def test_checks_claude_dir(self, tmp_path: Path) -> None:
        d = tmp_path / ".claude"
        d.mkdir()
        f = d / "space.yaml"
        f.write_text("name: test\n")
        assert discover_space_file(str(tmp_path)) == f

    def test_anteroom_preferred_over_claude(self, tmp_path: Path) -> None:
        ad = tmp_path / ".anteroom"
        ad.mkdir()
        af = ad / "space.yaml"
        af.write_text("name: anteroom\n")
        cd = tmp_path / ".claude"
        cd.mkdir()
        (cd / "space.yaml").write_text("name: claude\n")
        assert discover_space_file(str(tmp_path)) == af


class TestGetSpacesByName:
    def test_returns_all_matches(self) -> None:
        db = _make_db()
        s1 = create_space(db, "dup", "/a.yaml")
        s2 = create_space(db, "dup", "/b.yaml")
        matches = get_spaces_by_name(db, "dup")
        assert len(matches) == 2
        ids = {m["id"] for m in matches}
        assert s1["id"] in ids
        assert s2["id"] in ids

    def test_returns_empty_on_no_match(self) -> None:
        db = _make_db()
        assert get_spaces_by_name(db, "nonexistent") == []


class TestResolveSpace:
    def test_resolve_by_exact_id(self) -> None:
        db = _make_db()
        s = create_space(db, "myspace", "/path.yaml")
        match, candidates = resolve_space(db, s["id"])
        assert match is not None
        assert match["id"] == s["id"]
        assert candidates == []

    def test_resolve_by_name_unique(self) -> None:
        db = _make_db()
        s = create_space(db, "unique", "/path.yaml")
        match, candidates = resolve_space(db, "unique")
        assert match is not None
        assert match["id"] == s["id"]
        assert candidates == []

    def test_resolve_by_name_ambiguous(self) -> None:
        db = _make_db()
        create_space(db, "dup", "/a.yaml")
        create_space(db, "dup", "/b.yaml")
        match, candidates = resolve_space(db, "dup")
        assert match is None
        assert len(candidates) == 2

    def test_resolve_by_id_prefix(self) -> None:
        db = _make_db()
        s = create_space(db, "myspace", "/path.yaml")
        prefix = s["id"][:8]
        match, candidates = resolve_space(db, prefix)
        assert match is not None
        assert match["id"] == s["id"]

    def test_resolve_not_found(self) -> None:
        db = _make_db()
        match, candidates = resolve_space(db, "nonexistent")
        assert match is None
        assert candidates == []

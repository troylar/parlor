"""Tests for space-source integration (link/unlink/get)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from anteroom.db import ThreadSafeConnection


def _make_db() -> ThreadSafeConnection:
    """Create an in-memory DB with required tables."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "CREATE TABLE spaces (id TEXT PRIMARY KEY, name TEXT, file_path TEXT, "
        "file_hash TEXT DEFAULT '', last_loaded_at TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE sources (id TEXT PRIMARY KEY, type TEXT, title TEXT, content TEXT, "
        "mime_type TEXT, filename TEXT, url TEXT, storage_path TEXT, size_bytes INTEGER, "
        "content_hash TEXT, user_id TEXT, user_display_name TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE tags (id TEXT PRIMARY KEY, name TEXT UNIQUE, color TEXT DEFAULT '#3b82f6', "
        "user_id TEXT, user_display_name TEXT, created_at TEXT)"
    )
    conn.execute("CREATE TABLE source_tags (source_id TEXT, tag_id TEXT, PRIMARY KEY (source_id, tag_id))")
    conn.execute(
        "CREATE TABLE source_groups (id TEXT PRIMARY KEY, name TEXT, description TEXT DEFAULT '', "
        "user_id TEXT, user_display_name TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.execute("CREATE TABLE source_group_members (group_id TEXT, source_id TEXT, PRIMARY KEY (group_id, source_id))")
    conn.execute(
        "CREATE TABLE space_sources ("
        "space_id TEXT NOT NULL, source_id TEXT, group_id TEXT, tag_filter TEXT, "
        "created_at TEXT NOT NULL, "
        "FOREIGN KEY (space_id) REFERENCES spaces(id) ON DELETE CASCADE, "
        "CHECK ("
        "  (source_id IS NOT NULL AND group_id IS NULL AND tag_filter IS NULL) OR "
        "  (source_id IS NULL AND group_id IS NOT NULL AND tag_filter IS NULL) OR "
        "  (source_id IS NULL AND group_id IS NULL AND tag_filter IS NOT NULL)"
        "))"
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(f"INSERT INTO spaces VALUES ('sp1', 'myspace', '/s.yaml', '', '', '{now}', '{now}')")
    conn.execute(
        f"INSERT INTO sources VALUES ('src1', 'text', 'Source 1', 'content', 'text/plain', "
        f"NULL, NULL, NULL, NULL, NULL, NULL, NULL, '{now}', '{now}')"
    )
    conn.execute(
        f"INSERT INTO sources VALUES ('src2', 'text', 'Source 2', 'content2', 'text/plain', "
        f"NULL, NULL, NULL, NULL, NULL, NULL, NULL, '{now}', '{now}')"
    )
    conn.commit()
    return ThreadSafeConnection(conn)


def test_link_source_to_space_happy_path() -> None:
    from anteroom.services.storage import link_source_to_space

    db = _make_db()
    result = link_source_to_space(db, "sp1", source_id="src1")
    assert result["space_id"] == "sp1"
    assert result["source_id"] == "src1"


def test_link_source_to_space_duplicate_ignored() -> None:
    from anteroom.services.storage import link_source_to_space

    db = _make_db()
    link_source_to_space(db, "sp1", source_id="src1")
    # Should not raise - INSERT OR IGNORE
    link_source_to_space(db, "sp1", source_id="src1")


def test_link_source_to_space_requires_exactly_one() -> None:
    from anteroom.services.storage import link_source_to_space

    db = _make_db()
    with pytest.raises(ValueError, match="Exactly one"):
        link_source_to_space(db, "sp1")  # none provided
    with pytest.raises(ValueError, match="Exactly one"):
        link_source_to_space(db, "sp1", source_id="src1", group_id="g1")


def test_unlink_source_from_space() -> None:
    from anteroom.services.storage import link_source_to_space, unlink_source_from_space

    db = _make_db()
    link_source_to_space(db, "sp1", source_id="src1")
    assert unlink_source_from_space(db, "sp1", source_id="src1") is True


def test_unlink_source_from_space_no_args() -> None:
    from anteroom.services.storage import unlink_source_from_space

    db = _make_db()
    assert unlink_source_from_space(db, "sp1") is False


def test_get_space_sources_resolves_direct_links() -> None:
    from anteroom.services.storage import get_space_sources, link_source_to_space

    db = _make_db()
    link_source_to_space(db, "sp1", source_id="src1")
    link_source_to_space(db, "sp1", source_id="src2")

    sources = get_space_sources(db, "sp1")
    ids = {s["id"] for s in sources}
    assert ids == {"src1", "src2"}


def test_get_space_sources_empty() -> None:
    from anteroom.services.storage import get_space_sources

    db = _make_db()
    sources = get_space_sources(db, "sp1")
    assert sources == []


def test_get_space_sources_resolves_group() -> None:
    from anteroom.services.storage import get_space_sources, link_source_to_space

    db = _make_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute(f"INSERT INTO source_groups VALUES ('g1', 'my-group', '', NULL, NULL, '{now}', '{now}')")
    db.execute("INSERT INTO source_group_members VALUES ('g1', 'src1')")
    db.execute("INSERT INTO source_group_members VALUES ('g1', 'src2')")
    db.commit()

    link_source_to_space(db, "sp1", group_id="g1")
    sources = get_space_sources(db, "sp1")
    assert len(sources) == 2


def test_get_space_sources_resolves_tag_filter() -> None:
    from anteroom.services.storage import get_space_sources, link_source_to_space

    db = _make_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute(f"INSERT INTO tags VALUES ('t1', 'important', '#ff0000', NULL, NULL, '{now}')")
    db.execute("INSERT INTO source_tags VALUES ('src1', 't1')")
    db.commit()

    link_source_to_space(db, "sp1", tag_filter="important")
    sources = get_space_sources(db, "sp1")
    assert len(sources) == 1
    assert sources[0]["id"] == "src1"


def test_cascade_delete_space_removes_links() -> None:
    from anteroom.services.storage import link_source_to_space

    db = _make_db()
    link_source_to_space(db, "sp1", source_id="src1")
    db.execute("DELETE FROM spaces WHERE id = 'sp1'")
    db.commit()

    rows = db.execute("SELECT * FROM space_sources WHERE space_id = 'sp1'").fetchall()
    assert len(rows) == 0

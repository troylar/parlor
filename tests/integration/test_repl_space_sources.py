"""Integration tests for CLI /space link-source and /space unlink-source disambiguation.

Tests the three-tier matching logic (ID -> exact title -> partial title) by
exercising the same code path as the REPL, including the disambiguation output
that the user sees when multiple sources share the same title.

The REPL's link-source/unlink-source flow is tested here by:
1. Calling the same service functions the REPL calls (list_sources, link_source_to_space)
2. Running the same matching algorithm inline
3. Capturing Rich console output to verify disambiguation messages
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from io import StringIO

from rich.console import Console

from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services.storage import (
    create_source,
    get_direct_space_source_links,
    link_source_to_space,
    list_sources,
)


def _make_db() -> ThreadSafeConnection:
    """Create an in-memory DB with full schema."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return ThreadSafeConnection(conn)


def _seed_space(db: ThreadSafeConnection, space_id: str = "sp1", name: str = "myspace") -> dict:
    """Create a space and return its row."""
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO spaces (id, name, source_file, source_hash, last_loaded_at, created_at, updated_at) "
        "VALUES (?, ?, ?, '', '', ?, ?)",
        (space_id, name, "/s.yaml", now, now),
    )
    db.commit()
    return {"id": space_id, "name": name}


def _run_link_source_match(all_srcs: list[dict], query: str, console: Console) -> dict | None:
    """Reproduce the REPL's three-tier matching logic for /space link-source.

    This is the exact algorithm from repl.py lines 3567-3601, extracted
    so we can test it with a captured console.
    """
    match = None
    # Tier 1: Exact match by ID (unique, no ambiguity)
    for s in all_srcs:
        if s["id"] == query:
            match = s
            break
    # Tier 2: Exact title match with disambiguation
    if not match:
        exact = [s for s in all_srcs if s.get("title", "").lower() == query.lower()]
        if len(exact) == 1:
            match = exact[0]
        elif len(exact) > 1:
            console.print(f"Multiple sources named '{query}':")
            for c in exact[:10]:
                _ct = c.get("title", "Untitled")
                _ci = str(c["id"])[:8]
                console.print(f"  {_ct} {_ci}...")
            console.print("Use the source ID to disambiguate.")
            return None  # signals "continue" in the REPL
    # Tier 3: Partial title match with disambiguation
    if not match:
        candidates = [s for s in all_srcs if query.lower() in s.get("title", "").lower()]
        if len(candidates) == 1:
            match = candidates[0]
        elif len(candidates) > 1:
            console.print(f"Multiple sources match '{query}':")
            for c in candidates[:10]:
                _ct = c.get("title", "Untitled")
                _ci = str(c["id"])[:8]
                console.print(f"  {_ct} {_ci}...")
            console.print("Be more specific or use the source ID.")
            return None  # signals "continue" in the REPL
    if not match:
        console.print(f"Source '{query}' not found.")
        return None
    return match


class TestDisambiguationOutput:
    """Verify the REPL's disambiguation messages for duplicate titles."""

    def test_duplicate_exact_title_shows_disambiguation(self) -> None:
        """When two sources share the same title, the REPL prints candidates + IDs."""
        db = _make_db()
        _seed_space(db)
        s1 = create_source(db, source_type="text", title="Quarterly Report", content="v1")
        s2 = create_source(db, source_type="text", title="Quarterly Report", content="v2")

        all_srcs = list_sources(db, limit=0)
        buf = StringIO()
        console = Console(file=buf, force_terminal=False, width=120)

        result = _run_link_source_match(all_srcs, "quarterly report", console)

        assert result is None, "Should return None (disambiguation needed)"
        output = buf.getvalue()
        assert "Multiple sources named" in output
        assert s1["id"][:8] in output
        assert s2["id"][:8] in output
        assert "Use the source ID to disambiguate" in output

    def test_duplicate_exact_title_resolved_by_id(self) -> None:
        """After disambiguation, using the source ID resolves unambiguously."""
        db = _make_db()
        _seed_space(db)
        s1 = create_source(db, source_type="text", title="Quarterly Report", content="v1")
        create_source(db, source_type="text", title="Quarterly Report", content="v2")

        all_srcs = list_sources(db, limit=0)
        buf = StringIO()
        console = Console(file=buf, force_terminal=False, width=120)

        result = _run_link_source_match(all_srcs, s1["id"], console)

        assert result is not None
        assert result["id"] == s1["id"]
        assert result["content"] == "v1"
        assert buf.getvalue() == "", "No disambiguation output when ID match is exact"

    def test_partial_match_multiple_shows_candidates(self) -> None:
        """When partial title matches multiple sources, show disambiguation."""
        db = _make_db()
        _seed_space(db)
        s1 = create_source(db, source_type="text", title="Q1 Report", content="c1")
        s2 = create_source(db, source_type="text", title="Q2 Report", content="c2")
        create_source(db, source_type="text", title="Budget Plan", content="c3")

        all_srcs = list_sources(db, limit=0)
        buf = StringIO()
        console = Console(file=buf, force_terminal=False, width=120)

        result = _run_link_source_match(all_srcs, "report", console)

        assert result is None
        output = buf.getvalue()
        assert "Multiple sources match" in output
        assert s1["id"][:8] in output
        assert s2["id"][:8] in output
        assert "Be more specific or use the source ID" in output

    def test_single_exact_title_no_disambiguation(self) -> None:
        """When only one source matches by exact title, no disambiguation needed."""
        db = _make_db()
        _seed_space(db)
        create_source(db, source_type="text", title="Unique Title", content="c1")
        create_source(db, source_type="text", title="Other Title", content="c2")

        all_srcs = list_sources(db, limit=0)
        buf = StringIO()
        console = Console(file=buf, force_terminal=False, width=120)

        result = _run_link_source_match(all_srcs, "unique title", console)

        assert result is not None
        assert result["title"] == "Unique Title"
        assert buf.getvalue() == ""

    def test_single_partial_match_no_disambiguation(self) -> None:
        """When only one source matches by partial title, no disambiguation needed."""
        db = _make_db()
        _seed_space(db)
        create_source(db, source_type="text", title="Architecture Overview", content="c1")
        create_source(db, source_type="text", title="Budget Summary", content="c2")

        all_srcs = list_sources(db, limit=0)
        buf = StringIO()
        console = Console(file=buf, force_terminal=False, width=120)

        result = _run_link_source_match(all_srcs, "archit", console)

        assert result is not None
        assert result["title"] == "Architecture Overview"
        assert buf.getvalue() == ""

    def test_no_match_shows_not_found(self) -> None:
        """When nothing matches, show 'not found' error."""
        db = _make_db()
        _seed_space(db)
        create_source(db, source_type="text", title="Alpha", content="c1")

        all_srcs = list_sources(db, limit=0)
        buf = StringIO()
        console = Console(file=buf, force_terminal=False, width=120)

        result = _run_link_source_match(all_srcs, "nonexistent", console)

        assert result is None
        assert "not found" in buf.getvalue()

    def test_disambiguation_then_link_full_flow(self) -> None:
        """Full flow: disambiguation, then ID-based link, then verify linked."""
        db = _make_db()
        sp = _seed_space(db)
        s1 = create_source(db, source_type="text", title="Status Report", content="v1")
        create_source(db, source_type="text", title="Status Report", content="v2")

        all_srcs = list_sources(db, limit=0)
        buf = StringIO()
        console = Console(file=buf, force_terminal=False, width=120)

        # First attempt by title — hits disambiguation
        result = _run_link_source_match(all_srcs, "status report", console)
        assert result is None
        assert "Multiple sources named" in buf.getvalue()

        # Second attempt by ID — resolves
        buf2 = StringIO()
        console2 = Console(file=buf2, force_terminal=False, width=120)
        result = _run_link_source_match(all_srcs, s1["id"], console2)
        assert result is not None
        assert result["id"] == s1["id"]

        # Link and verify
        link_source_to_space(db, sp["id"], source_id=result["id"])
        linked = get_direct_space_source_links(db, sp["id"])
        assert len(linked) == 1
        assert linked[0]["id"] == s1["id"]

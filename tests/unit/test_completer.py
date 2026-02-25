"""Tests for AnteroomCompleter slug autocomplete."""

from __future__ import annotations

import sqlite3

import pytest

from anteroom.db import _FTS_SCHEMA, _FTS_TRIGGERS, _SCHEMA, ThreadSafeConnection
from anteroom.services.storage import (
    create_conversation,
    list_conversation_slugs,
    update_conversation_slug,
)


@pytest.fixture()
def db() -> ThreadSafeConnection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    try:
        conn.executescript(_FTS_SCHEMA)
        conn.executescript(_FTS_TRIGGERS)
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return ThreadSafeConnection(conn)


class TestListConversationSlugs:
    def test_empty_db_returns_empty(self, db: ThreadSafeConnection) -> None:
        assert list_conversation_slugs(db) == []

    def test_returns_slug_title_pairs(self, db: ThreadSafeConnection) -> None:
        create_conversation(db, title="Auth refactor")
        create_conversation(db, title="Bug investigation")
        results = list_conversation_slugs(db)
        assert len(results) == 2
        for slug, title in results:
            assert isinstance(slug, str)
            assert "-" in slug
            assert isinstance(title, str)

    def test_respects_limit(self, db: ThreadSafeConnection) -> None:
        for i in range(5):
            create_conversation(db, title=f"Conv {i}")
        results = list_conversation_slugs(db, limit=3)
        assert len(results) == 3

    def test_ordered_by_most_recent(self, db: ThreadSafeConnection) -> None:
        c1 = create_conversation(db, title="First")
        create_conversation(db, title="Second")
        # Update c1 to make it most recent
        db.execute("UPDATE conversations SET updated_at = '2099-01-01' WHERE id = ?", (c1["id"],))
        db.commit()
        results = list_conversation_slugs(db)
        assert results[0][1] == "First"

    def test_skips_null_slugs(self, db: ThreadSafeConnection) -> None:
        conv = create_conversation(db, title="Has slug")
        # Remove the slug from a conversation to simulate a legacy row
        db.execute("UPDATE conversations SET slug = NULL WHERE id = ?", (conv["id"],))
        c2 = create_conversation(db, title="Also has slug")
        db.commit()
        results = list_conversation_slugs(db)
        slugs = [s for s, _ in results]
        assert c2["slug"] in slugs
        assert len(results) == 1

    def test_empty_title_returns_empty_string(self, db: ThreadSafeConnection) -> None:
        create_conversation(db, title="")
        results = list_conversation_slugs(db)
        assert len(results) == 1
        assert results[0][1] == ""


class TestAnteroomCompleterSlugs:
    """Test slug completion in the completer using a mock document."""

    def _make_completer(self, db: ThreadSafeConnection) -> object:
        """Create a minimal AnteroomCompleter-like object for testing."""
        # Import here to avoid importing the full REPL module
        from prompt_toolkit.completion import Completion

        from anteroom.services import storage

        class TestCompleter:
            _SLUG_COMMANDS = {"resume", "delete", "rename"}

            def __init__(self, db: ThreadSafeConnection) -> None:
                self._db = db

            def _get_slug_completions(self, partial: str) -> list[Completion]:
                try:
                    slugs = storage.list_conversation_slugs(self._db, limit=50)
                except Exception:
                    return []
                results = []
                for slug, title in slugs:
                    if slug.startswith(partial):
                        display = title[:50] if title else ""
                        results.append(Completion(slug, start_position=-len(partial), display_meta=display))
                return results

            def complete_for_text(self, text: str) -> list[Completion]:
                """Simulate get_completions logic for slug commands."""
                if text.lstrip().startswith("/"):
                    parts = text.lstrip().split(None, 2)
                    cmd_name = parts[0].lstrip("/") if parts else ""
                    if cmd_name in self._SLUG_COMMANDS and len(parts) <= 2:
                        partial = parts[1] if len(parts) == 2 else ""
                        return self._get_slug_completions(partial)
                return []

        return TestCompleter(db)

    def test_resume_tab_shows_all_slugs(self, db: ThreadSafeConnection) -> None:
        create_conversation(db, title="Auth work")
        create_conversation(db, title="Bug fix")
        completer = self._make_completer(db)
        results = completer.complete_for_text("/resume ")
        assert len(results) == 2

    def test_resume_partial_filters(self, db: ThreadSafeConnection) -> None:
        c1 = create_conversation(db, title="Alpha")
        c2 = create_conversation(db, title="Beta")
        update_conversation_slug(db, c1["id"], "bold-amber-eagle")
        update_conversation_slug(db, c2["id"], "calm-blue-fox")
        completer = self._make_completer(db)
        results = completer.complete_for_text("/resume bol")
        assert len(results) == 1
        assert results[0].text == "bold-amber-eagle"

    def test_delete_tab_shows_slugs(self, db: ThreadSafeConnection) -> None:
        create_conversation(db, title="To delete")
        completer = self._make_completer(db)
        results = completer.complete_for_text("/delete ")
        assert len(results) == 1

    def test_rename_tab_shows_slugs(self, db: ThreadSafeConnection) -> None:
        create_conversation(db, title="To rename")
        completer = self._make_completer(db)
        results = completer.complete_for_text("/rename ")
        assert len(results) == 1

    def test_help_tab_no_slugs(self, db: ThreadSafeConnection) -> None:
        create_conversation(db, title="Irrelevant")
        completer = self._make_completer(db)
        results = completer.complete_for_text("/help ")
        assert len(results) == 0

    def test_completion_has_display_meta(self, db: ThreadSafeConnection) -> None:
        create_conversation(db, title="Auth refactor discussion")
        completer = self._make_completer(db)
        results = completer.complete_for_text("/resume ")
        assert len(results) == 1
        assert results[0].display_meta is not None

    def test_empty_db_no_completions(self, db: ThreadSafeConnection) -> None:
        completer = self._make_completer(db)
        results = completer.complete_for_text("/resume ")
        assert len(results) == 0

    def test_no_completions_for_second_arg_of_rename(self, db: ThreadSafeConnection) -> None:
        c = create_conversation(db, title="Test")
        update_conversation_slug(db, c["id"], "my-slug")
        completer = self._make_completer(db)
        # /rename my-slug New Title — 3 parts, should not complete
        results = completer.complete_for_text("/rename my-slug New")
        assert len(results) == 0

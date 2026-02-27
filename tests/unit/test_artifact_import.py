"""Tests for services/artifact_import.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services.artifact_import import (
    _heading_to_name,
    _split_markdown_sections,
    import_all,
    import_instructions,
    import_skills,
)


@pytest.fixture()
def db() -> ThreadSafeConnection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return ThreadSafeConnection(conn)


class TestSplitMarkdownSections:
    def test_single_section(self) -> None:
        text = "## Hello\nWorld\n"
        result = _split_markdown_sections(text)
        assert len(result) == 1
        assert result[0] == ("Hello", "World")

    def test_multiple_sections(self) -> None:
        text = "## First\nAAA\n## Second\nBBB\n"
        result = _split_markdown_sections(text)
        assert len(result) == 2
        assert result[0][0] == "First"
        assert result[1][0] == "Second"

    def test_ignores_preamble(self) -> None:
        text = "Some preamble text\n\n## Actual\nContent\n"
        result = _split_markdown_sections(text)
        assert len(result) == 1
        assert result[0][0] == "Actual"

    def test_empty_text(self) -> None:
        assert _split_markdown_sections("") == []

    def test_no_sections(self) -> None:
        assert _split_markdown_sections("Just text\nNo headings\n") == []

    def test_preserves_multiline_content(self) -> None:
        text = "## Section\nLine 1\nLine 2\nLine 3\n"
        result = _split_markdown_sections(text)
        assert "Line 1\nLine 2\nLine 3" in result[0][1]


class TestHeadingToName:
    def test_simple(self) -> None:
        assert _heading_to_name("Hello World") == "hello-world"

    def test_special_chars(self) -> None:
        assert _heading_to_name("My Rule (v2.0)!") == "my-rule-v2-0"

    def test_strips_edges(self) -> None:
        assert _heading_to_name("  --hello--  ") == "hello"

    def test_empty_returns_empty(self) -> None:
        assert _heading_to_name("") == ""

    def test_too_long_returns_empty(self) -> None:
        assert _heading_to_name("a" * 65) == ""


class TestImportSkills:
    def test_imports_yaml_files(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        (tmp_path / "greet.yaml").write_text("name: greet\ncontent: Hello!\n")
        result = import_skills(db, tmp_path)
        assert result.imported == 1
        assert result.errors == 0
        row = db.execute("SELECT * FROM artifacts WHERE name = 'greet'").fetchone()
        assert row is not None
        assert row["source"] == "local"

    def test_skips_non_mapping(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        (tmp_path / "bad.yaml").write_text("- just a list\n")
        result = import_skills(db, tmp_path)
        assert result.skipped == 1
        assert result.imported == 0

    def test_missing_dir(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        result = import_skills(db, tmp_path / "nope")
        assert result.imported == 0
        assert "not found" in result.details[0].lower()

    def test_uses_prompt_fallback(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        (tmp_path / "helper.yaml").write_text("name: helper\nprompt: Help me!\n")
        result = import_skills(db, tmp_path)
        assert result.imported == 1
        row = db.execute("SELECT content FROM artifacts WHERE name = 'helper'").fetchone()
        assert row["content"] == "Help me!"

    def test_idempotent(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        (tmp_path / "greet.yaml").write_text("name: greet\ncontent: Hello!\n")
        import_skills(db, tmp_path)
        result = import_skills(db, tmp_path)
        assert result.imported == 1  # upsert succeeds


class TestImportInstructions:
    def test_imports_sections(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        md = tmp_path / "ANTEROOM.md"
        md.write_text("## Code Style\nUse type hints.\n\n## Testing\nAlways test.\n")
        result = import_instructions(db, md)
        assert result.imported == 2
        row = db.execute("SELECT * FROM artifacts WHERE name = 'code-style'").fetchone()
        assert row is not None
        assert "type hints" in row["content"]

    def test_missing_file(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        result = import_instructions(db, tmp_path / "nope.md")
        assert result.imported == 0
        assert "not found" in result.details[0].lower()

    def test_no_sections(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        md = tmp_path / "ANTEROOM.md"
        md.write_text("Just plain text without headings.\n")
        result = import_instructions(db, md)
        assert result.imported == 0
        assert "no sections" in result.details[0].lower()


class TestImportAll:
    def test_imports_skills_and_instructions(
        self,
        db: ThreadSafeConnection,
        tmp_path: Path,
    ) -> None:
        skills = tmp_path / "skills"
        skills.mkdir()
        (skills / "greet.yaml").write_text("name: greet\ncontent: Hi\n")

        proj = tmp_path / "project"
        proj.mkdir()
        anteroom_md = proj / ".anteroom.md"
        anteroom_md.write_text("## Section\nContent here.\n")

        results = import_all(db, tmp_path, project_dir=proj)
        assert "skills" in results
        assert results["skills"].imported == 1

    def test_empty_data_dir(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        results = import_all(db, tmp_path)
        assert results == {}

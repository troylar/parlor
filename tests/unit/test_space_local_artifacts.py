"""Tests for space-local artifact discovery and loading."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

from anteroom.services.local_artifacts import discover_local_artifacts, load_local_artifacts
from anteroom.services.space_storage import get_space_local_dirs

# --- discover with space dirs ---


def test_discover_local_artifacts_from_space_dir(tmp_path: Path) -> None:
    """Artifacts under <space_dir>/.anteroom/local/rules/ are discovered."""
    local_dir = tmp_path / ".anteroom" / "local"
    rules_dir = local_dir / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "no-secrets.md").write_text("# No secrets\nDon't commit secrets.")

    found = discover_local_artifacts(local_dir)
    assert len(found) == 1
    assert found[0]["name"] == "no-secrets"
    assert found[0]["type"] == "rule"


def test_load_local_artifacts_with_space_dirs(tmp_path: Path) -> None:
    """load_local_artifacts discovers artifacts from space_dirs."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    space_repo = tmp_path / "repo1"
    rules_dir = space_repo / ".anteroom" / "local" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "space-rule.md").write_text("# Space rule content")

    with patch("anteroom.services.local_artifacts.upsert_artifact") as mock_upsert:
        count = load_local_artifacts(MagicMock(), data_dir, space_dirs=[space_repo])

    assert count == 1
    mock_upsert.assert_called_once()
    call_kwargs = mock_upsert.call_args
    assert "space-rule" in call_kwargs.kwargs.get("name", call_kwargs[1].get("name", ""))


def test_load_local_artifacts_empty_space_dirs_noop(tmp_path: Path) -> None:
    """Empty space_dirs list does not add artifacts."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    with patch("anteroom.services.local_artifacts.upsert_artifact") as mock_upsert:
        count = load_local_artifacts(MagicMock(), data_dir, space_dirs=[])

    assert count == 0
    mock_upsert.assert_not_called()


def test_load_local_artifacts_nonexistent_space_dir_skipped(tmp_path: Path) -> None:
    """Nonexistent space dir is silently skipped."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    nonexistent = tmp_path / "does-not-exist"

    with patch("anteroom.services.local_artifacts.upsert_artifact") as mock_upsert:
        count = load_local_artifacts(MagicMock(), data_dir, space_dirs=[nonexistent])

    assert count == 0
    mock_upsert.assert_not_called()


# --- get_space_local_dirs ---


def test_get_space_local_dirs_returns_paths() -> None:
    """get_space_local_dirs returns local_path values for a space."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute(
        "CREATE TABLE spaces (id TEXT PRIMARY KEY, name TEXT, file_path TEXT, "
        "file_hash TEXT DEFAULT '', last_loaded_at TEXT, created_at TEXT, updated_at TEXT)"
    )
    db.execute(
        "CREATE TABLE space_paths (id TEXT PRIMARY KEY, space_id TEXT, "
        "repo_url TEXT DEFAULT '', local_path TEXT, created_at TEXT, "
        "FOREIGN KEY (space_id) REFERENCES spaces(id))"
    )
    db.execute("INSERT INTO spaces VALUES ('s1', 'test', '/p', '', '', '', '')")
    db.execute("INSERT INTO space_paths VALUES ('p1', 's1', 'https://example.com/repo', '/home/user/repo', '')")
    db.execute("INSERT INTO space_paths VALUES ('p2', 's1', '', '/home/user/local', '')")
    db.execute("INSERT INTO space_paths VALUES ('p3', 's1', '', '', '')")
    db.commit()

    dirs = get_space_local_dirs(db, "s1")
    assert len(dirs) == 2
    assert "/home/user/repo" in dirs
    assert "/home/user/local" in dirs


def test_get_space_local_dirs_empty_for_no_paths() -> None:
    """Returns empty list when space has no paths."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE space_paths (id TEXT PRIMARY KEY, space_id TEXT, "
        "repo_url TEXT DEFAULT '', local_path TEXT, created_at TEXT)"
    )
    db.commit()

    dirs = get_space_local_dirs(db, "nonexistent")
    assert dirs == []

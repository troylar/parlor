"""Tests for the `aroom artifact` CLI subcommand."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services.artifact_storage import create_artifact


@pytest.fixture()
def db() -> ThreadSafeConnection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return ThreadSafeConnection(conn)


def _make_config() -> MagicMock:
    config = MagicMock()
    config.app.data_dir.__truediv__.return_value = "/tmp/test.db"
    return config


class TestRunArtifactList:
    def test_list_empty(self, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]) -> None:
        config = _make_config()
        args = MagicMock()
        args.artifact_action = "list"
        args.type = None
        args.namespace = None
        args.source = None

        from anteroom.__main__ import _run_artifact

        with patch("anteroom.db.get_db", return_value=db):
            _run_artifact(config, args)

        captured = capsys.readouterr()
        assert "No artifacts found" in captured.out

    def test_list_shows_artifacts(self, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]) -> None:
        create_artifact(db, "@core/skill/greet", "skill", "core", "greet", "hello")

        config = _make_config()
        args = MagicMock()
        args.artifact_action = "list"
        args.type = None
        args.namespace = None
        args.source = None

        from anteroom.__main__ import _run_artifact

        with patch("anteroom.db.get_db", return_value=db):
            _run_artifact(config, args)

        captured = capsys.readouterr()
        assert "@core/skill/greet" in captured.out


class TestRunArtifactShow:
    def test_show_existing(self, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]) -> None:
        create_artifact(db, "@core/skill/greet", "skill", "core", "greet", "Say hello")

        config = _make_config()
        args = MagicMock()
        args.artifact_action = "show"
        args.fqn = "@core/skill/greet"

        from anteroom.__main__ import _run_artifact

        with patch("anteroom.db.get_db", return_value=db):
            _run_artifact(config, args)

        captured = capsys.readouterr()
        assert "@core/skill/greet" in captured.out
        assert "Say hello" in captured.out

    def test_show_not_found(self, db: ThreadSafeConnection) -> None:
        config = _make_config()
        args = MagicMock()
        args.artifact_action = "show"
        args.fqn = "@no/such/thing"

        from anteroom.__main__ import _run_artifact

        with patch("anteroom.db.get_db", return_value=db), pytest.raises(SystemExit):
            _run_artifact(config, args)

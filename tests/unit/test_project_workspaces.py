"""Tests for project workspace features (#391): get_project_by_name, --project flag, project loading."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anteroom.db import _SCHEMA, ThreadSafeConnection, _create_indexes
from anteroom.services.storage import (
    create_conversation,
    create_project,
    get_conversation,
    get_project_by_name,
)


@pytest.fixture()
def db() -> ThreadSafeConnection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    _create_indexes(conn)
    conn.commit()
    return ThreadSafeConnection(conn)


class TestGetProjectByName:
    def test_returns_project_when_found(self, db: ThreadSafeConnection) -> None:
        proj = create_project(db, "my-project")
        result = get_project_by_name(db, "my-project")
        assert result is not None
        assert result["id"] == proj["id"]
        assert result["name"] == "my-project"

    def test_returns_none_when_not_found(self, db: ThreadSafeConnection) -> None:
        result = get_project_by_name(db, "nonexistent")
        assert result is None

    def test_name_is_case_insensitive(self, db: ThreadSafeConnection) -> None:
        create_project(db, "MyProject")
        assert get_project_by_name(db, "MyProject") is not None
        # Uses LOWER() for case-insensitive matching
        assert get_project_by_name(db, "myproject") is not None

    def test_unique_name_constraint(self, db: ThreadSafeConnection) -> None:
        create_project(db, "unique-name")
        with pytest.raises(sqlite3.IntegrityError):
            create_project(db, "unique-name")


class TestProjectConversationLink:
    def test_create_conversation_with_project_id(self, db: ThreadSafeConnection) -> None:
        proj = create_project(db, "test-proj")
        conv = create_conversation(db, title="Test", project_id=proj["id"])
        assert conv["project_id"] == proj["id"]

    def test_create_conversation_without_project_id(self, db: ThreadSafeConnection) -> None:
        conv = create_conversation(db, title="Test")
        assert conv.get("project_id") is None

    def test_get_conversation_includes_project_id(self, db: ThreadSafeConnection) -> None:
        proj = create_project(db, "test-proj")
        conv = create_conversation(db, title="Test", project_id=proj["id"])
        fetched = get_conversation(db, conv["id"])
        assert fetched is not None
        assert fetched["project_id"] == proj["id"]


class TestResolveProjectId:
    """Tests for _resolve_project_id in __main__.py."""

    @patch("anteroom.services.storage.get_project_by_name")
    @patch("anteroom.db.get_db")
    def test_resolve_returns_project_id(self, mock_get_db: MagicMock, mock_get_by_name: MagicMock) -> None:
        from anteroom.__main__ import _resolve_project_id

        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        mock_get_by_name.return_value = {"id": "proj-123", "name": "my-proj"}

        mock_config = MagicMock()
        mock_config.app.data_dir = Path("/tmp/test")
        result = _resolve_project_id(mock_config, "my-proj")
        assert result == "proj-123"
        mock_get_by_name.assert_called_once_with(mock_db, "my-proj")

    @patch("anteroom.services.storage.get_project_by_name")
    @patch("anteroom.db.get_db")
    def test_resolve_exits_when_not_found(self, mock_get_db: MagicMock, mock_get_by_name: MagicMock) -> None:
        from anteroom.__main__ import _resolve_project_id

        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        mock_get_by_name.return_value = None

        mock_config = MagicMock()
        mock_config.app.data_dir = Path("/tmp/test")
        with pytest.raises(SystemExit):
            _resolve_project_id(mock_config, "nonexistent")


class TestRunProjects:
    """Tests for _run_projects in __main__.py."""

    @patch("anteroom.services.storage.list_projects")
    @patch("anteroom.db.get_db")
    def test_run_projects_no_projects(
        self, mock_get_db: MagicMock, mock_list_projects: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from anteroom.__main__ import _run_projects

        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        mock_list_projects.return_value = []

        mock_config = MagicMock()
        mock_config.app.data_dir = Path("/tmp/test")
        _run_projects(mock_config)
        captured = capsys.readouterr()
        assert "no projects" in captured.out.lower()

    @patch("anteroom.services.storage.list_projects")
    @patch("anteroom.db.get_db")
    def test_run_projects_with_projects(
        self, mock_get_db: MagicMock, mock_list_projects: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from anteroom.__main__ import _run_projects

        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        mock_list_projects.return_value = [
            {"id": "p1", "name": "proj-a", "model": "gpt-4", "instructions": "Do stuff", "updated_at": "2026-01-01"},
            {"id": "p2", "name": "proj-b", "model": None, "instructions": None, "updated_at": "2026-01-02"},
        ]

        mock_config = MagicMock()
        mock_config.app.data_dir = Path("/tmp/test")
        _run_projects(mock_config)
        captured = capsys.readouterr()
        assert "proj-a" in captured.out
        assert "proj-b" in captured.out


class TestExecModeProjectLoading:
    """Tests for project loading in exec_mode.py."""

    def test_exec_mode_accepts_project_id(self) -> None:
        import inspect

        from anteroom.cli.exec_mode import run_exec_mode

        sig = inspect.signature(run_exec_mode)
        assert "project_id" in sig.parameters

    def test_exec_mode_build_system_prompt_accepts_project_instructions(self) -> None:
        import inspect

        from anteroom.cli.exec_mode import _build_system_prompt

        sig = inspect.signature(_build_system_prompt)
        assert "project_instructions" in sig.parameters

    def test_exec_mode_build_system_prompt_includes_project_instructions(self) -> None:
        from anteroom.cli.exec_mode import _build_system_prompt
        from anteroom.config import AIConfig, AppConfig

        config = AppConfig(ai=AIConfig(base_url="https://api.test", api_key="sk-test", model="test-model"))
        result = _build_system_prompt(
            config,
            "/tmp/test",
            instructions=None,
            project_instructions="You are working on project X.",
        )
        assert "You are working on project X." in result


class TestReplProjectLoading:
    """Tests for project loading in repl.py."""

    def test_run_cli_accepts_project_id(self) -> None:
        import inspect

        from anteroom.cli.repl import run_cli

        sig = inspect.signature(run_cli)
        assert "project_id" in sig.parameters

    def test_build_system_prompt_accepts_project_instructions(self) -> None:
        import inspect

        from anteroom.cli.repl import _build_system_prompt

        sig = inspect.signature(_build_system_prompt)
        assert "project_instructions" in sig.parameters

    def test_build_system_prompt_includes_project_instructions(self, tmp_path: Path) -> None:
        from anteroom.cli.repl import _build_system_prompt
        from anteroom.config import AIConfig, AppConfig

        config = AppConfig(ai=AIConfig(base_url="https://api.test", api_key="sk-test", model="test-model"))
        result = _build_system_prompt(
            config,
            str(tmp_path),
            instructions=None,
            project_instructions="You are working on project X.",
        )
        assert "You are working on project X." in result

    def test_build_system_prompt_without_project_instructions(self, tmp_path: Path) -> None:
        from anteroom.cli.repl import _build_system_prompt
        from anteroom.config import AIConfig, AppConfig

        config = AppConfig(ai=AIConfig(base_url="https://api.test", api_key="sk-test", model="test-model"))
        result = _build_system_prompt(
            config,
            str(tmp_path),
            instructions=None,
            project_instructions=None,
        )
        assert "test-model" in result

    def test_project_instructions_before_anteroom_instructions(self, tmp_path: Path) -> None:
        from anteroom.cli.repl import _build_system_prompt
        from anteroom.config import AIConfig, AppConfig

        config = AppConfig(ai=AIConfig(base_url="https://api.test", api_key="sk-test", model="test-model"))
        result = _build_system_prompt(
            config,
            str(tmp_path),
            instructions="ANTEROOM instructions here",
            project_instructions="Project instructions here",
        )
        proj_pos = result.index("Project instructions here")
        inst_pos = result.index("ANTEROOM instructions here")
        assert proj_pos < inst_pos, "Project instructions should come before ANTEROOM.md instructions"

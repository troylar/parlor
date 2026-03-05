"""Tests for __main__.py: argparse dispatch and subcommand functions."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anteroom.db import _SCHEMA, ThreadSafeConnection

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_config(port: int = 8080, host: str = "127.0.0.1") -> MagicMock:
    config = MagicMock()
    config.app.port = port
    config.app.host = host
    config.app.data_dir = Path("/tmp/anteroom-test")
    config.app.tls = False
    config.ai.base_url = "http://localhost:1234/v1"
    config.ai.model = "test-model"
    config.ai.verify_ssl = True
    config.ai.temperature = None
    config.ai.top_p = None
    config.ai.seed = None
    config.mcp_servers = []
    config.safety.approval_mode = "ask_for_writes"
    config.safety.allowed_tools = []
    config.safety.read_only = False
    config.compliance.rules = []
    config.audit.log_path = None
    config.audit.retention_days = 30
    config.identity = MagicMock()
    config.identity.private_key = "fake-private-key"
    config.storage.retention_days = 0
    config.storage.purge_attachments = True
    config.pack_sources = []
    return config


@pytest.fixture()
def db() -> ThreadSafeConnection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return ThreadSafeConnection(conn)


# ---------------------------------------------------------------------------
# _run_init
# ---------------------------------------------------------------------------


class TestRunInit:
    def test_run_init_calls_wizard(self) -> None:
        from anteroom.__main__ import _run_init

        with patch("anteroom.cli.setup.run_init_wizard") as mock_wizard:
            _run_init(force=False, team_config=None)

        mock_wizard.assert_called_once_with(force=False, team_config_path=None)

    def test_run_init_passes_force_flag(self) -> None:
        from anteroom.__main__ import _run_init

        with patch("anteroom.cli.setup.run_init_wizard") as mock_wizard:
            _run_init(force=True, team_config=None)

        mock_wizard.assert_called_once_with(force=True, team_config_path=None)

    def test_run_init_passes_team_config(self) -> None:
        from anteroom.__main__ import _run_init

        with patch("anteroom.cli.setup.run_init_wizard") as mock_wizard:
            _run_init(force=False, team_config="/tmp/team.yaml")

        mock_wizard.assert_called_once_with(force=False, team_config_path="/tmp/team.yaml")

    def test_main_dispatches_init(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._run_init") as mock_init,
            patch("sys.argv", ["aroom", "init"]),
        ):
            main()

        mock_init.assert_called_once_with(force=False, team_config=None)

    def test_main_init_force_flag(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._run_init") as mock_init,
            patch("sys.argv", ["aroom", "init", "--force"]),
        ):
            main()

        mock_init.assert_called_once_with(force=True, team_config=None)

    def test_main_init_team_config_flag(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._run_init") as mock_init,
            patch("sys.argv", ["aroom", "init", "--team-config", "/tmp/team.yaml"]),
        ):
            main()

        mock_init.assert_called_once_with(force=False, team_config="/tmp/team.yaml")


# ---------------------------------------------------------------------------
# _load_config_or_exit
# ---------------------------------------------------------------------------


class TestLoadConfigOrExit:
    def test_exits_when_config_missing_and_wizard_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from anteroom.__main__ import _load_config_or_exit

        nonexistent = tmp_path / "no_config.yaml"

        with (
            patch("anteroom.__main__._get_config_path", return_value=nonexistent),
            patch("anteroom.cli.setup.run_init_wizard", return_value=False),
            pytest.raises(SystemExit) as exc_info,
        ):
            _load_config_or_exit()

        assert exc_info.value.code == 1

    def test_exits_on_value_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _load_config_or_exit

        config_file = tmp_path / "config.yaml"
        config_file.write_text("ai:\n  base_url: http://localhost\n  api_key: test\n")

        with (
            patch("anteroom.__main__._get_config_path", return_value=config_file),
            patch("anteroom.__main__.load_config", side_effect=ValueError("Bad config")),
            pytest.raises(SystemExit) as exc_info,
        ):
            _load_config_or_exit()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Configuration error" in captured.err

    def test_exits_on_compliance_failure(self, tmp_path: Path) -> None:
        from anteroom.__main__ import _load_config_or_exit

        config_file = tmp_path / "config.yaml"
        config_file.write_text("ai:\n  base_url: http://localhost\n  api_key: test\n")

        mock_config = _make_config()
        mock_result = MagicMock()
        mock_result.is_compliant = False
        mock_result.format_report.return_value = "Rules violated"

        with (
            patch("anteroom.__main__._get_config_path", return_value=config_file),
            patch("anteroom.__main__.load_config", return_value=(mock_config, [])),
            patch("anteroom.services.compliance.validate_compliance", return_value=mock_result),
            pytest.raises(SystemExit) as exc_info,
        ):
            _load_config_or_exit()

        assert exc_info.value.code == 1

    def test_returns_config_on_success(self, tmp_path: Path) -> None:
        from anteroom.__main__ import _load_config_or_exit

        config_file = tmp_path / "config.yaml"
        config_file.write_text("ai:\n  base_url: http://localhost\n  api_key: test\n")

        mock_config = _make_config()
        mock_result = MagicMock()
        mock_result.is_compliant = True

        with (
            patch("anteroom.__main__._get_config_path", return_value=config_file),
            patch("anteroom.__main__.load_config", return_value=(mock_config, ["field1"])),
            patch("anteroom.services.compliance.validate_compliance", return_value=mock_result),
        ):
            path, config, enforced = _load_config_or_exit()

        assert path == config_file
        assert config is mock_config
        assert enforced == ["field1"]


# ---------------------------------------------------------------------------
# _run_config
# ---------------------------------------------------------------------------


class TestRunConfig:
    def test_main_config_validate_dispatches(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._run_config_validate") as mock_validate,
            patch("sys.argv", ["aroom", "config", "validate"]),
        ):
            main()

        mock_validate.assert_called_once()

    def test_main_config_no_subcommand_opens_editor(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.cli.setup.run_config_editor") as mock_editor,
            patch("sys.argv", ["aroom", "config"]),
        ):
            main()

        mock_editor.assert_called_once()

    def test_run_config_validate_exits_0_when_compliant(self, tmp_path: Path) -> None:
        from anteroom.__main__ import _run_config_validate

        mock_config = _make_config()
        mock_config.compliance.rules = []
        mock_result = MagicMock()
        mock_result.is_compliant = True

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(tmp_path / "config.yaml", mock_config, [])),
            patch("anteroom.services.compliance.validate_compliance", return_value=mock_result),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_config_validate()

        assert exc_info.value.code == 0

    def test_run_config_validate_exits_1_when_not_compliant(self, tmp_path: Path) -> None:
        from anteroom.__main__ import _run_config_validate

        mock_config = _make_config()
        mock_config.compliance.rules = [MagicMock()]
        mock_result = MagicMock()
        mock_result.is_compliant = False
        mock_result.format_report.return_value = "Violations found"

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(tmp_path / "config.yaml", mock_config, [])),
            patch("anteroom.services.compliance.validate_compliance", return_value=mock_result),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_config_validate()

        assert exc_info.value.code == 1

    def test_run_config_validate_no_rules_message(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_config_validate

        mock_config = _make_config()
        mock_config.compliance.rules = []
        mock_result = MagicMock()
        mock_result.is_compliant = True

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(tmp_path / "config.yaml", mock_config, [])),
            patch("anteroom.services.compliance.validate_compliance", return_value=mock_result),
            pytest.raises(SystemExit),
        ):
            _run_config_validate()

        captured = capsys.readouterr()
        assert "no rules defined" in captured.out

    def test_run_config_validate_shows_rule_count(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_config_validate

        mock_config = _make_config()
        mock_config.compliance.rules = [MagicMock(), MagicMock()]
        mock_result = MagicMock()
        mock_result.is_compliant = True

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(tmp_path / "config.yaml", mock_config, [])),
            patch("anteroom.services.compliance.validate_compliance", return_value=mock_result),
            pytest.raises(SystemExit),
        ):
            _run_config_validate()

        captured = capsys.readouterr()
        assert "2 rule(s) passed" in captured.out


# ---------------------------------------------------------------------------
# _run_db
# ---------------------------------------------------------------------------


class TestRunDb:
    def test_main_dispatches_db(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._run_db") as mock_run_db,
            patch("sys.argv", ["aroom", "db", "list"]),
        ):
            main()

        mock_run_db.assert_called_once()

    def test_db_list_empty(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        from anteroom.__main__ import _run_db

        config_file = tmp_path / "config.yaml"
        config_file.write_text("{}")

        args = argparse.Namespace(db_action="list", name=None, path=None)

        with patch("anteroom.__main__._get_config_path", return_value=config_file):
            _run_db(args)

        captured = capsys.readouterr()
        assert "No shared databases" in captured.out

    def test_db_list_shows_databases(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        from anteroom.__main__ import _run_db

        config_file = tmp_path / "config.yaml"
        config_file.write_text("databases:\n  mydb:\n    path: /tmp/test.db\n")

        args = argparse.Namespace(db_action="list", name=None, path=None)

        with patch("anteroom.__main__._get_config_path", return_value=config_file):
            _run_db(args)

        captured = capsys.readouterr()
        assert "mydb" in captured.out
        assert "/tmp/test.db" in captured.out

    def test_db_list_shows_passphrase_auth(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        from anteroom.__main__ import _run_db

        config_file = tmp_path / "config.yaml"
        config_file.write_text("databases:\n  mydb:\n    path: /tmp/test.db\n    passphrase_hash: abc123\n")

        args = argparse.Namespace(db_action="list", name=None, path=None)

        with patch("anteroom.__main__._get_config_path", return_value=config_file):
            _run_db(args)

        captured = capsys.readouterr()
        assert "auth: yes" in captured.out

    def test_db_unknown_action_exits(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        from anteroom.__main__ import _run_db

        config_file = tmp_path / "config.yaml"
        config_file.write_text("{}")

        args = argparse.Namespace(db_action="unknown_action", name=None, path=None)

        with (
            patch("anteroom.__main__._get_config_path", return_value=config_file),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_db(args)

        assert exc_info.value.code == 1

    def test_db_connect_missing_name_exits(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        from anteroom.__main__ import _run_db

        config_file = tmp_path / "config.yaml"
        config_file.write_text("{}")

        args = argparse.Namespace(db_action="connect", name=None, path=None)

        with (
            patch("anteroom.__main__._get_config_path", return_value=config_file),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_db(args)

        assert exc_info.value.code == 1

    def test_db_connect_not_found_exits(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        from anteroom.__main__ import _run_db

        config_file = tmp_path / "config.yaml"
        config_file.write_text("databases:\n  other_db:\n    path: /tmp/other.db\n")

        args = argparse.Namespace(db_action="connect", name="nonexistent", path=None)

        with (
            patch("anteroom.__main__._get_config_path", return_value=config_file),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_db(args)

        assert exc_info.value.code == 1

    def test_db_connect_success_no_passphrase(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        from anteroom.__main__ import _run_db

        config_file = tmp_path / "config.yaml"
        config_file.write_text("databases:\n  mydb:\n    path: /tmp/test.db\n")

        args = argparse.Namespace(db_action="connect", name="mydb", path=None)

        with patch("anteroom.__main__._get_config_path", return_value=config_file):
            _run_db(args)

        captured = capsys.readouterr()
        assert "Connected to 'mydb'" in captured.out

    def test_db_purge_dispatches(self, tmp_path: Path) -> None:
        from anteroom.__main__ import _run_db

        config_file = tmp_path / "config.yaml"
        config_file.write_text("{}")

        args = argparse.Namespace(db_action="purge", name=None, path=None)

        with (
            patch("anteroom.__main__._get_config_path", return_value=config_file),
            patch("anteroom.__main__._run_db_purge") as mock_purge,
        ):
            _run_db(args)

        mock_purge.assert_called_once_with(args)

    def test_db_encrypt_dispatches(self, tmp_path: Path) -> None:
        from anteroom.__main__ import _run_db

        config_file = tmp_path / "config.yaml"
        config_file.write_text("{}")

        args = argparse.Namespace(db_action="encrypt", name=None, path=None)

        with (
            patch("anteroom.__main__._get_config_path", return_value=config_file),
            patch("anteroom.__main__._run_db_encrypt") as mock_encrypt,
        ):
            _run_db(args)

        mock_encrypt.assert_called_once_with(args)


# ---------------------------------------------------------------------------
# _run_db_purge
# ---------------------------------------------------------------------------


class TestRunDbPurge:
    def test_purge_requires_before_or_older_than(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_db_purge

        mock_config = _make_config()
        mock_config.storage.retention_days = 0

        args = argparse.Namespace(before=None, older_than=None, dry_run=False, yes=False)

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_db_purge(args)

        assert exc_info.value.code == 1

    def test_purge_uses_config_retention_when_no_arg(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_db_purge

        mock_config = _make_config()
        mock_config.storage.retention_days = 30
        mock_config.app.data_dir = Path("/tmp/anteroom-test")

        args = argparse.Namespace(before=None, older_than=None, dry_run=True, yes=True)

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            patch("anteroom.db.init_db") as mock_init_db,
            patch("anteroom.services.retention.purge_conversations_before", return_value=0),
            patch("anteroom.services.retention.purge_orphaned_attachments", return_value=0),
        ):
            mock_db = MagicMock()
            mock_init_db.return_value = mock_db
            _run_db_purge(args)

        captured = capsys.readouterr()
        assert "30 days" in captured.out or "retention" in captured.out

    def test_purge_invalid_before_date_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_db_purge

        mock_config = _make_config()

        args = argparse.Namespace(before="not-a-date", older_than=None, dry_run=False, yes=False)

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_db_purge(args)

        assert exc_info.value.code == 1

    def test_purge_invalid_older_than_format_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_db_purge

        mock_config = _make_config()

        args = argparse.Namespace(before=None, older_than="30", dry_run=False, yes=False)

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_db_purge(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "must end with 'd'" in captured.err

    def test_purge_invalid_older_than_number_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_db_purge

        mock_config = _make_config()

        args = argparse.Namespace(before=None, older_than="xd", dry_run=False, yes=False)

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_db_purge(args)

        assert exc_info.value.code == 1

    def test_purge_dry_run_shows_preview(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_db_purge

        mock_config = _make_config()
        mock_config.app.data_dir = Path("/tmp/anteroom-test")

        args = argparse.Namespace(before="2024-01-01", older_than=None, dry_run=True, yes=False)

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            patch("anteroom.db.init_db") as mock_init_db,
            patch("anteroom.services.retention.purge_conversations_before", return_value=5),
            patch("anteroom.services.retention.purge_orphaned_attachments", return_value=2),
        ):
            mock_db = MagicMock()
            mock_init_db.return_value = mock_db
            _run_db_purge(args)

        captured = capsys.readouterr()
        assert "dry run" in captured.out
        assert "5" in captured.out

    def test_purge_nothing_to_purge_returns_early(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_db_purge

        mock_config = _make_config()
        mock_config.app.data_dir = Path("/tmp/anteroom-test")

        args = argparse.Namespace(before="2024-01-01", older_than=None, dry_run=False, yes=False)

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            patch("anteroom.db.init_db") as mock_init_db,
            patch("anteroom.services.retention.purge_conversations_before", return_value=0),
            patch("anteroom.services.retention.purge_orphaned_attachments", return_value=0),
        ):
            mock_db = MagicMock()
            mock_init_db.return_value = mock_db
            _run_db_purge(args)

        captured = capsys.readouterr()
        assert "Nothing to purge" in captured.out

    def test_purge_with_yes_flag_skips_confirm(self) -> None:
        from anteroom.__main__ import _run_db_purge

        mock_config = _make_config()
        mock_config.app.data_dir = Path("/tmp/anteroom-test")
        mock_config.storage.purge_attachments = True

        args = argparse.Namespace(before="2024-01-01", older_than=None, dry_run=False, yes=True)

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            patch("anteroom.db.init_db") as mock_init_db,
            patch("anteroom.services.retention.purge_conversations_before", return_value=3) as mock_purge,
            patch("anteroom.services.retention.purge_orphaned_attachments", return_value=1),
        ):
            mock_db = MagicMock()
            mock_init_db.return_value = mock_db
            _run_db_purge(args)

        # purge was called twice: once for dry_run preview, once for real
        assert mock_purge.call_count == 2


# ---------------------------------------------------------------------------
# _run_db_encrypt
# ---------------------------------------------------------------------------


class TestRunDbEncrypt:
    def test_encrypt_exits_when_sqlcipher_not_available(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_db_encrypt

        mock_config = _make_config()

        args = argparse.Namespace(yes=True)

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            patch("anteroom.services.encryption.is_sqlcipher_available", return_value=False),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_db_encrypt(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "sqlcipher3" in captured.err

    def test_encrypt_exits_when_no_identity_key(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_db_encrypt

        mock_config = _make_config()
        mock_config.identity.private_key = ""

        args = argparse.Namespace(yes=True)

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            patch("anteroom.services.encryption.is_sqlcipher_available", return_value=True),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_db_encrypt(args)

        assert exc_info.value.code == 1

    def test_encrypt_exits_when_db_not_found(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        from anteroom.__main__ import _run_db_encrypt

        mock_config = _make_config()
        mock_config.app.data_dir = tmp_path  # no chat.db here

        args = argparse.Namespace(yes=True)

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            patch("anteroom.services.encryption.is_sqlcipher_available", return_value=True),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_db_encrypt(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "database not found" in captured.err

    def test_encrypt_success(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        from anteroom.__main__ import _run_db_encrypt

        db_path = tmp_path / "chat.db"
        db_path.write_bytes(b"fake db content")

        mock_config = _make_config()
        mock_config.app.data_dir = tmp_path

        args = argparse.Namespace(yes=True)

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            patch("anteroom.services.encryption.is_sqlcipher_available", return_value=True),
            patch("anteroom.services.encryption.derive_db_key", return_value=b"key"),
            patch(
                "anteroom.services.encryption.migrate_plaintext_to_encrypted",
                return_value=tmp_path / "chat.db.bak-plaintext",
            ),
        ):
            _run_db_encrypt(args)

        captured = capsys.readouterr()
        assert "Encryption complete" in captured.out

    def test_encrypt_migration_error_exits(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        from anteroom.__main__ import _run_db_encrypt

        db_path = tmp_path / "chat.db"
        db_path.write_bytes(b"fake db content")

        mock_config = _make_config()
        mock_config.app.data_dir = tmp_path

        args = argparse.Namespace(yes=True)

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            patch("anteroom.services.encryption.is_sqlcipher_available", return_value=True),
            patch("anteroom.services.encryption.derive_db_key", return_value=b"key"),
            patch(
                "anteroom.services.encryption.migrate_plaintext_to_encrypted",
                side_effect=RuntimeError("migration failed"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_db_encrypt(args)

        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _run_usage
# ---------------------------------------------------------------------------


class TestRunUsage:
    def test_run_usage_all_periods(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_usage

        mock_config = _make_config()
        mock_config.app.data_dir = Path("/tmp/anteroom-test")
        mock_config.cli.usage.week_days = 7
        mock_config.cli.usage.month_days = 30
        mock_config.cli.usage.model_costs = {}

        with (
            patch("anteroom.db.init_db") as mock_init_db,
            patch("anteroom.services.storage.get_usage_stats", return_value=[]),
        ):
            mock_db = MagicMock()
            mock_init_db.return_value = mock_db
            _run_usage(mock_config)

        captured = capsys.readouterr()
        assert "Token Usage" in captured.out

    def test_run_usage_single_period(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_usage

        mock_config = _make_config()
        mock_config.app.data_dir = Path("/tmp/anteroom-test")
        mock_config.cli.usage.week_days = 7
        mock_config.cli.usage.month_days = 30
        mock_config.cli.usage.model_costs = {}

        with (
            patch("anteroom.db.init_db") as mock_init_db,
            patch("anteroom.services.storage.get_usage_stats", return_value=[]),
        ):
            mock_db = MagicMock()
            mock_init_db.return_value = mock_db
            _run_usage(mock_config, period="day")

        captured = capsys.readouterr()
        assert "Token Usage" in captured.out

    def test_run_usage_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        import json

        from anteroom.__main__ import _run_usage

        mock_config = _make_config()
        mock_config.app.data_dir = Path("/tmp/anteroom-test")
        mock_config.cli.usage.week_days = 7
        mock_config.cli.usage.month_days = 30
        mock_config.cli.usage.model_costs = {}

        with (
            patch("anteroom.db.init_db") as mock_init_db,
            patch("anteroom.services.storage.get_usage_stats", return_value=[]),
        ):
            mock_db = MagicMock()
            mock_init_db.return_value = mock_db
            _run_usage(mock_config, output_json=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, dict)

    def test_run_usage_with_stats(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_usage

        mock_config = _make_config()
        mock_config.app.data_dir = Path("/tmp/anteroom-test")
        mock_config.cli.usage.week_days = 7
        mock_config.cli.usage.month_days = 30
        mock_config.cli.usage.model_costs = {"gpt-4": {"input": 0.03, "output": 0.06}}

        stats = [
            {
                "model": "gpt-4",
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "total_tokens": 1500,
                "message_count": 5,
            }
        ]

        with (
            patch("anteroom.db.init_db") as mock_init_db,
            patch("anteroom.services.storage.get_usage_stats", return_value=stats),
        ):
            mock_db = MagicMock()
            mock_init_db.return_value = mock_db
            _run_usage(mock_config, period="all")

        captured = capsys.readouterr()
        assert "1,500" in captured.out
        assert "1,000" in captured.out

    def test_main_dispatches_usage(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_usage") as mock_run_usage,
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "usage"]):
                main()

        mock_run_usage.assert_called_once()


# ---------------------------------------------------------------------------
# _run_audit
# ---------------------------------------------------------------------------


class TestRunAudit:
    def test_main_dispatches_audit(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._run_audit") as mock_run_audit,
            patch("sys.argv", ["aroom", "audit", "purge"]),
        ):
            main()

        mock_run_audit.assert_called_once()

    def test_run_audit_no_action_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_audit

        args = argparse.Namespace(audit_action=None)

        with pytest.raises(SystemExit) as exc_info:
            _run_audit(args)

        assert exc_info.value.code == 1

    def test_run_audit_verify_log_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_audit

        mock_config = _make_config()

        args = argparse.Namespace(audit_action="verify", audit_file="/tmp/nonexistent-audit.jsonl")

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_audit(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err

    def test_run_audit_verify_no_identity_key(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        from anteroom.__main__ import _run_audit

        mock_config = _make_config()
        mock_config.identity.private_key = ""

        audit_file = tmp_path / "audit-2024-01-01.jsonl"
        audit_file.write_text('{"event_type":"test"}\n')

        args = argparse.Namespace(audit_action="verify", audit_file=str(audit_file))

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_audit(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "No identity key" in captured.err

    def test_run_audit_verify_empty_log(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        from anteroom.__main__ import _run_audit

        mock_config = _make_config()

        audit_file = tmp_path / "audit-2024-01-01.jsonl"
        audit_file.write_text("")

        args = argparse.Namespace(audit_action="verify", audit_file=str(audit_file))

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            patch("anteroom.services.audit.verify_chain", return_value=[]),
        ):
            _run_audit(args)

        captured = capsys.readouterr()
        assert "empty" in captured.out

    def test_run_audit_verify_valid_chain(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        from anteroom.__main__ import _run_audit

        mock_config = _make_config()

        audit_file = tmp_path / "audit.jsonl"
        audit_file.write_text('{"event_type":"auth"}\n')

        args = argparse.Namespace(audit_action="verify", audit_file=str(audit_file))

        results = [
            {"valid": True, "line": 1, "event_type": "auth", "timestamp": "2024-01-01T00:00:00Z"},
            {"valid": True, "line": 2, "event_type": "tool_call", "timestamp": "2024-01-01T00:01:00Z"},
        ]

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            patch("anteroom.services.audit.verify_chain", return_value=results),
        ):
            _run_audit(args)

        captured = capsys.readouterr()
        assert "INTACT" in captured.out

    def test_run_audit_verify_invalid_entries_exits(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        from anteroom.__main__ import _run_audit

        mock_config = _make_config()

        audit_file = tmp_path / "audit.jsonl"
        audit_file.write_text('{"event_type":"auth"}\n')

        args = argparse.Namespace(audit_action="verify", audit_file=str(audit_file))

        results = [
            {"valid": True, "line": 1, "event_type": "auth", "timestamp": "2024-01-01T00:00:00Z"},
            {
                "valid": False,
                "line": 2,
                "event_type": "tool_call",
                "timestamp": "2024-01-01T00:01:00Z",
                "error": "hash mismatch",
            },
        ]

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            patch("anteroom.services.audit.verify_chain", return_value=results),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_audit(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "INVALID" in captured.out

    def test_run_audit_purge_not_enabled_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_audit

        mock_config = _make_config()

        args = argparse.Namespace(audit_action="purge")

        mock_writer = MagicMock()
        mock_writer.enabled = False

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            patch("anteroom.services.audit.create_audit_writer", return_value=mock_writer),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_audit(args)

        assert exc_info.value.code == 1

    def test_run_audit_purge_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_audit

        mock_config = _make_config()

        args = argparse.Namespace(audit_action="purge")

        mock_writer = MagicMock()
        mock_writer.enabled = True
        mock_writer.purge_old_logs.return_value = 3

        with (
            patch("anteroom.__main__._load_config_or_exit", return_value=(Path("/tmp"), mock_config, [])),
            patch("anteroom.services.audit.create_audit_writer", return_value=mock_writer),
        ):
            _run_audit(args)

        captured = capsys.readouterr()
        assert "3" in captured.out


# ---------------------------------------------------------------------------
# _validate_pack_ref
# ---------------------------------------------------------------------------


class TestValidatePackRef:
    def test_valid_ref_returns_namespace_name(self) -> None:
        from anteroom.__main__ import _validate_pack_ref

        ns, name = _validate_pack_ref("myns/mypack")
        assert ns == "myns"
        assert name == "mypack"

    def test_missing_slash_defaults_namespace(self) -> None:
        from anteroom.__main__ import _validate_pack_ref

        ns, name = _validate_pack_ref("noSlash")
        assert ns == "default"
        assert name == "noSlash"

    def test_invalid_namespace_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _validate_pack_ref

        with pytest.raises(SystemExit) as exc_info:
            _validate_pack_ref("-invalid/pack")

        assert exc_info.value.code == 1

    def test_invalid_name_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _validate_pack_ref

        with pytest.raises(SystemExit) as exc_info:
            _validate_pack_ref("ns/-invalid")

        assert exc_info.value.code == 1

    def test_name_with_dots_and_dashes_valid(self) -> None:
        from anteroom.__main__ import _validate_pack_ref

        ns, name = _validate_pack_ref("my-ns/my.pack-1")
        assert ns == "my-ns"
        assert name == "my.pack-1"


# ---------------------------------------------------------------------------
# _pick_from_candidates
# ---------------------------------------------------------------------------


class TestPickFromCandidates:
    def test_non_tty_prints_ids(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _pick_from_candidates

        candidates = [{"id": "abc123"}, {"id": "def456"}]

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            result = _pick_from_candidates(candidates, "pack", lambda c: c["id"])

        assert result is None
        captured = capsys.readouterr()
        assert "abc123" in captured.err

    def test_tty_valid_selection(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _pick_from_candidates

        candidates = [{"id": "abc123", "name": "First"}, {"id": "def456", "name": "Second"}]

        with (
            patch("sys.stdin") as mock_stdin,
            patch("builtins.input", return_value="1"),
        ):
            mock_stdin.isatty.return_value = True
            result = _pick_from_candidates(candidates, "pack", lambda c: c["name"])

        assert result is candidates[0]

    def test_tty_invalid_selection_returns_none(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _pick_from_candidates

        candidates = [{"id": "abc123"}, {"id": "def456"}]

        with (
            patch("sys.stdin") as mock_stdin,
            patch("builtins.input", return_value="99"),
        ):
            mock_stdin.isatty.return_value = True
            result = _pick_from_candidates(candidates, "pack", lambda c: c["id"])

        assert result is None

    def test_tty_eof_returns_none(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _pick_from_candidates

        candidates = [{"id": "abc123"}]

        with (
            patch("sys.stdin") as mock_stdin,
            patch("builtins.input", side_effect=EOFError),
        ):
            mock_stdin.isatty.return_value = True
            result = _pick_from_candidates(candidates, "pack", lambda c: c["id"])

        assert result is None


# ---------------------------------------------------------------------------
# _run_artifact (dispatch only — full tests in test_artifact_cli.py)
# ---------------------------------------------------------------------------


class TestRunArtifactDispatch:
    def test_artifact_no_action_prints_usage(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_artifact

        mock_config = _make_config()
        args = argparse.Namespace(artifact_action=None)

        with patch("anteroom.db.get_db"):
            _run_artifact(mock_config, args)

        captured = capsys.readouterr()
        assert "Usage" in captured.out

    def test_main_dispatches_artifact(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_artifact") as mock_run,
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "artifact", "list"]):
                main()

        mock_run.assert_called_once()

    def test_artifact_import_no_flags_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_artifact

        mock_config = _make_config()
        args = argparse.Namespace(
            artifact_action="import",
            skills=False,
            instructions=False,
            import_all=False,
        )

        with (
            patch("anteroom.db.get_db") as mock_get_db,
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_get_db.return_value = MagicMock()
            _run_artifact(mock_config, args)

        assert exc_info.value.code == 1

    def test_artifact_create_success(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        from anteroom.__main__ import _run_artifact

        mock_config = _make_config()
        mock_config.app.data_dir = tmp_path
        args = argparse.Namespace(
            artifact_action="create",
            type="skill",
            name="my-skill",
            project=False,
        )

        fake_path = tmp_path / "artifacts" / "skill" / "my-skill.yaml"

        with (
            patch("anteroom.db.get_db") as mock_get_db,
            patch("anteroom.services.local_artifacts.scaffold_local_artifact", return_value=fake_path),
        ):
            mock_get_db.return_value = MagicMock()
            _run_artifact(mock_config, args)

        captured = capsys.readouterr()
        assert "Created" in captured.out

    def test_artifact_create_invalid_name_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_artifact

        mock_config = _make_config()
        args = argparse.Namespace(
            artifact_action="create",
            type="skill",
            name="invalid name!",
            project=False,
        )

        with (
            patch("anteroom.db.get_db") as mock_get_db,
            patch(
                "anteroom.services.local_artifacts.scaffold_local_artifact",
                side_effect=ValueError("Invalid name"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_get_db.return_value = MagicMock()
            _run_artifact(mock_config, args)

        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _run_space (dispatch / basic actions)
# ---------------------------------------------------------------------------


class TestRunSpaceDispatch:
    def test_space_no_action_prints_usage(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_space

        mock_config = _make_config()
        args = argparse.Namespace(space_action=None)

        with patch("anteroom.db.get_db"):
            _run_space(mock_config, args)

        captured = capsys.readouterr()
        assert "Usage" in captured.out

    def test_main_dispatches_space(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_space") as mock_run,
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "space", "list"]):
                main()

        mock_run.assert_called_once()

    def test_space_list_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_space

        mock_config = _make_config()
        args = argparse.Namespace(space_action="list")

        with (
            patch("anteroom.db.get_db") as mock_get_db,
            patch("anteroom.services.space_storage.list_spaces", return_value=[]),
        ):
            mock_get_db.return_value = MagicMock()
            _run_space(mock_config, args)

        captured = capsys.readouterr()
        assert "No spaces found" in captured.out

    def test_space_list_shows_spaces(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_space

        mock_config = _make_config()
        args = argparse.Namespace(space_action="list")

        spaces = [
            {"id": "abc123", "name": "my-space", "file_path": "/home/.anteroom/my-space.yaml", "last_loaded_at": ""},
        ]

        with (
            patch("anteroom.db.get_db") as mock_get_db,
            patch("anteroom.services.space_storage.list_spaces", return_value=spaces),
            patch("anteroom.services.space_storage.count_space_conversations", return_value=0),
            patch("anteroom.services.spaces.is_local_space", return_value=False),
        ):
            mock_get_db.return_value = MagicMock()
            _run_space(mock_config, args)

        captured = capsys.readouterr()
        assert "my-space" in captured.out

    def test_space_delete(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_space

        mock_config = _make_config()
        args = argparse.Namespace(space_action="delete", name="my-space")

        mock_space = {"id": "abc123", "name": "my-space", "file_path": "/tmp/my-space.yaml"}

        with (
            patch("anteroom.db.get_db") as mock_get_db,
            patch("anteroom.services.space_storage.resolve_space", return_value=(mock_space, [])),
            patch("anteroom.services.space_storage.delete_space") as mock_delete,
        ):
            mock_get_db.return_value = MagicMock()
            _run_space(mock_config, args)

        mock_delete.assert_called_once()
        captured = capsys.readouterr()
        assert "Deleted" in captured.out

    def test_space_delete_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_space

        mock_config = _make_config()
        args = argparse.Namespace(space_action="delete", name="nonexistent")

        with (
            patch("anteroom.db.get_db") as mock_get_db,
            patch("anteroom.services.space_storage.resolve_space", return_value=(None, [])),
        ):
            mock_get_db.return_value = MagicMock()
            _run_space(mock_config, args)

        captured = capsys.readouterr()
        assert "not found" in captured.out

    def test_space_create_invalid_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_space

        mock_config = _make_config()
        args = argparse.Namespace(space_action="create", name="invalid name!")

        with patch("anteroom.db.get_db") as mock_get_db:
            mock_get_db.return_value = MagicMock()
            _run_space(mock_config, args)

        captured = capsys.readouterr()
        assert "Invalid space name" in captured.out

    def test_space_create_success(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        from anteroom.__main__ import _run_space

        mock_config = _make_config()
        args = argparse.Namespace(space_action="create", name="my-space")

        mock_space = {"id": "abc12345-0000-0000-0000-000000000000", "name": "my-space"}

        with (
            patch("anteroom.db.get_db") as mock_get_db,
            patch("anteroom.services.spaces.write_space_template"),
            patch("anteroom.services.spaces.compute_file_hash", return_value="fakehash"),
            patch("anteroom.services.space_storage.create_space", return_value=mock_space),
            patch("anteroom.services.space_storage.get_space_by_name", return_value=None),
            patch("anteroom.services.space_storage.sync_space_paths"),
            patch("pathlib.Path.exists", return_value=False),
            patch("pathlib.Path.cwd", return_value=tmp_path),
        ):
            mock_get_db.return_value = MagicMock()
            _run_space(mock_config, args)

        captured = capsys.readouterr()
        assert "Created local space" in captured.out

    def test_space_show_existing(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_space

        mock_config = _make_config()
        args = argparse.Namespace(space_action="show", name="my-space")

        mock_space = {
            "id": "abc12345",
            "name": "my-space",
            "file_path": "/tmp/my-space.yaml",
            "file_hash": "deadbeefdeadbeef",
            "last_loaded_at": "2024-01-01",
            "created_at": "2024-01-01",
        }

        with (
            patch("anteroom.db.get_db") as mock_get_db,
            patch("anteroom.services.space_storage.resolve_space", return_value=(mock_space, [])),
        ):
            mock_get_db.return_value = MagicMock()
            _run_space(mock_config, args)

        captured = capsys.readouterr()
        assert "my-space" in captured.out
        assert "abc12345" in captured.out

    def test_space_load_file_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_space

        mock_config = _make_config()
        args = argparse.Namespace(space_action="load", path="/nonexistent/space.yaml")

        with patch("anteroom.db.get_db") as mock_get_db:
            mock_get_db.return_value = MagicMock()
            _run_space(mock_config, args)

        captured = capsys.readouterr()
        assert "not found" in captured.out or "Error" in captured.out

    def test_space_map_dir_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_space

        mock_config = _make_config()
        args = argparse.Namespace(space_action="map", name="my-space", dir_path="/nonexistent/dir")

        mock_space = {"id": "abc123", "name": "my-space", "file_path": "/tmp/my-space.yaml"}

        with (
            patch("anteroom.db.get_db") as mock_get_db,
            patch("anteroom.services.space_storage.resolve_space", return_value=(mock_space, [])),
        ):
            mock_get_db.return_value = MagicMock()
            _run_space(mock_config, args)

        captured = capsys.readouterr()
        assert "not found" in captured.out or "Error" in captured.out


# ---------------------------------------------------------------------------
# main() dispatch — global flags
# ---------------------------------------------------------------------------


class TestMainDispatch:
    def test_main_dispatches_pack(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_pack") as mock_run,
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "pack", "list"]):
                main()

        mock_run.assert_called_once()

    def test_main_allowed_tools_flag(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
        ):
            config = _make_config()
            config.safety.allowed_tools = []
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "--allowed-tools", "bash,write_file"]):
                main()

        assert "bash" in config.safety.allowed_tools
        assert "write_file" in config.safety.allowed_tools

    def test_main_allowed_tools_deduplicates(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
        ):
            config = _make_config()
            config.safety.allowed_tools = ["bash"]
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "--allowed-tools", "bash,write_file"]):
                main()

        assert config.safety.allowed_tools.count("bash") == 1

    def test_main_read_only_flag(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "--read-only"]):
                main()

        assert config.safety.read_only is True

    def test_main_read_only_suppressed_when_enforced(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
        ):
            config = _make_config()
            config.safety.read_only = False
            mock_load.return_value = (Path("/tmp/config.yaml"), config, ["safety.read_only"])
            with patch("sys.argv", ["aroom", "--read-only"]):
                main()

        assert config.safety.read_only is False

    def test_main_temperature_flag(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "--temperature", "0.7"]):
                main()

        assert config.ai.temperature == pytest.approx(0.7)

    def test_main_temperature_clamped_to_range(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "--temperature", "5.0"]):
                main()

        assert config.ai.temperature == pytest.approx(2.0)

    def test_main_top_p_flag(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "--top-p", "0.9"]):
                main()

        assert config.ai.top_p == pytest.approx(0.9)

    def test_main_seed_flag(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "--seed", "42"]):
                main()

        assert config.ai.seed == 42

    def test_main_approval_mode_auto_warns(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
        ):
            config = _make_config()
            config.safety.approval_mode = "ask"
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "--approval-mode", "auto"]):
                main()

        assert config.safety.approval_mode == "auto"
        captured = capsys.readouterr()
        assert "Auto-approval" in captured.err

    def test_main_test_flag_calls_test_connection(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__.asyncio.run") as mock_asyncio_run,
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "--test"]):
                main()

        mock_asyncio_run.assert_called_once()

    def test_main_dispatches_to_web_when_no_command(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web") as mock_web,
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom"]):
                main()

        mock_web.assert_called_once()

    def test_main_dispatches_chat(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_chat") as mock_chat,
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "chat"]):
                main()

        mock_chat.assert_called_once()

    def test_main_space_flag_resolves_id(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._resolve_space_id", return_value="space-456") as mock_resolve,
            patch("anteroom.__main__._run_chat") as mock_chat,
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            # --space is a global flag; must appear before the subcommand
            with patch("sys.argv", ["aroom", "--space", "my-space", "chat"]):
                main()

        mock_resolve.assert_called_once_with(config, "my-space")
        _, kwargs = mock_chat.call_args
        assert kwargs["space_id"] == "space-456"


# ---------------------------------------------------------------------------
# _resolve_space_id
# ---------------------------------------------------------------------------


class TestResolveIds:
    def test_resolve_space_id_success(self) -> None:
        from anteroom.__main__ import _resolve_space_id

        mock_config = _make_config()
        mock_space = {"id": "space-abc-123"}

        with (
            patch("anteroom.db.get_db") as mock_get_db,
            patch("anteroom.services.space_storage.resolve_space", return_value=(mock_space, [])),
        ):
            mock_get_db.return_value = MagicMock()
            result = _resolve_space_id(mock_config, "my-space")

        assert result == "space-abc-123"

    def test_resolve_space_id_multiple_matches_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _resolve_space_id

        mock_config = _make_config()
        candidates = [{"id": "aaa", "name": "space1"}, {"id": "bbb", "name": "space2"}]

        with (
            patch("anteroom.db.get_db") as mock_get_db,
            patch("anteroom.services.space_storage.resolve_space", return_value=(None, candidates)),
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_get_db.return_value = MagicMock()
            _resolve_space_id(mock_config, "space")

        assert exc_info.value.code == 1

    def test_resolve_space_id_not_found_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _resolve_space_id

        mock_config = _make_config()

        with (
            patch("anteroom.db.get_db") as mock_get_db,
            patch("anteroom.services.space_storage.resolve_space", return_value=(None, [])),
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_get_db.return_value = MagicMock()
            _resolve_space_id(mock_config, "nonexistent-space")

        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _run_chat
# ---------------------------------------------------------------------------


class TestRunChat:
    def test_run_chat_invalid_project_path_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_chat

        mock_config = _make_config()

        with pytest.raises(SystemExit) as exc_info:
            _run_chat(mock_config, project_path="/nonexistent/directory/that/does/not/exist")

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "not a directory" in captured.err

    def test_run_chat_model_override(self) -> None:
        from anteroom.__main__ import _run_chat

        mock_config = _make_config()

        with (
            patch("anteroom.cli.repl.run_cli"),
            patch("anteroom.__main__.asyncio.run"),
        ):
            _run_chat(mock_config, model="gpt-4-turbo")

        assert mock_config.ai.model == "gpt-4-turbo"

    def test_run_chat_keyboard_interrupt_suppressed(self) -> None:
        from anteroom.__main__ import _run_chat

        mock_config = _make_config()

        with patch("anteroom.__main__.asyncio.run", side_effect=KeyboardInterrupt):
            _run_chat(mock_config)

    def test_run_chat_api_connection_error_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_chat

        mock_config = _make_config()

        class FakeAPIConnectionError(Exception):
            pass

        FakeAPIConnectionError.__name__ = "APIConnectionError"

        with (
            patch("anteroom.__main__.asyncio.run", side_effect=FakeAPIConnectionError("conn refused")),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_chat(mock_config)

        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _run_exec
# ---------------------------------------------------------------------------


class TestRunExec:
    def test_run_exec_keyboard_interrupt_exits_130(self) -> None:
        from anteroom.__main__ import _run_exec

        mock_config = _make_config()

        with (
            patch("anteroom.__main__.asyncio.run", side_effect=KeyboardInterrupt),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_exec(mock_config, prompt="test")

        assert exc_info.value.code == 130

    def test_run_exec_timeout_clamped(self) -> None:
        from anteroom.__main__ import _run_exec

        mock_config = _make_config()

        with patch("anteroom.__main__.asyncio.run", return_value=0) as mock_run:
            with pytest.raises(SystemExit):
                _run_exec(mock_config, prompt="test", timeout=5.0)

        mock_run.assert_called_once()

    def test_run_exec_model_override(self) -> None:
        from anteroom.__main__ import _run_exec

        mock_config = _make_config()

        with patch("anteroom.__main__.asyncio.run", return_value=0):
            with pytest.raises(SystemExit):
                _run_exec(mock_config, prompt="test", model="gpt-4")

        assert mock_config.ai.model == "gpt-4"

    def test_run_exec_api_connection_error_exits_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.__main__ import _run_exec

        mock_config = _make_config()

        class FakeAPIConnectionError(Exception):
            pass

        FakeAPIConnectionError.__name__ = "APIConnectionError"

        with (
            patch("anteroom.__main__.asyncio.run", side_effect=FakeAPIConnectionError("conn refused")),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_exec(mock_config, prompt="test")

        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _run_pack (dispatch only — full tests in test_pack_cli.py)
# ---------------------------------------------------------------------------


class TestRunPackDispatch:
    def test_main_dispatches_pack_install(self) -> None:
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._run_pack_dispatch") as mock_dispatch,
        ):
            with patch("sys.argv", ["aroom", "pack", "install", "/tmp/pack"]):
                main()

        mock_dispatch.assert_called_once()
        args_passed = mock_dispatch.call_args[0][0]
        assert args_passed.pack_action == "install"

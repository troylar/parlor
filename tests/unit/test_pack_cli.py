"""Tests for the `aroom pack` CLI subcommand."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from anteroom.config import PackSourceConfig
from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services.pack_refresh import SourceRefreshResult
from anteroom.services.pack_sources import CachedSource
from anteroom.services.packs import install_pack, parse_manifest


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


def _create_pack_dir(tmp_path: Path) -> Path:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    (pack_dir / "skills").mkdir()
    (pack_dir / "skills" / "greet.yaml").write_text("content: Hello!\n")
    manifest_data = {
        "name": "test-pack",
        "namespace": "test-ns",
        "version": "1.0.0",
        "artifacts": [{"type": "skill", "name": "greet"}],
    }
    with open(pack_dir / "pack.yaml", "w", encoding="utf-8") as f:
        yaml.dump(manifest_data, f)
    return pack_dir


class TestRunPackNoAction:
    def test_no_action_prints_usage(self, capsys: pytest.CaptureFixture[str]) -> None:
        config = _make_config()
        args = MagicMock()
        args.pack_action = None

        from anteroom.__main__ import _run_pack

        with patch("anteroom.db.get_db"):
            _run_pack(config, args)

        captured = capsys.readouterr()
        assert "Usage" in captured.out


class TestRunPackList:
    def test_list_empty(self, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]) -> None:
        config = _make_config()
        args = MagicMock()
        args.pack_action = "list"

        from anteroom.__main__ import _run_pack

        with patch("anteroom.db.get_db", return_value=db):
            _run_pack(config, args)

        captured = capsys.readouterr()
        assert "No packs installed" in captured.out

    def test_list_shows_packs(
        self, tmp_path: Path, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        config = _make_config()
        args = MagicMock()
        args.pack_action = "list"

        from anteroom.__main__ import _run_pack

        with patch("anteroom.db.get_db", return_value=db):
            _run_pack(config, args)

        captured = capsys.readouterr()
        assert "test-ns" in captured.out
        assert "test-pack" in captured.out


class TestRunPackInstall:
    def test_install_success(
        self, tmp_path: Path, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pack_dir = _create_pack_dir(tmp_path)

        config = _make_config()
        args = MagicMock()
        args.pack_action = "install"
        args.path = str(pack_dir)
        args.project = False

        from anteroom.__main__ import _run_pack

        with patch("anteroom.db.get_db", return_value=db):
            _run_pack(config, args)

        captured = capsys.readouterr()
        assert "Installed" in captured.out
        assert "test-ns/test-pack" in captured.out

    def test_install_invalid_manifest(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        pack_dir = tmp_path / "bad-pack"
        pack_dir.mkdir()
        (pack_dir / "pack.yaml").write_text("not_valid: true\n")

        config = _make_config()
        args = MagicMock()
        args.pack_action = "install"
        args.path = str(pack_dir)
        args.project = False

        from anteroom.__main__ import _run_pack

        with patch("anteroom.db.get_db", return_value=MagicMock()), pytest.raises(SystemExit):
            _run_pack(config, args)

    def test_install_missing_artifact_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        manifest_data = {
            "name": "test-pack",
            "namespace": "test-ns",
            "version": "1.0.0",
            "artifacts": [{"type": "skill", "name": "missing"}],
        }
        with open(pack_dir / "pack.yaml", "w", encoding="utf-8") as f:
            yaml.dump(manifest_data, f)

        config = _make_config()
        args = MagicMock()
        args.pack_action = "install"
        args.path = str(pack_dir)
        args.project = False

        from anteroom.__main__ import _run_pack

        with patch("anteroom.db.get_db", return_value=MagicMock()), pytest.raises(SystemExit):
            _run_pack(config, args)


class TestRunPackShow:
    def test_show_existing(self, tmp_path: Path, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        config = _make_config()
        args = MagicMock()
        args.pack_action = "show"
        args.ref = "test-ns/test-pack"

        from anteroom.__main__ import _run_pack

        with patch("anteroom.db.get_db", return_value=db):
            _run_pack(config, args)

        captured = capsys.readouterr()
        assert "test-ns/test-pack" in captured.out
        assert "1.0.0" in captured.out

    def test_show_not_found(self, db: ThreadSafeConnection) -> None:
        config = _make_config()
        args = MagicMock()
        args.pack_action = "show"
        args.ref = "no/such-pack"

        from anteroom.__main__ import _run_pack

        with patch("anteroom.db.get_db", return_value=db), pytest.raises(SystemExit):
            _run_pack(config, args)

    def test_show_invalid_ref(self) -> None:
        config = _make_config()
        args = MagicMock()
        args.pack_action = "show"
        args.ref = "no-slash"

        from anteroom.__main__ import _run_pack

        with patch("anteroom.db.get_db", return_value=MagicMock()), pytest.raises(SystemExit):
            _run_pack(config, args)


class TestRunPackRemove:
    def test_remove_existing(
        self, tmp_path: Path, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        config = _make_config()
        args = MagicMock()
        args.pack_action = "remove"
        args.ref = "test-ns/test-pack"

        from anteroom.__main__ import _run_pack

        with patch("anteroom.db.get_db", return_value=db):
            _run_pack(config, args)

        captured = capsys.readouterr()
        assert "Removed" in captured.out

    def test_remove_not_found(self, db: ThreadSafeConnection) -> None:
        config = _make_config()
        args = MagicMock()
        args.pack_action = "remove"
        args.ref = "no/such-pack"

        from anteroom.__main__ import _run_pack

        with patch("anteroom.db.get_db", return_value=db), pytest.raises(SystemExit):
            _run_pack(config, args)

    def test_remove_invalid_ref(self) -> None:
        config = _make_config()
        args = MagicMock()
        args.pack_action = "remove"
        args.ref = "noslash"

        from anteroom.__main__ import _run_pack

        with patch("anteroom.db.get_db", return_value=MagicMock()), pytest.raises(SystemExit):
            _run_pack(config, args)


class TestRunPackUpdate:
    def test_update_success(self, tmp_path: Path, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        config = _make_config()
        args = MagicMock()
        args.pack_action = "update"
        args.path = str(pack_dir)
        args.project = False

        from anteroom.__main__ import _run_pack

        with patch("anteroom.db.get_db", return_value=db):
            _run_pack(config, args)

        captured = capsys.readouterr()
        assert "Updated" in captured.out
        assert "test-ns/test-pack" in captured.out

    def test_update_invalid_manifest(self, tmp_path: Path) -> None:
        pack_dir = tmp_path / "bad-pack"
        pack_dir.mkdir()
        (pack_dir / "pack.yaml").write_text("not_valid: true\n")

        config = _make_config()
        args = MagicMock()
        args.pack_action = "update"
        args.path = str(pack_dir)
        args.project = False

        from anteroom.__main__ import _run_pack

        with patch("anteroom.db.get_db", return_value=MagicMock()), pytest.raises(SystemExit):
            _run_pack(config, args)


class TestRunPackSources:
    def test_sources_no_config(self, capsys: pytest.CaptureFixture[str]) -> None:
        config = _make_config()
        config.pack_sources = []
        args = MagicMock()
        args.pack_action = "sources"

        from anteroom.__main__ import _run_pack

        with patch("anteroom.db.get_db", return_value=MagicMock()):
            _run_pack(config, args)

        captured = capsys.readouterr()
        assert "No pack sources configured" in captured.out

    def test_sources_shows_table(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        config = _make_config()
        config.pack_sources = [
            PackSourceConfig(url="https://example.com/packs.git", branch="main", refresh_interval=30),
        ]
        config.app.data_dir = tmp_path
        args = MagicMock()
        args.pack_action = "sources"

        from anteroom.__main__ import _run_pack

        with (
            patch("anteroom.db.get_db", return_value=MagicMock()),
            patch(
                "anteroom.services.pack_sources.list_cached_sources",
                return_value=[
                    CachedSource(
                        url="https://example.com/packs.git",
                        branch="main",
                        path=tmp_path / "cache",
                        ref="abc123def456",
                    )
                ],
            ),
        ):
            _run_pack(config, args)

        captured = capsys.readouterr()
        assert "example.com" in captured.out
        assert "abc123def456" in captured.out


class TestRunPackRefresh:
    def test_refresh_no_config(self, capsys: pytest.CaptureFixture[str]) -> None:
        config = _make_config()
        config.pack_sources = []
        args = MagicMock()
        args.pack_action = "refresh"

        from anteroom.__main__ import _run_pack

        with patch("anteroom.db.get_db", return_value=MagicMock()):
            _run_pack(config, args)

        captured = capsys.readouterr()
        assert "No pack sources configured" in captured.out

    def test_refresh_success(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        config = _make_config()
        config.pack_sources = [
            PackSourceConfig(url="https://example.com/packs.git"),
        ]
        config.app.data_dir = tmp_path
        args = MagicMock()
        args.pack_action = "refresh"

        from anteroom.__main__ import _run_pack

        mock_worker = MagicMock()
        mock_worker.refresh_all.return_value = [
            SourceRefreshResult(url="https://example.com/packs.git", success=True, packs_installed=1),
        ]

        with (
            patch("anteroom.db.get_db", return_value=MagicMock()),
            patch("anteroom.services.pack_refresh.PackRefreshWorker", return_value=mock_worker),
        ):
            _run_pack(config, args)

        captured = capsys.readouterr()
        assert "OK" in captured.out
        assert "1 installed" in captured.out

    def test_refresh_failure(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        config = _make_config()
        config.pack_sources = [
            PackSourceConfig(url="https://example.com/packs.git"),
        ]
        config.app.data_dir = tmp_path
        args = MagicMock()
        args.pack_action = "refresh"

        from anteroom.__main__ import _run_pack

        mock_worker = MagicMock()
        mock_worker.refresh_all.return_value = [
            SourceRefreshResult(url="https://example.com/packs.git", success=False, error="git not found"),
        ]

        with (
            patch("anteroom.db.get_db", return_value=MagicMock()),
            patch("anteroom.services.pack_refresh.PackRefreshWorker", return_value=mock_worker),
        ):
            _run_pack(config, args)

        captured = capsys.readouterr()
        assert "FAIL" in captured.out
        assert "git not found" in captured.out

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
        args = MagicMock()
        args.pack_action = None

        from anteroom.__main__ import _run_pack_dispatch

        _run_pack_dispatch(args)

        captured = capsys.readouterr()
        assert "Usage" in captured.out


class TestRunPackList:
    def test_list_empty(self, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]) -> None:
        args = MagicMock()
        args.pack_action = "list"

        from anteroom.__main__ import _run_pack

        _run_pack(Path("/tmp"), db, args)

        captured = capsys.readouterr()
        assert "No packs installed" in captured.out

    def test_list_shows_packs(
        self, tmp_path: Path, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        args = MagicMock()
        args.pack_action = "list"

        from anteroom.__main__ import _run_pack

        _run_pack(tmp_path, db, args)

        captured = capsys.readouterr()
        assert "test-ns" in captured.out
        assert "test-pack" in captured.out

    def test_list_shows_attachment_status(
        self, tmp_path: Path, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from anteroom.__main__ import _run_pack
        from anteroom.services.pack_attachments import attach_pack

        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        result = install_pack(db, manifest, pack_dir)
        attach_pack(db, result["id"], priority=10)

        args = MagicMock()
        args.pack_action = "list"
        _run_pack(tmp_path, db, args)

        captured = capsys.readouterr()
        assert "global" in captured.out
        assert "p10" in captured.out

    def test_list_shows_not_attached(
        self, tmp_path: Path, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from anteroom.__main__ import _run_pack

        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        args = MagicMock()
        args.pack_action = "list"
        _run_pack(tmp_path, db, args)

        captured = capsys.readouterr()
        assert "no" in captured.out

    def test_list_hides_default_priority(
        self, tmp_path: Path, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from anteroom.__main__ import _run_pack
        from anteroom.services.pack_attachments import attach_pack

        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        result = install_pack(db, manifest, pack_dir)
        attach_pack(db, result["id"])  # default priority 50

        args = MagicMock()
        args.pack_action = "list"
        _run_pack(tmp_path, db, args)

        captured = capsys.readouterr()
        assert "global" in captured.out
        # Default priority (50) should not show "p50"
        assert "p50" not in captured.out


class TestRunPackInstall:
    def test_install_success(
        self, tmp_path: Path, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pack_dir = _create_pack_dir(tmp_path)

        args = MagicMock()
        args.pack_action = "install"
        args.source = str(pack_dir)
        args.project = False
        args.attach = False
        args.branch = "main"
        args.subpath = None

        from anteroom.__main__ import _run_pack

        _run_pack(tmp_path, db, args)

        captured = capsys.readouterr()
        assert "Installed" in captured.out
        assert "test-ns/test-pack" in captured.out

    def test_install_invalid_manifest(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        pack_dir = tmp_path / "bad-pack"
        pack_dir.mkdir()
        (pack_dir / "pack.yaml").write_text("not_valid: true\n")

        args = MagicMock()
        args.pack_action = "install"
        args.source = str(pack_dir)
        args.project = False
        args.attach = False
        args.branch = "main"
        args.subpath = None

        from anteroom.__main__ import _run_pack

        with pytest.raises(SystemExit):
            _run_pack(tmp_path, MagicMock(), args)

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

        args = MagicMock()
        args.pack_action = "install"
        args.source = str(pack_dir)
        args.project = False
        args.attach = False
        args.branch = "main"
        args.subpath = None

        from anteroom.__main__ import _run_pack

        with pytest.raises(SystemExit):
            _run_pack(tmp_path, MagicMock(), args)


class TestRunPackShow:
    def test_show_existing(self, tmp_path: Path, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        args = MagicMock()
        args.pack_action = "show"
        args.ref = "test-ns/test-pack"

        from anteroom.__main__ import _run_pack

        _run_pack(tmp_path, db, args)

        captured = capsys.readouterr()
        assert "test-ns/test-pack" in captured.out
        assert "1.0.0" in captured.out

    def test_show_not_found(self, db: ThreadSafeConnection) -> None:
        args = MagicMock()
        args.pack_action = "show"
        args.ref = "no/such-pack"

        from anteroom.__main__ import _run_pack

        with pytest.raises(SystemExit):
            _run_pack(Path("/tmp"), db, args)

    def test_show_invalid_ref(self) -> None:
        args = MagicMock()
        args.pack_action = "show"
        args.ref = "no-slash"

        from anteroom.__main__ import _run_pack

        with pytest.raises(SystemExit):
            _run_pack(Path("/tmp"), MagicMock(), args)


class TestRunPackRemove:
    def test_remove_existing(
        self, tmp_path: Path, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        args = MagicMock()
        args.pack_action = "remove"
        args.ref = "test-ns/test-pack"

        from anteroom.__main__ import _run_pack

        _run_pack(tmp_path, db, args)

        captured = capsys.readouterr()
        assert "Removed" in captured.out

    def test_remove_not_found(self, db: ThreadSafeConnection) -> None:
        args = MagicMock()
        args.pack_action = "remove"
        args.ref = "no/such-pack"

        from anteroom.__main__ import _run_pack

        with pytest.raises(SystemExit):
            _run_pack(Path("/tmp"), db, args)

    def test_remove_invalid_ref(self) -> None:
        args = MagicMock()
        args.pack_action = "remove"
        args.ref = "noslash"

        from anteroom.__main__ import _run_pack

        with pytest.raises(SystemExit):
            _run_pack(Path("/tmp"), MagicMock(), args)


class TestRunPackUpdate:
    def test_update_success(self, tmp_path: Path, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        args = MagicMock()
        args.pack_action = "update"
        args.path = str(pack_dir)
        args.project = False

        from anteroom.__main__ import _run_pack

        _run_pack(tmp_path, db, args)

        captured = capsys.readouterr()
        assert "Updated" in captured.out
        assert "test-ns/test-pack" in captured.out

    def test_update_invalid_manifest(self, tmp_path: Path) -> None:
        pack_dir = tmp_path / "bad-pack"
        pack_dir.mkdir()
        (pack_dir / "pack.yaml").write_text("not_valid: true\n")

        args = MagicMock()
        args.pack_action = "update"
        args.path = str(pack_dir)
        args.project = False

        from anteroom.__main__ import _run_pack

        with pytest.raises(SystemExit):
            _run_pack(tmp_path, MagicMock(), args)


class TestRunPackSources:
    def test_sources_no_config(self, capsys: pytest.CaptureFixture[str]) -> None:
        config = _make_config()
        config.pack_sources = []
        args = MagicMock()
        args.pack_action = "sources"

        from anteroom.__main__ import _run_pack_with_config

        with patch("anteroom.db.get_db", return_value=MagicMock()):
            _run_pack_with_config(config, args)

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

        from anteroom.__main__ import _run_pack_with_config

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
            _run_pack_with_config(config, args)

        captured = capsys.readouterr()
        assert "example.com" in captured.out
        assert "abc123def456" in captured.out


class TestRunPackAttach:
    def test_attach_success(self, tmp_path: Path, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        args = MagicMock()
        args.pack_action = "attach"
        args.ref = "test-ns/test-pack"
        args.project = False
        args.priority = 50

        from anteroom.__main__ import _run_pack

        _run_pack(tmp_path, db, args)

        captured = capsys.readouterr()
        assert "Attached" in captured.out
        assert "global" in captured.out
        # Default priority (50) should NOT appear in output — it's noise
        assert "priority" not in captured.out

    def test_attach_with_custom_priority(
        self, tmp_path: Path, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        args = MagicMock()
        args.pack_action = "attach"
        args.ref = "test-ns/test-pack"
        args.project = False
        args.priority = 10

        from anteroom.__main__ import _run_pack

        _run_pack(tmp_path, db, args)

        captured = capsys.readouterr()
        assert "priority 10" in captured.out

    def test_attach_not_found(self, db: ThreadSafeConnection) -> None:
        args = MagicMock()
        args.pack_action = "attach"
        args.ref = "no/such-pack"
        args.project = False
        args.priority = 50

        from anteroom.__main__ import _run_pack

        with pytest.raises(SystemExit):
            _run_pack(Path("/tmp"), db, args)

    def test_attach_invalid_ref(self) -> None:
        args = MagicMock()
        args.pack_action = "attach"
        args.ref = "noslash"
        args.project = False
        args.priority = 50

        from anteroom.__main__ import _run_pack

        with pytest.raises(SystemExit):
            _run_pack(Path("/tmp"), MagicMock(), args)


class TestRunPackDetach:
    def test_detach_success(self, tmp_path: Path, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]) -> None:
        from anteroom.services.pack_attachments import attach_pack, resolve_pack_id

        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)
        pack_id = resolve_pack_id(db, "test-ns", "test-pack")
        attach_pack(db, pack_id)

        args = MagicMock()
        args.pack_action = "detach"
        args.ref = "test-ns/test-pack"
        args.project = False

        from anteroom.__main__ import _run_pack

        _run_pack(tmp_path, db, args)

        captured = capsys.readouterr()
        assert "Detached" in captured.out

    def test_detach_not_attached(
        self, tmp_path: Path, db: ThreadSafeConnection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pack_dir = _create_pack_dir(tmp_path)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        args = MagicMock()
        args.pack_action = "detach"
        args.ref = "test-ns/test-pack"
        args.project = False

        from anteroom.__main__ import _run_pack

        _run_pack(tmp_path, db, args)

        captured = capsys.readouterr()
        assert "Not attached" in captured.out


class TestRunPackRefresh:
    def test_refresh_no_config(self, capsys: pytest.CaptureFixture[str]) -> None:
        config = _make_config()
        config.pack_sources = []
        args = MagicMock()
        args.pack_action = "refresh"

        from anteroom.__main__ import _run_pack_with_config

        with patch("anteroom.db.get_db", return_value=MagicMock()):
            _run_pack_with_config(config, args)

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

        from anteroom.__main__ import _run_pack_with_config

        mock_worker = MagicMock()
        mock_worker.refresh_all.return_value = [
            SourceRefreshResult(url="https://example.com/packs.git", success=True, packs_installed=1),
        ]

        with (
            patch("anteroom.db.get_db", return_value=MagicMock()),
            patch("anteroom.services.pack_refresh.PackRefreshWorker", return_value=mock_worker),
        ):
            _run_pack_with_config(config, args)

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

        from anteroom.__main__ import _run_pack_with_config

        mock_worker = MagicMock()
        mock_worker.refresh_all.return_value = [
            SourceRefreshResult(url="https://example.com/packs.git", success=False, error="git not found"),
        ]

        with (
            patch("anteroom.db.get_db", return_value=MagicMock()),
            patch("anteroom.services.pack_refresh.PackRefreshWorker", return_value=mock_worker),
        ):
            _run_pack_with_config(config, args)

        captured = capsys.readouterr()
        assert "FAIL" in captured.out
        assert "git not found" in captured.out


# ---------------------------------------------------------------------------
# aroom pack add-source (#559)
# ---------------------------------------------------------------------------


class TestRunPackAddSource:
    def test_adds_source_to_config(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        args = MagicMock()
        args.pack_action = "add-source"
        args.url = "https://github.com/acme/packs.git"
        args.team_config = None

        config_path = tmp_path / "config.yaml"

        from anteroom.__main__ import _run_pack_dispatch

        with patch("anteroom.config._get_config_path", return_value=config_path):
            _run_pack_dispatch(args)

        captured = capsys.readouterr()
        assert "Added pack source" in captured.out

        data = yaml.safe_load(config_path.read_text())
        assert len(data["pack_sources"]) == 1
        assert data["pack_sources"][0]["url"] == "https://github.com/acme/packs.git"

    def test_rejects_http(self, capsys: pytest.CaptureFixture[str]) -> None:
        args = MagicMock()
        args.pack_action = "add-source"
        args.url = "http://example.com/packs.git"
        args.team_config = None

        from anteroom.__main__ import _run_pack_dispatch

        with pytest.raises(SystemExit):
            _run_pack_dispatch(args)

        captured = capsys.readouterr()
        assert "Plaintext HTTP" in captured.out

    def test_rejects_ext_scheme(self, capsys: pytest.CaptureFixture[str]) -> None:
        args = MagicMock()
        args.pack_action = "add-source"
        args.url = "ext::sh -c exploit"
        args.team_config = None

        from anteroom.__main__ import _run_pack_dispatch

        with pytest.raises(SystemExit):
            _run_pack_dispatch(args)

        captured = capsys.readouterr()
        assert "not allowed" in captured.out

    def test_duplicate_source_skipped(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        args = MagicMock()
        args.pack_action = "add-source"
        args.url = "https://github.com/acme/packs.git"
        args.team_config = None

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.dump({"pack_sources": [{"url": "https://github.com/acme/packs.git", "branch": "main"}]})
        )

        from anteroom.__main__ import _run_pack_dispatch

        with patch("anteroom.config._get_config_path", return_value=config_path):
            _run_pack_dispatch(args)

        captured = capsys.readouterr()
        assert "already configured" in captured.out

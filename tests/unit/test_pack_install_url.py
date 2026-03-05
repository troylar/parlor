"""Tests for pack install from git URL (issue #762).

Covers:
- is_git_url() detection
- _ensure_db_for_pack_ops() zero-config DB creation
- _run_pack_install() URL routing
- _install_from_url() clone + manifest discovery
- _install_from_path() local directory install
- --attach flag auto-attachment
- argparser changes (source, --branch, --path, --attach)
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from anteroom.services.pack_sources import is_git_url

# ---------------------------------------------------------------------------
# is_git_url
# ---------------------------------------------------------------------------


class TestIsGitUrl:
    def test_https(self) -> None:
        assert is_git_url("https://github.com/org/repo.git") is True

    def test_ssh(self) -> None:
        assert is_git_url("ssh://git@github.com/org/repo.git") is True

    def test_git_protocol(self) -> None:
        assert is_git_url("git://github.com/org/repo.git") is True

    def test_git_at_shorthand(self) -> None:
        assert is_git_url("git@github.com:org/repo.git") is True

    def test_http_matched(self) -> None:
        # http:// is detected as a URL (but rejected later by _validate_url_scheme)
        assert is_git_url("http://example.com/repo.git") is True

    def test_local_path(self) -> None:
        assert is_git_url("/home/user/my-pack") is False

    def test_relative_path(self) -> None:
        assert is_git_url("./my-pack") is False

    def test_plain_name(self) -> None:
        assert is_git_url("my-pack") is False

    def test_windows_path(self) -> None:
        assert is_git_url("C:\\Users\\pack") is False


# ---------------------------------------------------------------------------
# _ensure_db_for_pack_ops
# ---------------------------------------------------------------------------


class TestEnsureDbForPackOps:
    def test_creates_data_dir_and_db_without_config(self, tmp_path: Path) -> None:
        """When no config.yaml exists, creates ~/.anteroom/ and opens DB."""
        data_dir = tmp_path / ".anteroom"

        with (
            patch("anteroom.__main__._get_config_path", return_value=tmp_path / "config.yaml"),
            patch("anteroom.config._resolve_data_dir", return_value=data_dir),
        ):
            # Lazy import to avoid triggering module-level side effects
            from anteroom.__main__ import _ensure_db_for_pack_ops

            result_dir, db = _ensure_db_for_pack_ops()

        assert result_dir == data_dir
        assert data_dir.is_dir()
        assert (data_dir / "chat.db").exists()
        # DB is usable — can execute queries
        db.execute("SELECT 1")

    def test_uses_config_data_dir_when_config_exists(self, tmp_path: Path) -> None:
        """When config.yaml exists, use the configured data_dir."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("ai:\n  base_url: http://localhost\n")
        custom_data_dir = tmp_path / "custom-data"

        mock_config = MagicMock()
        mock_config.app.data_dir = custom_data_dir

        with (
            patch("anteroom.__main__._get_config_path", return_value=config_path),
            patch("anteroom.__main__.load_config", return_value=(mock_config, [])),
        ):
            from anteroom.__main__ import _ensure_db_for_pack_ops

            result_dir, db = _ensure_db_for_pack_ops()

        assert result_dir == custom_data_dir
        assert custom_data_dir.is_dir()

    def test_falls_back_on_config_parse_error(self, tmp_path: Path) -> None:
        """When config.yaml exists but is invalid, fall back to default data_dir."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("invalid: yaml: content: [")
        data_dir = tmp_path / ".anteroom"

        with (
            patch("anteroom.__main__._get_config_path", return_value=config_path),
            patch("anteroom.__main__.load_config", side_effect=ValueError("bad config")),
            patch("anteroom.config._resolve_data_dir", return_value=data_dir),
        ):
            from anteroom.__main__ import _ensure_db_for_pack_ops

            result_dir, db = _ensure_db_for_pack_ops()

        assert result_dir == data_dir


# ---------------------------------------------------------------------------
# _collect_pack_overlay (two-phase config load)
# ---------------------------------------------------------------------------


class TestCollectPackOverlay:
    def test_returns_none_when_no_db(self, tmp_path: Path) -> None:
        """No DB file → no overlays."""
        from anteroom.__main__ import _collect_pack_overlay

        with patch("anteroom.config._resolve_data_dir", return_value=tmp_path):
            result = _collect_pack_overlay()

        assert result is None

    def test_returns_none_when_no_active_packs(self, tmp_path: Path) -> None:
        """DB exists but no packs attached → no overlays."""
        from anteroom.__main__ import _collect_pack_overlay
        from anteroom.db import get_db

        # Create the DB
        data_dir = tmp_path / ".anteroom"
        data_dir.mkdir()
        get_db(data_dir / "chat.db")

        with patch("anteroom.config._resolve_data_dir", return_value=data_dir):
            result = _collect_pack_overlay()

        assert result is None

    def test_returns_merged_overlays(self, tmp_path: Path) -> None:
        """Packs with config overlays → merged dict returned."""
        from anteroom.__main__ import _collect_pack_overlay

        data_dir = tmp_path / ".anteroom"
        data_dir.mkdir()

        merged = {"ai": {"base_url": "http://localhost:11434/v1"}}

        with (
            patch("anteroom.config._resolve_data_dir", return_value=data_dir),
            patch("anteroom.db.get_db") as mock_get_db,
            patch(
                "anteroom.services.pack_attachments.get_active_pack_ids",
                return_value=["pack1"],
            ),
            patch(
                "anteroom.services.config_overlays.collect_pack_overlays",
                return_value=[("pack1", merged)],
            ),
            patch(
                "anteroom.services.config_overlays.merge_pack_overlays",
                return_value=merged,
            ),
        ):
            # Make the DB path "exist"
            db_path = data_dir / "chat.db"
            db_path.touch()
            result = _collect_pack_overlay()

        assert result == {"ai": {"base_url": "http://localhost:11434/v1"}}

    def test_exception_returns_none(self, tmp_path: Path) -> None:
        """Errors during overlay collection are swallowed (returns None)."""
        from anteroom.__main__ import _collect_pack_overlay

        data_dir = tmp_path / ".anteroom"
        data_dir.mkdir()
        (data_dir / "chat.db").touch()

        with (
            patch("anteroom.config._resolve_data_dir", return_value=data_dir),
            patch("anteroom.db.get_db", side_effect=RuntimeError("boom")),
        ):
            result = _collect_pack_overlay()

        assert result is None


class TestLoadConfigWithPackOverlay:
    def test_skips_init_wizard_when_pack_provides_config(self, tmp_path: Path) -> None:
        """If pack overlays provide required fields, init wizard is skipped."""
        from anteroom.__main__ import _load_config_or_exit

        config_path = tmp_path / "config.yaml"
        pack_overlay = {"ai": {"base_url": "http://localhost:11434/v1", "api_key": "test"}}

        mock_config = MagicMock()
        mock_config.app.data_dir = tmp_path

        with (
            patch("anteroom.__main__._get_config_path", return_value=config_path),
            patch("anteroom.__main__._collect_pack_overlay", return_value=pack_overlay),
            patch(
                "anteroom.__main__.load_config",
                return_value=(mock_config, []),
            ) as mock_load,
            patch("anteroom.services.compliance.validate_compliance") as mock_compliance,
        ):
            mock_compliance.return_value = MagicMock(is_compliant=True)
            _config_path, config, enforced = _load_config_or_exit()

        # load_config was called with pack_config
        mock_load.assert_called_once()
        call_kwargs = mock_load.call_args[1]
        assert call_kwargs["pack_config"] == pack_overlay

    def test_runs_init_wizard_when_no_config_and_no_overlays(self, tmp_path: Path) -> None:
        """No config file AND no pack overlays → init wizard runs."""
        from anteroom.__main__ import _load_config_or_exit

        config_path = tmp_path / "config.yaml"

        with (
            patch("anteroom.__main__._get_config_path", return_value=config_path),
            patch("anteroom.__main__._collect_pack_overlay", return_value=None),
            patch("anteroom.cli.setup.run_init_wizard", return_value=False) as mock_wizard,
            pytest.raises(SystemExit),
        ):
            _load_config_or_exit()

        mock_wizard.assert_called_once()


# ---------------------------------------------------------------------------
# _install_from_url
# ---------------------------------------------------------------------------


class TestInstallFromUrl:
    def _make_args(self, **kwargs: Any) -> argparse.Namespace:
        defaults = {
            "branch": "main",
            "subpath": None,
            "priority": 50,
            "project": False,
            "attach": False,
        }
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_git_not_available(self, tmp_path: Path) -> None:
        from anteroom.__main__ import _install_from_url

        console = MagicMock()
        args = self._make_args()
        db = MagicMock()

        with (
            patch("anteroom.services.pack_sources.check_git_available", return_value=False),
            pytest.raises(SystemExit),
        ):
            _install_from_url(tmp_path, db, args, console, "https://github.com/org/repo.git")

        console.print.assert_any_call("[red]git is not installed or not on PATH.[/red]")

    def test_clone_failure(self, tmp_path: Path) -> None:
        from anteroom.__main__ import _install_from_url
        from anteroom.services.pack_sources import PackSourceResult

        console = MagicMock()
        args = self._make_args()
        db = MagicMock()

        with (
            patch("anteroom.services.pack_sources.check_git_available", return_value=True),
            patch(
                "anteroom.services.pack_sources.clone_source",
                return_value=PackSourceResult(success=False, error="auth failed"),
            ),
            pytest.raises(SystemExit),
        ):
            _install_from_url(tmp_path, db, args, console, "https://github.com/org/repo.git")

    def test_no_manifests_found(self, tmp_path: Path) -> None:
        from anteroom.__main__ import _install_from_url
        from anteroom.services.pack_sources import PackSourceResult

        cache_path = tmp_path / "cache"
        cache_path.mkdir()

        console = MagicMock()
        args = self._make_args()
        db = MagicMock()

        with (
            patch("anteroom.services.pack_sources.check_git_available", return_value=True),
            patch(
                "anteroom.services.pack_sources.clone_source",
                return_value=PackSourceResult(success=True, path=cache_path),
            ),
            pytest.raises(SystemExit),
        ):
            _install_from_url(tmp_path, db, args, console, "https://github.com/org/repo.git")

    def test_discovers_and_installs_manifests(self, tmp_path: Path) -> None:
        from anteroom.__main__ import _install_from_url
        from anteroom.services.pack_sources import PackSourceResult
        from anteroom.services.packs import PackManifest

        cache_path = tmp_path / "cache"
        pack_dir = cache_path / "my-pack"
        pack_dir.mkdir(parents=True)
        (pack_dir / "pack.yaml").write_text("namespace: test\nname: my-pack\n")

        manifest = PackManifest(namespace="test", name="my-pack", version="1.0.0")
        install_result = {
            "id": "abc123",
            "namespace": "test",
            "name": "my-pack",
            "version": "1.0.0",
            "artifact_count": 2,
        }

        console = MagicMock()
        args = self._make_args()
        db = MagicMock()

        with (
            patch("anteroom.services.pack_sources.check_git_available", return_value=True),
            patch(
                "anteroom.services.pack_sources.clone_source",
                return_value=PackSourceResult(success=True, path=cache_path),
            ),
            patch("anteroom.services.packs.parse_manifest", return_value=manifest),
            patch("anteroom.services.packs.validate_manifest", return_value=[]),
            patch("anteroom.services.packs.install_pack", return_value=install_result) as mock_install,
        ):
            _install_from_url(tmp_path, db, args, console, "https://github.com/org/repo.git")

        mock_install.assert_called_once()

    def test_subpath_narrows_search(self, tmp_path: Path) -> None:
        from anteroom.__main__ import _install_from_url
        from anteroom.services.pack_sources import PackSourceResult
        from anteroom.services.packs import PackManifest

        cache_path = tmp_path / "cache"
        sub = cache_path / "packs" / "starter"
        sub.mkdir(parents=True)
        (sub / "pack.yaml").write_text("namespace: test\nname: starter\n")
        # Also put a pack.yaml at root — should NOT be found
        (cache_path / "pack.yaml").write_text("namespace: root\nname: root\n")

        manifest = PackManifest(namespace="test", name="starter", version="1.0.0")
        install_result = {
            "id": "abc123",
            "namespace": "test",
            "name": "starter",
            "version": "1.0.0",
            "artifact_count": 0,
        }

        console = MagicMock()
        args = self._make_args(subpath="packs/starter")
        db = MagicMock()

        with (
            patch("anteroom.services.pack_sources.check_git_available", return_value=True),
            patch(
                "anteroom.services.pack_sources.clone_source",
                return_value=PackSourceResult(success=True, path=cache_path),
            ),
            patch("anteroom.services.packs.parse_manifest", return_value=manifest),
            patch("anteroom.services.packs.validate_manifest", return_value=[]),
            patch("anteroom.services.packs.install_pack", return_value=install_result) as mock_install,
        ):
            _install_from_url(tmp_path, db, args, console, "https://github.com/org/repo.git")

        # Should only be called once (for the subpath pack, not root)
        assert mock_install.call_count == 1

    def test_attach_flag(self, tmp_path: Path) -> None:
        from anteroom.__main__ import _install_from_url
        from anteroom.services.pack_sources import PackSourceResult
        from anteroom.services.packs import PackManifest

        cache_path = tmp_path / "cache"
        cache_path.mkdir(parents=True)
        (cache_path / "pack.yaml").write_text("namespace: test\nname: pack1\n")

        manifest = PackManifest(namespace="test", name="pack1", version="1.0.0")
        install_result = {
            "id": "abc123",
            "namespace": "test",
            "name": "pack1",
            "version": "1.0.0",
            "artifact_count": 0,
        }

        console = MagicMock()
        args = self._make_args(attach=True)
        db = MagicMock()

        with (
            patch("anteroom.services.pack_sources.check_git_available", return_value=True),
            patch(
                "anteroom.services.pack_sources.clone_source",
                return_value=PackSourceResult(success=True, path=cache_path),
            ),
            patch("anteroom.services.packs.parse_manifest", return_value=manifest),
            patch("anteroom.services.packs.validate_manifest", return_value=[]),
            patch("anteroom.services.packs.install_pack", return_value=install_result),
            patch("anteroom.services.pack_attachments.attach_pack") as mock_attach,
        ):
            _install_from_url(tmp_path, db, args, console, "https://github.com/org/repo.git")

        mock_attach.assert_called_once_with(db, "abc123", priority=50)


# ---------------------------------------------------------------------------
# _install_from_path
# ---------------------------------------------------------------------------


class TestInstallFromPath:
    def _make_args(self, **kwargs: Any) -> argparse.Namespace:
        defaults = {"project": False, "attach": False, "priority": 50}
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_missing_manifest(self, tmp_path: Path) -> None:
        from anteroom.__main__ import _install_from_path

        console = MagicMock()
        args = self._make_args()
        db = MagicMock()

        with pytest.raises(SystemExit):
            _install_from_path(db, args, console, tmp_path)

    def test_successful_install(self, tmp_path: Path) -> None:
        from anteroom.__main__ import _install_from_path
        from anteroom.services.packs import PackManifest

        (tmp_path / "pack.yaml").write_text("namespace: test\nname: local\n")
        manifest = PackManifest(namespace="test", name="local", version="1.0.0")
        install_result = {
            "id": "def456",
            "namespace": "test",
            "name": "local",
            "version": "1.0.0",
            "artifact_count": 1,
        }

        console = MagicMock()
        args = self._make_args()
        db = MagicMock()

        with (
            patch("anteroom.services.packs.parse_manifest", return_value=manifest),
            patch("anteroom.services.packs.validate_manifest", return_value=[]),
            patch("anteroom.services.packs.install_pack", return_value=install_result),
        ):
            _install_from_path(db, args, console, tmp_path)

        # Check printed output
        printed = [call.args[0] for call in console.print.call_args_list]
        assert any("Installed" in p for p in printed)

    def test_attach_flag_on_local_install(self, tmp_path: Path) -> None:
        from anteroom.__main__ import _install_from_path
        from anteroom.services.packs import PackManifest

        (tmp_path / "pack.yaml").write_text("namespace: test\nname: local\n")
        manifest = PackManifest(namespace="test", name="local", version="1.0.0")
        install_result = {
            "id": "def456",
            "namespace": "test",
            "name": "local",
            "version": "1.0.0",
            "artifact_count": 1,
        }

        console = MagicMock()
        args = self._make_args(attach=True)
        db = MagicMock()

        with (
            patch("anteroom.services.packs.parse_manifest", return_value=manifest),
            patch("anteroom.services.packs.validate_manifest", return_value=[]),
            patch("anteroom.services.packs.install_pack", return_value=install_result),
            patch("anteroom.services.pack_attachments.attach_pack") as mock_attach,
        ):
            _install_from_path(db, args, console, tmp_path)

        mock_attach.assert_called_once_with(db, "def456", priority=50)


# ---------------------------------------------------------------------------
# _run_pack_dispatch routing
# ---------------------------------------------------------------------------


class TestRunPackDispatch:
    def test_routes_install_url_to_install_from_url(self) -> None:
        """Verify URL sources are routed correctly."""
        from anteroom.__main__ import _run_pack_dispatch

        args = argparse.Namespace(
            pack_action="install",
            source="https://github.com/org/repo.git",
            branch="main",
            subpath=None,
            project=False,
            attach=False,
            team_config=None,
        )

        with (
            patch("anteroom.__main__._ensure_db_for_pack_ops") as mock_db,
            patch("anteroom.__main__._run_pack") as mock_run,
        ):
            mock_db.return_value = (Path("/tmp/data"), MagicMock())
            _run_pack_dispatch(args)

        mock_run.assert_called_once()

    def test_routes_install_path_to_install_from_path(self) -> None:
        """Verify local paths are routed correctly."""
        from anteroom.__main__ import _run_pack_dispatch

        args = argparse.Namespace(
            pack_action="install",
            source="/home/user/my-pack",
            branch="main",
            subpath=None,
            project=False,
            attach=False,
            team_config=None,
        )

        with (
            patch("anteroom.__main__._ensure_db_for_pack_ops") as mock_db,
            patch("anteroom.__main__._run_pack") as mock_run,
        ):
            mock_db.return_value = (Path("/tmp/data"), MagicMock())
            _run_pack_dispatch(args)

        mock_run.assert_called_once()

    def test_sources_requires_config(self) -> None:
        """sources subcommand should go through _load_config_or_exit."""
        from anteroom.__main__ import _run_pack_dispatch

        args = argparse.Namespace(
            pack_action="sources",
            team_config=None,
        )

        mock_config = MagicMock()
        mock_config.pack_sources = []
        mock_config.app.data_dir = Path("/tmp")

        with (
            patch(
                "anteroom.__main__._load_config_or_exit",
                return_value=(Path("/tmp/config.yaml"), mock_config, []),
            ) as mock_load,
            patch("anteroom.__main__._run_pack_with_config"),
        ):
            _run_pack_dispatch(args)

        mock_load.assert_called_once()


# ---------------------------------------------------------------------------
# Argparser
# ---------------------------------------------------------------------------


class TestPackInstallArgparser:
    def test_source_positional_arg(self) -> None:
        """The install subcommand accepts a positional 'source' argument."""

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        pack = sub.add_parser("pack")
        pack_sub = pack.add_subparsers(dest="pack_action")
        install = pack_sub.add_parser("install")
        install.add_argument("source")
        install.add_argument("--attach", action="store_true")
        install.add_argument("--branch", default="main")
        install.add_argument("--path", dest="subpath", default=None)

        args = parser.parse_args(["pack", "install", "https://github.com/org/repo.git", "--attach", "--branch", "dev"])
        assert args.source == "https://github.com/org/repo.git"
        assert args.attach is True
        assert args.branch == "dev"

    def test_subpath_flag(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        pack = sub.add_parser("pack")
        pack_sub = pack.add_subparsers(dest="pack_action")
        install = pack_sub.add_parser("install")
        install.add_argument("source")
        install.add_argument("--path", dest="subpath", default=None)

        args = parser.parse_args(["pack", "install", "https://github.com/org/repo.git", "--path", "packs/starter"])
        assert args.subpath == "packs/starter"

"""Tests for anteroom.services.pack_refresh — background pack source refresh."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from anteroom.config import PackSourceConfig
from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services.pack_refresh import (
    PackRefreshWorker,
    SourceRefreshResult,
    install_from_source,
)
from anteroom.services.pack_sources import PackSourceResult

_MODULE = "anteroom.services.pack_refresh"


@pytest.fixture()
def db() -> ThreadSafeConnection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return ThreadSafeConnection(conn)


def _create_pack_in_dir(base: Path, name: str = "test-pack", namespace: str = "test-ns") -> Path:
    """Create a minimal valid pack directory with one skill artifact."""
    pack_dir = base / namespace / name
    pack_dir.mkdir(parents=True)
    (pack_dir / "skills").mkdir()
    (pack_dir / "skills" / "greet.yaml").write_text("content: Hello!\nmetadata:\n  tier: read\n", encoding="utf-8")
    manifest = {
        "name": name,
        "namespace": namespace,
        "version": "1.0.0",
        "description": "A test pack",
        "artifacts": [{"type": "skill", "name": "greet"}],
    }
    with open(pack_dir / "pack.yaml", "w", encoding="utf-8") as f:
        yaml.dump(manifest, f)
    return pack_dir


class TestInstallFromSource:
    def test_install_new_pack(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        _create_pack_in_dir(source_dir)

        result = install_from_source(db, source_dir)
        assert result.installed == 1
        assert result.updated == 0
        assert result.attached == 0

    def test_update_existing_pack(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        _create_pack_in_dir(source_dir)

        # First install
        install_from_source(db, source_dir)
        # Bump version so install_from_source detects a change
        manifest_path = source_dir / "test-ns" / "test-pack" / "pack.yaml"
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        data["version"] = "2.0.0"
        with open(manifest_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)
        # Second run should update due to version change
        result = install_from_source(db, source_dir)
        assert result.installed == 0
        assert result.updated == 1

    def test_multiple_packs(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        _create_pack_in_dir(source_dir, name="pack-a", namespace="ns")
        _create_pack_in_dir(source_dir, name="pack-b", namespace="ns")

        result = install_from_source(db, source_dir)
        assert result.installed == 2
        assert result.updated == 0

    def test_invalid_manifest_skipped(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        bad_pack = source_dir / "bad"
        bad_pack.mkdir()
        (bad_pack / "pack.yaml").write_text("not_valid: true\n", encoding="utf-8")

        result = install_from_source(db, source_dir)
        assert result.installed == 0
        assert result.updated == 0

    def test_empty_source_dir(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        source_dir = tmp_path / "source"
        source_dir.mkdir()

        result = install_from_source(db, source_dir)
        assert result.installed == 0
        assert result.updated == 0


class TestInstallFromSourceAutoAttach:
    """Tests for auto_attach and priority parameters."""

    def test_auto_attach_new_pack(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """When auto_attach=True, newly installed packs are attached."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        _create_pack_in_dir(source_dir)

        result = install_from_source(db, source_dir, auto_attach=True)
        assert result.installed == 1
        assert result.attached == 1

    def test_no_attach_when_disabled(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """When auto_attach=False, packs are installed but not attached."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        _create_pack_in_dir(source_dir)

        result = install_from_source(db, source_dir, auto_attach=False)
        assert result.installed == 1
        assert result.attached == 0

    def test_default_no_attach(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Default auto_attach=False keeps backward compatibility."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        _create_pack_in_dir(source_dir)

        result = install_from_source(db, source_dir)
        assert result.attached == 0

    def test_attach_conflict_warns_but_succeeds(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Install succeeds even if auto-attach raises ValueError (conflict)."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        _create_pack_in_dir(source_dir)

        with patch("anteroom.services.pack_attachments.attach_pack", side_effect=ValueError("conflict")):
            result = install_from_source(db, source_dir, auto_attach=True)

        assert result.installed == 1
        assert result.attached == 0  # attach failed, but install succeeded

    def test_skips_attach_on_update(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Updates don't re-attach — attachment persists across updates."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        _create_pack_in_dir(source_dir)

        # First install with auto-attach
        install_from_source(db, source_dir, auto_attach=True)

        # Bump version
        manifest_path = source_dir / "test-ns" / "test-pack" / "pack.yaml"
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        data["version"] = "2.0.0"
        with open(manifest_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

        # Update should not attempt attach again
        result = install_from_source(db, source_dir, auto_attach=True)
        assert result.updated == 1
        assert result.attached == 0

    def test_custom_priority(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Priority is passed through to attach_pack."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        _create_pack_in_dir(source_dir)

        with patch("anteroom.services.pack_attachments.attach_pack") as mock_attach:
            install_from_source(db, source_dir, auto_attach=True, priority=10)
            mock_attach.assert_called_once()
            assert mock_attach.call_args.kwargs["priority"] == 10

    def test_idempotent_attach(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Running install_from_source twice with auto_attach doesn't duplicate."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        _create_pack_in_dir(source_dir)

        # First run installs and attaches
        r1 = install_from_source(db, source_dir, auto_attach=True)
        assert r1.installed == 1
        assert r1.attached == 1

        # Remove source dir and recreate with new version to force re-scan
        import shutil

        shutil.rmtree(source_dir)
        source_dir.mkdir()
        _create_pack_in_dir(source_dir)

        # Second run — same version, skips install entirely
        r2 = install_from_source(db, source_dir, auto_attach=True)
        assert r2.installed == 0
        assert r2.attached == 0


class TestSourceRefreshResult:
    def test_defaults(self) -> None:
        r = SourceRefreshResult(url="https://example.com/repo.git", success=True)
        assert r.packs_updated == 0
        assert r.packs_installed == 0
        assert r.packs_attached == 0
        assert r.error == ""
        assert r.changed is False


class TestPackRefreshWorkerConstruction:
    def test_separates_auto_and_manual_sources(self) -> None:
        sources = [
            PackSourceConfig(url="https://a.com/repo.git", refresh_interval=30),
            PackSourceConfig(url="https://b.com/repo.git", refresh_interval=0),
            PackSourceConfig(url="https://c.com/repo.git", refresh_interval=15),
        ]
        worker = PackRefreshWorker(db=MagicMock(), data_dir=Path("/tmp"), sources=sources)
        # Only auto-refresh sources (interval > 0) go in the polling loop
        assert len(worker._sources) == 2
        assert len(worker._manual_sources) == 1
        assert worker._manual_sources[0].url == "https://b.com/repo.git"

    def test_empty_sources(self) -> None:
        worker = PackRefreshWorker(db=MagicMock(), data_dir=Path("/tmp"), sources=[])
        assert len(worker._sources) == 0
        assert not worker.running

    def test_accepts_callback_and_event_loop(self) -> None:
        cb = MagicMock()
        loop = MagicMock()
        worker = PackRefreshWorker(
            db=MagicMock(), data_dir=Path("/tmp"), sources=[], on_packs_changed=cb, event_loop=loop
        )
        assert worker._on_packs_changed is cb
        assert worker._event_loop is loop


class TestPackRefreshWorkerRefreshSource:
    def test_success_no_changes(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        source = PackSourceConfig(url="https://example.com/repo.git")
        worker = PackRefreshWorker(db=db, data_dir=tmp_path, sources=[source])

        # ensure_source succeeds, cache dir is empty (no packs)
        cache_path = tmp_path / "cache" / "sources" / "abc123"
        cache_path.mkdir(parents=True)

        with (
            patch(f"{_MODULE}.ensure_source", return_value=PackSourceResult(success=True, path=cache_path)),
            patch(f"{_MODULE}.resolve_cache_path", return_value=cache_path),
        ):
            result = worker.refresh_source(source)

        assert result.success
        assert result.packs_installed == 0
        assert result.packs_updated == 0
        assert result.packs_attached == 0

    def test_ensure_source_failure(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        source = PackSourceConfig(url="https://example.com/repo.git")
        worker = PackRefreshWorker(db=db, data_dir=tmp_path, sources=[source])

        with patch(
            f"{_MODULE}.ensure_source",
            return_value=PackSourceResult(success=False, error="git clone failed"),
        ):
            result = worker.refresh_source(source)

        assert not result.success
        assert "git clone failed" in result.error

    def test_success_with_new_pack(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        source = PackSourceConfig(url="https://example.com/repo.git")
        worker = PackRefreshWorker(db=db, data_dir=tmp_path, sources=[source])

        cache_path = tmp_path / "cache" / "sources" / "abc123"
        cache_path.mkdir(parents=True)
        _create_pack_in_dir(cache_path)

        with (
            patch(
                f"{_MODULE}.ensure_source",
                return_value=PackSourceResult(success=True, path=cache_path, changed=True),
            ),
            patch(f"{_MODULE}.resolve_cache_path", return_value=cache_path),
        ):
            result = worker.refresh_source(source)

        assert result.success
        assert result.packs_installed == 1
        assert result.changed

    def test_auto_attach_passed_through(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """refresh_source passes auto_attach and priority from source config."""
        source = PackSourceConfig(url="https://example.com/repo.git", auto_attach=True, priority=10)
        worker = PackRefreshWorker(db=db, data_dir=tmp_path, sources=[source])

        cache_path = tmp_path / "cache" / "sources" / "abc123"
        cache_path.mkdir(parents=True)
        _create_pack_in_dir(cache_path)

        with (
            patch(
                f"{_MODULE}.ensure_source",
                return_value=PackSourceResult(success=True, path=cache_path, changed=True),
            ),
            patch(f"{_MODULE}.resolve_cache_path", return_value=cache_path),
        ):
            result = worker.refresh_source(source)

        assert result.packs_installed == 1
        assert result.packs_attached == 1


class TestPackRefreshWorkerRefreshAll:
    def test_refresh_all_includes_manual_sources(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        sources = [
            PackSourceConfig(url="https://a.com/repo.git", refresh_interval=30),
            PackSourceConfig(url="https://b.com/repo.git", refresh_interval=0),
        ]
        worker = PackRefreshWorker(db=db, data_dir=tmp_path, sources=sources)

        cache_path = tmp_path / "cache" / "sources" / "abc123"
        cache_path.mkdir(parents=True)

        with (
            patch(
                f"{_MODULE}.ensure_source",
                return_value=PackSourceResult(success=True, path=cache_path),
            ),
            patch(f"{_MODULE}.resolve_cache_path", return_value=cache_path),
        ):
            results = worker.refresh_all()

        assert len(results) == 2


class TestPackRefreshWorkerRunOnce:
    @pytest.mark.asyncio()
    async def test_run_once_refreshes_due_sources(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        sources = [PackSourceConfig(url="https://a.com/repo.git", refresh_interval=5)]
        worker = PackRefreshWorker(db=db, data_dir=tmp_path, sources=sources)

        cache_path = tmp_path / "cache" / "sources" / "abc123"
        cache_path.mkdir(parents=True)

        with (
            patch(
                f"{_MODULE}.ensure_source",
                return_value=PackSourceResult(success=True, path=cache_path),
            ),
            patch(f"{_MODULE}.resolve_cache_path", return_value=cache_path),
        ):
            results = await worker.run_once()

        assert len(results) == 1

    @pytest.mark.asyncio()
    async def test_run_once_skips_not_due(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        sources = [PackSourceConfig(url="https://a.com/repo.git", refresh_interval=5)]
        worker = PackRefreshWorker(db=db, data_dir=tmp_path, sources=sources)

        cache_path = tmp_path / "cache" / "sources" / "abc123"
        cache_path.mkdir(parents=True)

        with (
            patch(
                f"{_MODULE}.ensure_source",
                return_value=PackSourceResult(success=True, path=cache_path),
            ),
            patch(f"{_MODULE}.resolve_cache_path", return_value=cache_path),
        ):
            # First run marks as refreshed
            await worker.run_once()
            # Second run should skip (not enough time elapsed)
            results = await worker.run_once()

        assert len(results) == 0

    @pytest.mark.asyncio()
    async def test_run_once_handles_failure_gracefully(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        sources = [PackSourceConfig(url="https://a.com/repo.git", refresh_interval=5)]
        worker = PackRefreshWorker(db=db, data_dir=tmp_path, sources=sources)

        with patch(f"{_MODULE}.ensure_source", side_effect=RuntimeError("network error")):
            results = await worker.run_once()

        assert len(results) == 1
        assert not results[0].success
        assert "network error" in results[0].error


class TestPackRefreshWorkerCallback:
    """Tests for the on_packs_changed callback."""

    @pytest.mark.asyncio()
    async def test_callback_fires_on_packs_installed(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Callback fires when packs are installed."""
        cb = MagicMock()
        sources = [PackSourceConfig(url="https://a.com/repo.git", refresh_interval=5)]
        worker = PackRefreshWorker(db=db, data_dir=tmp_path, sources=sources, on_packs_changed=cb)

        cache_path = tmp_path / "cache" / "sources" / "abc123"
        cache_path.mkdir(parents=True)
        _create_pack_in_dir(cache_path)

        with (
            patch(
                f"{_MODULE}.ensure_source",
                return_value=PackSourceResult(success=True, path=cache_path, changed=True),
            ),
            patch(f"{_MODULE}.resolve_cache_path", return_value=cache_path),
        ):
            await worker.run_once()

        cb.assert_called_once()

    @pytest.mark.asyncio()
    async def test_no_callback_when_unchanged(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Callback does NOT fire when nothing changed."""
        cb = MagicMock()
        sources = [PackSourceConfig(url="https://a.com/repo.git", refresh_interval=5)]
        worker = PackRefreshWorker(db=db, data_dir=tmp_path, sources=sources, on_packs_changed=cb)

        cache_path = tmp_path / "cache" / "sources" / "abc123"
        cache_path.mkdir(parents=True)
        # Empty dir — no packs to install

        with (
            patch(
                f"{_MODULE}.ensure_source",
                return_value=PackSourceResult(success=True, path=cache_path),
            ),
            patch(f"{_MODULE}.resolve_cache_path", return_value=cache_path),
        ):
            await worker.run_once()

        cb.assert_not_called()

    @pytest.mark.asyncio()
    async def test_callback_via_event_loop(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """When event_loop is provided, callback is scheduled via call_soon_threadsafe."""
        cb = MagicMock()
        mock_loop = MagicMock()
        sources = [PackSourceConfig(url="https://a.com/repo.git", refresh_interval=5)]
        worker = PackRefreshWorker(db=db, data_dir=tmp_path, sources=sources, on_packs_changed=cb, event_loop=mock_loop)

        cache_path = tmp_path / "cache" / "sources" / "abc123"
        cache_path.mkdir(parents=True)
        _create_pack_in_dir(cache_path)

        with (
            patch(
                f"{_MODULE}.ensure_source",
                return_value=PackSourceResult(success=True, path=cache_path, changed=True),
            ),
            patch(f"{_MODULE}.resolve_cache_path", return_value=cache_path),
        ):
            await worker.run_once()

        mock_loop.call_soon_threadsafe.assert_called_once_with(cb)
        cb.assert_not_called()  # Not called directly — scheduled on loop


class TestPackRefreshWorkerBackoff:
    def test_is_due_applies_backoff_on_failures(self, tmp_path: Path) -> None:
        """After failures, _is_due should require longer intervals."""
        source = PackSourceConfig(url="https://a.com/repo.git", refresh_interval=5)
        worker = PackRefreshWorker(db=MagicMock(), data_dir=tmp_path, sources=[source])
        state = worker._sources[0]

        # Simulate a refresh that just happened
        state.last_refreshed = time.monotonic()
        state.consecutive_failures = 0
        assert not worker._is_due(state)

        # With 0 failures, due after 5*60 = 300s
        state.last_refreshed = time.monotonic() - 301
        assert worker._is_due(state)

        # With 3 failures, interval = 300 * 2^3 = 2400s (capped at MAX_INTERVAL=7200)
        state.last_refreshed = time.monotonic() - 301
        state.consecutive_failures = 3
        assert not worker._is_due(state)  # 301s < 2400s

        # After enough time with 3 failures (2400s)
        state.last_refreshed = time.monotonic() - 2401
        assert worker._is_due(state)

    def test_backoff_caps_at_max_interval(self, tmp_path: Path) -> None:
        """Backoff interval should never exceed MAX_INTERVAL (7200s)."""
        source = PackSourceConfig(url="https://a.com/repo.git", refresh_interval=5)
        worker = PackRefreshWorker(db=MagicMock(), data_dir=tmp_path, sources=[source])
        state = worker._sources[0]

        # With 20 failures, the raw backoff would be 300 * 2^20 = huge, but capped at 7200
        state.consecutive_failures = 20
        state.last_refreshed = time.monotonic() - 7201
        assert worker._is_due(state)

        state.last_refreshed = time.monotonic() - 7199
        assert not worker._is_due(state)

    def test_no_backoff_on_success(self, tmp_path: Path) -> None:
        """With 0 consecutive failures, normal interval applies."""
        source = PackSourceConfig(url="https://a.com/repo.git", refresh_interval=5)
        worker = PackRefreshWorker(db=MagicMock(), data_dir=tmp_path, sources=[source])
        state = worker._sources[0]
        state.consecutive_failures = 0
        state.last_refreshed = time.monotonic() - 301  # 5 min + 1s
        assert worker._is_due(state)


class TestPackRefreshWorkerLifecycle:
    def test_start_sets_task(self, tmp_path: Path) -> None:
        worker = PackRefreshWorker(db=MagicMock(), data_dir=tmp_path, sources=[])
        with patch.object(worker, "run_forever", new_callable=AsyncMock):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._start_and_check(worker))
            finally:
                loop.close()

    async def _start_and_check(self, worker: PackRefreshWorker) -> None:
        with patch.object(worker, "run_forever", new_callable=AsyncMock):
            worker.start()
            assert worker._task is not None
            worker.stop()
            assert not worker.running

    def test_stop_without_start(self, tmp_path: Path) -> None:
        worker = PackRefreshWorker(db=MagicMock(), data_dir=tmp_path, sources=[])
        worker.stop()
        assert not worker.running

    @pytest.mark.asyncio()
    async def test_run_forever_stops_on_flag(self, tmp_path: Path) -> None:
        worker = PackRefreshWorker(db=MagicMock(), data_dir=tmp_path, sources=[])
        worker._poll_interval = 0.01

        async def stop_soon() -> None:
            await asyncio.sleep(0.05)
            worker.stop()

        await asyncio.gather(worker.run_forever(), stop_soon())
        assert not worker.running

"""Tests for anteroom.services.pack_refresh — background pack source refresh."""

from __future__ import annotations

import sqlite3
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

        installed, updated = install_from_source(db, source_dir)
        assert installed == 1
        assert updated == 0

    def test_update_existing_pack(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        _create_pack_in_dir(source_dir)

        # First install
        install_from_source(db, source_dir)
        # Second run should update
        installed, updated = install_from_source(db, source_dir)
        assert installed == 0
        assert updated == 1

    def test_multiple_packs(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        _create_pack_in_dir(source_dir, name="pack-a", namespace="ns")
        _create_pack_in_dir(source_dir, name="pack-b", namespace="ns")

        installed, updated = install_from_source(db, source_dir)
        assert installed == 2
        assert updated == 0

    def test_invalid_manifest_skipped(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        bad_pack = source_dir / "bad"
        bad_pack.mkdir()
        (bad_pack / "pack.yaml").write_text("not_valid: true\n", encoding="utf-8")

        installed, updated = install_from_source(db, source_dir)
        assert installed == 0
        assert updated == 0

    def test_empty_source_dir(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        source_dir = tmp_path / "source"
        source_dir.mkdir()

        installed, updated = install_from_source(db, source_dir)
        assert installed == 0
        assert updated == 0


class TestSourceRefreshResult:
    def test_defaults(self) -> None:
        r = SourceRefreshResult(url="https://example.com/repo.git", success=True)
        assert r.packs_updated == 0
        assert r.packs_installed == 0
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


class TestPackRefreshWorkerLifecycle:
    def test_start_sets_task(self, tmp_path: Path) -> None:
        worker = PackRefreshWorker(db=MagicMock(), data_dir=tmp_path, sources=[])
        with patch.object(worker, "run_forever", new_callable=AsyncMock):
            import asyncio

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
            import asyncio

            await asyncio.sleep(0.05)
            worker.stop()

        import asyncio

        await asyncio.gather(worker.run_forever(), stop_soon())
        assert not worker.running

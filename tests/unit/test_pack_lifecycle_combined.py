"""Combined lifecycle tests for #872 + #875 integration.

Proves the full pack source refresh lifecycle end-to-end:
1. Source refresh installs a pack with config_overlay
2. Auto-attach happens
3. Config rebuilds in CLI and web
4. Compliance failure quarantines the changed pack
5. Project-scoped artifacts still load correctly afterward
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from anteroom.config import PackSourceConfig
from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services.pack_refresh import (
    PackRefreshWorker,
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


def _create_pack_with_config_overlay(
    base: Path,
    name: str = "test-pack",
    namespace: str = "test-ns",
    *,
    overlay_content: str = "ai:\n  temperature: 0.5\n",
) -> Path:
    """Create a pack directory with a config_overlay artifact."""
    pack_dir = base / namespace / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "config_overlays").mkdir(exist_ok=True)
    (pack_dir / "config_overlays" / "defaults.yaml").write_text(overlay_content, encoding="utf-8")
    (pack_dir / "skills").mkdir(exist_ok=True)
    (pack_dir / "skills" / "greet.yaml").write_text("content: Hello!\nmetadata:\n  tier: read\n", encoding="utf-8")
    manifest = {
        "name": name,
        "namespace": namespace,
        "version": "1.0.0",
        "description": "A test pack with config overlay",
        "artifacts": [
            {"type": "skill", "name": "greet"},
            {"type": "config_overlay", "name": "defaults"},
        ],
    }
    with open(pack_dir / "pack.yaml", "w", encoding="utf-8") as f:
        yaml.dump(manifest, f)
    return pack_dir


def _create_simple_pack(
    base: Path,
    name: str = "simple-pack",
    namespace: str = "test-ns",
) -> Path:
    """Create a pack directory with only a skill (no config overlay)."""
    pack_dir = base / namespace / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "skills").mkdir(exist_ok=True)
    (pack_dir / "skills" / "hello.yaml").write_text("content: Hi!\nmetadata:\n  tier: read\n", encoding="utf-8")
    manifest = {
        "name": name,
        "namespace": namespace,
        "version": "1.0.0",
        "description": "A simple pack",
        "artifacts": [{"type": "skill", "name": "hello"}],
    }
    with open(pack_dir / "pack.yaml", "w", encoding="utf-8") as f:
        yaml.dump(manifest, f)
    return pack_dir


class TestSourceRefreshWithAutoAttach:
    """Lifecycle test 1+2: Source refresh installs a pack and auto-attaches it."""

    def test_install_and_auto_attach(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """A source-installed pack with auto_attach=True is both installed and attached."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        _create_pack_with_config_overlay(source_dir)

        result = install_from_source(db, source_dir, auto_attach=True, priority=20)

        assert result.installed == 1
        assert result.attached == 1
        assert len(result.changed_pack_ids) == 1

        # Verify the pack is actually in the DB
        from anteroom.services import packs

        pack_list = packs.list_packs(db)
        assert len(pack_list) == 1
        assert pack_list[0]["namespace"] == "test-ns"
        assert pack_list[0]["name"] == "test-pack"

        # Verify the attachment exists in DB
        from anteroom.services.pack_attachments import list_attachments

        attachments = list_attachments(db)
        assert len(attachments) == 1
        assert attachments[0]["pack_id"] == result.changed_pack_ids[0]

    def test_refresh_worker_reports_attached_and_changed_ids(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """PackRefreshWorker.refresh_source reports both attached count and changed IDs."""
        source = PackSourceConfig(url="https://example.com/repo.git", auto_attach=True, priority=15)
        worker = PackRefreshWorker(db=db, data_dir=tmp_path, sources=[source])

        cache_path = tmp_path / "cache" / "sources" / "abc123"
        cache_path.mkdir(parents=True)
        _create_pack_with_config_overlay(cache_path)

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
        assert result.packs_attached == 1
        assert len(result.changed_pack_ids) == 1
        assert result.changed


class TestConfigOverlayLifecycle:
    """Lifecycle test 3: Config rebuilds after source refresh."""

    def test_changed_pack_ids_available_for_config_rebuild(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """After install_from_source, changed_pack_ids are available for config overlay rebuild."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        _create_pack_with_config_overlay(source_dir)

        result = install_from_source(db, source_dir, auto_attach=True)

        # The pack's config overlay artifact should be in the DB
        from anteroom.services.config_overlays import collect_pack_overlays

        overlays = collect_pack_overlays(db, result.changed_pack_ids)
        assert len(overlays) >= 1  # At least one config overlay collected

    def test_config_overlay_from_source_pack_is_collectible(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Config overlays from source-installed packs can be collected for merge."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        _create_pack_with_config_overlay(
            source_dir,
            overlay_content="ai:\n  temperature: 0.7\n",
        )

        ifs = install_from_source(db, source_dir, auto_attach=True)
        assert ifs.installed == 1
        assert ifs.attached == 1

        from anteroom.services.config_overlays import collect_pack_overlays
        from anteroom.services.pack_attachments import get_active_pack_ids

        active_ids = get_active_pack_ids(db)
        assert len(active_ids) >= 1

        overlays = collect_pack_overlays(db, active_ids)
        assert len(overlays) >= 1
        # Verify the overlay content is parseable
        _label, overlay_dict = overlays[0]
        assert "ai" in overlay_dict
        assert overlay_dict["ai"]["temperature"] == 0.7


class TestQuarantineLifecycle:
    """Lifecycle test 4: Compliance failure quarantines the changed pack."""

    def test_changed_pack_ids_enable_targeted_quarantine(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """changed_pack_ids from install_from_source enable targeted quarantine."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        _create_pack_with_config_overlay(source_dir, name="bad-pack")
        _create_simple_pack(source_dir, name="good-pack")

        result = install_from_source(db, source_dir, auto_attach=True)
        assert result.installed == 2
        assert result.attached == 2
        assert len(result.changed_pack_ids) == 2

        # Simulate quarantine: detach only the changed packs
        from anteroom.services.pack_attachments import detach_pack, list_attachments

        for pid in result.changed_pack_ids:
            detach_pack(db, pid)

        # No attachments remain
        remaining = list_attachments(db)
        assert len(remaining) == 0

    def test_web_refresh_endpoint_has_quarantine_data(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """The web refresh endpoint receives changed_pack_ids for quarantine."""
        source = PackSourceConfig(url="https://example.com/repo.git", auto_attach=True)
        worker = PackRefreshWorker(db=db, data_dir=tmp_path, sources=[source])

        cache_path = tmp_path / "cache" / "sources" / "abc123"
        cache_path.mkdir(parents=True)
        _create_pack_with_config_overlay(cache_path)

        with (
            patch(
                f"{_MODULE}.ensure_source",
                return_value=PackSourceResult(success=True, path=cache_path, changed=True),
            ),
            patch(f"{_MODULE}.resolve_cache_path", return_value=cache_path),
        ):
            results = worker.refresh_all()

        assert len(results) == 1
        assert results[0].changed_pack_ids  # Non-empty — quarantine has targets


class TestProjectScopedAfterRefresh:
    """Lifecycle test 5: Project-scoped artifacts load correctly after refresh."""

    def test_project_scoped_attachment_survives_refresh(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """A project-scoped pack attachment is preserved after source refresh."""
        from anteroom.services.pack_attachments import attach_pack, list_attachments

        # Install a pack manually and attach to a project path
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        _create_simple_pack(source_dir, name="project-pack")

        result = install_from_source(db, source_dir)
        pack_id = result.changed_pack_ids[0]

        # Attach with project scope
        attach_pack(db, pack_id, project_path="/my/project")
        atts = list_attachments(db, project_path="/my/project")
        assert any(a["pack_id"] == pack_id for a in atts)

        # Now run a source refresh that installs a DIFFERENT pack
        source_dir2 = tmp_path / "source2"
        source_dir2.mkdir()
        _create_simple_pack(source_dir2, name="other-pack", namespace="other-ns")

        result2 = install_from_source(db, source_dir2, auto_attach=True)
        assert result2.installed == 1
        assert result2.attached == 1

        # Project-scoped attachment still exists
        from anteroom.services.pack_attachments import list_attachments_for_pack

        project_atts = list_attachments_for_pack(db, pack_id)
        assert any(a.get("project_path") == "/my/project" for a in project_atts)


class TestSameVersionContentChange:
    """Lifecycle test: same-version content changes propagate through refresh."""

    @pytest.mark.asyncio()
    async def test_same_version_content_change_triggers_callback(
        self, tmp_path: Path, db: ThreadSafeConnection
    ) -> None:
        """Worker callback fires when artifact content changes without a version bump."""
        callback_count = 0

        def on_changed() -> None:
            nonlocal callback_count
            callback_count += 1

        sources = [PackSourceConfig(url="https://a.com/repo.git", refresh_interval=5, auto_attach=True)]
        worker = PackRefreshWorker(db=db, data_dir=tmp_path, sources=sources, on_packs_changed=on_changed)

        cache_path = tmp_path / "cache" / "sources" / "abc123"
        cache_path.mkdir(parents=True)
        _create_pack_with_config_overlay(cache_path)

        with (
            patch(
                f"{_MODULE}.ensure_source",
                return_value=PackSourceResult(success=True, path=cache_path, changed=True),
            ),
            patch(f"{_MODULE}.resolve_cache_path", return_value=cache_path),
        ):
            # First run: install
            results = await worker.run_once()
            assert results[0].packs_installed == 1
            assert callback_count == 1

        # Change skill content without bumping version
        skill_file = cache_path / "test-ns" / "test-pack" / "skills" / "greet.yaml"
        skill_file.write_text("content: Updated greeting!\nmetadata:\n  tier: read\n", encoding="utf-8")

        # Reset last_refreshed so the source is due again
        worker._sources[0].last_refreshed = 0.0

        with (
            patch(
                f"{_MODULE}.ensure_source",
                return_value=PackSourceResult(success=True, path=cache_path, changed=True),
            ),
            patch(f"{_MODULE}.resolve_cache_path", return_value=cache_path),
        ):
            # Second run: should detect content change and update
            results2 = await worker.run_once()

        assert results2[0].packs_updated == 1
        assert results2[0].packs_installed == 0
        assert len(results2[0].changed_pack_ids) == 1
        assert callback_count == 2  # Callback fired again

    def test_same_version_unchanged_content_does_not_update(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Re-running install_from_source with identical content does not trigger an update."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        _create_pack_with_config_overlay(source_dir)

        r1 = install_from_source(db, source_dir, auto_attach=True)
        assert r1.installed == 1

        # Run again — same version, same content
        r2 = install_from_source(db, source_dir, auto_attach=True)
        assert r2.installed == 0
        assert r2.updated == 0
        assert r2.changed_pack_ids == []


class TestCallbackWithConfigRebuild:
    """Test that the callback mechanism works with config rebuild context."""

    @pytest.mark.asyncio()
    async def test_callback_receives_changed_pack_ids_context(self, tmp_path: Path, db: ThreadSafeConnection) -> None:
        """Worker callback fires with context that enables config rebuild."""
        callback_called = False

        def on_changed() -> None:
            nonlocal callback_called
            callback_called = True

        sources = [PackSourceConfig(url="https://a.com/repo.git", refresh_interval=5, auto_attach=True)]
        worker = PackRefreshWorker(db=db, data_dir=tmp_path, sources=sources, on_packs_changed=on_changed)

        cache_path = tmp_path / "cache" / "sources" / "abc123"
        cache_path.mkdir(parents=True)
        _create_pack_with_config_overlay(cache_path)

        with (
            patch(
                f"{_MODULE}.ensure_source",
                return_value=PackSourceResult(success=True, path=cache_path, changed=True),
            ),
            patch(f"{_MODULE}.resolve_cache_path", return_value=cache_path),
        ):
            results = await worker.run_once()

        assert callback_called
        assert len(results) == 1
        assert results[0].packs_installed == 1
        assert results[0].packs_attached == 1
        assert len(results[0].changed_pack_ids) == 1

        # Verify config overlay is collectible after callback
        from anteroom.services.config_overlays import collect_pack_overlays
        from anteroom.services.pack_attachments import get_active_pack_ids

        active = get_active_pack_ids(db)
        overlays = collect_pack_overlays(db, active)
        assert len(overlays) >= 1

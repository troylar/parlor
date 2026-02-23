"""Tests for config file watcher."""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from anteroom.services.config_watcher import ConfigWatcher


def _r(p: str | Path) -> Path:
    return Path(p).resolve()


class TestConfigWatcher:
    def test_init_snapshots_mtimes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            cfg = base / "config.yaml"
            cfg.write_text(yaml.dump({"ai": {"model": "gpt-4"}}))

            watcher = ConfigWatcher([cfg], MagicMock())
            assert cfg.resolve() in watcher._mtimes

    def test_init_skips_missing_files(self) -> None:
        watcher = ConfigWatcher([Path("/nonexistent/config.yaml")], MagicMock())
        assert len(watcher._mtimes) == 0

    def test_watching_property(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            cfg = base / "config.yaml"
            cfg.write_text("ai:\n  model: gpt-4\n")

            watcher = ConfigWatcher([cfg], MagicMock())
            assert cfg.resolve() in watcher.watching

    def test_add_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            cfg1 = base / "a.yaml"
            cfg2 = base / "b.yaml"
            cfg1.write_text("ai:\n  model: gpt-4\n")
            cfg2.write_text("ai:\n  model: llama3\n")

            watcher = ConfigWatcher([cfg1], MagicMock())
            assert len(watcher.watching) == 1

            watcher.add_path(cfg2)
            assert len(watcher.watching) == 2

    def test_add_path_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            cfg = base / "config.yaml"
            cfg.write_text("ai:\n  model: gpt-4\n")

            watcher = ConfigWatcher([cfg], MagicMock())
            watcher.add_path(cfg)
            assert len(watcher.watching) == 1

    @pytest.mark.asyncio
    async def test_detects_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            cfg = base / "config.yaml"
            cfg.write_text(yaml.dump({"ai": {"model": "gpt-4"}}))

            callback = AsyncMock()
            watcher = ConfigWatcher([cfg], callback, interval=0.1)

            # Ensure mtime changes
            time.sleep(0.05)
            cfg.write_text(yaml.dump({"ai": {"model": "llama3"}}))

            await watcher.start()
            await asyncio.sleep(0.3)
            await watcher.stop()

            assert callback.call_count >= 1
            call_path, call_raw = callback.call_args[0]
            assert call_path == cfg.resolve()
            assert call_raw["ai"]["model"] == "llama3"

    @pytest.mark.asyncio
    async def test_no_change_no_callback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            cfg = base / "config.yaml"
            cfg.write_text(yaml.dump({"ai": {"model": "gpt-4"}}))

            callback = AsyncMock()
            watcher = ConfigWatcher([cfg], callback, interval=0.1)

            await watcher.start()
            await asyncio.sleep(0.3)
            await watcher.stop()

            callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_invalid_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            cfg = base / "config.yaml"
            cfg.write_text(yaml.dump({"ai": {"model": "gpt-4"}}))

            callback = AsyncMock()
            watcher = ConfigWatcher([cfg], callback, interval=0.1)

            time.sleep(0.05)
            cfg.write_text("not: valid: yaml: [[[")

            await watcher.start()
            await asyncio.sleep(0.3)
            await watcher.stop()

            callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_non_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            cfg = base / "config.yaml"
            cfg.write_text(yaml.dump({"ai": {"model": "gpt-4"}}))

            callback = AsyncMock()
            watcher = ConfigWatcher([cfg], callback, interval=0.1)

            time.sleep(0.05)
            cfg.write_text("- just\n- a\n- list\n")

            await watcher.start()
            await asyncio.sleep(0.3)
            await watcher.stop()

            callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_validation_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            cfg = base / "config.yaml"
            cfg.write_text(yaml.dump({"ai": {"model": "gpt-4"}}))

            callback = AsyncMock()
            watcher = ConfigWatcher([cfg], callback, interval=0.1)

            time.sleep(0.05)
            # MCP servers with missing name is an error-level validation issue
            cfg.write_text(
                yaml.dump(
                    {
                        "mcp_servers": [{"transport": "stdio"}],
                    }
                )
            )

            await watcher.start()
            await asyncio.sleep(0.3)
            await watcher.stop()

            callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_accepts_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            cfg = base / "config.yaml"
            cfg.write_text(yaml.dump({"ai": {"model": "gpt-4"}}))

            callback = AsyncMock()
            watcher = ConfigWatcher([cfg], callback, interval=0.1)

            time.sleep(0.05)
            # Unknown key is a warning, not an error
            cfg.write_text(yaml.dump({"ai": {"model": "llama3"}, "unknown_section": True}))

            await watcher.start()
            await asyncio.sleep(0.3)
            await watcher.stop()

            assert callback.call_count >= 1

    @pytest.mark.asyncio
    async def test_sync_callback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            cfg = base / "config.yaml"
            cfg.write_text(yaml.dump({"ai": {"model": "gpt-4"}}))

            calls: list[tuple] = []

            def sync_cb(path, raw):
                calls.append((path, raw))

            watcher = ConfigWatcher([cfg], sync_cb, interval=0.1)

            time.sleep(0.05)
            cfg.write_text(yaml.dump({"ai": {"model": "llama3"}}))

            await watcher.start()
            await asyncio.sleep(0.3)
            await watcher.stop()

            assert len(calls) >= 1

    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        watcher = ConfigWatcher([], MagicMock(), interval=0.1)
        await watcher.start()
        assert watcher._task is not None
        await watcher.stop()
        assert watcher._task is None

    @pytest.mark.asyncio
    async def test_start_idempotent(self) -> None:
        watcher = ConfigWatcher([], MagicMock(), interval=0.1)
        await watcher.start()
        task1 = watcher._task
        await watcher.start()
        assert watcher._task is task1
        await watcher.stop()

    @pytest.mark.asyncio
    async def test_skips_nonexistent_during_poll(self) -> None:
        callback = AsyncMock()
        watcher = ConfigWatcher([Path("/nonexistent/config.yaml")], callback, interval=0.1)

        await watcher.start()
        await asyncio.sleep(0.3)
        await watcher.stop()

        callback.assert_not_called()

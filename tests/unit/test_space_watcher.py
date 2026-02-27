"""Tests for SpaceFileWatcher."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
import yaml

from anteroom.services.space_watcher import SpaceFileWatcher


@pytest.mark.asyncio
async def test_detects_mtime_change(tmp_path: Path) -> None:
    """Watcher detects file modification and calls callback."""
    space_file = tmp_path / "space.yaml"
    space_file.write_text(yaml.dump({"name": "s1", "version": "1"}), encoding="utf-8")

    received: list[dict] = []

    def on_change(raw: dict) -> None:
        received.append(raw)

    watcher = SpaceFileWatcher(space_file, on_change, interval=0.1)

    # Force mtime change
    time.sleep(1.1)
    space_file.write_text(yaml.dump({"name": "s1", "version": "2"}), encoding="utf-8")

    await watcher.start()
    await asyncio.sleep(2.0)
    await watcher.stop()

    assert len(received) >= 1
    assert received[0]["name"] == "s1"
    assert received[0]["version"] == "2"


@pytest.mark.asyncio
async def test_invalid_yaml_ignored(tmp_path: Path) -> None:
    """Watcher ignores invalid YAML changes."""
    space_file = tmp_path / "space.yaml"
    space_file.write_text(yaml.dump({"name": "s1"}), encoding="utf-8")

    received: list[dict] = []

    def on_change(raw: dict) -> None:
        received.append(raw)

    watcher = SpaceFileWatcher(space_file, on_change, interval=0.1)

    time.sleep(1.1)
    space_file.write_text("{{invalid yaml: [", encoding="utf-8")

    await watcher.start()
    await asyncio.sleep(2.0)
    await watcher.stop()

    assert len(received) == 0


@pytest.mark.asyncio
async def test_missing_name_ignored(tmp_path: Path) -> None:
    """Watcher ignores files without a name field."""
    space_file = tmp_path / "space.yaml"
    space_file.write_text(yaml.dump({"name": "s1"}), encoding="utf-8")

    received: list[dict] = []

    def on_change(raw: dict) -> None:
        received.append(raw)

    watcher = SpaceFileWatcher(space_file, on_change, interval=0.1)

    time.sleep(1.1)
    space_file.write_text(yaml.dump({"version": "2"}), encoding="utf-8")

    await watcher.start()
    await asyncio.sleep(2.0)
    await watcher.stop()

    assert len(received) == 0


@pytest.mark.asyncio
async def test_callback_receives_parsed_config(tmp_path: Path) -> None:
    """Callback receives the full parsed YAML dict."""
    space_file = tmp_path / "space.yaml"
    space_file.write_text(yaml.dump({"name": "s1"}), encoding="utf-8")

    received: list[dict] = []

    def on_change(raw: dict) -> None:
        received.append(raw)

    watcher = SpaceFileWatcher(space_file, on_change, interval=0.1)

    time.sleep(1.1)
    space_file.write_text(
        yaml.dump({"name": "s1", "instructions": "do stuff", "config": {"ai": {"model": "x"}}}),
        encoding="utf-8",
    )

    await watcher.start()
    await asyncio.sleep(2.0)
    await watcher.stop()

    assert len(received) >= 1
    assert received[0]["instructions"] == "do stuff"
    assert received[0]["config"]["ai"]["model"] == "x"


@pytest.mark.asyncio
async def test_nonexistent_file_no_error(tmp_path: Path) -> None:
    """Watcher handles nonexistent file gracefully."""
    space_file = tmp_path / "missing.yaml"

    received: list[dict] = []

    def on_change(raw: dict) -> None:
        received.append(raw)

    watcher = SpaceFileWatcher(space_file, on_change, interval=0.1)

    await watcher.start()
    await asyncio.sleep(2.0)
    await watcher.stop()

    assert len(received) == 0


def test_interval_minimum_enforced() -> None:
    """Interval is clamped to at least 1 second."""
    from pathlib import Path

    watcher = SpaceFileWatcher(Path("/tmp/fake.yaml"), lambda _: None, interval=0.1)
    assert watcher.interval >= 1.0


@pytest.mark.asyncio
async def test_async_callback(tmp_path: Path) -> None:
    """Watcher works with async callbacks."""
    space_file = tmp_path / "space.yaml"
    space_file.write_text(yaml.dump({"name": "s1"}), encoding="utf-8")

    received: list[dict] = []

    async def on_change(raw: dict) -> None:
        received.append(raw)

    watcher = SpaceFileWatcher(space_file, on_change, interval=0.1)

    time.sleep(1.1)
    space_file.write_text(yaml.dump({"name": "s1", "version": "2"}), encoding="utf-8")

    await watcher.start()
    await asyncio.sleep(2.0)
    await watcher.stop()

    assert len(received) >= 1

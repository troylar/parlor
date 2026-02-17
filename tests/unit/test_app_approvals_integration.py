from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest


@dataclass
class _FakeAI:
    verify_ssl: bool = True


@dataclass
class _FakeApp:
    data_dir: Path
    tls: bool = False
    host: str = "127.0.0.1"
    port: int = 8080


@dataclass
class _FakeIdentity:
    user_id: str = "u1"
    display_name: str = "User"
    public_key: str = "pk"


@dataclass
class _FakeSharedDb:
    name: str
    path: str
    passphrase_hash: str | None = None


@dataclass
class _FakeConfig:
    app: _FakeApp
    ai: _FakeAI = field(default_factory=_FakeAI)
    identity: _FakeIdentity | None = field(default_factory=_FakeIdentity)
    shared_databases: list[_FakeSharedDb] = field(default_factory=list)
    mcp_servers: list = field(default_factory=list)


@pytest.mark.asyncio
async def test_lifespan_wires_confirm_callback_and_publishes(
    monkeypatch, tmp_path: Path
) -> None:
    # Import inside test to avoid side effects during collection.
    import anteroom.app as app_mod

    published: list[tuple[str, dict]] = []

    class FakeEventBus:
        def start_polling(self, _db_manager):
            return None

        def stop_polling(self):
            return None

        async def publish(self, channel: str, event: dict) -> None:
            published.append((channel, event))

    # Avoid initializing real DB or vec.
    class _FakeDb:
        def close(self):
            return None

    monkeypatch.setattr(app_mod, "init_db", lambda _p: _FakeDb())
    monkeypatch.setattr(app_mod, "has_vec_support", lambda _c: False)

    # Avoid tool registration side effects.
    monkeypatch.setattr(app_mod, "register_default_tools", lambda *_a, **_k: None)

    # Avoid embedding service factory requiring full config.
    monkeypatch.setattr(app_mod, "create_embedding_service", lambda _cfg: None)

    # Ensure our FakeEventBus is used.
    monkeypatch.setattr(app_mod, "EventBus", FakeEventBus)

    # Capture the confirm callback set on ToolRegistry.
    confirm_callbacks: list = []

    class FakeToolRegistry:
        def list_tools(self):
            return []

        def set_confirm_callback(self, cb):
            confirm_callbacks.append(cb)

    monkeypatch.setattr(app_mod, "ToolRegistry", FakeToolRegistry)

    cfg = _FakeConfig(
        app=_FakeApp(data_dir=tmp_path),
        shared_databases=[_FakeSharedDb(name="team", path=str(tmp_path / "team.db"))],
    )

    # Build a minimal FastAPI-like object with state.
    class _State:  # simple object
        pass

    class _App:
        def __init__(self):
            self.state = _State()
            self.state.config = cfg

    app = _App()

    # Enter lifespan to execute wiring.
    cm = app_mod.lifespan(app)  # async context manager
    await cm.__aenter__()
    try:
        assert (
            confirm_callbacks
        ), "Expected ToolRegistry.set_confirm_callback to be called"
        confirm = confirm_callbacks[0]

        # Start confirm flow and resolve it.
        task = asyncio.create_task(confirm("rm -rf important"))

        # Wait until approval_id exists in manager by polling.
        mgr = app.state.approval_manager
        # Grab the only pending approval id
        for _ in range(100):
            async with mgr._lock:  # type: ignore[attr-defined]
                pending_ids = list(mgr._pending.keys())  # type: ignore[attr-defined]
            if pending_ids:
                approval_id = pending_ids[0]
                break
            await asyncio.sleep(0)
        else:
            raise AssertionError("approval id was not created")

        ok = await mgr.resolve(approval_id, True)
        assert ok is True
        assert await task is True

        # Verify publish went to personal + shared
        channels = [c for c, _e in published]
        assert "global:personal" in channels
        assert "global:team" in channels

        # Verify event shape
        _ch, evt = published[0]
        assert evt["type"] == "destructive_approval_requested"
        assert "approval_id" in evt["data"]
        assert evt["data"]["message"]
    finally:
        await cm.__aexit__(None, None, None)

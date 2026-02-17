from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_confirm_destructive_publishes_and_waits(monkeypatch) -> None:
    """Unit-test the confirm callback wiring logic without spinning up the full app.

    We simulate the pieces used in app.lifespan(): an approval manager and event bus.
    """

    class FakeEventBus:
        def __init__(self):
            self.published = []

        async def publish(self, channel: str, event: dict) -> None:
            self.published.append((channel, event))

    from anteroom.services.approvals import ApprovalManager

    mgr = ApprovalManager()
    bus = FakeEventBus()

    config = SimpleNamespace(shared_databases=[SimpleNamespace(name="team")])

    async def confirm(message: str) -> bool:
        approval_id = await mgr.request(message)
        event = {"type": "destructive_approval_requested", "data": {"approval_id": approval_id, "message": message}}
        await bus.publish("global:personal", event)
        for sdb in getattr(config, "shared_databases", []) or []:
            if getattr(sdb, "name", None):
                await bus.publish(f"global:{sdb.name}", event)

        # Resolve asynchronously as if UI clicked Proceed
        async def _resolver():
            await asyncio.sleep(0)
            await mgr.resolve(approval_id, True)

        asyncio.create_task(_resolver())
        return await mgr.wait(approval_id, timeout_s=1.0)

    ok = await confirm("rm -rf important")
    assert ok is True

    channels = [c for (c, _e) in bus.published]
    assert channels == ["global:personal", "global:team"]

    for _c, e in bus.published:
        assert e["type"] == "destructive_approval_requested"
        assert "approval_id" in e["data"]
        assert e["data"]["message"]

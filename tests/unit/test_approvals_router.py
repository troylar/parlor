from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.routers.approvals import router
from anteroom.services.approvals import ApprovalManager


def test_respond_approval_ok_and_resolved_flag() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.approval_manager = ApprovalManager()

    client = TestClient(app)

    # Create a pending approval
    approval_id = TestClient(app).app.state.approval_manager  # type: ignore[attr-defined]
    # Access the manager from state directly (same instance)
    mgr: ApprovalManager = app.state.approval_manager

    import anyio

    async def _req_id() -> str:
        return await mgr.request("Danger")

    aid = anyio.run(_req_id)

    resp = client.post("/api/approvals/respond", json={"approval_id": aid, "approved": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["resolved"] is True

    # Second time is still ok but not resolved
    resp2 = client.post("/api/approvals/respond", json={"approval_id": aid, "approved": True})
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["ok"] is True
    assert body2["resolved"] is False


def test_respond_approval_without_manager() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    resp = client.post("/api/approvals/respond", json={"approval_id": "x", "approved": True})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False

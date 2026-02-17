"""Tests for the approvals router."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.routers.approvals import router


def _create_test_app() -> tuple[FastAPI, dict]:
    app = FastAPI()
    pending = {}
    app.state.pending_approvals = pending

    app.include_router(router, prefix="/api")
    return app, pending


class TestApprovalsRouter:
    def test_approve_valid(self) -> None:
        app, pending = _create_test_app()
        event = asyncio.Event()
        entry = {"event": event, "approved": False}
        pending["test-id"] = entry

        client = TestClient(app)
        resp = client.post(
            "/api/approvals/test-id/respond",
            json={"approved": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is True
        assert event.is_set()
        assert entry["approved"] is True
        assert "test-id" not in pending

    def test_deny_valid(self) -> None:
        app, pending = _create_test_app()
        event = asyncio.Event()
        pending["test-id"] = {"event": event, "approved": False}

        client = TestClient(app)
        resp = client.post(
            "/api/approvals/test-id/respond",
            json={"approved": False},
        )
        assert resp.status_code == 200
        assert resp.json()["approved"] is False
        assert event.is_set()

    def test_unknown_id_returns_404(self) -> None:
        app, pending = _create_test_app()
        client = TestClient(app)
        resp = client.post(
            "/api/approvals/nonexistent/respond",
            json={"approved": True},
        )
        assert resp.status_code == 404

    def test_malformed_json_returns_422(self) -> None:
        app, pending = _create_test_app()
        event = asyncio.Event()
        pending["test-id"] = {"event": event, "approved": False}

        client = TestClient(app)
        resp = client.post(
            "/api/approvals/test-id/respond",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_missing_approved_field_defaults_false(self) -> None:
        app, pending = _create_test_app()
        event = asyncio.Event()
        entry = {"event": event, "approved": False}
        pending["test-id"] = entry

        client = TestClient(app)
        resp = client.post(
            "/api/approvals/test-id/respond",
            json={},
        )
        assert resp.status_code == 200
        assert resp.json()["approved"] is False
        assert entry["approved"] is False

    def test_concurrent_resolve_first_wins(self) -> None:
        app, pending = _create_test_app()
        event = asyncio.Event()
        pending["test-id"] = {"event": event, "approved": False}

        client = TestClient(app)
        resp1 = client.post(
            "/api/approvals/test-id/respond",
            json={"approved": True},
        )
        assert resp1.status_code == 200
        assert "test-id" not in pending

        resp2 = client.post(
            "/api/approvals/test-id/respond",
            json={"approved": False},
        )
        assert resp2.status_code == 404

    def test_oversized_approval_id_rejected(self) -> None:
        app, pending = _create_test_app()
        client = TestClient(app)
        resp = client.post(
            f"/api/approvals/{'x' * 65}/respond",
            json={"approved": True},
        )
        assert resp.status_code == 400
        assert "Invalid approval ID" in resp.json()["detail"]

    def test_non_ascii_approval_id_rejected(self) -> None:
        app, pending = _create_test_app()
        client = TestClient(app)
        resp = client.post(
            "/api/approvals/\u00e9\u00e8\u00ea/respond",
            json={"approved": True},
        )
        assert resp.status_code == 400

    def test_special_chars_in_approval_id_rejected(self) -> None:
        app, pending = _create_test_app()
        client = TestClient(app)
        resp = client.post(
            "/api/approvals/test%20id%3Bfoo/respond",
            json={"approved": True},
        )
        assert resp.status_code == 400

    def test_max_length_approval_id_accepted(self) -> None:
        app, pending = _create_test_app()
        long_id = "a" * 64
        event = asyncio.Event()
        pending[long_id] = {"event": event, "approved": False}
        client = TestClient(app)
        resp = client.post(
            f"/api/approvals/{long_id}/respond",
            json={"approved": True},
        )
        assert resp.status_code == 200

    def test_missing_pending_approvals_attr_returns_404(self) -> None:
        app = FastAPI()
        app.include_router(router, prefix="/api")
        # Don't set app.state.pending_approvals
        client = TestClient(app)
        resp = client.post(
            "/api/approvals/test-id/respond",
            json={"approved": True},
        )
        assert resp.status_code == 404

    def test_scope_field_accepted(self) -> None:
        app, pending = _create_test_app()
        event = asyncio.Event()
        entry = {"event": event, "approved": False, "scope": "once"}
        pending["test-id"] = entry

        client = TestClient(app)
        resp = client.post(
            "/api/approvals/test-id/respond",
            json={"approved": True, "scope": "session"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is True
        assert data["scope"] == "session"
        assert entry["scope"] == "session"

    def test_scope_defaults_to_once(self) -> None:
        app, pending = _create_test_app()
        event = asyncio.Event()
        entry = {"event": event, "approved": False, "scope": "once"}
        pending["test-id"] = entry

        client = TestClient(app)
        resp = client.post(
            "/api/approvals/test-id/respond",
            json={"approved": True},
        )
        assert resp.status_code == 200
        assert resp.json()["scope"] == "once"

    def test_invalid_scope_rejected(self) -> None:
        app, pending = _create_test_app()
        event = asyncio.Event()
        entry = {"event": event, "approved": False, "scope": "once"}
        pending["test-id"] = entry

        client = TestClient(app)
        resp = client.post(
            "/api/approvals/test-id/respond",
            json={"approved": True, "scope": "invalid_scope"},
        )
        # Pydantic rejects invalid Literal values with 422
        assert resp.status_code == 422

    def test_always_scope_accepted(self) -> None:
        app, pending = _create_test_app()
        event = asyncio.Event()
        entry = {"event": event, "approved": False, "scope": "once"}
        pending["test-id"] = entry

        client = TestClient(app)
        resp = client.post(
            "/api/approvals/test-id/respond",
            json={"approved": True, "scope": "always"},
        )
        assert resp.status_code == 200
        assert resp.json()["scope"] == "always"
        assert entry["scope"] == "always"

    @pytest.mark.asyncio
    async def test_timeout_returns_denied(self) -> None:
        """Simulate what happens when the approval times out."""
        event = asyncio.Event()
        pending = {"test-id": {"event": event, "approved": False}}

        try:
            await asyncio.wait_for(event.wait(), timeout=0.05)
        except asyncio.TimeoutError:
            pass

        result = pending.pop("test-id", {})
        assert result.get("approved") is False

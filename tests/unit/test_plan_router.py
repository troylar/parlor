"""Tests for the plan router."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.routers.plan import router


def _create_test_app(data_dir: Path | None = None) -> tuple[FastAPI, Path]:
    app = FastAPI()
    if data_dir is None:
        data_dir = Path(tempfile.mkdtemp())
    mock_config = MagicMock()
    mock_config.app.data_dir = data_dir
    app.state.config = mock_config
    app.include_router(router, prefix="/api")
    return app, data_dir


def _write_plan(data_dir: Path, conv_id: str, content: str) -> Path:
    plans_dir = data_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plans_dir / f"{conv_id}.md"
    plan_path.write_text(content, encoding="utf-8")
    return plan_path


class TestGetPlan:
    def test_returns_content_when_plan_exists(self) -> None:
        app, data_dir = _create_test_app()
        _write_plan(data_dir, "conv-123", "## Overview\nTest plan")
        client = TestClient(app)
        resp = client.get("/api/conversations/conv-123/plan")
        assert resp.status_code == 200
        data = resp.json()
        assert data["exists"] is True
        assert "Test plan" in data["content"]

    def test_returns_not_exists_when_no_plan(self) -> None:
        app, _ = _create_test_app()
        client = TestClient(app)
        resp = client.get("/api/conversations/conv-456/plan")
        assert resp.status_code == 200
        data = resp.json()
        assert data["exists"] is False
        assert data["content"] is None

    def test_rejects_invalid_conversation_id(self) -> None:
        app, _ = _create_test_app()
        client = TestClient(app)
        resp = client.get("/api/conversations/bad!chars@here/plan")
        assert resp.status_code == 400

    def test_rejects_empty_conversation_id(self) -> None:
        app, _ = _create_test_app()
        client = TestClient(app)
        # FastAPI routing won't match empty segment, so this is more for coverage
        resp = client.get("/api/conversations/ /plan")
        assert resp.status_code == 400

    def test_rejects_too_long_conversation_id(self) -> None:
        app, _ = _create_test_app()
        client = TestClient(app)
        long_id = "a" * 65
        resp = client.get(f"/api/conversations/{long_id}/plan")
        assert resp.status_code == 400


class TestApprovePlan:
    def test_approves_existing_plan(self) -> None:
        app, data_dir = _create_test_app()
        plan_path = _write_plan(data_dir, "conv-789", "## Steps\n1. Do something")
        client = TestClient(app)
        resp = client.post(
            "/api/conversations/conv-789/plan/approve",
            headers={"Content-Type": "application/json"},
            content="{}",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"
        assert "Do something" in data["content"]
        assert not plan_path.exists()

    def test_returns_404_when_no_plan(self) -> None:
        app, _ = _create_test_app()
        client = TestClient(app)
        resp = client.post(
            "/api/conversations/conv-nope/plan/approve",
            headers={"Content-Type": "application/json"},
            content="{}",
        )
        assert resp.status_code == 404

    def test_rejects_invalid_conversation_id(self) -> None:
        app, _ = _create_test_app()
        client = TestClient(app)
        resp = client.post(
            "/api/conversations/bad!chars/plan/approve",
            headers={"Content-Type": "application/json"},
            content="{}",
        )
        assert resp.status_code == 400

    def test_rejects_wrong_content_type(self) -> None:
        app, data_dir = _create_test_app()
        _write_plan(data_dir, "conv-ct", "plan content")
        client = TestClient(app)
        resp = client.post(
            "/api/conversations/conv-ct/plan/approve",
            headers={"Content-Type": "text/plain"},
            content="{}",
        )
        assert resp.status_code == 415


class TestRejectPlan:
    def test_rejects_existing_plan(self) -> None:
        app, data_dir = _create_test_app()
        plan_path = _write_plan(data_dir, "conv-rej", "## Plan\nStuff to reject")
        client = TestClient(app)
        resp = client.post(
            "/api/conversations/conv-rej/plan/reject",
            headers={"Content-Type": "application/json"},
            content="{}",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected"
        assert not plan_path.exists()

    def test_returns_404_when_no_plan(self) -> None:
        app, _ = _create_test_app()
        client = TestClient(app)
        resp = client.post(
            "/api/conversations/conv-none/plan/reject",
            headers={"Content-Type": "application/json"},
            content="{}",
        )
        assert resp.status_code == 404

    def test_rejects_invalid_conversation_id(self) -> None:
        app, _ = _create_test_app()
        client = TestClient(app)
        resp = client.post(
            "/api/conversations/bad!id/plan/reject",
            headers={"Content-Type": "application/json"},
            content="{}",
        )
        assert resp.status_code == 400

    def test_accepts_reason_in_body(self) -> None:
        app, data_dir = _create_test_app()
        _write_plan(data_dir, "conv-reason", "some plan")
        client = TestClient(app)
        resp = client.post(
            "/api/conversations/conv-reason/plan/reject",
            json={"reason": "Not detailed enough"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    def test_rejects_wrong_content_type(self) -> None:
        app, data_dir = _create_test_app()
        _write_plan(data_dir, "conv-ct2", "plan content")
        client = TestClient(app)
        resp = client.post(
            "/api/conversations/conv-ct2/plan/reject",
            headers={"Content-Type": "text/plain"},
            content="{}",
        )
        # 422 from Pydantic body parse failure or 415 from _require_json — either rejects wrong type
        assert resp.status_code in (415, 422)

    def test_rejects_oversized_reason(self) -> None:
        app, data_dir = _create_test_app()
        _write_plan(data_dir, "conv-big", "some plan")
        client = TestClient(app)
        resp = client.post(
            "/api/conversations/conv-big/plan/reject",
            json={"reason": "x" * 4097},
        )
        assert resp.status_code == 422

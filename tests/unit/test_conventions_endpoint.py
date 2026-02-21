"""Tests for GET /config/conventions endpoint (#215)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from anteroom.cli.instructions import ConventionsInfo


@pytest.fixture
def _mock_app():
    """Create a minimal FastAPI app with just the config router."""
    from unittest.mock import MagicMock

    from fastapi import FastAPI

    from anteroom.routers.config_api import router

    app = FastAPI()
    app.include_router(router)

    mock_config = MagicMock()
    mock_config.ai.base_url = "https://api.example.com"
    mock_config.ai.api_key = "sk-test"
    mock_config.ai.model = "gpt-4o"
    mock_config.ai.user_system_prompt = None
    mock_config.identity = None
    app.state.config = mock_config
    app.state.mcp_manager = None

    return app


class TestConventionsEndpoint:
    def test_returns_project_conventions(self, _mock_app):
        info = ConventionsInfo(
            path=Path("/project/ANTEROOM.md"),
            content="# My conventions",
            source="project",
            estimated_tokens=100,
            is_oversized=False,
        )
        with patch("anteroom.routers.config_api.discover_conventions", return_value=info):
            client = TestClient(_mock_app)
            resp = client.get("/config/conventions")

        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "project"
        assert data["content"] == "# My conventions"
        assert data["path"] == "/project/ANTEROOM.md"
        assert data["estimated_tokens"] == 100
        assert data["warning"] is None

    def test_returns_none_when_no_file(self, _mock_app):
        info = ConventionsInfo(path=None, content=None, source="none")
        with patch("anteroom.routers.config_api.discover_conventions", return_value=info):
            client = TestClient(_mock_app)
            resp = client.get("/config/conventions")

        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "none"
        assert data["content"] is None
        assert data["path"] is None

    def test_returns_warning_for_oversized(self, _mock_app):
        info = ConventionsInfo(
            path=Path("/project/ANTEROOM.md"),
            content="x" * 20000,
            source="project",
            estimated_tokens=5000,
            is_oversized=True,
        )
        with patch("anteroom.routers.config_api.discover_conventions", return_value=info):
            client = TestClient(_mock_app)
            resp = client.get("/config/conventions")

        assert resp.status_code == 200
        data = resp.json()
        assert data["warning"] is not None
        assert "5,000" in data["warning"]

    def test_returns_global_conventions(self, _mock_app):
        info = ConventionsInfo(
            path=Path("/home/user/.anteroom/ANTEROOM.md"),
            content="# Global rules",
            source="global",
            estimated_tokens=50,
            is_oversized=False,
        )
        with patch("anteroom.routers.config_api.discover_conventions", return_value=info):
            client = TestClient(_mock_app)
            resp = client.get("/config/conventions")

        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "global"

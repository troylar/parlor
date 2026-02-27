"""Tests for the packs API router."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.routers.packs import router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.db = MagicMock()
    return app


class TestListPacksEndpoint:
    def test_list_empty(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.list_packs.return_value = []
            client = TestClient(app)
            resp = client.get("/api/packs")
            assert resp.status_code == 200
            assert resp.json() == []

    def test_list_with_packs(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.list_packs.return_value = [
                {
                    "namespace": "test-ns",
                    "name": "test-pack",
                    "version": "1.0.0",
                    "artifact_count": 3,
                    "source_path": "/secret/path/to/pack",
                }
            ]
            client = TestClient(app)
            resp = client.get("/api/packs")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["name"] == "test-pack"
            assert data[0]["artifact_count"] == 3
            assert "source_path" not in data[0]

    def test_list_calls_service_with_db(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.list_packs.return_value = []
            client = TestClient(app)
            client.get("/api/packs")
            mock_packs.list_packs.assert_called_once_with(app.state.db)


class TestGetPackEndpoint:
    def test_get_existing(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.get_pack.return_value = {
                "namespace": "test-ns",
                "name": "test-pack",
                "version": "1.0.0",
                "source_path": "/secret/internal/path",
                "artifacts": [
                    {
                        "fqn": "@test-ns/skill/greet",
                        "type": "skill",
                        "content_hash": "abc123",
                        "content": "sensitive system instructions",
                    },
                ],
            }
            client = TestClient(app)
            resp = client.get("/api/packs/test-ns/test-pack")
            assert resp.status_code == 200
            data = resp.json()
            assert data["name"] == "test-pack"
            assert len(data["artifacts"]) == 1
            assert "source_path" not in data
            assert "content" not in data["artifacts"][0]

    def test_get_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.get_pack.return_value = None
            client = TestClient(app)
            resp = client.get("/api/packs/no/such-pack")
            assert resp.status_code == 404

    def test_get_calls_service_with_correct_args(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.get_pack.return_value = {"name": "p", "namespace": "n", "artifacts": []}
            client = TestClient(app)
            client.get("/api/packs/my-ns/my-pack")
            mock_packs.get_pack.assert_called_once_with(app.state.db, "my-ns", "my-pack")

    def test_get_not_found_does_not_reflect_input(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.get_pack.return_value = None
            client = TestClient(app)
            resp = client.get("/api/packs/evil-ns/evil-name")
            assert resp.status_code == 404
            assert "evil-ns" not in resp.json()["detail"]
            assert "evil-name" not in resp.json()["detail"]

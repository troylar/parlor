"""Tests for the sources API router."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.routers.sources import router


def _make_app(*, config=None) -> FastAPI:
    """Create a minimal FastAPI app with the sources router."""
    app = FastAPI()
    app.include_router(router, prefix="/api")

    mock_db = MagicMock()
    mock_db_manager = MagicMock()
    mock_db_manager.get.return_value = mock_db
    app.state.db = mock_db
    app.state.db_manager = mock_db_manager

    if config is None:
        config = MagicMock()
        config.identity = None
        config.app.data_dir = "/tmp/test"
    app.state.config = config
    app.state.embedding_worker = None

    return app


class TestListSources:
    def test_list_empty(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.list_sources.return_value = []
            client = TestClient(app)
            resp = client.get("/api/sources")
            assert resp.status_code == 200
            assert resp.json()["sources"] == []

    def test_list_with_filters(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.list_sources.return_value = [{"id": "s1", "title": "Test"}]
            client = TestClient(app)
            resp = client.get("/api/sources?type=text&search=test")
            assert resp.status_code == 200
            mock_storage.list_sources.assert_called_once()
            call_kwargs = mock_storage.list_sources.call_args
            assert call_kwargs[1]["source_type"] == "text"
            assert call_kwargs[1]["search"] == "test"


class TestCreateSource:
    def test_create_text_source(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.create_source.return_value = {
                "id": "s1",
                "type": "text",
                "title": "Note",
                "content": "Hello",
            }
            client = TestClient(app)
            resp = client.post(
                "/api/sources",
                json={"type": "text", "title": "Note", "content": "Hello"},
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 201
            assert resp.json()["id"] == "s1"

    def test_create_text_source_requires_content(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/sources",
            json={"type": "text", "title": "Note"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_create_url_source_requires_url(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/sources",
            json={"type": "url", "title": "Link"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_create_source_wrong_content_type(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/sources",
            content=b"not json",
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 415


class TestUploadSource:
    def test_upload_file(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.save_source_file.return_value = {
                "id": "s1",
                "type": "file",
                "title": "test.txt",
                "filename": "test.txt",
                "content": "file content",
            }
            client = TestClient(app)
            resp = client.post(
                "/api/sources/upload",
                files={"file": ("test.txt", io.BytesIO(b"file content"), "text/plain")},
            )
            assert resp.status_code == 201
            assert resp.json()["id"] == "s1"
            mock_storage.save_source_file.assert_called_once()

    def test_upload_with_title(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.save_source_file.return_value = {
                "id": "s2",
                "type": "file",
                "title": "Custom Title",
            }
            client = TestClient(app)
            resp = client.post(
                "/api/sources/upload",
                files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
                data={"title": "Custom Title"},
            )
            assert resp.status_code == 201
            call_kwargs = mock_storage.save_source_file.call_args
            assert call_kwargs[1]["title"] == "Custom Title"

    def test_upload_invalid_mime_returns_400(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.save_source_file.side_effect = ValueError("Unsupported file type")
            client = TestClient(app)
            resp = client.post(
                "/api/sources/upload",
                files={"file": ("malware.exe", io.BytesIO(b"\x00\x00"), "application/x-msdownload")},
            )
            assert resp.status_code == 400
            assert "Unsupported file type" in resp.json()["detail"]

    def test_upload_oversized_returns_400(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.save_source_file.side_effect = ValueError("File too large")
            client = TestClient(app)
            resp = client.post(
                "/api/sources/upload",
                files={"file": ("big.bin", io.BytesIO(b"x" * 100), "application/octet-stream")},
            )
            assert resp.status_code == 400
            assert "File too large" in resp.json()["detail"]


class TestGetSource:
    def test_get_existing_source(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.get_source.return_value = {"id": "s1", "title": "Test"}
            client = TestClient(app)
            # Use a valid UUID
            resp = client.get("/api/sources/12345678-1234-1234-1234-123456789012")
            assert resp.status_code == 200

    def test_get_missing_source(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.get_source.return_value = None
            client = TestClient(app)
            resp = client.get("/api/sources/12345678-1234-1234-1234-123456789012")
            assert resp.status_code == 404

    def test_get_source_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/sources/not-a-uuid")
        assert resp.status_code == 400


class TestUpdateSource:
    def test_update_source(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.update_source.return_value = {"id": "s1", "title": "Updated"}
            client = TestClient(app)
            resp = client.patch(
                "/api/sources/12345678-1234-1234-1234-123456789012",
                json={"title": "Updated"},
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 200

    def test_update_source_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.update_source.return_value = None
            client = TestClient(app)
            resp = client.patch(
                "/api/sources/12345678-1234-1234-1234-123456789012",
                json={"title": "Updated"},
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 404


class TestDeleteSource:
    def test_delete_source(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.delete_source.return_value = True
            client = TestClient(app)
            resp = client.delete("/api/sources/12345678-1234-1234-1234-123456789012")
            assert resp.status_code == 200

    def test_delete_source_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.delete_source.return_value = False
            client = TestClient(app)
            resp = client.delete("/api/sources/12345678-1234-1234-1234-123456789012")
            assert resp.status_code == 404


class TestSourceTags:
    def test_tag_source(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.add_tag_to_source.return_value = True
            client = TestClient(app)
            resp = client.post(
                "/api/sources/12345678-1234-1234-1234-123456789012/tags/12345678-1234-1234-1234-123456789013"
            )
            assert resp.status_code == 201

    def test_untag_source(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.remove_tag_from_source.return_value = True
            client = TestClient(app)
            resp = client.delete(
                "/api/sources/12345678-1234-1234-1234-123456789012/tags/12345678-1234-1234-1234-123456789013"
            )
            assert resp.status_code == 200


class TestSourceGroups:
    def test_list_groups(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.list_source_groups.return_value = []
            client = TestClient(app)
            resp = client.get("/api/source-groups")
            assert resp.status_code == 200
            assert resp.json()["groups"] == []

    def test_create_group(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.create_source_group.return_value = {"id": "g1", "name": "Research"}
            client = TestClient(app)
            resp = client.post(
                "/api/source-groups",
                json={"name": "Research"},
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 201

    def test_delete_group(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.delete_source_group.return_value = True
            client = TestClient(app)
            resp = client.delete("/api/source-groups/12345678-1234-1234-1234-123456789012")
            assert resp.status_code == 200


class TestProjectSources:
    def test_get_project_sources(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.get_project_sources.return_value = []
            client = TestClient(app)
            resp = client.get("/api/projects/12345678-1234-1234-1234-123456789012/sources")
            assert resp.status_code == 200
            assert resp.json()["sources"] == []

    def test_link_source_to_project(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.get_project.return_value = {"id": "p1", "name": "Test"}
            mock_storage.link_source_to_project.return_value = {
                "project_id": "p1",
                "source_id": "s1",
            }
            client = TestClient(app)
            resp = client.post(
                "/api/projects/12345678-1234-1234-1234-123456789012/sources",
                json={"source_id": "s1"},
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 201

    def test_link_source_project_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.get_project.return_value = None
            client = TestClient(app)
            resp = client.post(
                "/api/projects/12345678-1234-1234-1234-123456789012/sources",
                json={"source_id": "s1"},
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 404

    def test_link_requires_exactly_one(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/projects/12345678-1234-1234-1234-123456789012/sources",
            json={"source_id": "s1", "group_id": "g1"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_unlink_source_from_project(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            client = TestClient(app)
            resp = client.delete("/api/projects/12345678-1234-1234-1234-123456789012/sources?source_id=s1")
            assert resp.status_code == 200
            assert resp.json()["status"] == "unlinked"
            mock_storage.unlink_source_from_project.assert_called_once()

    def test_unlink_requires_exactly_one(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.delete("/api/projects/12345678-1234-1234-1234-123456789012/sources")
        assert resp.status_code == 422

    def test_unlink_rejects_multiple_params(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.delete("/api/projects/12345678-1234-1234-1234-123456789012/sources?source_id=s1&group_id=g1")
        assert resp.status_code == 422


def _make_app_no_db_manager(*, config=None) -> FastAPI:
    """Create a minimal FastAPI app without db_manager (uses direct db)."""
    app = FastAPI()
    app.include_router(router, prefix="/api")

    mock_db = MagicMock()
    app.state.db = mock_db
    # Deliberately no db_manager attribute

    if config is None:
        config = MagicMock()
        config.identity = None
        config.app.data_dir = "/tmp/test"
    app.state.config = config
    app.state.embedding_worker = None

    return app


class TestGetDbFallback:
    """Tests for _get_db falling back to request.app.state.db (line 21)."""

    def test_uses_direct_db_when_no_db_manager(self) -> None:
        app = _make_app_no_db_manager()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.list_sources.return_value = [{"id": "s1"}]
            client = TestClient(app)
            resp = client.get("/api/sources")
            assert resp.status_code == 200
            # Confirm storage was called with the direct db object
            mock_storage.list_sources.assert_called_once()
            call_args = mock_storage.list_sources.call_args
            assert call_args[0][0] is app.state.db


class TestGetIdentity:
    """Tests for _get_identity returning user_id and display_name (line 34)."""

    def test_create_source_with_identity(self) -> None:
        config = MagicMock()
        config.identity.user_id = "user-123"
        config.identity.display_name = "Alice"
        config.app.data_dir = "/tmp/test"
        app = _make_app(config=config)
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.create_source.return_value = {
                "id": "s1",
                "type": "text",
                "title": "Note",
                "content": "Hello",
            }
            client = TestClient(app)
            resp = client.post(
                "/api/sources",
                json={"type": "text", "title": "Note", "content": "Hello"},
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 201
            call_kwargs = mock_storage.create_source.call_args[1]
            assert call_kwargs["user_id"] == "user-123"
            assert call_kwargs["user_display_name"] == "Alice"


class TestEmbeddingWorker:
    """Tests for embedding worker integration paths (lines 103-106, 138-141, 176-179)."""

    def test_create_source_triggers_embed_when_worker_present(self) -> None:
        app = _make_app()
        mock_worker = MagicMock()
        mock_worker.embed_source = MagicMock(return_value=None)
        app.state.embedding_worker = mock_worker

        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.create_source.return_value = {
                "id": "s1",
                "type": "text",
                "title": "Note",
                "content": "Hello",
            }
            client = TestClient(app)
            resp = client.post(
                "/api/sources",
                json={"type": "text", "title": "Note", "content": "Hello"},
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 201
            mock_worker.embed_source.assert_called_once_with("s1")

    def test_create_source_embed_exception_is_swallowed(self) -> None:
        app = _make_app()
        mock_worker = MagicMock()

        async def _raise(*_args, **_kwargs) -> None:
            raise RuntimeError("embed failed")

        mock_worker.embed_source = _raise
        app.state.embedding_worker = mock_worker

        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.create_source.return_value = {
                "id": "s1",
                "type": "text",
                "title": "Note",
                "content": "Hello",
            }
            client = TestClient(app)
            resp = client.post(
                "/api/sources",
                json={"type": "text", "title": "Note", "content": "Hello"},
                headers={"Content-Type": "application/json"},
            )
            # Should succeed even if embed raises
            assert resp.status_code == 201

    def test_upload_triggers_embed_when_worker_present(self) -> None:
        app = _make_app()
        mock_worker = MagicMock()
        mock_worker.embed_source = MagicMock(return_value=None)
        app.state.embedding_worker = mock_worker

        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.save_source_file.return_value = {
                "id": "s2",
                "type": "file",
                "title": "test.txt",
                "content": "file content",
            }
            client = TestClient(app)
            resp = client.post(
                "/api/sources/upload",
                files={"file": ("test.txt", io.BytesIO(b"file content"), "text/plain")},
            )
            assert resp.status_code == 201
            mock_worker.embed_source.assert_called_once_with("s2")

    def test_upload_embed_exception_is_swallowed(self) -> None:
        app = _make_app()
        mock_worker = MagicMock()

        async def _raise(*_args, **_kwargs) -> None:
            raise RuntimeError("embed failed")

        mock_worker.embed_source = _raise
        app.state.embedding_worker = mock_worker

        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.save_source_file.return_value = {
                "id": "s2",
                "type": "file",
                "title": "test.txt",
                "content": "file content",
            }
            client = TestClient(app)
            resp = client.post(
                "/api/sources/upload",
                files={"file": ("test.txt", io.BytesIO(b"file content"), "text/plain")},
            )
            assert resp.status_code == 201

    def test_update_source_triggers_embed_when_content_changed(self) -> None:
        app = _make_app()
        mock_worker = MagicMock()
        mock_worker.embed_source = MagicMock(return_value=None)
        app.state.embedding_worker = mock_worker

        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.update_source.return_value = {"id": "s1", "title": "Updated", "content": "new content"}
            client = TestClient(app)
            resp = client.patch(
                "/api/sources/12345678-1234-1234-1234-123456789012",
                json={"content": "new content"},
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 200
            mock_worker.embed_source.assert_called_once_with("12345678-1234-1234-1234-123456789012")

    def test_update_source_embed_exception_is_swallowed(self) -> None:
        app = _make_app()
        mock_worker = MagicMock()

        async def _raise(*_args, **_kwargs) -> None:
            raise RuntimeError("embed failed")

        mock_worker.embed_source = _raise
        app.state.embedding_worker = mock_worker

        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.update_source.return_value = {"id": "s1", "title": "Updated", "content": "new content"}
            client = TestClient(app)
            resp = client.patch(
                "/api/sources/12345678-1234-1234-1234-123456789012",
                json={"content": "new content"},
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 200

    def test_update_source_no_embed_when_content_not_changed(self) -> None:
        app = _make_app()
        mock_worker = MagicMock()
        mock_worker.embed_source = MagicMock(return_value=None)
        app.state.embedding_worker = mock_worker

        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.update_source.return_value = {"id": "s1", "title": "Updated"}
            client = TestClient(app)
            resp = client.patch(
                "/api/sources/12345678-1234-1234-1234-123456789012",
                json={"title": "Updated"},
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 200
            mock_worker.embed_source.assert_not_called()


class TestTagSourceFailure:
    """Tests for tag_source returning 400 when add_tag_to_source fails (line 203)."""

    def test_tag_source_failure_returns_400(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.add_tag_to_source.return_value = False
            client = TestClient(app)
            resp = client.post(
                "/api/sources/12345678-1234-1234-1234-123456789012/tags/12345678-1234-1234-1234-123456789013"
            )
            assert resp.status_code == 400
            assert "Failed to tag source" in resp.json()["detail"]


class TestUpdateSourceGroup:
    """Tests for update_source_group endpoint (lines 245-253)."""

    def test_update_group_success(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.update_source_group.return_value = {"id": "g1", "name": "Updated"}
            client = TestClient(app)
            resp = client.patch(
                "/api/source-groups/12345678-1234-1234-1234-123456789012",
                json={"name": "Updated"},
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 200
            assert resp.json()["name"] == "Updated"

    def test_update_group_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.update_source_group.return_value = None
            client = TestClient(app)
            resp = client.patch(
                "/api/source-groups/12345678-1234-1234-1234-123456789012",
                json={"name": "Updated"},
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 404
            assert "Source group not found" in resp.json()["detail"]

    def test_update_group_wrong_content_type(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.patch(
            "/api/source-groups/12345678-1234-1234-1234-123456789012",
            content=b"not json",
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 415

    def test_update_group_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.patch(
            "/api/source-groups/not-a-uuid",
            json={"name": "Updated"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400


class TestDeleteSourceGroupNotFound:
    """Test delete_source_group returning 404 (line 261)."""

    def test_delete_group_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.delete_source_group.return_value = False
            client = TestClient(app)
            resp = client.delete("/api/source-groups/12345678-1234-1234-1234-123456789012")
            assert resp.status_code == 404
            assert "Source group not found" in resp.json()["detail"]


class TestGroupMembership:
    """Tests for add_to_group and remove_from_group endpoints (lines 267-281)."""

    def test_add_source_to_group_success(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.add_source_to_group.return_value = True
            client = TestClient(app)
            resp = client.post(
                "/api/source-groups/12345678-1234-1234-1234-123456789012/sources/12345678-1234-1234-1234-123456789013"
            )
            assert resp.status_code == 201
            assert resp.json()["status"] == "added"

    def test_add_source_to_group_failure(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.add_source_to_group.return_value = False
            client = TestClient(app)
            resp = client.post(
                "/api/source-groups/12345678-1234-1234-1234-123456789012/sources/12345678-1234-1234-1234-123456789013"
            )
            assert resp.status_code == 400
            assert "Failed to add source to group" in resp.json()["detail"]

    def test_add_source_to_group_invalid_group_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/source-groups/not-a-uuid/sources/12345678-1234-1234-1234-123456789013")
        assert resp.status_code == 400

    def test_add_source_to_group_invalid_source_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/source-groups/12345678-1234-1234-1234-123456789012/sources/not-a-uuid")
        assert resp.status_code == 400

    def test_remove_source_from_group_success(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.sources.storage") as mock_storage:
            mock_storage.remove_source_from_group.return_value = None
            client = TestClient(app)
            resp = client.delete(
                "/api/source-groups/12345678-1234-1234-1234-123456789012/sources/12345678-1234-1234-1234-123456789013"
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "removed"
            mock_storage.remove_source_from_group.assert_called_once()

    def test_remove_source_from_group_invalid_group_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.delete("/api/source-groups/not-a-uuid/sources/12345678-1234-1234-1234-123456789013")
        assert resp.status_code == 400

    def test_remove_source_from_group_invalid_source_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.delete("/api/source-groups/12345678-1234-1234-1234-123456789012/sources/not-a-uuid")
        assert resp.status_code == 400

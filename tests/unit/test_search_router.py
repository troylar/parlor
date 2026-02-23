"""Tests for the search router."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.routers.search import router


def _make_app(*, vec_enabled: bool = False, embedding_service=None) -> FastAPI:
    """Create a minimal FastAPI app with the search router."""
    app = FastAPI()
    app.include_router(router, prefix="/api")

    # Minimal state
    app.state.vec_enabled = vec_enabled
    app.state.embedding_service = embedding_service

    # Mock db
    mock_db = MagicMock()
    mock_db_manager = MagicMock()
    mock_db_manager.get.return_value = mock_db
    app.state.db = mock_db
    app.state.db_manager = mock_db_manager

    return app


class TestUnifiedSearch:
    def test_keyword_mode_uses_fts(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.search.storage") as mock_storage:
            mock_storage.list_conversations.return_value = [{"id": "c1", "title": "Test Conv", "message_count": 5}]
            client = TestClient(app)
            resp = client.get("/api/search?q=hello&mode=keyword")
            assert resp.status_code == 200
            data = resp.json()
            assert data["mode"] == "keyword"
            assert len(data["results"]) == 1

    def test_auto_mode_falls_back_to_keyword(self) -> None:
        app = _make_app(vec_enabled=False)
        with patch("anteroom.routers.search.storage") as mock_storage:
            mock_storage.list_conversations.return_value = []
            client = TestClient(app)
            resp = client.get("/api/search?q=test&mode=auto")
            assert resp.status_code == 200
            assert resp.json()["mode"] == "keyword"

    def test_semantic_mode_errors_when_unavailable(self) -> None:
        app = _make_app(vec_enabled=False)
        client = TestClient(app)
        resp = client.get("/api/search?q=test&mode=semantic")
        assert resp.status_code == 503

    def test_auto_mode_uses_semantic_when_available(self) -> None:
        service = AsyncMock()
        service.embed = AsyncMock(return_value=[0.1] * 1536)
        app = _make_app(vec_enabled=True, embedding_service=service)

        with patch("anteroom.routers.search.storage") as mock_storage:
            mock_storage.search_similar_messages.return_value = [
                {
                    "message_id": "m1",
                    "conversation_id": "c1",
                    "content": "Hello",
                    "role": "user",
                    "distance": 0.1,
                }
            ]
            client = TestClient(app)
            resp = client.get("/api/search?q=hello&mode=auto")
            assert resp.status_code == 200
            data = resp.json()
            assert data["mode"] == "semantic"

    def test_missing_query_returns_422(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/search")
        assert resp.status_code == 422

    def test_invalid_mode_returns_422(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/search?q=test&mode=invalid")
        assert resp.status_code == 422


class TestSemanticSearch:
    def test_returns_grouped_results(self) -> None:
        service = AsyncMock()
        service.embed = AsyncMock(return_value=[0.1] * 1536)
        app = _make_app(vec_enabled=True, embedding_service=service)

        with patch("anteroom.routers.search.storage") as mock_storage:
            mock_storage.search_similar_messages.return_value = [
                {"message_id": "m1", "conversation_id": "c1", "content": "Hello", "role": "user", "distance": 0.1},
                {"message_id": "m2", "conversation_id": "c1", "content": "World", "role": "assistant", "distance": 0.2},
            ]
            mock_storage.get_conversation.return_value = {"title": "Test Conv"}

            client = TestClient(app)
            resp = client.get("/api/search/semantic?q=hello")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["results"]) == 1  # grouped by conversation
            assert data["results"][0]["conversation_id"] == "c1"
            assert len(data["results"][0]["messages"]) == 2

    def test_errors_when_service_unavailable(self) -> None:
        app = _make_app(vec_enabled=True, embedding_service=None)
        client = TestClient(app)
        resp = client.get("/api/search/semantic?q=hello")
        assert resp.status_code == 503

    def test_errors_when_vec_not_loaded(self) -> None:
        service = AsyncMock()
        app = _make_app(vec_enabled=False, embedding_service=service)
        client = TestClient(app)
        resp = client.get("/api/search/semantic?q=hello")
        assert resp.status_code == 503

    def test_errors_when_embedding_fails(self) -> None:
        service = AsyncMock()
        service.embed = AsyncMock(return_value=None)
        app = _make_app(vec_enabled=True, embedding_service=service)

        client = TestClient(app)
        resp = client.get("/api/search/semantic?q=hello")
        assert resp.status_code == 500


class TestSearchTypeFilter:
    """Type filter parameter on unified search endpoint."""

    def test_keyword_search_with_type_filter(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.search.storage") as mock_storage:
            mock_storage.list_conversations.return_value = [
                {"id": "c1", "title": "Note Conv", "type": "note", "message_count": 3}
            ]
            client = TestClient(app)
            resp = client.get("/api/search?q=hello&mode=keyword&type=note")
            assert resp.status_code == 200
            data = resp.json()
            assert data["mode"] == "keyword"
            mock_storage.list_conversations.assert_called_once()
            call_kwargs = mock_storage.list_conversations.call_args
            assert call_kwargs.kwargs.get("conversation_type") == "note"

    def test_keyword_search_results_include_type(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.search.storage") as mock_storage:
            mock_storage.list_conversations.return_value = [
                {"id": "c1", "title": "A Doc", "type": "document", "message_count": 1}
            ]
            client = TestClient(app)
            resp = client.get("/api/search?q=hello&mode=keyword")
            assert resp.status_code == 200
            data = resp.json()
            assert data["results"][0]["type"] == "document"

    def test_invalid_type_filter_rejected(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/search?q=hello&type=invalid")
        assert resp.status_code == 422

    def test_sql_injection_type_filter_rejected(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/search?q=hello&type=chat' OR '1'='1")
        assert resp.status_code == 422

    def test_no_type_filter_passes_none(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.search.storage") as mock_storage:
            mock_storage.list_conversations.return_value = []
            client = TestClient(app)
            resp = client.get("/api/search?q=hello&mode=keyword")
            assert resp.status_code == 200
            call_kwargs = mock_storage.list_conversations.call_args
            assert call_kwargs.kwargs.get("conversation_type") is None


class TestSemanticSearchTypeInResults:
    """Semantic search results include conversation type."""

    def test_semantic_results_include_type(self) -> None:
        service = AsyncMock()
        service.embed = AsyncMock(return_value=[0.1] * 1536)
        app = _make_app(vec_enabled=True, embedding_service=service)

        with patch("anteroom.routers.search.storage") as mock_storage:
            mock_storage.search_similar_messages.return_value = [
                {"message_id": "m1", "conversation_id": "c1", "content": "Hello", "role": "user", "distance": 0.1}
            ]
            mock_storage.get_conversation.return_value = {"title": "My Note", "type": "note"}

            client = TestClient(app)
            resp = client.get("/api/search/semantic?q=hello")
            assert resp.status_code == 200
            data = resp.json()
            assert data["results"][0]["type"] == "note"


class TestAutoModeFallback:
    def test_auto_falls_back_to_keyword_when_embed_returns_none(self) -> None:
        service = AsyncMock()
        service.embed = AsyncMock(return_value=None)
        app = _make_app(vec_enabled=True, embedding_service=service)

        with patch("anteroom.routers.search.storage") as mock_storage:
            mock_storage.list_conversations.return_value = []
            client = TestClient(app)
            resp = client.get("/api/search?q=test&mode=auto")
            assert resp.status_code == 200
            assert resp.json()["mode"] == "keyword"


class TestProjectScopedSearch:
    """Verify project_id parameter passes through to storage calls (#179)."""

    def test_semantic_search_passes_project_id(self) -> None:
        service = AsyncMock()
        service.embed = AsyncMock(return_value=[0.1] * 384)
        app = _make_app(vec_enabled=True, embedding_service=service)

        with patch("anteroom.routers.search.storage") as mock_storage:
            mock_storage.search_similar_messages.return_value = []
            mock_storage.search_similar_source_chunks.return_value = []
            mock_storage.get_conversation.return_value = None
            client = TestClient(app)
            resp = client.get("/api/search/semantic?q=test&project_id=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
            assert resp.status_code == 200
            mock_storage.search_similar_source_chunks.assert_called_once()
            call_kwargs = mock_storage.search_similar_source_chunks.call_args
            assert call_kwargs.kwargs.get("project_id") == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_semantic_search_invalid_project_id(self) -> None:
        service = AsyncMock()
        service.embed = AsyncMock(return_value=[0.1] * 384)
        app = _make_app(vec_enabled=True, embedding_service=service)

        client = TestClient(app)
        resp = client.get("/api/search/semantic?q=test&project_id=not-a-uuid")
        assert resp.status_code == 400

    def test_unified_search_passes_project_id(self) -> None:
        service = AsyncMock()
        service.embed = AsyncMock(return_value=[0.1] * 384)
        app = _make_app(vec_enabled=True, embedding_service=service)

        with patch("anteroom.routers.search.storage") as mock_storage:
            mock_storage.search_similar_messages.return_value = []
            mock_storage.search_similar_source_chunks.return_value = []
            client = TestClient(app)
            resp = client.get("/api/search?q=test&mode=semantic&project_id=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
            assert resp.status_code == 200
            call_kwargs = mock_storage.search_similar_source_chunks.call_args
            assert call_kwargs.kwargs.get("project_id") == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_semantic_search_no_project_id_passes_none(self) -> None:
        service = AsyncMock()
        service.embed = AsyncMock(return_value=[0.1] * 384)
        app = _make_app(vec_enabled=True, embedding_service=service)

        with patch("anteroom.routers.search.storage") as mock_storage:
            mock_storage.search_similar_messages.return_value = []
            mock_storage.search_similar_source_chunks.return_value = []
            mock_storage.get_conversation.return_value = None
            client = TestClient(app)
            resp = client.get("/api/search/semantic?q=test")
            assert resp.status_code == 200
            call_kwargs = mock_storage.search_similar_source_chunks.call_args
            assert call_kwargs.kwargs.get("project_id") is None

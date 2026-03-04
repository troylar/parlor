"""Security and functional tests for the conversations router.

Covers: type validation, entries endpoint, UUID validation, input boundaries.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.routers.conversations import router


def _make_app() -> FastAPI:
    """Create a minimal FastAPI app with the conversations router."""
    app = FastAPI()
    app.include_router(router, prefix="/api")

    mock_db = MagicMock()
    mock_db_manager = MagicMock()
    mock_db_manager.get.return_value = mock_db
    app.state.db = mock_db
    app.state.db_manager = mock_db_manager

    mock_config = MagicMock()
    mock_config.identity = None
    mock_config.app.data_dir = MagicMock()
    app.state.config = mock_config

    return app


class TestCreateConversationSecurity:
    """POST /conversations — type validation and input security."""

    def test_create_with_valid_type_chat(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.create_conversation.return_value = {
                "id": str(uuid.uuid4()),
                "title": "Test",
                "type": "chat",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            }
            client = TestClient(app)
            resp = client.post("/api/conversations", json={"type": "chat"})
            assert resp.status_code == 201

    def test_create_with_valid_type_note(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.create_conversation.return_value = {
                "id": str(uuid.uuid4()),
                "title": "Test",
                "type": "note",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            }
            client = TestClient(app)
            resp = client.post("/api/conversations", json={"type": "note"})
            assert resp.status_code == 201

    def test_create_with_valid_type_document(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.create_conversation.return_value = {
                "id": str(uuid.uuid4()),
                "title": "Test",
                "type": "document",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            }
            client = TestClient(app)
            resp = client.post("/api/conversations", json={"type": "document"})
            assert resp.status_code == 201

    def test_create_rejects_invalid_type(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/conversations", json={"type": "invalid"})
        assert resp.status_code == 400
        assert "Invalid conversation type" in resp.json()["detail"]

    def test_create_rejects_sql_injection_in_type(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/conversations", json={"type": "chat'; DROP TABLE conversations;--"})
        assert resp.status_code == 400

    def test_create_rejects_xss_in_type(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/conversations", json={"type": "<script>alert(1)</script>"})
        assert resp.status_code == 400

    def test_create_rejects_empty_type(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/conversations", json={"type": ""})
        assert resp.status_code == 400

    def test_create_defaults_to_chat_type(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.create_conversation.return_value = {
                "id": str(uuid.uuid4()),
                "title": "New Conversation",
                "type": "chat",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            }
            client = TestClient(app)
            resp = client.post("/api/conversations", json={})
            assert resp.status_code == 201
            mock_storage.create_conversation.assert_called_once()
            call_kwargs = mock_storage.create_conversation.call_args
            assert call_kwargs.kwargs.get("conversation_type") == "chat"

    def test_create_rejects_type_with_newlines(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/conversations", json={"type": "chat\nnote"})
        assert resp.status_code == 400

    def test_create_rejects_type_with_null_bytes(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/conversations", json={"type": "chat\x00"})
        assert resp.status_code == 400

    def test_create_rejects_oversized_title(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/conversations", json={"title": "x" * 201})
        assert resp.status_code == 400
        assert "200" in resp.json()["detail"]

    def test_create_accepts_max_length_title(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.create_conversation.return_value = {
                "id": str(uuid.uuid4()),
                "title": "x" * 200,
                "type": "chat",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            }
            client = TestClient(app)
            resp = client.post("/api/conversations", json={"title": "x" * 200})
            assert resp.status_code == 201


class TestListConversationsTypeSecurity:
    """GET /conversations — type filter validation."""

    def test_list_with_valid_type_filter(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.list_conversations.return_value = []
            client = TestClient(app)
            resp = client.get("/api/conversations?type=note")
            assert resp.status_code == 200

    def test_list_rejects_invalid_type_filter(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/conversations?type=invalid")
        assert resp.status_code == 422

    def test_list_rejects_sql_injection_in_type_filter(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/conversations?type=chat' OR '1'='1")
        assert resp.status_code == 422

    def test_list_without_type_returns_all(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.list_conversations.return_value = []
            client = TestClient(app)
            resp = client.get("/api/conversations")
            assert resp.status_code == 200
            mock_storage.list_conversations.assert_called_once()
            call_kwargs = mock_storage.list_conversations.call_args
            assert call_kwargs.kwargs.get("conversation_type") is None


class TestUpdateConversationTypeSecurity:
    """PATCH /conversations/{id} — type update validation."""

    def test_update_type_to_valid_value(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            mock_storage.update_conversation_type.return_value = {"id": conv_id, "type": "note"}
            client = TestClient(app)
            resp = client.patch(f"/api/conversations/{conv_id}", json={"type": "note"})
            assert resp.status_code == 200

    def test_update_rejects_invalid_type(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.patch(f"/api/conversations/{conv_id}", json={"type": "malicious"})
        assert resp.status_code == 422


class TestEntriesEndpointSecurity:
    """POST /conversations/{id}/entries — security for note/document entries."""

    def _make_note_conv(self, conv_id: str) -> dict:
        return {
            "id": conv_id,
            "title": "Test Note",
            "type": "note",
            "created_at": "2024-01-01",
            "updated_at": "2024-01-01",
        }

    def test_entries_on_note_conversation(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = self._make_note_conv(conv_id)
            mock_storage.create_message.return_value = {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": "Hello",
                "position": 0,
                "created_at": "2024-01-01",
            }
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/entries", json={"content": "Hello"})
            assert resp.status_code == 201

    def test_entries_on_document_conversation(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {
                "id": conv_id,
                "type": "document",
                "title": "Doc",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            }
            mock_storage.create_message.return_value = {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": "# Heading",
                "position": 0,
                "created_at": "2024-01-01",
            }
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/entries", json={"content": "# Heading"})
            assert resp.status_code == 201

    def test_entries_rejected_on_chat_conversation(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {
                "id": conv_id,
                "type": "chat",
                "title": "Chat",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            }
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/entries", json={"content": "Hello"})
            assert resp.status_code == 400
            assert "note or document" in resp.json()["detail"]

    def test_entries_rejected_on_nonexistent_conversation(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = None
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/entries", json={"content": "Hello"})
            assert resp.status_code == 404

    def test_entries_reject_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/conversations/not-a-uuid/entries", json={"content": "Hello"})
        assert resp.status_code == 400
        assert "Invalid ID format" in resp.json()["detail"]

    def test_entries_reject_empty_content(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.post(f"/api/conversations/{conv_id}/entries", json={"content": ""})
        assert resp.status_code == 422

    def test_entries_reject_missing_content(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.post(f"/api/conversations/{conv_id}/entries", json={})
        assert resp.status_code == 422

    def test_entries_reject_oversized_content(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.post(f"/api/conversations/{conv_id}/entries", json={"content": "x" * 100001})
        assert resp.status_code == 422

    def test_entries_accept_max_length_content(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = self._make_note_conv(conv_id)
            mock_storage.create_message.return_value = {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": "x" * 100000,
                "position": 0,
                "created_at": "2024-01-01",
            }
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/entries", json={"content": "x" * 100000})
            assert resp.status_code == 201

    def test_entries_content_with_html_tags_stored_as_is(self) -> None:
        """Verify content with HTML is stored raw — XSS prevention is at the render layer (DOMPurify)."""
        conv_id = str(uuid.uuid4())
        app = _make_app()
        html_content = '<script>alert("xss")</script><img src=x onerror=alert(1)>'
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = self._make_note_conv(conv_id)
            mock_storage.create_message.return_value = {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": html_content,
                "position": 0,
                "created_at": "2024-01-01",
            }
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/entries", json={"content": html_content})
            assert resp.status_code == 201
            # Verify storage received the raw content (XSS prevention is client-side via DOMPurify)
            mock_storage.create_message.assert_called_once()
            stored_content = mock_storage.create_message.call_args[0][3]
            assert stored_content == html_content

    def test_entries_content_with_sql_injection_attempt(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = self._make_note_conv(conv_id)
            mock_storage.create_message.return_value = {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": "'; DROP TABLE messages;--",
                "position": 0,
                "created_at": "2024-01-01",
            }
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/entries", json={"content": "'; DROP TABLE messages;--"})
            assert resp.status_code == 201


class TestUUIDValidation:
    """UUID validation across all endpoints."""

    def test_get_conversation_invalid_identifier(self) -> None:
        app = _make_app()
        client = TestClient(app)
        # Input that is neither a valid UUID nor a valid slug (contains uppercase/special chars)
        resp = client.get("/api/conversations/NOT-VALID!")
        assert resp.status_code == 400

    def test_get_conversation_valid_slug_not_found(self) -> None:
        app = _make_app()
        client = TestClient(app)
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = None
            resp = client.get("/api/conversations/not-a-uuid")
        # Valid slug format but no conversation found
        assert resp.status_code == 404

    def test_delete_conversation_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.delete("/api/conversations/not-a-uuid")
        assert resp.status_code == 400

    def test_patch_conversation_invalid_identifier(self) -> None:
        app = _make_app()
        client = TestClient(app)
        # Input that is neither a valid UUID nor a valid slug
        resp = client.patch("/api/conversations/NOT-VALID!", json={"title": "Test"})
        assert resp.status_code == 400

    def test_entries_path_traversal_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/conversations/../../etc/passwd/entries", json={"content": "x"})
        # FastAPI resolves the path before routing, so this returns 404 (no matching route)
        # or 400 if it reaches our UUID validation. Either way, it's rejected.
        assert resp.status_code in (400, 404)

    def test_fork_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/conversations/not-a-uuid/fork", json={"up_to_position": 0})
        assert resp.status_code == 400

    def test_get_valid_uuid_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = None
            client = TestClient(app)
            resp = client.get(f"/api/conversations/{conv_id}")
            assert resp.status_code == 404


class TestConversationUpdateModelValidation:
    """ConversationUpdate Pydantic model — type field regex validation."""

    def test_model_accepts_valid_types(self) -> None:
        from anteroom.models import ConversationUpdate

        for t in ("chat", "note", "document"):
            model = ConversationUpdate(type=t)
            assert model.type == t

    def test_model_rejects_invalid_type(self) -> None:
        from pydantic import ValidationError

        from anteroom.models import ConversationUpdate

        with pytest.raises(ValidationError):
            ConversationUpdate(type="bad")

    def test_model_rejects_partial_match(self) -> None:
        from pydantic import ValidationError

        from anteroom.models import ConversationUpdate

        with pytest.raises(ValidationError):
            ConversationUpdate(type="chat_extra")

    def test_model_rejects_case_variants(self) -> None:
        from pydantic import ValidationError

        from anteroom.models import ConversationUpdate

        for t in ("Chat", "NOTE", "Document", "CHAT"):
            with pytest.raises(ValidationError):
                ConversationUpdate(type=t)

    def test_model_allows_none_type(self) -> None:
        from anteroom.models import ConversationUpdate

        model = ConversationUpdate(type=None)
        assert model.type is None


class TestDeleteMessageEndpoint:
    """DELETE /conversations/{id}/messages/{mid} — single message deletion."""

    def test_delete_message_success(self) -> None:
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "note"}
            mock_storage.delete_message.return_value = True
            client = TestClient(app)
            resp = client.delete(f"/api/conversations/{conv_id}/messages/{msg_id}")
            assert resp.status_code == 204
            mock_storage.delete_message.assert_called_once()

    def test_delete_message_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "note"}
            mock_storage.delete_message.return_value = False
            client = TestClient(app)
            resp = client.delete(f"/api/conversations/{conv_id}/messages/{msg_id}")
            assert resp.status_code == 404

    def test_delete_message_conversation_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = None
            client = TestClient(app)
            resp = client.delete(f"/api/conversations/{conv_id}/messages/{msg_id}")
            assert resp.status_code == 404

    def test_delete_message_rejected_for_chat_type(self) -> None:
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            client = TestClient(app)
            resp = client.delete(f"/api/conversations/{conv_id}/messages/{msg_id}")
            assert resp.status_code == 400
            assert "note and document" in resp.json()["detail"]
            mock_storage.delete_message.assert_not_called()

    def test_delete_message_allowed_for_document_type(self) -> None:
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "document"}
            mock_storage.delete_message.return_value = True
            client = TestClient(app)
            resp = client.delete(f"/api/conversations/{conv_id}/messages/{msg_id}")
            assert resp.status_code == 204

    def test_delete_message_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.delete("/api/conversations/not-a-uuid/messages/also-not-uuid")
        assert resp.status_code == 400


class TestReplaceDocumentEndpoint:
    """PUT /conversations/{id}/document — full document replacement."""

    def test_replace_document_success(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "document"}
            mock_storage.replace_document_content.return_value = {
                "id": str(uuid.uuid4()),
                "content": "new content",
                "role": "user",
                "position": 0,
                "created_at": "2024-01-01",
            }
            client = TestClient(app)
            resp = client.put(f"/api/conversations/{conv_id}/document", json={"content": "new content"})
            assert resp.status_code == 200
            assert resp.json()["content"] == "new content"

    def test_replace_document_rejects_non_document(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            client = TestClient(app)
            resp = client.put(f"/api/conversations/{conv_id}/document", json={"content": "hello"})
            assert resp.status_code == 400
            assert "document" in resp.json()["detail"].lower()

    def test_replace_document_rejects_note_type(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "note"}
            client = TestClient(app)
            resp = client.put(f"/api/conversations/{conv_id}/document", json={"content": "hello"})
            assert resp.status_code == 400

    def test_replace_document_conversation_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = None
            client = TestClient(app)
            resp = client.put(f"/api/conversations/{conv_id}/document", json={"content": "hello"})
            assert resp.status_code == 404

    def test_replace_document_empty_content(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.put(f"/api/conversations/{conv_id}/document", json={"content": ""})
        assert resp.status_code == 422

    def test_replace_document_missing_content(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.put(f"/api/conversations/{conv_id}/document", json={})
        assert resp.status_code == 422

    def test_replace_document_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.put("/api/conversations/not-a-uuid/document", json={"content": "hello"})
        assert resp.status_code == 400


class TestEntryCreateModelValidation:
    """EntryCreate Pydantic model — content length boundaries."""

    def test_accepts_valid_content(self) -> None:
        from anteroom.models import EntryCreate

        model = EntryCreate(content="Hello world")
        assert model.content == "Hello world"

    def test_rejects_empty_content(self) -> None:
        from pydantic import ValidationError

        from anteroom.models import EntryCreate

        with pytest.raises(ValidationError):
            EntryCreate(content="")

    def test_accepts_min_length_content(self) -> None:
        from anteroom.models import EntryCreate

        model = EntryCreate(content="x")
        assert model.content == "x"

    def test_accepts_max_length_content(self) -> None:
        from anteroom.models import EntryCreate

        model = EntryCreate(content="x" * 100000)
        assert len(model.content) == 100000

    def test_rejects_over_max_length(self) -> None:
        from pydantic import ValidationError

        from anteroom.models import EntryCreate

        with pytest.raises(ValidationError):
            EntryCreate(content="x" * 100001)


class TestConversationCreateModelValidation:
    """ConversationCreate Pydantic model — type field regex validation."""

    def test_defaults_to_chat(self) -> None:
        from anteroom.models import ConversationCreate

        model = ConversationCreate()
        assert model.type == "chat"

    def test_accepts_note(self) -> None:
        from anteroom.models import ConversationCreate

        model = ConversationCreate(type="note")
        assert model.type == "note"

    def test_rejects_invalid(self) -> None:
        from pydantic import ValidationError

        from anteroom.models import ConversationCreate

        with pytest.raises(ValidationError):
            ConversationCreate(type="notebook")


class TestDocumentContentModelValidation:
    """DocumentContent Pydantic model — content length boundaries."""

    def test_accepts_valid_content(self) -> None:
        from anteroom.models import DocumentContent

        model = DocumentContent(content="Hello world")
        assert model.content == "Hello world"

    def test_rejects_empty_content(self) -> None:
        from pydantic import ValidationError

        from anteroom.models import DocumentContent

        with pytest.raises(ValidationError):
            DocumentContent(content="")

    def test_accepts_min_length_content(self) -> None:
        from anteroom.models import DocumentContent

        model = DocumentContent(content="x")
        assert model.content == "x"

    def test_accepts_max_length_content(self) -> None:
        from anteroom.models import DocumentContent

        model = DocumentContent(content="x" * 500000)
        assert len(model.content) == 500000

    def test_rejects_over_max_length(self) -> None:
        from pydantic import ValidationError

        from anteroom.models import DocumentContent

        with pytest.raises(ValidationError):
            DocumentContent(content="x" * 500001)


class TestDeleteMessageSecurity:
    """DELETE /conversations/{id}/messages/{mid} — security tests."""

    def test_delete_message_sql_injection_in_message_id(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.delete(f"/api/conversations/{conv_id}/messages/'; DROP TABLE messages;--")
        assert resp.status_code == 400

    def test_delete_message_path_traversal_message_id(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.delete(f"/api/conversations/{conv_id}/messages/../../etc/passwd")
        assert resp.status_code in (400, 404)

    def test_delete_message_valid_uuid_but_wrong_conv(self) -> None:
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "note"}
            mock_storage.delete_message.return_value = False
            client = TestClient(app)
            resp = client.delete(f"/api/conversations/{conv_id}/messages/{msg_id}")
            assert resp.status_code == 404
            assert "Message not found" in resp.json()["detail"]


class TestReplaceDocumentSecurity:
    """PUT /conversations/{id}/document — security tests."""

    def test_replace_document_sql_injection_in_content(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "document"}
            mock_storage.replace_document_content.return_value = {
                "id": str(uuid.uuid4()),
                "content": "'; DROP TABLE messages;--",
                "role": "user",
                "position": 0,
                "created_at": "2024-01-01",
            }
            client = TestClient(app)
            resp = client.put(
                f"/api/conversations/{conv_id}/document",
                json={"content": "'; DROP TABLE messages;--"},
            )
            assert resp.status_code == 200
            mock_storage.replace_document_content.assert_called_once()

    def test_replace_document_xss_in_content(self) -> None:
        conv_id = str(uuid.uuid4())
        xss_content = '<script>alert("xss")</script>'
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "document"}
            mock_storage.replace_document_content.return_value = {
                "id": str(uuid.uuid4()),
                "content": xss_content,
                "role": "user",
                "position": 0,
                "created_at": "2024-01-01",
            }
            client = TestClient(app)
            resp = client.put(
                f"/api/conversations/{conv_id}/document",
                json={"content": xss_content},
            )
            assert resp.status_code == 200
            stored = mock_storage.replace_document_content.call_args
            assert stored.args[2] == xss_content

    def test_replace_document_path_traversal_in_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.put("/api/conversations/../../etc/passwd/document", json={"content": "x"})
        assert resp.status_code in (400, 404)

    def test_replace_document_oversized_content(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.put(
            f"/api/conversations/{conv_id}/document",
            json={"content": "x" * 500001},
        )
        assert resp.status_code == 422


class TestCreateConversationContentType:
    """POST /conversations — Content-Type enforcement."""

    def test_create_rejects_non_json_content_type(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/conversations", content="type=chat", headers={"content-type": "text/plain"})
        assert resp.status_code == 415
        assert "application/json" in resp.json()["detail"]

    def test_create_rejects_form_encoded(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/conversations",
            content="type=chat",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 415


class TestForkConversation:
    """POST /conversations/{id}/fork — fork endpoint."""

    def test_fork_success(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            mock_storage.list_messages.return_value = [
                {"id": "m1", "position": 0, "role": "user", "content": "hello"},
                {"id": "m2", "position": 1, "role": "assistant", "content": "hi"},
            ]
            mock_storage.fork_conversation.return_value = {
                "id": str(uuid.uuid4()),
                "title": "Forked",
                "type": "chat",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            }
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/fork", json={"up_to_position": 0})
            assert resp.status_code == 201
            mock_storage.fork_conversation.assert_called_once()

    def test_fork_conversation_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = None
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/fork", json={"up_to_position": 0})
            assert resp.status_code == 404

    def test_fork_invalid_position(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            mock_storage.list_messages.return_value = [
                {"id": "m1", "position": 0, "role": "user", "content": "hello"},
            ]
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/fork", json={"up_to_position": 5})
            assert resp.status_code == 400
            assert "Invalid position" in resp.json()["detail"]

    def test_fork_negative_position(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.post(f"/api/conversations/{conv_id}/fork", json={"up_to_position": -1})
        assert resp.status_code == 422


class TestCanvasCreateModelValidation:
    """CanvasCreate Pydantic model — title min_length constraint."""

    def test_rejects_empty_title(self) -> None:
        from pydantic import ValidationError

        from anteroom.models import CanvasCreate

        with pytest.raises(ValidationError):
            CanvasCreate(title="")

    def test_accepts_default_title(self) -> None:
        from anteroom.models import CanvasCreate

        model = CanvasCreate()
        assert model.title == "Untitled"

    def test_accepts_single_char_title(self) -> None:
        from anteroom.models import CanvasCreate

        model = CanvasCreate(title="X")
        assert model.title == "X"


class TestUpdateMessageEndpoint:
    """PATCH /conversations/{id}/messages/{mid} — message editing."""

    def test_update_message_success(self) -> None:
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            mock_storage.update_message_content.return_value = {
                "id": msg_id,
                "content": "updated",
                "role": "user",
                "position": 0,
                "created_at": "2024-01-01",
            }
            client = TestClient(app)
            resp = client.patch(f"/api/conversations/{conv_id}/messages/{msg_id}", json={"content": "updated"})
            assert resp.status_code == 200
            assert resp.json()["content"] == "updated"

    def test_update_message_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            mock_storage.update_message_content.return_value = None
            client = TestClient(app)
            resp = client.patch(f"/api/conversations/{conv_id}/messages/{msg_id}", json={"content": "updated"})
            assert resp.status_code == 404

    def test_update_message_conversation_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = None
            client = TestClient(app)
            resp = client.patch(f"/api/conversations/{conv_id}/messages/{msg_id}", json={"content": "updated"})
            assert resp.status_code == 404

    def test_update_message_empty_content(self) -> None:
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.patch(f"/api/conversations/{conv_id}/messages/{msg_id}", json={"content": ""})
        assert resp.status_code == 422

    def test_update_message_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.patch("/api/conversations/bad-uuid/messages/also-bad", json={"content": "x"})
        assert resp.status_code == 400


class TestDeleteMessagesAfterEndpoint:
    """DELETE /conversations/{id}/messages — bulk delete after position."""

    def test_delete_messages_after_success(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            mock_storage.delete_messages_after_position.return_value = 3
            client = TestClient(app)
            resp = client.delete(f"/api/conversations/{conv_id}/messages?after_position=2")
            assert resp.status_code == 204

    def test_delete_messages_after_conversation_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = None
            client = TestClient(app)
            resp = client.delete(f"/api/conversations/{conv_id}/messages?after_position=0")
            assert resp.status_code == 404

    def test_delete_messages_after_negative_position(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.delete(f"/api/conversations/{conv_id}/messages?after_position=-1")
        assert resp.status_code == 422

    def test_delete_messages_after_missing_param(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.delete(f"/api/conversations/{conv_id}/messages")
        assert resp.status_code == 422

    def test_delete_messages_after_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.delete("/api/conversations/bad-uuid/messages?after_position=0")
        assert resp.status_code == 400


class TestRewindConversationEndpoint:
    """POST /conversations/{id}/rewind — rewind to position."""

    def test_rewind_success(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with (
            patch("anteroom.routers.conversations.storage") as mock_storage,
            patch("anteroom.routers.conversations.rewind_service") as mock_rewind,
        ):
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            mock_storage.list_messages.return_value = [
                {"id": "m1", "position": 0, "role": "user", "content": "hello"},
                {"id": "m2", "position": 1, "role": "assistant", "content": "hi"},
            ]

            from anteroom.services.rewind import RewindResult

            mock_rewind.return_value = RewindResult(deleted_messages=1, reverted_files=[], skipped_files=[])
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/rewind", json={"to_position": 0})
            assert resp.status_code == 200
            assert resp.json()["deleted_messages"] == 1

    def test_rewind_conversation_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = None
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/rewind", json={"to_position": 0})
            assert resp.status_code == 404

    def test_rewind_invalid_position(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            mock_storage.list_messages.return_value = [
                {"id": "m1", "position": 0, "role": "user", "content": "hello"},
            ]
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/rewind", json={"to_position": 5})
            assert resp.status_code == 400
            assert "Invalid position" in resp.json()["detail"]

    def test_rewind_negative_position(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.post(f"/api/conversations/{conv_id}/rewind", json={"to_position": -1})
        assert resp.status_code == 422


class TestExportConversationEndpoint:
    """GET /conversations/{id}/export — markdown export."""

    def test_export_success(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {
                "id": conv_id,
                "title": "Test Chat",
                "type": "chat",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            }
            mock_storage.list_messages.return_value = [
                {"id": "m1", "role": "user", "content": "Hello", "position": 0, "created_at": "2024-01-01"},
            ]
            client = TestClient(app)
            resp = client.get(f"/api/conversations/{conv_id}/export")
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/markdown")
            assert "Test Chat" in resp.text
            assert "Hello" in resp.text

    def test_export_conversation_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = None
            client = TestClient(app)
            resp = client.get(f"/api/conversations/{conv_id}/export")
            assert resp.status_code == 404

    def test_export_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/conversations/bad-uuid/export")
        assert resp.status_code == 400

    def test_export_content_disposition_header(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {
                "id": conv_id,
                "title": "My Chat",
                "type": "chat",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            }
            mock_storage.list_messages.return_value = []
            client = TestClient(app)
            resp = client.get(f"/api/conversations/{conv_id}/export")
            assert resp.status_code == 200
            assert "content-disposition" in resp.headers
            assert "attachment" in resp.headers["content-disposition"]
            assert ".md" in resp.headers["content-disposition"]


class TestForkContentTypeEnforcement:
    """POST /conversations/{id}/fork — Content-Type enforcement.

    Note: Pydantic body parsing fires before _require_json for endpoints with
    typed body params, so non-JSON bodies get 422 from Pydantic.
    _require_json provides defense-in-depth for edge cases where body parsing succeeds.
    """

    def test_fork_rejects_non_json_content_type(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            f"/api/conversations/{conv_id}/fork",
            content="up_to_position=0",
            headers={"content-type": "text/plain"},
        )
        assert resp.status_code == 422

    def test_fork_rejects_form_encoded(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            f"/api/conversations/{conv_id}/fork",
            content="up_to_position=0",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 422


class TestGetConversationEndpoint:
    """GET /conversations/{id} — retrieve conversation with messages."""

    def test_get_conversation_success(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {
                "id": conv_id,
                "title": "Test",
                "type": "chat",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            }
            mock_storage.list_messages.return_value = [
                {"id": "m1", "role": "user", "content": "hello", "position": 0},
            ]
            client = TestClient(app)
            resp = client.get(f"/api/conversations/{conv_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["id"] == conv_id
            assert len(data["messages"]) == 1
            assert data["messages"][0]["content"] == "hello"

    def test_get_conversation_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = None
            client = TestClient(app)
            resp = client.get(f"/api/conversations/{conv_id}")
            assert resp.status_code == 404


class TestDeleteConversationEndpoint:
    """DELETE /conversations/{id} — delete conversation."""

    def test_delete_conversation_success(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.delete_conversation.return_value = True
            client = TestClient(app)
            resp = client.delete(f"/api/conversations/{conv_id}")
            assert resp.status_code == 204

    def test_delete_conversation_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.delete_conversation.return_value = False
            client = TestClient(app)
            resp = client.delete(f"/api/conversations/{conv_id}")
            assert resp.status_code == 404

    def test_delete_conversation_embeddings_cleanup_failure_ignored(self) -> None:
        """Embeddings cleanup failure should not block conversation deletion."""
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.delete_embeddings_for_conversation.side_effect = Exception("no vec table")
            mock_storage.delete_conversation.return_value = True
            client = TestClient(app)
            resp = client.delete(f"/api/conversations/{conv_id}")
            assert resp.status_code == 204


class TestUpdateConversationEndpoint:
    """PATCH /conversations/{id} — update conversation fields."""

    def test_update_title(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat", "title": "Old"}
            mock_storage.update_conversation_title.return_value = {"id": conv_id, "title": "New", "type": "chat"}
            client = TestClient(app)
            resp = client.patch(f"/api/conversations/{conv_id}", json={"title": "New"})
            assert resp.status_code == 200
            mock_storage.update_conversation_title.assert_called_once()

    def test_update_model(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            mock_storage.update_conversation_model.return_value = {"id": conv_id, "type": "chat", "model": "gpt-4o"}
            client = TestClient(app)
            resp = client.patch(f"/api/conversations/{conv_id}", json={"model": "gpt-4o"})
            assert resp.status_code == 200
            mock_storage.update_conversation_model.assert_called_once()

    def test_update_folder_id(self) -> None:
        conv_id = str(uuid.uuid4())
        folder_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            mock_storage.move_conversation_to_folder.return_value = None
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat", "folder_id": folder_id}
            client = TestClient(app)
            resp = client.patch(f"/api/conversations/{conv_id}", json={"folder_id": folder_id})
            assert resp.status_code == 200
            mock_storage.move_conversation_to_folder.assert_called_once()

    def test_update_folder_id_empty_clears(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            mock_storage.move_conversation_to_folder.return_value = None
            client = TestClient(app)
            resp = client.patch(f"/api/conversations/{conv_id}", json={"folder_id": ""})
            assert resp.status_code == 200
            call_args = mock_storage.move_conversation_to_folder.call_args
            assert call_args[1].get("folder_id") is None or call_args[0][2] is None

    def test_update_conversation_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = None
            client = TestClient(app)
            resp = client.patch(f"/api/conversations/{conv_id}", json={"title": "New"})
            assert resp.status_code == 404


class TestCopyConversationEndpoint:
    """POST /conversations/{id}/copy — cross-database copy."""

    def test_copy_conversation_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = None
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/copy?target_db=other")
            assert resp.status_code == 404

    def test_copy_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/conversations/bad-uuid/copy?target_db=other")
        assert resp.status_code == 400

    def test_copy_missing_target_db(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.post(f"/api/conversations/{conv_id}/copy")
        assert resp.status_code == 422

    def test_copy_success(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            mock_storage.copy_conversation_to_db.return_value = {
                "id": str(uuid.uuid4()),
                "title": "Copied",
                "type": "chat",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            }
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/copy?target_db=other")
            assert resp.status_code == 201
            mock_storage.copy_conversation_to_db.assert_called_once()

    def test_copy_target_db_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        # db_manager.get is called twice: once for source (via _get_db) and once for target
        # We need source to succeed but raise on the target lookup
        source_db = MagicMock()

        def get_side_effect(name=None):
            if name == "nonexistent":
                raise KeyError("not found")
            return source_db

        app.state.db_manager.get.side_effect = get_side_effect
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/copy?target_db=nonexistent")
            assert resp.status_code == 404


class TestFolderEndpoints:
    """Folder CRUD endpoints."""

    def test_list_folders(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.list_folders.return_value = []
            client = TestClient(app)
            resp = client.get("/api/folders")
            assert resp.status_code == 200

    def test_create_folder(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.create_folder.return_value = {
                "id": str(uuid.uuid4()),
                "name": "Test Folder",
            }
            client = TestClient(app)
            resp = client.post("/api/folders", json={"name": "Test Folder"})
            assert resp.status_code == 201

    def test_create_folder_with_parent(self) -> None:
        parent_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.create_folder.return_value = {"id": str(uuid.uuid4()), "name": "Sub"}
            client = TestClient(app)
            resp = client.post("/api/folders", json={"name": "Sub", "parent_id": parent_id})
            assert resp.status_code == 201

    def test_create_folder_invalid_parent_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/folders", json={"name": "Sub", "parent_id": "bad"})
        assert resp.status_code == 400

    def test_update_folder(self) -> None:
        folder_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.update_folder.return_value = {"id": folder_id, "name": "Renamed"}
            client = TestClient(app)
            resp = client.patch(f"/api/folders/{folder_id}", json={"name": "Renamed"})
            assert resp.status_code == 200

    def test_update_folder_not_found(self) -> None:
        folder_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.update_folder.return_value = None
            client = TestClient(app)
            resp = client.patch(f"/api/folders/{folder_id}", json={"name": "X"})
            assert resp.status_code == 404

    def test_delete_folder(self) -> None:
        folder_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.delete_folder.return_value = True
            client = TestClient(app)
            resp = client.delete(f"/api/folders/{folder_id}")
            assert resp.status_code == 204

    def test_delete_folder_not_found(self) -> None:
        folder_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.delete_folder.return_value = False
            client = TestClient(app)
            resp = client.delete(f"/api/folders/{folder_id}")
            assert resp.status_code == 404

    def test_delete_folder_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.delete("/api/folders/bad-uuid")
        assert resp.status_code == 400


class TestTagEndpoints:
    """Tag CRUD and conversation tagging endpoints."""

    def test_list_tags(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.list_tags.return_value = []
            client = TestClient(app)
            resp = client.get("/api/tags")
            assert resp.status_code == 200

    def test_create_tag(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.create_tag.return_value = {
                "id": str(uuid.uuid4()),
                "name": "Important",
                "color": "#3b82f6",
            }
            client = TestClient(app)
            resp = client.post("/api/tags", json={"name": "Important"})
            assert resp.status_code == 201

    def test_update_tag(self) -> None:
        tag_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.update_tag.return_value = {"id": tag_id, "name": "Renamed"}
            client = TestClient(app)
            resp = client.patch(f"/api/tags/{tag_id}", json={"name": "Renamed"})
            assert resp.status_code == 200

    def test_update_tag_not_found(self) -> None:
        tag_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.update_tag.return_value = None
            client = TestClient(app)
            resp = client.patch(f"/api/tags/{tag_id}", json={"name": "X"})
            assert resp.status_code == 404

    def test_delete_tag(self) -> None:
        tag_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.delete_tag.return_value = True
            client = TestClient(app)
            resp = client.delete(f"/api/tags/{tag_id}")
            assert resp.status_code == 204

    def test_delete_tag_not_found(self) -> None:
        tag_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.delete_tag.return_value = False
            client = TestClient(app)
            resp = client.delete(f"/api/tags/{tag_id}")
            assert resp.status_code == 404

    def test_add_tag_to_conversation(self) -> None:
        conv_id = str(uuid.uuid4())
        tag_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            mock_storage.get_conversation_tags.return_value = [{"id": tag_id, "name": "Test"}]
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/tags/{tag_id}")
            assert resp.status_code == 201
            mock_storage.add_tag_to_conversation.assert_called_once()

    def test_add_tag_conversation_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        tag_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.get_conversation.return_value = None
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/tags/{tag_id}")
            assert resp.status_code == 404

    def test_remove_tag_from_conversation(self) -> None:
        conv_id = str(uuid.uuid4())
        tag_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            client = TestClient(app)
            resp = client.delete(f"/api/conversations/{conv_id}/tags/{tag_id}")
            assert resp.status_code == 204
            mock_storage.remove_tag_from_conversation.assert_called_once()

    def test_add_tag_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/conversations/bad/tags/also-bad")
        assert resp.status_code == 400


class TestListConversationsSpaceFilter:
    """GET /conversations?space_id= — space filtering."""

    def test_space_id_passed_to_storage(self) -> None:
        app = _make_app()
        sid = str(uuid.uuid4())
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.list_conversations.return_value = []
            client = TestClient(app)
            resp = client.get(f"/api/conversations?space_id={sid}")
            assert resp.status_code == 200
            mock_storage.list_conversations.assert_called_once()
            call_kwargs = mock_storage.list_conversations.call_args
            assert call_kwargs.kwargs.get("space_id") == sid or call_kwargs[1].get("space_id") == sid

    def test_space_id_omitted_passes_none(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.list_conversations.return_value = []
            client = TestClient(app)
            resp = client.get("/api/conversations")
            assert resp.status_code == 200
            call_kwargs = mock_storage.list_conversations.call_args
            assert call_kwargs.kwargs.get("space_id") is None or call_kwargs[1].get("space_id") is None


class TestCreateConversationSpaceId:
    """POST /conversations — space_id handling."""

    def test_create_with_valid_space_id(self) -> None:
        app = _make_app()
        sid = str(uuid.uuid4())
        with (
            patch("anteroom.routers.conversations.storage") as mock_storage,
            patch("anteroom.services.space_storage.get_space") as mock_get_space,
        ):
            mock_get_space.return_value = {"id": sid, "name": "test-space"}
            mock_storage.create_conversation.return_value = {
                "id": str(uuid.uuid4()),
                "title": "Test",
                "type": "chat",
                "space_id": sid,
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            }
            client = TestClient(app)
            resp = client.post("/api/conversations", json={"type": "chat", "space_id": sid})
            assert resp.status_code == 201
            mock_storage.create_conversation.assert_called_once()
            assert mock_storage.create_conversation.call_args.kwargs.get("space_id") == sid

    def test_create_with_invalid_space_id_format(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/conversations", json={"type": "chat", "space_id": "not-a-uuid"})
        assert resp.status_code == 400

    def test_create_with_nonexistent_space_id(self) -> None:
        app = _make_app()
        sid = str(uuid.uuid4())
        with patch("anteroom.services.space_storage.get_space") as mock_get_space:
            mock_get_space.return_value = None
            client = TestClient(app)
            resp = client.post("/api/conversations", json={"type": "chat", "space_id": sid})
            assert resp.status_code == 404

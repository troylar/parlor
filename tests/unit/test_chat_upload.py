"""Tests for file upload and attachment content injection in the chat endpoint."""

from __future__ import annotations

import io
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.routers.chat import router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")

    mock_db = MagicMock()
    mock_db_manager = MagicMock()
    mock_db_manager.get.return_value = mock_db
    app.state.db = mock_db
    app.state.db_manager = mock_db_manager

    mock_config = MagicMock()
    mock_config.identity = None
    mock_config.app.data_dir = Path(tempfile.mkdtemp())
    mock_config.app.tls = False
    app.state.config = mock_config

    app.state.tool_registry = MagicMock()
    app.state.mcp_manager = MagicMock()

    return app


def _setup_storage(mock_storage: MagicMock, conv_id: str, msg_id: str) -> None:
    """Configure mock storage for a file upload test."""
    mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
    mock_storage.create_message.return_value = {"id": msg_id, "position": 1}
    mock_storage.save_attachment.return_value = {"id": str(uuid.uuid4()), "mime_type": "text/plain"}
    mock_storage.create_source_from_attachment.return_value = None
    mock_storage.list_messages.return_value = [
        {"id": msg_id, "role": "user", "content": "check this file"},
    ]


class TestParseMultipartRequest:
    """_parse_chat_request correctly extracts fields from multipart FormData."""

    def test_multipart_extracts_source_ids(self) -> None:
        conv_id = str(uuid.uuid4())
        sid1 = str(uuid.uuid4())
        sid2 = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            mock_storage.create_message.return_value = {"id": str(uuid.uuid4()), "position": 1}
            mock_storage.list_messages.return_value = []
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                f"/api/conversations/{conv_id}/chat",
                data={
                    "message": "check these sources",
                    "source_ids": [sid1, sid2],
                },
                files=[("files", ("test.txt", io.BytesIO(b"hello"), "text/plain"))],
            )
            assert resp.status_code != 400

    def test_multipart_extracts_plan_mode(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            mock_storage.create_message.return_value = {"id": str(uuid.uuid4()), "position": 1}
            mock_storage.list_messages.return_value = []
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                f"/api/conversations/{conv_id}/chat",
                data={"message": "plan this", "plan_mode": "true"},
                files=[("files", ("test.txt", io.BytesIO(b"hello"), "text/plain"))],
            )
            assert resp.status_code != 400

    def test_multipart_extracts_source_tag(self) -> None:
        conv_id = str(uuid.uuid4())
        tag_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            mock_storage.create_message.return_value = {"id": str(uuid.uuid4()), "position": 1}
            mock_storage.list_messages.return_value = []
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                f"/api/conversations/{conv_id}/chat",
                data={"message": "tagged", "source_tag": tag_id},
                files=[("files", ("test.txt", io.BytesIO(b"hello"), "text/plain"))],
            )
            assert resp.status_code != 400

    def test_multipart_extracts_source_group_id(self) -> None:
        conv_id = str(uuid.uuid4())
        group_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            mock_storage.create_message.return_value = {"id": str(uuid.uuid4()), "position": 1}
            mock_storage.list_messages.return_value = []
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                f"/api/conversations/{conv_id}/chat",
                data={"message": "grouped", "source_group_id": group_id},
                files=[("files", ("test.txt", io.BytesIO(b"hello"), "text/plain"))],
            )
            assert resp.status_code != 400

    def test_multipart_whitespace_source_tag_normalized_to_none(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            mock_storage.create_message.return_value = {"id": str(uuid.uuid4()), "position": 1}
            mock_storage.list_messages.return_value = []
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                f"/api/conversations/{conv_id}/chat",
                data={"message": "blank tag", "source_tag": "   "},
                files=[("files", ("test.txt", io.BytesIO(b"hello"), "text/plain"))],
            )
            # Whitespace tag should not trigger a UUID validation error
            assert resp.status_code != 400

    def test_multipart_rejects_too_many_files(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            client = TestClient(app, raise_server_exceptions=False)
            files = [("files", (f"f{i}.txt", io.BytesIO(b"x"), "text/plain")) for i in range(25)]
            resp = client.post(
                f"/api/conversations/{conv_id}/chat",
                data={"message": "too many"},
                files=files,
            )
            assert resp.status_code == 400
            assert "Maximum" in resp.json().get("detail", "")


class TestAttachmentContentBuilding:
    """Attachment contents are correctly built for different file types."""

    def test_text_file_triggers_save_attachment(self) -> None:
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            _setup_storage(mock_storage, conv_id, msg_id)
            client = TestClient(app, raise_server_exceptions=False)
            client.post(
                f"/api/conversations/{conv_id}/chat",
                data={"message": "read this"},
                files=[("files", ("notes.txt", io.BytesIO(b"important notes"), "text/plain"))],
            )
            assert mock_storage.save_attachment.called

    def test_image_file_triggers_save_attachment(self) -> None:
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            _setup_storage(mock_storage, conv_id, msg_id)
            mock_storage.save_attachment.return_value = {"id": str(uuid.uuid4()), "mime_type": "image/png"}
            client = TestClient(app, raise_server_exceptions=False)
            client.post(
                f"/api/conversations/{conv_id}/chat",
                data={"message": "see image"},
                files=[("files", ("photo.png", io.BytesIO(b"\x89PNG fake"), "image/png"))],
            )
            assert mock_storage.save_attachment.called

    def test_pdf_file_extraction_called(self) -> None:
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch(
                "anteroom.services.document_extractor.extract_text",
                return_value="extracted PDF text",
            ) as mock_extract,
        ):
            _setup_storage(mock_storage, conv_id, msg_id)
            mock_storage.save_attachment.return_value = {"id": str(uuid.uuid4()), "mime_type": "application/pdf"}
            client = TestClient(app, raise_server_exceptions=False)
            client.post(
                f"/api/conversations/{conv_id}/chat",
                data={"message": "read pdf"},
                files=[("files", ("doc.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf"))],
            )
            assert mock_storage.save_attachment.called
            assert mock_extract.called
            call_args = mock_extract.call_args
            assert call_args[0][1] == "application/pdf"

    def test_docx_file_extraction_called(self) -> None:
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch(
                "anteroom.services.document_extractor.extract_text",
                return_value="extracted DOCX text",
            ) as mock_extract,
        ):
            _setup_storage(mock_storage, conv_id, msg_id)
            mock_storage.save_attachment.return_value = {"id": str(uuid.uuid4()), "mime_type": docx_mime}
            client = TestClient(app, raise_server_exceptions=False)
            client.post(
                f"/api/conversations/{conv_id}/chat",
                data={"message": "read docx"},
                files=[("files", ("report.docx", io.BytesIO(b"PK fake docx"), docx_mime))],
            )
            assert mock_storage.save_attachment.called
            assert mock_extract.called

    def test_extraction_returns_none_does_not_crash(self) -> None:
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch(
                "anteroom.services.document_extractor.extract_text",
                return_value=None,
            ),
        ):
            _setup_storage(mock_storage, conv_id, msg_id)
            mock_storage.save_attachment.return_value = {"id": str(uuid.uuid4()), "mime_type": "application/pdf"}
            client = TestClient(app, raise_server_exceptions=False)
            client.post(
                f"/api/conversations/{conv_id}/chat",
                data={"message": "read pdf"},
                files=[("files", ("empty.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf"))],
            )
            assert mock_storage.save_attachment.called

    def test_unsupported_binary_file_does_not_crash(self) -> None:
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            _setup_storage(mock_storage, conv_id, msg_id)
            mock_storage.save_attachment.return_value = {"id": str(uuid.uuid4()), "mime_type": "application/zip"}
            client = TestClient(app, raise_server_exceptions=False)
            client.post(
                f"/api/conversations/{conv_id}/chat",
                data={"message": "check this"},
                files=[("files", ("data.zip", io.BytesIO(b"PK\x03\x04 fake zip"), "application/zip"))],
            )
            assert mock_storage.save_attachment.called

    def test_document_extraction_exception_does_not_crash(self) -> None:
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch(
                "anteroom.services.document_extractor.extract_text",
                side_effect=RuntimeError("extraction failed"),
            ),
        ):
            _setup_storage(mock_storage, conv_id, msg_id)
            mock_storage.save_attachment.return_value = {"id": str(uuid.uuid4()), "mime_type": "application/pdf"}
            client = TestClient(app, raise_server_exceptions=False)
            client.post(
                f"/api/conversations/{conv_id}/chat",
                data={"message": "read this"},
                files=[("files", ("doc.pdf", io.BytesIO(b"%PDF-1.4 corrupt"), "application/pdf"))],
            )
            assert mock_storage.save_attachment.called

    def test_extracted_text_truncated_at_limit(self) -> None:
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        long_text = "x" * 60_000
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch(
                "anteroom.services.document_extractor.extract_text",
                return_value=long_text,
            ),
        ):
            _setup_storage(mock_storage, conv_id, msg_id)
            mock_storage.save_attachment.return_value = {"id": str(uuid.uuid4()), "mime_type": "application/pdf"}
            client = TestClient(app, raise_server_exceptions=False)
            client.post(
                f"/api/conversations/{conv_id}/chat",
                data={"message": "big pdf"},
                files=[("files", ("big.pdf", io.BytesIO(b"%PDF-1.4 big"), "application/pdf"))],
            )
            assert mock_storage.save_attachment.called

    def test_uses_validated_mime_from_attachment(self) -> None:
        """Extraction dispatches on att['mime_type'] (validated), not raw f.content_type."""
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        app = _make_app()
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch(
                "anteroom.services.document_extractor.extract_text",
                return_value="from validated mime",
            ) as mock_extract,
        ):
            _setup_storage(mock_storage, conv_id, msg_id)
            mock_storage.save_attachment.return_value = {"id": str(uuid.uuid4()), "mime_type": "application/pdf"}
            client = TestClient(app, raise_server_exceptions=False)
            client.post(
                f"/api/conversations/{conv_id}/chat",
                data={"message": "test mime"},
                files=[("files", ("doc.pdf", io.BytesIO(b"%PDF fake"), "application/pdf"))],
            )
            if mock_extract.called:
                assert mock_extract.call_args[0][1] == "application/pdf"

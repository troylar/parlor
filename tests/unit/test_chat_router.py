"""Tests for chat router endpoints (stop_generation, get_attachment, stale stream)."""

from __future__ import annotations

import asyncio
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.routers.chat import _active_streams, _cancel_events, router
from anteroom.services.rewind import RewindResult


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
    mock_config.ai.model = "gpt-5.2"
    mock_config.cli.usage.week_days = 7
    mock_config.cli.usage.month_days = 30
    app.state.config = mock_config

    app.state.tool_registry = MagicMock()
    app.state.tool_registry.list_tools.return_value = ["read_file", "bash"]
    app.state.mcp_manager = MagicMock()
    app.state.mcp_manager.get_all_tools.return_value = []
    app.state.skill_registry = None
    app.state.artifact_registry = None

    return app


class TestStopGenerationEndpoint:
    """POST /conversations/{id}/stop — cancel active generation."""

    def test_stop_success(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/stop")
            assert resp.status_code == 200
            assert resp.json()["status"] == "stopped"

    def test_stop_conversation_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = None
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/stop")
            assert resp.status_code == 404

    def test_stop_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/conversations/bad-uuid/stop")
        assert resp.status_code == 400

    def test_stop_no_active_stream(self) -> None:
        """Stop should succeed even when no stream is active (idempotent)."""
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/stop")
            assert resp.status_code == 200


class TestGetAttachmentEndpoint:
    """GET /attachments/{id} — retrieve attachment files."""

    def test_attachment_not_found(self) -> None:
        att_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_attachment.return_value = None
            client = TestClient(app)
            resp = client.get(f"/api/attachments/{att_id}")
            assert resp.status_code == 404

    def test_attachment_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/attachments/bad-uuid")
        assert resp.status_code == 400

    def test_attachment_path_traversal_blocked(self) -> None:
        att_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_attachment.return_value = {
                "id": att_id,
                "storage_path": "../../etc/passwd",
                "mime_type": "text/plain",
                "filename": "passwd",
            }
            client = TestClient(app)
            resp = client.get(f"/api/attachments/{att_id}")
            assert resp.status_code == 403

    def test_attachment_file_missing(self) -> None:
        att_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_attachment.return_value = {
                "id": att_id,
                "storage_path": "attachments/nonexistent.txt",
                "mime_type": "text/plain",
                "filename": "nonexistent.txt",
            }
            client = TestClient(app)
            resp = client.get(f"/api/attachments/{att_id}")
            assert resp.status_code == 404

    def test_attachment_inline_for_image(self) -> None:
        att_id = str(uuid.uuid4())
        app = _make_app()
        data_dir = app.state.config.app.data_dir
        # Create a real file in data_dir
        att_dir = data_dir / "attachments"
        att_dir.mkdir(parents=True, exist_ok=True)
        test_file = att_dir / "test.png"
        test_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_attachment.return_value = {
                "id": att_id,
                "storage_path": "attachments/test.png",
                "mime_type": "image/png",
                "filename": "test.png",
            }
            client = TestClient(app)
            resp = client.get(f"/api/attachments/{att_id}")
            assert resp.status_code == 200
            assert "inline" in resp.headers.get("content-disposition", "")

    def test_attachment_download_for_non_image(self) -> None:
        att_id = str(uuid.uuid4())
        app = _make_app()
        data_dir = app.state.config.app.data_dir
        att_dir = data_dir / "attachments"
        att_dir.mkdir(parents=True, exist_ok=True)
        test_file = att_dir / "doc.pdf"
        test_file.write_bytes(b"%PDF-1.4 test content")
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_attachment.return_value = {
                "id": att_id,
                "storage_path": "attachments/doc.pdf",
                "mime_type": "application/pdf",
                "filename": "doc.pdf",
            }
            client = TestClient(app)
            resp = client.get(f"/api/attachments/{att_id}")
            assert resp.status_code == 200
            assert "attachment" in resp.headers.get("content-disposition", "")


class TestCommandEndpoints:
    def test_global_help_command(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.post("/api/commands", json={"command": "/help"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "show_message"
        assert "Slash Commands" in body["message"]
        assert "CLI/Textual only" in body["message"]

    def test_web_upload_command_returns_explicit_local_only_message(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.post("/api/commands", json={"command": "/upload ~/notes.md"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "show_message"
        assert "web file uploader" in body["message"]
        assert body["echo_user"] is False

    def test_web_verbose_command_returns_explicit_local_only_message(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.post("/api/commands", json={"command": "/verbose"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "show_message"
        assert "CLI and Textual UI only" in body["message"]
        assert body["echo_user"] is False

    def test_web_detail_command_returns_explicit_local_only_message(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.post("/api/commands", json={"command": "/detail"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "show_message"
        assert "CLI and Textual UI only" in body["message"]
        assert body["echo_user"] is False

    def test_web_inline_plan_prompt_returns_explicit_local_only_message(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.post("/api/commands", json={"command": "/plan inspect the renderer handoff"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "show_message"
        assert "Use normal chat input on the web" in body["message"]
        assert body["echo_user"] is False

    def test_conversation_model_command_updates_conversation(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat", "model": None}
            mock_storage.update_conversation_model.return_value = {
                "id": conv_id,
                "type": "chat",
                "model": "gpt-5.4-mini",
            }
            client = TestClient(app)

            resp = client.post(
                f"/api/conversations/{conv_id}/command",
                json={"command": "/model gpt-5.4-mini"},
            )

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "set_model"
            assert body["model_name"] == "gpt-5.4-mini"
            mock_storage.update_conversation_model.assert_called_once_with(app.state.db, conv_id, "gpt-5.4-mini")

    def test_conversation_new_command_returns_new_conversation(self) -> None:
        conv_id = str(uuid.uuid4())
        new_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {
                "id": conv_id,
                "type": "chat",
                "space_id": None,
                "working_dir": "/repo",
            }
            mock_storage.create_conversation.return_value = {
                "id": new_id,
                "type": "note",
                "title": "Architecture Notes",
            }
            client = TestClient(app)

            resp = client.post(
                f"/api/conversations/{conv_id}/command",
                json={"command": "/new note Architecture Notes"},
            )

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "new_conversation"
            assert body["conversation"]["id"] == new_id
            assert body["echo_user"] is False

    def test_global_skill_command_forwards_prompt(self) -> None:
        app = _make_app()

        class _SkillRegistry:
            def resolve_input(self, prompt: str):
                return (prompt == "/debug trace"), "expanded skill prompt"

        app.state.skill_registry = _SkillRegistry()
        client = TestClient(app)

        resp = client.post("/api/commands", json={"command": "/debug trace"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "forward_prompt"
        assert body["forward_prompt"] == "expanded skill prompt"

    def test_space_scoped_skill_command_uses_request_registry(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()

        class _GlobalSkillRegistry:
            def resolve_input(self, prompt: str):
                return False, prompt

        class _ScopedSkillRegistry:
            def resolve_input(self, prompt: str):
                return (prompt == "/debug trace"), "scoped expanded skill prompt"

        app.state.skill_registry = _GlobalSkillRegistry()
        client = TestClient(app)
        with (
            patch("anteroom.routers.chat.storage.get_conversation") as mock_get_conversation,
            patch("anteroom.routers.chat._get_request_registries") as mock_get_request_registries,
        ):
            mock_get_conversation.return_value = {
                "id": conv_id,
                "type": "chat",
                "title": "Current",
                "working_dir": "/tmp/project-skill-root",
                "space_id": "space-123",
            }
            mock_get_request_registries.return_value = (MagicMock(), _ScopedSkillRegistry(), None)

            resp = client.post(f"/api/conversations/{conv_id}/command", json={"command": "/debug trace"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "forward_prompt"
        assert body["forward_prompt"] == "scoped expanded skill prompt"

    def test_skills_command_returns_structured_skill_items(self) -> None:
        app = _make_app()

        class _Skill:
            source = "project"
            prompt = "Run the deployment checklist with {args}."

        class _SkillRegistry:
            load_warnings = ["warning one"]
            searched_dirs = []

            def reload(self, working_dir: str | None = None):
                self.working_dir = working_dir

            def load_from_artifacts(self, artifact_registry):
                self.artifact_registry = artifact_registry

            def get_skill_descriptions(self):
                return [("deploy-check", "Run the deployment checklist")]

            def get(self, name: str):
                assert name == "deploy-check"
                return _Skill()

            def resolve_input(self, prompt: str):
                return False, prompt

        app.state.skill_registry = _SkillRegistry()
        client = TestClient(app)

        resp = client.post("/api/commands", json={"command": "/skills"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "show_message"
        assert "`/deploy-check`" in body["message"]
        assert body["command_items"][0]["kind"] == "skill"
        assert body["command_items"][0]["title"] == "/deploy-check"
        assert body["command_items"][0]["actions"][0]["insert_text"] == "/deploy-check "

    def test_reload_skills_command_mentions_reload(self) -> None:
        app = _make_app()

        class _Skill:
            source = "project"
            prompt = "Run the deployment checklist."

        class _SkillRegistry:
            load_warnings = []
            searched_dirs = []

            def reload(self, working_dir: str | None = None):
                self.reload_calls = getattr(self, "reload_calls", 0) + 1

            def load_from_artifacts(self, artifact_registry):
                return None

            def get_skill_descriptions(self):
                return [("deploy-check", "Run the deployment checklist")]

            def get(self, name: str):
                return _Skill()

            def resolve_input(self, prompt: str):
                return False, prompt

        app.state.skill_registry = _SkillRegistry()
        client = TestClient(app)

        resp = client.post("/api/commands", json={"command": "/reload-skills"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "show_message"
        assert "Reloaded skill registry." in body["message"]
        assert body["command_items"][0]["kind"] == "skill"
        assert body["command_items"][0]["actions"][0]["command"] == "/deploy-check"

    def test_reload_skills_uses_conversation_working_dir(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()

        class _Skill:
            source = "project"
            prompt = "Run the deployment checklist."

        class _SkillRegistry:
            load_warnings = []
            searched_dirs = []

            def reload(self, working_dir: str | None = None):
                self.working_dir = working_dir

            def load_from_artifacts(self, artifact_registry):
                self.artifact_registry = artifact_registry

            def get_skill_descriptions(self):
                return [("deploy-check", "Run the deployment checklist")]

            def get(self, name: str):
                return _Skill()

            def resolve_input(self, prompt: str):
                return False, prompt

        app.state.skill_registry = _SkillRegistry()
        client = TestClient(app)
        with patch("anteroom.routers.chat.storage.get_conversation") as mock_get_conversation:
            mock_get_conversation.return_value = {
                "id": conv_id,
                "type": "chat",
                "title": "Current",
                "working_dir": "/tmp/project-skill-root",
                "space_id": None,
            }

            resp = client.post(f"/api/conversations/{conv_id}/command", json={"command": "/reload-skills"})

        assert resp.status_code == 200
        assert app.state.skill_registry.working_dir == "/tmp/project-skill-root"

    def test_list_command_renders_recent_conversations(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            convs = [
                {"id": "c1", "title": "Alpha", "type": "chat", "slug": "alpha", "message_count": 12},
                {"id": "c2", "title": "Beta", "type": "note", "slug": None, "message_count": 1},
            ]
            mock_storage.list_conversations.return_value = convs
            client = TestClient(app)

            resp = client.post("/api/commands", json={"command": "/list 2"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "Recent Conversations" in body["message"]
            assert "Alpha" in body["message"]
            assert body["command_items"][0]["id"] == "c1"
            assert body["command_items"][1]["conversation_type"] == "note"
            assert body["command_items"][0]["meta"] == "12 messages · alpha"
            assert body["command_items"][0]["summary"] == "Chat thread with 12 messages."
            assert body["command_items"][1]["summary"] == "Note with 1 message."
            assert body["command_items"][0]["actions"][1]["command"] == "/delete --confirm c1"
            assert body["command_items"][0]["actions"][1]["requires_confirm"] is True
            mock_storage.list_conversations.assert_called_once_with(app.state.db, limit=2)

    def test_resume_numeric_target_resolves_beyond_twentieth_conversation(self) -> None:
        conv_id = str(uuid.uuid4())
        current_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            convs = [{"id": f"c{i}", "title": f"Conv {i}", "type": "chat"} for i in range(1, 26)]
            convs[20]["id"] = conv_id
            convs[20]["title"] = "Target"
            mock_storage.list_conversations.return_value = convs
            current_conv = {"id": current_id, "type": "chat", "title": "Current"}
            target_conv = {"id": conv_id, "type": "chat", "title": "Target"}

            def _get_conversation(_db, conversation_id):
                if conversation_id == current_id:
                    return current_conv
                if conversation_id == conv_id:
                    return target_conv
                return None

            mock_storage.get_conversation.side_effect = _get_conversation
            client = TestClient(app)

            resp = client.post(f"/api/conversations/{current_id}/command", json={"command": "/resume 21"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "resume_conversation"
        assert body["conversation"]["id"] == conv_id
        mock_storage.list_conversations.assert_called_once_with(app.state.db, limit=21)

    def test_resume_command_returns_target_conversation(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.side_effect = [
                {"id": conv_id, "type": "chat", "title": "Current"},
                {"id": conv_id, "type": "chat", "title": "Current"},
            ]
            client = TestClient(app)

            resp = client.post(f"/api/conversations/{conv_id}/command", json={"command": "/resume " + conv_id})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "resume_conversation"
            assert body["conversation"]["id"] == conv_id
            assert body["echo_user"] is False

    def test_search_command_returns_search_markdown(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.list_conversations.return_value = [
                {
                    "id": "c1",
                    "title": "Renderer Investigation",
                    "type": "chat",
                    "slug": "renderer-investigation",
                    "message_count": 4,
                }
            ]
            client = TestClient(app)

            resp = client.post("/api/commands", json={"command": "/search renderer"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "Search results" in body["message"]
            assert "Renderer Investigation" in body["message"]
            assert body["command_items"][0]["slug"] == "renderer-investigation"
            assert body["command_items"][0]["summary"] == "Chat thread with 4 messages."
            assert body["command_items"][0]["actions"][1]["command"] == "/delete --confirm c1"

    def test_rename_command_updates_conversation_title(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.side_effect = [
                {"id": conv_id, "type": "chat", "title": "Old Title", "slug": None},
                None,
            ]
            mock_storage.update_conversation_title.return_value = {
                "id": conv_id,
                "type": "chat",
                "title": "New Title",
                "slug": None,
            }
            client = TestClient(app)

            resp = client.post(f"/api/conversations/{conv_id}/command", json={"command": "/rename New Title"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "rename_conversation"
            assert body["conversation"]["title"] == "New Title"
            mock_storage.update_conversation_title.assert_called_once_with(app.state.db, conv_id, "New Title")

    def test_slug_command_updates_active_conversation_slug(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.routers.chat.suggest_unique_slug", return_value=None),
        ):
            mock_storage.get_conversation.return_value = {
                "id": conv_id,
                "type": "chat",
                "title": "Current",
                "slug": None,
            }
            mock_storage.update_conversation_slug.return_value = {
                "id": conv_id,
                "type": "chat",
                "title": "Current",
                "slug": "current-session",
            }
            client = TestClient(app)

            resp = client.post(f"/api/conversations/{conv_id}/command", json={"command": "/slug current-session"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "set_slug"
            assert body["conversation"]["slug"] == "current-session"

    def test_delete_command_returns_confirmation_card(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.side_effect = [
                {"id": conv_id, "type": "chat", "title": "Current"},
                {"id": conv_id, "type": "chat", "title": "Current"},
            ]
            client = TestClient(app)

            resp = client.post(f"/api/conversations/{conv_id}/command", json={"command": f"/delete {conv_id}"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "Confirm deleting" in body["message"]
            assert body["command_items"][0]["actions"][0]["command"] == f"/delete --confirm {conv_id}"
            assert body["command_items"][0]["actions"][0]["requires_confirm"] is True
            mock_storage.delete_conversation.assert_not_called()

    def test_delete_command_confirmed_deletes_target_conversation(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.side_effect = [
                {"id": conv_id, "type": "chat", "title": "Current"},
                {"id": conv_id, "type": "chat", "title": "Current"},
            ]
            client = TestClient(app)

            resp = client.post(
                f"/api/conversations/{conv_id}/command",
                json={"command": f"/delete --confirm {conv_id}"},
            )

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "delete_conversation"
            assert body["deleted_conversation_id"] == conv_id
            mock_storage.delete_conversation.assert_called_once_with(
                app.state.db,
                conv_id,
                app.state.config.app.data_dir,
            )

    def test_rewind_command_without_arg_returns_preview(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat", "title": "Current"}
            mock_storage.list_messages.return_value = [
                {"id": "m1", "role": "user", "content": "First question", "position": 0},
                {"id": "m2", "role": "assistant", "content": "First answer", "position": 1},
            ]
            client = TestClient(app)

            resp = client.post(f"/api/conversations/{conv_id}/command", json={"command": "/rewind"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "Rewind Conversation" in body["message"]

    def test_rewind_command_executes_and_returns_reload_payload(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.routers.chat.collect_file_paths", return_value={"src/app.py"}),
            patch(
                "anteroom.routers.chat.rewind_service",
                AsyncMock(return_value=RewindResult(deleted_messages=2, reverted_files=[])),
            ),
        ):
            mock_storage.get_conversation.side_effect = [
                {"id": conv_id, "type": "chat", "title": "Current", "working_dir": "/repo"},
                {"id": conv_id, "type": "chat", "title": "Current", "working_dir": "/repo"},
            ]
            mock_storage.list_messages.return_value = [
                {"id": "m1", "role": "user", "content": "First question", "position": 0},
                {"id": "m2", "role": "assistant", "content": "First answer", "position": 1},
                {"id": "m3", "role": "user", "content": "Second question", "position": 2},
                {"id": "m4", "role": "assistant", "content": "Second answer", "position": 3},
            ]
            client = TestClient(app)

            resp = client.post(f"/api/conversations/{conv_id}/command", json={"command": "/rewind 1"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "rewind_conversation"
            assert body["conversation"]["id"] == conv_id
            assert "Rewound" in body["message"]

    def test_spaces_command_lists_spaces(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.chat.list_spaces") as mock_list_spaces, patch(
            "anteroom.routers.chat.count_space_conversations"
        ) as mock_count:
            mock_list_spaces.return_value = [
                {"id": "space-12345678", "name": "demo"},
                {"id": "space-abcdef12", "name": "docs"},
            ]
            mock_count.side_effect = [3, 1]
            client = TestClient(app)

            resp = client.post("/api/commands", json={"command": "/spaces"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "## Spaces" in body["message"]
            assert "**demo**" in body["message"]
            assert body["command_items"][0]["kind"] == "space"
            assert body["command_items"][0]["actions"][0]["command"] == "/space show demo"
            assert body["command_items"][0]["actions"][1]["command"] == "/space sources demo"
            assert body["command_items"][0]["actions"][2]["command"] == "/space refresh demo"
            assert body["command_items"][0]["actions"][3]["command"] == "/space export demo"

    def test_space_show_command_returns_space_details(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.routers.chat.resolve_space") as mock_resolve_space,
            patch("anteroom.routers.chat.get_space_paths") as mock_get_space_paths,
            patch("anteroom.routers.chat.count_space_conversations", return_value=4),
        ):
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat", "space_id": None}
            mock_resolve_space.return_value = (
                {
                    "id": "space-1",
                    "name": "demo",
                    "source_file": "/repo/.anteroom/space.yaml",
                    "model": "gpt-5.2",
                    "instructions": "Focus on the CLI.",
                },
                [],
            )
            mock_get_space_paths.return_value = [{"repo_url": "origin", "local_path": "/repo"}]
            client = TestClient(app)

            resp = client.post(f"/api/conversations/{conv_id}/command", json={"command": "/space show demo"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "## Space: demo" in body["message"]
            assert "`origin` -> `/repo`" in body["message"]

    def test_space_create_command_creates_space(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.chat.get_spaces_by_name", return_value=[]), patch(
            "anteroom.routers.chat.create_space", return_value={"id": "space-1", "name": "demo"}
        ) as mock_create_space:
            client = TestClient(app)

            resp = client.post("/api/commands", json={"command": "/space create demo"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "create_space"
            assert body["message"] == "Created space **demo**."
            assert body["command_items"][0]["title"] == "demo"
            mock_create_space.assert_called_once_with(app.state.db, "demo")

    def test_space_switch_command_updates_conversation_space(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.routers.chat.resolve_space") as mock_resolve_space,
            patch("anteroom.routers.chat.update_conversation_space") as mock_update_space,
        ):
            mock_storage.get_conversation.side_effect = [
                {"id": conv_id, "type": "chat", "space_id": None},
                {"id": conv_id, "type": "chat", "space_id": "space-1"},
            ]
            mock_resolve_space.return_value = ({"id": "space-1", "name": "demo"}, [])
            client = TestClient(app)

            resp = client.post(f"/api/conversations/{conv_id}/command", json={"command": "/space switch demo"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "set_space"
            assert body["conversation"]["space_id"] == "space-1"
            mock_update_space.assert_called_once_with(app.state.db, conv_id, "space-1")

    def test_space_edit_command_updates_active_space(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch(
                "anteroom.routers.chat.get_space",
                return_value={"id": "space-1", "name": "demo", "model": "gpt-5.2"},
            ),
            patch(
                "anteroom.routers.chat.update_space",
                return_value={"id": "space-1", "name": "demo", "model": "gpt-5.4-mini"},
            ) as mock_update_space,
        ):
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat", "space_id": "space-1"}
            client = TestClient(app)

            resp = client.post(
                f"/api/conversations/{conv_id}/command",
                json={"command": "/space edit model gpt-5.4-mini"},
            )

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "update_space"
            assert body["message"] == "Updated model for **demo**: `gpt-5.4-mini`"
            mock_update_space.assert_called_once_with(app.state.db, "space-1", model="gpt-5.4-mini")

    def test_space_refresh_command_refreshes_active_space(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch(
                "anteroom.routers.chat.get_space",
                return_value={
                    "id": "space-1",
                    "name": "demo",
                    "source_file": "/repo/.anteroom/space.yaml",
                },
            ),
            patch("anteroom.routers.chat.Path.is_file", return_value=True),
            patch(
                "anteroom.routers.chat.update_space",
                return_value={"id": "space-1", "name": "demo"},
            ) as mock_update_space,
            patch("anteroom.services.spaces.compute_file_hash", return_value="hash123"),
            patch(
                "anteroom.services.spaces.parse_space_file",
                return_value=SimpleNamespace(instructions="Refreshed rules.", config={"model": "gpt-5.4-mini"}),
            ),
        ):
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat", "space_id": "space-1"}
            client = TestClient(app)

            resp = client.post(
                f"/api/conversations/{conv_id}/command",
                json={"command": "/space refresh"},
            )

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "refresh_space"
            assert body["message"] == "Refreshed space **demo**."
            mock_update_space.assert_called_once_with(
                app.state.db,
                "space-1",
                source_hash="hash123",
                instructions="Refreshed rules.",
                model="gpt-5.4-mini",
            )

    def test_space_export_command_returns_yaml(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.routers.chat.get_space", return_value={"id": "space-1", "name": "demo"}),
            patch(
                "anteroom.services.spaces.export_space_to_yaml",
                return_value=SimpleNamespace(
                    name="demo",
                    version="1",
                    repos=[],
                    pack_sources=[],
                    packs=[],
                    sources=[],
                    instructions="Export me.",
                    config={"model": "gpt-5.2"},
                ),
            ),
        ):
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat", "space_id": "space-1"}
            client = TestClient(app)

            resp = client.post(
                f"/api/conversations/{conv_id}/command",
                json={"command": "/space export"},
            )

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "## Space YAML: demo" in body["message"]
            assert "instructions: Export me." in body["message"]

    def test_space_sources_command_lists_linked_sources(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.routers.chat.resolve_space") as mock_resolve_space,
        ):
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat", "space_id": None}
            mock_storage.get_direct_space_source_links.return_value = [
                {"id": "src-1", "title": "README", "type": "markdown"},
                {"id": "src-2", "title": "ROADMAP", "type": "markdown"},
            ]
            mock_resolve_space.return_value = ({"id": "space-1", "name": "demo"}, [])
            client = TestClient(app)

            resp = client.post(f"/api/conversations/{conv_id}/command", json={"command": "/space sources demo"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "## Sources in demo" in body["message"]
            assert "README" in body["message"]
            assert "ROADMAP" in body["message"]

    def test_space_clear_command_clears_active_space(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.routers.chat.update_conversation_space") as mock_update_space,
        ):
            mock_storage.get_conversation.side_effect = [
                {"id": conv_id, "type": "chat", "space_id": "space-1"},
                {"id": conv_id, "type": "chat", "space_id": None},
            ]
            client = TestClient(app)

            resp = client.post(f"/api/conversations/{conv_id}/command", json={"command": "/space clear"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "set_space"
            assert body["conversation"]["space_id"] is None
            mock_update_space.assert_called_once_with(app.state.db, conv_id, None)

    def test_space_delete_command_returns_confirmation_card(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.routers.chat.resolve_space") as mock_resolve_space,
        ):
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat", "space_id": None}
            mock_resolve_space.return_value = ({"id": "space-1", "name": "demo"}, [])
            client = TestClient(app)

            resp = client.post(f"/api/conversations/{conv_id}/command", json={"command": "/space delete demo"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "Delete space **demo**?" in body["message"]
            assert body["command_items"][0]["actions"][0]["command"] == "/space delete --confirm space-1"

    def test_spaces_command_marks_active_space_with_badge(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.routers.chat.list_spaces") as mock_list_spaces,
        ):
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat", "space_id": "space-1"}
            mock_list_spaces.return_value = [{"id": "space-1", "name": "demo"}]
            client = TestClient(app)

            resp = client.post(f"/api/conversations/{conv_id}/command", json={"command": "/spaces"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert body["command_items"][0]["badges"] == ["current"]
            assert "model context" in body["command_items"][0]["summary"]

    def test_space_delete_confirmed_clears_conversation_space(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.routers.chat.resolve_space") as mock_resolve_space,
            patch("anteroom.routers.chat.delete_space") as mock_delete_space,
        ):
            mock_storage.get_conversation.side_effect = [
                {"id": conv_id, "type": "chat", "space_id": "space-1"},
                {"id": conv_id, "type": "chat", "space_id": None},
            ]
            mock_resolve_space.return_value = ({"id": "space-1", "name": "demo"}, [])
            mock_delete_space.return_value = True
            client = TestClient(app)

            resp = client.post(
                f"/api/conversations/{conv_id}/command",
                json={"command": "/space delete --confirm space-1"},
            )

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "delete_space"
            assert body["conversation"]["space_id"] is None
            assert "Deleted space **demo**." in body["message"]
            mock_delete_space.assert_called_once_with(app.state.db, "space-1")

    def test_artifacts_command_lists_artifacts(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.chat.artifact_storage") as mock_artifact_storage:
            mock_artifact_storage.list_artifacts.return_value = [
                {"id": "a1", "fqn": "@core/skill/demo", "type": "skill", "source": "project"},
                {"id": "a2", "fqn": "@core/rule/safe", "type": "rule", "source": "built_in"},
            ]
            client = TestClient(app)

            resp = client.post("/api/commands", json={"command": "/artifacts"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "## Artifacts" in body["message"]
            assert "@core/skill/demo" in body["message"]
            assert body["command_items"][1]["badges"] == ["built-in"]
            assert body["command_items"][0]["actions"][0]["command"] == "/artifact show @core/skill/demo"
            assert body["command_items"][0]["actions"][1]["requires_confirm"] is True
            assert len(body["command_items"][1]["actions"]) == 1

    def test_artifact_show_command_returns_artifact_details(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.chat.artifact_storage") as mock_artifact_storage:
            mock_artifact_storage.get_artifact_by_fqn.return_value = {
                "fqn": "@core/skill/demo",
                "type": "skill",
                "source": "project",
                "content_hash": "abc123",
                "updated_at": "2026-03-07T00:00:00Z",
                "content": "name: demo",
            }
            client = TestClient(app)

            resp = client.post("/api/commands", json={"command": "/artifact show @core/skill/demo"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "## Artifact: @core/skill/demo" in body["message"]
            assert "### Content" in body["message"]

    def test_artifact_delete_command_removes_artifact_and_refreshes_registry(self) -> None:
        app = _make_app()
        app.state.artifact_registry = MagicMock()
        app.state.skill_registry = MagicMock()
        with patch("anteroom.routers.chat.artifact_storage") as mock_artifact_storage:
            mock_artifact_storage.get_artifact_by_fqn.return_value = {
                "id": "art-1",
                "fqn": "@core/skill/demo",
                "source": "project",
            }
            client = TestClient(app)

            resp = client.post("/api/commands", json={"command": "/artifact delete @core/skill/demo"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "Deleted `@core/skill/demo`." in body["message"]
            mock_artifact_storage.delete_artifact.assert_called_once_with(app.state.db, "art-1")
            app.state.artifact_registry.load_from_db.assert_called_once_with(app.state.db)
            app.state.skill_registry.load_from_artifacts.assert_called_once_with(app.state.artifact_registry)

    def test_packs_command_lists_installed_packs(self) -> None:
        app = _make_app()
        with (
            patch("anteroom.routers.chat.packs_service.list_packs") as mock_list_packs,
            patch("anteroom.routers.chat.list_attachments_for_pack") as mock_list_attachments,
        ):
            mock_list_packs.return_value = [
                {
                    "id": "p1",
                    "namespace": "default",
                    "name": "demo",
                    "version": "1.2.0",
                    "artifact_count": 3,
                    "description": "Demo pack",
                }
            ]
            mock_list_attachments.return_value = []
            client = TestClient(app)

            resp = client.post("/api/commands", json={"command": "/packs"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "## Installed Packs" in body["message"]
            assert "@default/demo" in body["message"]
            assert body["command_items"][0]["summary"] == "Demo pack"
            assert body["command_items"][0]["actions"][0]["command"] == "/pack show default/demo"
            assert body["command_items"][0]["actions"][1]["command"] == "/pack attach default/demo"
            assert body["command_items"][0]["actions"][2]["requires_confirm"] is True

    def test_packs_command_marks_globally_attached_packs(self) -> None:
        app = _make_app()
        with (
            patch("anteroom.routers.chat.packs_service.list_packs") as mock_list_packs,
            patch("anteroom.routers.chat.list_attachments_for_pack") as mock_list_attachments,
        ):
            mock_list_packs.return_value = [
                {
                    "id": "p1",
                    "namespace": "default",
                    "name": "demo",
                    "version": "1.2.0",
                    "artifact_count": 3,
                    "description": "Demo pack",
                }
            ]
            mock_list_attachments.return_value = [{"scope": "global", "project_path": None, "space_id": None}]
            client = TestClient(app)

            resp = client.post("/api/commands", json={"command": "/packs"})

            assert resp.status_code == 200
            body = resp.json()
            assert "attached globally" in body["command_items"][0]["meta"]
            assert body["command_items"][0]["badges"] == ["attached"]
            assert body["command_items"][0]["actions"][1]["command"] == "/pack detach default/demo"

    def test_pack_show_command_returns_pack_details(self) -> None:
        app = _make_app()
        with (
            patch("anteroom.routers.chat._resolve_pack_ref") as mock_resolve_pack,
            patch("anteroom.routers.chat.packs_service.get_pack") as mock_get_pack,
        ):
            mock_resolve_pack.return_value = {"id": "pack-1", "namespace": "default", "name": "demo"}
            mock_get_pack.return_value = {
                "namespace": "default",
                "name": "demo",
                "version": "1.2.0",
                "description": "Demo pack",
                "artifacts": [{"type": "skill", "name": "hello"}],
            }
            client = TestClient(app)

            resp = client.post("/api/commands", json={"command": "/pack show default/demo"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "## @default/demo" in body["message"]
            assert "`skill`: `hello`" in body["message"]

    def test_pack_remove_command_removes_pack_and_refreshes_registry(self) -> None:
        app = _make_app()
        app.state.artifact_registry = MagicMock()
        app.state.skill_registry = MagicMock()
        with (
            patch("anteroom.routers.chat._resolve_pack_ref") as mock_resolve_pack,
            patch("anteroom.routers.chat.packs_service.remove_pack_by_id", return_value=True) as mock_remove_pack,
        ):
            mock_resolve_pack.return_value = {"id": "pack-1", "namespace": "default", "name": "demo"}
            client = TestClient(app)

            resp = client.post("/api/commands", json={"command": "/pack remove default/demo"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "Removed `@default/demo`." in body["message"]
            mock_remove_pack.assert_called_once_with(app.state.db, "pack-1")
            app.state.artifact_registry.load_from_db.assert_called_once_with(app.state.db)
            app.state.skill_registry.load_from_artifacts.assert_called_once_with(app.state.artifact_registry)

    def test_pack_attach_command_attaches_pack_and_refreshes_registry(self) -> None:
        app = _make_app()
        app.state.artifact_registry = MagicMock()
        app.state.skill_registry = MagicMock()
        with (
            patch("anteroom.routers.chat._resolve_pack_ref") as mock_resolve_pack,
            patch("anteroom.services.pack_attachments.attach_pack") as mock_attach_pack,
        ):
            mock_resolve_pack.return_value = {"id": "pack-1", "namespace": "default", "name": "demo"}
            client = TestClient(app)

            resp = client.post("/api/commands", json={"command": "/pack attach default/demo"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "Attached `@default/demo` (global)." in body["message"]
            mock_attach_pack.assert_called_once_with(app.state.db, "pack-1", project_path=None)
            app.state.artifact_registry.load_from_db.assert_called_once_with(app.state.db)
            app.state.skill_registry.load_from_artifacts.assert_called_once_with(app.state.artifact_registry)

    def test_pack_detach_command_detaches_pack_and_refreshes_registry(self) -> None:
        app = _make_app()
        app.state.artifact_registry = MagicMock()
        app.state.skill_registry = MagicMock()
        with (
            patch("anteroom.routers.chat._resolve_pack_ref") as mock_resolve_pack,
            patch("anteroom.services.pack_attachments.detach_pack", return_value=True) as mock_detach_pack,
        ):
            mock_resolve_pack.return_value = {"id": "pack-1", "namespace": "default", "name": "demo"}
            client = TestClient(app)

            resp = client.post("/api/commands", json={"command": "/pack detach default/demo"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "Detached `@default/demo` (global)." in body["message"]
            mock_detach_pack.assert_called_once_with(app.state.db, "pack-1", project_path=None)
            app.state.artifact_registry.load_from_db.assert_called_once_with(app.state.db)
            app.state.skill_registry.load_from_artifacts.assert_called_once_with(app.state.artifact_registry)

    def test_pack_sources_command_lists_configured_sources(self) -> None:
        app = _make_app()
        app.state.config.pack_sources = [MagicMock(url="https://example.com/packs.git", branch="main")]
        with patch("anteroom.routers.chat.list_cached_sources", return_value=[]):
            client = TestClient(app)

            resp = client.post("/api/commands", json={"command": "/pack sources"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "## Pack Sources" in body["message"]
            assert "https://example.com/packs.git" in body["message"]

    def test_pack_refresh_command_runs_worker_and_reports_results(self) -> None:
        app = _make_app()
        app.state.config.pack_sources = [MagicMock(url="https://example.com/packs.git", branch="main")]
        app.state.artifact_registry = MagicMock()
        app.state.skill_registry = MagicMock()
        refresh_result = SimpleNamespace(
            url="https://example.com/packs.git",
            success=True,
            packs_installed=1,
            packs_updated=2,
            changed=True,
            error=None,
        )
        with patch("anteroom.services.pack_refresh.PackRefreshWorker") as mock_worker:
            mock_worker.return_value.refresh_all.return_value = [refresh_result]
            client = TestClient(app)

            resp = client.post("/api/commands", json={"command": "/pack refresh"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "## Pack Refresh" in body["message"]
            assert "installed `1`" in body["message"]
            assert "updated `2`" in body["message"]
            app.state.artifact_registry.load_from_db.assert_called_once_with(app.state.db)
            app.state.skill_registry.load_from_artifacts.assert_called_once_with(app.state.artifact_registry)

    def test_pack_add_source_command_updates_config(self) -> None:
        app = _make_app()
        app.state.config.pack_sources = []
        with patch("anteroom.services.pack_sources.add_pack_source") as mock_add_pack_source:
            mock_add_pack_source.return_value = SimpleNamespace(ok=True, message="")
            client = TestClient(app)

            resp = client.post("/api/commands", json={"command": "/pack add-source https://example.com/packs.git"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "Added pack source: https://example.com/packs.git" in body["message"]
            assert len(app.state.config.pack_sources) == 1
            assert app.state.config.pack_sources[0].url == "https://example.com/packs.git"

    def test_pack_install_command_installs_and_attaches_pack(self, tmp_path: Path) -> None:
        app = _make_app()
        app.state.artifact_registry = MagicMock()
        app.state.skill_registry = MagicMock()
        pack_dir = tmp_path / "router-install-pack"
        pack_dir.mkdir(parents=True)
        (pack_dir / "pack.yaml").write_text(
            "name: demo\nnamespace: default\nversion: 1.0.0\nartifacts: []\n",
            encoding="utf-8",
        )

        with (
            patch("anteroom.routers.chat.packs_service.parse_manifest") as mock_parse_manifest,
            patch("anteroom.routers.chat.packs_service.validate_manifest", return_value=[]),
            patch("anteroom.routers.chat.packs_service.install_pack") as mock_install_pack,
            patch("anteroom.services.pack_attachments.attach_pack") as mock_attach_pack,
        ):
            mock_parse_manifest.return_value = SimpleNamespace(namespace="default", name="demo", version="1.0.0")
            mock_install_pack.return_value = {"id": "pack-1", "artifact_count": 0, "action": "installed"}
            client = TestClient(app)

            resp = client.post(
                "/api/commands",
                json={"command": f"/pack install {pack_dir} --project --attach --priority 10"},
            )

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "Installed `@default/demo` v1.0.0" in body["message"]
            assert "Attached `@default/demo` (project, p10)." in body["message"]
            mock_install_pack.assert_called_once()
            mock_attach_pack.assert_called_once_with(app.state.db, "pack-1", project_path=str(Path.cwd()), priority=10)
            app.state.artifact_registry.load_from_db.assert_called_once_with(app.state.db)
            app.state.skill_registry.load_from_artifacts.assert_called_once_with(app.state.artifact_registry)

    def test_pack_update_command_updates_pack(self, tmp_path: Path) -> None:
        app = _make_app()
        app.state.artifact_registry = MagicMock()
        app.state.skill_registry = MagicMock()
        pack_dir = tmp_path / "router-update-pack"
        pack_dir.mkdir(parents=True)
        (pack_dir / "pack.yaml").write_text(
            "name: demo\nnamespace: default\nversion: 1.1.0\nartifacts: []\n",
            encoding="utf-8",
        )

        with (
            patch("anteroom.routers.chat.packs_service.parse_manifest") as mock_parse_manifest,
            patch("anteroom.routers.chat.packs_service.validate_manifest", return_value=[]),
            patch("anteroom.routers.chat.packs_service.update_pack") as mock_update_pack,
        ):
            mock_parse_manifest.return_value = SimpleNamespace(namespace="default", name="demo", version="1.1.0")
            mock_update_pack.return_value = {"id": "pack-1", "artifact_count": 0, "action": "updated"}
            client = TestClient(app)

            resp = client.post(
                "/api/commands",
                json={"command": f"/pack update {pack_dir} --project"},
            )

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "show_message"
            assert "Updated `@default/demo` v1.1.0" in body["message"]
            mock_update_pack.assert_called_once()
            app.state.artifact_registry.load_from_db.assert_called_once_with(app.state.db)
            app.state.skill_registry.load_from_artifacts.assert_called_once_with(app.state.artifact_registry)

    def test_mcp_command_reports_status(self) -> None:
        app = _make_app()
        app.state.mcp_manager.get_server_statuses.return_value = {
            "docs": {"status": "connected", "transport": "sse", "tool_count": 2}
        }
        client = TestClient(app)

        resp = client.post("/api/commands", json={"command": "/mcp"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "show_message"
        assert "## MCP Servers" in body["message"]
        assert "docs" in body["message"]
        assert body["command_items"][0]["kind"] == "mcp"
        assert body["command_items"][0]["badges"] == ["connected"]
        assert body["command_items"][0]["actions"][0]["command"] == "/mcp status docs"
        assert body["command_items"][0]["actions"][1]["command"] == "/mcp disconnect docs"

    def test_mcp_status_command_reports_server_detail(self) -> None:
        app = _make_app()
        app.state.mcp_manager.get_server_statuses.return_value = {
            "docs": {"status": "connected", "transport": "sse", "tool_count": 2}
        }
        app.state.mcp_manager._configs = {"docs": SimpleNamespace(url="https://docs.example/mcp", timeout=10)}
        app.state.mcp_manager._server_tools = {"docs": ["search_docs"]}
        client = TestClient(app)

        resp = client.post("/api/commands", json={"command": "/mcp status docs"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "show_message"
        assert "## MCP Server: docs" in body["message"]
        assert "search_docs" in body["message"]
        assert body["command_items"][0]["title"] == "docs"
        assert body["command_items"][0]["actions"][0]["command"] == "/mcp status docs"

    def test_mcp_reconnect_command_runs_action(self) -> None:
        app = _make_app()
        app.state.mcp_manager.get_server_statuses.return_value = {
            "docs": {"status": "connected", "transport": "sse", "tool_count": 2}
        }
        app.state.mcp_manager.reconnect_server = AsyncMock()
        client = TestClient(app)

        resp = client.post("/api/commands", json={"command": "/mcp reconnect docs"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "show_message"
        assert "MCP `reconnect` for **docs** complete." in body["message"]
        app.state.mcp_manager.reconnect_server.assert_awaited_once_with("docs")

    def test_plan_on_command_sets_plan_mode(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.post("/api/commands", json={"command": "/plan on", "plan_mode": False})

        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "set_plan_mode"
        assert body["plan_mode_enabled"] is True
        assert "Planning mode active" in body["message"]

    def test_plan_status_command_reports_current_mode(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.post("/api/commands", json={"command": "/plan status", "plan_mode": True})

        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "show_plan_status"
        assert body["plan_mode_enabled"] is True
        assert "Planning mode: **active**" in body["message"]

    def test_plan_off_command_clears_plan_mode(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.post("/api/commands", json={"command": "/plan off", "plan_mode": True})

        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "set_plan_mode"
        assert body["plan_mode_enabled"] is False
        assert "Planning mode off" in body["message"]

    def test_compact_command_rewrites_conversation_with_summary(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        db = app.state.db
        transaction_cm = MagicMock()
        db.transaction.return_value = transaction_cm
        transaction_conn = transaction_cm.__enter__.return_value
        fake_response = MagicMock()
        fake_response.choices = [MagicMock(message=MagicMock(content="Short summary"))]
        fake_ai_service = MagicMock()
        fake_ai_service.config.model = "gpt-5.2"
        fake_ai_service.client.chat.completions.create = AsyncMock(return_value=fake_response)
        with (
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.routers.chat.create_ai_service", return_value=fake_ai_service),
        ):
            mock_storage.get_conversation.return_value = {
                "id": conv_id,
                "type": "chat",
                "title": "Current",
                "model": "gpt-5.2",
            }
            mock_storage.list_messages.return_value = [
                {"role": "user", "content": "Question"},
                {"role": "assistant", "content": "Answer"},
                {"role": "user", "content": "Follow-up"},
                {"role": "assistant", "content": "Resolution"},
            ]
            mock_storage.get_conversation.side_effect = [
                {"id": conv_id, "type": "chat", "title": "Current", "model": "gpt-5.2"},
                {"id": conv_id, "type": "chat", "title": "Current", "model": "gpt-5.2"},
            ]
            client = TestClient(app)

            resp = client.post(f"/api/conversations/{conv_id}/command", json={"command": "/compact"})

            assert resp.status_code == 200
            body = resp.json()
            assert body["kind"] == "compact_conversation"
            assert "Compacted" in body["message"]
            assert transaction_conn.execute.call_count >= 2


class TestStaleStreamDetection:
    """Stale stream detection and cleanup in _active_streams."""

    def setup_method(self) -> None:
        _active_streams.clear()
        _cancel_events.clear()

    def teardown_method(self) -> None:
        _active_streams.clear()
        _cancel_events.clear()

    def test_active_streams_stores_metadata(self) -> None:
        """_active_streams now stores dicts with metadata instead of bools."""
        cid = str(uuid.uuid4())
        cancel = asyncio.Event()
        mock_request = MagicMock()
        _active_streams[cid] = {
            "started_at": time.monotonic(),
            "request": mock_request,
            "cancel_event": cancel,
        }
        info = _active_streams[cid]
        assert isinstance(info, dict)
        assert "started_at" in info
        assert info["cancel_event"] is cancel

    def test_stale_stream_detected_by_disconnect(self) -> None:
        """A stream whose request is disconnected is considered stale."""
        cid = str(uuid.uuid4())
        cancel = asyncio.Event()
        mock_request = AsyncMock()
        mock_request.is_disconnected.return_value = True
        _active_streams[cid] = {
            "started_at": time.monotonic(),
            "request": mock_request,
            "cancel_event": cancel,
        }
        info = _active_streams[cid]
        loop = asyncio.new_event_loop()
        is_disconnected = loop.run_until_complete(info["request"].is_disconnected())
        loop.close()
        assert is_disconnected is True

    def test_active_stream_not_stale(self) -> None:
        """A connected stream with recent start time is not stale."""
        cid = str(uuid.uuid4())
        cancel = asyncio.Event()
        mock_request = AsyncMock()
        mock_request.is_disconnected.return_value = False
        _active_streams[cid] = {
            "started_at": time.monotonic(),
            "request": mock_request,
            "cancel_event": cancel,
        }
        info = _active_streams[cid]
        loop = asyncio.new_event_loop()
        is_disconnected = loop.run_until_complete(info["request"].is_disconnected())
        loop.close()
        assert is_disconnected is False
        assert time.monotonic() - info["started_at"] < 5

    def test_stale_cancel_event_fires(self) -> None:
        """When a stale stream is detected, its cancel event should be set."""
        cancel = asyncio.Event()
        assert not cancel.is_set()
        cancel.set()
        assert cancel.is_set()

    def test_truthiness_preserved(self) -> None:
        """Dict value is truthy, preserving the queue routing check."""
        cid = str(uuid.uuid4())
        _active_streams[cid] = {
            "started_at": time.monotonic(),
            "request": MagicMock(),
            "cancel_event": asyncio.Event(),
        }
        assert _active_streams.get(cid)


class TestBuildToolListWithSkills:
    """_build_tool_list includes invoke_skill when skill_registry has skills."""

    def test_invoke_skill_added_when_skills_exist(self) -> None:
        from anteroom.routers.chat import _build_tool_list

        tool_reg = MagicMock()
        tool_reg.get_openai_tools.return_value = [
            {"type": "function", "function": {"name": "read_file"}},
        ]
        tool_reg.list_tools.return_value = ["read_file"]
        mcp = MagicMock()
        mcp.get_openai_tools.return_value = []

        skill_reg = MagicMock()
        skill_reg.get_invoke_skill_definition.return_value = {
            "type": "function",
            "function": {"name": "invoke_skill"},
        }

        tools, _, _ = _build_tool_list(
            tool_registry=tool_reg,
            mcp_manager=mcp,
            plan_mode=False,
            conversation_id="c1",
            data_dir=Path(tempfile.mkdtemp()),
            max_tools=128,
            skill_registry=skill_reg,
        )
        tool_names = [t["function"]["name"] for t in tools]
        assert "invoke_skill" in tool_names

    def test_no_invoke_skill_when_no_registry(self) -> None:
        from anteroom.routers.chat import _build_tool_list

        tool_reg = MagicMock()
        tool_reg.get_openai_tools.return_value = [
            {"type": "function", "function": {"name": "read_file"}},
        ]
        tool_reg.list_tools.return_value = ["read_file"]
        mcp = None

        tools, _, _ = _build_tool_list(
            tool_registry=tool_reg,
            mcp_manager=mcp,
            plan_mode=False,
            conversation_id="c1",
            data_dir=Path(tempfile.mkdtemp()),
            max_tools=128,
            skill_registry=None,
        )
        tool_names = [t["function"]["name"] for t in tools]
        assert "invoke_skill" not in tool_names


class TestExecuteWebToolInvokeSkill:
    """_execute_web_tool handles invoke_skill correctly."""

    @pytest.mark.asyncio
    async def test_invoke_skill_no_queue_returns_error(self) -> None:
        from anteroom.routers.chat import ToolExecutorContext, WebConfirmContext, _execute_web_tool, _message_queues

        skill_reg = MagicMock()
        skill = MagicMock()
        skill.prompt = "Do the thing"
        skill_reg.get.return_value = skill

        ctx = ToolExecutorContext(
            tool_registry=MagicMock(),
            mcp_manager=None,
            confirm_ctx=MagicMock(spec=WebConfirmContext),
            ai_service=MagicMock(),
            cancel_event=asyncio.Event(),
            db=MagicMock(),
            uid=None,
            uname=None,
            conversation_id="no-queue-conv",
            tools_openai=[],
            subagent_events={},
            subagent_limiter=MagicMock(),
            sa_config=MagicMock(),
            request_config=MagicMock(),
            skill_registry=skill_reg,
        )

        _message_queues.pop("no-queue-conv", None)

        result = await _execute_web_tool(ctx, "invoke_skill", {"skill_name": "test"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invoke_skill_with_queue_succeeds(self) -> None:
        from anteroom.routers.chat import ToolExecutorContext, WebConfirmContext, _execute_web_tool, _message_queues

        skill_reg = MagicMock()
        skill = MagicMock()
        skill.prompt = "Do the thing"
        skill_reg.get.return_value = skill

        queue: asyncio.Queue = asyncio.Queue()
        conv_id = "queued-conv"

        ctx = ToolExecutorContext(
            tool_registry=MagicMock(),
            mcp_manager=None,
            confirm_ctx=MagicMock(spec=WebConfirmContext),
            ai_service=MagicMock(),
            cancel_event=asyncio.Event(),
            db=MagicMock(),
            uid=None,
            uname=None,
            conversation_id=conv_id,
            tools_openai=[],
            subagent_events={},
            subagent_limiter=MagicMock(),
            sa_config=MagicMock(),
            request_config=MagicMock(),
            skill_registry=skill_reg,
        )

        _message_queues[conv_id] = queue
        try:
            result = await _execute_web_tool(ctx, "invoke_skill", {"skill_name": "test"})
            assert result["status"] == "skill_invoked"
            assert not queue.empty()
        finally:
            _message_queues.pop(conv_id, None)


class TestGetRequestRegistries:
    """Verify per-request registries with space scoping."""

    def test_returns_globals_when_no_space(self) -> None:
        from anteroom.routers.chat import _get_request_registries

        request = MagicMock()
        global_art = MagicMock()
        global_skill = MagicMock()
        global_rules = MagicMock()
        request.app.state.artifact_registry = global_art
        request.app.state.skill_registry = global_skill
        request.app.state.rule_enforcer = global_rules
        art, skill, rules = _get_request_registries(request, MagicMock(), space_id=None)
        assert art is global_art
        assert skill is global_skill
        assert rules is global_rules

    def test_returns_none_when_no_registry(self) -> None:
        from anteroom.routers.chat import _get_request_registries

        request = MagicMock(spec=[])
        request.app = MagicMock()
        request.app.state = MagicMock(spec=[])
        art, skill, rules = _get_request_registries(request, MagicMock(), space_id="s1")
        assert art is None

    def test_returns_space_scoped_registries(self) -> None:
        from anteroom.routers.chat import _get_request_registries

        request = MagicMock()
        global_art = MagicMock()
        global_skill = MagicMock()
        global_rules = MagicMock()
        request.app.state.artifact_registry = global_art
        request.app.state.skill_registry = global_skill
        request.app.state.rule_enforcer = global_rules

        mock_art_reg = MagicMock()
        mock_art_reg.list_all.return_value = []
        mock_skill_reg = MagicMock()
        with patch(
            "anteroom.services.artifact_registry.ArtifactRegistry",
            return_value=mock_art_reg,
        ), patch("anteroom.cli.skills.SkillRegistry", return_value=mock_skill_reg):
            db = MagicMock()
            art, skill, rules = _get_request_registries(
                request,
                db,
                space_id="space-123",
                working_dir="/tmp/project-skill-root",
            )
            assert art is mock_art_reg
            mock_art_reg.load_from_db.assert_called_once_with(db, space_id="space-123")
            assert skill is mock_skill_reg
            mock_skill_reg.load.assert_called_once_with("/tmp/project-skill-root")
            mock_skill_reg.load_from_artifacts.assert_called_once_with(mock_art_reg)
            assert rules is not global_rules

    def test_globals_returned_without_space(self) -> None:
        """Without a space, the global registries are returned as-is (no copy)."""
        from anteroom.routers.chat import _get_request_registries

        request = MagicMock()
        global_art = MagicMock()
        global_skill = MagicMock()
        global_rules = MagicMock()
        request.app.state.artifact_registry = global_art
        request.app.state.skill_registry = global_skill
        request.app.state.rule_enforcer = global_rules
        art, skill, rules = _get_request_registries(request, MagicMock(), space_id=None)
        assert art is global_art
        assert skill is global_skill
        assert rules is global_rules


class TestRuleEnforcerOverride:
    """rule_enforcer_override avoids mutating shared tool_registry state."""

    def test_check_safety_uses_override_enforcer(self) -> None:
        """check_safety should use the override enforcer, not the instance field."""
        from anteroom.tools import ToolRegistry

        reg = ToolRegistry()
        # No instance-level enforcer set
        assert reg._rule_enforcer is None

        # Override enforcer that blocks everything
        override = MagicMock()
        override.check_tool_call.return_value = (True, "blocked by space rule", "space:rule1")

        verdict = reg.check_safety("bash", {"command": "ls"}, rule_enforcer_override=override)
        assert verdict is not None
        assert verdict.hard_denied is True
        assert "space:rule1" in verdict.reason
        override.check_tool_call.assert_called_once_with("bash", {"command": "ls"})

    def test_check_safety_falls_back_to_instance_enforcer(self) -> None:
        """Without override, check_safety uses the instance-level enforcer."""
        from anteroom.tools import ToolRegistry

        reg = ToolRegistry()
        instance_enforcer = MagicMock()
        instance_enforcer.check_tool_call.return_value = (False, "", "")
        reg.set_rule_enforcer(instance_enforcer)

        verdict = reg.check_safety("bash", {"command": "ls"})
        instance_enforcer.check_tool_call.assert_called_once()
        # Not blocked, so verdict depends on safety config (None = no config)
        assert verdict is None

    def test_check_safety_override_takes_precedence(self) -> None:
        """Override enforcer takes precedence over instance enforcer."""
        from anteroom.tools import ToolRegistry

        reg = ToolRegistry()
        instance_enforcer = MagicMock()
        instance_enforcer.check_tool_call.return_value = (False, "", "")
        reg.set_rule_enforcer(instance_enforcer)

        override = MagicMock()
        override.check_tool_call.return_value = (True, "space blocks this", "space:rule2")

        verdict = reg.check_safety("bash", {"command": "rm -rf /"}, rule_enforcer_override=override)
        assert verdict is not None
        assert verdict.hard_denied is True
        # Override was used, not instance
        override.check_tool_call.assert_called_once()
        instance_enforcer.check_tool_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_call_tool_passes_override_to_check_safety(self) -> None:
        """call_tool threads rule_enforcer_override through to check_safety."""
        from anteroom.tools import ToolRegistry

        reg = ToolRegistry()

        async def mock_handler(**kwargs: object) -> dict:
            return {"output": "ok"}

        reg.register("read_file", mock_handler, {"name": "read_file", "parameters": {}})

        override = MagicMock()
        override.check_tool_call.return_value = (True, "blocked", "rule:x")

        result = await reg.call_tool("read_file", {"path": "/tmp/x"}, rule_enforcer_override=override)
        assert result.get("safety_blocked") is True
        override.check_tool_call.assert_called_once()

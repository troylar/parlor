from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.cli.commands import CommandContext, execute_slash_command
from anteroom.cli.textual_app import AgentLoopTextualBackend
from anteroom.db import init_db
from anteroom.routers.chat import router as chat_router
from anteroom.services import storage
from anteroom.services.pack_attachments import list_attachments_for_pack
from anteroom.services.packs import ManifestArtifact, PackManifest, install_pack
from anteroom.services.space_storage import create_space, get_space, list_spaces


class _Skill:
    source = "project"
    prompt = "Run checks with {args}."


class _SkillRegistry:
    load_warnings = ["warning one"]
    searched_dirs = []

    def reload(self, working_dir: str | None = None):
        return None

    def load_from_artifacts(self, artifact_registry):
        return None

    def get_skill_descriptions(self):
        return [("deploy-check", "Run the deployment checklist")]

    def get(self, name: str):
        return _Skill()

    def get_invoke_skill_definition(self):
        return {
            "type": "function",
            "function": {
                "name": "invoke_skill",
                "description": "Invoke a skill",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    def resolve_input(self, prompt: str):
        if prompt.startswith("/deploy-check"):
            return True, "expanded"
        return False, prompt


class _FakeMcpManager:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []
        self._configs = {"docs": SimpleNamespace(url="https://docs.example/mcp", timeout=10)}
        self._server_tools = {"docs": ["search_docs"]}

    def get_all_tools(self) -> list[dict]:
        return []

    def get_server_statuses(self) -> dict[str, dict[str, object]]:
        return {
            "docs": {"status": "connected", "transport": "sse", "tool_count": 1},
            "broken": {"status": "error", "transport": "sse", "tool_count": 0, "error_message": "auth failed"},
        }

    async def connect_server(self, name: str) -> None:
        self.actions.append(("connect", name))

    async def disconnect_server(self, name: str) -> None:
        self.actions.append(("disconnect", name))

    async def reconnect_server(self, name: str) -> None:
        self.actions.append(("reconnect", name))


def _shared_context() -> CommandContext:
    return CommandContext(
        current_model="gpt-5.2",
        working_dir="/repo",
        available_tools=("read_file", "bash"),
        skill_registry=_SkillRegistry(),
    )


def _backend_config(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        ai=SimpleNamespace(model="gpt-5.2"),
        app=SimpleNamespace(data_dir=data_dir),
        cli=SimpleNamespace(
            file_reference_max_chars=100_000,
            usage=SimpleNamespace(week_days=7, month_days=30),
            skills=SimpleNamespace(auto_invoke=True),
        ),
        identity=None,
        pack_sources=[],
    )


def _textual_backend(tmp_path: Path, *, resume_conversation_id: str | None = None) -> AgentLoopTextualBackend:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = init_db(tmp_path / "matrix.db")
    return AgentLoopTextualBackend(
        config=_backend_config(tmp_path),
        db=db,
        ai_service=SimpleNamespace(client=None),
        tool_executor=None,
        tools_openai=[],
        extra_system_prompt="",
        working_dir="/repo",
        tool_registry=None,
        skill_registry=_SkillRegistry(),
        artifact_registry=None,
        mcp_manager=_FakeMcpManager(),
        resume_conversation_id=resume_conversation_id,
    )


def _web_client(tmp_path: Path) -> tuple[TestClient, object]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    app = FastAPI()
    app.include_router(chat_router, prefix="/api")
    db = init_db(tmp_path / "web.db")
    app.state.db = db
    app.state.db_manager = SimpleNamespace(get=lambda name=None: db)
    app.state.config = SimpleNamespace(
        identity=None,
        app=SimpleNamespace(data_dir=tmp_path),
        ai=SimpleNamespace(model="gpt-5.2"),
        cli=SimpleNamespace(usage=SimpleNamespace(week_days=7, month_days=30)),
        pack_sources=[],
    )
    app.state.tool_registry = SimpleNamespace(list_tools=lambda: ["read_file", "bash"])
    app.state.mcp_manager = _FakeMcpManager()
    app.state.skill_registry = _SkillRegistry()
    app.state.artifact_registry = None
    return TestClient(app), db


@pytest.mark.asyncio
async def test_help_command_matrix(tmp_path: Path) -> None:
    shared = execute_slash_command("/help", _shared_context())
    backend = _textual_backend(tmp_path / "textual")
    textual = await backend.execute_slash_command("/help")
    client, _ = _web_client(tmp_path / "web")

    web = client.post("/api/commands", json={"command": "/help"})

    assert shared is not None and shared.kind == "show_help"
    assert textual is not None and textual.kind == "show_help"
    assert web.status_code == 200
    assert web.json()["kind"] == "show_message"
    assert "Slash Commands" in web.json()["message"]


@pytest.mark.asyncio
async def test_space_sources_command_matrix(tmp_path: Path) -> None:
    shared = execute_slash_command("/space sources demo", _shared_context())
    backend = _textual_backend(tmp_path / "textual")
    create_space(backend.db, "demo")
    textual = await backend.execute_slash_command("/space sources demo")
    client, db = _web_client(tmp_path / "web")
    create_space(db, "demo")
    conv = storage.create_conversation(db, title="Space Thread", working_dir="/repo")

    web = client.post(f"/api/conversations/{conv['id']}/command", json={"command": "/space sources demo"})

    assert shared is not None and shared.kind == "show_space_sources"
    assert textual is not None and textual.kind == "show_space_sources"
    assert web.status_code == 200
    assert web.json()["kind"] == "show_message"
    assert "No sources linked to space `demo`." in web.json()["message"]


@pytest.mark.asyncio
async def test_skills_command_matrix(tmp_path: Path) -> None:
    shared = execute_slash_command("/skills", _shared_context())
    backend = _textual_backend(tmp_path / "textual")
    textual = await backend.execute_slash_command("/skills")
    client, _ = _web_client(tmp_path / "web")

    web = client.post("/api/commands", json={"command": "/skills"})

    assert shared is not None and shared.kind == "show_skills"
    assert shared.skill_entries and shared.skill_entries[0].display_name == "deploy-check"
    assert textual is not None and textual.kind == "show_skills"
    assert textual.skill_entries and textual.skill_entries[0].display_name == "deploy-check"
    assert web.status_code == 200
    body = web.json()
    assert body["kind"] == "show_message"
    assert body["command_items"][0]["kind"] == "skill"
    assert body["command_items"][0]["title"] == "/deploy-check"


def test_web_local_only_commands_return_explicit_messages(tmp_path: Path) -> None:
    client, _ = _web_client(tmp_path / "web-local-only")

    commands = {
        "/upload ~/notes.md": "web file uploader",
        "/verbose": "CLI and Textual UI only",
        "/detail": "CLI and Textual UI only",
        "/plan inspect renderer handoff": "Use normal chat input on the web",
    }

    for command, expected in commands.items():
        resp = client.post("/api/commands", json={"command": command})

        assert resp.status_code == 200, command
        body = resp.json()
        assert body["kind"] == "show_message"
        assert expected in (body.get("message") or ""), command
        assert body["echo_user"] is False, command


@pytest.mark.asyncio
async def test_reload_skills_warning_matrix(tmp_path: Path) -> None:
    shared = execute_slash_command("/reload-skills", _shared_context())
    backend = _textual_backend(tmp_path / "textual")
    textual = await backend.execute_slash_command("/reload-skills")
    client, _ = _web_client(tmp_path / "web")

    web = client.post("/api/commands", json={"command": "/reload-skills"})

    assert shared is not None and shared.kind == "show_skills"
    assert shared.skill_warnings == ("warning one",)
    assert textual is not None and textual.kind == "show_skills"
    assert textual.skill_warnings == ("warning one",)
    assert web.status_code == 200
    body = web.json()
    assert body["kind"] == "show_message"
    assert "Reloaded skill registry." in body["message"]
    assert "warning one" in body["message"]


@pytest.mark.asyncio
async def test_custom_skill_forward_prompt_matrix(tmp_path: Path) -> None:
    shared = execute_slash_command("/deploy-check staging", _shared_context())
    backend = _textual_backend(tmp_path / "textual")
    textual = await backend.execute_slash_command("/deploy-check staging")
    client, _ = _web_client(tmp_path / "web")

    web = client.post("/api/commands", json={"command": "/deploy-check staging"})

    assert shared is not None and shared.kind == "forward_prompt"
    assert shared.forward_prompt == "expanded"
    assert textual is not None and textual.kind == "forward_prompt"
    assert textual.forward_prompt == "expanded"
    assert web.status_code == 200
    body = web.json()
    assert body["kind"] == "forward_prompt"
    assert body["forward_prompt"] == "expanded"


@pytest.mark.asyncio
async def test_quit_command_matrix(tmp_path: Path) -> None:
    shared = execute_slash_command("/quit", _shared_context())
    backend = _textual_backend(tmp_path / "textual")
    textual = await backend.execute_slash_command("/quit")
    client, _ = _web_client(tmp_path / "web")

    web = client.post("/api/commands", json={"command": "/quit"})

    assert shared is not None and shared.kind == "exit"
    assert textual is not None and textual.kind == "exit"
    assert web.status_code == 200
    body = web.json()
    assert body["kind"] == "show_message"
    assert "/quit" in body["message"]
    assert "web UI" in body["message"]


@pytest.mark.asyncio
async def test_plan_mode_command_matrix(tmp_path: Path) -> None:
    shared_on = execute_slash_command("/plan on", _shared_context())
    shared_status = execute_slash_command(
        "/plan status",
        CommandContext(
            current_model="gpt-5.2",
            working_dir="/repo",
            available_tools=("read_file", "bash"),
            skill_registry=_SkillRegistry(),
            plan_mode=True,
        ),
    )
    shared_off = execute_slash_command(
        "/plan off",
        CommandContext(
            current_model="gpt-5.2",
            working_dir="/repo",
            available_tools=("read_file", "bash"),
            skill_registry=_SkillRegistry(),
            plan_mode=True,
        ),
    )
    backend = _textual_backend(tmp_path / "textual")
    textual_on = await backend.execute_slash_command("/plan on")
    assert backend._set_plan_mode(textual_on.plan_mode_enabled is True).startswith("Planning mode active")
    textual_status = await backend.execute_slash_command("/plan status")
    textual_off = await backend.execute_slash_command("/plan off")
    assert backend._set_plan_mode(textual_off.plan_mode_enabled is True) == "Planning mode off. Full tools restored."
    client, _ = _web_client(tmp_path / "web")

    web_on = client.post("/api/commands", json={"command": "/plan on", "plan_mode": False})
    web_status = client.post("/api/commands", json={"command": "/plan status", "plan_mode": True})
    web_off = client.post("/api/commands", json={"command": "/plan off", "plan_mode": True})

    assert shared_on is not None and shared_on.kind == "set_plan_mode"
    assert shared_on.plan_mode_enabled is True
    assert shared_status is not None and shared_status.kind == "show_plan_status"
    assert shared_status.plan_mode_enabled is True
    assert shared_off is not None and shared_off.kind == "set_plan_mode"
    assert shared_off.plan_mode_enabled is False

    assert textual_on is not None and textual_on.kind == "set_plan_mode"
    assert textual_on.plan_mode_enabled is True
    assert textual_status is not None and textual_status.kind == "show_plan_status"
    assert textual_status.plan_mode_enabled is True
    assert textual_off is not None and textual_off.kind == "set_plan_mode"
    assert textual_off.plan_mode_enabled is False

    assert web_on.status_code == 200
    assert web_on.json()["kind"] == "set_plan_mode"
    assert web_on.json()["plan_mode_enabled"] is True
    assert web_status.status_code == 200
    assert web_status.json()["kind"] == "show_plan_status"
    assert web_status.json()["plan_mode_enabled"] is True
    assert web_off.status_code == 200
    assert web_off.json()["kind"] == "set_plan_mode"
    assert web_off.json()["plan_mode_enabled"] is False


@pytest.mark.asyncio
async def test_new_command_matrix(tmp_path: Path) -> None:
    shared = execute_slash_command("/new note Architecture Notes", _shared_context())
    backend = _textual_backend(tmp_path / "textual")
    textual = await backend.execute_slash_command("/new note Architecture Notes")
    client, _ = _web_client(tmp_path / "web")

    web = client.post("/api/commands", json={"command": "/new note Architecture Notes"})

    assert shared is not None and shared.kind == "new_conversation"
    assert shared.conversation_type == "note"
    assert textual is not None and textual.kind == "new_conversation"
    assert textual.conversation_type == "note"
    assert web.status_code == 200
    body = web.json()
    assert body["kind"] == "new_conversation"
    assert body["conversation"]["type"] == "note"
    assert body["conversation"]["title"] == "Architecture Notes"


@pytest.mark.asyncio
async def test_missing_resume_target_matrix(tmp_path: Path) -> None:
    shared = execute_slash_command("/resume missing-slug", _shared_context())
    backend = _textual_backend(tmp_path / "textual")
    textual = await backend.execute_slash_command("/resume missing-slug")
    textual_message = await backend.resume_conversation("missing-slug")
    client, _ = _web_client(tmp_path / "web")

    web = client.post("/api/commands", json={"command": "/resume missing-slug"})

    assert shared is not None and shared.kind == "resume_conversation"
    assert shared.resume_target == "missing-slug"
    assert textual is not None and textual.kind == "resume_conversation"
    assert textual.resume_target == "missing-slug"
    assert textual_message == "Conversation not found. Use `/list` to see available conversations."
    assert web.status_code == 200
    body = web.json()
    assert body["kind"] == "show_message"
    assert body["message"] == "Conversation not found. Use `/list` to see available conversations."


@pytest.mark.asyncio
async def test_missing_space_target_matrix(tmp_path: Path) -> None:
    shared = execute_slash_command("/space switch missing", _shared_context())

    backend = _textual_backend(tmp_path / "textual")
    conv = storage.create_conversation(backend.db, title="Space Test")
    backend.resume_conversation_id = conv["id"]
    await backend.load_history()
    textual = await backend.execute_slash_command("/space switch missing")
    textual_message = backend._switch_space("missing")

    client, db = _web_client(tmp_path / "web")
    web_conv = storage.create_conversation(db, title="Space Test")
    web = client.post(f"/api/conversations/{web_conv['id']}/command", json={"command": "/space switch missing"})

    assert shared is not None and shared.kind == "set_space"
    assert shared.space_target == "missing"
    assert textual is not None and textual.kind == "set_space"
    assert textual.space_target == "missing"
    assert textual_message == "Space `missing` not found."
    assert web.status_code == 200
    body = web.json()
    assert body["kind"] == "show_message"
    assert body["message"] == "Space `missing` not found."


@pytest.mark.asyncio
async def test_missing_artifact_and_pack_target_matrix(tmp_path: Path) -> None:
    shared_artifact = execute_slash_command("/artifact show @missing/skill/demo", _shared_context())
    shared_pack = execute_slash_command("/pack show missing/demo", _shared_context())

    backend = _textual_backend(tmp_path / "textual")
    textual_artifact = await backend.execute_slash_command("/artifact show @missing/skill/demo")
    textual_pack = await backend.execute_slash_command("/pack show missing/demo")

    client, _ = _web_client(tmp_path / "web")
    web_artifact = client.post("/api/commands", json={"command": "/artifact show @missing/skill/demo"})
    web_pack = client.post("/api/commands", json={"command": "/pack show missing/demo"})

    assert shared_artifact is not None and shared_artifact.kind == "show_artifact"
    assert shared_pack is not None and shared_pack.kind == "show_pack"
    assert textual_artifact is not None and textual_artifact.kind == "show_artifact"
    assert textual_pack is not None and textual_pack.kind == "show_pack"
    assert backend._artifact_show_markdown("@missing/skill/demo") == "Artifact not found."
    assert backend._pack_show_markdown("missing/demo") == "Pack `missing/demo` not found."

    assert web_artifact.status_code == 200
    assert web_artifact.json()["kind"] == "show_message"
    assert web_artifact.json()["message"] == "Artifact not found."

    assert web_pack.status_code == 200
    assert web_pack.json()["kind"] == "show_message"
    assert web_pack.json()["message"] == "Pack `missing/demo` not found."


@pytest.mark.asyncio
async def test_invalid_rewind_position_matrix(tmp_path: Path) -> None:
    shared = execute_slash_command("/rewind 99", _shared_context())

    backend = _textual_backend(tmp_path / "textual")
    conv = storage.create_conversation(backend.db, title="Rewind Test")
    storage.create_message(backend.db, conv["id"], "user", "first")
    storage.create_message(backend.db, conv["id"], "assistant", "reply")
    backend.resume_conversation_id = conv["id"]
    await backend.load_history()
    textual = await backend.execute_slash_command("/rewind 99")
    textual_message = await backend.rewind_current_conversation("99")

    client, db = _web_client(tmp_path / "web")
    web_conv = storage.create_conversation(db, title="Rewind Test")
    storage.create_message(db, web_conv["id"], "user", "first")
    storage.create_message(db, web_conv["id"], "assistant", "reply")
    web = client.post(f"/api/conversations/{web_conv['id']}/command", json={"command": "/rewind 99"})

    assert shared is not None and shared.kind == "rewind_conversation"
    assert shared.rewind_arg == "99"
    assert textual is not None and textual.kind == "rewind_conversation"
    assert textual.rewind_arg == "99"
    assert textual_message == "Position 99 not found."
    assert web.status_code == 200
    body = web.json()
    assert body["kind"] == "show_message"
    assert body["message"] == "Position 99 not found."


@pytest.mark.asyncio
async def test_conversation_listing_matrix(tmp_path: Path) -> None:
    backend = _textual_backend(tmp_path / "textual")
    storage.create_conversation(backend.db, title="Alpha")
    storage.create_conversation(backend.db, title="Beta")

    client, db = _web_client(tmp_path / "web")
    storage.create_conversation(db, title="Alpha")
    storage.create_conversation(db, title="Beta")

    shared = execute_slash_command("/list 5", _shared_context())
    textual = await backend.execute_slash_command("/list 5")
    web = client.post("/api/commands", json={"command": "/list 5"})

    assert shared is not None and shared.kind == "list_conversations"
    assert shared.list_limit == 5
    assert textual is not None and textual.kind == "list_conversations"
    assert textual.list_limit == 5
    assert web.status_code == 200
    body = web.json()
    assert body["kind"] == "show_message"
    assert len(body["command_items"]) >= 2
    assert {item["title"] for item in body["command_items"]} >= {"Alpha", "Beta"}


@pytest.mark.asyncio
async def test_space_switch_matrix(tmp_path: Path) -> None:
    shared = execute_slash_command("/space switch demo", _shared_context())

    backend = _textual_backend(tmp_path / "textual")
    conv = storage.create_conversation(backend.db, title="Space Test")
    create_space(backend.db, "demo")
    backend.resume_conversation_id = conv["id"]
    await backend.load_history()
    textual = await backend.execute_slash_command("/space switch demo")
    refreshed = storage.get_conversation(backend.db, conv["id"])

    client, db = _web_client(tmp_path / "web")
    web_conv = storage.create_conversation(db, title="Space Test")
    space = create_space(db, "demo")
    web = client.post(f"/api/conversations/{web_conv['id']}/command", json={"command": "/space switch demo"})

    assert shared is not None and shared.kind == "set_space"
    assert shared.space_target == "demo"
    assert textual is not None and textual.kind == "set_space"
    assert textual.space_target == "demo"
    assert refreshed is not None and refreshed["space_id"] is None
    assert web.status_code == 200
    body = web.json()
    assert body["kind"] == "set_space"
    assert body["conversation"]["space_id"] == space["id"]


@pytest.mark.asyncio
async def test_space_create_matrix(tmp_path: Path) -> None:
    shared = execute_slash_command("/space create demo", _shared_context())

    backend = _textual_backend(tmp_path / "textual")
    textual = await backend.execute_slash_command("/space create demo")

    client, db = _web_client(tmp_path / "web")
    web = client.post("/api/commands", json={"command": "/space create demo"})

    assert shared is not None and shared.kind == "create_space"
    assert shared.space_target == "demo"
    assert textual is not None and textual.kind == "create_space"
    assert textual.space_target == "demo"
    assert backend._create_space(textual.space_target or "") == "Created space **demo**."
    assert web.status_code == 200
    body = web.json()
    assert body["kind"] == "create_space"
    assert body["message"] == "Created space **demo**."
    assert list_spaces(db)[0]["name"] == "demo"


@pytest.mark.asyncio
async def test_space_alias_matrix(tmp_path: Path) -> None:
    shared = execute_slash_command("/space select demo", _shared_context())

    backend = _textual_backend(tmp_path / "textual")
    conv = storage.create_conversation(backend.db, title="Space Alias Test")
    create_space(backend.db, "demo")
    backend.resume_conversation_id = conv["id"]
    await backend.load_history()
    textual = await backend.execute_slash_command("/space use demo")

    client, db = _web_client(tmp_path / "web")
    web_conv = storage.create_conversation(db, title="Space Alias Test")
    space = create_space(db, "demo")
    web = client.post(f"/api/conversations/{web_conv['id']}/command", json={"command": "/space select demo"})

    assert shared is not None and shared.kind == "set_space"
    assert shared.space_target == "demo"
    assert textual is not None and textual.kind == "set_space"
    assert textual.space_target == "demo"
    assert web.status_code == 200
    body = web.json()
    assert body["kind"] == "set_space"
    assert body["conversation"]["space_id"] == space["id"]


@pytest.mark.asyncio
async def test_space_edit_matrix(tmp_path: Path) -> None:
    shared = execute_slash_command("/space edit model gpt-5.4-mini", _shared_context())

    backend = _textual_backend(tmp_path / "textual")
    conv = storage.create_conversation(backend.db, title="Space Edit")
    alpha = create_space(backend.db, "demo", model="gpt-5.2")
    backend.resume_conversation_id = conv["id"]
    await backend.load_history()
    backend._active_space = alpha
    textual = await backend.execute_slash_command("/space edit model gpt-5.4-mini")

    client, db = _web_client(tmp_path / "web")
    web_space = create_space(db, "demo", model="gpt-5.2")
    web_conv = storage.create_conversation(
        db,
        title="Space Edit",
        working_dir=str(tmp_path),
        space_id=web_space["id"],
    )
    web = client.post(
        f"/api/conversations/{web_conv['id']}/command",
        json={"command": "/space edit model gpt-5.4-mini"},
    )

    assert shared is not None and shared.kind == "update_space"
    assert shared.space_edit_field == "model"
    assert shared.space_edit_value == "gpt-5.4-mini"
    assert textual is not None and textual.kind == "update_space"
    assert backend._edit_space(textual.space_edit_field or "", textual.space_edit_value or "") == (
        "Updated model for **demo**: `gpt-5.4-mini`"
    )
    assert get_space(backend.db, alpha["id"])["model"] == "gpt-5.4-mini"
    assert web.status_code == 200
    body = web.json()
    assert body["kind"] == "update_space"
    assert body["message"] == "Updated model for **demo**: `gpt-5.4-mini`"
    assert get_space(db, web_space["id"])["model"] == "gpt-5.4-mini"


@pytest.mark.asyncio
async def test_space_refresh_matrix(tmp_path: Path) -> None:
    shared = execute_slash_command("/space refresh demo", _shared_context())

    source_path = tmp_path / "space.yaml"
    source_path.write_text(
        "name: demo\nversion: '1'\ninstructions: Refreshed demo rules.\nconfig:\n  model: gpt-5.4-mini\n"
    )

    backend = _textual_backend(tmp_path / "textual")
    conv = storage.create_conversation(backend.db, title="Space Refresh")
    alpha = create_space(backend.db, "demo", model="gpt-5.2", source_file=str(source_path))
    backend.resume_conversation_id = conv["id"]
    await backend.load_history()
    backend._active_space = alpha
    textual = await backend.execute_slash_command("/space refresh demo")

    client, db = _web_client(tmp_path / "web")
    web_space = create_space(db, "demo", model="gpt-5.2", source_file=str(source_path))
    web_conv = storage.create_conversation(
        db,
        title="Space Refresh",
        working_dir=str(tmp_path),
        space_id=web_space["id"],
    )
    web = client.post(f"/api/conversations/{web_conv['id']}/command", json={"command": "/space refresh demo"})

    assert shared is not None and shared.kind == "refresh_space"
    assert shared.space_target == "demo"
    assert textual is not None and textual.kind == "refresh_space"
    assert backend._refresh_space(textual.space_target or "") == "Refreshed space **demo**."
    assert get_space(backend.db, alpha["id"])["model"] == "gpt-5.4-mini"
    assert web.status_code == 200
    body = web.json()
    assert body["kind"] == "refresh_space"
    assert body["message"] == "Refreshed space **demo**."
    assert get_space(db, web_space["id"])["model"] == "gpt-5.4-mini"


@pytest.mark.asyncio
async def test_space_export_matrix(tmp_path: Path) -> None:
    shared = execute_slash_command("/space export demo", _shared_context())

    backend = _textual_backend(tmp_path / "textual")
    conv = storage.create_conversation(backend.db, title="Space Export")
    alpha = create_space(backend.db, "demo", instructions="Export me.", model="gpt-5.2")
    backend.resume_conversation_id = conv["id"]
    await backend.load_history()
    backend._active_space = alpha
    textual = await backend.execute_slash_command("/space export demo")

    client, db = _web_client(tmp_path / "web")
    web_space = create_space(db, "demo", instructions="Export me.", model="gpt-5.2")
    web_conv = storage.create_conversation(
        db,
        title="Space Export",
        working_dir=str(tmp_path),
        space_id=web_space["id"],
    )
    web = client.post(f"/api/conversations/{web_conv['id']}/command", json={"command": "/space export demo"})

    assert shared is not None and shared.kind == "export_space"
    assert shared.space_target == "demo"
    assert textual is not None and textual.kind == "export_space"
    exported = backend._export_space(textual.space_target or "")
    assert "## Space YAML: demo" in exported
    assert "instructions: Export me." in exported
    assert web.status_code == 200
    body = web.json()
    assert body["kind"] == "show_message"
    assert "## Space YAML: demo" in body["message"]
    assert "instructions: Export me." in body["message"]


@pytest.mark.asyncio
async def test_pack_attach_detach_matrix(tmp_path: Path) -> None:
    shared_attach = execute_slash_command("/pack attach demo/focus-fold", _shared_context())
    shared_detach = execute_slash_command("/pack detach demo/focus-fold", _shared_context())

    pack_dir = tmp_path / "demo-pack"
    (pack_dir / "instructions").mkdir(parents=True)
    (pack_dir / "pack.yaml").write_text(
        "\n".join(
            [
                "name: focus-fold",
                "namespace: demo",
                "version: 1.2.3",
                "description: Focus fold guidance",
                "artifacts:",
                "  - type: instruction",
                "    name: fold-guidance",
            ]
        ),
        encoding="utf-8",
    )
    (pack_dir / "instructions" / "fold-guidance.md").write_text("Explain fold transitions clearly.", encoding="utf-8")
    manifest = PackManifest(
        name="focus-fold",
        namespace="demo",
        version="1.2.3",
        description="Focus fold guidance",
        artifacts=(ManifestArtifact(type="instruction", name="fold-guidance"),),
    )

    backend = _textual_backend(tmp_path / "textual")
    install_pack(backend.db, manifest, pack_dir)
    backend.db.execute(
        "SELECT id FROM packs WHERE namespace = ? AND name = ?",
        ("demo", "focus-fold"),
    ).fetchone()
    textual_attach = await backend.execute_slash_command("/pack attach demo/focus-fold")
    textual_detach = await backend.execute_slash_command("/pack detach demo/focus-fold")

    client, db = _web_client(tmp_path / "web")
    install_pack(db, manifest, pack_dir)
    web_pack_row = db.execute(
        "SELECT id FROM packs WHERE namespace = ? AND name = ?",
        ("demo", "focus-fold"),
    ).fetchone()
    web_attach = client.post("/api/commands", json={"command": "/pack attach demo/focus-fold"})
    attached = list_attachments_for_pack(db, web_pack_row["id"])
    web_detach = client.post("/api/commands", json={"command": "/pack detach demo/focus-fold"})

    assert shared_attach is not None and shared_attach.kind == "attach_pack"
    assert shared_attach.pack_ref == "demo/focus-fold"
    assert shared_detach is not None and shared_detach.kind == "detach_pack"
    assert shared_detach.pack_ref == "demo/focus-fold"
    assert textual_attach is not None and textual_attach.kind == "attach_pack"
    assert backend._attach_pack(textual_attach.pack_ref or "") == "Attached `@demo/focus-fold` (global)."
    assert textual_detach is not None and textual_detach.kind == "detach_pack"
    assert backend._detach_pack(textual_detach.pack_ref or "") == "Detached `@demo/focus-fold` (global)."
    assert web_attach.status_code == 200
    assert web_attach.json()["message"] == "Attached `@demo/focus-fold` (global)."
    assert len(attached) == 1
    assert web_detach.status_code == 200
    assert web_detach.json()["message"] == "Detached `@demo/focus-fold` (global)."


@pytest.mark.asyncio
async def test_pack_install_update_matrix(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack-install-matrix"
    (pack_dir / "skills").mkdir(parents=True)
    (pack_dir / "skills" / "hello.yaml").write_text("content: hello\n", encoding="utf-8")
    (pack_dir / "pack.yaml").write_text(
        "\n".join(
            [
                "name: shared-pack",
                "namespace: demo",
                "version: 1.0.0",
                "artifacts:",
                "  - type: skill",
                "    name: hello",
            ]
        ),
        encoding="utf-8",
    )

    install_command = f"/pack install {pack_dir} --project --attach --priority 10"
    update_command = f"/pack update {pack_dir} --project"
    shared_context = CommandContext(
        current_model="gpt-5.2",
        working_dir=str(tmp_path),
        available_tools=("read_file", "bash"),
        skill_registry=_SkillRegistry(),
    )
    shared_install = execute_slash_command(install_command, shared_context)
    shared_update = execute_slash_command(update_command, shared_context)

    backend = _textual_backend(tmp_path / "textual")
    backend.working_dir = str(tmp_path)
    textual_install = await backend.execute_slash_command(install_command)
    textual_install_message = backend._install_or_update_pack(
        textual_install.pack_path or "",
        update=False,
        project_scope=textual_install.pack_project_scope,
        attach_after_install=textual_install.pack_attach_after_install,
        priority=textual_install.pack_priority or 50,
    )

    (pack_dir / "pack.yaml").write_text(
        "\n".join(
            [
                "name: shared-pack",
                "namespace: demo",
                "version: 1.1.0",
                "artifacts:",
                "  - type: skill",
                "    name: hello",
            ]
        ),
        encoding="utf-8",
    )
    textual_update = await backend.execute_slash_command(update_command)
    textual_update_message = backend._install_or_update_pack(
        textual_update.pack_path or "",
        update=True,
        project_scope=textual_update.pack_project_scope,
    )

    web_pack_dir = tmp_path / "web-pack-install-matrix"
    (web_pack_dir / "skills").mkdir(parents=True)
    (web_pack_dir / "skills" / "hello.yaml").write_text("content: hello\n", encoding="utf-8")
    (web_pack_dir / "pack.yaml").write_text(
        "\n".join(
            [
                "name: shared-pack-web",
                "namespace: demo",
                "version: 1.0.0",
                "artifacts:",
                "  - type: skill",
                "    name: hello",
            ]
        ),
        encoding="utf-8",
    )
    client, db = _web_client(tmp_path / "web")
    web_conv = storage.create_conversation(db, title="Pack Thread", working_dir=str(tmp_path))
    web_install = client.post(
        f"/api/conversations/{web_conv['id']}/command",
        json={"command": f"/pack install {web_pack_dir} --project --attach --priority 10"},
    )
    (web_pack_dir / "pack.yaml").write_text(
        "\n".join(
            [
                "name: shared-pack-web",
                "namespace: demo",
                "version: 1.1.0",
                "artifacts:",
                "  - type: skill",
                "    name: hello",
            ]
        ),
        encoding="utf-8",
    )
    web_update = client.post(
        f"/api/conversations/{web_conv['id']}/command",
        json={"command": f"/pack update {web_pack_dir} --project"},
    )

    assert shared_install is not None and shared_install.kind == "install_pack"
    assert shared_install.pack_project_scope is True
    assert shared_install.pack_attach_after_install is True
    assert shared_install.pack_priority == 10
    assert shared_update is not None and shared_update.kind == "update_pack"

    assert textual_install is not None and textual_install.kind == "install_pack"
    assert "Installed `@demo/shared-pack` v1.0.0" in textual_install_message
    assert "Attached `@demo/shared-pack` (project, p10)." in textual_install_message
    assert textual_update is not None and textual_update.kind == "update_pack"
    assert "Updated `@demo/shared-pack` v1.1.0" in textual_update_message

    assert web_install.status_code == 200
    assert "Installed `@demo/shared-pack-web` v1.0.0" in web_install.json()["message"]
    assert "Attached `@demo/shared-pack-web` (project, p10)." in web_install.json()["message"]
    assert web_update.status_code == 200
    assert "Updated `@demo/shared-pack-web` v1.1.0" in web_update.json()["message"]


@pytest.mark.asyncio
async def test_mcp_command_matrix(tmp_path: Path) -> None:
    shared_status = execute_slash_command("/mcp", _shared_context())
    shared_detail = execute_slash_command("/mcp status docs", _shared_context())
    shared_action = execute_slash_command("/mcp reconnect broken", _shared_context())

    backend = _textual_backend(tmp_path / "textual")
    textual_status = await backend.execute_slash_command("/mcp")
    textual_detail = await backend.execute_slash_command("/mcp status docs")
    textual_action = await backend.execute_slash_command("/mcp reconnect broken")
    textual_message = await backend._run_mcp_action(
        textual_action.mcp_action or "",
        textual_action.mcp_server_name or "",
    )

    client, _ = _web_client(tmp_path / "web")
    web_status = client.post("/api/commands", json={"command": "/mcp"})
    web_detail = client.post("/api/commands", json={"command": "/mcp status docs"})
    web_action = client.post("/api/commands", json={"command": "/mcp reconnect broken"})

    assert shared_status is not None and shared_status.kind == "show_mcp_status"
    assert shared_detail is not None and shared_detail.kind == "show_mcp_server_detail"
    assert shared_detail.mcp_server_name == "docs"
    assert shared_action is not None and shared_action.kind == "run_mcp_action"
    assert shared_action.mcp_action == "reconnect"
    assert shared_action.mcp_server_name == "broken"

    assert textual_status is not None and textual_status.kind == "show_mcp_status"
    assert textual_detail is not None and textual_detail.kind == "show_mcp_server_detail"
    assert textual_action is not None and textual_action.kind == "run_mcp_action"
    assert "MCP `reconnect` for **broken** complete." in textual_message

    assert web_status.status_code == 200
    assert "## MCP Servers" in web_status.json()["message"]
    assert web_status.json()["command_items"][0]["kind"] == "mcp"
    assert web_detail.status_code == 200
    assert "## MCP Server: docs" in web_detail.json()["message"]
    assert web_detail.json()["command_items"][0]["title"] == "docs"
    assert web_action.status_code == 200
    assert "MCP `reconnect` for **broken** complete." in web_action.json()["message"]


def test_web_representative_commands_do_not_fall_back_to_unsupported(tmp_path: Path) -> None:
    client, db = _web_client(tmp_path / "web")
    space = create_space(db, "demo")
    conv = storage.create_conversation(db, title="Parity Thread", working_dir=str(tmp_path), space_id=space["id"])

    pack_dir = tmp_path / "web-pack-contract"
    (pack_dir / "skills").mkdir(parents=True)
    (pack_dir / "skills" / "hello.yaml").write_text("content: hello\n", encoding="utf-8")
    manifest = PackManifest(
        namespace="demo",
        name="focus-fold",
        version="1.0.0",
        description="Contract pack",
        artifacts=(ManifestArtifact(type="skill", name="hello"),),
    )
    install_pack(db, manifest, pack_dir)

    commands = [
        (None, "/help"),
        (None, "/tools"),
        (None, "/usage"),
        (None, "/skills"),
        (None, "/reload-skills"),
        (None, "/list 5"),
        (None, "/search parity"),
        (None, "/spaces"),
        (None, "/space create another-space"),
        (conv["id"], "/space show demo"),
        (conv["id"], "/space sources demo"),
        (conv["id"], "/space refresh demo"),
        (conv["id"], "/space export demo"),
        (conv["id"], "/space clear"),
        (None, "/artifacts"),
        (None, "/packs"),
        (None, "/pack sources"),
        (None, "/pack refresh"),
        (conv["id"], "/pack show demo/focus-fold"),
        (conv["id"], "/pack attach demo/focus-fold"),
        (conv["id"], "/pack detach demo/focus-fold"),
        (conv["id"], "/plan on"),
        (conv["id"], "/plan status"),
        (conv["id"], "/plan off"),
        (conv["id"], "/mcp"),
        (conv["id"], "/model gpt-5.4-mini"),
        (conv["id"], "/slug parity-thread"),
        (conv["id"], "/compact"),
        (conv["id"], "/rewind 1"),
    ]

    for conversation_id, command in commands:
        path = "/api/commands" if conversation_id is None else f"/api/conversations/{conversation_id}/command"
        resp = client.post(path, json={"command": command})

        assert resp.status_code == 200, command
        body = resp.json()
        assert "not supported on web yet" not in (body.get("message") or ""), command

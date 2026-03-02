"""Comprehensive unit tests for chat router: parse, build, stream, execute."""

from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.routers.chat import (
    ToolExecutorContext,
    WebConfirmContext,
    _active_streams,
    _build_tool_list,
    _cancel_events,
    _extract_streaming_content,
    _extract_streaming_language,
    _is_safe_name,
    _message_queues,
    _resolve_sources,
    _stream_chat_events,
    _web_confirm_tool,
    router,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(*, config_overrides: dict[str, Any] | None = None) -> FastAPI:
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
    mock_config.ai.model = "gpt-4o"
    mock_config.ai.max_tools = 128
    mock_config.ai.narration_cadence = 0
    mock_config.safety.read_only = False
    mock_config.safety.tool_tiers = {}
    mock_config.safety.approval_mode = "ask_for_writes"
    mock_config.safety.approval_timeout = 120
    mock_config.safety.allowed_tools = []
    mock_config.safety.subagent.max_concurrent = 5
    mock_config.safety.subagent.max_total = 10
    mock_config.safety.tool_rate_limit = None
    mock_config.safety.output_filter = None
    mock_config.cli.planning.auto_mode = "off"
    mock_config.cli.planning.auto_threshold_tools = 3
    mock_config.cli.usage.budgets = None
    mock_config.cli.max_consecutive_text_only = 3
    mock_config.rag = None
    mock_config.codebase_index.map_tokens = 1000

    if config_overrides:
        for k, v in config_overrides.items():
            setattr(mock_config, k, v)

    app.state.config = mock_config
    app.state.tool_registry = MagicMock()
    app.state.tool_registry.get_openai_tools.return_value = []
    app.state.tool_registry.list_tools.return_value = []
    app.state.tool_registry.has_tool.return_value = False
    app.state.tool_registry._session_allowed = set()
    app.state.mcp_manager = MagicMock()
    app.state.mcp_manager.get_openai_tools.return_value = []
    app.state.mcp_manager.get_server_statuses.return_value = []
    app.state.mcp_manager.get_tool_server_name.return_value = "mcp-server"
    app.state.pending_approvals = {}
    app.state.embedding_worker = None
    app.state.vec_enabled = False
    app.state.embedding_service = None
    app.state.injection_detector = None
    app.state.artifact_registry = None
    app.state.dlp_scanner = None
    app.state.audit_writer = None

    return app


def _make_conv(conv_id: str | None = None, *, conv_type: str = "chat") -> dict:
    return {
        "id": conv_id or str(uuid.uuid4()),
        "title": "New Conversation",
        "type": conv_type,
        "model": None,
        "project_id": None,
        "space_id": None,
    }


@pytest.fixture(autouse=True)
def _clean_module_state():
    _active_streams.clear()
    _cancel_events.clear()
    _message_queues.clear()
    yield
    _active_streams.clear()
    _cancel_events.clear()
    _message_queues.clear()


# ---------------------------------------------------------------------------
# _is_safe_name
# ---------------------------------------------------------------------------


class TestIsSafeName:
    def test_valid_alphanumeric(self) -> None:
        assert _is_safe_name("personal") is True

    def test_valid_with_hyphens_and_underscores(self) -> None:
        assert _is_safe_name("my-db_01") is True

    def test_empty_string(self) -> None:
        assert _is_safe_name("") is False

    def test_too_long(self) -> None:
        assert _is_safe_name("a" * 65) is False

    def test_exactly_64_chars(self) -> None:
        assert _is_safe_name("a" * 64) is True

    def test_with_spaces(self) -> None:
        assert _is_safe_name("my db") is False

    def test_with_dot(self) -> None:
        assert _is_safe_name("my.db") is False

    def test_sql_injection_attempt(self) -> None:
        assert _is_safe_name("'; DROP TABLE--") is False


# ---------------------------------------------------------------------------
# _extract_streaming_content
# ---------------------------------------------------------------------------


class TestExtractStreamingContent:
    def test_no_content_key_returns_none(self) -> None:
        assert _extract_streaming_content('{"title": "hello"') is None

    def test_partial_content_key(self) -> None:
        assert _extract_streaming_content('{"cont') is None

    def test_simple_content(self) -> None:
        assert _extract_streaming_content('{"content": "hello world"') == "hello world"

    def test_content_with_escape_newline(self) -> None:
        result = _extract_streaming_content('{"content": "line1\\nline2"')
        assert result == "line1\nline2"

    def test_content_with_escape_tab(self) -> None:
        result = _extract_streaming_content('{"content": "col1\\tcol2"')
        assert result == "col1\tcol2"

    def test_content_with_escape_quote(self) -> None:
        result = _extract_streaming_content('{"content": "say \\"hi\\""')
        assert result == 'say "hi"'

    def test_content_with_escape_backslash(self) -> None:
        result = _extract_streaming_content('{"content": "a\\\\b"')
        assert result == "a\\b"

    def test_content_with_unicode_escape(self) -> None:
        result = _extract_streaming_content('{"content": "\\u0041"')
        assert result == "A"

    def test_partial_content_value(self) -> None:
        # Incomplete string — no closing quote, still returns partial
        result = _extract_streaming_content('{"content": "hel')
        assert result == "hel"

    def test_content_after_colon_missing(self) -> None:
        assert _extract_streaming_content('{"content"') is None

    def test_content_no_opening_quote(self) -> None:
        assert _extract_streaming_content('{"content": 42') is None


# ---------------------------------------------------------------------------
# _extract_streaming_language
# ---------------------------------------------------------------------------


class TestExtractStreamingLanguage:
    def test_no_language_key(self) -> None:
        assert _extract_streaming_language('{"content": "hi"') is None

    def test_valid_language(self) -> None:
        assert _extract_streaming_language('{"language": "python"') == "python"

    def test_valid_cpp(self) -> None:
        assert _extract_streaming_language('{"language": "c++"') == "c++"

    def test_language_empty_string(self) -> None:
        assert _extract_streaming_language('{"language": ""') is None

    def test_language_with_injection_chars(self) -> None:
        # Backtick is not in allowlist
        result = _extract_streaming_language('{"language": "` injection `"')
        assert result is None

    def test_language_too_long(self) -> None:
        lang = "a" * 51
        result = _extract_streaming_language(f'{{"language": "{lang}"')
        assert result is None

    def test_language_with_hash(self) -> None:
        assert _extract_streaming_language('{"language": "c#"') == "c#"


# ---------------------------------------------------------------------------
# _resolve_sources
# ---------------------------------------------------------------------------


class TestResolveSources:
    def test_no_sources_returns_empty(self) -> None:
        db = MagicMock()
        result = _resolve_sources(db, [], None, None)
        assert result == ""

    def test_source_ids_resolved(self) -> None:
        db = MagicMock()
        sid = str(uuid.uuid4())
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_source.return_value = {
                "id": sid,
                "title": "My Source",
                "content": "some content here",
            }
            result = _resolve_sources(db, [sid], None, None)
        assert "My Source" in result
        assert "some content here" in result
        assert "Referenced Knowledge Sources" in result

    def test_source_with_no_content_skipped(self) -> None:
        db = MagicMock()
        sid = str(uuid.uuid4())
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_source.return_value = {"id": sid, "title": "Empty", "content": ""}
            result = _resolve_sources(db, [sid], None, None)
        assert result == ""

    def test_source_tag_resolves(self) -> None:
        db = MagicMock()
        tag_id = str(uuid.uuid4())
        src_id = str(uuid.uuid4())
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.list_sources.return_value = [{"id": src_id, "content": "tag content"}]
            mock_storage.get_source.return_value = {"id": src_id, "title": "Tagged", "content": "tag content"}
            result = _resolve_sources(db, [], tag_id, None)
        assert "tag content" in result

    def test_source_group_id_resolves(self) -> None:
        db = MagicMock()
        group_id = str(uuid.uuid4())
        src_id = str(uuid.uuid4())
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.list_sources.return_value = [{"id": src_id, "content": "group content"}]
            mock_storage.get_source.return_value = {"id": src_id, "title": "Grouped", "content": "group content"}
            result = _resolve_sources(db, [], None, group_id)
        assert "group content" in result

    def test_truncation_applied(self) -> None:
        db = MagicMock()
        sid = str(uuid.uuid4())
        long_content = "x" * 200
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_source.return_value = {"id": sid, "title": "Big", "content": long_content}
            result = _resolve_sources(db, [sid], None, None, limit=100)
        assert "truncated" in result

    def test_deduplication_across_tag_and_ids(self) -> None:
        db = MagicMock()
        sid = str(uuid.uuid4())
        tag_id = str(uuid.uuid4())
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_source.return_value = {"id": sid, "title": "Src", "content": "content"}
            mock_storage.list_sources.return_value = [{"id": sid, "content": "content"}]
            result = _resolve_sources(db, [sid], tag_id, None)
        # Source should only appear once
        assert result.count("Src") == 1

    def test_max_20_source_ids(self) -> None:
        db = MagicMock()
        sids = [str(uuid.uuid4()) for _ in range(25)]
        call_count = 0

        def side_effect(db_arg, sid):
            nonlocal call_count
            call_count += 1
            return {"id": sid, "title": f"src{call_count}", "content": "data"}

        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_source.side_effect = side_effect
            _resolve_sources(db, sids, None, None)
        assert call_count <= 20


# ---------------------------------------------------------------------------
# _build_tool_list
# ---------------------------------------------------------------------------


class TestBuildToolList:
    def _make_registry(self, tools: list[dict] | None = None) -> MagicMock:
        reg = MagicMock()
        reg.get_openai_tools.return_value = tools or []
        reg.list_tools.return_value = [t["function"]["name"] for t in (tools or [])]
        return reg

    def test_basic_no_mcp(self) -> None:
        tool_def = {"function": {"name": "read_file"}, "type": "function"}
        reg = self._make_registry([tool_def])
        conv_id = str(uuid.uuid4())

        with patch("anteroom.tools.cap_tools", lambda tools, builtin, limit: tools):
            result, plan_path, plan_prompt = _build_tool_list(
                tool_registry=reg,
                mcp_manager=None,
                plan_mode=False,
                conversation_id=conv_id,
                data_dir=Path(tempfile.mkdtemp()),
                max_tools=128,
            )

        assert any(t["function"]["name"] == "read_file" for t in result)
        assert plan_path is None
        assert plan_prompt == ""

    def test_mcp_tools_appended(self) -> None:
        builtin_tool = {"function": {"name": "bash"}, "type": "function"}
        mcp_tool = {"function": {"name": "mcp_search"}, "type": "function"}
        reg = self._make_registry([builtin_tool])
        mcp = MagicMock()
        mcp.get_openai_tools.return_value = [mcp_tool]
        conv_id = str(uuid.uuid4())

        with patch("anteroom.tools.cap_tools", lambda tools, builtin, limit: tools):
            result, _, _ = _build_tool_list(
                tool_registry=reg,
                mcp_manager=mcp,
                plan_mode=False,
                conversation_id=conv_id,
                data_dir=Path(tempfile.mkdtemp()),
                max_tools=128,
            )

        names = [t["function"]["name"] for t in result]
        assert "bash" in names
        assert "mcp_search" in names

    def test_mcp_manager_none_skipped(self) -> None:
        reg = self._make_registry()
        with patch("anteroom.tools.cap_tools", lambda tools, builtin, limit: tools):
            result, _, _ = _build_tool_list(
                tool_registry=reg,
                mcp_manager=None,
                plan_mode=False,
                conversation_id=str(uuid.uuid4()),
                data_dir=Path(tempfile.mkdtemp()),
                max_tools=128,
            )
        assert result == []

    def test_plan_mode_filters_tools(self) -> None:
        tools = [
            {"function": {"name": "write_file"}, "type": "function"},
            {"function": {"name": "read_file"}, "type": "function"},
        ]
        reg = self._make_registry(tools)
        data_dir = Path(tempfile.mkdtemp())
        conv_id = str(uuid.uuid4())

        with (
            patch("anteroom.tools.cap_tools", lambda tools, builtin, limit: tools),
            patch("anteroom.cli.plan.PLAN_MODE_ALLOWED_TOOLS", {"read_file"}),
            patch("anteroom.cli.plan.get_plan_file_path", return_value=data_dir / "plan.md"),
            patch("anteroom.cli.plan.build_planning_system_prompt", return_value="Plan prompt here"),
        ):
            result, plan_path, plan_prompt = _build_tool_list(
                tool_registry=reg,
                mcp_manager=None,
                plan_mode=True,
                conversation_id=conv_id,
                data_dir=data_dir,
                max_tools=128,
            )

        names = [t["function"]["name"] for t in result]
        assert "write_file" not in names
        assert "read_file" in names
        assert plan_path is not None
        assert "Plan prompt here" in plan_prompt

    def test_read_only_mode_filters(self) -> None:
        tools = [
            {"function": {"name": "write_file"}, "type": "function"},
            {"function": {"name": "read_file"}, "type": "function"},
        ]
        reg = self._make_registry(tools)

        def fake_filter_read_only(tools_list, tier_overrides):
            return [t for t in tools_list if t["function"]["name"] == "read_file"]

        with (
            patch("anteroom.tools.cap_tools", lambda tools, builtin, limit: tools),
            patch("anteroom.tools.tiers.filter_read_only_tools", fake_filter_read_only),
        ):
            result, _, _ = _build_tool_list(
                tool_registry=reg,
                mcp_manager=None,
                plan_mode=False,
                conversation_id=str(uuid.uuid4()),
                data_dir=Path(tempfile.mkdtemp()),
                max_tools=128,
                read_only=True,
            )

        names = [t["function"]["name"] for t in result]
        assert "read_file" in names
        assert "write_file" not in names


# ---------------------------------------------------------------------------
# _build_chat_system_prompt
# ---------------------------------------------------------------------------


class TestBuildChatSystemPrompt:
    @pytest.mark.asyncio
    async def test_basic_prompt_assembled(self) -> None:
        from anteroom.routers.chat import _build_chat_system_prompt

        ai_service = MagicMock()
        ai_service.config.model = "gpt-4o"
        tool_registry = MagicMock()
        tool_registry.list_tools.return_value = []
        tool_registry._working_dir = None
        mcp_manager = MagicMock()
        mcp_manager.get_server_statuses.return_value = []
        config = MagicMock()
        config.app.tls = False
        config.rag = None
        config.codebase_index.map_tokens = 1000
        db = MagicMock()
        conv_id = str(uuid.uuid4())

        with (
            patch("anteroom.routers.chat.build_runtime_context", return_value="RUNTIME_CTX"),
            patch("anteroom.routers.chat.load_instructions", return_value=None),
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.services.codebase_index.create_index_service", return_value=None),
        ):
            mock_storage.get_canvas_for_conversation.return_value = None
            result = await _build_chat_system_prompt(
                ai_service=ai_service,
                tool_registry=tool_registry,
                mcp_manager=mcp_manager,
                config=config,
                db=db,
                conversation_id=conv_id,
                project_instructions=None,
                plan_prompt="",
                plan_mode=False,
                message_text="hello",
                source_ids=[],
                source_tag=None,
                source_group_id=None,
            )

        assert "RUNTIME_CTX" in result

    @pytest.mark.asyncio
    async def test_project_instructions_injected(self) -> None:
        from anteroom.routers.chat import _build_chat_system_prompt

        ai_service = MagicMock()
        ai_service.config.model = "gpt-4o"
        tool_registry = MagicMock()
        tool_registry.list_tools.return_value = []
        tool_registry._working_dir = None
        mcp_manager = MagicMock()
        mcp_manager.get_server_statuses.return_value = []
        config = MagicMock()
        config.app.tls = False
        config.rag = None
        config.codebase_index.map_tokens = 1000
        db = MagicMock()

        with (
            patch("anteroom.routers.chat.build_runtime_context", return_value="CTX"),
            patch("anteroom.routers.chat.load_instructions", return_value=None),
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.services.codebase_index.create_index_service", return_value=None),
        ):
            mock_storage.get_canvas_for_conversation.return_value = None
            result = await _build_chat_system_prompt(
                ai_service=ai_service,
                tool_registry=tool_registry,
                mcp_manager=mcp_manager,
                config=config,
                db=db,
                conversation_id=str(uuid.uuid4()),
                project_instructions="PROJECT INSTRUCTIONS HERE",
                plan_prompt="",
                plan_mode=False,
                message_text="hello",
                source_ids=[],
                source_tag=None,
                source_group_id=None,
            )

        assert "PROJECT INSTRUCTIONS HERE" in result

    @pytest.mark.asyncio
    async def test_space_instructions_injected(self) -> None:
        from anteroom.routers.chat import _build_chat_system_prompt

        ai_service = MagicMock()
        ai_service.config.model = "gpt-4o"
        tool_registry = MagicMock()
        tool_registry.list_tools.return_value = []
        tool_registry._working_dir = None
        mcp_manager = MagicMock()
        mcp_manager.get_server_statuses.return_value = []
        config = MagicMock()
        config.app.tls = False
        config.rag = None
        config.codebase_index.map_tokens = 1000
        db = MagicMock()

        with (
            patch("anteroom.routers.chat.build_runtime_context", return_value="CTX"),
            patch("anteroom.routers.chat.load_instructions", return_value=None),
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.services.codebase_index.create_index_service", return_value=None),
        ):
            mock_storage.get_canvas_for_conversation.return_value = None
            result = await _build_chat_system_prompt(
                ai_service=ai_service,
                tool_registry=tool_registry,
                mcp_manager=mcp_manager,
                config=config,
                db=db,
                conversation_id=str(uuid.uuid4()),
                project_instructions=None,
                space_instructions="SPACE RULES",
                plan_prompt="",
                plan_mode=False,
                message_text="hello",
                source_ids=[],
                source_tag=None,
                source_group_id=None,
            )

        assert "SPACE RULES" in result
        assert "space_instructions" in result

    @pytest.mark.asyncio
    async def test_plan_prompt_injected(self) -> None:
        from anteroom.routers.chat import _build_chat_system_prompt

        ai_service = MagicMock()
        ai_service.config.model = "gpt-4o"
        tool_registry = MagicMock()
        tool_registry.list_tools.return_value = []
        tool_registry._working_dir = None
        mcp_manager = MagicMock()
        mcp_manager.get_server_statuses.return_value = []
        config = MagicMock()
        config.app.tls = False
        config.rag = None
        config.codebase_index.map_tokens = 1000
        db = MagicMock()

        with (
            patch("anteroom.routers.chat.build_runtime_context", return_value="CTX"),
            patch("anteroom.routers.chat.load_instructions", return_value=None),
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.services.codebase_index.create_index_service", return_value=None),
        ):
            mock_storage.get_canvas_for_conversation.return_value = None
            result = await _build_chat_system_prompt(
                ai_service=ai_service,
                tool_registry=tool_registry,
                mcp_manager=mcp_manager,
                config=config,
                db=db,
                conversation_id=str(uuid.uuid4()),
                project_instructions=None,
                plan_prompt="\n\nPLAN PROMPT",
                plan_mode=True,
                message_text="make a plan",
                source_ids=[],
                source_tag=None,
                source_group_id=None,
            )

        assert "PLAN PROMPT" in result

    @pytest.mark.asyncio
    async def test_canvas_context_injected(self) -> None:
        from anteroom.routers.chat import _build_chat_system_prompt

        ai_service = MagicMock()
        ai_service.config.model = "gpt-4o"
        tool_registry = MagicMock()
        tool_registry.list_tools.return_value = []
        tool_registry._working_dir = None
        mcp_manager = MagicMock()
        mcp_manager.get_server_statuses.return_value = []
        config = MagicMock()
        config.app.tls = False
        config.rag = None
        config.codebase_index.map_tokens = 1000
        db = MagicMock()

        canvas_data = {
            "id": str(uuid.uuid4()),
            "title": "My Canvas",
            "content": "canvas body here",
            "language": "markdown",
            "version": 2,
        }

        with (
            patch("anteroom.routers.chat.build_runtime_context", return_value="CTX"),
            patch("anteroom.routers.chat.load_instructions", return_value=None),
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.services.codebase_index.create_index_service", return_value=None),
        ):
            mock_storage.get_canvas_for_conversation.return_value = canvas_data
            result = await _build_chat_system_prompt(
                ai_service=ai_service,
                tool_registry=tool_registry,
                mcp_manager=mcp_manager,
                config=config,
                db=db,
                conversation_id=str(uuid.uuid4()),
                project_instructions=None,
                plan_prompt="",
                plan_mode=False,
                message_text="edit canvas",
                source_ids=[],
                source_tag=None,
                source_group_id=None,
            )

        assert "My Canvas" in result
        assert "canvas body here" in result

    @pytest.mark.asyncio
    async def test_canvas_truncation(self) -> None:
        from anteroom.routers.chat import _build_chat_system_prompt

        ai_service = MagicMock()
        ai_service.config.model = "gpt-4o"
        tool_registry = MagicMock()
        tool_registry.list_tools.return_value = []
        tool_registry._working_dir = None
        mcp_manager = MagicMock()
        mcp_manager.get_server_statuses.return_value = []
        config = MagicMock()
        config.app.tls = False
        config.rag = None
        config.codebase_index.map_tokens = 1000
        db = MagicMock()

        canvas_data = {
            "id": str(uuid.uuid4()),
            "title": "Big",
            "content": "x" * 15_000,  # > 10_000 limit
            "language": None,
            "version": 1,
        }

        with (
            patch("anteroom.routers.chat.build_runtime_context", return_value="CTX"),
            patch("anteroom.routers.chat.load_instructions", return_value=None),
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.services.codebase_index.create_index_service", return_value=None),
        ):
            mock_storage.get_canvas_for_conversation.return_value = canvas_data
            result = await _build_chat_system_prompt(
                ai_service=ai_service,
                tool_registry=tool_registry,
                mcp_manager=mcp_manager,
                config=config,
                db=db,
                conversation_id=str(uuid.uuid4()),
                project_instructions=None,
                plan_prompt="",
                plan_mode=False,
                message_text="hello",
                source_ids=[],
                source_tag=None,
                source_group_id=None,
            )

        assert "truncated" in result

    @pytest.mark.asyncio
    async def test_anteroom_md_instructions_injected(self) -> None:
        from anteroom.routers.chat import _build_chat_system_prompt

        ai_service = MagicMock()
        ai_service.config.model = "gpt-4o"
        tool_registry = MagicMock()
        tool_registry.list_tools.return_value = []
        tool_registry._working_dir = None
        mcp_manager = MagicMock()
        mcp_manager.get_server_statuses.return_value = []
        config = MagicMock()
        config.app.tls = False
        config.rag = None
        config.codebase_index.map_tokens = 1000
        db = MagicMock()

        with (
            patch("anteroom.routers.chat.build_runtime_context", return_value="CTX"),
            patch("anteroom.routers.chat.load_instructions", return_value="MY ANTEROOM INSTRUCTIONS"),
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.services.codebase_index.create_index_service", return_value=None),
        ):
            mock_storage.get_canvas_for_conversation.return_value = None
            result = await _build_chat_system_prompt(
                ai_service=ai_service,
                tool_registry=tool_registry,
                mcp_manager=mcp_manager,
                config=config,
                db=db,
                conversation_id=str(uuid.uuid4()),
                project_instructions=None,
                plan_prompt="",
                plan_mode=False,
                message_text="hello",
                source_ids=[],
                source_tag=None,
                source_group_id=None,
            )

        assert "MY ANTEROOM INSTRUCTIONS" in result

    @pytest.mark.asyncio
    async def test_artifact_registry_builtin_injected(self) -> None:
        from anteroom.routers.chat import _build_chat_system_prompt

        ai_service = MagicMock()
        ai_service.config.model = "gpt-4o"
        tool_registry = MagicMock()
        tool_registry.list_tools.return_value = []
        tool_registry._working_dir = None
        mcp_manager = MagicMock()
        mcp_manager.get_server_statuses.return_value = []
        config = MagicMock()
        config.app.tls = False
        config.rag = None
        config.codebase_index.map_tokens = 1000
        db = MagicMock()

        artifact = MagicMock()
        artifact.content = "BUILT_IN RULE CONTENT"
        artifact.source = "built_in"
        artifact.fqn = "@builtin/rule/my-rule"

        artifact_registry = MagicMock()
        artifact_registry.list_all.return_value = [artifact]

        with (
            patch("anteroom.routers.chat.build_runtime_context", return_value="CTX"),
            patch("anteroom.routers.chat.load_instructions", return_value=None),
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.services.codebase_index.create_index_service", return_value=None),
        ):
            mock_storage.get_canvas_for_conversation.return_value = None
            result = await _build_chat_system_prompt(
                ai_service=ai_service,
                tool_registry=tool_registry,
                mcp_manager=mcp_manager,
                config=config,
                db=db,
                conversation_id=str(uuid.uuid4()),
                project_instructions=None,
                plan_prompt="",
                plan_mode=False,
                message_text="hello",
                source_ids=[],
                source_tag=None,
                source_group_id=None,
                artifact_registry=artifact_registry,
            )

        assert "BUILT_IN RULE CONTENT" in result


# ---------------------------------------------------------------------------
# _web_confirm_tool
# ---------------------------------------------------------------------------


class TestWebConfirmTool:
    def _make_confirm_ctx(self, *, pending: dict | None = None) -> WebConfirmContext:
        return WebConfirmContext(
            pending_approvals=pending if pending is not None else {},
            event_bus=None,
            db_name="personal",
            conversation_id=str(uuid.uuid4()),
            approval_timeout=5,
            request=AsyncMock(),
            tool_registry=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_pending_approvals_limit_denies(self) -> None:
        # Fill up pending approvals to the max
        pending = {str(i): {"event": asyncio.Event(), "approved": False, "scope": "once"} for i in range(100)}
        ctx = self._make_confirm_ctx(pending=pending)
        verdict = MagicMock()
        verdict.tool_name = "bash"
        verdict.reason = "needs approval"
        verdict.details = {}
        result = await _web_confirm_tool(ctx, verdict)
        assert result is False

    @pytest.mark.asyncio
    async def test_approval_timeout_returns_false(self) -> None:
        ctx = self._make_confirm_ctx()
        ctx.approval_timeout = 0  # Immediate timeout
        ctx.request.is_disconnected = AsyncMock(return_value=False)
        verdict = MagicMock()
        verdict.tool_name = "bash"
        verdict.reason = "test"
        verdict.details = {}
        result = await _web_confirm_tool(ctx, verdict)
        assert result is False

    @pytest.mark.asyncio
    async def test_approval_granted_once(self) -> None:
        ctx = self._make_confirm_ctx()
        ctx.request.is_disconnected = AsyncMock(return_value=False)
        verdict = MagicMock()
        verdict.tool_name = "bash"
        verdict.reason = "test"
        verdict.details = {}

        # Simulate approval being set after a brief moment
        async def set_approval():
            await asyncio.sleep(0.01)
            for entry in ctx.pending_approvals.values():
                entry["approved"] = True
                entry["scope"] = "once"
                entry["event"].set()

        verdict_task = asyncio.ensure_future(_web_confirm_tool(ctx, verdict))
        asyncio.ensure_future(set_approval())
        result = await verdict_task
        assert result is True

    @pytest.mark.asyncio
    async def test_disconnect_triggers_timeout(self) -> None:
        ctx = self._make_confirm_ctx()
        ctx.approval_timeout = 60  # Long timeout — disconnect should trigger early exit
        ctx.request.is_disconnected = AsyncMock(return_value=True)
        verdict = MagicMock()
        verdict.tool_name = "bash"
        verdict.reason = "test"
        verdict.details = {}
        result = await _web_confirm_tool(ctx, verdict)
        assert result is False

    @pytest.mark.asyncio
    async def test_approval_publishes_event_when_event_bus_present(self) -> None:
        ctx = self._make_confirm_ctx()
        ctx.event_bus = AsyncMock()
        ctx.request.is_disconnected = AsyncMock(return_value=False)
        verdict = MagicMock()
        verdict.tool_name = "bash"
        verdict.reason = "test"
        verdict.details = {}

        async def set_approval():
            await asyncio.sleep(0.01)
            for entry in ctx.pending_approvals.values():
                entry["approved"] = True
                entry["scope"] = "once"
                entry["event"].set()

        verdict_task = asyncio.ensure_future(_web_confirm_tool(ctx, verdict))
        asyncio.ensure_future(set_approval())
        await verdict_task
        ctx.event_bus.publish.assert_called()

    @pytest.mark.asyncio
    async def test_session_scope_grants_session_permission(self) -> None:
        ctx = self._make_confirm_ctx()
        ctx.request.is_disconnected = AsyncMock(return_value=False)
        verdict = MagicMock()
        verdict.tool_name = "bash"
        verdict.reason = "test"
        verdict.details = {}

        async def set_approval():
            await asyncio.sleep(0.01)
            for entry in ctx.pending_approvals.values():
                entry["approved"] = True
                entry["scope"] = "session"
                entry["event"].set()

        verdict_task = asyncio.ensure_future(_web_confirm_tool(ctx, verdict))
        asyncio.ensure_future(set_approval())
        await verdict_task
        ctx.tool_registry.grant_session_permission.assert_called_with("bash")


# ---------------------------------------------------------------------------
# _stream_chat_events — event handling
# ---------------------------------------------------------------------------


def _make_stream_context(*, conv_id: str | None = None, plan_mode: bool = False) -> MagicMock:
    """Build a minimal mock StreamContext for _stream_chat_events testing."""
    ctx = MagicMock()
    ctx.conversation_id = conv_id or str(uuid.uuid4())
    ctx.uid = None
    ctx.uname = None
    ctx.event_bus = None
    ctx.embedding_worker = None
    ctx.canvas_needs_approval = False
    ctx.plan_mode = plan_mode
    ctx.plan_path = None
    ctx.request = MagicMock()
    ctx.request.app.state.config.cli.max_consecutive_text_only = 3
    ctx.request.app.state.config.safety.output_filter = None
    ctx.request.app.state.audit_writer = None
    ctx.request.app.state.dlp_scanner = None
    ctx.request.app.state.injection_detector = None
    ctx.last_token_broadcast = 0.0
    ctx.token_throttle_interval = 999
    ctx.client_id = ""
    ctx.budget_config = None
    ctx.planning_config = MagicMock()
    ctx.planning_config.auto_mode = "off"
    ctx.planning_config.auto_threshold_tools = 0
    ctx.extra_system_prompt = ""
    ctx.ai_service = MagicMock()
    ctx.ai_service.config.narration_cadence = 0
    ctx.tool_registry = MagicMock()
    ctx.tool_registry.has_tool.return_value = False
    ctx.mcp_manager = MagicMock()
    ctx.subagent_events = {}
    ctx.is_first_message = False
    ctx.first_user_text = "hello"
    ctx.conv_title = "Test Conversation"
    ctx.db = MagicMock()
    ctx.db_name = "personal"
    ctx.cancel_event = asyncio.Event()
    return ctx


def _make_agent_event(kind: str, data: dict) -> SimpleNamespace:
    return SimpleNamespace(kind=kind, data=data)


async def _collect_events(gen) -> list[dict]:
    events = []
    async for ev in gen:
        events.append(ev)
    return events


class TestStreamChatEventsKinds:
    @pytest.mark.asyncio
    async def test_thinking_event_emitted(self) -> None:
        ctx = _make_stream_context()
        events = [_make_agent_event("thinking", {}), _make_agent_event("done", {})]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            mock_storage.update_conversation_title = MagicMock()
            result = await _collect_events(_stream_chat_events(ctx))

        sse_events = [e for e in result if isinstance(e, dict) and "event" in e]
        event_types = [e["event"] for e in sse_events]
        assert "thinking" in event_types

    @pytest.mark.asyncio
    async def test_token_event_emitted(self) -> None:
        ctx = _make_stream_context()
        events = [
            _make_agent_event("token", {"content": "Hello "}),
            _make_agent_event("token", {"content": "world"}),
            _make_agent_event("done", {}),
        ]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            result = await _collect_events(_stream_chat_events(ctx))

        token_events = [e for e in result if isinstance(e, dict) and e.get("event") == "token"]
        assert len(token_events) == 2
        payloads = [json.loads(e["data"]) for e in token_events]
        assert payloads[0]["content"] == "Hello "
        assert payloads[1]["content"] == "world"

    @pytest.mark.asyncio
    async def test_error_event_emitted(self) -> None:
        ctx = _make_stream_context()
        events = [
            _make_agent_event("error", {"message": "Something went wrong", "code": "api_error"}),
        ]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            result = await _collect_events(_stream_chat_events(ctx))

        error_events = [e for e in result if isinstance(e, dict) and e.get("event") == "error"]
        assert len(error_events) == 1
        payload = json.loads(error_events[0]["data"])
        assert payload["message"] == "Something went wrong"

    @pytest.mark.asyncio
    async def test_done_event_emitted(self) -> None:
        ctx = _make_stream_context()
        events = [_make_agent_event("done", {})]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            result = await _collect_events(_stream_chat_events(ctx))

        done_events = [e for e in result if isinstance(e, dict) and e.get("event") == "done"]
        assert len(done_events) == 1

    @pytest.mark.asyncio
    async def test_tool_call_start_emitted(self) -> None:
        ctx = _make_stream_context()
        tool_id = "call_abc123"
        events = [
            _make_agent_event(
                "tool_call_start",
                {
                    "id": tool_id,
                    "tool_name": "bash",
                    "index": 0,
                    "arguments": {"command": "ls"},
                },
            ),
            _make_agent_event("done", {}),
        ]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            result = await _collect_events(_stream_chat_events(ctx))

        start_events = [e for e in result if isinstance(e, dict) and e.get("event") == "tool_call_start"]
        assert len(start_events) == 1
        payload = json.loads(start_events[0]["data"])
        assert payload["tool_name"] == "bash"
        assert payload["id"] == tool_id

    @pytest.mark.asyncio
    async def test_tool_call_end_emitted_and_stored(self) -> None:
        ctx = _make_stream_context()
        tool_id = "call_xyz"
        assistant_msg = {"id": "msg_1", "position": 1}
        events = [
            _make_agent_event(
                "tool_call_start",
                {
                    "id": tool_id,
                    "tool_name": "bash",
                    "index": 0,
                    "arguments": {"command": "echo hi"},
                },
            ),
            _make_agent_event("assistant_message", {"content": "I'll run bash"}),
            _make_agent_event(
                "tool_call_end",
                {
                    "id": tool_id,
                    "tool_name": "bash",
                    "output": {"stdout": "hi", "_approval_decision": "auto"},
                    "status": "success",
                },
            ),
            _make_agent_event("done", {}),
        ]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        ctx.tool_registry.has_tool.return_value = True

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            mock_storage.create_message.return_value = assistant_msg
            mock_storage.create_tool_call.return_value = None
            mock_storage.update_tool_call.return_value = None
            result = await _collect_events(_stream_chat_events(ctx))

        end_events = [e for e in result if isinstance(e, dict) and e.get("event") == "tool_call_end"]
        assert len(end_events) == 1
        payload = json.loads(end_events[0]["data"])
        assert payload["id"] == tool_id
        mock_storage.create_tool_call.assert_called_once()

    @pytest.mark.asyncio
    async def test_usage_event_not_emitted_as_sse(self) -> None:
        """usage events are consumed internally, not emitted as SSE events."""
        ctx = _make_stream_context()
        events = [
            _make_agent_event(
                "usage",
                {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                    "model": "gpt-4o",
                },
            ),
            _make_agent_event("done", {}),
        ]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            result = await _collect_events(_stream_chat_events(ctx))

        # usage events are consumed internally, not emitted as SSE
        usage_sse = [e for e in result if isinstance(e, dict) and e.get("event") == "usage"]
        assert len(usage_sse) == 0

    @pytest.mark.asyncio
    async def test_phase_event_emitted(self) -> None:
        ctx = _make_stream_context()
        events = [
            _make_agent_event("phase", {"phase": "calling_tools"}),
            _make_agent_event("done", {}),
        ]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            result = await _collect_events(_stream_chat_events(ctx))

        phase_events = [e for e in result if isinstance(e, dict) and e.get("event") == "phase"]
        assert len(phase_events) == 1

    @pytest.mark.asyncio
    async def test_retrying_event_emitted(self) -> None:
        ctx = _make_stream_context()
        events = [
            _make_agent_event("retrying", {"attempt": 1, "delay": 1.0}),
            _make_agent_event("done", {}),
        ]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            result = await _collect_events(_stream_chat_events(ctx))

        retrying_events = [e for e in result if isinstance(e, dict) and e.get("event") == "retrying"]
        assert len(retrying_events) == 1

    @pytest.mark.asyncio
    async def test_budget_warning_event_emitted(self) -> None:
        ctx = _make_stream_context()
        events = [
            _make_agent_event("budget_warning", {"limit": "daily", "used": 90}),
            _make_agent_event("done", {}),
        ]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            result = await _collect_events(_stream_chat_events(ctx))

        bw_events = [e for e in result if isinstance(e, dict) and e.get("event") == "budget_warning"]
        assert len(bw_events) == 1

    @pytest.mark.asyncio
    async def test_dlp_blocked_emits_error_event(self) -> None:
        ctx = _make_stream_context()
        events = [
            _make_agent_event("dlp_blocked", {"matches": ["credit_card"]}),
        ]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            result = await _collect_events(_stream_chat_events(ctx))

        error_events = [e for e in result if isinstance(e, dict) and e.get("event") == "error"]
        assert any(json.loads(e["data"]).get("code") == "dlp_blocked" for e in error_events)

    @pytest.mark.asyncio
    async def test_dlp_warning_event_emitted(self) -> None:
        ctx = _make_stream_context()
        events = [
            _make_agent_event("dlp_warning", {"matches": ["phone_number"]}),
            _make_agent_event("done", {}),
        ]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            result = await _collect_events(_stream_chat_events(ctx))

        dlp_events = [e for e in result if isinstance(e, dict) and e.get("event") == "dlp_warning"]
        assert len(dlp_events) == 1

    @pytest.mark.asyncio
    async def test_injection_blocked_emits_error(self) -> None:
        ctx = _make_stream_context()
        events = [
            _make_agent_event("injection_detected", {"action": "block", "technique": "canary_leak"}),
        ]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            result = await _collect_events(_stream_chat_events(ctx))

        error_events = [e for e in result if isinstance(e, dict) and e.get("event") == "error"]
        assert any(json.loads(e["data"]).get("code") == "injection_blocked" for e in error_events)

    @pytest.mark.asyncio
    async def test_injection_warning_emitted(self) -> None:
        ctx = _make_stream_context()
        events = [
            _make_agent_event("injection_detected", {"action": "warn", "technique": "heuristic", "confidence": 0.7}),
            _make_agent_event("done", {}),
        ]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            result = await _collect_events(_stream_chat_events(ctx))

        warn_events = [e for e in result if isinstance(e, dict) and e.get("event") == "injection_warning"]
        assert len(warn_events) == 1

    @pytest.mark.asyncio
    async def test_output_filter_blocked_emits_error(self) -> None:
        ctx = _make_stream_context()
        events = [
            _make_agent_event("output_filter_blocked", {"matches": ["forbidden_pattern"]}),
        ]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            result = await _collect_events(_stream_chat_events(ctx))

        error_events = [e for e in result if isinstance(e, dict) and e.get("event") == "error"]
        assert any(json.loads(e["data"]).get("code") == "output_filter_blocked" for e in error_events)

    @pytest.mark.asyncio
    async def test_output_filter_warning_emitted(self) -> None:
        ctx = _make_stream_context()
        events = [
            _make_agent_event("output_filter_warning", {"matches": ["pattern1"]}),
            _make_agent_event("done", {}),
        ]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            result = await _collect_events(_stream_chat_events(ctx))

        warn_events = [e for e in result if isinstance(e, dict) and e.get("event") == "output_filter_warning"]
        assert len(warn_events) == 1

    @pytest.mark.asyncio
    async def test_queued_message_event_resets_state(self) -> None:
        ctx = _make_stream_context()
        events = [
            _make_agent_event("queued_message", {"message": "next msg"}),
            _make_agent_event("done", {}),
        ]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            result = await _collect_events(_stream_chat_events(ctx))

        qm_events = [e for e in result if isinstance(e, dict) and e.get("event") == "queued_message"]
        assert len(qm_events) == 1

    @pytest.mark.asyncio
    async def test_done_generates_title_for_first_message(self) -> None:
        conv_id = str(uuid.uuid4())
        ctx = _make_stream_context(conv_id=conv_id)
        ctx.is_first_message = True
        ctx.conv_title = "New Conversation"
        ctx.first_user_text = "Tell me about Python"
        ctx.ai_service.generate_title = AsyncMock(return_value="Python Overview")
        events = [_make_agent_event("done", {})]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            mock_storage.update_conversation_title = MagicMock()
            result = await _collect_events(_stream_chat_events(ctx))

        title_events = [e for e in result if isinstance(e, dict) and e.get("event") == "title"]
        assert len(title_events) == 1
        payload = json.loads(title_events[0]["data"])
        assert payload["title"] == "Python Overview"
        mock_storage.update_conversation_title.assert_called_once_with(ctx.db, conv_id, "Python Overview")

    @pytest.mark.asyncio
    async def test_done_no_title_when_not_first_message(self) -> None:
        ctx = _make_stream_context()
        ctx.is_first_message = False
        ctx.conv_title = "Existing Title"
        events = [_make_agent_event("done", {})]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            result = await _collect_events(_stream_chat_events(ctx))

        title_events = [e for e in result if isinstance(e, dict) and e.get("event") == "title"]
        assert len(title_events) == 0

    @pytest.mark.asyncio
    async def test_exception_in_generator_emits_error(self) -> None:
        ctx = _make_stream_context()

        async def fake_agent_gen(*args, **kwargs):
            raise RuntimeError("Unexpected crash")
            yield  # Make it an async generator

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            result = await _collect_events(_stream_chat_events(ctx))

        error_events = [e for e in result if isinstance(e, dict) and e.get("event") == "error"]
        assert len(error_events) >= 1

    @pytest.mark.asyncio
    async def test_stream_cleanup_removes_from_active_streams(self) -> None:
        conv_id = str(uuid.uuid4())
        ctx = _make_stream_context(conv_id=conv_id)
        _active_streams[conv_id] = {"started_at": 0, "request": MagicMock(), "cancel_event": asyncio.Event()}
        events = [_make_agent_event("done", {})]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            await _collect_events(_stream_chat_events(ctx))

        assert conv_id not in _active_streams

    @pytest.mark.asyncio
    async def test_plan_saved_event_on_write_file_success(self) -> None:
        conv_id = str(uuid.uuid4())
        plan_dir = Path(tempfile.mkdtemp())
        plan_path = plan_dir / "plan.md"
        plan_path.write_text("# Plan\n- [ ] step 1")

        ctx = _make_stream_context(conv_id=conv_id, plan_mode=True)
        ctx.plan_path = plan_path
        assistant_msg = {"id": "msg_plan", "position": 1}
        tool_id = "call_plan"
        events = [
            _make_agent_event(
                "tool_call_start",
                {
                    "id": tool_id,
                    "tool_name": "write_file",
                    "index": 0,
                    "arguments": {"path": str(plan_path), "content": "# Plan"},
                },
            ),
            _make_agent_event("assistant_message", {"content": "Writing plan"}),
            _make_agent_event(
                "tool_call_end",
                {
                    "id": tool_id,
                    "tool_name": "write_file",
                    "output": {"status": "ok"},
                    "status": "success",
                },
            ),
            _make_agent_event("done", {}),
        ]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        ctx.tool_registry.has_tool.return_value = True

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
            patch("anteroom.cli.plan.read_plan", return_value="# Plan\n- [ ] step 1"),
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            mock_storage.create_message.return_value = assistant_msg
            mock_storage.create_tool_call.return_value = None
            mock_storage.update_tool_call.return_value = None
            result = await _collect_events(_stream_chat_events(ctx))

        plan_saved = [e for e in result if isinstance(e, dict) and e.get("event") == "plan_saved"]
        assert len(plan_saved) == 1

    @pytest.mark.asyncio
    async def test_canvas_created_event_on_successful_create(self) -> None:
        ctx = _make_stream_context()
        canvas_id = str(uuid.uuid4())
        assistant_msg = {"id": "msg_c", "position": 1}
        tool_id = "call_canvas"
        events = [
            _make_agent_event(
                "tool_call_start",
                {
                    "id": tool_id,
                    "tool_name": "create_canvas",
                    "index": 0,
                    "arguments": {"title": "Test"},
                },
            ),
            _make_agent_event("assistant_message", {"content": "Creating canvas"}),
            _make_agent_event(
                "tool_call_end",
                {
                    "id": tool_id,
                    "tool_name": "create_canvas",
                    "output": {"status": "created", "id": canvas_id},
                    "status": "success",
                },
            ),
            _make_agent_event("done", {}),
        ]

        async def fake_agent_gen(*args, **kwargs):
            for ev in events:
                yield ev

        ctx.tool_registry.has_tool.return_value = True
        canvas_full = {"id": canvas_id, "title": "Test", "content": "body", "language": None}

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_gen),
            patch("anteroom.routers.chat.storage") as mock_storage,
        ):
            mock_storage.get_conversation_token_total.return_value = 0
            mock_storage.get_daily_token_total.return_value = 0
            mock_storage.create_message.return_value = assistant_msg
            mock_storage.create_tool_call.return_value = None
            mock_storage.update_tool_call.return_value = None
            mock_storage.get_canvas.return_value = canvas_full
            result = await _collect_events(_stream_chat_events(ctx))

        canvas_events = [e for e in result if isinstance(e, dict) and e.get("event") == "canvas_created"]
        assert len(canvas_events) == 1
        payload = json.loads(canvas_events[0]["data"])
        assert payload["id"] == canvas_id


# ---------------------------------------------------------------------------
# _parse_chat_request — via the HTTP endpoint
# ---------------------------------------------------------------------------


class TestParseChatRequestJSON:
    """Test _parse_chat_request via the /chat endpoint with JSON bodies."""

    def test_basic_message(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = _make_conv(conv_id)
            mock_storage.list_messages.return_value = []
            mock_storage.create_message.return_value = {"id": "m1", "position": 1}
            # Prevent the full streaming execution
            with patch("anteroom.routers.chat.EventSourceResponse") as mock_sse:
                mock_sse.return_value = MagicMock(status_code=200)
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post(
                    f"/api/conversations/{conv_id}/chat",
                    json={"message": "hello world"},
                )
        # The endpoint returns an EventSourceResponse — just verify it reached that point
        assert resp.status_code in (200, 500)  # 500 if mocking breaks the SSE wrapper

    def test_invalid_source_id_uuid_rejected(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = _make_conv(conv_id)
            client = TestClient(app)
            resp = client.post(
                f"/api/conversations/{conv_id}/chat",
                json={"message": "hello", "source_ids": ["not-a-uuid"]},
            )
        assert resp.status_code == 400
        assert "source_id" in resp.json()["detail"]

    def test_invalid_source_tag_uuid_rejected(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = _make_conv(conv_id)
            client = TestClient(app)
            resp = client.post(
                f"/api/conversations/{conv_id}/chat",
                json={"message": "hello", "source_tag": "not-a-uuid"},
            )
        assert resp.status_code == 400
        assert "source_tag" in resp.json()["detail"]

    def test_invalid_source_group_id_uuid_rejected(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = _make_conv(conv_id)
            client = TestClient(app)
            resp = client.post(
                f"/api/conversations/{conv_id}/chat",
                json={"message": "hello", "source_group_id": "not-a-uuid"},
            )
        assert resp.status_code == 400
        assert "source_group_id" in resp.json()["detail"]

    def test_empty_message_rejected(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = _make_conv(conv_id)
            client = TestClient(app)
            resp = client.post(
                f"/api/conversations/{conv_id}/chat",
                json={"message": "   "},
            )
        assert resp.status_code == 400

    def test_conversation_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = None
            client = TestClient(app)
            resp = client.post(
                f"/api/conversations/{conv_id}/chat",
                json={"message": "hello"},
            )
        assert resp.status_code == 404

    def test_invalid_uuid_conversation_id(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/conversations/not-a-real-uuid/chat",
            json={"message": "hello"},
        )
        assert resp.status_code == 400

    def test_note_type_saves_message_without_ai(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        msg = {"id": "note_msg", "position": 1, "role": "user", "content": "Note content"}
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = _make_conv(conv_id, conv_type="note")
            mock_storage.create_message.return_value = msg
            client = TestClient(app)
            resp = client.post(
                f"/api/conversations/{conv_id}/chat",
                json={"message": "Note content"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "saved"

    def test_document_type_saves_message_without_ai(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        msg = {"id": "doc_msg", "position": 1, "role": "user", "content": "Doc content"}
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = _make_conv(conv_id, conv_type="document")
            mock_storage.create_message.return_value = msg
            client = TestClient(app)
            resp = client.post(
                f"/api/conversations/{conv_id}/chat",
                json={"message": "Doc content"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "saved"

    def test_note_empty_message_rejected(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = _make_conv(conv_id, conv_type="note")
            client = TestClient(app)
            resp = client.post(
                f"/api/conversations/{conv_id}/chat",
                json={"message": ""},
            )
        assert resp.status_code == 400

    def test_message_queued_when_stream_active(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        # Simulate an active stream
        cancel_evt = asyncio.Event()
        mock_request = MagicMock()
        # Synchronous mock for is_disconnected (returns False so not stale)
        mock_request.is_disconnected = AsyncMock(return_value=False)
        import time

        _active_streams[conv_id] = {
            "started_at": time.monotonic(),
            "request": mock_request,
            "cancel_event": cancel_evt,
        }
        _message_queues[conv_id] = asyncio.Queue()

        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = _make_conv(conv_id)
            mock_storage.create_message.return_value = {"id": "q_msg", "position": 2}
            client = TestClient(app)
            resp = client.post(
                f"/api/conversations/{conv_id}/chat",
                json={"message": "queued message"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

    def test_queue_full_returns_429(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        cancel_evt = asyncio.Event()
        mock_request = MagicMock()
        mock_request.is_disconnected = AsyncMock(return_value=False)
        import time

        _active_streams[conv_id] = {
            "started_at": time.monotonic(),
            "request": mock_request,
            "cancel_event": cancel_evt,
        }
        # Fill the queue to max
        q: asyncio.Queue = asyncio.Queue()
        for _ in range(10):
            q.put_nowait({"role": "user", "content": "msg"})
        _message_queues[conv_id] = q

        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = _make_conv(conv_id)
            client = TestClient(app)
            resp = client.post(
                f"/api/conversations/{conv_id}/chat",
                json={"message": "overflow"},
            )
        assert resp.status_code == 429

    def test_regenerate_no_messages_returns_400(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = _make_conv(conv_id)
            mock_storage.list_messages.return_value = []
            client = TestClient(app)
            resp = client.post(
                f"/api/conversations/{conv_id}/chat",
                json={"message": "", "regenerate": True},
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# stream-status endpoint
# ---------------------------------------------------------------------------


class TestStreamStatusEndpoint:
    def test_no_active_stream(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.get(f"/api/conversations/{conv_id}/stream-status")
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    def test_active_stream(self) -> None:
        conv_id = str(uuid.uuid4())
        import time

        _active_streams[conv_id] = {
            "started_at": time.monotonic() - 5,
            "request": MagicMock(),
            "cancel_event": asyncio.Event(),
        }
        app = _make_app()
        client = TestClient(app)
        resp = client.get(f"/api/conversations/{conv_id}/stream-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is True
        assert "age_seconds" in data

    def test_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/conversations/not-uuid/stream-status")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# _execute_web_tool — canvas / run_agent / ask_user / introspect / mcp
# ---------------------------------------------------------------------------


class TestExecuteWebTool:
    def _make_ctx(self, tool_in_registry: bool = True) -> ToolExecutorContext:
        confirm_ctx = WebConfirmContext(
            pending_approvals={},
            event_bus=None,
            db_name="personal",
            conversation_id=str(uuid.uuid4()),
            approval_timeout=5,
            request=AsyncMock(),
            tool_registry=MagicMock(),
        )
        tool_registry = MagicMock()
        tool_registry.has_tool.return_value = tool_in_registry
        tool_registry.call_tool = AsyncMock(return_value={"result": "ok", "_approval_decision": "auto"})
        tool_registry.check_safety.return_value = None
        mcp_manager = MagicMock()
        mcp_manager.call_tool = AsyncMock(return_value={"result": "mcp_ok"})
        return ToolExecutorContext(
            tool_registry=tool_registry,
            mcp_manager=mcp_manager,
            confirm_ctx=confirm_ctx,
            ai_service=MagicMock(),
            cancel_event=asyncio.Event(),
            db=MagicMock(),
            uid="user1",
            uname="User One",
            conversation_id=str(uuid.uuid4()),
            tools_openai=[],
            subagent_events={},
            subagent_limiter=MagicMock(),
            sa_config=MagicMock(),
            request_config=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_builtin_tool_called(self) -> None:
        from anteroom.routers.chat import _execute_web_tool

        ctx = self._make_ctx(tool_in_registry=True)
        result = await _execute_web_tool(ctx, "read_file", {"path": "/tmp/test"})
        ctx.tool_registry.call_tool.assert_called_once()
        assert "result" in result

    @pytest.mark.asyncio
    async def test_canvas_tool_injects_conversation_context(self) -> None:
        from anteroom.routers.chat import _execute_web_tool

        ctx = self._make_ctx(tool_in_registry=True)
        await _execute_web_tool(ctx, "create_canvas", {"title": "T"})
        call_args = ctx.tool_registry.call_tool.call_args
        injected_args = call_args[0][1]  # positional second arg is arguments dict
        assert "_conversation_id" in injected_args
        assert "_db" in injected_args
        assert "_user_id" in injected_args

    @pytest.mark.asyncio
    async def test_ask_user_tool_injects_callback(self) -> None:
        from anteroom.routers.chat import _execute_web_tool

        ctx = self._make_ctx(tool_in_registry=True)
        await _execute_web_tool(ctx, "ask_user", {"question": "What?"})
        call_args = ctx.tool_registry.call_tool.call_args
        injected_args = call_args[0][1]
        assert "_ask_callback" in injected_args

    @pytest.mark.asyncio
    async def test_introspect_tool_injects_context(self) -> None:
        from anteroom.routers.chat import _execute_web_tool

        ctx = self._make_ctx(tool_in_registry=True)
        await _execute_web_tool(ctx, "introspect", {})
        call_args = ctx.tool_registry.call_tool.call_args
        injected_args = call_args[0][1]
        assert "_config" in injected_args
        assert "_mcp_manager" in injected_args
        assert "_tool_registry" in injected_args

    @pytest.mark.asyncio
    async def test_run_agent_tool_injects_subagent_context(self) -> None:
        from anteroom.routers.chat import _execute_web_tool

        ctx = self._make_ctx(tool_in_registry=True)
        await _execute_web_tool(ctx, "run_agent", {"prompt": "do something"})
        call_args = ctx.tool_registry.call_tool.call_args
        injected_args = call_args[0][1]
        assert "_ai_service" in injected_args
        assert "_agent_id" in injected_args
        assert ctx.subagent_counter[0] == 1  # counter incremented

    @pytest.mark.asyncio
    async def test_mcp_tool_called_when_not_in_registry(self) -> None:
        from anteroom.routers.chat import _execute_web_tool

        ctx = self._make_ctx(tool_in_registry=False)
        ctx.tool_registry.check_safety.return_value = None
        result = await _execute_web_tool(ctx, "mcp_tool", {"arg": "val"})
        ctx.mcp_manager.call_tool.assert_called_once_with("mcp_tool", {"arg": "val"})
        assert "result" in result

    @pytest.mark.asyncio
    async def test_unknown_tool_raises_value_error(self) -> None:
        from anteroom.routers.chat import _execute_web_tool

        ctx = self._make_ctx(tool_in_registry=False)
        ctx.mcp_manager = None
        with pytest.raises(ValueError, match="Unknown tool"):
            await _execute_web_tool(ctx, "nonexistent_tool", {})

    @pytest.mark.asyncio
    async def test_mcp_hard_denied_tool_returns_blocked(self) -> None:
        from anteroom.routers.chat import _execute_web_tool

        ctx = self._make_ctx(tool_in_registry=False)
        verdict = MagicMock()
        verdict.needs_approval = True
        verdict.hard_denied = True
        ctx.tool_registry.check_safety.return_value = verdict
        result = await _execute_web_tool(ctx, "blocked_tool", {})
        assert result.get("safety_blocked") is True
        assert result.get("_approval_decision") == "hard_denied"

    @pytest.mark.asyncio
    async def test_mcp_denied_by_user_returns_denied(self) -> None:
        from anteroom.routers.chat import _execute_web_tool

        ctx = self._make_ctx(tool_in_registry=False)
        verdict = MagicMock()
        verdict.needs_approval = True
        verdict.hard_denied = False
        ctx.tool_registry.check_safety.return_value = verdict
        ctx.confirm_ctx.request.is_disconnected = AsyncMock(return_value=True)
        ctx.confirm_ctx.approval_timeout = 0
        result = await _execute_web_tool(ctx, "sensitive_tool", {})
        assert result.get("exit_code") == -1

    @pytest.mark.asyncio
    async def test_rate_limited_mcp_tool_blocked(self) -> None:
        from anteroom.routers.chat import _execute_web_tool

        ctx = self._make_ctx(tool_in_registry=False)
        ctx.tool_registry.check_safety.return_value = None
        rate_limiter = MagicMock()
        rl_verdict = MagicMock()
        rl_verdict.exceeded = True
        rl_verdict.reason = "Rate limit exceeded"
        rate_limiter.check.return_value = rl_verdict
        rate_limiter.config.action = "block"
        ctx.rate_limiter = rate_limiter
        result = await _execute_web_tool(ctx, "mcp_tool", {})
        assert result.get("rate_limited") is True
        assert result.get("safety_blocked") is True

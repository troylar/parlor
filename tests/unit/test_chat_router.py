"""Tests for web UI introspect wiring in routers/chat.py (_execute_web_tool)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from anteroom.routers.chat import ToolExecutorContext, WebConfirmContext, _execute_web_tool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_confirm_ctx() -> WebConfirmContext:
    mock_request = AsyncMock()
    mock_request.is_disconnected.return_value = False
    return WebConfirmContext(
        pending_approvals={},
        event_bus=AsyncMock(),
        db_name="test-db",
        conversation_id="conv-test-1",
        approval_timeout=30,
        request=mock_request,
        tool_registry=MagicMock(),
    )


def _make_tool_ctx(*, conversation_id: str = "conv-test-1", db: Any = None) -> ToolExecutorContext:
    tool_registry = MagicMock()
    tool_registry.has_tool.return_value = True
    tool_registry.call_tool = AsyncMock(return_value={"result": "ok"})
    tool_registry.check_safety.return_value = None

    return ToolExecutorContext(
        tool_registry=tool_registry,
        mcp_manager=None,
        confirm_ctx=_make_confirm_ctx(),
        ai_service=MagicMock(),
        cancel_event=asyncio.Event(),
        db=db or MagicMock(),
        uid="user-1",
        uname="testuser",
        conversation_id=conversation_id,
        tools_openai=[],
        subagent_events={},
        subagent_limiter=MagicMock(),
        sa_config=MagicMock(),
        request_config=MagicMock(),
        rate_limiter=None,
        skill_registry=None,
        rule_enforcer=None,
    )


def _fake_conversation(
    *,
    title: str = "Test Conv",
    slug: str = "test-slug",
    space_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": "conv-test-1",
        "title": title,
        "slug": slug,
        "space_id": space_id,
        "message_count": 999,  # intentionally wrong — must NOT be used
    }


# ---------------------------------------------------------------------------
# Tests: _runtime_info keys are present
# ---------------------------------------------------------------------------


class TestIntrospectRuntimeInfoKeys:
    async def test_runtime_info_includes_interface_web(self) -> None:
        ctx = _make_tool_ctx()
        conv = _fake_conversation()
        msgs = [MagicMock(), MagicMock(), MagicMock()]

        with (
            patch("anteroom.services.storage.get_conversation", return_value=conv),
            patch("anteroom.services.storage.list_messages", return_value=msgs),
            patch("anteroom.services.storage.get_conversation_token_total", return_value={"total": 100}),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        call_args = ctx.tool_registry.call_tool.call_args
        arguments = call_args.args[1]
        assert "_runtime_info" in arguments
        assert arguments["_runtime_info"]["interface"] == "web"

    async def test_runtime_info_includes_conversation_id(self) -> None:
        ctx = _make_tool_ctx(conversation_id="conv-xyz")
        conv = _fake_conversation()

        with (
            patch("anteroom.services.storage.get_conversation", return_value=conv),
            patch("anteroom.services.storage.list_messages", return_value=[]),
            patch("anteroom.services.storage.get_conversation_token_total", return_value=0),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        rt = arguments["_runtime_info"]
        assert rt["conversation_id"] == "conv-xyz"

    async def test_runtime_info_includes_conversation_title(self) -> None:
        ctx = _make_tool_ctx()
        conv = _fake_conversation(title="My Project Chat")

        with (
            patch("anteroom.services.storage.get_conversation", return_value=conv),
            patch("anteroom.services.storage.list_messages", return_value=[]),
            patch("anteroom.services.storage.get_conversation_token_total", return_value=0),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        assert arguments["_runtime_info"]["conversation_title"] == "My Project Chat"

    async def test_runtime_info_includes_slug(self) -> None:
        ctx = _make_tool_ctx()
        conv = _fake_conversation(slug="clever-mongoose")

        with (
            patch("anteroom.services.storage.get_conversation", return_value=conv),
            patch("anteroom.services.storage.list_messages", return_value=[]),
            patch("anteroom.services.storage.get_conversation_token_total", return_value=0),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        assert arguments["_runtime_info"]["slug"] == "clever-mongoose"


# ---------------------------------------------------------------------------
# Tests: message_count is derived from list_messages, NOT get_conversation
# ---------------------------------------------------------------------------


class TestIntrospectMessageCount:
    async def test_message_count_from_list_messages_not_conversation_field(self) -> None:
        ctx = _make_tool_ctx()
        conv = _fake_conversation()  # message_count=999 — must be ignored
        msgs = [MagicMock(), MagicMock()]  # actual count = 2

        with (
            patch("anteroom.services.storage.get_conversation", return_value=conv),
            patch("anteroom.services.storage.list_messages", return_value=msgs),
            patch("anteroom.services.storage.get_conversation_token_total", return_value=0),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        assert arguments["_runtime_info"]["message_count"] == 2

    async def test_message_count_is_len_of_list_messages_result(self) -> None:
        ctx = _make_tool_ctx()
        conv = _fake_conversation()
        msgs = [MagicMock() for _ in range(7)]

        with (
            patch("anteroom.services.storage.get_conversation", return_value=conv),
            patch("anteroom.services.storage.list_messages", return_value=msgs),
            patch("anteroom.services.storage.get_conversation_token_total", return_value=0),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        assert arguments["_runtime_info"]["message_count"] == 7

    async def test_message_count_zero_when_list_messages_raises(self) -> None:
        ctx = _make_tool_ctx()
        conv = _fake_conversation()

        with (
            patch("anteroom.services.storage.get_conversation", return_value=conv),
            patch("anteroom.services.storage.list_messages", side_effect=RuntimeError("db error")),
            patch("anteroom.services.storage.get_conversation_token_total", return_value=0),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        assert arguments["_runtime_info"]["message_count"] == 0


# ---------------------------------------------------------------------------
# Tests: token_totals is derived from get_conversation_token_total
# ---------------------------------------------------------------------------


class TestIntrospectTokenTotals:
    async def test_token_totals_from_storage(self) -> None:
        ctx = _make_tool_ctx()
        conv = _fake_conversation()
        token_data = {"prompt_tokens": 500, "completion_tokens": 200, "total_tokens": 700}

        with (
            patch("anteroom.services.storage.get_conversation", return_value=conv),
            patch("anteroom.services.storage.list_messages", return_value=[]),
            patch("anteroom.services.storage.get_conversation_token_total", return_value=token_data),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        assert arguments["_runtime_info"]["token_totals"] == token_data

    async def test_token_totals_zero_when_raises(self) -> None:
        ctx = _make_tool_ctx()
        conv = _fake_conversation()

        with (
            patch("anteroom.services.storage.get_conversation", return_value=conv),
            patch("anteroom.services.storage.list_messages", return_value=[]),
            patch(
                "anteroom.services.storage.get_conversation_token_total",
                side_effect=RuntimeError("db error"),
            ),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        assert arguments["_runtime_info"]["token_totals"] == 0


# ---------------------------------------------------------------------------
# Tests: get_conversation raises — _runtime_info only has interface
# ---------------------------------------------------------------------------


class TestIntrospectGetConversationRaises:
    async def test_runtime_info_only_has_interface_on_storage_error(self) -> None:
        ctx = _make_tool_ctx()

        with patch(
            "anteroom.services.storage.get_conversation",
            side_effect=RuntimeError("db error"),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        rt = arguments["_runtime_info"]
        assert set(rt.keys()) == {"interface"}
        assert rt["interface"] == "web"

    async def test_no_conversation_id_in_runtime_info_on_error(self) -> None:
        ctx = _make_tool_ctx()

        with patch(
            "anteroom.services.storage.get_conversation",
            side_effect=Exception("unexpected"),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        assert "conversation_id" not in arguments["_runtime_info"]

    async def test_no_message_count_in_runtime_info_on_error(self) -> None:
        ctx = _make_tool_ctx()

        with patch(
            "anteroom.services.storage.get_conversation",
            side_effect=Exception("unexpected"),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        assert "message_count" not in arguments["_runtime_info"]

    async def test_no_token_totals_in_runtime_info_on_error(self) -> None:
        ctx = _make_tool_ctx()

        with patch(
            "anteroom.services.storage.get_conversation",
            side_effect=Exception("unexpected"),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        assert "token_totals" not in arguments["_runtime_info"]


# ---------------------------------------------------------------------------
# Tests: arguments include _active_space and _db (parity with CLI)
# ---------------------------------------------------------------------------


class TestIntrospectParityUnderscoredParams:
    async def test_arguments_include_active_space(self) -> None:
        ctx = _make_tool_ctx()
        conv = _fake_conversation()

        with (
            patch("anteroom.services.storage.get_conversation", return_value=conv),
            patch("anteroom.services.storage.list_messages", return_value=[]),
            patch("anteroom.services.storage.get_conversation_token_total", return_value=0),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        assert "_active_space" in arguments

    async def test_arguments_include_db(self) -> None:
        ctx = _make_tool_ctx()
        conv = _fake_conversation()

        with (
            patch("anteroom.services.storage.get_conversation", return_value=conv),
            patch("anteroom.services.storage.list_messages", return_value=[]),
            patch("anteroom.services.storage.get_conversation_token_total", return_value=0),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        assert "_db" in arguments
        assert arguments["_db"] is ctx.db

    async def test_web_and_cli_share_same_underscore_param_keys(self) -> None:
        """Web introspect must pass the same set of _ params as the CLI."""
        ctx = _make_tool_ctx()
        conv = _fake_conversation()

        with (
            patch("anteroom.services.storage.get_conversation", return_value=conv),
            patch("anteroom.services.storage.list_messages", return_value=[]),
            patch("anteroom.services.storage.get_conversation_token_total", return_value=0),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        underscore_keys = {k for k in arguments if k.startswith("_")}

        # These are the same keys the CLI passes (repl.py lines 1379-1389)
        expected_cli_keys = {
            "_config",
            "_mcp_manager",
            "_tool_registry",
            "_skill_registry",
            "_instructions_info",
            "_tools_openai",
            "_working_dir",
            "_active_space",
            "_db",
            "_runtime_info",
        }
        assert expected_cli_keys == underscore_keys

    async def test_active_space_none_when_no_space_id(self) -> None:
        ctx = _make_tool_ctx()
        conv = _fake_conversation(space_id=None)

        with (
            patch("anteroom.services.storage.get_conversation", return_value=conv),
            patch("anteroom.services.storage.list_messages", return_value=[]),
            patch("anteroom.services.storage.get_conversation_token_total", return_value=0),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        assert arguments["_active_space"] is None

    async def test_active_space_populated_when_space_id_present(self) -> None:
        ctx = _make_tool_ctx()
        conv = _fake_conversation(space_id="space-abc")
        fake_space = {"id": "space-abc", "name": "my-space"}

        with (
            patch("anteroom.services.storage.get_conversation", return_value=conv),
            patch("anteroom.services.storage.list_messages", return_value=[]),
            patch("anteroom.services.storage.get_conversation_token_total", return_value=0),
            patch("anteroom.services.space_storage.get_space", return_value=fake_space),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        assert arguments["_active_space"] == fake_space

    async def test_active_space_in_runtime_info_when_space_present(self) -> None:
        ctx = _make_tool_ctx()
        conv = _fake_conversation(space_id="space-abc")
        fake_space = {"id": "space-abc", "name": "my-space"}

        with (
            patch("anteroom.services.storage.get_conversation", return_value=conv),
            patch("anteroom.services.storage.list_messages", return_value=[]),
            patch("anteroom.services.storage.get_conversation_token_total", return_value=0),
            patch("anteroom.services.space_storage.get_space", return_value=fake_space),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        rt = arguments["_runtime_info"]
        assert rt.get("active_space") == {"name": "my-space", "id": "space-abc"}

    async def test_runtime_info_interface_is_web_not_cli(self) -> None:
        """Ensure the web router does not accidentally set interface=cli."""
        ctx = _make_tool_ctx()
        conv = _fake_conversation()

        with (
            patch("anteroom.services.storage.get_conversation", return_value=conv),
            patch("anteroom.services.storage.list_messages", return_value=[]),
            patch("anteroom.services.storage.get_conversation_token_total", return_value=0),
        ):
            await _execute_web_tool(ctx, "introspect", {})

        arguments = ctx.tool_registry.call_tool.call_args.args[1]
        assert arguments["_runtime_info"]["interface"] != "cli"
        assert arguments["_runtime_info"]["interface"] == "web"

"""Tests for the web UI ask_user callback and SSE keepalive wrapper (#439)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from anteroom.routers.chat import WebConfirmContext, _web_ask_user_callback, _with_keepalive

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    *,
    pending: dict | None = None,
    event_bus: Any = None,
    approval_timeout: int = 30,
    is_disconnected: bool = False,
) -> WebConfirmContext:
    if pending is None:
        pending = {}
    mock_request = AsyncMock()
    mock_request.is_disconnected.return_value = is_disconnected
    return WebConfirmContext(
        pending_approvals=pending,
        event_bus=event_bus or AsyncMock(),
        db_name="test",
        conversation_id="conv-1",
        approval_timeout=approval_timeout,
        request=mock_request,
        tool_registry=MagicMock(),
    )


async def _answer_after(
    ctx: WebConfirmContext, answer: str, delay: float = 0.05, *, exclude_ids: set[str] | None = None
):
    """Simulate a user responding to ask_user after a short delay."""
    exclude = exclude_ids or set()
    await asyncio.sleep(delay)
    for ask_id, entry in ctx.pending_approvals.items():
        if ask_id not in exclude and "event" in entry and isinstance(entry["event"], asyncio.Event):
            entry["answer"] = answer
            entry["event"].set()
            return
    raise RuntimeError("No pending ask_user entry found")


# ---------------------------------------------------------------------------
# _with_keepalive tests
# ---------------------------------------------------------------------------


class TestWithKeepalive:
    async def test_passes_through_events(self) -> None:
        async def gen():
            yield "a"
            yield "b"

        results = [e async for e in _with_keepalive(gen(), interval=10)]
        assert results == ["a", "b"]

    async def test_yields_keepalive_on_silence(self) -> None:
        stall = asyncio.Event()

        async def gen():
            yield "first"
            await stall.wait()
            yield "second"

        g = gen()
        results = []
        async for event in _with_keepalive(g, interval=0.05):
            results.append(event)
            if isinstance(event, dict) and "comment" in event:
                stall.set()

        assert results[0] == "first"
        assert results[1] == {"comment": "keepalive"}
        assert results[-1] == "second"

    async def test_multiple_keepalives_during_long_stall(self) -> None:
        async def gen():
            yield "start"
            # Block for long enough to produce multiple keepalives at 0.02s interval
            await asyncio.sleep(0.15)
            yield "end"

        results = [e async for e in _with_keepalive(gen(), interval=0.02)]
        keepalives = [e for e in results if isinstance(e, dict) and "comment" in e]
        assert len(keepalives) >= 3
        assert results[0] == "start"
        assert results[-1] == "end"

    async def test_empty_generator(self) -> None:
        async def gen():
            return
            yield  # make it a generator  # noqa: RET504

        results = [e async for e in _with_keepalive(gen(), interval=0.05)]
        assert results == []

    async def test_fast_events_no_keepalive(self) -> None:
        async def gen():
            for i in range(5):
                yield i

        results = [e async for e in _with_keepalive(gen(), interval=10)]
        assert results == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# _web_ask_user_callback tests
# ---------------------------------------------------------------------------


class TestWebAskUserCallback:
    async def test_returns_answer_when_event_set(self) -> None:
        ctx = _make_ctx()
        task = asyncio.create_task(_answer_after(ctx, "PostgreSQL"))
        result = await _web_ask_user_callback(ctx, "Which DB?")
        assert result == "PostgreSQL"
        await task

    async def test_returns_empty_on_timeout(self) -> None:
        ctx = _make_ctx(approval_timeout=0)
        result = await _web_ask_user_callback(ctx, "Which DB?")
        assert result == ""

    async def test_entry_cleaned_on_timeout(self) -> None:
        ctx = _make_ctx(approval_timeout=0)
        await _web_ask_user_callback(ctx, "Which DB?")
        assert len(ctx.pending_approvals) == 0

    async def test_returns_empty_on_disconnect(self) -> None:
        ctx = _make_ctx(is_disconnected=True)
        result = await _web_ask_user_callback(ctx, "Which DB?")
        assert result == ""

    async def test_entry_cleaned_on_disconnect(self) -> None:
        ctx = _make_ctx(is_disconnected=True)
        await _web_ask_user_callback(ctx, "Which DB?")
        assert len(ctx.pending_approvals) == 0

    async def test_pending_limit_returns_empty(self) -> None:
        pending = {f"id-{i}": {} for i in range(100)}
        ctx = _make_ctx(pending=pending)
        result = await _web_ask_user_callback(ctx, "Which DB?")
        assert result == ""
        assert len(pending) == 100

    async def test_publishes_event_with_options(self) -> None:
        event_bus = AsyncMock()
        ctx = _make_ctx(event_bus=event_bus)
        task = asyncio.create_task(_answer_after(ctx, "Red"))
        await _web_ask_user_callback(ctx, "Pick a color", options=["Red", "Blue"])
        await task

        event_bus.publish.assert_called_once()
        call_args = event_bus.publish.call_args
        payload = call_args.args[1]
        assert payload["type"] == "ask_user_required"
        assert payload["data"]["question"] == "Pick a color"
        assert payload["data"]["options"] == ["Red", "Blue"]

    async def test_no_options_field_when_none(self) -> None:
        event_bus = AsyncMock()
        ctx = _make_ctx(event_bus=event_bus)
        task = asyncio.create_task(_answer_after(ctx, "answer"))
        await _web_ask_user_callback(ctx, "Question?")
        await task

        payload = event_bus.publish.call_args.args[1]
        assert "options" not in payload["data"]

    async def test_publishes_to_global_channel(self) -> None:
        event_bus = AsyncMock()
        ctx = _make_ctx(event_bus=event_bus)
        task = asyncio.create_task(_answer_after(ctx, "ok"))
        await _web_ask_user_callback(ctx, "Question?")
        await task

        channel = event_bus.publish.call_args.args[0]
        assert channel == "global:test"

    async def test_no_publish_without_event_bus(self) -> None:
        ctx = _make_ctx(event_bus=None)
        task = asyncio.create_task(_answer_after(ctx, "ok"))
        result = await _web_ask_user_callback(ctx, "Question?")
        assert result == "ok"
        await task

    async def test_concurrent_ask_user_and_approval(self) -> None:
        pending: dict[str, Any] = {}
        approval_event = asyncio.Event()
        pending["approval-1"] = {"event": approval_event, "approved": False, "scope": "once"}
        ctx = _make_ctx(pending=pending)
        task = asyncio.create_task(_answer_after(ctx, "answer", exclude_ids={"approval-1"}))
        result = await _web_ask_user_callback(ctx, "Question?")
        assert result == "answer"
        assert "approval-1" in pending
        await task

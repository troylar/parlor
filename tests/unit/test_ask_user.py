"""Tests for the ask_user tool handler (#299)."""

from __future__ import annotations

import pytest

from anteroom.tools.ask_user import handle


class TestAskUserHandler:
    @pytest.mark.asyncio
    async def test_returns_answer_from_callback(self) -> None:
        async def callback(question: str) -> str:
            return "yes, do it"

        result = await handle(question="Should I proceed?", _ask_callback=callback)
        assert result == {"answer": "yes, do it"}

    @pytest.mark.asyncio
    async def test_strips_question_whitespace(self) -> None:
        received: list[str] = []

        async def callback(question: str) -> str:
            received.append(question)
            return "ok"

        await handle(question="  What color?  ", _ask_callback=callback)
        assert received == ["What color?"]

    @pytest.mark.asyncio
    async def test_empty_question_returns_error(self) -> None:
        async def callback(question: str) -> str:
            return "should not be called"

        result = await handle(question="", _ask_callback=callback)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_whitespace_only_question_returns_error(self) -> None:
        async def callback(question: str) -> str:
            return "should not be called"

        result = await handle(question="   ", _ask_callback=callback)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_callback_fails_closed(self) -> None:
        result = await handle(question="What?", _ask_callback=None)
        assert "error" in result
        assert "best judgment" in result["error"].lower() or "no interactive" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_no_callback_default_fails_closed(self) -> None:
        """Handler with no _ask_callback kwarg at all fails closed."""
        result = await handle(question="What?")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_callback_eof_returns_empty_answer(self) -> None:
        async def callback(question: str) -> str:
            raise EOFError()

        result = await handle(question="What?", _ask_callback=callback)
        assert result["answer"] == ""
        assert "cancelled" in result.get("note", "").lower()

    @pytest.mark.asyncio
    async def test_callback_keyboard_interrupt_returns_empty(self) -> None:
        async def callback(question: str) -> str:
            raise KeyboardInterrupt()

        result = await handle(question="What?", _ask_callback=callback)
        assert result["answer"] == ""

    @pytest.mark.asyncio
    async def test_callback_exception_returns_error(self) -> None:
        async def callback(question: str) -> str:
            raise RuntimeError("broken")

        result = await handle(question="What?", _ask_callback=callback)
        assert "error" in result
        assert "RuntimeError" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_answer_is_valid(self) -> None:
        async def callback(question: str) -> str:
            return ""

        result = await handle(question="What?", _ask_callback=callback)
        assert result == {"answer": ""}

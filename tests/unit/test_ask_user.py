"""Tests for the ask_user tool handler (#299, #312)."""

from __future__ import annotations

import pytest

from anteroom.tools.ask_user import DEFINITION, handle


class TestAskUserHandler:
    @pytest.mark.asyncio
    async def test_returns_answer_from_callback(self) -> None:
        async def callback(question: str, options: list[str] | None = None) -> str:
            return "yes, do it"

        result = await handle(question="Should I proceed?", _ask_callback=callback)
        assert result == {"answer": "yes, do it"}

    @pytest.mark.asyncio
    async def test_strips_question_whitespace(self) -> None:
        received: list[str] = []

        async def callback(question: str, options: list[str] | None = None) -> str:
            received.append(question)
            return "ok"

        await handle(question="  What color?  ", _ask_callback=callback)
        assert received == ["What color?"]

    @pytest.mark.asyncio
    async def test_empty_question_returns_error(self) -> None:
        async def callback(question: str, options: list[str] | None = None) -> str:
            return "should not be called"

        result = await handle(question="", _ask_callback=callback)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_whitespace_only_question_returns_error(self) -> None:
        async def callback(question: str, options: list[str] | None = None) -> str:
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
        result = await handle(question="What?")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_callback_eof_returns_cancelled(self) -> None:
        async def callback(question: str, options: list[str] | None = None) -> str:
            raise EOFError()

        result = await handle(question="What?", _ask_callback=callback)
        assert result["answer"] == ""
        assert result["cancelled"] is True

    @pytest.mark.asyncio
    async def test_callback_keyboard_interrupt_returns_cancelled(self) -> None:
        async def callback(question: str, options: list[str] | None = None) -> str:
            raise KeyboardInterrupt()

        result = await handle(question="What?", _ask_callback=callback)
        assert result["answer"] == ""
        assert result["cancelled"] is True

    @pytest.mark.asyncio
    async def test_callback_exception_returns_error(self) -> None:
        async def callback(question: str, options: list[str] | None = None) -> str:
            raise RuntimeError("broken")

        result = await handle(question="What?", _ask_callback=callback)
        assert "error" in result
        assert "RuntimeError" in result["error"]

    @pytest.mark.asyncio
    async def test_cancel_sentinel_returns_cancelled(self) -> None:
        """Empty string from callback means user cancelled."""

        async def callback(question: str, options: list[str] | None = None) -> str:
            return ""

        result = await handle(question="What?", _ask_callback=callback)
        assert result["cancelled"] is True
        assert result["answer"] == ""


class TestAskUserOptions:
    @pytest.mark.asyncio
    async def test_options_passed_to_callback(self) -> None:
        received_opts: list[list[str] | None] = []

        async def callback(question: str, options: list[str] | None = None) -> str:
            received_opts.append(options)
            return "Option A"

        await handle(question="Pick one", options=["Option A", "Option B"], _ask_callback=callback)
        assert received_opts == [["Option A", "Option B"]]

    @pytest.mark.asyncio
    async def test_no_options_passes_none(self) -> None:
        received_opts: list[list[str] | None] = []

        async def callback(question: str, options: list[str] | None = None) -> str:
            received_opts.append(options)
            return "freeform"

        await handle(question="What?", _ask_callback=callback)
        assert received_opts == [None]

    @pytest.mark.asyncio
    async def test_empty_options_treated_as_none(self) -> None:
        received_opts: list[list[str] | None] = []

        async def callback(question: str, options: list[str] | None = None) -> str:
            received_opts.append(options)
            return "freeform"

        await handle(question="What?", options=[], _ask_callback=callback)
        assert received_opts == [None]

    @pytest.mark.asyncio
    async def test_blank_options_filtered_out(self) -> None:
        received_opts: list[list[str] | None] = []

        async def callback(question: str, options: list[str] | None = None) -> str:
            received_opts.append(options)
            return "A"

        await handle(question="Pick", options=["A", "", "  ", "B"], _ask_callback=callback)
        assert received_opts == [["A", "B"]]

    @pytest.mark.asyncio
    async def test_all_blank_options_treated_as_none(self) -> None:
        received_opts: list[list[str] | None] = []

        async def callback(question: str, options: list[str] | None = None) -> str:
            received_opts.append(options)
            return "freeform"

        await handle(question="What?", options=["", "  "], _ask_callback=callback)
        assert received_opts == [None]

    @pytest.mark.asyncio
    async def test_options_with_selection_returns_answer(self) -> None:
        async def callback(question: str, options: list[str] | None = None) -> str:
            return "Option B"

        result = await handle(question="Pick", options=["Option A", "Option B"], _ask_callback=callback)
        assert result == {"answer": "Option B"}


class TestAskUserSchema:
    def test_definition_has_options_parameter(self) -> None:
        props = DEFINITION["parameters"]["properties"]
        assert "options" in props
        assert props["options"]["type"] == "array"
        assert props["options"]["items"]["type"] == "string"

    def test_options_not_required(self) -> None:
        assert "options" not in DEFINITION["parameters"]["required"]

    def test_question_is_required(self) -> None:
        assert "question" in DEFINITION["parameters"]["required"]

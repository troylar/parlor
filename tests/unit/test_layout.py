"""Tests for the full-screen layout module."""

from __future__ import annotations

import pytest
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document

from anteroom.cli.layout import (
    AnteroomLayout,
    InputLexer,
    OutputControl,
    OutputPaneWriter,
    _DialogVisible,
    _strip_bg,
    create_anteroom_style,
    format_header,
    input_line_prefix,
)

# ---------------------------------------------------------------------------
# OutputControl
# ---------------------------------------------------------------------------


class TestOutputControl:
    def test_initial_state(self):
        ctrl = OutputControl()
        assert ctrl.fragment_count == 0

    def test_append_text(self):
        ctrl = OutputControl()
        ctrl.append_text("hello")
        assert ctrl.fragment_count == 1
        assert ctrl._output_fragments[0] == ("", "hello")

    def test_append_text_with_style(self):
        ctrl = OutputControl()
        ctrl.append_text("styled", "class:test")
        assert ctrl._output_fragments[0] == ("class:test", "styled")

    def test_append_fragments(self):
        ctrl = OutputControl()
        ctrl.append([("class:a", "one"), ("class:b", "two")])
        assert ctrl.fragment_count == 2

    def test_append_newline(self):
        ctrl = OutputControl()
        ctrl.append_newline()
        assert ctrl._output_fragments[0] == ("", "\n")

    def test_clear(self):
        ctrl = OutputControl()
        ctrl.append_text("hello")
        ctrl.append_text("world")
        assert ctrl.fragment_count == 2
        ctrl.clear()
        assert ctrl.fragment_count == 0

    def test_create_content_single_line(self):
        ctrl = OutputControl()
        ctrl.append_text("short")
        content = ctrl.create_content(80, 10)
        assert content.line_count >= 1

    def test_create_content_cursor_at_end(self):
        ctrl = OutputControl()
        for i in range(20):
            ctrl.append_text(f"line {i}\n")
        content = ctrl.create_content(80, 10)
        assert content.cursor_position.y == content.line_count - 1

    def test_create_content_empty(self):
        ctrl = OutputControl()
        content = ctrl.create_content(80, 10)
        assert content.line_count <= 1

    def test_get_fragments_returns_stored(self):
        ctrl = OutputControl()
        ctrl.append_text("test")
        frags = ctrl._get_output_fragments()
        assert len(frags) == 1
        assert frags[0] == ("", "test")

    def test_scroll_up_moves_cursor(self):
        ctrl = OutputControl()
        for i in range(50):
            ctrl.append_text(f"line {i}\n")
        ctrl.scroll_up(10)
        content = ctrl.create_content(80, 20)
        assert content.cursor_position.y == content.line_count - 1 - 10

    def test_scroll_down_moves_cursor_back(self):
        ctrl = OutputControl()
        for i in range(50):
            ctrl.append_text(f"line {i}\n")
        ctrl.scroll_up(20)
        ctrl.scroll_down(5)
        content = ctrl.create_content(80, 20)
        assert content.cursor_position.y == content.line_count - 1 - 15

    def test_scroll_down_clamps_to_zero(self):
        ctrl = OutputControl()
        for i in range(50):
            ctrl.append_text(f"line {i}\n")
        ctrl.scroll_up(5)
        ctrl.scroll_down(999)
        content = ctrl.create_content(80, 20)
        assert content.cursor_position.y == content.line_count - 1

    def test_scroll_to_bottom(self):
        ctrl = OutputControl()
        for i in range(50):
            ctrl.append_text(f"line {i}\n")
        ctrl.scroll_up(30)
        ctrl.scroll_to_bottom()
        content = ctrl.create_content(80, 20)
        assert content.cursor_position.y == content.line_count - 1

    def test_scroll_to_top(self):
        ctrl = OutputControl()
        for i in range(50):
            ctrl.append_text(f"line {i}\n")
        ctrl.scroll_to_top()
        content = ctrl.create_content(80, 20)
        # Cursor should be at line 0 (clamped to valid range)
        assert content.cursor_position.y == 0

    def test_append_resets_scroll(self):
        ctrl = OutputControl()
        for i in range(50):
            ctrl.append_text(f"line {i}\n")
        ctrl.scroll_up(20)
        ctrl.append_text("new content\n")
        content = ctrl.create_content(80, 20)
        assert content.cursor_position.y == content.line_count - 1

    def test_scroll_up_clamps_to_max(self):
        ctrl = OutputControl()
        for i in range(10):
            ctrl.append_text(f"line {i}\n")
        ctrl.scroll_up(999999)
        content = ctrl.create_content(80, 20)
        assert content.cursor_position.y == 0

    def test_mouse_scroll_up(self):
        from prompt_toolkit.mouse_events import (
            MouseButton,
            MouseEvent,
            MouseEventType,
        )

        ctrl = OutputControl()
        for i in range(50):
            ctrl.append_text(f"line {i}\n")
        event = MouseEvent(
            position=(0, 0),
            event_type=MouseEventType.SCROLL_UP,
            button=MouseButton.NONE,
            modifiers=frozenset(),
        )
        ctrl.mouse_handler(event)
        assert ctrl._scroll_offset == 3

    def test_mouse_scroll_down(self):
        from prompt_toolkit.mouse_events import (
            MouseButton,
            MouseEvent,
            MouseEventType,
        )

        ctrl = OutputControl()
        for i in range(50):
            ctrl.append_text(f"line {i}\n")
        ctrl.scroll_up(10)
        event = MouseEvent(
            position=(0, 0),
            event_type=MouseEventType.SCROLL_DOWN,
            button=MouseButton.NONE,
            modifiers=frozenset(),
        )
        ctrl.mouse_handler(event)
        assert ctrl._scroll_offset == 7


# ---------------------------------------------------------------------------
# format_header
# ---------------------------------------------------------------------------


class TestFormatHeader:
    def test_empty(self):
        h = format_header()
        assert len(h) == 2  # leading + trailing space
        assert h[0] == ("class:header", " ")
        assert h[1] == ("class:header", " ")

    def test_model_only(self):
        h = format_header(model="gpt-4o")
        assert any(f[1] == "gpt-4o" for f in h)
        model_frag = next(f for f in h if f[1] == "gpt-4o")
        assert model_frag[0] == "class:header.model"

    def test_working_dir_shortens_home(self):
        import os

        home = os.path.expanduser("~")
        h = format_header(working_dir=f"{home}/projects/test")
        dir_frag = next(f for f in h if "~/projects/test" in f[1])
        assert dir_frag[0] == "class:header.dir"

    def test_git_branch(self):
        h = format_header(model="m", git_branch="issue-257-fullscreen")
        assert any(f[1] == "issue-257-fullscreen" for f in h)

    def test_project_name(self):
        h = format_header(project_name="my-project")
        assert any(f[1] == "my-project" for f in h)

    def test_space_name(self):
        h = format_header(space_name="my-space")
        assert any(f[1] == "my-space" for f in h)

    def test_conv_title(self):
        h = format_header(conv_title="Fix login bug")
        assert any(f[1] == "Fix login bug" for f in h)
        title_frag = next(f for f in h if f[1] == "Fix login bug")
        assert title_frag[0] == "class:header.title"

    def test_plan_mode(self):
        h = format_header(plan_mode=True)
        plan_frag = next(f for f in h if f[1] == "PLAN")
        assert plan_frag[0] == "class:header.plan"

    def test_all_fields(self):
        h = format_header(
            model="gpt-4",
            working_dir="/tmp/test",
            git_branch="main",
            project_name="proj",
            space_name="space",
            conv_title="My Chat",
            plan_mode=True,
        )
        texts = [f[1] for f in h]
        assert "gpt-4" in texts
        assert "/tmp/test" in texts
        assert "main" in texts
        assert "proj" in texts
        assert "space" in texts
        assert "My Chat" in texts
        assert "PLAN" in texts

    def test_separators_between_fields(self):
        h = format_header(model="m", git_branch="b")
        sep_frags = [f for f in h if f[0] == "class:header.sep"]
        assert len(sep_frags) >= 1
        assert "\u00b7" in sep_frags[0][1]


# ---------------------------------------------------------------------------
# input_line_prefix
# ---------------------------------------------------------------------------


class TestInputLinePrefix:
    def test_first_line(self):
        result = input_line_prefix(0, 0)
        assert result == [("class:prompt", "> ")]

    def test_continuation_line(self):
        result = input_line_prefix(1, 0)
        assert result == [("class:prompt.continuation", ". ")]

    def test_later_lines(self):
        result = input_line_prefix(5, 0)
        assert result == [("class:prompt.continuation", ". ")]


# ---------------------------------------------------------------------------
# AnteroomLayout
# ---------------------------------------------------------------------------


class TestAnteroomLayout:
    def _make_layout(self):
        buf = Buffer(name="test-input")
        return AnteroomLayout(
            header_fn=lambda: [("class:header", " test ")],
            footer_fn=lambda: [("class:footer", " status ")],
            input_buffer=buf,
        )

    def test_layout_created(self):
        al = self._make_layout()
        assert al.layout is not None

    def test_output_control(self):
        al = self._make_layout()
        assert isinstance(al.output, OutputControl)

    def test_input_buffer(self):
        buf = Buffer(name="my-buf")
        al = AnteroomLayout(
            header_fn=lambda: [],
            footer_fn=lambda: [],
            input_buffer=buf,
        )
        assert al.input_buffer is buf

    def test_append_output(self):
        al = self._make_layout()
        al.append_output("hello")
        assert al.output.fragment_count == 1

    def test_append_output_fragments(self):
        al = self._make_layout()
        al.append_output_fragments([("class:a", "x"), ("class:b", "y")])
        assert al.output.fragment_count == 2

    def test_append_ansi(self):
        al = self._make_layout()
        al.append_ansi("\033[31mred\033[0m")
        assert al.output.fragment_count > 0

    def test_clear_output(self):
        al = self._make_layout()
        al.append_output("data")
        al.clear_output()
        assert al.output.fragment_count == 0

    def test_focus_input(self):
        al = self._make_layout()
        al.focus_input()

    def test_set_status(self):
        al = self._make_layout()
        al.set_status([("class:status", "Thinking...")])
        assert al._status_fragments == [("class:status", "Thinking...")]

    def test_clear_status(self):
        al = self._make_layout()
        al.set_status([("class:status", "busy")])
        al.clear_status()
        assert al._status_fragments == []

    def test_status_hidden_when_empty(self):
        al = self._make_layout()
        from anteroom.cli.layout import _HasContent

        filt = _HasContent(al)
        assert not filt()
        al.set_status([("class:status", "x")])
        assert filt()
        al.clear_status()
        assert not filt()


# ---------------------------------------------------------------------------
# OutputPaneWriter
# ---------------------------------------------------------------------------


class TestOutputPaneWriter:
    def test_write_and_flush(self):
        ctrl = OutputControl()
        calls = []
        writer = OutputPaneWriter(ctrl, lambda: calls.append(1))
        writer.write("\033[31mhello\033[0m")
        assert ctrl.fragment_count == 0  # not flushed yet
        writer.flush()
        assert ctrl.fragment_count > 0
        assert len(calls) == 1  # invalidate was called

    def test_flush_empty(self):
        ctrl = OutputControl()
        calls = []
        writer = OutputPaneWriter(ctrl, lambda: calls.append(1))
        writer.flush()
        assert ctrl.fragment_count == 0
        assert len(calls) == 0

    def test_multiple_writes_before_flush(self):
        ctrl = OutputControl()
        writer = OutputPaneWriter(ctrl, lambda: None)
        writer.write("hello ")
        writer.write("world")
        writer.flush()
        texts = "".join(f[1] for f in ctrl._output_fragments)
        assert "hello " in texts
        assert "world" in texts

    def test_encoding(self):
        ctrl = OutputControl()
        writer = OutputPaneWriter(ctrl, lambda: None)
        assert writer.encoding == "utf-8"

    def test_isatty(self):
        ctrl = OutputControl()
        writer = OutputPaneWriter(ctrl, lambda: None)
        assert writer.isatty() is True

    def test_rich_console_integration(self):
        from rich.console import Console

        ctrl = OutputControl()
        calls = []
        writer = OutputPaneWriter(ctrl, lambda: calls.append(1))
        c = Console(file=writer, force_terminal=True, width=80)
        c.print("[bold]hello[/bold] world")
        assert ctrl.fragment_count > 0
        assert len(calls) >= 1


# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------


class TestStyle:
    def test_create_style(self):
        style = create_anteroom_style()
        assert style is not None

    def test_style_has_header(self):
        style = create_anteroom_style()
        class_names = [rule[0] for rule in style.style_rules]
        assert "header" in class_names

    def test_style_has_separator(self):
        style = create_anteroom_style()
        class_names = [rule[0] for rule in style.style_rules]
        assert "separator" in class_names

    def test_style_has_prompt(self):
        style = create_anteroom_style()
        class_names = [rule[0] for rule in style.style_rules]
        assert "prompt" in class_names

    def test_style_has_toolbar_styles(self):
        style = create_anteroom_style()
        class_names = [rule[0] for rule in style.style_rules]
        assert "bottom-toolbar" in class_names
        assert "bottom-toolbar.model" in class_names


# ---------------------------------------------------------------------------
# Application integration
# ---------------------------------------------------------------------------


class TestApplicationIntegration:
    """Tests for creating a prompt_toolkit Application with AnteroomLayout."""

    def test_application_creation(self):
        from prompt_toolkit.application import Application

        buf = Buffer(name="test-input")
        al = AnteroomLayout(
            header_fn=lambda: [("class:header", " test ")],
            footer_fn=lambda: [("class:footer", " status ")],
            input_buffer=buf,
        )
        app: Application[None] = Application(
            layout=al.layout,
            style=create_anteroom_style(),
            full_screen=True,
        )
        assert app.layout is al.layout

    def test_output_pane_writer_fileno_raises(self):
        ctrl = OutputControl()
        writer = OutputPaneWriter(ctrl, lambda: None)
        import pytest

        with pytest.raises(AttributeError):
            writer.fileno()

    def test_multiple_ansi_appends(self):
        al = AnteroomLayout(
            header_fn=lambda: [],
            footer_fn=lambda: [],
            input_buffer=Buffer(name="test"),
        )
        al.append_ansi("\033[31mred\033[0m")
        al.append_ansi("\033[32mgreen\033[0m")
        assert al.output.fragment_count > 0

    def test_format_header_no_home_dir(self):
        """Working dir that doesn't start with ~ should be unchanged."""
        h = format_header(working_dir="/tmp/test")
        dir_frag = next(f for f in h if f[1] == "/tmp/test")
        assert dir_frag[0] == "class:header.dir"

    def test_output_control_many_lines_auto_scroll(self):
        ctrl = OutputControl()
        for i in range(100):
            ctrl.append_text(f"line {i}\n")
        content = ctrl.create_content(80, 20)
        assert content.cursor_position.y == content.line_count - 1


# ---------------------------------------------------------------------------
# _strip_bg
# ---------------------------------------------------------------------------


class TestStripBg:
    def test_removes_bg_hex(self):
        frags = [("bold bg:#1e1e2e", "hello")]
        result = _strip_bg(frags)
        assert result == [("bold", "hello")]

    def test_removes_bg_named(self):
        frags = [("bg:ansired bold", "text")]
        result = _strip_bg(frags)
        assert result == [("bold", "text")]

    def test_preserves_fg_only(self):
        frags = [("#ff0000 bold", "text")]
        result = _strip_bg(frags)
        assert result == [("#ff0000 bold", "text")]

    def test_empty_style(self):
        frags = [("", "text")]
        result = _strip_bg(frags)
        assert result == [("", "text")]

    def test_bg_only_style(self):
        frags = [("bg:#000000", "text")]
        result = _strip_bg(frags)
        assert result == [("", "text")]

    def test_multiple_fragments(self):
        frags = [
            ("bold bg:#1e1e2e", "a"),
            ("#ff0000", "b"),
            ("bg:ansiblue italic", "c"),
        ]
        result = _strip_bg(frags)
        assert result == [("bold", "a"), ("#ff0000", "b"), ("italic", "c")]

    def test_writer_flush_strips_bg(self):
        ctrl = OutputControl()
        calls = []
        writer = OutputPaneWriter(ctrl, lambda: calls.append(1))
        writer.write("\033[48;2;30;30;46mhello\033[0m")
        writer.flush()
        assert ctrl.fragment_count > 0
        for style, _text in ctrl._output_fragments:
            assert "bg:" not in style

    def test_append_ansi_strips_bg(self):
        al = AnteroomLayout(
            header_fn=lambda: [],
            footer_fn=lambda: [],
            input_buffer=Buffer(name="test"),
        )
        al.append_ansi("\033[48;2;30;30;46mcolored bg\033[0m")
        assert al.output.fragment_count > 0
        for style, _text in al.output._output_fragments:
            assert "bg:" not in style


# ---------------------------------------------------------------------------
# InputLexer tests
# ---------------------------------------------------------------------------


class TestInputLexer:
    def test_slash_command_highlighted(self):
        doc = Document("/help")
        lexer = InputLexer()
        get_line = lexer.lex_document(doc)
        fragments = get_line(0)
        assert fragments[0] == ("class:input.command", "/help")

    def test_slash_command_with_args(self):
        doc = Document("/resume my-conv")
        lexer = InputLexer()
        get_line = lexer.lex_document(doc)
        fragments = get_line(0)
        assert fragments[0] == ("class:input.command", "/resume")
        assert fragments[1] == ("", " my-conv")

    def test_plain_text_no_highlight(self):
        doc = Document("hello world")
        lexer = InputLexer()
        get_line = lexer.lex_document(doc)
        fragments = get_line(0)
        assert fragments == [("", "hello world")]

    def test_slash_only_on_first_line(self):
        doc = Document("/help\n/tools")
        lexer = InputLexer()
        get_line = lexer.lex_document(doc)
        # First line highlighted
        assert get_line(0)[0][0] == "class:input.command"
        # Second line not highlighted
        assert get_line(1) == [("", "/tools")]

    def test_empty_input(self):
        doc = Document("")
        lexer = InputLexer()
        get_line = lexer.lex_document(doc)
        assert get_line(0) == [("", "")]


# ---------------------------------------------------------------------------
# Dialog overlay
# ---------------------------------------------------------------------------


class TestDialogOverlay:
    def _make_layout(self):
        buf = Buffer(name="test-input")
        return AnteroomLayout(
            header_fn=lambda: [("class:header", " test ")],
            footer_fn=lambda: [("class:footer", " status ")],
            input_buffer=buf,
        )

    def test_dialog_initially_hidden(self):
        al = self._make_layout()
        assert al._dialog_visible is False
        filt = _DialogVisible(al)
        assert not filt()

    def test_dialog_visible_filter_tracks_state(self):
        al = self._make_layout()
        filt = _DialogVisible(al)
        assert not filt()
        al._dialog_visible = True
        assert filt()
        al._dialog_visible = False
        assert not filt()

    @pytest.mark.asyncio
    async def test_show_dialog_sets_visible(self):
        import asyncio

        al = self._make_layout()

        async def _simulate_accept():
            await asyncio.sleep(0.01)
            assert al._dialog_visible is True
            assert al._dialog_title == "Test Title"
            al._dialog_buffer.text = "y"
            al._on_dialog_accept(al._dialog_buffer)

        task = asyncio.create_task(_simulate_accept())
        result = await al.show_dialog(
            title="Test Title",
            body_fragments=[("class:dialog.body", "body text")],
        )
        await task
        assert result == "y"
        assert al._dialog_visible is False

    @pytest.mark.asyncio
    async def test_show_dialog_cancel_returns_none(self):
        import asyncio

        al = self._make_layout()

        async def _simulate_cancel():
            await asyncio.sleep(0.01)
            al.cancel_dialog()

        task = asyncio.create_task(_simulate_cancel())
        result = await al.show_dialog(
            title="Cancel Test",
            body_fragments=[("class:dialog.body", "cancel me")],
        )
        await task
        assert result is None
        assert al._dialog_visible is False

    def test_hide_dialog_resets_state(self):
        al = self._make_layout()
        al._dialog_visible = True
        al._dialog_buffer.text = "leftover"
        al.hide_dialog()
        assert al._dialog_visible is False
        assert al._dialog_buffer.text == ""
        assert al._dialog_event is None

    def test_cancel_dialog_signals_event(self):
        import asyncio

        al = self._make_layout()
        al._dialog_event = asyncio.Event()
        al._dialog_result = "something"
        al.cancel_dialog()
        assert al._dialog_result is None
        assert al._dialog_event.is_set()

    def test_on_dialog_accept_sets_result(self):
        import asyncio

        al = self._make_layout()
        al._dialog_event = asyncio.Event()
        al._dialog_buffer.text = "test input"
        al._on_dialog_accept(al._dialog_buffer)
        assert al._dialog_result == "test input"
        assert al._dialog_event.is_set()

    def test_dialog_body_fragments_returned(self):
        al = self._make_layout()
        body = [("class:dialog.body", "hello")]
        al._dialog_body_fragments = body
        assert al._get_dialog_body() == body

    def test_dialog_title_returned(self):
        al = self._make_layout()
        al._dialog_title = "My Title"
        frags = al._get_dialog_title()
        assert len(frags) == 1
        assert "My Title" in frags[0][1]

    def test_dialog_styles_present(self):
        style = create_anteroom_style()
        class_names = [rule[0] for rule in style.style_rules]
        assert "dialog.frame" in class_names
        assert "dialog.title" in class_names
        assert "dialog.body" in class_names
        assert "dialog.input" in class_names
        assert "dialog.hint" in class_names
        assert "dialog.option" in class_names
        assert "dialog.option.key" in class_names
        assert "dialog.shadow" in class_names


# ---------------------------------------------------------------------------
# Dialog overlay — approval flow simulation
# ---------------------------------------------------------------------------


class TestDialogApprovalFlow:
    """Tests simulating the fullscreen approval dialog flow."""

    def _make_layout(self):
        buf = Buffer(name="test-input")
        return AnteroomLayout(
            header_fn=lambda: [("class:header", " test ")],
            footer_fn=lambda: [("class:footer", " status ")],
            input_buffer=buf,
        )

    @pytest.mark.asyncio
    async def test_approval_allow_once(self):
        import asyncio

        al = self._make_layout()

        async def _type_y():
            await asyncio.sleep(0.01)
            al._dialog_buffer.text = "y"
            al._on_dialog_accept(al._dialog_buffer)

        task = asyncio.create_task(_type_y())
        result = await al.show_dialog(
            title="Approval Required",
            body_fragments=[("class:dialog.body", "Destructive command detected")],
        )
        await task
        assert result == "y"

    @pytest.mark.asyncio
    async def test_approval_allow_session(self):
        import asyncio

        al = self._make_layout()

        async def _type_s():
            await asyncio.sleep(0.01)
            al._dialog_buffer.text = "s"
            al._on_dialog_accept(al._dialog_buffer)

        task = asyncio.create_task(_type_s())
        result = await al.show_dialog(
            title="Approval Required",
            body_fragments=[("class:dialog.body", "test")],
        )
        await task
        assert result == "s"

    @pytest.mark.asyncio
    async def test_approval_allow_always(self):
        import asyncio

        al = self._make_layout()

        async def _type_a():
            await asyncio.sleep(0.01)
            al._dialog_buffer.text = "a"
            al._on_dialog_accept(al._dialog_buffer)

        task = asyncio.create_task(_type_a())
        result = await al.show_dialog(
            title="Approval Required",
            body_fragments=[("class:dialog.body", "test")],
        )
        await task
        assert result == "a"

    @pytest.mark.asyncio
    async def test_approval_deny(self):
        import asyncio

        al = self._make_layout()

        async def _type_n():
            await asyncio.sleep(0.01)
            al._dialog_buffer.text = "n"
            al._on_dialog_accept(al._dialog_buffer)

        task = asyncio.create_task(_type_n())
        result = await al.show_dialog(
            title="Approval Required",
            body_fragments=[("class:dialog.body", "test")],
        )
        await task
        assert result == "n"

    @pytest.mark.asyncio
    async def test_approval_escape_returns_none(self):
        import asyncio

        al = self._make_layout()

        async def _press_escape():
            await asyncio.sleep(0.01)
            al.cancel_dialog()

        task = asyncio.create_task(_press_escape())
        result = await al.show_dialog(
            title="Approval Required",
            body_fragments=[("class:dialog.body", "test")],
        )
        await task
        assert result is None

    @pytest.mark.asyncio
    async def test_approval_body_includes_command(self):
        import asyncio

        al = self._make_layout()
        body = [
            ("class:dialog.body", "  Destructive command detected\n"),
            ("class:dialog.hint", "  Command: rm -rf build\n\n"),
            ("class:dialog.option.key", "  [y]"),
            ("class:dialog.option", " Allow once   "),
        ]

        async def _accept():
            await asyncio.sleep(0.01)
            assert al._dialog_body_fragments == body
            al._dialog_buffer.text = "y"
            al._on_dialog_accept(al._dialog_buffer)

        task = asyncio.create_task(_accept())
        await al.show_dialog(title="Approval Required", body_fragments=body)
        await task

    @pytest.mark.asyncio
    async def test_dialog_focus_moves_to_dialog_input(self):
        import asyncio

        al = self._make_layout()

        async def _check_focus_and_accept():
            await asyncio.sleep(0.01)
            assert al._dialog_visible is True
            al._dialog_buffer.text = "y"
            al._on_dialog_accept(al._dialog_buffer)

        task = asyncio.create_task(_check_focus_and_accept())
        await al.show_dialog(
            title="Focus Test",
            body_fragments=[("class:dialog.body", "test")],
        )
        await task
        assert al._dialog_visible is False

    @pytest.mark.asyncio
    async def test_dialog_hides_after_accept(self):
        import asyncio

        al = self._make_layout()

        async def _accept():
            await asyncio.sleep(0.01)
            al._dialog_buffer.text = "done"
            al._on_dialog_accept(al._dialog_buffer)

        task = asyncio.create_task(_accept())
        result = await al.show_dialog(
            title="Test",
            body_fragments=[("class:dialog.body", "test")],
        )
        await task
        assert result == "done"
        assert al._dialog_visible is False
        assert al._dialog_event is None
        assert al._dialog_buffer.text == ""


# ---------------------------------------------------------------------------
# Dialog overlay — ask_user flow simulation
# ---------------------------------------------------------------------------


class TestDialogAskUserFlow:
    """Tests simulating the fullscreen ask_user dialog flow."""

    def _make_layout(self):
        buf = Buffer(name="test-input")
        return AnteroomLayout(
            header_fn=lambda: [("class:header", " test ")],
            footer_fn=lambda: [("class:footer", " status ")],
            input_buffer=buf,
        )

    @pytest.mark.asyncio
    async def test_ask_user_freeform_answer(self):
        import asyncio

        al = self._make_layout()

        async def _type_answer():
            await asyncio.sleep(0.01)
            al._dialog_buffer.text = "my custom answer"
            al._on_dialog_accept(al._dialog_buffer)

        task = asyncio.create_task(_type_answer())
        result = await al.show_dialog(
            title="Question",
            body_fragments=[("class:dialog.body", "  What should I do?\n\n")],
        )
        await task
        assert result == "my custom answer"

    @pytest.mark.asyncio
    async def test_ask_user_numeric_selection(self):
        import asyncio

        al = self._make_layout()
        body = [
            ("class:dialog.body", "  Which option?\n\n"),
            ("class:dialog.option.key", "  1. "),
            ("class:dialog.option", "Option A\n"),
            ("class:dialog.option.key", "  2. "),
            ("class:dialog.option", "Option B\n"),
        ]

        async def _type_number():
            await asyncio.sleep(0.01)
            al._dialog_buffer.text = "2"
            al._on_dialog_accept(al._dialog_buffer)

        task = asyncio.create_task(_type_number())
        result = await al.show_dialog(title="Question", body_fragments=body)
        await task
        assert result == "2"

    @pytest.mark.asyncio
    async def test_ask_user_escape_returns_none(self):
        import asyncio

        al = self._make_layout()

        async def _press_escape():
            await asyncio.sleep(0.01)
            al.cancel_dialog()

        task = asyncio.create_task(_press_escape())
        result = await al.show_dialog(
            title="Question",
            body_fragments=[("class:dialog.body", "  A question?\n\n")],
        )
        await task
        assert result is None

    @pytest.mark.asyncio
    async def test_ask_user_empty_answer(self):
        import asyncio

        al = self._make_layout()

        async def _type_empty():
            await asyncio.sleep(0.01)
            al._dialog_buffer.text = ""
            al._on_dialog_accept(al._dialog_buffer)

        task = asyncio.create_task(_type_empty())
        result = await al.show_dialog(
            title="Question",
            body_fragments=[("class:dialog.body", "  question\n\n")],
        )
        await task
        assert result == ""

    @pytest.mark.asyncio
    async def test_ask_user_with_options_body_format(self):
        import asyncio

        al = self._make_layout()
        body = [
            ("class:dialog.body", "  Pick one\n\n"),
            ("class:dialog.option.key", "  1. "),
            ("class:dialog.option", "Alpha\n"),
            ("class:dialog.option.key", "  2. "),
            ("class:dialog.option", "Beta\n"),
            ("class:dialog.option.key", "  3. "),
            ("class:dialog.option", "Gamma\n"),
        ]

        async def _accept():
            await asyncio.sleep(0.01)
            assert al._dialog_body_fragments == body
            al._dialog_buffer.text = "1"
            al._on_dialog_accept(al._dialog_buffer)

        task = asyncio.create_task(_accept())
        result = await al.show_dialog(title="Question", body_fragments=body)
        await task
        assert result == "1"

    def test_cancel_dialog_when_no_event_is_noop(self):
        al = self._make_layout()
        al._dialog_event = None
        al.cancel_dialog()
        assert al._dialog_result is None

    @pytest.mark.asyncio
    async def test_multiple_dialogs_sequential(self):
        import asyncio

        al = self._make_layout()

        async def _accept_with(text, delay=0.01):
            await asyncio.sleep(delay)
            al._dialog_buffer.text = text
            al._on_dialog_accept(al._dialog_buffer)

        # First dialog
        task1 = asyncio.create_task(_accept_with("first"))
        r1 = await al.show_dialog(
            title="Dialog 1",
            body_fragments=[("class:dialog.body", "one")],
        )
        await task1
        assert r1 == "first"
        assert al._dialog_visible is False

        # Second dialog
        task2 = asyncio.create_task(_accept_with("second"))
        r2 = await al.show_dialog(
            title="Dialog 2",
            body_fragments=[("class:dialog.body", "two")],
        )
        await task2
        assert r2 == "second"
        assert al._dialog_visible is False

    @pytest.mark.asyncio
    async def test_fullscreen_layout_none_guard(self):
        """When renderer._fullscreen_layout is None, callbacks should fall through."""
        # The guard: `renderer.is_fullscreen() and renderer._fullscreen_layout is not None`
        # We can't test the repl callbacks directly, but verify hide_dialog is safe
        # when called on a layout that was never shown.
        al = self._make_layout()
        assert al._dialog_visible is False
        al.hide_dialog()
        assert al._dialog_visible is False

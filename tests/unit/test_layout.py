"""Tests for the full-screen layout module."""

from __future__ import annotations

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document

from anteroom.cli.layout import (
    AnteroomLayout,
    InputLexer,
    OutputControl,
    OutputPaneWriter,
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

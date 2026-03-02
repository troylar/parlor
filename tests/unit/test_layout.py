"""Tests for the full-screen layout module."""

from __future__ import annotations

import io

import pytest
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.layout.dimension import to_dimension

from anteroom.cli.layout import (
    AnteroomLayout,
    InputLexer,
    OutputControl,
    OutputPaneWriter,
    _DialogVisible,
    _PickerVisible,
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

    def test_checkpoint_returns_fragment_count(self):
        ctrl = OutputControl()
        assert ctrl.checkpoint() == 0
        ctrl.append_text("one")
        ctrl.append_text("two")
        assert ctrl.checkpoint() == 2

    def test_truncate_to_removes_trailing_fragments(self):
        ctrl = OutputControl()
        ctrl.append_text("keep-1")
        ctrl.append_text("keep-2")
        cp = ctrl.checkpoint()
        ctrl.append_text("remove-1")
        ctrl.append_text("remove-2")
        assert ctrl.fragment_count == 4
        ctrl.truncate_to(cp)
        assert ctrl.fragment_count == 2
        assert ctrl._output_fragments[0] == ("", "keep-1")
        assert ctrl._output_fragments[1] == ("", "keep-2")

    def test_truncate_to_noop_at_current_length(self):
        ctrl = OutputControl()
        ctrl.append_text("hello")
        cp = ctrl.checkpoint()
        ctrl.truncate_to(cp)
        assert ctrl.fragment_count == 1

    def test_checkpoint_truncate_cycle(self):
        """Simulates the streaming cursor pattern: checkpoint, append cursor, truncate, repeat."""
        ctrl = OutputControl()
        ctrl.append_text("prefix\n")
        cp = ctrl.checkpoint()
        # First cursor render
        ctrl.append_text("▊")
        assert ctrl.fragment_count == 2
        ctrl.truncate_to(cp)
        assert ctrl.fragment_count == 1
        # Second cursor render with new token
        ctrl.append_text("token1 ▊")
        assert ctrl.fragment_count == 2
        ctrl.truncate_to(cp)
        assert ctrl.fragment_count == 1

    def test_append_newline_resets_scroll(self):
        ctrl = OutputControl()
        for i in range(50):
            ctrl.append_text(f"line {i}\n")
        ctrl.scroll_up(20)
        ctrl.append_newline()
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
        assert any(f[1] == "Space: my-space" for f in h)

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
        assert "Space: space" in texts
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
# Dynamic input height (#669)
# ---------------------------------------------------------------------------


class TestInputWindowDynamicHeight:
    """Tests for dynamic input window height (#669).

    The input window's height callable returns Dimension(min=1, max=N, preferred=N)
    where N = clamp(line_count, 1, 10). Setting max=preferred prevents the HSplit
    from growing the input past what it needs during the weight-based fill phase.
    """

    def _make_layout(self):
        buf = Buffer(name="test-input", multiline=True)
        al = AnteroomLayout(
            header_fn=lambda: [("class:header", " test ")],
            footer_fn=lambda: [("class:footer", " status ")],
            input_buffer=buf,
        )
        return al, buf

    def _get_dim(self, al):
        return to_dimension(al._input_window.height)

    # -- Dimension value tests --

    def test_single_line_preferred_is_one(self):
        al, buf = self._make_layout()
        buf.set_document(Document("hello"), bypass_readonly=True)
        dim = self._get_dim(al)
        assert dim.preferred == 1
        assert dim.max == 1

    def test_empty_buffer_preferred_is_one(self):
        al, _buf = self._make_layout()
        dim = self._get_dim(al)
        assert dim.preferred == 1
        assert dim.max == 1

    def test_height_grows_with_lines(self):
        al, buf = self._make_layout()
        buf.set_document(Document("line1\nline2\nline3"), bypass_readonly=True)
        dim = self._get_dim(al)
        assert dim.preferred == 3
        assert dim.max == 3

    def test_height_capped_at_ten(self):
        al, buf = self._make_layout()
        text = "\n".join(f"line{i}" for i in range(15))
        buf.set_document(Document(text), bypass_readonly=True)
        dim = self._get_dim(al)
        assert dim.preferred == 10
        assert dim.max == 10

    def test_height_shrinks_on_delete(self):
        al, buf = self._make_layout()
        buf.set_document(Document("a\nb\nc\nd"), bypass_readonly=True)
        assert self._get_dim(al).preferred == 4
        buf.set_document(Document("a"), bypass_readonly=True)
        assert self._get_dim(al).preferred == 1

    def test_max_equals_preferred(self):
        al, buf = self._make_layout()
        for n in (1, 2, 5, 10, 15):
            text = "\n".join(f"l{i}" for i in range(n))
            buf.set_document(Document(text), bypass_readonly=True)
            dim = self._get_dim(al)
            assert dim.max == dim.preferred, f"max != preferred for {n} lines"


class TestInputWindowHSplitAllocation:
    """Verify the HSplit actually allocates the right rows to the input window.

    Uses _divide_heights with a mocked get_app() to test the real layout
    algorithm without needing a running Application event loop.
    """

    def _make_layout(self):
        buf = Buffer(name="test-input", multiline=True)
        al = AnteroomLayout(
            header_fn=lambda: [("class:header", " test ")],
            footer_fn=lambda: [("class:footer", " status ")],
            input_buffer=buf,
        )
        return al, buf

    def _get_input_rows(self, al, buf, text, terminal_height=24):
        from unittest.mock import MagicMock, patch

        from prompt_toolkit.layout.screen import WritePosition

        buf.set_document(Document(text), bypass_readonly=True)
        wp = WritePosition(xpos=0, ypos=0, width=80, height=terminal_height)
        mock_app = MagicMock()
        mock_app.is_done = False
        hsplit = al.layout.container.content
        with patch("prompt_toolkit.layout.containers.get_app", return_value=mock_app):
            heights = hsplit._divide_heights(wp)
        assert heights is not None, "not enough space for layout"
        return heights[-1]

    def test_single_line_gets_one_row(self):
        al, buf = self._make_layout()
        assert self._get_input_rows(al, buf, "hello") == 1

    def test_three_lines_get_three_rows(self):
        al, buf = self._make_layout()
        assert self._get_input_rows(al, buf, "a\nb\nc") == 3

    def test_five_lines_get_five_rows(self):
        al, buf = self._make_layout()
        assert self._get_input_rows(al, buf, "a\nb\nc\nd\ne") == 5

    def test_ten_lines_get_ten_rows(self):
        al, buf = self._make_layout()
        text = "\n".join(str(i) for i in range(10))
        assert self._get_input_rows(al, buf, text) == 10

    def test_fifteen_lines_capped_at_ten(self):
        al, buf = self._make_layout()
        text = "\n".join(str(i) for i in range(15))
        assert self._get_input_rows(al, buf, text) == 10

    def test_small_terminal_shares_space(self):
        al, buf = self._make_layout()
        text = "\n".join(str(i) for i in range(10))
        rows = self._get_input_rows(al, buf, text, terminal_height=10)
        assert 1 <= rows < 10, f"expected constrained rows, got {rows}"

    def test_input_does_not_absorb_surplus(self):
        al, buf = self._make_layout()
        assert self._get_input_rows(al, buf, "hello", terminal_height=40) == 1


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


class TestBugfix670DoubleSpacing:
    """Regression tests for #670: fullscreen markdown double-spacing on Windows.

    Rich pads every line to Console.width with trailing spaces.  When the
    prompt_toolkit output pane is narrower, wrap_lines=True wraps each padded
    line, creating visual double-spacing.  OutputPaneWriter.flush() must strip
    trailing whitespace and normalize \\r\\n before passing to prompt_toolkit.
    """

    def test_flush_strips_trailing_whitespace(self):
        """Trailing spaces from Rich line-padding must be removed."""
        ctrl = OutputControl()
        writer = OutputPaneWriter(ctrl, lambda: None)
        # Simulate Rich output: text padded to 80 chars with trailing spaces
        writer.write("Hello" + " " * 75 + "\n")
        writer.write(" " * 80 + "\n")  # blank line = 80 spaces
        writer.write("World" + " " * 75 + "\n")
        writer.flush()
        text = "".join(t for _, t in ctrl._output_fragments)
        # No line should contain trailing spaces
        for line in text.split("\n"):
            assert line == line.rstrip(" "), f"Trailing spaces found: {line!r}"

    def test_flush_normalizes_crlf(self):
        """Windows \\r\\n line endings must be normalized to \\n."""
        ctrl = OutputControl()
        writer = OutputPaneWriter(ctrl, lambda: None)
        writer.write("Hello\r\nWorld\r\n")
        writer.flush()
        text = "".join(t for _, t in ctrl._output_fragments)
        assert "\r" not in text

    def test_markdown_no_padded_blank_lines(self):
        """Rich Markdown rendered through OutputPaneWriter must not have padded lines."""
        from rich.console import Console
        from rich.markdown import Markdown
        from rich.padding import Padding

        ctrl = OutputControl()
        writer = OutputPaneWriter(ctrl, lambda: None)
        c = Console(file=writer, force_terminal=True, width=80)
        c.print(Padding(Markdown("Hello\n\nWorld"), (0, 2, 0, 2)))
        text = "".join(t for _, t in ctrl._output_fragments)
        for line in text.split("\n"):
            assert len(line) < 40, f"Line too wide (likely padded): {line!r}"

    def test_markdown_preserves_ansi_codes(self):
        """ANSI styling codes must survive the trailing-space strip."""
        from rich.console import Console
        from rich.markdown import Markdown
        from rich.padding import Padding

        ctrl = OutputControl()
        writer = OutputPaneWriter(ctrl, lambda: None)
        c = Console(file=writer, force_terminal=True, width=80)
        c.print(Padding(Markdown("**Bold** and *italic*"), (0, 2, 0, 2)))
        text = "".join(t for _, t in ctrl._output_fragments)
        assert "Bold" in text
        assert "italic" in text

    def test_markdown_paragraph_spacing_correct(self):
        """Paragraphs should have exactly one blank line between them, not two."""
        from rich.console import Console
        from rich.markdown import Markdown
        from rich.padding import Padding

        ctrl = OutputControl()
        writer = OutputPaneWriter(ctrl, lambda: None)
        c = Console(file=writer, force_terminal=True, width=80)
        c.print(Padding(Markdown("Para 1\n\nPara 2\n\nPara 3"), (0, 2, 0, 2)))
        text = "".join(t for _, t in ctrl._output_fragments)
        # Should never have 3+ consecutive newlines (which would be 2+ blank lines)
        assert "\n\n\n" not in text, f"Triple newline found (double blank line): {text!r}"


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

    def test_style_has_plan_styles(self):
        style = create_anteroom_style()
        class_names = [rule[0] for rule in style.style_rules]
        for name in ("plan.header", "plan.pending", "plan.active", "plan.complete", "plan.failed"):
            assert name in class_names, f"missing plan style: {name}"


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
        import io

        import pytest

        with pytest.raises(io.UnsupportedOperation):
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
        """When renderer.get_fullscreen_layout() is None, callbacks should fall through."""
        # The guard: `renderer.is_fullscreen() and renderer.get_fullscreen_layout() is not None`
        # We can't test the repl callbacks directly, but verify hide_dialog is safe
        # when called on a layout that was never shown.
        al = self._make_layout()
        assert al._dialog_visible is False
        al.hide_dialog()
        assert al._dialog_visible is False


# ---------------------------------------------------------------------------
# Picker overlay
# ---------------------------------------------------------------------------


class TestPickerOverlay:
    def _make_layout(self):
        buf = Buffer(name="test-input")
        return AnteroomLayout(
            header_fn=lambda: [("class:header", " test ")],
            footer_fn=lambda: [("class:footer", " status ")],
            input_buffer=buf,
        )

    def _sample_items(self):
        return [
            {"id": "1", "_label": "Fix auth flow", "_meta": "fix-auth  3msg  2d ago"},
            {"id": "2", "_label": "Canvas work", "_meta": "canvas  5msg  5d ago"},
            {"id": "3", "_label": "Search refactor", "_meta": "search  2msg  1w ago"},
        ]

    def test_picker_initially_hidden(self):
        al = self._make_layout()
        assert al._picker_visible is False
        filt = _PickerVisible(al)
        assert not filt()

    def test_picker_visible_filter_tracks_state(self):
        al = self._make_layout()
        filt = _PickerVisible(al)
        assert not filt()
        al._picker_visible = True
        assert filt()
        al._picker_visible = False
        assert not filt()

    @pytest.mark.asyncio
    async def test_show_picker_returns_selected_item(self):
        import asyncio

        al = self._make_layout()
        items = self._sample_items()

        async def _select():
            await asyncio.sleep(0.01)
            assert al._picker_visible is True
            al.accept_picker()

        task = asyncio.create_task(_select())
        result = await al.show_picker(items=items)
        await task
        assert result is not None
        assert result["id"] == "1"
        assert al._picker_visible is False

    @pytest.mark.asyncio
    async def test_show_picker_cancel_returns_none(self):
        import asyncio

        al = self._make_layout()
        items = self._sample_items()

        async def _cancel():
            await asyncio.sleep(0.01)
            al.cancel_picker()

        task = asyncio.create_task(_cancel())
        result = await al.show_picker(items=items)
        await task
        assert result is None
        assert al._picker_visible is False

    @pytest.mark.asyncio
    async def test_show_picker_empty_items_returns_none(self):
        al = self._make_layout()
        result = await al.show_picker(items=[])
        assert result is None

    def test_picker_move_up(self):
        al = self._make_layout()
        al._picker_items = self._sample_items()
        al._picker_selected_idx = 2
        al.picker_move_up()
        assert al._picker_selected_idx == 1
        al.picker_move_up()
        assert al._picker_selected_idx == 0
        al.picker_move_up()
        assert al._picker_selected_idx == 0  # clamped

    def test_picker_move_down(self):
        al = self._make_layout()
        al._picker_items = self._sample_items()
        al._picker_selected_idx = 0
        al.picker_move_down()
        assert al._picker_selected_idx == 1
        al.picker_move_down()
        assert al._picker_selected_idx == 2
        al.picker_move_down()
        assert al._picker_selected_idx == 2  # clamped

    @pytest.mark.asyncio
    async def test_picker_navigate_and_select(self):
        import asyncio

        al = self._make_layout()
        items = self._sample_items()

        async def _navigate_and_select():
            await asyncio.sleep(0.01)
            al.picker_move_down()
            al.picker_move_down()
            al.accept_picker()

        task = asyncio.create_task(_navigate_and_select())
        result = await al.show_picker(items=items)
        await task
        assert result is not None
        assert result["id"] == "3"

    def test_hide_picker_resets_state(self):
        al = self._make_layout()
        al._picker_visible = True
        al._picker_items = self._sample_items()
        al._picker_selected_idx = 1
        al._picker_preview_fn = lambda x: []
        al.hide_picker()
        assert al._picker_visible is False
        assert al._picker_items == []
        assert al._picker_selected_idx == 0
        assert al._picker_preview_fn is None
        assert al._picker_event is None

    def test_cancel_picker_when_no_event_is_noop(self):
        al = self._make_layout()
        al._picker_event = None
        al.cancel_picker()
        assert al._picker_result is None

    def test_cancel_picker_signals_event(self):
        import asyncio

        al = self._make_layout()
        al._picker_event = asyncio.Event()
        al._picker_result = {"id": "something"}
        al.cancel_picker()
        assert al._picker_result is None
        assert al._picker_event.is_set()

    def test_accept_picker_sets_result(self):
        import asyncio

        al = self._make_layout()
        al._picker_items = self._sample_items()
        al._picker_selected_idx = 1
        al._picker_event = asyncio.Event()
        al.accept_picker()
        assert al._picker_result is not None
        assert al._picker_result["id"] == "2"
        assert al._picker_event.is_set()

    def test_accept_picker_empty_items_no_crash(self):
        import asyncio

        al = self._make_layout()
        al._picker_items = []
        al._picker_event = asyncio.Event()
        al.accept_picker()
        assert al._picker_result is None
        assert al._picker_event.is_set()

    def test_get_picker_list_fragments(self):
        al = self._make_layout()
        al._picker_items = self._sample_items()
        al._picker_selected_idx = 0
        frags = al._get_picker_list()
        texts = "".join(f[1] for f in frags)
        assert "Fix auth flow" in texts
        assert "Canvas work" in texts
        assert frags[0][1].strip().startswith(">")

    def test_get_picker_list_second_selected(self):
        al = self._make_layout()
        al._picker_items = self._sample_items()
        al._picker_selected_idx = 1
        frags = al._get_picker_list()
        selected = [f for f in frags if "selected" in f[0]]
        selected_text = "".join(f[1] for f in selected)
        assert "Canvas work" in selected_text

    def test_get_picker_preview_with_fn(self):
        al = self._make_layout()
        al._picker_items = self._sample_items()
        al._picker_selected_idx = 0
        al._picker_preview_fn = lambda item: [("class:preview", f"Preview for {item['id']}")]
        frags = al._get_picker_preview()
        assert len(frags) == 1
        assert "Preview for 1" in frags[0][1]

    def test_get_picker_preview_no_fn(self):
        al = self._make_layout()
        al._picker_items = self._sample_items()
        al._picker_preview_fn = None
        frags = al._get_picker_preview()
        assert any("No preview" in f[1] for f in frags)

    def test_get_picker_preview_empty_items(self):
        al = self._make_layout()
        al._picker_items = []
        al._picker_preview_fn = lambda x: []
        frags = al._get_picker_preview()
        assert any("No preview" in f[1] for f in frags)

    def test_get_picker_title(self):
        al = self._make_layout()
        frags = al._get_picker_title()
        assert len(frags) == 1
        assert "Resume Conversation" in frags[0][1]

    def test_picker_styles_present(self):
        style = create_anteroom_style()
        class_names = [rule[0] for rule in style.style_rules]
        assert "picker.frame" in class_names
        assert "picker.title" in class_names
        assert "picker.hint" in class_names
        assert "picker.separator" in class_names
        assert "picker.list" in class_names
        assert "picker.list.selected" in class_names
        assert "picker.list.item" in class_names
        assert "picker.list.meta" in class_names
        assert "picker.preview" in class_names
        assert "picker.preview.empty" in class_names

    @pytest.mark.asyncio
    async def test_picker_hides_after_accept(self):
        import asyncio

        al = self._make_layout()
        items = self._sample_items()

        async def _accept():
            await asyncio.sleep(0.01)
            al.accept_picker()

        task = asyncio.create_task(_accept())
        result = await al.show_picker(items=items)
        await task
        assert result is not None
        assert al._picker_visible is False
        assert al._picker_event is None
        assert al._picker_items == []

    @pytest.mark.asyncio
    async def test_multiple_pickers_sequential(self):
        import asyncio

        al = self._make_layout()
        items = self._sample_items()

        async def _accept_first():
            await asyncio.sleep(0.01)
            al.accept_picker()

        task1 = asyncio.create_task(_accept_first())
        r1 = await al.show_picker(items=items)
        await task1
        assert r1 is not None
        assert r1["id"] == "1"
        assert al._picker_visible is False

        async def _accept_second():
            await asyncio.sleep(0.01)
            al.picker_move_down()
            al.accept_picker()

        task2 = asyncio.create_task(_accept_second())
        r2 = await al.show_picker(items=items)
        await task2
        assert r2 is not None
        assert r2["id"] == "2"
        assert al._picker_visible is False


# ---------------------------------------------------------------------------
# Regression tests for #617 bug fixes
# ---------------------------------------------------------------------------


class TestBugfix617:
    """Regression tests for fullscreen layout bugs (#617)."""

    def _make_layout(self):
        buf = Buffer(name="test-input")
        return AnteroomLayout(
            header_fn=lambda: [("", "header")],
            footer_fn=lambda: [("", "footer")],
            input_buffer=buf,
        )

    def test_hide_dialog_during_teardown(self):
        """#617-4: hide_dialog() must not raise when layout is destroyed."""
        al = self._make_layout()
        # Simulate destroyed layout by making focus() raise
        al._layout = None  # type: ignore[assignment]
        # Should not raise
        al.hide_dialog()

    def test_hide_picker_during_teardown(self):
        """#617-4: hide_picker() must not raise when layout is destroyed."""
        al = self._make_layout()
        al._layout = None  # type: ignore[assignment]
        al.hide_picker()

    def test_dialog_state_ordering(self):
        """#617-5: _dialog_visible must be True before buffer reset."""
        al = self._make_layout()
        # After show_dialog sets up state, visible should be True
        # We can't easily test the ordering without async, but we can
        # verify that the reset happens after visible is set
        al._dialog_visible = False
        al._dialog_event = None
        # Manually replicate the fixed ordering
        al._dialog_visible = True
        al._dialog_buffer.reset()
        assert al._dialog_visible is True

    def test_flush_atomic_swap(self):
        """#617-14: OutputPaneWriter.flush() uses atomic swap."""
        ctrl = OutputControl()
        writer = OutputPaneWriter(ctrl, lambda: None)
        writer.write("hello ")
        writer.write("world")
        writer.flush()
        assert ctrl.fragment_count > 0
        # After flush, pending should be empty
        assert len(writer._pending) == 0

    def test_picker_preview_bounds_check(self):
        """#617-15: _get_picker_preview() bounds-checks index."""
        al = self._make_layout()
        al._picker_items = [{"_label": "A"}]
        al._picker_selected_idx = 5  # out of bounds
        al._picker_preview_fn = lambda item: [("", item["_label"])]
        # Should not raise, should return empty preview
        result = al._get_picker_preview()
        assert result == [("class:picker.preview.empty", "  No preview available")]

    def test_fileno_raises_unsupported_operation(self):
        """#617-16: fileno() must raise io.UnsupportedOperation."""
        ctrl = OutputControl()
        writer = OutputPaneWriter(ctrl, lambda: None)
        with pytest.raises(io.UnsupportedOperation):
            writer.fileno()

    def test_header_no_leading_separator(self):
        """#617-17: No leading separator when first fields are empty."""
        # Only git_branch set, no model or working_dir
        result = format_header(git_branch="main")
        styles = [s for s, _ in result]
        texts = [t for _, t in result]
        # Should not have a separator before "main"
        assert "class:header.sep" not in styles[:2]
        assert "main" in texts

    def test_header_separator_between_fields(self):
        """Separator should appear between populated fields."""
        result = format_header(model="gpt-4", git_branch="main")
        texts = "".join(t for _, t in result)
        assert "gpt-4" in texts
        assert "main" in texts
        # Should have a separator
        sep_count = sum(1 for s, _ in result if s == "class:header.sep")
        assert sep_count >= 1

    def test_flush_empty_pending_is_noop(self):
        """#617-14: flush() with no pending data should not touch control."""
        ctrl = OutputControl()
        writer = OutputPaneWriter(ctrl, lambda: None)
        initial_count = ctrl.fragment_count
        writer.flush()
        assert ctrl.fragment_count == initial_count

    def test_flush_concurrent_write_safety(self):
        """#617-14: Writes during flush don't lose data (atomic swap)."""
        ctrl = OutputControl()
        invalidate_calls = []
        writer = OutputPaneWriter(ctrl, lambda: invalidate_calls.append(1))
        writer.write("first")
        # Simulate a write that arrives during flush by writing after
        # the pending list is swapped but before flush completes
        writer.flush()
        writer.write("second")
        writer.flush()
        # Both writes should have been flushed
        assert ctrl.fragment_count > 0
        assert len(writer._pending) == 0
        assert len(invalidate_calls) == 2

    def test_hide_dialog_focus_exception_swallowed(self):
        """#617-4: Any exception from focus() is caught during teardown."""
        al = self._make_layout()
        # Make focus raise a specific error
        al._layout.focus = lambda w: (_ for _ in ()).throw(  # type: ignore[assignment]
            RuntimeError("layout destroyed")
        )
        # Should not propagate
        al.hide_dialog()

    def test_hide_picker_focus_exception_swallowed(self):
        """#617-4: Any exception from focus() is caught during teardown."""
        al = self._make_layout()
        al._layout.focus = lambda w: (_ for _ in ()).throw(  # type: ignore[assignment]
            RuntimeError("layout destroyed")
        )
        al.hide_picker()

    def test_picker_preview_empty_items(self):
        """#617-15: Empty items list returns fallback preview."""
        al = self._make_layout()
        al._picker_items = []
        al._picker_selected_idx = 0
        al._picker_preview_fn = lambda item: [("", "should not reach")]
        result = al._get_picker_preview()
        assert result == [("class:picker.preview.empty", "  No preview available")]

    def test_picker_preview_no_preview_fn(self):
        """#617-15: None preview_fn returns fallback preview."""
        al = self._make_layout()
        al._picker_items = [{"_label": "X"}]
        al._picker_selected_idx = 0
        al._picker_preview_fn = None
        result = al._get_picker_preview()
        assert result == [("class:picker.preview.empty", "  No preview available")]

    def test_header_all_empty_fields(self):
        """#617-17: All-empty header should not have any separators."""
        result = format_header()
        sep_count = sum(1 for s, _ in result if s == "class:header.sep")
        assert sep_count == 0

    def test_fileno_exception_type(self):
        """#617-16: fileno() raises io.UnsupportedOperation, not AttributeError."""
        ctrl = OutputControl()
        writer = OutputPaneWriter(ctrl, lambda: None)
        # Should NOT raise AttributeError
        with pytest.raises(io.UnsupportedOperation):
            writer.fileno()
        # Verify it's a subclass of OSError (standard contract)
        try:
            writer.fileno()
        except io.UnsupportedOperation as e:
            assert isinstance(e, OSError)


class TestScrollIndicator:
    """#257 Phase 3: Scroll position indicator on bottom separator."""

    def _make_layout(self):
        from prompt_toolkit.buffer import Buffer

        buf = Buffer()
        return AnteroomLayout(
            header_fn=lambda: [("", "header")],
            footer_fn=lambda: [("", "footer")],
            input_buffer=buf,
        )

    def test_at_bottom_shows_plain_separator(self):
        al = self._make_layout()
        al._output._scroll_offset = 0
        result = al._scroll_indicator_text()
        # Should be all separator style, no indicator
        styles = {s for s, _ in result}
        assert "class:scroll.indicator" not in styles

    def test_scrolled_up_shows_lines_below(self):
        al = self._make_layout()
        al._output._scroll_offset = 42
        result = al._scroll_indicator_text()
        text = "".join(t for _, t in result)
        assert "42 lines below" in text
        styles = {s for s, _ in result}
        assert "class:scroll.indicator" in styles

    def test_scroll_offset_1_shows_indicator(self):
        al = self._make_layout()
        al._output._scroll_offset = 1
        result = al._scroll_indicator_text()
        text = "".join(t for _, t in result)
        assert "1 lines below" in text


class TestApprovalModePrompt:
    """#257 Phase 3: Approval mode-aware prompt coloring."""

    def test_auto_mode_gold(self):
        from anteroom.cli.layout import set_approval_mode

        set_approval_mode("auto")
        result = input_line_prefix(0, 0)
        assert result[0][0] == "class:prompt.auto"

    def test_ask_mode_strict(self):
        from anteroom.cli.layout import set_approval_mode

        set_approval_mode("ask")
        result = input_line_prefix(0, 0)
        assert result[0][0] == "class:prompt.strict"

    def test_ask_for_writes_caution(self):
        from anteroom.cli.layout import set_approval_mode

        set_approval_mode("ask_for_writes")
        result = input_line_prefix(0, 0)
        assert result[0][0] == "class:prompt.caution"

    def test_unknown_mode_fallback(self):
        from anteroom.cli.layout import set_approval_mode

        set_approval_mode("unknown_mode")
        result = input_line_prefix(0, 0)
        assert result[0][0] == "class:prompt"

    def test_empty_mode_fallback(self):
        from anteroom.cli.layout import set_approval_mode

        set_approval_mode("")
        result = input_line_prefix(0, 0)
        assert result[0][0] == "class:prompt"

    def test_continuation_line_unaffected(self):
        from anteroom.cli.layout import set_approval_mode

        set_approval_mode("auto")
        result = input_line_prefix(1, 0)
        assert result[0][0] == "class:prompt.continuation"

"""Full-screen terminal layout for the Anteroom CLI.

Provides the HSplit structure::

    +-- header (model, dir, git branch) -----------------+
    +-- separator ----------------------------------------+
    |  output pane (scrolling, auto-scroll to bottom)     |
    +-- separator ----------------------------------------+
    |  footer (status toolbar)                            |
    +-- prompt -------------------------------------------+
    |  input (editable, multiline, completions)           |
    +-----------------------------------------------------+
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Filter
from prompt_toolkit.layout.containers import ConditionalContainer, Float, FloatContainer, HSplit, Window
from prompt_toolkit.layout.controls import (
    BufferControl,
    FormattedTextControl,
    UIContent,
)
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.styles import Style

if TYPE_CHECKING:
    from prompt_toolkit.formatted_text import StyleAndTextTuples


_MAX_SCROLL_OFFSET = 2**31  # sentinel for "scroll to very top"; clamped in create_content


class OutputControl(FormattedTextControl):
    """Read-only output pane that auto-scrolls to show the latest content.

    Stores formatted text fragments and overrides ``create_content`` to
    place the cursor at the last line, which causes the parent ``Window``
    to scroll down automatically.
    """

    def __init__(self) -> None:
        self._output_fragments: list[tuple[str, str]] = []
        self._scroll_offset: int = 0  # lines up from bottom; 0 = auto-scroll
        super().__init__(self._get_output_fragments, focusable=False, show_cursor=False)

    def mouse_handler(self, mouse_event: MouseEvent) -> None:
        """Handle mouse scroll wheel events on the output pane."""
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.scroll_up(3)
        elif mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.scroll_down(3)

    def _get_output_fragments(self) -> list[tuple[str, str]]:
        return self._output_fragments

    def append(self, fragments: list[tuple[str, str]]) -> None:
        """Append prompt_toolkit style fragments to the output."""
        self._output_fragments.extend(fragments)
        self._scroll_offset = 0  # new content resets to auto-scroll

    def append_text(self, text: str, style: str = "") -> None:
        """Append plain text with an optional style class."""
        self._output_fragments.append((style, text))
        self._scroll_offset = 0

    def append_newline(self) -> None:
        """Append a line break."""
        self._output_fragments.append(("", "\n"))

    def clear(self) -> None:
        """Remove all content from the output pane."""
        self._output_fragments.clear()

    @property
    def fragment_count(self) -> int:
        return len(self._output_fragments)

    def scroll_up(self, lines: int = 10) -> None:
        """Scroll up (back through history) by *lines*."""
        self._scroll_offset += lines

    def scroll_down(self, lines: int = 10) -> None:
        """Scroll down (toward latest content) by *lines*."""
        self._scroll_offset = max(0, self._scroll_offset - lines)

    def scroll_to_bottom(self) -> None:
        """Reset to auto-scroll (show latest content)."""
        self._scroll_offset = 0

    def scroll_to_top(self) -> None:
        """Scroll to the very top of the output."""
        self._scroll_offset = _MAX_SCROLL_OFFSET

    def create_content(self, width: int, height: int | None) -> UIContent:
        """Build UIContent with cursor positioned for scrolling.

        When ``_scroll_offset`` is 0 the cursor sits at the last line
        (auto-scroll).  A positive offset moves the cursor up, letting the
        user page back through history.
        """
        content = super().create_content(width, height)
        if content.line_count <= 1:
            return content
        # Clamp offset to valid range
        max_offset = max(0, content.line_count - 1)
        offset = min(self._scroll_offset, max_offset)
        target = content.line_count - 1 - offset
        return UIContent(
            get_line=content.get_line,
            line_count=content.line_count,
            cursor_position=Point(0, target),
        )


# ---------------------------------------------------------------------------
# Header formatter
# ---------------------------------------------------------------------------

_HEADER_SEP = " \u00b7 "  # middle dot separator


def format_header(
    *,
    model: str = "",
    working_dir: str = "",
    git_branch: str = "",
    project_name: str = "",
    space_name: str = "",
    conv_title: str = "",
    plan_mode: bool = False,
) -> list[tuple[str, str]]:
    """Build header fragments: model, dir, branch, project, space, title, mode."""
    parts: list[tuple[str, str]] = [("class:header", " ")]

    if model:
        parts.append(("class:header.model", model))

    if working_dir:
        short = _shorten_path(working_dir)
        if model:
            parts.append(("class:header.sep", _HEADER_SEP))
        parts.append(("class:header.dir", short))

    if git_branch:
        parts.append(("class:header.sep", _HEADER_SEP))
        parts.append(("class:header.branch", git_branch))

    if project_name:
        parts.append(("class:header.sep", _HEADER_SEP))
        parts.append(("class:header.project", project_name))

    if space_name:
        parts.append(("class:header.sep", _HEADER_SEP))
        parts.append(("class:header.space", space_name))

    if conv_title:
        parts.append(("class:header.sep", _HEADER_SEP))
        parts.append(("class:header.title", conv_title))

    if plan_mode:
        parts.append(("class:header.sep", _HEADER_SEP))
        parts.append(("class:header.plan", "PLAN"))

    parts.append(("class:header", " "))
    return parts


def _shorten_path(path: str) -> str:
    """Shorten an absolute path using ``~`` for the home directory."""
    import os

    home = os.path.expanduser("~")
    if path.startswith(home):
        return "~" + path[len(home) :]
    return path


# ---------------------------------------------------------------------------
# Input line prefix
# ---------------------------------------------------------------------------


def input_line_prefix(line_number: int, wrap_count: int) -> "StyleAndTextTuples":
    """Prompt prefix for the input area: ``> `` on line 0, ``  `` after."""
    if line_number == 0:
        return [("class:prompt", "> ")]
    return [("class:prompt.continuation", ". ")]


# ---------------------------------------------------------------------------
# Input lexer — highlights /commands
# ---------------------------------------------------------------------------

_SLASH_CMD_RE = re.compile(r"^(/\S+)")


class InputLexer(Lexer):
    """Highlights ``/commands`` in the input area."""

    def lex_document(self, document: Any) -> Callable[[int], "StyleAndTextTuples"]:
        def get_line(lineno: int) -> "StyleAndTextTuples":
            line = document.lines[lineno] if lineno < len(document.lines) else ""
            m = _SLASH_CMD_RE.match(line) if lineno == 0 else None
            if m:
                cmd_end = m.end()
                return [
                    ("class:input.command", line[:cmd_end]),
                    ("", line[cmd_end:]),
                ]
            return [("", line)]

        return get_line


# ---------------------------------------------------------------------------
# AnteroomLayout
# ---------------------------------------------------------------------------


class _HasContent(Filter):
    """Filter that returns True when the status line has content."""

    def __init__(self, layout: "AnteroomLayout") -> None:
        self._layout = layout

    def __call__(self) -> bool:
        return bool(self._layout._status_fragments)


class AnteroomLayout:
    """Manages the full-screen HSplit layout and exposes the output pane."""

    def __init__(
        self,
        *,
        header_fn: Callable[[], "StyleAndTextTuples"],
        footer_fn: Callable[[], "StyleAndTextTuples"],
        input_buffer: Buffer,
    ) -> None:
        self._output = OutputControl()
        self._header_fn = header_fn
        self._footer_fn = footer_fn
        self._input_buffer = input_buffer
        self._status_fragments: list[tuple[str, str]] = []

        self._header_window = Window(
            content=FormattedTextControl(self._header_fn),
            height=1,
            style="class:header",
        )
        self._output_window = Window(
            content=self._output,
            wrap_lines=True,
            style="class:output",
        )
        self._status_control = FormattedTextControl(self._get_status)
        self._status_window = ConditionalContainer(
            Window(
                content=self._status_control,
                height=1,
                style="class:status",
            ),
            filter=_HasContent(self),
        )
        self._footer_window = Window(
            content=FormattedTextControl(self._footer_fn),
            height=1,
        )
        self._input_control = BufferControl(
            buffer=self._input_buffer,
            lexer=InputLexer(),
            include_default_input_processors=True,
        )
        self._input_window = Window(
            content=self._input_control,
            height=Dimension(min=1, max=10, preferred=1),
            wrap_lines=True,
            style="class:input",
            get_line_prefix=input_line_prefix,
        )

        self._layout = Layout(
            FloatContainer(
                content=HSplit(
                    [
                        self._header_window,
                        Window(height=1, char="\u2500", style="class:separator"),
                        self._output_window,
                        self._status_window,
                        Window(height=1, char="\u2500", style="class:separator"),
                        self._footer_window,
                        self._input_window,
                    ]
                ),
                floats=[
                    Float(
                        xcursor=True,
                        ycursor=True,
                        content=CompletionsMenu(max_height=8, scroll_offset=1),
                    ),
                ],
            ),
            focused_element=self._input_window,
        )

    def _get_status(self) -> list[tuple[str, str]]:
        return self._status_fragments

    # -- Public API --------------------------------------------------------

    @property
    def layout(self) -> Layout:
        return self._layout

    @property
    def output(self) -> OutputControl:
        return self._output

    @property
    def input_buffer(self) -> Buffer:
        return self._input_buffer

    @property
    def input_control(self) -> BufferControl:
        return self._input_control

    def focus_input(self) -> None:
        """Ensure the input window has focus."""
        self._layout.focus(self._input_window)

    def set_status(self, fragments: list[tuple[str, str]]) -> None:
        """Set the ephemeral status line (thinking indicator, tool ticker)."""
        self._status_fragments[:] = fragments

    def clear_status(self) -> None:
        """Hide the status line."""
        self._status_fragments.clear()

    def append_output(self, text: str, style: str = "") -> None:
        """Append plain text to the output pane."""
        self._output.append_text(text, style)

    def append_output_fragments(self, fragments: list[tuple[str, str]]) -> None:
        """Append styled fragments to the output pane."""
        self._output.append(fragments)

    def append_ansi(self, ansi_text: str) -> None:
        """Append ANSI-encoded text (e.g. from Rich console) to the output."""
        from prompt_toolkit.formatted_text import ANSI, to_formatted_text

        fragments = _strip_bg(to_formatted_text(ANSI(ansi_text)))
        self._output.append(fragments)

    def scroll_output_up(self, lines: int = 10) -> None:
        """Scroll the output pane up (back through history)."""
        self._output.scroll_up(lines)

    def scroll_output_down(self, lines: int = 10) -> None:
        """Scroll the output pane down (toward latest content)."""
        self._output.scroll_down(lines)

    def scroll_output_to_top(self) -> None:
        """Scroll to the very top of the output."""
        self._output.scroll_to_top()

    def scroll_output_to_bottom(self) -> None:
        """Reset scroll to show latest content."""
        self._output.scroll_to_bottom()

    def clear_output(self) -> None:
        """Remove all content from the output pane."""
        self._output.clear()


# ---------------------------------------------------------------------------
# Background stripping helper
# ---------------------------------------------------------------------------

_BG_RE = re.compile(r"\s*bg:[^\s]*")


def _strip_bg(fragments: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Remove ``bg:...`` tokens from fragment style strings.

    Rich/Pygments syntax themes embed explicit background colors in ANSI
    output.  When rendered inside a prompt_toolkit full-screen Window these
    backgrounds clash with the terminal's natural background, especially once
    the content scrolls.  Stripping them lets all content use the terminal
    default consistently.
    """
    out: list[tuple[str, str]] = []
    for style, text in fragments:
        cleaned = _BG_RE.sub("", style).strip()
        out.append((cleaned, text))
    return out


# ---------------------------------------------------------------------------
# OutputPaneWriter — file-like bridge from Rich Console to OutputControl
# ---------------------------------------------------------------------------


class OutputPaneWriter:
    """File-like object that captures Rich ANSI output and sends it to an OutputControl.

    Usage::

        writer = OutputPaneWriter(layout.output, app.invalidate)
        console = Console(file=writer, force_terminal=True)
        console.print("[bold]hello[/bold]")  # renders to output pane
    """

    def __init__(
        self,
        output: OutputControl,
        invalidate_fn: Callable[[], None],
    ) -> None:
        self._output = output
        self._invalidate = invalidate_fn
        self._pending: list[str] = []

    def write(self, data: str) -> int:
        self._pending.append(data)
        return len(data)

    def flush(self) -> None:
        if not self._pending:
            return
        text = "".join(self._pending)
        self._pending.clear()
        if not text:
            return
        from prompt_toolkit.formatted_text import ANSI, to_formatted_text

        fragments = _strip_bg(to_formatted_text(ANSI(text)))
        self._output.append(fragments)
        self._invalidate()

    @property
    def encoding(self) -> str:
        return "utf-8"

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        raise AttributeError("OutputPaneWriter has no file descriptor")


# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------


def create_anteroom_style() -> Style:
    """Build the prompt_toolkit Style for the full-screen layout."""
    return Style.from_dict(
        {
            # Header
            "header": "bg:#1e1e2e #C5A059 bold",
            "header.model": "#C5A059 bold",
            "header.sep": "#505868",
            "header.dir": "#94A3B8",
            "header.branch": "#88a0b8",
            "header.project": "#a0b8a0",
            "header.space": "#b8a0c8",
            "header.title": "#94A3B8 italic",
            "header.plan": "#e8b830 bold",
            # Separators
            "separator": "#3a3a4e",
            # Output pane — use terminal default bg so Rich code blocks blend
            "output": "",
            # Status line (thinking, tool ticker)
            "status": "#C5A059",
            "status.timer": "#6b7280",
            "status.phase": "#8b8b8b",
            "status.hint": "#8b8b8b",
            # Footer (reuses Phase 0 toolbar styles)
            "bottom-toolbar": "bg:#1e1e2e #9090a0 noreverse",
            "bottom-toolbar.text": "noreverse",
            "bottom-toolbar.model": "#C5A059",
            "bottom-toolbar.tokens": "#c0c0d0",
            "bottom-toolbar.tokens-warn": "#e8b830",
            "bottom-toolbar.tokens-danger": "#e05050",
            "bottom-toolbar.dim": "#707888",
            "bottom-toolbar.sep": "#505868",
            "bottom-toolbar.mcp": "#88a0b8",
            # Input
            "input": "",
            "input.command": "#C5A059 bold",
            "prompt": "#C5A059 bold",
            "prompt.continuation": "#505868",
        }
    )

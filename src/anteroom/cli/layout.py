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

import asyncio
import re
from typing import TYPE_CHECKING, Any, Callable

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Filter
from prompt_toolkit.layout.containers import ConditionalContainer, Float, FloatContainer, HSplit, VSplit, Window
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
        else:
            return
        from prompt_toolkit.application import get_app

        get_app().invalidate()

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
        self._scroll_offset = 0

    def clear(self) -> None:
        """Remove all content from the output pane."""
        self._output_fragments.clear()

    @property
    def fragment_count(self) -> int:
        return len(self._output_fragments)

    def checkpoint(self) -> int:
        """Return current fragment count as a checkpoint for later truncation."""
        return len(self._output_fragments)

    def truncate_to(self, checkpoint: int) -> None:
        """Remove all fragments from *checkpoint* onward."""
        del self._output_fragments[checkpoint:]

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
    space_name: str = "",
    conv_title: str = "",
    plan_mode: bool = False,
) -> list[tuple[str, str]]:
    """Build header fragments: model, dir, branch, space, title, mode."""
    parts: list[tuple[str, str]] = [("class:header", " ")]

    if model:
        parts.append(("class:header.model", model))

    if working_dir:
        short = _shorten_path(working_dir)
        if model:
            parts.append(("class:header.sep", _HEADER_SEP))
        parts.append(("class:header.dir", short))

    def _sep_append(style: str, text: str) -> None:
        # Only add separator if there's already content beyond the leading space
        if len(parts) > 1:
            parts.append(("class:header.sep", _HEADER_SEP))
        parts.append((style, text))

    if git_branch:
        _sep_append("class:header.branch", git_branch)

    if space_name:
        _sep_append("class:header.space", f"Space: {space_name}")

    if conv_title:
        _sep_append("class:header.title", conv_title)

    if plan_mode:
        _sep_append("class:header.plan", "PLAN")

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


_APPROVAL_PROMPT_STYLES = {
    "auto": "class:prompt.auto",
    "ask_for_dangerous": "class:prompt.safe",
    "ask_for_writes": "class:prompt.caution",
    "ask": "class:prompt.strict",
}

_current_approval_mode: str = ""


def set_approval_mode(mode: str) -> None:
    """Update the approval mode used for prompt prefix coloring."""
    global _current_approval_mode
    _current_approval_mode = mode


def input_line_prefix(line_number: int, wrap_count: int) -> "StyleAndTextTuples":
    """Prompt prefix for the input area: ``> `` on line 0, ``  `` after.

    Color varies by approval mode when set.
    """
    if line_number == 0:
        style = _APPROVAL_PROMPT_STYLES.get(_current_approval_mode, "class:prompt")
        return [(style, "> ")]
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


class _DialogVisible(Filter):
    """Filter that returns True when the modal dialog overlay is visible."""

    def __init__(self, layout: "AnteroomLayout") -> None:
        self._layout = layout

    def __call__(self) -> bool:
        return self._layout._dialog_visible


class _PickerVisible(Filter):
    """Filter that returns True when the picker overlay is visible."""

    def __init__(self, layout: "AnteroomLayout") -> None:
        self._layout = layout

    def __call__(self) -> bool:
        return self._layout._picker_visible


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
        self._mouse_mode: bool = True  # True = scroll capture, False = text selection

        # Picker overlay state
        self._picker_visible: bool = False
        self._picker_items: list[dict[str, Any]] = []
        self._picker_selected_idx: int = 0
        self._picker_result: dict[str, Any] | None = None
        self._picker_event: asyncio.Event | None = None
        self._picker_preview_fn: Callable[[dict[str, Any]], list[tuple[str, str]]] | None = None

        # Dialog overlay state
        self._dialog_visible: bool = False
        self._dialog_title: str = ""
        self._dialog_body_fragments: list[tuple[str, str]] = []
        self._dialog_result: str | None = None
        self._dialog_event: asyncio.Event | None = None

        self._dialog_buffer = Buffer(
            name="anteroom-dialog",
            multiline=False,
            accept_handler=self._on_dialog_accept,
        )

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
            height=lambda: Dimension(
                min=1,
                max=min(10, max(1, self._input_buffer.document.line_count)),
                preferred=min(10, max(1, self._input_buffer.document.line_count)),
            ),
            wrap_lines=True,
            style="class:input",
            get_line_prefix=input_line_prefix,
        )

        # Dialog overlay containers
        self._dialog_input_window = Window(
            content=BufferControl(buffer=self._dialog_buffer),
            height=1,
            style="class:dialog.input",
        )
        dialog_inner = HSplit(
            [
                Window(height=1, style="class:dialog.frame"),
                Window(
                    content=FormattedTextControl(self._get_dialog_title),
                    height=1,
                    style="class:dialog.title",
                ),
                Window(height=1, style="class:dialog.frame"),
                Window(
                    content=FormattedTextControl(self._get_dialog_body),
                    style="class:dialog.body",
                    wrap_lines=True,
                ),
                Window(height=1, style="class:dialog.frame"),
                VSplit(
                    [
                        Window(width=2, style="class:dialog.frame"),
                        Window(
                            content=FormattedTextControl(lambda: [("class:dialog.input.prompt", " > ")]),
                            width=3,
                            height=1,
                            style="class:dialog.input",
                        ),
                        self._dialog_input_window,
                        Window(width=1, style="class:dialog.input"),
                    ]
                ),
                Window(height=1, style="class:dialog.frame"),
                Window(
                    content=FormattedTextControl(lambda: [("class:dialog.hint", "  Enter: submit  Escape: cancel")]),
                    height=1,
                    style="class:dialog.hint",
                ),
            ],
            style="class:dialog.frame",
        )
        dialog_centered = HSplit(
            [
                Window(style="class:dialog.shadow"),  # top spacer
                VSplit(
                    [
                        Window(width=2, style="class:dialog.shadow"),  # left margin
                        dialog_inner,
                        Window(width=2, style="class:dialog.shadow"),  # right margin
                    ]
                ),
                Window(style="class:dialog.shadow"),  # bottom spacer
            ]
        )

        # Picker overlay containers (full-page)
        picker_list_window = Window(
            content=FormattedTextControl(self._get_picker_list),
            width=55,
            wrap_lines=False,
            style="class:picker.list",
        )
        picker_preview_window = Window(
            content=FormattedTextControl(self._get_picker_preview),
            wrap_lines=True,
            style="class:picker.preview",
        )
        picker_body = VSplit(
            [
                picker_list_window,
                Window(width=1, char="\u2502", style="class:picker.separator"),
                picker_preview_window,
            ]
        )
        picker_fullpage = HSplit(
            [
                Window(
                    content=FormattedTextControl(self._get_picker_title),
                    height=1,
                    style="class:picker.title",
                ),
                Window(height=1, char="\u2500", style="class:picker.border"),
                picker_body,
                Window(
                    content=FormattedTextControl(
                        lambda: [("class:picker.hint", "  \u2191\u2193/jk navigate  Enter select  Esc cancel")]
                    ),
                    height=1,
                    style="class:picker.hint",
                ),
            ],
            style="class:picker.frame",
        )

        self._bottom_sep_window = Window(
            content=FormattedTextControl(self._scroll_indicator_text),
            height=1,
            style="class:separator",
        )

        self._layout = Layout(
            FloatContainer(
                content=HSplit(
                    [
                        self._header_window,
                        Window(height=1, char="\u2500", style="class:separator"),
                        self._output_window,
                        self._status_window,
                        self._bottom_sep_window,
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
                    Float(
                        content=ConditionalContainer(
                            dialog_centered,
                            filter=_DialogVisible(self),
                        ),
                        transparent=False,
                    ),
                    Float(
                        content=ConditionalContainer(
                            picker_fullpage,
                            filter=_PickerVisible(self),
                        ),
                        transparent=False,
                        left=0,
                        right=0,
                        top=0,
                        bottom=0,
                    ),
                ],
            ),
            focused_element=self._input_window,
        )

    def _get_status(self) -> list[tuple[str, str]]:
        return self._status_fragments

    def _get_dialog_title(self) -> list[tuple[str, str]]:
        return [("class:dialog.title", f"  {self._dialog_title}")]

    def _get_dialog_body(self) -> list[tuple[str, str]]:
        return self._dialog_body_fragments

    def _on_dialog_accept(self, buf: Buffer) -> bool:
        self._dialog_result = buf.text
        if self._dialog_event is not None:
            self._dialog_event.set()
        return False  # do not append to history

    def _scroll_indicator_text(self) -> list[tuple[str, str]]:
        """Build bottom separator fragments with scroll offset and mouse mode indicator."""
        offset = self._output._scroll_offset
        parts: list[tuple[str, str]] = []
        # Mouse mode indicator (right-aligned)
        if not self._mouse_mode:
            mode_label = " SELECT MODE (Ctrl-S to exit) "
        else:
            mode_label = ""
        # Scroll offset indicator (centered)
        if offset > 0:
            scroll_label = f" \u2191 {offset} lines below "
        else:
            scroll_label = ""
        if not scroll_label and not mode_label:
            return [("class:separator", "\u2500" * 80)]
        content_len = len(scroll_label) + len(mode_label)
        remaining = max(0, 80 - content_len)
        if scroll_label and mode_label:
            left_pad = remaining // 2
            right_pad = remaining - left_pad
            parts.append(("class:separator", "\u2500" * left_pad))
            parts.append(("class:scroll.indicator", scroll_label))
            parts.append(("class:separator", "\u2500" * right_pad))
            parts.append(("class:select.indicator", mode_label))
        elif scroll_label:
            pad = remaining // 2
            parts.append(("class:separator", "\u2500" * pad))
            parts.append(("class:scroll.indicator", scroll_label))
            parts.append(("class:separator", "\u2500" * (remaining - pad)))
        else:
            parts.append(("class:separator", "\u2500" * remaining))
            parts.append(("class:select.indicator", mode_label))
        return parts

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

    # -- Dialog overlay API ------------------------------------------------

    async def show_dialog(
        self,
        *,
        title: str,
        body_fragments: list[tuple[str, str]],
    ) -> str | None:
        """Show a modal dialog overlay and wait for user input.

        Returns the text entered by the user, or ``None`` if they pressed
        Escape to cancel.
        """
        self._dialog_title = title
        self._dialog_body_fragments = body_fragments
        self._dialog_result = None
        self._dialog_event = asyncio.Event()
        self._dialog_visible = True
        self._dialog_buffer.reset()
        self._layout.focus(self._dialog_input_window)
        try:
            await self._dialog_event.wait()
        finally:
            self.hide_dialog()
        return self._dialog_result

    def hide_dialog(self) -> None:
        """Dismiss the dialog overlay and return focus to the main input."""
        self._dialog_visible = False
        self._dialog_buffer.reset()
        self._dialog_event = None
        try:
            self._layout.focus(self._input_window)
        except Exception:
            pass  # layout may be destroyed during app teardown

    def cancel_dialog(self) -> None:
        """Cancel the dialog (Escape pressed). Sets result to None and signals."""
        self._dialog_result = None
        if self._dialog_event is not None:
            self._dialog_event.set()

    # -- Picker overlay content methods ------------------------------------

    def _get_picker_title(self) -> list[tuple[str, str]]:
        return [("class:picker.title", "  Resume Conversation")]

    def _get_picker_list(self) -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = []
        for i, item in enumerate(self._picker_items):
            is_sel = i == self._picker_selected_idx
            label = item.get("_label", "")
            meta = item.get("_meta", "")
            if is_sel:
                fragments.extend(
                    [
                        ("class:picker.list.selected", f" > {label}"),
                        ("class:picker.list.selected-meta", f"  {meta}"),
                        ("class:picker.list.selected", "\n"),
                    ]
                )
            else:
                fragments.extend(
                    [
                        ("class:picker.list.item", f"   {label}"),
                        ("class:picker.list.meta", f"  {meta}"),
                        ("class:picker.list.item", "\n"),
                    ]
                )
        return fragments

    def _get_picker_preview(self) -> list[tuple[str, str]]:
        items = self._picker_items
        idx = self._picker_selected_idx
        if not items or self._picker_preview_fn is None:
            return [("class:picker.preview.empty", "  No preview available")]
        if idx >= len(items):
            return [("class:picker.preview.empty", "  No preview available")]
        return self._picker_preview_fn(items[idx])

    # -- Picker overlay API ------------------------------------------------

    async def show_picker(
        self,
        *,
        items: list[dict[str, Any]],
        preview_fn: Callable[[dict[str, Any]], list[tuple[str, str]]] | None = None,
    ) -> dict[str, Any] | None:
        """Show a full-page picker overlay and wait for the user to select an item.

        Returns the selected item dict, or ``None`` if the user cancelled.
        """
        if not items:
            return None
        self._picker_items = items
        self._picker_selected_idx = 0
        self._picker_preview_fn = preview_fn
        self._picker_result = None
        self._picker_event = asyncio.Event()
        self._picker_visible = True
        try:
            await self._picker_event.wait()
        finally:
            self.hide_picker()
        return self._picker_result

    def picker_move_up(self) -> None:
        """Move the picker selection up by one."""
        if self._picker_selected_idx > 0:
            self._picker_selected_idx -= 1

    def picker_move_down(self) -> None:
        """Move the picker selection down by one."""
        if self._picker_selected_idx < len(self._picker_items) - 1:
            self._picker_selected_idx += 1

    def accept_picker(self) -> None:
        """Accept the current picker selection."""
        if self._picker_items:
            self._picker_result = self._picker_items[self._picker_selected_idx]
        if self._picker_event is not None:
            self._picker_event.set()

    def cancel_picker(self) -> None:
        """Cancel the picker (Escape pressed)."""
        self._picker_result = None
        if self._picker_event is not None:
            self._picker_event.set()

    def hide_picker(self) -> None:
        """Dismiss the picker overlay and return focus to the main input."""
        self._picker_visible = False
        self._picker_items = []
        self._picker_selected_idx = 0
        self._picker_preview_fn = None
        self._picker_event = None
        try:
            self._layout.focus(self._input_window)
        except Exception:
            pass  # layout may be destroyed during app teardown

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

        fragments = _strip_bg(list(to_formatted_text(ANSI(ansi_text))))  # type: ignore[arg-type]
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

    def set_mouse_mode(self, enabled: bool) -> None:
        """Toggle mouse capture mode. Off enables native text selection."""
        self._mouse_mode = enabled

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
        # Atomic swap prevents race between write() and flush()
        pending, self._pending = self._pending, []
        text = "".join(pending)
        if not text:
            return
        # Normalize Windows line endings (\r\n → \n) and strip trailing
        # whitespace that Rich adds for line padding.  Rich pads every line
        # (including blank lines) to Console.width with spaces.  When the
        # prompt_toolkit output pane is narrower, wrap_lines=True wraps each
        # padded line, creating visual double-spacing.  Stripping trailing
        # spaces lets prompt_toolkit handle wrapping at the actual pane width.
        text = text.replace("\r\n", "\n")
        text = re.sub(r" +$", "", text, flags=re.MULTILINE)
        from prompt_toolkit.formatted_text import ANSI, to_formatted_text

        fragments = _strip_bg(list(to_formatted_text(ANSI(text))))  # type: ignore[arg-type]
        self._output.append(fragments)
        self._invalidate()

    @property
    def encoding(self) -> str:
        return "utf-8"

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        import io

        raise io.UnsupportedOperation("OutputPaneWriter has no file descriptor")


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
            # Dialog overlay
            "dialog.frame": "bg:#1e1e2e",
            "dialog.title": "bg:#1e1e2e #C5A059 bold",
            "dialog.body": "bg:#1e1e2e #d0d0d0",
            "dialog.input": "bg:#2a2a3e #e0e0e0",
            "dialog.input.prompt": "bg:#2a2a3e #C5A059",
            "dialog.hint": "bg:#1e1e2e #555568",
            "dialog.option": "bg:#1e1e2e #94A3B8",
            "dialog.option.key": "bg:#1e1e2e #C5A059 bold",
            "dialog.shadow": "bg:#0d0d18",
            # Picker overlay
            "picker.frame": "bg:#1a1a2e #e0e0e0",
            "picker.title": "bg:#C5A059 #1a1a2e bold",
            "picker.border": "#3a3a4e",
            "picker.hint": "bg:#1a1a2e #6b7280",
            "picker.separator": "#3a3a4e",
            "picker.shadow": "bg:#0a0a15",
            "picker.list": "bg:#1a1a2e",
            "picker.list.selected": "bg:#2a2a3e #C5A059 bold",
            "picker.list.selected-meta": "bg:#2a2a3e #94A3B8",
            "picker.list.item": "#e0e0e0",
            "picker.list.meta": "#6b7280",
            "picker.list.badge": "#C5A059 italic",
            "picker.preview": "bg:#1a1a2e #e0e0e0",
            "picker.preview.role-user": "#C5A059 bold",
            "picker.preview.role-ai": "#94A3B8 bold",
            "picker.preview.content": "#e0e0e0",
            "picker.preview.empty": "#6b7280 italic",
            # Turn separators
            "turn.user": "#94A3B8 bold",
            "turn.ai": "#C5A059 bold",
            "turn.user.text": "#94A3B8",
            # Tool call blocks — intentionally dimmer than main text
            "tool.frame": "#3a3a4e",
            "tool.name": "#8b8b8b bold",
            "tool.arg": "#6b7280",
            "tool.ok": "#4a8a6a",
            "tool.err": "#b05555",
            "tool.elapsed": "#6b7280",
            "tool.detail": "#6b7280",
            # Scroll indicator
            "scroll.indicator": "#C5A059",
            "select.indicator": "#CD6B6B bold",
            # Approval mode prompts
            "prompt.auto": "#C5A059 bold",
            "prompt.safe": "#4EC9B0 bold",
            "prompt.caution": "#569CD6 bold",
            "prompt.strict": "#CD6B6B bold",
            # Streaming cursor
            "streaming.cursor": "#C5A059",
            # Plan checklist
            "plan.header": "#C5A059 bold",
            "plan.pending": "#6b7280",
            "plan.active": "#e8b830",
            "plan.complete": "#4a8a6a",
            "plan.failed": "#b05555",
        }
    )

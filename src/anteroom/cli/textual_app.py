from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Protocol

from prompt_toolkit.history import FileHistory
from rich.console import Group
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Collapsible, Footer, Input, Markdown, Static, TextArea

from ..services import storage
from ..services.agent_loop import AgentEvent, _build_compaction_history, run_agent_loop
from ..services.context_trust import sanitize_trust_tags, wrap_untrusted
from ..services.rewind import collect_file_paths
from ..services.rewind import rewind_conversation as rewind_service
from .commands import (
    CommandContext,
    CommandResult,
    build_help_markdown,
    build_skills_markdown,
    build_tools_markdown,
    execute_slash_command,
    parse_slash_command,
)
from .instructions import discover_conventions
from .plan import (
    PLAN_MODE_ALLOWED_TOOLS,
    build_planning_system_prompt,
    get_plan_file_path,
    parse_plan_command,
    read_plan,
)

logger = logging.getLogger(__name__)

_STREAM_STALL_SECS = 2.0
_SPINNER_FRAMES = ("◜", "◠", "◝", "◞", "◡", "◟")


class TextualChatBackend(Protocol):
    async def load_history(self) -> list[tuple[str, str]]: ...

    async def submit_turn(self, prompt: str) -> AsyncGenerator[AgentEvent, None]: ...

    def cancel_current_turn(self) -> None: ...


@dataclass
class SessionSnapshot:
    model: str
    working_dir: str
    tool_count: int
    instructions_loaded: bool
    plan_mode: bool = False
    git_branch: str | None = None
    version: str | None = None
    skill_count: int = 0
    pack_count: int = 0


@dataclass
class BoardRow:
    status: str
    text: str
    badges: list[str] = field(default_factory=list)


@dataclass
class ToolRow:
    id: int
    tool_name: str
    category: str
    badge: str
    text: str
    status: str = "active"


_PROCEDURE_COPY: dict[str, str] = {
    "search": "I'm identifying the files and symbols that matter.",
    "read": "I'm reading the relevant material closely.",
    "command": "I'm verifying the result before I trust it.",
    "edit": "I'm making the change carefully and checking the shape of it.",
    "question": "I need one more decision before I continue.",
    "model": "I'm synthesizing the findings into an answer.",
    "model_direct": "I'm answering directly from what we already know.",
    "orient_model": "I'm orienting to the request before I act.",
}


def _one_line(value: Any, *, limit: int = 72) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ")
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _strip_regex_noise(pattern: str) -> str:
    cleaned = pattern.replace("\\b", "").replace("\\s+", " ").strip()
    return cleaned or pattern


def _pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, default=str)


def _summarize_tool_output(output: Any) -> str:
    if isinstance(output, dict):
        if "error" in output:
            return str(output["error"])[:500]
        if "content" in output:
            return str(output["content"])[:500]
        if "stdout" in output:
            return str(output["stdout"])[:500]
        return _pretty_json(output)[:500]
    return str(output)[:500]


def _shape_transcript_markdown(text: str) -> str:
    return _soften_transcript_markdown(_flatten_transcript_headings(text))


def _flatten_transcript_headings(text: str) -> str:
    lines = text.splitlines()
    flattened: list[str] = []
    in_fence = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            flattened.append(line)
            continue
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            if level and len(stripped) > level and stripped[level] == " ":
                heading = stripped[level + 1 :].strip()
                if heading:
                    if flattened and flattened[-1].strip():
                        flattened.append("")
                    flattened.append(heading.upper() if level <= 2 else heading)
                    flattened.append("")
                    continue
        flattened.append(line)
    return "\n".join(flattened)


def _soften_transcript_markdown(text: str) -> str:
    lines = text.splitlines()
    softened: list[str] = []
    in_fence = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            if softened and softened[-1].strip():
                softened.append("")
            continue
        if in_fence:
            softened.append(line)
            continue
        softened.append(_replace_transcript_inline_markdown(line))
    return "\n".join(softened)


def _replace_transcript_inline_markdown(line: str) -> str:
    line = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", line)
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
    line = re.sub(r"`([^`]+)`", r"\1", line)
    line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
    line = re.sub(r"__([^_]+)__", r"\1", line)
    line = re.sub(r"~~([^~]+)~~", r"\1", line)
    return line


def _tool_category(tool_name: str) -> str:
    name = tool_name.lower()
    if any(part in name for part in ("grep", "glob", "search", "find", "query", "list")):
        return "search"
    if any(part in name for part in ("read", "open", "view")):
        return "read"
    if any(part in name for part in ("bash", "exec", "command", "python")):
        return "command"
    if any(part in name for part in ("write", "edit", "patch", "apply")):
        return "edit"
    if any(part in name for part in ("ask", "request_user_input")):
        return "question"
    return "command"


def _tool_badge(tool_name: str, category: str) -> str:
    name = tool_name.lower()
    if "grep" in name:
        return "grep"
    if "glob" in name:
        return "glob"
    if "read" in name or "open" in name or "view" in name:
        return "read"
    if "bash" in name or "exec" in name or "python" in name:
        return "bash"
    if "write" in name:
        return "write"
    if "edit" in name or "patch" in name or "apply" in name:
        return "edit"
    if "ask" in name:
        return "ask"
    return category


def _tool_summary(tool_name: str, arguments: dict[str, Any]) -> tuple[str, str, str]:
    category = _tool_category(tool_name)
    badge = _tool_badge(tool_name, category)
    if category == "search":
        path = arguments.get("path") or arguments.get("cwd") or arguments.get("root") or arguments.get("directory")
        pattern = arguments.get("pattern") or arguments.get("query") or arguments.get("glob")
        if path and pattern:
            return (
                category,
                badge,
                f'Searching {_one_line(path, limit=28)} for "{_one_line(_strip_regex_noise(pattern), limit=28)}"',
            )
        if pattern:
            return category, badge, f'Searching for "{_one_line(_strip_regex_noise(pattern), limit=40)}"'
        if path:
            return category, badge, f"Searching {_one_line(path, limit=48)}"
        return category, badge, f"Searching with {tool_name}"
    if category == "read":
        paths = arguments.get("paths")
        if isinstance(paths, list) and paths:
            first = _one_line(paths[0], limit=30)
            if len(paths) == 1:
                return category, badge, f"Reading {first}"
            return category, badge, f"Reading {first} and {len(paths) - 1} more file(s)"
        path = arguments.get("path") or arguments.get("file") or arguments.get("filepath")
        if path:
            return category, badge, f"Reading {_one_line(path, limit=52)}"
        return category, badge, f"Reading with {tool_name}"
    if category == "command":
        command = arguments.get("command") or arguments.get("cmd") or arguments.get("script")
        if command:
            return category, badge, f"Running {_one_line(command, limit=56)}"
        return category, badge, f"Running {tool_name}"
    if category == "edit":
        path = arguments.get("path") or arguments.get("file") or arguments.get("target")
        if path:
            return category, badge, f"Updating {_one_line(path, limit=52)}"
        return category, badge, f"Updating with {tool_name}"
    if category == "question":
        question = arguments.get("question") or arguments.get("prompt")
        if question:
            return category, badge, f"Asking: {_one_line(question, limit=52)}"
        return category, badge, "Waiting for your decision"
    return category, badge, f"Using {tool_name}"


class PaneTextArea(TextArea):
    def __init__(self, empty_message: str, *, id: str | None = None) -> None:
        super().__init__(
            "",
            id=id,
            read_only=True,
            soft_wrap=True,
            show_cursor=False,
            compact=True,
            highlight_cursor_line=False,
        )
        self.empty_message = empty_message
        self.add_class("pane-text")
        self.load_text(empty_message)

    @property
    def plain_text(self) -> str:
        return self.text

    def set_plain_text(self, text: str) -> None:
        self.load_text(text or self.empty_message)


class BoardWidget(PaneTextArea):
    def __init__(self, empty_message: str, *, id: str | None = None) -> None:
        super().__init__(empty_message, id=id)
        self.rows: list[BoardRow] = []
        self.spinner_index = 0
        self.add_class("board-body")
        self.set_rows([])

    @property
    def plain_text(self) -> str:
        if not self.rows:
            return self.empty_message
        lines = []
        for row in self.rows:
            marker = {
                "active": _SPINNER_FRAMES[self.spinner_index % len(_SPINNER_FRAMES)],
                "complete": "✓",
                "error": "!",
            }.get(row.status, _SPINNER_FRAMES[self.spinner_index % len(_SPINNER_FRAMES)])
            badges = "".join(f" [{badge}]" for badge in row.badges)
            lines.append(f"{marker} {row.text}{badges}")
        return "\n".join(lines)

    def set_rows(self, rows: list[BoardRow], *, spinner_index: int | None = None) -> None:
        self.rows = list(rows)
        if spinner_index is not None:
            self.spinner_index = spinner_index
        self.set_plain_text(self.plain_text)


def _escape_transcript_markdown(text: str) -> str:
    return text.replace("\\", "\\\\").replace("`", "\\`").replace("*", "\\*").replace("_", "\\_").replace("#", "\\#")


def _quote_markdown_block(text: str) -> str:
    lines = text.splitlines() or [text]
    quoted = [f"> {line}" if line.strip() else ">" for line in lines]
    return "\n".join(quoted)


class TranscriptPane(Markdown):
    def __init__(self, empty_message: str, *, id: str | None = None) -> None:
        super().__init__(empty_message, id=id, open_links=False)
        self.empty_message = empty_message
        self.read_only = True
        self.text = empty_message
        self.add_class("transcript-markdown")

    async def set_markdown_text(self, text: str) -> None:
        self.text = text or self.empty_message
        await self.update(self.text)


class Composer(TextArea):
    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(
            "",
            id=id,
            soft_wrap=True,
            show_line_numbers=False,
            compact=True,
            highlight_cursor_line=False,
            tab_behavior="indent",
            placeholder="Ask for the next step...  Enter to send, Alt+Enter or Ctrl+J for newline",
        )
        self.add_class("composer-textarea")

    def on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            self.app.call_later(self.app.action_submit_composer)
            event.prevent_default()
            event.stop()
            return
        browsing_history = getattr(self.app, "_history_index", None) is not None
        if event.key == "up" and (self.cursor_location[0] == 0 or browsing_history):
            self.app.call_later(self.app.action_history_previous)
            event.prevent_default()
            event.stop()
            return
        if event.key == "down" and (self.cursor_location[0] >= self.text.count("\n") or browsing_history):
            self.app.call_later(self.app.action_history_next)
            event.prevent_default()
            event.stop()
            return
        if event.key in {"shift+enter", "alt+enter", "ctrl+j"}:
            self.insert("\n")
            self.app.call_later(self.app.action_reset_history_navigation)
            event.prevent_default()
            event.stop()
            return
        if event.key == "ctrl+c":
            self.load_text("")
            self.app.call_later(self.app.action_reset_history_navigation)
            event.prevent_default()
            event.stop()
            return
        if event.character or event.key in {"backspace", "delete"}:
            self.app.call_later(self.app.action_reset_history_navigation)


class ApprovalScreen(ModalScreen[str | None]):
    CSS = """
    ApprovalScreen {
        align: center middle;
        background: rgba(3, 6, 10, 0.78);
    }
    #approval-dialog {
        width: 88;
        max-width: 92%;
        border: round #355074;
        background: #0B121A;
        padding: 1 2;
    }
    .dialog-title {
        color: #CDA45A;
        text-style: bold;
        margin-bottom: 1;
    }
    .dialog-copy {
        color: #F5F2EA;
        margin-bottom: 1;
    }
    .dialog-detail {
        color: #9FB9E8;
        margin-bottom: 1;
    }
    .dialog-actions {
        height: auto;
    }
    .dialog-actions Button {
        margin-right: 1;
    }
    """

    def __init__(self, verdict: Any) -> None:
        super().__init__()
        self.verdict = verdict

    def compose(self) -> ComposeResult:
        detail = self.verdict.details.get("command") or self.verdict.details.get("path") or ""
        with Container(id="approval-dialog"):
            yield Static("Approval Needed", classes="dialog-title")
            yield Static(self.verdict.reason, classes="dialog-copy")
            if detail:
                yield Static(_one_line(detail, limit=120), classes="dialog-detail")
            with Horizontal(classes="dialog-actions"):
                yield Button("Allow Once", id="once", variant="primary")
                yield Button("This Session", id="session")
                yield Button("Always", id="always")
                yield Button("Deny", id="deny", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        choice = {
            "once": "yes",
            "session": "session",
            "always": "always",
            "deny": "deny",
        }.get(event.button.id or "", "deny")
        self.dismiss(choice)


class QuestionScreen(ModalScreen[str]):
    CSS = """
    QuestionScreen {
        align: center middle;
        background: rgba(3, 6, 10, 0.78);
    }
    #question-dialog {
        width: 92;
        max-width: 92%;
        border: round #355074;
        background: #0B121A;
        padding: 1 2;
    }
    """

    def __init__(self, question: str, options: list[str] | None) -> None:
        super().__init__()
        self.question = question
        self.options = options or []

    def compose(self) -> ComposeResult:
        with Container(id="question-dialog"):
            yield Static("Question", classes="dialog-title")
            yield Static(self.question, classes="dialog-copy")
            if self.options:
                for index, option in enumerate(self.options, start=1):
                    yield Button(f"{index}. {option}", id=f"option-{index}", classes="question-option")
                yield Button("Cancel", id="cancel", variant="error")
            else:
                yield Input(placeholder="Type your answer", id="question-input")
                with Horizontal(classes="dialog-actions"):
                    yield Button("Submit", id="submit", variant="primary")
                    yield Button("Cancel", id="cancel", variant="error")

    def on_mount(self) -> None:
        if not self.options:
            self.query_one("#question-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "cancel":
            self.dismiss("")
            return
        if button_id == "submit":
            answer = self.query_one("#question-input", Input).value.strip()
            self.dismiss(answer)
            return
        if button_id.startswith("option-"):
            index = int(button_id.split("-")[1]) - 1
            if 0 <= index < len(self.options):
                self.dismiss(self.options[index])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "question-input":
            self.dismiss(event.value.strip())


class SessionBanner(Static):
    def __init__(self, snapshot: SessionSnapshot) -> None:
        super().__init__("")
        self.snapshot = snapshot
        self.add_class("session-banner")
        self.update(self._render_banner())

    def update_snapshot(self, snapshot: SessionSnapshot) -> None:
        self.snapshot = snapshot
        self.update(self._render_banner())

    def _render_banner(self) -> Group:
        lead = ["ANTEROOM"]
        if self.snapshot.version:
            lead.append(self.snapshot.version)
        lead.append(self.snapshot.model)
        lead.append(f"{self.snapshot.tool_count} tools")
        if self.snapshot.instructions_loaded:
            lead.append("instructions loaded")
        if self.snapshot.plan_mode:
            lead.append("plan mode")
        line_one = Text("  ".join(lead), style="#B7B0A1")
        extras = [self.snapshot.working_dir]
        if self.snapshot.git_branch:
            extras.append(self.snapshot.git_branch)
        if self.snapshot.skill_count:
            extras.append(f"{self.snapshot.skill_count} skills")
        if self.snapshot.pack_count:
            extras.append(f"{self.snapshot.pack_count} packs")
        line_two = Text("  ".join(extras), style="#7E8799")
        return Group(line_one, line_two)


class TextualChatApp(App[None]):
    CSS = """
    Screen {
        background: #05080D;
        color: #F5F2EA;
    }

    #layout {
        height: 1fr;
        padding: 1 1 0 1;
    }

    #status-strip {
        border: round #203247;
        background: #09111A;
        height: auto;
        padding: 0 1;
        margin-bottom: 1;
    }

    #main-grid {
        height: 1fr;
        min-height: 0;
    }

    #transcript-panel, .board-panel, #composer-row {
        border: round #22364D;
        background: #09111A;
    }

    #transcript-panel {
        width: 4fr;
        height: 1fr;
        margin-right: 1;
        padding: 1 1 0 1;
        border: tall #294361;
    }

    #sidebar {
        width: 52;
        height: 1fr;
        min-height: 0;
    }

    .board-panel {
        height: 1fr;
        min-height: 0;
        padding: 1;
        margin-bottom: 1;
        border: round #1E3148;
    }

    #trace-collapsible {
        border: round #1B2B3E;
        background: #071018;
        padding: 0 1;
    }

    #trace-collapsible > Contents {
        padding-top: 1;
    }

    .panel-title {
        color: #CDA45A;
        text-style: bold;
        margin-bottom: 1;
    }

    .session-banner {
        height: auto;
    }

    #transcript-scroll {
        height: 1fr;
        min-height: 0;
        background: #0C1621;
        border: none;
        scrollbar-color: #3A5A80;
        scrollbar-color-hover: #5D84B6;
    }

    .pane-text {
        width: 1fr;
        height: 1fr;
        background: #0C1621;
        border: none;
        color: #F5F2EA;
        padding: 0 1 1 1;
        scrollbar-color: #3A5A80;
        scrollbar-color-hover: #5D84B6;
    }

    .transcript-markdown {
        width: 1fr;
        height: auto;
        min-height: 1;
        border: none;
        padding: 0 1 1 1;
    }

    .transcript-markdown MarkdownH3 {
        color: #F3E1A2;
        text-style: bold;
        margin-top: 1;
        margin-bottom: 1;
    }

    .transcript-markdown MarkdownH6 {
        color: #86A9D8;
        text-style: bold;
        margin-top: 1;
        margin-bottom: 1;
    }

    .transcript-markdown MarkdownParagraph {
        margin-bottom: 1;
    }

    .transcript-markdown MarkdownBlockQuote {
        background: #0E1824;
        border-left: heavy #3A5A80;
        color: #D7DDE7;
        margin: 0 0 1 0;
        padding: 0 1;
    }

    .transcript-markdown MarkdownBulletList,
    .transcript-markdown MarkdownOrderedList {
        margin-bottom: 1;
    }

    .transcript-markdown MarkdownHorizontalRule {
        color: #203247;
        margin: 1 0;
    }

    .transcript-markdown MarkdownFence {
        background: #081019;
        border: round #21344A;
        padding: 0 1;
    }

    #procedure-panel {
        height: 3fr;
        background: #0B141E;
    }

    #tools-panel {
        height: 2fr;
        background: #0A121A;
    }

    #composer-row {
        height: auto;
        min-height: 7;
        padding: 1;
        margin: 1 0 1 0;
        background: #09111A;
    }

    #composer {
        width: 1fr;
        height: 5;
        margin-right: 1;
    }

    #send {
        height: 3;
        min-width: 12;
        background: #3A6FB1;
        color: #F5F2EA;
        border: none;
    }

    .composer-textarea {
        border: none;
        background: #0C1621;
        color: #F5F2EA;
        padding: 0 1;
        scrollbar-color: #3A5A80;
        scrollbar-color-hover: #5D84B6;
    }
    """

    BINDINGS = [
        ("escape", "cancel_turn", "Cancel"),
        ("ctrl+enter", "submit_composer", "Send"),
        ("meta+enter", "submit_composer", "Send"),
        ("meta+c", "copy_selection", "Copy"),
        ("ctrl+shift+c", "copy_selection", "Copy"),
        ("ctrl+l", "focus_composer", "Composer"),
        ("ctrl+1", "focus_transcript", "Transcript"),
        ("ctrl+2", "focus_working", "Working"),
        ("ctrl+3", "focus_tools", "Tools"),
        ("ctrl+4", "focus_trace", "Trace"),
        ("ctrl+q", "quit_app", "Quit"),
        ("ctrl+t", "toggle_trace", "Trace"),
    ]

    def __init__(
        self,
        *,
        backend: TextualChatBackend,
        session: SessionSnapshot,
        initial_prompt: str | None = None,
        ui_bridge: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.backend = backend
        self.session = session
        self.initial_prompt = initial_prompt
        self.ui_bridge = ui_bridge
        self.busy = False
        self._turn_task: asyncio.Task[None] | None = None
        self._assistant_index: int | None = None
        self._transcript_entries: list[tuple[str, str]] = []
        self._transcript_flush_task: asyncio.Task[None] | None = None
        self._procedure_rows: dict[str, BoardRow] = {}
        self._procedure_order: list[str] = []
        self._tool_rows: list[ToolRow] = []
        self._tool_id = 0
        self._trace_lines: list[str] = []
        self._turn_started_at: float | None = None
        self._stream_event_count = 0
        self._stream_token_count = 0
        self._stream_char_count = 0
        self._last_event_kind: str | None = None
        self._last_event_at: float | None = None
        self._last_token_at: float | None = None
        self._assistant_message_seen = False
        self._stream_stall_visible = False
        self._spinner_index = 0
        self._prompt_history: list[str] = []
        self._history_index: int | None = None
        self._history_draft = ""
        self._last_auto_copied_selection: str | None = None
        self._transcript_auto_follow = True
        self._heartbeat = None
        self._initial_prompt_task: asyncio.Task[None] | None = None
        self.title = "Anteroom"
        self.sub_title = "Procedure-first developer UI"

    class TurnFinished(Message):
        def __init__(self) -> None:
            super().__init__()

    def compose(self) -> ComposeResult:
        with Container(id="layout"):
            with Container(id="status-strip"):
                yield SessionBanner(self.session)
            with Horizontal(id="main-grid"):
                with Vertical(id="transcript-panel"):
                    yield Static("TRANSCRIPT", classes="panel-title")
                    with VerticalScroll(id="transcript-scroll"):
                        yield TranscriptPane(
                            "Start the conversation and the record will appear here.", id="transcript-pane"
                        )
                with Vertical(id="sidebar"):
                    with Container(id="procedure-panel", classes="board-panel"):
                        yield Static("WORKING", classes="panel-title")
                        yield BoardWidget("Waiting for the next procedure.", id="procedure-board")
                    with Container(id="tools-panel", classes="board-panel"):
                        yield Static("TOOLS", classes="panel-title")
                        yield BoardWidget(
                            "The instruments will appear when the agent reaches for them.", id="tool-board"
                        )
                    with Collapsible(title="TRACE", collapsed=True, id="trace-collapsible"):
                        yield BoardWidget("Trace is empty.", id="trace-board")
            with Horizontal(id="composer-row"):
                yield Composer(id="composer")
                yield Button("Send", variant="primary", id="send")
        yield Footer()

    async def on_mount(self) -> None:
        if self.ui_bridge is not None:
            self.ui_bridge["ask"] = self._ask_user_dialog
            self.ui_bridge["confirm"] = self._confirm_dialog
        self._heartbeat = self.set_interval(0.5, self._on_stream_heartbeat)
        history = await self.backend.load_history()
        for role, content in history:
            if role not in ("user", "assistant"):
                continue
            await self._append_bubble(role, content)
        self._prompt_history = await self._load_prompt_history()
        self.call_after_refresh(self._focus_composer)
        if self.initial_prompt:
            self.call_after_refresh(self._schedule_initial_prompt)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send":
            await self._submit_from_input()

    async def action_submit_composer(self) -> None:
        await self._submit_from_input()

    def action_focus_composer(self) -> None:
        self._focus_composer()

    def action_focus_transcript(self) -> None:
        self.query_one("#transcript-pane", TranscriptPane).focus()

    def action_focus_working(self) -> None:
        self.query_one("#procedure-board", BoardWidget).focus()

    def action_focus_tools(self) -> None:
        self.query_one("#tool-board", BoardWidget).focus()

    def action_focus_trace(self) -> None:
        trace = self.query_one("#trace-collapsible", Collapsible)
        trace.collapsed = False
        self.query_one("#trace-board", BoardWidget).focus()

    async def action_history_previous(self) -> None:
        self._browse_history(1)

    async def action_history_next(self) -> None:
        self._browse_history(-1)

    async def action_reset_history_navigation(self) -> None:
        self._reset_history_navigation()

    async def on_textual_chat_app_turn_finished(self, _: TurnFinished) -> None:
        self._assistant_index = None
        self.busy = False
        await self._flush_transcript_now()
        try:
            composer = self.query_one("#composer", Composer)
            send = self.query_one("#send", Button)
        except NoMatches:
            return
        composer.disabled = False
        send.disabled = False
        composer.focus()

    async def action_cancel_turn(self) -> None:
        if self.busy:
            self.backend.cancel_current_turn()

    def action_quit_app(self) -> None:
        self.exit()

    def _schedule_initial_prompt(self) -> None:
        prompt = (self.initial_prompt or "").strip()
        if not prompt or self.busy:
            return
        self.initial_prompt = None
        self._initial_prompt_task = asyncio.create_task(self._submit_prompt(prompt))
        self._initial_prompt_task.add_done_callback(self._log_initial_prompt_result)

    def _log_initial_prompt_result(self, task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Initial Textual prompt failed")

    def action_toggle_trace(self) -> None:
        trace = self.query_one("#trace-collapsible", Collapsible)
        trace.collapsed = not trace.collapsed

    def action_copy_selection(self) -> None:
        selected_text = self.screen.get_selected_text()
        if selected_text:
            self.copy_to_clipboard(selected_text)
            return
        focused = self.screen.focused
        widgets: list[Widget] = []
        widgets.extend(self.screen.selections.keys())
        if focused is not None:
            widgets.append(focused)
        widgets.extend(
            [
                self.query_one("#transcript-pane", TranscriptPane),
                self.query_one("#procedure-board", BoardWidget),
                self.query_one("#tool-board", BoardWidget),
                self.query_one("#trace-board", BoardWidget),
                self.query_one("#composer", Composer),
            ]
        )
        seen: set[int] = set()
        for widget in widgets:
            marker = id(widget)
            if marker in seen:
                continue
            seen.add(marker)
            selected_text = self._selected_text_for_widget(widget)
            if selected_text:
                self.copy_to_clipboard(selected_text)
                return

    async def on_mouse_up(self, event: events.MouseUp) -> None:
        if not self._should_auto_copy_selection():
            return
        selected_text = self.screen.get_selected_text()
        if not selected_text:
            self._last_auto_copied_selection = None
            return
        if selected_text == self._last_auto_copied_selection:
            return
        if not self._selection_is_from_display_surface():
            return
        self.copy_to_clipboard(selected_text)
        self._last_auto_copied_selection = selected_text

    async def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if self._event_targets_transcript(event):
            self._transcript_auto_follow = False

    async def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if self._event_targets_transcript(event):
            self.call_after_refresh(self._maybe_resume_transcript_follow)

    @staticmethod
    def _selected_text_for_widget(widget: Widget) -> str | None:
        if isinstance(widget, TextArea):
            return widget.selected_text or None
        selection = getattr(widget, "text_selection", None)
        get_selection = getattr(widget, "get_selection", None)
        if selection is not None and callable(get_selection):
            extracted = get_selection(selection)
            if extracted is None:
                return None
            text, _ending = extracted
            return text or None
        return None

    @staticmethod
    def _should_auto_copy_selection() -> bool:
        return os.environ.get("TERM_PROGRAM") == "WarpTerminal"

    def _selection_is_from_display_surface(self) -> bool:
        surface_roots = [
            self.query_one("#transcript-pane", TranscriptPane),
            self.query_one("#procedure-board", BoardWidget),
            self.query_one("#tool-board", BoardWidget),
            self.query_one("#trace-board", BoardWidget),
        ]
        copyable_widgets: set[int] = set()
        for root in surface_roots:
            copyable_widgets.add(id(root))
            copyable_widgets.update(id(child) for child in root.query("*").results())
        return any(id(widget) in copyable_widgets for widget in self.screen.selections)

    async def _submit_from_input(self) -> None:
        composer = self.query_one("#composer", Composer)
        prompt = composer.text.strip()
        if not prompt or self.busy:
            return
        composer.load_text("")
        self._reset_history_navigation()
        await self._record_prompt_history(prompt)
        if await self._maybe_handle_slash_command(prompt):
            return
        await self._submit_prompt(prompt)

    async def _submit_prompt(self, prompt: str, *, display_prompt: str | None = None) -> None:
        self.busy = True
        self._reset_turn_state()
        self._turn_started_at = time.monotonic()
        composer = self.query_one("#composer", Composer)
        send = self.query_one("#send", Button)
        composer.disabled = True
        send.disabled = True
        visible_prompt = display_prompt or prompt
        await self._append_bubble("user", visible_prompt)
        self._trace(f"user_prompt {visible_prompt}")
        self._turn_task = asyncio.create_task(self._run_turn(prompt))

    async def _run_turn(self, prompt: str) -> None:
        queue: asyncio.Queue[AgentEvent | BaseException | None] = asyncio.Queue()

        async def _produce_events() -> None:
            try:
                async for event in self.backend.submit_turn(prompt):
                    await queue.put(event)
            except BaseException as exc:  # pragma: no cover - exercised via outer handler
                await queue.put(exc)
            finally:
                await queue.put(None)

        producer = asyncio.create_task(_produce_events())
        try:
            pending: AgentEvent | BaseException | None = None
            while True:
                item = pending if pending is not None else await queue.get()
                pending = None

                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                if item.kind == "token":
                    combined = item.data.get("content", "")
                    while True:
                        try:
                            next_item = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        if next_item is None or isinstance(next_item, BaseException):
                            pending = next_item
                            break
                        if next_item.kind == "token":
                            combined += next_item.data.get("content", "")
                            continue
                        pending = next_item
                        break
                    item = AgentEvent(kind="token", data={"content": combined})

                await self._handle_event(item)
                await asyncio.sleep(0)
        except Exception as exc:
            logger.exception("Textual turn failed")
            self._set_recovery_row(
                "blocked",
                "The turn failed before I could finish safely.",
                status="error",
                badges=["model error"],
            )
            await self._ensure_assistant_bubble()
            assert self._assistant_index is not None
            self._transcript_entries[self._assistant_index] = ("assistant", f"Error: {exc}")
            await self._refresh_transcript()
            self._trace(f"fatal_error {exc}")
        finally:
            if not producer.done():
                producer.cancel()
                try:
                    await producer
                except asyncio.CancelledError:
                    pass
            self.post_message(self.TurnFinished())

    async def _handle_event(self, event: AgentEvent) -> None:
        kind = event.kind
        data = event.data
        self._record_stream_event(event)
        self._trace_event(event)

        if kind == "thinking":
            if not self._procedure_rows:
                self._set_procedure_row(
                    "orient",
                    _PROCEDURE_COPY["orient_model"]
                    if not self._tool_rows
                    else "I'm orienting to the request before I move.",
                    status="active",
                    badges=self._status_badges(default="model"),
                )
            return
        if kind == "phase":
            phase = _one_line(data.get("phase", ""), limit=48)
            if phase:
                if phase.lower().startswith("waiting"):
                    if (
                        self._tool_rows
                        or "search" in self._procedure_rows
                        or "read" in self._procedure_rows
                        or "model" in self._procedure_rows
                    ):
                        return
                    text = _PROCEDURE_COPY["orient_model"]
                else:
                    text = f"I'm {phase.lower()}."
                self._set_procedure_row("orient", text, status="active", badges=self._status_badges(default="model"))
            return
        if kind == "retrying":
            if self._assistant_index is not None and not self._assistant_message_seen and self._stream_char_count > 0:
                self._assistant_index = None
            self._set_recovery_row(
                "retry",
                "I lost the response once, so I'm retrying from verified context.",
                status="active",
                badges=["retry"],
            )
            return
        if kind == "tool_call_start":
            self._drop_procedure_row("orient")
            self._start_tool(data.get("tool_name", "tool"), data.get("arguments", {}) or {})
            return
        if kind == "tool_call_end":
            self._finish_tool(
                data.get("tool_name", "tool"),
                data.get("status", "success"),
                data.get("output"),
            )
            return
        if kind == "token":
            content = data.get("content", "")
            model_copy = _PROCEDURE_COPY["model"] if self._tool_rows else _PROCEDURE_COPY["model_direct"]
            self._drop_procedure_row("orient")
            self._set_procedure_row("model", model_copy, status="active", badges=["model"])
            await self._append_assistant_chunk(content)
            return
        if kind == "assistant_message":
            content = data.get("content", "")
            if content:
                self._drop_procedure_row("orient")
                self._complete_active_rows()
                self._set_procedure_row(
                    "model",
                    "I'm ready to answer with verified findings.",
                    status="complete",
                    badges=["done"],
                )
                await self._set_assistant_message(content)
            return
        if kind in {"error", "dlp_blocked", "output_filter_blocked", "injection_detected"}:
            message = _one_line(data.get("message") or data.get("detail") or "A blocking error occurred.", limit=72)
            if self._assistant_index is not None and not self._assistant_message_seen and self._stream_char_count > 0:
                await self._mark_partial_assistant_interrupted(message)
            self._set_recovery_row("error", message, status="error", badges=["model error"])
            return
        if kind in {"dlp_warning", "output_filter_warning", "budget_warning"}:
            message = _one_line(data.get("message") or "I hit a warning and adjusted the plan.", limit=72)
            self._set_recovery_row("warning", message, status="error", badges=["warning"])
            return
        if kind == "done":
            self._complete_active_rows()
            if "model" in self._procedure_rows and self._procedure_rows["model"].status == "active":
                self._set_procedure_row(
                    "model",
                    "I'm ready to answer with verified findings.",
                    status="complete",
                    badges=["done"],
                )
            self._refresh_boards()

    def _start_tool(self, tool_name: str, arguments: dict[str, Any]) -> None:
        category, badge, summary = _tool_summary(tool_name, arguments)
        self._tool_id += 1
        self._tool_rows.append(
            ToolRow(id=self._tool_id, tool_name=tool_name, category=category, badge=badge, text=summary)
        )

        count = sum(1 for row in self._tool_rows if row.category == category and row.status == "active")
        badges = [badge if count == 1 else f"{badge} x{count}"]
        self._set_procedure_row(
            category,
            _PROCEDURE_COPY.get(category, "I'm using the right tool for this step."),
            status="active",
            badges=badges,
        )

    def _finish_tool(self, tool_name: str, status: str, output: Any) -> None:
        matched: ToolRow | None = None
        for row in reversed(self._tool_rows):
            if row.tool_name == tool_name and row.status == "active":
                matched = row
                break
        if matched is None:
            return
        matched.status = "complete" if status == "success" else "error"
        active_remaining = sum(
            1 for row in self._tool_rows if row.category == matched.category and row.status == "active"
        )
        category_badge = matched.badge if active_remaining <= 1 else f"{matched.badge} x{active_remaining}"
        if status == "success":
            state = "active" if active_remaining else "complete"
            badges = [category_badge] if active_remaining else [matched.badge]
            self._set_procedure_row(
                matched.category,
                _PROCEDURE_COPY.get(matched.category, "I'm using the right tool for this step."),
                status=state,
                badges=badges,
            )
        else:
            self._set_recovery_row(
                "tool-error",
                f"{matched.text} failed, so I'm adjusting before I continue.",
                status="error",
                badges=["tool error"],
            )
            if active_remaining == 0 and matched.category in self._procedure_rows:
                self._procedure_rows[matched.category].status = "complete"
        if output and status != "success":
            self._trace(f"tool_error_output {output}")
        self._refresh_boards()

    async def _append_bubble(self, role: str, content: str) -> None:
        self._transcript_entries.append((role, content))
        await self._flush_transcript_now()

    async def _ensure_assistant_bubble(self) -> None:
        if self._assistant_index is None:
            self._transcript_entries.append(("assistant", ""))
            self._assistant_index = len(self._transcript_entries) - 1
            await self._flush_transcript_now()

    async def _append_assistant_chunk(self, chunk: str) -> None:
        await self._ensure_assistant_bubble()
        role, text = self._transcript_entries[self._assistant_index]
        self._transcript_entries[self._assistant_index] = (role, text + chunk)
        self._queue_transcript_flush()

    async def _set_assistant_message(self, content: str) -> None:
        await self._ensure_assistant_bubble()
        self._transcript_entries[self._assistant_index] = ("assistant", content)
        await self._flush_transcript_now()

    async def _mark_partial_assistant_interrupted(self, reason: str) -> None:
        if self._assistant_index is None:
            return
        role, text = self._transcript_entries[self._assistant_index]
        if not text.strip():
            return
        note = f"\n\n[stream interrupted: {reason}]"
        if note.strip() in text:
            return
        self._transcript_entries[self._assistant_index] = (role, text.rstrip() + note)
        await self._flush_transcript_now()

    def _set_procedure_row(self, key: str, text: str, *, status: str, badges: list[str]) -> None:
        if key not in self._procedure_rows:
            self._procedure_order.append(key)
        self._procedure_rows[key] = BoardRow(status=status, text=text, badges=list(badges))
        self._refresh_boards()

    def _set_recovery_row(self, key: str, text: str, *, status: str, badges: list[str]) -> None:
        if key not in self._procedure_rows:
            self._procedure_order.append(key)
        self._procedure_rows[key] = BoardRow(status=status, text=text, badges=list(badges))
        self._refresh_boards()

    def _drop_procedure_row(self, key: str) -> None:
        self._procedure_rows.pop(key, None)
        self._procedure_order = [existing for existing in self._procedure_order if existing != key]

    def _stream_summary_row(self) -> BoardRow | None:
        if self._stream_event_count == 0 and not self.busy:
            return None

        parts: list[str] = []
        if self._stream_event_count:
            parts.append(f"{self._stream_event_count} events")
        if self._stream_token_count:
            parts.append(f"{self._stream_token_count} chunks")
        if self._stream_char_count:
            parts.append(f"{self._stream_char_count} chars")
        if self._last_token_at is not None:
            parts.append(f"last chunk {time.monotonic() - self._last_token_at:.1f}s ago")
        elif self._turn_started_at is not None:
            parts.append(f"waiting {time.monotonic() - self._turn_started_at:.1f}s")
        if self._last_event_kind:
            parts.append(f"last {self._last_event_kind}")

        status = "active" if self.busy and not self._assistant_message_seen else "complete"
        return BoardRow(status=status, text="stream · " + " · ".join(parts))

    def _refresh_boards(self) -> None:
        procedure_board = self.query_one("#procedure-board", BoardWidget)
        tool_board = self.query_one("#tool-board", BoardWidget)
        trace_board = self.query_one("#trace-board", BoardWidget)

        ordered_rows = [self._procedure_rows[key] for key in self._procedure_order if key in self._procedure_rows]
        procedure_board.set_rows(ordered_rows[-5:], spinner_index=self._spinner_index)

        tool_rows = [BoardRow(status=row.status, text=row.text, badges=[row.badge]) for row in self._tool_rows[-6:]]
        tool_board.set_rows(tool_rows, spinner_index=self._spinner_index)
        trace_rows: list[BoardRow] = []
        stream_row = self._stream_summary_row()
        if stream_row is not None:
            trace_rows.append(stream_row)
        trace_rows.extend(BoardRow(status="complete", text=line) for line in self._trace_lines[-7:])
        trace_board.set_rows(trace_rows, spinner_index=self._spinner_index)

    def _reset_turn_state(self) -> None:
        self._assistant_index = None
        self._procedure_rows = {}
        self._procedure_order = []
        self._tool_rows = []
        self._tool_id = 0
        self._trace_lines = []
        self._turn_started_at = None
        self._stream_event_count = 0
        self._stream_token_count = 0
        self._stream_char_count = 0
        self._last_event_kind = None
        self._last_event_at = None
        self._last_token_at = None
        self._assistant_message_seen = False
        self._stream_stall_visible = False
        self._spinner_index = 0
        self._refresh_boards()

    def _complete_active_rows(self) -> None:
        for row in self._procedure_rows.values():
            if row.status == "active":
                row.status = "complete"
        for row in self._tool_rows:
            if row.status == "active":
                row.status = "complete"

    def _trace(self, line: str) -> None:
        self._trace_lines.append(_one_line(line, limit=100))
        if self.is_mounted:
            self._refresh_boards()

    def _trace_event(self, event: AgentEvent) -> None:
        if event.kind == "token":
            content = event.data.get("content", "")
            self._trace(f"token chars={len(content)} total_chars={self._stream_char_count}")
            return
        if event.kind == "assistant_message":
            content = event.data.get("content", "")
            self._trace(f"assistant_message chars={len(content)}")
            return
        self._trace(f"{event.kind} {event.data}")

    def _record_stream_event(self, event: AgentEvent) -> None:
        now = time.monotonic()
        self._stream_event_count += 1
        self._last_event_kind = event.kind
        self._last_event_at = now
        if event.kind == "token":
            content = event.data.get("content", "")
            self._stream_token_count += 1
            self._stream_char_count += len(content)
            self._last_token_at = now
            if self._stream_stall_visible:
                self._stream_stall_visible = False
                self._drop_procedure_row("stream-stall")
        elif event.kind == "assistant_message":
            self._assistant_message_seen = True
            if self._stream_stall_visible:
                self._stream_stall_visible = False
                self._drop_procedure_row("stream-stall")

    def _on_stream_heartbeat(self) -> None:
        if not self.is_mounted:
            return
        self._spinner_index = (self._spinner_index + 1) % len(_SPINNER_FRAMES)
        if not self.busy:
            if self._stream_stall_visible:
                self._stream_stall_visible = False
                self._drop_procedure_row("stream-stall")
                self._refresh_boards()
            return

        if self._assistant_message_seen:
            if self._stream_stall_visible:
                self._stream_stall_visible = False
                self._drop_procedure_row("stream-stall")
            self._refresh_boards()
            return

        if self._last_token_at is not None:
            stalled_for = time.monotonic() - self._last_token_at
            if stalled_for >= _STREAM_STALL_SECS:
                self._stream_stall_visible = True
                self._set_procedure_row(
                    "stream-stall",
                    "The model has gone quiet, but I'm still watching the stream.",
                    status="active",
                    badges=["model slow"],
                )
                return

        if self._stream_stall_visible:
            self._stream_stall_visible = False
            self._drop_procedure_row("stream-stall")
        self._refresh_boards()

    async def _refresh_transcript(self) -> None:
        try:
            pane = self.query_one("#transcript-pane", TranscriptPane)
            scroll = self.query_one("#transcript-scroll", VerticalScroll)
        except NoMatches:
            return
        previous_scroll_y = scroll.scroll_y
        rendered = self._render_transcript_entries()
        await pane.set_markdown_text(rendered if rendered else pane.empty_message)
        pane.call_after_refresh(lambda: self._restore_transcript_scroll(previous_scroll_y))
        self.call_after_refresh(lambda: self._restore_transcript_scroll(previous_scroll_y))
        self.set_timer(0.01, lambda: self._restore_transcript_scroll(previous_scroll_y))

    def _restore_transcript_scroll(self, previous_scroll_y: float) -> None:
        if not self.is_mounted:
            return
        try:
            scroll = self.query_one("#transcript-scroll", VerticalScroll)
        except NoMatches:
            return
        if self._transcript_auto_follow:
            scroll.scroll_end(animate=False, immediate=True)
            return
        scroll.scroll_to(y=previous_scroll_y, animate=False, immediate=True, force=True)

    def _maybe_resume_transcript_follow(self) -> None:
        if not self.is_mounted:
            return
        try:
            scroll = self.query_one("#transcript-scroll", VerticalScroll)
        except NoMatches:
            return
        if (scroll.max_scroll_y - scroll.scroll_y) <= 1:
            self._transcript_auto_follow = True

    async def _confirm_dialog(self, verdict: Any) -> str | None:
        return await self._await_modal(ApprovalScreen(verdict))

    async def _ask_user_dialog(self, question: str, options: list[str] | None) -> str:
        answer = await self._await_modal(QuestionScreen(question, options))
        return answer or ""

    async def _confirm_destructive_action(self, prompt_text: str, *, confirm_label: str = "Delete") -> bool:
        choice = await self._ask_user_dialog(prompt_text, [confirm_label, "Cancel"])
        if not choice:
            return False
        return choice.strip().lower().startswith(confirm_label.lower())

    async def _await_modal(self, screen: ModalScreen[Any]) -> Any:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()

        def _resolve(result: Any) -> None:
            if not future.done():
                future.set_result(result)

        self.push_screen(screen, callback=_resolve)
        result = await future
        self.call_after_refresh(self._focus_composer)
        return result

    def _focus_composer(self) -> None:
        composer = self.query_one("#composer", Composer)
        if not composer.disabled:
            composer.focus()

    async def _load_prompt_history(self) -> list[str]:
        loader = getattr(self.backend, "load_prompt_history", None)
        if callable(loader):
            loaded = await loader()
            return [item for item in loaded if item]
        return []

    async def _record_prompt_history(self, prompt: str) -> None:
        self._prompt_history = [entry for entry in self._prompt_history if entry != prompt]
        self._prompt_history.insert(0, prompt)
        saver = getattr(self.backend, "append_prompt_history", None)
        if callable(saver):
            await saver(prompt)

    def _reset_history_navigation(self) -> None:
        self._history_index = None
        self._history_draft = ""

    def _browse_history(self, direction: int) -> None:
        if not self._prompt_history or self.busy:
            return
        composer = self.query_one("#composer", Composer)
        if self._history_index is None:
            if direction < 0:
                return
            self._history_draft = composer.text
            self._history_index = 0
        else:
            next_index = self._history_index + direction
            if next_index < 0:
                composer.load_text(self._history_draft)
                composer.move_cursor(
                    (composer.text.count("\n"), len(composer.text.split("\n")[-1]) if composer.text else 0)
                )
                self._reset_history_navigation()
                return
            if next_index >= len(self._prompt_history):
                return
            self._history_index = next_index

        composer.load_text(self._prompt_history[self._history_index])
        lines = composer.text.split("\n") if composer.text else [""]
        composer.move_cursor((len(lines) - 1, len(lines[-1])))

    def on_key(self, event: events.Key) -> None:
        if self._transcript_surface_focused():
            if event.key in {"up", "pageup", "home"}:
                self._transcript_auto_follow = False
            elif event.key in {"down", "pagedown", "end"}:
                self.call_after_refresh(self._maybe_resume_transcript_follow)

    def _transcript_surface_focused(self) -> bool:
        try:
            transcript = self.query_one("#transcript-pane", TranscriptPane)
            scroll = self.query_one("#transcript-scroll", VerticalScroll)
        except NoMatches:
            return False
        focused = self.screen.focused
        return focused in {transcript, scroll}

    def _event_targets_transcript(self, event: events.MouseEvent) -> bool:
        try:
            transcript = self.query_one("#transcript-pane", TranscriptPane)
            scroll = self.query_one("#transcript-scroll", VerticalScroll)
        except NoMatches:
            return False
        widget = event.widget
        while widget is not None:
            if widget in {transcript, scroll}:
                return True
            widget = widget.parent
        return False

    async def _maybe_handle_slash_command(self, prompt: str) -> bool:
        if not prompt.startswith("/"):
            return False
        handler = getattr(self.backend, "execute_slash_command", None)
        if not callable(handler):
            return False
        result = await handler(prompt)
        if result is None:
            return False
        if result.kind == "new_conversation":
            self._transcript_entries = []
            await self._flush_transcript_now()
        if result.echo_user:
            await self._append_bubble("user", prompt)
        await self._apply_slash_command_result(result, prompt)
        self._sync_session_snapshot()
        return True

    def _sync_session_snapshot(self) -> None:
        self.session.plan_mode = bool(getattr(self.backend, "plan_mode_active", lambda: False)())
        self.query_one(".session-banner", SessionBanner).update_snapshot(self.session)

    async def _apply_slash_command_result(self, result: CommandResult, prompt: str) -> None:
        if result.kind == "forward_prompt" and result.forward_prompt:
            await self._submit_prompt(result.forward_prompt, display_prompt=prompt)
            return
        if result.kind == "exit":
            self.exit()
            return
        if result.kind == "show_message" and result.message and result.command.name == "/rewind":
            await self._reload_transcript_from_backend()
            await self._append_bubble("assistant", result.message)
            return
        if result.kind == "show_message" and result.message:
            await self._append_bubble("assistant", result.message)
            return
        if result.kind == "show_help":
            await self._append_bubble("assistant", build_help_markdown())
            return
        if result.kind == "list_conversations":
            await self._append_bubble("assistant", self.backend._list_conversations_markdown(result.list_limit or 20))
            return
        if result.kind == "search_conversations":
            await self._append_bubble(
                "assistant",
                self.backend._search_conversations_markdown(result.search_query or ""),
            )
            return
        if result.kind == "compact_conversation":
            message = await self.backend.compact_current_conversation()
            await self._append_bubble("assistant", message)
            return
        if result.kind == "rewind_conversation":
            message = await self.backend.rewind_current_conversation(result.rewind_arg or "")
            await self._reload_transcript_from_backend()
            await self._append_bubble("assistant", message)
            return
        if result.kind == "show_tools":
            await self._append_bubble("assistant", build_tools_markdown(result.tool_names))
            return
        if result.kind == "show_usage":
            await self._append_bubble("assistant", self._usage_markdown())
            return
        if result.kind == "show_skills":
            await self._append_bubble(
                "assistant",
                build_skills_markdown(
                    result.skill_entries,
                    result.skill_warnings,
                    has_registry=getattr(self.backend, "skill_registry", None) is not None,
                ),
            )
            return
        if result.kind == "show_model":
            await self._append_bubble("assistant", f"Current model: `{result.model_name or self.session.model}`")
            return
        if result.kind == "show_slug":
            await self._append_bubble("assistant", self.backend._slug_command(""))
            return
        if result.kind == "set_model" and result.model_name:
            self.backend.set_active_model(result.model_name)
            self.session.model = result.model_name
            self.query_one(".session-banner", SessionBanner).update_snapshot(self.session)
            await self._append_bubble("assistant", f"Active model changed to `{result.model_name}`.")
            return
        if result.kind == "rename_conversation":
            await self._append_bubble(
                "assistant",
                self.backend._rename_conversation(result.conversation_title or result.command.arg),
            )
            return
        if result.kind == "set_slug":
            await self._append_bubble("assistant", self.backend._slug_command(result.slug_value or ""))
            return
        if result.kind == "new_conversation":
            self.backend.start_new_conversation(
                conversation_type=result.conversation_type or "chat",
                title=result.conversation_title or "New Conversation",
            )
            await self._append_bubble(
                "assistant",
                "Started a new conversation. The transcript is clear and the next prompt will begin a fresh thread.",
            )
            return
        if result.kind == "resume_conversation":
            summary = await self.backend.resume_conversation(result.resume_target)
            await self._reload_transcript_from_backend()
            if summary:
                await self._append_bubble("assistant", summary)
            return
        if result.kind == "show_spaces":
            await self._append_bubble("assistant", self.backend._space_list_markdown())
            return
        if result.kind == "create_space":
            await self._append_bubble("assistant", self.backend._create_space(result.space_target or ""))
            return
        if result.kind == "update_space":
            await self._append_bubble(
                "assistant",
                self.backend._edit_space(result.space_edit_field or "", result.space_edit_value or ""),
            )
            return
        if result.kind == "refresh_space":
            await self._append_bubble("assistant", self.backend._refresh_space(result.space_target or ""))
            return
        if result.kind == "export_space":
            await self._append_bubble("assistant", self.backend._export_space(result.space_target or ""))
            return
        if result.kind == "show_space":
            await self._append_bubble("assistant", self.backend._space_show_markdown(result.space_target or ""))
            return
        if result.kind == "show_space_sources":
            await self._append_bubble("assistant", self.backend._space_sources_markdown(result.space_target or ""))
            return
        if result.kind == "set_space":
            message = (
                self.backend._clear_space()
                if not (result.space_target or "").strip()
                else self.backend._switch_space(result.space_target or "")
            )
            await self._append_bubble("assistant", message)
            return
        if result.kind == "delete_space":
            target = result.space_target or ""
            if not await self._confirm_destructive_action(
                f"Delete space `{target}`? This cannot be undone.",
                confirm_label="Delete",
            ):
                await self._append_bubble("assistant", "Cancelled.")
                return
            await self._append_bubble("assistant", self.backend._delete_space(result.space_target or ""))
            return
        if result.kind == "show_artifacts":
            await self._append_bubble(
                "assistant",
                self.backend._artifact_list_markdown("", from_plural_alias=result.command.name == "/artifacts"),
            )
            return
        if result.kind == "show_artifact":
            await self._append_bubble("assistant", self.backend._artifact_show_markdown(result.artifact_fqn or ""))
            return
        if result.kind == "delete_artifact":
            target = result.artifact_fqn or ""
            if not await self._confirm_destructive_action(
                f"Delete artifact `{target}`? This cannot be undone.",
                confirm_label="Delete",
            ):
                await self._append_bubble("assistant", "Cancelled.")
                return
            await self._append_bubble("assistant", self.backend._delete_artifact(result.artifact_fqn or ""))
            return
        if result.kind == "show_packs":
            await self._append_bubble("assistant", self.backend._pack_list_markdown())
            return
        if result.kind == "show_pack":
            await self._append_bubble("assistant", self.backend._pack_show_markdown(result.pack_ref or ""))
            return
        if result.kind == "install_pack":
            await self._append_bubble(
                "assistant",
                self.backend._install_or_update_pack(
                    result.pack_path or "",
                    update=False,
                    project_scope=result.pack_project_scope,
                    attach_after_install=result.pack_attach_after_install,
                    priority=result.pack_priority or 50,
                ),
            )
            return
        if result.kind == "update_pack":
            await self._append_bubble(
                "assistant",
                self.backend._install_or_update_pack(
                    result.pack_path or "",
                    update=True,
                    project_scope=result.pack_project_scope,
                ),
            )
            return
        if result.kind == "attach_pack":
            await self._append_bubble(
                "assistant",
                self.backend._attach_pack(result.pack_ref or "", project_scope=result.pack_project_scope),
            )
            return
        if result.kind == "detach_pack":
            await self._append_bubble(
                "assistant",
                self.backend._detach_pack(result.pack_ref or "", project_scope=result.pack_project_scope),
            )
            return
        if result.kind == "delete_pack":
            target = result.pack_ref or ""
            if not await self._confirm_destructive_action(
                f"Remove pack `{target}`? This also detaches its artifacts.",
                confirm_label="Remove",
            ):
                await self._append_bubble("assistant", "Cancelled.")
                return
            await self._append_bubble("assistant", self.backend._remove_pack(result.pack_ref or ""))
            return
        if result.kind == "show_pack_sources":
            await self._append_bubble("assistant", self.backend._pack_sources_markdown())
            return
        if result.kind == "refresh_pack_sources":
            await self._append_bubble("assistant", self.backend._refresh_pack_sources_markdown())
            return
        if result.kind == "add_pack_source":
            await self._append_bubble("assistant", self.backend._add_pack_source(result.pack_source_url or ""))
            return
        if result.kind == "show_mcp_status":
            await self._append_bubble("assistant", self.backend._mcp_status_markdown())
            return
        if result.kind == "show_mcp_server_detail":
            await self._append_bubble(
                "assistant",
                self.backend._mcp_server_detail_markdown(result.mcp_server_name or ""),
            )
            return
        if result.kind == "run_mcp_action":
            await self._append_bubble(
                "assistant",
                await self.backend._run_mcp_action(result.mcp_action or "", result.mcp_server_name or ""),
            )
            return
        if result.kind == "show_plan_status":
            await self._append_bubble("assistant", self.backend._plan_status_markdown())
            return
        if result.kind == "set_plan_mode":
            await self._append_bubble("assistant", self.backend._set_plan_mode(result.plan_mode_enabled is True))
            return
        if result.kind == "delete_conversation":
            target = result.delete_target or ""
            choice = await self._ask_user_dialog(
                f"Delete conversation `{target}`? This cannot be undone.",
                ["Delete", "Cancel"],
            )
            if not choice or not choice.lower().startswith("delete"):
                await self._append_bubble("assistant", "Cancelled.")
                return
            message = await self.backend.delete_conversation(target)
            if message:
                await self._reload_transcript_from_backend()
                await self._append_bubble("assistant", message)

    async def _reload_transcript_from_backend(self) -> None:
        self._transcript_entries = []
        await self._flush_transcript_now()
        history = await self.backend.load_history()
        for role, content in history:
            if role in ("user", "assistant"):
                await self._append_bubble(role, content)

    def _status_badges(self, *, default: str) -> list[str]:
        return ["plan"] if self.session.plan_mode else [default]

    def _queue_transcript_flush(self) -> None:
        if self._transcript_flush_task is None or self._transcript_flush_task.done():
            self._transcript_flush_task = asyncio.create_task(self._flush_transcript_soon())

    async def _flush_transcript_soon(self) -> None:
        await asyncio.sleep(0.01)
        await self._refresh_transcript()

    async def _flush_transcript_now(self) -> None:
        if self._transcript_flush_task is not None and not self._transcript_flush_task.done():
            self._transcript_flush_task.cancel()
            try:
                await self._transcript_flush_task
            except asyncio.CancelledError:
                pass
        self._transcript_flush_task = None
        await self._refresh_transcript()

    def _render_transcript_entries(self) -> str:
        blocks: list[str] = []
        for index, (role, content) in enumerate(self._transcript_entries):
            label = "YOU" if role == "user" else "AI" if role == "assistant" else role.upper()
            display_content = content if role == "assistant" else _escape_transcript_markdown(content)
            if (
                role == "assistant"
                and index == self._assistant_index
                and self.busy
                and not self._assistant_message_seen
                and display_content.strip()
            ):
                display_content = display_content.rstrip() + "\n\n_waiting for continuation..._"
            if display_content:
                if role == "user":
                    blocks.append(f"###### {label}\n\n{_quote_markdown_block(display_content)}")
                else:
                    blocks.append(f"### {label}\n\n{display_content}")
            else:
                blocks.append(f"### {label}")
        return "\n\n---\n\n".join(blocks)


class AgentLoopTextualBackend:
    def __init__(
        self,
        *,
        config: Any,
        db: Any,
        ai_service: Any,
        tool_executor: Any,
        tools_openai: list[dict[str, Any]] | None,
        extra_system_prompt: str,
        working_dir: str,
        tool_registry: Any = None,
        mcp_manager: Any | None = None,
        skill_registry: Any | None = None,
        artifact_registry: Any | None = None,
        resume_conversation_id: str | None = None,
        dlp_scanner: Any | None = None,
        injection_detector: Any | None = None,
        output_filter: Any | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.ai_service = ai_service
        self.tool_executor = tool_executor
        self.tools_openai = tools_openai
        self.extra_system_prompt = extra_system_prompt
        self.working_dir = working_dir
        self.tool_registry = tool_registry
        self.mcp_manager = mcp_manager
        self.skill_registry = skill_registry
        self.artifact_registry = artifact_registry
        self.resume_conversation_id = resume_conversation_id
        self.dlp_scanner = dlp_scanner
        self.injection_detector = injection_detector
        self.output_filter = output_filter
        self._conversation: dict[str, Any] | None = None
        self._messages: list[dict[str, Any]] | None = None
        self._history_loaded = False
        self._current_cancel_event: asyncio.Event | None = None
        self._id_kwargs = self._identity_kwargs()
        self._prompt_history = FileHistory(str(Path(self.config.app.data_dir) / "cli_history"))
        self._pending_conversation_type = "chat"
        self._pending_conversation_title = "New Conversation"
        self._verbosity = "compact"
        self._current_turn_tools: list[dict[str, Any]] = []
        self._last_turn_tools: list[dict[str, Any]] = []
        self._plan_active = False
        self._plan_file: Path | None = None
        self._pending_plan_activation = False
        self._full_tools_backup: list[dict[str, Any]] | None = None
        self._active_space: dict[str, Any] | None = None

    async def load_history(self) -> list[tuple[str, str]]:
        if self._history_loaded:
            return self._plain_history()
        self._history_loaded = True
        self._messages = []
        if not self.resume_conversation_id:
            return []

        conv = storage.get_conversation(self.db, self.resume_conversation_id)
        if not conv:
            return []

        self._conversation = conv
        self._sync_space_from_conversation(conv)
        stored_dir = conv.get("working_dir")
        if stored_dir and os.path.isdir(stored_dir):
            resolved = os.path.realpath(stored_dir)
            from ..tools import bash, edit, glob_tool, grep, read, write

            for module in [read, write, edit, bash, glob_tool, grep]:
                if hasattr(module, "set_working_dir"):
                    module.set_working_dir(resolved)
            if self.tool_registry is not None:
                self.tool_registry._working_dir = resolved
            self.working_dir = resolved
        self._messages = self._load_conversation_messages(self.resume_conversation_id)
        plan_file = get_plan_file_path(self.config.app.data_dir, conv["id"])
        if plan_file.exists():
            self._apply_plan_mode(conv["id"])
        return self._plain_history()

    async def submit_turn(self, prompt: str) -> AsyncGenerator[AgentEvent, None]:
        if not self._history_loaded:
            await self.load_history()
        if self._messages is None:
            self._messages = []

        expanded = self._expand_file_references(prompt)
        conv = self._conversation
        if conv is None:
            conv = storage.create_conversation(
                self.db,
                title=self._pending_conversation_title,
                conversation_type=self._pending_conversation_type,
                working_dir=self.working_dir,
                **self._id_kwargs,
            )
            self._conversation = conv
            self.resume_conversation_id = conv["id"]
            if self._active_space:
                from ..services.space_storage import update_conversation_space

                update_conversation_space(self.db, conv["id"], self._active_space["id"])
                conv["space_id"] = self._active_space["id"]
            self._pending_conversation_type = "chat"
            self._pending_conversation_title = "New Conversation"
            if self._pending_plan_activation or (self._plan_active and self._plan_file is None):
                self._apply_plan_mode(conv["id"])

        storage.create_message(self.db, conv["id"], "user", expanded, **self._id_kwargs)
        self._messages.append({"role": "user", "content": expanded})

        cancel_event = asyncio.Event()
        self._current_cancel_event = cancel_event
        loop = asyncio.get_event_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, cancel_event.set)
        except (NotImplementedError, RuntimeError):
            pass

        user_attempt = 0
        budget_cfg = self.config.cli.usage.budgets

        async def _get_token_totals() -> tuple[int, int]:
            return (
                storage.get_conversation_token_total(self.db, conv["id"]),
                storage.get_daily_token_total(self.db),
            )

        try:
            while True:
                user_attempt += 1
                should_retry = False
                self._current_turn_tools = []

                async for event in run_agent_loop(
                    ai_service=self.ai_service,
                    messages=self._messages,
                    tool_executor=self.tool_executor,
                    tools_openai=self.tools_openai,
                    cancel_event=cancel_event,
                    extra_system_prompt=self.extra_system_prompt,
                    max_iterations=self.config.cli.max_tool_iterations,
                    narration_cadence=self.ai_service.config.narration_cadence,
                    tool_output_max_chars=self.config.cli.tool_output_max_chars,
                    budget_config=budget_cfg,
                    get_token_totals=_get_token_totals,
                    dlp_scanner=self.dlp_scanner,
                    injection_detector=self.injection_detector,
                    output_filter=self.output_filter,
                    max_consecutive_text_only=self.config.cli.max_consecutive_text_only,
                    max_line_repeats=self.config.cli.max_line_repeats,
                    max_identical_tool_repeats=self.config.cli.max_identical_tool_repeats,
                ):
                    if event.kind == "tool_call_start":
                        self._record_tool_start(
                            event.data.get("tool_name", "tool"),
                            event.data.get("arguments", {}) or {},
                        )
                    elif event.kind == "tool_call_end":
                        self._record_tool_end(
                            event.data.get("tool_name", "tool"),
                            event.data.get("status", "success"),
                            event.data.get("output"),
                        )
                    if event.kind == "assistant_message" and event.data.get("content"):
                        storage.create_message(
                            self.db,
                            conv["id"],
                            "assistant",
                            event.data["content"],
                            **self._id_kwargs,
                        )
                        self._messages.append({"role": "assistant", "content": event.data["content"]})
                    elif (
                        event.kind == "error"
                        and event.data.get("retryable")
                        and user_attempt < self.config.cli.max_retries
                    ):
                        should_retry = True
                    yield event

                if not should_retry:
                    self._last_turn_tools = [dict(item) for item in self._current_turn_tools]
                    break
                yield AgentEvent(
                    kind="retrying",
                    data={
                        "attempt": user_attempt + 1,
                        "max_attempts": self.config.cli.max_retries,
                        "reason": "turn_retry",
                    },
                )
            if not cancel_event.is_set():
                try:
                    title = await self.ai_service.generate_title(prompt)
                    storage.update_conversation_title(self.db, conv["id"], title)
                except Exception:
                    pass
        finally:
            self._current_cancel_event = None
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, RuntimeError):
                pass

    def cancel_current_turn(self) -> None:
        if self._current_cancel_event is not None:
            self._current_cancel_event.set()

    def start_new_conversation(self, *, conversation_type: str, title: str) -> None:
        self._conversation = None
        self.resume_conversation_id = None
        self._messages = []
        self._history_loaded = True
        self._pending_conversation_type = conversation_type
        self._pending_conversation_title = title

    def set_active_model(self, model_name: str) -> None:
        self.config.ai.model = model_name
        if hasattr(self.ai_service, "config") and hasattr(self.ai_service.config, "model"):
            self.ai_service.config.model = model_name

    def plan_mode_active(self) -> bool:
        return self._plan_active

    def _strip_planning_prompt(self, prompt: str) -> str:
        return re.sub(r"\n*<planning_mode>.*?</planning_mode>", "", prompt, flags=re.DOTALL)

    def _apply_plan_mode(self, conversation_id: str) -> None:
        self._plan_file = get_plan_file_path(self.config.app.data_dir, conversation_id)
        self._plan_active = True
        self._pending_plan_activation = False
        if self._full_tools_backup is None:
            self._full_tools_backup = list(self.tools_openai) if self.tools_openai else None
        if self.tools_openai:
            self.tools_openai = [
                tool for tool in self.tools_openai if tool.get("function", {}).get("name") in PLAN_MODE_ALLOWED_TOOLS
            ]
        self.extra_system_prompt = self._strip_planning_prompt(self.extra_system_prompt)
        self.extra_system_prompt += "\n\n" + build_planning_system_prompt(self._plan_file)

    def _exit_plan_mode(self) -> None:
        self._plan_active = False
        self._pending_plan_activation = False
        self.extra_system_prompt = self._strip_planning_prompt(self.extra_system_prompt)
        if self._full_tools_backup is not None:
            self.tools_openai = self._full_tools_backup
            self._full_tools_backup = None

    def _plan_status_markdown(self) -> str:
        if not self._plan_active:
            return "Planning mode: **off**"
        lines = ["## Planning Mode", "", "Status: **active**"]
        if self._plan_file is not None:
            content = read_plan(self._plan_file)
            if content:
                lines.append(f"Plan file: `{self._plan_file}` ({len(content)} chars)")
                lines.extend(["", *content.splitlines()[:20]])
                if len(content.splitlines()) > 20:
                    lines.extend(["", f"... {len(content.splitlines()) - 20} more lines"])
            else:
                lines.append(f"Plan file: `{self._plan_file}` (not yet written)")
        else:
            lines.append("Plan file: pending conversation start")
        return "\n".join(lines)

    def _set_plan_mode(self, active: bool) -> str:
        if active:
            if self._plan_active:
                return "Already in planning mode."
            self._plan_active = True
            if self._conversation:
                self._apply_plan_mode(self._conversation["id"])
            else:
                self._pending_plan_activation = True
            return "Planning mode active. The next turn will stay in investigation mode until you turn it off."
        if not self._plan_active:
            return "Planning mode: **off**"
        self._exit_plan_mode()
        return "Planning mode off. Full tools restored."

    def _handle_plan_command(self, prompt: str) -> CommandResult:
        sub, inline_prompt = parse_plan_command(prompt)
        parsed = parse_slash_command(prompt)
        assert parsed is not None
        if sub in {"on", "start"}:
            return CommandResult(
                kind="show_message",
                command=parsed,
                message=self._set_plan_mode(True),
                echo_user=False,
            )
        if sub == "status":
            return CommandResult(kind="show_message", command=parsed, message=self._plan_status_markdown())
        if sub == "off":
            return CommandResult(
                kind="show_message",
                command=parsed,
                message=self._set_plan_mode(False),
                echo_user=False,
            )
        if inline_prompt:
            if not self._plan_active:
                self._plan_active = True
                if self._conversation:
                    self._apply_plan_mode(self._conversation["id"])
                else:
                    self._pending_plan_activation = True
            return CommandResult(
                kind="forward_prompt",
                command=parsed,
                forward_prompt=inline_prompt,
                echo_user=False,
            )
        return CommandResult(
            kind="show_message",
            command=parsed,
            message="Supported plan commands here: `/plan on`, `/plan off`, `/plan status`, or `/plan <prompt>`.",
        )

    async def resume_conversation(self, target: str | None) -> str:
        from .repl import _resolve_conversation

        if target:
            conv = _resolve_conversation(self.db, target)
        else:
            convs = storage.list_conversations(self.db, limit=1)
            conv = storage.get_conversation(self.db, convs[0]["id"]) if convs else None
        if not conv:
            return "Conversation not found. Use `/list` to see available conversations."

        self._conversation = conv
        self.resume_conversation_id = conv["id"]
        self._history_loaded = False
        history = await self.load_history()
        exchange_count = len([entry for entry in history if entry[0] in {"user", "assistant"}])
        return f"Resumed **{conv.get('title', 'Untitled')}** ({exchange_count} visible messages)."

    def _list_conversations_markdown(self, limit: int = 20) -> str:
        convs = storage.list_conversations(self.db, limit=limit)
        if not convs:
            return "No conversations yet."
        lines = ["## Recent Conversations"]
        for i, conv in enumerate(convs, start=1):
            badge = f" ({conv['type']})" if conv.get("type") not in {None, "chat"} else ""
            slug = f" — `{conv['slug']}`" if conv.get("slug") else ""
            lines.append(f"{i}. **{conv.get('title', 'Untitled')}**{badge}{slug}")
        lines.append("")
        lines.append("Use `/resume <number|slug|id>` to open one.")
        return "\n".join(lines)

    def _search_conversations_markdown(self, arg: str) -> str:
        query = arg.strip()
        if not query:
            return "Usage: `/search <query>`"
        type_filter = None
        force_keyword = False
        if query.startswith("--keyword "):
            force_keyword = True
            query = query[len("--keyword ") :].strip()
        elif query.startswith("--type "):
            rest = query[len("--type ") :].strip()
            parts = rest.split(maxsplit=1)
            if parts and parts[0] in {"chat", "note", "document"}:
                type_filter = parts[0]
                query = parts[1] if len(parts) > 1 else ""
        if not query:
            return "Usage: `/search <query>`"
        results = storage.list_conversations(self.db, search=query, limit=20, conversation_type=type_filter)
        if not results:
            return f"No conversations matching `{query}`."
        heading = "Keyword search" if force_keyword else "Search results"
        lines = [f"## {heading}", ""]
        for i, conv in enumerate(results, start=1):
            badge = f" ({conv['type']})" if conv.get("type") not in {None, "chat"} else ""
            slug = f" — `{conv['slug']}`" if conv.get("slug") else ""
            lines.append(f"{i}. **{conv.get('title', 'Untitled')}**{badge}{slug}")
        lines.append("")
        lines.append("Use `/resume <number|slug|id>` to open one.")
        return "\n".join(lines)

    def _rewind_messages_markdown(self) -> str:
        if not self._conversation:
            return "No active conversation to rewind."
        stored = storage.list_messages(self.db, self._conversation["id"])
        if len(stored) < 2:
            return "Not enough messages to rewind."
        lines = ["## Rewind Conversation", ""]
        for message in stored:
            role_label = "You" if message["role"] == "user" else "AI"
            preview = _one_line(message["content"], limit=80)
            lines.append(f"{message['position']}. **{role_label}** {preview}")
        lines.append("")
        lines.append("Use `/rewind <position>` to keep that message and delete everything after it.")
        lines.append("Add `--undo-files` to also revert files changed by later write/edit tool calls.")
        return "\n".join(lines)

    def _rename_conversation(self, arg: str) -> str:
        from .repl import _resolve_conversation

        if not arg.strip():
            return "Usage: `/rename <title>` or `/rename <number|slug|id> <title>`"
        parts = arg.split(maxsplit=1)
        target = None
        new_title = ""
        if len(parts) == 1:
            new_title = parts[0].strip()
        else:
            maybe_target, remainder = parts
            resolved = _resolve_conversation(self.db, maybe_target)
            if resolved:
                target = resolved
                new_title = remainder.strip()
            else:
                new_title = arg.strip()
        if not new_title:
            return "Usage: `/rename <title>` or `/rename <number|slug|id> <title>`"
        if target is None:
            target = self._conversation
        if not target:
            return "No active conversation to rename."
        storage.update_conversation_title(self.db, target["id"], new_title)
        if self._conversation and self._conversation.get("id") == target["id"]:
            self._conversation["title"] = new_title
        return f"Renamed conversation to **{new_title}**."

    def _slug_command(self, arg: str) -> str:
        from ..services.slug import is_valid_slug, suggest_unique_slug

        if not self._conversation:
            return "No active conversation."
        if not arg.strip():
            return f"Current slug: `{self._conversation.get('slug') or 'none'}`"
        desired = arg.strip().lower()
        if not is_valid_slug(desired):
            return "Invalid slug. Use lowercase letters, numbers, and hyphens."
        suggestion = suggest_unique_slug(self.db, desired)
        if suggestion is None:
            storage.update_conversation_slug(self.db, self._conversation["id"], desired)
            self._conversation["slug"] = desired
            return f"Slug set to `{desired}`."
        return f"`{desired}` is already taken. Try `{suggestion}`."

    def _conventions_markdown(self) -> str:
        info = discover_conventions(self.working_dir)
        if info.source == "none":
            return "No conventions file found. Create `ANTEROOM.md` in the project root to define conventions."
        label = "Project" if info.source == "project" else "Global"
        lines = [f"## {label} Conventions", "", f"`{info.path}`", ""]
        if info.warning:
            lines.append(f"Warning: {info.warning}")
            lines.append("")
        preview = (info.content or "").splitlines()[:50]
        lines.extend(preview)
        if info.content and len(info.content.splitlines()) > 50:
            lines.extend(["", f"... {len(info.content.splitlines()) - 50} more lines"])
        return "\n".join(lines)

    def _strip_space_instructions(self, prompt: str) -> str:
        return re.sub(r"\n*<space_instructions[^>]*>.*?</space_instructions>", "", prompt, flags=re.DOTALL)

    def _inject_space_instructions(self, space: dict[str, Any], instructions: str | None = None) -> None:
        self.extra_system_prompt = self._strip_space_instructions(self.extra_system_prompt)
        text = instructions if instructions is not None else (space.get("instructions") or "")
        if not text:
            return
        safe_name = sanitize_trust_tags(space["name"]).replace('"', "&quot;")
        safe_instructions = sanitize_trust_tags(text)
        self.extra_system_prompt += (
            '\n\n<space_instructions space="' + safe_name + '">\n' + safe_instructions + "\n</space_instructions>"
        )

    def _sync_space_from_conversation(self, conv: dict[str, Any]) -> None:
        from ..services.space_storage import get_space

        space_id = conv.get("space_id")
        if not space_id:
            self._active_space = None
            self.extra_system_prompt = self._strip_space_instructions(self.extra_system_prompt)
            return
        space = get_space(self.db, space_id)
        self._active_space = space
        if space:
            self._inject_space_instructions(space)
        else:
            self.extra_system_prompt = self._strip_space_instructions(self.extra_system_prompt)

    def _resolve_space(self, target: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        from ..services.space_storage import resolve_space

        if not target:
            return None, []
        return resolve_space(self.db, target)

    def _space_list_markdown(self) -> str:
        from ..services.space_storage import count_space_conversations, list_spaces

        spaces = list_spaces(self.db)
        if not spaces:
            return "No spaces yet."
        lines = ["## Spaces", ""]
        for space in spaces:
            active = " · active" if self._active_space and self._active_space["id"] == space["id"] else ""
            count = count_space_conversations(self.db, space["id"])
            lines.append(f"- **{space['name']}** · {count} conversations · `{space['id'][:8]}...`{active}")
        lines.append("")
        lines.append("Use `/space show <name>` or `/space switch <name>`.")
        return "\n".join(lines)

    def _create_space(self, target: str) -> str:
        from ..services.space_storage import create_space, get_spaces_by_name

        name = target.strip()
        if not name:
            return "Usage: `/space create <name>`"
        existing = get_spaces_by_name(self.db, name)
        if existing:
            return f"Space **{name}** already exists."
        space = create_space(self.db, name)
        return f"Created space **{space['name']}**."

    def _edit_space(self, field: str, value: str) -> str:
        from ..services.space_storage import get_space, update_space

        if not self._active_space:
            return "No active space."
        field = field.strip().lower()
        if field not in {"instructions", "model", "name"}:
            return "Unknown field. Use: `instructions`, `model`, or `name`."
        if field == "name" and not value.strip():
            return "Usage: `/space edit name <new-name>`"

        updates: dict[str, Any]
        if field == "instructions":
            updates = {"instructions": value}
        elif field == "model":
            updates = {"model": value or None}
        else:
            updates = {"name": value.strip()}

        current_name = self._active_space["name"]
        updated = update_space(self.db, self._active_space["id"], **updates)
        refreshed = get_space(self.db, self._active_space["id"])
        if refreshed:
            self._active_space = refreshed
        if field == "instructions":
            if refreshed and (refreshed.get("instructions") or "").strip():
                self._inject_space_instructions(refreshed, refreshed.get("instructions", ""))
            else:
                self.extra_system_prompt = self._strip_space_instructions(self.extra_system_prompt)
            return f"Updated instructions for **{current_name}**."
        if field == "model":
            return f"Updated model for **{current_name}**: `{value or '(cleared)'}`"
        assert updated is not None
        return f"Renamed space to **{updated['name']}**."

    def _refresh_space(self, target: str) -> str:
        from ..services.space_storage import get_space, update_space
        from ..services.spaces import compute_file_hash, parse_space_file

        if target.strip():
            space, candidates = self._resolve_space(target)
            if candidates:
                names = ", ".join(candidate["name"] for candidate in candidates[:5])
                return f"Space `{target}` is ambiguous. Matches: {names}"
        else:
            space = self._active_space
        if not space:
            return "No active space."
        source_file = space.get("source_file", "")
        if not source_file:
            return "This space has no source file to refresh from."
        fpath = Path(source_file)
        if not fpath.is_file():
            return f"Space file not found: `{fpath}`"
        try:
            cfg = parse_space_file(fpath)
        except Exception:
            return f"Failed to refresh space from `{fpath}`"

        updated = update_space(
            self.db,
            space["id"],
            source_hash=compute_file_hash(fpath),
            instructions=cfg.instructions or "",
            model=cfg.config.get("model") or None,
        )
        refreshed = get_space(self.db, space["id"])
        if refreshed and self._active_space and self._active_space["id"] == space["id"]:
            self._active_space = refreshed
            if (refreshed.get("instructions") or "").strip():
                self._inject_space_instructions(refreshed, refreshed.get("instructions", ""))
            else:
                self.extra_system_prompt = self._strip_space_instructions(self.extra_system_prompt)
        if updated is None:
            return f"Failed to refresh space from `{fpath}`"
        return f"Refreshed space **{updated['name']}**."

    def _export_space(self, target: str) -> str:
        import yaml

        from ..services.spaces import export_space_to_yaml

        if target.strip():
            space, candidates = self._resolve_space(target)
            if candidates:
                names = ", ".join(candidate["name"] for candidate in candidates[:5])
                return f"Space `{target}` is ambiguous. Matches: {names}"
        else:
            space = self._active_space
        if not space:
            return "No active space."
        try:
            cfg = export_space_to_yaml(self.db, space["id"])
        except ValueError as exc:
            return str(exc)

        data: dict[str, Any] = {"name": cfg.name, "version": cfg.version}
        if cfg.repos:
            data["repos"] = cfg.repos
        if cfg.pack_sources:
            data["pack_sources"] = [{"url": ps.url, "branch": ps.branch} for ps in cfg.pack_sources]
        if cfg.packs:
            data["packs"] = cfg.packs
        if cfg.sources:
            data["sources"] = [{k: v for k, v in [("path", s.path), ("url", s.url)] if v} for s in cfg.sources]
        if cfg.instructions:
            data["instructions"] = cfg.instructions
        if cfg.config:
            data["config"] = cfg.config

        dumped = yaml.dump(data, default_flow_style=False, sort_keys=False).strip()
        return f"## Space YAML: {space['name']}\n\n```yaml\n{dumped}\n```"

    def _space_show_markdown(self, target: str) -> str:
        from ..services.space_storage import count_space_conversations, get_space_paths

        if not target and self._active_space:
            space = self._active_space
        else:
            space, candidates = self._resolve_space(target)
            if candidates:
                names = ", ".join(candidate["name"] for candidate in candidates[:5])
                return f"Space `{target}` is ambiguous. Matches: {names}"
            if not space:
                return f"Space `{target}` not found."
        assert space is not None
        paths = get_space_paths(self.db, space["id"])
        lines = [f"## Space: {space['name']}", ""]
        if space.get("source_file"):
            lines.append(f"- Source: `{space['source_file']}`")
        if space.get("model"):
            lines.append(f"- Model: `{space['model']}`")
        lines.append(f"- Conversations: `{count_space_conversations(self.db, space['id'])}`")
        instructions = space.get("instructions") or ""
        if instructions:
            lines.append(f"- Instructions: {_one_line(instructions, limit=100)}")
        if paths:
            lines.extend(["", "### Paths"])
            for path in paths:
                label = path.get("repo_url") or "(mapped)"
                lines.append(f"- `{label}` -> `{path['local_path']}`")
        return "\n".join(lines)

    def _space_sources_markdown(self, target: str) -> str:
        from ..services.storage import get_direct_space_source_links

        if not target and self._active_space:
            space = self._active_space
        else:
            space, candidates = self._resolve_space(target)
            if candidates:
                names = ", ".join(candidate["name"] for candidate in candidates[:5])
                return f"Space `{target}` is ambiguous. Matches: {names}"
            if not space:
                return f"Space `{target}` not found."
        assert space is not None
        linked = get_direct_space_source_links(self.db, space["id"])
        if not linked:
            return f"No sources linked to space `{space['name']}`."
        lines = [f"## Sources in {space['name']}", ""]
        for source in linked:
            title = source.get("title") or "Untitled"
            source_type = source.get("type") or "unknown"
            lines.append(f"- **{title}** · {source_type} · `{str(source['id'])[:8]}...`")
        return "\n".join(lines)

    def _switch_space(self, target: str) -> str:
        space, candidates = self._resolve_space(target)
        if candidates:
            names = ", ".join(candidate["name"] for candidate in candidates[:5])
            return f"Space `{target}` is ambiguous. Matches: {names}"
        if not space:
            return f"Space `{target}` not found."
        self._active_space = space
        self._inject_space_instructions(space)
        if self._conversation:
            from ..services.space_storage import update_conversation_space

            update_conversation_space(self.db, self._conversation["id"], space["id"])
            self._conversation["space_id"] = space["id"]
        return f"Active space: **{space['name']}**"

    def _clear_space(self) -> str:
        if not self._active_space:
            return "No active space."
        old_name = self._active_space["name"]
        self._active_space = None
        self.extra_system_prompt = self._strip_space_instructions(self.extra_system_prompt)
        if self._conversation:
            from ..services.space_storage import update_conversation_space

            update_conversation_space(self.db, self._conversation["id"], None)
            self._conversation["space_id"] = None
        return f"Cleared space: **{old_name}**"

    def _delete_space(self, target: str) -> str:
        from ..services.space_storage import delete_space

        space, candidates = self._resolve_space(target)
        if candidates:
            names = ", ".join(candidate["name"] for candidate in candidates[:5])
            return f"Space `{target}` is ambiguous. Matches: {names}"
        if not space:
            return f"Space `{target}` not found."
        deleted_space_id = space["id"]
        deleted_space_name = space["name"]
        if not delete_space(self.db, deleted_space_id):
            return f"Space `{target}` not found."
        if self._active_space and self._active_space["id"] == deleted_space_id:
            self._active_space = None
            self.extra_system_prompt = self._strip_space_instructions(self.extra_system_prompt)
        if self._conversation and self._conversation.get("space_id") == deleted_space_id:
            self._conversation["space_id"] = None
        return f"Deleted space **{deleted_space_name}**."

    def _refresh_artifact_prompt(self) -> None:
        if self.artifact_registry is None:
            return
        self.extra_system_prompt = re.sub(
            r"\n*<artifact [^>]*>.*?</artifact>",
            "",
            self.extra_system_prompt,
            flags=re.DOTALL,
        )
        self.extra_system_prompt = re.sub(
            r"\n*<untrusted-content [^>]*origin=\"artifact:[^\"]*\"[^>]*>.*?</untrusted-content>",
            "",
            self.extra_system_prompt,
            flags=re.DOTALL,
        )

        from ..services.artifacts import ArtifactType

        for artifact_type in (ArtifactType.INSTRUCTION, ArtifactType.RULE, ArtifactType.CONTEXT):
            artifacts = self.artifact_registry.list_all(artifact_type=artifact_type)
            for artifact in artifacts:
                if not artifact.content.strip():
                    continue
                if artifact.source == "built_in":
                    tag = f'<artifact type="{artifact_type.value}" fqn="{artifact.fqn}">'
                    self.extra_system_prompt += f"\n{tag}\n{artifact.content}\n</artifact>"
                else:
                    wrapped = wrap_untrusted(
                        artifact.content,
                        origin=f"artifact:{artifact.fqn}",
                        content_type=artifact_type.value,
                    )
                    self.extra_system_prompt += f"\n{wrapped}"

        enforcer = getattr(self.tool_registry, "_rule_enforcer", None) if self.tool_registry else None
        if enforcer is not None:
            enforcer.load_rules(self.artifact_registry.list_all(artifact_type=ArtifactType.RULE))

    def _artifact_list_markdown(self, arg: str, *, from_plural_alias: bool = False) -> str:
        from ..services import artifact_storage as artifact_storage_service

        artifact_type = None
        source = None
        for token in arg.split():
            if token.startswith("--type="):
                artifact_type = token.split("=", 1)[1]
            elif token.startswith("--source="):
                source = token.split("=", 1)[1]
        try:
            artifacts = artifact_storage_service.list_artifacts(self.db, artifact_type=artifact_type, source=source)
        except ValueError as exc:
            return f"Invalid filter: `{exc}`"
        if not artifacts:
            return "No artifacts found."
        lines = ["## Artifacts", ""]
        for artifact in artifacts:
            lines.append(f"- `{artifact['fqn']}` · {artifact.get('type', '?')} · {artifact.get('source', '?')}")
        if not from_plural_alias:
            lines.extend(["", "Use `/artifact show <fqn>` to inspect one."])
        return "\n".join(lines)

    def _artifact_show_markdown(self, fqn: str) -> str:
        from ..services import artifact_storage as artifact_storage_service
        from ..services.artifacts import validate_fqn

        if not fqn:
            return "Usage: `/artifact show <fqn>`"
        if not validate_fqn(fqn):
            return "Invalid FQN format."
        artifact = artifact_storage_service.get_artifact_by_fqn(self.db, fqn)
        if not artifact:
            return "Artifact not found."
        lines = [
            f"## Artifact: {artifact['fqn']}",
            "",
            f"- Type: `{artifact['type']}`",
            f"- Source: `{artifact['source']}`",
            f"- Hash: `{artifact['content_hash']}`",
            f"- Updated: `{artifact.get('updated_at', '')}`",
            "",
            "### Content",
            artifact["content"],
        ]
        return "\n".join(lines)

    def _delete_artifact(self, fqn: str) -> str:
        from ..services import artifact_storage as artifact_storage_service
        from ..services.artifacts import validate_fqn

        if not fqn:
            return "Usage: `/artifact delete <fqn>`"
        if not validate_fqn(fqn):
            return "Invalid FQN format."
        artifact = artifact_storage_service.get_artifact_by_fqn(self.db, fqn)
        if not artifact:
            return "Artifact not found."
        artifact_storage_service.delete_artifact(self.db, artifact["id"])
        if self.artifact_registry is not None:
            self.artifact_registry.load_from_db(self.db)
            self._refresh_artifact_prompt()
            if self.skill_registry is not None:
                self.skill_registry.load_from_artifacts(self.artifact_registry)
                self._refresh_skill_tool_schema()
        return f"Deleted `{fqn}`."

    def _resolve_pack(self, ref: str) -> dict[str, Any] | None:
        from ..services import packs as packs_service

        namespace, _, name = ref.rpartition("/")
        if not namespace:
            namespace = "default"
        pack, _ = packs_service.resolve_pack(self.db, namespace, name)
        return pack

    def _pack_list_markdown(self) -> str:
        from ..services import packs as packs_service

        installed = packs_service.list_packs(self.db)
        if not installed:
            return "No packs installed. Install one with the CLI: `/pack install <path>`."
        lines = ["## Installed Packs", ""]
        for pack in installed:
            namespace = pack.get("namespace", "default")
            description = f" — {pack['description']}" if pack.get("description") else ""
            lines.append(
                f"- `@{namespace}/{pack['name']}` v{pack.get('version', '?')} "
                f"({pack.get('artifact_count', 0)} artifacts){description}"
            )
        return "\n".join(lines)

    def _pack_show_markdown(self, ref: str) -> str:
        from ..services import packs as packs_service

        if not ref:
            return "Usage: `/pack show <namespace/name>`"
        pack = self._resolve_pack(ref)
        if not pack:
            return f"Pack `{ref}` not found."
        info = packs_service.get_pack(self.db, pack["namespace"], pack["name"])
        if not info:
            return f"Pack `{ref}` not found."
        lines = [f"## @{info['namespace']}/{info['name']}", "", f"- Version: `{info.get('version', '?')}`"]
        if info.get("description"):
            lines.append(f"- Description: {info['description']}")
        artifacts = info.get("artifacts", [])
        lines.append(f"- Artifacts: `{len(artifacts)}`")
        if artifacts:
            lines.extend(["", "### Artifacts"])
            for artifact in artifacts:
                lines.append(f"- `{artifact.get('type', '?')}`: `{artifact.get('name', '?')}`")
        return "\n".join(lines)

    def _remove_pack(self, ref: str) -> str:
        from ..services import packs as packs_service

        if not ref:
            return "Usage: `/pack remove <namespace/name>`"
        pack = self._resolve_pack(ref)
        if not pack:
            return f"Pack `{ref}` not found."
        removed = packs_service.remove_pack_by_id(self.db, pack["id"])
        if not removed:
            return f"Pack `{ref}` not found."
        if self.artifact_registry is not None:
            self.artifact_registry.load_from_db(self.db)
            self._refresh_artifact_prompt()
            if self.skill_registry is not None:
                self.skill_registry.load_from_artifacts(self.artifact_registry)
                self._refresh_skill_tool_schema()
        return f"Removed `@{pack['namespace']}/{pack['name']}`."

    def _attach_pack(self, ref: str, *, project_scope: bool = False) -> str:
        from ..services.pack_attachments import attach_pack

        if not ref:
            return "Usage: `/pack attach <namespace/name> [--project]`"
        pack = self._resolve_pack(ref)
        if not pack:
            return f"Pack `{ref}` not found."
        try:
            attach_pack(self.db, pack["id"], project_path=self.working_dir if project_scope else None)
        except ValueError as exc:
            return str(exc)
        if self.artifact_registry is not None:
            self.artifact_registry.load_from_db(self.db)
            self._refresh_artifact_prompt()
            if self.skill_registry is not None:
                self.skill_registry.load_from_artifacts(self.artifact_registry)
                self._refresh_skill_tool_schema()
        scope = "project" if project_scope else "global"
        return f"Attached `@{pack['namespace']}/{pack['name']}` ({scope})."

    def _detach_pack(self, ref: str, *, project_scope: bool = False) -> str:
        from ..services.pack_attachments import detach_pack

        if not ref:
            return "Usage: `/pack detach <namespace/name> [--project]`"
        pack = self._resolve_pack(ref)
        if not pack:
            return f"Pack `{ref}` not found."
        removed = detach_pack(self.db, pack["id"], project_path=self.working_dir if project_scope else None)
        if not removed:
            return f"Pack `{ref}` is not attached at {'project' if project_scope else 'global'} scope."
        if self.artifact_registry is not None:
            self.artifact_registry.load_from_db(self.db)
            self._refresh_artifact_prompt()
            if self.skill_registry is not None:
                self.skill_registry.load_from_artifacts(self.artifact_registry)
                self._refresh_skill_tool_schema()
        scope = "project" if project_scope else "global"
        return f"Detached `@{pack['namespace']}/{pack['name']}` ({scope})."

    def _pack_sources_markdown(self) -> str:
        from ..services.pack_sources import list_cached_sources

        sources_cfg = getattr(self.config, "pack_sources", []) or []
        if not sources_cfg:
            return "No pack sources configured. Add one with the CLI: `/pack add-source <url>`."
        cached = list_cached_sources(self.config.app.data_dir)
        cached_map = {entry.url: entry for entry in cached}
        lines = ["## Pack Sources", ""]
        for source in sources_cfg:
            url = getattr(source, "url", None) or "?"
            branch = getattr(source, "branch", "main") or "main"
            cached_entry = cached_map.get(url)
            status = f"cached ({cached_entry.ref[:8]})" if cached_entry else "not cloned"
            lines.append(f"- `{url}` ({branch}) — {status}")
        return "\n".join(lines)

    def _refresh_pack_sources_markdown(self) -> str:
        sources_cfg = getattr(self.config, "pack_sources", []) or []
        if not sources_cfg:
            return "No pack sources configured. Add one with the CLI: `/pack add-source <url>`."

        from ..services.pack_refresh import PackRefreshWorker

        worker = PackRefreshWorker(db=self.db, data_dir=self.config.app.data_dir, sources=sources_cfg)
        results = worker.refresh_all()
        if self.artifact_registry is not None and any(result.changed for result in results):
            self.artifact_registry.load_from_db(self.db)
            self._refresh_artifact_prompt()
            if self.skill_registry is not None:
                self.skill_registry.load_from_artifacts(self.artifact_registry)
                self._refresh_skill_tool_schema()

        lines = ["## Pack Refresh", ""]
        for result in results:
            if result.success:
                lines.append(
                    f"- `{result.url}` — installed `{result.packs_installed}`, "
                    f"updated `{result.packs_updated}`, changed `{str(result.changed).lower()}`"
                )
            else:
                lines.append(f"- `{result.url}` — error: {result.error or 'unknown error'}")
        return "\n".join(lines)

    def _add_pack_source(self, url: str) -> str:
        if not url:
            return "Usage: `/pack add-source <git-url>`"

        from ..config import PackSourceConfig
        from ..services.pack_sources import add_pack_source

        result = add_pack_source(url)
        if not result.ok:
            return result.message or "Failed to add pack source."

        sources_cfg = getattr(self.config, "pack_sources", None)
        if sources_cfg is None:
            self.config.pack_sources = []
            sources_cfg = self.config.pack_sources
        if not any(getattr(source, "url", None) == url for source in sources_cfg):
            sources_cfg.append(PackSourceConfig(url=url, branch="main", refresh_interval=30))

        return result.message or f"Added pack source: {url}"

    def _install_or_update_pack(
        self,
        pack_path_str: str,
        *,
        update: bool,
        project_scope: bool = False,
        attach_after_install: bool = False,
        priority: int = 50,
    ) -> str:
        from pathlib import Path

        from ..services import packs as packs_service
        from ..services.pack_attachments import attach_pack

        if not pack_path_str:
            return "Usage: `/pack update <path>`" if update else "Usage: `/pack install <path>`"

        base_dir = Path(self.working_dir or ".")
        pack_path = Path(pack_path_str).expanduser()
        if not pack_path.is_absolute():
            pack_path = base_dir / pack_path
        pack_path = pack_path.resolve()
        manifest_path = pack_path / "pack.yaml"
        if not manifest_path.exists():
            return f"No pack.yaml found in `{pack_path}`."

        try:
            manifest = packs_service.parse_manifest(manifest_path)
            errors = packs_service.validate_manifest(manifest, pack_path)
            if errors:
                return "\n".join(["Pack validation failed:"] + [f"- {err}" for err in errors])
            project_dir = Path(self.working_dir) if project_scope else None
            result = (
                packs_service.update_pack(self.db, manifest, pack_path, project_dir=project_dir)
                if update
                else packs_service.install_pack(self.db, manifest, pack_path, project_dir=project_dir)
            )
        except ValueError as exc:
            return str(exc)

        attach_note = ""
        if attach_after_install:
            try:
                attach_pack(
                    self.db,
                    result["id"],
                    project_path=self.working_dir if project_scope else None,
                    priority=priority,
                )
                attach_scope = "project" if project_scope else "global"
                attach_note = f"\nAttached `@{manifest.namespace}/{manifest.name}` ({attach_scope}, p{priority})."
            except ValueError as exc:
                attach_note = f"\nPack installed but not attached: {exc}"

        if self.artifact_registry is not None:
            self.artifact_registry.load_from_db(self.db)
            self._refresh_artifact_prompt()
            if self.skill_registry is not None:
                self.skill_registry.load_from_artifacts(self.artifact_registry)
                self._refresh_skill_tool_schema()

        action_word = "Updated" if update or result.get("action") == "updated" else "Installed"
        return (
            f"{action_word} `@{manifest.namespace}/{manifest.name}` v{manifest.version} "
            f"({result.get('artifact_count', 0)} artifacts).{attach_note}"
        )

    def _mcp_status_markdown(self) -> str:
        if self.mcp_manager is None:
            return "No MCP servers configured."
        statuses = self.mcp_manager.get_server_statuses()
        if not statuses:
            return "No MCP servers configured."
        lines = ["## MCP Servers", ""]
        for name, info in statuses.items():
            status = info.get("status", "unknown")
            transport = info.get("transport", "?")
            tool_count = info.get("tool_count", 0)
            err = info.get("error_message")
            suffix = f" — {err}" if err else ""
            lines.append(f"- **{name}** · {transport} · {status} · {tool_count} tools{suffix}")
        lines.append("")
        lines.append("Use `/mcp status <name>` for details or `/mcp connect|disconnect|reconnect <name>`.")
        return "\n".join(lines)

    def _mcp_server_detail_markdown(self, name: str) -> str:
        if self.mcp_manager is None:
            return "No MCP servers configured."
        statuses = self.mcp_manager.get_server_statuses()
        if name not in statuses:
            known = ", ".join(sorted(statuses)) if statuses else "none"
            return f"Unknown MCP server `{name}`. Available: {known}"
        info = statuses[name]
        lines = [f"## MCP Server: {name}", ""]
        lines.append(f"- Status: **{info.get('status', 'unknown')}**")
        lines.append(f"- Transport: `{info.get('transport', '?')}`")
        lines.append(f"- Tools: `{info.get('tool_count', 0)}`")
        err = info.get("error_message")
        if err:
            lines.append(f"- Error: `{err}`")
        config = getattr(self.mcp_manager, "_configs", {}).get(name)
        if config:
            if getattr(config, "command", None):
                cmd = f"{config.command} {' '.join(config.args)}".strip()
                lines.append(f"- Command: `{cmd}`")
            if getattr(config, "url", None):
                lines.append(f"- URL: `{config.url}`")
            if getattr(config, "timeout", None) is not None:
                lines.append(f"- Timeout: `{config.timeout}s`")
        server_tools = getattr(self.mcp_manager, "_server_tools", {}).get(name, [])
        if server_tools:
            lines.extend(["", "### Tools"])
            for tool in server_tools:
                desc = tool.get("description", "")
                suffix = f" — {desc}" if desc else ""
                lines.append(f"- `{tool.get('name', '?')}`{suffix}")
        return "\n".join(lines)

    async def _run_mcp_action(self, action: str, server_name: str) -> str:
        if self.mcp_manager is None:
            return "No MCP servers configured."
        try:
            if action == "connect":
                await self.mcp_manager.connect_server(server_name)
            elif action == "disconnect":
                await self.mcp_manager.disconnect_server(server_name)
            elif action == "reconnect":
                await self.mcp_manager.reconnect_server(server_name)
            else:
                return f"Unknown MCP action `{action}`."
        except ValueError as exc:
            return str(exc)
        if hasattr(self.mcp_manager, "get_all_tools"):
            return f"MCP `{action}` for **{server_name}** complete.\n\n{self._mcp_status_markdown()}"
        return f"MCP `{action}` for **{server_name}** complete."

    async def delete_conversation(self, target: str) -> str:
        from .repl import _resolve_conversation

        conv = _resolve_conversation(self.db, target)
        if not conv:
            return "Conversation not found. Use `/list` to see available conversations."

        title = conv.get("title", "Untitled")
        storage.delete_conversation(self.db, conv["id"], self.config.app.data_dir)
        if self._conversation and self._conversation.get("id") == conv["id"]:
            self._conversation = None
            self.resume_conversation_id = None
            self._messages = []
            self._history_loaded = True
        return f"Deleted **{title}**."

    async def rewind_current_conversation(self, arg: str) -> str:
        if not self._conversation:
            return "No active conversation to rewind."
        stored = storage.list_messages(self.db, self._conversation["id"])
        if len(stored) < 2:
            return "Not enough messages to rewind."
        if not arg.strip():
            return self._rewind_messages_markdown()

        tokens = [token for token in arg.split() if token]
        undo_files = False
        if "--undo-files" in tokens:
            undo_files = True
            tokens = [token for token in tokens if token != "--undo-files"]
        if len(tokens) != 1 or not tokens[0].isdigit():
            return "Usage: `/rewind <position> [--undo-files]`"

        target_pos = int(tokens[0])
        positions = {message["position"] for message in stored}
        if target_pos not in positions:
            return f"Position {target_pos} not found."

        msgs_after = [message for message in stored if message["position"] > target_pos]
        msg_ids_after = [message["id"] for message in msgs_after]
        file_paths = collect_file_paths(self.db, msg_ids_after)

        rewind_result = await rewind_service(
            db=self.db,
            conversation_id=self._conversation["id"],
            to_position=target_pos,
            undo_files=undo_files,
            data_dir=self.config.app.data_dir,
            working_dir=self.working_dir,
        )

        self._history_loaded = False
        await self.load_history()

        summary = f"Rewound **{rewind_result.deleted_messages}** message(s) to position `{target_pos}`."
        if rewind_result.reverted_files:
            summary += f"\n\nReverted **{len(rewind_result.reverted_files)}** file(s)."
        elif file_paths and not undo_files:
            summary += (
                f"\n\nLeft **{len(file_paths)}** changed file(s) in place. "
                "Re-run with `--undo-files` to revert them."
            )
        if rewind_result.skipped_files:
            skipped = "\n".join(f"- `{item}`" for item in rewind_result.skipped_files)
            summary += f"\n\n### Skipped file reverts\n{skipped}"
        return summary

    async def compact_current_conversation(self) -> str:
        if self._messages is None:
            await self.load_history()
        if not self._conversation or not self._messages:
            return "No active conversation to compact."
        if len(self._messages) < 4:
            return "Not enough messages to compact."

        original_count = len(self._messages)
        history_text = _build_compaction_history(self._messages)
        summary_prompt = (
            "Summarize the following conversation concisely, preserving:\n"
            "- Key decisions and conclusions\n"
            "- File paths that were read, written, or edited\n"
            "- Important code changes and their purpose\n"
            "- Which steps of any multi-step plan have been COMPLETED (tool_result SUCCESS) vs remaining\n"
            "- Current state of the task — what has been done and what is next\n"
            "- Any errors encountered and how they were resolved\n\n" + history_text
        )
        try:
            response = await self.ai_service.client.chat.completions.create(
                model=self.ai_service.config.model,
                messages=[{"role": "user", "content": summary_prompt}],
                max_completion_tokens=1000,
            )
            summary = response.choices[0].message.content or "Conversation summary unavailable."
        except Exception:
            return "Failed to generate summary."

        compact_note = f"Previous conversation summary (auto-compacted from {original_count} messages):\n\n{summary}"
        self._messages.clear()
        self._messages.append({"role": "system", "content": compact_note})
        return f"Compacted **{original_count}** messages into a working summary."

    async def load_prompt_history(self) -> list[str]:
        return list(self._prompt_history.load_history_strings())

    async def append_prompt_history(self, prompt: str) -> None:
        self._prompt_history.append_string(prompt)

    async def execute_slash_command(self, prompt: str) -> CommandResult | None:
        result = execute_slash_command(
            prompt,
            CommandContext(
                current_model=self.config.ai.model,
                working_dir=self.working_dir,
                available_tools=self._tool_names(),
                tool_registry=self.tool_registry,
                skill_registry=self.skill_registry,
                artifact_registry=self.artifact_registry,
                plan_mode=self.plan_mode_active(),
            ),
        )
        if result and result.kind == "show_skills":
            self._refresh_skill_tool_schema()
            return result
        if result is not None:
            return result

        parsed = parse_slash_command(prompt)
        if parsed is None:
            return None
        if parsed.name == "/list":
            limit = 20
            if parsed.arg.isdigit():
                limit = max(1, int(parsed.arg))
            return CommandResult(kind="show_message", command=parsed, message=self._list_conversations_markdown(limit))
        if parsed.name == "/last":
            return CommandResult(kind="resume_conversation", command=parsed, resume_target=None, echo_user=False)
        if parsed.name == "/resume":
            if not parsed.arg:
                return CommandResult(kind="show_message", command=parsed, message=self._list_conversations_markdown())
            return CommandResult(
                kind="resume_conversation",
                command=parsed,
                resume_target=parsed.arg,
                echo_user=False,
            )
        if parsed.name == "/search":
            return CommandResult(
                kind="show_message",
                command=parsed,
                message=self._search_conversations_markdown(parsed.arg),
            )
        if parsed.name == "/plan":
            return self._handle_plan_command(prompt)
        if parsed.name in {"/space", "/spaces"}:
            parts = prompt.split(maxsplit=2)
            sub = parts[1].lower() if len(parts) >= 2 else ""
            if parsed.name == "/spaces":
                sub = "list"
            if sub in {"", "list"}:
                return CommandResult(kind="show_message", command=parsed, message=self._space_list_markdown())
            if sub == "show":
                target = parts[2].strip() if len(parts) >= 3 else ""
                return CommandResult(kind="show_message", command=parsed, message=self._space_show_markdown(target))
            if sub in {"switch", "select", "use"}:
                target = parts[2].strip() if len(parts) >= 3 else ""
                if not target:
                    return CommandResult(
                        kind="show_message",
                        command=parsed,
                        message="Usage: `/space switch <name|id>`",
                    )
                return CommandResult(kind="show_message", command=parsed, message=self._switch_space(target))
            if sub == "clear":
                return CommandResult(kind="show_message", command=parsed, message=self._clear_space())
            return CommandResult(
                kind="show_message",
                command=parsed,
                message=(
                    "Supported space commands here: `/space`, `/space show <name>`, "
                    "`/space switch <name>`, `/space clear`."
                ),
            )
        if parsed.name in {"/artifact", "/artifacts"}:
            parts = prompt.split(maxsplit=2)
            sub = parts[1].lower() if len(parts) >= 2 else ""
            if parsed.name == "/artifacts":
                sub = "list"
            if sub in {"", "list"}:
                rest = " ".join(parts[1:]) if parsed.name == "/artifacts" else (parts[2] if len(parts) >= 3 else "")
                return CommandResult(
                    kind="show_message",
                    command=parsed,
                    message=self._artifact_list_markdown(rest, from_plural_alias=parsed.name == "/artifacts"),
                )
            if sub == "show":
                target = parts[2].strip() if len(parts) >= 3 else ""
                return CommandResult(kind="show_message", command=parsed, message=self._artifact_show_markdown(target))
            if sub == "delete":
                target = parts[2].strip() if len(parts) >= 3 else ""
                return CommandResult(kind="show_message", command=parsed, message=self._delete_artifact(target))
            if sub == "import":
                return CommandResult(
                    kind="show_message",
                    command=parsed,
                    message="Use the CLI: `aroom artifact import --skills|--instructions|--all`",
                )
            if sub == "create":
                return CommandResult(
                    kind="show_message",
                    command=parsed,
                    message="Use the CLI: `aroom artifact create <type> <name>`",
                )
            return CommandResult(
                kind="show_message",
                command=parsed,
                message="Usage: `/artifact {list,show,delete,import,create}`",
            )
        if parsed.name in {"/pack", "/packs"}:
            parts = prompt.split(maxsplit=2)
            sub = parts[1].lower() if len(parts) >= 2 else ""
            if parsed.name == "/packs":
                sub = "list"
            if sub in {"", "list"}:
                return CommandResult(kind="show_message", command=parsed, message=self._pack_list_markdown())
            if sub == "show":
                target = parts[2].strip() if len(parts) >= 3 else ""
                return CommandResult(kind="show_message", command=parsed, message=self._pack_show_markdown(target))
            if sub == "remove":
                target = parts[2].strip() if len(parts) >= 3 else ""
                return CommandResult(kind="show_message", command=parsed, message=self._remove_pack(target))
            if sub == "sources":
                return CommandResult(kind="show_message", command=parsed, message=self._pack_sources_markdown())
            if sub in {"install", "update", "attach", "detach", "refresh", "add-source"}:
                return CommandResult(
                    kind="show_message",
                    command=parsed,
                    message="Use the CLI for this pack operation: `aroom pack ...`",
                )
            return CommandResult(
                kind="show_message",
                command=parsed,
                message="Usage: `/pack [list|show|remove|sources|install|update|attach|detach|refresh|add-source]`",
            )
        if parsed.name == "/verbose":
            return CommandResult(
                kind="show_message",
                command=parsed,
                message=self._cycle_verbosity_message(),
            )
        if parsed.name == "/detail":
            return CommandResult(
                kind="show_message",
                command=parsed,
                message=self._tool_detail_markdown(),
            )
        if parsed.name == "/rewind":
            return CommandResult(
                kind="show_message",
                command=parsed,
                message=await self.rewind_current_conversation(parsed.arg),
            )
        if parsed.name == "/delete":
            return CommandResult(
                kind="delete_conversation",
                command=parsed,
                delete_target=parsed.arg,
                echo_user=False,
            )
        if parsed.name == "/compact":
            return CommandResult(
                kind="compact_conversation",
                command=parsed,
                echo_user=False,
            )
        if parsed.name == "/rename":
            return CommandResult(kind="show_message", command=parsed, message=self._rename_conversation(parsed.arg))
        if parsed.name == "/slug":
            return CommandResult(kind="show_message", command=parsed, message=self._slug_command(parsed.arg))
        if parsed.name in {"/conventions", "/instructions"}:
            return CommandResult(kind="show_message", command=parsed, message=self._conventions_markdown())
        return CommandResult(
            kind="show_message",
            command=parsed,
            message=(
                f"`{parsed.name}` isn't wired into the Textual UI yet. "
                "Use `/help` to see the supported commands in this interface."
            ),
        )

    def _record_tool_start(self, tool_name: str, arguments: dict[str, Any]) -> None:
        self._current_turn_tools.append(
            {
                "tool_name": tool_name,
                "arguments": arguments,
                "status": "active",
                "output": None,
                "started_at": time.monotonic(),
                "elapsed": 0.0,
            }
        )

    def _record_tool_end(self, tool_name: str, status: str, output: Any) -> None:
        for item in reversed(self._current_turn_tools):
            if item["tool_name"] == tool_name and item.get("status") == "active":
                item["status"] = status
                item["output"] = output
                item["elapsed"] = max(0.0, time.monotonic() - item.get("started_at", time.monotonic()))
                return

    def _cycle_verbosity_message(self) -> str:
        order = ["compact", "detailed", "verbose"]
        current_index = order.index(self._verbosity)
        self._verbosity = order[(current_index + 1) % len(order)]
        return f"Verbosity: **{self._verbosity}**"

    def _tool_detail_markdown(self) -> str:
        if not self._last_turn_tools:
            return "No tool calls in the last turn."

        lines = ["## Last Turn Tool Calls", ""]
        for item in self._last_turn_tools:
            status = item.get("status", "unknown")
            icon = "✓" if status == "success" else "!"
            elapsed = item.get("elapsed", 0.0) or 0.0
            elapsed_text = f" ({elapsed:.1f}s)" if elapsed >= 0.1 else ""
            lines.append(f"- {icon} **{item['tool_name']}**{elapsed_text}")
            arguments = item.get("arguments") or {}
            if arguments:
                lines.append("  ```json")
                lines.append(_pretty_json(arguments))
                lines.append("  ```")
            output = item.get("output")
            if output:
                lines.append("  Output:")
                lines.extend(f"  {line}" for line in _summarize_tool_output(output).splitlines())
            lines.append("")
        return "\n".join(lines).rstrip()

    def _plain_history(self) -> list[tuple[str, str]]:
        if not self._conversation:
            return []
        result: list[tuple[str, str]] = []
        for msg in storage.list_messages(self.db, self._conversation["id"]):
            role = msg.get("role")
            if role in ("user", "assistant") and msg.get("content"):
                result.append((role, msg["content"]))
        return result

    def _identity_kwargs(self) -> dict[str, Any]:
        identity = getattr(self.config, "identity", None)
        if identity:
            return {"user_id": identity.user_id, "user_display_name": identity.display_name}
        return {"user_id": None, "user_display_name": None}

    def _expand_file_references(self, text: str) -> str:
        from .repl import _expand_file_references

        return _expand_file_references(text, self.working_dir, file_max_chars=self.config.cli.file_reference_max_chars)

    def _load_conversation_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        from .repl import _load_conversation_messages

        return _load_conversation_messages(self.db, conversation_id)

    def _tool_names(self) -> list[str]:
        names: list[str] = []
        for tool in self.tools_openai or []:
            function = tool.get("function") if isinstance(tool, dict) else None
            if isinstance(function, dict) and function.get("name"):
                names.append(str(function["name"]))
        return names

    def _refresh_skill_tool_schema(self) -> None:
        if self.tools_openai is None:
            self.tools_openai = []
        self.tools_openai = [
            tool
            for tool in self.tools_openai
            if not (isinstance(tool, dict) and tool.get("function", {}).get("name") == "invoke_skill")
        ]
        if self.config.cli.skills.auto_invoke and self.skill_registry is not None:
            invoke_def = self.skill_registry.get_invoke_skill_definition()
            if invoke_def:
                self.tools_openai.append(invoke_def)

    def _usage_markdown(self) -> str:
        now = datetime.now(timezone.utc)
        periods = [
            ("Today", now - timedelta(days=1)),
            ("This week", now - timedelta(days=self.config.cli.usage.week_days)),
            ("This month", now - timedelta(days=self.config.cli.usage.month_days)),
            ("All time", None),
        ]
        lines = ["## Usage"]
        for label, since_dt in periods:
            stats = storage.get_usage_stats(self.db, since=since_dt.isoformat() if since_dt else None)
            total_tokens = sum(s.get("total_tokens", 0) or 0 for s in stats)
            total_messages = sum(s.get("message_count", 0) or 0 for s in stats)
            lines.append(f"- **{label}:** {total_tokens:,} tokens across {total_messages} messages")
        return "\n".join(lines)


async def run_textual_chat(
    *,
    backend: TextualChatBackend,
    session: SessionSnapshot,
    initial_prompt: str | None = None,
    ui_bridge: dict[str, Any] | None = None,
) -> None:
    app = TextualChatApp(backend=backend, session=session, initial_prompt=initial_prompt, ui_bridge=ui_bridge)
    await app.run_async()

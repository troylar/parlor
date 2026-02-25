"""Conversation picker helpers for the CLI REPL."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..services import storage
from . import renderer
from .renderer import CHROME


def picker_relative_time(ts: str) -> str:
    """Format a timestamp as a relative time string for the picker UI."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - dt
        if delta.days > 0:
            return f"{delta.days}d ago"
        hours = delta.seconds // 3600
        if hours > 0:
            return f"{hours}h ago"
        mins = delta.seconds // 60
        return f"{mins}m ago" if mins > 0 else "just now"
    except (ValueError, TypeError):
        return ""


def picker_type_badge(conv_type: str) -> str:
    """Return a type badge string for non-chat conversation types."""
    return {"note": "[note]", "document": "[doc]"}.get(conv_type, "")


def picker_format_preview(messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Format messages into styled fragments for the picker preview pane."""
    fragments: list[tuple[str, str]] = []
    recent = messages[-8:] if len(messages) > 8 else messages
    for msg in recent:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content or not isinstance(content, str):
            continue
        if role == "user":
            fragments.append(("class:preview.role-user", " You: "))
            text = content[:200].replace("\n", " ")
            if len(content) > 200:
                text += "..."
            fragments.append(("class:preview.content", f"{text}\n\n"))
        elif role == "assistant":
            fragments.append(("class:preview.role-ai", " AI: "))
            text = content[:300].replace("\n", " ")
            if len(content) > 300:
                text += "..."
            fragments.append(("class:preview.content", f"{text}\n\n"))
    if not fragments:
        fragments.append(("class:preview.empty", " (no messages)"))
    return fragments


def resolve_conversation(db: Any, target: str) -> dict[str, Any] | None:
    """Resolve a target (list number, UUID, or slug) to a conversation dict."""
    if target.isdigit():
        idx = int(target) - 1
        convs = storage.list_conversations(db, limit=20)
        if 0 <= idx < len(convs):
            return storage.get_conversation(db, convs[idx]["id"])
        return None
    return storage.get_conversation(db, target)


def show_resume_info(db: Any, conv: dict[str, Any], ai_messages: list[dict[str, Any]]) -> None:
    """Display resume header with last exchange context."""
    stored = storage.list_messages(db, conv["id"])
    title = conv.get("title", "Untitled")
    renderer.console.print(f"[{CHROME}]Resumed: {title} ({len(ai_messages)} messages)[/{CHROME}]")
    renderer.render_conversation_recap(stored)


async def show_resume_picker(db: Any) -> dict[str, Any] | None:
    """Show an interactive conversation picker with preview panel."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings as PickerKB
    from prompt_toolkit.layout.containers import HSplit, VSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.layout import Layout
    from prompt_toolkit.styles import Style

    convs = storage.list_conversations(db, limit=20)
    if not convs:
        renderer.console.print(f"[{CHROME}]No conversations found[/{CHROME}]\n")
        return None

    selected_idx = [0]
    preview_cache: dict[str, list[dict[str, Any]]] = {}

    def _get_list_text() -> FormattedText:
        fragments: list[tuple[str, str]] = []
        for i, c in enumerate(convs):
            is_sel = i == selected_idx[0]
            title = (c.get("title") or "Untitled")[:35]
            slug = (c.get("slug") or "")[:20]
            badge = picker_type_badge(c.get("type") or "chat")
            ts = picker_relative_time(c.get("updated_at") or "")
            count = c.get("message_count") or 0

            if is_sel:
                fragments.append(("class:list.selected", f" > {title}"))
                if badge:
                    fragments.append(("class:list.badge", f" {badge}"))
                fragments.append(("class:list.selected-meta", f"  {slug}  {count}msg  {ts}"))
                fragments.append(("class:list.selected", "\n"))
            else:
                fragments.append(("class:list.item", f"   {title}"))
                if badge:
                    fragments.append(("class:list.badge", f" {badge}"))
                fragments.append(("class:list.meta", f"  {slug}  {count}msg  {ts}"))
                fragments.append(("class:list.item", "\n"))
        return FormattedText(fragments)

    def _get_preview_text() -> FormattedText:
        if not convs:
            return FormattedText([])
        conv_id = convs[selected_idx[0]]["id"]
        if conv_id not in preview_cache:
            preview_cache[conv_id] = storage.list_messages(db, conv_id)
        msgs = preview_cache[conv_id]
        return FormattedText(picker_format_preview(msgs))

    list_control = FormattedTextControl(_get_list_text)
    preview_control = FormattedTextControl(_get_preview_text)

    def _refresh() -> None:
        list_control.text = _get_list_text()
        preview_control.text = _get_preview_text()

    picker_kb = PickerKB()
    result: list[dict[str, Any] | None] = [None]

    @picker_kb.add("up")
    @picker_kb.add("k")
    def _up(event: Any) -> None:
        if selected_idx[0] > 0:
            selected_idx[0] -= 1
            _refresh()

    @picker_kb.add("down")
    @picker_kb.add("j")
    def _down(event: Any) -> None:
        if selected_idx[0] < len(convs) - 1:
            selected_idx[0] += 1
            _refresh()

    @picker_kb.add("enter")
    def _select(event: Any) -> None:
        result[0] = convs[selected_idx[0]]
        event.app.exit()

    @picker_kb.add("escape")
    @picker_kb.add("c-c")
    def _cancel(event: Any) -> None:
        event.app.exit()

    separator = Window(width=1, char="\u2502", style="class:separator")
    body = VSplit(
        [
            Window(content=list_control, width=50, wrap_lines=False),
            separator,
            Window(content=preview_control, wrap_lines=True),
        ]
    )
    title_bar = Window(
        content=FormattedTextControl(
            FormattedText(
                [
                    ("class:title", " Resume Conversation  "),
                    ("class:hint", " \u2191\u2193 navigate  Enter select  Esc cancel "),
                ]
            )
        ),
        height=1,
    )
    layout = Layout(HSplit([title_bar, body]))

    style = Style.from_dict(
        {
            "title": "bg:#C5A059 #1a1a2e bold",
            "hint": "bg:#3a3a4e #94A3B8",
            "separator": "#3a3a4e",
            "list.selected": "bg:#2a2a3e #C5A059 bold",
            "list.selected-meta": "bg:#2a2a3e #94A3B8",
            "list.item": "#e0e0e0",
            "list.meta": "#6b7280",
            "list.badge": "#C5A059 italic",
            "preview.role-user": "#C5A059 bold",
            "preview.role-ai": "#94A3B8 bold",
            "preview.content": "#e0e0e0",
            "preview.empty": "#6b7280 italic",
        }
    )

    app: Application[None] = Application(
        layout=layout,
        key_bindings=picker_kb,
        style=style,
        full_screen=True,
    )
    await app.run_async()
    return result[0]

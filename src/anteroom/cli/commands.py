"""Slash command handlers extracted from the REPL loop."""

from __future__ import annotations

import logging
import sqlite3 as _sqlite3
import subprocess
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any

from ..services import storage
from ..services.rewind import collect_file_paths
from ..services.rewind import rewind_conversation as rewind_service
from ..services.slug import is_valid_slug, suggest_unique_slug
from . import renderer
from .instructions import discover_conventions
from .pickers import resolve_conversation, show_resume_info, show_resume_picker
from .renderer import CHROME, MUTED

logger = logging.getLogger(__name__)


class CommandResult(Enum):
    """Outcome of a slash command, telling the main loop what to do next."""

    CONTINUE = auto()
    EXIT = auto()
    FALL_THROUGH = auto()


@dataclass
class ReplSession:
    """Mutable REPL state that slash commands need to read/write."""

    conv: dict[str, Any]
    ai_messages: list[dict[str, Any]]
    is_first_message: bool
    current_model: str
    tools_openai: list[dict[str, Any]] | None
    extra_system_prompt: str
    all_tool_names: list[str]
    db: Any
    config: Any
    working_dir: str
    ai_service: Any
    identity_kwargs: dict[str, str | None] = field(default_factory=dict)
    skill_registry: Any = None
    mcp_manager: Any = None
    tool_registry: Any = None

    # Plan mode state
    plan_active: list[bool] = field(default_factory=lambda: [False])
    plan_file: list[Any] = field(default_factory=lambda: [None])
    plan_checklist_steps: list[str] = field(default_factory=list)
    plan_current_step: list[int] = field(default_factory=lambda: [0])

    # Callbacks
    apply_plan_mode: Any = None
    exit_plan_mode: Any = None
    rebuild_tools: Any = None
    compact_messages: Any = None
    create_ai_service_fn: Any = None


async def handle_slash_command(
    session: ReplSession,
    user_input: str,
) -> tuple[CommandResult, str]:
    """Handle a slash command, returning (result, possibly_modified_user_input).

    Returns CONTINUE if the command was fully handled (caller should loop).
    Returns EXIT if the user wants to quit.
    Returns FALL_THROUGH if user_input was modified and should be sent to the agent.
    """
    cmd = user_input.lower().split()[0]
    s = session

    if cmd in ("/quit", "/exit"):
        return CommandResult.EXIT, user_input

    elif cmd == "/new":
        parts = user_input.split(maxsplit=2)
        conv_type = "chat"
        conv_title = "New Conversation"
        if len(parts) >= 2 and parts[1] in ("note", "doc", "document"):
            conv_type = "document" if parts[1] in ("doc", "document") else "note"
            conv_title = parts[2].strip() if len(parts) >= 3 else f"New {conv_type.title()}"
        s.conv = storage.create_conversation(s.db, title=conv_title, conversation_type=conv_type, **s.identity_kwargs)
        s.ai_messages = []
        s.is_first_message = conv_type == "chat"
        if s.plan_active[0] and s.apply_plan_mode:
            s.apply_plan_mode(s.conv["id"])
        type_label = f" ({conv_type})" if conv_type != "chat" else ""
        renderer.console.print(f"[{CHROME}]New conversation started{type_label}[/{CHROME}]\n")
        return CommandResult.CONTINUE, user_input

    elif cmd == "/append":
        parts = user_input.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            renderer.console.print(f"[{CHROME}]Usage: /append <text>[/{CHROME}]\n")
            return CommandResult.CONTINUE, user_input
        current_type = s.conv.get("type", "chat")
        if current_type != "note":
            renderer.render_error("Current conversation is not a note. Use /new note <title> first.")
            return CommandResult.CONTINUE, user_input
        entry_text = parts[1].strip()
        storage.create_message(s.db, s.conv["id"], "user", entry_text, **s.identity_kwargs)
        renderer.console.print(f"[{CHROME}]Entry added to '{s.conv.get('title', 'Untitled')}'[/{CHROME}]\n")
        return CommandResult.CONTINUE, user_input

    elif cmd == "/tools":
        renderer.render_tools(s.all_tool_names)
        return CommandResult.CONTINUE, user_input

    elif cmd == "/conventions":
        _handle_conventions(s.working_dir)
        return CommandResult.CONTINUE, user_input

    elif cmd == "/upload":
        await _handle_upload(s, user_input)
        return CommandResult.CONTINUE, user_input

    elif cmd == "/usage":
        from .repl import _show_usage_stats

        _show_usage_stats(s.db, s.config)
        return CommandResult.CONTINUE, user_input

    elif cmd == "/help":
        from .dialogs import show_help_dialog

        await show_help_dialog()
        return CommandResult.CONTINUE, user_input

    elif cmd == "/compact":
        if s.compact_messages:
            await s.compact_messages(s.ai_service, s.ai_messages, s.db, s.conv["id"])
        return CommandResult.CONTINUE, user_input

    elif cmd == "/last":
        convs = storage.list_conversations(s.db, limit=1)
        if convs:
            s.conv = storage.get_conversation(s.db, convs[0]["id"]) or s.conv
            from .repl import _load_conversation_messages

            s.ai_messages = _load_conversation_messages(s.db, s.conv["id"])
            s.is_first_message = False
            show_resume_info(s.db, s.conv, s.ai_messages)
        else:
            renderer.console.print(f"[{CHROME}]No previous conversations[/{CHROME}]\n")
        return CommandResult.CONTINUE, user_input

    elif cmd == "/list":
        _handle_list(s, user_input)
        return CommandResult.CONTINUE, user_input

    elif cmd == "/delete":
        _handle_delete(s, user_input)
        return CommandResult.CONTINUE, user_input

    elif cmd == "/rename":
        _handle_rename(s, user_input)
        return CommandResult.CONTINUE, user_input

    elif cmd == "/slug":
        _handle_slug(s, user_input)
        return CommandResult.CONTINUE, user_input

    elif cmd == "/search":
        await _handle_search(s, user_input)
        return CommandResult.CONTINUE, user_input

    elif cmd == "/skills":
        _handle_skills(s)
        return CommandResult.CONTINUE, user_input

    elif cmd == "/mcp":
        await _handle_mcp(s, user_input)
        return CommandResult.CONTINUE, user_input

    elif cmd == "/model":
        _handle_model(s, user_input)
        return CommandResult.CONTINUE, user_input

    elif cmd == "/plan":
        return await _handle_plan(s, user_input)

    elif cmd == "/verbose":
        new_v = renderer.cycle_verbosity()
        renderer.render_verbosity_change(new_v)
        return CommandResult.CONTINUE, user_input

    elif cmd == "/detail":
        renderer.render_tool_detail()
        return CommandResult.CONTINUE, user_input

    elif cmd == "/resume":
        await _handle_resume(s, user_input)
        return CommandResult.CONTINUE, user_input

    elif cmd == "/rewind":
        await _handle_rewind(s, user_input)
        return CommandResult.CONTINUE, user_input

    # Unknown command — fall through (might be a skill invocation handled later)
    return CommandResult.FALL_THROUGH, user_input


def _handle_conventions(working_dir: str) -> None:
    info = discover_conventions(working_dir)
    if info.source == "none":
        renderer.console.print(
            f"[{CHROME}]No conventions file found.[/{CHROME}]\n"
            f"  [{MUTED}]Create ANTEROOM.md in your project root to define conventions.[/{MUTED}]\n"
        )
    else:
        label = "Project" if info.source == "project" else "Global"
        renderer.console.print(f"\n[bold]{label} conventions:[/bold] {info.path}")
        renderer.console.print(f"  [{MUTED}]~{info.estimated_tokens:,} tokens[/{MUTED}]")
        if info.warning:
            renderer.console.print(f"  [yellow]{info.warning}[/yellow]")
        renderer.console.print()
        lines = (info.content or "").splitlines()
        preview = lines[:50]
        for line in preview:
            renderer.console.print(f"  {line}")
        if len(lines) > 50:
            renderer.console.print(
                f"\n  [{MUTED}]... {len(lines) - 50} more lines. View full file: {info.path}[/{MUTED}]"
            )
        renderer.console.print()


async def _handle_upload(s: ReplSession, user_input: str) -> None:
    parts = user_input.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        renderer.console.print(f"[{CHROME}]Usage: /upload <path>[/{CHROME}]\n")
        return
    upload_path = Path(parts[1].strip()).expanduser().resolve()
    if not upload_path.is_file():
        renderer.console.print(f"[{CHROME}]File not found: {upload_path}[/{CHROME}]\n")
        return
    try:
        import mimetypes

        import filetype as _ft

        max_size_mb = 10
        max_size = max_size_mb * 1024 * 1024
        file_size = upload_path.stat().st_size
        if file_size > max_size:
            size_mb = file_size // (1024 * 1024)
            renderer.console.print(
                f"[{CHROME}]File too large ({size_mb} MB). Maximum is {max_size_mb} MB.[/{CHROME}]\n"
            )
            return
        file_data = upload_path.read_bytes()
        guess = _ft.guess(file_data)
        mime = guess.mime if guess else (mimetypes.guess_type(str(upload_path))[0] or "text/plain")
        source = storage.save_source_file(
            s.db,
            title=upload_path.name,
            filename=upload_path.name,
            mime_type=mime,
            data=file_data,
            data_dir=s.config.app.data_dir,
            user_id=s.config.identity.user_id if s.config.identity else None,
            user_display_name=s.config.identity.display_name
            if s.config.identity and hasattr(s.config.identity, "display_name")
            else None,
        )
        renderer.console.print(
            f"[{CHROME}]Uploaded {upload_path.name} \u2192 source {source['id'][:8]}\u2026[/{CHROME}]"
        )
        if source.get("content"):
            renderer.console.print(f"  [{MUTED}]{mime}, {len(source['content']):,} chars extracted[/{MUTED}]")
        else:
            renderer.console.print(f"  [{MUTED}]{mime}, stored (no text extracted)[/{MUTED}]")
        renderer.console.print()
    except Exception:
        logger.error("CLI upload failed", exc_info=True)
        renderer.console.print(f"[{CHROME}]Upload failed[/{CHROME}]\n")


def _handle_list(s: ReplSession, user_input: str) -> None:
    parts = user_input.split()
    list_limit = 20
    if len(parts) >= 2 and parts[1].isdigit():
        list_limit = max(1, int(parts[1]))
    convs = storage.list_conversations(s.db, limit=list_limit + 1)
    has_more = len(convs) > list_limit
    display_convs = convs[:list_limit]
    if display_convs:
        renderer.console.print("\n[bold]Recent conversations:[/bold]")
        for i, c in enumerate(display_convs):
            msg_count = c.get("message_count", 0)
            ctype = c.get("type", "chat")
            type_badge = f" [cyan]\\[{ctype}][/cyan]" if ctype != "chat" else ""
            slug_label = f" [{MUTED}]{c['slug']}[/{MUTED}]" if c.get("slug") else ""
            renderer.console.print(f"  {i + 1}. {c['title']}{type_badge} ({msg_count} msgs){slug_label}")
        if has_more:
            more_n = list_limit + 20
            msg = f"... more available. Use /list {more_n} to show more."
            renderer.console.print(f"  [{MUTED}]{msg}[/{MUTED}]")
        renderer.console.print("  Use [bold]/resume <number>[/bold] or [bold]/resume <slug>[/bold]\n")
    else:
        renderer.console.print(f"[{CHROME}]No conversations[/{CHROME}]\n")


def _handle_delete(s: ReplSession, user_input: str) -> None:
    parts = user_input.split(maxsplit=1)
    if len(parts) < 2:
        renderer.console.print(f"[{CHROME}]Usage: /delete <number|slug|id>[/{CHROME}]\n")
        return
    target = parts[1].strip()
    to_delete = resolve_conversation(s.db, target)
    if not to_delete:
        renderer.render_error(f"Conversation not found: {target}. Use /list to see conversations.")
        return
    title = to_delete.get("title", "Untitled")
    try:
        answer = input(f'  Delete "{title}"? [y/N] ').strip().lower()
    except (EOFError, KeyboardInterrupt):
        renderer.console.print(f"[{CHROME}]Cancelled[/{CHROME}]\n")
        return
    if answer not in ("y", "yes"):
        renderer.console.print(f"[{CHROME}]Cancelled[/{CHROME}]\n")
        return
    storage.delete_conversation(s.db, to_delete["id"], s.config.app.data_dir)
    renderer.console.print(f"[{CHROME}]Deleted: {title}[/{CHROME}]\n")
    if s.conv.get("id") == to_delete["id"]:
        s.conv = storage.create_conversation(s.db, **s.identity_kwargs)
        s.ai_messages = []
        s.is_first_message = True


def _handle_rename(s: ReplSession, user_input: str) -> None:
    parts = user_input.split(maxsplit=2)
    if len(parts) < 2:
        renderer.console.print(f"[{CHROME}]Usage: /rename <title> or /rename <N|id|slug> <title>[/{CHROME}]\n")
        return
    first_arg = parts[1].strip()
    looks_like_target = first_arg.isdigit() or ("-" in first_arg and len(first_arg) >= 36)
    if not looks_like_target and len(parts) == 3:
        maybe_conv = storage.get_conversation(s.db, first_arg)
        if maybe_conv:
            looks_like_target = True
    if len(parts) == 3 and looks_like_target:
        target = parts[1].strip()
        new_title = parts[2].strip()
        resolved = resolve_conversation(s.db, target)
        if not resolved:
            renderer.render_error(f"Conversation not found: {target}. Use /list to see conversations.")
            return
        resolved_id = resolved["id"]
    else:
        new_title = user_input.split(maxsplit=1)[1].strip()
        resolved_id = s.conv.get("id")
    if not resolved_id:
        renderer.render_error("No active conversation to rename.")
        return
    if not new_title:
        renderer.console.print(f"[{CHROME}]Usage: /rename <title> or /rename <N|id|slug> <title>[/{CHROME}]\n")
        return
    storage.update_conversation_title(s.db, resolved_id, new_title)
    renderer.console.print(f'[{CHROME}]Renamed conversation to "{new_title}"[/{CHROME}]\n')
    if s.conv.get("id") == resolved_id:
        s.conv["title"] = new_title


def _handle_slug(s: ReplSession, user_input: str) -> None:
    parts = user_input.split(maxsplit=1)
    if len(parts) < 2:
        current_slug = s.conv.get("slug", "none")
        renderer.console.print(f"[{CHROME}]Slug: {current_slug}[/{CHROME}]\n")
        return
    desired = parts[1].strip().lower()
    if not s.conv.get("id"):
        renderer.render_error("No active conversation.")
        return
    if not is_valid_slug(desired):
        renderer.render_error("Invalid slug. Use lowercase letters, numbers, and hyphens (e.g. my-project).")
        return
    suggestion = suggest_unique_slug(s.db, desired)
    if suggestion is None:
        try:
            storage.update_conversation_slug(s.db, s.conv["id"], desired)
            s.conv["slug"] = desired
            renderer.console.print(f"[{CHROME}]Slug set to: {desired}[/{CHROME}]\n")
        except _sqlite3.IntegrityError:
            fallback = suggest_unique_slug(s.db, desired)
            renderer.render_error(f'"{desired}" is taken. Try: {fallback}')
    else:
        renderer.console.print(f'[{CHROME}]"{desired}" is taken. Suggestion: {suggestion}[/{CHROME}]\n')


async def _handle_search(s: ReplSession, user_input: str) -> None:
    parts = user_input.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        renderer.console.print(f"[{CHROME}]Usage: /search <query> | /search --keyword <query>[/{CHROME}]\n")
        return
    search_arg = parts[1].strip()

    force_keyword = False
    type_filter = None
    if search_arg.startswith("--keyword "):
        force_keyword = True
        search_arg = search_arg[len("--keyword ") :].strip()
        if not search_arg:
            renderer.console.print(f"[{CHROME}]Usage: /search --keyword <query>[/{CHROME}]\n")
            return
    elif search_arg.startswith("--type "):
        rest = search_arg[len("--type ") :].strip()
        type_parts = rest.split(maxsplit=1)
        if type_parts and type_parts[0] in ("chat", "note", "document"):
            type_filter = type_parts[0]
            search_arg = type_parts[1] if len(type_parts) > 1 else ""
        else:
            renderer.render_error("Invalid type. Use: chat, note, or document")
            return
        if not search_arg:
            renderer.console.print(f"[{CHROME}]Usage: /search --type <type> <query>[/{CHROME}]\n")
            return

    query = search_arg

    use_semantic = False
    _emb_svc = None
    if not force_keyword:
        try:
            from ..db import has_vec_support as _has_vec
            from ..services.embeddings import create_embedding_service as _create_emb

            raw_conn = s.db._conn if hasattr(s.db, "_conn") else None
            if raw_conn and _has_vec(raw_conn):
                _emb_svc = _create_emb(s.config)
                if _emb_svc:
                    use_semantic = True
        except Exception:
            pass

    if use_semantic and _emb_svc is not None:
        try:
            query_emb = await _emb_svc.embed(query)
            if query_emb:
                sem_results = storage.search_similar_messages(s.db, query_emb, limit=20)
                if sem_results:
                    renderer.console.print(f"\n[bold]Semantic search results for '{query}':[/bold]")
                    for i, r in enumerate(sem_results):
                        snippet = r["content"][:80].replace("\n", " ")
                        dist = r.get("distance", 0)
                        relevance = max(0, 100 - int(dist * 100))
                        renderer.console.print(
                            f"  {i + 1}. [{r['role']}] {snippet}... "
                            f"[{CHROME}]({relevance}% match, {r['conversation_id'][:8]}...)[/{CHROME}]"
                        )
                    renderer.console.print()
                    return
        except Exception:
            pass

    results = storage.list_conversations(s.db, search=query, limit=20, conversation_type=type_filter)
    if results:
        renderer.console.print(f"\n[bold]Search results for '{query}':[/bold]")
        for i, c in enumerate(results):
            msg_count = c.get("message_count", 0)
            renderer.console.print(f"  {i + 1}. {c['title']} ({msg_count} msgs) [{CHROME}]{c['id'][:8]}...[/{CHROME}]")
        renderer.console.print("  Use [bold]/resume <number|slug>[/bold] to open\n")
    else:
        renderer.console.print(f"[{CHROME}]No conversations matching '{query}'[/{CHROME}]\n")


def _handle_skills(s: ReplSession) -> None:
    if s.skill_registry:
        skills = s.skill_registry.list_skills()
        if skills:
            renderer.console.print("\n[bold]Available skills:[/bold]")
            for sk in skills:
                src = sk.source
                renderer.console.print(f"  /{sk.name} - {sk.description} [{CHROME}]({src})[/{CHROME}]")
            renderer.console.print()
        else:
            renderer.console.print(
                f"[{CHROME}]No skills loaded. Add .yaml files to ~/.anteroom/skills/ or .anteroom/skills/[/{CHROME}]\n"
            )


async def _handle_mcp(s: ReplSession, user_input: str) -> None:
    parts = user_input.split()
    if len(parts) == 1:
        if s.mcp_manager:
            renderer.render_mcp_status(s.mcp_manager.get_server_statuses())
        else:
            renderer.console.print(f"[{CHROME}]No MCP servers configured.[/{CHROME}]\n")
    elif len(parts) >= 2 and parts[1].lower() == "status":
        if not s.mcp_manager:
            renderer.render_error("No MCP servers configured")
            return
        if len(parts) >= 3:
            renderer.render_mcp_server_detail(parts[2], s.mcp_manager.get_server_statuses(), s.mcp_manager)
        else:
            renderer.render_mcp_status(s.mcp_manager.get_server_statuses())
    elif len(parts) >= 3:
        action = parts[1].lower()
        server_name = parts[2]
        if not s.mcp_manager:
            renderer.render_error("No MCP servers configured")
            return
        try:
            if action == "connect":
                await s.mcp_manager.connect_server(server_name)
                status = s.mcp_manager.get_server_statuses().get(server_name, {})
                if status.get("status") == "connected":
                    renderer.console.print(f"[green]Connected: {server_name}[/green]\n")
                else:
                    err = status.get("error_message", "unknown error")
                    renderer.render_error(f"Failed to connect '{server_name}': {err}")
            elif action == "disconnect":
                await s.mcp_manager.disconnect_server(server_name)
                renderer.console.print(f"[{CHROME}]Disconnected: {server_name}[/{CHROME}]\n")
            elif action == "reconnect":
                await s.mcp_manager.reconnect_server(server_name)
                status = s.mcp_manager.get_server_statuses().get(server_name, {})
                if status.get("status") == "connected":
                    renderer.console.print(f"[green]Reconnected: {server_name}[/green]\n")
                else:
                    err = status.get("error_message", "unknown error")
                    renderer.render_error(f"Failed to reconnect '{server_name}': {err}")
            else:
                renderer.render_error(f"Unknown action: {action}. Use connect, disconnect, reconnect, or status.")
                return
            if s.rebuild_tools:
                s.rebuild_tools()
        except ValueError as e:
            renderer.render_error(str(e))
    else:
        renderer.console.print(
            f"[{CHROME}]Usage: /mcp [status [name]|connect|disconnect|reconnect <name>][/{CHROME}]\n"
        )


def _handle_model(s: ReplSession, user_input: str) -> None:
    parts = user_input.split(maxsplit=1)
    if len(parts) < 2:
        renderer.console.print(f"[{CHROME}]Current model: {s.current_model}[/{CHROME}]")
        renderer.console.print(f"[{CHROME}]Usage: /model <model_name>[/{CHROME}]\n")
        return
    new_model = parts[1].strip()
    s.current_model = new_model
    if s.create_ai_service_fn:
        s.ai_service = s.create_ai_service_fn(s.config.ai)
    s.ai_service.config.model = new_model
    renderer.console.print(f"[{CHROME}]Switched to model: {new_model}[/{CHROME}]\n")


async def _handle_plan(s: ReplSession, user_input: str) -> tuple[CommandResult, str]:
    from .plan import delete_plan, get_editor, parse_plan_command, parse_plan_steps, read_plan

    sub, inline_prompt = parse_plan_command(user_input)
    if sub in ("on", "start"):
        if s.plan_active[0]:
            renderer.console.print(f"[{CHROME}]Already in planning mode[/{CHROME}]\n")
        elif s.apply_plan_mode:
            s.apply_plan_mode(s.conv["id"])
            renderer.console.print(
                f"[yellow]Planning mode active.[/yellow] The AI will explore and write a plan.\n"
                f"  [{MUTED}]Use /plan approve to execute, /plan off to exit.[/{MUTED}]\n"
            )
        return CommandResult.CONTINUE, user_input
    elif sub == "approve":
        if not s.plan_active[0]:
            renderer.console.print(f"[{CHROME}]Not in planning mode[/{CHROME}]\n")
        elif s.plan_file[0] is None:
            renderer.console.print(f"[{CHROME}]No plan file path set[/{CHROME}]\n")
        else:
            content = read_plan(s.plan_file[0])
            if not content:
                renderer.console.print(
                    f"[{CHROME}]No plan file found at {s.plan_file[0]}[/{CHROME}]\n"
                    f"  [{MUTED}]The AI needs to write the plan first.[/{MUTED}]\n"
                )
            else:
                if s.exit_plan_mode:
                    s.exit_plan_mode(plan_content=content)
                delete_plan(s.plan_file[0])
                steps = parse_plan_steps(content)
                s.plan_checklist_steps.clear()
                s.plan_checklist_steps.extend(steps)
                s.plan_current_step[0] = 0
                if steps:
                    renderer.start_plan(steps)
                renderer.console.print(
                    "[green]Plan approved.[/green] Full tools restored.\n"
                    f"  [{MUTED}]Plan injected into context. "
                    f"Send a message to start.[/{MUTED}]\n"
                )
        return CommandResult.CONTINUE, user_input
    elif sub == "status":
        if s.plan_active[0]:
            renderer.console.print("[yellow]Planning mode: active[/yellow]")
            if s.plan_file[0]:
                content = read_plan(s.plan_file[0])
                if content:
                    renderer.console.print(f"  Plan file: {s.plan_file[0]} ({len(content)} chars)")
                    lines = content.splitlines()
                    preview = lines[:20]
                    renderer.console.print()
                    for line in preview:
                        renderer.console.print(f"  {line}")
                    if len(lines) > 20:
                        renderer.console.print(f"\n  [{MUTED}]... {len(lines) - 20} more lines[/{MUTED}]")
                else:
                    renderer.console.print(f"  [{MUTED}]Plan file: {s.plan_file[0]} (not yet written)[/{MUTED}]")
        else:
            renderer.console.print(f"[{CHROME}]Planning mode: off[/{CHROME}]")
        renderer.console.print()
        return CommandResult.CONTINUE, user_input
    elif sub == "edit":
        if not s.plan_active[0]:
            renderer.console.print(f"[{CHROME}]Not in planning mode[/{CHROME}]\n")
            return CommandResult.CONTINUE, user_input
        if s.plan_file[0] is None:
            renderer.console.print(f"[{CHROME}]No plan file path set[/{CHROME}]\n")
            return CommandResult.CONTINUE, user_input
        edit_args = user_input.split(maxsplit=2)
        edit_instruction = edit_args[2] if len(edit_args) > 2 else ""
        if edit_instruction:
            return CommandResult.FALL_THROUGH, f"Revise the plan based on this feedback: {edit_instruction}"
        else:
            content = read_plan(s.plan_file[0])
            if not content:
                renderer.console.print(
                    f"[{CHROME}]No plan file yet \u2014 the AI needs to write it first.[/{CHROME}]\n"
                )
                return CommandResult.CONTINUE, user_input
            editor = get_editor()
            subprocess.call([editor, str(s.plan_file[0])])
            renderer.console.print(
                "Plan updated. Use [bold]/plan status[/bold] to review, [bold]/plan approve[/bold] to execute.\n"
            )
            return CommandResult.CONTINUE, user_input
    elif sub == "reject":
        if not s.plan_active[0]:
            renderer.console.print(f"[{CHROME}]Not in planning mode[/{CHROME}]\n")
            return CommandResult.CONTINUE, user_input
        reject_parts = user_input.split(maxsplit=2)
        if len(reject_parts) < 3 or not reject_parts[2].strip():
            renderer.console.print(f"[{CHROME}]Usage: /plan reject <reason for rejection>[/{CHROME}]\n")
            return CommandResult.CONTINUE, user_input
        reason = reject_parts[2].strip()
        modified = (
            f"The plan has been rejected. Reason: {reason}\n\n"
            "Please revise the plan based on this feedback and write the updated "
            "plan to the same plan file. Keep exploring if you need more information."
        )
        return CommandResult.FALL_THROUGH, modified
    elif sub == "off":
        if not s.plan_active[0]:
            renderer.console.print(f"[{CHROME}]Not in planning mode[/{CHROME}]\n")
        elif s.exit_plan_mode:
            s.exit_plan_mode()
            renderer.console.print(f"[{CHROME}]Planning mode off. Full tools restored.[/{CHROME}]\n")
        return CommandResult.CONTINUE, user_input
    else:
        if not inline_prompt:
            renderer.console.print(
                f"[{CHROME}]Usage: /plan [on|approve|status|edit|reject|off] or /plan <prompt>[/{CHROME}]\n"
            )
            return CommandResult.CONTINUE, user_input
        if not s.plan_active[0] and s.apply_plan_mode:
            s.apply_plan_mode(s.conv["id"])
            renderer.console.print("[yellow]Planning mode active.[/yellow]\n")
        return CommandResult.FALL_THROUGH, inline_prompt


async def _handle_resume(s: ReplSession, user_input: str) -> None:
    parts = user_input.split(maxsplit=1)
    if len(parts) < 2:
        picked = await show_resume_picker(s.db)
        if picked is None:
            return
        loaded = storage.get_conversation(s.db, picked["id"])
    else:
        target = parts[1].strip()
        loaded = resolve_conversation(s.db, target)
    if not loaded:
        renderer.render_error("Conversation not found. Use /list to see conversations.")
        return
    s.conv = loaded
    from .repl import _load_conversation_messages

    s.ai_messages = _load_conversation_messages(s.db, s.conv["id"])
    s.is_first_message = False
    show_resume_info(s.db, s.conv, s.ai_messages)


async def _handle_rewind(s: ReplSession, user_input: str) -> None:
    stored = storage.list_messages(s.db, s.conv["id"])
    if len(stored) < 2:
        renderer.console.print(f"[{CHROME}]Not enough messages to rewind[/{CHROME}]\n")
        return

    renderer.console.print("\n[bold]Messages:[/bold]")
    for msg in stored:
        role_label = "You" if msg["role"] == "user" else "AI"
        preview = msg["content"][:80].replace("\n", " ")
        if len(msg["content"]) > 80:
            preview += "..."
        renderer.console.print(f"  {msg['position']}. [{role_label}] {preview}")

    renderer.console.print(f"\n[{CHROME}]Enter position to rewind to (keep that message, delete after):[/{CHROME}]")
    try:
        pos_input = input("  Position: ").strip()
    except (EOFError, KeyboardInterrupt):
        renderer.console.print(f"[{CHROME}]Cancelled[/{CHROME}]\n")
        return

    if not pos_input.isdigit():
        renderer.render_error("Invalid position")
        return

    target_pos = int(pos_input)
    positions = [m["position"] for m in stored]
    if target_pos not in positions:
        renderer.render_error(f"Position {target_pos} not found")
        return

    msgs_after = [m for m in stored if m["position"] > target_pos]
    msg_ids_after = [m["id"] for m in msgs_after]
    file_paths = collect_file_paths(s.db, msg_ids_after)

    undo_files = False
    if file_paths:
        renderer.console.print(f"\n[yellow]{len(file_paths)} file(s) were modified after this point:[/yellow]")
        for fp in sorted(file_paths):
            renderer.console.print(f"  - {fp}")
        try:
            answer = input("  Undo file changes? [y/N] ").strip().lower()
            undo_files = answer in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            renderer.console.print(f"[{CHROME}]Cancelled[/{CHROME}]\n")
            return

    result = await rewind_service(
        db=s.db,
        conversation_id=s.conv["id"],
        to_position=target_pos,
        undo_files=undo_files,
        working_dir=s.working_dir,
    )

    from .repl import _load_conversation_messages

    s.ai_messages = _load_conversation_messages(s.db, s.conv["id"])

    summary = f"Rewound {result.deleted_messages} message(s)"
    if result.reverted_files:
        summary += f", reverted {len(result.reverted_files)} file(s)"
    if result.skipped_files:
        summary += f", {len(result.skipped_files)} skipped"
    renderer.console.print(f"[{CHROME}]{summary}[/{CHROME}]\n")

    if result.skipped_files:
        for sf in result.skipped_files:
            renderer.console.print(f"  [yellow]Skipped: {sf}[/yellow]")
        renderer.console.print()

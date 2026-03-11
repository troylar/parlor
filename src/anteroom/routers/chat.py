"""Chat streaming endpoint with SSE."""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import os
import re
import time as time_mod
import uuid as uuid_mod
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from ..cli.commands import (
    CommandContext,
    CommandResult,
    build_help_markdown,
    build_skills_markdown,
    build_tools_markdown,
    execute_slash_command,
    parse_slash_command,
)
from ..cli.instructions import load_instructions
from ..config import CliConfig, build_runtime_context
from ..models import ChatRequest
from ..services import artifact_storage, storage
from ..services import packs as packs_service
from ..services.agent_loop import _build_compaction_history
from ..services.ai_service import AIService, create_ai_service
from ..services.artifacts import validate_fqn
from ..services.context_trust import trusted_section_marker, untrusted_section_marker, wrap_untrusted
from ..services.pack_attachments import list_attachments_for_pack
from ..services.pack_sources import list_cached_sources
from ..services.rewind import collect_file_paths
from ..services.rewind import rewind_conversation as rewind_service
from ..services.slug import is_valid_slug, suggest_unique_slug
from ..services.space_storage import (
    count_space_conversations,
    create_space,
    delete_space,
    get_space,
    get_space_paths,
    get_spaces_by_name,
    list_spaces,
    resolve_space,
    update_conversation_space,
    update_space,
)
from ..tools.path_utils import safe_resolve_pathlib

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

_CANVAS_STREAMING_TOOLS = {"create_canvas", "update_canvas"}
MAX_CANVAS_ARGS_ACCUM = 100_000 + 1024


def _extract_streaming_content(accumulated_args: str) -> str | None:
    """Extract partial 'content' value from an incomplete JSON argument string.

    Parses just enough of the accumulating JSON to pull out the content value
    as it streams in character-by-character. Returns None if the "content" key
    hasn't appeared yet.
    """
    key = '"content"'
    key_pos = accumulated_args.find(key)
    if key_pos == -1:
        return None

    # Skip past key, optional whitespace, colon, optional whitespace, opening quote
    pos = key_pos + len(key)
    length = len(accumulated_args)

    # Skip whitespace
    while pos < length and accumulated_args[pos] in " \t\n\r":
        pos += 1
    # Expect colon
    if pos >= length or accumulated_args[pos] != ":":
        return None
    pos += 1
    # Skip whitespace
    while pos < length and accumulated_args[pos] in " \t\n\r":
        pos += 1
    # Expect opening quote
    if pos >= length or accumulated_args[pos] != '"':
        return None
    pos += 1

    # Now decode the JSON string value (handling escape sequences)
    result: list[str] = []
    while pos < length:
        ch = accumulated_args[pos]
        if ch == '"':
            # End of string value
            break
        if ch == "\\":
            pos += 1
            if pos >= length:
                break
            esc = accumulated_args[pos]
            if esc == "n":
                result.append("\n")
            elif esc == "t":
                result.append("\t")
            elif esc == "r":
                result.append("\r")
            elif esc == '"':
                result.append('"')
            elif esc == "\\":
                result.append("\\")
            elif esc == "/":
                result.append("/")
            elif esc == "b":
                result.append("\b")
            elif esc == "f":
                result.append("\f")
            elif esc == "u":
                # Unicode escape: \uXXXX
                hex_str = accumulated_args[pos + 1 : pos + 5]
                if len(hex_str) == 4:
                    try:
                        result.append(chr(int(hex_str, 16)))
                        pos += 4
                    except ValueError:
                        result.append(esc)
                else:
                    # Incomplete unicode escape — stop here
                    break
            else:
                result.append(esc)
        else:
            result.append(ch)
        pos += 1

    return "".join(result)


def _extract_streaming_language(accumulated_args: str) -> str | None:
    """Extract the 'language' value from an incomplete JSON argument string.

    Returns the language string if found, or None if not yet available.
    """
    key = '"language"'
    key_pos = accumulated_args.find(key)
    if key_pos == -1:
        return None

    pos = key_pos + len(key)
    length = len(accumulated_args)

    while pos < length and accumulated_args[pos] in " \t\n\r":
        pos += 1
    if pos >= length or accumulated_args[pos] != ":":
        return None
    pos += 1
    while pos < length and accumulated_args[pos] in " \t\n\r":
        pos += 1
    if pos >= length or accumulated_args[pos] != '"':
        return None
    pos += 1

    result: list[str] = []
    while pos < length:
        ch = accumulated_args[pos]
        if ch == '"':
            break
        if ch == "\\":
            pos += 1
            if pos >= length:
                break
            esc = accumulated_args[pos]
            result.append(esc)
        else:
            result.append(ch)
        pos += 1

    val = "".join(result)
    if not val:
        return None
    # Allowlist: only safe language identifier characters to prevent markdown injection
    if not re.fullmatch(r"[a-zA-Z0-9_+#.\-]{1,50}", val):
        return None
    return val


MAX_FILES_PER_REQUEST = 10
MAX_QUEUED_MESSAGES = 10

SAFE_INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


@dataclass
class ChatRequestContext:
    """Parsed and validated chat request data."""

    message_text: str
    regenerate: bool
    plan_mode: bool
    source_ids: list[str]
    source_tag: str | None
    source_group_id: str | None
    files: list = field(default_factory=list)


_cancel_events: dict[str, set[asyncio.Event]] = defaultdict(set)
_message_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
_active_streams: dict[str, dict[str, Any]] = {}
_stream_locks: dict[str, asyncio.Lock] = {}  # per-conversation lock for stream registration


def _validate_uuid(value: str) -> str:
    try:
        uuid_mod.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")
    return value


def _get_db(request: Request) -> Any:
    """Resolve database connection from optional ?db= query parameter."""
    db_name = request.query_params.get("db")
    if hasattr(request.app.state, "db_manager"):
        return request.app.state.db_manager.get(db_name)
    return request.app.state.db


def _get_db_name(request: Request) -> str:
    """Return validated database name from query param."""
    db_name = request.query_params.get("db") or "personal"
    # SECURITY: only allow alphanumeric, hyphens, underscores (max 64 chars)
    if not _is_safe_name(db_name):
        return "personal"
    return db_name


def _is_safe_name(name: str) -> bool:
    """Validate a name contains only safe characters."""
    import re

    return bool(re.match(r"^[a-zA-Z0-9_-]{1,64}$", name))


def _get_event_bus(request: Request) -> Any:
    return getattr(request.app.state, "event_bus", None)


def _get_client_id(request: Request) -> str:
    """Return validated client ID from header. Must be a valid UUID or empty."""
    raw = request.headers.get("x-client-id", "")
    if not raw:
        return ""
    try:
        uuid_mod.UUID(raw)
        return raw
    except ValueError:
        return ""


def _get_identity(request: Request) -> tuple[str | None, str | None]:
    identity = getattr(request.app.state.config, "identity", None)
    if identity:
        return identity.user_id, identity.display_name
    return None, None


def _require_json(request: Request) -> None:
    ct = request.headers.get("content-type", "")
    if not ct.startswith("application/json"):
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")


def _get_ai_service(request: Request, model_override: str | None = None) -> AIService:
    config = request.app.state.config
    if model_override:
        ai_config = copy.copy(config.ai)
        ai_config.model = model_override
        return create_ai_service(ai_config)
    return create_ai_service(config.ai)


def _get_request_registries(
    request: Request,
    db: Any,
    space_id: str | None,
    working_dir: str | None = None,
) -> tuple[Any, Any, Any]:
    """Return (artifact_registry, skill_registry, rule_enforcer) scoped to the request.

    When *space_id* is set, builds per-request registries that include
    both global/project skills for *working_dir* and space-scoped pack
    artifacts, skills, and rules.
    Without a space, returns the app-level globals unchanged.
    """
    global_art = getattr(request.app.state, "artifact_registry", None)
    global_skill = getattr(request.app.state, "skill_registry", None)
    global_rules = getattr(request.app.state, "rule_enforcer", None)

    if global_art is None or not space_id:
        return global_art, global_skill, global_rules

    from ..services.artifact_registry import ArtifactRegistry
    from ..services.artifacts import ArtifactType

    art_reg = ArtifactRegistry()
    art_reg.load_from_db(db, space_id=space_id)

    # Build per-request skill registry from space-scoped artifacts
    skill_reg = None
    if global_skill is not None:
        from ..cli.skills import SkillRegistry

        skill_reg = SkillRegistry()
        skill_reg.load(working_dir)
        skill_reg.load_from_artifacts(art_reg)

    # Build per-request rule enforcer from space-scoped artifacts
    rule_enf = None
    if global_rules is not None:
        from ..services.rule_enforcer import RuleEnforcer

        rule_enf = RuleEnforcer()
        rule_enf.load_rules(art_reg.list_all(artifact_type=ArtifactType.RULE))

    return art_reg, skill_reg, rule_enf


def _command_tool_names(request: Request) -> tuple[str, ...]:
    names: list[str] = []
    tool_registry = getattr(request.app.state, "tool_registry", None)
    if tool_registry is not None:
        try:
            names.extend(tool_registry.list_tools())
        except Exception:
            logger.debug("Failed to enumerate tool registry names", exc_info=True)
    mcp_manager = getattr(request.app.state, "mcp_manager", None)
    if mcp_manager is not None and hasattr(mcp_manager, "get_all_tools"):
        try:
            for tool in mcp_manager.get_all_tools() or []:
                if isinstance(tool, dict) and tool.get("name"):
                    names.append(str(tool["name"]))
        except Exception:
            logger.debug("Failed to enumerate MCP tool names", exc_info=True)
    return tuple(dict.fromkeys(names))


def _usage_markdown(db: Any, config: Any) -> str:
    now = datetime.now(timezone.utc)
    periods = [
        ("Today", now - timedelta(days=1)),
        ("This week", now - timedelta(days=config.cli.usage.week_days)),
        ("This month", now - timedelta(days=config.cli.usage.month_days)),
        ("All time", None),
    ]
    lines = ["## Usage"]
    for label, since_dt in periods:
        stats = storage.get_usage_stats(db, since=since_dt.isoformat() if since_dt else None)
        total_tokens = sum(s.get("total_tokens", 0) or 0 for s in stats)
        total_messages = sum(s.get("message_count", 0) or 0 for s in stats)
        lines.append(f"- **{label}:** {total_tokens:,} tokens across {total_messages} messages")
    return "\n".join(lines)


def _command_response(
    *,
    kind: str,
    message: str | None = None,
    echo_user: bool = True,
    conversation: dict[str, Any] | None = None,
    deleted_conversation_id: str | None = None,
    command_items: list[dict[str, Any]] | None = None,
    forward_prompt: str | None = None,
    model_name: str | None = None,
    plan_mode_enabled: bool | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"kind": kind, "echo_user": echo_user}
    if message is not None:
        payload["message"] = message
    if conversation is not None:
        payload["conversation"] = conversation
    if deleted_conversation_id is not None:
        payload["deleted_conversation_id"] = deleted_conversation_id
    if command_items is not None:
        payload["command_items"] = command_items
    if forward_prompt is not None:
        payload["forward_prompt"] = forward_prompt
    if model_name is not None:
        payload["model_name"] = model_name
    if plan_mode_enabled is not None:
        payload["plan_mode_enabled"] = plan_mode_enabled
    return payload


def _resolve_conversation_target(db: Any, target: str) -> dict[str, Any] | None:
    if target.isdigit():
        idx = int(target) - 1
        convs = storage.list_conversations(db, limit=max(20, idx + 1))
        if 0 <= idx < len(convs):
            return storage.get_conversation(db, convs[idx]["id"])
        return None
    return storage.get_conversation(db, target)


def _list_conversations_markdown(db: Any, limit: int = 20, convs: list[dict[str, Any]] | None = None) -> str:
    convs = convs if convs is not None else storage.list_conversations(db, limit=limit)
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


def _conversation_items(convs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for idx, conv in enumerate(convs, start=1):
        title = conv.get("title", "Untitled")
        conversation_type = conv.get("type") or "chat"
        message_count = int(conv.get("message_count") or 0)
        meta_parts = [f"{message_count} message{'s' if message_count != 1 else ''}"]
        if conv.get("slug"):
            meta_parts.append(str(conv["slug"]))
        items.append(
            {
                "kind": "conversation",
                "index": idx,
                "id": conv["id"],
                "title": title,
                "conversation_type": conversation_type,
                "slug": conv.get("slug"),
                "meta": " · ".join(meta_parts),
                "summary": _conversation_summary(conversation_type, message_count),
                "actions": [
                    {"label": "Open", "conversation_id": conv["id"], "variant": "primary"},
                    {
                        "label": "Delete",
                        "command": f"/delete --confirm {conv['id']}",
                        "requires_confirm": True,
                        "confirm_text": f"Delete {title}?",
                        "confirm_label": "Delete",
                    },
                ],
            }
        )
    return items


def _delete_conversation_item(conv: dict[str, Any]) -> dict[str, Any]:
    title = conv.get("title", "Untitled")
    meta_parts = []
    if conv.get("type") not in {None, "chat"}:
        meta_parts.append(str(conv["type"]))
    if conv.get("slug"):
        meta_parts.append(str(conv["slug"]))
    return {
        "kind": "conversation",
        "id": conv["id"],
        "title": title,
        "conversation_type": conv.get("type") or "chat",
        "slug": conv.get("slug"),
        "meta": " · ".join(meta_parts) if meta_parts else "Delete conversation",
        "summary": "This permanently removes the conversation history.",
        "actions": [
            {
                "label": "Delete",
                "command": f"/delete --confirm {conv['id']}",
                "variant": "primary",
                "requires_confirm": True,
                "confirm_label": "Delete",
                "confirm_text": f"Delete {title}?",
            }
        ],
    }


def _conversation_summary(conversation_type: str, message_count: int) -> str:
    label = {"chat": "Chat thread", "note": "Note", "document": "Document"}.get(conversation_type, "Conversation")
    if message_count <= 0:
        return f"{label} with no messages yet."
    return f"{label} with {message_count} message{'s' if message_count != 1 else ''}."


def _space_items(spaces: list[dict[str, Any]], active_space_id: str | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for space in spaces:
        is_active = active_space_id == space["id"]
        actions = [
            {"label": "Show", "command": f"/space show {space['name']}"},
            {"label": "Sources", "command": f"/space sources {space['name']}"},
            {"label": "Refresh", "command": f"/space refresh {space['name']}"},
            {"label": "Export", "command": f"/space export {space['name']}"},
        ]
        if not is_active:
            actions.append({"label": "Switch", "command": f"/space switch {space['name']}", "variant": "primary"})
        actions.append(
            {
                "label": "Delete",
                "command": f"/space delete --confirm {space['id']}",
                "requires_confirm": True,
                "confirm_text": f"Delete {space['name']}?",
                "confirm_label": "Delete",
            }
        )
        items.append(
            {
                "kind": "space",
                "id": space["id"],
                "title": space["name"],
                "meta": "Active space" if is_active else "Space",
                "summary": "Use this space's instructions, sources, and model context."
                if is_active
                else "Switch into this shared context or inspect its sources and config.",
                "badges": ["current"] if is_active else [],
                "actions": actions,
            }
        )
    return items


def _mcp_items(statuses: dict[str, dict[str, Any]], focus_name: str | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    names = [focus_name] if focus_name and focus_name in statuses else sorted(statuses.keys())
    for name in names:
        info = statuses[name]
        status = str(info.get("status", "unknown"))
        transport = str(info.get("transport", "?"))
        tool_count = int(info.get("tool_count", 0) or 0)
        badges = [status]
        actions = [
            {"label": "Status", "command": f"/mcp status {name}", "variant": "primary"},
            {"label": "Reconnect", "command": f"/mcp reconnect {name}"},
        ]
        if status == "connected":
            actions.insert(1, {"label": "Disconnect", "command": f"/mcp disconnect {name}"})
        else:
            actions.insert(1, {"label": "Connect", "command": f"/mcp connect {name}"})
        summary = f"{transport.upper()} transport with {tool_count} tool{'s' if tool_count != 1 else ''}."
        if info.get("error_message"):
            summary += f" Last error: {info['error_message']}."
        items.append(
            {
                "kind": "mcp",
                "id": name,
                "title": name,
                "meta": f"{transport} · {tool_count} tool{'s' if tool_count != 1 else ''}",
                "summary": summary,
                "badges": badges,
                "actions": actions,
            }
        )
    return items


def _artifact_items(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for artifact in artifacts:
        is_builtin = artifact.get("source") == "built_in"
        actions = [{"label": "Show", "command": f"/artifact show {artifact['fqn']}", "variant": "primary"}]
        if not is_builtin:
            actions.append(
                {
                    "label": "Delete",
                    "command": f"/artifact delete {artifact['fqn']}",
                    "requires_confirm": True,
                    "confirm_text": f"Delete {artifact['fqn']}?",
                    "confirm_label": "Delete",
                }
            )
        items.append(
            {
                "kind": "artifact",
                "id": artifact.get("id") or artifact["fqn"],
                "title": artifact["fqn"],
                "meta": " · ".join([artifact.get("type", "?"), artifact.get("source", "?")]),
                "summary": (
                    f"{artifact.get('type', 'artifact').capitalize()} artifact from "
                    f"{artifact.get('source', 'unknown')}."
                ),
                "badges": ["built-in"] if is_builtin else [],
                "actions": actions,
            }
        )
    return items


def _pack_items(packs: list[dict[str, Any]], *, attached_refs: set[str] | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    attached_refs = attached_refs or set()
    for pack in packs:
        ref = f"{pack.get('namespace', 'default')}/{pack['name']}"
        is_attached = ref in attached_refs
        actions = [{"label": "Show", "command": f"/pack show {ref}", "variant": "primary"}]
        if is_attached:
            actions.append({"label": "Detach", "command": f"/pack detach {ref}"})
        else:
            actions.append({"label": "Attach", "command": f"/pack attach {ref}"})
        actions.append(
            {
                "label": "Remove",
                "command": f"/pack remove {ref}",
                "requires_confirm": True,
                "confirm_text": f"Remove @{ref}?",
                "confirm_label": "Remove",
            }
        )
        items.append(
            {
                "kind": "pack",
                "id": pack.get("id") or ref,
                "title": f"@{ref}",
                "meta": " · ".join(
                    part
                    for part in [
                        f"v{pack.get('version', '?')}",
                        f"{pack.get('artifact_count', 0)} artifacts",
                        "attached globally" if is_attached else "",
                    ]
                    if part
                ),
                "summary": pack.get("description") or "Reusable pack of artifacts and instructions.",
                "badges": ["attached"] if is_attached else [],
                "actions": actions,
            }
        )
    return items


def _skill_items(entries: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for entry in entries:
        title = f"/{entry.display_name}"
        meta_parts = [entry.description or "No description."]
        if entry.source:
            meta_parts.append(entry.source)
        actions = []
        if getattr(entry, "accepts_args", False):
            actions.append(
                {
                    "label": "Insert",
                    "insert_text": f"{title} ",
                    "variant": "primary",
                }
            )
        else:
            actions.append(
                {
                    "label": "Run",
                    "command": title,
                    "variant": "primary",
                }
            )
            actions.append(
                {
                    "label": "Insert",
                    "insert_text": title,
                }
            )
        items.append(
            {
                "kind": "skill",
                "id": entry.display_name,
                "title": title,
                "meta": " · ".join(meta_parts),
                "summary": entry.description or "Reusable command shortcut.",
                "badges": [entry.source] if entry.source else [],
                "actions": actions,
            }
        )
    return items


def _search_conversations_markdown(
    db: Any,
    arg: str,
    results: list[dict[str, Any]] | None = None,
) -> str:
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
    results = (
        results
        if results is not None
        else storage.list_conversations(
            db,
            search=query,
            limit=20,
            conversation_type=type_filter,
        )
    )
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


def _rename_conversation(
    db: Any,
    active_conversation: dict[str, Any] | None,
    arg: str,
) -> tuple[dict[str, Any] | None, str]:
    if not arg.strip():
        return None, "Usage: `/rename <title>` or `/rename <number|slug|id> <title>`"
    parts = arg.split(maxsplit=1)
    target = None
    new_title = ""
    if len(parts) == 1:
        new_title = parts[0].strip()
    else:
        maybe_target, remainder = parts
        resolved = _resolve_conversation_target(db, maybe_target)
        if resolved:
            target = resolved
            new_title = remainder.strip()
        else:
            new_title = arg.strip()
    if not new_title:
        return None, "Usage: `/rename <title>` or `/rename <number|slug|id> <title>`"
    if target is None:
        target = active_conversation
    if not target:
        return None, "No active conversation to rename."
    updated = storage.update_conversation_title(db, target["id"], new_title)
    return updated, f"Renamed conversation to **{new_title}**."


def _set_conversation_slug(
    db: Any,
    conversation: dict[str, Any] | None,
    desired: str | None,
) -> tuple[dict[str, Any] | None, str]:
    if not conversation:
        return None, "No active conversation."
    if not desired:
        return conversation, f"Current slug: `{conversation.get('slug') or 'none'}`"
    if not is_valid_slug(desired):
        return None, "Invalid slug. Use lowercase letters, numbers, and hyphens."
    suggestion = suggest_unique_slug(db, desired)
    if suggestion is None:
        updated = storage.update_conversation_slug(db, conversation["id"], desired)
        return updated, f"Slug set to `{desired}`."
    return None, f"`{desired}` is already taken. Try `{suggestion}`."


def _rewind_messages_markdown(db: Any, conversation: dict[str, Any] | None) -> str:
    if not conversation:
        return "No active conversation to rewind."
    stored = storage.list_messages(db, conversation["id"])
    if len(stored) < 2:
        return "Not enough messages to rewind."
    lines = ["## Rewind Conversation", ""]
    for message in stored:
        role_label = "You" if message["role"] == "user" else "AI"
        preview = (message.get("content") or "").strip().replace("\n", " ")
        preview = (preview[:77] + "...") if len(preview) > 80 else preview
        lines.append(f"{message['position']}. **{role_label}** {preview}")
    lines.append("")
    lines.append("Use `/rewind <position>` to keep that message and delete everything after it.")
    lines.append("Add `--undo-files` to also revert files changed by later write/edit tool calls.")
    return "\n".join(lines)


async def _rewind_conversation(
    request: Request,
    conversation: dict[str, Any] | None,
    arg: str,
) -> tuple[dict[str, Any] | None, str]:
    if not conversation:
        return None, "No active conversation to rewind."
    db = _get_db(request)
    stored = storage.list_messages(db, conversation["id"])
    if len(stored) < 2:
        return None, "Not enough messages to rewind."
    if not arg.strip():
        return conversation, _rewind_messages_markdown(db, conversation)

    tokens = [token for token in arg.split() if token]
    undo_files = False
    if "--undo-files" in tokens:
        undo_files = True
        tokens = [token for token in tokens if token != "--undo-files"]
    if len(tokens) != 1 or not tokens[0].isdigit():
        return None, "Usage: `/rewind <position> [--undo-files]`"

    target_pos = int(tokens[0])
    positions = {message["position"] for message in stored}
    if target_pos not in positions:
        return None, f"Position {target_pos} not found."

    msgs_after = [message for message in stored if message["position"] > target_pos]
    msg_ids_after = [message["id"] for message in msgs_after]
    file_paths = collect_file_paths(db, msg_ids_after)
    rewind_result = await rewind_service(
        db=db,
        conversation_id=conversation["id"],
        to_position=target_pos,
        undo_files=undo_files,
        data_dir=request.app.state.config.app.data_dir,
        working_dir=conversation.get("working_dir") or os.getcwd(),
    )

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
    return storage.get_conversation(db, conversation["id"]), summary


async def _compact_conversation(
    request: Request,
    conversation: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str]:
    if not conversation:
        return None, "No active conversation to compact."
    db = _get_db(request)
    messages = storage.list_messages(db, conversation["id"])
    compactable = [
        {"role": m["role"], "content": m.get("content", "")}
        for m in messages
        if m["role"] in {"user", "assistant"}
    ]
    if len(compactable) < 4:
        return None, "Not enough messages to compact."

    history_text = _build_compaction_history(compactable)
    summary_prompt = (
        "Summarize the following conversation concisely, preserving:\n"
        "- Key decisions and conclusions\n"
        "- File paths that were read, written, or edited\n"
        "- Important code changes and their purpose\n"
        "- Which steps of any multi-step plan have been completed vs remaining\n"
        "- Current state of the task — what has been done and what is next\n"
        "- Any errors encountered and how they were resolved\n\n" + history_text
    )
    try:
        ai_service = _get_ai_service(request, conversation.get("model") or request.app.state.config.ai.model)
        response = await ai_service.client.chat.completions.create(
            model=ai_service.config.model,
            messages=[{"role": "user", "content": summary_prompt}],
            max_completion_tokens=1000,
        )
        summary = response.choices[0].message.content or "Conversation summary unavailable."
    except Exception:
        logger.exception("Failed to compact conversation on web")
        return None, "Failed to generate summary."

    compact_note = f"Previous conversation summary (auto-compacted from {len(compactable)} messages):\n\n{summary}"
    now = datetime.now(timezone.utc).isoformat()
    with db.transaction() as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation["id"],))
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at, position) VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid_mod.uuid4()), conversation["id"], "system", compact_note, now, 0),
        )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation["id"]))
    return (
        storage.get_conversation(db, conversation["id"]),
        f"Compacted **{len(compactable)}** messages into a working summary.",
    )


def _space_list_markdown(db: Any, active_space_id: str | None = None) -> str:
    spaces = list_spaces(db)
    if not spaces:
        return "No spaces yet."
    lines = ["## Spaces", ""]
    for space in spaces:
        active = " · active" if active_space_id and active_space_id == space["id"] else ""
        count = count_space_conversations(db, space["id"])
        lines.append(f"- **{space['name']}** · {count} conversations · `{space['id'][:8]}...`{active}")
    lines.append("")
    lines.append("Use `/space show <name>` or `/space switch <name>`.")
    return "\n".join(lines)


def _create_space_markdown(db: Any, target: str | None) -> tuple[dict[str, Any] | None, str]:
    name = (target or "").strip()
    if not name:
        return None, "Usage: `/space create <name>`"
    if get_spaces_by_name(db, name):
        return None, f"Space **{name}** already exists."
    space = create_space(db, name)
    return space, f"Created space **{space['name']}**."


def _update_space_markdown(
    db: Any,
    conversation: dict[str, Any] | None,
    edit_field: str | None,
    edit_value: str | None,
) -> tuple[dict[str, Any] | None, str]:
    if not conversation or not conversation.get("space_id"):
        return None, "No active space."
    space = get_space(db, conversation["space_id"])
    if not space:
        return None, "No active space."

    field = (edit_field or "").strip().lower()
    value = edit_value or ""
    if field not in {"instructions", "model", "name"}:
        return None, "Unknown field. Use: `instructions`, `model`, or `name`."
    if field == "name" and not value.strip():
        return None, "Usage: `/space edit name <new-name>`"

    if field == "instructions":
        updates = {"instructions": value}
        message = f"Updated instructions for **{space['name']}**."
    elif field == "model":
        updates = {"model": value or None}
        message = f"Updated model for **{space['name']}**: `{value or '(cleared)'}`"
    else:
        updates = {"name": value.strip()}
        message = f"Renamed space to **{value.strip()}**."

    updated = update_space(db, space["id"], **updates)
    return updated, message


def _refresh_space_markdown(
    db: Any,
    conversation: dict[str, Any] | None,
    target: str | None,
) -> tuple[dict[str, Any] | None, str]:
    from ..services.spaces import compute_file_hash, parse_space_file

    if target and target.strip():
        space, candidates = resolve_space(db, target.strip())
        if candidates:
            names = ", ".join(candidate["name"] for candidate in candidates[:5])
            return None, f"Space `{target}` is ambiguous. Matches: {names}"
    elif conversation and conversation.get("space_id"):
        space = get_space(db, conversation["space_id"])
    else:
        space = None
    if not space:
        return None, "No active space."
    source_file = space.get("source_file", "")
    if not source_file:
        return None, "This space has no source file to refresh from."
    fpath = Path(source_file)
    if not fpath.is_file():
        return None, f"Space file not found: `{fpath}`"
    try:
        cfg = parse_space_file(fpath)
    except Exception:
        return None, f"Failed to refresh space from `{fpath}`"

    updated = update_space(
        db,
        space["id"],
        source_hash=compute_file_hash(fpath),
        instructions=cfg.instructions or "",
        model=cfg.config.get("model") or None,
    )
    if not updated:
        return None, f"Failed to refresh space from `{fpath}`"
    return updated, f"Refreshed space **{updated['name']}**."


def _export_space_markdown(
    db: Any,
    conversation: dict[str, Any] | None,
    target: str | None,
) -> str:
    import yaml

    from ..services.spaces import export_space_to_yaml

    if target and target.strip():
        space, candidates = resolve_space(db, target.strip())
        if candidates:
            names = ", ".join(candidate["name"] for candidate in candidates[:5])
            return f"Space `{target}` is ambiguous. Matches: {names}"
    elif conversation and conversation.get("space_id"):
        space = get_space(db, conversation["space_id"])
    else:
        space = None
    if not space:
        return "No active space."
    try:
        cfg = export_space_to_yaml(db, space["id"])
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


def _space_show_markdown(db: Any, active_space_id: str | None, target: str | None) -> str:
    if not target and active_space_id:
        from ..services.space_storage import get_space

        space = get_space(db, active_space_id)
        candidates: list[dict[str, Any]] = []
    else:
        space, candidates = resolve_space(db, target or "")
    if candidates:
        names = ", ".join(candidate["name"] for candidate in candidates[:5])
        return f"Space `{target}` is ambiguous. Matches: {names}"
    if not space:
        return f"Space `{target}` not found."
    paths = get_space_paths(db, space["id"])
    lines = [f"## Space: {space['name']}", ""]
    if space.get("source_file"):
        lines.append(f"- Source: `{space['source_file']}`")
    if space.get("model"):
        lines.append(f"- Model: `{space['model']}`")
    lines.append(f"- Conversations: `{count_space_conversations(db, space['id'])}`")
    instructions = space.get("instructions") or ""
    if instructions:
        preview = instructions.strip().replace("\n", " ")
        preview = (preview[:97] + "...") if len(preview) > 100 else preview
        lines.append(f"- Instructions: {preview}")
    if paths:
        lines.extend(["", "### Paths"])
        for path in paths:
            label = path.get("repo_url") or "(mapped)"
            lines.append(f"- `{label}` -> `{path['local_path']}`")
    return "\n".join(lines)


def _space_sources_markdown(db: Any, active_space_id: str | None, target: str | None) -> str:
    if not target and active_space_id:
        from ..services.space_storage import get_space

        space = get_space(db, active_space_id)
        candidates: list[dict[str, Any]] = []
    else:
        space, candidates = resolve_space(db, target or "")
    if candidates:
        names = ", ".join(candidate["name"] for candidate in candidates[:5])
        return f"Space `{target}` is ambiguous. Matches: {names}"
    if not space:
        return f"Space `{target}` not found."
    linked = storage.get_direct_space_source_links(db, space["id"])
    if not linked:
        return f"No sources linked to space `{space['name']}`."
    lines = [f"## Sources in {space['name']}", ""]
    for source in linked:
        title = source.get("title") or "Untitled"
        source_type = source.get("type") or "unknown"
        lines.append(f"- **{title}** · {source_type} · `{str(source['id'])[:8]}...`")
    return "\n".join(lines)


def _set_space(
    db: Any,
    conversation: dict[str, Any] | None,
    target: str | None,
) -> tuple[dict[str, Any] | None, str]:
    if not conversation:
        return None, "Open or create a conversation before changing spaces."
    if target == "":
        if not conversation.get("space_id"):
            return conversation, "No active space."
        update_conversation_space(db, conversation["id"], None)
        updated = storage.get_conversation(db, conversation["id"])
        return updated, "Cleared active space."

    space, candidates = resolve_space(db, target or "")
    if candidates:
        names = ", ".join(candidate["name"] for candidate in candidates[:5])
        return None, f"Space `{target}` is ambiguous. Matches: {names}"
    if not space:
        return None, f"Space `{target}` not found."
    update_conversation_space(db, conversation["id"], space["id"])
    updated = storage.get_conversation(db, conversation["id"])
    return updated, f"Active space: **{space['name']}**"


def _delete_space(
    db: Any,
    conversation: dict[str, Any] | None,
    target: str | None,
) -> tuple[dict[str, Any] | None, str]:
    space, candidates = resolve_space(db, target or "")
    if candidates:
        names = ", ".join(candidate["name"] for candidate in candidates[:5])
        return conversation, f"Space `{target}` is ambiguous. Matches: {names}"
    if not space:
        return conversation, f"Space `{target}` not found."
    deleted_space_id = space["id"]
    deleted_space_name = space["name"]
    if not delete_space(db, deleted_space_id):
        return conversation, f"Space `{target}` not found."
    updated = conversation
    if conversation:
        updated = storage.get_conversation(db, conversation["id"])
    return updated, f"Deleted space **{deleted_space_name}**."


def _artifact_list_markdown(db: Any) -> str:
    artifacts = artifact_storage.list_artifacts(db)
    if not artifacts:
        return "No artifacts found."
    lines = ["## Artifacts", ""]
    for artifact in artifacts:
        lines.append(f"- `{artifact['fqn']}` · {artifact.get('type', '?')} · {artifact.get('source', '?')}")
    lines.extend(["", "Use `/artifact show <fqn>` to inspect one."])
    return "\n".join(lines)


def _artifact_show_markdown(db: Any, fqn: str) -> str:
    if not fqn:
        return "Usage: `/artifact show <fqn>`"
    if not validate_fqn(fqn):
        return "Invalid FQN format."
    artifact = artifact_storage.get_artifact_by_fqn(db, fqn)
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


def _delete_artifact(request: Request, fqn: str) -> str:
    if not fqn:
        return "Usage: `/artifact delete <fqn>`"
    if not validate_fqn(fqn):
        return "Invalid FQN format."
    db = _get_db(request)
    artifact = artifact_storage.get_artifact_by_fqn(db, fqn)
    if not artifact:
        return "Artifact not found."
    if artifact.get("source") == "built_in":
        return "Cannot delete built-in artifacts."
    artifact_storage.delete_artifact(db, artifact["id"])
    artifact_registry = getattr(request.app.state, "artifact_registry", None)
    if artifact_registry is not None:
        artifact_registry.load_from_db(db)
    skill_registry = getattr(request.app.state, "skill_registry", None)
    if skill_registry is not None and artifact_registry is not None:
        skill_registry.load_from_artifacts(artifact_registry)
    return f"Deleted `{fqn}`."


def _resolve_pack_ref(db: Any, ref: str) -> dict[str, Any] | None:
    namespace, _, name = ref.rpartition("/")
    if not namespace:
        namespace = "default"
    pack, _ = packs_service.resolve_pack(db, namespace, name)
    return pack


def _pack_list_markdown(db: Any) -> str:
    installed = packs_service.list_packs(db)
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


def _pack_show_markdown(db: Any, ref: str) -> str:
    if not ref:
        return "Usage: `/pack show <namespace/name>`"
    pack = _resolve_pack_ref(db, ref)
    if not pack:
        return f"Pack `{ref}` not found."
    info = packs_service.get_pack(db, pack["namespace"], pack["name"])
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


def _remove_pack(request: Request, ref: str) -> str:
    if not ref:
        return "Usage: `/pack remove <namespace/name>`"
    db = _get_db(request)
    pack = _resolve_pack_ref(db, ref)
    if not pack:
        return f"Pack `{ref}` not found."
    removed = packs_service.remove_pack_by_id(db, pack["id"])
    if not removed:
        return f"Pack `{ref}` not found."
    artifact_registry = getattr(request.app.state, "artifact_registry", None)
    if artifact_registry is not None:
        artifact_registry.load_from_db(db)
    skill_registry = getattr(request.app.state, "skill_registry", None)
    if skill_registry is not None and artifact_registry is not None:
        skill_registry.load_from_artifacts(artifact_registry)
    return f"Removed `@{pack['namespace']}/{pack['name']}`."


def _attach_pack_markdown(
    request: Request,
    conversation: dict[str, Any] | None,
    ref: str,
    *,
    project_scope: bool,
) -> str:
    from ..services.pack_attachments import attach_pack

    if not ref:
        return "Usage: `/pack attach <namespace/name> [--project]`"
    db = _get_db(request)
    pack = _resolve_pack_ref(db, ref)
    if not pack:
        return f"Pack `{ref}` not found."
    try:
        attach_pack(
            db,
            pack["id"],
            project_path=(conversation.get("working_dir") if conversation else None) if project_scope else None,
        )
    except ValueError as exc:
        return str(exc)
    artifact_registry = getattr(request.app.state, "artifact_registry", None)
    if artifact_registry is not None:
        artifact_registry.load_from_db(db)
    skill_registry = getattr(request.app.state, "skill_registry", None)
    if skill_registry is not None and artifact_registry is not None:
        skill_registry.load_from_artifacts(artifact_registry)
    scope = "project" if project_scope else "global"
    return f"Attached `@{pack['namespace']}/{pack['name']}` ({scope})."


def _detach_pack_markdown(
    request: Request,
    conversation: dict[str, Any] | None,
    ref: str,
    *,
    project_scope: bool,
) -> str:
    from ..services.pack_attachments import detach_pack

    if not ref:
        return "Usage: `/pack detach <namespace/name> [--project]`"
    db = _get_db(request)
    pack = _resolve_pack_ref(db, ref)
    if not pack:
        return f"Pack `{ref}` not found."
    removed = detach_pack(
        db,
        pack["id"],
        project_path=(conversation.get("working_dir") if conversation else None) if project_scope else None,
    )
    if not removed:
        return f"Pack `{ref}` is not attached at {'project' if project_scope else 'global'} scope."
    artifact_registry = getattr(request.app.state, "artifact_registry", None)
    if artifact_registry is not None:
        artifact_registry.load_from_db(db)
    skill_registry = getattr(request.app.state, "skill_registry", None)
    if skill_registry is not None and artifact_registry is not None:
        skill_registry.load_from_artifacts(artifact_registry)
    scope = "project" if project_scope else "global"
    return f"Detached `@{pack['namespace']}/{pack['name']}` ({scope})."


def _pack_sources_markdown(request: Request) -> str:
    sources_cfg = getattr(request.app.state.config, "pack_sources", []) or []
    if not sources_cfg:
        return "No pack sources configured. Add one with the CLI: `/pack add-source <url>`."
    cached = list_cached_sources(request.app.state.config.app.data_dir)
    cached_map = {entry.url: entry for entry in cached}
    lines = ["## Pack Sources", ""]
    for source in sources_cfg:
        url = getattr(source, "url", None) or "?"
        branch = getattr(source, "branch", "main") or "main"
        cached_entry = cached_map.get(url)
        status = f"cached ({cached_entry.ref[:8]})" if cached_entry else "not cloned"
        lines.append(f"- `{url}` ({branch}) — {status}")
    return "\n".join(lines)


def _refresh_pack_sources_markdown(request: Request) -> str:
    sources_cfg = getattr(request.app.state.config, "pack_sources", []) or []
    if not sources_cfg:
        return "No pack sources configured. Add one with the CLI: `/pack add-source <url>`."

    db = _get_db(request)
    from ..services.pack_refresh import PackRefreshWorker

    worker = PackRefreshWorker(db=db, data_dir=request.app.state.config.app.data_dir, sources=sources_cfg)
    results = worker.refresh_all()
    if any(result.changed for result in results):
        artifact_registry = getattr(request.app.state, "artifact_registry", None)
        if artifact_registry is not None:
            artifact_registry.load_from_db(db)
        skill_registry = getattr(request.app.state, "skill_registry", None)
        if skill_registry is not None and artifact_registry is not None:
            skill_registry.load_from_artifacts(artifact_registry)

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


def _add_pack_source_markdown(request: Request, url: str) -> str:
    if not url:
        return "Usage: `/pack add-source <git-url>`"

    from ..config import PackSourceConfig
    from ..services.pack_sources import add_pack_source

    result = add_pack_source(url)
    if not result.ok:
        return result.message or "Failed to add pack source."

    sources_cfg = getattr(request.app.state.config, "pack_sources", None)
    if sources_cfg is None:
        request.app.state.config.pack_sources = []
        sources_cfg = request.app.state.config.pack_sources
    if not any(getattr(source, "url", None) == url for source in sources_cfg):
        sources_cfg.append(PackSourceConfig(url=url, branch="main", refresh_interval=30))

    return result.message or f"Added pack source: {url}"


def _install_or_update_pack_markdown(
    request: Request,
    conversation: dict[str, Any] | None,
    pack_path_str: str,
    *,
    update: bool,
    project_scope: bool = False,
    attach_after_install: bool = False,
    priority: int = 50,
) -> str:
    from pathlib import Path

    from ..services.pack_attachments import attach_pack

    if not pack_path_str:
        return "Usage: `/pack update <path>`" if update else "Usage: `/pack install <path>`"

    db = _get_db(request)
    working_dir = (conversation.get("working_dir") if conversation else None) or os.getcwd()
    pack_path = Path(pack_path_str).expanduser()
    if not pack_path.is_absolute():
        pack_path = Path(working_dir) / pack_path
    pack_path = pack_path.resolve()
    manifest_path = pack_path / "pack.yaml"
    if not manifest_path.exists():
        return f"No pack.yaml found in `{pack_path}`."

    try:
        manifest = packs_service.parse_manifest(manifest_path)
        errors = packs_service.validate_manifest(manifest, pack_path)
        if errors:
            return "\n".join(["Pack validation failed:"] + [f"- {err}" for err in errors])
        project_dir = Path(working_dir) if project_scope else None
        result = (
            packs_service.update_pack(db, manifest, pack_path, project_dir=project_dir)
            if update
            else packs_service.install_pack(db, manifest, pack_path, project_dir=project_dir)
        )
    except ValueError as exc:
        return str(exc)

    attach_note = ""
    if attach_after_install:
        try:
            attach_pack(
                db,
                result["id"],
                project_path=working_dir if project_scope else None,
                priority=priority,
            )
            attach_scope = "project" if project_scope else "global"
            attach_note = f"\nAttached `@{manifest.namespace}/{manifest.name}` ({attach_scope}, p{priority})."
        except ValueError as exc:
            attach_note = f"\nPack installed but not attached: {exc}"

    artifact_registry = getattr(request.app.state, "artifact_registry", None)
    if artifact_registry is not None:
        artifact_registry.load_from_db(db)
    skill_registry = getattr(request.app.state, "skill_registry", None)
    if skill_registry is not None and artifact_registry is not None:
        skill_registry.load_from_artifacts(artifact_registry)

    action_word = "Updated" if update or result.get("action") == "updated" else "Installed"
    return (
        f"{action_word} `@{manifest.namespace}/{manifest.name}` v{manifest.version} "
        f"({result.get('artifact_count', 0)} artifacts).{attach_note}"
    )


def _mcp_status_markdown(request: Request) -> str:
    mcp_manager = getattr(request.app.state, "mcp_manager", None)
    if mcp_manager is None:
        return "No MCP servers configured."
    statuses = mcp_manager.get_server_statuses()
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


def _mcp_server_detail_markdown(request: Request, name: str) -> str:
    mcp_manager = getattr(request.app.state, "mcp_manager", None)
    if mcp_manager is None:
        return "No MCP servers configured."
    statuses = mcp_manager.get_server_statuses()
    if name not in statuses:
        available = ", ".join(sorted(statuses.keys())) if statuses else "none"
        return f"Unknown MCP server `{name}`. Available: {available}"

    info = statuses[name]
    lines = [f"## MCP Server: {name}", ""]
    lines.append(f"- Status: `{info.get('status', 'unknown')}`")
    lines.append(f"- Transport: `{info.get('transport', '?')}`")
    lines.append(f"- Tools: `{info.get('tool_count', 0)}`")
    if info.get("error_message"):
        lines.append(f"- Error: `{info['error_message']}`")
    config = getattr(mcp_manager, "_configs", {}).get(name)
    if config is not None:
        url = getattr(config, "url", None)
        timeout = getattr(config, "timeout", None)
        if url:
            lines.append(f"- URL: `{url}`")
        if timeout is not None:
            lines.append(f"- Timeout: `{timeout}`")
    server_tools = getattr(mcp_manager, "_server_tools", {}).get(name, [])
    if server_tools:
        lines.extend(["", "### Tools"])
        for tool in server_tools:
            lines.append(f"- `{tool}`")
    return "\n".join(lines)


async def _run_mcp_action_markdown(request: Request, action: str, server_name: str) -> str:
    mcp_manager = getattr(request.app.state, "mcp_manager", None)
    if mcp_manager is None:
        return "No MCP servers configured."
    try:
        if action == "connect":
            await mcp_manager.connect_server(server_name)
        elif action == "disconnect":
            await mcp_manager.disconnect_server(server_name)
        elif action == "reconnect":
            await mcp_manager.reconnect_server(server_name)
        else:
            return f"Unknown MCP action `{action}`."
    except ValueError as exc:
        return str(exc)
    return f"MCP `{action}` for **{server_name}** complete.\n\n{_mcp_status_markdown(request)}"


async def _execute_web_command(
    request: Request,
    *,
    conversation_id: str | None,
    raw_command: str,
    plan_mode: bool = False,
) -> dict[str, Any]:
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id) if conversation_id else None
    if conversation_id and not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    parsed = parse_slash_command(raw_command)
    if parsed is None:
        raise HTTPException(status_code=400, detail="Not a slash command")

    working_dir = (conv.get("working_dir") if conv else None) or os.getcwd()
    space_id = conv.get("space_id") if conv else None
    artifact_registry, skill_registry, _ = _get_request_registries(request, db, space_id, working_dir)
    if parsed.name == "/reload-skills" and skill_registry is not None:
        skill_registry.reload(working_dir)
        if artifact_registry is not None:
            skill_registry.load_from_artifacts(artifact_registry)

    current_model = (conv.get("model") if conv else None) or request.app.state.config.ai.model
    context = CommandContext(
        current_model=current_model,
        working_dir=working_dir,
        available_tools=_command_tool_names(request),
        tool_registry=getattr(request.app.state, "tool_registry", None),
        skill_registry=skill_registry,
        artifact_registry=artifact_registry,
        plan_mode=plan_mode,
    )
    result = execute_slash_command(raw_command, context)
    if result is None:
        local_message = _web_local_only_command_message(parsed.name, raw_command)
        if local_message:
            return _command_response(kind="show_message", message=local_message, echo_user=False)
        raise HTTPException(status_code=400, detail="Unsupported slash command")
    return await _apply_web_command_result(request, conversation_id=conversation_id, conversation=conv, result=result)


def _web_local_only_command_message(command_name: str, raw_command: str) -> str | None:
    if command_name == "/upload":
        return "Use the web file uploader instead of `/upload <path>`."
    if command_name == "/verbose":
        return "`/verbose` is available in the CLI and Textual UI only."
    if command_name == "/detail":
        return "`/detail` is available in the CLI and Textual UI only."
    if command_name == "/plan" and len(raw_command.strip().split(maxsplit=1)) > 1:
        return "Inline `/plan <prompt>` is available in the CLI and Textual UI only. Use normal chat input on the web."
    return None


async def _apply_web_command_result(
    request: Request,
    *,
    conversation_id: str | None,
    conversation: dict[str, Any] | None,
    result: CommandResult,
) -> dict[str, Any]:
    db = _get_db(request)
    if result.kind == "exit":
        return _command_response(
            kind="show_message",
            message="`/quit` is not available in the web UI.",
            echo_user=False,
        )
    if result.kind == "forward_prompt" and result.forward_prompt:
        return _command_response(
            kind="forward_prompt",
            forward_prompt=result.forward_prompt,
            echo_user=True,
        )
    if result.kind == "show_help":
        return _command_response(kind="show_message", message=build_help_markdown(), echo_user=True)
    if result.kind == "show_spaces":
        spaces = list_spaces(db)
        return _command_response(
            kind="show_message",
            message=_space_list_markdown(db, conversation.get("space_id") if conversation else None),
            command_items=_space_items(spaces, conversation.get("space_id") if conversation else None),
        )
    if result.kind == "create_space":
        created_space, message = _create_space_markdown(db, result.space_target)
        return _command_response(
            kind="create_space",
            message=message,
            command_items=_space_items([created_space], conversation.get("space_id") if conversation else None)
            if created_space
            else None,
            echo_user=False,
        )
    if result.kind == "update_space":
        updated_space, message = _update_space_markdown(
            db,
            conversation,
            result.space_edit_field,
            result.space_edit_value,
        )
        return _command_response(
            kind="update_space",
            message=message,
            command_items=_space_items([updated_space], conversation.get("space_id") if conversation else None)
            if updated_space
            else None,
            echo_user=False,
        )
    if result.kind == "refresh_space":
        refreshed_space, message = _refresh_space_markdown(db, conversation, result.space_target)
        return _command_response(
            kind="refresh_space",
            message=message,
            command_items=_space_items([refreshed_space], conversation.get("space_id") if conversation else None)
            if refreshed_space
            else None,
            echo_user=False,
        )
    if result.kind == "export_space":
        return _command_response(
            kind="show_message",
            message=_export_space_markdown(db, conversation, result.space_target),
            echo_user=False,
        )
    if result.kind == "show_artifacts":
        artifacts = artifact_storage.list_artifacts(db)
        return _command_response(
            kind="show_message",
            message=_artifact_list_markdown(db),
            command_items=_artifact_items(artifacts),
        )
    if result.kind == "show_packs":
        packs = packs_service.list_packs(db)

        attached_refs: set[str] = set()
        for pack in packs:
            ref = f"{pack.get('namespace', 'default')}/{pack['name']}"
            attachments = list_attachments_for_pack(db, pack["id"])
            if any(
                attachment.get("scope") == "global"
                and not attachment.get("project_path")
                and not attachment.get("space_id")
                for attachment in attachments
            ):
                attached_refs.add(ref)
        return _command_response(
            kind="show_message",
            message=_pack_list_markdown(db),
            command_items=_pack_items(packs, attached_refs=attached_refs),
        )
    if result.kind == "show_pack_sources":
        return _command_response(kind="show_message", message=_pack_sources_markdown(request))
    if result.kind == "refresh_pack_sources":
        return _command_response(kind="show_message", message=_refresh_pack_sources_markdown(request))
    if result.kind == "add_pack_source":
        return _command_response(
            kind="show_message",
            message=_add_pack_source_markdown(request, result.pack_source_url or ""),
        )
    if result.kind == "list_conversations":
        convs = storage.list_conversations(db, limit=result.list_limit or 20)
        return _command_response(
            kind="show_message",
            message=_list_conversations_markdown(db, result.list_limit or 20, convs),
            command_items=_conversation_items(convs),
        )
    if result.kind == "resume_conversation":
        target = result.resume_target
        if target:
            conv = _resolve_conversation_target(db, target)
        else:
            convs = storage.list_conversations(db, limit=1)
            conv = storage.get_conversation(db, convs[0]["id"]) if convs else None
        if not conv:
            return _command_response(
                kind="show_message",
                message="Conversation not found. Use `/list` to see available conversations.",
                echo_user=False,
            )
        return _command_response(
            kind="resume_conversation",
            message=f"Resumed **{conv.get('title', 'Untitled')}**.",
            conversation=conv,
            echo_user=False,
        )
    if result.kind == "delete_conversation":
        conv = _resolve_conversation_target(db, result.delete_target or "")
        if not conv:
            return _command_response(
                kind="show_message",
                message="Conversation not found. Use `/list` to see available conversations.",
                echo_user=False,
            )
        if not (result.command.arg or "").strip().startswith("--confirm "):
            return _command_response(
                kind="show_message",
                message=f"Confirm deleting **{conv.get('title', 'Untitled')}**.",
                command_items=[_delete_conversation_item(conv)],
                echo_user=False,
            )
        title = conv.get("title", "Untitled")
        storage.delete_conversation(db, conv["id"], request.app.state.config.app.data_dir)
        return _command_response(
            kind="delete_conversation",
            message=f"Deleted **{title}**.",
            deleted_conversation_id=conv["id"],
            echo_user=False,
        )
    if result.kind == "rewind_conversation":
        updated, message = await _rewind_conversation(request, conversation, result.rewind_arg or "")
        if updated and (result.rewind_arg or "").strip():
            return _command_response(
                kind="rewind_conversation",
                message=message,
                conversation=updated,
                echo_user=False,
            )
        return _command_response(
            kind="show_message",
            message=message,
            conversation=updated,
            echo_user=False,
        )
    if result.kind == "search_conversations":
        query = (result.search_query or "").strip()
        type_filter = None
        if query.startswith("--keyword "):
            query = query[len("--keyword ") :].strip()
        elif query.startswith("--type "):
            rest = query[len("--type ") :].strip()
            parts = rest.split(maxsplit=1)
            if parts and parts[0] in {"chat", "note", "document"}:
                type_filter = parts[0]
                query = parts[1] if len(parts) > 1 else ""
        search_results = (
            storage.list_conversations(db, search=query, limit=20, conversation_type=type_filter)
            if query
            else []
        )
        return _command_response(
            kind="show_message",
            message=_search_conversations_markdown(db, result.search_query or "", search_results),
            command_items=_conversation_items(search_results),
        )
    if result.kind == "show_tools":
        return _command_response(kind="show_message", message=build_tools_markdown(result.tool_names), echo_user=True)
    if result.kind == "show_usage":
        return _command_response(kind="show_message", message=_usage_markdown(db, request.app.state.config))
    if result.kind == "show_mcp_status":
        mcp_manager = getattr(request.app.state, "mcp_manager", None)
        statuses = mcp_manager.get_server_statuses() if mcp_manager is not None else {}
        return _command_response(
            kind="show_message",
            message=_mcp_status_markdown(request),
            command_items=_mcp_items(statuses),
        )
    if result.kind == "show_mcp_server_detail":
        mcp_manager = getattr(request.app.state, "mcp_manager", None)
        statuses = mcp_manager.get_server_statuses() if mcp_manager is not None else {}
        return _command_response(
            kind="show_message",
            message=_mcp_server_detail_markdown(request, result.mcp_server_name or ""),
            command_items=_mcp_items(statuses, focus_name=result.mcp_server_name),
        )
    if result.kind == "run_mcp_action":
        return _command_response(
            kind="show_message",
            message=await _run_mcp_action_markdown(request, result.mcp_action or "", result.mcp_server_name or ""),
        )
    if result.kind == "show_skills":
        message = build_skills_markdown(
            result.skill_entries,
            result.skill_warnings,
            has_registry=getattr(request.app.state, "skill_registry", None) is not None,
        )
        if result.command.name == "/reload-skills":
            message = "Reloaded skill registry.\n\n" + message
        return _command_response(
            kind="show_message",
            message=message,
            command_items=_skill_items(list(result.skill_entries)),
        )
    if result.kind == "show_plan_status":
        message = "Planning mode: **active**" if result.plan_mode_enabled else "Planning mode: **off**"
        return _command_response(
            kind="show_plan_status",
            message=message,
            plan_mode_enabled=bool(result.plan_mode_enabled),
        )
    if result.kind == "set_plan_mode":
        enabled = bool(result.plan_mode_enabled)
        message = (
            "Planning mode active. The next turn will stay in investigation mode until you turn it off."
            if enabled
            else "Planning mode off. Full tools restored."
        )
        return _command_response(kind="set_plan_mode", message=message, plan_mode_enabled=enabled, echo_user=False)
    if result.kind == "show_model":
        return _command_response(kind="show_message", message=f"Current model: `{result.model_name}`")
    if result.kind == "show_artifact":
        return _command_response(kind="show_message", message=_artifact_show_markdown(db, result.artifact_fqn or ""))
    if result.kind == "show_pack":
        return _command_response(kind="show_message", message=_pack_show_markdown(db, result.pack_ref or ""))
    if result.kind == "install_pack":
        return _command_response(
            kind="show_message",
            message=_install_or_update_pack_markdown(
                request,
                conversation,
                result.pack_path or "",
                update=False,
                project_scope=result.pack_project_scope,
                attach_after_install=result.pack_attach_after_install,
                priority=result.pack_priority or 50,
            ),
            echo_user=False,
        )
    if result.kind == "update_pack":
        return _command_response(
            kind="show_message",
            message=_install_or_update_pack_markdown(
                request,
                conversation,
                result.pack_path or "",
                update=True,
                project_scope=result.pack_project_scope,
            ),
            echo_user=False,
        )
    if result.kind == "show_space":
        return _command_response(
            kind="show_message",
            message=_space_show_markdown(
                db,
                conversation.get("space_id") if conversation else None,
                result.space_target,
            ),
        )
    if result.kind == "show_space_sources":
        return _command_response(
            kind="show_message",
            message=_space_sources_markdown(
                db,
                conversation.get("space_id") if conversation else None,
                result.space_target,
            ),
        )
    if result.kind == "show_slug":
        _, message = _set_conversation_slug(db, conversation, None)
        return _command_response(kind="show_message", message=message)
    if result.kind == "set_model":
        if not conversation_id:
            return _command_response(
                kind="show_message",
                message="Open or create a conversation before changing the model.",
            )
        updated = storage.update_conversation_model(db, conversation_id, result.model_name or "")
        return _command_response(
            kind="set_model",
            message=f"Active model changed to `{result.model_name}`.",
            conversation=updated,
            model_name=result.model_name,
        )
    if result.kind == "rename_conversation":
        updated, message = _rename_conversation(db, conversation, result.conversation_title or "")
        return _command_response(
            kind="rename_conversation" if updated else "show_message",
            message=message,
            conversation=updated,
        )
    if result.kind == "set_slug":
        updated, message = _set_conversation_slug(db, conversation, result.slug_value)
        return _command_response(
            kind="set_slug" if updated else "show_message",
            message=message,
            conversation=updated,
        )
    if result.kind == "set_space":
        updated, message = _set_space(db, conversation, result.space_target)
        return _command_response(
            kind="set_space" if updated else "show_message",
            message=message,
            conversation=updated,
        )
    if result.kind == "delete_space":
        if not result.command.arg.startswith("delete --confirm "):
            target = result.space_target or ""
            space, candidates = resolve_space(db, target)
            if candidates:
                names = ", ".join(candidate["name"] for candidate in candidates[:5])
                return _command_response(
                    kind="show_message",
                    message=f"Space `{target}` is ambiguous. Matches: {names}",
                )
            if not space:
                return _command_response(kind="show_message", message=f"Space `{target}` not found.")
            return _command_response(
                kind="show_message",
                message=f"Delete space **{space['name']}**?",
                command_items=[
                    {
                        "kind": "space",
                        "id": space["id"],
                        "title": space["name"],
                        "meta": "Delete space",
                        "actions": [
                            {
                                "label": "Delete",
                                "command": f"/space delete --confirm {space['id']}",
                                "requires_confirm": True,
                                "confirm_text": f"Delete {space['name']}?",
                                "confirm_label": "Delete",
                            }
                        ],
                    }
                ],
                echo_user=False,
            )
        updated, message = _delete_space(db, conversation, result.space_target)
        return _command_response(
            kind="delete_space",
            message=message,
            conversation=updated,
            echo_user=False,
        )
    if result.kind == "delete_artifact":
        return _command_response(
            kind="show_message",
            message=_delete_artifact(request, result.artifact_fqn or ""),
            echo_user=False,
        )
    if result.kind == "attach_pack":
        return _command_response(
            kind="show_message",
            message=_attach_pack_markdown(
                request,
                conversation,
                result.pack_ref or "",
                project_scope=result.pack_project_scope,
            ),
            echo_user=False,
        )
    if result.kind == "detach_pack":
        return _command_response(
            kind="show_message",
            message=_detach_pack_markdown(
                request,
                conversation,
                result.pack_ref or "",
                project_scope=result.pack_project_scope,
            ),
            echo_user=False,
        )
    if result.kind == "delete_pack":
        return _command_response(
            kind="show_message",
            message=_remove_pack(request, result.pack_ref or ""),
            echo_user=False,
        )
    if result.kind == "new_conversation":
        uid, uname = _get_identity(request)
        space_id = conversation.get("space_id") if conversation else None
        conv = storage.create_conversation(
            db,
            title=result.conversation_title or "New Conversation",
            conversation_type=result.conversation_type or "chat",
            user_id=uid,
            user_display_name=uname,
            working_dir=(conversation.get("working_dir") if conversation else None),
            space_id=space_id,
        )
        return _command_response(
            kind="new_conversation",
            message="Started a new conversation.",
            conversation=conv,
            echo_user=False,
        )
    if result.kind == "show_message":
        return _command_response(kind="show_message", message=result.message or "", echo_user=result.echo_user)
    if result.kind == "compact_conversation":
        updated, message = await _compact_conversation(request, conversation)
        return _command_response(
            kind="compact_conversation" if updated else "show_message",
            message=message,
            conversation=updated,
            echo_user=False,
        )
    raise HTTPException(status_code=500, detail=f"Unhandled web command result kind: {result.kind}")
def _resolve_sources(
    db: Any,
    source_ids: list[str],
    source_tag: str | None,
    source_group_id: str | None,
    limit: int = 50_000,
    *,
    space_id: str | None = None,
) -> str:
    """Resolve source references and return XML-delimited source content string.

    When *space_id* is given, only sources linked to that space are included.
    Sources auto-injected by the space resolution layer always pass this check;
    this guard prevents a client from injecting arbitrary source IDs that don't
    belong to the current scope.
    """
    # Pre-compute allowed source IDs when scoping is active
    _allowed_ids: set[str] | None = None
    if space_id:
        _allowed_ids = set()
        _allowed_ids.update(s["id"] for s in storage.get_space_sources(db, space_id))

    _referenced_sources: list[dict[str, Any]] = []
    if source_ids:
        for sid in source_ids[:20]:
            if _allowed_ids is not None and sid not in _allowed_ids:
                continue
            src = storage.get_source(db, sid)
            if src and src.get("content"):
                _referenced_sources.append(src)
    if source_tag:
        tagged = storage.list_sources(db, tag_id=source_tag, limit=20)
        for src in tagged:
            if _allowed_ids is not None and src["id"] not in _allowed_ids:
                continue
            if src.get("content") and src["id"] not in {s["id"] for s in _referenced_sources}:
                full = storage.get_source(db, src["id"])
                if full and full.get("content"):
                    _referenced_sources.append(full)
    if source_group_id:
        grouped = storage.list_sources(db, group_id=source_group_id, limit=20)
        for src in grouped:
            if _allowed_ids is not None and src["id"] not in _allowed_ids:
                continue
            if src.get("content") and src["id"] not in {s["id"] for s in _referenced_sources}:
                full = storage.get_source(db, src["id"])
                if full and full.get("content"):
                    _referenced_sources.append(full)

    if not _referenced_sources:
        return ""

    source_parts: list[str] = []
    total_chars = 0
    for src in _referenced_sources:
        content = src.get("content", "")
        remaining = limit - total_chars
        if remaining <= 0:
            break
        if len(content) > remaining:
            content = content[:remaining] + "\n[...truncated...]"
        safe_title = str(src.get("title", ""))[:200]
        src_id = src["id"]
        source_parts.append(f"### {safe_title}\n" + wrap_untrusted(content, f"source:{src_id}", "reference"))
        total_chars += len(content)
    if source_parts:
        return (
            "\n\n## Referenced Knowledge Sources\n"
            "The user has attached the following sources as context for this conversation.\n"
            + "\n\n".join(source_parts)
        )
    return ""


def _build_tool_list(
    tool_registry: Any,
    mcp_manager: Any,
    plan_mode: bool,
    conversation_id: str,
    data_dir: Path,
    max_tools: int,
    read_only: bool = False,
    tier_overrides: dict[str, str] | None = None,
    skill_registry: Any = None,
) -> tuple[list[dict[str, Any]], Path | None, str]:
    """Build the unified tool list (builtins + MCP) and return (tools, plan_path, plan_prompt).

    plan_prompt is the planning system prompt to inject, or empty string if not in plan mode.
    """
    tools_openai: list[dict[str, Any]] = list(tool_registry.get_openai_tools())
    if mcp_manager:
        mcp_tools = mcp_manager.get_openai_tools()
        if mcp_tools:
            tools_openai.extend(mcp_tools)

    if read_only:
        from ..tools.tiers import filter_read_only_tools

        tools_openai = filter_read_only_tools(tools_openai, tier_overrides)

    plan_path: Path | None = None
    plan_prompt = ""
    if plan_mode:
        from ..cli.plan import PLAN_MODE_ALLOWED_TOOLS, build_planning_system_prompt, get_plan_file_path

        plan_path = get_plan_file_path(data_dir, conversation_id)
        tools_openai = [t for t in tools_openai if t.get("function", {}).get("name") in PLAN_MODE_ALLOWED_TOOLS]
        plan_prompt = "\n\n" + build_planning_system_prompt(plan_path)

    from ..tools import cap_tools

    tools_openai = cap_tools(tools_openai, set(tool_registry.list_tools()), limit=max_tools)

    # Append invoke_skill AFTER cap_tools so it's never dropped by tool capping
    # (matches CLI behavior where invoke_skill is appended post-cap)
    if skill_registry is not None:
        invoke_def = skill_registry.get_invoke_skill_definition()
        if invoke_def:
            tools_openai.append(invoke_def)

    return tools_openai, plan_path, plan_prompt


async def _build_chat_system_prompt(
    *,
    ai_service: AIService,
    tool_registry: Any,
    mcp_manager: Any,
    config: Any,
    db: Any,
    conversation_id: str,
    space_instructions: str | None = None,
    plan_prompt: str,
    plan_mode: bool,
    message_text: str,
    source_ids: list[str],
    source_tag: str | None,
    source_group_id: str | None,
    vec_enabled: bool = False,
    embedding_service: Any = None,
    reranker_service: Any = None,
    injection_detector: Any = None,
    artifact_registry: Any = None,
    skill_registry: Any = None,
    space_id: str | None = None,
    attachment_filenames: list[str] | None = None,
    vec_manager: Any | None = None,
) -> tuple[str, dict[str, Any]]:
    """Assemble the extra system prompt from all context sources.

    Returns (extra_prompt, metadata) where metadata includes RAG/source status.
    """
    from ..services.context_trust import sanitize_trust_tags

    meta: dict[str, Any] = {}

    # Runtime context for self-awareness
    runtime_ctx = build_runtime_context(
        model=ai_service.config.model,
        builtin_tools=list(tool_registry.list_tools()),
        mcp_servers=mcp_manager.get_server_statuses() if mcp_manager else None,
        interface="web",
        tls_enabled=config.app.tls,
    )
    extra = trusted_section_marker() + runtime_ctx

    # Space instructions
    if space_instructions:
        safe_instr = sanitize_trust_tags(space_instructions)
        extra += "\n\n<space_instructions>\n" + safe_instr + "\n</space_instructions>"

    # ANTEROOM.md conventions
    file_instructions = load_instructions()
    if file_instructions:
        extra += "\n\n" + file_instructions

    # Inject artifacts (instructions, rules, context) from registry
    if artifact_registry is not None:
        _artifact_parts: list[str] = []
        for _atype in ("instruction", "rule", "context"):
            for _art in artifact_registry.list_all(artifact_type=_atype):
                if _art.content:
                    if _art.source == "built_in":
                        tag = f'<artifact type="{_atype}" fqn="{_art.fqn}">'
                        _artifact_parts.append(f"{tag}\n{_art.content}\n</artifact>")
                    else:
                        _artifact_parts.append(
                            wrap_untrusted(_art.content, origin=f"artifact:{_art.fqn}", content_type=_atype)
                        )
        if _artifact_parts:
            extra += "\n\n" + "\n".join(_artifact_parts)

    # Skill catalog
    if skill_registry is not None:
        skill_descs = skill_registry.get_skill_descriptions()
        if skill_descs:
            skill_lines = [
                "\n<available_skills>",
                "The following skills are available. When the user's request clearly matches a skill, "
                "use the invoke_skill tool to run it.",
            ]
            for sname, sdesc in skill_descs:
                skill_lines.append(f"- {sname}: {sdesc}")
            skill_lines.append("</available_skills>")
            extra += "\n".join(skill_lines)

    # Plan mode prompt
    if plan_prompt:
        extra += plan_prompt

    # Inject canary token into trusted section (before untrusted marker)
    if injection_detector is not None and injection_detector.enabled:
        _canary_seg = injection_detector.canary_prompt_segment()
        if _canary_seg:
            extra += _canary_seg

    # Structural separation: everything below this marker is external/untrusted data
    extra += untrusted_section_marker()

    # Attachment guidance — placed in the untrusted section because filenames are user-controlled
    if attachment_filenames:
        sanitized = [sanitize_trust_tags(fn) for fn in attachment_filenames]
        names = ", ".join(sanitized)
        extra += (
            "\n\n## Attached Files\n"
            f"The user has attached the following file(s): {names}\n"
            "Their content has been extracted and included directly in the user's message below. "
            "Read and use that content to answer the user's request. "
            "Do NOT use file tools (read_file, pptx, xlsx, docx, glob_files, etc.) to re-read these files — "
            "the content is already available in the conversation."
        )

    # Canvas context (cap at 10K chars)
    canvas_context_limit = 10_000
    canvas_data = storage.get_canvas_for_conversation(db, conversation_id)
    if canvas_data:
        content = canvas_data["content"] or ""
        truncated = len(content) > canvas_context_limit
        if truncated:
            content = content[:canvas_context_limit]
        truncation_notice = "[...truncated, full content available via canvas tools...]\n" if truncated else ""
        # SECURITY-REVIEW: title, language, and content are all user-controlled data.
        # Wrapped in defensive prompt envelope to mitigate indirect prompt injection.
        safe_title = str(canvas_data["title"] or "")[:200]
        safe_lang = str(canvas_data.get("language") or "text")[:50]
        canvas_body = f"{content}\n{truncation_notice}"
        canvas_context = (
            f"\n\n## Current Canvas\n"
            f"Title: {safe_title}\n"
            f"Language: {safe_lang}\n"
            f"Version: {canvas_data['version']}\n"
            f"{wrap_untrusted(canvas_body, 'canvas', 'user-data')}\n"
            f"Use patch_canvas for small targeted edits or update_canvas for full rewrites."
        )
        extra += canvas_context

    # Source references
    source_content = _resolve_sources(db, source_ids, source_tag, source_group_id, space_id=space_id)
    if source_content:
        extra += source_content
        meta["sources_truncated"] = "[...truncated...]" in source_content

    # RAG context (skip in plan mode)
    rag_config = getattr(config, "rag", None)
    _rag_mode = getattr(rag_config, "retrieval_mode", "dense") if rag_config else "dense"
    _rag_uses_keyword = _rag_mode in ("keyword", "hybrid")
    # Keyword and hybrid modes can run without embeddings; dense requires both.
    _rag_has_backend = (vec_enabled and embedding_service) or _rag_uses_keyword
    if rag_config and rag_config.enabled and not plan_mode and _rag_has_backend and message_text.strip():
        try:
            from ..services.rag import format_rag_context, retrieve_context, strip_rag_context

            extra = strip_rag_context(extra)
            _reranker_cfg = getattr(config, "reranker", None)
            rag_chunks, rag_reason = await retrieve_context(
                query=message_text,
                db=db,
                embedding_service=embedding_service,
                config=rag_config,
                current_conversation_id=conversation_id,
                space_id=space_id,
                vec_manager=vec_manager,
                reranker_service=reranker_service,
                reranker_config=_reranker_cfg,
            )
            meta["rag_status"] = "ok" if rag_chunks else "no_results"
            meta["rag_chunks"] = len(rag_chunks)
            if rag_reason:
                meta["rag_reason"] = rag_reason
            meta["rag_sources"] = [
                {"label": c.source_label, "type": c.source_type, "source_id": c.source_id} for c in rag_chunks
            ]
            if rag_chunks:
                extra += format_rag_context(rag_chunks)
        except Exception:
            logger.debug("RAG retrieval failed, continuing without context", exc_info=True)
            meta["rag_status"] = "failed"
            meta["rag_chunks"] = 0
            meta["rag_sources"] = []
    else:
        # Capture the reason RAG was skipped so prompt_meta is always consistent
        if not rag_config:
            meta["rag_status"] = "no_config"
        elif not rag_config.enabled:
            meta["rag_status"] = "disabled"
        elif plan_mode:
            meta["rag_status"] = "skipped_plan_mode"
        elif not _rag_has_backend:
            meta["rag_status"] = "no_vec_support"
        else:
            meta["rag_status"] = "skipped"
        meta["rag_chunks"] = 0
        meta["rag_sources"] = []

    # Codebase index
    try:
        from ..services.codebase_index import create_index_service

        _index_service = create_index_service(config)
        _index_root = getattr(tool_registry, "_working_dir", None) or os.getcwd()
        if _index_service:
            _index_map = _index_service.get_map(_index_root, token_budget=config.codebase_index.map_tokens)
            if _index_map:
                extra += "\n" + _index_map
    except Exception:
        logger.debug("Codebase index unavailable, continuing without it", exc_info=True)

    return extra, meta


@dataclass
class WebConfirmContext:
    """Shared state for approval and ask_user callbacks."""

    pending_approvals: dict[str, Any]
    event_bus: Any
    db_name: str
    conversation_id: str
    approval_timeout: int
    request: Request
    tool_registry: Any
    last_resolved_scope: dict[int, str] = field(default_factory=dict)


async def _web_confirm_tool(ctx: WebConfirmContext, verdict: Any) -> bool:
    """Handle tool approval via the web UI event bus."""
    import secrets as _secrets

    max_pending = 100
    if len(ctx.pending_approvals) >= max_pending:
        logger.warning("Pending approvals limit reached (%d); denying", len(ctx.pending_approvals))
        return False

    approval_id = _secrets.token_urlsafe(16)
    approval_event = asyncio.Event()
    entry = {"event": approval_event, "approved": False, "scope": "once"}
    ctx.pending_approvals[approval_id] = entry

    if ctx.event_bus:
        await ctx.event_bus.publish(
            f"global:{ctx.db_name}",
            {
                "type": "approval_required",
                "data": {
                    "approval_id": approval_id,
                    "tool_name": verdict.tool_name,
                    "reason": verdict.reason,
                    "details": verdict.details,
                    "conversation_id": ctx.conversation_id,
                },
            },
        )

    try:
        elapsed = 0.0
        poll_interval = 1.0
        while not approval_event.is_set():
            if elapsed >= ctx.approval_timeout:
                raise asyncio.TimeoutError()
            try:
                if await ctx.request.is_disconnected():
                    raise asyncio.TimeoutError()
            except asyncio.TimeoutError:
                raise
            except Exception:
                pass
            try:
                await asyncio.wait_for(approval_event.wait(), timeout=poll_interval)
            except asyncio.TimeoutError:
                elapsed += poll_interval
    except asyncio.TimeoutError:
        logger.warning("Approval timed out (id=%s): %s", approval_id, verdict.reason)
        if ctx.event_bus:
            await ctx.event_bus.publish(
                f"global:{ctx.db_name}",
                {
                    "type": "approval_resolved",
                    "data": {
                        "approval_id": approval_id,
                        "approved": False,
                        "reason": "timed_out",
                    },
                },
            )
        return False
    finally:
        ctx.pending_approvals.pop(approval_id, None)

    approved = bool(entry.get("approved", False))
    if approved:
        scope = str(entry.get("scope", "once"))
        task = asyncio.current_task()
        if task is not None:
            ctx.last_resolved_scope[id(task)] = scope
        if scope in ("session", "always"):
            ctx.tool_registry.grant_session_permission(verdict.tool_name)
        if scope == "always":
            try:
                from ..config import write_allowed_tool

                write_allowed_tool(verdict.tool_name)
            except Exception as e:
                logger.warning("Could not persist 'Allow Always' for %s: %s", verdict.tool_name, e)

        if ctx.event_bus:
            await ctx.event_bus.publish(
                f"conversation:{ctx.conversation_id}",
                {
                    "type": "approval_executing",
                    "data": {
                        "conversation_id": ctx.conversation_id,
                        "tool_name": verdict.tool_name,
                    },
                },
            )

    return approved


async def _web_ask_user_callback(ctx: WebConfirmContext, question: str, options: list[str] | None = None) -> str:
    """Handle ask_user tool via the web UI event bus."""
    import secrets as _secrets

    max_pending = 100
    if len(ctx.pending_approvals) >= max_pending:
        logger.warning("Pending approvals limit reached (%d); skipping ask_user", len(ctx.pending_approvals))
        return ""

    ask_id = _secrets.token_urlsafe(16)
    ask_event = asyncio.Event()
    entry: dict[str, Any] = {"event": ask_event, "approved": False, "scope": "once", "answer": ""}
    ctx.pending_approvals[ask_id] = entry

    event_data: dict[str, Any] = {
        "ask_id": ask_id,
        "question": question,
        "conversation_id": ctx.conversation_id,
    }
    if options:
        event_data["options"] = options

    if ctx.event_bus:
        await ctx.event_bus.publish(
            f"global:{ctx.db_name}",
            {
                "type": "ask_user_required",
                "data": event_data,
            },
        )

    try:
        elapsed = 0.0
        poll_interval = 1.0
        while not ask_event.is_set():
            if elapsed >= ctx.approval_timeout:
                raise asyncio.TimeoutError()
            try:
                if await ctx.request.is_disconnected():
                    raise asyncio.TimeoutError()
            except asyncio.TimeoutError:
                raise
            except Exception:
                pass
            try:
                await asyncio.wait_for(ask_event.wait(), timeout=poll_interval)
            except asyncio.TimeoutError:
                elapsed += poll_interval
    except asyncio.TimeoutError:
        logger.warning("ask_user timed out (id=%s)", ask_id)
        return ""
    finally:
        ctx.pending_approvals.pop(ask_id, None)

    return str(entry.get("answer", ""))


@dataclass
class ToolExecutorContext:
    """Shared state for tool execution in the web UI."""

    tool_registry: Any
    mcp_manager: Any
    confirm_ctx: WebConfirmContext
    ai_service: Any
    cancel_event: asyncio.Event
    db: Any
    uid: str | None
    uname: str | None
    conversation_id: str
    tools_openai: list[dict[str, Any]]
    subagent_events: dict[str, list[dict[str, Any]]]
    subagent_limiter: Any
    sa_config: Any
    request_config: Any
    rate_limiter: Any = None
    skill_registry: Any = None
    rule_enforcer: Any = None
    subagent_counter: list[int] = field(default_factory=lambda: [0])
    max_subagent_events: int = 500


async def _web_event_sink_fn(ctx: ToolExecutorContext, agent_id: str, event: Any) -> None:
    """Buffer sub-agent events for SSE emission, partitioned by agent_id."""
    kind = event.kind
    data = event.data
    if kind in ("subagent_start", "subagent_end", "tool_call_start"):
        buf = ctx.subagent_events.setdefault(agent_id, [])
        if len(buf) < ctx.max_subagent_events:
            buf.append({"kind": kind, "agent_id": agent_id, **data})


def _scope_to_decision(confirm_ctx: WebConfirmContext) -> str:
    """Map the last resolved approval scope to an audit decision string."""
    task = asyncio.current_task()
    task_id = id(task) if task is not None else None
    scope = confirm_ctx.last_resolved_scope.pop(task_id, "once") if task_id else "once"
    return {"once": "allowed_once", "session": "allowed_session", "always": "allowed_always"}.get(scope, "allowed_once")


async def _execute_web_tool(ctx: ToolExecutorContext, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool call in the web UI context."""

    async def _confirm(verdict: Any) -> bool:
        return await _web_confirm_tool(ctx.confirm_ctx, verdict)

    async def _ask_user(question: str, options: list[str] | None = None) -> str:
        return await _web_ask_user_callback(ctx.confirm_ctx, question, options)

    if tool_name in ("create_canvas", "update_canvas", "patch_canvas"):
        arguments = {
            **arguments,
            "_conversation_id": ctx.conversation_id,
            "_db": ctx.db,
            "_user_id": ctx.uid,
            "_user_display_name": ctx.uname,
        }
    elif tool_name == "run_agent":
        ctx.subagent_counter[0] += 1
        arguments = {
            **arguments,
            "_ai_service": ctx.ai_service,
            "_tool_registry": ctx.tool_registry,
            "_mcp_manager": ctx.mcp_manager,
            "_cancel_event": ctx.cancel_event,
            "_depth": 0,
            "_agent_id": f"agent-{ctx.subagent_counter[0]}",
            "_event_sink": lambda agent_id, event: _web_event_sink_fn(ctx, agent_id, event),
            "_limiter": ctx.subagent_limiter,
            "_confirm_callback": _confirm,
            "_config": ctx.sa_config,
        }
    elif tool_name == "invoke_skill":
        skill_name = arguments.get("skill_name", "")
        skill = ctx.skill_registry.get(skill_name) if ctx.skill_registry else None
        if not skill:
            return {"error": f"Unknown skill: {skill_name}"}
        args = arguments.get("args", "")
        prompt = skill.prompt
        if args:
            from ..cli.skills import _expand_args
            from ..services.context_trust import sanitize_trust_tags

            args = sanitize_trust_tags(args[:2000])
            prompt = _expand_args(prompt, f"<skill_args>{args}</skill_args>")
        queue = _message_queues.get(ctx.conversation_id)
        if queue is None:
            return {"error": "Skill invocation unavailable (no active message queue)"}
        await queue.put({"role": "user", "content": prompt})
        return {"status": "skill_invoked", "skill": skill_name}
    elif tool_name == "ask_user":
        arguments = {**arguments, "_ask_callback": _ask_user}
    elif tool_name == "introspect":
        arguments = {
            **arguments,
            "_config": ctx.request_config,
            "_mcp_manager": ctx.mcp_manager,
            "_tool_registry": ctx.tool_registry,
            "_skill_registry": ctx.skill_registry,
            "_instructions_info": None,
            "_tools_openai": ctx.tools_openai,
            "_working_dir": None,
        }

    if ctx.tool_registry.has_tool(tool_name):
        result = await ctx.tool_registry.call_tool(
            tool_name, arguments, confirm_callback=_confirm, rule_enforcer_override=ctx.rule_enforcer
        )
        if result.get("_approval_decision") == "allowed_once":
            result["_approval_decision"] = _scope_to_decision(ctx.confirm_ctx)
        return dict(result)
    if ctx.mcp_manager:
        verdict = ctx.tool_registry.check_safety(tool_name, arguments, rule_enforcer_override=ctx.rule_enforcer)
        if verdict and verdict.needs_approval:
            if verdict.hard_denied:
                return {
                    "error": f"Tool '{tool_name}' is blocked by configuration",
                    "safety_blocked": True,
                    "_approval_decision": "hard_denied",
                }
            confirmed = await _confirm(verdict)
            if not confirmed:
                return {"error": "Operation denied by user", "exit_code": -1, "_approval_decision": "denied"}
        # Rate limiting for MCP tools (built-in tools checked in call_tool)
        if ctx.rate_limiter:
            rl_v = ctx.rate_limiter.check(tool_name)
            if rl_v and rl_v.exceeded and ctx.rate_limiter.config.action == "block":
                return {
                    "error": rl_v.reason,
                    "safety_blocked": True,
                    "rate_limited": True,
                    "_approval_decision": "rate_limited",
                }
        result = await ctx.mcp_manager.call_tool(tool_name, arguments)
        if ctx.rate_limiter:
            ctx.rate_limiter.record_call(success="error" not in result)
        decision = _scope_to_decision(ctx.confirm_ctx) if (verdict and verdict.needs_approval) else "auto"
        result["_approval_decision"] = decision
        return dict(result)
    raise ValueError(f"Unknown tool: {tool_name}")


def _canvas_needs_approval(safety_config: Any, tool_registry: Any) -> bool:
    """Check whether canvas tools would require approval in the current session.

    Canvas tools are READ tier by default, so this returns False unless the
    user has explicitly overridden the tier via config or denied them.
    """
    from ..tools.tiers import ApprovalMode, get_tool_tier, parse_approval_mode, should_require_approval

    if safety_config is None:
        return True
    raw_mode = safety_config.approval_mode
    mode: ApprovalMode = parse_approval_mode(str(raw_mode)) if not isinstance(raw_mode, int) else ApprovalMode(raw_mode)
    tier = get_tool_tier("create_canvas", getattr(safety_config, "tool_tiers", None))
    allowed = getattr(tool_registry, "_session_allowed", None) or set()
    config_allowed = set(safety_config.allowed_tools) if safety_config.allowed_tools else None
    result = should_require_approval("create_canvas", tier, mode, config_allowed, None, allowed)
    return result is True


@dataclass
class StreamContext:
    """State for the SSE chat event stream."""

    ai_service: Any
    ai_messages: list[dict[str, Any]]
    tool_executor: Any
    tools: list[dict[str, Any]] | None
    cancel_event: asyncio.Event
    extra_system_prompt: str
    conversation_id: str
    plan_mode: bool
    plan_path: Path | None
    db: Any
    db_name: str
    uid: str | None
    uname: str | None
    event_bus: Any
    client_id: str
    tool_registry: Any
    mcp_manager: Any
    subagent_events: dict[str, list[dict[str, Any]]]
    is_first_message: bool
    first_user_text: str
    conv_title: str
    embedding_worker: Any
    planning_config: Any
    budget_config: Any = None
    request: Any = None
    canvas_needs_approval: bool = False
    token_throttle_interval: float = 0.1
    last_token_broadcast: float = 0.0
    prompt_meta: dict[str, Any] = field(default_factory=dict)
    user_msg: dict[str, Any] | None = None


_DISCONNECT_POLL_INTERVAL = 3  # seconds


async def _poll_disconnect(
    request: Any, cancel_event: asyncio.Event, interval: float = _DISCONNECT_POLL_INTERVAL
) -> None:
    """Background task that polls for client disconnect and cancels the stream."""
    try:
        while not cancel_event.is_set():
            await asyncio.sleep(interval)
            try:
                if await request.is_disconnected():
                    logger.info("Client disconnected — cancelling stream")
                    cancel_event.set()
                    return
            except Exception:
                logger.debug("is_disconnected() check failed — treating as disconnected", exc_info=True)
                cancel_event.set()
                return
    except asyncio.CancelledError:
        pass


def _fallback_stream_cleanup(gen: Any) -> None:
    """Emergency cleanup when the inner SSE generator's finally block failed to run.

    Extracts the StreamContext from the generator's locals and cleans up
    _active_streams, _message_queues, _cancel_events, and _stream_locks.
    """
    try:
        # _stream_chat_events generators expose their locals via gi_frame
        frame = getattr(gen, "ag_frame", None) or getattr(gen, "gi_frame", None)
        if frame is None:
            return
        ctx = frame.f_locals.get("ctx")
        if ctx is None:
            return
        cid = ctx.conversation_id
        _active_streams.pop(cid, None)
        queue = _message_queues.get(cid)
        if queue and queue.empty():
            _message_queues.pop(cid, None)
        ce_set = _cancel_events.get(cid)
        if ce_set is not None:
            ce_set.discard(ctx.cancel_event)
            if not ce_set and _cancel_events.get(cid) is ce_set:
                _cancel_events.pop(cid, None)
        if cid not in _active_streams:
            _stream_locks.pop(cid, None)
        logger.info("Fallback cleanup completed for conversation %s", cid)
    except Exception:
        logger.debug("Fallback cleanup failed", exc_info=True)


_KEEPALIVE_INTERVAL = 15  # seconds between SSE keepalive pings


async def _with_keepalive(gen: Any, interval: float = _KEEPALIVE_INTERVAL) -> Any:
    """Wrap an async generator to yield keepalive comments during long pauses.

    Prevents browsers and proxies from closing the SSE connection when the
    agent loop blocks (e.g. during ask_user or tool approval waits).

    Uses asyncio.wait() instead of wait_for() to avoid cancelling the
    underlying generator coroutine on timeout.
    """
    aiter = gen.__aiter__()
    pending_next: asyncio.Task | None = None
    try:
        while True:
            if pending_next is None:
                pending_next = asyncio.ensure_future(aiter.__anext__())
            done, _ = await asyncio.wait({pending_next}, timeout=interval)
            if done:
                try:
                    yield pending_next.result()
                except StopAsyncIteration:
                    break
                pending_next = None
            else:
                yield {"comment": "keepalive"}
    finally:
        if pending_next is not None and not pending_next.done():
            pending_next.cancel()
            try:
                await pending_next
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
        # Ensure the inner generator's finally block runs (cleans up
        # _active_streams, _message_queues, _cancel_events, and the
        # disconnect poller task).
        try:
            await aiter.aclose()
        except Exception:
            # If aclose() fails, the inner generator's finally block never ran.
            # Do manual fallback cleanup to prevent permanent resource leaks.
            logger.warning("Inner generator aclose() failed; running fallback cleanup", exc_info=True)
            _fallback_stream_cleanup(gen)


async def _stream_chat_events(ctx: StreamContext) -> Any:
    """Async generator that yields SSE events for the chat stream."""
    from ..services.agent_loop import run_agent_loop

    current_assistant_msg = None
    _pending_tool_inputs: dict[str, Any] = {}
    _streamed_content = ""
    _canvas_args_accum: dict[int, str] = {}
    _canvas_content_sent: dict[int, int] = {}
    _canvas_stream_started: set[int] = set()

    # Spawn background disconnect poller so stale streams are cancelled promptly
    _disconnect_task: asyncio.Task[None] | None = None
    if ctx.request is not None:
        _disconnect_task = asyncio.create_task(_poll_disconnect(ctx.request, ctx.cancel_event))

    if ctx.event_bus:
        await ctx.event_bus.publish(
            f"conversation:{ctx.conversation_id}",
            {
                "type": "stream_start",
                "data": {"conversation_id": ctx.conversation_id, "client_id": ctx.client_id},
            },
        )

    # Emit prompt metadata (RAG status, source info) as an early event
    if ctx.prompt_meta:
        yield {"event": "prompt_meta", "data": json.dumps(ctx.prompt_meta)}

    # Emit user message metadata so the client can attach action buttons
    if ctx.user_msg:
        yield {
            "event": "user_message",
            "data": json.dumps({"id": ctx.user_msg["id"], "position": ctx.user_msg["position"]}),
        }

    try:
        _planning_cfg = ctx.planning_config

        async def _get_token_totals() -> tuple[int, int]:
            return (
                storage.get_conversation_token_total(ctx.db, ctx.conversation_id),
                storage.get_daily_token_total(ctx.db),
            )

        # Retrieve app-scoped DLP scanner (constructed once at startup)
        _dlp_scanner = getattr(getattr(ctx.request.app, "state", None), "dlp_scanner", None)

        # Retrieve app-scoped injection detector
        _injection_detector = getattr(getattr(ctx.request.app, "state", None), "injection_detector", None)

        # Construct per-request output content filter (needs system prompt for leak detection)
        _output_filter = None
        _app_config = getattr(getattr(ctx.request.app, "state", None), "config", None)
        _of_cfg = getattr(getattr(_app_config, "safety", None), "output_filter", None)
        if _of_cfg is not None and _of_cfg.enabled:
            from ..services.output_filter import OutputContentFilter

            _output_filter = OutputContentFilter(_of_cfg, system_prompt=ctx.extra_system_prompt)

        agent_gen = run_agent_loop(
            ai_service=ctx.ai_service,
            messages=ctx.ai_messages,
            tool_executor=ctx.tool_executor,
            tools_openai=ctx.tools,
            cancel_event=ctx.cancel_event,
            extra_system_prompt=ctx.extra_system_prompt,
            message_queue=_message_queues.get(ctx.conversation_id),
            narration_cadence=ctx.ai_service.config.narration_cadence,
            auto_plan_threshold=(
                _planning_cfg.auto_threshold_tools if not ctx.plan_mode and _planning_cfg.auto_mode != "off" else 0
            ),
            budget_config=ctx.budget_config,
            get_token_totals=_get_token_totals,
            dlp_scanner=_dlp_scanner,
            injection_detector=_injection_detector,
            output_filter=_output_filter,
            max_consecutive_text_only=getattr(
                getattr(_app_config, "cli", None),
                "max_consecutive_text_only",
                CliConfig.max_consecutive_text_only,
            ),
            max_line_repeats=getattr(
                getattr(_app_config, "cli", None),
                "max_line_repeats",
                CliConfig.max_line_repeats,
            ),
            max_identical_tool_repeats=getattr(
                getattr(_app_config, "cli", None),
                "max_identical_tool_repeats",
                CliConfig.max_identical_tool_repeats,
            ),
        )
        _pending_usage: dict[str, Any] | None = None
        async for agent_event in _with_keepalive(agent_gen):
            if isinstance(agent_event, dict) and "comment" in agent_event:
                yield agent_event
                continue

            kind = agent_event.kind
            data = agent_event.data

            if kind == "usage":
                _pending_usage = data
                continue

            elif kind == "thinking":
                yield {"event": "thinking", "data": json.dumps({})}

            elif kind == "phase":
                yield {"event": "phase", "data": json.dumps(data)}

            elif kind == "retrying":
                yield {"event": "retrying", "data": json.dumps(data)}

            elif kind == "token":
                yield {"event": "token", "data": json.dumps(data)}
                _streamed_content += data.get("content", "")

                if ctx.event_bus:
                    now = time_mod.monotonic()
                    if now - ctx.last_token_broadcast >= ctx.token_throttle_interval:
                        ctx.last_token_broadcast = now
                        await ctx.event_bus.publish(
                            f"conversation:{ctx.conversation_id}",
                            {
                                "type": "stream_token",
                                "data": {
                                    "conversation_id": ctx.conversation_id,
                                    "content": data.get("content", ""),
                                    "client_id": ctx.client_id,
                                },
                            },
                        )

            elif kind == "tool_call_args_delta":
                tool_name = data.get("tool_name", "")
                idx = data.get("index", 0)
                if tool_name in _CANVAS_STREAMING_TOOLS and not ctx.canvas_needs_approval:
                    _canvas_args_accum.setdefault(idx, "")
                    if len(_canvas_args_accum[idx]) > MAX_CANVAS_ARGS_ACCUM:
                        continue
                    _canvas_args_accum[idx] += data.get("delta", "")
                    content = _extract_streaming_content(_canvas_args_accum[idx])
                    if content is not None:
                        prev_len = _canvas_content_sent.get(idx, 0)
                        if len(content) > prev_len:
                            delta_text = content[prev_len:]
                            _canvas_content_sent[idx] = len(content)
                            if idx not in _canvas_stream_started:
                                _canvas_stream_started.add(idx)
                                language = _extract_streaming_language(_canvas_args_accum[idx])
                                yield {
                                    "event": "canvas_stream_start",
                                    "data": json.dumps({"tool_name": tool_name, "language": language}),
                                }
                            yield {
                                "event": "canvas_streaming",
                                "data": json.dumps({"content_delta": delta_text}),
                            }

            elif kind == "tool_batch_start":
                yield {
                    "event": "tool_batch_start",
                    "data": json.dumps({"call_count": data["call_count"]}),
                }

            elif kind == "tool_call_start":
                idx = data.get("index", 0)
                _canvas_args_accum.pop(idx, None)
                _canvas_content_sent.pop(idx, None)
                _canvas_stream_started.discard(idx)
                _pending_tool_inputs[data["id"]] = data["arguments"]
                yield {
                    "event": "tool_call_start",
                    "data": json.dumps(
                        {
                            "id": data["id"],
                            "tool_name": data["tool_name"],
                            "server_name": "",
                            "input": data["arguments"],
                        }
                    ),
                }

            elif kind == "assistant_message":
                current_assistant_msg = storage.create_message(
                    ctx.db,
                    ctx.conversation_id,
                    "assistant",
                    data["content"],
                    user_id=ctx.uid,
                    user_display_name=ctx.uname,
                )

                if _pending_usage and current_assistant_msg:
                    storage.update_message_usage(
                        ctx.db,
                        current_assistant_msg["id"],
                        _pending_usage.get("prompt_tokens", 0),
                        _pending_usage.get("completion_tokens", 0),
                        _pending_usage.get("total_tokens", 0),
                        _pending_usage.get("model", ""),
                    )
                    _pending_usage = None

                if ctx.embedding_worker and current_assistant_msg:
                    asyncio.create_task(
                        ctx.embedding_worker.embed_message(
                            current_assistant_msg["id"],
                            data["content"],
                            ctx.conversation_id,
                        )
                    )

                if ctx.event_bus and current_assistant_msg:
                    await ctx.event_bus.publish(
                        f"conversation:{ctx.conversation_id}",
                        {
                            "type": "new_message",
                            "data": {
                                "conversation_id": ctx.conversation_id,
                                "message_id": current_assistant_msg["id"],
                                "role": "assistant",
                                "content": data["content"],
                                "position": current_assistant_msg["position"],
                                "client_id": ctx.client_id,
                            },
                        },
                    )

            elif kind == "tool_call_end":
                if not current_assistant_msg:
                    logger.warning(
                        "tool_call_end received before assistant_message — tool call %s (%s) will not be stored in DB",
                        data.get("id", "?"),
                        data.get("tool_name", "?"),
                    )
                if current_assistant_msg:
                    tool_input = _pending_tool_inputs.pop(data["id"], {})
                    if ctx.tool_registry.has_tool(data["tool_name"]):
                        server_name = "builtin"
                    elif ctx.mcp_manager:
                        server_name = ctx.mcp_manager.get_tool_server_name(data["tool_name"])
                    else:
                        server_name = "unknown"
                    tool_output = data["output"]
                    approval_decision = None
                    if isinstance(tool_output, dict):
                        approval_decision = tool_output.pop("_approval_decision", None)
                    storage.create_tool_call(
                        ctx.db,
                        current_assistant_msg["id"],
                        data["tool_name"],
                        server_name,
                        tool_input,
                        data["id"],
                        approval_decision=approval_decision,
                    )
                    storage.update_tool_call(ctx.db, data["id"], tool_output, data["status"])
                    # Audit log: tool call event
                    _audit_writer = (
                        getattr(
                            getattr(getattr(ctx.request, "app", None), "state", None),
                            "audit_writer",
                            None,
                        )
                        if ctx.request
                        else None
                    )
                    if _audit_writer is not None:
                        from ..services.audit import AuditEntry

                        _audit_writer.emit(
                            AuditEntry.create(
                                "tool_calls.executed",
                                "info",
                                conversation_id=ctx.conversation_id,
                                tool_name=data["tool_name"],
                                details={
                                    "tool_call_id": data["id"],
                                    "status": data["status"],
                                    "approval_decision": approval_decision or "auto",
                                    "server": server_name,
                                    "tool_input": str(tool_input)[:500],
                                    "tool_output": str(tool_output)[:500] if tool_output else "",
                                },
                            )
                        )
                sse_output = data["output"]
                yield {
                    "event": "tool_call_end",
                    "data": json.dumps({"id": data["id"], "output": sse_output, "status": data["status"]}),
                }

                if (
                    ctx.plan_mode
                    and ctx.plan_path
                    and data["tool_name"] == "write_file"
                    and data.get("status") == "success"
                    and ctx.plan_path.exists()
                ):
                    from ..cli.plan import read_plan

                    _plan_content = read_plan(ctx.plan_path)
                    if _plan_content:
                        yield {
                            "event": "plan_saved",
                            "data": json.dumps({"content": _plan_content, "conversation_id": ctx.conversation_id}),
                        }

                if data["tool_name"] == "create_canvas" and data.get("status") == "success":
                    output = data.get("output", {})
                    if output.get("status") == "created":
                        canvas_full = storage.get_canvas(ctx.db, output["id"])
                        if canvas_full:
                            yield {
                                "event": "canvas_created",
                                "data": json.dumps(
                                    {
                                        "id": canvas_full["id"],
                                        "title": canvas_full["title"],
                                        "content": canvas_full["content"],
                                        "language": canvas_full.get("language"),
                                    }
                                ),
                            }
                elif data["tool_name"] == "update_canvas" and data.get("status") == "success":
                    output = data.get("output", {})
                    if output.get("status") == "updated":
                        canvas_full = storage.get_canvas(ctx.db, output["id"])
                        if canvas_full:
                            yield {
                                "event": "canvas_updated",
                                "data": json.dumps(
                                    {
                                        "id": canvas_full["id"],
                                        "title": canvas_full["title"],
                                        "content": canvas_full["content"],
                                        "language": canvas_full.get("language"),
                                    }
                                ),
                            }
                elif data["tool_name"] == "patch_canvas" and data.get("status") == "success":
                    output = data.get("output", {})
                    if output.get("status") == "patched":
                        canvas_full = storage.get_canvas(ctx.db, output["id"])
                        if canvas_full:
                            yield {
                                "event": "canvas_patched",
                                "data": json.dumps(
                                    {
                                        "id": canvas_full["id"],
                                        "title": canvas_full["title"],
                                        "version": canvas_full["version"],
                                        "edits_applied": output.get("edits_applied", 0),
                                        "content": canvas_full["content"],
                                    }
                                ),
                            }

                if data["tool_name"] == "run_agent":
                    for sa_agent_id in list(ctx.subagent_events.keys()):
                        events = ctx.subagent_events[sa_agent_id]
                        if any(e["kind"] == "subagent_end" for e in events):
                            for sa_event in events:
                                yield {
                                    "event": "subagent_event",
                                    "data": json.dumps(sa_event),
                                }
                            del ctx.subagent_events[sa_agent_id]

            elif kind == "tool_batch_end":
                yield {
                    "event": "tool_batch_end",
                    "data": json.dumps(
                        {
                            "call_count": data["call_count"],
                            "elapsed_seconds": data["elapsed_seconds"],
                        }
                    ),
                }

            elif kind == "error":
                yield {"event": "error", "data": json.dumps(data)}

            elif kind == "budget_warning":
                yield {"event": "budget_warning", "data": json.dumps(data)}

            elif kind == "dlp_blocked":
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {
                            "message": "Response blocked by DLP policy",
                            "code": "dlp_blocked",
                            "rules": data.get("matches", []),
                        }
                    ),
                }

            elif kind == "dlp_warning":
                yield {
                    "event": "dlp_warning",
                    "data": json.dumps(
                        {
                            "message": "Sensitive data detected in response",
                            "rules": data.get("matches", []),
                        }
                    ),
                }

            elif kind == "injection_detected":
                action = data.get("action", "warn")
                if action == "block":
                    yield {
                        "event": "error",
                        "data": json.dumps(
                            {
                                "message": "Tool output blocked: prompt injection detected",
                                "code": "injection_blocked",
                                "technique": data.get("technique", ""),
                            }
                        ),
                    }
                else:
                    yield {
                        "event": "injection_warning",
                        "data": json.dumps(
                            {
                                "message": data.get("detail", "Prompt injection detected"),
                                "technique": data.get("technique", ""),
                                "confidence": data.get("confidence", 0),
                            }
                        ),
                    }

            elif kind == "output_filter_blocked":
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {
                            "message": "Response blocked by output content filter",
                            "code": "output_filter_blocked",
                            "rules": data.get("matches", []),
                        }
                    ),
                }

            elif kind == "output_filter_warning":
                yield {
                    "event": "output_filter_warning",
                    "data": json.dumps(
                        {
                            "message": "Forbidden content detected in response",
                            "rules": data.get("matches", []),
                        }
                    ),
                }

            elif kind == "queued_message":
                current_assistant_msg = None
                _streamed_content = ""
                yield {"event": "queued_message", "data": json.dumps(data)}

            elif kind == "done":
                if ctx.is_first_message and ctx.conv_title == "New Conversation":
                    title = await ctx.ai_service.generate_title(ctx.first_user_text)
                    title = (title or "")[:200] or "New Conversation"
                    storage.update_conversation_title(ctx.db, ctx.conversation_id, title)
                    yield {"event": "title", "data": json.dumps({"title": title})}

                    if ctx.event_bus:
                        await ctx.event_bus.publish(
                            f"global:{ctx.db_name}",
                            {
                                "type": "title_changed",
                                "data": {
                                    "conversation_id": ctx.conversation_id,
                                    "title": title,
                                    "client_id": ctx.client_id,
                                },
                            },
                        )

                if ctx.event_bus:
                    await ctx.event_bus.publish(
                        f"conversation:{ctx.conversation_id}",
                        {
                            "type": "stream_done",
                            "data": {"conversation_id": ctx.conversation_id, "client_id": ctx.client_id},
                        },
                    )

                _done_payload: dict[str, Any] = {"plan_mode": ctx.plan_mode}
                if current_assistant_msg:
                    _done_payload["assistant_message_id"] = current_assistant_msg["id"]
                    _done_payload["assistant_message_position"] = current_assistant_msg["position"]
                yield {"event": "done", "data": json.dumps(_done_payload)}

    except Exception:
        logger.exception("Chat stream error")
        yield {"event": "error", "data": json.dumps({"message": "An internal error occurred"})}
    finally:
        if _disconnect_task is not None and not _disconnect_task.done():
            _disconnect_task.cancel()
            try:
                await _disconnect_task
            except asyncio.CancelledError:
                pass
        _active_streams.pop(ctx.conversation_id, None)
        queue = _message_queues.get(ctx.conversation_id)
        if queue and queue.empty():
            _message_queues.pop(ctx.conversation_id, None)
        # Atomic cancel event cleanup: discard + conditional pop in one block
        # to prevent a race where a new stream adds to the set between operations
        ce_set = _cancel_events.get(ctx.conversation_id)
        if ce_set is not None:
            ce_set.discard(ctx.cancel_event)
            # Only remove the set if it's still the same object and is empty
            if not ce_set and _cancel_events.get(ctx.conversation_id) is ce_set:
                _cancel_events.pop(ctx.conversation_id, None)
        # Clean up per-conversation lock if no other stream is active
        if ctx.conversation_id not in _active_streams:
            _stream_locks.pop(ctx.conversation_id, None)


async def _parse_chat_request(request: Request) -> ChatRequestContext:
    """Parse and validate the chat request body (multipart or JSON)."""
    content_type = request.headers.get("content-type", "")
    regenerate = False
    plan_mode = False
    source_ids: list[str] = []
    source_tag: str | None = None
    source_group_id: str | None = None
    if "multipart/form-data" in content_type:
        form = await request.form()
        message_text = str(form.get("message", ""))
        files = form.getlist("files")
        if len(files) > MAX_FILES_PER_REQUEST:
            raise HTTPException(status_code=400, detail=f"Maximum {MAX_FILES_PER_REQUEST} files per request")
        plan_mode = str(form.get("plan_mode", "")).lower() == "true"
        source_ids = [s for s in form.getlist("source_ids") if isinstance(s, str) and s]
        _source_tag_raw = form.get("source_tag")
        source_tag = str(_source_tag_raw) if isinstance(_source_tag_raw, str) and _source_tag_raw else None
        if source_tag and not source_tag.strip():
            source_tag = None
        _source_group_id_raw = form.get("source_group_id")
        source_group_id = (
            str(_source_group_id_raw) if isinstance(_source_group_id_raw, str) and _source_group_id_raw else None
        )
        if source_group_id and not source_group_id.strip():
            source_group_id = None
    else:
        body = ChatRequest(**(await request.json()))
        message_text = body.message
        regenerate = body.regenerate
        plan_mode = body.plan_mode
        source_ids = body.source_ids
        source_tag = body.source_tag
        source_group_id = body.source_group_id
        files = []

    # Validate source reference UUIDs
    for sid in source_ids:
        try:
            uuid_mod.UUID(sid)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid source_id format: {sid[:50]}")
    if source_tag:
        try:
            uuid_mod.UUID(source_tag)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid source_tag format")
    if source_group_id:
        try:
            uuid_mod.UUID(source_group_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid source_group_id format")

    return ChatRequestContext(
        message_text=message_text,
        regenerate=regenerate,
        plan_mode=plan_mode,
        source_ids=source_ids,
        source_tag=source_tag,
        source_group_id=source_group_id,
        files=files,
    )


@router.post("/commands")
async def execute_global_command(request: Request) -> Any:
    _require_json(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    command = body.get("command", "").strip() if isinstance(body, dict) else ""
    plan_mode = bool(body.get("plan_mode", False)) if isinstance(body, dict) else False
    if not command:
        raise HTTPException(status_code=400, detail="Command is required")
    return JSONResponse(
        await _execute_web_command(
            request,
            conversation_id=None,
            raw_command=command,
            plan_mode=plan_mode,
        )
    )


@router.post("/conversations/{conversation_id}/command")
async def execute_conversation_command(conversation_id: str, request: Request) -> Any:
    _validate_uuid(conversation_id)
    _require_json(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    command = body.get("command", "").strip() if isinstance(body, dict) else ""
    plan_mode = bool(body.get("plan_mode", False)) if isinstance(body, dict) else False
    if not command:
        raise HTTPException(status_code=400, detail="Command is required")
    return JSONResponse(
        await _execute_web_command(request, conversation_id=conversation_id, raw_command=command, plan_mode=plan_mode)
    )


@router.post("/conversations/{conversation_id}/chat")
async def chat(conversation_id: str, request: Request) -> Any:
    _validate_uuid(conversation_id)
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # For note/document types, save message without AI response
    conv_type = conv.get("type", "chat")
    if conv_type in ("note", "document"):
        content_type = request.headers.get("content-type", "")
        if "multipart/form-data" in content_type:
            form = await request.form()
            message_text = str(form.get("message", ""))
        else:
            body = ChatRequest(**(await request.json()))
            message_text = body.message

        if not message_text.strip():
            raise HTTPException(status_code=400, detail="Message content cannot be empty")

        uid, uname = _get_identity(request)
        msg = storage.create_message(db, conversation_id, "user", message_text, user_id=uid, user_display_name=uname)

        _embedding_worker = getattr(request.app.state, "embedding_worker", None)
        if _embedding_worker:
            asyncio.create_task(_embedding_worker.embed_message(msg["id"], message_text, conversation_id))

        event_bus = _get_event_bus(request)
        if event_bus:
            asyncio.create_task(
                event_bus.publish(
                    f"conversation:{conversation_id}",
                    {
                        "type": "new_message",
                        "data": {
                            "conversation_id": conversation_id,
                            "message_id": msg["id"],
                            "role": "user",
                            "content": message_text,
                            "position": msg["position"],
                            "client_id": _get_client_id(request),
                        },
                    },
                )
            )

        return JSONResponse({"status": "saved", "message": msg})

    req_ctx = await _parse_chat_request(request)
    message_text = req_ctx.message_text
    regenerate = req_ctx.regenerate
    plan_mode = req_ctx.plan_mode
    source_ids = req_ctx.source_ids
    source_tag = req_ctx.source_tag
    source_group_id = req_ctx.source_group_id
    files = req_ctx.files

    if not regenerate and not message_text.strip():
        raise HTTPException(status_code=400, detail="Message content cannot be empty")

    uid, uname = _get_identity(request)

    # Acquire per-conversation lock to prevent TOCTOU race between
    # stale stream eviction and new stream registration.
    if conversation_id not in _stream_locks:
        _stream_locks[conversation_id] = asyncio.Lock()
    _conv_stream_lock = _stream_locks[conversation_id]

    # Queue message if a stream is already active for this conversation.
    # The lock prevents a TOCTOU race where two concurrent requests both
    # see the slot as empty after stale eviction and create duplicate streams.
    async with _conv_stream_lock:
        if not regenerate and _active_streams.get(conversation_id):
            stream_info = _active_streams[conversation_id]
            stream_age = time_mod.monotonic() - stream_info.get("started_at", 0)

            # Detect stale streams: cancel_event already set (client disconnected
            # via _poll_disconnect) or stream exceeded timeout
            is_stale = stream_info.get("cancel_event", asyncio.Event()).is_set()
            _safety_cfg = getattr(request.app.state.config, "safety", None)
            stale_timeout = (_safety_cfg.approval_timeout if _safety_cfg else 120) + 30
            if not is_stale and stream_age > stale_timeout:
                is_stale = True

            if is_stale:
                logger.info("Cleaning up stale stream for conversation %s (age=%.0fs)", conversation_id, stream_age)
                old_cancel = stream_info.get("cancel_event")
                if old_cancel:
                    old_cancel.set()
                _active_streams.pop(conversation_id, None)
                # Fall through to create a new stream
            else:
                queue = _message_queues.get(conversation_id)
                if queue and queue.qsize() >= MAX_QUEUED_MESSAGES:
                    raise HTTPException(status_code=429, detail="Message queue full (max 10)")
                storage.create_message(db, conversation_id, "user", message_text, user_id=uid, user_display_name=uname)
                if queue is None:
                    queue = asyncio.Queue()
                    _message_queues[conversation_id] = queue
                await queue.put({"role": "user", "content": message_text})
                return JSONResponse({"status": "queued", "position": queue.qsize()})

    if regenerate:
        existing = storage.list_messages(db, conversation_id)
        if not existing:
            raise HTTPException(status_code=400, detail="No messages to regenerate from")

    user_msg = None
    attachment_contents: list[dict[str, Any]] = []

    event_bus = _get_event_bus(request)
    db_name = _get_db_name(request)
    client_id = _get_client_id(request)

    if not regenerate:
        user_msg = storage.create_message(
            db, conversation_id, "user", message_text, user_id=uid, user_display_name=uname
        )

        # Trigger async embedding for user message
        _embedding_worker = getattr(request.app.state, "embedding_worker", None)
        if _embedding_worker and user_msg:
            asyncio.create_task(_embedding_worker.embed_message(user_msg["id"], message_text, conversation_id))

        if event_bus and user_msg:
            asyncio.create_task(
                event_bus.publish(
                    f"conversation:{conversation_id}",
                    {
                        "type": "new_message",
                        "data": {
                            "conversation_id": conversation_id,
                            "message_id": user_msg["id"],
                            "role": "user",
                            "content": message_text,
                            "position": user_msg["position"],
                            "client_id": client_id,
                        },
                    },
                )
            )

        if files:
            data_dir = request.app.state.config.app.data_dir
            for f in files:
                if hasattr(f, "read"):
                    file_data = await f.read()
                    att = storage.save_attachment(
                        db,
                        user_msg["id"],
                        conversation_id,
                        f.filename or "unnamed",
                        f.content_type or "application/octet-stream",
                        file_data,
                        data_dir,
                    )
                    # Dual citizenship: also create a source from this attachment
                    try:
                        identity = getattr(request.app.state.config, "identity", None)
                        _uid = identity.user_id if identity else None
                        _udn = identity.display_name if identity else None
                        source = storage.create_source_from_attachment(
                            db,
                            att["id"],
                            data_dir,
                            user_id=_uid,
                            user_display_name=_udn,
                        )
                        if source:
                            worker = getattr(request.app.state, "embedding_worker", None)
                            if worker and source.get("content"):
                                try:
                                    await worker.embed_source(source["id"])
                                except Exception:
                                    pass
                    except Exception:
                        logger.debug("Failed to create source from attachment %s", att["id"], exc_info=True)
                    if f.content_type and f.content_type.startswith("image/"):
                        b64_data = base64.b64encode(file_data).decode("ascii")
                        attachment_contents.append(
                            {
                                "type": "image_url",
                                "image_url": f"data:{f.content_type};base64,{b64_data}",
                            }
                        )
                    elif f.content_type and f.content_type.startswith("text"):
                        try:
                            attachment_contents.append(
                                {
                                    "type": "text",
                                    "filename": f.filename,
                                    "content": file_data.decode("utf-8", errors="replace"),
                                }
                            )
                        except Exception:
                            pass
                    else:
                        try:
                            from ..services.document_extractor import EXTRACTABLE_MIME_TYPES, extract_text

                            validated_mime = att.get("mime_type") or f.content_type
                            extracted = None
                            if validated_mime and validated_mime in EXTRACTABLE_MIME_TYPES:
                                extracted = extract_text(file_data, validated_mime).text
                            if extracted:
                                max_chars = 50_000
                                if len(extracted) > max_chars:
                                    extracted = extracted[:max_chars] + "\n\n[... truncated]"
                                attachment_contents.append(
                                    {
                                        "type": "text",
                                        "filename": f.filename,
                                        "content": extracted,
                                    }
                                )
                            else:
                                attachment_contents.append(
                                    {
                                        "type": "text",
                                        "filename": f.filename,
                                        "content": (
                                            f"[Attached file: {f.filename} ({validated_mime})"
                                            f" — content could not be extracted automatically."
                                            f" Use the appropriate tool to read this file.]"
                                        ),
                                    }
                                )
                        except Exception:
                            logger.debug("Document extraction failed for %s", f.filename, exc_info=True)

    cancel_event = asyncio.Event()
    async with _conv_stream_lock:
        _cancel_events[conversation_id].add(cancel_event)
        _active_streams[conversation_id] = {
            "started_at": time_mod.monotonic(),
            "cancel_event": cancel_event,
        }
        if conversation_id not in _message_queues:
            _message_queues[conversation_id] = asyncio.Queue()

    # Resolve model override: conversation model > space model > global default
    model_override = conv.get("model") or None
    space_instructions: str | None = None

    # Resolve space context
    space_id = conv.get("space_id")
    if space_id:
        from ..services.space_storage import get_space as _get_space_by_id

        _space = _get_space_by_id(db, space_id)
        if _space:
            if _space.get("instructions"):
                space_instructions = _space["instructions"]
            # Override model from space if set and no conversation-level override
            if not model_override and _space.get("model"):
                model_override = _space["model"]
            # Auto-inject space sources
            space_sources = storage.get_space_sources(db, space_id)
            space_source_ids = {s["id"] for s in space_sources}
            existing_ids = set(source_ids)
            for sid in space_source_ids:
                if sid not in existing_ids:
                    source_ids.append(sid)

    ai_service = _get_ai_service(request, model_override=model_override)

    # Build message history
    history = storage.list_messages(db, conversation_id)
    ai_messages: list[dict[str, Any]] = []
    for msg in history:
        content: Any = msg["content"]
        if user_msg and msg["id"] == user_msg["id"] and attachment_contents:
            parts: list[dict[str, Any]] = [{"type": "text", "text": msg["content"]}]
            for att in attachment_contents:
                if att["type"] == "image_url":
                    parts.append({"type": "image_url", "image_url": {"url": att["image_url"]}})
                elif att["type"] == "text":
                    parts.append({"type": "text", "text": f"[Attached file: {att['filename']}]\n{att['content']}"})
            content = parts
        ai_messages.append({"role": msg["role"], "content": content})

    # Build unified tool list: builtins + MCP
    tool_registry = request.app.state.tool_registry
    mcp_manager = request.app.state.mcp_manager
    working_dir = conv.get("working_dir") or os.getcwd()

    # Build per-request registries scoped to the active space
    req_art_reg, req_skill_reg, req_rule_enf = _get_request_registries(request, db, space_id, working_dir)

    tools_openai, plan_path, plan_prompt = _build_tool_list(
        tool_registry=tool_registry,
        mcp_manager=mcp_manager,
        plan_mode=plan_mode,
        conversation_id=conversation_id,
        data_dir=request.app.state.config.app.data_dir,
        max_tools=request.app.state.config.ai.max_tools,
        read_only=request.app.state.config.safety.read_only,
        tier_overrides=request.app.state.config.safety.tool_tiers,
        skill_registry=req_skill_reg,
    )

    tools = tools_openai if tools_openai else None

    is_first_message = not regenerate and len(history) <= 1
    first_user_text = message_text

    _att_filenames = [a["filename"] for a in attachment_contents if a.get("filename")] if attachment_contents else []

    extra_system_prompt, prompt_meta = await _build_chat_system_prompt(
        ai_service=ai_service,
        tool_registry=tool_registry,
        mcp_manager=mcp_manager,
        config=request.app.state.config,
        db=db,
        conversation_id=conversation_id,
        space_instructions=space_instructions,
        plan_prompt=plan_prompt,
        plan_mode=plan_mode,
        message_text=message_text,
        source_ids=source_ids,
        source_tag=source_tag,
        source_group_id=source_group_id,
        vec_enabled=getattr(request.app.state, "vec_enabled", False),
        embedding_service=getattr(request.app.state, "embedding_service", None),
        reranker_service=getattr(request.app.state, "reranker_service", None),
        injection_detector=getattr(request.app.state, "injection_detector", None),
        artifact_registry=req_art_reg,
        skill_registry=req_skill_reg,
        space_id=space_id,
        attachment_filenames=_att_filenames,
        vec_manager=getattr(request.app.state, "vec_manager", None),
    )

    # Build per-request safety approval context
    pending_approvals = getattr(request.app.state, "pending_approvals", {})
    safety_config = getattr(request.app.state.config, "safety", None)
    approval_timeout = safety_config.approval_timeout if safety_config else 120

    confirm_ctx = WebConfirmContext(
        pending_approvals=pending_approvals,
        event_bus=event_bus,
        db_name=db_name,
        conversation_id=conversation_id,
        approval_timeout=approval_timeout,
        request=request,
        tool_registry=tool_registry,
    )

    async def _web_confirm(verdict: Any) -> bool:
        return await _web_confirm_tool(confirm_ctx, verdict)

    async def _web_ask_user(question: str, options: list[str] | None = None) -> str:
        return await _web_ask_user_callback(confirm_ctx, question, options)

    from ..tools.subagent import SubagentLimiter

    _sa_config = getattr(request.app.state.config.safety, "subagent", None)
    # SECURITY-REVIEW: Limiter is per-request, not global. A single user with
    # multiple browser tabs can spawn up to max_total * concurrent_requests
    # sub-agents. Acceptable for a single-user local app; revisit if
    # multi-user support is added.
    _subagent_limiter = SubagentLimiter(
        max_concurrent=_sa_config.max_concurrent if _sa_config else 5,
        max_total=_sa_config.max_total if _sa_config else 10,
    )
    from ..services.tool_rate_limit import ToolRateLimiter

    _rate_limiter = ToolRateLimiter(safety_config.tool_rate_limit if safety_config else None)
    tool_registry.set_rate_limiter(_rate_limiter)
    _subagent_events: dict[str, list[dict[str, Any]]] = {}

    tool_exec_ctx = ToolExecutorContext(
        tool_registry=tool_registry,
        mcp_manager=mcp_manager,
        confirm_ctx=confirm_ctx,
        ai_service=ai_service,
        cancel_event=cancel_event,
        db=db,
        uid=uid,
        uname=uname,
        conversation_id=conversation_id,
        tools_openai=tools_openai,
        subagent_events=_subagent_events,
        subagent_limiter=_subagent_limiter,
        sa_config=_sa_config,
        request_config=request.app.state.config,
        rate_limiter=_rate_limiter,
        skill_registry=req_skill_reg,
        rule_enforcer=req_rule_enf,
    )

    async def _tool_executor(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await _execute_web_tool(tool_exec_ctx, tool_name, arguments)

    stream_ctx = StreamContext(
        ai_service=ai_service,
        ai_messages=ai_messages,
        tool_executor=_tool_executor,
        tools=tools,
        cancel_event=cancel_event,
        extra_system_prompt=extra_system_prompt,
        conversation_id=conversation_id,
        plan_mode=plan_mode,
        plan_path=plan_path,
        db=db,
        db_name=db_name,
        uid=uid,
        uname=uname,
        event_bus=event_bus,
        client_id=client_id,
        tool_registry=tool_registry,
        mcp_manager=mcp_manager,
        subagent_events=_subagent_events,
        is_first_message=is_first_message,
        first_user_text=first_user_text,
        conv_title=conv["title"],
        embedding_worker=getattr(request.app.state, "embedding_worker", None),
        planning_config=request.app.state.config.cli.planning,
        budget_config=getattr(request.app.state.config.cli.usage, "budgets", None),
        canvas_needs_approval=_canvas_needs_approval(safety_config, tool_registry),
        request=request,
        prompt_meta=prompt_meta,
        user_msg=user_msg,
    )

    return EventSourceResponse(_stream_chat_events(stream_ctx))


@router.post("/conversations/{conversation_id}/stop")
async def stop_generation(conversation_id: str, request: Request) -> Any:
    _validate_uuid(conversation_id)
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    events = _cancel_events.get(conversation_id, set())
    for event in events:
        event.set()
    return {"status": "stopped"}


@router.get("/conversations/{conversation_id}/stream-status")
async def stream_status(conversation_id: str, request: Request) -> Any:
    _validate_uuid(conversation_id)
    stream_info = _active_streams.get(conversation_id)
    if not stream_info:
        return {"active": False}
    age = time_mod.monotonic() - stream_info.get("started_at", 0)
    # Evict stale streams: if the originating request disconnected or the
    # stream has been active for too long, clean it up and report inactive.
    stale_request = stream_info.get("request")
    max_age = 180  # 3 minutes hard cap
    if age > max_age:
        logger.info("Evicting stale stream for %s (age=%.0fs)", conversation_id, age)
        cancel_ev = stream_info.get("cancel_event")
        if cancel_ev:
            cancel_ev.set()
        _active_streams.pop(conversation_id, None)
        return {"active": False}
    # Check if the originating SSE request is still connected.  If it
    # disconnected (e.g. page refresh), the SSE generator is orphaned and
    # _poll_disconnect will eventually clean it up — this is an eager check.
    if stale_request:
        try:
            disconnected = await stale_request.is_disconnected()
        except Exception:
            disconnected = True
        if disconnected:
            logger.info("Evicting disconnected stream for %s (age=%.0fs)", conversation_id, age)
            cancel_ev = stream_info.get("cancel_event")
            if cancel_ev:
                cancel_ev.set()
            _active_streams.pop(conversation_id, None)
            return {"active": False}
    return {"active": True, "age_seconds": round(age)}


@router.get("/attachments/{attachment_id}")
async def get_attachment(attachment_id: str, request: Request) -> Any:
    _validate_uuid(attachment_id)
    db = _get_db(request)
    att = storage.get_attachment(db, attachment_id)
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    data_dir = request.app.state.config.app.data_dir
    file_path = safe_resolve_pathlib(data_dir / att["storage_path"])
    if not file_path.is_relative_to(safe_resolve_pathlib(data_dir)):
        raise HTTPException(status_code=403, detail="Access denied")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Attachment file missing")
    from fastapi.responses import FileResponse

    media_type = att["mime_type"]
    disposition = "inline" if media_type in SAFE_INLINE_TYPES else "attachment"
    # SECURITY-REVIEW: file_path checked with is_relative_to(data_dir) above; filename sanitized on upload
    return FileResponse(
        str(file_path),
        media_type=media_type,
        filename=att["filename"],
        headers={"Content-Disposition": f'{disposition}; filename="{att["filename"]}"'},
    )

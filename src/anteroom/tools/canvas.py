"""Canvas tools for AI to create and update canvas content."""

from __future__ import annotations

from typing import Any

CANVAS_CREATE_DEFINITION: dict[str, Any] = {
    "name": "create_canvas",
    "description": (
        "Create a canvas panel with rich content alongside the chat. "
        "Use this when the user asks you to write code, documents, articles, or any structured content "
        "that benefits from a dedicated editing panel. The canvas appears next to the chat "
        "for the user to view and edit."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Title for the canvas (e.g. 'fibonacci.py', 'Project README', 'SQL Schema')",
            },
            "content": {
                "type": "string",
                "description": "The full content to display in the canvas",
            },
            "language": {
                "type": "string",
                "description": "Programming language for syntax highlighting (e.g. 'python', 'javascript', 'sql'). "
                "Omit for plain text or markdown.",
            },
        },
        "required": ["title", "content"],
    },
}

MAX_PATCH_EDITS = 50
MAX_CANVAS_CONTENT = 100_000

CANVAS_PATCH_DEFINITION: dict[str, Any] = {
    "name": "patch_canvas",
    "description": (
        "Apply incremental search/replace edits to the existing canvas content. "
        "Use this instead of update_canvas when making small, targeted changes — "
        "it is more token-efficient. Each edit's search string must match exactly once in the current content. "
        "Edits are applied sequentially (each operates on the result of the previous)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "description": "Array of search/replace pairs to apply sequentially",
                "items": {
                    "type": "object",
                    "properties": {
                        "search": {
                            "type": "string",
                            "description": "Exact string to find in the current canvas content",
                        },
                        "replace": {
                            "type": "string",
                            "description": "Replacement string",
                        },
                    },
                    "required": ["search", "replace"],
                },
            },
        },
        "required": ["edits"],
    },
}

CANVAS_UPDATE_DEFINITION: dict[str, Any] = {
    "name": "update_canvas",
    "description": (
        "Update the content of the existing canvas panel. "
        "Use this when the user asks you to modify, improve, or change the canvas content. "
        "Provide the complete updated content (not a diff)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The complete updated content for the canvas",
            },
            "title": {
                "type": "string",
                "description": "Optional new title for the canvas",
            },
        },
        "required": ["content"],
    },
}


async def handle_create_canvas(
    title: str,
    content: str,
    language: str | None = None,
    _conversation_id: str | None = None,
    _db: Any = None,
    _user_id: str | None = None,
    _user_display_name: str | None = None,
) -> dict[str, Any]:
    if not _conversation_id or not _db:
        return {"error": "Canvas tools require conversation context"}

    from ..services import storage

    conv = storage.get_conversation(_db, _conversation_id)
    if not conv:
        return {"error": "Conversation not found"}

    if len(content) > MAX_CANVAS_CONTENT:
        return {"error": f"Content too large ({len(content)} chars). Maximum is {MAX_CANVAS_CONTENT}."}

    existing = storage.get_canvas_for_conversation(_db, _conversation_id)
    if existing:
        return {"error": "A canvas already exists for this conversation. Use update_canvas instead."}

    canvas = storage.create_canvas(
        _db,
        _conversation_id,
        title=title,
        content=content,
        language=language,
        user_id=_user_id,
        user_display_name=_user_display_name,
    )
    return {
        "status": "created",
        "id": canvas["id"],
        "title": canvas["title"],
        "language": canvas.get("language"),
    }


async def handle_update_canvas(
    content: str,
    title: str | None = None,
    _conversation_id: str | None = None,
    _db: Any = None,
    _user_id: str | None = None,
    _user_display_name: str | None = None,
) -> dict[str, Any]:
    if not _conversation_id or not _db:
        return {"error": "Canvas tools require conversation context"}

    from ..services import storage

    conv = storage.get_conversation(_db, _conversation_id)
    if not conv:
        return {"error": "Conversation not found"}

    if len(content) > MAX_CANVAS_CONTENT:
        return {"error": f"Content too large ({len(content)} chars). Maximum is {MAX_CANVAS_CONTENT}."}

    canvas = storage.get_canvas_for_conversation(_db, _conversation_id)
    if not canvas:
        return {"error": "No canvas exists for this conversation. Use create_canvas first."}

    updated = storage.update_canvas(_db, canvas["id"], content=content, title=title)
    if not updated:
        return {"error": "Failed to update canvas"}

    return {
        "status": "updated",
        "id": updated["id"],
        "title": updated["title"],
        "version": updated["version"],
    }


async def handle_patch_canvas(
    edits: list[dict[str, str]],
    _conversation_id: str | None = None,
    _db: Any = None,
    _user_id: str | None = None,
    _user_display_name: str | None = None,
) -> dict[str, Any]:
    if not _conversation_id or not _db:
        return {"error": "Canvas tools require conversation context"}

    from ..services import storage

    conv = storage.get_conversation(_db, _conversation_id)
    if not conv:
        return {"error": "Conversation not found"}

    if not edits:
        return {"error": "No edits provided"}

    if len(edits) > MAX_PATCH_EDITS:
        return {"error": f"Too many edits ({len(edits)}). Maximum is {MAX_PATCH_EDITS}."}

    canvas = storage.get_canvas_for_conversation(_db, _conversation_id)
    if not canvas:
        return {"error": "No canvas exists for this conversation. Use create_canvas first."}

    content = canvas.get("content") or ""
    applied_patches: list[dict[str, Any]] = []

    for i, edit in enumerate(edits):
        search = edit.get("search", "")
        replace = edit.get("replace", "")

        if not search:
            return {
                "error": "Empty search string",
                "edit_index": i,
                "failed_edit": edit,
            }

        count = content.count(search)
        if count == 0:
            return {
                "error": "Search string not found in canvas content",
                "edit_index": i,
                "failed_edit": edit,
            }
        if count > 1:
            return {
                "error": f"Search string is ambiguous ({count} matches). Provide more context to match exactly once.",
                "edit_index": i,
                "failed_edit": edit,
            }

        offset = content.index(search)
        content = content[:offset] + replace + content[offset + len(search) :]

        if len(content) > MAX_CANVAS_CONTENT:
            return {
                "error": f"Content exceeded size limit after edit {i + 1} "
                f"({len(content)} chars). Maximum is {MAX_CANVAS_CONTENT}."
            }

        applied_patches.append(
            {
                "search": search,
                "replace": replace,
                "offset": offset,
                "length": len(search),
            }
        )

    if len(content) > MAX_CANVAS_CONTENT:
        return {"error": f"Patched content too large ({len(content)} chars). Maximum is {MAX_CANVAS_CONTENT}."}

    updated = storage.update_canvas(_db, canvas["id"], content=content)
    if not updated:
        return {"error": "Failed to update canvas"}

    return {
        "status": "patched",
        "id": updated["id"],
        "title": updated["title"],
        "version": updated["version"],
        "edits_applied": len(applied_patches),
    }

"""Edit file via exact string replacement."""

from __future__ import annotations

import os
from typing import Any

from .security import validate_path

_working_dir: str = os.getcwd()

DEFINITION: dict[str, Any] = {
    "name": "edit_file",
    "description": (
        "Edit a file by replacing an exact string with new text. "
        "The old_text must appear exactly once in the file (must be unique). "
        "Use replace_all=true to replace all occurrences."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path (relative to working directory or absolute)"},
            "old_text": {"type": "string", "description": "The exact text to find and replace"},
            "new_text": {"type": "string", "description": "The replacement text"},
            "replace_all": {
                "type": "boolean",
                "description": "If true, replace all occurrences. Default false (must be unique).",
                "default": False,
            },
        },
        "required": ["path", "old_text", "new_text"],
    },
}


def set_working_dir(d: str) -> None:
    global _working_dir
    _working_dir = d


async def handle(path: str, old_text: str, new_text: str, replace_all: bool = False, **_: Any) -> dict[str, Any]:
    resolved, error = validate_path(path, _working_dir)
    if error:
        return {"error": error}
    if not os.path.isfile(resolved):
        return {"error": f"File not found: {path}"}
    try:
        with open(resolved, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return {"error": str(e)}

    count = content.count(old_text)
    if count == 0:
        return {"error": "old_text not found in file"}
    if count > 1 and not replace_all:
        return {
            "error": f"old_text matches {count} times. Use replace_all=true or provide more context to make it unique."
        }

    if replace_all:
        new_content = content.replace(old_text, new_text)
    else:
        new_content = content.replace(old_text, new_text, 1)

    try:
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(new_content)
    except OSError as e:
        return {"error": str(e)}

    return {"status": "ok", "replacements": count if replace_all else 1}

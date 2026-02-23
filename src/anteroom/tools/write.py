"""Write/create file tool."""

from __future__ import annotations

import os
from typing import Any

from .security import validate_path

_working_dir: str = os.getcwd()

DEFINITION: dict[str, Any] = {
    "name": "write_file",
    "description": "Write content to a file. Creates parent directories if needed. Overwrites existing files.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path (relative to working directory or absolute)"},
            "content": {"type": "string", "description": "The content to write to the file"},
        },
        "required": ["path", "content"],
    },
}


def set_working_dir(d: str) -> None:
    global _working_dir
    _working_dir = d


async def handle(path: str, content: str, **_: Any) -> dict[str, Any]:
    resolved, error = validate_path(path, _working_dir)
    if error:
        return {"error": error}
    try:
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        # Capture pre-write state for diff rendering
        old_content: str | None = None
        already_exists = os.path.isfile(resolved)
        if already_exists:
            try:
                with open(resolved, encoding="utf-8") as f:
                    old_content = f.read()
            except (OSError, UnicodeDecodeError):
                old_content = None
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
        action = "updated" if already_exists else "created"
        lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        result: dict[str, Any] = {
            "status": "ok",
            "path": resolved,
            "bytes_written": len(content.encode("utf-8")),
            "action": action,
            "lines": lines,
        }
        # Include content for diff rendering (stripped before sending to LLM)
        if old_content is not None:
            result["_old_content"] = old_content
        result["_new_content"] = content
        return result
    except OSError as e:
        return {"error": str(e)}

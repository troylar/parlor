"""File pattern matching tool using glob."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .security import validate_path

_MAX_RESULTS = 500

_working_dir: str = os.getcwd()

DEFINITION: dict[str, Any] = {
    "name": "glob_files",
    "description": (
        "Find files matching a glob pattern. Returns matching file paths sorted by modification time (newest first)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": 'Glob pattern (e.g. "**/*.py", "src/**/*.ts")'},
            "path": {
                "type": "string",
                "description": "Directory to search in. Defaults to working directory.",
            },
        },
        "required": ["pattern"],
    },
}


def set_working_dir(d: str) -> None:
    global _working_dir
    _working_dir = d


async def handle(pattern: str, path: str | None = None, **_: Any) -> dict[str, Any]:
    if "\x00" in pattern:
        return {"error": "Pattern contains null bytes"}

    base_path = path or _working_dir
    resolved, error = validate_path(base_path, _working_dir)
    if error:
        return {"error": error}

    base = Path(resolved)
    if not base.is_dir():
        return {"error": f"Directory not found: {base_path}"}

    try:
        matches = sorted(base.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError as e:
        return {"error": str(e)}

    results = [str(m.relative_to(base)) for m in matches[:_MAX_RESULTS] if m.is_file()]
    truncated = len(matches) > _MAX_RESULTS

    return {"files": results, "count": len(results), "truncated": truncated}

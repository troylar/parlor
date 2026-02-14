"""Regex file search tool."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .security import validate_path

_MAX_OUTPUT = 100_000
_MAX_FILE_SIZE = 5_000_000  # 5MB
_MAX_MATCHES = 200

_working_dir: str = os.getcwd()

DEFINITION: dict[str, Any] = {
    "name": "grep",
    "description": (
        "Search file contents using a regex pattern. "
        "Returns matching lines with file paths, line numbers, and optional context."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {
                "type": "string",
                "description": "File or directory to search in. Defaults to working directory.",
            },
            "glob": {
                "type": "string",
                "description": 'Glob to filter files (e.g. "*.py", "**/*.ts"). Default: all files.',
            },
            "context": {
                "type": "integer",
                "description": "Number of context lines before and after each match. Default 0.",
                "default": 0,
            },
            "case_insensitive": {
                "type": "boolean",
                "description": "Case insensitive search. Default false.",
                "default": False,
            },
        },
        "required": ["pattern"],
    },
}


def set_working_dir(d: str) -> None:
    global _working_dir
    _working_dir = d


def _search_file(
    file_path: Path,
    regex: re.Pattern[str],
    context: int,
) -> list[dict[str, Any]]:
    try:
        if file_path.stat().st_size > _MAX_FILE_SIZE:
            return []
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    lines = text.splitlines()
    matches = []
    for i, line in enumerate(lines):
        if regex.search(line):
            start = max(0, i - context)
            end = min(len(lines), i + context + 1)
            context_lines = []
            for j in range(start, end):
                prefix = ">" if j == i else " "
                context_lines.append(f"{prefix}{j + 1:>6}\t{lines[j]}")
            matches.append({
                "line_number": i + 1,
                "content": "\n".join(context_lines),
            })
    return matches


async def handle(
    pattern: str,
    path: str | None = None,
    glob: str | None = None,
    context: int = 0,
    case_insensitive: bool = False,
    **_: Any,
) -> dict[str, Any]:
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return {"error": f"Invalid regex: {e}"}

    base_path = path or _working_dir
    resolved, error = validate_path(base_path, _working_dir)
    if error:
        return {"error": error}

    base = Path(resolved)

    if base.is_file():
        results = _search_file(base, regex, context)
        return {
            "matches": [{"file": str(base), **m} for m in results[:_MAX_MATCHES]],
            "total_matches": len(results),
        }

    if not base.is_dir():
        return {"error": f"Path not found: {base_path}"}

    if glob and "\x00" in glob:
        return {"error": "Glob pattern contains null bytes"}

    file_pattern = glob or "**/*"
    all_matches: list[dict[str, Any]] = []
    try:
        for file_path in sorted(base.glob(file_pattern)):
            if not file_path.is_file():
                continue
            file_matches = _search_file(file_path, regex, context)
            for m in file_matches:
                all_matches.append({"file": str(file_path.relative_to(base)), **m})
                if len(all_matches) >= _MAX_MATCHES:
                    break
            if len(all_matches) >= _MAX_MATCHES:
                break
    except OSError as e:
        return {"error": str(e)}

    output = []
    for m in all_matches:
        output.append(f"{m['file']}:{m['line_number']}")
        output.append(m["content"])
        output.append("")

    content = "\n".join(output)
    if len(content) > _MAX_OUTPUT:
        content = content[:_MAX_OUTPUT] + "\n... (truncated)"

    return {"content": content, "total_matches": len(all_matches), "truncated": len(all_matches) >= _MAX_MATCHES}

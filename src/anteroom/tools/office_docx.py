"""DOCX (Word document) create/read/edit tool.

Requires python-docx: ``pip install anteroom[office]``
"""

from __future__ import annotations

import json
import os
from typing import Any

from .security import validate_path

try:
    import docx

    AVAILABLE = True
except ImportError:
    AVAILABLE = False

_MAX_OUTPUT = 100_000
_MAX_CONTENT_BLOCKS = 200

_working_dir: str = os.getcwd()

DEFINITION: dict[str, Any] = {
    "name": "docx",
    "description": (
        "Create, read, or edit Word documents (.docx). "
        "Action 'create' builds a new document from content blocks. "
        "Action 'read' extracts text, headings, and tables. "
        "Action 'edit' performs find/replace and can append new content."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "read", "edit"],
                "description": "Operation to perform.",
            },
            "path": {
                "type": "string",
                "description": "File path (relative to working directory or absolute).",
            },
            "content_blocks": {
                "type": "array",
                "description": (
                    "List of content blocks for create/edit append. "
                    "Each block: {type: 'heading'|'paragraph'|'table', text?: str, level?: int, rows?: [[str]]}."
                ),
                "items": {"type": "object"},
            },
            "replacements": {
                "type": "array",
                "description": "List of {old: str, new: str} for find/replace in edit action.",
                "items": {"type": "object"},
            },
        },
        "required": ["action", "path"],
    },
}


def set_working_dir(d: str) -> None:
    global _working_dir
    _working_dir = d


async def handle(action: str, path: str, **kwargs: Any) -> dict[str, Any]:
    if not AVAILABLE:
        return {"error": "python-docx is not installed. Install with: pip install anteroom[office]"}

    resolved, error = validate_path(path, _working_dir)
    if error:
        return {"error": error}

    if action == "create":
        return _create(resolved, path, **kwargs)
    elif action == "read":
        return _read(resolved, path, **kwargs)
    elif action == "edit":
        return _edit(resolved, path, **kwargs)
    else:
        return {"error": f"Unknown action: {action}. Use 'create', 'read', or 'edit'."}


def _create(resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    content_blocks: list[dict[str, Any]] = kwargs.get("content_blocks") or []
    if not content_blocks:
        return {"error": "content_blocks is required for create action"}
    if len(content_blocks) > _MAX_CONTENT_BLOCKS:
        return {"error": f"Too many content blocks (max {_MAX_CONTENT_BLOCKS})"}

    document = docx.Document()

    for block in content_blocks:
        block_type = block.get("type", "paragraph")
        if block_type == "heading":
            level = block.get("level", 1)
            level = max(0, min(9, level))
            document.add_heading(block.get("text", ""), level=level)
        elif block_type == "paragraph":
            document.add_paragraph(block.get("text", ""))
        elif block_type == "table":
            rows = block.get("rows", [])
            if not rows:
                continue
            cols = max(len(r) for r in rows) if rows else 1
            table = document.add_table(rows=len(rows), cols=cols)
            for i, row_data in enumerate(rows):
                for j, cell_text in enumerate(row_data):
                    if j < cols:
                        table.rows[i].cells[j].text = str(cell_text)
        else:
            document.add_paragraph(block.get("text", ""))

    os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
    document.save(resolved)
    return {"result": f"Created {display_path}", "path": display_path, "blocks_written": len(content_blocks)}


def _read(resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    if not os.path.isfile(resolved):
        return {"error": f"File not found: {display_path}"}

    try:
        document = docx.Document(resolved)
    except Exception:
        return {"error": f"Unable to read DOCX file: {display_path}"}

    output_parts: list[str] = []

    for para in document.paragraphs:
        style_name = para.style.name if para.style else ""
        if style_name.startswith("Heading"):
            try:
                level = int(style_name.split()[-1])
            except (ValueError, IndexError):
                level = 1
            output_parts.append(f"{'#' * level} {para.text}")
        else:
            if para.text.strip():
                output_parts.append(para.text)

    for i, table in enumerate(document.tables):
        output_parts.append(f"\n[Table {i + 1}]")
        table_rows: list[list[str]] = []
        for row in table.rows:
            table_rows.append([cell.text for cell in row.cells])
        output_parts.append(json.dumps(table_rows, ensure_ascii=False))

    content = "\n".join(output_parts)
    if len(content) > _MAX_OUTPUT:
        content = content[:_MAX_OUTPUT] + "\n... (truncated)"

    return {
        "content": content,
        "paragraphs": len(document.paragraphs),
        "tables": len(document.tables),
    }


def _edit(resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    if not os.path.isfile(resolved):
        return {"error": f"File not found: {display_path}"}

    try:
        document = docx.Document(resolved)
    except Exception:
        return {"error": f"Unable to read DOCX file: {display_path}"}

    replacements: list[dict[str, str]] = kwargs.get("replacements") or []
    content_blocks: list[dict[str, Any]] = kwargs.get("content_blocks") or []

    if not replacements and not content_blocks:
        return {"error": "Provide 'replacements' and/or 'content_blocks' for edit action"}

    if content_blocks and len(content_blocks) > _MAX_CONTENT_BLOCKS:
        return {"error": f"Too many content blocks (max {_MAX_CONTENT_BLOCKS})"}

    replacements_made = 0
    for rep in replacements:
        old = rep.get("old", "")
        new = rep.get("new", "")
        if not old:
            continue
        for para in document.paragraphs:
            if old in para.text:
                for run in para.runs:
                    if old in run.text:
                        run.text = run.text.replace(old, new)
                        replacements_made += 1

    for block in content_blocks:
        block_type = block.get("type", "paragraph")
        if block_type == "heading":
            level = block.get("level", 1)
            level = max(0, min(9, level))
            document.add_heading(block.get("text", ""), level=level)
        elif block_type == "paragraph":
            document.add_paragraph(block.get("text", ""))
        elif block_type == "table":
            rows = block.get("rows", [])
            if not rows:
                continue
            cols = max(len(r) for r in rows) if rows else 1
            table = document.add_table(rows=len(rows), cols=cols)
            for i, row_data in enumerate(rows):
                for j, cell_text in enumerate(row_data):
                    if j < cols:
                        table.rows[i].cells[j].text = str(cell_text)

    document.save(resolved)
    return {
        "result": f"Edited {display_path}",
        "path": display_path,
        "replacements_made": replacements_made,
        "blocks_appended": len(content_blocks),
    }

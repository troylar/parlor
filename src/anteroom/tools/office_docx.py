"""DOCX (Word document) create/read/edit tool.

Backends:
- COM (Windows + Office + pywin32): full Office object model
- Library (python-docx): cross-platform XML manipulation

Install: ``pip install anteroom[office]`` or ``pip install anteroom[office-com]``
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from .security import validate_path

_BACKEND: str | None = None
_com_mod: Any = None

if sys.platform == "win32":
    try:
        from . import office_com as _com_mod_import

        if _com_mod_import.COM_AVAILABLE:
            _com_mod = _com_mod_import
            _BACKEND = "com"
    except ImportError:
        pass

if _BACKEND is None:
    try:
        import docx  # noqa: F401

        _BACKEND = "lib"
    except ImportError:
        pass

AVAILABLE = _BACKEND is not None

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
        return {"error": "No docx backend available. Install with: pip install anteroom[office]"}

    resolved, error = validate_path(path, _working_dir)
    if error:
        return {"error": error}

    if _BACKEND == "com":
        return await _dispatch_com(action, resolved, path, **kwargs)

    if action == "create":
        return _create_lib(resolved, path, **kwargs)
    elif action == "read":
        return _read_lib(resolved, path, **kwargs)
    elif action == "edit":
        return _edit_lib(resolved, path, **kwargs)
    else:
        return {"error": f"Unknown action: {action}. Use 'create', 'read', or 'edit'."}


# ---------------------------------------------------------------------------
# COM backend
# ---------------------------------------------------------------------------


async def _dispatch_com(action: str, resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    manager = _com_mod.get_manager()
    if action == "create":
        return await manager.run_com(_create_com, manager, resolved, display_path, **kwargs)
    elif action == "read":
        return await manager.run_com(_read_com, manager, resolved, display_path, **kwargs)
    elif action == "edit":
        return await manager.run_com(_edit_com, manager, resolved, display_path, **kwargs)
    else:
        return {"error": f"Unknown action: {action}. Use 'create', 'read', or 'edit'."}


def _create_com(manager: Any, resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    content_blocks: list[dict[str, Any]] = kwargs.get("content_blocks") or []
    if not content_blocks:
        return {"error": "content_blocks is required for create action"}
    if len(content_blocks) > _MAX_CONTENT_BLOCKS:
        return {"error": f"Too many content blocks (max {_MAX_CONTENT_BLOCKS})"}

    word = manager.get_app("Word.Application")
    doc = word.Documents.Add()

    try:
        rng = doc.Content
        for block in content_blocks:
            block_type = block.get("type", "paragraph")
            text = block.get("text", "")

            if block_type == "heading":
                level = block.get("level", 1)
                level = max(0, min(9, level))
                rng.InsertAfter(text + "\n")
                rng.Start = rng.End - len(text) - 1
                if level > 0:
                    rng.Style = f"Heading {level}"
                else:
                    rng.Style = "Title"
                rng.Start = rng.End

            elif block_type == "table":
                rows = block.get("rows", [])
                if not rows:
                    continue
                cols = max(len(r) for r in rows) if rows else 1
                rng.Start = rng.End
                table = doc.Tables.Add(rng, len(rows), cols)
                for i, row_data in enumerate(rows):
                    for j, cell_text in enumerate(row_data):
                        if j < cols:
                            table.Cell(i + 1, j + 1).Range.Text = str(cell_text)
                rng.Start = doc.Content.End - 1
                rng.End = rng.Start

            else:
                rng.InsertAfter(text + "\n")
                rng.Start = rng.End

        os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
        doc.SaveAs2(os.path.abspath(resolved))
    finally:
        doc.Close(SaveChanges=False)

    return {"result": f"Created {display_path}", "path": display_path, "blocks_written": len(content_blocks)}


def _read_com(manager: Any, resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    if not os.path.isfile(resolved):
        return {"error": f"File not found: {display_path}"}

    word = manager.get_app("Word.Application")
    try:
        doc = word.Documents.Open(os.path.abspath(resolved), ReadOnly=True)
    except Exception:
        return {"error": f"Unable to read DOCX file: {display_path}"}

    try:
        output_parts: list[str] = []

        for para in doc.Paragraphs:
            style_name = para.Style.NameLocal
            text = para.Range.Text.rstrip("\r\x07")
            if style_name.startswith("Heading"):
                try:
                    level = int(style_name.split()[-1])
                except (ValueError, IndexError):
                    level = 1
                output_parts.append(f"{'#' * level} {text}")
            elif text.strip():
                output_parts.append(text)

        for i in range(1, doc.Tables.Count + 1):
            output_parts.append(f"\n[Table {i}]")
            table = doc.Tables(i)
            table_rows: list[list[str]] = []
            for r in range(1, table.Rows.Count + 1):
                row_data: list[str] = []
                for c in range(1, table.Columns.Count + 1):
                    cell_text = table.Cell(r, c).Range.Text.rstrip("\r\x07")
                    row_data.append(cell_text)
                table_rows.append(row_data)
            output_parts.append(json.dumps(table_rows, ensure_ascii=False))

        content = "\n".join(output_parts)
        if len(content) > _MAX_OUTPUT:
            content = content[:_MAX_OUTPUT] + "\n... (truncated)"

        para_count = doc.Paragraphs.Count
        table_count = doc.Tables.Count
    finally:
        doc.Close(SaveChanges=False)

    return {
        "content": content,
        "paragraphs": para_count,
        "tables": table_count,
    }


def _edit_com(manager: Any, resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    if not os.path.isfile(resolved):
        return {"error": f"File not found: {display_path}"}

    word = manager.get_app("Word.Application")
    try:
        doc = word.Documents.Open(os.path.abspath(resolved))
    except Exception:
        return {"error": f"Unable to read DOCX file: {display_path}"}

    replacements: list[dict[str, str]] = kwargs.get("replacements") or []
    content_blocks: list[dict[str, Any]] = kwargs.get("content_blocks") or []

    if not replacements and not content_blocks:
        doc.Close(SaveChanges=False)
        return {"error": "Provide 'replacements' and/or 'content_blocks' for edit action"}

    if content_blocks and len(content_blocks) > _MAX_CONTENT_BLOCKS:
        doc.Close(SaveChanges=False)
        return {"error": f"Too many content blocks (max {_MAX_CONTENT_BLOCKS})"}

    try:
        replacements_made = 0
        for rep in replacements:
            old = rep.get("old", "")
            new = rep.get("new", "")
            if not old:
                continue
            find = doc.Content.Find
            find.ClearFormatting()
            find.Replacement.ClearFormatting()
            while find.Execute(FindText=old, ReplaceWith=new, Replace=1):
                replacements_made += 1

        rng = doc.Content
        rng.Start = rng.End
        for block in content_blocks:
            block_type = block.get("type", "paragraph")
            text = block.get("text", "")

            if block_type == "heading":
                level = block.get("level", 1)
                level = max(0, min(9, level))
                rng.InsertAfter(text + "\n")
                rng.Start = rng.End - len(text) - 1
                if level > 0:
                    rng.Style = f"Heading {level}"
                else:
                    rng.Style = "Title"
                rng.Start = rng.End
            elif block_type == "table":
                rows = block.get("rows", [])
                if not rows:
                    continue
                cols = max(len(r) for r in rows) if rows else 1
                rng.Start = rng.End
                table = doc.Tables.Add(rng, len(rows), cols)
                for i, row_data in enumerate(rows):
                    for j, cell_text in enumerate(row_data):
                        if j < cols:
                            table.Cell(i + 1, j + 1).Range.Text = str(cell_text)
                rng.Start = doc.Content.End - 1
                rng.End = rng.Start
            else:
                rng.InsertAfter(text + "\n")
                rng.Start = rng.End

        doc.Save()
    finally:
        doc.Close(SaveChanges=False)

    return {
        "result": f"Edited {display_path}",
        "path": display_path,
        "replacements_made": replacements_made,
        "blocks_appended": len(content_blocks),
    }


# ---------------------------------------------------------------------------
# Library backend (python-docx)
# ---------------------------------------------------------------------------


def _create_lib(resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    import docx as _docx

    content_blocks: list[dict[str, Any]] = kwargs.get("content_blocks") or []
    if not content_blocks:
        return {"error": "content_blocks is required for create action"}
    if len(content_blocks) > _MAX_CONTENT_BLOCKS:
        return {"error": f"Too many content blocks (max {_MAX_CONTENT_BLOCKS})"}

    document = _docx.Document()

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


def _read_lib(resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    import docx as _docx

    if not os.path.isfile(resolved):
        return {"error": f"File not found: {display_path}"}

    try:
        document = _docx.Document(resolved)
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


def _edit_lib(resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    import docx as _docx

    if not os.path.isfile(resolved):
        return {"error": f"File not found: {display_path}"}

    try:
        document = _docx.Document(resolved)
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

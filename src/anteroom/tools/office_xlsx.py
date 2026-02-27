"""XLSX (Excel spreadsheet) create/read/edit tool.

Requires openpyxl: ``pip install anteroom[office]``
"""

from __future__ import annotations

import json
import os
from typing import Any

from .security import validate_path

try:
    import openpyxl

    AVAILABLE = True
except ImportError:
    AVAILABLE = False

_MAX_OUTPUT = 100_000
_MAX_CONTENT_BLOCKS = 200
_MAX_ROWS = 10_000

_working_dir: str = os.getcwd()

DEFINITION: dict[str, Any] = {
    "name": "xlsx",
    "description": (
        "Create, read, or edit Excel spreadsheets (.xlsx). "
        "Action 'create' builds a new workbook with named sheets and row data. "
        "Action 'read' extracts cell data as JSON rows. "
        "Action 'edit' updates specific cells, appends rows, or adds sheets."
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
            "sheets": {
                "type": "array",
                "description": (
                    "Sheets for create action. Each: {name: str, rows: [[value, ...], ...], headers?: [str, ...]}."
                ),
                "items": {"type": "object"},
            },
            "sheet_name": {
                "type": "string",
                "description": "Sheet name to read/edit. Defaults to active sheet.",
            },
            "cell_range": {
                "type": "string",
                "description": "Cell range to read, e.g. 'A1:C10'. Defaults to all data.",
            },
            "updates": {
                "type": "array",
                "description": "List of {cell: str, value: any} for edit action, e.g. {cell: 'A1', value: 42}.",
                "items": {"type": "object"},
            },
            "append_rows": {
                "type": "array",
                "description": "Rows to append for edit action. Each row: [value, ...].",
                "items": {"type": "array"},
            },
            "add_sheets": {
                "type": "array",
                "description": "New sheets to add for edit action. Each: {name: str, rows?: [[value, ...]]}.",
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
        return {"error": "openpyxl is not installed. Install with: pip install anteroom[office]"}

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
    sheets: list[dict[str, Any]] = kwargs.get("sheets") or []
    if not sheets:
        return {"error": "sheets is required for create action"}
    if len(sheets) > _MAX_CONTENT_BLOCKS:
        return {"error": f"Too many sheets (max {_MAX_CONTENT_BLOCKS})"}

    wb = openpyxl.Workbook()
    # Remove default sheet — we'll create our own
    default_sheet = wb.active
    if default_sheet is not None:
        wb.remove(default_sheet)

    total_rows = 0
    for sheet_def in sheets:
        name = sheet_def.get("name", "Sheet")
        ws = wb.create_sheet(title=str(name))
        headers: list[str] = sheet_def.get("headers") or []
        rows: list[list[Any]] = sheet_def.get("rows") or []

        if headers:
            ws.append(headers)
            total_rows += 1

        for row in rows:
            if total_rows >= _MAX_ROWS:
                break
            ws.append(row)
            total_rows += 1

    if total_rows >= _MAX_ROWS:
        return {"error": f"Too many rows (max {_MAX_ROWS})"}

    os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
    wb.save(resolved)
    return {
        "result": f"Created {display_path}",
        "path": display_path,
        "sheets_created": len(sheets),
        "total_rows": total_rows,
    }


def _read(resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    if not os.path.isfile(resolved):
        return {"error": f"File not found: {display_path}"}

    try:
        wb = openpyxl.load_workbook(resolved, read_only=True, data_only=True)
    except Exception:
        return {"error": f"Unable to read XLSX file: {display_path}"}

    sheet_name: str | None = kwargs.get("sheet_name")
    cell_range: str | None = kwargs.get("cell_range")

    try:
        available = list(wb.sheetnames)
        if sheet_name:
            if sheet_name not in available:
                wb.close()
                return {"error": f"Sheet '{sheet_name}' not found. Available: {available}"}
            ws = wb[sheet_name]
        else:
            ws = wb.active
            if ws is None:
                wb.close()
                return {"error": "No active sheet found"}
    except Exception:
        wb.close()
        return {"error": f"Unable to access sheet in: {display_path}"}

    output_rows: list[list[Any]] = []
    try:
        if cell_range:
            for row in ws[cell_range]:
                output_rows.append([cell.value for cell in row])
                if len(output_rows) >= _MAX_ROWS:
                    break
        else:
            for row in ws.iter_rows():
                output_rows.append([cell.value for cell in row])
                if len(output_rows) >= _MAX_ROWS:
                    break
    except Exception:
        wb.close()
        return {"error": f"Unable to read range from: {display_path}"}

    sheet_title = ws.title
    sheets_available = list(wb.sheetnames)
    wb.close()

    content = json.dumps(output_rows, ensure_ascii=False, default=str)
    if len(content) > _MAX_OUTPUT:
        content = content[:_MAX_OUTPUT] + "\n... (truncated)"

    return {
        "content": content,
        "sheet": sheet_title,
        "sheets_available": sheets_available,
        "rows_read": len(output_rows),
    }


def _edit(resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    if not os.path.isfile(resolved):
        return {"error": f"File not found: {display_path}"}

    try:
        wb = openpyxl.load_workbook(resolved)
    except Exception:
        return {"error": f"Unable to read XLSX file: {display_path}"}

    sheet_name: str | None = kwargs.get("sheet_name")
    updates: list[dict[str, Any]] = kwargs.get("updates") or []
    append_rows: list[list[Any]] = kwargs.get("append_rows") or []
    add_sheets: list[dict[str, Any]] = kwargs.get("add_sheets") or []

    if not updates and not append_rows and not add_sheets:
        wb.close()
        return {"error": "Provide 'updates', 'append_rows', and/or 'add_sheets' for edit action"}

    if append_rows and len(append_rows) > _MAX_ROWS:
        wb.close()
        return {"error": f"Too many rows to append (max {_MAX_ROWS})"}

    cells_updated = 0
    rows_appended = 0
    sheets_added = 0

    if updates or append_rows:
        if sheet_name:
            if sheet_name not in wb.sheetnames:
                wb.close()
                return {"error": f"Sheet '{sheet_name}' not found. Available: {wb.sheetnames}"}
            ws = wb[sheet_name]
        else:
            ws = wb.active
            if ws is None:
                wb.close()
                return {"error": "No active sheet found"}

        for upd in updates:
            cell = upd.get("cell", "")
            value = upd.get("value")
            if not cell:
                continue
            ws[cell] = value
            cells_updated += 1

        for row in append_rows:
            ws.append(row)
            rows_appended += 1

    for sheet_def in add_sheets:
        name = sheet_def.get("name", "Sheet")
        ws_new = wb.create_sheet(title=str(name))
        rows = sheet_def.get("rows") or []
        for row in rows[:_MAX_ROWS]:
            ws_new.append(row)
        sheets_added += 1

    wb.save(resolved)
    wb.close()
    return {
        "result": f"Edited {display_path}",
        "path": display_path,
        "cells_updated": cells_updated,
        "rows_appended": rows_appended,
        "sheets_added": sheets_added,
    }

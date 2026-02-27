"""XLSX (Excel spreadsheet) create/read/edit tool.

Backends:
- COM (Windows + Office + pywin32): full Office object model
- Library (openpyxl): cross-platform XML manipulation

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
        import openpyxl  # noqa: F401

        _BACKEND = "lib"
    except ImportError:
        pass

AVAILABLE = _BACKEND is not None

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
        return {"error": "No xlsx backend available. Install with: pip install anteroom[office]"}

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
    sheets: list[dict[str, Any]] = kwargs.get("sheets") or []
    if not sheets:
        return {"error": "sheets is required for create action"}
    if len(sheets) > _MAX_CONTENT_BLOCKS:
        return {"error": f"Too many sheets (max {_MAX_CONTENT_BLOCKS})"}

    excel = manager.get_app("Excel.Application")
    wb = excel.Workbooks.Add()

    try:
        # Remove default sheets (Excel creates 1 by default)
        while wb.Worksheets.Count > 1:
            wb.Worksheets(wb.Worksheets.Count).Delete()

        total_rows = 0
        for idx, sheet_def in enumerate(sheets):
            name = str(sheet_def.get("name", "Sheet"))
            if idx == 0:
                ws = wb.Worksheets(1)
                ws.Name = name
            else:
                ws = wb.Worksheets.Add(After=wb.Worksheets(wb.Worksheets.Count))
                ws.Name = name

            headers: list[str] = sheet_def.get("headers") or []
            rows: list[list[Any]] = sheet_def.get("rows") or []

            row_num = 1
            if headers:
                for col_num, val in enumerate(headers, 1):
                    ws.Cells(row_num, col_num).Value = val
                row_num += 1
                total_rows += 1

            for row in rows:
                if total_rows >= _MAX_ROWS:
                    break
                for col_num, val in enumerate(row, 1):
                    ws.Cells(row_num, col_num).Value = val
                row_num += 1
                total_rows += 1

        if total_rows >= _MAX_ROWS:
            wb.Close(SaveChanges=False)
            return {"error": f"Too many rows (max {_MAX_ROWS})"}

        os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
        # 51 = xlOpenXMLWorkbook (.xlsx)
        wb.SaveAs(os.path.abspath(resolved), FileFormat=51)
    finally:
        wb.Close(SaveChanges=False)

    return {
        "result": f"Created {display_path}",
        "path": display_path,
        "sheets_created": len(sheets),
        "total_rows": total_rows,
    }


def _read_com(manager: Any, resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    if not os.path.isfile(resolved):
        return {"error": f"File not found: {display_path}"}

    excel = manager.get_app("Excel.Application")
    try:
        wb = excel.Workbooks.Open(os.path.abspath(resolved), ReadOnly=True)
    except Exception:
        return {"error": f"Unable to read XLSX file: {display_path}"}

    try:
        sheet_name: str | None = kwargs.get("sheet_name")
        cell_range: str | None = kwargs.get("cell_range")

        available = [wb.Worksheets(i).Name for i in range(1, wb.Worksheets.Count + 1)]
        if sheet_name:
            if sheet_name not in available:
                wb.Close(SaveChanges=False)
                return {"error": f"Sheet '{sheet_name}' not found. Available: {available}"}
            ws = wb.Worksheets(sheet_name)
        else:
            ws = wb.ActiveSheet

        output_rows: list[list[Any]] = []
        if cell_range:
            rng = ws.Range(cell_range)
            for r in range(1, rng.Rows.Count + 1):
                row_data = []
                for c in range(1, rng.Columns.Count + 1):
                    row_data.append(rng.Cells(r, c).Value)
                output_rows.append(row_data)
                if len(output_rows) >= _MAX_ROWS:
                    break
        else:
            used = ws.UsedRange
            if used is not None:
                for r in range(1, used.Rows.Count + 1):
                    row_data = []
                    for c in range(1, used.Columns.Count + 1):
                        row_data.append(used.Cells(r, c).Value)
                    output_rows.append(row_data)
                    if len(output_rows) >= _MAX_ROWS:
                        break

        sheet_title = ws.Name
    finally:
        wb.Close(SaveChanges=False)

    content = json.dumps(output_rows, ensure_ascii=False, default=str)
    if len(content) > _MAX_OUTPUT:
        content = content[:_MAX_OUTPUT] + "\n... (truncated)"

    return {
        "content": content,
        "sheet": sheet_title,
        "sheets_available": available,
        "rows_read": len(output_rows),
    }


def _edit_com(manager: Any, resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    if not os.path.isfile(resolved):
        return {"error": f"File not found: {display_path}"}

    excel = manager.get_app("Excel.Application")
    try:
        wb = excel.Workbooks.Open(os.path.abspath(resolved))
    except Exception:
        return {"error": f"Unable to read XLSX file: {display_path}"}

    sheet_name: str | None = kwargs.get("sheet_name")
    updates: list[dict[str, Any]] = kwargs.get("updates") or []
    append_rows: list[list[Any]] = kwargs.get("append_rows") or []
    add_sheets: list[dict[str, Any]] = kwargs.get("add_sheets") or []

    if not updates and not append_rows and not add_sheets:
        wb.Close(SaveChanges=False)
        return {"error": "Provide 'updates', 'append_rows', and/or 'add_sheets' for edit action"}

    if append_rows and len(append_rows) > _MAX_ROWS:
        wb.Close(SaveChanges=False)
        return {"error": f"Too many rows to append (max {_MAX_ROWS})"}

    try:
        cells_updated = 0
        rows_appended = 0
        sheets_added = 0

        if updates or append_rows:
            if sheet_name:
                sheet_names = [wb.Worksheets(i).Name for i in range(1, wb.Worksheets.Count + 1)]
                if sheet_name not in sheet_names:
                    wb.Close(SaveChanges=False)
                    return {"error": f"Sheet '{sheet_name}' not found. Available: {sheet_names}"}
                ws = wb.Worksheets(sheet_name)
            else:
                ws = wb.ActiveSheet

            for upd in updates:
                cell = upd.get("cell", "")
                value = upd.get("value")
                if not cell:
                    continue
                ws.Range(cell).Value = value
                cells_updated += 1

            if append_rows:
                used = ws.UsedRange
                next_row = used.Row + used.Rows.Count if used is not None else 1
                for row in append_rows:
                    for col_num, val in enumerate(row, 1):
                        ws.Cells(next_row, col_num).Value = val
                    next_row += 1
                    rows_appended += 1

        for sheet_def in add_sheets:
            name = str(sheet_def.get("name", "Sheet"))
            ws_new = wb.Worksheets.Add(After=wb.Worksheets(wb.Worksheets.Count))
            ws_new.Name = name
            rows = sheet_def.get("rows") or []
            for r_idx, row in enumerate(rows[:_MAX_ROWS], 1):
                for c_idx, val in enumerate(row, 1):
                    ws_new.Cells(r_idx, c_idx).Value = val
            sheets_added += 1

        wb.Save()
    finally:
        wb.Close(SaveChanges=False)

    return {
        "result": f"Edited {display_path}",
        "path": display_path,
        "cells_updated": cells_updated,
        "rows_appended": rows_appended,
        "sheets_added": sheets_added,
    }


# ---------------------------------------------------------------------------
# Library backend (openpyxl)
# ---------------------------------------------------------------------------


def _create_lib(resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    import openpyxl as _openpyxl

    sheets: list[dict[str, Any]] = kwargs.get("sheets") or []
    if not sheets:
        return {"error": "sheets is required for create action"}
    if len(sheets) > _MAX_CONTENT_BLOCKS:
        return {"error": f"Too many sheets (max {_MAX_CONTENT_BLOCKS})"}

    wb = _openpyxl.Workbook()
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


def _read_lib(resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    import openpyxl as _openpyxl

    if not os.path.isfile(resolved):
        return {"error": f"File not found: {display_path}"}

    try:
        wb = _openpyxl.load_workbook(resolved, read_only=True, data_only=True)
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


def _edit_lib(resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    import openpyxl as _openpyxl

    if not os.path.isfile(resolved):
        return {"error": f"File not found: {display_path}"}

    try:
        wb = _openpyxl.load_workbook(resolved)
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

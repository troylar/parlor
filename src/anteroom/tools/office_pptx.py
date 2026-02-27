"""PPTX (PowerPoint presentation) create/read/edit tool.

Backends:
- COM (Windows + Office + pywin32): full Office object model
- Library (python-pptx): cross-platform XML manipulation

Install: ``pip install anteroom[office]`` or ``pip install anteroom[office-com]``
"""

from __future__ import annotations

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
        from pptx import Presentation  # noqa: F401

        _BACKEND = "lib"
    except ImportError:
        pass

AVAILABLE = _BACKEND is not None

_MAX_OUTPUT = 100_000
_MAX_CONTENT_BLOCKS = 200
_MAX_SLIDES = 100

_working_dir: str = os.getcwd()

DEFINITION: dict[str, Any] = {
    "name": "pptx",
    "description": (
        "Create, read, or edit PowerPoint presentations (.pptx). "
        "Action 'create' builds a new presentation with slides. "
        "Action 'read' extracts slide text, notes, and structure. "
        "Action 'edit' performs find/replace across slides and can append new slides."
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
            "slides": {
                "type": "array",
                "description": (
                    "Slides for create/edit append. Each slide: "
                    "{title?: str, content?: str, bullets?: [str], notes?: str, layout?: int}. "
                    "layout: 0=title slide, 1=title+content (default), 5=blank, 6=blank."
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
        return {"error": "No pptx backend available. Install with: pip install anteroom[office]"}

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


def _add_slide_com(prs: Any, slide_def: dict[str, Any]) -> None:
    """Add a single slide to a COM presentation."""
    layout_idx = slide_def.get("layout", 2)  # ppLayoutText = 2 in COM
    # COM layout indices: 1=Title, 2=Title+Content, 7=Blank
    try:
        layout = prs.SlideMaster.CustomLayouts(layout_idx)
    except Exception:
        layout = prs.SlideMaster.CustomLayouts(2)

    slide = prs.Slides.AddSlide(prs.Slides.Count + 1, layout)

    title_text = slide_def.get("title")
    if title_text and slide.Shapes.HasTitle:
        slide.Shapes.Title.TextFrame.TextRange.Text = str(title_text)

    content = slide_def.get("content")
    bullets = slide_def.get("bullets")

    if content or bullets:
        # Find the body placeholder (index 2 in COM)
        body = None
        for i in range(1, slide.Shapes.Count + 1):
            shape = slide.Shapes(i)
            if shape.HasTextFrame and shape.PlaceholderFormat.Type == 2:  # ppPlaceholderBody
                body = shape
                break

        if body is not None:
            tf = body.TextFrame.TextRange
            if content:
                tf.Text = str(content)
            elif bullets:
                tf.Text = str(bullets[0])
                for bullet in bullets[1:]:
                    tf.InsertAfter("\r" + str(bullet))

    notes_text = slide_def.get("notes")
    if notes_text:
        slide.NotesPage.Shapes(2).TextFrame.TextRange.Text = str(notes_text)


def _create_com(manager: Any, resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    slides: list[dict[str, Any]] = kwargs.get("slides") or []
    if not slides:
        return {"error": "slides is required for create action"}
    if len(slides) > _MAX_SLIDES:
        return {"error": f"Too many slides (max {_MAX_SLIDES})"}

    ppt = manager.get_app("PowerPoint.Application")
    prs = ppt.Presentations.Add(WithWindow=False)

    try:
        for slide_def in slides:
            _add_slide_com(prs, slide_def)

        os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
        # 24 = ppSaveAsOpenXMLPresentation (.pptx)
        prs.SaveAs(os.path.abspath(resolved), FileFormat=24)
    finally:
        prs.Close()

    return {"result": f"Created {display_path}", "path": display_path, "slides_created": len(slides)}


def _read_com(manager: Any, resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    if not os.path.isfile(resolved):
        return {"error": f"File not found: {display_path}"}

    ppt = manager.get_app("PowerPoint.Application")
    try:
        prs = ppt.Presentations.Open(os.path.abspath(resolved), ReadOnly=True, WithWindow=False)
    except Exception:
        return {"error": f"Unable to read PPTX file: {display_path}"}

    try:
        output_parts: list[str] = []
        slide_count = prs.Slides.Count

        for i in range(1, slide_count + 1):
            slide = prs.Slides(i)
            output_parts.append(f"--- Slide {i} ---")
            for j in range(1, slide.Shapes.Count + 1):
                shape = slide.Shapes(j)
                if shape.HasTextFrame:
                    text = shape.TextFrame.TextRange.Text.strip()
                    if text:
                        output_parts.append(text)
            if slide.HasNotesPage:
                try:
                    notes = slide.NotesPage.Shapes(2).TextFrame.TextRange.Text.strip()
                    if notes:
                        output_parts.append(f"[Notes] {notes}")
                except Exception:
                    pass

        content = "\n".join(output_parts)
        if len(content) > _MAX_OUTPUT:
            content = content[:_MAX_OUTPUT] + "\n... (truncated)"
    finally:
        prs.Close()

    return {
        "content": content,
        "slides": slide_count,
    }


def _edit_com(manager: Any, resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    if not os.path.isfile(resolved):
        return {"error": f"File not found: {display_path}"}

    ppt = manager.get_app("PowerPoint.Application")
    try:
        prs = ppt.Presentations.Open(os.path.abspath(resolved), WithWindow=False)
    except Exception:
        return {"error": f"Unable to read PPTX file: {display_path}"}

    replacements: list[dict[str, str]] = kwargs.get("replacements") or []
    slides: list[dict[str, Any]] = kwargs.get("slides") or []

    if not replacements and not slides:
        prs.Close()
        return {"error": "Provide 'replacements' and/or 'slides' for edit action"}

    if slides and len(slides) > _MAX_SLIDES:
        prs.Close()
        return {"error": f"Too many slides to append (max {_MAX_SLIDES})"}

    current_count = prs.Slides.Count
    if slides and current_count + len(slides) > _MAX_SLIDES:
        prs.Close()
        return {"error": f"Total slides would exceed limit (max {_MAX_SLIDES})"}

    try:
        replacements_made = 0
        for rep in replacements:
            old = rep.get("old", "")
            new = rep.get("new", "")
            if not old:
                continue
            for i in range(1, prs.Slides.Count + 1):
                slide = prs.Slides(i)
                for j in range(1, slide.Shapes.Count + 1):
                    shape = slide.Shapes(j)
                    if shape.HasTextFrame:
                        tr = shape.TextFrame.TextRange
                        find = tr.Find(old)
                        while find is not None:
                            find.Text = new
                            replacements_made += 1
                            find = tr.Find(old)

        for slide_def in slides:
            _add_slide_com(prs, slide_def)

        prs.Save()
    finally:
        prs.Close()

    return {
        "result": f"Edited {display_path}",
        "path": display_path,
        "replacements_made": replacements_made,
        "slides_appended": len(slides),
    }


# ---------------------------------------------------------------------------
# Library backend (python-pptx)
# ---------------------------------------------------------------------------


def _add_slide_lib(prs: Any, slide_def: dict[str, Any]) -> None:
    """Add a single slide to a python-pptx presentation."""
    layout_idx = slide_def.get("layout", 1)
    try:
        layout = prs.slide_layouts[layout_idx]
    except IndexError:
        layout = prs.slide_layouts[1] if len(prs.slide_layouts) > 1 else prs.slide_layouts[0]

    slide = prs.slides.add_slide(layout)

    title_text = slide_def.get("title")
    if title_text and slide.shapes.title:
        slide.shapes.title.text = str(title_text)

    content = slide_def.get("content")
    bullets = slide_def.get("bullets")

    if content or bullets:
        body_placeholder = None
        for shape in slide.placeholders:
            if shape.placeholder_format.idx == 1:
                body_placeholder = shape
                break

        if body_placeholder is not None and hasattr(body_placeholder, "text_frame"):
            tf = body_placeholder.text_frame
            if content:
                tf.text = str(content)
            elif bullets:
                for i, bullet in enumerate(bullets):
                    if i == 0:
                        tf.text = str(bullet)
                    else:
                        p = tf.add_paragraph()
                        p.text = str(bullet)

    notes_text = slide_def.get("notes")
    if notes_text:
        notes_slide = slide.notes_slide
        notes_slide.notes_text_frame.text = str(notes_text)


def _create_lib(resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    from pptx import Presentation as _Presentation

    slides: list[dict[str, Any]] = kwargs.get("slides") or []
    if not slides:
        return {"error": "slides is required for create action"}
    if len(slides) > _MAX_SLIDES:
        return {"error": f"Too many slides (max {_MAX_SLIDES})"}

    prs = _Presentation()

    for slide_def in slides:
        _add_slide_lib(prs, slide_def)

    os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
    prs.save(resolved)
    return {"result": f"Created {display_path}", "path": display_path, "slides_created": len(slides)}


def _read_lib(resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    from pptx import Presentation as _Presentation

    if not os.path.isfile(resolved):
        return {"error": f"File not found: {display_path}"}

    try:
        prs = _Presentation(resolved)
    except Exception:
        return {"error": f"Unable to read PPTX file: {display_path}"}

    output_parts: list[str] = []

    for i, slide in enumerate(prs.slides, 1):
        output_parts.append(f"--- Slide {i} ---")
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = para.text.strip()
                    if text:
                        output_parts.append(text)
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                output_parts.append(f"[Notes] {notes}")

    content = "\n".join(output_parts)
    if len(content) > _MAX_OUTPUT:
        content = content[:_MAX_OUTPUT] + "\n... (truncated)"

    return {
        "content": content,
        "slides": len(prs.slides),
    }


def _edit_lib(resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    from pptx import Presentation as _Presentation

    if not os.path.isfile(resolved):
        return {"error": f"File not found: {display_path}"}

    try:
        prs = _Presentation(resolved)
    except Exception:
        return {"error": f"Unable to read PPTX file: {display_path}"}

    replacements: list[dict[str, str]] = kwargs.get("replacements") or []
    slides: list[dict[str, Any]] = kwargs.get("slides") or []

    if not replacements and not slides:
        return {"error": "Provide 'replacements' and/or 'slides' for edit action"}

    if slides and len(slides) > _MAX_SLIDES:
        return {"error": f"Too many slides to append (max {_MAX_SLIDES})"}

    current_count = len(prs.slides)
    if slides and current_count + len(slides) > _MAX_SLIDES:
        return {"error": f"Total slides would exceed limit (max {_MAX_SLIDES})"}

    replacements_made = 0
    for rep in replacements:
        old = rep.get("old", "")
        new = rep.get("new", "")
        if not old:
            continue
        for slide in prs.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        for run in para.runs:
                            if old in run.text:
                                run.text = run.text.replace(old, new)
                                replacements_made += 1

    for slide_def in slides:
        _add_slide_lib(prs, slide_def)

    prs.save(resolved)
    return {
        "result": f"Edited {display_path}",
        "path": display_path,
        "replacements_made": replacements_made,
        "slides_appended": len(slides),
    }

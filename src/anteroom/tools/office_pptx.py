"""PPTX (PowerPoint presentation) create/read/edit tool.

Requires python-pptx: ``pip install anteroom[office]``
"""

from __future__ import annotations

import os
from typing import Any

from .security import validate_path

try:
    from pptx import Presentation

    AVAILABLE = True
except ImportError:
    AVAILABLE = False

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
        return {"error": "python-pptx is not installed. Install with: pip install anteroom[office]"}

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


def _add_slide(prs: Any, slide_def: dict[str, Any]) -> None:
    """Add a single slide to a presentation."""
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


def _create(resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    slides: list[dict[str, Any]] = kwargs.get("slides") or []
    if not slides:
        return {"error": "slides is required for create action"}
    if len(slides) > _MAX_SLIDES:
        return {"error": f"Too many slides (max {_MAX_SLIDES})"}

    prs = Presentation()

    for slide_def in slides:
        _add_slide(prs, slide_def)

    os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
    prs.save(resolved)
    return {"result": f"Created {display_path}", "path": display_path, "slides_created": len(slides)}


def _read(resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    if not os.path.isfile(resolved):
        return {"error": f"File not found: {display_path}"}

    try:
        prs = Presentation(resolved)
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


def _edit(resolved: str, display_path: str, **kwargs: Any) -> dict[str, Any]:
    if not os.path.isfile(resolved):
        return {"error": f"File not found: {display_path}"}

    try:
        prs = Presentation(resolved)
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
        _add_slide(prs, slide_def)

    prs.save(resolved)
    return {
        "result": f"Edited {display_path}",
        "path": display_path,
        "replacements_made": replacements_made,
        "slides_appended": len(slides),
    }

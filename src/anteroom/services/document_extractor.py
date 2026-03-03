"""Text extraction from binary document formats.

Provides extract_text() which dispatches to format-specific extractors.
Dependencies (pypdf, python-docx, python-pptx, openpyxl) are optional —
extraction gracefully returns None when they are not installed.
"""

from __future__ import annotations

import logging
from io import BytesIO

logger = logging.getLogger(__name__)

# MIME types we can extract text from (when the library is available).
_PDF_TYPES = frozenset({"application/pdf"})
_DOCX_TYPES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)
_PPTX_TYPES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)
_XLSX_TYPES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
)

EXTRACTABLE_MIME_TYPES = _PDF_TYPES | _DOCX_TYPES | _PPTX_TYPES | _XLSX_TYPES


def extract_text(data: bytes, mime_type: str) -> str | None:
    """Extract text content from binary document bytes.

    Returns the extracted text, or None if extraction is not possible
    (unsupported format, missing library, corrupt file).
    """
    if mime_type in _PDF_TYPES:
        return _extract_pdf(data)
    if mime_type in _DOCX_TYPES:
        return _extract_docx(data)
    if mime_type in _PPTX_TYPES:
        return _extract_pptx(data)
    if mime_type in _XLSX_TYPES:
        return _extract_xlsx(data)
    return None


def _extract_pdf(data: bytes) -> str | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.warning(
            "pypdf not installed — PDF text extraction unavailable. Install with: pip install anteroom[docs]"
        )
        return None
    try:
        reader = PdfReader(BytesIO(data))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        result = "\n\n".join(pages).strip()
        return result if result else None
    except Exception:
        logger.warning("Failed to extract text from PDF", exc_info=True)
        return None


def _extract_docx(data: bytes) -> str | None:
    try:
        from docx import Document
    except ImportError:
        logger.warning(
            "python-docx not installed — DOCX text extraction unavailable. Install with: pip install anteroom[docs]"
        )
        return None
    try:
        doc = Document(BytesIO(data))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        result = "\n\n".join(paragraphs).strip()
        return result if result else None
    except Exception:
        logger.warning("Failed to extract text from DOCX", exc_info=True)
        return None


def _extract_pptx(data: bytes) -> str | None:
    try:
        from pptx import Presentation
    except ImportError:
        logger.warning(
            "python-pptx not installed — PPTX text extraction unavailable. Install with: pip install anteroom[office]"
        )
        return None
    try:
        prs = Presentation(BytesIO(data))
        slides: list[str] = []
        for i, slide in enumerate(prs.slides, 1):
            parts: list[str] = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    text = shape.text_frame.text.strip()
                    if text:
                        parts.append(text)
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    parts.append(f"[Speaker notes: {notes}]")
            if parts:
                slides.append(f"--- Slide {i} ---\n" + "\n".join(parts))
        result = "\n\n".join(slides).strip()
        return result if result else None
    except Exception:
        logger.warning("Failed to extract text from PPTX", exc_info=True)
        return None


def _extract_xlsx(data: bytes) -> str | None:
    try:
        from openpyxl import load_workbook
    except ImportError:
        logger.warning(
            "openpyxl not installed — XLSX text extraction unavailable. Install with: pip install anteroom[office]"
        )
        return None
    try:
        wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
        sheets: list[str] = []
        for ws in wb.worksheets:
            rows: list[str] = []
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) if c is not None else "" for c in row]
                if any(cells):
                    rows.append("\t".join(cells))
            if rows:
                sheets.append(f"## Sheet: {ws.title}\n" + "\n".join(rows))
        wb.close()
        result = "\n\n".join(sheets).strip()
        return result if result else None
    except Exception:
        logger.warning("Failed to extract text from XLSX", exc_info=True)
        return None

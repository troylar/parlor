"""Text extraction from binary document formats.

Provides extract_text() which dispatches to format-specific extractors.
Dependencies (pypdf, python-docx) are optional — extraction gracefully
returns None when they are not installed.
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

EXTRACTABLE_MIME_TYPES = _PDF_TYPES | _DOCX_TYPES | _PPTX_TYPES


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
            "python-pptx not installed — PPTX text extraction unavailable. "
            "Install with: pip install anteroom[docs]"
        )
        return None
    try:
        prs = Presentation(BytesIO(data))
        slides: list[str] = []
        for slide in prs.slides:
            texts: list[str] = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        text = "".join(run.text for run in para.runs).strip()
                        if text:
                            texts.append(text)
            if texts:
                slides.append("\n".join(texts))
        result = "\n\n".join(slides).strip()
        return result if result else None
    except Exception:
        logger.warning("Failed to extract text from PPTX", exc_info=True)
        return None

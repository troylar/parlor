"""Tests for document text extraction."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from anteroom.services.document_extractor import (
    EXTRACTABLE_MIME_TYPES,
    _extract_docx,
    _extract_pdf,
    extract_text,
)


class TestExtractText:
    def test_unsupported_mime_returns_none(self) -> None:
        assert extract_text(b"data", "image/png") is None

    def test_unknown_mime_returns_none(self) -> None:
        assert extract_text(b"data", "application/octet-stream") is None

    def test_extractable_mime_types_contains_pdf(self) -> None:
        assert "application/pdf" in EXTRACTABLE_MIME_TYPES

    def test_extractable_mime_types_contains_docx(self) -> None:
        assert "application/vnd.openxmlformats-officedocument.wordprocessingml.document" in EXTRACTABLE_MIME_TYPES

    def test_dispatches_to_pdf(self) -> None:
        with patch("anteroom.services.document_extractor._extract_pdf", return_value="pdf text") as mock:
            result = extract_text(b"data", "application/pdf")
            assert result == "pdf text"
            mock.assert_called_once_with(b"data")

    def test_dispatches_to_docx(self) -> None:
        docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        with patch("anteroom.services.document_extractor._extract_docx", return_value="docx text") as mock:
            result = extract_text(b"data", docx_mime)
            assert result == "docx text"
            mock.assert_called_once_with(b"data")


def _make_pypdf_mock(reader_instance: MagicMock) -> MagicMock:
    """Create a mock pypdf module where PdfReader returns the given instance."""
    mod = MagicMock()
    mod.PdfReader.return_value = reader_instance
    return mod


def _make_docx_mock(doc_instance: MagicMock) -> MagicMock:
    """Create a mock docx module where Document returns the given instance."""
    mod = MagicMock()
    mod.Document.return_value = doc_instance
    return mod


class TestPdfExtraction:
    def test_extracts_text_from_pdf(self) -> None:
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Hello from PDF"
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        with patch.dict(sys.modules, {"pypdf": _make_pypdf_mock(mock_reader)}):
            result = _extract_pdf(b"fake-pdf-bytes")
            assert result == "Hello from PDF"

    def test_multi_page_pdf(self) -> None:
        pages = []
        for text in ["Page one.", "Page two."]:
            p = MagicMock()
            p.extract_text.return_value = text
            pages.append(p)
        mock_reader = MagicMock()
        mock_reader.pages = pages

        with patch.dict(sys.modules, {"pypdf": _make_pypdf_mock(mock_reader)}):
            result = _extract_pdf(b"fake-pdf")
            assert result == "Page one.\n\nPage two."

    def test_empty_pdf_returns_none(self) -> None:
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        with patch.dict(sys.modules, {"pypdf": _make_pypdf_mock(mock_reader)}):
            result = _extract_pdf(b"fake-pdf")
            assert result is None

    def test_pages_with_none_text_skipped(self) -> None:
        p1 = MagicMock()
        p1.extract_text.return_value = "Good page"
        p2 = MagicMock()
        p2.extract_text.return_value = None
        mock_reader = MagicMock()
        mock_reader.pages = [p1, p2]

        with patch.dict(sys.modules, {"pypdf": _make_pypdf_mock(mock_reader)}):
            result = _extract_pdf(b"fake-pdf")
            assert result == "Good page"

    def test_missing_pypdf_returns_none(self) -> None:
        with patch.dict(sys.modules, {"pypdf": None}):
            result = _extract_pdf(b"fake-pdf")
            assert result is None

    def test_corrupt_pdf_returns_none(self) -> None:
        mock_mod = MagicMock()
        mock_mod.PdfReader.side_effect = Exception("Corrupt PDF")
        with patch.dict(sys.modules, {"pypdf": mock_mod}):
            result = _extract_pdf(b"corrupt-data")
            assert result is None


class TestDocxExtraction:
    def test_extracts_text_from_docx(self) -> None:
        mock_para1 = MagicMock()
        mock_para1.text = "First paragraph"
        mock_para2 = MagicMock()
        mock_para2.text = "Second paragraph"
        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para1, mock_para2]

        with patch.dict(sys.modules, {"docx": _make_docx_mock(mock_doc)}):
            result = _extract_docx(b"fake-docx-bytes")
            assert result == "First paragraph\n\nSecond paragraph"

    def test_skips_empty_paragraphs(self) -> None:
        paras = []
        for text in ["Content", "", "   ", "More content"]:
            p = MagicMock()
            p.text = text
            paras.append(p)
        mock_doc = MagicMock()
        mock_doc.paragraphs = paras

        with patch.dict(sys.modules, {"docx": _make_docx_mock(mock_doc)}):
            result = _extract_docx(b"fake-docx")
            assert result == "Content\n\nMore content"

    def test_empty_docx_returns_none(self) -> None:
        mock_doc = MagicMock()
        mock_doc.paragraphs = []

        with patch.dict(sys.modules, {"docx": _make_docx_mock(mock_doc)}):
            result = _extract_docx(b"fake-docx")
            assert result is None

    def test_missing_python_docx_returns_none(self) -> None:
        with patch.dict(sys.modules, {"docx": None}):
            result = _extract_docx(b"fake-docx")
            assert result is None

    def test_corrupt_docx_returns_none(self) -> None:
        mock_mod = MagicMock()
        mock_mod.Document.side_effect = Exception("Corrupt DOCX")
        with patch.dict(sys.modules, {"docx": mock_mod}):
            result = _extract_docx(b"corrupt-data")
            assert result is None

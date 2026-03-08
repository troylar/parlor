"""Tests for document text extraction."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from anteroom.services.document_extractor import (
    EXTRACTABLE_MIME_TYPES,
    ExtractionResult,
    _extract_docx,
    _extract_pdf,
    _extract_pptx,
    _extract_xlsx,
    extract_text,
)


class TestExtractText:
    def test_unsupported_mime_returns_no_text(self) -> None:
        result = extract_text(b"data", "image/png")
        assert result.text is None
        assert result.warnings == []

    def test_unknown_mime_returns_no_text(self) -> None:
        result = extract_text(b"data", "application/octet-stream")
        assert result.text is None
        assert result.warnings == []

    def test_extractable_mime_types_contains_pdf(self) -> None:
        assert "application/pdf" in EXTRACTABLE_MIME_TYPES

    def test_extractable_mime_types_contains_docx(self) -> None:
        assert "application/vnd.openxmlformats-officedocument.wordprocessingml.document" in EXTRACTABLE_MIME_TYPES

    def test_extractable_mime_types_contains_pptx(self) -> None:
        assert "application/vnd.openxmlformats-officedocument.presentationml.presentation" in EXTRACTABLE_MIME_TYPES

    def test_extractable_mime_types_contains_xlsx(self) -> None:
        assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in EXTRACTABLE_MIME_TYPES

    def test_dispatches_to_pptx(self) -> None:
        pptx_mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        mock_result = ExtractionResult(text="pptx text")
        with patch("anteroom.services.document_extractor._extract_pptx", return_value=mock_result) as mock:
            result = extract_text(b"data", pptx_mime)
            assert result.text == "pptx text"
            mock.assert_called_once_with(b"data")

    def test_dispatches_to_xlsx(self) -> None:
        xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        mock_result = ExtractionResult(text="xlsx text")
        with patch("anteroom.services.document_extractor._extract_xlsx", return_value=mock_result) as mock:
            result = extract_text(b"data", xlsx_mime)
            assert result.text == "xlsx text"
            mock.assert_called_once_with(b"data")

    def test_dispatches_to_pdf(self) -> None:
        mock_result = ExtractionResult(text="pdf text")
        with patch("anteroom.services.document_extractor._extract_pdf", return_value=mock_result) as mock:
            result = extract_text(b"data", "application/pdf")
            assert result.text == "pdf text"
            mock.assert_called_once_with(b"data")

    def test_dispatches_to_docx(self) -> None:
        docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        mock_result = ExtractionResult(text="docx text")
        with patch("anteroom.services.document_extractor._extract_docx", return_value=mock_result) as mock:
            result = extract_text(b"data", docx_mime)
            assert result.text == "docx text"
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
            assert result.text == "Hello from PDF"
            assert result.warnings == []

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
            assert result.text == "Page one.\n\nPage two."

    def test_empty_pdf_returns_no_text(self) -> None:
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        with patch.dict(sys.modules, {"pypdf": _make_pypdf_mock(mock_reader)}):
            result = _extract_pdf(b"fake-pdf")
            assert result.text is None
            assert result.warnings == []

    def test_pages_with_none_text_skipped(self) -> None:
        p1 = MagicMock()
        p1.extract_text.return_value = "Good page"
        p2 = MagicMock()
        p2.extract_text.return_value = None
        mock_reader = MagicMock()
        mock_reader.pages = [p1, p2]

        with patch.dict(sys.modules, {"pypdf": _make_pypdf_mock(mock_reader)}):
            result = _extract_pdf(b"fake-pdf")
            assert result.text == "Good page"

    def test_missing_pypdf_returns_warning(self) -> None:
        with patch.dict(sys.modules, {"pypdf": None}):
            result = _extract_pdf(b"fake-pdf")
            assert result.text is None
            assert len(result.warnings) == 1
            assert "pypdf not installed" in result.warnings[0]

    def test_corrupt_pdf_returns_warning(self) -> None:
        mock_mod = MagicMock()
        mock_mod.PdfReader.side_effect = Exception("Corrupt PDF")
        with patch.dict(sys.modules, {"pypdf": mock_mod}):
            result = _extract_pdf(b"corrupt-data")
            assert result.text is None
            assert len(result.warnings) == 1
            assert "Failed to extract text from PDF" in result.warnings[0]


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
            assert result.text == "First paragraph\n\nSecond paragraph"

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
            assert result.text == "Content\n\nMore content"

    def test_empty_docx_returns_no_text(self) -> None:
        mock_doc = MagicMock()
        mock_doc.paragraphs = []

        with patch.dict(sys.modules, {"docx": _make_docx_mock(mock_doc)}):
            result = _extract_docx(b"fake-docx")
            assert result.text is None

    def test_missing_python_docx_returns_warning(self) -> None:
        with patch.dict(sys.modules, {"docx": None}):
            result = _extract_docx(b"fake-docx")
            assert result.text is None
            assert len(result.warnings) == 1
            assert "python-docx not installed" in result.warnings[0]

    def test_corrupt_docx_returns_warning(self) -> None:
        mock_mod = MagicMock()
        mock_mod.Document.side_effect = Exception("Corrupt DOCX")
        with patch.dict(sys.modules, {"docx": mock_mod}):
            result = _extract_docx(b"corrupt-data")
            assert result.text is None
            assert len(result.warnings) == 1
            assert "Failed to extract text from DOCX" in result.warnings[0]


def _make_pptx_mock(presentation_instance: MagicMock) -> MagicMock:
    """Create a mock pptx module where Presentation returns the given instance."""
    mod = MagicMock()
    mod.Presentation.return_value = presentation_instance
    return mod


def _make_openpyxl_mock(workbook_instance: MagicMock) -> MagicMock:
    """Create a mock openpyxl module where load_workbook returns the given instance."""
    mod = MagicMock()
    mod.load_workbook.return_value = workbook_instance
    return mod


class TestPptxExtraction:
    def test_extracts_text_from_slide(self) -> None:
        mock_shape = MagicMock()
        mock_shape.has_text_frame = True
        mock_shape.text_frame.text = "Slide title"
        mock_slide = MagicMock()
        mock_slide.shapes = [mock_shape]
        mock_slide.has_notes_slide = False
        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]

        with patch.dict(sys.modules, {"pptx": _make_pptx_mock(mock_prs)}):
            result = _extract_pptx(b"fake-pptx")
            assert result.text is not None
            assert "Slide title" in result.text
            assert "Slide 1" in result.text

    def test_extracts_notes(self) -> None:
        mock_shape = MagicMock()
        mock_shape.has_text_frame = True
        mock_shape.text_frame.text = "Content"
        mock_slide = MagicMock()
        mock_slide.shapes = [mock_shape]
        mock_slide.has_notes_slide = True
        mock_slide.notes_slide.notes_text_frame.text = "Speaker note here"
        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]

        with patch.dict(sys.modules, {"pptx": _make_pptx_mock(mock_prs)}):
            result = _extract_pptx(b"fake-pptx")
            assert result.text is not None
            assert "Speaker notes: Speaker note here" in result.text

    def test_multi_slide(self) -> None:
        slides = []
        for text in ["First slide", "Second slide"]:
            shape = MagicMock()
            shape.has_text_frame = True
            shape.text_frame.text = text
            slide = MagicMock()
            slide.shapes = [shape]
            slide.has_notes_slide = False
            slides.append(slide)
        mock_prs = MagicMock()
        mock_prs.slides = slides

        with patch.dict(sys.modules, {"pptx": _make_pptx_mock(mock_prs)}):
            result = _extract_pptx(b"fake-pptx")
            assert result.text is not None
            assert "Slide 1" in result.text
            assert "Slide 2" in result.text
            assert "First slide" in result.text
            assert "Second slide" in result.text

    def test_empty_presentation_returns_no_text(self) -> None:
        mock_prs = MagicMock()
        mock_prs.slides = []

        with patch.dict(sys.modules, {"pptx": _make_pptx_mock(mock_prs)}):
            result = _extract_pptx(b"fake-pptx")
            assert result.text is None

    def test_shapes_without_text_skipped(self) -> None:
        mock_shape = MagicMock()
        mock_shape.has_text_frame = False
        mock_slide = MagicMock()
        mock_slide.shapes = [mock_shape]
        mock_slide.has_notes_slide = False
        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]

        with patch.dict(sys.modules, {"pptx": _make_pptx_mock(mock_prs)}):
            result = _extract_pptx(b"fake-pptx")
            assert result.text is None

    def test_missing_python_pptx_returns_warning(self) -> None:
        with patch.dict(sys.modules, {"pptx": None}):
            result = _extract_pptx(b"fake-pptx")
            assert result.text is None
            assert len(result.warnings) == 1
            assert "python-pptx not installed" in result.warnings[0]

    def test_corrupt_pptx_returns_warning(self) -> None:
        mock_mod = MagicMock()
        mock_mod.Presentation.side_effect = Exception("Corrupt PPTX")
        with patch.dict(sys.modules, {"pptx": mock_mod}):
            result = _extract_pptx(b"corrupt-data")
            assert result.text is None
            assert len(result.warnings) == 1
            assert "Failed to extract text from PPTX" in result.warnings[0]


class TestXlsxExtraction:
    def test_extracts_cell_data(self) -> None:
        mock_ws = MagicMock()
        mock_ws.title = "Sheet1"
        mock_ws.iter_rows.return_value = [("Name", "Age"), ("Alice", 30)]
        mock_wb = MagicMock()
        mock_wb.worksheets = [mock_ws]

        with patch.dict(sys.modules, {"openpyxl": _make_openpyxl_mock(mock_wb)}):
            result = _extract_xlsx(b"fake-xlsx")
            assert result.text is not None
            assert "Sheet1" in result.text
            assert "Name" in result.text
            assert "Alice" in result.text

    def test_multi_sheet(self) -> None:
        ws1 = MagicMock()
        ws1.title = "Data"
        ws1.iter_rows.return_value = [("x", "y")]
        ws2 = MagicMock()
        ws2.title = "Summary"
        ws2.iter_rows.return_value = [("total", 100)]
        mock_wb = MagicMock()
        mock_wb.worksheets = [ws1, ws2]

        with patch.dict(sys.modules, {"openpyxl": _make_openpyxl_mock(mock_wb)}):
            result = _extract_xlsx(b"fake-xlsx")
            assert result.text is not None
            assert "Data" in result.text
            assert "Summary" in result.text

    def test_empty_workbook_returns_no_text(self) -> None:
        mock_wb = MagicMock()
        mock_wb.worksheets = []

        with patch.dict(sys.modules, {"openpyxl": _make_openpyxl_mock(mock_wb)}):
            result = _extract_xlsx(b"fake-xlsx")
            assert result.text is None

    def test_none_cells_become_empty_string(self) -> None:
        mock_ws = MagicMock()
        mock_ws.title = "Sheet1"
        mock_ws.iter_rows.return_value = [("val", None, "other")]
        mock_wb = MagicMock()
        mock_wb.worksheets = [mock_ws]

        with patch.dict(sys.modules, {"openpyxl": _make_openpyxl_mock(mock_wb)}):
            result = _extract_xlsx(b"fake-xlsx")
            assert result.text is not None
            assert "val" in result.text
            assert "other" in result.text

    def test_all_none_rows_skipped(self) -> None:
        mock_ws = MagicMock()
        mock_ws.title = "Sheet1"
        mock_ws.iter_rows.return_value = [(None, None), ("data", "here")]
        mock_wb = MagicMock()
        mock_wb.worksheets = [mock_ws]

        with patch.dict(sys.modules, {"openpyxl": _make_openpyxl_mock(mock_wb)}):
            result = _extract_xlsx(b"fake-xlsx")
            assert result.text is not None
            assert "data" in result.text

    def test_missing_openpyxl_returns_warning(self) -> None:
        with patch.dict(sys.modules, {"openpyxl": None}):
            result = _extract_xlsx(b"fake-xlsx")
            assert result.text is None
            assert len(result.warnings) == 1
            assert "openpyxl not installed" in result.warnings[0]

    def test_corrupt_xlsx_returns_warning(self) -> None:
        mock_mod = MagicMock()
        mock_mod.load_workbook.side_effect = Exception("Corrupt XLSX")
        with patch.dict(sys.modules, {"openpyxl": mock_mod}):
            result = _extract_xlsx(b"corrupt-data")
            assert result.text is None
            assert len(result.warnings) == 1
            assert "Failed to extract text from XLSX" in result.warnings[0]

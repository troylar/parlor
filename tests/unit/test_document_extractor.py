"""Tests for document text extraction."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from anteroom.services.document_extractor import (
    EXTRACTABLE_MIME_TYPES,
    _extract_docx,
    _extract_pdf,
    _extract_pptx,
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

    def test_extractable_mime_types_contains_pptx(self) -> None:
        assert (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation" in EXTRACTABLE_MIME_TYPES
        )

    def test_dispatches_to_pptx(self) -> None:
        pptx_mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        with patch("anteroom.services.document_extractor._extract_pptx", return_value="pptx text") as mock:
            result = extract_text(b"data", pptx_mime)
            assert result == "pptx text"
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


def _make_pptx_mock(prs_instance: MagicMock) -> MagicMock:
    """Create a mock pptx module where Presentation returns the given instance."""
    mod = MagicMock()
    mod.Presentation.return_value = prs_instance
    return mod


def _make_slide(shape_texts: list[list[str]]) -> MagicMock:
    """Create a mock slide with shapes containing paragraph runs.

    shape_texts is a list of shapes, each a list of paragraph texts.
    Example: [["Title", "Subtitle"], ["Body text"]] creates 2 shapes.
    """
    slide = MagicMock()
    shapes = []
    for paras_text in shape_texts:
        shape = MagicMock()
        shape.has_text_frame = True
        shape.has_table = False
        paragraphs = []
        for text in paras_text:
            para = MagicMock()
            run = MagicMock()
            run.text = text
            para.runs = [run]
            paragraphs.append(para)
        shape.text_frame.paragraphs = paragraphs
        shapes.append(shape)
    slide.shapes = shapes
    return slide


def _make_table_shape(rows_data: list[list[str]]) -> MagicMock:
    """Create a mock shape with a table.

    rows_data is a list of rows, each a list of cell text values.
    Example: [["Name", "Age"], ["Alice", "30"]] creates a 2x2 table.
    """
    shape = MagicMock()
    shape.has_table = True
    shape.has_text_frame = False
    rows = []
    for row_texts in rows_data:
        row = MagicMock()
        cells = []
        for text in row_texts:
            cell = MagicMock()
            cell.text = text
            cells.append(cell)
        row.cells = cells
        rows.append(row)
    shape.table.rows = rows
    return shape


class TestPptxExtraction:
    def test_extracts_text_from_pptx(self) -> None:
        slide = _make_slide([["Hello from PPTX"]])
        mock_prs = MagicMock()
        mock_prs.slides = [slide]

        with patch.dict(sys.modules, {"pptx": _make_pptx_mock(mock_prs)}):
            result = _extract_pptx(b"fake-pptx-bytes")
            assert "Hello from PPTX" in result
            assert "--- Slide 1 ---" in result

    def test_multi_slide_pptx(self) -> None:
        slide1 = _make_slide([["Slide one"]])
        slide2 = _make_slide([["Slide two"]])
        mock_prs = MagicMock()
        mock_prs.slides = [slide1, slide2]

        with patch.dict(sys.modules, {"pptx": _make_pptx_mock(mock_prs)}):
            result = _extract_pptx(b"fake-pptx")
            assert "--- Slide 1 ---" in result
            assert "Slide one" in result
            assert "--- Slide 2 ---" in result
            assert "Slide two" in result

    def test_multiple_shapes_per_slide(self) -> None:
        slide = _make_slide([["Title"], ["Body text"]])
        mock_prs = MagicMock()
        mock_prs.slides = [slide]

        with patch.dict(sys.modules, {"pptx": _make_pptx_mock(mock_prs)}):
            result = _extract_pptx(b"fake-pptx")
            assert "Title" in result
            assert "Body text" in result

    def test_skips_shapes_without_text_frame(self) -> None:
        slide = MagicMock()
        shape_with_text = MagicMock()
        shape_with_text.has_text_frame = True
        shape_with_text.has_table = False
        para = MagicMock()
        run = MagicMock()
        run.text = "Has text"
        para.runs = [run]
        shape_with_text.text_frame.paragraphs = [para]

        shape_no_text = MagicMock()
        shape_no_text.has_text_frame = False
        shape_no_text.has_table = False

        slide.shapes = [shape_no_text, shape_with_text]
        mock_prs = MagicMock()
        mock_prs.slides = [slide]

        with patch.dict(sys.modules, {"pptx": _make_pptx_mock(mock_prs)}):
            result = _extract_pptx(b"fake-pptx")
            assert "Has text" in result

    def test_table_extraction(self) -> None:
        table_shape = _make_table_shape([["Name", "Age"], ["Alice", "30"], ["Bob", "25"]])
        slide = MagicMock()
        slide.shapes = [table_shape]
        mock_prs = MagicMock()
        mock_prs.slides = [slide]

        with patch.dict(sys.modules, {"pptx": _make_pptx_mock(mock_prs)}):
            result = _extract_pptx(b"fake-pptx")
            assert "| Name | Age |" in result
            assert "| --- | --- |" in result
            assert "| Alice | 30 |" in result
            assert "| Bob | 25 |" in result

    def test_mixed_text_and_tables(self) -> None:
        text_shape = MagicMock()
        text_shape.has_text_frame = True
        text_shape.has_table = False
        para = MagicMock()
        run = MagicMock()
        run.text = "Title text"
        para.runs = [run]
        text_shape.text_frame.paragraphs = [para]

        table_shape = _make_table_shape([["Col A", "Col B"], ["1", "2"]])

        slide = MagicMock()
        slide.shapes = [text_shape, table_shape]
        mock_prs = MagicMock()
        mock_prs.slides = [slide]

        with patch.dict(sys.modules, {"pptx": _make_pptx_mock(mock_prs)}):
            result = _extract_pptx(b"fake-pptx")
            assert "Title text" in result
            assert "| Col A | Col B |" in result

    def test_empty_pptx_returns_none(self) -> None:
        mock_prs = MagicMock()
        mock_prs.slides = []

        with patch.dict(sys.modules, {"pptx": _make_pptx_mock(mock_prs)}):
            result = _extract_pptx(b"fake-pptx")
            assert result is None

    def test_slides_with_empty_text_returns_none(self) -> None:
        slide = MagicMock()
        shape = MagicMock()
        shape.has_text_frame = True
        shape.has_table = False
        para = MagicMock()
        run = MagicMock()
        run.text = "   "
        para.runs = [run]
        shape.text_frame.paragraphs = [para]
        slide.shapes = [shape]
        mock_prs = MagicMock()
        mock_prs.slides = [slide]

        with patch.dict(sys.modules, {"pptx": _make_pptx_mock(mock_prs)}):
            result = _extract_pptx(b"fake-pptx")
            assert result is None

    def test_missing_pptx_returns_none(self) -> None:
        with patch.dict(sys.modules, {"pptx": None}):
            result = _extract_pptx(b"fake-pptx")
            assert result is None

    def test_corrupt_pptx_returns_none(self) -> None:
        mock_mod = MagicMock()
        mock_mod.Presentation.side_effect = Exception("Corrupt PPTX")
        with patch.dict(sys.modules, {"pptx": mock_mod}):
            result = _extract_pptx(b"corrupt-data")
            assert result is None

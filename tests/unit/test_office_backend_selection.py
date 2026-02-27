"""Tests for backend selection logic across all office tools.

Verifies that:
- On non-Windows platforms, _BACKEND is "lib" (if library available) or None
- COM-related imports never execute on non-Windows
- The AVAILABLE flag correctly reflects backend availability
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest


class TestDocxBackendSelection:
    def test_lib_backend_on_non_windows(self):
        from anteroom.tools.office_docx import _BACKEND

        if sys.platform != "win32":
            assert _BACKEND in ("lib", None)

    def test_available_matches_backend(self):
        from anteroom.tools.office_docx import _BACKEND, AVAILABLE

        assert AVAILABLE == (_BACKEND is not None)

    def test_lib_backend_when_no_com(self):
        """When win32com is not importable, should fall back to lib."""
        from anteroom.tools.office_docx import _BACKEND, AVAILABLE

        if sys.platform != "win32":
            if AVAILABLE:
                assert _BACKEND == "lib"

    @pytest.mark.asyncio
    async def test_handle_error_when_unavailable(self):
        from anteroom.tools.office_docx import handle

        with patch("anteroom.tools.office_docx.AVAILABLE", False):
            result = await handle(action="read", path="test.docx")
            assert "error" in result
            assert "pip install" in result["error"]

    @pytest.mark.asyncio
    async def test_lib_dispatch_when_backend_is_lib(self):
        """Ensure lib backend is used when _BACKEND == 'lib'."""
        from anteroom.tools.office_docx import _BACKEND, handle

        if _BACKEND != "lib":
            pytest.skip("lib backend not active")

        with patch("anteroom.tools.office_docx._create_lib") as mock_create:
            mock_create.return_value = {"result": "ok"}
            await handle(
                action="create",
                path="test.docx",
                content_blocks=[{"type": "paragraph", "text": "x"}],
            )
            mock_create.assert_called_once()


class TestXlsxBackendSelection:
    def test_lib_backend_on_non_windows(self):
        from anteroom.tools.office_xlsx import _BACKEND

        if sys.platform != "win32":
            assert _BACKEND in ("lib", None)

    def test_available_matches_backend(self):
        from anteroom.tools.office_xlsx import _BACKEND, AVAILABLE

        assert AVAILABLE == (_BACKEND is not None)

    @pytest.mark.asyncio
    async def test_handle_error_when_unavailable(self):
        from anteroom.tools.office_xlsx import handle

        with patch("anteroom.tools.office_xlsx.AVAILABLE", False):
            result = await handle(action="read", path="test.xlsx")
            assert "error" in result
            assert "pip install" in result["error"]


class TestPptxBackendSelection:
    def test_lib_backend_on_non_windows(self):
        from anteroom.tools.office_pptx import _BACKEND

        if sys.platform != "win32":
            assert _BACKEND in ("lib", None)

    def test_available_matches_backend(self):
        from anteroom.tools.office_pptx import _BACKEND, AVAILABLE

        assert AVAILABLE == (_BACKEND is not None)

    @pytest.mark.asyncio
    async def test_handle_error_when_unavailable(self):
        from anteroom.tools.office_pptx import handle

        with patch("anteroom.tools.office_pptx.AVAILABLE", False):
            result = await handle(action="read", path="test.pptx")
            assert "error" in result
            assert "pip install" in result["error"]


class TestNoComOnNonWindows:
    """Verify COM code is never reached on non-Windows platforms."""

    def test_com_module_does_not_import_win32com_on_non_windows(self):
        if sys.platform == "win32":
            pytest.skip("Only relevant on non-Windows")
        assert "win32com" not in sys.modules or sys.platform == "win32"

    def test_all_backends_are_lib_or_none_on_non_windows(self):
        if sys.platform == "win32":
            pytest.skip("Only relevant on non-Windows")

        from anteroom.tools.office_docx import _BACKEND as _DOCX_BACKEND  # noqa: N812
        from anteroom.tools.office_pptx import _BACKEND as _PPTX_BACKEND  # noqa: N812
        from anteroom.tools.office_xlsx import _BACKEND as _XLSX_BACKEND  # noqa: N812

        for name, backend in [("docx", _DOCX_BACKEND), ("xlsx", _XLSX_BACKEND), ("pptx", _PPTX_BACKEND)]:
            assert backend in ("lib", None), f"{name} backend is '{backend}' on non-Windows"

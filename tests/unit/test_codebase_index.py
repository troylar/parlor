"""Tests for tree-sitter codebase index service."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from anteroom.services.codebase_index import (
    CodebaseIndexService,
    CodebaseMap,
    FileSymbols,
    SymbolInfo,
    _classify_node,
    _estimate_tokens,
    _extract_imports_python,
    _rank_files,
    create_index_service,
)


class TestClassifyNode:
    def test_import_types(self) -> None:
        assert _classify_node("import_statement", "python") == "import"
        assert _classify_node("import_from_statement", "python") == "import"
        assert _classify_node("use_declaration", "rust") == "import"

    def test_class_types(self) -> None:
        assert _classify_node("class_definition", "python") == "class"
        assert _classify_node("class_declaration", "java") == "class"
        assert _classify_node("struct_item", "rust") == "class"
        assert _classify_node("module", "ruby") == "class"

    def test_interface_type(self) -> None:
        assert _classify_node("interface_declaration", "typescript") == "interface"

    def test_type_alias(self) -> None:
        assert _classify_node("type_alias_declaration", "typescript") == "type"

    def test_type_declaration(self) -> None:
        assert _classify_node("type_declaration", "go") == "type"

    def test_enum_type(self) -> None:
        assert _classify_node("enum_item", "rust") == "enum"

    def test_method_type(self) -> None:
        assert _classify_node("method_declaration", "java") == "method"

    def test_function_type(self) -> None:
        assert _classify_node("function_definition", "python") == "function"
        assert _classify_node("function_declaration", "javascript") == "function"
        assert _classify_node("impl_item", "rust") == "function"

    def test_export_type(self) -> None:
        assert _classify_node("export_statement", "javascript") == "export"

    def test_unknown_type(self) -> None:
        assert _classify_node("some_random_node", "python") == "symbol"


class TestExtractImportsPython:
    def test_from_import(self) -> None:
        node = MagicMock()
        node.text = b"from os.path import join"
        result = _extract_imports_python(node)
        assert result == ["os.path"]

    def test_plain_import(self) -> None:
        node = MagicMock()
        node.text = b"import os, sys"
        result = _extract_imports_python(node)
        assert result == ["os", "sys"]

    def test_import_with_alias(self) -> None:
        node = MagicMock()
        node.text = b"import numpy as np"
        result = _extract_imports_python(node)
        assert result == ["numpy"]

    def test_empty_from(self) -> None:
        node = MagicMock()
        node.text = b"from "
        result = _extract_imports_python(node)
        # "from " splits to ["from", ""] — len < 2 after strip, so empty
        assert result == []


class TestEstimateTokens:
    def test_fallback_char_estimate(self) -> None:
        # tiktoken is installed, so mock it away to test the fallback
        with patch.dict("sys.modules", {"tiktoken": None}):
            # Force the except branch by making import fail
            original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

            def _mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
                if name == "tiktoken":
                    raise ImportError("mocked")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=_mock_import):
                text = "a" * 400
                result = _estimate_tokens(text)
                assert result == 100

    def test_tiktoken_if_available(self) -> None:
        # tiktoken is a dependency, so it should work
        result = _estimate_tokens("hello world")
        assert isinstance(result, int)
        assert result > 0


class TestRankFiles:
    def test_files_ranked_by_import_count(self) -> None:
        files = [
            FileSymbols(path="src/a.py", language="python", symbols=[], imports=["b"]),
            FileSymbols(path="src/b.py", language="python", symbols=[], imports=[]),
            FileSymbols(path="src/c.py", language="python", symbols=[], imports=["b"]),
        ]
        ranked = _rank_files(files)
        # b.py is imported by a.py and c.py, so should be first
        assert ranked[0].path == "src/b.py"

    def test_ties_sorted_alphabetically(self) -> None:
        files = [
            FileSymbols(path="src/z.py", language="python", symbols=[], imports=[]),
            FileSymbols(path="src/a.py", language="python", symbols=[], imports=[]),
        ]
        ranked = _rank_files(files)
        assert ranked[0].path == "src/a.py"
        assert ranked[1].path == "src/z.py"

    def test_empty_list(self) -> None:
        assert _rank_files([]) == []


class TestCodebaseIndexService:
    def test_is_available_without_treesitter(self) -> None:
        with patch.dict("sys.modules", {"tree_sitter": None}):
            service = CodebaseIndexService()
            service._available = None
            # Force reimport failure
            with patch("builtins.__import__", side_effect=ImportError("no tree_sitter")):
                assert service.is_available() is False

    def test_is_available_caches_result(self) -> None:
        service = CodebaseIndexService()
        service._available = True
        assert service.is_available() is True

    def test_scan_fallback_without_treesitter(self) -> None:
        service = CodebaseIndexService()
        service._available = False

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a Python file
            (Path(tmpdir) / "hello.py").write_text("def greet(): pass\n")
            result = service.scan(tmpdir)

        assert isinstance(result, CodebaseMap)
        assert len(result.files) == 1
        assert result.files[0].path == "hello.py"
        assert result.files[0].language == "python"
        assert result.files[0].symbols == []  # fallback = no symbols

    def test_scan_respects_exclude_dirs(self) -> None:
        service = CodebaseIndexService(exclude_dirs=["hidden"])
        service._available = False

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "hello.py").write_text("x = 1\n")
            hidden = Path(tmpdir) / "hidden"
            hidden.mkdir()
            (hidden / "secret.py").write_text("y = 2\n")
            result = service.scan(tmpdir)

        paths = [f.path for f in result.files]
        assert "hello.py" in paths
        assert "hidden/secret.py" not in paths

    def test_scan_respects_language_filter(self) -> None:
        service = CodebaseIndexService(languages=["python"])
        service._available = False

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "hello.py").write_text("x = 1\n")
            (Path(tmpdir) / "app.js").write_text("let x = 1;\n")
            result = service.scan(tmpdir)

        paths = [f.path for f in result.files]
        assert "hello.py" in paths
        assert "app.js" not in paths

    def test_format_map_basic(self) -> None:
        service = CodebaseIndexService()
        cmap = CodebaseMap(
            root="/tmp/test",
            files=[
                FileSymbols(
                    path="src/main.py",
                    language="python",
                    symbols=[
                        SymbolInfo(name="greet", kind="function", signature="def greet(name: str) -> str:"),
                        SymbolInfo(name="os", kind="import", signature="import os"),
                    ],
                ),
            ],
        )
        result = service.format_map(cmap, token_budget=5000)
        assert "src/main.py" in result
        assert "def greet(name: str) -> str:" in result
        assert "import os" not in result  # imports are excluded from output

    def test_format_map_respects_token_budget(self) -> None:
        service = CodebaseIndexService()
        files = []
        for i in range(100):
            files.append(
                FileSymbols(
                    path=f"src/module_{i}.py",
                    language="python",
                    symbols=[
                        SymbolInfo(
                            name=f"func_{i}",
                            kind="function",
                            signature=f"def func_{i}(x: int, y: int, z: int) -> dict[str, Any]:",
                        ),
                    ],
                )
            )
        cmap = CodebaseMap(root="/tmp/test", files=files)
        result = service.format_map(cmap, token_budget=200)
        # Should not include all 100 files
        assert result.count("## src/module_") < 100

    def test_format_map_empty_symbols(self) -> None:
        service = CodebaseIndexService()
        cmap = CodebaseMap(
            root="/tmp/test",
            files=[FileSymbols(path="src/empty.py", language="python", symbols=[])],
        )
        result = service.format_map(cmap, token_budget=5000)
        assert "src/empty.py" in result
        assert "(no exported symbols)" in result

    def test_get_map_wraps_in_xml(self) -> None:
        service = CodebaseIndexService()
        service._available = False

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "hello.py").write_text("x = 1\n")
            result = service.get_map(tmpdir, token_budget=5000)

        assert result.startswith("\n<codebase_index>")
        assert result.endswith("</codebase_index>")

    def test_get_map_empty_dir(self) -> None:
        service = CodebaseIndexService()
        service._available = False

        with tempfile.TemporaryDirectory() as tmpdir:
            result = service.get_map(tmpdir)

        assert result == ""


class TestCreateIndexService:
    def test_returns_service_when_enabled(self) -> None:
        config = MagicMock()
        config.codebase_index.enabled = True
        config.codebase_index.exclude_dirs = [".git", "node_modules"]
        config.codebase_index.languages = []
        result = create_index_service(config)
        assert isinstance(result, CodebaseIndexService)

    def test_returns_none_when_disabled(self) -> None:
        config = MagicMock()
        config.codebase_index.enabled = False
        result = create_index_service(config)
        assert result is None

    def test_passes_languages_filter(self) -> None:
        config = MagicMock()
        config.codebase_index.enabled = True
        config.codebase_index.exclude_dirs = []
        config.codebase_index.languages = ["python", "typescript"]
        result = create_index_service(config)
        assert result is not None
        assert result._languages == {"python", "typescript"}

    def test_none_languages_means_all(self) -> None:
        config = MagicMock()
        config.codebase_index.enabled = True
        config.codebase_index.exclude_dirs = []
        config.codebase_index.languages = None
        result = create_index_service(config)
        assert result is not None
        assert result._languages is None

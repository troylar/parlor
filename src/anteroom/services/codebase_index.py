"""Tree-sitter codebase index for token-efficient context injection.

Parses source files to extract symbols (functions, classes, types, imports),
ranks them by dependency centrality, and produces a token-budgeted map
for injection into the system prompt.

Requires optional dependency: ``pip install anteroom[index]``
Degrades gracefully to filename-only listing when tree-sitter is unavailable.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Language detection from file extensions
_EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cs": "c_sharp",
}

# tree-sitter node types to extract per language
_SYMBOL_QUERIES: dict[str, list[str]] = {
    "python": ["function_definition", "class_definition", "import_from_statement", "import_statement"],
    "javascript": ["function_declaration", "class_declaration", "import_statement", "export_statement"],
    "typescript": [
        "function_declaration",
        "class_declaration",
        "import_statement",
        "export_statement",
        "interface_declaration",
        "type_alias_declaration",
    ],
    "go": ["function_declaration", "method_declaration", "type_declaration", "import_declaration"],
    "rust": ["function_item", "struct_item", "enum_item", "impl_item", "use_declaration"],
    "java": ["class_declaration", "method_declaration", "import_declaration", "interface_declaration"],
    "ruby": ["method", "class", "module"],
    "c": ["function_definition", "struct_specifier", "type_definition"],
    "cpp": ["function_definition", "class_specifier", "struct_specifier", "type_definition"],
    "c_sharp": ["class_declaration", "method_declaration", "interface_declaration"],
}


class CodebaseIndexUnavailableError(Exception):
    """Raised when tree-sitter is not installed."""


@dataclass
class SymbolInfo:
    """A single extracted symbol."""

    name: str
    kind: str  # function, class, method, import, type, interface
    signature: str  # human-readable one-line signature


@dataclass
class FileSymbols:
    """Symbols extracted from a single file."""

    path: str  # relative to project root
    language: str
    symbols: list[SymbolInfo] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)  # imported module names
    mtime: float = 0.0


@dataclass
class CodebaseMap:
    """The complete codebase symbol map."""

    root: str
    files: list[FileSymbols] = field(default_factory=list)
    scan_time: float = 0.0


def _classify_node(node_type: str, language: str) -> str:
    """Map tree-sitter node type to a human-readable kind."""
    if "import" in node_type or "use_declaration" in node_type:
        return "import"
    if "class" in node_type or "struct" in node_type or "module" == node_type:
        return "class"
    if "interface" in node_type:
        return "interface"
    if "type" in node_type and "alias" in node_type:
        return "type"
    if "type_declaration" in node_type:
        return "type"
    if "enum" in node_type:
        return "enum"
    if "method" in node_type:
        return "method"
    if "function" in node_type or "impl_item" == node_type:
        return "function"
    if "export" in node_type:
        return "export"
    return "symbol"


def _extract_name(node: Any, language: str) -> str:
    """Extract the name from a tree-sitter node."""
    # Look for a 'name' or 'identifier' child
    for child in node.children:
        if child.type in ("identifier", "name", "type_identifier", "property_identifier"):
            return child.text.decode("utf-8") if isinstance(child.text, bytes) else child.text
    # For import statements, use the full text trimmed
    if "import" in node.type or "use_declaration" in node.type:
        text = node.text.decode("utf-8") if isinstance(node.text, bytes) else node.text
        return text.strip().split("\n")[0][:80]
    return ""


def _extract_signature(node: Any, language: str, kind: str) -> str:
    """Extract a one-line signature from a tree-sitter node."""
    text = node.text.decode("utf-8") if isinstance(node.text, bytes) else node.text
    first_line = text.split("\n")[0].strip()
    # Truncate long signatures
    if len(first_line) > 120:
        first_line = first_line[:117] + "..."
    return first_line


def _extract_imports_python(node: Any) -> list[str]:
    """Extract imported module names from Python import nodes."""
    modules: list[str] = []
    text = node.text.decode("utf-8") if isinstance(node.text, bytes) else node.text
    text = text.strip()
    if text.startswith("from "):
        parts = text.split()
        if len(parts) >= 2:
            modules.append(parts[1])
    elif text.startswith("import "):
        parts = text.replace("import ", "").split(",")
        for p in parts:
            mod = p.strip().split(" as ")[0].strip()
            if mod:
                modules.append(mod)
    return modules


# Skip files larger than 1 MB to prevent excessive memory/CPU usage
_MAX_FILE_SIZE = 1_048_576


def _parse_file(
    file_path: Path,
    root: Path,
    language: str,
    parser: Any,
    ts_language: Any,
) -> FileSymbols | None:
    """Parse a single file and extract symbols."""
    try:
        if file_path.stat().st_size > _MAX_FILE_SIZE:
            return None
        source = file_path.read_bytes()
    except (OSError, PermissionError):
        return None

    rel_path = str(file_path.relative_to(root))
    mtime = file_path.stat().st_mtime

    parser.language = ts_language
    tree = parser.parse(source)
    root_node = tree.root_node

    query_types = set(_SYMBOL_QUERIES.get(language, []))
    if not query_types:
        return FileSymbols(path=rel_path, language=language, mtime=mtime)

    symbols: list[SymbolInfo] = []
    imports: list[str] = []

    def _walk(node: Any) -> None:
        if node.type in query_types:
            kind = _classify_node(node.type, language)
            name = _extract_name(node, language)
            sig = _extract_signature(node, language, kind)
            if name or kind == "import":
                symbols.append(SymbolInfo(name=name, kind=kind, signature=sig))
            if kind == "import" and language == "python":
                imports.extend(_extract_imports_python(node))
            elif kind == "import":
                if name:
                    imports.append(name)
        # Recurse into children (but not too deep for imports)
        for child in node.children:
            _walk(child)

    _walk(root_node)

    return FileSymbols(path=rel_path, language=language, symbols=symbols, imports=imports, mtime=mtime)


def _rank_files(files: list[FileSymbols]) -> list[FileSymbols]:
    """Rank files by how often they are imported by other files."""
    # Build a map of module name → file path
    # For Python: src/anteroom/config.py → anteroom.config
    import_counts: dict[str, int] = {}
    for f in files:
        import_counts[f.path] = 0

    # Count how many times each file's module name appears in other files' imports
    path_to_modules: dict[str, set[str]] = {}
    for f in files:
        modules: set[str] = set()
        # Convert path to possible module names
        p = f.path.replace("/", ".").replace("\\", ".")
        if p.endswith(".py"):
            p = p[:-3]
        if p.endswith(".__init__"):
            p = p[:-9]
        modules.add(p)
        # Also add just the filename stem
        modules.add(Path(f.path).stem)
        path_to_modules[f.path] = modules

    for f in files:
        for imp in f.imports:
            for path, modules in path_to_modules.items():
                if path == f.path:
                    continue
                for mod in modules:
                    if imp == mod or imp.endswith("." + mod) or mod.endswith("." + imp):
                        import_counts[path] = import_counts.get(path, 0) + 1
                        break

    return sorted(files, key=lambda f: (-import_counts.get(f.path, 0), f.path))


def _estimate_tokens(text: str) -> int:
    """Estimate token count. Use tiktoken if available, else chars/4."""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text) // 4


class CodebaseIndexService:
    """Builds and caches a token-efficient codebase symbol map."""

    def __init__(self, exclude_dirs: list[str] | None = None, languages: list[str] | None = None) -> None:
        self._exclude_dirs = set(exclude_dirs or [])
        self._languages = set(languages) if languages else None
        self._parser: Any = None
        self._lang_cache: dict[str, Any] = {}
        self._file_cache: dict[str, FileSymbols] = {}
        self._available: bool | None = None

    def _ensure_parser(self) -> Any:
        """Lazy-load tree-sitter parser on first use."""
        if self._parser is not None:
            return self._parser
        try:
            import tree_sitter

            self._parser = tree_sitter.Parser()
            return self._parser
        except ImportError:
            self._available = False
            raise CodebaseIndexUnavailableError(
                "tree-sitter is not installed. Install with: pip install anteroom[index]"
            )

    def _get_language(self, lang_name: str) -> Any:
        """Get a tree-sitter Language object for the given language."""
        if lang_name in self._lang_cache:
            return self._lang_cache[lang_name]
        try:
            import tree_sitter_language_pack

            ts_lang = tree_sitter_language_pack.get_language(lang_name)
            self._lang_cache[lang_name] = ts_lang
            return ts_lang
        except (ImportError, Exception) as exc:
            logger.debug("Language %s not available: %s", lang_name, exc)
            return None

    def is_available(self) -> bool:
        """Check if tree-sitter is available without raising."""
        if self._available is not None:
            return self._available
        try:
            self._ensure_parser()
            self._available = True
        except CodebaseIndexUnavailableError:
            self._available = False
        return self._available

    def scan(self, root_dir: str) -> CodebaseMap:
        """Scan a directory and build the codebase symbol map."""
        root = Path(root_dir).resolve()
        start = time.monotonic()

        source_files: list[tuple[Path, str]] = []
        for dirpath, dirnames, filenames in os.walk(root):
            # Filter excluded directories in-place
            dirnames[:] = [
                d
                for d in dirnames
                if d not in self._exclude_dirs and not any(d.endswith(exc) for exc in self._exclude_dirs)
            ]
            for fname in filenames:
                ext = Path(fname).suffix
                lang = _EXTENSION_MAP.get(ext)
                if lang and (not self._languages or lang in self._languages):
                    source_files.append((Path(dirpath) / fname, lang))

        if not self.is_available():
            # Fallback: filename-only listing
            files = []
            for fpath, lang in source_files:
                rel = str(fpath.relative_to(root))
                files.append(FileSymbols(path=rel, language=lang, mtime=fpath.stat().st_mtime))
            return CodebaseMap(
                root=str(root), files=sorted(files, key=lambda f: f.path), scan_time=time.monotonic() - start
            )

        parser = self._ensure_parser()
        files: list[FileSymbols] = []

        for fpath, lang in source_files:
            rel = str(fpath.relative_to(root))

            # Mtime cache: skip re-parsing if file hasn't changed
            try:
                current_mtime = fpath.stat().st_mtime
            except OSError:
                continue
            cached = self._file_cache.get(rel)
            if cached and cached.mtime == current_mtime:
                files.append(cached)
                continue

            ts_lang = self._get_language(lang)
            if ts_lang is None:
                files.append(FileSymbols(path=rel, language=lang, mtime=current_mtime))
                continue

            result = _parse_file(fpath, root, lang, parser, ts_lang)
            if result:
                self._file_cache[rel] = result
                files.append(result)

        ranked = _rank_files(files)
        return CodebaseMap(root=str(root), files=ranked, scan_time=time.monotonic() - start)

    def format_map(self, cmap: CodebaseMap, token_budget: int = 1000) -> str:
        """Format the codebase map as a token-budgeted string."""
        lines: list[str] = ["# Codebase Map (auto-generated)", ""]
        current_tokens = _estimate_tokens("\n".join(lines))

        for fsym in cmap.files:
            # Build file section
            section_lines: list[str] = [f"## {fsym.path}"]
            for sym in fsym.symbols:
                if sym.kind == "import":
                    continue  # Don't include imports in the map output
                prefix = f"- {sym.kind}: " if sym.kind not in ("function", "method") else "- "
                section_lines.append(f"{prefix}{sym.signature}")

            if len(section_lines) == 1:
                # File with no symbols (or only imports) — just show the filename
                section_lines.append("  (no exported symbols)")

            section_text = "\n".join(section_lines) + "\n"
            section_tokens = _estimate_tokens(section_text)

            if current_tokens + section_tokens > token_budget:
                # Try adding just the filename
                short = f"## {fsym.path}\n"
                short_tokens = _estimate_tokens(short)
                if current_tokens + short_tokens <= token_budget:
                    lines.append(short)
                    current_tokens += short_tokens
                else:
                    break  # Budget exhausted
            else:
                lines.append(section_text)
                current_tokens += section_tokens

        return "\n".join(lines).rstrip()

    def get_map(self, root_dir: str, token_budget: int = 1000) -> str:
        """Scan and format a codebase map, wrapped in XML tags for system prompt injection."""
        cmap = self.scan(root_dir)
        if not cmap.files:
            return ""
        formatted = self.format_map(cmap, token_budget)
        return f"\n<codebase_index>\n{formatted}\n</codebase_index>"


def create_index_service(
    config: Any,
) -> CodebaseIndexService | None:
    """Factory: create a CodebaseIndexService if enabled, else None."""
    if not config.codebase_index.enabled:
        return None
    return CodebaseIndexService(
        exclude_dirs=config.codebase_index.exclude_dirs,
        languages=config.codebase_index.languages or None,
    )

"""Tab completer for / commands, @ file paths, and conversation slugs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from ..services import storage


class ParlorCompleter(Completer):
    """Tab completer for / commands, @ file paths, and conversation slugs."""

    _slug_commands = frozenset({"resume", "delete", "rename"})

    def __init__(self, commands: list[str], skill_names: list[str], wd: str, db: Any) -> None:
        self._commands = commands
        self._skill_names = skill_names
        self._wd = wd
        self._db = db

    def _get_slug_completions(self, partial: str) -> Any:
        """Yield slug completions matching the partial input."""
        try:
            slugs = storage.list_conversation_slugs(self._db, limit=50)
        except Exception:
            return
        for slug, title in slugs:
            if slug.startswith(partial):
                display = title[:50] if title else ""
                yield Completion(slug, start_position=-len(partial), display_meta=display)

    def get_completions(self, document: Document, complete_event: Any) -> Any:
        text = document.text_before_cursor
        word = document.get_word_before_cursor(WORD=True)

        if text.lstrip().startswith("/") and " " not in text.strip():
            # Complete / commands and skills
            prefix = word.lstrip("/")
            for cmd in self._commands:
                if cmd.startswith(prefix):
                    yield Completion(f"/{cmd}", start_position=-len(word))
            for sname in self._skill_names:
                if sname.startswith(prefix):
                    yield Completion(f"/{sname}", start_position=-len(word))
        elif text.lstrip().startswith("/"):
            # Check if we're completing an argument after a slug-accepting command
            parts = text.lstrip().split(None, 2)
            cmd_name = parts[0].lstrip("/") if parts else ""
            if cmd_name in self._slug_commands and len(parts) <= 2:
                partial = parts[1] if len(parts) == 2 else ""
                yield from self._get_slug_completions(partial)
        elif "@" in word:
            # Complete file paths after @
            at_idx = word.rfind("@")
            partial = word[at_idx + 1 :]
            base = Path(self._wd)
            if "/" in partial:
                parent_str, stem = partial.rsplit("/", 1)
                parent = base / parent_str
            else:
                parent = base
                stem = partial
                parent_str = ""
            try:
                if parent.is_dir():
                    for entry in sorted(parent.iterdir()):
                        name = entry.name
                        if name.startswith("."):
                            continue
                        if name.lower().startswith(stem.lower()):
                            suffix = "/" if entry.is_dir() else ""
                            if parent_str:
                                full = f"@{parent_str}/{name}{suffix}"
                            else:
                                full = f"@{name}{suffix}"
                            yield Completion(full, start_position=-len(word))
            except OSError:
                pass

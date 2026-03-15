"""Integration tests: REPL slash-command completion sourced from shared metadata.

Verifies that the live REPL's tab-completion behavior is consistent with the
shared command engine metadata in commands.py, and that every advertised
subcommand has a corresponding handler in the REPL's inline dispatcher.

Addresses senior-review blocker 3 on PR #945: cli/commands.py and cli/repl.py
changes need REPL-level integration coverage per .claude/rules/ux-testing.md.
"""

from __future__ import annotations

import re
from pathlib import Path

from anteroom.cli.commands import (
    ALL_COMMAND_NAMES,
    COMMAND_DESCRIPTIONS,
    SUBCOMMAND_COMPLETIONS,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SRC_DIR = Path(__file__).resolve().parents[2] / "src" / "anteroom" / "cli"


def _read_repl_source() -> str:
    """Read repl.py source for static analysis."""
    return (_SRC_DIR / "repl.py").read_text()


def _extract_handler_subcommands(source: str, command: str) -> set[str]:
    """Extract subcommands handled in a specific command branch of the REPL dispatcher.

    Finds the ``elif cmd in ("/command", "/commands"):`` block and parses
    ``sub == "xxx"`` and ``sub in ("xxx", "yyy")`` patterns within it.
    Scoping to the specific handler prevents false positives from other
    command families that share the same ``sub`` variable name.
    """
    # Find the start of the command handler block
    singular = command
    plural = command + "s"
    # Try both orderings: ("/spaces", "/space") and ("/space", "/spaces")
    for pat in [
        rf'elif cmd in \("/{re.escape(plural)}".*?"/{re.escape(singular)}"\):',
        rf'elif cmd in \("/{re.escape(singular)}".*?"/{re.escape(plural)}"\):',
        rf'elif cmd == "/{re.escape(singular)}":',
    ]:
        match = re.search(pat, source)
        if match:
            break
    if not match:
        return set()

    # Extract from the start of this handler to the next top-level elif/else
    start = match.start()
    # Find the next elif at the same indentation level (command-family boundary)
    rest = source[start:]
    indent_match = re.match(r"(\s*)", rest)
    indent = indent_match.group(1) if indent_match else ""
    # Split at the next elif/else at the same indentation
    block_end = re.search(rf"\n{re.escape(indent)}(?:elif |else:)", rest[1:])
    block = rest[: block_end.start() + 1] if block_end else rest

    subs: set[str] = set()
    subs.update(re.findall(r'sub\s*==\s*["\'](\w[\w-]*)["\']', block))
    for m in re.finditer(r"sub\s+in\s+\(([^)]+)\)", block):
        inner = m.group(1)
        subs.update(re.findall(r'["\'](\w[\w-]*)["\']', inner))
    return subs


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSharedMetadataWiring:
    """Verify the REPL imports and uses shared metadata from commands.py."""

    def test_repl_imports_shared_metadata(self) -> None:
        """repl.py must import command metadata from the shared engine."""
        source = _read_repl_source()
        assert "from anteroom.cli.commands import" in source
        assert "ALL_COMMAND_NAMES" in source
        assert "COMMAND_DESCRIPTIONS" in source
        assert "SUBCOMMAND_COMPLETIONS" in source

    def test_all_command_names_have_descriptions(self) -> None:
        """Every command in ALL_COMMAND_NAMES must have a description."""
        missing = [name for name in ALL_COMMAND_NAMES if name not in COMMAND_DESCRIPTIONS]
        assert missing == [], f"Commands without descriptions: {missing}"

    def test_subcommand_completions_keys_are_valid_commands(self) -> None:
        """Every key in SUBCOMMAND_COMPLETIONS must be a known command."""
        for key in SUBCOMMAND_COMPLETIONS:
            assert key in ALL_COMMAND_NAMES, f"SUBCOMMAND_COMPLETIONS key {key!r} is not in ALL_COMMAND_NAMES"


class TestSpaceSubcommandConsistency:
    """Verify /space subcommand completions match what the REPL can handle."""

    def test_no_unreachable_space_subcommands(self) -> None:
        """Every /space subcommand in completion metadata must have a REPL handler.

        This is the integration-level guard that catches tab-completing
        subcommands the REPL cannot execute (e.g. the /space delete gap
        caught in the senior review).
        """
        source = _read_repl_source()
        handled = _extract_handler_subcommands(source, "space")
        # The completions metadata should be a subset of what the REPL handles
        completions = set(SUBCOMMAND_COMPLETIONS.get("space", []))
        unreachable = completions - handled
        assert unreachable == set(), (
            f"SUBCOMMAND_COMPLETIONS['space'] advertises subcommands the REPL "
            f"cannot execute: {unreachable}. Either add a handler in repl.py "
            f"or remove from SUBCOMMAND_COMPLETIONS until Phase 2 wiring."
        )

    def test_space_completions_do_not_include_delete(self) -> None:
        """Regression: /space delete must not be tab-completable until wired."""
        assert "delete" not in SUBCOMMAND_COMPLETIONS.get("space", [])

    def test_space_usage_text_matches_completions(self) -> None:
        """The REPL's /space usage text should list the same subcommands."""
        source = _read_repl_source()
        # Find all pipe-separated words in the Usage: /space [...] string.
        # The string spans multiple f-string lines so we extract all
        # word-like tokens between the opening [ and closing ].
        collapsed = source.replace("\n", " ")
        match = re.search(r"Usage:\s*/space\s*\[([^\]]+)\]", collapsed)
        assert match is not None, "Could not find /space usage string in repl.py"
        raw = match.group(1)
        usage_subs = set(re.findall(r"[a-z][\w-]*", raw))
        completion_subs = set(SUBCOMMAND_COMPLETIONS.get("space", []))
        # Usage may list fewer (no aliases like select/use), but should not
        # list any subcommand that isn't in the completion set or vice versa
        # for non-alias entries. Check that completions don't advertise
        # anything outside what usage+aliases cover.
        aliases = {"select", "use"}
        effective_completions = completion_subs - aliases
        extra_in_completions = effective_completions - usage_subs
        assert extra_in_completions == set(), (
            f"Completion metadata advertises subcommands not in usage text: {extra_in_completions}"
        )


class TestPackSubcommandConsistency:
    """Verify /pack subcommand completions are consistent."""

    def test_pack_completions_are_all_handled(self) -> None:
        """Every /pack subcommand in completion metadata must have a REPL handler."""
        source = _read_repl_source()
        handled = _extract_handler_subcommands(source, "pack")
        completions = set(SUBCOMMAND_COMPLETIONS.get("pack", []))
        unreachable = completions - handled
        assert unreachable == set(), (
            f"SUBCOMMAND_COMPLETIONS['pack'] advertises subcommands the REPL cannot execute: {unreachable}"
        )

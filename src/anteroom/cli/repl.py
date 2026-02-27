"""REPL loop and one-shot mode for the Anteroom CLI."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import signal
import sqlite3 as _sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from rich.markup import escape

from .. import __version__
from ..config import AppConfig, build_runtime_context
from ..db import init_db
from ..services import packs as packs_service
from ..services import storage
from ..services.agent_loop import _build_compaction_history, run_agent_loop
from ..services.ai_service import AIService, create_ai_service
from ..services.context_trust import sanitize_trust_tags, trusted_section_marker, untrusted_section_marker
from ..services.embeddings import get_effective_dimensions
from ..services.rewind import collect_file_paths
from ..services.rewind import rewind_conversation as rewind_service
from ..services.slug import is_valid_slug, suggest_unique_slug
from ..tools import ToolRegistry, register_default_tools
from . import renderer
from .instructions import (
    CONVENTIONS_TOKEN_WARNING_THRESHOLD,
    discover_conventions,
    estimate_tokens,
    find_global_instructions,
    find_project_instructions_path,
)
from .renderer import CHROME, GOLD, MUTED
from .skills import SkillRegistry

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"


def _add_signal_handler(loop: asyncio.AbstractEventLoop, sig: int, callback: Any) -> bool:
    """Add a signal handler, returning False on Windows where it's unsupported."""
    if _IS_WINDOWS:
        return False
    try:
        loop.add_signal_handler(sig, callback)
        return True
    except NotImplementedError:
        return False


def _remove_signal_handler(loop: asyncio.AbstractEventLoop, sig: int) -> None:
    """Remove a signal handler, no-op on Windows."""
    if _IS_WINDOWS:
        return
    try:
        loop.remove_signal_handler(sig)
    except NotImplementedError:
        pass


async def _watch_for_escape(cancel_event: asyncio.Event) -> None:
    """Watch for Escape key press during AI generation to cancel."""
    loop = asyncio.get_event_loop()

    if _IS_WINDOWS:
        import msvcrt

        def _poll() -> None:
            while not cancel_event.is_set():
                if msvcrt.kbhit():
                    ch = msvcrt.getch()
                    if ch == b"\x1b":
                        # Distinguish bare Escape from escape sequences (arrow keys, etc.)
                        time.sleep(0.05)
                        if not msvcrt.kbhit():
                            cancel_event.set()
                            return
                        # Consume the rest of the escape sequence
                        while msvcrt.kbhit():
                            msvcrt.getch()
                time.sleep(0.05)
    else:
        import select
        import termios
        import tty

        def _poll() -> None:
            fd = sys.stdin.fileno()
            if not os.isatty(fd):
                return
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                while not cancel_event.is_set():
                    ready, _, _ = select.select([sys.stdin], [], [], 0.1)
                    if ready:
                        ch = sys.stdin.read(1)
                        if ch == "\x1b":
                            # Distinguish bare Escape from escape sequences
                            more, _, _ = select.select([sys.stdin], [], [], 0.05)
                            if not more:
                                cancel_event.set()
                                return
                            # Consume the rest of the escape sequence
                            while True:
                                more, _, _ = select.select([sys.stdin], [], [], 0.01)
                                if more:
                                    sys.stdin.read(1)
                                else:
                                    break
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    try:
        await loop.run_in_executor(None, _poll)
    except asyncio.CancelledError:
        pass


_MAX_PASTE_DISPLAY_LINES = 6
_PASTE_THRESHOLD = 0.05  # 50ms; paste arrives faster than human typing


def _is_paste(last_text_change: float, threshold: float = _PASTE_THRESHOLD) -> bool:
    """Return True if Enter arrived fast enough after last buffer change to be paste."""
    return (time.monotonic() - last_text_change) < threshold


def _collapse_long_input(user_input: str) -> None:
    """Collapse long pasted input for terminal readability.

    Replaces the displayed multi-line input with the first few lines
    plus a "... (N more lines)" indicator. The actual content is
    preserved; only the visual display is truncated.
    """
    if not sys.stdout.isatty():
        return

    lines = user_input.split("\n")
    if len(lines) <= _MAX_PASTE_DISPLAY_LINES:
        return

    term_cols = shutil.get_terminal_size((80, 24)).columns
    usable = max(term_cols - 2, 10)  # 2 = "❯ " prompt width

    # Estimate terminal rows the prompt_toolkit input occupied
    total_rows = sum(max(1, (len(ln) + usable - 1) // usable) if ln else 1 for ln in lines)

    show = 3
    hidden = len(lines) - show

    # Move cursor up to input start and clear to end of screen.
    # Use renderer._stdout (real fd) to bypass patch_stdout() proxy
    # which corrupts raw ESC bytes.
    renderer._stdout.write(f"\033[{total_rows}A\033[J")
    # Reprint truncated with styled prompt via Rich (handles patch_stdout correctly)
    renderer.console.print(f"[bold cyan]❯[/] {escape(lines[0])}")
    for ln in lines[1:show]:
        renderer.console.print(f"  {escape(ln)}")
    renderer.console.print(f"  [dim]... ({hidden} more lines)[/]")
    renderer._stdout.flush()


_FILE_REF_RE = re.compile(r"@((?:[^\s\"']+|\"[^\"]+\"|'[^']+'))")


def _detect_git_branch() -> str | None:
    """Detect the current git branch, or None if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _load_conversation_messages(db: Any, conversation_id: str) -> list[dict[str, Any]]:
    """Load existing conversation messages into AI message format."""
    stored = storage.list_messages(db, conversation_id)
    messages: list[dict[str, Any]] = []
    for msg in stored:
        role = msg["role"]
        if role in ("user", "assistant", "system"):
            entry: dict[str, Any] = {"role": role, "content": msg["content"]}
            # Reconstruct tool_calls for assistant messages
            tool_calls = msg.get("tool_calls", [])
            if tool_calls and role == "assistant":
                entry["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["tool_name"],
                            "arguments": json.dumps(tc["input"]),
                        },
                    }
                    for tc in tool_calls
                ]
                # Add tool result messages
                messages.append(entry)
                for tc in tool_calls:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(tc.get("output", {})),
                        }
                    )
                continue
            messages.append(entry)
    return messages


# Context window management — overridden at runtime by config.cli thresholds
_CONTEXT_WARN_TOKENS = 80_000
_CONTEXT_AUTO_COMPACT_TOKENS = 100_000


_tiktoken_encoding = None


def _get_tiktoken_encoding():
    global _tiktoken_encoding
    if _tiktoken_encoding is None:
        try:
            import tiktoken

            _tiktoken_encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _tiktoken_encoding = False  # Signal fallback
    return _tiktoken_encoding


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Count tokens using tiktoken, falling back to char estimate."""
    enc = _get_tiktoken_encoding()

    total = 0
    for msg in messages:
        # Per-message overhead (~4 tokens for role/separators)
        total += 4
        content = msg.get("content", "")
        if isinstance(content, str):
            if enc:
                total += len(enc.encode(content, allowed_special="all"))
            else:
                total += len(content) // 4
        elif isinstance(content, list):
            for part in content:
                text = str(part) if isinstance(part, dict) else ""
                if enc:
                    total += len(enc.encode(text, allowed_special="all"))
                else:
                    total += len(text) // 4
        for tc in msg.get("tool_calls", []):
            if isinstance(tc, dict):
                func = tc.get("function", {})
                args = func.get("arguments", "")
                name = func.get("name", "")
                if enc:
                    total += len(enc.encode(args, allowed_special="all")) + len(enc.encode(name, allowed_special="all"))
                else:
                    total += (len(args) + len(name)) // 4
    return total


async def _check_for_update(current: str) -> str | None:
    """Check PyPI for a newer version. Returns latest if newer, else None."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "index",
            "versions",
            "anteroom",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode != 0:
            return None
        output = stdout.decode().strip()
        # Output format: "anteroom (X.Y.Z)"
        if "(" in output and ")" in output:
            latest = output.split("(")[1].split(")")[0].strip()
            from packaging.version import Version

            if Version(latest) > Version(current):
                return latest
    except Exception:
        if proc and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
    return None


def _picker_relative_time(ts: str) -> str:
    """Format a timestamp as a relative time string for the picker UI."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - dt
        if delta.days > 0:
            return f"{delta.days}d ago"
        hours = delta.seconds // 3600
        if hours > 0:
            return f"{hours}h ago"
        mins = delta.seconds // 60
        return f"{mins}m ago" if mins > 0 else "just now"
    except (ValueError, TypeError):
        return ""


def _picker_type_badge(conv_type: str) -> str:
    """Return a type badge string for non-chat conversation types."""
    return {"note": "[note]", "document": "[doc]"}.get(conv_type, "")


def _picker_format_preview(messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Format messages into styled fragments for the picker preview pane."""
    fragments: list[tuple[str, str]] = []
    recent = messages[-8:] if len(messages) > 8 else messages
    for msg in recent:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content or not isinstance(content, str):
            continue
        if role == "user":
            fragments.append(("class:preview.role-user", " You: "))
            text = content[:200].replace("\n", " ")
            if len(content) > 200:
                text += "..."
            fragments.append(("class:preview.content", f"{text}\n\n"))
        elif role == "assistant":
            fragments.append(("class:preview.role-ai", " AI: "))
            text = content[:300].replace("\n", " ")
            if len(content) > 300:
                text += "..."
            fragments.append(("class:preview.content", f"{text}\n\n"))
    if not fragments:
        fragments.append(("class:preview.empty", " (no messages)"))
    return fragments


def _resolve_conversation(db: Any, target: str) -> dict[str, Any] | None:
    """Resolve a target (list number, UUID, or slug) to a conversation dict."""
    if target.isdigit():
        idx = int(target) - 1
        convs = storage.list_conversations(db, limit=20)
        if 0 <= idx < len(convs):
            return storage.get_conversation(db, convs[idx]["id"])
        return None
    return storage.get_conversation(db, target)


def _restore_working_dir(
    conv: dict[str, Any],
    tool_registry: Any,
    current_working_dir: str,
) -> str:
    """Restore working directory from a resumed conversation.

    Returns the effective working directory (stored or current fallback).
    """
    stored_dir = conv.get("working_dir")
    if not stored_dir:
        return current_working_dir
    # Resolve symlinks for consistent validation
    stored_dir = os.path.realpath(stored_dir)
    if not os.path.isdir(stored_dir):
        logger.warning("Stored working_dir %s no longer exists, using current", stored_dir)
        renderer.console.print(
            f"[{MUTED}]Note: original directory {stored_dir} no longer exists, using current[/{MUTED}]"
        )
        return current_working_dir
    # Block sensitive system directories
    _blocked_prefixes = ("/proc/", "/sys/", "/dev/")
    if any(stored_dir.startswith(p) or stored_dir == p.rstrip("/") for p in _blocked_prefixes):
        logger.warning("Blocked unsafe stored working_dir: %s", stored_dir)
        return current_working_dir
    # Re-scope tools to the stored directory
    from ..tools import bash, edit, glob_tool, grep, read, write

    for module in [read, write, edit, bash, glob_tool, grep]:
        if hasattr(module, "set_working_dir"):
            module.set_working_dir(stored_dir)
    tool_registry._working_dir = stored_dir
    return stored_dir


def _show_resume_info(db: Any, conv: dict[str, Any], ai_messages: list[dict[str, Any]]) -> None:
    """Display resume header with last exchange context."""
    stored = storage.list_messages(db, conv["id"])
    title = conv.get("title", "Untitled")
    renderer.console.print(f"[{CHROME}]Resumed: {title} ({len(ai_messages)} messages)[/{CHROME}]")
    renderer.render_conversation_recap(stored)


def _show_usage_stats(db: Any, config: Any) -> None:
    """Display token usage statistics for today, this week, and this month."""
    usage_cfg = config.cli.usage
    now = datetime.now(timezone.utc)
    periods = [
        ("Today", (now - timedelta(days=1)).isoformat()),
        ("This week", (now - timedelta(days=usage_cfg.week_days)).isoformat()),
        ("This month", (now - timedelta(days=usage_cfg.month_days)).isoformat()),
        ("All time", None),
    ]

    renderer.console.print("\n[bold]Token Usage[/bold]")

    for label, since in periods:
        stats = storage.get_usage_stats(db, since=since)
        if not stats:
            renderer.console.print(f"\n  [{MUTED}]{label}: no usage data[/{MUTED}]")
            continue

        total_prompt = sum(s.get("prompt_tokens", 0) or 0 for s in stats)
        total_completion = sum(s.get("completion_tokens", 0) or 0 for s in stats)
        total_tokens = sum(s.get("total_tokens", 0) or 0 for s in stats)
        total_messages = sum(s.get("message_count", 0) or 0 for s in stats)

        # Calculate cost
        total_cost = 0.0
        for s in stats:
            model = s.get("model", "") or ""
            prompt_t = s.get("prompt_tokens", 0) or 0
            completion_t = s.get("completion_tokens", 0) or 0
            costs = usage_cfg.model_costs.get(model, {})
            input_rate = costs.get("input", 0.0)
            output_rate = costs.get("output", 0.0)
            total_cost += (prompt_t / 1_000_000) * input_rate + (completion_t / 1_000_000) * output_rate

        renderer.console.print(f"\n  [bold]{label}[/bold] ({total_messages} messages)")
        renderer.console.print(f"    Prompt:     {total_prompt:>12,} tokens")
        renderer.console.print(f"    Completion: {total_completion:>12,} tokens")
        renderer.console.print(f"    Total:      {total_tokens:>12,} tokens")
        if total_cost > 0:
            renderer.console.print(f"    Est. cost:  ${total_cost:>11,.4f}")

        # Per-model breakdown if multiple models
        if len(stats) > 1:
            renderer.console.print(f"    [{MUTED}]By model:[/{MUTED}]")
            for s in stats:
                model = s.get("model", "unknown") or "unknown"
                t = s.get("total_tokens", 0) or 0
                renderer.console.print(f"      {model}: {t:,} tokens")

    renderer.console.print()


_EXIT_COMMANDS = frozenset({"/quit", "/exit"})


async def _drain_input_to_msg_queue(
    input_queue: asyncio.Queue[str],
    msg_queue: asyncio.Queue[dict[str, Any]],
    working_dir: str,
    db: Any,
    conversation_id: str,
    cancel_event: asyncio.Event,
    exit_flag: asyncio.Event,
    warn_callback: Any | None = None,
    identity_kwargs: dict[str, str | None] | None = None,
    file_max_chars: int = 100_000,
    skill_registry: Any | None = None,
) -> None:
    """Drain input_queue into msg_queue, filtering out / commands.

    - /quit and /exit trigger cancel_event and exit_flag
    - Skill invocations are expanded and queued as user messages
    - Other / commands are ignored with a warning
    - Normal text is expanded and queued as user messages
    """
    while not input_queue.empty():
        try:
            queued_text = input_queue.get_nowait()
            if queued_text.startswith("/"):
                cmd = queued_text.lower().split()[0]
                if cmd in _EXIT_COMMANDS:
                    cancel_event.set()
                    exit_flag.set()
                    break
                # Try expanding as a skill invocation
                if skill_registry:
                    is_skill, skill_prompt = skill_registry.resolve_input(queued_text)
                    if is_skill:
                        q_expanded = _expand_file_references(skill_prompt, working_dir, file_max_chars=file_max_chars)
                        storage.create_message(db, conversation_id, "user", q_expanded, **(identity_kwargs or {}))
                        await msg_queue.put({"role": "user", "content": q_expanded})
                        continue
                if warn_callback:
                    warn_callback(cmd)
                continue
            q_expanded = _expand_file_references(queued_text, working_dir, file_max_chars=file_max_chars)
            storage.create_message(db, conversation_id, "user", q_expanded, **(identity_kwargs or {}))
            await msg_queue.put({"role": "user", "content": q_expanded})
        except asyncio.QueueEmpty:
            break


def _expand_file_references(text: str, working_dir: str, file_max_chars: int = 100_000) -> str:
    """Expand @path references in user input.

    @file.py      -> includes file contents inline
    @src/          -> includes directory listing
    @"path with spaces/file.py" -> handles quoted paths
    """
    from ..tools.security import validate_path as _validate_ref_path

    def _replace(match: re.Match[str]) -> str:
        raw_path = match.group(1).strip("\"'")
        validated, error = _validate_ref_path(raw_path, working_dir)
        if error:
            return match.group(0)  # Skip blocked paths silently
        resolved = Path(validated)

        if resolved.is_file():
            try:
                content = resolved.read_text(encoding="utf-8", errors="replace")
                if len(content) > file_max_chars:
                    content = content[:file_max_chars] + "\n... (truncated)"
                return f'\n<file path="{raw_path}">\n{content}\n</file>\n'
            except OSError:
                return match.group(0)
        elif resolved.is_dir():
            try:
                entries = sorted(resolved.iterdir())
                listing = []
                for entry in entries[:200]:
                    suffix = "/" if entry.is_dir() else ""
                    listing.append(f"  {entry.name}{suffix}")
                content = "\n".join(listing)
                return f'\n<directory path="{raw_path}">\n{content}\n</directory>\n'
            except OSError:
                return match.group(0)
        else:
            return match.group(0)

    return _FILE_REF_RE.sub(_replace, text)


def _detect_project_context(working_dir: str) -> str:
    """Detect project context from the working directory for the system prompt."""
    from pathlib import Path

    wd = Path(working_dir)
    lines: list[str] = []

    # Git awareness
    git_dir = wd / ".git"
    if git_dir.exists():
        lines.append("This is a git repository.")

    # Project type detection
    markers = {
        "pyproject.toml": "Python project (pyproject.toml)",
        "setup.py": "Python project (setup.py)",
        "package.json": "Node.js project (package.json)",
        "Cargo.toml": "Rust project (Cargo.toml)",
        "go.mod": "Go project (go.mod)",
        "Makefile": "Has Makefile",
    }
    for marker, desc in markers.items():
        if (wd / marker).exists():
            lines.append(desc)
            break

    # Common directories
    dirs = [d.name for d in wd.iterdir() if d.is_dir() and not d.name.startswith(".")]
    notable = [d for d in dirs if d in ("src", "tests", "test", "lib", "docs", "scripts", "cmd", "pkg", "internal")]
    if notable:
        lines.append(f"Key directories: {', '.join(sorted(notable))}")

    return "\n".join(lines)


def _build_introspect_instructions_info(working_dir: str) -> dict[str, Any]:
    """Build a summary of loaded instructions for the introspect tool."""
    from .instructions import estimate_tokens, find_global_instructions_path, find_project_instructions_path

    sources: list[dict[str, Any]] = []
    total_tokens = 0

    global_result = find_global_instructions_path()
    if global_result:
        path, content = global_result
        tokens = estimate_tokens(content)
        sources.append({"path": str(path), "source": "global", "estimated_tokens": tokens})
        total_tokens += tokens

    project_result = find_project_instructions_path(working_dir)
    if project_result:
        path, content = project_result
        tokens = estimate_tokens(content)
        sources.append({"path": str(path), "source": "project", "estimated_tokens": tokens})
        total_tokens += tokens

    return {"sources": sources, "total_tokens": total_tokens}


def _build_system_prompt(
    config: AppConfig,
    working_dir: str,
    instructions: str | None,
    builtin_tools: list[str] | None = None,
    mcp_servers: dict[str, Any] | None = None,
    project_instructions: str | None = None,
) -> str:
    runtime_ctx = build_runtime_context(
        model=config.ai.model,
        builtin_tools=builtin_tools,
        mcp_servers=mcp_servers,
        interface="cli",
        working_dir=working_dir,
    )
    parts = [trusted_section_marker() + runtime_ctx]

    # Project context
    project_ctx = _detect_project_context(working_dir)
    if project_ctx:
        parts.append(f"\n<project_context>\nWorking directory: {working_dir}\n{project_ctx}\n</project_context>")
    else:
        parts.append(f"\n<project_context>\nWorking directory: {working_dir}\n</project_context>")

    if project_instructions:
        parts.append(f"\n{project_instructions}")
    if instructions:
        parts.append(f"\n{instructions}")
    return "\n".join(parts)


def _identity_kwargs(config: AppConfig) -> dict[str, str | None]:
    """Extract user_id/user_display_name from config identity, or empty dict."""
    if config.identity:
        return {"user_id": config.identity.user_id, "user_display_name": config.identity.display_name}
    return {"user_id": None, "user_display_name": None}


async def _check_project_trust(
    file_path: Path,
    content: str,
    trust_project: bool = False,
    data_dir: Path | None = None,
) -> str | None:
    """Gate project-level ANTEROOM.md behind user trust consent.

    Returns the file content if trusted, or None if denied/skipped.
    Caller must check no_project_context before calling this function.
    """
    from ..services.trust import check_trust, compute_content_hash, save_trust_decision

    folder_path = str(file_path.parent)
    content_hash = compute_content_hash(content)

    if trust_project:
        save_trust_decision(folder_path, content_hash, data_dir=data_dir)
        return content

    status = check_trust(folder_path, content_hash, data_dir=data_dir)

    if status == "trusted":
        return content

    # Both "changed" and "untrusted" require user consent
    file_size = len(content.encode("utf-8"))

    if status == "changed":
        renderer.console.print("\n[yellow bold]Warning:[/yellow bold] ANTEROOM.md has changed since last trusted.")
    else:
        renderer.console.print(
            "\n[yellow bold]Warning:[/yellow bold] This project contains an ANTEROOM.md "
            "file that will be loaded into the AI context."
        )

    renderer.console.print(f"  Path: [{MUTED}]{file_path}[/{MUTED}]")
    renderer.console.print(f"  Size: [{MUTED}]{file_size:,} bytes[/{MUTED}]")

    try:
        from prompt_toolkit import PromptSession as _TrustSession

        _trust_session = _TrustSession()

        while True:
            answer = await _trust_session.prompt_async(
                "  [y] Trust this folder  [r] Trust parent  [v] View  [n] Skip: "
            )
            choice = answer.strip().lower()

            if choice in ("y", "yes"):
                save_trust_decision(folder_path, content_hash, data_dir=data_dir)
                renderer.console.print(f"  [{MUTED}]Trusted: {folder_path}[/{MUTED}]\n")
                return content

            if choice in ("r", "recursive"):
                parent_path = str(file_path.parent.parent)
                save_trust_decision(parent_path, content_hash, recursive=True, data_dir=data_dir)
                renderer.console.print(f"  [{MUTED}]Trusted (recursive): {parent_path}[/{MUTED}]\n")
                return content

            if choice in ("v", "view"):
                renderer.console.print(f"\n[dim]{'─' * 60}[/dim]")
                # Limit display to prevent terminal flooding
                lines = content.splitlines()
                if len(lines) > 50:
                    for line in lines[:50]:
                        renderer.console.print(f"  [dim]{line}[/dim]")
                    renderer.console.print(f"  [dim]... ({len(lines) - 50} more lines)[/dim]")
                else:
                    for line in lines:
                        renderer.console.print(f"  [dim]{line}[/dim]")
                renderer.console.print(f"[dim]{'─' * 60}[/dim]\n")
                continue

            if choice in ("n", "no", ""):
                renderer.console.print(f"  [{MUTED}]Skipped: project context not loaded[/{MUTED}]\n")
                return None

    except (EOFError, KeyboardInterrupt):
        renderer.console.print(f"\n  [{MUTED}]Skipped: project context not loaded[/{MUTED}]\n")
        return None


async def _load_instructions_with_trust(
    working_dir: str,
    trust_project: bool = False,
    no_project_context: bool = False,
    data_dir: Path | None = None,
) -> str | None:
    """Load global + project instructions with trust gating on project files."""
    parts: list[str] = []

    # Global instructions (~/.anteroom/ANTEROOM.md) are loaded unconditionally.
    # They share the same trust boundary as the config file itself — if an attacker
    # can write to ~/.anteroom/, they already control the app configuration.
    global_inst = find_global_instructions()
    if global_inst:
        parts.append(f"# Global Instructions\n{global_inst}")

    if not no_project_context:
        result = find_project_instructions_path(working_dir)
        if result is not None:
            file_path, content = result
            trusted_content = await _check_project_trust(
                file_path,
                content,
                trust_project=trust_project,
                data_dir=data_dir,
            )
            if trusted_content is not None:
                tokens = estimate_tokens(trusted_content)
                if tokens > CONVENTIONS_TOKEN_WARNING_THRESHOLD:
                    renderer.console.print(
                        f"  [yellow]Warning: ANTEROOM.md is ~{tokens:,} tokens "
                        f"(threshold: {CONVENTIONS_TOKEN_WARNING_THRESHOLD:,}). "
                        f"Large files reduce prompt effectiveness.[/yellow]\n"
                    )
                parts.append(f"# Project Instructions\n{trusted_content}")

    if not parts:
        return None
    return "\n\n".join(parts)


async def _run_mcp_startup_live(
    mcp_manager: Any,
    mcp_servers: list[Any],
    statuses: dict[str, dict[str, Any]],
) -> None:
    """Connect MCP servers in parallel, printing each result as it resolves."""
    server_names = [s.name for s in mcp_servers]
    pending = set(server_names)

    def _on_status(name: str, status: dict[str, Any]) -> None:
        statuses[name] = status
        st = status.get("status", "unknown")
        if st == "connected":
            pending.discard(name)
            count = status.get("tool_count", 0)
            renderer.console.print(f"  [green]✓[/green] [{MUTED}]{name} ({count} tools)[/{MUTED}]")
        elif st == "error":
            pending.discard(name)
            err = status.get("error_message", "failed")
            if len(err) > 40:
                err = err[:37] + "..."
            renderer.console.print(f"  [red]✗[/red] [{MUTED}]{name} ({err})[/{MUTED}]")

    try:
        with renderer.startup_step(f"Connecting {len(server_names)} MCP server(s)..."):
            await mcp_manager.startup(status_callback=_on_status)
    except Exception as e:
        logger.warning("MCP startup failed: %s", e)

    # Print summary
    connected = sum(1 for s in statuses.values() if s.get("status") == "connected")
    failed = sum(1 for s in statuses.values() if s.get("status") == "error")
    total_tools = sum(s.get("tool_count", 0) for s in statuses.values())
    if connected or failed:
        parts = [f"{connected} server(s)", f"{total_tools} tools"]
        if failed:
            parts.append(f"{failed} failed")
        renderer.console.print(f"  [{MUTED}]MCP: {', '.join(parts)}[/{MUTED}]\n")


async def run_cli(
    config: AppConfig,
    prompt: str | None = None,
    no_tools: bool = False,
    continue_last: bool = False,
    conversation_id: str | None = None,
    project_id: str | None = None,
    trust_project: bool = False,
    no_project_context: bool = False,
    plan_mode: bool = False,
) -> None:
    """Main entry point for CLI mode."""
    working_dir = os.getcwd()

    # Init DB (same as web UI)
    db_path = config.app.data_dir / "chat.db"
    config.app.data_dir.mkdir(parents=True, exist_ok=True)
    vec_dims = get_effective_dimensions(config)

    # Derive encryption key if encryption at rest is enabled
    encryption_key: bytes | None = None
    if config.storage.encrypt_at_rest:
        from ..services.encryption import derive_db_key, is_sqlcipher_available

        if not is_sqlcipher_available():
            renderer.render_error(
                "Encryption at rest enabled but sqlcipher3 not installed. Install with: pip install sqlcipher3"
            )
            return
        pk = config.identity.private_key if config.identity else ""
        if not pk:
            renderer.render_error("Encryption at rest requires an identity key. Run: aroom init")
            return
        encryption_key = derive_db_key(pk)

    db = init_db(db_path, vec_dimensions=vec_dims, encryption_key=encryption_key)

    # Load named project if specified via --project
    _project: dict[str, Any] | None = None
    _project_instructions: str | None = None
    if project_id:
        _project = storage.get_project(db, project_id)
        if _project:
            if _project.get("instructions"):
                _project_instructions = _project["instructions"]
            if _project.get("model") and not config.ai.model:
                config.ai.model = _project["model"]
            renderer.console.print(f"[dim]Project:[/dim] {_project['name']}")
        else:
            renderer.render_error(f"Project ID {project_id} not found in database")
            project_id = None

    # Clean up empty conversations
    try:
        storage.delete_empty_conversations(db, config.app.data_dir)
    except Exception:
        logger.debug("Failed to clean up empty conversations")

    # Register built-in tools
    tool_registry = ToolRegistry()
    if config.cli.builtin_tools and not no_tools:
        register_default_tools(tool_registry, working_dir=working_dir)

    # Prepare MCP manager (startup deferred to background after REPL prompt appears)
    mcp_manager = None
    _mcp_statuses: dict[str, dict[str, Any]] = {}
    if config.mcp_servers:
        try:
            from ..services.mcp_manager import McpManager

            mcp_manager = McpManager(config.mcp_servers, tool_warning_threshold=config.mcp_tool_warning_threshold)
        except Exception as e:
            logger.warning("Failed to initialize MCP manager: %s", e)
            renderer.render_error(f"MCP init failed: {e}")

    # Initialize audit writer
    from ..services.audit import AuditEntry, create_audit_writer

    _private_key = config.identity.private_key if config.identity else ""
    audit_writer = create_audit_writer(config, private_key_pem=_private_key)

    # Start retention worker if configured
    retention_worker = None
    if config.storage.retention_days > 0:
        from ..services.retention import RetentionWorker

        retention_worker = RetentionWorker(
            db=db,
            data_dir=config.app.data_dir,
            retention_days=config.storage.retention_days,
            check_interval=config.storage.retention_check_interval,
            purge_attachments=config.storage.purge_attachments,
        )
        retention_worker.start()

    # Set up safety configuration and confirmation callback
    from ..tools.safety import SafetyVerdict

    _approval_lock = asyncio.Lock()

    async def _confirm_destructive(verdict: SafetyVerdict) -> bool:
        from rich.markup import escape

        async with _approval_lock:
            renderer.console.print(f"\n[yellow bold]Warning:[/yellow bold] {verdict.reason}")
            if verdict.details.get("command"):
                renderer.console.print(f"  Command: [{MUTED}]{verdict.details['command']}[/{MUTED}]")
            elif verdict.details.get("path"):
                renderer.console.print(f"  Path: [{MUTED}]{verdict.details['path']}[/{MUTED}]")
            try:
                from prompt_toolkit import PromptSession as _ConfirmSession

                _confirm_session = _ConfirmSession()
                answer = await _confirm_session.prompt_async(
                    "  [y] Allow once  [s] Allow for session  [a] Allow always  [n] Deny: "
                )
                choice = answer.strip().lower()
                if choice in ("a", "always"):
                    tool_registry.grant_session_permission(verdict.tool_name)
                    _persist_allowed_tool(verdict.tool_name)
                    renderer.console.print(f"  [{MUTED}]✓ Allowed: {escape(verdict.tool_name)} (always)[/{MUTED}]\n")
                    return True
                if choice in ("s", "session"):
                    tool_registry.grant_session_permission(verdict.tool_name)
                    renderer.console.print(f"  [{MUTED}]✓ Allowed: {escape(verdict.tool_name)} (session)[/{MUTED}]\n")
                    return True
                if choice in ("y", "yes"):
                    renderer.console.print(f"  [{MUTED}]✓ Allowed: {escape(verdict.tool_name)} (once)[/{MUTED}]\n")
                    return True
                renderer.console.print(f"  [{MUTED}]✗ Denied: {escape(verdict.tool_name)}[/{MUTED}]\n")
                return False
            except (EOFError, KeyboardInterrupt):
                renderer.console.print(f"  [{MUTED}]✗ Denied: {escape(verdict.tool_name)}[/{MUTED}]\n")
                return False

    def _persist_allowed_tool(tool_name: str) -> None:
        """Append a tool to safety.allowed_tools in the config file."""
        try:
            from ..config import write_allowed_tool

            write_allowed_tool(tool_name)
        except Exception as e:
            renderer.console.print(f"[{MUTED}]Could not persist preference: {e}[/{MUTED}]")

    tool_registry.set_safety_config(config.safety, working_dir=working_dir)
    tool_registry.set_confirm_callback(_confirm_destructive)

    # Set up ask_user callback for mid-turn questions
    async def _ask_user_callback(question: str, options: list[str] | None = None) -> str:
        await renderer.stop_thinking()
        renderer.console.print(f"\n[yellow bold]Question:[/yellow bold] {question}")
        try:
            from prompt_toolkit import PromptSession as _AskSession

            _ask_session = _AskSession()

            if options:
                for i, opt in enumerate(options, 1):
                    renderer.console.print(f"  [{MUTED}]{i}.[/{MUTED}] {opt}")
                hint = "(enter number to select, or type a custom answer; esc to cancel)"
                renderer.console.print(f"  [{MUTED}]{hint}[/{MUTED}]")
                answer = await _ask_session.prompt_async("  Choice: ")
                answer = answer.strip()
                if answer.isdigit():
                    idx = int(answer)
                    if 1 <= idx <= len(options):
                        answer = options[idx - 1]
                    else:
                        renderer.console.print(f"  [{MUTED}]Invalid choice #{idx}, using as freeform answer[/{MUTED}]")
            else:
                renderer.console.print(f"  [{MUTED}](esc to cancel)[/{MUTED}]")
                answer = await _ask_session.prompt_async("  Answer: ")
                answer = answer.strip()

            renderer.console.print()
            renderer.start_thinking()
            return answer
        except (EOFError, KeyboardInterrupt):
            renderer.console.print(f"  [{MUTED}](cancelled)[/{MUTED}]\n")
            renderer.start_thinking()
            return ""

    # Build unified tool executor
    _subagent_counter = 0
    _active_cancel_event: list[asyncio.Event | None] = [None]

    from ..services.tool_rate_limit import ToolRateLimiter
    from ..tools.subagent import SubagentLimiter

    _sa_config = config.safety.subagent
    _subagent_limiter = SubagentLimiter(
        max_concurrent=_sa_config.max_concurrent,
        max_total=_sa_config.max_total,
    )

    _rate_limiter = ToolRateLimiter(config.safety.tool_rate_limit)
    tool_registry.set_rate_limiter(_rate_limiter)

    # Construct DLP scanner if configured
    _dlp_scanner = None
    if config.safety.dlp.enabled:
        from ..services.dlp import DlpScanner

        _dlp_scanner = DlpScanner(config.safety.dlp)

    # Construct injection detector if configured
    _injection_detector = None
    if config.safety.prompt_injection.enabled:
        from ..services.injection_detector import InjectionDetector

        _injection_detector = InjectionDetector(config.safety.prompt_injection)

    async def _cli_event_sink(agent_id: str, event: Any) -> None:
        """Render sub-agent progress events in the CLI."""
        kind = event.kind
        data = event.data
        if kind == "subagent_start":
            renderer.render_subagent_start(
                agent_id, data.get("prompt", ""), data.get("model", ""), data.get("depth", 1)
            )
        elif kind == "tool_call_start":
            renderer.render_subagent_tool(agent_id, data.get("tool_name", ""), data.get("arguments"))
        elif kind == "subagent_end":
            renderer.render_subagent_end(
                agent_id, data.get("elapsed_seconds", 0), data.get("tool_calls", []), data.get("error")
            )

    def _audit_tool_call(
        writer: Any, tool_name: str, arguments: dict[str, Any], result: dict[str, Any], conv_id: str | None
    ) -> None:
        if writer is None or not writer.enabled:
            return
        approval = result.get("_approval_decision", "auto") if isinstance(result, dict) else "auto"
        status = "error" if isinstance(result, dict) and result.get("error") else "success"
        writer.emit(
            AuditEntry.create(
                "tool_calls.executed",
                "info",
                conversation_id=conv_id or "",
                tool_name=tool_name,
                user_id=config.identity.user_id if config.identity else "",
                details={
                    "status": status,
                    "approval_decision": approval,
                    "tool_input": str(arguments)[:500],
                    "tool_output": str(result)[:500] if result else "",
                },
            )
        )

    # Mutable ref so tool_executor can queue skill prompts into the per-turn msg_queue
    _active_msg_queue: list[asyncio.Queue[dict[str, Any]] | None] = [None]

    async def tool_executor(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal _subagent_counter
        if tool_name == "invoke_skill":
            # Apply safety tier check (invoke_skill is READ tier but respects denied_tools)
            verdict = tool_registry.check_safety(tool_name, arguments)
            if verdict and verdict.needs_approval:
                if verdict.hard_denied:
                    return {"error": f"Tool '{tool_name}' is blocked by configuration", "safety_blocked": True}
                confirmed = await _confirm_destructive(verdict)
                if not confirmed:
                    return {"error": "Operation denied by user", "exit_code": -1}
            skill_name = arguments.get("skill_name", "")
            skill = skill_registry.get(skill_name) if skill_registry else None
            if not skill:
                return {"error": f"Unknown skill: {skill_name}"}
            queue = _active_msg_queue[0]
            if queue is None:
                return {"error": "Skill invocation unavailable in this context"}
            args = arguments.get("args", "")
            prompt = skill.prompt
            if args:
                # Trust boundary: args originate from the LLM, not the user.
                # Truncate and delimit to limit injection surface; the skill
                # prompt itself is from trusted local YAML files.
                from .skills import _expand_args

                args = args[:2000]
                prompt = _expand_args(prompt, f"<skill_args>{args}</skill_args>")
            await queue.put({"role": "user", "content": prompt})
            return {"status": "skill_invoked", "skill": skill_name}
        if tool_name == "run_agent":
            _subagent_counter += 1
            arguments = {
                **arguments,
                "_ai_service": ai_service,
                "_tool_registry": tool_registry,
                "_mcp_manager": mcp_manager,
                "_cancel_event": _active_cancel_event[0],
                "_depth": 0,
                "_agent_id": f"agent-{_subagent_counter}",
                "_event_sink": _cli_event_sink,
                "_limiter": _subagent_limiter,
                "_confirm_callback": _confirm_destructive,
                "_config": _sa_config,
            }
        elif tool_name == "ask_user":
            arguments = {**arguments, "_ask_callback": _ask_user_callback}
        elif tool_name == "introspect":
            arguments = {
                **arguments,
                "_config": config,
                "_mcp_manager": mcp_manager,
                "_tool_registry": tool_registry,
                "_skill_registry": skill_registry,
                "_instructions_info": _introspect_instructions_info,
                "_tools_openai": tools_openai,
                "_working_dir": working_dir,
            }
        if tool_registry.has_tool(tool_name):
            result = await tool_registry.call_tool(tool_name, arguments)
            _audit_tool_call(audit_writer, tool_name, arguments, result, conversation_id)
            return result
        if mcp_manager:
            # MCP tools bypass ToolRegistry — apply safety gate here
            verdict = tool_registry.check_safety(tool_name, arguments)
            if verdict and verdict.needs_approval:
                if verdict.hard_denied:
                    return {"error": f"Tool '{tool_name}' is blocked by configuration", "safety_blocked": True}
                confirmed = await _confirm_destructive(verdict)
                if not confirmed:
                    return {"error": "Operation denied by user", "exit_code": -1}
            # Rate limiting for MCP tools (built-in tools are checked in call_tool)
            if _rate_limiter:
                rl_v = _rate_limiter.check(tool_name)
                if rl_v and rl_v.exceeded and _rate_limiter.config.action == "block":
                    return {"error": rl_v.reason, "safety_blocked": True, "rate_limited": True}
            result = await mcp_manager.call_tool(tool_name, arguments)
            if _rate_limiter:
                _rate_limiter.record_call(success="error" not in result)
            _audit_tool_call(audit_writer, tool_name, arguments, result, conversation_id)
            return result
        raise ValueError(f"Unknown tool: {tool_name}")

    # Build unified tool list (exclude canvas tools — they require web UI context)
    _canvas_tool_names = {"create_canvas", "update_canvas", "patch_canvas"}
    tools_openai: list[dict[str, Any]] = []
    tools_openai.extend(
        t for t in tool_registry.get_openai_tools() if t.get("function", {}).get("name") not in _canvas_tool_names
    )
    if mcp_manager:
        mcp_tools = mcp_manager.get_openai_tools()
        if mcp_tools:
            tools_openai.extend(mcp_tools)

    # Cap tools to provider limit, prioritising built-in tools
    from ..tools import cap_tools

    tools_openai = cap_tools(tools_openai, set(tool_registry.list_tools()), limit=config.ai.max_tools)
    if config.safety.read_only:
        from ..tools.tiers import filter_read_only_tools

        tools_openai = filter_read_only_tools(tools_openai, config.safety.tool_tiers)
    tools_openai_or_none = tools_openai if tools_openai else None

    # Load skills (before system prompt so skill descriptions can be injected)
    skill_registry = SkillRegistry()
    skill_registry.load(working_dir)
    for warn in skill_registry.load_warnings:
        renderer.console.print(f"[yellow]Skill warning:[/yellow] {warn}")

    # Load ANTEROOM.md instructions (with trust gating for project-level files)
    instructions = await _load_instructions_with_trust(
        working_dir,
        trust_project=trust_project,
        no_project_context=no_project_context,
        data_dir=config.app.data_dir,
    )
    # Build introspect instructions info for the introspect tool
    _introspect_instructions_info = _build_introspect_instructions_info(working_dir)

    mcp_statuses = mcp_manager.get_server_statuses() if mcp_manager else None
    extra_system_prompt = _build_system_prompt(
        config,
        working_dir,
        instructions,
        builtin_tools=tool_registry.list_tools(),
        mcp_servers=mcp_statuses,
        project_instructions=_project_instructions,
    )

    # Inject canary token into trusted section (before untrusted marker)
    if _injection_detector is not None and _injection_detector.enabled:
        _canary_seg = _injection_detector.canary_prompt_segment()
        if _canary_seg:
            extra_system_prompt += _canary_seg

    # Structural separation: everything below is external/auto-generated context
    extra_system_prompt += untrusted_section_marker()

    # Inject codebase index (tree-sitter symbol map) if enabled
    try:
        from ..services.codebase_index import create_index_service

        _index_service = create_index_service(config)
        if _index_service:
            _index_map = _index_service.get_map(working_dir, token_budget=config.codebase_index.map_tokens)
            if _index_map:
                extra_system_prompt += "\n" + _index_map
    except Exception:
        logger.debug("Codebase index unavailable, continuing without it", exc_info=True)

    # Inject skill catalog and invoke_skill tool only when auto-invoke is enabled
    if config.cli.skills.auto_invoke:
        skill_descs = skill_registry.get_skill_descriptions()
        if skill_descs:
            skill_lines = [
                "\n<available_skills>",
                "The following skills are available. When the user's request clearly matches a skill, "
                "use the invoke_skill tool to run it.",
            ]
            for name, desc in skill_descs:
                skill_lines.append(f"- {name}: {desc}")
            skill_lines.append("</available_skills>")
            extra_system_prompt += "\n".join(skill_lines)
    if config.cli.skills.auto_invoke:
        invoke_def = skill_registry.get_invoke_skill_definition()
        if invoke_def:
            tools_openai.append(invoke_def)
            tools_openai_or_none = tools_openai if tools_openai else None

    ai_service = create_ai_service(config.ai)

    # Validate connection before proceeding
    with renderer.startup_step("Validating AI connection..."):
        valid, message, _ = await ai_service.validate_connection()
    if not valid:
        renderer.render_error(f"Cannot connect to AI service: {message}")
        renderer.console.print(f"  [{MUTED}]base_url: {config.ai.base_url}[/{MUTED}]")
        renderer.console.print(f"  [{MUTED}]model: {config.ai.model}[/{MUTED}]")
        renderer.console.print(f"  [{MUTED}]Check ~/.anteroom/config.yaml[/{MUTED}]\n")
        if mcp_manager:
            await mcp_manager.shutdown()
        db.close()
        return

    all_tool_names = tool_registry.list_tools()
    if mcp_manager:
        all_tool_names.extend(t["name"] for t in mcp_manager.get_all_tools())

    # Resolve conversation to continue
    resume_conversation_id: str | None = None
    if conversation_id:
        resume_conversation_id = conversation_id
    elif continue_last:
        convs = storage.list_conversations(db, limit=1)
        if convs:
            resume_conversation_id = convs[0]["id"]

    # Construct output content filter if configured (needs system prompt for leak detection)
    _output_filter = None
    if config.safety.output_filter.enabled:
        from ..services.output_filter import OutputContentFilter

        _output_filter = OutputContentFilter(config.safety.output_filter, system_prompt=extra_system_prompt)

    try:
        if prompt:
            await _run_one_shot(
                config=config,
                db=db,
                ai_service=ai_service,
                tool_executor=tool_executor,
                tools_openai=tools_openai_or_none,
                extra_system_prompt=extra_system_prompt,
                prompt=prompt,
                working_dir=working_dir,
                tool_registry=tool_registry,
                resume_conversation_id=resume_conversation_id,
                cancel_event_ref=_active_cancel_event,
                dlp_scanner=_dlp_scanner,
                injection_detector=_injection_detector,
                output_filter=_output_filter,
                project_id=project_id,
            )
        else:
            git_branch = _detect_git_branch()
            build_date = renderer._get_build_date()
            with renderer.startup_step("Checking for updates..."):
                latest_version = await _check_for_update(__version__)
            installed_packs = packs_service.list_packs(db)
            pack_count = len(installed_packs)
            pack_names = [p["name"] for p in installed_packs] if installed_packs else None
            is_first_run = not storage.list_conversations(db, limit=1)
            renderer.render_welcome(
                model=config.ai.model,
                tool_count=len(all_tool_names),
                instructions_loaded=instructions is not None,
                working_dir=working_dir,
                git_branch=git_branch,
                version=__version__,
                build_date=build_date,
                skill_count=len(skill_registry.list_skills()),
                pack_count=pack_count,
                pack_names=pack_names,
                is_first_run=is_first_run,
            )
            if latest_version:
                renderer.render_update_available(__version__, latest_version)
            if config.mcp_servers and mcp_manager:
                await _run_mcp_startup_live(mcp_manager, config.mcp_servers, _mcp_statuses)
                # Rebuild tool list now that MCP tools are available
                mcp_tools_post = mcp_manager.get_openai_tools()
                if mcp_tools_post:
                    tools_openai.extend(mcp_tools_post)
                    tools_openai[:] = cap_tools(
                        tools_openai, set(tool_registry.list_tools()), limit=config.ai.max_tools
                    )
                    if config.safety.read_only:
                        from ..tools.tiers import filter_read_only_tools as _fro

                        tools_openai[:] = _fro(tools_openai, config.safety.tool_tiers)
                    tools_openai_or_none = tools_openai if tools_openai else None
                    all_tool_names.extend(t["name"] for t in mcp_manager.get_all_tools())
            if config.safety.read_only:
                renderer.console.print(
                    f"[yellow]Read-only mode active.[/yellow] Only READ-tier tools are available.\n"
                    f"  [{MUTED}]Write, execute, and destructive tools are disabled.[/{MUTED}]\n"
                )
            if plan_mode:
                renderer.console.print(
                    f"[yellow]Planning mode active.[/yellow] The AI will explore and write a plan.\n"
                    f"  [{MUTED}]Use /plan approve to execute, /plan off to exit.[/{MUTED}]\n"
                )
            await _run_repl(
                config=config,
                db=db,
                ai_service=ai_service,
                tool_executor=tool_executor,
                tools_openai=tools_openai_or_none,
                extra_system_prompt=extra_system_prompt,
                all_tool_names=all_tool_names,
                working_dir=working_dir,
                resume_conversation_id=resume_conversation_id,
                skill_registry=skill_registry,
                mcp_manager=mcp_manager,
                tool_registry=tool_registry,
                cancel_event_ref=_active_cancel_event,
                subagent_limiter=_subagent_limiter,
                rate_limiter=_rate_limiter,
                plan_mode=plan_mode,
                skill_msg_queue_ref=_active_msg_queue,
                dlp_scanner=_dlp_scanner,
                injection_detector=_injection_detector,
                output_filter=_output_filter,
                project_id=project_id,
                instructions=instructions,
                project_instructions=_project_instructions,
            )
    finally:
        if retention_worker:
            retention_worker.stop()
        if mcp_manager:
            try:
                await mcp_manager.shutdown()
            except BaseException:
                pass
        db.close()


async def _run_one_shot(
    config: AppConfig,
    db: Any,
    ai_service: AIService,
    tool_executor: Any,
    tools_openai: list[dict[str, Any]] | None,
    extra_system_prompt: str,
    prompt: str,
    working_dir: str,
    tool_registry: Any = None,
    resume_conversation_id: str | None = None,
    cancel_event_ref: list[asyncio.Event | None] | None = None,
    dlp_scanner: Any | None = None,
    injection_detector: Any | None = None,
    output_filter: Any | None = None,
    project_id: str | None = None,
) -> None:
    """Run a single prompt and exit."""
    id_kw = _identity_kwargs(config)
    expanded = _expand_file_references(prompt, working_dir, file_max_chars=config.cli.file_reference_max_chars)

    if resume_conversation_id:
        conv = storage.get_conversation(db, resume_conversation_id)
        if not conv:
            renderer.render_error(f"Conversation {resume_conversation_id} not found")
            return
        working_dir = _restore_working_dir(conv, tool_registry, working_dir)
        messages = _load_conversation_messages(db, resume_conversation_id)
    else:
        conv = storage.create_conversation(db, working_dir=working_dir, project_id=project_id, **id_kw)
        messages = []

    storage.create_message(db, conv["id"], "user", expanded, **id_kw)
    messages.append({"role": "user", "content": expanded})

    cancel_event = asyncio.Event()
    if cancel_event_ref is not None:
        cancel_event_ref[0] = cancel_event

    loop = asyncio.get_event_loop()
    _add_signal_handler(loop, signal.SIGINT, cancel_event.set)
    escape_task = asyncio.create_task(_watch_for_escape(cancel_event))

    renderer.clear_subagent_state()
    thinking = False
    user_attempt = 0

    _budget_cfg = config.cli.usage.budgets

    async def _get_token_totals() -> tuple[int, int]:
        return (
            storage.get_conversation_token_total(db, conv["id"]),
            storage.get_daily_token_total(db),
        )

    try:
        while True:
            user_attempt += 1
            should_retry = False

            async for event in run_agent_loop(
                ai_service=ai_service,
                messages=messages,
                tool_executor=tool_executor,
                tools_openai=tools_openai,
                cancel_event=cancel_event,
                extra_system_prompt=extra_system_prompt,
                max_iterations=config.cli.max_tool_iterations,
                narration_cadence=ai_service.config.narration_cadence,
                tool_output_max_chars=config.cli.tool_output_max_chars,
                budget_config=_budget_cfg,
                get_token_totals=_get_token_totals,
                dlp_scanner=dlp_scanner,
                injection_detector=injection_detector,
                output_filter=output_filter,
            ):
                if event.kind == "thinking":
                    if not thinking:
                        renderer.start_thinking()
                        thinking = True
                elif event.kind == "phase":
                    renderer.set_thinking_phase(event.data.get("phase", ""))
                elif event.kind == "retrying":
                    renderer.set_retrying(event.data)
                elif event.kind == "token":
                    if not thinking:
                        renderer.start_thinking()
                        thinking = True
                    renderer.render_token(event.data["content"])
                    renderer.increment_thinking_tokens()
                    renderer.increment_streaming_chars(len(event.data.get("content", "")))
                    renderer.update_thinking()
                elif event.kind == "tool_call_start":
                    if thinking:
                        await renderer.stop_thinking()
                        thinking = False
                    renderer.render_tool_call_start(event.data["tool_name"], event.data["arguments"])
                elif event.kind == "tool_call_end":
                    renderer.render_tool_call_end(event.data["tool_name"], event.data["status"], event.data["output"])
                elif event.kind == "assistant_message":
                    if event.data["content"]:
                        storage.create_message(db, conv["id"], "assistant", event.data["content"], **id_kw)
                elif event.kind == "dlp_blocked":
                    if thinking:
                        await renderer.stop_thinking(error_msg="Response blocked by DLP policy")
                        thinking = False
                    else:
                        renderer.render_error("Response blocked by DLP policy")
                elif event.kind == "dlp_warning":
                    rules = ", ".join(event.data.get("matches", []))
                    renderer.render_error(f"DLP warning: sensitive data detected [{rules}]")
                elif event.kind == "injection_detected":
                    action = event.data.get("action", "warn")
                    detail = event.data.get("detail", "prompt injection detected")
                    if action == "block":
                        renderer.render_error(f"Tool output blocked: {detail}")
                    else:
                        renderer.render_error(f"Injection warning: {detail}")
                elif event.kind == "output_filter_blocked":
                    if thinking:
                        await renderer.stop_thinking(error_msg="Response blocked by output content filter")
                        thinking = False
                    else:
                        renderer.render_error("Response blocked by output content filter")
                elif event.kind == "output_filter_warning":
                    rules = ", ".join(event.data.get("matches", []))
                    renderer.render_error(f"Output filter warning: forbidden content detected [{rules}]")
                elif event.kind == "error":
                    error_msg = event.data.get("message", "Unknown error")
                    retryable = event.data.get("retryable", False)
                    if thinking and retryable and user_attempt < config.cli.max_retries:
                        # Show countdown on thinking line, auto-retry
                        should_retry = await renderer.thinking_countdown(
                            config.cli.retry_delay, cancel_event, error_msg
                        )
                        if should_retry and not cancel_event.is_set():
                            # Reset cancel_event for the retry
                            cancel_event.clear()
                            renderer.start_thinking()
                            # thinking stays True
                        else:
                            await renderer.stop_thinking(cancel_msg="cancelled")
                            thinking = False
                    elif thinking and retryable and user_attempt >= config.cli.max_retries:
                        # Exhausted user retries
                        await renderer.stop_thinking(error_msg=f"{error_msg} · {user_attempt} attempts failed")
                        thinking = False
                    elif thinking:
                        # Non-retryable error
                        await renderer.stop_thinking(error_msg=error_msg)
                        thinking = False
                    else:
                        renderer.render_error(error_msg)
                elif event.kind == "budget_warning":
                    if thinking:
                        await renderer.stop_thinking()
                        thinking = False
                    renderer.render_warning(event.data.get("message", "Token budget warning"))
                elif event.kind == "done":
                    if thinking and cancel_event.is_set():
                        await renderer.stop_thinking(cancel_msg="cancelled")
                        thinking = False
                    elif thinking:
                        await renderer.stop_thinking()
                        thinking = False
                    if not cancel_event.is_set():
                        renderer.render_response_end()

            if not should_retry:
                break

        if not cancel_event.is_set():
            try:
                title = await ai_service.generate_title(prompt)
                storage.update_conversation_title(db, conv["id"], title)
            except Exception:
                pass

    except KeyboardInterrupt:
        if thinking:
            renderer.stop_thinking_sync()
            thinking = False
        renderer.render_response_end()
    finally:
        cancel_event.set()
        escape_task.cancel()
        _remove_signal_handler(loop, signal.SIGINT)


def _patch_completion_menu_position() -> None:
    """Patch FloatContainer so the completion menu renders above the cursor.

    prompt_toolkit positions the completion menu below the cursor by default and
    only flips above when there is more vertical space above than below.  This
    patches ``FloatContainer._draw_float`` at the **class** level so completion
    menus are always placed above the cursor line — eliminating clipping when the
    prompt is near the terminal bottom.
    """
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.layout.containers import Float, FloatContainer
    from prompt_toolkit.layout.menus import CompletionsMenu, MultiColumnCompletionsMenu
    from prompt_toolkit.layout.screen import WritePosition

    _orig = FloatContainer._draw_float

    def _draw_float_patched(
        self: Any,
        fl: Float,
        screen: Any,
        mouse_handlers: Any,
        write_position: WritePosition,
        style: str,
        erase_bg: bool,
        z_index: int | None,
    ) -> None:
        if not isinstance(fl.content, (CompletionsMenu, MultiColumnCompletionsMenu)):
            return _orig(self, fl, screen, mouse_handlers, write_position, style, erase_bg, z_index)

        try:
            from prompt_toolkit.application.current import get_app

            cpos = screen.get_menu_position(fl.attach_to_window or get_app().layout.current_window)
            cursor = Point(x=cpos.x - write_position.xpos, y=cpos.y - write_position.ypos)

            fl_w = fl.get_width()
            width = (
                fl_w
                if fl_w is not None
                else min(write_position.width, fl.content.preferred_width(write_position.width).preferred)
            )
            xpos = cursor.x
            if xpos + width > write_position.width:
                xpos = max(0, write_position.width - width)

            fl_h = fl.get_height()
            height = fl_h if fl_h is not None else fl.content.preferred_height(width, write_position.height).preferred

            if cursor.y >= height:
                height = min(height, cursor.y)
                ypos = cursor.y - height
            else:
                ypos = cursor.y + 1
                height = min(height, write_position.height - ypos)

            if height > 0 and width > 0:
                fl.content.write_to_screen(
                    screen,
                    mouse_handlers,
                    WritePosition(
                        xpos=xpos + write_position.xpos,
                        ypos=ypos + write_position.ypos,
                        width=width,
                        height=height,
                    ),
                    style,
                    erase_bg=not fl.transparent(),
                    z_index=z_index,
                )
        except Exception:
            return _orig(self, fl, screen, mouse_handlers, write_position, style, erase_bg, z_index)

    FloatContainer._draw_float = _draw_float_patched  # type: ignore[assignment]


async def _run_repl(
    config: AppConfig,
    db: Any,
    ai_service: AIService,
    tool_executor: Any,
    tools_openai: list[dict[str, Any]] | None,
    extra_system_prompt: str,
    all_tool_names: list[str],
    working_dir: str,
    resume_conversation_id: str | None = None,
    skill_registry: SkillRegistry | None = None,
    mcp_manager: Any = None,
    tool_registry: Any = None,
    cancel_event_ref: list[asyncio.Event | None] | None = None,
    subagent_limiter: Any = None,
    rate_limiter: Any = None,
    plan_mode: bool = False,
    skill_msg_queue_ref: list[asyncio.Queue[dict[str, Any]] | None] | None = None,
    dlp_scanner: Any | None = None,
    injection_detector: Any | None = None,
    output_filter: Any | None = None,
    project_id: str | None = None,
    instructions: str | None = None,
    project_instructions: str | None = None,
) -> None:
    """Run the interactive REPL."""
    id_kw = _identity_kwargs(config)

    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.document import Document
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style as PtStyle

    class AnteroomCompleter(Completer):
        """Tab completer for / commands, @ file paths, and conversation slugs."""

        _slug_commands = frozenset({"resume", "delete", "rename"})

        def __init__(self, commands: list[str], skill_names: list[str], wd: str, db: Any) -> None:
            self._commands = commands
            self._skill_names = skill_names
            self._wd = wd
            self._db = db

        def update_skill_names(self, skill_names: list[str]) -> None:
            self._skill_names = skill_names

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

    commands = [
        "new",
        "append",
        "last",
        "list",
        "search",
        "resume",
        "delete",
        "rename",
        "slug",
        "rewind",
        "compact",
        "conventions",
        "tools",
        "skills",
        "reload-skills",
        "project",
        "projects",
        "mcp",
        "model",
        "plan",
        "upload",
        "usage",
        "verbose",
        "detail",
        "help",
        "quit",
        "exit",
    ]
    skill_names = [s.name for s in skill_registry.list_skills()] if skill_registry else []
    completer = AnteroomCompleter(commands, skill_names, working_dir, db)

    def _rebuild_tools() -> None:
        """Rebuild the tool list after MCP changes."""
        nonlocal tools_openai, all_tool_names
        new_tools: list[dict[str, Any]] = []
        if tool_registry:
            new_tools.extend(tool_registry.get_openai_tools())
        if mcp_manager:
            mcp_tools = mcp_manager.get_openai_tools()
            if mcp_tools:
                new_tools.extend(mcp_tools)
        from ..tools import cap_tools as _cap_tools

        builtin = set(tool_registry.list_tools()) if tool_registry else set()
        new_tools = _cap_tools(new_tools, builtin, limit=config.ai.max_tools)
        if config.safety.read_only:
            from ..tools.tiers import filter_read_only_tools as _fro2

            new_tools = _fro2(new_tools, config.safety.tool_tiers)
        tools_openai = new_tools if new_tools else None
        new_names: list[str] = list(tool_registry.list_tools()) if tool_registry else []
        if mcp_manager:
            new_names.extend(t["name"] for t in mcp_manager.get_all_tools())
        all_tool_names = new_names

    history_path = config.app.data_dir / "cli_history"

    # Map Shift+Enter (CSI u: \x1b[13;2u) to Ctrl+J for terminals that
    # support the kitty keyboard protocol (iTerm2, kitty, WezTerm, foot).
    # Terminal.app doesn't send this sequence — Shift+Enter = Enter there.
    try:
        from prompt_toolkit.input import vt100_parser

        vt100_parser.ANSI_SEQUENCES["\x1b[13;2u"] = "c-j"
    except Exception:
        pass

    # Key bindings
    kb = KeyBindings()

    # Paste detection: track buffer changes to distinguish paste from typing.
    # Pasted characters arrive in < 5ms bursts; human typing is > 50ms apart.
    _last_text_change: list[float] = [0.0]

    # Enter submits; Alt+Enter / Shift+Enter / Ctrl+J inserts newline
    @kb.add("enter")
    def _submit(event: Any) -> None:
        if _is_paste(_last_text_change[0]):
            # Rapid input (paste) — insert newline, don't submit
            event.current_buffer.insert_text("\n")
        else:
            event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")
    @kb.add("c-j")
    def _newline(event: Any) -> None:
        event.current_buffer.insert_text("\n")

    # Ctrl+C: clear buffer if text present (with double-press exit), exit if empty
    _exit_flag: list[bool] = [False]
    _last_ctrl_c: list[float] = [0.0]

    @kb.add("c-c")
    def _handle_ctrl_c(event: Any) -> None:
        import time

        buf = event.current_buffer
        now = time.monotonic()
        if buf.text:
            buf.reset()
            _last_ctrl_c[0] = now
        elif now - _last_ctrl_c[0] < 2.0:
            _exit_flag[0] = True
            buf.validate_and_handle()
        else:
            _exit_flag[0] = True
            buf.validate_and_handle()

    # Styled prompt — dim while agent is working to signal "you can type to queue"
    _prompt_text = HTML("<style fg='#C5A059'>❯</style> ")
    _prompt_dim = HTML(f"<style fg='{CHROME}'>❯</style> ")
    _continuation = "  "  # align with "❯ "

    def _prompt() -> HTML:
        return _prompt_dim if agent_busy.is_set() else _prompt_text

    _repl_style = PtStyle.from_dict(
        {
            "completion-menu": f"bg:#1a1a2e {CHROME}",
            "completion-menu.completion": f"bg:#1a1a2e {CHROME}",
            "completion-menu.completion.current": f"bg:{GOLD} #1a1a2e",
            "completion-menu.meta.completion": f"bg:#1a1a2e {MUTED}",
            "completion-menu.meta.completion.current": f"bg:{GOLD} #1a1a2e",
        }
    )

    session: PromptSession[str] = PromptSession(
        history=FileHistory(str(history_path)),
        key_bindings=kb,
        multiline=True,
        prompt_continuation=_continuation,
        completer=completer,
        reserve_space_for_menu=4,
        style=_repl_style,
    )

    _patch_completion_menu_position()

    # Hook buffer changes for paste detection timing
    def _on_buffer_change(_buf: Any) -> None:
        _last_text_change[0] = time.monotonic()

    session.default_buffer.on_text_changed += _on_buffer_change

    current_model = config.ai.model

    if resume_conversation_id:
        conv_data = storage.get_conversation(db, resume_conversation_id)
        if conv_data:
            conv = conv_data
            ai_messages = _load_conversation_messages(db, resume_conversation_id)
            is_first_message = False
            working_dir = _restore_working_dir(conv, tool_registry, working_dir)
            _show_resume_info(db, conv, ai_messages)
            # Load project from resumed conversation if not already set via --project
            if not project_id and conv.get("project_id"):
                project_id = conv["project_id"]
                _proj = storage.get_project(db, project_id)
                if _proj:
                    if _proj.get("instructions"):
                        project_instructions = _proj["instructions"]
                    if _proj.get("model") and not config.ai.model:
                        config.ai.model = _proj["model"]
                    renderer.console.print(f"[dim]Project:[/dim] {_proj['name']}")
                    # Rebuild system prompt with project instructions
                    extra_system_prompt = _build_system_prompt(
                        config,
                        working_dir,
                        instructions,
                        builtin_tools=tool_registry.list_tools(),
                        mcp_servers=mcp_manager.get_server_statuses() if mcp_manager else None,
                        project_instructions=project_instructions,
                    )
        else:
            renderer.render_error(f"Conversation {resume_conversation_id} not found, starting new")
            conv = storage.create_conversation(db, working_dir=working_dir, project_id=project_id, **id_kw)
            ai_messages = []
            is_first_message = True
    else:
        conv = storage.create_conversation(db, working_dir=working_dir, project_id=project_id, **id_kw)
        ai_messages: list[dict[str, Any]] = []
        is_first_message = True

    async def _show_help_dialog() -> None:
        """Show help in a floating dialog that doesn't disturb scrollback."""
        from prompt_toolkit.formatted_text import FormattedText
        from prompt_toolkit.shortcuts import message_dialog
        from prompt_toolkit.styles import Style

        cmd = "#C5A059 bold"
        desc = "#94A3B8"
        help_text = FormattedText(
            [
                ("bold", " Conversations\n"),
                (cmd, "  /new"),
                (desc, "              Start a new chat conversation\n"),
                (cmd, "  /new note <t>"),
                (desc, "     Start a new note\n"),
                (cmd, "  /new doc <t>"),
                (desc, "      Start a new document\n"),
                (cmd, "  /append <text>"),
                (desc, "    Add entry to current note\n"),
                (cmd, "  /last"),
                (desc, "             Resume the most recent conversation\n"),
                (cmd, "  /list [N]"),
                (desc, "         Show recent conversations (default 20)\n"),
                (cmd, "  /search <query>"),
                (desc, "   Search conversations by content\n"),
                (cmd, "  /resume [N|id|slug]"),
                (desc, " Resume (picker if no arg)\n"),
                (cmd, "  /delete <N|id|slug>"),
                (desc, " Delete a conversation\n"),
                (cmd, "  /rename [N|id|slug] <title>"),
                (desc, "\n                          Rename a conversation\n"),
                (cmd, "  /slug [name]"),
                (desc, "        Show or set conversation slug\n"),
                (cmd, "  /rewind"),
                (desc, "           Roll back to an earlier message\n"),
                ("", "\n"),
                ("bold", " Session\n"),
                (cmd, "  /compact"),
                (desc, "          Summarize history to free context\n"),
                (cmd, "  /model <name>"),
                (desc, "     Switch AI model mid-session\n"),
                (cmd, "  /tools"),
                (desc, "            List available tools\n"),
                (cmd, "  /skills"),
                (desc, "           List loaded skills (auto-reloads)\n"),
                (cmd, "  /reload-skills"),
                (desc, "    Reload skill files from disk\n"),
                (cmd, "  /mcp"),
                (desc, "              Show MCP server status\n"),
                (cmd, "  /conventions"),
                (desc, "      Show loaded ANTEROOM.md conventions\n"),
                (cmd, "  /plan"),
                (desc, "             Plan mode: on/approve/status/off\n"),
                (cmd, "  /verbose"),
                (desc, "          Cycle: compact > detailed > verbose\n"),
                (cmd, "  /detail"),
                (desc, "           Replay last turn's tool calls\n"),
                (cmd, "  /usage"),
                (desc, "            Show token usage statistics\n"),
                ("", "\n"),
                ("bold", " Input\n"),
                (cmd, "  /upload <path>"),
                (desc, "    Upload a file to the conversation\n"),
                (cmd, "  @<path>"),
                (desc, "           Include file contents inline\n"),
                (cmd, "  Alt+Enter"),
                (desc, "         Insert newline\n"),
                (cmd, "  Escape"),
                (desc, "            Cancel AI generation\n"),
                (cmd, "  /quit"),
                (desc, " · "),
                (cmd, "Ctrl+D"),
                (desc, "      Exit\n"),
            ]
        )
        dialog_style = Style.from_dict(
            {
                "dialog": "bg:#1a1a2e",
                "dialog frame.label": "bg:#1a1a2e #C5A059 bold",
                "dialog.body": "bg:#1a1a2e #e0e0e0",
                "dialog shadow": "bg:#0a0a15",
                "button": "bg:#C5A059 #1a1a2e",
                "button.focused": "bg:#e0c070 #1a1a2e bold",
            }
        )
        await message_dialog(
            title="Help",
            text=help_text,
            ok_text="Close",
            style=dialog_style,
        ).run_async()

    async def _show_resume_picker() -> dict[str, Any] | None:
        """Show an interactive conversation picker with preview panel."""
        from prompt_toolkit.application import Application
        from prompt_toolkit.formatted_text import FormattedText
        from prompt_toolkit.key_binding import KeyBindings as PickerKB
        from prompt_toolkit.layout.containers import HSplit, VSplit, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.layout.layout import Layout
        from prompt_toolkit.styles import Style

        convs = storage.list_conversations(db, limit=20)
        if not convs:
            renderer.console.print(f"[{CHROME}]No conversations found[/{CHROME}]\n")
            return None

        selected_idx = [0]
        preview_cache: dict[str, list[dict[str, Any]]] = {}

        def _get_list_text() -> FormattedText:
            fragments: list[tuple[str, str]] = []
            for i, c in enumerate(convs):
                is_sel = i == selected_idx[0]
                title = (c.get("title") or "Untitled")[:35]
                slug = (c.get("slug") or "")[:20]
                badge = _picker_type_badge(c.get("type") or "chat")
                ts = _picker_relative_time(c.get("updated_at") or "")
                count = c.get("message_count") or 0

                if is_sel:
                    fragments.append(("class:list.selected", f" > {title}"))
                    if badge:
                        fragments.append(("class:list.badge", f" {badge}"))
                    fragments.append(("class:list.selected-meta", f"  {slug}  {count}msg  {ts}"))
                    fragments.append(("class:list.selected", "\n"))
                else:
                    fragments.append(("class:list.item", f"   {title}"))
                    if badge:
                        fragments.append(("class:list.badge", f" {badge}"))
                    fragments.append(("class:list.meta", f"  {slug}  {count}msg  {ts}"))
                    fragments.append(("class:list.item", "\n"))
            return FormattedText(fragments)

        def _get_preview_text() -> FormattedText:
            if not convs:
                return FormattedText([])
            conv_id = convs[selected_idx[0]]["id"]
            if conv_id not in preview_cache:
                preview_cache[conv_id] = storage.list_messages(db, conv_id)
            msgs = preview_cache[conv_id]
            return FormattedText(_picker_format_preview(msgs))

        list_control = FormattedTextControl(_get_list_text)
        preview_control = FormattedTextControl(_get_preview_text)

        def _refresh() -> None:
            list_control.text = _get_list_text()
            preview_control.text = _get_preview_text()

        picker_kb = PickerKB()
        result: list[dict[str, Any] | None] = [None]

        @picker_kb.add("up")
        @picker_kb.add("k")
        def _up(event: Any) -> None:
            if selected_idx[0] > 0:
                selected_idx[0] -= 1
                _refresh()

        @picker_kb.add("down")
        @picker_kb.add("j")
        def _down(event: Any) -> None:
            if selected_idx[0] < len(convs) - 1:
                selected_idx[0] += 1
                _refresh()

        @picker_kb.add("enter")
        def _select(event: Any) -> None:
            result[0] = convs[selected_idx[0]]
            event.app.exit()

        @picker_kb.add("escape")
        @picker_kb.add("c-c")
        def _cancel(event: Any) -> None:
            event.app.exit()

        separator = Window(width=1, char="│", style="class:separator")
        body = VSplit(
            [
                Window(content=list_control, width=50, wrap_lines=False),
                separator,
                Window(content=preview_control, wrap_lines=True),
            ]
        )
        title_bar = Window(
            content=FormattedTextControl(
                FormattedText(
                    [
                        ("class:title", " Resume Conversation  "),
                        ("class:hint", " ↑↓ navigate  Enter select  Esc cancel "),
                    ]
                )
            ),
            height=1,
        )
        layout = Layout(HSplit([title_bar, body]))

        style = Style.from_dict(
            {
                "title": "bg:#C5A059 #1a1a2e bold",
                "hint": "bg:#3a3a4e #94A3B8",
                "separator": "#3a3a4e",
                "list.selected": "bg:#2a2a3e #C5A059 bold",
                "list.selected-meta": "bg:#2a2a3e #94A3B8",
                "list.item": "#e0e0e0",
                "list.meta": "#6b7280",
                "list.badge": "#C5A059 italic",
                "preview.role-user": "#C5A059 bold",
                "preview.role-ai": "#94A3B8 bold",
                "preview.content": "#e0e0e0",
                "preview.empty": "#6b7280 italic",
            }
        )

        app: Application[None] = Application(
            layout=layout,
            key_bindings=picker_kb,
            style=style,
            full_screen=True,
        )
        await app.run_async()
        return result[0]

    # -- Concurrent input/output architecture --
    # Instead of blocking on prompt_async then running agent loop sequentially,
    # we use two coroutines: one collects input, one processes agent responses.
    # prompt_toolkit's patch_stdout keeps the input prompt anchored at the bottom.

    input_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=10)
    agent_busy = asyncio.Event()  # set while agent loop is running
    exit_flag = asyncio.Event()
    _current_cancel_event: list[asyncio.Event | None] = [None]

    # Escape cancels the agent loop (only active during streaming).
    # prompt_toolkit's key processor handles the Escape timeout (~100ms)
    # to distinguish bare Escape from escape sequences (arrow keys, etc.).
    @kb.add("escape", filter=Condition(lambda: agent_busy.is_set()))
    def _cancel_on_escape(event: Any) -> None:
        ce = _current_cancel_event[0]
        if ce is not None:
            ce.set()
            # Cancel display is handled by stop_thinking(cancel_msg="cancelled")
            # in the event loop when it detects the cancel_event.

    async def _collect_input() -> None:
        """Continuously collect user input and put on queue."""
        while not exit_flag.is_set():
            _exit_flag[0] = False
            try:
                user_input_raw = await session.prompt_async(_prompt)
            except EOFError:
                exit_flag.set()
                return
            except KeyboardInterrupt:
                continue

            if _exit_flag[0]:
                exit_flag.set()
                return

            _collapse_long_input(user_input_raw)
            text = user_input_raw.strip()
            if not text:
                continue

            if agent_busy.is_set():
                if input_queue.full():
                    renderer.console.print("[yellow]Queue full (max 10 messages)[/yellow]")
                    continue
                renderer.console.print(f"[{CHROME}]Message queued[/{CHROME}]")

            await input_queue.put(text)
            agent_busy.set()

    def _has_pending_work() -> bool:
        """Check if there's more work queued."""
        return not input_queue.empty() and not exit_flag.is_set()

    async def _agent_runner() -> None:
        """Process messages from input_queue, run commands and agent loop."""
        nonlocal conv, ai_messages, is_first_message, tools_openai, all_tool_names
        nonlocal current_model, ai_service, extra_system_prompt, working_dir

        # -- Plan mode state --
        from .plan import (
            PLAN_MODE_ALLOWED_TOOLS,
            build_planning_system_prompt,
            delete_plan,
            get_editor,
            get_plan_file_path,
            parse_plan_command,
            parse_plan_steps,
            read_plan,
        )

        _plan_active: list[bool] = [plan_mode]
        _plan_file: list[Path | None] = [None]
        _full_tools_backup: list[list[dict[str, Any]] | None] = [None]
        _plan_checklist_steps: list[str] = []  # parsed step descriptions for live checklist
        _plan_current_step: list[int] = [0]  # index of the step currently in progress

        # -- RAG state --
        _rag_embedding_service: list[Any] = [None]
        _rag_service_checked: list[bool] = [False]

        async def _get_rag_embedding_service() -> Any:
            """Lazily create embedding service for RAG retrieval, with auto-detect probe."""
            if _rag_service_checked[0]:
                return _rag_embedding_service[0]
            _rag_service_checked[0] = True
            try:
                from ..services.embeddings import create_embedding_service

                svc = create_embedding_service(config)
                if svc and config.embeddings.enabled is None:
                    probe_ok = await svc.probe()
                    if not probe_ok:
                        logger.info("Embedding endpoint unavailable; semantic search disabled")
                        svc = None
                _rag_embedding_service[0] = svc
                return svc
            except Exception:
                logger.debug("RAG: failed to create embedding service", exc_info=True)
                return None

        def _apply_plan_mode(conv_id: str) -> None:
            nonlocal tools_openai, extra_system_prompt
            plan_path = get_plan_file_path(config.app.data_dir, conv_id)
            _plan_file[0] = plan_path
            _plan_active[0] = True
            # Back up full tools before filtering
            _full_tools_backup[0] = tools_openai

            # Filter tools to plan-mode allowlist
            if tools_openai:
                tools_openai = [t for t in tools_openai if t.get("function", {}).get("name") in PLAN_MODE_ALLOWED_TOOLS]

            # Inject planning prompt (remove existing if re-entering)
            extra_system_prompt = _strip_planning_prompt(extra_system_prompt)
            extra_system_prompt += "\n\n" + build_planning_system_prompt(plan_path)

        def _exit_plan_mode(plan_content: str | None = None) -> None:
            nonlocal tools_openai, extra_system_prompt
            _plan_active[0] = False

            # Restore full tools
            if _full_tools_backup[0] is not None:
                tools_openai = _full_tools_backup[0]
                _full_tools_backup[0] = None
            else:
                _rebuild_tools()

            # Remove planning prompt, optionally inject approved plan
            extra_system_prompt = _strip_planning_prompt(extra_system_prompt)
            if plan_content:
                extra_system_prompt += (
                    "\n\n<approved_plan>\n"
                    "The user has approved the following implementation plan. "
                    "Execute it step by step.\n\n" + plan_content + "\n</approved_plan>"
                )

        def _strip_planning_prompt(prompt: str) -> str:
            prompt = re.sub(r"\n*<planning_mode>.*?</planning_mode>", "", prompt, flags=re.DOTALL)
            return prompt

        # -- Project state --
        _active_project: list[dict[str, Any] | None] = [None]

        def _resolve_project(name_or_id: str) -> dict[str, Any] | None:
            """Look up a project by name (case-insensitive) or UUID prefix."""
            proj = storage.get_project_by_name(db, name_or_id)
            if proj:
                return proj
            proj = storage.get_project(db, name_or_id)
            if proj:
                return proj
            # Try UUID prefix match
            all_projects = storage.list_projects(db)
            matches = [p for p in all_projects if p["id"].startswith(name_or_id)]
            if len(matches) == 1:
                return matches[0]
            return None

        def _inject_project_instructions(project: dict[str, Any]) -> None:
            nonlocal extra_system_prompt
            extra_system_prompt = _strip_project_instructions(extra_system_prompt)
            instructions = project.get("instructions", "")
            if instructions:
                safe_name = sanitize_trust_tags(project["name"]).replace('"', "&quot;")
                safe_instructions = sanitize_trust_tags(instructions)
                extra_system_prompt += (
                    '\n\n<project_instructions project="'
                    + safe_name
                    + '">\n'
                    + safe_instructions
                    + "\n</project_instructions>"
                )

        def _strip_project_instructions(prompt: str) -> str:
            return re.sub(r"\n*<project_instructions[^>]*>.*?</project_instructions>", "", prompt, flags=re.DOTALL)

        # Apply plan mode at startup if --plan was passed
        if _plan_active[0]:
            _apply_plan_mode(conv["id"])

        while not exit_flag.is_set():
            # If agent_busy was set (by _collect_input) but we're back here waiting
            # for input, clear it so the prompt renders as gold (idle).
            if agent_busy.is_set() and not _has_pending_work():
                agent_busy.clear()
                session.app.invalidate()

            try:
                user_input = await asyncio.wait_for(input_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            # Handle commands
            if user_input.startswith("/"):
                cmd = user_input.lower().split()[0]
                if cmd in ("/quit", "/exit"):
                    exit_flag.set()
                    return
                elif cmd == "/new":
                    parts = user_input.split(maxsplit=2)
                    conv_type = "chat"
                    conv_title = "New Conversation"
                    if len(parts) >= 2 and parts[1] in ("note", "doc", "document"):
                        conv_type = "document" if parts[1] in ("doc", "document") else "note"
                        conv_title = parts[2].strip() if len(parts) >= 3 else f"New {conv_type.title()}"
                    active_pid = _active_project[0]["id"] if _active_project[0] else None
                    conv = storage.create_conversation(
                        db,
                        title=conv_title,
                        conversation_type=conv_type,
                        working_dir=working_dir,
                        project_id=active_pid,
                        **id_kw,
                    )
                    ai_messages = []
                    is_first_message = conv_type == "chat"
                    if _plan_active[0]:
                        _apply_plan_mode(conv["id"])
                    type_label = f" ({conv_type})" if conv_type != "chat" else ""
                    renderer.console.print(f"[{CHROME}]New conversation started{type_label}[/{CHROME}]\n")
                    continue
                elif cmd == "/append":
                    parts = user_input.split(maxsplit=1)
                    if len(parts) < 2 or not parts[1].strip():
                        renderer.console.print(f"[{CHROME}]Usage: /append <text>[/{CHROME}]\n")
                        continue
                    current_type = conv.get("type", "chat")
                    if current_type != "note":
                        renderer.render_error("Current conversation is not a note. Use /new note <title> first.")
                        continue
                    entry_text = parts[1].strip()
                    storage.create_message(db, conv["id"], "user", entry_text, **id_kw)
                    renderer.console.print(f"[{CHROME}]Entry added to '{conv.get('title', 'Untitled')}'[/{CHROME}]\n")
                    continue
                elif cmd == "/tools":
                    renderer.render_tools(all_tool_names)
                    continue
                elif cmd == "/conventions":
                    info = discover_conventions(working_dir)
                    if info.source == "none":
                        renderer.console.print(
                            f"[{CHROME}]No conventions file found.[/{CHROME}]\n"
                            f"  [{MUTED}]Create ANTEROOM.md in your project root to define conventions.[/{MUTED}]\n"
                        )
                    else:
                        label = "Project" if info.source == "project" else "Global"
                        renderer.console.print(f"\n[bold]{label} conventions:[/bold] {info.path}")
                        renderer.console.print(f"  [{MUTED}]~{info.estimated_tokens:,} tokens[/{MUTED}]")
                        if info.warning:
                            renderer.console.print(f"  [yellow]{info.warning}[/yellow]")
                        renderer.console.print()
                        lines = (info.content or "").splitlines()
                        preview = lines[:50]
                        for line in preview:
                            renderer.console.print(f"  {line}")
                        if len(lines) > 50:
                            renderer.console.print(
                                f"\n  [{MUTED}]... {len(lines) - 50} more lines. View full file: {info.path}[/{MUTED}]"
                            )
                        renderer.console.print()
                    continue
                elif cmd == "/upload":
                    parts = user_input.split(maxsplit=1)
                    if len(parts) < 2 or not parts[1].strip():
                        renderer.console.print(f"[{CHROME}]Usage: /upload <path>[/{CHROME}]\n")
                        continue
                    upload_path = Path(parts[1].strip()).expanduser().resolve()
                    if not upload_path.is_file():
                        renderer.console.print(f"[{CHROME}]File not found: {upload_path}[/{CHROME}]\n")
                        continue
                    try:
                        import mimetypes

                        import filetype as _ft

                        max_size_mb = 10
                        max_size = max_size_mb * 1024 * 1024
                        file_size = upload_path.stat().st_size
                        if file_size > max_size:
                            size_mb = file_size // (1024 * 1024)
                            renderer.console.print(
                                f"[{CHROME}]File too large ({size_mb} MB). Maximum is {max_size_mb} MB.[/{CHROME}]\n"
                            )
                            continue
                        file_data = upload_path.read_bytes()
                        guess = _ft.guess(file_data)
                        mime = guess.mime if guess else (mimetypes.guess_type(str(upload_path))[0] or "text/plain")
                        source = storage.save_source_file(
                            db,
                            title=upload_path.name,
                            filename=upload_path.name,
                            mime_type=mime,
                            data=file_data,
                            data_dir=config.app.data_dir,
                            user_id=config.identity.user_id if config.identity else None,
                            user_display_name=config.identity.display_name
                            if config.identity and hasattr(config.identity, "display_name")
                            else None,
                        )
                        renderer.console.print(
                            f"[{CHROME}]Uploaded {upload_path.name} → source {source['id'][:8]}…[/{CHROME}]"
                        )
                        if source.get("content"):
                            renderer.console.print(
                                f"  [{MUTED}]{mime}, {len(source['content']):,} chars extracted[/{MUTED}]"
                            )
                        else:
                            renderer.console.print(f"  [{MUTED}]{mime}, stored (no text extracted)[/{MUTED}]")
                        renderer.console.print()
                    except Exception:
                        logger.error("CLI upload failed", exc_info=True)
                        renderer.console.print(f"[{CHROME}]Upload failed[/{CHROME}]\n")
                    continue
                elif cmd == "/usage":
                    _show_usage_stats(db, config)
                    continue
                elif cmd == "/help":
                    await _show_help_dialog()
                    continue
                elif cmd == "/compact":
                    await _compact_messages(ai_service, ai_messages, db, conv["id"])
                    continue
                elif cmd == "/last":
                    convs = storage.list_conversations(db, limit=1)
                    if convs:
                        conv = storage.get_conversation(db, convs[0]["id"]) or conv
                        ai_messages = _load_conversation_messages(db, conv["id"])
                        is_first_message = False
                        _show_resume_info(db, conv, ai_messages)
                    else:
                        renderer.console.print(f"[{CHROME}]No previous conversations[/{CHROME}]\n")
                    continue
                elif cmd == "/list":
                    parts = user_input.split()
                    list_limit = 20
                    if len(parts) >= 2 and parts[1].isdigit():
                        list_limit = max(1, int(parts[1]))
                    convs = storage.list_conversations(db, limit=list_limit + 1)
                    has_more = len(convs) > list_limit
                    display_convs = convs[:list_limit]
                    if display_convs:
                        renderer.console.print("\n[bold]Recent conversations:[/bold]")
                        for i, c in enumerate(display_convs):
                            msg_count = c.get("message_count", 0)
                            ctype = c.get("type", "chat")
                            type_badge = f" [cyan]\\[{ctype}][/cyan]" if ctype != "chat" else ""
                            slug_label = f" [{MUTED}]{c['slug']}[/{MUTED}]" if c.get("slug") else ""
                            renderer.console.print(
                                f"  {i + 1}. {c['title']}{type_badge} ({msg_count} msgs){slug_label}"
                            )
                        if has_more:
                            more_n = list_limit + 20
                            msg = f"... more available. Use /list {more_n} to show more."
                            renderer.console.print(f"  [{MUTED}]{msg}[/{MUTED}]")
                        renderer.console.print("  Use [bold]/resume <number>[/bold] or [bold]/resume <slug>[/bold]\n")
                    else:
                        renderer.console.print(f"[{CHROME}]No conversations[/{CHROME}]\n")
                    continue
                elif cmd == "/delete":
                    parts = user_input.split(maxsplit=1)
                    if len(parts) < 2:
                        renderer.console.print(f"[{CHROME}]Usage: /delete <number|slug|id>[/{CHROME}]\n")
                        continue
                    target = parts[1].strip()
                    to_delete = _resolve_conversation(db, target)
                    if not to_delete:
                        renderer.render_error(f"Conversation not found: {target}. Use /list to see conversations.")
                        continue
                    title = to_delete.get("title", "Untitled")
                    try:
                        answer = input(f'  Delete "{title}"? [y/N] ').strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        renderer.console.print(f"[{CHROME}]Cancelled[/{CHROME}]\n")
                        continue
                    if answer not in ("y", "yes"):
                        renderer.console.print(f"[{CHROME}]Cancelled[/{CHROME}]\n")
                        continue
                    storage.delete_conversation(db, to_delete["id"], config.app.data_dir)
                    renderer.console.print(f"[{CHROME}]Deleted: {title}[/{CHROME}]\n")
                    if conv.get("id") == to_delete["id"]:
                        conv = storage.create_conversation(db, working_dir=working_dir, project_id=project_id, **id_kw)
                        ai_messages = []
                        is_first_message = True
                    continue
                elif cmd == "/rename":
                    parts = user_input.split(maxsplit=2)
                    if len(parts) < 2:
                        renderer.console.print(
                            f"[{CHROME}]Usage: /rename <title> or /rename <N|id|slug> <title>[/{CHROME}]\n"
                        )
                        continue
                    # Two forms: /rename <title> (current conv) or /rename <target> <title>
                    first_arg = parts[1].strip()
                    looks_like_target = first_arg.isdigit() or ("-" in first_arg and len(first_arg) >= 36)
                    # Also treat as target if it resolves as a slug
                    if not looks_like_target and len(parts) == 3:
                        maybe_conv = storage.get_conversation(db, first_arg)
                        if maybe_conv:
                            looks_like_target = True
                    if len(parts) == 3 and looks_like_target:
                        target = parts[1].strip()
                        new_title = parts[2].strip()
                        resolved = _resolve_conversation(db, target)
                        if not resolved:
                            renderer.render_error(f"Conversation not found: {target}. Use /list to see conversations.")
                            continue
                        resolved_id = resolved["id"]
                    else:
                        # /rename <title> — rename current conversation
                        new_title = user_input.split(maxsplit=1)[1].strip()
                        resolved_id = conv.get("id")
                    if not resolved_id:
                        renderer.render_error("No active conversation to rename.")
                        continue
                    if not new_title:
                        renderer.console.print(
                            f"[{CHROME}]Usage: /rename <title> or /rename <N|id|slug> <title>[/{CHROME}]\n"
                        )
                        continue
                    storage.update_conversation_title(db, resolved_id, new_title)
                    renderer.console.print(f'[{CHROME}]Renamed conversation to "{new_title}"[/{CHROME}]\n')
                    if conv.get("id") == resolved_id:
                        conv["title"] = new_title
                    continue
                elif cmd == "/slug":
                    parts = user_input.split(maxsplit=1)
                    if len(parts) < 2:
                        # Show current slug
                        current_slug = conv.get("slug", "none")
                        renderer.console.print(f"[{CHROME}]Slug: {current_slug}[/{CHROME}]\n")
                        continue
                    desired = parts[1].strip().lower()
                    if not conv.get("id"):
                        renderer.render_error("No active conversation.")
                        continue
                    if not is_valid_slug(desired):
                        renderer.render_error(
                            "Invalid slug. Use lowercase letters, numbers, and hyphens (e.g. my-project)."
                        )
                        continue
                    suggestion = suggest_unique_slug(db, desired)
                    if suggestion is None:
                        # Desired slug is available
                        try:
                            storage.update_conversation_slug(db, conv["id"], desired)
                            conv["slug"] = desired
                            renderer.console.print(f"[{CHROME}]Slug set to: {desired}[/{CHROME}]\n")
                        except _sqlite3.IntegrityError:
                            # Race: slug was taken between check and write
                            fallback = suggest_unique_slug(db, desired)
                            renderer.render_error(f'"{desired}" is taken. Try: {fallback}')
                    else:
                        renderer.console.print(f'[{CHROME}]"{desired}" is taken. Suggestion: {suggestion}[/{CHROME}]\n')
                    continue
                elif cmd == "/search":
                    parts = user_input.split(maxsplit=1)
                    if len(parts) < 2 or not parts[1].strip():
                        renderer.console.print(
                            f"[{CHROME}]Usage: /search <query> | /search --keyword <query>[/{CHROME}]\n"
                        )
                        continue
                    search_arg = parts[1].strip()

                    # Check for --keyword flag
                    force_keyword = False
                    type_filter = None
                    if search_arg.startswith("--keyword "):
                        force_keyword = True
                        search_arg = search_arg[len("--keyword ") :].strip()
                        if not search_arg:
                            renderer.console.print(f"[{CHROME}]Usage: /search --keyword <query>[/{CHROME}]\n")
                            continue
                    elif search_arg.startswith("--type "):
                        rest = search_arg[len("--type ") :].strip()
                        type_parts = rest.split(maxsplit=1)
                        if type_parts and type_parts[0] in ("chat", "note", "document"):
                            type_filter = type_parts[0]
                            search_arg = type_parts[1] if len(type_parts) > 1 else ""
                        else:
                            renderer.render_error("Invalid type. Use: chat, note, or document")
                            continue
                        if not search_arg:
                            renderer.console.print(f"[{CHROME}]Usage: /search --type <type> <query>[/{CHROME}]\n")
                            continue

                    query = search_arg

                    # Try semantic search if vec is available
                    use_semantic = False
                    if not force_keyword:
                        try:
                            from ..db import has_vec_support as _has_vec
                            from ..services.embeddings import create_embedding_service as _create_emb

                            raw_conn = db._conn if hasattr(db, "_conn") else None
                            if raw_conn and _has_vec(raw_conn):
                                _emb_svc = _create_emb(config)
                                if _emb_svc:
                                    use_semantic = True
                        except Exception:
                            pass

                    if use_semantic:
                        try:
                            query_emb = await _emb_svc.embed(query)
                            if query_emb:
                                sem_results = storage.search_similar_messages(db, query_emb, limit=20)
                                if sem_results:
                                    renderer.console.print(f"\n[bold]Semantic search results for '{query}':[/bold]")
                                    for i, r in enumerate(sem_results):
                                        snippet = r["content"][:80].replace("\n", " ")
                                        dist = r.get("distance", 0)
                                        relevance = max(0, 100 - int(dist * 100))
                                        renderer.console.print(
                                            f"  {i + 1}. [{r['role']}] {snippet}... "
                                            f"[{CHROME}]({relevance}% match, {r['conversation_id'][:8]}...)[/{CHROME}]"
                                        )
                                    renderer.console.print()
                                    continue
                        except Exception:
                            pass  # Fall through to keyword search

                    results = storage.list_conversations(db, search=query, limit=20, conversation_type=type_filter)
                    if results:
                        renderer.console.print(f"\n[bold]Search results for '{query}':[/bold]")
                        for i, c in enumerate(results):
                            msg_count = c.get("message_count", 0)
                            renderer.console.print(
                                f"  {i + 1}. {c['title']} ({msg_count} msgs) [{CHROME}]{c['id'][:8]}...[/{CHROME}]"
                            )
                        renderer.console.print("  Use [bold]/resume <number|slug>[/bold] to open\n")
                    else:
                        renderer.console.print(f"[{CHROME}]No conversations matching '{query}'[/{CHROME}]\n")
                    continue
                elif cmd in ("/skills", "/reload-skills"):
                    if skill_registry:
                        skills = skill_registry.reload(working_dir)
                        if skills:
                            renderer.console.print("\n[bold]Available skills:[/bold]")
                            for s in skills:
                                src = s.source
                                renderer.console.print(f"  /{s.name} - {s.description} [{CHROME}]({src})[/{CHROME}]")
                        else:
                            renderer.console.print(
                                f"\n[{CHROME}]No skills loaded. Add .yaml files to"
                                f" ~/.anteroom/skills/ or .anteroom/skills/[/{CHROME}]"
                            )
                        if skill_registry.load_warnings:
                            n = len(skill_registry.load_warnings)
                            renderer.console.print(f"\n[yellow]Warnings ({n}):[/yellow]")
                            for warn in skill_registry.load_warnings:
                                renderer.console.print(f"  [yellow]- {warn}[/yellow]")
                        if skill_registry.searched_dirs:
                            renderer.console.print(f"\n[{CHROME}]Searched directories:[/{CHROME}]")
                            for sd in skill_registry.searched_dirs:
                                if sd.source == "default":
                                    continue
                                status = f"{sd.skill_count} skill(s)" if sd.exists else "not found"
                                renderer.console.print(f"  [{CHROME}]{sd.path} ({sd.source}) — {status}[/{CHROME}]")
                        renderer.console.print()
                        # Rebuild invoke_skill tool schema so LLM sees updated skill list
                        if config.cli.skills.auto_invoke and tools_openai is not None:
                            tools_openai[:] = [
                                t for t in tools_openai if t.get("function", {}).get("name") != "invoke_skill"
                            ]
                            invoke_def = skill_registry.get_invoke_skill_definition()
                            if invoke_def:
                                tools_openai.append(invoke_def)
                        # Refresh tab-completion skill names
                        completer.update_skill_names([s.name for s in skills])
                    continue
                elif cmd in ("/projects", "/project"):
                    parts = user_input.split(maxsplit=2)
                    sub = parts[1].lower() if len(parts) >= 2 else ""

                    # /projects is shorthand for /project list
                    if cmd == "/projects":
                        sub = "list"

                    if sub == "list" or (cmd == "/project" and not sub):
                        projects = storage.list_projects(db)
                        if not projects:
                            renderer.console.print(
                                f"[{CHROME}]No projects. Create one with /project create <name>[/{CHROME}]\n"
                            )
                            continue
                        renderer.console.print("\n[bold]Projects:[/bold]")
                        for p in projects:
                            cnt = storage.count_project_conversations(db, p["id"])
                            model_info = f" model={p['model']}" if p.get("model") else ""
                            active = (
                                " [green](active)[/green]"
                                if (_active_project[0] and _active_project[0]["id"] == p["id"])
                                else ""
                            )
                            renderer.console.print(
                                f"  {p['name']} — {cnt} conversations{model_info}{active}"
                                f" [{MUTED}]{p['id'][:8]}...[/{MUTED}]"
                            )
                        renderer.console.print()

                    elif sub == "create":
                        pname = parts[2].strip() if len(parts) >= 3 else ""
                        if not pname:
                            renderer.console.print(f"[{CHROME}]Usage: /project create <name>[/{CHROME}]\n")
                            continue
                        existing = storage.get_project_by_name(db, pname)
                        if existing:
                            renderer.render_error(f"Project '{pname}' already exists.")
                            continue
                        renderer.console.print(
                            f"[{CHROME}]Enter instructions (empty line to finish, Enter to skip):[/{CHROME}]"
                        )
                        instr_lines: list[str] = []
                        try:
                            while True:
                                line = await session.prompt_async("  ")
                                if line == "":
                                    break
                                instr_lines.append(line)
                        except (EOFError, KeyboardInterrupt):
                            pass
                        instr_text = "\n".join(instr_lines).strip()
                        renderer.console.print(f"[{CHROME}]Model override (press Enter for default):[/{CHROME}]")
                        try:
                            model_input = await session.prompt_async("  ")
                        except (EOFError, KeyboardInterrupt):
                            model_input = ""
                        model_val = model_input.strip() or None
                        proj = storage.create_project(
                            db,
                            name=pname,
                            instructions=instr_text,
                            model=model_val,
                            **id_kw,
                        )
                        renderer.console.print(
                            f"[green]Created project: {proj['name']}[/green] [{MUTED}]{proj['id'][:8]}...[/{MUTED}]\n"
                        )

                    elif sub in ("select", "use"):
                        target = parts[2].strip() if len(parts) >= 3 else ""
                        if not target:
                            renderer.console.print(f"[{CHROME}]Usage: /project select <name|id>[/{CHROME}]\n")
                            continue
                        proj = _resolve_project(target)
                        if not proj:
                            renderer.render_error(
                                f"Project '{target}' not found. Run /projects to list available projects."
                            )
                            continue
                        _active_project[0] = proj
                        storage.update_conversation_project(db, conv["id"], proj["id"])
                        conv["project_id"] = proj["id"]
                        _inject_project_instructions(proj)
                        if proj.get("model") and not conv.get("model"):
                            current_model = proj["model"]
                            ai_service = create_ai_service(config.ai)
                            ai_service.config.model = proj["model"]
                            renderer.console.print(f"[{CHROME}]Model: {proj['model']}[/{CHROME}]")
                        renderer.console.print(f"[green]Active project: {proj['name']}[/green]\n")

                    elif sub == "edit":
                        target = parts[2].strip() if len(parts) >= 3 else ""
                        if not target:
                            if _active_project[0]:
                                target = _active_project[0]["name"]
                            else:
                                renderer.console.print(f"[{CHROME}]Usage: /project edit <name|id>[/{CHROME}]\n")
                                continue
                        proj = _resolve_project(target)
                        if not proj:
                            renderer.render_error(f"Project '{target}' not found.")
                            continue
                        renderer.console.print(f"[bold]Editing: {proj['name']}[/bold]")
                        renderer.console.print(f"[{CHROME}]New name (Enter to keep '{proj['name']}'):[/{CHROME}]")
                        try:
                            new_name = await session.prompt_async("  ")
                        except (EOFError, KeyboardInterrupt):
                            new_name = ""
                        renderer.console.print(
                            f"[{CHROME}]New instructions (Enter to keep current, 'clear' to remove):[/{CHROME}]"
                        )
                        new_instr_lines: list[str] = []
                        try:
                            while True:
                                line = await session.prompt_async("  ")
                                if line == "":
                                    break
                                new_instr_lines.append(line)
                        except (EOFError, KeyboardInterrupt):
                            pass
                        new_instr = "\n".join(new_instr_lines).strip()
                        renderer.console.print(f"[{CHROME}]New model (Enter to keep, 'clear' to remove):[/{CHROME}]")
                        try:
                            new_model_input = await session.prompt_async("  ")
                        except (EOFError, KeyboardInterrupt):
                            new_model_input = ""
                        update_kw: dict[str, Any] = {}
                        if new_name.strip():
                            update_kw["name"] = new_name.strip()
                        if new_instr == "clear":
                            update_kw["instructions"] = ""
                        elif new_instr:
                            update_kw["instructions"] = new_instr
                        if new_model_input.strip() == "clear":
                            update_kw["model"] = None
                        elif new_model_input.strip():
                            update_kw["model"] = new_model_input.strip()
                        if update_kw:
                            updated = storage.update_project(db, proj["id"], **update_kw)
                            if updated and _active_project[0] and _active_project[0]["id"] == proj["id"]:
                                _active_project[0] = updated
                                _inject_project_instructions(updated)
                            renderer.console.print("[green]Project updated[/green]\n")
                        else:
                            renderer.console.print(f"[{CHROME}]No changes[/{CHROME}]\n")

                    elif sub == "delete":
                        target = parts[2].strip() if len(parts) >= 3 else ""
                        if not target:
                            renderer.console.print(f"[{CHROME}]Usage: /project delete <name|id>[/{CHROME}]\n")
                            continue
                        proj = _resolve_project(target)
                        if not proj:
                            renderer.render_error(f"Project '{target}' not found.")
                            continue
                        renderer.console.print(f"[yellow]Delete project '{proj['name']}'? (y/N)[/yellow]")
                        try:
                            confirm = await session.prompt_async("  ")
                        except (EOFError, KeyboardInterrupt):
                            confirm = "n"
                        if confirm.strip().lower() != "y":
                            renderer.console.print(f"[{CHROME}]Cancelled[/{CHROME}]\n")
                            continue
                        storage.delete_project(db, proj["id"])
                        if _active_project[0] and _active_project[0]["id"] == proj["id"]:
                            _active_project[0] = None
                            extra_system_prompt = _strip_project_instructions(extra_system_prompt)
                        renderer.console.print(f"[green]Deleted: {proj['name']}[/green]\n")

                    elif sub == "clear":
                        if not _active_project[0]:
                            renderer.console.print(f"[{CHROME}]No active project[/{CHROME}]\n")
                            continue
                        old_name = _active_project[0]["name"]
                        _active_project[0] = None
                        storage.update_conversation_project(db, conv["id"], None)
                        conv["project_id"] = None
                        extra_system_prompt = _strip_project_instructions(extra_system_prompt)
                        renderer.console.print(f"[{CHROME}]Cleared project: {old_name}[/{CHROME}]\n")

                    elif sub == "sources":
                        proj = _active_project[0]
                        if not proj:
                            renderer.console.print(
                                f"[{CHROME}]No active project. Use /project select <name> first.[/{CHROME}]\n"
                            )
                            continue
                        sources = storage.get_project_sources(db, proj["id"])
                        if not sources:
                            renderer.console.print(f"[{CHROME}]No sources linked to '{proj['name']}'[/{CHROME}]\n")
                            continue
                        renderer.console.print(f"\n[bold]Sources for {proj['name']}:[/bold]")
                        for s in sources:
                            title = s.get("title") or s.get("url") or s["id"][:8]
                            renderer.console.print(f"  {title} [{MUTED}]{s['id'][:8]}...[/{MUTED}]")
                        renderer.console.print()

                    else:
                        if _active_project[0]:
                            renderer.console.print(f"[{CHROME}]Active project: {_active_project[0]['name']}[/{CHROME}]")
                        renderer.console.print(
                            f"[{CHROME}]Usage: /project [list|create|select|edit|delete|clear|sources][/{CHROME}]\n"
                        )
                    continue
                elif cmd == "/artifact-check":
                    from .services import artifact_health

                    _ahc_report = artifact_health.run_health_check(db, project_dir=working_dir)
                    renderer.console.print()
                    renderer.console.print("[bold]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold]")
                    renderer.console.print("[bold]  🏥 Artifact Health Check[/bold]")
                    renderer.console.print("[bold]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold]")
                    renderer.console.print()
                    renderer.console.print(
                        f"  📊 Loaded: {_ahc_report.artifact_count} artifacts from {_ahc_report.pack_count} packs"
                    )
                    renderer.console.print(
                        f"  📏 Total size: {_ahc_report.total_size_bytes:,} bytes"
                        f" (~{_ahc_report.estimated_tokens:,} tokens)"
                    )
                    renderer.console.print()
                    if not _ahc_report.issues:
                        renderer.console.print("[green]  ✅ No issues found[/green]\n")
                    else:
                        for _ahc_issue in _ahc_report.issues:
                            _icon = {"error": "❌", "warn": "⚠️", "info": "💡"}.get(_ahc_issue.severity.value, "•")
                            renderer.console.print(f"  {_icon} {_ahc_issue.message}")
                        renderer.console.print()
                        _parts_summary = []
                        if _ahc_report.error_count:
                            _parts_summary.append(f"❌ {_ahc_report.error_count} errors")
                        if _ahc_report.warn_count:
                            _parts_summary.append(f"⚠️ {_ahc_report.warn_count} warnings")
                        if _ahc_report.info_count:
                            _parts_summary.append(f"💡 {_ahc_report.info_count} suggestions")
                        renderer.console.print(f"  {' '.join(_parts_summary)}\n")
                    continue
                elif cmd == "/mcp":
                    parts = user_input.split()
                    if len(parts) == 1:
                        if mcp_manager:
                            renderer.render_mcp_status(mcp_manager.get_server_statuses())
                        else:
                            renderer.console.print(f"[{CHROME}]No MCP servers configured.[/{CHROME}]\n")
                    elif len(parts) >= 2 and parts[1].lower() == "status":
                        if not mcp_manager:
                            renderer.render_error("No MCP servers configured")
                            continue
                        if len(parts) >= 3:
                            renderer.render_mcp_server_detail(parts[2], mcp_manager.get_server_statuses(), mcp_manager)
                        else:
                            renderer.render_mcp_status(mcp_manager.get_server_statuses())
                    elif len(parts) >= 3:
                        action = parts[1].lower()
                        server_name = parts[2]
                        if not mcp_manager:
                            renderer.render_error("No MCP servers configured")
                            continue
                        try:
                            if action == "connect":
                                await mcp_manager.connect_server(server_name)
                                status = mcp_manager.get_server_statuses().get(server_name, {})
                                if status.get("status") == "connected":
                                    renderer.console.print(f"[green]Connected: {server_name}[/green]\n")
                                else:
                                    err = status.get("error_message", "unknown error")
                                    renderer.render_error(f"Failed to connect '{server_name}': {err}")
                            elif action == "disconnect":
                                await mcp_manager.disconnect_server(server_name)
                                renderer.console.print(f"[{CHROME}]Disconnected: {server_name}[/{CHROME}]\n")
                            elif action == "reconnect":
                                await mcp_manager.reconnect_server(server_name)
                                status = mcp_manager.get_server_statuses().get(server_name, {})
                                if status.get("status") == "connected":
                                    renderer.console.print(f"[green]Reconnected: {server_name}[/green]\n")
                                else:
                                    err = status.get("error_message", "unknown error")
                                    renderer.render_error(f"Failed to reconnect '{server_name}': {err}")
                            else:
                                renderer.render_error(
                                    f"Unknown action: {action}. Use connect, disconnect, reconnect, or status."
                                )
                                continue
                            _rebuild_tools()
                        except ValueError as e:
                            renderer.render_error(str(e))
                    else:
                        renderer.console.print(
                            f"[{CHROME}]Usage: /mcp [status [name]|connect|disconnect|reconnect <name>][/{CHROME}]\n"
                        )
                    continue
                elif cmd == "/model":
                    parts = user_input.split(maxsplit=1)
                    if len(parts) < 2:
                        renderer.console.print(f"[{CHROME}]Current model: {current_model}[/{CHROME}]")
                        renderer.console.print(f"[{CHROME}]Usage: /model <model_name>[/{CHROME}]\n")
                        continue
                    new_model = parts[1].strip()
                    current_model = new_model
                    ai_service = create_ai_service(config.ai)
                    ai_service.config.model = new_model
                    renderer.console.print(f"[{CHROME}]Switched to model: {new_model}[/{CHROME}]\n")
                    continue
                elif cmd == "/plan":
                    sub, inline_prompt = parse_plan_command(user_input)
                    if sub in ("on", "start"):
                        if _plan_active[0]:
                            renderer.console.print(f"[{CHROME}]Already in planning mode[/{CHROME}]\n")
                        else:
                            _apply_plan_mode(conv["id"])
                            renderer.console.print(
                                f"[yellow]Planning mode active.[/yellow] The AI will explore and write a plan.\n"
                                f"  [{MUTED}]Use /plan approve to execute, /plan off to exit.[/{MUTED}]\n"
                            )
                        continue
                    elif sub == "approve":
                        if not _plan_active[0]:
                            renderer.console.print(f"[{CHROME}]Not in planning mode[/{CHROME}]\n")
                        elif _plan_file[0] is None:
                            renderer.console.print(f"[{CHROME}]No plan file path set[/{CHROME}]\n")
                        else:
                            content = read_plan(_plan_file[0])
                            if not content:
                                renderer.console.print(
                                    f"[{CHROME}]No plan file found at {_plan_file[0]}[/{CHROME}]\n"
                                    f"  [{MUTED}]The AI needs to write the plan first.[/{MUTED}]\n"
                                )
                            else:
                                _exit_plan_mode(plan_content=content)
                                delete_plan(_plan_file[0])
                                # Parse implementation steps for live checklist
                                steps = parse_plan_steps(content)
                                _plan_checklist_steps.clear()
                                _plan_checklist_steps.extend(steps)
                                _plan_current_step[0] = 0
                                if steps:
                                    renderer.start_plan(steps)
                                renderer.console.print(
                                    "[green]Plan approved.[/green] Full tools restored.\n"
                                    f"  [{MUTED}]Plan injected into context. "
                                    f"Send a message to start.[/{MUTED}]\n"
                                )
                        continue
                    elif sub == "status":
                        if _plan_active[0]:
                            renderer.console.print("[yellow]Planning mode: active[/yellow]")
                            if _plan_file[0]:
                                content = read_plan(_plan_file[0])
                                if content:
                                    renderer.console.print(f"  Plan file: {_plan_file[0]} ({len(content)} chars)")
                                    lines = content.splitlines()
                                    preview = lines[:20]
                                    renderer.console.print()
                                    for line in preview:
                                        renderer.console.print(f"  {line}")
                                    if len(lines) > 20:
                                        renderer.console.print(
                                            f"\n  [{MUTED}]... {len(lines) - 20} more lines[/{MUTED}]"
                                        )
                                else:
                                    renderer.console.print(
                                        f"  [{MUTED}]Plan file: {_plan_file[0]} (not yet written)[/{MUTED}]"
                                    )
                        else:
                            renderer.console.print(f"[{CHROME}]Planning mode: off[/{CHROME}]")
                        renderer.console.print()
                        continue
                    elif sub == "edit":
                        if not _plan_active[0]:
                            renderer.console.print(f"[{CHROME}]Not in planning mode[/{CHROME}]\n")
                            continue
                        if _plan_file[0] is None:
                            renderer.console.print(f"[{CHROME}]No plan file path set[/{CHROME}]\n")
                            continue
                        edit_args = user_input.split(maxsplit=2)
                        edit_instruction = edit_args[2] if len(edit_args) > 2 else ""
                        if edit_instruction:
                            user_input = f"Revise the plan based on this feedback: {edit_instruction}"
                        else:
                            content = read_plan(_plan_file[0])
                            if not content:
                                renderer.console.print(
                                    f"[{CHROME}]No plan file yet — the AI needs to write it first.[/{CHROME}]\n"
                                )
                                continue
                            import subprocess

                            editor = get_editor()
                            subprocess.call([editor, str(_plan_file[0])])
                            renderer.console.print(
                                "Plan updated. Use [bold]/plan status[/bold] to review, "
                                "[bold]/plan approve[/bold] to execute.\n"
                            )
                            continue
                    elif sub == "reject":
                        if not _plan_active[0]:
                            renderer.console.print(f"[{CHROME}]Not in planning mode[/{CHROME}]\n")
                            continue
                        reject_parts = user_input.split(maxsplit=2)
                        if len(reject_parts) < 3 or not reject_parts[2].strip():
                            renderer.console.print(f"[{CHROME}]Usage: /plan reject <reason for rejection>[/{CHROME}]\n")
                            continue
                        reason = reject_parts[2].strip()
                        user_input = (
                            f"The plan has been rejected. Reason: {reason}\n\n"
                            "Please revise the plan based on this feedback and write the updated "
                            "plan to the same plan file. Keep exploring if you need more information."
                        )
                    elif sub == "off":
                        if not _plan_active[0]:
                            renderer.console.print(f"[{CHROME}]Not in planning mode[/{CHROME}]\n")
                        else:
                            _exit_plan_mode()
                            renderer.console.print(f"[{CHROME}]Planning mode off. Full tools restored.[/{CHROME}]\n")
                        continue
                    else:
                        # Inline prompt mode: /plan <prompt text>
                        if not inline_prompt:
                            renderer.console.print(
                                f"[{CHROME}]Usage: /plan [on|approve|status|edit|reject|off]"
                                f" or /plan <prompt>[/{CHROME}]\n"
                            )
                            continue
                        if not _plan_active[0]:
                            _apply_plan_mode(conv["id"])
                            renderer.console.print("[yellow]Planning mode active.[/yellow]\n")
                        user_input = inline_prompt
                        # Fall through to agent loop — do NOT continue
                elif cmd == "/verbose":
                    new_v = renderer.cycle_verbosity()
                    renderer.render_verbosity_change(new_v)
                    continue
                elif cmd == "/detail":
                    renderer.render_tool_detail()
                    continue
                elif cmd == "/resume":
                    parts = user_input.split(maxsplit=1)
                    if len(parts) < 2:
                        picked = await _show_resume_picker()
                        if picked is None:
                            continue
                        loaded = storage.get_conversation(db, picked["id"])
                    else:
                        target = parts[1].strip()
                        loaded = _resolve_conversation(db, target)
                    if not loaded:
                        renderer.render_error("Conversation not found. Use /list to see conversations.")
                        continue
                    conv = loaded
                    working_dir = _restore_working_dir(conv, tool_registry, working_dir)
                    ai_messages = _load_conversation_messages(db, conv["id"])
                    is_first_message = False
                    _show_resume_info(db, conv, ai_messages)
                    continue
                elif cmd == "/rewind":
                    stored = storage.list_messages(db, conv["id"])
                    if len(stored) < 2:
                        renderer.console.print(f"[{CHROME}]Not enough messages to rewind[/{CHROME}]\n")
                        continue

                    renderer.console.print("\n[bold]Messages:[/bold]")
                    for msg in stored:
                        role_label = "You" if msg["role"] == "user" else "AI"
                        preview = msg["content"][:80].replace("\n", " ")
                        if len(msg["content"]) > 80:
                            preview += "..."
                        renderer.console.print(f"  {msg['position']}. [{role_label}] {preview}")

                    renderer.console.print(
                        f"\n[{CHROME}]Enter position to rewind to (keep that message, delete after):[/{CHROME}]"
                    )
                    try:
                        pos_input = input("  Position: ").strip()
                    except (EOFError, KeyboardInterrupt):
                        renderer.console.print(f"[{CHROME}]Cancelled[/{CHROME}]\n")
                        continue

                    if not pos_input.isdigit():
                        renderer.render_error("Invalid position")
                        continue

                    target_pos = int(pos_input)
                    positions = [m["position"] for m in stored]
                    if target_pos not in positions:
                        renderer.render_error(f"Position {target_pos} not found")
                        continue

                    msgs_after = [m for m in stored if m["position"] > target_pos]
                    msg_ids_after = [m["id"] for m in msgs_after]
                    file_paths = collect_file_paths(db, msg_ids_after)

                    undo_files = False
                    if file_paths:
                        renderer.console.print(
                            f"\n[yellow]{len(file_paths)} file(s) were modified after this point:[/yellow]"
                        )
                        for fp in sorted(file_paths):
                            renderer.console.print(f"  - {fp}")
                        try:
                            answer = input("  Undo file changes? [y/N] ").strip().lower()
                            undo_files = answer in ("y", "yes")
                        except (EOFError, KeyboardInterrupt):
                            renderer.console.print(f"[{CHROME}]Cancelled[/{CHROME}]\n")
                            continue

                    result = await rewind_service(
                        db=db,
                        conversation_id=conv["id"],
                        to_position=target_pos,
                        undo_files=undo_files,
                        working_dir=working_dir,
                    )

                    ai_messages = _load_conversation_messages(db, conv["id"])

                    summary = f"Rewound {result.deleted_messages} message(s)"
                    if result.reverted_files:
                        summary += f", reverted {len(result.reverted_files)} file(s)"
                    if result.skipped_files:
                        summary += f", {len(result.skipped_files)} skipped"
                    renderer.console.print(f"[{CHROME}]{summary}[/{CHROME}]\n")

                    if result.skipped_files:
                        for sf in result.skipped_files:
                            renderer.console.print(f"  [yellow]Skipped: {sf}[/yellow]")
                        renderer.console.print()
                    continue

            # Check for skill invocation — preserve original for title generation
            original_user_input = user_input
            if skill_registry and user_input.startswith("/"):
                is_skill, skill_prompt = skill_registry.resolve_input(user_input)
                if is_skill:
                    user_input = skill_prompt

            # For note/document types, save message without AI response
            current_conv_type = conv.get("type", "chat")
            if current_conv_type in ("note", "document"):
                expanded = _expand_file_references(
                    user_input, working_dir, file_max_chars=config.cli.file_reference_max_chars
                )
                storage.create_message(db, conv["id"], "user", expanded, **id_kw)
                renderer.console.print(f"[{CHROME}]Entry added to '{conv.get('title', 'Untitled')}'[/{CHROME}]\n")
                if is_first_message:
                    is_first_message = False
                continue

            # Visual separation is handled by start_thinking() which writes
            # \n + Thinking... as a single atomic write to avoid a race with
            # prompt_toolkit's cursor teardown on the first message (#249).

            # Expand file references
            expanded = _expand_file_references(
                user_input, working_dir, file_max_chars=config.cli.file_reference_max_chars
            )

            # Auto-compact if approaching context limit (thresholds from config)
            token_estimate = _estimate_tokens(ai_messages)
            auto_compact_threshold = config.cli.context_auto_compact_tokens
            warn_threshold = config.cli.context_warn_tokens
            if token_estimate > auto_compact_threshold:
                renderer.console.print(
                    f"[yellow]Context approaching limit (~{token_estimate:,} tokens). Auto-compacting...[/yellow]"
                )
                await _compact_messages(ai_service, ai_messages, db, conv["id"])
            elif token_estimate > warn_threshold:
                renderer.console.print(
                    f"[yellow]Context: ~{token_estimate:,} tokens. Use /compact to free space.[/yellow]"
                )

            # Store user message
            storage.create_message(db, conv["id"], "user", expanded, **id_kw)
            ai_messages.append({"role": "user", "content": expanded})

            # RAG: retrieve relevant context from knowledge base
            if config.rag.enabled and not _plan_active[0]:
                try:
                    from ..services.rag import format_rag_context, retrieve_context, strip_rag_context

                    _rag_emb = await _get_rag_embedding_service()
                    if _rag_emb:
                        _rag_chunks = await retrieve_context(
                            query=expanded,
                            db=db,
                            embedding_service=_rag_emb,
                            config=config.rag,
                            current_conversation_id=conv["id"],
                        )
                        # Strip any previous RAG context and inject fresh
                        extra_system_prompt = strip_rag_context(extra_system_prompt)
                        if _rag_chunks:
                            extra_system_prompt += format_rag_context(_rag_chunks)
                            renderer.console.print(
                                f"  [{MUTED}][RAG: {len(_rag_chunks)} relevant chunk(s) retrieved][/{MUTED}]"
                            )
                except Exception:
                    logger.debug("RAG retrieval failed in CLI", exc_info=True)

            # Build message queue for queued follow-ups during agent loop
            msg_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            if skill_msg_queue_ref is not None:
                skill_msg_queue_ref[0] = msg_queue

            # Stream response
            renderer.clear_turn_history()
            renderer.clear_subagent_state()
            if subagent_limiter is not None:
                subagent_limiter.reset()
            if rate_limiter is not None:
                rate_limiter.reset()
            cancel_event = asyncio.Event()
            _current_cancel_event[0] = cancel_event
            if cancel_event_ref is not None:
                cancel_event_ref[0] = cancel_event
            loop = asyncio.get_event_loop()
            original_handler = signal.getsignal(signal.SIGINT)
            _add_signal_handler(loop, signal.SIGINT, cancel_event.set)

            agent_busy.set()

            thinking = False
            user_attempt = 0

            _budget_cfg = config.cli.usage.budgets

            async def _get_token_totals() -> tuple[int, int]:
                return (
                    storage.get_conversation_token_total(db, conv["id"]),
                    storage.get_daily_token_total(db),
                )

            try:
                response_token_count = 0
                total_elapsed = 0.0
                _pending_usage: dict | None = None

                # Drain any messages that arrived while we were setting up
                def _warn(cmd: str) -> None:
                    renderer.console.print(
                        f"[yellow]Command {cmd} ignored during streaming. Queue messages only.[/yellow]"
                    )

                await _drain_input_to_msg_queue(
                    input_queue,
                    msg_queue,
                    working_dir,
                    db,
                    conv["id"],
                    cancel_event,
                    exit_flag,
                    warn_callback=_warn,
                    identity_kwargs=id_kw,
                    file_max_chars=config.cli.file_reference_max_chars,
                    skill_registry=skill_registry,
                )

                while True:
                    user_attempt += 1
                    should_retry = False

                    async for event in run_agent_loop(
                        ai_service=ai_service,
                        messages=ai_messages,
                        tool_executor=tool_executor,
                        tools_openai=tools_openai,
                        cancel_event=cancel_event,
                        extra_system_prompt=extra_system_prompt,
                        max_iterations=config.cli.max_tool_iterations,
                        message_queue=msg_queue,
                        narration_cadence=ai_service.config.narration_cadence,
                        tool_output_max_chars=config.cli.tool_output_max_chars,
                        auto_plan_threshold=(
                            config.cli.planning.auto_threshold_tools
                            if not _plan_active[0] and config.cli.planning.auto_mode != "off"
                            else 0
                        ),
                        budget_config=_budget_cfg,
                        get_token_totals=_get_token_totals,
                        dlp_scanner=dlp_scanner,
                        injection_detector=injection_detector,
                        output_filter=output_filter,
                    ):
                        # Drain input_queue into msg_queue during streaming
                        await _drain_input_to_msg_queue(
                            input_queue,
                            msg_queue,
                            working_dir,
                            db,
                            conv["id"],
                            cancel_event,
                            exit_flag,
                            warn_callback=_warn,
                            identity_kwargs=id_kw,
                            file_max_chars=config.cli.file_reference_max_chars,
                            skill_registry=skill_registry,
                        )

                        if event.kind == "thinking":
                            if not thinking:
                                # Advance plan: if a step was in_progress, mark it complete
                                # and move to the next step
                                if _plan_checklist_steps and _plan_current_step[0] < len(_plan_checklist_steps):
                                    idx = _plan_current_step[0]
                                    if renderer.get_plan_steps() and idx < len(renderer.get_plan_steps()):
                                        step_state = renderer.get_plan_steps()[idx]
                                        if step_state.get("status") == "in_progress":
                                            renderer.update_plan_step(idx, "complete")
                                            _plan_current_step[0] = idx + 1
                                renderer.start_thinking(newline=True)
                                thinking = True
                        elif event.kind == "phase":
                            renderer.set_thinking_phase(event.data.get("phase", ""))
                        elif event.kind == "retrying":
                            renderer.set_retrying(event.data)
                        elif event.kind == "token":
                            if not thinking:
                                renderer.start_thinking(newline=True)
                                thinking = True
                            renderer.render_token(event.data["content"])
                            renderer.increment_thinking_tokens()
                            renderer.increment_streaming_chars(len(event.data.get("content", "")))
                            renderer.update_thinking()
                            enc = _get_tiktoken_encoding()
                            if enc:
                                response_token_count += len(enc.encode(event.data["content"], allowed_special="all"))
                            else:
                                response_token_count += max(1, len(event.data["content"]) // 4)
                        elif event.kind == "tool_call_start":
                            if thinking:
                                total_elapsed += await renderer.stop_thinking()
                                thinking = False
                            # Advance plan checklist: mark current step as in_progress
                            if _plan_checklist_steps and _plan_current_step[0] < len(_plan_checklist_steps):
                                idx = _plan_current_step[0]
                                renderer.update_plan_step(idx, "in_progress")
                            renderer.render_tool_call_start(event.data["tool_name"], event.data["arguments"])
                        elif event.kind == "tool_call_end":
                            renderer.render_tool_call_end(
                                event.data["tool_name"], event.data["status"], event.data["output"]
                            )
                        elif event.kind == "auto_plan_suggest":
                            _auto_mode = config.cli.planning.auto_mode
                            if _auto_mode == "auto" and not _plan_active[0]:
                                if thinking:
                                    total_elapsed += await renderer.stop_thinking()
                                    thinking = False
                                renderer.console.print(
                                    "\n[yellow]Complex task detected "
                                    f"({event.data['tool_calls']} tool calls). "
                                    "Switching to planning mode...[/yellow]\n"
                                )
                                cancel_event.set()
                                _apply_plan_mode(conv["id"])
                            elif _auto_mode == "suggest" and not _plan_active[0]:
                                if thinking:
                                    total_elapsed += await renderer.stop_thinking()
                                    thinking = False
                                renderer.console.print(
                                    f"\n[yellow]This task looks complex "
                                    f"({event.data['tool_calls']} tool calls). "
                                    f"Consider using /plan for better results.[/yellow]\n"
                                )
                                renderer.start_thinking(newline=True)
                                thinking = True
                        elif event.kind == "usage":
                            _pending_usage = event.data
                        elif event.kind == "assistant_message":
                            if event.data["content"]:
                                msg = storage.create_message(
                                    db, conv["id"], "assistant", event.data["content"], **id_kw
                                )
                                if _pending_usage:
                                    storage.update_message_usage(
                                        db,
                                        msg["id"],
                                        _pending_usage.get("prompt_tokens", 0),
                                        _pending_usage.get("completion_tokens", 0),
                                        _pending_usage.get("total_tokens", 0),
                                        _pending_usage.get("model", ""),
                                    )
                                    _pending_usage = None
                        elif event.kind == "dlp_blocked":
                            if thinking:
                                total_elapsed += await renderer.stop_thinking(
                                    error_msg="Response blocked by DLP policy"
                                )
                                thinking = False
                            else:
                                renderer.render_error("Response blocked by DLP policy")
                        elif event.kind == "dlp_warning":
                            rules = ", ".join(event.data.get("matches", []))
                            renderer.render_error(f"DLP warning: sensitive data detected [{rules}]")
                        elif event.kind == "injection_detected":
                            action = event.data.get("action", "warn")
                            detail = event.data.get("detail", "prompt injection detected")
                            if action == "block":
                                renderer.render_error(f"Tool output blocked: {detail}")
                            else:
                                renderer.render_error(f"Injection warning: {detail}")
                        elif event.kind == "output_filter_blocked":
                            if thinking:
                                total_elapsed += await renderer.stop_thinking(
                                    error_msg="Response blocked by output content filter"
                                )
                                thinking = False
                            else:
                                renderer.render_error("Response blocked by output content filter")
                        elif event.kind == "output_filter_warning":
                            rules = ", ".join(event.data.get("matches", []))
                            renderer.render_error(f"Output filter warning: forbidden content detected [{rules}]")
                        elif event.kind == "queued_message":
                            if thinking:
                                total_elapsed += await renderer.stop_thinking()
                                thinking = False
                            renderer.save_turn_history()
                            renderer.render_newline()
                            renderer.render_response_end()
                            renderer.render_newline()
                            renderer.console.print(f"[{CHROME}]Processing queued message...[/{CHROME}]")
                            renderer.render_newline()
                            renderer.clear_turn_history()
                            response_token_count = 0
                        elif event.kind == "error":
                            error_msg = event.data.get("message", "Unknown error")
                            retryable = event.data.get("retryable", False)
                            if thinking and retryable and user_attempt < config.cli.max_retries:
                                should_retry = await renderer.thinking_countdown(
                                    config.cli.retry_delay, cancel_event, error_msg
                                )
                                if should_retry and not cancel_event.is_set():
                                    cancel_event.clear()
                                    renderer.start_thinking()
                                else:
                                    total_elapsed += await renderer.stop_thinking(cancel_msg="cancelled")
                                    thinking = False
                            elif thinking and retryable and user_attempt >= config.cli.max_retries:
                                total_elapsed += await renderer.stop_thinking(
                                    error_msg=f"{error_msg} · {user_attempt} attempts failed"
                                )
                                thinking = False
                            elif thinking:
                                total_elapsed += await renderer.stop_thinking(error_msg=error_msg)
                                thinking = False
                            else:
                                renderer.render_error(error_msg)
                        elif event.kind == "budget_warning":
                            if thinking:
                                total_elapsed += await renderer.stop_thinking()
                                thinking = False
                            renderer.render_warning(event.data.get("message", "Token budget warning"))
                        elif event.kind == "done":
                            # Mark any in-progress plan step as complete
                            if _plan_checklist_steps and _plan_current_step[0] < len(_plan_checklist_steps):
                                idx = _plan_current_step[0]
                                if renderer.get_plan_steps() and idx < len(renderer.get_plan_steps()):
                                    step_state = renderer.get_plan_steps()[idx]
                                    if step_state.get("status") == "in_progress":
                                        renderer.update_plan_step(idx, "complete")
                            collapse = bool(_plan_checklist_steps)
                            if thinking and cancel_event.is_set():
                                total_elapsed += await renderer.stop_thinking(
                                    cancel_msg="cancelled", collapse_plan=collapse
                                )
                                thinking = False
                            elif thinking:
                                total_elapsed += await renderer.stop_thinking(collapse_plan=collapse)
                                thinking = False
                            # Clear plan checklist state after collapsing
                            if _plan_checklist_steps:
                                _plan_checklist_steps.clear()
                                _plan_current_step[0] = 0
                            if not cancel_event.is_set():
                                renderer.save_turn_history()
                                renderer.render_response_end()
                                renderer.render_newline()
                                context_tokens = _estimate_tokens(ai_messages)
                                renderer.render_context_footer(
                                    current_tokens=context_tokens,
                                    max_context=config.cli.model_context_window,
                                    auto_compact_threshold=config.cli.context_auto_compact_tokens,
                                    response_tokens=response_token_count,
                                    elapsed=total_elapsed,
                                )
                                renderer.render_newline()

                    if not should_retry:
                        break

                # Generate title on first exchange (skip if user cancelled)
                if is_first_message:
                    is_first_message = False
                    if not cancel_event.is_set():
                        try:
                            title = await ai_service.generate_title(original_user_input)
                            storage.update_conversation_title(db, conv["id"], title)
                        except Exception:
                            pass

            except KeyboardInterrupt:
                if thinking:
                    renderer.stop_thinking_sync()
                renderer.render_response_end()
            finally:
                if thinking:
                    renderer.stop_thinking_sync()
                    thinking = False
                if not _has_pending_work():
                    agent_busy.clear()
                    session.app.invalidate()
                _current_cancel_event[0] = None
                if cancel_event_ref is not None:
                    cancel_event_ref[0] = None
                cancel_event.set()
                _remove_signal_handler(loop, signal.SIGINT)
                if not _IS_WINDOWS:
                    signal.signal(signal.SIGINT, original_handler)

    from prompt_toolkit.patch_stdout import patch_stdout as _patch_stdout

    with _patch_stdout():
        renderer.use_stdout_console()
        renderer.set_tool_dedup(config.cli.tool_dedup)
        renderer.configure_thresholds(
            esc_hint_delay=config.cli.esc_hint_delay,
            stall_display=config.cli.stall_display_threshold,
            stall_warning=config.cli.stall_warning_threshold,
        )
        input_task = asyncio.create_task(_collect_input())
        runner_task = asyncio.create_task(_agent_runner())

        # Wait for either task to signal exit
        done_tasks, pending_tasks = await asyncio.wait({input_task, runner_task}, return_when=asyncio.FIRST_COMPLETED)
        exit_flag.set()
        for t in pending_tasks:
            t.cancel()
            try:
                await t
            except BaseException:
                pass

    # Show resume hint after patch_stdout exits so output isn't swallowed
    if conv.get("id") and not is_first_message:
        resume_label = conv.get("slug") or conv["id"][:8]
        from rich.console import Console as _HintConsole

        _hint_console = _HintConsole(stderr=True)
        _hint_console.print(
            f"\n[{CHROME}]To resume this conversation:[/{CHROME}]"
            f"\n[{CHROME}]  aroom chat -c              [/{CHROME}][{MUTED}](continue last)[/{MUTED}]"
            f"\n[{CHROME}]  aroom chat -r {resume_label}[/{CHROME}][{MUTED}]"
            f" (this conversation)[/{MUTED}]\n"
        )


async def _compact_messages(
    ai_service: AIService,
    ai_messages: list[dict[str, Any]],
    db: Any,
    conversation_id: str,
) -> None:
    """Summarize conversation history to reduce context size."""
    if len(ai_messages) < 4:
        renderer.console.print(f"[{CHROME}]Not enough messages to compact[/{CHROME}]\n")
        return

    original_count = len(ai_messages)
    original_tokens = _estimate_tokens(ai_messages)

    history_text = _build_compaction_history(ai_messages)

    summary_prompt = (
        "Summarize the following conversation concisely, preserving:\n"
        "- Key decisions and conclusions\n"
        "- File paths that were read, written, or edited\n"
        "- Important code changes and their purpose\n"
        "- Which steps of any multi-step plan have been COMPLETED (tool_result SUCCESS) vs remaining\n"
        "- Current state of the task — what has been done and what is next\n"
        "- Any errors encountered and how they were resolved\n\n" + history_text
    )

    try:
        renderer.console.print(f"[{CHROME}]Generating summary...[/{CHROME}]")
        response = await ai_service.client.chat.completions.create(
            model=ai_service.config.model,
            messages=[{"role": "user", "content": summary_prompt}],
            max_completion_tokens=1000,
        )
        summary = response.choices[0].message.content or "Conversation summary unavailable."
    except Exception:
        renderer.render_error("Failed to generate summary")
        return

    ai_messages.clear()
    compact_note = (
        f"Previous conversation summary "
        f"(auto-compacted from {original_count} messages, "
        f"~{original_tokens:,} tokens):\n\n{summary}"
    )
    ai_messages.append({"role": "system", "content": compact_note})

    new_tokens = _estimate_tokens(ai_messages)
    renderer.render_compact_done(original_count, 1)
    renderer.console.print(f"  [{CHROME}]~{original_tokens:,} -> ~{new_tokens:,} tokens[/{CHROME}]\n")

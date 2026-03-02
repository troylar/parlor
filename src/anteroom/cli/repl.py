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
from ..services.context_trust import (
    sanitize_trust_tags,
    trusted_section_marker,
    untrusted_section_marker,
    wrap_untrusted,
)
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
                if msvcrt.kbhit():  # type: ignore[attr-defined]
                    ch = msvcrt.getch()  # type: ignore[attr-defined]
                    if ch == b"\x1b":
                        # Distinguish bare Escape from escape sequences (arrow keys, etc.)
                        time.sleep(0.05)
                        if not msvcrt.kbhit():  # type: ignore[attr-defined]
                            cancel_event.set()
                            return
                        # Consume the rest of the escape sequence
                        while msvcrt.kbhit():  # type: ignore[attr-defined]
                            msvcrt.getch()  # type: ignore[attr-defined]
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
_SUB_PROMPT_TIMEOUT = 300  # seconds — failsafe for stuck sub-prompts


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

    # In fullscreen mode the input pane handles display; raw cursor codes
    # would be meaningless inside the OutputPaneWriter.
    if renderer.is_fullscreen():
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


_tiktoken_encoding: Any = None


def _get_tiktoken_encoding() -> Any:
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
    stored_dir: str | None = conv.get("working_dir")
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
    identity_kwargs: dict[str, Any] | None = None,
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
    artifact_registry: Any = None,
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

    # Inject artifacts (instructions, rules, context) from the registry
    if artifact_registry is not None:
        from ..services.artifacts import ArtifactType

        for art_type in (ArtifactType.INSTRUCTION, ArtifactType.RULE, ArtifactType.CONTEXT):
            artifacts = artifact_registry.list_all(artifact_type=art_type)
            for art in artifacts:
                if art.content.strip():
                    if art.source == "built_in":
                        tag = f'<artifact type="{art_type.value}" fqn="{art.fqn}">'
                        parts.append(f"\n{tag}\n{art.content}\n</artifact>")
                    else:
                        wrapped = wrap_untrusted(art.content, origin=f"artifact:{art.fqn}", content_type=art_type.value)
                        parts.append(f"\n{wrapped}")

    return "\n".join(parts)


def _identity_kwargs(config: AppConfig) -> dict[str, Any]:
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

    if renderer.is_fullscreen() and renderer.get_fullscreen_layout() is not None:
        while True:
            body: list[tuple[str, str]] = [
                ("class:dialog.body", f"  Path: {file_path}\n"),
                ("class:dialog.body", f"  Size: {file_size:,} bytes\n\n"),
                ("class:dialog.option.key", "  [y]"),
                ("class:dialog.option", " Trust this folder   "),
                ("class:dialog.option.key", "[r]"),
                ("class:dialog.option", " Trust parent\n"),
                ("class:dialog.option.key", "  [v]"),
                ("class:dialog.option", " View content        "),
                ("class:dialog.option.key", "[n]"),
                ("class:dialog.option", " Skip"),
            ]
            answer = await renderer.get_fullscreen_layout().show_dialog(
                title="Project Trust — ANTEROOM.md",
                body_fragments=body,
            )
            if answer is None:
                renderer.console.print(f"  [{MUTED}]Skipped: project context not loaded[/{MUTED}]\n")
                return None
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
        return None  # unreachable but keeps mypy happy

    try:
        from prompt_toolkit import PromptSession as _TrustSession

        _trust_session: Any = _TrustSession()

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


async def _resolve_pack_interactive(
    db: Any,
    ns: str,
    name: str,
    *,
    escape_markup: bool = False,
) -> dict[str, Any] | None:
    """Resolve a pack by namespace/name, prompting the user to disambiguate if needed.

    Returns the pack dict on success, ``None`` if not found or user cancels.
    When *escape_markup* is True, Rich markup characters in ``ns``/``name`` are
    escaped in console output (used by attach/detach which accept arbitrary input).
    """
    _pm, _pc = packs_service.resolve_pack(db, ns, name)
    if not _pm and _pc:
        if renderer.is_fullscreen() and renderer.get_fullscreen_layout() is not None:
            _pk_body: list[tuple[str, str]] = [
                ("class:dialog.body", f"  Multiple packs match @{ns}/{name}:\n\n"),
            ]
            for _pi, _c in enumerate(_pc, 1):
                _pk_body.append(
                    (
                        "class:dialog.body",
                        f"  [{_pi}] {_c.get('namespace', '')}/{_c.get('name', '')} "
                        f"v{_c.get('version', '')} [{_c['id'][:8]}...]\n",
                    )
                )
            _pk_ans = await renderer.get_fullscreen_layout().show_dialog(
                title="Select Pack",
                body_fragments=_pk_body,
            )
            if _pk_ans is not None:
                try:
                    _idx = int(_pk_ans.strip()) - 1
                    if 0 <= _idx < len(_pc):
                        _pm = _pc[_idx]
                except ValueError:
                    pass
        else:
            if escape_markup:
                from rich.markup import escape as rich_escape

                display_ns, display_name = rich_escape(ns), rich_escape(name)
            else:
                display_ns, display_name = ns, name
            renderer.console.print(f"\nMultiple packs match @{display_ns}/{display_name}:")
            for _pi, _c in enumerate(_pc, 1):
                renderer.console.print(
                    f"  {_pi}. {_c.get('namespace', '')}/{_c.get('name', '')} "
                    f"v{_c.get('version', '')} [{_c['id'][:8]}...]"
                )
            try:
                _ch = input(f"Select (1-{len(_pc)}): ").strip()
                _idx = int(_ch) - 1
                if 0 <= _idx < len(_pc):
                    _pm = _pc[_idx]
            except (ValueError, EOFError, KeyboardInterrupt):
                pass
    return _pm


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
    space_id: str | None = None,
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

    # Load space if specified via --space or auto-detect by cwd
    _space: dict[str, Any] | None = None
    _space_instructions: str | None = None
    if space_id:
        from ..services.space_storage import get_space

        _space = get_space(db, space_id)
        if _space:
            renderer.console.print(f"[dim]Space:[/dim] {_space['name']}")
        else:
            renderer.render_error(f"Space ID {space_id} not found in database")
            space_id = None
    if not _space:
        from ..services.space_storage import resolve_space_by_cwd

        _space = resolve_space_by_cwd(db, working_dir)
        if _space:
            space_id = _space["id"]
            renderer.console.print(f"[dim]Space (auto):[/dim] {_space['name']}")
    if not _space:
        from ..services.space_storage import discover_space_file

        _discovered_path = discover_space_file(working_dir)
        if _discovered_path:
            try:
                from ..services.spaces import file_hash, parse_space_file, validate_space

                _disc_cfg = parse_space_file(_discovered_path)
                _disc_errors = validate_space(_disc_cfg)
                if not _disc_errors:
                    from ..services.space_storage import get_space_by_name as _gsbn_disc

                    if not _gsbn_disc(db, _disc_cfg.name):
                        from ..services.space_storage import create_space as _cs_disc

                        _space = _cs_disc(db, _disc_cfg.name, str(_discovered_path), file_hash(_discovered_path))
                        space_id = _space["id"]
                        renderer.console.print(f"[dim]Space (discovered):[/dim] {_disc_cfg.name}")
                else:
                    logger.debug("Discovered space file %s has errors: %s", _discovered_path, _disc_errors)
            except Exception:
                logger.debug("Failed to load discovered space file %s", _discovered_path, exc_info=True)
    if _space:
        # Load space instructions from the space YAML file
        try:
            from ..services.spaces import parse_space_file

            space_cfg = parse_space_file(Path(_space["file_path"]))
            if space_cfg.instructions:
                _space_instructions = space_cfg.instructions
        except Exception:
            logger.debug("Failed to load space instructions", exc_info=True)

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

        def _apply_choice(choice: str) -> bool:
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

        async with _approval_lock:
            if renderer.is_fullscreen() and renderer.get_fullscreen_layout() is not None:
                body: list[tuple[str, str]] = [
                    ("class:dialog.body", f"  {verdict.reason}\n"),
                ]
                cmd = verdict.details.get("command", "")
                path = verdict.details.get("path", "")
                if cmd:
                    body.append(("class:dialog.hint", f"  Command: {cmd}\n\n"))
                elif path:
                    body.append(("class:dialog.hint", f"  Path: {path}\n\n"))
                else:
                    body.append(("class:dialog.body", "\n"))
                body.extend(
                    [
                        ("class:dialog.option.key", "  [y]"),
                        ("class:dialog.option", " Allow once   "),
                        ("class:dialog.option.key", "[s]"),
                        ("class:dialog.option", " Session\n"),
                        ("class:dialog.option.key", "  [a]"),
                        ("class:dialog.option", " Always       "),
                        ("class:dialog.option.key", "[n]"),
                        ("class:dialog.option", " Deny"),
                    ]
                )
                answer = await renderer.get_fullscreen_layout().show_dialog(
                    title="Approval Required",
                    body_fragments=body,
                )
                if answer is None:
                    renderer.console.print(f"  [{MUTED}]✗ Denied: {escape(verdict.tool_name)}[/{MUTED}]\n")
                    return False
                return _apply_choice(answer.strip().lower())

            renderer.console.print(f"\n[yellow bold]Warning:[/yellow bold] {verdict.reason}")
            if verdict.details.get("command"):
                renderer.console.print(f"  Command: [{MUTED}]{verdict.details['command']}[/{MUTED}]")
            elif verdict.details.get("path"):
                renderer.console.print(f"  Path: [{MUTED}]{verdict.details['path']}[/{MUTED}]")
            try:
                from prompt_toolkit import PromptSession as _ConfirmSession

                _confirm_session: Any = _ConfirmSession()
                answer = await _confirm_session.prompt_async(
                    "  [y] Allow once  [s] Allow for session  [a] Allow always  [n] Deny: "
                )
                return _apply_choice(answer.strip().lower())
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
        def _resolve_choice(answer: str, opts: list[str] | None) -> str:
            if opts and answer.isdigit():
                idx = int(answer)
                if 1 <= idx <= len(opts):
                    return opts[idx - 1]
                renderer.console.print(f"  [{MUTED}]Invalid choice #{idx}, using as freeform answer[/{MUTED}]")
            return answer

        await renderer.stop_thinking()

        if renderer.is_fullscreen() and renderer.get_fullscreen_layout() is not None:
            body: list[tuple[str, str]] = [("class:dialog.body", f"  {question}\n\n")]
            if options:
                for i, opt in enumerate(options, 1):
                    body.append(("class:dialog.option.key", f"  {i}. "))
                    body.append(("class:dialog.option", f"{opt}\n"))
            answer = await renderer.get_fullscreen_layout().show_dialog(
                title="Question",
                body_fragments=body,
            )
            renderer.start_thinking()
            if answer is None:
                return ""
            return _resolve_choice(answer.strip(), options)

        renderer.console.print(f"\n[yellow bold]Question:[/yellow bold] {question}")
        try:
            from prompt_toolkit import PromptSession as _AskSession

            _ask_session: Any = _AskSession()

            if options:
                for i, opt in enumerate(options, 1):
                    renderer.console.print(f"  [{MUTED}]{i}.[/{MUTED}] {opt}")
                hint = "(enter number to select, or type a custom answer; esc to cancel)"
                renderer.console.print(f"  [{MUTED}]{hint}[/{MUTED}]")
                answer = await _ask_session.prompt_async("  Choice: ")
                answer = _resolve_choice(answer.strip(), options)
            else:
                renderer.console.print(f"  [{MUTED}](esc to cancel)[/{MUTED}]")
                answer = await _ask_session.prompt_async("  Answer: ")
                answer = answer.strip()

            renderer.console.print()
            renderer.start_thinking()
            return str(answer)
        except (EOFError, KeyboardInterrupt):
            renderer.console.print(f"  [{MUTED}](cancelled)[/{MUTED}]\n")
            renderer.start_thinking()
            return ""

    # Build unified tool executor
    _subagent_counter = 0
    _active_cancel_event: list[asyncio.Event | None] = [None]

    from typing import cast as _cast

    from ..services.tool_rate_limit import ToolRateLimitConfig as _SvcRateLimitConfig
    from ..services.tool_rate_limit import ToolRateLimiter
    from ..tools.subagent import SubagentLimiter

    _sa_config = config.safety.subagent
    _subagent_limiter = SubagentLimiter(
        max_concurrent=_sa_config.max_concurrent,
        max_total=_sa_config.max_total,
    )

    _rate_limiter = ToolRateLimiter(_cast(_SvcRateLimitConfig, config.safety.tool_rate_limit))
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
                # Truncate, sanitize trust tags to prevent envelope breakout,
                # and delimit to limit injection surface.
                from .skills import _expand_args

                args = sanitize_trust_tags(args[:2000])
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
    # Initialize artifact registry with 6-layer precedence
    _artifact_registry = None
    try:
        from ..services.artifact_registry import ArtifactRegistry

        _artifact_registry = ArtifactRegistry()
        _artifact_registry.load_from_db(db)
        if _artifact_registry.count:
            skill_registry.load_from_artifacts(_artifact_registry)
    except Exception:
        pass

    extra_system_prompt = _build_system_prompt(
        config,
        working_dir,
        instructions,
        builtin_tools=tool_registry.list_tools(),
        mcp_servers=mcp_statuses,
        project_instructions=_project_instructions,
        artifact_registry=_artifact_registry,
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
                artifact_registry=_artifact_registry,
                space=_space,
                space_instructions=_space_instructions,
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
                max_consecutive_text_only=config.cli.max_consecutive_text_only,
                max_line_repeats=config.cli.max_line_repeats,
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

    FloatContainer._draw_float = _draw_float_patched  # type: ignore[method-assign]


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
    artifact_registry: Any = None,
    space: dict[str, Any] | None = None,
    space_instructions: str | None = None,
) -> None:
    """Run the interactive REPL."""
    id_kw = _identity_kwargs(config)

    from prompt_toolkit import PromptSession
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.document import Document
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style as PtStyle

    from .layout import AnteroomLayout, create_anteroom_style, format_header

    _command_descriptions: dict[str, str] = {
        "new": "new conversation",
        "append": "add to last message",
        "last": "continue last conversation",
        "list": "list conversations",
        "search": "search conversations",
        "resume": "resume a conversation",
        "delete": "delete a conversation",
        "rename": "rename a conversation",
        "slug": "show conversation slug",
        "rewind": "undo messages",
        "compact": "compress context",
        "conventions": "show project conventions",
        "instructions": "show project conventions (alias)",
        "tools": "list available tools",
        "skills": "list loaded skills",
        "reload-skills": "reload skill files",
        "pack": "manage packs",
        "packs": "list installed packs",
        "project": "manage projects",
        "projects": "list projects",
        "space": "manage spaces",
        "spaces": "list spaces",
        "mcp": "MCP server status",
        "model": "switch model",
        "plan": "planning mode",
        "upload": "upload a file",
        "usage": "token usage stats",
        "verbose": "cycle verbosity",
        "detail": "tool call details",
        "help": "show help",
        "artifact": "manage artifacts",
        "artifacts": "list artifacts",
        "artifact-check": "artifact health check",
        "quit": "exit",
        "exit": "exit",
    }

    _subcommand_completions: dict[str, list[str]] = {
        "artifact": ["list", "show", "delete", "import", "create"],
        "pack": ["list", "show", "install", "remove", "sources", "attach", "detach", "update", "add-source", "refresh"],
        "space": ["list", "show", "switch", "create", "load", "refresh", "clear", "init", "clone", "map"],
    }

    class AnteroomCompleter(Completer):
        """Tab completer for / commands, @ file paths, and conversation slugs."""

        _slug_commands = frozenset({"resume", "delete", "rename"})

        def __init__(
            self,
            commands: list[str],
            skill_names: list[str],
            skill_descriptions: dict[str, str],
            wd: str,
            db: Any,
        ) -> None:
            self._commands = commands
            self._skill_names = skill_names
            self._skill_descriptions = skill_descriptions
            self._wd = wd
            self._db = db

        def update_skill_names(self, skill_names: list[str], skill_descriptions: dict[str, str]) -> None:
            self._skill_names = skill_names
            self._skill_descriptions = skill_descriptions

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

            if text.lstrip().startswith("/") and " " not in text.lstrip():
                prefix = word.lstrip("/")
                for cmd in self._commands:
                    if cmd.startswith(prefix):
                        meta = _command_descriptions.get(cmd, "")
                        yield Completion(f"/{cmd} ", start_position=-len(word), display_meta=meta)
                for sname in self._skill_names:
                    if sname.startswith(prefix):
                        desc = self._skill_descriptions.get(sname, "skill")
                        yield Completion(f"/{sname} ", start_position=-len(word), display_meta=desc)
            elif text.lstrip().startswith("/"):
                # Check if we're completing an argument after a slug-accepting command
                parts = text.lstrip().split(None, 2)
                cmd_name = parts[0].lstrip("/") if parts else ""
                if cmd_name in self._slug_commands and len(parts) <= 2:
                    partial = parts[1] if len(parts) == 2 else ""
                    yield from self._get_slug_completions(partial)
                elif cmd_name in _subcommand_completions and len(parts) <= 2:
                    partial = parts[1] if len(parts) == 2 else ""
                    for sc in _subcommand_completions[cmd_name]:
                        if sc.startswith(partial):
                            yield Completion(sc + " ", start_position=-len(partial))
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
        "instructions",
        "tools",
        "skills",
        "reload-skills",
        "artifact",
        "artifacts",
        "artifact-check",
        "pack",
        "packs",
        "project",
        "projects",
        "space",
        "spaces",
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
    skill_descs = {s.name: s.description for s in skill_registry.list_skills()} if skill_registry else {}
    completer = AnteroomCompleter(commands, skill_names, skill_descs, working_dir, db)

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
    # natively send kitty keyboard protocol sequences (iTerm2, kitty, WezTerm).
    # Terminal.app doesn't send this sequence — Shift+Enter = Enter there.
    # See #673 for Warp terminal limitations.
    try:
        from prompt_toolkit.input import vt100_parser

        vt100_parser.ANSI_SEQUENCES["\x1b[13;2u"] = "c-j"  # type: ignore[assignment]
    except Exception:
        pass

    # Key bindings
    kb = KeyBindings()

    # Paste detection: track buffer changes to distinguish paste from typing.
    # Pasted characters arrive in < 5ms bursts; human typing is > 50ms apart.
    _last_text_change: list[float] = [0.0]

    # Enter submits; Alt+Enter / Shift+Enter / Ctrl+J inserts newline
    def _accept_completion(buf: Any) -> bool:
        """Accept the current completion if the menu is open. Returns True if handled."""
        if buf.complete_state and buf.complete_state.current_completion:
            saved_completer = buf.completer
            buf.completer = None
            try:
                buf.apply_completion(buf.complete_state.current_completion)
            finally:
                buf.completer = saved_completer
            return True
        return False

    @kb.add("enter")
    def _submit(event: Any) -> None:
        buf = event.current_buffer
        if _accept_completion(buf):
            return
        if _is_paste(_last_text_change[0]):
            # Rapid input (paste) — insert newline, don't submit
            buf.insert_text("\n")
        else:
            buf.validate_and_handle()

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
            # Second Ctrl+C within 2 seconds — exit
            _exit_flag[0] = True
            buf.validate_and_handle()
        else:
            # First Ctrl+C with empty buffer — show hint, don't exit
            _last_ctrl_c[0] = now
            renderer.console.print(f"[{CHROME}]Press Ctrl+C again to exit[/{CHROME}]")

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
            "bottom-toolbar": "bg:#1e1e2e #9090a0 noreverse",
            "bottom-toolbar.text": "noreverse",
            "bottom-toolbar.model": GOLD,
            "bottom-toolbar.tokens": "#c0c0d0",
            "bottom-toolbar.tokens-warn": "#e8b830",
            "bottom-toolbar.tokens-danger": "#e05050",
            "bottom-toolbar.dim": "#707888",
            "bottom-toolbar.sep": "#505868",
            "bottom-toolbar.mcp": "#88a0b8",
        }
    )

    _toolbar_cache: list[tuple[str, str]] = []
    _toolbar_msg_count: list[int] = [0]

    def _toolbar_refresh() -> None:
        """Recompute the cached toolbar content."""
        _toolbar_msg_count[0] = len(ai_messages)
        _toolbar_cache[:] = renderer.format_status_toolbar(
            model=current_model,
            current_tokens=_estimate_tokens(ai_messages),
            max_context=config.cli.model_context_window,
            message_count=len(ai_messages),
            approval_mode=config.safety.approval_mode,
            tool_count=len(all_tool_names),
            mcp_statuses=mcp_manager.get_server_statuses() if mcp_manager else None,
        )

    def _bottom_toolbar() -> list[tuple[str, str]]:
        if len(ai_messages) != _toolbar_msg_count[0] or not _toolbar_cache:
            _toolbar_refresh()
        return _toolbar_cache

    session: PromptSession[str] = PromptSession(
        history=FileHistory(str(history_path)),
        key_bindings=kb,
        multiline=True,
        prompt_continuation=_continuation,
        completer=completer,
        reserve_space_for_menu=4,
        style=_repl_style,
        bottom_toolbar=_bottom_toolbar,
    )

    _patch_completion_menu_position()

    # Hook buffer changes for paste detection timing
    def _on_buffer_change(_buf: Any) -> None:
        _last_text_change[0] = time.monotonic()

    session.default_buffer.on_text_changed += _on_buffer_change

    # -- Full-screen Application setup --
    # The input buffer feeds accepted text into the input queue.
    # An asyncio.Event signals when new input is available.
    _input_ready = asyncio.Event()
    _accepted_text: list[str] = [""]
    _sub_prompt_event: list[asyncio.Event | None] = [None]

    def _on_accept(buf: Buffer) -> bool:
        """Buffer accept handler — stash text and signal the input loop."""
        _accepted_text[0] = buf.text
        # If a sub-prompt is active, signal it instead of the main input loop
        if _sub_prompt_event[0] is not None:
            _sub_prompt_event[0].set()
        else:
            _input_ready.set()
        return True  # keep text in buffer (we clear it after reading)

    async def _fs_sub_prompt(prompt_text: str = "  ") -> str:
        """Prompt for a single line of input within the fullscreen app.

        Used by interactive slash commands (/project create, /model, etc.)
        that need sub-prompt input without leaving fullscreen.
        """
        evt = asyncio.Event()
        _sub_prompt_event[0] = evt
        # Show prompt hint in the output pane
        renderer.console.print(f"[{CHROME}]{prompt_text}[/{CHROME}]", end="")
        _fs_input_buffer.reset()
        _fs_app.invalidate()
        try:
            await asyncio.wait_for(evt.wait(), timeout=_SUB_PROMPT_TIMEOUT)
            result = _accepted_text[0]
            _fs_input_buffer.reset()
            _fs_app.invalidate()
            return result
        except asyncio.TimeoutError:
            return ""
        finally:
            _sub_prompt_event[0] = None

    _fs_input_buffer = Buffer(
        name="anteroom-input",
        completer=completer,
        complete_while_typing=True,
        history=FileHistory(str(history_path)),
        accept_handler=_on_accept,
        multiline=True,
    )

    def _should_auto_complete() -> bool:
        text = _fs_input_buffer.text
        stripped = text.lstrip()
        if stripped.startswith("/") and " " not in stripped:
            return True
        if "@" in (text.split()[-1] if text.split() else ""):
            return True
        return False

    _fs_input_buffer.complete_while_typing = Condition(_should_auto_complete)
    _fs_input_buffer.on_text_changed += _on_buffer_change

    _cached_git_branch: list[str] = [""]
    _cached_git_branch_time: list[float] = [0.0]
    _git_branch_pending: list[bool] = [False]
    _cached_project_name: list[str] = [""]
    _cached_project_id: list[str | None] = [None]
    _cached_project_time: list[float] = [0.0]
    _header_plan_mode: list[bool] = [plan_mode]

    def _fetch_git_branch_sync() -> str:
        """Run git rev-parse in a thread-safe way (called via asyncio.to_thread)."""
        import subprocess as _sp

        try:
            return (
                _sp.check_output(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=working_dir,
                    stderr=_sp.DEVNULL,
                    timeout=5,
                )
                .decode("utf-8", errors="replace")
                .strip()
            )
        except Exception:
            return ""

    async def _refresh_git_branch() -> None:
        """Refresh git branch cache in a background thread."""
        if _git_branch_pending[0]:
            return
        _git_branch_pending[0] = True
        try:
            _cached_git_branch[0] = await asyncio.to_thread(_fetch_git_branch_sync)
            _cached_git_branch_time[0] = time.monotonic()
        finally:
            _git_branch_pending[0] = False
        _fs_app.invalidate()

    def _header_fn() -> list[tuple[str, str]]:
        """Build header fragments from current session state."""
        # Schedule background git branch refresh when cache is stale
        now = time.monotonic()
        if now - _cached_git_branch_time[0] > 5.0 and not _git_branch_pending[0]:
            try:
                asyncio.get_running_loop().create_task(_refresh_git_branch())
            except RuntimeError:
                pass  # no event loop yet

        # Use conv["project_id"] which is updated by /project select
        _pid = conv.get("project_id") or project_id
        _pname = ""
        if _pid:
            if now - _cached_project_time[0] > 30.0 or _cached_project_id[0] != _pid:
                _pdata = storage.get_project(db, _pid)
                _cached_project_name[0] = _pdata.get("name", "") if _pdata else ""
                _cached_project_id[0] = _pid
                _cached_project_time[0] = now
            _pname = _cached_project_name[0]

        return format_header(
            model=current_model,
            working_dir=working_dir,
            git_branch=_cached_git_branch[0],
            project_name=_pname,
            space_name=space["name"] if space else "",
            conv_title=conv.get("title", "") or "",
            plan_mode=_header_plan_mode[0],
        )

    _anteroom_layout = AnteroomLayout(
        header_fn=_header_fn,
        footer_fn=_bottom_toolbar,
        input_buffer=_fs_input_buffer,
    )

    _use_fullscreen = sys.stdout.isatty() and sys.stdin.isatty()

    _fs_app: Application[None] = Application(
        layout=_anteroom_layout.layout,
        key_bindings=kb,
        style=create_anteroom_style(),
        full_screen=_use_fullscreen,
        mouse_support=False,  # disabled so terminal handles text selection + copy
    )

    # Set approval mode for prompt coloring
    from anteroom.cli.layout import set_approval_mode

    set_approval_mode(config.safety.approval_mode)

    current_model = config.ai.model
    _pending_resume_info = False
    ai_messages: list[dict[str, Any]] = []

    if resume_conversation_id:
        conv_data = storage.get_conversation(db, resume_conversation_id)
        if conv_data:
            conv = conv_data
            ai_messages = _load_conversation_messages(db, resume_conversation_id)
            is_first_message = False
            working_dir = _restore_working_dir(conv, tool_registry, working_dir)
            # Resume info is deferred to _run_fullscreen() so it renders in the output pane
            _pending_resume_info = True
            # Load project from resumed conversation if not already set via --project
            if not project_id and conv.get("project_id"):
                project_id = str(conv["project_id"])
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
                        artifact_registry=artifact_registry,
                    )
            # Load space from resumed conversation
            if not space and conv.get("space_id"):
                from ..services.space_storage import get_space as _get_resumed_space

                _resumed_space = _get_resumed_space(db, conv["space_id"])
                if _resumed_space:
                    space = _resumed_space
                    renderer.console.print(f"[dim]Space:[/dim] {_resumed_space['name']}")
        else:
            renderer.render_error(f"Conversation {resume_conversation_id} not found, starting new")
            conv = storage.create_conversation(db, working_dir=working_dir, project_id=project_id, **id_kw)
            ai_messages = []
            is_first_message = True
    else:
        conv = storage.create_conversation(db, working_dir=working_dir, project_id=project_id, **id_kw)
        ai_messages = []
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
    @kb.add("pageup")
    def _scroll_up(event: Any) -> None:
        _anteroom_layout.scroll_output_up(10)
        _fs_app.invalidate()

    @kb.add("pagedown")
    def _scroll_down(event: Any) -> None:
        _anteroom_layout.scroll_output_down(10)
        _fs_app.invalidate()

    @kb.add("home")
    def _scroll_to_top(event: Any) -> None:
        _anteroom_layout.scroll_output_to_top()
        _fs_app.invalidate()

    @kb.add("end")
    def _scroll_to_bottom(event: Any) -> None:
        _anteroom_layout.scroll_output_to_bottom()
        _fs_app.invalidate()

    @kb.add("tab")
    def _tab_complete(event: Any) -> None:
        buf = event.current_buffer
        if buf.complete_state:
            if not _accept_completion(buf):
                buf.complete_next()
        else:
            buf.start_completion()

    @kb.add("s-tab")
    def _tab_complete_prev(event: Any) -> None:
        buf = event.current_buffer
        if buf.complete_state:
            buf.complete_previous()

    _picker_filter = Condition(lambda: _anteroom_layout._picker_visible)

    @kb.add("up", filter=_picker_filter)
    @kb.add("k", filter=_picker_filter)
    def _picker_up(event: Any) -> None:
        _anteroom_layout.picker_move_up()
        _fs_app.invalidate()

    @kb.add("down", filter=_picker_filter)
    @kb.add("j", filter=_picker_filter)
    def _picker_down(event: Any) -> None:
        _anteroom_layout.picker_move_down()
        _fs_app.invalidate()

    @kb.add("enter", filter=_picker_filter)
    def _picker_accept(event: Any) -> None:
        _anteroom_layout.accept_picker()

    @kb.add("escape", filter=_picker_filter)
    def _picker_cancel(event: Any) -> None:
        _anteroom_layout.cancel_picker()

    _dialog_esc_filter = Condition(lambda: _anteroom_layout._dialog_visible and not _anteroom_layout._picker_visible)

    @kb.add("escape", filter=_dialog_esc_filter)
    def _dialog_cancel_on_escape(event: Any) -> None:
        _anteroom_layout.cancel_dialog()

    _agent_esc_filter = Condition(
        lambda: agent_busy.is_set() and not _anteroom_layout._dialog_visible and not _anteroom_layout._picker_visible
    )

    @kb.add("escape", filter=_agent_esc_filter)
    def _cancel_on_escape(event: Any) -> None:
        ce = _current_cancel_event[0]
        if ce is not None:
            ce.set()
            # Cancel display is handled by stop_thinking(cancel_msg="cancelled")
            # in the event loop when it detects the cancel_event.

    async def _collect_input() -> None:
        """Continuously collect user input via the fullscreen buffer accept handler."""
        while not exit_flag.is_set():
            _exit_flag[0] = False

            # Wait for the buffer accept handler to fire
            try:
                await _input_ready.wait()
            except asyncio.CancelledError:
                return

            user_input_raw = _accepted_text[0]
            # Clear after reading — prevents losing input set between iterations
            _input_ready.clear()
            # Clear the input buffer for the next prompt
            _fs_input_buffer.reset()
            _fs_app.invalidate()

            if _exit_flag[0]:
                exit_flag.set()
                return

            _collapse_long_input(user_input_raw)
            text = user_input_raw.strip()
            if not text:
                continue

            # Render styled user turn separator with input text
            renderer.render_user_turn(text)
            _fs_app.invalidate()

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
            _header_plan_mode[0] = True
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
            _header_plan_mode[0] = False

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

        # -- Space state --
        _active_space: list[dict[str, Any] | None] = [space]

        async def _resolve_space(name_or_id: str) -> dict[str, Any] | None:
            """Look up a space by name, UUID, or UUID prefix. Shows picker on ambiguity."""
            from ..services.space_storage import resolve_space

            match, candidates = resolve_space(db, name_or_id)
            if match:
                return match
            if candidates:
                if renderer.is_fullscreen() and renderer.get_fullscreen_layout() is not None:
                    _sp_lines: list[tuple[str, str]] = [
                        ("class:dialog.body", f"  Multiple spaces match '{name_or_id}':\n\n"),
                    ]
                    for i, c in enumerate(candidates, 1):
                        _sp_lines.append(("class:dialog.body", f"  [{i}] {c['name']} [{c['id'][:8]}...]\n"))
                    _sp_ans = await renderer.get_fullscreen_layout().show_dialog(
                        title="Select Space",
                        body_fragments=_sp_lines,
                    )
                    if _sp_ans is not None:
                        try:
                            idx = int(_sp_ans.strip()) - 1
                            if 0 <= idx < len(candidates):
                                return candidates[idx]
                        except ValueError:
                            pass
                else:
                    renderer.console.print(f"\nMultiple spaces match '{name_or_id}':")
                    for i, c in enumerate(candidates, 1):
                        renderer.console.print(f"  {i}. {c['name']} [{c['id'][:8]}...]")
                    try:
                        choice = input(f"Select (1-{len(candidates)}): ").strip()
                        idx = int(choice) - 1
                        if 0 <= idx < len(candidates):
                            return candidates[idx]
                    except (ValueError, EOFError, KeyboardInterrupt):
                        pass
            return None

        def _inject_space_instructions(sp: dict[str, Any], instr: str | None = None) -> None:
            nonlocal extra_system_prompt
            extra_system_prompt = _strip_space_instructions(extra_system_prompt)
            if not instr:
                # Load from file
                try:
                    from ..services.spaces import parse_space_file as _psf

                    cfg = _psf(Path(sp["file_path"]))
                    instr = cfg.instructions
                except Exception:
                    pass
            if instr:
                safe_name = sanitize_trust_tags(sp["name"]).replace('"', "&quot;")
                safe_instr = sanitize_trust_tags(instr)
                extra_system_prompt += (
                    '\n\n<space_instructions space="' + safe_name + '">\n' + safe_instr + "\n</space_instructions>"
                )

        def _strip_space_instructions(prompt: str) -> str:
            return re.sub(r"\n*<space_instructions[^>]*>.*?</space_instructions>", "", prompt, flags=re.DOTALL)

        # Inject initial space instructions if space is active
        if _active_space[0] and space_instructions:
            _inject_space_instructions(_active_space[0], space_instructions)

        # Apply plan mode at startup if --plan was passed
        if _plan_active[0]:
            _apply_plan_mode(conv["id"])

        while not exit_flag.is_set():
            # If agent_busy was set (by _collect_input) but we're back here waiting
            # for input, clear it so the prompt renders as gold (idle).
            if agent_busy.is_set() and not _has_pending_work():
                agent_busy.clear()
                _fs_app.invalidate()

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
                    active_sid = _active_space[0]["id"] if _active_space[0] else None
                    conv = storage.create_conversation(
                        db,
                        title=conv_title,
                        conversation_type=conv_type,
                        working_dir=working_dir,
                        project_id=active_pid,
                        **id_kw,
                    )
                    if active_sid:
                        from ..services.space_storage import update_conversation_space

                        update_conversation_space(db, conv["id"], active_sid)
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
                elif cmd in ("/conventions", "/instructions"):
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
                            f"[{CHROME}]Uploaded {upload_path.name} -> source {source['id'][:8]}...[/{CHROME}]"
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
                    if renderer.is_fullscreen() and renderer.get_fullscreen_layout() is not None:
                        _del_body: list[tuple[str, str]] = [
                            ("class:dialog.body", f'  Delete "{title}"?\n\n'),
                            ("class:dialog.option.key", "  [y]"),
                            ("class:dialog.option", " Yes   "),
                            ("class:dialog.option.key", "[n]"),
                            ("class:dialog.option", " No"),
                        ]
                        answer = await renderer.get_fullscreen_layout().show_dialog(
                            title="Delete Conversation",
                            body_fragments=_del_body,
                        )
                        if answer is None or answer.strip().lower() not in ("y", "yes"):
                            renderer.console.print(f"[{CHROME}]Cancelled[/{CHROME}]\n")
                            continue
                    else:
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

                    if use_semantic and _emb_svc is not None:
                        try:
                            query_emb = await _emb_svc.embed(query)
                            if query_emb:
                                sem_results = storage.search_similar_messages(
                                    db, query_emb, limit=20, conversation_type=type_filter
                                )
                                if sem_results:
                                    renderer.console.print(f"\n[bold]Semantic search results for '{query}':[/bold]")
                                    _type_badges = {"note": "[note]", "document": "[doc]"}
                                    for i, r in enumerate(sem_results):
                                        snippet = r["content"][:80].replace("\n", " ")
                                        dist = r.get("distance", 0)
                                        relevance = max(0, 100 - int(dist * 100))
                                        ctype = r.get("conversation_type", "chat")
                                        badge = _type_badges.get(ctype, "")
                                        badge_str = f" {badge}" if badge else ""
                                        renderer.console.print(
                                            f"  {i + 1}. [{r['role']}]{badge_str} {snippet}... "
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
                        # Refresh tab-completion skill names and descriptions
                        completer.update_skill_names(
                            [s.name for s in skills],
                            {s.name: s.description for s in skills},
                        )
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
                                line = await _fs_sub_prompt("  ")
                                if line == "":
                                    break
                                instr_lines.append(line)
                        except (EOFError, KeyboardInterrupt):
                            pass
                        instr_text = "\n".join(instr_lines).strip()
                        renderer.console.print(f"[{CHROME}]Model override (press Enter for default):[/{CHROME}]")
                        try:
                            model_input = await _fs_sub_prompt("  ")
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
                        proj = _resolve_project(target)  # type: ignore[assignment]
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
                            _toolbar_refresh()
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
                        proj = _resolve_project(target)  # type: ignore[assignment]
                        if not proj:
                            renderer.render_error(f"Project '{target}' not found.")
                            continue
                        renderer.console.print(f"[bold]Editing: {proj['name']}[/bold]")
                        renderer.console.print(f"[{CHROME}]New name (Enter to keep '{proj['name']}'):[/{CHROME}]")
                        try:
                            new_name = await _fs_sub_prompt("  ")
                        except (EOFError, KeyboardInterrupt):
                            new_name = ""
                        renderer.console.print(
                            f"[{CHROME}]New instructions (Enter to keep current, 'clear' to remove):[/{CHROME}]"
                        )
                        new_instr_lines: list[str] = []
                        try:
                            while True:
                                line = await _fs_sub_prompt("  ")
                                if line == "":
                                    break
                                new_instr_lines.append(line)
                        except (EOFError, KeyboardInterrupt):
                            pass
                        new_instr = "\n".join(new_instr_lines).strip()
                        renderer.console.print(f"[{CHROME}]New model (Enter to keep, 'clear' to remove):[/{CHROME}]")
                        try:
                            new_model_input = await _fs_sub_prompt("  ")
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
                        proj = _resolve_project(target)  # type: ignore[assignment]
                        if not proj:
                            renderer.render_error(f"Project '{target}' not found.")
                            continue
                        renderer.console.print(f"[yellow]Delete project '{proj['name']}'? (y/N)[/yellow]")
                        try:
                            confirm = await _fs_sub_prompt("  ")
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
                        proj = _active_project[0]  # type: ignore[assignment]
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
                        for psrc in sources:
                            title = psrc.get("title") or psrc.get("url") or psrc["id"][:8]
                            renderer.console.print(f"  {title} [{MUTED}]{psrc['id'][:8]}...[/{MUTED}]")
                        renderer.console.print()

                    else:
                        if _active_project[0]:
                            renderer.console.print(f"[{CHROME}]Active project: {_active_project[0]['name']}[/{CHROME}]")
                        renderer.console.print(
                            f"[{CHROME}]Usage: /project [list|create|select|edit|delete|clear|sources][/{CHROME}]\n"
                        )
                    continue
                elif cmd in ("/spaces", "/space"):
                    from ..services.space_storage import (
                        count_space_conversations,
                    )
                    from ..services.space_storage import (
                        list_spaces as _list_spaces,
                    )
                    from ..services.space_storage import (
                        update_conversation_space as _update_conv_space,
                    )

                    parts = user_input.split(maxsplit=2)
                    sub = parts[1].lower() if len(parts) >= 2 else ""
                    if cmd == "/spaces":
                        sub = "list"

                    if sub == "list" or (cmd == "/space" and not sub):
                        from ..services.spaces import is_local_space as _is_local

                        spaces = _list_spaces(db)
                        if not spaces:
                            renderer.console.print(
                                f"[{CHROME}]No spaces. Create one with: /space create <name>[/{CHROME}]"
                            )
                            renderer.console.print(
                                f"[{CHROME}]  or: /space init  (derives name from directory)[/{CHROME}]\n"
                            )
                            continue
                        renderer.console.print("\n[bold]Spaces:[/bold]")
                        for sp in spaces:
                            cnt = count_space_conversations(db, sp["id"])
                            active = (
                                " [green](active)[/green]"
                                if (_active_space[0] and _active_space[0]["id"] == sp["id"])
                                else ""
                            )
                            _fp = sp["file_path"]
                            origin = "local" if (_fp and _is_local(_fp)) else "global"
                            renderer.console.print(
                                f"  {sp['name']}{active}"
                                f" [{MUTED}]{origin} · {cnt} conversations · {sp['id'][:8]}...[/{MUTED}]"
                            )
                        renderer.console.print()

                    elif sub in ("switch", "select", "use"):
                        target = parts[2].strip() if len(parts) >= 3 else ""
                        if not target:
                            renderer.console.print(f"[{CHROME}]Usage: /space switch <name>[/{CHROME}]\n")
                            continue
                        sp = await _resolve_space(target)
                        if not sp:
                            renderer.render_error(f"Space '{target}' not found. Run /spaces to list available spaces.")
                            continue
                        _active_space[0] = sp
                        _update_conv_space(db, conv["id"], sp["id"])
                        _inject_space_instructions(sp)
                        renderer.console.print(f"[green]Active space: {sp['name']}[/green]\n")

                    elif sub == "show":
                        target = parts[2].strip() if len(parts) >= 3 else ""
                        if not target and _active_space[0]:
                            target = _active_space[0]["name"]
                        if not target:
                            renderer.console.print(f"[{CHROME}]Usage: /space show <name>[/{CHROME}]\n")
                            continue
                        sp = await _resolve_space(target)
                        if not sp:
                            renderer.render_error(f"Space '{target}' not found.")
                            continue
                        from ..services.space_storage import get_space_paths as _get_sp_paths

                        paths = _get_sp_paths(db, sp["id"])
                        cnt = count_space_conversations(db, sp["id"])
                        renderer.console.print(f"\n[bold]{sp['name']}[/bold]")
                        renderer.console.print(f"  File:  {sp['file_path']}")
                        renderer.console.print(f"  Convs: {cnt}")
                        if paths:
                            renderer.console.print("  Paths:")
                            for p in paths:
                                label = p.get("repo_url") or "(mapped)"
                                renderer.console.print(f"    {label} -> {p['local_path']}")
                        renderer.console.print()

                    elif sub == "refresh":
                        sp = _active_space[0]  # type: ignore[assignment]
                        if not sp:
                            renderer.console.print(f"[{CHROME}]No active space[/{CHROME}]\n")
                            continue
                        from ..services.spaces import file_hash as _fh
                        from ..services.spaces import parse_space_file as _psf2

                        fpath = Path(sp["file_path"])
                        if not fpath.is_file():
                            renderer.render_error(f"Space file not found: {fpath}")
                            continue
                        from ..services.space_storage import update_space as _update_sp

                        new_hash = _fh(fpath)
                        _update_sp(db, sp["id"], file_hash=new_hash)
                        try:
                            cfg = _psf2(fpath)
                            if cfg.instructions:
                                _inject_space_instructions(sp, cfg.instructions)
                        except Exception:
                            pass
                        renderer.console.print(f"[green]Refreshed: {sp['name']}[/green]\n")

                    elif sub == "clear":
                        if not _active_space[0]:
                            renderer.console.print(f"[{CHROME}]No active space[/{CHROME}]\n")
                            continue
                        old_name = _active_space[0]["name"]
                        _active_space[0] = None
                        _update_conv_space(db, conv["id"], None)
                        extra_system_prompt = _strip_space_instructions(extra_system_prompt)
                        renderer.console.print(f"[{CHROME}]Cleared space: {old_name}[/{CHROME}]\n")

                    elif sub in ("create", "init"):
                        import re as _re_mod

                        from ..services.space_storage import (
                            create_space as _cs,
                        )
                        from ..services.space_storage import (
                            sync_space_paths as _ssp,
                        )
                        from ..services.spaces import file_hash as _fh2
                        from ..services.spaces import slugify_dir_name as _slug
                        from ..services.spaces import write_space_template as _wst

                        _cwd = Path(working_dir)

                        if sub == "init":
                            name = _slug(_cwd.name)
                            if not name:
                                renderer.render_error(
                                    "Cannot derive a space name from this directory. Use /space create <name> instead."
                                )
                                continue
                        else:
                            name = parts[2].strip() if len(parts) >= 3 else ""
                            if not name:
                                renderer.console.print(f"[{CHROME}]Usage: /space create <name>[/{CHROME}]\n")
                                continue

                        if not _re_mod.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$", name):
                            renderer.render_error(
                                f"Invalid space name: {name!r} (must be alphanumeric, hyphens, underscores)"
                            )
                            continue

                        spath = _cwd / ".anteroom" / "space.yaml"
                        if spath.exists():
                            renderer.console.print(f"[yellow]Space file already exists:[/yellow] {spath}")
                            renderer.console.print(
                                "  Use [bold]/space load[/bold] to register it, or edit it directly.\n"
                            )
                            continue

                        _wst(spath, name)
                        sp = _cs(db, name, str(spath), _fh2(spath))
                        _ssp(db, sp["id"], [{"local_path": str(_cwd)}])

                        # Auto-activate the new space
                        _active_space[0] = sp
                        _update_conv_space(db, conv["id"], sp["id"])
                        _inject_space_instructions(sp)

                        renderer.console.print(f"[green]Created local space: {sp['name']}[/green]\n")
                        renderer.console.print(f"  File: {spath}")
                        renderer.console.print("  Edit the YAML to add instructions, packs, and config.\n")

                    elif sub == "load":
                        target = parts[2].strip() if len(parts) >= 3 else ""
                        if not target:
                            renderer.console.print(f"[{CHROME}]Usage: /space load <path-to-yaml>[/{CHROME}]\n")
                            continue
                        from ..services.space_storage import create_space as _cs
                        from ..services.spaces import file_hash as _fh2
                        from ..services.spaces import parse_space_file as _psf3
                        from ..services.spaces import validate_space as _vs

                        spath = Path(target).expanduser().resolve()
                        if not spath.is_file():
                            renderer.render_error(f"File not found: {spath}")
                            continue
                        try:
                            scfg = _psf3(spath)
                        except (ValueError, FileNotFoundError) as e:
                            renderer.render_error(str(e))
                            continue
                        errors = _vs(scfg)
                        if errors:
                            for err in errors:
                                renderer.render_error(err)
                            continue
                        sp = _cs(db, scfg.name, str(spath), _fh2(spath))
                        renderer.console.print(
                            f"[green]Loaded space: {sp['name']}[/green] [{MUTED}]{sp['id'][:8]}...[/{MUTED}]\n"
                        )

                    elif sub == "clone":
                        _clone_name = parts[2].strip() if len(parts) >= 3 else ""
                        if not _clone_name:
                            renderer.console.print(f"[{CHROME}]Usage: /space clone <name>[/{CHROME}]\n")
                            continue
                        from ..services.space_storage import (
                            get_space_paths as _get_sp_paths,
                        )
                        from ..services.space_storage import (
                            resolve_space as _resolve_sp,
                        )

                        _sp_match, _sp_cands = _resolve_sp(db, _clone_name)
                        if not _sp_match:
                            renderer.console.print(f"[{CHROME}]Space not found: {_clone_name}[/{CHROME}]\n")
                            continue
                        try:
                            from ..services.space_bootstrap import bootstrap_space as _boot_space
                            from ..services.spaces import parse_space_file as _psf_clone

                            sp_file = _sp_match.get("file_path")
                            if not sp_file or not Path(sp_file).is_file():
                                renderer.render_error("Space has no valid YAML file to clone from.")
                                continue
                            scfg = _psf_clone(Path(sp_file))
                            result = _boot_space(db, scfg, None, config.app.data_dir)
                            if result.errors:
                                for err in result.errors:
                                    renderer.render_error(err)
                            else:
                                renderer.console.print(f"[green]Cloned space: {_sp_match['name']}[/green]\n")
                        except Exception as e:
                            renderer.render_error(str(e))

                    elif sub == "map":
                        _map_dir = parts[2].strip() if len(parts) >= 3 else ""
                        if not _map_dir:
                            renderer.console.print(f"[{CHROME}]Usage: /space map <directory>[/{CHROME}]\n")
                            continue
                        if not _active_space[0]:
                            renderer.console.print(
                                f"[{CHROME}]No active space. Switch first: /space switch <name>[/{CHROME}]\n"
                            )
                            continue
                        from ..services.space_storage import (
                            get_space_paths as _get_sp_paths2,
                        )
                        from ..services.space_storage import (
                            sync_space_paths as _sync_sp_paths,
                        )

                        _map_path = Path(_map_dir).expanduser().resolve()
                        if not _map_path.is_dir():
                            renderer.render_error(f"Not a directory: {_map_path}")
                            continue
                        try:
                            existing = _get_sp_paths2(db, _active_space[0]["id"])
                            existing.append({"local_path": str(_map_path), "repo_url": ""})
                            _sync_sp_paths(db, _active_space[0]["id"], existing)
                            renderer.console.print(
                                f"[green]Mapped[/green] {_map_path} to space {_active_space[0]['name']}\n"
                            )
                        except Exception as e:
                            renderer.render_error(str(e))

                    else:
                        if _active_space[0]:
                            renderer.console.print(f"[{CHROME}]Active space: {_active_space[0]['name']}[/{CHROME}]")
                        renderer.console.print(
                            f"[{CHROME}]Usage: /space [list|show|switch|create|load|refresh|clear|"
                            f"clone|map][/{CHROME}]\n"
                        )
                    continue
                elif cmd in ("/packs", "/pack"):
                    parts = user_input.split(maxsplit=2)
                    sub = parts[1].lower() if len(parts) >= 2 else ""
                    if cmd == "/packs":
                        sub = "list"

                    if sub == "list" or (cmd == "/pack" and not sub):
                        installed = packs_service.list_packs(db)
                        if not installed:
                            renderer.console.print(
                                f"\n[{CHROME}]No packs installed."
                                f" Install from a directory: /pack install <path>[/{CHROME}]\n"
                            )
                            continue
                        renderer.console.print("\n[bold]Installed Packs:[/bold]")
                        for p in installed:
                            ns = p.get("namespace", "default")
                            desc = f" — {p['description']}" if p.get("description") else ""
                            renderer.console.print(
                                f"  @{ns}/{p['name']} v{p.get('version', '?')}"
                                f" ({p.get('artifact_count', 0)} artifacts){desc}"
                            )
                        renderer.console.print()

                    elif sub == "show":
                        ref = parts[2].strip() if len(parts) >= 3 else ""
                        if not ref:
                            renderer.console.print(f"[{CHROME}]Usage: /pack show <namespace/name>[/{CHROME}]\n")
                            continue
                        ns, _, name = ref.rpartition("/")
                        if not ns:
                            ns = "default"
                        pack_info = await _resolve_pack_interactive(db, ns, name)
                        if not pack_info:
                            renderer.console.print(f"[{CHROME}]Pack @{ns}/{name} not found.[/{CHROME}]\n")
                            continue
                        renderer.console.print(f"\n[bold]@{ns}/{name}[/bold] v{pack_info.get('version', '?')}")
                        if pack_info.get("description"):
                            renderer.console.print(f"  {pack_info['description']}")
                        artifacts = pack_info.get("artifacts", [])
                        if artifacts:
                            renderer.console.print(f"\n  [bold]Artifacts ({len(artifacts)}):[/bold]")
                            for a in artifacts:
                                renderer.console.print(f"    {a.get('type', '?')}: {a.get('name', '?')}")
                        renderer.console.print()

                    elif sub == "install":
                        target = parts[2].strip() if len(parts) >= 3 else ""
                        if not target:
                            renderer.console.print(f"[{CHROME}]Usage: /pack install <path>[/{CHROME}]\n")
                            continue
                        pack_path = Path(target).expanduser().resolve()
                        manifest_path = pack_path / "pack.yaml"
                        if not manifest_path.exists():
                            renderer.console.print(f"[{CHROME}]No pack.yaml found in {pack_path}[/{CHROME}]\n")
                            continue
                        try:
                            manifest = packs_service.parse_manifest(manifest_path)
                            errors = packs_service.validate_manifest(manifest, pack_path)
                            if errors:
                                for err in errors:
                                    renderer.console.print(f"[red]  {err}[/red]")
                                continue
                            result = packs_service.install_pack(db, manifest, pack_path)
                            renderer.console.print(
                                f"[green]Installed[/green] @{manifest.namespace}/{manifest.name}"
                                f" v{manifest.version} ({result.get('artifact_count', 0)} artifacts)"
                            )
                        except ValueError as exc:
                            renderer.console.print(f"[red]{exc}[/red]")
                        renderer.console.print()

                    elif sub == "remove":
                        ref = parts[2].strip() if len(parts) >= 3 else ""
                        if not ref:
                            renderer.console.print(f"[{CHROME}]Usage: /pack remove <namespace/name>[/{CHROME}]\n")
                            continue
                        ns, _, name = ref.rpartition("/")
                        if not ns:
                            ns = "default"
                        _pm = await _resolve_pack_interactive(db, ns, name)
                        if not _pm:
                            renderer.console.print(f"[{CHROME}]Pack @{ns}/{name} not found.[/{CHROME}]\n")
                            continue
                        removed = packs_service.remove_pack_by_id(db, _pm["id"])
                        if removed:
                            renderer.console.print(f"[green]Removed[/green] @{ns}/{name}\n")
                        else:
                            renderer.console.print(f"[{CHROME}]Pack @{ns}/{name} not found.[/{CHROME}]\n")

                    elif sub == "sources":
                        from ..services.pack_sources import list_cached_sources

                        sources_cfg = getattr(config, "pack_sources", []) or []
                        if not sources_cfg:
                            renderer.console.print(
                                f"\n[{CHROME}]No pack sources configured. Add one: /pack add-source <url>[/{CHROME}]\n"
                            )
                            continue
                        data_dir = config.app.data_dir
                        cached = list_cached_sources(data_dir)
                        cached_map = {c.url: c for c in cached}
                        renderer.console.print("\n[bold]Pack Sources:[/bold]")
                        for psc in sources_cfg:
                            url = getattr(psc, "url", None) or "?"
                            branch = getattr(psc, "branch", "main") or "main"
                            cached_entry = cached_map.get(url)
                            status = (
                                f"[green]cached[/green] ({cached_entry.ref[:8]})"
                                if cached_entry
                                else "[yellow]not cloned[/yellow]"
                            )
                            renderer.console.print(f"  {url} ({branch}) — {status}")
                        renderer.console.print()

                    elif sub == "refresh":
                        from ..services import pack_sources as ps_mod

                        sources_cfg = getattr(config, "pack_sources", []) or []
                        if not sources_cfg:
                            renderer.console.print(
                                f"[{CHROME}]No pack sources configured. Add one: /pack add-source <url>[/{CHROME}]\n"
                            )
                            continue
                        data_dir = config.app.data_dir
                        total_installed = 0
                        total_updated = 0
                        for psc in sources_cfg:
                            url = getattr(psc, "url", None) or "?"
                            branch = getattr(psc, "branch", "main") or "main"
                            renderer.console.print(f"  Refreshing {url}...")
                            src_result = ps_mod.ensure_source(url, branch, data_dir)
                            if not src_result.success:
                                renderer.console.print(f"  [red]Failed: {src_result.error}[/red]")
                                continue
                            if src_result.path:
                                from ..services.pack_refresh import install_from_source

                                i, u = install_from_source(db, src_result.path)
                                total_installed += i
                                total_updated += u
                        renderer.console.print(
                            f"[green]Done:[/green] {total_installed} installed, {total_updated} updated\n"
                        )

                    elif sub == "add-source":
                        url = parts[2].strip() if len(parts) >= 3 else ""
                        if not url:
                            renderer.console.print(f"[{CHROME}]Usage: /pack add-source <git-url>[/{CHROME}]\n")
                            continue

                        from rich.markup import escape as rich_escape

                        from ..services.pack_sources import add_pack_source

                        add_result = add_pack_source(url)
                        if not add_result.ok:
                            renderer.console.print(f"[red]{rich_escape(add_result.message)}[/red]\n")
                            continue
                        if add_result.message:
                            renderer.console.print(f"[{CHROME}]{rich_escape(add_result.message)}[/{CHROME}]\n")
                            continue
                        renderer.console.print(f"[green]Added pack source:[/green] {rich_escape(url)}")
                        renderer.console.print(f"[{MUTED}]Run /pack refresh to clone and install packs.[/{MUTED}]\n")

                    elif sub == "attach":
                        ref = parts[2].strip() if len(parts) >= 3 else ""
                        if not ref:
                            renderer.console.print(
                                f"[{CHROME}]Usage: /pack attach <namespace/name> [--project][/{CHROME}]\n"
                            )
                            continue
                        ns, _, name = ref.rpartition("/")
                        if not ns:
                            ns = "default"

                        from rich.markup import escape as rich_escape

                        from ..services.pack_attachments import attach_pack

                        _pm = await _resolve_pack_interactive(db, ns, name, escape_markup=True)
                        if not _pm:
                            renderer.console.print(
                                f"[{CHROME}]Pack @{rich_escape(ns)}/{rich_escape(name)} not found.[/{CHROME}]\n"
                            )
                            continue
                        project_path = str(Path(working_dir)) if "--project" in user_input else None
                        try:
                            attach_pack(db, _pm["id"], project_path=project_path)
                        except ValueError as exc:
                            renderer.console.print(f"[red]{rich_escape(str(exc))}[/red]\n")
                            continue
                        scope = "project" if project_path else "global"
                        renderer.console.print(
                            f"[green]Attached[/green] @{rich_escape(ns)}/{rich_escape(name)} ({scope})\n"
                        )

                    elif sub == "detach":
                        ref = parts[2].strip() if len(parts) >= 3 else ""
                        if not ref:
                            renderer.console.print(
                                f"[{CHROME}]Usage: /pack detach <namespace/name> [--project][/{CHROME}]\n"
                            )
                            continue
                        ns, _, name = ref.rpartition("/")
                        if not ns:
                            ns = "default"

                        from rich.markup import escape as rich_escape

                        from ..services.pack_attachments import detach_pack

                        _pm = await _resolve_pack_interactive(db, ns, name, escape_markup=True)
                        if not _pm:
                            renderer.console.print(
                                f"[{CHROME}]Pack @{rich_escape(ns)}/{rich_escape(name)} not found.[/{CHROME}]\n"
                            )
                            continue
                        project_path = str(Path(working_dir)) if "--project" in user_input else None
                        removed = detach_pack(db, _pm["id"], project_path=project_path)
                        if removed:
                            scope = "project" if project_path else "global"
                            renderer.console.print(
                                f"[green]Detached[/green] @{rich_escape(ns)}/{rich_escape(name)} ({scope})\n"
                            )
                        else:
                            renderer.console.print(
                                f"[yellow]Not attached:[/yellow] @{rich_escape(ns)}/{rich_escape(name)}\n"
                            )

                    elif sub == "update":
                        target = parts[2].strip() if len(parts) >= 3 else ""
                        if not target:
                            renderer.console.print(f"[{CHROME}]Usage: /pack update <path>[/{CHROME}]\n")
                            continue
                        pack_path = Path(target).expanduser().resolve()
                        manifest_path = pack_path / "pack.yaml"
                        if not manifest_path.exists():
                            renderer.console.print(f"[{CHROME}]No pack.yaml found in {pack_path}[/{CHROME}]\n")
                            continue
                        try:
                            manifest = packs_service.parse_manifest(manifest_path)
                            errors = packs_service.validate_manifest(manifest, pack_path)
                            if errors:
                                for err in errors:
                                    renderer.console.print(f"[red]  {err}[/red]")
                                continue
                            result = packs_service.update_pack(db, manifest, pack_path)
                            renderer.console.print(
                                f"[green]Updated[/green] @{manifest.namespace}/{manifest.name}"
                                f" v{manifest.version} ({result.get('artifact_count', 0)} artifacts)"
                            )
                        except ValueError as exc:
                            renderer.console.print(f"[red]{exc}[/red]")
                        renderer.console.print()

                    else:
                        renderer.console.print(
                            f"[{CHROME}]Usage: /pack"
                            f" [list|show|install|update|remove|attach|detach|sources|refresh|add-source][/{CHROME}]\n"
                        )
                    continue
                elif cmd in ("/artifact", "/artifacts"):
                    from ..services import artifact_storage as _art_store
                    from ..services.artifacts import validate_fqn as _validate_fqn

                    parts = user_input.split(maxsplit=2)
                    sub = parts[1].lower() if len(parts) >= 2 else ""
                    if cmd == "/artifacts":
                        sub = "list"

                    if sub == "list" or not sub:
                        _atype = None
                        _asource = None
                        _rest = parts[2] if len(parts) >= 3 else ""
                        for _tok in _rest.split():
                            if _tok.startswith("--type="):
                                _atype = _tok.split("=", 1)[1]
                            elif _tok.startswith("--source="):
                                _asource = _tok.split("=", 1)[1]
                        arts = _art_store.list_artifacts(db, artifact_type=_atype, source=_asource)
                        if not arts:
                            renderer.console.print(f"[{CHROME}]No artifacts found.[/{CHROME}]\n")
                            continue
                        renderer.console.print("\n[bold]Artifacts:[/bold]")
                        for a in arts:
                            renderer.console.print(f"  {a['fqn']}  [{a.get('type', '?')}]  ({a.get('source', '?')})")
                        renderer.console.print()

                    elif sub == "show":
                        _fqn = parts[2].strip() if len(parts) >= 3 else ""
                        if not _fqn:
                            renderer.console.print(f"[{CHROME}]Usage: /artifact show <fqn>[/{CHROME}]\n")
                            continue
                        if not _validate_fqn(_fqn):
                            renderer.console.print(f"[{CHROME}]Invalid FQN format.[/{CHROME}]\n")
                            continue
                        art = _art_store.get_artifact_by_fqn(db, _fqn)
                        if not art:
                            renderer.console.print(f"[{CHROME}]Artifact not found.[/{CHROME}]\n")
                            continue
                        from rich.markup import escape as _art_esc

                        renderer.console.print(f"\n[bold]FQN:[/bold]       {_art_esc(art['fqn'])}")
                        renderer.console.print(f"[bold]Type:[/bold]      {_art_esc(art['type'])}")
                        renderer.console.print(f"[bold]Source:[/bold]    {_art_esc(art['source'])}")
                        renderer.console.print(f"[bold]Hash:[/bold]      {_art_esc(art['content_hash'])}")
                        renderer.console.print(f"[bold]Updated:[/bold]   {_art_esc(art.get('updated_at', ''))}")
                        renderer.console.print()
                        renderer.console.print("[bold]Content:[/bold]")
                        renderer.console.print(_art_esc(art["content"]))
                        renderer.console.print()

                    elif sub == "delete":
                        _fqn = parts[2].strip() if len(parts) >= 3 else ""
                        if not _fqn:
                            renderer.console.print(f"[{CHROME}]Usage: /artifact delete <fqn>[/{CHROME}]\n")
                            continue
                        if not _validate_fqn(_fqn):
                            renderer.console.print(f"[{CHROME}]Invalid FQN format.[/{CHROME}]\n")
                            continue
                        art = _art_store.get_artifact_by_fqn(db, _fqn)
                        if not art:
                            renderer.console.print(f"[{CHROME}]Artifact not found.[/{CHROME}]\n")
                            continue
                        _art_store.delete_artifact(db, art["id"])
                        renderer.console.print(f"[green]Deleted[/green] {_fqn}\n")

                    elif sub == "import":
                        renderer.console.print(
                            f"[{CHROME}]Use the CLI: aroom artifact import --skills|--instructions|--all[/{CHROME}]\n"
                        )

                    elif sub == "create":
                        renderer.console.print(
                            f"[{CHROME}]Use the CLI: aroom artifact create <type> <name>[/{CHROME}]\n"
                        )

                    else:
                        renderer.console.print(
                            f"[{CHROME}]Usage: /artifact {{list,show,delete,import,create}}[/{CHROME}]\n"
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
                                srv_status = mcp_manager.get_server_statuses().get(server_name, {})
                                if srv_status.get("status") == "connected":
                                    renderer.console.print(f"[green]Connected: {server_name}[/green]\n")
                                else:
                                    err = srv_status.get("error_message", "unknown error")
                                    renderer.render_error(f"Failed to connect '{server_name}': {err}")
                            elif action == "disconnect":
                                await mcp_manager.disconnect_server(server_name)
                                renderer.console.print(f"[{CHROME}]Disconnected: {server_name}[/{CHROME}]\n")
                            elif action == "reconnect":
                                await mcp_manager.reconnect_server(server_name)
                                srv_status = mcp_manager.get_server_statuses().get(server_name, {})
                                if srv_status.get("status") == "connected":
                                    renderer.console.print(f"[green]Reconnected: {server_name}[/green]\n")
                                else:
                                    err = srv_status.get("error_message", "unknown error")
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
                    _toolbar_refresh()
                    renderer.console.print(f"[{CHROME}]Switched to model: {new_model}[/{CHROME}]\n")
                    continue
                elif cmd == "/plan":
                    sub, inline_prompt = parse_plan_command(user_input)  # type: ignore[assignment]
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
                        if renderer.is_fullscreen() and renderer.get_fullscreen_layout() is not None:
                            convs = storage.list_conversations(db, limit=20)
                            if not convs:
                                renderer.render_info("No conversations found.")
                                continue
                            for c in convs:
                                title = (c.get("title") or "Untitled")[:35]
                                badge = _picker_type_badge(c.get("type") or "chat")
                                ts = _picker_relative_time(c.get("updated_at") or "")
                                count = c.get("message_count") or 0
                                slug = (c.get("slug") or "")[:20]
                                c["_label"] = f"{title}" + (f" {badge}" if badge else "")
                                c["_meta"] = f"{slug}  {count}msg  {ts}"

                            preview_cache: dict[str, list[tuple[str, str]]] = {}

                            def _preview(item: dict[str, Any]) -> list[tuple[str, str]]:
                                cid = item["id"]
                                if cid not in preview_cache:
                                    msgs = storage.list_messages(db, cid)
                                    preview_cache[cid] = _picker_format_preview(msgs)
                                return preview_cache[cid]

                            _fs_layout = renderer.get_fullscreen_layout()
                            picked = await _fs_layout.show_picker(items=convs, preview_fn=_preview)
                        else:
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
                    for smsg in stored:
                        role_label = "You" if smsg["role"] == "user" else "AI"
                        msg_preview = smsg["content"][:80].replace("\n", " ")
                        if len(smsg["content"]) > 80:
                            msg_preview += "..."
                        renderer.console.print(f"  {smsg['position']}. [{role_label}] {msg_preview}")

                    if renderer.is_fullscreen() and renderer.get_fullscreen_layout() is not None:
                        _rw_body: list[tuple[str, str]] = [
                            ("class:dialog.body", "  Enter position to rewind to\n"),
                            ("class:dialog.body", "  (keep that message, delete after)\n"),
                        ]
                        pos_input_raw = await renderer.get_fullscreen_layout().show_dialog(
                            title="Rewind Conversation",
                            body_fragments=_rw_body,
                        )
                        if pos_input_raw is None:
                            renderer.console.print(f"[{CHROME}]Cancelled[/{CHROME}]\n")
                            continue
                        pos_input = pos_input_raw.strip()
                    else:
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
                        if renderer.is_fullscreen() and renderer.get_fullscreen_layout() is not None:
                            _undo_body: list[tuple[str, str]] = [
                                (
                                    "class:dialog.body",
                                    f"  {len(file_paths)} file(s) modified after this point.\n  Undo file changes?\n\n",
                                ),
                                ("class:dialog.option.key", "  [y]"),
                                ("class:dialog.option", " Yes   "),
                                ("class:dialog.option.key", "[n]"),
                                ("class:dialog.option", " No"),
                            ]
                            _undo_ans = await renderer.get_fullscreen_layout().show_dialog(
                                title="Undo File Changes",
                                body_fragments=_undo_body,
                            )
                            if _undo_ans is None:
                                renderer.console.print(f"[{CHROME}]Cancelled[/{CHROME}]\n")
                                continue
                            undo_files = _undo_ans.strip().lower() in ("y", "yes")
                        else:
                            try:
                                answer = input("  Undo file changes? [y/N] ").strip().lower()
                                undo_files = answer in ("y", "yes")
                            except (EOFError, KeyboardInterrupt):
                                renderer.console.print(f"[{CHROME}]Cancelled[/{CHROME}]\n")
                                continue

                    rewind_result = await rewind_service(
                        db=db,
                        conversation_id=conv["id"],
                        to_position=target_pos,
                        undo_files=undo_files,
                        working_dir=working_dir,
                    )

                    ai_messages = _load_conversation_messages(db, conv["id"])

                    summary = f"Rewound {rewind_result.deleted_messages} message(s)"
                    if rewind_result.reverted_files:
                        summary += f", reverted {len(rewind_result.reverted_files)} file(s)"
                    if rewind_result.skipped_files:
                        summary += f", {len(rewind_result.skipped_files)} skipped"
                    renderer.console.print(f"[{CHROME}]{summary}[/{CHROME}]\n")

                    if rewind_result.skipped_files:
                        for sf in rewind_result.skipped_files:
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
                        max_consecutive_text_only=config.cli.max_consecutive_text_only,
                        max_line_repeats=config.cli.max_line_repeats,
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
                                new_msg = storage.create_message(
                                    db, conv["id"], "assistant", event.data["content"], **id_kw
                                )
                                if _pending_usage:
                                    storage.update_message_usage(
                                        db,
                                        new_msg["id"],
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
                            # Show styled separator for the queued user message
                            queued_content = event.data.get("content", "")
                            renderer.render_user_turn(queued_content)
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
                                _toolbar_refresh()
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
                    _fs_app.invalidate()
                _current_cancel_event[0] = None
                if cancel_event_ref is not None:
                    cancel_event_ref[0] = None
                cancel_event.set()
                _remove_signal_handler(loop, signal.SIGINT)
                if not _IS_WINDOWS:
                    signal.signal(signal.SIGINT, original_handler)

    # -- Full-screen application lifecycle --
    if _use_fullscreen:
        renderer.use_fullscreen_output(_anteroom_layout, _fs_app.invalidate)
        renderer.install_fullscreen_log_handler()
    renderer.set_tool_dedup(config.cli.tool_dedup)
    renderer.configure_thresholds(
        esc_hint_delay=config.cli.esc_hint_delay,
        stall_display=config.cli.stall_display_threshold,
        stall_warning=config.cli.stall_warning_threshold,
    )

    async def _run_fullscreen() -> None:
        """Run input collector and agent runner inside the fullscreen app."""
        try:
            # Render deferred resume info now that console writes to the output pane
            if _pending_resume_info:
                _show_resume_info(db, conv, ai_messages)

            input_task = asyncio.create_task(_collect_input())
            runner_task = asyncio.create_task(_agent_runner())

            # Wait for either task to signal exit
            done_tasks, pending_tasks = await asyncio.wait(
                {input_task, runner_task}, return_when=asyncio.FIRST_COMPLETED
            )
            exit_flag.set()
            _input_ready.set()  # unblock _collect_input if waiting
            for t in pending_tasks:
                t.cancel()
                try:
                    await t
                except BaseException:
                    pass
        finally:
            # Always exit the fullscreen application, even on unhandled exceptions
            _fs_app.exit()

    asyncio.get_running_loop().call_soon(lambda: asyncio.create_task(_run_fullscreen()))

    await _fs_app.run_async()

    # Restore original log handlers now that fullscreen layout is gone
    if _use_fullscreen:
        renderer.restore_log_handlers()

    # Show resume hint after fullscreen exits
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

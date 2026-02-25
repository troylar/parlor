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
from ..services import storage
from ..services.agent_loop import _build_compaction_history, run_agent_loop
from ..services.ai_service import AIService, create_ai_service
from ..services.embeddings import get_effective_dimensions
from ..tools import ToolRegistry, register_default_tools
from . import renderer
from .agent_turn import AgentTurnContext, RagEmbeddingCache, inject_rag_context, run_agent_turn
from .commands import CommandResult, ReplSession, handle_slash_command
from .instructions import (
    CONVENTIONS_TOKEN_WARNING_THRESHOLD,
    estimate_tokens,
    find_global_instructions,
    find_project_instructions_path,
)
from .pickers import show_resume_info as _show_resume_info
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
) -> None:
    """Drain input_queue into msg_queue, filtering out / commands.

    - /quit and /exit trigger cancel_event and exit_flag
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
) -> str:
    runtime_ctx = build_runtime_context(
        model=config.ai.model,
        builtin_tools=builtin_tools,
        mcp_servers=mcp_servers,
        interface="cli",
        working_dir=working_dir,
    )
    parts = [runtime_ctx]

    # Project context
    project_ctx = _detect_project_context(working_dir)
    if project_ctx:
        parts.append(f"\n<project_context>\nWorking directory: {working_dir}\n{project_ctx}\n</project_context>")
    else:
        parts.append(f"\n<project_context>\nWorking directory: {working_dir}\n</project_context>")

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
    db = init_db(db_path, vec_dimensions=vec_dims)

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

    from ..tools.subagent import SubagentLimiter

    _sa_config = config.safety.subagent
    _subagent_limiter = SubagentLimiter(
        max_concurrent=_sa_config.max_concurrent,
        max_total=_sa_config.max_total,
    )

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
                # Truncate to limit injection surface; the skill prompt itself
                # is from trusted local YAML files.
                args = args[:2000]
                prompt = f"{prompt}\n\nARGUMENTS: {args}"
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
            return await tool_registry.call_tool(tool_name, arguments)
        if mcp_manager:
            # MCP tools bypass ToolRegistry — apply safety gate here
            verdict = tool_registry.check_safety(tool_name, arguments)
            if verdict and verdict.needs_approval:
                if verdict.hard_denied:
                    return {"error": f"Tool '{tool_name}' is blocked by configuration", "safety_blocked": True}
                confirmed = await _confirm_destructive(verdict)
                if not confirmed:
                    return {"error": "Operation denied by user", "exit_code": -1}
            return await mcp_manager.call_tool(tool_name, arguments)
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
    )

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
                resume_conversation_id=resume_conversation_id,
                cancel_event_ref=_active_cancel_event,
            )
        else:
            git_branch = _detect_git_branch()
            build_date = renderer._get_build_date()
            with renderer.startup_step("Checking for updates..."):
                latest_version = await _check_for_update(__version__)
            renderer.render_welcome(
                model=config.ai.model,
                tool_count=len(all_tool_names),
                instructions_loaded=instructions is not None,
                working_dir=working_dir,
                git_branch=git_branch,
                version=__version__,
                build_date=build_date,
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
                    tools_openai_or_none = tools_openai if tools_openai else None
                    all_tool_names.extend(t["name"] for t in mcp_manager.get_all_tools())
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
                plan_mode=plan_mode,
                skill_msg_queue_ref=_active_msg_queue,
            )
    finally:
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
    resume_conversation_id: str | None = None,
    cancel_event_ref: list[asyncio.Event | None] | None = None,
) -> None:
    """Run a single prompt and exit."""
    id_kw = _identity_kwargs(config)
    expanded = _expand_file_references(prompt, working_dir, file_max_chars=config.cli.file_reference_max_chars)

    if resume_conversation_id:
        conv = storage.get_conversation(db, resume_conversation_id)
        if not conv:
            renderer.render_error(f"Conversation {resume_conversation_id} not found")
            return
        messages = _load_conversation_messages(db, resume_conversation_id)
    else:
        conv = storage.create_conversation(db, **id_kw)
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
    plan_mode: bool = False,
    skill_msg_queue_ref: list[asyncio.Queue[dict[str, Any]] | None] | None = None,
) -> None:
    """Run the interactive REPL."""
    id_kw = _identity_kwargs(config)

    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.styles import Style as PtStyle

    from .completer import AnteroomCompleter

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
        tools_openai = new_tools if new_tools else None
        new_names: list[str] = list(tool_registry.list_tools()) if tool_registry else []
        if mcp_manager:
            new_names.extend(t["name"] for t in mcp_manager.get_all_tools())
        all_tool_names = new_names

    history_path = config.app.data_dir / "cli_history"

    from .keybindings import KeybindingState, create_keybindings, on_buffer_change, patch_shift_enter

    patch_shift_enter()
    _kb_state = KeybindingState()
    kb = create_keybindings(_kb_state)
    _exit_flag = _kb_state.exit_flag_value

    # Styled prompt — dim while agent is working to signal "you can type to queue"
    _prompt_text = HTML("<style fg='#C5A059'>❯</style> ")
    _prompt_dim = HTML(f"<style fg='{CHROME}'>❯</style> ")
    _continuation = "  "  # align with "❯ "
    agent_busy = _kb_state.agent_busy

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

    session.default_buffer.on_text_changed += on_buffer_change(_kb_state)

    current_model = config.ai.model

    if resume_conversation_id:
        conv_data = storage.get_conversation(db, resume_conversation_id)
        if conv_data:
            conv = conv_data
            ai_messages = _load_conversation_messages(db, resume_conversation_id)
            is_first_message = False
            _show_resume_info(db, conv, ai_messages)
        else:
            renderer.render_error(f"Conversation {resume_conversation_id} not found, starting new")
            conv = storage.create_conversation(db, **id_kw)
            ai_messages = []
            is_first_message = True
    else:
        conv = storage.create_conversation(db, **id_kw)
        ai_messages: list[dict[str, Any]] = []
        is_first_message = True

    # -- Concurrent input/output architecture --
    # Instead of blocking on prompt_async then running agent loop sequentially,
    # we use two coroutines: one collects input, one processes agent responses.
    # prompt_toolkit's patch_stdout keeps the input prompt anchored at the bottom.

    input_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=10)
    exit_flag = asyncio.Event()
    _current_cancel_event = _kb_state.current_cancel_event

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
        nonlocal current_model, ai_service, extra_system_prompt

        # -- Plan mode state --
        from .plan import enter_plan_mode, leave_plan_mode

        _plan_active: list[bool] = [plan_mode]
        _plan_file: list[Path | None] = [None]
        _full_tools_backup: list[list[dict[str, Any]] | None] = [None]
        _plan_checklist_steps: list[str] = []  # parsed step descriptions for live checklist
        _plan_current_step: list[int] = [0]  # index of the step currently in progress

        # -- RAG state --
        _rag_cache = RagEmbeddingCache(config)

        def _apply_plan_mode(conv_id: str) -> None:
            nonlocal tools_openai, extra_system_prompt
            tools_openai, extra_system_prompt = enter_plan_mode(
                conv_id,
                config.app.data_dir,
                _plan_active,
                _plan_file,
                _full_tools_backup,
                tools_openai,
                extra_system_prompt,
            )

        def _exit_plan_mode(plan_content: str | None = None) -> None:
            nonlocal tools_openai, extra_system_prompt
            result_tools, extra_system_prompt = leave_plan_mode(
                _plan_active,
                _full_tools_backup,
                extra_system_prompt,
                rebuild_tools_fn=_rebuild_tools,
                plan_content=plan_content,
            )
            if result_tools is not None:
                tools_openai = result_tools

        # Apply plan mode at startup if --plan was passed
        if _plan_active[0]:
            _apply_plan_mode(conv["id"])

        # Build the ReplSession used by handle_slash_command
        _cmd_session = ReplSession(
            conv=conv,
            ai_messages=ai_messages,
            is_first_message=is_first_message,
            current_model=current_model,
            tools_openai=tools_openai,
            extra_system_prompt=extra_system_prompt,
            all_tool_names=all_tool_names,
            db=db,
            config=config,
            working_dir=working_dir,
            ai_service=ai_service,
            identity_kwargs=id_kw,
            skill_registry=skill_registry,
            mcp_manager=mcp_manager,
            tool_registry=tool_registry,
            plan_active=_plan_active,
            plan_file=_plan_file,
            plan_checklist_steps=_plan_checklist_steps,
            plan_current_step=_plan_current_step,
            apply_plan_mode=_apply_plan_mode,
            exit_plan_mode=_exit_plan_mode,
            rebuild_tools=_rebuild_tools,
            compact_messages=_compact_messages,
            create_ai_service_fn=create_ai_service,
        )

        def _sync_from_session() -> None:
            """Sync mutable state back from _cmd_session to local variables."""
            nonlocal conv, ai_messages, is_first_message, current_model
            nonlocal tools_openai, extra_system_prompt, all_tool_names, ai_service
            conv = _cmd_session.conv
            ai_messages = _cmd_session.ai_messages
            is_first_message = _cmd_session.is_first_message
            current_model = _cmd_session.current_model
            tools_openai = _cmd_session.tools_openai
            extra_system_prompt = _cmd_session.extra_system_prompt
            all_tool_names = _cmd_session.all_tool_names
            ai_service = _cmd_session.ai_service

        def _sync_to_session() -> None:
            """Sync local variables into _cmd_session before a command call."""
            _cmd_session.conv = conv
            _cmd_session.ai_messages = ai_messages
            _cmd_session.is_first_message = is_first_message
            _cmd_session.current_model = current_model
            _cmd_session.tools_openai = tools_openai
            _cmd_session.extra_system_prompt = extra_system_prompt
            _cmd_session.all_tool_names = all_tool_names
            _cmd_session.ai_service = ai_service

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

            # Handle slash commands
            if user_input.startswith("/"):
                _sync_to_session()
                cmd_result, user_input = await handle_slash_command(_cmd_session, user_input)
                _sync_from_session()
                if cmd_result == CommandResult.EXIT:
                    exit_flag.set()
                    return
                elif cmd_result == CommandResult.CONTINUE:
                    continue
                # FALL_THROUGH: user_input was modified, continue to agent loop

            # Check for skill invocation
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
            extra_system_prompt = await inject_rag_context(
                config=config,
                plan_active=_plan_active,
                db=db,
                conv_id=conv["id"],
                expanded=expanded,
                extra_system_prompt=extra_system_prompt,
                rag_cache=_rag_cache,
            )

            # Build message queue for queued follow-ups during agent loop
            msg_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            if skill_msg_queue_ref is not None:
                skill_msg_queue_ref[0] = msg_queue

            # Stream response via agent turn
            cancel_event = asyncio.Event()
            turn_ctx = AgentTurnContext(
                ai_service=ai_service,
                ai_messages=ai_messages,
                tool_executor=tool_executor,
                tools_openai=tools_openai,
                extra_system_prompt=extra_system_prompt,
                cancel_event=cancel_event,
                config=config,
                db=db,
                conv=conv,
                identity_kwargs=id_kw,
                user_input=user_input,
                is_first_message=is_first_message,
                msg_queue=msg_queue,
                input_queue=input_queue,
                exit_flag=exit_flag,
                working_dir=working_dir,
                subagent_limiter=subagent_limiter,
                cancel_event_ref=cancel_event_ref,
                current_cancel_event=_current_cancel_event,
                agent_busy=agent_busy,
                session_invalidate=session.app.invalidate,
                has_pending_work=_has_pending_work,
                plan_checklist_steps=_plan_checklist_steps,
                plan_current_step=_plan_current_step,
                plan_active=_plan_active,
                apply_plan_mode=_apply_plan_mode,
                get_tiktoken_encoding=_get_tiktoken_encoding,
                estimate_tokens_fn=_estimate_tokens,
                drain_input_fn=_drain_input_to_msg_queue,
            )
            is_first_message = await run_agent_turn(turn_ctx)

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

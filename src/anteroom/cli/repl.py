"""REPL loop and one-shot mode for the Parlor CLI."""

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
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import AppConfig, build_runtime_context
from ..db import init_db
from ..services import storage
from ..services.agent_loop import _build_compaction_history, run_agent_loop
from ..services.ai_service import AIService, create_ai_service
from ..services.embeddings import get_effective_dimensions
from ..services.rewind import collect_file_paths
from ..services.rewind import rewind_conversation as rewind_service
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

    # Move cursor up to input start and clear to end of screen
    sys.stdout.write(f"\033[{total_rows}A\033[J")
    # Reprint truncated with styled prompt
    sys.stdout.write(f"\033[1;96m❯\033[0m {lines[0]}\n")
    for ln in lines[1:show]:
        sys.stdout.write(f"  {ln}\n")
    sys.stdout.write(f"  \033[90m... ({hidden} more lines)\033[0m\n")
    sys.stdout.flush()


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


def _show_resume_info(db: Any, conv: dict[str, Any], ai_messages: list[dict[str, Any]]) -> None:
    """Display resume header with last exchange context."""
    stored = storage.list_messages(db, conv["id"])
    title = conv.get("title", "Untitled")
    renderer.console.print(f"[{CHROME}]Resumed: {title} ({len(ai_messages)} messages)[/{CHROME}]")
    renderer.render_conversation_recap(stored)


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

    # Register built-in tools
    tool_registry = ToolRegistry()
    if config.cli.builtin_tools and not no_tools:
        register_default_tools(tool_registry, working_dir=working_dir)

    # Start MCP servers
    mcp_manager = None
    if config.mcp_servers:
        try:
            from ..services.mcp_manager import McpManager

            mcp_manager = McpManager(config.mcp_servers)
            server_count = len(config.mcp_servers)
            label = f"Starting {server_count} MCP server{'s' if server_count != 1 else ''}..."
            with renderer.startup_step(label):
                await mcp_manager.startup()
            # Show per-server errors at startup so user knows immediately
            for name, status in mcp_manager.get_server_statuses().items():
                if status.get("status") == "error":
                    err = status.get("error_message", "unknown error")
                    renderer.render_error(f"MCP '{name}': {err}")
        except Exception as e:
            logger.warning("Failed to start MCP servers: %s", e)
            renderer.render_error(f"MCP startup failed: {e}")

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

    async def tool_executor(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal _subagent_counter
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

    tools_openai_or_none = tools_openai if tools_openai else None

    # Load ANTEROOM.md instructions (with trust gating for project-level files)
    instructions = await _load_instructions_with_trust(
        working_dir,
        trust_project=trust_project,
        no_project_context=no_project_context,
        data_dir=config.app.data_dir,
    )
    mcp_statuses = mcp_manager.get_server_statuses() if mcp_manager else None
    extra_system_prompt = _build_system_prompt(
        config,
        working_dir,
        instructions,
        builtin_tools=tool_registry.list_tools(),
        mcp_servers=mcp_statuses,
    )

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

    # Load skills
    skill_registry = SkillRegistry()
    skill_registry.load(working_dir)
    for warn in skill_registry.load_warnings:
        renderer.console.print(f"[yellow]Skill warning:[/yellow] {warn}")

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

    class ParlorCompleter(Completer):
        """Tab completer for / commands and @ file paths."""

        def __init__(self, commands: list[str], skill_names: list[str], wd: str) -> None:
            self._commands = commands
            self._skill_names = skill_names
            self._wd = wd

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
        "rewind",
        "compact",
        "conventions",
        "tools",
        "skills",
        "mcp",
        "model",
        "plan",
        "verbose",
        "detail",
        "help",
        "quit",
        "exit",
    ]
    skill_names = [s.name for s in skill_registry.list_skills()] if skill_registry else []
    completer = ParlorCompleter(commands, skill_names, working_dir)

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

    # Ctrl+C: clear buffer if text present, exit if empty
    _exit_flag: list[bool] = [False]

    @kb.add("c-c")
    def _handle_ctrl_c(event: Any) -> None:
        buf = event.current_buffer
        if buf.text:
            buf.reset()
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
                (cmd, "  /resume <N|id>"),
                (desc, "    Resume by list number or ID\n"),
                (cmd, "  /delete <N|id>"),
                (desc, "    Delete a conversation\n"),
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
                (desc, "           List loaded skills\n"),
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
                ("", "\n"),
                ("bold", " Input\n"),
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
        nonlocal current_model, ai_service, extra_system_prompt

        # -- Plan mode state --
        from .plan import (
            PLAN_MODE_ALLOWED_TOOLS,
            build_planning_system_prompt,
            delete_plan,
            get_editor,
            get_plan_file_path,
            parse_plan_command,
            read_plan,
        )

        _plan_active: list[bool] = [plan_mode]
        _plan_file: list[Path | None] = [None]
        _full_tools_backup: list[list[dict[str, Any]] | None] = [None]

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
                    conv = storage.create_conversation(db, title=conv_title, conversation_type=conv_type, **id_kw)
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
                            renderer.console.print(
                                f"  {i + 1}. {c['title']}{type_badge}"
                                f" ({msg_count} msgs) [{CHROME}]{c['id'][:8]}...[/{CHROME}]"
                            )
                        if has_more:
                            more_n = list_limit + 20
                            msg = f"... more available. Use /list {more_n} to show more."
                            renderer.console.print(f"  [{MUTED}]{msg}[/{MUTED}]")
                        renderer.console.print("  Use [bold]/resume <number>[/bold] or [bold]/resume <id>[/bold]\n")
                    else:
                        renderer.console.print(f"[{CHROME}]No conversations[/{CHROME}]\n")
                    continue
                elif cmd == "/delete":
                    parts = user_input.split(maxsplit=1)
                    if len(parts) < 2:
                        renderer.console.print(
                            f"[{CHROME}]Usage: /delete <number> or /delete <conversation_id>[/{CHROME}]\n"
                        )
                        continue
                    target = parts[1].strip()
                    resolved_id = None
                    if target.isdigit():
                        idx = int(target) - 1
                        convs = storage.list_conversations(db, limit=20)
                        if 0 <= idx < len(convs):
                            resolved_id = convs[idx]["id"]
                        else:
                            renderer.render_error(f"Invalid number: {target}. Use /list to see conversations.")
                            continue
                    else:
                        resolved_id = target
                    to_delete = storage.get_conversation(db, resolved_id)
                    if not to_delete:
                        renderer.render_error(f"Conversation not found: {target}")
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
                    storage.delete_conversation(db, resolved_id, config.app.data_dir)
                    renderer.console.print(f"[{CHROME}]Deleted: {title}[/{CHROME}]\n")
                    if conv.get("id") == resolved_id:
                        conv = storage.create_conversation(db, **id_kw)
                        ai_messages = []
                        is_first_message = True
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
                        renderer.console.print("  Use [bold]/resume <number>[/bold] to open\n")
                    else:
                        renderer.console.print(f"[{CHROME}]No conversations matching '{query}'[/{CHROME}]\n")
                    continue
                elif cmd == "/skills":
                    if skill_registry:
                        skills = skill_registry.list_skills()
                        if skills:
                            renderer.console.print("\n[bold]Available skills:[/bold]")
                            for s in skills:
                                src = s.source
                                renderer.console.print(f"  /{s.name} - {s.description} [{CHROME}]({src})[/{CHROME}]")
                            renderer.console.print()
                        else:
                            renderer.console.print(
                                f"[{CHROME}]No skills loaded. Add .yaml files to"
                                f" ~/.anteroom/skills/ or .anteroom/skills/[/{CHROME}]\n"
                            )
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
                        renderer.console.print(
                            f"[{CHROME}]Usage: /resume <number> (from /list) or /resume <conversation_id>[/{CHROME}]\n"
                        )
                        continue
                    target = parts[1].strip()
                    resolved_id = None
                    if target.isdigit():
                        idx = int(target) - 1
                        convs = storage.list_conversations(db, limit=20)
                        if 0 <= idx < len(convs):
                            resolved_id = convs[idx]["id"]
                        else:
                            renderer.render_error(f"Invalid number: {target}. Use /list to see conversations.")
                            continue
                    else:
                        resolved_id = target
                    loaded = storage.get_conversation(db, resolved_id)
                    if loaded:
                        conv = loaded
                        ai_messages = _load_conversation_messages(db, conv["id"])
                        is_first_message = False
                        _show_resume_info(db, conv, ai_messages)
                    else:
                        renderer.render_error(f"Conversation not found: {resolved_id}")
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

            # Build message queue for queued follow-ups during agent loop
            msg_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

            # Stream response
            renderer.clear_turn_history()
            renderer.clear_subagent_state()
            if subagent_limiter is not None:
                subagent_limiter.reset()
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
            try:
                response_token_count = 0
                total_elapsed = 0.0

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
                        )

                        if event.kind == "thinking":
                            if not thinking:
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
                        elif event.kind == "assistant_message":
                            if event.data["content"]:
                                storage.create_message(db, conv["id"], "assistant", event.data["content"], **id_kw)
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
                        elif event.kind == "done":
                            if thinking and cancel_event.is_set():
                                total_elapsed += await renderer.stop_thinking(cancel_msg="cancelled")
                                thinking = False
                            elif thinking:
                                total_elapsed += await renderer.stop_thinking()
                                thinking = False
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
                            title = await ai_service.generate_title(user_input)
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

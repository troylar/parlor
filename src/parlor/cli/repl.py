"""REPL loop and one-shot mode for the Parlor CLI."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import signal
import subprocess
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..db import init_db
from ..services import storage
from ..services.agent_loop import run_agent_loop
from ..services.ai_service import AIService
from ..tools import ToolRegistry, register_default_tools
from . import renderer
from .instructions import load_instructions
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

_FILE_REF_RE = re.compile(r"@((?:[^\s\"']+|\"[^\"]+\"|'[^']+'))")


def _detect_git_branch() -> str | None:
    """Detect the current git branch, or None if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
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
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(tc.get("output", {})),
                    })
                continue
            messages.append(entry)
    return messages

# Context window management
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
                total += len(enc.encode(content))
            else:
                total += len(content) // 4
        elif isinstance(content, list):
            for part in content:
                text = str(part) if isinstance(part, dict) else ""
                if enc:
                    total += len(enc.encode(text))
                else:
                    total += len(text) // 4
        for tc in msg.get("tool_calls", []):
            if isinstance(tc, dict):
                func = tc.get("function", {})
                args = func.get("arguments", "")
                name = func.get("name", "")
                if enc:
                    total += len(enc.encode(args)) + len(enc.encode(name))
                else:
                    total += (len(args) + len(name)) // 4
    return total


def _expand_file_references(text: str, working_dir: str) -> str:
    """Expand @path references in user input.

    @file.py      -> includes file contents inline
    @src/          -> includes directory listing
    @"path with spaces/file.py" -> handles quoted paths
    """

    def _replace(match: re.Match[str]) -> str:
        raw_path = match.group(1).strip("\"'")
        full_path = Path(working_dir) / raw_path if not os.path.isabs(raw_path) else Path(raw_path)
        resolved = full_path.resolve()

        if resolved.is_file():
            try:
                content = resolved.read_text(encoding="utf-8", errors="replace")
                if len(content) > 100_000:
                    content = content[:100_000] + "\n... (truncated)"
                return f"\n<file path=\"{raw_path}\">\n{content}\n</file>\n"
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
                return f"\n<directory path=\"{raw_path}\">\n{content}\n</directory>\n"
            except OSError:
                return match.group(0)
        else:
            return match.group(0)

    return _FILE_REF_RE.sub(_replace, text)


def _build_system_prompt(config: AppConfig, working_dir: str, instructions: str | None) -> str:
    parts = [
        f"You are an AI coding assistant working in: {working_dir}",
        "You have tools to read, write, and edit files, run shell commands, and search the codebase.",
        "When given a task, break it down and execute it step by step using your tools.",
    ]
    if instructions:
        parts.append(f"\n{instructions}")
    parts.append(f"\n{config.ai.system_prompt}")
    return "\n".join(parts)


async def run_cli(
    config: AppConfig,
    prompt: str | None = None,
    no_tools: bool = False,
    continue_last: bool = False,
    conversation_id: str | None = None,
) -> None:
    """Main entry point for CLI mode."""
    working_dir = os.getcwd()

    # Init DB (same as web UI)
    db_path = config.app.data_dir / "chat.db"
    config.app.data_dir.mkdir(parents=True, exist_ok=True)
    db = init_db(db_path)

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
            await mcp_manager.startup()
        except Exception as e:
            logger.warning("Failed to start MCP servers: %s", e)
            renderer.render_error(f"MCP startup failed: {e}")

    # Set up confirmation prompt for destructive operations
    async def _confirm_destructive(message: str) -> bool:
        renderer.console.print(f"\n[yellow bold]Warning:[/yellow bold] {message}")
        try:
            answer = input("  Proceed? [y/N] ").strip().lower()
            return answer in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    tool_registry.set_confirm_callback(_confirm_destructive)

    # Build unified tool executor
    async def tool_executor(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_registry.has_tool(tool_name):
            return await tool_registry.call_tool(tool_name, arguments)
        if mcp_manager:
            return await mcp_manager.call_tool(tool_name, arguments)
        raise ValueError(f"Unknown tool: {tool_name}")

    # Build unified tool list
    tools_openai: list[dict[str, Any]] = []
    tools_openai.extend(tool_registry.get_openai_tools())
    if mcp_manager:
        mcp_tools = mcp_manager.get_openai_tools()
        if mcp_tools:
            tools_openai.extend(mcp_tools)

    tools_openai_or_none = tools_openai if tools_openai else None

    # Load PARLOR.md instructions
    instructions = load_instructions(working_dir)
    extra_system_prompt = _build_system_prompt(config, working_dir, instructions)

    ai_service = AIService(config.ai)

    all_tool_names = tool_registry.list_tools()
    if mcp_manager:
        all_tool_names.extend(t["name"] for t in mcp_manager.get_all_tools())

    # Load skills
    skill_registry = SkillRegistry()
    skill_registry.load(working_dir)

    # Resolve conversation to continue
    resume_conversation_id: str | None = None
    if conversation_id:
        resume_conversation_id = conversation_id
    elif continue_last:
        convs = storage.list_conversations(db, limit=1)
        if convs:
            resume_conversation_id = convs[0]["id"]

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
        )
    else:
        git_branch = _detect_git_branch()
        renderer.render_welcome(
            model=config.ai.model,
            tool_count=len(all_tool_names),
            instructions_loaded=instructions is not None,
            working_dir=working_dir,
            git_branch=git_branch,
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
        )

    # Cleanup
    if mcp_manager:
        await mcp_manager.shutdown()
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
) -> None:
    """Run a single prompt and exit."""
    expanded = _expand_file_references(prompt, working_dir)

    if resume_conversation_id:
        conv = storage.get_conversation(db, resume_conversation_id)
        if not conv:
            renderer.render_error(f"Conversation {resume_conversation_id} not found")
            return
        messages = _load_conversation_messages(db, resume_conversation_id)
    else:
        conv = storage.create_conversation(db)
        messages = []

    storage.create_message(db, conv["id"], "user", expanded)
    messages.append({"role": "user", "content": expanded})

    cancel_event = asyncio.Event()

    loop = asyncio.get_event_loop()
    _add_signal_handler(loop, signal.SIGINT, cancel_event.set)

    thinking = False
    try:
        async for event in run_agent_loop(
            ai_service=ai_service,
            messages=messages,
            tool_executor=tool_executor,
            tools_openai=tools_openai,
            cancel_event=cancel_event,
            extra_system_prompt=extra_system_prompt,
            max_iterations=config.cli.max_tool_iterations,
        ):
            if event.kind == "token":
                if not thinking:
                    renderer.start_thinking()
                    thinking = True
                renderer.render_token(event.data["content"])
                renderer.update_thinking()
            elif event.kind == "tool_call_start":
                if thinking:
                    renderer.stop_thinking()
                    thinking = False
                renderer.render_tool_call_start(event.data["tool_name"], event.data["arguments"])
            elif event.kind == "tool_call_end":
                renderer.render_tool_call_end(
                    event.data["tool_name"], event.data["status"], event.data["output"]
                )
            elif event.kind == "assistant_message":
                if event.data["content"]:
                    storage.create_message(db, conv["id"], "assistant", event.data["content"])
            elif event.kind == "error":
                if thinking:
                    renderer.stop_thinking()
                    thinking = False
                renderer.render_error(event.data.get("message", "Unknown error"))
            elif event.kind == "done":
                if thinking:
                    renderer.stop_thinking()
                    thinking = False
                renderer.render_response_end()

        try:
            title = await ai_service.generate_title(prompt)
            storage.update_conversation_title(db, conv["id"], title)
        except Exception:
            pass

    except KeyboardInterrupt:
        if thinking:
            renderer.stop_thinking()
            thinking = False
        renderer.render_response_end()
    finally:
        _remove_signal_handler(loop, signal.SIGINT)


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
) -> None:
    """Run the interactive REPL."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.document import Document
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings

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
                partial = word[at_idx + 1:]
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
        "new", "last", "list", "resume", "compact",
        "tools", "skills", "model", "help", "quit", "exit",
    ]
    skill_names = [s.name for s in skill_registry.list_skills()] if skill_registry else []
    completer = ParlorCompleter(commands, skill_names, working_dir)

    history_path = config.app.data_dir / "cli_history"

    # Key bindings
    kb = KeyBindings()

    @kb.add("escape", "enter")
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

    session: PromptSession[str] = PromptSession(
        history=FileHistory(str(history_path)),
        key_bindings=kb,
        multiline=False,
        completer=completer,
    )

    current_model = config.ai.model

    if resume_conversation_id:
        conv_data = storage.get_conversation(db, resume_conversation_id)
        if conv_data:
            conv = conv_data
            ai_messages = _load_conversation_messages(db, resume_conversation_id)
            is_first_message = False
            renderer.console.print(
                f"[grey62]Resumed: {conv.get('title', 'Untitled')} ({len(ai_messages)} messages)[/grey62]\n"
            )
        else:
            renderer.render_error(f"Conversation {resume_conversation_id} not found, starting new")
            conv = storage.create_conversation(db)
            ai_messages = []
            is_first_message = True
    else:
        conv = storage.create_conversation(db)
        ai_messages: list[dict[str, Any]] = []
        is_first_message = True

    while True:
        _exit_flag[0] = False
        try:
            user_input = await session.prompt_async("you> ", multiline=False)
        except EOFError:
            break
        except KeyboardInterrupt:
            continue

        if _exit_flag[0]:
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # Handle commands
        if user_input.startswith("/"):
            cmd = user_input.lower().split()[0]
            if cmd in ("/quit", "/exit"):
                break
            elif cmd == "/new":
                conv = storage.create_conversation(db)
                ai_messages = []
                is_first_message = True
                renderer.console.print("[grey62]New conversation started[/grey62]\n")
                continue
            elif cmd == "/tools":
                renderer.render_tools(all_tool_names)
                continue
            elif cmd == "/help":
                renderer.render_help()
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
                    renderer.console.print(
                        f"[grey62]Resumed: {conv.get('title', 'Untitled')} ({len(ai_messages)} messages)[/grey62]\n"
                    )
                else:
                    renderer.console.print("[grey62]No previous conversations[/grey62]\n")
                continue
            elif cmd == "/list":
                convs = storage.list_conversations(db, limit=20)
                if convs:
                    renderer.console.print("\n[bold]Recent conversations:[/bold]")
                    for i, c in enumerate(convs):
                        msg_count = c.get("message_count", 0)
                        renderer.console.print(
                            f"  {i + 1}. {c['title']} ({msg_count} msgs) [grey62]{c['id'][:8]}...[/grey62]"
                        )
                    renderer.console.print("  Use [bold]/resume <number>[/bold] or [bold]/resume <id>[/bold]\n")
                else:
                    renderer.console.print("[grey62]No conversations[/grey62]\n")
                continue
            elif cmd == "/skills":
                if skill_registry:
                    skills = skill_registry.list_skills()
                    if skills:
                        renderer.console.print("\n[bold]Available skills:[/bold]")
                        for s in skills:
                            renderer.console.print(f"  /{s.name} - {s.description} [grey62]({s.source})[/grey62]")
                        renderer.console.print()
                    else:
                        renderer.console.print(
                            "[grey62]No skills loaded. Add .yaml files to"
                            " ~/.parlor/skills/ or .parlor/skills/[/grey62]\n"
                        )
                continue
            elif cmd == "/model":
                parts = user_input.split(maxsplit=1)
                if len(parts) < 2:
                    renderer.console.print(f"[grey62]Current model: {current_model}[/grey62]")
                    renderer.console.print("[grey62]Usage: /model <model_name>[/grey62]\n")
                    continue
                new_model = parts[1].strip()
                current_model = new_model
                ai_service = AIService(config.ai)
                ai_service.config.model = new_model
                renderer.console.print(f"[grey62]Switched to model: {new_model}[/grey62]\n")
                continue
            elif cmd == "/resume":
                parts = user_input.split(maxsplit=1)
                if len(parts) < 2:
                    renderer.console.print("[grey62]Usage: /resume <number> or /resume <conversation_id>[/grey62]\n")
                    continue
                target = parts[1].strip()
                resolved_id = None
                if target.isdigit():
                    idx = int(target) - 1
                    convs = storage.list_conversations(db, limit=20)
                    if 0 <= idx < len(convs):
                        resolved_id = convs[idx]["id"]
                    else:
                        renderer.render_error(f"Invalid number: {target}")
                        continue
                else:
                    resolved_id = target
                loaded = storage.get_conversation(db, resolved_id)
                if loaded:
                    conv = loaded
                    ai_messages = _load_conversation_messages(db, conv["id"])
                    is_first_message = False
                    renderer.console.print(
                        f"[grey62]Resumed: {conv.get('title', 'Untitled')} ({len(ai_messages)} messages)[/grey62]\n"
                    )
                else:
                    renderer.render_error(f"Conversation not found: {resolved_id}")
                continue

        # Check for skill invocation
        if skill_registry and user_input.startswith("/"):
            is_skill, skill_prompt = skill_registry.resolve_input(user_input)
            if is_skill:
                user_input = skill_prompt

        # Expand file references
        expanded = _expand_file_references(user_input, working_dir)

        # Auto-compact if approaching context limit
        token_estimate = _estimate_tokens(ai_messages)
        if token_estimate > _CONTEXT_AUTO_COMPACT_TOKENS:
            renderer.console.print(
                f"[yellow]Context approaching limit (~{token_estimate:,} tokens). Auto-compacting...[/yellow]"
            )
            await _compact_messages(ai_service, ai_messages, db, conv["id"])
        elif token_estimate > _CONTEXT_WARN_TOKENS:
            renderer.console.print(
                f"[yellow]Context: ~{token_estimate:,} tokens. Use /compact to free space.[/yellow]"
            )

        # Store user message
        storage.create_message(db, conv["id"], "user", expanded)
        ai_messages.append({"role": "user", "content": expanded})

        # Stream response
        cancel_event = asyncio.Event()
        loop = asyncio.get_event_loop()
        original_handler = signal.getsignal(signal.SIGINT)
        _add_signal_handler(loop, signal.SIGINT, cancel_event.set)

        thinking = False
        try:
            response_token_count = 0
            total_elapsed = 0.0
            async for event in run_agent_loop(
                ai_service=ai_service,
                messages=ai_messages,
                tool_executor=tool_executor,
                tools_openai=tools_openai,
                cancel_event=cancel_event,
                extra_system_prompt=extra_system_prompt,
                max_iterations=config.cli.max_tool_iterations,
            ):
                if event.kind == "token":
                    if not thinking:
                        renderer.start_thinking()
                        thinking = True
                    renderer.render_token(event.data["content"])
                    renderer.update_thinking()
                    enc = _get_tiktoken_encoding()
                    if enc:
                        response_token_count += len(enc.encode(event.data["content"]))
                    else:
                        response_token_count += max(1, len(event.data["content"]) // 4)
                elif event.kind == "tool_call_start":
                    if thinking:
                        total_elapsed += renderer.stop_thinking()
                        thinking = False
                    renderer.render_tool_call_start(event.data["tool_name"], event.data["arguments"])
                elif event.kind == "tool_call_end":
                    renderer.render_tool_call_end(
                        event.data["tool_name"], event.data["status"], event.data["output"]
                    )
                elif event.kind == "assistant_message":
                    if event.data["content"]:
                        storage.create_message(db, conv["id"], "assistant", event.data["content"])
                elif event.kind == "error":
                    if thinking:
                        total_elapsed += renderer.stop_thinking()
                        thinking = False
                    renderer.render_error(event.data.get("message", "Unknown error"))
                elif event.kind == "done":
                    if thinking:
                        total_elapsed += renderer.stop_thinking()
                        thinking = False
                    renderer.render_newline()
                    renderer.render_response_end()
                    renderer.render_newline()
                    # Show context footer
                    context_tokens = _estimate_tokens(ai_messages)
                    renderer.render_context_footer(
                        current_tokens=context_tokens,
                        auto_compact_threshold=_CONTEXT_AUTO_COMPACT_TOKENS,
                        response_tokens=response_token_count,
                        elapsed=total_elapsed,
                    )
                    renderer.render_newline()

            # Generate title on first exchange
            if is_first_message:
                is_first_message = False
                try:
                    title = await ai_service.generate_title(user_input)
                    storage.update_conversation_title(db, conv["id"], title)
                except Exception:
                    pass

        except KeyboardInterrupt:
            if thinking:
                renderer.stop_thinking()
            renderer.render_response_end()
        finally:
            _remove_signal_handler(loop, signal.SIGINT)
            if not _IS_WINDOWS:
                signal.signal(signal.SIGINT, original_handler)


async def _compact_messages(
    ai_service: AIService,
    ai_messages: list[dict[str, Any]],
    db: Any,
    conversation_id: str,
) -> None:
    """Summarize conversation history to reduce context size."""
    if len(ai_messages) < 4:
        renderer.console.print("[grey62]Not enough messages to compact[/grey62]\n")
        return

    original_count = len(ai_messages)
    original_tokens = _estimate_tokens(ai_messages)

    # Build summary from all messages, truncating long tool outputs
    history_text = []
    for msg in ai_messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str) and content:
            truncated = content[:500] + "..." if len(content) > 500 else content
            history_text.append(f"{role}: {truncated}")
        tool_calls = msg.get("tool_calls", [])
        for tc in tool_calls:
            func = tc.get("function", {})
            history_text.append(f"  tool_call: {func.get('name', '?')}")

    summary_prompt = (
        "Summarize the following conversation concisely, preserving:\n"
        "- Key decisions and conclusions\n"
        "- File paths that were read, written, or edited\n"
        "- Important code changes and their purpose\n"
        "- Current state of the task\n"
        "- Any errors encountered and how they were resolved\n\n"
        + "\n".join(history_text)
    )

    try:
        renderer.console.print("[grey62]Generating summary...[/grey62]")
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
    renderer.console.print(f"  [grey62]~{original_tokens:,} -> ~{new_tokens:,} tokens[/grey62]\n")

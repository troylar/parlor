"""CLI entry point for Parlor."""

from __future__ import annotations

import argparse
import asyncio
import sys
import webbrowser
from pathlib import Path

import uvicorn

from .config import _get_config_path, load_config


def _print_setup_guide(config_path: Path) -> None:
    print(
        f"\nTo get started, create {config_path} with:\n\n"
        "ai:\n"
        '  base_url: "https://your-ai-endpoint/v1"\n'
        '  api_key: "your-api-key"\n'
        '  model: "gpt-4"\n'
        '  system_prompt: "You are a helpful assistant."\n'
        "\nOr set environment variables:\n"
        "  AI_CHAT_BASE_URL=https://your-ai-endpoint/v1\n"
        "  AI_CHAT_API_KEY=your-api-key\n"
        "  AI_CHAT_MODEL=gpt-4\n",
        file=sys.stderr,
    )


def _load_config_or_exit() -> tuple[Path, object]:
    config_path = _get_config_path()
    if not config_path.exists():
        print(f"No configuration file found at {config_path}", file=sys.stderr)
        _print_setup_guide(config_path)
        sys.exit(1)
    try:
        config = load_config()
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        _print_setup_guide(config_path)
        sys.exit(1)
    return config_path, config


async def _validate_ai_connection(config) -> None:
    from .services.ai_service import AIService

    ai_service = AIService(config.ai)
    valid, message, models = await ai_service.validate_connection()
    if valid:
        print(f"AI connection: OK ({config.ai.model})")
        if models:
            print(f"  Available models: {', '.join(models[:5])}")
    else:
        print(f"AI connection: WARNING - {message}", file=sys.stderr)
        print("  The app will start, but chat may not work until the AI service is reachable.", file=sys.stderr)


async def _test_connection(config) -> None:
    from .services.ai_service import AIService

    ai_service = AIService(config.ai)

    print("Config:")
    print(f"  Endpoint: {config.ai.base_url}")
    print(f"  Model:    {config.ai.model}")
    print(f"  SSL:      {'enabled' if config.ai.verify_ssl else 'disabled'}")

    print("\n1. Listing models...")
    try:
        valid, message, models = await ai_service.validate_connection()
        if valid:
            print(f"   OK - {len(models)} model(s) available")
            for m in models[:10]:
                print(f"     - {m}")
        else:
            print(f"   FAILED - {message}")
            sys.exit(1)
    except Exception as e:
        print(f"   FAILED - {e}")
        sys.exit(1)

    print(f"\n2. Sending test prompt to {config.ai.model}...")
    try:
        response = await ai_service.client.chat.completions.create(
            model=config.ai.model,
            messages=[{"role": "user", "content": "Say hello in one sentence."}],
            max_tokens=50,
        )
        reply = response.choices[0].message.content or "(empty response)"
        print(f"   OK - Response: {reply.strip()}")
    except Exception as e:
        print(f"   FAILED - {e}")
        sys.exit(1)

    print("\nAll checks passed.")


def _run_web(config, config_path: Path) -> None:
    """Launch the web UI server."""
    print(f"Config loaded from {config_path}")
    print(f"  AI endpoint: {config.ai.base_url}")
    print(f"  Model: {config.ai.model}")
    print(f"  Data dir: {config.app.data_dir}")
    if config.mcp_servers:
        print(f"  MCP servers: {', '.join(s.name for s in config.mcp_servers)}")

    try:
        asyncio.run(_validate_ai_connection(config))
    except Exception:
        print("AI connection: Could not validate (will try on first request)", file=sys.stderr)

    from .app import create_app

    app = create_app(config)

    url = f"http://{config.app.host}:{config.app.port}"
    print(f"\nStarting Parlor at {url}")

    if config.app.host in ("0.0.0.0", "::"):
        print("  WARNING: Binding to all interfaces. The app is accessible from the network.", file=sys.stderr)

    webbrowser.open(url)

    uvicorn.run(app, host=config.app.host, port=config.app.port, log_level="info")


def _run_chat(
    config,
    prompt: str | None = None,
    no_tools: bool = False,
    continue_last: bool = False,
    resume_id: str | None = None,
    project_path: str | None = None,
) -> None:
    """Launch the CLI chat mode."""
    import os

    if project_path:
        resolved = os.path.abspath(project_path)
        if not os.path.isdir(resolved):
            print(f"Error: {project_path} is not a directory", file=sys.stderr)
            sys.exit(1)
        os.chdir(resolved)

    from .cli.repl import run_cli

    asyncio.run(run_cli(
        config,
        prompt=prompt,
        no_tools=no_tools,
        continue_last=continue_last,
        conversation_id=resume_id,
    ))


def main() -> None:
    parser = argparse.ArgumentParser(prog="parlor", description="Parlor - a private parlor for AI conversation")
    subparsers = parser.add_subparsers(dest="command")

    # `parlor chat` subcommand
    chat_parser = subparsers.add_parser("chat", help="Interactive CLI chat mode")
    chat_parser.add_argument("prompt", nargs="?", default=None, help="One-shot prompt (omit for REPL)")
    chat_parser.add_argument("--no-tools", action="store_true", help="Disable built-in tools")
    chat_parser.add_argument(
        "-c", "--continue", dest="continue_last",
        action="store_true", help="Continue the last conversation",
    )
    chat_parser.add_argument(
        "-r", "--resume", dest="resume_id",
        default=None, help="Resume a conversation by ID",
    )
    chat_parser.add_argument(
        "-p", "--path", dest="project_path",
        default=None, help="Project root directory (default: cwd)",
    )

    # Global flags
    parser.add_argument("--test", action="store_true", help="Test connection settings and exit")

    args = parser.parse_args()

    config_path, config = _load_config_or_exit()

    if args.test:
        asyncio.run(_test_connection(config))
        return

    if args.command == "chat":
        _run_chat(
            config,
            prompt=args.prompt,
            no_tools=args.no_tools,
            continue_last=args.continue_last,
            resume_id=args.resume_id,
            project_path=args.project_path,
        )
    else:
        _run_web(config, config_path)


if __name__ == "__main__":
    main()

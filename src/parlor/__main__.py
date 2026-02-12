"""CLI entry point for ai-chat command."""

from __future__ import annotations

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


def main() -> None:
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


if __name__ == "__main__":
    main()

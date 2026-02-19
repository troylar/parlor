"""CLI entry point for Anteroom."""

from __future__ import annotations

import argparse
import asyncio
import sys
import webbrowser
from pathlib import Path

import uvicorn

from . import __version__
from .config import _get_config_path, load_config


def _run_init(force: bool = False) -> None:
    """Interactive setup wizard for ~/.anteroom/config.yaml."""
    from .cli.setup import run_init_wizard

    run_init_wizard(force=force)


def _load_config_or_exit() -> tuple[Path, object]:
    config_path = _get_config_path()
    if not config_path.exists():
        print(f"No configuration file found at {config_path}", file=sys.stderr)
        from .cli.setup import run_init_wizard

        if not run_init_wizard():
            sys.exit(1)
        # Re-check after wizard
        if not config_path.exists():
            sys.exit(1)
    try:
        config = load_config()
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        print("Run 'aroom config' to fix your configuration.", file=sys.stderr)
        sys.exit(1)
    return config_path, config


async def _validate_ai_connection(config) -> None:
    from .services.ai_service import create_ai_service

    ai_service = create_ai_service(config.ai)
    valid, message, models = await ai_service.validate_connection()
    if valid:
        print(f"AI connection: OK ({config.ai.model})")
        if models:
            print(f"  Available models: {', '.join(models[:5])}")
    else:
        print(f"AI connection: WARNING - {message}", file=sys.stderr)
        print("  The app will start, but chat may not work until the AI service is reachable.", file=sys.stderr)


async def _test_connection(config) -> None:
    from .services.ai_service import create_ai_service

    ai_service = create_ai_service(config.ai)

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
            max_completion_tokens=50,
        )
        reply = response.choices[0].message.content or "(empty response)"
        print(f"   OK - Response: {reply.strip()}")
    except Exception as e:
        print(f"   FAILED - {e}")
        sys.exit(1)

    print("\nAll checks passed.")


def _run_db(args) -> None:
    """Handle `aroom db` subcommands."""
    import getpass
    import stat

    import yaml

    from .services.db_auth import hash_passphrase

    config_path = _get_config_path()
    raw: dict = {}
    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}

    action = args.db_action

    if action == "create":
        name = args.name
        db_path = args.path
        if not name or not db_path:
            print("Error: --name and --path are required for 'db create'", file=sys.stderr)
            sys.exit(1)

        passphrase = getpass.getpass("Set passphrase (empty for no auth): ")
        passphrase_hash = ""
        if passphrase:
            confirm = getpass.getpass("Confirm passphrase: ")
            if passphrase != confirm:
                print("Error: passphrases do not match", file=sys.stderr)
                sys.exit(1)
            passphrase_hash = hash_passphrase(passphrase)

        databases = raw.setdefault("databases", {})
        databases[name] = {"path": db_path}
        if passphrase_hash:
            databases[name]["passphrase_hash"] = passphrase_hash

        # Ensure the DB file's parent directory exists
        db_dir = Path(db_path).expanduser().parent
        db_dir.mkdir(parents=True, exist_ok=True)

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(raw, f, default_flow_style=False, sort_keys=False)
        config_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

        print(f"Database '{name}' registered at {db_path}")
        if passphrase_hash:
            print("Passphrase protection enabled.")

    elif action == "list":
        databases = raw.get("databases", {})
        shared = raw.get("shared_databases", [])
        if not databases and not shared:
            print("No shared databases configured.")
            return
        print("Databases:")
        for db_name, db_conf in databases.items():
            path = db_conf.get("path", "?") if isinstance(db_conf, dict) else db_conf
            auth = "yes" if isinstance(db_conf, dict) and db_conf.get("passphrase_hash") else "no"
            print(f"  {db_name}: {path} (auth: {auth})")
        for sdb in shared:
            print(f"  {sdb['name']}: {sdb['path']} (legacy format)")

    elif action == "connect":
        name = args.name
        if not name:
            print("Error: database name is required", file=sys.stderr)
            sys.exit(1)
        databases = raw.get("databases", {})
        if name not in databases:
            print(f"Error: database '{name}' not found in config", file=sys.stderr)
            sys.exit(1)
        db_conf = databases[name]
        if isinstance(db_conf, dict) and db_conf.get("passphrase_hash"):
            passphrase = getpass.getpass(f"Passphrase for '{name}': ")
            from .services.db_auth import verify_passphrase

            if not verify_passphrase(passphrase, db_conf["passphrase_hash"]):
                print("Error: invalid passphrase", file=sys.stderr)
                sys.exit(1)
        print(f"Connected to '{name}' at {db_conf.get('path', db_conf) if isinstance(db_conf, dict) else db_conf}")

    else:
        print(f"Unknown db action: {action}", file=sys.stderr)
        sys.exit(1)


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
    except KeyboardInterrupt:
        pass
    except Exception as e:
        err_name = type(e).__name__
        if "APIConnectionError" in err_name or "ConnectError" in err_name:
            print(
                f"AI connection: Cannot reach {config.ai.base_url} (will try on first request)",
                file=sys.stderr,
            )
        else:
            print("AI connection: Could not validate (will try on first request)", file=sys.stderr)

    from .app import create_app

    app = create_app(config)

    ssl_kwargs: dict[str, str] = {}
    scheme = "http"
    if config.app.tls:
        from .tls import ensure_certificates

        cert_path, key_path = ensure_certificates(config.app.data_dir)
        ssl_kwargs["ssl_certfile"] = str(cert_path)
        ssl_kwargs["ssl_keyfile"] = str(key_path)
        scheme = "https"

    url = f"{scheme}://{config.app.host}:{config.app.port}"
    print(f"\nStarting Anteroom at {url}")

    if config.app.host in ("0.0.0.0", "::"):
        print("  WARNING: Binding to all interfaces. The app is accessible from the network.", file=sys.stderr)

    webbrowser.open(url)

    uvicorn.run(app, host=config.app.host, port=config.app.port, log_level="info", **ssl_kwargs)


def _run_chat(
    config,
    prompt: str | None = None,
    no_tools: bool = False,
    continue_last: bool = False,
    resume_id: str | None = None,
    project_path: str | None = None,
    model: str | None = None,
) -> None:
    """Launch the CLI chat mode."""
    import os

    if project_path:
        # SECURITY-REVIEW: CLI arg from local user, not remote input; validated as existing directory
        resolved = os.path.abspath(project_path)
        if not os.path.isdir(resolved):
            print(f"Error: {project_path} is not a directory", file=sys.stderr)
            sys.exit(1)
        os.chdir(resolved)

    if model:
        config.ai.model = model

    from .cli.repl import run_cli

    # SECURITY-REVIEW: CLI args from local user; all storage queries use parameterized ?
    try:
        asyncio.run(
            run_cli(
                config,
                prompt=prompt,
                no_tools=no_tools,
                continue_last=continue_last,
                conversation_id=resume_id,
            )
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except BaseException as e:
        if type(e).__name__ in ("ExceptionGroup", "BaseExceptionGroup"):
            # ExceptionGroup from anyio TaskGroup teardown during Ctrl+C.
            # Only suppress if all member exceptions are cancellation/interrupt —
            # re-raise if any contain real errors to avoid masking failures.
            exceptions = getattr(e, "exceptions", ())
            _suppressed = (KeyboardInterrupt, asyncio.CancelledError)
            if all(isinstance(exc, _suppressed) for exc in exceptions):
                pass
            else:
                raise
        else:
            err_name = type(e).__name__
            if "APIConnectionError" in err_name or "ConnectError" in err_name:
                print(
                    f"\nCannot connect to API at {config.ai.base_url}.",
                    file=sys.stderr,
                )
                print("Check the URL and your network connection.", file=sys.stderr)
                print("  Config: ~/.anteroom/config.yaml (ai.base_url)", file=sys.stderr)
                print("  Env var: AI_CHAT_BASE_URL", file=sys.stderr)
                sys.exit(1)
            else:
                raise


def main() -> None:
    parser = argparse.ArgumentParser(prog="aroom", description="Anteroom - your gateway to AI conversation")
    subparsers = parser.add_subparsers(dest="command")

    # `aroom init` subcommand
    init_parser = subparsers.add_parser("init", help="Interactive setup wizard for config")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing config without asking")

    # `aroom config` subcommand
    subparsers.add_parser("config", help="View and edit configuration")

    # `aroom chat` subcommand
    chat_parser = subparsers.add_parser("chat", help="Interactive CLI chat mode")
    chat_parser.add_argument("prompt", nargs="?", default=None, help="One-shot prompt (omit for REPL)")
    chat_parser.add_argument("--no-tools", action="store_true", help="Disable built-in tools")
    chat_parser.add_argument(
        "-c",
        "--continue",
        dest="continue_last",
        action="store_true",
        help="Continue the last conversation",
    )
    chat_parser.add_argument(
        "-r",
        "--resume",
        dest="resume_id",
        default=None,
        help="Resume a conversation by ID",
    )
    chat_parser.add_argument(
        "-p",
        "--path",
        dest="project_path",
        default=None,
        help="Project root directory (default: cwd)",
    )
    chat_parser.add_argument(
        "-m",
        "--model",
        dest="model",
        default=None,
        help="Override AI model (e.g., gpt-4o, claude-3-opus)",
    )
    # `aroom db` subcommand
    db_parser = subparsers.add_parser("db", help="Manage shared databases")
    db_parser.add_argument("db_action", choices=["create", "list", "connect"], help="Database action")
    db_parser.add_argument("name", nargs="?", default=None, help="Database name")
    db_parser.add_argument("--path", default=None, help="Path to database file")

    # Global flags
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--test", action="store_true", help="Test connection settings and exit")
    parser.add_argument(
        "--allowed-tools",
        dest="allowed_tools",
        default=None,
        help="Comma-separated list of pre-allowed tools (e.g., bash,write_file)",
    )
    parser.add_argument(
        "--approval-mode",
        dest="approval_mode",
        default=None,
        choices=["auto", "ask_for_dangerous", "ask_for_writes", "ask"],
        help="Override approval mode for this session",
    )

    args = parser.parse_args()

    if args.command == "init":
        _run_init(force=getattr(args, "force", False))
        return

    if args.command == "config":
        from .cli.setup import run_config_editor

        run_config_editor()
        return

    if args.command == "db":
        _run_db(args)
        return

    config_path, config = _load_config_or_exit()

    # Apply global safety flag overrides (work for both web UI and CLI modes)
    _approval_mode = getattr(args, "approval_mode", None)
    _allowed_tools = getattr(args, "allowed_tools", None)
    if _approval_mode:
        config.safety.approval_mode = _approval_mode
        if _approval_mode == "auto":
            print(
                "WARNING: Auto-approval mode active. ALL tool calls will execute without confirmation,",
                file=sys.stderr,
            )
            print(
                "  including destructive commands (rm, git push --force, etc.).",
                file=sys.stderr,
            )
    if _allowed_tools:
        extra = [t.strip() for t in _allowed_tools.split(",") if t.strip()]
        existing = set(config.safety.allowed_tools)
        config.safety.allowed_tools.extend(t for t in extra if t not in existing)

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
            model=args.model,
        )
    else:
        _run_web(config, config_path)


if __name__ == "__main__":
    main()

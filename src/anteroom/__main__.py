"""CLI entry point for Anteroom."""

from __future__ import annotations

import argparse
import asyncio
import errno
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from . import __version__
from .config import _get_config_path, load_config


def _run_init(force: bool = False, team_config: str | None = None) -> None:
    """Interactive setup wizard for ~/.anteroom/config.yaml."""
    from .cli.setup import run_init_wizard

    run_init_wizard(force=force, team_config_path=team_config)


def _load_config_or_exit(
    team_config_path: Path | None = None,
    *,
    interactive: bool = False,
) -> tuple[Path, object, list[str]]:
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
        config, enforced_fields = load_config(
            team_config_path=team_config_path,
            interactive=interactive,
        )
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        print("Run 'aroom config' to fix your configuration.", file=sys.stderr)
        sys.exit(1)

    # Compliance rules validation (fail closed)
    from .services.compliance import validate_compliance

    compliance_result = validate_compliance(config)
    if not compliance_result.is_compliant:
        print(f"Configuration compliance failure:\n{compliance_result.format_report()}", file=sys.stderr)
        print("\nFix the configuration or contact your team administrator.", file=sys.stderr)
        sys.exit(1)

    return config_path, config, enforced_fields


def _run_config_validate(team_config_path: Path | None = None) -> None:
    """Run compliance validation and report results."""
    _config_path, config, _enforced = _load_config_or_exit(
        team_config_path,
        interactive=False,
    )

    from .services.compliance import validate_compliance

    result = validate_compliance(config)
    rule_count = len(config.compliance.rules)
    if result.is_compliant:
        if rule_count == 0:
            print("Compliance: no rules defined. Add rules to compliance.rules in config.")
        else:
            print(f"Compliance: OK ({rule_count} rule(s) passed)")
        sys.exit(0)
    else:
        print(result.format_report(), file=sys.stderr)
        sys.exit(1)


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

    elif action == "purge":
        _run_db_purge(args)

    elif action == "encrypt":
        _run_db_encrypt(args)

    else:
        print(f"Unknown db action: {action}", file=sys.stderr)
        sys.exit(1)


def _run_db_purge(args: object) -> None:
    """Handle `aroom db purge` — delete old conversations."""
    from datetime import datetime, timedelta, timezone

    from .db import init_db
    from .services.retention import purge_conversations_before, purge_orphaned_attachments

    _config_path, config, _enforced = _load_config_or_exit()

    before_str = getattr(args, "before", None)
    older_than_str = getattr(args, "older_than", None)
    dry_run = getattr(args, "dry_run", False)
    skip_confirm = getattr(args, "yes", False)

    if not before_str and not older_than_str:
        if config.storage.retention_days > 0:
            older_than_str = f"{config.storage.retention_days}d"
            print(f"Using configured retention: {config.storage.retention_days} days")
        else:
            print("Error: specify --before YYYY-MM-DD or --older-than Nd", file=sys.stderr)
            sys.exit(1)

    if before_str:
        try:
            cutoff = datetime.strptime(before_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"Error: invalid date format '{before_str}', expected YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)
    else:
        assert older_than_str is not None
        if not older_than_str.endswith("d"):
            print(f"Error: --older-than must end with 'd' (e.g., 90d), got '{older_than_str}'", file=sys.stderr)
            sys.exit(1)
        try:
            days = int(older_than_str[:-1])
        except ValueError:
            print(f"Error: invalid number in '{older_than_str}'", file=sys.stderr)
            sys.exit(1)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    db = init_db(config.app.data_dir / "chat.db")

    # Preview
    count = purge_conversations_before(db, cutoff, config.app.data_dir, dry_run=True)
    orphaned = purge_orphaned_attachments(config.app.data_dir, db, dry_run=True)

    print(f"Cutoff:       {cutoff.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Conversations: {count} to purge")
    print(f"Orphaned dirs: {orphaned} to remove")

    if count == 0 and orphaned == 0:
        print("Nothing to purge.")
        db.close()
        return

    if dry_run:
        print("(dry run — no changes made)")
        db.close()
        return

    if not skip_confirm:
        answer = input("Proceed with purge? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            db.close()
            return

    purged = purge_conversations_before(
        db, cutoff, config.app.data_dir, purge_attachments=config.storage.purge_attachments
    )
    orphans_removed = purge_orphaned_attachments(config.app.data_dir, db)
    db.close()
    print(f"Purged {purged} conversation(s), removed {orphans_removed} orphaned attachment dir(s).")


def _run_db_encrypt(args: object) -> None:
    """Handle `aroom db encrypt` — migrate plaintext DB to encrypted."""
    _config_path, config, _enforced = _load_config_or_exit()

    from .services.encryption import derive_db_key, is_sqlcipher_available, migrate_plaintext_to_encrypted

    if not is_sqlcipher_available():
        print("Error: sqlcipher3 not installed. Install with: pip install sqlcipher3", file=sys.stderr)
        sys.exit(1)

    private_key = config.identity.private_key if config.identity else ""
    if not private_key:
        print("Error: no identity key found. Run: aroom init", file=sys.stderr)
        sys.exit(1)

    db_path = config.app.data_dir / "chat.db"
    if not db_path.exists():
        print(f"Error: database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    skip_confirm = getattr(args, "yes", False)

    print(f"Database: {db_path}")
    print("This will encrypt the database in place.")
    print("A backup will be created with a .bak-plaintext suffix.")

    if not skip_confirm:
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return

    key = derive_db_key(private_key)
    try:
        backup_path = migrate_plaintext_to_encrypted(db_path, key)
        print(f"Encryption complete. Backup at: {backup_path}")
        print()
        print("WARNING: The backup contains your UNENCRYPTED data.")
        print("After verifying the encrypted database works, securely delete it:")
        print(f"  rm '{backup_path}'")
        print()
        print("Update your config.yaml to enable encryption:")
        print("  storage:")
        print("    encrypt_at_rest: true")
    except Exception as e:
        print(f"Error: migration failed: {e}", file=sys.stderr)
        sys.exit(1)


def _run_usage(
    config, period: str | None = None, conversation_id: str | None = None, output_json: bool = False
) -> None:
    """Show token usage and cost statistics."""
    import json
    from datetime import datetime, timedelta, timezone

    from .db import init_db
    from .services import storage

    db = init_db(config.app.data_dir)
    usage_cfg = config.cli.usage
    now = datetime.now(timezone.utc)

    if period:
        periods = {
            "day": ("Today", (now - timedelta(days=1)).isoformat()),
            "week": ("This week", (now - timedelta(days=usage_cfg.week_days)).isoformat()),
            "month": ("This month", (now - timedelta(days=usage_cfg.month_days)).isoformat()),
            "all": ("All time", None),
        }
        selected = [periods[period]]
    else:
        selected = [
            ("Today", (now - timedelta(days=1)).isoformat()),
            ("This week", (now - timedelta(days=usage_cfg.week_days)).isoformat()),
            ("This month", (now - timedelta(days=usage_cfg.month_days)).isoformat()),
            ("All time", None),
        ]

    all_results = {}
    for label, since in selected:
        stats = storage.get_usage_stats(db, since=since, conversation_id=conversation_id)
        total_prompt = sum(s.get("prompt_tokens", 0) or 0 for s in stats)
        total_completion = sum(s.get("completion_tokens", 0) or 0 for s in stats)
        total_tokens = sum(s.get("total_tokens", 0) or 0 for s in stats)
        total_messages = sum(s.get("message_count", 0) or 0 for s in stats)

        total_cost = 0.0
        for s in stats:
            model = s.get("model", "") or ""
            prompt_t = s.get("prompt_tokens", 0) or 0
            completion_t = s.get("completion_tokens", 0) or 0
            costs = usage_cfg.model_costs.get(model, {})
            input_rate = costs.get("input", 0.0)
            output_rate = costs.get("output", 0.0)
            total_cost += (prompt_t / 1_000_000) * input_rate + (completion_t / 1_000_000) * output_rate

        all_results[label] = {
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_tokens,
            "message_count": total_messages,
            "estimated_cost": round(total_cost, 4),
            "by_model": [
                {
                    "model": s.get("model", "unknown"),
                    "prompt_tokens": s.get("prompt_tokens", 0) or 0,
                    "completion_tokens": s.get("completion_tokens", 0) or 0,
                    "total_tokens": s.get("total_tokens", 0) or 0,
                    "message_count": s.get("message_count", 0) or 0,
                }
                for s in stats
            ],
        }

    if output_json:
        print(json.dumps(all_results, indent=2))
        return

    print("\nToken Usage")
    print("=" * 50)
    for label, data in all_results.items():
        if data["total_tokens"] == 0:
            print(f"\n  {label}: no usage data")
            continue
        print(f"\n  {label} ({data['message_count']} messages)")
        print(f"    Prompt:     {data['prompt_tokens']:>12,} tokens")
        print(f"    Completion: {data['completion_tokens']:>12,} tokens")
        print(f"    Total:      {data['total_tokens']:>12,} tokens")
        if data["estimated_cost"] > 0:
            print(f"    Est. cost:  ${data['estimated_cost']:>11,.4f}")
        if len(data["by_model"]) > 1:
            print("    By model:")
            for m in data["by_model"]:
                print(f"      {m['model']}: {m['total_tokens']:,} tokens")
    print()


def _run_audit(args) -> None:
    """Handle `aroom audit` subcommands."""
    action = getattr(args, "audit_action", None)
    if not action:
        print("Usage: aroom audit {verify,purge}", file=sys.stderr)
        sys.exit(1)

    config_path, config, _enforced = _load_config_or_exit()

    if action == "verify":
        from .services.audit import verify_chain

        audit_file = getattr(args, "audit_file", None)
        if audit_file:
            log_path = Path(audit_file).resolve()
        else:
            from datetime import datetime, timezone

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            log_dir = Path(config.audit.log_path) if config.audit.log_path else config.app.data_dir / "audit"
            log_path = log_dir / f"audit-{today}.jsonl"

        if not log_path.exists():
            print(f"Audit log not found: {log_path}", file=sys.stderr)
            sys.exit(1)

        private_key = config.identity.private_key if config.identity else ""
        if not private_key:
            print("No identity key found. Cannot verify HMAC chain.", file=sys.stderr)
            sys.exit(1)

        results = verify_chain(log_path, private_key)
        if not results:
            print(f"Audit log is empty: {log_path}")
            return

        valid_count = sum(1 for r in results if r["valid"])
        invalid_count = len(results) - valid_count

        print(f"Audit Log: {log_path}")
        print(f"Entries:   {len(results)}")
        print(f"Valid:     {valid_count}")
        if invalid_count:
            print(f"INVALID:   {invalid_count}")
            print("\nTampered or corrupted entries:")
            for r in results:
                if not r["valid"]:
                    print(f"  Line {r['line']}: {r['event_type']} at {r['timestamp']} {r.get('error', '')}")
            sys.exit(1)
        else:
            print("Chain:     INTACT")

    elif action == "purge":
        from .services.audit import create_audit_writer

        private_key = config.identity.private_key if config.identity else ""
        writer = create_audit_writer(config, private_key_pem=private_key)
        if not writer.enabled:
            print("Audit log is not enabled.", file=sys.stderr)
            sys.exit(1)
        deleted = writer.purge_old_logs()
        print(f"Purged {deleted} audit log file(s) older than {config.audit.retention_days} days.")


def _run_web(config, config_path: Path, *, debug: bool = False, enforced_fields: list[str] | None = None) -> None:
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

    app = create_app(config, enforced_fields=enforced_fields)

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

    def _open_browser_when_ready(host: str, port: int, url: str) -> None:
        """Wait for the server to accept connections, then open the browser."""
        for _ in range(50):  # 5 seconds max
            try:
                with socket.create_connection((host, port), timeout=0.5):
                    webbrowser.open(url)
                    return
            except OSError:
                time.sleep(0.1)

    browser_host = "127.0.0.1" if config.app.host in ("0.0.0.0", "::") else config.app.host
    browser_thread = threading.Thread(
        target=_open_browser_when_ready,
        args=(browser_host, config.app.port, url),
        daemon=True,
    )
    browser_thread.start()

    try:
        uvicorn.run(
            app,
            host=config.app.host,
            port=config.app.port,
            log_level="debug" if debug else "info",
            **ssl_kwargs,
        )
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            port = config.app.port
            alt = port + 1 if port < 65535 else port - 1
            print(f"\nPort {port} is already in use.", file=sys.stderr)
            print(f"Try a different port with: aroom --port {alt}", file=sys.stderr)
            print(f"  Or set env var: AI_CHAT_PORT={alt}", file=sys.stderr)
            sys.exit(1)
        if e.errno == errno.EADDRNOTAVAIL:
            print(
                f"\nAddress {config.app.host}:{config.app.port} is not available on this host.",
                file=sys.stderr,
            )
            print("Check the host binding in your config (app.host).", file=sys.stderr)
            sys.exit(1)
        raise


def _resolve_project_id(config: object, project_name: str) -> str:
    """Resolve a project name to its ID, or exit with an error."""
    from .db import get_db
    from .services import storage

    db = get_db(config.app.data_dir / "anteroom.db")
    project = storage.get_project_by_name(db, project_name)
    if not project:
        print(f"Error: Project '{project_name}' not found.", file=sys.stderr)
        print("Run `aroom projects` to list available projects.", file=sys.stderr)
        sys.exit(1)
    return project["id"]


def _run_projects(config: object) -> None:
    """List all named projects."""
    from rich.console import Console
    from rich.table import Table

    from .db import get_db
    from .services import storage

    db = get_db(config.app.data_dir / "anteroom.db")
    projects = storage.list_projects(db)
    if not projects:
        print("No projects found. Create one in the web UI.")
        return

    console = Console()
    table = Table(title="Projects")
    table.add_column("Name", style="bold")
    table.add_column("Model")
    table.add_column("Instructions", max_width=50)
    table.add_column("Updated")

    for p in projects:
        instructions_preview = (p.get("instructions") or "")[:50]
        if len(p.get("instructions") or "") > 50:
            instructions_preview += "..."
        table.add_row(
            p["name"],
            p.get("model") or "(default)",
            instructions_preview,
            p.get("updated_at", "")[:10],
        )
    console.print(table)


def _run_chat(
    config,
    prompt: str | None = None,
    no_tools: bool = False,
    continue_last: bool = False,
    resume_id: str | None = None,
    project_path: str | None = None,
    project_id: str | None = None,
    model: str | None = None,
    trust_project: bool = False,
    no_project_context: bool = False,
    plan_mode: bool = False,
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
                project_id=project_id,
                trust_project=trust_project,
                no_project_context=no_project_context,
                plan_mode=plan_mode,
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


def _run_exec(
    config,
    prompt: str,
    output_json: bool = False,
    no_conversation: bool = False,
    no_tools: bool = False,
    model: str | None = None,
    timeout: float = 120.0,
    quiet: bool = False,
    verbose: bool = False,
    no_project_context: bool = False,
    trust_project: bool = False,
    project_id: str | None = None,
) -> None:
    """Launch non-interactive exec mode."""
    if model:
        config.ai.model = model

    timeout = max(10.0, min(timeout, 600.0))

    from .cli.exec_mode import run_exec_mode

    try:
        exit_code = asyncio.run(
            run_exec_mode(
                config,
                prompt=prompt,
                output_json=output_json,
                no_conversation=no_conversation,
                no_tools=no_tools,
                timeout=timeout,
                quiet=quiet,
                verbose=verbose,
                no_project_context=no_project_context,
                trust_project=trust_project,
                project_id=project_id,
            )
        )
        sys.exit(exit_code)
    except (KeyboardInterrupt, asyncio.CancelledError):
        sys.exit(130)
    except BaseException as e:
        if type(e).__name__ in ("ExceptionGroup", "BaseExceptionGroup"):
            exceptions = getattr(e, "exceptions", ())
            _suppressed = (KeyboardInterrupt, asyncio.CancelledError)
            if all(isinstance(exc, _suppressed) for exc in exceptions):
                sys.exit(130)
            else:
                raise
        err_name = type(e).__name__
        if "APIConnectionError" in err_name or "ConnectError" in err_name:
            print(f"Cannot connect to API at {config.ai.base_url}.", file=sys.stderr)
            sys.exit(1)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(prog="aroom", description="Anteroom - your gateway to AI conversation")
    subparsers = parser.add_subparsers(dest="command")

    # `aroom init` subcommand
    init_parser = subparsers.add_parser("init", help="Interactive setup wizard for config")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing config without asking")
    init_parser.add_argument(
        "--team-config",
        dest="init_team_config",
        default=None,
        help="Bootstrap from a team config file (sets team_config_path and prompts for required keys)",
    )

    # `aroom config` subcommand
    config_parser = subparsers.add_parser("config", help="View and edit configuration")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_subparsers.add_parser("validate", help="Check compliance rules without starting the app")

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
    chat_parser.add_argument(
        "--trust-project",
        action="store_true",
        help="Auto-trust the current project's ANTEROOM.md without prompting",
    )
    chat_parser.add_argument(
        "--no-project-context",
        action="store_true",
        help="Skip loading project-level ANTEROOM.md entirely",
    )
    chat_parser.add_argument(
        "--plan",
        action="store_true",
        help="Start in planning mode (explore-only, then approve to implement)",
    )
    # `aroom exec` subcommand
    exec_parser = subparsers.add_parser("exec", help="Non-interactive exec mode for scripting and CI")
    exec_parser.add_argument("prompt", help="Prompt to execute")
    exec_parser.add_argument("--json", dest="output_json", action="store_true", help="Output structured JSON")
    exec_parser.add_argument(
        "--no-conversation",
        action="store_true",
        help="Skip user/assistant message persistence (tool audit always retained)",
    )
    exec_parser.add_argument("--no-tools", action="store_true", help="Disable all tool use")
    exec_parser.add_argument("-m", "--model", dest="exec_model", default=None, help="Override AI model")
    exec_parser.add_argument(
        "--timeout", type=float, default=120.0, help="Wall-clock timeout in seconds (default: 120, exit code 124)"
    )
    exec_parser.add_argument("-q", "--quiet", action="store_true", help="Suppress all stderr progress")
    exec_parser.add_argument("-v", "--verbose", action="store_true", help="Show full tool call detail on stderr")
    exec_parser.add_argument("--no-project-context", action="store_true", help="Skip loading ANTEROOM.md")
    exec_parser.add_argument(
        "--trust-project",
        action="store_true",
        help="Trust and load the project's ANTEROOM.md (skipped by default in exec mode)",
    )

    # `aroom usage` subcommand
    usage_parser = subparsers.add_parser("usage", help="Show token usage and cost statistics")
    usage_parser.add_argument(
        "--period",
        choices=["day", "week", "month", "all"],
        default=None,
        help="Time period (default: show all periods)",
    )
    usage_parser.add_argument(
        "--conversation",
        dest="conversation_id",
        default=None,
        help="Filter to a specific conversation ID",
    )
    usage_parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Output as JSON",
    )

    # `aroom audit` subcommand
    audit_parser = subparsers.add_parser("audit", help="Audit log management")
    audit_subparsers = audit_parser.add_subparsers(dest="audit_action")
    audit_verify_parser = audit_subparsers.add_parser("verify", help="Verify HMAC chain integrity of audit log")
    audit_verify_parser.add_argument(
        "--file",
        dest="audit_file",
        default=None,
        help="Path to specific audit log file (default: today's log)",
    )
    audit_subparsers.add_parser("purge", help="Delete audit logs older than retention period")

    # `aroom db` subcommand
    db_parser = subparsers.add_parser("db", help="Manage shared databases and data lifecycle")
    db_parser.add_argument(
        "db_action", choices=["create", "list", "connect", "purge", "encrypt"], help="Database action"
    )
    db_parser.add_argument("name", nargs="?", default=None, help="Database name")
    db_parser.add_argument("--path", default=None, help="Path to database file")
    db_parser.add_argument("--before", default=None, help="Purge conversations before this date (YYYY-MM-DD)")
    db_parser.add_argument(
        "--older-than", dest="older_than", default=None, help="Purge conversations older than N days (e.g., 90d, 30d)"
    )
    db_parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="Preview purge without deleting")
    db_parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")

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
    parser.add_argument(
        "--read-only",
        dest="read_only",
        action="store_true",
        default=False,
        help="Enable read-only mode: only READ-tier tools are available",
    )
    parser.add_argument(
        "--port",
        dest="port",
        type=int,
        default=None,
        help="Override port for web UI (e.g., --port 9090)",
    )
    parser.add_argument(
        "--temperature",
        dest="temperature",
        type=float,
        default=None,
        help="Override model temperature (0.0-2.0; lower = more focused)",
    )
    parser.add_argument(
        "--top-p",
        dest="top_p",
        type=float,
        default=None,
        help="Override model top_p (0.0-1.0)",
    )
    parser.add_argument(
        "--seed",
        dest="seed",
        type=int,
        default=None,
        help="Set random seed for deterministic outputs",
    )
    parser.add_argument(
        "--project",
        dest="project_name",
        default=None,
        help="Load a named project (instructions, model override, source context)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable debug logging to stderr (useful for MCP troubleshooting)",
    )
    parser.add_argument(
        "--team-config",
        dest="team_config",
        default=None,
        help="Path to team configuration file (YAML)",
    )

    # `aroom projects` subcommand
    subparsers.add_parser("projects", help="List named projects")

    args = parser.parse_args()

    # Configure logging early, before any module-level loggers are used.
    # Priority: --debug flag > AI_CHAT_LOG_LEVEL env var > default (WARNING)
    _valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    log_level_name = os.environ.get("AI_CHAT_LOG_LEVEL", "").upper()
    if args.debug:
        log_level = logging.DEBUG
    elif log_level_name in _valid_log_levels:
        log_level = getattr(logging, log_level_name)
    else:
        log_level = logging.WARNING
    logging.basicConfig(
        level=log_level,
        stream=sys.stderr,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "init":
        _run_init(
            force=getattr(args, "force", False),
            team_config=getattr(args, "init_team_config", None),
        )
        return

    if args.command == "config":
        if getattr(args, "config_command", None) == "validate":
            tc_arg = getattr(args, "team_config", None)
            tc_path = Path(tc_arg) if tc_arg else None
            _run_config_validate(team_config_path=tc_path)
            return
        from .cli.setup import run_config_editor

        run_config_editor()
        return

    if args.command == "db":
        _run_db(args)
        return

    if args.command == "audit":
        _run_audit(args)
        return

    _team_config_arg = getattr(args, "team_config", None)
    _team_config_path = Path(_team_config_arg) if _team_config_arg else None
    _is_interactive = args.command in ("chat", None)  # chat or web UI (default)
    config_path, config, enforced_fields = _load_config_or_exit(
        team_config_path=_team_config_path,
        interactive=_is_interactive,
    )

    # Apply global safety flag overrides (work for both web UI and CLI modes)
    _approval_mode = getattr(args, "approval_mode", None)
    _allowed_tools = getattr(args, "allowed_tools", None)
    if _approval_mode:
        if "safety.approval_mode" in enforced_fields:
            print(
                "WARNING: --approval-mode ignored; 'safety.approval_mode' is enforced by team config.",
                file=sys.stderr,
            )
        else:
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

    _read_only = getattr(args, "read_only", False)
    if _read_only:
        if "safety.read_only" in enforced_fields:
            print(
                "WARNING: --read-only ignored; 'safety.read_only' is enforced by team config.",
                file=sys.stderr,
            )
        else:
            config.safety.read_only = True

    _port = getattr(args, "port", None)
    if _port is not None:
        if "app.port" in enforced_fields:
            print("WARNING: --port ignored; 'app.port' is enforced by team config.", file=sys.stderr)
        else:
            if not 1 <= _port <= 65535:
                print(f"Invalid port: {_port}. Must be between 1 and 65535.", file=sys.stderr)
                sys.exit(1)
            config.app.port = _port

    _temperature = getattr(args, "temperature", None)
    if _temperature is not None:
        config.ai.temperature = max(0.0, min(2.0, _temperature))

    _top_p = getattr(args, "top_p", None)
    if _top_p is not None:
        config.ai.top_p = max(0.0, min(1.0, _top_p))

    _seed = getattr(args, "seed", None)
    if _seed is not None:
        config.ai.seed = _seed

    if args.test:
        asyncio.run(_test_connection(config))
        return

    if args.command == "usage":
        _run_usage(
            config,
            period=getattr(args, "period", None),
            conversation_id=getattr(args, "conversation_id", None),
            output_json=getattr(args, "output_json", False),
        )
        return

    if args.command == "projects":
        _run_projects(config)
        return

    # Resolve --project <name> to project_id
    _project_name = getattr(args, "project_name", None)
    _project_id: str | None = None
    if _project_name:
        _project_id = _resolve_project_id(config, _project_name)

    if args.command == "chat":
        _run_chat(
            config,
            prompt=args.prompt,
            no_tools=args.no_tools,
            continue_last=args.continue_last,
            resume_id=args.resume_id,
            project_path=args.project_path,
            project_id=_project_id,
            model=args.model,
            trust_project=args.trust_project,
            no_project_context=args.no_project_context,
            plan_mode=args.plan,
        )
    elif args.command == "exec":
        _run_exec(
            config,
            prompt=args.prompt,
            output_json=args.output_json,
            no_conversation=args.no_conversation,
            no_tools=args.no_tools,
            model=args.exec_model,
            timeout=args.timeout,
            quiet=args.quiet,
            verbose=args.verbose,
            no_project_context=args.no_project_context,
            trust_project=args.trust_project,
            project_id=_project_id,
        )
    else:
        _run_web(config, config_path, debug=args.debug, enforced_fields=enforced_fields)


if __name__ == "__main__":
    main()

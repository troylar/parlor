"""CLI entry point for Anteroom."""

from __future__ import annotations

import argparse
import asyncio
import errno
import json
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

import uvicorn

if TYPE_CHECKING:
    from rich.console import Console

    from .db import ThreadSafeConnection

from . import __version__
from .config import AppConfig, _get_config_path, load_config


def _run_init(force: bool = False, team_config: str | None = None) -> None:
    """Interactive setup wizard for ~/.anteroom/config.yaml."""
    from .cli.setup import run_init_wizard

    run_init_wizard(force=force, team_config_path=team_config)


def _collect_pack_overlay() -> dict[str, Any] | None:
    """Collect merged config overlays from attached packs.

    Opens the DB at the default data_dir (without requiring config.yaml)
    and queries for globally-attached packs with ``config_overlay``
    artifacts.  Returns the merged overlay dict, or ``None`` if no
    overlays are active.

    This is Phase 1 of the two-phase config load: we need the DB to
    discover pack overlays, then feed those overlays into
    :func:`load_config` (Phase 2) so they participate in the merge chain.
    """
    from .config import _resolve_data_dir
    from .db import get_db

    data_dir = _resolve_data_dir()
    db_path = data_dir / "chat.db"
    if not db_path.exists():
        return None

    try:
        db = get_db(db_path)
        from .services.config_overlays import collect_pack_overlays, merge_pack_overlays
        from .services.pack_attachments import get_active_pack_ids, get_attachment_priorities

        active_ids = get_active_pack_ids(db)
        if not active_ids:
            return None

        overlays = collect_pack_overlays(db, active_ids)
        if not overlays:
            return None

        priorities = get_attachment_priorities(db, active_ids)
        return merge_pack_overlays(overlays, priorities)
    except Exception:
        logging.getLogger(__name__).debug("Failed to collect pack overlays", exc_info=True)
        return None


def _load_config_or_exit(
    team_config_path: Path | None = None,
    *,
    interactive: bool = False,
) -> tuple[Path, AppConfig, list[str]]:
    config_path = _get_config_path()

    # Phase 1: collect pack config overlays from the DB (if any).
    # This runs before the init wizard so that pack overlays can provide
    # required fields like ai.base_url and ai.api_key, enabling
    # zero-config onboarding via `aroom pack install <url> --attach`.
    pack_config = _collect_pack_overlay()

    if not config_path.exists() and not pack_config:
        # No config file AND no pack overlays — run the init wizard.
        print(f"No configuration file found at {config_path}", file=sys.stderr)
        from .cli.setup import run_init_wizard

        if not run_init_wizard():
            sys.exit(1)
        if not config_path.exists():
            sys.exit(1)

    # Phase 2: load config with pack overlays in the merge chain.
    try:
        config, enforced_fields = load_config(
            team_config_path=team_config_path,
            interactive=interactive,
            pack_config=pack_config,
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


def _ensure_db_for_pack_ops() -> tuple[Path, "ThreadSafeConnection"]:
    """Return (data_dir, db) without requiring config.yaml.

    For pack commands that need a database but not a full config (e.g.
    ``aroom pack install <url>``), this creates ``~/.anteroom/`` and
    initializes the DB with default settings.  If a config file exists,
    it is loaded normally to honour the user's ``data_dir`` setting.
    """
    from .config import _resolve_data_dir
    from .db import get_db

    config_path = _get_config_path()
    if config_path.exists():
        try:
            config, _ = load_config()
            data_dir = config.app.data_dir
        except Exception:
            # Config may be malformed YAML (yaml.YAMLError), invalid
            # (ValueError), or unreadable (OSError).  Fall back to the
            # default data directory so pack operations still work.
            data_dir = _resolve_data_dir()
    else:
        data_dir = _resolve_data_dir()

    data_dir.mkdir(parents=True, exist_ok=True)
    db = get_db(data_dir / "chat.db")
    return data_dir, db


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


def _run_config_view(team_config_path: Path | None = None, *, with_sources: bool = False) -> None:
    """Display current configuration, optionally annotated with source layers.

    Without ``--with-sources``, prints the final merged config as YAML.

    With ``--with-sources``, displays a Rich table where each config key is
    annotated with the layer that set it.  Layers are reconstructed by
    re-reading the raw config files and checking environment variables::

        default  → value comes from dataclass defaults (no file set it)
        personal → value present in ~/.anteroom/config.yaml
        team     → value present in team config file
        team (enforced) → team config + listed in ``enforce`` section
        env var  → overridden by an AI_CHAT_* environment variable

    Pack and space layers require DB / runtime context and are not yet
    tracked here — they will show as "default" or "personal" depending
    on whether the personal file also sets the key.  This is a known v1
    limitation documented in the PR (#759).
    """
    from dataclasses import asdict

    from rich.console import Console
    from rich.table import Table

    config_path, config, enforced_fields = _load_config_or_exit(
        team_config_path,
        interactive=False,
    )

    sensitive_keys = {"ai.api_key", "embeddings.api_key"}

    def _redact_sensitive(flat: dict[str, Any]) -> dict[str, Any]:
        """Replace sensitive values with '***' in a flat dot-path dict."""
        return {k: "***" if k in sensitive_keys and v else v for k, v in flat.items()}

    if not with_sources:
        import yaml

        from .services.config_overlays import flatten_to_dot_paths

        console = Console()
        flat = flatten_to_dot_paths(asdict(config))
        redacted = _redact_sensitive(flat)
        console.print(yaml.dump(redacted, default_flow_style=False, sort_keys=True))
        return

    import os

    import yaml

    from .services.config_overlays import flatten_to_dot_paths, track_config_sources

    # Reconstruct layers in precedence order (lowest to highest).
    # track_config_sources uses last-writer-wins, so we add layers from
    # lowest precedence to highest — the final source_map reflects the
    # highest-precedence layer that actually set each key.
    layers: list[tuple[str, dict[str, Any]]] = []

    # 1. Team config (lowest precedence among file layers)
    from .services.team_config import discover_team_config, load_team_config

    team_path = discover_team_config(
        cli_path=team_config_path,
        env_path=os.environ.get("AI_CHAT_TEAM_CONFIG"),
    )
    if team_path:
        team_raw, _enforced = load_team_config(team_path, interactive=False)
        if team_raw:
            layers.append(("team", team_raw))

    # 2. Personal config (overrides team)
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            personal_raw = yaml.safe_load(f) or {}
        if personal_raw:
            layers.append(("personal", personal_raw))

    # 3. Environment variable overrides (highest tracked precedence).
    # Check which AI_CHAT_* env vars are set and build a synthetic layer.
    env_overrides: dict[str, Any] = {}
    _env_to_dotpath = {
        "AI_CHAT_BASE_URL": "ai.base_url",
        "AI_CHAT_API_KEY": "ai.api_key",
        "AI_CHAT_API_KEY_COMMAND": "ai.api_key_command",
        "AI_CHAT_MODEL": "ai.model",
        "AI_CHAT_SYSTEM_PROMPT": "ai.system_prompt",
        "AI_CHAT_VERIFY_SSL": "ai.verify_ssl",
        "AI_CHAT_REQUEST_TIMEOUT": "ai.request_timeout",
        "AI_CHAT_CONNECT_TIMEOUT": "ai.connect_timeout",
        "AI_CHAT_WRITE_TIMEOUT": "ai.write_timeout",
        "AI_CHAT_POOL_TIMEOUT": "ai.pool_timeout",
        "AI_CHAT_FIRST_TOKEN_TIMEOUT": "ai.first_token_timeout",
        "AI_CHAT_CHUNK_STALL_TIMEOUT": "ai.chunk_stall_timeout",
    }
    for env_var, dot_path in _env_to_dotpath.items():
        if os.environ.get(env_var):
            # Build nested dict from dot path for track_config_sources
            parts = dot_path.split(".")
            d: dict[str, Any] = env_overrides
            for part in parts[:-1]:
                d = d.setdefault(part, {})
            d[parts[-1]] = os.environ[env_var]
    if env_overrides:
        layers.append(("env var", env_overrides))

    # Build source map — last writer wins, matching real precedence
    enforced_set = set(enforced_fields)
    source_map = track_config_sources(layers)

    # Flatten final merged config for display
    final_flat = _redact_sensitive(flatten_to_dot_paths(asdict(config)))

    console = Console()
    table = Table(title="Configuration", show_lines=False)
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")
    table.add_column("Source", style="green")

    for key in sorted(final_flat.keys()):
        value = str(final_flat[key])
        if len(value) > 80:
            value = value[:77] + "..."
        # Enforced fields always show as team (enforced), regardless of
        # what source_map says — enforcement re-applies after every merge.
        if key in enforced_set:
            source = "[bold red]team (enforced)[/bold red]"
        elif key in source_map:
            source = source_map[key]
        else:
            source = "default"
        table.add_row(key, value, source)

    console.print(table)


async def _validate_ai_connection(config: AppConfig) -> None:
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


def _check_knowledge_deps() -> None:
    """Check availability of optional knowledge pipeline dependencies."""
    deps = [
        ("fastembed", "Local embeddings (default)", "pip install fastembed"),
        ("pypdf", "PDF text extraction", "pip install anteroom[office]"),
        ("docx", "DOCX text extraction", "pip install anteroom[office]"),
        ("pptx", "PPTX text extraction", "pip install anteroom[office]"),
        ("openpyxl", "XLSX text extraction", "pip install anteroom[office]"),
        ("usearch", "Vector similarity search", "pip install usearch"),
    ]
    all_ok = True
    for module, description, install_hint in deps:
        try:
            __import__(module)
            print(f"   OK - {description}")
        except ImportError:
            print(f"   MISSING - {description} — install with: {install_hint}")
            all_ok = False
    if all_ok:
        print("   All knowledge pipeline dependencies available.")
    else:
        print("   Some optional dependencies are missing (knowledge features will degrade gracefully).")


async def _test_connection(config: AppConfig) -> None:
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

    print("\n3. Checking knowledge pipeline dependencies...")
    _check_knowledge_deps()
    print("\nAll connection checks passed.")


def _run_db(args: argparse.Namespace) -> None:
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
    config: AppConfig, period: str | None = None, conversation_id: str | None = None, output_json: bool = False
) -> None:
    """Show token usage and cost statistics."""
    import json
    from datetime import datetime, timedelta, timezone

    from .db import init_db
    from .services import storage

    db = init_db(config.app.data_dir / "chat.db")
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

    all_results: dict[str, dict[str, Any]] = {}
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


def _run_audit(args: argparse.Namespace) -> None:
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


def _run_web(
    config: AppConfig, config_path: Path, *, debug: bool = False, enforced_fields: list[str] | None = None
) -> None:
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

    ssl_kwargs: dict[str, Any] = {}
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


_STEP_LABELS: dict[str, str] = {
    "database": "Initializing database",
    "mcp_servers": "Connecting to MCP servers",
    "tools": "Registering tools",
    "embeddings": "Probing embeddings",
    "packs": "Loading packs",
    "artifacts": "Loading artifacts and skills",
    "ready": "Ready",
}


def _read_last_progress(path: Path) -> dict[str, str] | None:
    """Read the progress file and return the last parseable JSON event."""
    try:
        text = path.read_text()
    except OSError:
        return None
    last: dict[str, str] | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                last = obj
        except (json.JSONDecodeError, ValueError):
            continue
    return last


def _run_start(config: AppConfig, config_path: Path, args: argparse.Namespace) -> None:
    """Start the web UI server in the background."""
    from .services.server_manager import ServerManager

    mgr = ServerManager(data_dir=config.app.data_dir, host=config.app.host, port=config.app.port)
    status = mgr.get_status()

    if status.alive and status.responding:
        print(f"Server is already running (PID {status.pid}) on port {status.port}.", file=sys.stderr)
        sys.exit(1)

    if status.alive and not status.responding:
        print(f"Process {status.pid} exists but port {status.port} is not responding.", file=sys.stderr)
        print("Use 'aroom stop' to clean up, then try again.", file=sys.stderr)
        sys.exit(1)

    extra_args: list[str] = []
    if config.app.tls:
        extra_args.append("--tls")

    try:
        pid = mgr.start_background(
            debug=getattr(args, "debug", False),
            extra_args=extra_args or None,
        )
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    scheme = "https" if config.app.tls else "http"
    probe_host = "127.0.0.1" if config.app.host in ("0.0.0.0", "::") else config.app.host
    url = f"{scheme}://{probe_host}:{config.app.port}"

    from rich.console import Console
    from rich.status import Status

    console = Console()
    status_text = "Starting Anteroom..."
    started = False

    with Status(status_text, console=console, spinner="dots") as spinner:
        for _ in range(100):
            if mgr.is_port_responding(probe_host, config.app.port, timeout=0.5):
                started = True
                break
            progress = _read_last_progress(mgr.progress_path)
            if progress:
                step = progress.get("step", "")
                step_status = progress.get("status", "")
                detail = progress.get("detail", "")
                if step == "error":
                    console.print(f"[red]Server failed to start: {detail}[/red]")
                    sys.exit(1)
                if step_status == "running":
                    label = _STEP_LABELS.get(step, step)
                    if detail:
                        label = f"{label} ({detail})"
                    spinner.update(f"{label}...")
            time.sleep(0.1)

    if started:
        console.print(f"[green]Anteroom started at {url} (PID {pid})[/green]")
        if not getattr(args, "no_browser", False):
            webbrowser.open(url)
    else:
        print(f"Anteroom starting at {url} (PID {pid})", file=sys.stderr)
        print("  Server may still be initializing. Check: aroom status", file=sys.stderr)
        print(f"  Logs: {mgr.log_path}", file=sys.stderr)


def _run_stop(config: AppConfig) -> None:
    """Stop the background web UI server."""
    from .services.server_manager import ServerManager

    mgr = ServerManager(data_dir=config.app.data_dir, host=config.app.host, port=config.app.port)
    status = mgr.get_status()

    if status.pid is None:
        print("No server is running (no PID file found).")
        return

    if not status.alive:
        mgr.clear_pid()
        print(f"Cleaned up stale PID file (process {status.pid} was not running).")
        return

    print(f"Stopping Anteroom (PID {status.pid})...")
    stopped = mgr.stop()
    if stopped:
        print("Server stopped.")
    else:
        if not mgr.is_process_alive(status.pid):
            print("Server stopped (exited during shutdown).")
        else:
            print("Could not stop the server.", file=sys.stderr)
            sys.exit(1)


def _run_status(config: AppConfig) -> None:
    """Show web UI server status."""
    from .services.server_manager import ServerManager

    mgr = ServerManager(data_dir=config.app.data_dir, host=config.app.host, port=config.app.port)
    status = mgr.get_status()

    if status.pid is None and not status.responding:
        print("Server is not running.")
        return

    if status.responding:
        scheme = "https" if config.app.tls else "http"
        probe_host = "127.0.0.1" if config.app.host in ("0.0.0.0", "::") else config.app.host
        url = f"{scheme}://{probe_host}:{status.port}"

        uptime_str = ""
        if status.start_time is not None:
            elapsed = time.time() - status.start_time
            if elapsed >= 0:
                hours, remainder = divmod(int(elapsed), 3600)
                minutes, seconds = divmod(remainder, 60)
                if hours > 0:
                    uptime_str = f" (uptime: {hours}h {minutes}m)"
                elif minutes > 0:
                    uptime_str = f" (uptime: {minutes}m {seconds}s)"
                else:
                    uptime_str = f" (uptime: {seconds}s)"

        pid_str = f"PID {status.pid}" if status.pid else "PID unknown"
        print(f"Server is running at {url} ({pid_str}){uptime_str}")
        print(f"  Log: {status.log_path}")
    elif status.alive:
        print(f"Process {status.pid} is running but port {status.port} is not responding.")
        print(f"  Log: {status.log_path}")
    else:
        mgr.clear_pid()
        print(f"Stale PID file found (process {status.pid} is not running). Cleaned up.")


def _resolve_space_id(config: AppConfig, space_name: str) -> str:
    """Resolve a space name to its ID, or exit with an error."""
    from .db import get_db
    from .services.space_storage import resolve_space

    db = get_db(config.app.data_dir / "chat.db")
    match, candidates = resolve_space(db, space_name)
    if match:
        return str(match["id"])
    if candidates:
        print(f"Error: Multiple spaces match '{space_name}':", file=sys.stderr)
        for c in candidates:
            print(f"  {c['id'][:8]}  {c['name']}", file=sys.stderr)
        print("Use a GUID prefix to disambiguate.", file=sys.stderr)
        sys.exit(1)
    print(f"Error: Space '{space_name}' not found.", file=sys.stderr)
    print("Run `aroom space list` to list available spaces.", file=sys.stderr)
    sys.exit(1)


def _run_artifact(config: AppConfig, args: argparse.Namespace) -> None:
    """Handle `aroom artifact` subcommands."""
    from rich.console import Console
    from rich.markup import escape
    from rich.table import Table

    from .db import get_db
    from .services import artifact_storage

    action = getattr(args, "artifact_action", None)
    if not action:
        print("Usage: aroom artifact {list,show,check,import,create}")
        return

    db = get_db(config.app.data_dir / "chat.db")
    console = Console()

    if action == "list":
        try:
            arts = artifact_storage.list_artifacts(
                db,
                artifact_type=getattr(args, "type", None),
                namespace=getattr(args, "namespace", None),
                source=getattr(args, "source", None),
            )
        except ValueError as e:
            console.print(f"[red]Invalid filter: {e}[/red]")
            sys.exit(1)
        if not arts:
            console.print("[dim]No artifacts found.[/dim]")
            return
        table = Table(title="Artifacts")
        table.add_column("FQN", style="bold")
        table.add_column("Type")
        table.add_column("Source")
        table.add_column("Hash", max_width=12)
        table.add_column("Updated")
        for a in arts:
            table.add_row(
                a["fqn"],
                a["type"],
                a["source"],
                a.get("content_hash", "")[:12],
                a.get("updated_at", "")[:10],
            )
        console.print(table)

    elif action == "show":
        fqn = args.fqn
        art = artifact_storage.get_artifact_by_fqn(db, fqn)
        if not art:
            console.print(f"[red]Artifact not found:[/red] {escape(fqn)}")
            sys.exit(1)
        console.print(f"[bold]FQN:[/bold]       {escape(art['fqn'])}")
        console.print(f"[bold]Type:[/bold]      {escape(art['type'])}")
        console.print(f"[bold]Namespace:[/bold] {escape(art['namespace'])}")
        console.print(f"[bold]Name:[/bold]      {escape(art['name'])}")
        console.print(f"[bold]Source:[/bold]    {escape(art['source'])}")
        console.print(f"[bold]Hash:[/bold]      {escape(art['content_hash'])}")
        console.print(f"[bold]Updated:[/bold]   {escape(art['updated_at'])}")
        versions = artifact_storage.list_artifact_versions(db, art["id"])
        console.print(f"[bold]Versions:[/bold]  {len(versions)}")
        console.print()
        console.print("[bold]Content:[/bold]")
        console.print(escape(art["content"]))

    elif action == "check":
        _run_artifact_check(config, args, db, console)

    elif action == "import":
        from pathlib import Path

        from .services.artifact_import import import_all, import_instructions, import_skills

        do_skills = getattr(args, "skills", False)
        do_instructions = getattr(args, "instructions", False)
        do_all = getattr(args, "import_all", False)

        if not (do_skills or do_instructions or do_all):
            console.print("[red]Specify --skills, --instructions, or --all[/red]")
            sys.exit(1)

        if do_all:
            results = import_all(db, config.app.data_dir, project_dir=Path.cwd())
            for category, result in results.items():
                msg = f"{result.imported} imported, {result.skipped} skipped, {result.errors} errors"
                console.print(f"[bold]{category}:[/bold] {msg}")
                for detail in result.details:
                    console.print(f"  {detail}")
        else:
            if do_skills:
                result = import_skills(db, config.app.data_dir / "skills")
                msg = f"{result.imported} imported, {result.skipped} skipped, {result.errors} errors"
                console.print(f"[bold]Skills:[/bold] {msg}")
                for detail in result.details:
                    console.print(f"  {detail}")
            if do_instructions:
                for name in (".anteroom.md", "ANTEROOM.md", "anteroom.md"):
                    path = Path.cwd() / name
                    if path.is_file():
                        result = import_instructions(db, path)
                        console.print(f"[bold]Instructions:[/bold] {result.imported} imported, {result.errors} errors")
                        for detail in result.details:
                            console.print(f"  {detail}")
                        break
                else:
                    console.print("[dim]No ANTEROOM.md found in current directory.[/dim]")

    elif action == "create":
        from pathlib import Path

        from .services.local_artifacts import scaffold_local_artifact

        art_type = args.type
        art_name = args.name
        is_project = getattr(args, "project", False)
        project_dir = Path.cwd() if is_project else None
        try:
            path = scaffold_local_artifact(
                art_type,
                art_name,
                config.app.data_dir,
                project=is_project,
                project_dir=project_dir,
            )
            console.print(f"[green]Created[/green] {escape(str(path))}")
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

    elif action == "delete":
        from .services.artifacts import validate_fqn

        fqn = args.fqn
        if not validate_fqn(fqn):
            console.print(f"[red]Invalid FQN format:[/red] {escape(fqn)}")
            sys.exit(1)
        art = artifact_storage.get_artifact_by_fqn(db, fqn)
        if not art:
            console.print(f"[red]Artifact not found:[/red] {escape(fqn)}")
            sys.exit(1)
        if art.get("source") == "built_in":
            console.print("[red]Cannot delete built-in artifacts.[/red]")
            sys.exit(1)
        artifact_storage.delete_artifact(db, art["id"])
        console.print(f"[green]Deleted[/green] {escape(fqn)}")


def _run_artifact_check(config: AppConfig, args: argparse.Namespace, db: Any, console: Any) -> None:
    """Handle `aroom artifact check` subcommand."""
    import json as json_mod
    from pathlib import Path

    from .services import artifact_health

    project_dir = Path.cwd() if getattr(args, "project", False) else None
    fix = getattr(args, "fix", False)
    json_output = getattr(args, "json_output", False)

    report = artifact_health.run_health_check(db, project_dir=project_dir, fix=fix)

    if json_output:
        print(json_mod.dumps(report.to_dict(), indent=2))
        return

    console.print()
    console.print("[bold]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold]")
    console.print("[bold]  🏥 Artifact Health Check[/bold]")
    console.print("[bold]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold]")
    console.print()
    console.print(f"  📊 Loaded: {report.artifact_count} artifacts from {report.pack_count} packs")
    console.print(f"  📏 Total size: {report.total_size_bytes:,} bytes (~{report.estimated_tokens:,} tokens)")
    console.print()

    if not report.issues:
        console.print("[green]  ✅ No issues found — artifact ecosystem is healthy[/green]")
        console.print()
        return

    categories: dict[str, list] = {}
    for issue in report.issues:
        categories.setdefault(issue.category, []).append(issue)

    category_labels = {
        "config_conflict": ("📋 Config Conflicts", "red"),
        "skill_collision": ("📋 Skill Collisions", "yellow"),
        "shadow": ("📋 Shadows", "dim"),
        "empty_artifact": ("📋 Quality", "yellow"),
        "malformed": ("📋 Malformed", "red"),
        "lock_drift": ("📋 Lock Drift", "yellow"),
        "orphaned": ("📋 Orphaned Artifacts", "yellow"),
        "duplicate_content": ("📋 Duplicates", "yellow"),
        "bloat": ("📋 Bloat Report", "dim"),
        "fix_applied": ("📋 Fixes Applied", "green"),
    }

    for cat, issues in categories.items():
        label, color = category_labels.get(cat, (f"📋 {cat}", "white"))
        console.print(f"[bold]{label}[/bold]")
        for issue in issues:
            icon = {"error": "❌", "warn": "⚠️", "info": "💡"}.get(issue.severity.value, "•")
            console.print(f"  {icon} {issue.message}")
        console.print()

    console.print("[bold]────────────────────────────────────────────[/bold]")
    parts = []
    if report.error_count:
        parts.append(f"[red]❌ {report.error_count} errors[/red]")
    if report.warn_count:
        parts.append(f"[yellow]⚠️ {report.warn_count} warnings[/yellow]")
    if report.info_count:
        parts.append(f"[dim]💡 {report.info_count} suggestions[/dim]")
    console.print(f"  {' '.join(parts)}")

    if report.error_count:
        console.print("\n  [bold]👉 Fix errors first[/bold]")
    console.print("[bold]────────────────────────────────────────────[/bold]")
    console.print()


def _validate_pack_ref(ref: str) -> tuple[str, str]:
    """Parse and validate a pack reference (namespace/name).

    Returns (namespace, name) or raises SystemExit on invalid input.
    """
    import re

    parts = ref.split("/", 1)
    if len(parts) == 1:
        namespace, name = "default", parts[0]
    else:
        namespace, name = parts
    safe_re = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
    if not safe_re.match(namespace):
        print(f"Invalid namespace: {namespace!r}. Must match [a-zA-Z0-9][a-zA-Z0-9._-]{{0,63}}", file=sys.stderr)
        sys.exit(1)
    if not safe_re.match(name):
        print(f"Invalid pack name: {name!r}. Must match [a-zA-Z0-9][a-zA-Z0-9._-]{{0,63}}", file=sys.stderr)
        sys.exit(1)
    return namespace, name


def _pick_from_candidates(
    candidates: list[dict],
    entity_type: str,
    display_fn: object,
) -> dict | None:
    """Interactive picker for ambiguous name resolution.

    Shows a numbered list when on a TTY, or prints IDs for non-interactive use.
    """
    if not sys.stdin.isatty():
        print(f"Error: Multiple {entity_type}s match. Use a GUID to disambiguate:", file=sys.stderr)
        for c in candidates:
            print(f"  {c.get('id', '?')}", file=sys.stderr)
        return None

    print(f"Multiple {entity_type}s match:")
    for i, c in enumerate(candidates, 1):
        label = display_fn(c) if callable(display_fn) else str(c)
        print(f"  {i}. {label}")
    try:
        choice = input(f"Select (1-{len(candidates)}): ").strip()
        idx = int(choice) - 1
        if 0 <= idx < len(candidates):
            return candidates[idx]
    except (ValueError, EOFError, KeyboardInterrupt):
        pass
    print("Cancelled.", file=sys.stderr)
    return None


def _run_pack_install(
    data_dir: Path,
    db: "ThreadSafeConnection",
    args: argparse.Namespace,
    console: "Console",
) -> None:
    """Install a pack from a local path or a git URL.

    When *source* is a git URL (``https://``, ``ssh://``, ``git@host:path``),
    the repo is shallow-cloned into the pack source cache and ``pack.yaml``
    manifests are discovered automatically.  If ``--path`` is given it
    narrows discovery to that subdirectory.  When ``--attach`` is given
    the pack is attached to global scope after installation.
    """
    from .services.pack_sources import is_git_url

    source: str = args.source

    if is_git_url(source):
        _install_from_url(data_dir, db, args, console, source)
    else:
        _install_from_path(db, args, console, Path(source).resolve())


def _install_from_url(
    data_dir: Path,
    db: "ThreadSafeConnection",
    args: argparse.Namespace,
    console: "Console",
    url: str,
) -> None:
    """Clone a git repo and install all discovered packs."""
    from rich.markup import escape

    from .services import packs
    from .services.pack_sources import check_git_available, clone_source

    if not check_git_available():
        console.print("[red]git is not installed or not on PATH.[/red]")
        sys.exit(1)

    branch = getattr(args, "branch", "main") or "main"
    console.print(f"Cloning [bold]{escape(url)}[/bold] (branch: {escape(branch)})...")

    result = clone_source(url, branch, data_dir)
    if not result.success:
        console.print(f"[red]Clone failed:[/red] {escape(result.error)}")
        sys.exit(1)

    cache_path = result.path
    if cache_path is None:
        console.print("[red]Clone succeeded but no path returned.[/red]")
        sys.exit(1)

    # Discover pack.yaml manifests in the cloned repo
    subpath = getattr(args, "subpath", None)
    search_root = cache_path / subpath if subpath else cache_path
    # Path traversal prevention: subpath must not escape the cache directory
    try:
        resolved_root = search_root.resolve()
        if not resolved_root.is_relative_to(cache_path.resolve()):
            console.print("[red]Subdirectory must not escape the cloned repository.[/red]")
            sys.exit(1)
    except (OSError, ValueError):
        console.print("[red]Invalid subdirectory path.[/red]")
        sys.exit(1)
    if not search_root.is_dir():
        console.print(f"[red]Subdirectory not found:[/red] {escape(subpath or '')}")
        sys.exit(1)

    manifests = sorted(
        p
        for p in search_root.rglob("pack.yaml")
        if ".git" not in p.parts and p.resolve().is_relative_to(search_root.resolve())
    )
    if not manifests:
        console.print(f"[red]No pack.yaml found in {escape(str(search_root))}[/red]")
        sys.exit(1)

    installed_packs: list[dict[str, Any]] = []
    project_dir = Path.cwd() if getattr(args, "project", False) else None

    for manifest_path in manifests:
        pack_dir = manifest_path.parent
        try:
            manifest = packs.parse_manifest(manifest_path)
        except ValueError as e:
            console.print(f"[yellow]Skipping {escape(str(manifest_path))}:[/yellow] {e}")
            continue

        errors = packs.validate_manifest(manifest, pack_dir)
        if errors:
            console.print(f"[yellow]Skipping {escape(manifest.namespace)}/{escape(manifest.name)}:[/yellow]")
            for err in errors:
                console.print(f"  - {err}")
            continue

        try:
            install_result: dict[str, Any] = packs.install_pack(
                db,
                manifest,
                pack_dir,
                project_dir=project_dir,
            )
        except ValueError as e:
            console.print(f"[yellow]Skipping {escape(manifest.namespace)}/{escape(manifest.name)}:[/yellow] {e}")
            continue

        console.print(
            f"[green]Installed[/green] {escape(install_result['namespace'])}/{escape(install_result['name'])} "
            f"v{escape(install_result['version'])} ({install_result['artifact_count']} artifacts)"
        )
        installed_packs.append(install_result)

    if not installed_packs:
        console.print("[red]No packs were installed.[/red]")
        sys.exit(1)

    # Auto-attach if requested
    if getattr(args, "attach", False):
        from .services.pack_attachments import attach_pack

        priority = getattr(args, "priority", 50) or 50
        for p in installed_packs:
            try:
                attach_pack(db, p["id"], priority=priority)
                pri_note = f", priority {priority}" if priority != 50 else ""
                console.print(
                    f"[green]Attached[/green] {escape(p['namespace'])}/{escape(p['name'])} (global{pri_note})"
                )
            except ValueError as e:
                console.print(f"[yellow]Attach warning:[/yellow] {e}")


def _install_from_path(
    db: "ThreadSafeConnection",
    args: argparse.Namespace,
    console: "Console",
    pack_path: Path,
) -> None:
    """Install a pack from a local directory."""
    from rich.markup import escape

    from .services import packs

    manifest_path = pack_path / "pack.yaml"
    try:
        manifest = packs.parse_manifest(manifest_path)
    except ValueError as e:
        console.print(f"[red]Invalid manifest:[/red] {e}")
        sys.exit(1)

    errors = packs.validate_manifest(manifest, pack_path)
    if errors:
        console.print("[red]Manifest validation errors:[/red]")
        for err in errors:
            console.print(f"  - {err}")
        sys.exit(1)

    project_dir = Path.cwd() if getattr(args, "project", False) else None
    try:
        install_result: dict[str, Any] = packs.install_pack(db, manifest, pack_path, project_dir=project_dir)
    except ValueError as e:
        console.print(f"[red]Install failed:[/red] {e}")
        sys.exit(1)

    action_word = "Updated" if install_result.get("action") == "updated" else "Installed"
    console.print(
        f"[green]{action_word}[/green] {escape(install_result['namespace'])}/{escape(install_result['name'])} "
        f"v{escape(install_result['version'])} ({install_result['artifact_count']} artifacts)"
    )

    # Auto-attach if requested
    if getattr(args, "attach", False):
        from .services.pack_attachments import attach_pack

        priority = getattr(args, "priority", 50) or 50
        try:
            attach_pack(db, install_result["id"], priority=priority)
            ns = escape(install_result["namespace"])
            nm = escape(install_result["name"])
            pri_note = f", priority {priority}" if priority != 50 else ""
            console.print(f"[green]Attached[/green] {ns}/{nm} (global{pri_note})")
        except ValueError as e:
            console.print(f"[yellow]Attach warning:[/yellow] {e}")


def _run_pack_dispatch(args: argparse.Namespace) -> None:
    """Route ``aroom pack`` subcommands without requiring a full config.

    This wrapper exists so that ``aroom pack install <url>`` works even
    before ``aroom init`` has been run.  It calls
    :func:`_ensure_db_for_pack_ops` (which only needs ``~/.anteroom/``)
    instead of :func:`_load_config_or_exit` (which triggers the init
    wizard).
    """
    action = getattr(args, "pack_action", None)
    if not action:
        print("Usage: aroom pack {list,install,show,remove,update,sources,refresh,attach,detach,add-source}")
        return

    # add-source doesn't need a DB — it writes config.yaml directly
    if action == "add-source":
        from rich.console import Console
        from rich.markup import escape

        from .services.pack_sources import add_pack_source

        url = args.url.strip()
        result = add_pack_source(url)
        console = Console()
        if not result.ok:
            console.print(f"[red]{escape(result.message)}[/red]")
            sys.exit(1)
        if result.message:
            console.print(f"[yellow]{escape(result.message)}[/yellow]")
            return
        console.print(f"[green]Added pack source:[/green] {escape(url)}")
        console.print("Run [bold]aroom pack refresh[/bold] to clone and install packs.")
        return

    # sources/refresh need full config for pack_sources list
    if action in ("sources", "refresh"):
        _team_config_arg = getattr(args, "team_config", None)
        _team_config_path = Path(_team_config_arg) if _team_config_arg else None
        _config_path, config, _enforced = _load_config_or_exit(team_config_path=_team_config_path)
        _run_pack_with_config(config, args)
        return

    data_dir, db = _ensure_db_for_pack_ops()
    _run_pack(data_dir, db, args)


def _run_pack_with_config(config: AppConfig, args: argparse.Namespace) -> None:
    """Handle pack subcommands that require the full config (sources, refresh)."""
    from rich.console import Console
    from rich.markup import escape
    from rich.table import Table

    from .db import get_db

    db = get_db(config.app.data_dir / "chat.db")
    console = Console()
    action = getattr(args, "pack_action", None)

    if action == "sources":
        from .services.pack_sources import list_cached_sources

        sources = getattr(config, "pack_sources", [])
        if not sources:
            console.print("[dim]No pack sources configured in config.yaml.[/dim]")
            return

        table = Table(title="Pack Sources")
        table.add_column("URL", style="bold")
        table.add_column("Branch")
        table.add_column("Refresh")
        table.add_column("Cached", justify="center")
        table.add_column("Ref", max_width=12)

        cached = list_cached_sources(config.app.data_dir)
        cached_urls = {c.url: c for c in cached}

        for src in sources:
            cached_src = cached_urls.get(src.url)
            is_cached = cached_src is not None
            ref = cached_src.ref[:12] if cached_src and cached_src.ref else "-"
            interval = f"{src.refresh_interval}m" if src.refresh_interval > 0 else "manual"
            table.add_row(
                src.url,
                src.branch,
                interval,
                "[green]yes[/green]" if is_cached else "[dim]no[/dim]",
                ref,
            )
        console.print(table)

    elif action == "refresh":
        from .services.pack_refresh import PackRefreshWorker

        sources = getattr(config, "pack_sources", [])
        if not sources:
            console.print("[dim]No pack sources configured in config.yaml.[/dim]")
            return

        worker = PackRefreshWorker(db=db, data_dir=config.app.data_dir, sources=sources)
        results = worker.refresh_all()
        for r in results:
            if r.success:
                parts = []
                if r.packs_installed:
                    parts.append(f"{r.packs_installed} installed")
                if r.packs_updated:
                    parts.append(f"{r.packs_updated} updated")
                status = ", ".join(parts) if parts else "up to date"
                console.print(f"[green]OK[/green] {escape(r.url)} — {status}")
            else:
                console.print(f"[red]FAIL[/red] {escape(r.url)} — {escape(r.error)}")


def _run_pack(data_dir: Path, db: "ThreadSafeConnection", args: argparse.Namespace) -> None:
    """Handle ``aroom pack`` subcommands that only need a database."""
    from rich.console import Console
    from rich.markup import escape
    from rich.table import Table

    from .services import packs

    action = getattr(args, "pack_action", None)
    console = Console()

    if action == "list":
        pack_list = packs.list_packs(db)
        if not pack_list:
            console.print("[dim]No packs installed.[/dim]")
            return

        from .services.pack_attachments import list_attachments_for_pack

        table = Table(title="Installed Packs")
        table.add_column("Namespace", style="bold")
        table.add_column("Name", style="bold")
        table.add_column("Version")
        table.add_column("Artifacts", justify="right")
        table.add_column("Attached", style="green")
        table.add_column("Installed")
        for p in pack_list:
            attachments = list_attachments_for_pack(db, p["id"])
            if attachments:
                att_parts = []
                for att in attachments:
                    scope = att.get("scope", "global")
                    pri = att.get("priority", 50)
                    pri_note = f" p{pri}" if pri != 50 else ""
                    att_parts.append(f"{scope}{pri_note}")
                att_str = ", ".join(att_parts)
            else:
                att_str = "[dim]no[/dim]"
            table.add_row(
                p["namespace"],
                p["name"],
                p["version"],
                str(p.get("artifact_count", 0)),
                att_str,
                p.get("installed_at", "")[:10],
            )
        console.print(table)

    elif action == "install":
        _run_pack_install(data_dir, db, args, console)

    elif action == "show":
        namespace, name = _validate_pack_ref(args.ref)
        match, candidates = packs.resolve_pack(db, namespace, name)
        if not match and candidates:
            match = _pick_from_candidates(
                candidates,
                "pack",
                lambda c: f"{c['id'][:8]}  {c.get('namespace', '')}/{c.get('name', '')} v{c.get('version', '')}",
            )
        if not match:
            console.print(f"[red]Pack not found:[/red] {escape(args.ref)}")
            sys.exit(1)

        pack_info = packs.get_pack(db, match["namespace"], match["name"])
        if not pack_info:
            console.print(f"[red]Pack not found:[/red] {escape(args.ref)}")
            sys.exit(1)

        console.print(f"[bold]Name:[/bold]        {escape(pack_info['namespace'])}/{escape(pack_info['name'])}")
        console.print(f"[bold]Version:[/bold]     {escape(pack_info['version'])}")
        console.print(f"[bold]Description:[/bold] {escape(pack_info.get('description', ''))}")
        console.print(f"[bold]Source:[/bold]      {escape(pack_info.get('source_path', ''))}")
        console.print(f"[bold]Installed:[/bold]   {escape(pack_info.get('installed_at', ''))}")
        console.print(f"[bold]Artifacts:[/bold]   {pack_info.get('artifact_count', 0)}")

        artifacts = pack_info.get("artifacts", [])
        if artifacts:
            console.print()
            table = Table(title="Artifacts")
            table.add_column("FQN", style="bold")
            table.add_column("Type")
            table.add_column("Hash", max_width=12)
            for a in artifacts:
                table.add_row(a["fqn"], a["type"], a.get("content_hash", "")[:12])
            console.print(table)

    elif action == "remove":
        namespace, name = _validate_pack_ref(args.ref)
        match, candidates = packs.resolve_pack(db, namespace, name)
        if not match and candidates:
            match = _pick_from_candidates(
                candidates,
                "pack",
                lambda c: f"{c['id'][:8]}  {c.get('namespace', '')}/{c.get('name', '')} v{c.get('version', '')}",
            )
        if not match:
            console.print(f"[red]Pack not found:[/red] {escape(args.ref)}")
            sys.exit(1)
        removed = packs.remove_pack_by_id(db, match["id"])
        if removed:
            console.print(f"[green]Removed[/green] {escape(args.ref)}")
        else:
            console.print(f"[red]Pack not found:[/red] {escape(args.ref)}")
            sys.exit(1)

    elif action == "update":
        pack_path = Path(args.path).resolve()
        manifest_path = pack_path / "pack.yaml"
        try:
            manifest = packs.parse_manifest(manifest_path)
        except ValueError as e:
            console.print(f"[red]Invalid manifest:[/red] {e}")
            sys.exit(1)

        errors = packs.validate_manifest(manifest, pack_path)
        if errors:
            console.print("[red]Manifest validation errors:[/red]")
            for err in errors:
                console.print(f"  - {err}")
            sys.exit(1)

        project_dir = Path.cwd() if getattr(args, "project", False) else None
        try:
            update_result: dict[str, Any] = packs.update_pack(db, manifest, pack_path, project_dir=project_dir)
        except ValueError as e:
            console.print(f"[red]Update failed:[/red] {e}")
            sys.exit(1)

        console.print(
            f"[green]Updated[/green] {escape(update_result['namespace'])}/{escape(update_result['name'])} "
            f"v{escape(update_result['version'])} ({update_result['artifact_count']} artifacts)"
        )

    elif action == "attach":
        from .services.pack_attachments import attach_pack

        namespace, name = _validate_pack_ref(args.ref)
        match, candidates = packs.resolve_pack(db, namespace, name)
        if not match and candidates:
            match = _pick_from_candidates(
                candidates,
                "pack",
                lambda c: f"{c['id'][:8]}  {c.get('namespace', '')}/{c.get('name', '')} v{c.get('version', '')}",
            )
        if not match:
            console.print(f"[red]Pack not found:[/red] {escape(args.ref)}")
            sys.exit(1)

        project_path = str(Path.cwd()) if getattr(args, "project", False) else None
        priority = getattr(args, "priority", 50) or 50
        try:
            attach_pack(db, match["id"], project_path=project_path, priority=priority)
        except ValueError as e:
            console.print(f"[red]Attach failed:[/red] {e}")
            sys.exit(1)

        scope = "project" if project_path else "global"
        pri_note = f", priority {priority}" if priority != 50 else ""
        console.print(f"[green]Attached[/green] {escape(args.ref)} ({scope}{pri_note})")

    elif action == "detach":
        from .services.pack_attachments import detach_pack

        namespace, name = _validate_pack_ref(args.ref)
        match, candidates = packs.resolve_pack(db, namespace, name)
        if not match and candidates:
            match = _pick_from_candidates(
                candidates,
                "pack",
                lambda c: f"{c['id'][:8]}  {c.get('namespace', '')}/{c.get('name', '')} v{c.get('version', '')}",
            )
        if not match:
            console.print(f"[red]Pack not found:[/red] {escape(args.ref)}")
            sys.exit(1)

        project_path = str(Path.cwd()) if getattr(args, "project", False) else None
        removed = detach_pack(db, match["id"], project_path=project_path)
        if removed:
            scope = "project" if project_path else "global"
            console.print(f"[green]Detached[/green] {escape(args.ref)} ({scope})")
        else:
            console.print(f"[yellow]Not attached:[/yellow] {escape(args.ref)}")


def _run_space(config: AppConfig, args: argparse.Namespace) -> None:
    """Handle `aroom space` subcommands."""
    from pathlib import Path

    from rich.console import Console
    from rich.markup import escape
    from rich.table import Table

    from .db import get_db
    from .services.space_storage import (
        count_space_conversations,
        create_space,
        delete_space,
        get_space_by_name,
        list_spaces,
        resolve_space,
        sync_space_paths,
    )
    from .services.spaces import (
        compute_file_hash,
        is_local_space,
        parse_space_file,
        slugify_dir_name,
        validate_space,
        write_space_template,
    )

    console = Console()
    action = getattr(args, "space_action", None)
    if not action:
        console.print("Usage: aroom space {list,create,init,load,show,delete,refresh,clone,map,move-root}")
        return

    db = get_db(config.app.data_dir / "chat.db")

    def _resolve(name_or_id: str) -> dict | None:
        match, candidates = resolve_space(db, name_or_id)
        if match:
            return match
        if candidates:
            return _pick_from_candidates(
                candidates,
                "space",
                lambda c: f"{c['id'][:8]}  {c['name']}  ({c.get('source_file', '')})",
            )
        console.print(f"[red]Error:[/red] Space {escape(name_or_id)!r} not found")
        return None

    if action == "list":
        spaces = list_spaces(db)
        if not spaces:
            console.print("[dim]No spaces found. Create one with:[/dim] aroom space create <name>")
            console.print("[dim]  or from inside a project:[/dim] aroom space init")
            return
        table = Table(title="Spaces")
        table.add_column("Name", style="bold")
        table.add_column("Origin")
        table.add_column("Conversations", justify="right")
        table.add_column("Last Loaded")
        for s in spaces:
            sf = s.get("source_file", "")
            origin = "local" if (sf and is_local_space(sf)) else "global"
            count = count_space_conversations(db, s["id"])
            table.add_row(
                s["name"],
                origin,
                str(count),
                s.get("last_loaded_at", ""),
            )
        console.print(table)

    elif action in ("create", "init"):
        import re as _re

        cwd = Path.cwd()

        if action == "init":
            name = slugify_dir_name(cwd.name)
            if not name:
                console.print(
                    "[red]Error:[/red] Cannot derive a space name from the current directory. "
                    "Use [bold]aroom space create <name>[/bold] instead."
                )
                return
        else:
            name = args.name

        if not _re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$", name):
            console.print(
                f"[red]Error:[/red] Invalid space name: {escape(name)!r} (must be alphanumeric, hyphens, underscores)"
            )
            return

        # Default: create local space in cwd/.anteroom/space.yaml
        target = cwd / ".anteroom" / "space.yaml"
        if target.exists():
            console.print(f"[yellow]Space file already exists:[/yellow] {target}")
            console.print("  Use [bold]aroom space load[/bold] to register it, or edit it directly.")
            return

        existing = get_space_by_name(db, name)
        if existing:
            console.print(f"[yellow]Space '{escape(name)}' already exists.[/yellow]")
            console.print(f"  Use [bold]aroom space show {escape(name)}[/bold] to view it.")
            return

        write_space_template(target, name)
        s = create_space(db, name, source_file=str(target), source_hash=compute_file_hash(target))
        # Map cwd so resolve_space_by_cwd() finds this space
        sync_space_paths(db, s["id"], [{"local_path": str(cwd)}])
        console.print(f"[green]Created local space:[/green] {escape(s['name'])}")
        console.print(f"  File: {target}")
        console.print()
        console.print("  This space will activate automatically when you run [bold]aroom chat[/bold]")
        console.print("  from this directory.  Edit the YAML to add instructions, packs, and config.")

    elif action == "load":
        path = Path(args.path).expanduser().resolve()
        if not path.is_file():
            console.print(f"[red]Error:[/red] File not found: {path}")
            return
        space_cfg = parse_space_file(path)
        errors = validate_space(space_cfg)
        if errors:
            console.print("[red]Validation errors:[/red]")
            for e in errors:
                console.print(f"  - {e}")
            return
        existing = get_space_by_name(db, space_cfg.name)
        if existing:
            console.print(f"[yellow]Space '{escape(space_cfg.name)}' already exists.[/yellow]")
            console.print(f"  Use [bold]aroom space show {escape(space_cfg.name)}[/bold] to view it.")
            return
        s = create_space(
            db,
            space_cfg.name,
            source_file=str(path),
            source_hash=compute_file_hash(path),
            instructions=space_cfg.instructions or "",
            model=space_cfg.config.get("model"),
        )
        console.print(f"[green]Loaded space:[/green] {escape(s['name'])} (id: {s['id'][:8]}...)")

    elif action == "show":
        space = _resolve(args.name)
        if not space:
            return
        console.print(f"[bold]{escape(space['name'])}[/bold]")
        console.print(f"  ID:          {space['id']}")
        _sf = space.get("source_file", "")
        console.print(f"  Source:      {_sf or '(DB-only)'}")
        _sh = space.get("source_hash", "")
        if _sh:
            console.print(f"  Hash:        {_sh[:16]}...")
        if space.get("instructions"):
            _ip = space["instructions"][:80].replace("\n", " ")
            console.print(f"  Instructions: {_ip}...")
        if space.get("model"):
            console.print(f"  Model:       {space['model']}")
        console.print(f"  Last loaded: {space['last_loaded_at']}")
        console.print(f"  Created:     {space['created_at']}")

    elif action == "delete":
        space = _resolve(args.name)
        if not space:
            return
        delete_space(db, space["id"])
        console.print(f"[green]Deleted space:[/green] {escape(args.name)}")

    elif action == "refresh":
        space = _resolve(args.name)
        if not space:
            return
        _sf = space.get("source_file", "")
        if not _sf:
            console.print("[red]Error:[/red] Space has no source file to refresh from.")
            return
        path = Path(_sf)
        if not path.is_file():
            console.print(f"[red]Error:[/red] Space source file not found: {path}")
            return

        from .services.spaces import sync_space_from_file

        try:
            updated = sync_space_from_file(db, path)
            console.print(f"[green]Refreshed space:[/green] {escape(updated['name'])}")
        except ValueError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            return

    elif action == "clone":
        space = _resolve(args.name)
        if not space:
            return
        _sf = space.get("source_file", "")
        if not _sf:
            console.print("[red]Error:[/red] Space has no source file to clone from.")
            return
        path = Path(_sf)
        if not path.is_file():
            console.print(f"[red]Error:[/red] Space file not found: {path}")
            return
        space_cfg = parse_space_file(path)
        if not space_cfg.repos:
            console.print("[dim]No repos defined in this space.[/dim]")
            return
        from .services.space_bootstrap import clone_repos
        from .services.spaces import SpaceLocalConfig, parse_local_file, resolve_local_path, write_local_file

        # Resolve repos root: local config > prompt > default
        local_path = resolve_local_path(path)
        local_cfg = parse_local_file(local_path) if local_path else SpaceLocalConfig()

        if local_cfg.repos_root:
            repos_root = Path(local_cfg.repos_root)
        else:
            default_root = Path.home() / ".anteroom" / "spaces" / space_cfg.name / "repos"
            if sys.stdin.isatty():
                console.print("[dim]Repos root directory[/dim]")
                try:
                    user_input = input(f"  [{default_root}]: ").strip()
                except (EOFError, KeyboardInterrupt):
                    user_input = ""
                repos_root = Path(user_input) if user_input else default_root
            else:
                repos_root = default_root
            # Save to local config
            local_cfg.repos_root = str(repos_root)
            local_file = path.with_suffix("").with_suffix(".local.yaml")
            write_local_file(local_file, local_cfg)
            console.print(f"  [dim]Saved repos root to {local_file}[/dim]")

        results = clone_repos(space_cfg.repos, repos_root)
        for r in results:
            if r.success:
                console.print(f"  [green]OK[/green]   {r.url} -> {r.local_path}")
            else:
                console.print(f"  [red]FAIL[/red] {r.url}: {r.error}")

        # Sync paths to DB
        from .services.space_storage import sync_space_paths

        new_paths = [
            {"repo_url": r.url, "local_path": str(r.local_path)} for r in results if r.success and r.local_path
        ]
        if new_paths:
            sync_space_paths(db, space["id"], new_paths)

    elif action == "map":
        space = _resolve(args.name)
        if not space:
            return
        dir_path = Path(args.dir_path).expanduser().resolve()
        if not dir_path.is_dir():
            console.print(f"[red]Error:[/red] Directory not found: {dir_path}")
            return
        from .services.space_storage import get_space_paths, sync_space_paths

        existing_paths = get_space_paths(db, space["id"])
        new_entry = {"repo_url": "", "local_path": str(dir_path)}
        all_paths = [{"repo_url": p.get("repo_url", ""), "local_path": p["local_path"]} for p in existing_paths]
        # Check for duplicates
        if str(dir_path) in {p["local_path"] for p in all_paths}:
            console.print(f"[dim]Path already mapped: {dir_path}[/dim]")
            return
        all_paths.append(new_entry)
        sync_space_paths(db, space["id"], all_paths)
        console.print(f"[green]Mapped:[/green] {dir_path} -> {escape(args.name)}")

    elif action == "move-root":
        space = _resolve(args.name)
        if not space:
            return
        new_root = Path(args.new_root).expanduser().resolve()
        if not new_root.is_dir():
            console.print(f"[red]Error:[/red] Directory not found: {new_root}")
            return
        from .services.spaces import SpaceLocalConfig, parse_local_file, resolve_local_path, write_local_file

        _sf = space.get("source_file", "")
        if not _sf:
            console.print("[red]Error:[/red] Space has no source file.")
            return
        path = Path(_sf)
        local_path = resolve_local_path(path)
        local_cfg = parse_local_file(local_path) if local_path else SpaceLocalConfig()
        local_cfg.repos_root = str(new_root)
        local_file = path.with_suffix("").with_suffix(".local.yaml")
        write_local_file(local_file, local_cfg)
        console.print(f"[green]Repos root updated:[/green] {new_root}")


def _run_chat(
    config: AppConfig,
    prompt: str | None = None,
    no_tools: bool = False,
    continue_last: bool = False,
    resume_id: str | None = None,
    project_path: str | None = None,
    model: str | None = None,
    trust_project: bool = False,
    no_project_context: bool = False,
    plan_mode: bool = False,
    space_id: str | None = None,
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
                trust_project=trust_project,
                no_project_context=no_project_context,
                plan_mode=plan_mode,
                space_id=space_id,
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
    config: AppConfig,
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
    space_id: str | None = None,
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
                space_id=space_id,
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
    view_parser = config_subparsers.add_parser("view", help="Display current configuration")
    view_parser.add_argument("--with-sources", action="store_true", help="Show which layer set each value")

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
        help="Comma-separated pre-allowed tools (e.g., bash,write_file). Skips approval gate",
    )
    parser.add_argument(
        "--denied-tools",
        dest="denied_tools",
        default=None,
        help="Comma-separated hard-blocked tools (e.g., bash,run_agent). Blocked without prompt",
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
        "--space",
        dest="space_name",
        default=None,
        help="Load a named space (workspace with repos, packs, sources, config)",
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
    parser.add_argument(
        "--_bg-worker",
        dest="bg_worker",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )

    artifact_parser = subparsers.add_parser("artifact", help="Manage artifacts")
    artifact_subparsers = artifact_parser.add_subparsers(dest="artifact_action")
    art_list_parser = artifact_subparsers.add_parser("list", help="List all artifacts")
    _art_types = ["skill", "rule", "instruction", "context", "memory", "mcp_server", "config_overlay"]
    art_list_parser.add_argument("--type", choices=_art_types)
    art_list_parser.add_argument("--namespace")
    art_list_parser.add_argument("--source", choices=["built_in", "global", "team", "project", "local", "inline"])
    art_show_parser = artifact_subparsers.add_parser("show", help="Show artifact details by FQN")
    art_show_parser.add_argument("fqn", help="Fully-qualified name, e.g. @core/skill/greet")
    art_check_parser = artifact_subparsers.add_parser("check", help="Run artifact health check")
    art_check_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON",
    )
    art_check_parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-fix issues (removes exact duplicates)",
    )
    art_check_parser.add_argument(
        "--project",
        action="store_true",
        help="Include lock file validation for current directory",
    )

    pack_parser = subparsers.add_parser("pack", help="Manage artifact packs")
    pack_subparsers = pack_parser.add_subparsers(dest="pack_action")
    pack_subparsers.add_parser("list", help="List installed packs")
    pack_install_parser = pack_subparsers.add_parser("install", help="Install a pack from a local path or git URL")
    pack_install_parser.add_argument(
        "source",
        help="Path to pack directory or git URL (https://, ssh://, git@host:path)",
    )
    pack_install_parser.add_argument(
        "--project",
        action="store_true",
        help="Copy pack into .anteroom/packs/",
    )
    pack_install_parser.add_argument(
        "--attach",
        action="store_true",
        help="Attach the pack to global scope after install",
    )
    pack_install_parser.add_argument(
        "--branch",
        default="main",
        help="Git branch to clone (default: main)",
    )
    pack_install_parser.add_argument(
        "--path",
        dest="subpath",
        default=None,
        help="Subdirectory within the repo containing pack.yaml",
    )
    pack_install_parser.add_argument(
        "--priority",
        type=int,
        default=50,
        help="Precedence for --attach when packs conflict (1=highest, 100=lowest). Default: 50.",
    )
    pack_show_parser = pack_subparsers.add_parser("show", help="Show pack details")
    pack_show_parser.add_argument("ref", help="Pack reference as namespace/name")
    pack_remove_parser = pack_subparsers.add_parser("remove", help="Remove an installed pack")
    pack_remove_parser.add_argument("ref", help="Pack reference as namespace/name")
    pack_update_parser = pack_subparsers.add_parser("update", help="Update a pack from a local directory")
    pack_update_parser.add_argument("path", help="Path to pack directory containing pack.yaml")
    pack_update_parser.add_argument(
        "--project",
        action="store_true",
        help="Copy pack into .anteroom/packs/",
    )
    pack_subparsers.add_parser("sources", help="List configured pack sources and cache status")
    pack_subparsers.add_parser("refresh", help="Pull all configured pack sources and update packs")
    pack_attach_parser = pack_subparsers.add_parser("attach", help="Attach a pack to global or project scope")
    pack_attach_parser.add_argument("ref", help="Pack reference as namespace/name")
    pack_attach_parser.add_argument("--project", action="store_true", help="Attach to current project only")
    pack_attach_parser.add_argument(
        "--priority",
        type=int,
        default=50,
        help="Precedence when packs conflict (1=highest, 100=lowest). Default: 50.",
    )
    pack_detach_parser = pack_subparsers.add_parser("detach", help="Detach a pack from global or project scope")
    pack_detach_parser.add_argument("ref", help="Pack reference as namespace/name")
    pack_detach_parser.add_argument("--project", action="store_true", help="Detach from current project only")
    pack_add_source_parser = pack_subparsers.add_parser("add-source", help="Add a git pack source to config")
    pack_add_source_parser.add_argument("url", help="Git repository URL (https:// or ssh://)")

    # `aroom space` subcommand
    space_parser = subparsers.add_parser("space", help="Manage spaces")
    space_subparsers = space_parser.add_subparsers(dest="space_action")
    space_subparsers.add_parser("list", help="List all spaces")
    space_create_parser = space_subparsers.add_parser("create", help="Create a new global space")
    space_create_parser.add_argument("name", help="Space name")
    space_subparsers.add_parser("init", help="Create a local space, deriving the name from the directory")
    space_load_parser = space_subparsers.add_parser("load", help="Load an existing space YAML file")
    space_load_parser.add_argument("path", help="Path to space YAML file")
    space_show_parser = space_subparsers.add_parser("show", help="Show space details")
    space_show_parser.add_argument("name", help="Space name")
    space_delete_parser = space_subparsers.add_parser("delete", help="Delete a space")
    space_delete_parser.add_argument("name", help="Space name")
    space_refresh_parser = space_subparsers.add_parser("refresh", help="Refresh a space from its YAML file")
    space_refresh_parser.add_argument("name", help="Space name")
    space_clone_parser = space_subparsers.add_parser("clone", help="Clone repos for a space")
    space_clone_parser.add_argument("name", help="Space name")
    space_map_parser = space_subparsers.add_parser("map", help="Map a local directory to a space")
    space_map_parser.add_argument("name", help="Space name")
    space_map_parser.add_argument("dir_path", help="Directory path to map")
    space_move_root_parser = space_subparsers.add_parser("move-root", help="Change repos root for a space")
    space_move_root_parser.add_argument("name", help="Space name")
    space_move_root_parser.add_argument("new_root", help="New repos root directory")

    # `aroom start` subcommand
    start_parser = subparsers.add_parser("start", help="Start the web UI server in the background")
    start_parser.add_argument("--no-browser", action="store_true", help="Do not open browser on start")

    # `aroom stop` subcommand
    subparsers.add_parser("stop", help="Stop the background web UI server")

    # `aroom status` subcommand
    subparsers.add_parser("status", help="Show web UI server status")

    # `aroom artifact import` subcommand
    art_import_parser = artifact_subparsers.add_parser("import", help="Import skills/instructions into artifacts")
    art_import_parser.add_argument("--skills", action="store_true", help="Import skills from ~/.anteroom/skills/")
    art_import_parser.add_argument("--instructions", action="store_true", help="Import ANTEROOM.md as artifacts")
    art_import_parser.add_argument("--all", action="store_true", dest="import_all", help="Import everything")

    # `aroom artifact create` subcommand
    art_create_parser = artifact_subparsers.add_parser("create", help="Create a new local artifact from template")
    art_create_parser.add_argument("type", choices=_art_types, help="Artifact type")
    art_create_parser.add_argument("name", help="Artifact name")
    art_create_parser.add_argument("--project", action="store_true", help="Create in project .anteroom/local/")

    art_delete_parser = artifact_subparsers.add_parser("delete", help="Delete an artifact by FQN")
    art_delete_parser.add_argument("fqn", help="Artifact FQN (e.g. @namespace/type/name)")

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
        if getattr(args, "config_command", None) == "view":
            tc_arg = getattr(args, "team_config", None)
            tc_path = Path(tc_arg) if tc_arg else None
            _run_config_view(team_config_path=tc_path, with_sources=getattr(args, "with_sources", False))
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

    # Pack commands work without config.yaml for zero-config onboarding.
    # We route them before _load_config_or_exit() so `aroom pack install <url>`
    # works even before `aroom init`.
    if args.command == "pack":
        _run_pack_dispatch(args)
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
    _denied_tools = getattr(args, "denied_tools", None)
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
    if _denied_tools:
        extra = [t.strip() for t in _denied_tools.split(",") if t.strip()]
        existing = set(config.safety.denied_tools)
        config.safety.denied_tools.extend(t for t in extra if t not in existing)

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

    if args.command == "artifact":
        _run_artifact(config, args)
        return

    if args.command == "space":
        _run_space(config, args)
        return

    if args.command == "start":
        _run_start(config, config_path, args)
        return

    if args.command == "stop":
        _run_stop(config)
        return

    if args.command == "status":
        _run_status(config)
        return

    if getattr(args, "bg_worker", False):
        _run_web(config, config_path, debug=args.debug, enforced_fields=enforced_fields)
        return

    # Resolve --space <name> to space_id
    _space_name = getattr(args, "space_name", None)
    _space_id: str | None = None
    if _space_name:
        _space_id = _resolve_space_id(config, _space_name)

    if args.command == "chat":
        _run_chat(
            config,
            prompt=args.prompt,
            no_tools=args.no_tools,
            continue_last=args.continue_last,
            resume_id=args.resume_id,
            project_path=args.project_path,
            model=args.model,
            trust_project=args.trust_project,
            no_project_context=args.no_project_context,
            plan_mode=args.plan,
            space_id=_space_id,
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
            space_id=_space_id,
        )
    else:
        _run_web(config, config_path, debug=args.debug, enforced_fields=enforced_fields)


if __name__ == "__main__":
    main()

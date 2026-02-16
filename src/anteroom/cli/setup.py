"""Rich-styled setup wizard and config editor for Anteroom."""

from __future__ import annotations

import asyncio
import getpass
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

console = Console()

# Color palette from renderer.py
GOLD = "#C5A059"
SLATE = "#94A3B8"
BLUE = "#38B6F6"


@dataclass
class ProviderPreset:
    name: str
    base_url: str
    needs_api_key: bool
    suggested_models: list[str] = field(default_factory=list)
    url_template: bool = False
    notes: str = ""


PROVIDER_PRESETS: list[ProviderPreset] = [
    ProviderPreset(
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        needs_api_key=True,
        suggested_models=["gpt-4o", "gpt-4o-mini", "gpt-4", "o1", "o3-mini"],
    ),
    ProviderPreset(
        name="Azure OpenAI",
        base_url="https://{resource}.openai.azure.com/openai/deployments/{deployment}",
        needs_api_key=True,
        url_template=True,
        notes="Requires Azure resource name and deployment name",
    ),
    ProviderPreset(
        name="Ollama",
        base_url="http://localhost:11434/v1",
        needs_api_key=False,
        suggested_models=["llama3", "mistral", "codellama", "gemma", "phi3"],
    ),
    ProviderPreset(
        name="LM Studio",
        base_url="http://localhost:1234/v1",
        needs_api_key=False,
    ),
    ProviderPreset(
        name="Anthropic (via proxy)",
        base_url="",
        needs_api_key=True,
        notes="Requires an OpenAI-compatible proxy URL",
    ),
    ProviderPreset(
        name="Custom",
        base_url="",
        needs_api_key=True,
    ),
]


def _is_interactive() -> bool:
    return sys.stdin.isatty()


def _redact_key(key: str) -> str:
    if not key:
        return "(not set)"
    if len(key) <= 8:
        return "****"
    return f"{key[:3]}...{key[-4:]}"


def _validate_url(url: str) -> bool:
    return bool(re.match(r"^https?://\S+", url))


def _select_provider() -> ProviderPreset:
    console.print(f"\n[{GOLD}]Select your AI provider:[/]")
    for i, preset in enumerate(PROVIDER_PRESETS, 1):
        notes = f"  [{SLATE}]({preset.notes})[/]" if preset.notes else ""
        console.print(f"  [{BLUE}]{i}[/] {preset.name}{notes}")
    console.print()

    choices = [str(i) for i in range(1, len(PROVIDER_PRESETS) + 1)]
    choice = Prompt.ask(f"[{SLATE}]Provider[/]", choices=choices, default="1")
    return PROVIDER_PRESETS[int(choice) - 1]


def _collect_base_url(preset: ProviderPreset) -> str:
    if preset.url_template:
        resource = Prompt.ask(f"[{SLATE}]Azure resource name[/]")
        deployment = Prompt.ask(f"[{SLATE}]Azure deployment name[/]")
        url = preset.base_url.replace("{resource}", resource).replace("{deployment}", deployment)
        console.print(f"  [{SLATE}]URL: {url}[/]")
        return url

    default = preset.base_url or None
    while True:
        url = Prompt.ask(f"[{SLATE}]Base URL[/]", default=default or "")
        if not url:
            console.print("  [red]URL is required[/]")
            continue
        if not _validate_url(url):
            console.print("  [red]Invalid URL format. Must start with http:// or https://[/]")
            continue
        return url


def _collect_api_key(preset: ProviderPreset) -> tuple[str, str]:
    if not preset.needs_api_key:
        console.print(f"  [{SLATE}]No API key needed for {preset.name}[/]")
        return "", ""

    use_command = Confirm.ask(
        f"[{SLATE}]Use a command to fetch the API key dynamically (e.g. from a secret manager)?[/]",
        default=False,
    )

    if use_command:
        cmd = Prompt.ask(f"[{SLATE}]API key command[/]")
        return "", cmd

    # SECURITY-REVIEW: getpass hides input; key stored in local config with 0600 perms
    console.print(f"[{SLATE}]API key (input hidden):[/]", end=" ")
    api_key = getpass.getpass("")
    return api_key, ""


def _collect_model(preset: ProviderPreset, available_models: list[str] | None = None) -> str:
    models_to_show: list[str] = []

    if available_models:
        # Prioritize preset suggestions, mark them with a star
        suggested = set(preset.suggested_models) if preset.suggested_models else set()
        starred = [m for m in available_models if m in suggested]
        rest = [m for m in available_models if m not in suggested]
        models_to_show = starred + rest
    elif preset.suggested_models:
        models_to_show = preset.suggested_models

    if models_to_show:
        console.print(f"\n[{GOLD}]Available models:[/]")
        display_limit = min(len(models_to_show), 15)
        suggested_set = set(preset.suggested_models) if preset.suggested_models else set()
        for i, m in enumerate(models_to_show[:display_limit], 1):
            star = f" [{GOLD}]*[/]" if m in suggested_set and available_models else ""
            console.print(f"  [{BLUE}]{i}[/] {m}{star}")
        if len(models_to_show) > display_limit:
            console.print(f"  [{SLATE}]... and {len(models_to_show) - display_limit} more[/]")
        console.print()

        choice = Prompt.ask(f"[{SLATE}]Model (number or name)[/]", default="1")
        if choice.isdigit() and 1 <= int(choice) <= len(models_to_show):
            return models_to_show[int(choice) - 1]
        return choice
    else:
        default = preset.suggested_models[0] if preset.suggested_models else "gpt-4"
        return Prompt.ask(f"[{SLATE}]Model name[/]", default=default)


def _collect_system_prompt() -> str:
    use_custom = Confirm.ask(f"\n[{SLATE}]Set a custom system prompt?[/]", default=False)
    if use_custom:
        return Prompt.ask(f"[{SLATE}]System prompt[/]")
    return ""


def _collect_app_settings(current: dict[str, Any] | None = None) -> dict[str, Any]:
    current = current or {}
    host = Prompt.ask(f"[{SLATE}]Host[/]", default=current.get("host", "127.0.0.1"))
    port = Prompt.ask(f"[{SLATE}]Port[/]", default=str(current.get("port", 8080)))
    tls = Confirm.ask(f"[{SLATE}]Enable TLS (HTTPS)?[/]", default=current.get("tls", False))
    return {"host": host, "port": int(port), "tls": tls}


def _test_connection_with_spinner(
    base_url: str,
    api_key: str,
    api_key_command: str,
    model: str,
    verify_ssl: bool = True,
) -> tuple[bool, list[str]]:
    from ..config import AIConfig
    from ..services.ai_service import create_ai_service

    ai_config = AIConfig(
        base_url=base_url,
        api_key=api_key or "not-needed",
        api_key_command=api_key_command,
        model=model,
        verify_ssl=verify_ssl,
    )
    ai_service = create_ai_service(ai_config)

    with console.status(f"[{GOLD}]Testing connection...[/]", spinner="dots12"):
        try:
            valid, message, models = asyncio.run(ai_service.validate_connection())
        except Exception as e:
            console.print(f"  [red]Connection failed: {e}[/]")
            return False, []

    if valid:
        console.print(f"  [green]Connected successfully — {len(models)} model(s) available[/]")
        return True, models
    else:
        console.print(f"  [red]Connection failed: {message}[/]")
        return False, []


def _render_summary(config_data: dict[str, Any], config_path: Path) -> None:
    ai = config_data.get("ai", {})
    app = config_data.get("app", {})

    table = Table(title="Configuration Summary", show_header=False, border_style=GOLD)
    table.add_column("Setting", style=SLATE)
    table.add_column("Value")

    table.add_row("Config file", str(config_path))
    table.add_row("Provider URL", ai.get("base_url", ""))
    table.add_row("API key", _redact_key(ai.get("api_key", "")))
    if ai.get("api_key_command"):
        table.add_row("API key command", ai["api_key_command"])
    table.add_row("Model", ai.get("model", ""))
    if ai.get("system_prompt"):
        prompt_preview = ai["system_prompt"]
        if len(prompt_preview) > 60:
            prompt_preview = prompt_preview[:57] + "..."
        table.add_row("System prompt", prompt_preview)
    if app:
        table.add_row("Host", f"{app.get('host', '127.0.0.1')}:{app.get('port', 8080)}")
        if app.get("tls"):
            table.add_row("TLS", "enabled")

    identity = config_data.get("identity", {})
    if identity.get("user_id"):
        table.add_row("Identity", f"{identity.get('display_name', '')} ({identity['user_id'][:8]}...)")

    console.print()
    console.print(table)
    console.print()


def _write_config(config_data: dict[str, Any], config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # SECURITY-REVIEW: API key written to user's local config file with restricted permissions
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)

    config_path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def run_init_wizard(force: bool = False) -> bool:
    """Main setup wizard. Returns True if config was written successfully."""
    from ..config import _get_config_path

    if not _is_interactive():
        print(
            "Non-interactive terminal detected.\n\n"
            "To configure Anteroom, either:\n"
            "  1. Run 'aroom init' in an interactive terminal\n"
            "  2. Create ~/.anteroom/config.yaml manually:\n\n"
            "     ai:\n"
            '       base_url: "https://api.openai.com/v1"\n'
            '       api_key: "your-api-key"\n'
            '       model: "gpt-4o"\n\n'
            "  3. Set environment variables:\n"
            "     AI_CHAT_BASE_URL=https://api.openai.com/v1\n"
            "     AI_CHAT_API_KEY=your-api-key\n"
            "     AI_CHAT_MODEL=gpt-4o\n",
            file=sys.stderr,
        )
        return False

    config_path = _get_config_path()

    if config_path.exists() and not force:
        if not Confirm.ask(f"[{SLATE}]Config exists at {config_path}. Overwrite?[/]", default=False):
            console.print("Cancelled.")
            return False

    # Welcome
    console.print()
    console.print(
        Panel(
            f"[bold {GOLD}]A N T E R O O M[/]\n[{SLATE}]Let's get you set up.[/]",
            border_style=GOLD,
            padding=(1, 4),
        )
    )

    try:
        # 1. Provider
        preset = _select_provider()

        # 2. Base URL
        base_url = _collect_base_url(preset)

        # 3. API key
        api_key, api_key_command = _collect_api_key(preset)

        # 4. Connection test
        available_models: list[str] | None = None
        connected = False

        test_now = Confirm.ask(f"\n[{SLATE}]Test connection now?[/]", default=True)
        if test_now:
            temp_model = preset.suggested_models[0] if preset.suggested_models else "gpt-4"
            connected, models = _test_connection_with_spinner(base_url, api_key, api_key_command, temp_model)
            if connected:
                available_models = models
            else:
                while True:
                    retry_choice = Prompt.ask(
                        f"[{SLATE}]Retry, continue anyway, or quit?[/]",
                        choices=["retry", "continue", "quit"],
                        default="retry",
                    )
                    if retry_choice == "quit":
                        console.print("Setup cancelled.")
                        return False
                    if retry_choice == "continue":
                        break
                    connected, models = _test_connection_with_spinner(base_url, api_key, api_key_command, temp_model)
                    if connected:
                        available_models = models
                        break

        # 5. Model
        model = _collect_model(preset, available_models)

        # 6. System prompt
        system_prompt = _collect_system_prompt()

        # 7. User Identity
        console.print(f"\n[{GOLD}]User Identity[/]")
        console.print(f"  [{SLATE}]Used to identify your messages in shared databases.[/]")
        identity_display_name = Prompt.ask(
            f"[{SLATE}]Display name[/]",
            default=getpass.getuser(),
        )

        from ..identity import generate_identity

        identity_data = generate_identity(identity_display_name)

        # Build config
        config_data: dict[str, Any] = {
            "ai": {
                "base_url": base_url,
                "model": model,
            }
        }
        if api_key:
            config_data["ai"]["api_key"] = api_key
        if api_key_command:
            config_data["ai"]["api_key_command"] = api_key_command
        if system_prompt:
            config_data["ai"]["system_prompt"] = system_prompt

        config_data["identity"] = identity_data

        # 8. Summary
        _render_summary(config_data, config_path)

        # 9. Confirm & write
        if not Confirm.ask(f"[{SLATE}]Write configuration?[/]", default=True):
            console.print("Setup cancelled.")
            return False

        _write_config(config_data, config_path)

        # Identity warning
        console.print(
            Panel(
                f"Your identity has been generated.\n\n"
                f"  User ID: {identity_data['user_id']}\n"
                f"  Display name: {identity_data['display_name']}\n\n"
                f"[bold yellow]IMPORTANT:[/] Your identity section in\n"
                f"config.yaml contains a private key that\n"
                f"proves ownership of your user ID. If you\n"
                f"delete or lose it, you will not be able\n"
                f"to recover your identity, and your\n"
                f"messages in shared databases will become\n"
                f"unverifiable.\n\n"
                f"Back up your config file to preserve your\n"
                f"identity across reinstalls.",
                border_style=GOLD,
                title="Identity",
                padding=(1, 2),
            )
        )

        # Success
        console.print(
            Panel(
                f"[green]Configuration saved to {config_path}[/]\n\n"
                f"[{SLATE}]Next steps:[/]\n"
                f"  [{BLUE}]aroom[/]        Launch web UI\n"
                f"  [{BLUE}]aroom chat[/]   Start CLI chat\n"
                f"  [{BLUE}]aroom --test[/] Verify connection\n"
                f"  [{BLUE}]aroom config[/] Edit settings",
                border_style="green",
                title="Setup Complete",
                padding=(1, 2),
            )
        )
        return True

    except (KeyboardInterrupt, EOFError):
        console.print("\n\nSetup cancelled.")
        return False


def run_config_editor() -> bool:
    """Interactive config editor. Returns True if changes were saved."""
    from ..config import _get_config_path

    if not _is_interactive():
        print("Config editor requires an interactive terminal.", file=sys.stderr)
        return False

    config_path = _get_config_path()

    if not config_path.exists():
        console.print(f"[{SLATE}]No config found. Starting setup wizard...[/]\n")
        return run_init_wizard()

    with open(config_path) as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    original_raw = yaml.dump(raw, default_flow_style=False, sort_keys=False)
    ai = raw.setdefault("ai", {})
    app = raw.setdefault("app", {})
    changed = False

    try:
        while True:
            _render_summary(raw, config_path)

            console.print(f"[{GOLD}]What would you like to change?[/]")
            console.print(f"  [{BLUE}]1[/] Provider & endpoint")
            console.print(f"  [{BLUE}]2[/] API key")
            console.print(f"  [{BLUE}]3[/] Model")
            console.print(f"  [{BLUE}]4[/] System prompt")
            console.print(f"  [{BLUE}]5[/] App settings (host, port, TLS)")
            console.print(f"  [{BLUE}]6[/] Test connection")
            console.print(f"  [{BLUE}]7[/] View identity")
            console.print(f"  [{BLUE}]8[/] Done")
            console.print()

            choice = Prompt.ask(f"[{SLATE}]Choice[/]", choices=["1", "2", "3", "4", "5", "6", "7", "8"], default="8")

            if choice == "1":
                preset = _select_provider()
                url = _collect_base_url(preset)
                ai["base_url"] = url
                changed = True
                if Confirm.ask(f"\n[{SLATE}]Test connection with new endpoint?[/]", default=True):
                    model = ai.get("model", "gpt-4")
                    _test_connection_with_spinner(url, ai.get("api_key", ""), ai.get("api_key_command", ""), model)

            elif choice == "2":
                # Build a minimal preset for key collection
                needs_key_preset = ProviderPreset(name="current", base_url="", needs_api_key=True)
                api_key, api_key_command = _collect_api_key(needs_key_preset)
                if api_key:
                    ai["api_key"] = api_key
                    ai.pop("api_key_command", None)
                elif api_key_command:
                    ai["api_key_command"] = api_key_command
                    ai.pop("api_key", None)
                changed = True
                if Confirm.ask(f"\n[{SLATE}]Test connection with new key?[/]", default=True):
                    model = ai.get("model", "gpt-4")
                    _test_connection_with_spinner(
                        ai.get("base_url", ""),
                        ai.get("api_key", ""),
                        ai.get("api_key_command", ""),
                        model,
                    )

            elif choice == "3":
                # Try to get available models
                available_models: list[str] | None = None
                if ai.get("base_url"):
                    ok, models = _test_connection_with_spinner(
                        ai.get("base_url", ""),
                        ai.get("api_key", ""),
                        ai.get("api_key_command", ""),
                        ai.get("model", "gpt-4"),
                    )
                    if ok:
                        available_models = models
                dummy_preset = ProviderPreset(name="current", base_url="", needs_api_key=False)
                model = _collect_model(dummy_preset, available_models)
                ai["model"] = model
                changed = True

            elif choice == "4":
                current = ai.get("system_prompt", "")
                if current:
                    console.print(f"\n[{SLATE}]Current: {current[:80]}{'...' if len(current) > 80 else ''}[/]")
                if Confirm.ask(f"[{SLATE}]Clear custom system prompt (use default)?[/]", default=not bool(current)):
                    ai.pop("system_prompt", None)
                else:
                    ai["system_prompt"] = Prompt.ask(f"[{SLATE}]System prompt[/]")
                changed = True

            elif choice == "5":
                settings = _collect_app_settings(app)
                app.update(settings)
                raw["app"] = app
                changed = True

            elif choice == "6":
                _test_connection_with_spinner(
                    ai.get("base_url", ""),
                    ai.get("api_key", ""),
                    ai.get("api_key_command", ""),
                    ai.get("model", "gpt-4"),
                )

            elif choice == "7":
                identity = raw.get("identity", {})
                if identity.get("user_id"):
                    import hashlib

                    pub_key = identity.get("public_key", "")
                    fingerprint = hashlib.sha256(pub_key.encode()).hexdigest()[:16] if pub_key else "N/A"
                    console.print(f"\n[{GOLD}]User Identity[/]")
                    console.print(f"  [{SLATE}]User ID:[/] {identity['user_id']}")
                    console.print(f"  [{SLATE}]Display name:[/] {identity.get('display_name', '')}")
                    console.print(f"  [{SLATE}]Public key fingerprint:[/] {fingerprint}")

                    if Confirm.ask(f"\n[{SLATE}]Change display name?[/]", default=False):
                        new_name = Prompt.ask(f"[{SLATE}]New display name[/]", default=identity.get("display_name", ""))
                        identity["display_name"] = new_name
                        raw["identity"] = identity
                        changed = True
                    console.print()
                else:
                    console.print(f"\n[{SLATE}]No identity configured. Run 'aroom init' to generate one.[/]\n")

            elif choice == "8":
                break

        # Check if anything actually changed
        current_yaml = yaml.dump(raw, default_flow_style=False, sort_keys=False)
        if changed and current_yaml != original_raw:
            _render_summary(raw, config_path)
            if Confirm.ask(f"[{SLATE}]Save changes?[/]", default=True):
                _write_config(raw, config_path)
                console.print(f"\n[green]Configuration saved to {config_path}[/]\n")
                return True
            else:
                console.print("Changes discarded.")
                return False
        else:
            console.print(f"[{SLATE}]No changes made.[/]\n")
            return False

    except (KeyboardInterrupt, EOFError):
        console.print("\n\nCancelled.")
        return False

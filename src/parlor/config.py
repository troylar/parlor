"""Configuration loader: YAML file with environment variable fallbacks."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class AIConfig:
    base_url: str
    api_key: str
    model: str = "gpt-4"
    system_prompt: str = "You are a helpful assistant."
    verify_ssl: bool = True


@dataclass
class McpServerConfig:
    name: str
    transport: str  # "stdio" or "sse"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class SharedDatabaseConfig:
    name: str
    path: str


@dataclass
class AppSettings:
    host: str = "127.0.0.1"
    port: int = 8080
    data_dir: Path = field(default_factory=lambda: Path.home() / ".ai-chat")


@dataclass
class CliConfig:
    builtin_tools: bool = True
    max_tool_iterations: int = 50


@dataclass
class AppConfig:
    ai: AIConfig
    app: AppSettings = field(default_factory=AppSettings)
    mcp_servers: list[McpServerConfig] = field(default_factory=list)
    shared_databases: list[SharedDatabaseConfig] = field(default_factory=list)
    cli: CliConfig = field(default_factory=CliConfig)


def _get_config_path(data_dir: Path | None = None) -> Path:
    if data_dir:
        return data_dir / "config.yaml"
    return Path.home() / ".ai-chat" / "config.yaml"


def load_config(config_path: Path | None = None) -> AppConfig:
    raw: dict[str, Any] = {}
    path = config_path or _get_config_path()

    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

    ai_raw = raw.get("ai", {})
    base_url = ai_raw.get("base_url") or os.environ.get("AI_CHAT_BASE_URL", "")
    api_key = ai_raw.get("api_key") or os.environ.get("AI_CHAT_API_KEY", "")
    model = ai_raw.get("model") or os.environ.get("AI_CHAT_MODEL", "gpt-4")
    system_prompt = ai_raw.get("system_prompt") or os.environ.get(
        "AI_CHAT_SYSTEM_PROMPT", "You are a helpful assistant."
    )

    if not base_url:
        raise ValueError(
            "AI base_url is required. Set 'ai.base_url' in config.yaml "
            f"({path}) or AI_CHAT_BASE_URL environment variable."
        )
    if not api_key:
        raise ValueError(
            f"AI api_key is required. Set 'ai.api_key' in config.yaml ({path}) or AI_CHAT_API_KEY environment variable."
        )

    verify_ssl_raw = ai_raw.get("verify_ssl", os.environ.get("AI_CHAT_VERIFY_SSL", "true"))
    verify_ssl = str(verify_ssl_raw).lower() not in ("false", "0", "no")

    ai = AIConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        system_prompt=system_prompt,
        verify_ssl=verify_ssl,
    )

    app_raw = raw.get("app", {})
    data_dir = Path(os.path.expanduser(app_raw.get("data_dir", "~/.ai-chat")))
    app_settings = AppSettings(
        host=app_raw.get("host", "127.0.0.1"),
        port=int(app_raw.get("port", 8080)),
        data_dir=data_dir,
    )

    mcp_servers: list[McpServerConfig] = []
    for srv in raw.get("mcp_servers", []):
        env_raw = srv.get("env", {})
        env: dict[str, str] = {}
        for k, v in env_raw.items():
            env[k] = os.path.expandvars(str(v))
        mcp_servers.append(
            McpServerConfig(
                name=srv["name"],
                transport=srv.get("transport", "stdio"),
                command=srv.get("command"),
                args=srv.get("args", []),
                url=srv.get("url"),
                env=env,
            )
        )

    shared_databases: list[SharedDatabaseConfig] = []
    for sdb in raw.get("shared_databases", []):
        shared_databases.append(
            SharedDatabaseConfig(
                name=sdb["name"],
                path=os.path.expanduser(sdb["path"]),
            )
        )

    app_settings.data_dir.mkdir(parents=True, exist_ok=True)
    try:
        app_settings.data_dir.chmod(stat.S_IRWXU)  # 0700
        if path.exists():
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass  # May fail on Windows or non-owned files

    cli_raw = raw.get("cli", {})
    cli_config = CliConfig(
        builtin_tools=cli_raw.get("builtin_tools", True),
        max_tool_iterations=int(cli_raw.get("max_tool_iterations", 50)),
    )

    return AppConfig(
        ai=ai, app=app_settings, mcp_servers=mcp_servers,
        shared_databases=shared_databases, cli=cli_config,
    )

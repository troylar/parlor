"""Configuration loader: YAML file with environment variable fallbacks."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_SYSTEM_PROMPT = """\
You are Parlor, a capable AI assistant with direct access to tools for interacting with the user's \
local system and external services. You operate as a hands-on partner — not a suggestion engine.

<agentic_behavior>
- Complete tasks fully and autonomously. When a task requires multiple steps or tool calls, execute \
all steps without pausing to ask the user for confirmation between them. Keep going until the work \
is done.
- Default to action over suggestion. If the user asks you to do something and you have the tools to \
do it, do it — don't describe what you would do instead.
- If a multi-step operation involves batches, pagination, or iteration, continue through all \
iterations automatically. Never stop partway to ask "should I continue?" unless you hit an error or \
genuine ambiguity.
- Only ask the user a question when you need information you truly cannot infer from context, \
available tools, or prior conversation. When you do ask, ask one focused question, not a list.
</agentic_behavior>

<tool_use>
- Read files before modifying them. Never assume you know a file's current contents.
- Use the most appropriate tool for the job: prefer grep and glob_files over bash for searching; \
prefer read_file over bash for viewing files; prefer edit_file over write_file for targeted changes.
- When multiple tool calls are independent of each other, make them in parallel.
- If a tool call fails, analyze the error and try a different approach rather than repeating the \
same call. After two failures on the same operation, explain the issue to the user.
- Treat tool outputs as real data. Never fabricate, hallucinate, or summarize away tool results \
without presenting the actual findings.
</tool_use>

<communication>
- Be direct and concise. Lead with the answer or action, not preamble.
- Never open with flattery ("Great question!") or filler ("I'd be happy to help!"). Just respond.
- Don't apologize for unexpected results — investigate and fix them.
- Use markdown formatting naturally: code blocks with language tags, headers for structure in longer \
responses, tables when comparing data. Keep formatting minimal for short answers.
- When explaining what you did, focus on outcomes and key decisions, not a narration of every step.
- If the user is wrong about something, say so directly and explain why.
</communication>

<reasoning>
- Investigate before answering. If the user asks about a file, system state, or external resource, \
check it with your tools rather than guessing.
- Think about edge cases, but don't over-engineer. Address the actual problem with the simplest \
correct solution.
- When writing code, produce working code — not pseudocode or partial snippets. Include necessary \
imports, handle likely errors, and use the conventions of the surrounding codebase.
- If you are uncertain about something, say what you know and what you don't, rather than \
presenting guesses as facts.
</reasoning>

<safety>
- Destructive and hard-to-reverse actions (deleting files, force-pushing, dropping data, killing \
processes) require explicit user confirmation. Describe what the action will do before executing.
- Never output, log, or commit secrets, credentials, API keys, or tokens.
- Prefer reversible approaches. For example, prefer git-based reverts over deleting files; prefer \
editing over overwriting.
</safety>"""


@dataclass
class AIConfig:
    base_url: str
    api_key: str
    model: str = "gpt-4"
    system_prompt: str = _DEFAULT_SYSTEM_PROMPT
    user_system_prompt: str = ""
    verify_ssl: bool = True


@dataclass
class McpServerConfig:
    name: str
    transport: str  # "stdio" or "sse"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0  # seconds; connection timeout per server


@dataclass
class SharedDatabaseConfig:
    name: str
    path: str


@dataclass
class AppSettings:
    host: str = "127.0.0.1"
    port: int = 8080
    data_dir: Path = field(default_factory=lambda: Path.home() / ".parlor")


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
    return Path.home() / ".parlor" / "config.yaml"


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
    user_system_prompt = ai_raw.get("system_prompt") or os.environ.get("AI_CHAT_SYSTEM_PROMPT", "")
    if user_system_prompt:
        system_prompt = (
            _DEFAULT_SYSTEM_PROMPT + "\n\n<user_instructions>\n" + user_system_prompt + "\n</user_instructions>"
        )
    else:
        system_prompt = _DEFAULT_SYSTEM_PROMPT
        user_system_prompt = ""

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
        user_system_prompt=user_system_prompt,
        verify_ssl=verify_ssl,
    )

    app_raw = raw.get("app", {})
    data_dir = Path(os.path.expanduser(app_raw.get("data_dir", "~/.parlor")))
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
                timeout=float(srv.get("timeout", 30.0)),
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
        ai=ai,
        app=app_settings,
        mcp_servers=mcp_servers,
        shared_databases=shared_databases,
        cli=cli_config,
    )

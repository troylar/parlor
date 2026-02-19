"""Configuration loader: YAML file with environment variable fallbacks."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_BUILTIN_TOOL_DESCRIPTIONS: dict[str, str] = {
    "read_file": "Read file contents with line numbers. Use this instead of bash cat/head/tail.",
    "write_file": "Create or overwrite a file. Only use for new files or full rewrites; prefer edit_file for changes.",
    "edit_file": "Exact string replacement in files. Preferred for targeted code changes.",
    "bash": "Run shell commands (git, build tools, tests, installs). Do NOT use for file reading or searching.",
    "glob_files": "Find files by name/path pattern (e.g. '**/*.py'). Use instead of bash find or ls.",
    "grep": "Regex search across file contents. Use instead of bash grep or rg.",
    "create_canvas": "Create a rich content panel (code, docs, diagrams) alongside chat.",
    "update_canvas": "Replace canvas content entirely with new content.",
    "patch_canvas": "Apply incremental search/replace edits to an existing canvas.",
    "run_agent": "Spawn a sub-agent for parallel or isolated tasks. Each gets its own context.",
}


def _get_version() -> str:
    try:
        from importlib.metadata import version

        return version("anteroom")
    except Exception:
        return "unknown"


def build_runtime_context(
    *,
    model: str,
    builtin_tools: list[str] | None = None,
    mcp_servers: dict[str, dict[str, Any]] | None = None,
    interface: str = "web",
    working_dir: str | None = None,
    tls_enabled: bool = False,
) -> str:
    """Build an XML-tagged runtime context block for the system prompt."""
    version = _get_version()
    iface_label = "Web UI" if interface == "web" else "CLI REPL"

    lines = [
        "<anteroom_context>",
        f"You are Anteroom v{version}, running via the {iface_label}.",
        f"Current model: {model}",
    ]

    # Tools
    tool_lines: list[str] = []
    if builtin_tools:
        for name in builtin_tools:
            desc = _BUILTIN_TOOL_DESCRIPTIONS.get(name, "")
            tool_lines.append(f"  - {name}: {desc}" if desc else f"  - {name}")
    if mcp_servers:
        for srv_name, srv_info in mcp_servers.items():
            status = srv_info.get("status", "unknown")
            if status == "connected":
                tools = srv_info.get("tools", [])
                if isinstance(tools, list):
                    for t in tools:
                        t_name = t.get("name", t) if isinstance(t, dict) else t
                        tool_lines.append(f'  - {t_name} (via MCP server "{srv_name}")')
    if tool_lines:
        lines.append("")
        lines.append("Available tools:")
        lines.extend(tool_lines)

    # MCP servers
    if mcp_servers:
        lines.append("")
        lines.append("MCP servers:")
        for srv_name, srv_info in mcp_servers.items():
            status = srv_info.get("status", "unknown")
            tool_count = srv_info.get("tool_count", 0)
            lines.append(f"  - {srv_name}: {status} ({tool_count} tools)")

    # Capabilities
    lines.append("")
    lines.append("Anteroom capabilities:")
    if interface == "web":
        lines.append(
            "  - Web UI: 4 themes (Midnight/Dawn/Aurora/Ember), conversation folders & tags, "
            "projects with custom instructions, file attachments, command palette (Cmd/Ctrl+K), "
            "model switching, prompt queuing, shared databases"
        )
    else:
        lines.append(
            "  - CLI: built-in file/shell tools, MCP integration, skills system, "
            "@file references, /commands, ANTEROOM.md project instructions"
        )
    lines.append(
        "  - Shared: SQLite with FTS search, conversation forking & rewinding, "
        "SSE streaming, OpenAI-compatible API backend"
    )

    # Config details
    if interface == "cli" and working_dir:
        lines.append(f"\nWorking directory: {working_dir}")
    if interface == "web":
        lines.append(f"\nTLS: {'enabled' if tls_enabled else 'disabled'}")

    lines.append("</anteroom_context>")
    return "\n".join(lines)


_DEFAULT_SYSTEM_PROMPT = """\
You are Anteroom, a capable AI coding assistant with direct access to tools for interacting with \
the user's local system and external services. You operate as a hands-on partner — not a suggestion \
engine. You help developers write, debug, refactor, and understand code.

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
DO NOT use bash to do what dedicated tools can do:
- To read files, use read_file — not cat, head, tail, or sed.
- To edit files, use edit_file — not sed, awk, or echo redirection.
- To create files, use write_file — not cat with heredoc or echo.
- To search for files by name, use glob_files — not find or ls.
- To search file contents, use grep — not bash grep or rg.
Reserve bash for system commands that require shell execution: git, build tools, package managers, \
running tests, starting servers.

Tool selection:
- Prefer edit_file over write_file for modifying existing files. edit_file makes targeted changes; \
write_file replaces the entire file.
- Prefer grep over bash for searching code. Prefer glob_files over bash for finding files.
- Read files before modifying them. Never assume you know a file's current contents.

Parallel execution:
- When multiple tool calls are independent of each other, make them all in parallel in the same \
response. For example, reading 3 files should be 3 parallel read_file calls, not sequential.
- If one tool call depends on the result of another, run them sequentially — never guess at \
dependent values.

Error handling:
- If a tool call fails, analyze the error and try a different approach. Do not repeat the exact \
same call.
- After two failures on the same operation, explain the issue to the user.
- Treat tool outputs as real data. Never fabricate or hallucinate tool results.
</tool_use>

<code_modification>
- Always read a file before modifying it. Do not propose changes to code you have not read.
- Prefer editing existing files over creating new ones. Build on existing work.
- Understand existing code before suggesting modifications. Look at surrounding patterns, naming \
conventions, and architecture before writing new code.
- Produce working code with necessary imports, error handling, and type hints. Never output \
pseudocode or partial snippets when the user needs a real implementation.
- Match the conventions of the surrounding codebase: indentation, naming, patterns, structure.

Avoid over-engineering:
- Only make changes that are directly requested or clearly necessary.
- Don't add features, refactor code, or make "improvements" beyond what was asked.
- Don't add docstrings, comments, or type annotations to code you didn't change.
- Don't create helpers, utilities, or abstractions for one-time operations. Three similar lines \
of code is better than a premature abstraction.
- Don't add error handling or validation for scenarios that cannot happen. Trust internal code and \
framework guarantees; only validate at system boundaries.
</code_modification>

<git_operations>
When performing git operations:
- Never run destructive git commands (push --force, reset --hard, checkout ., clean -f, branch -D) \
unless the user explicitly requests them.
- Never amend published commits or skip hooks (--no-verify) unless explicitly asked.
- When staging files, prefer adding specific files by name rather than "git add -A" or "git add .", \
which can accidentally include secrets or binaries.
- Never force-push to main/master. Warn the user if they request it.
- Prefer creating new commits over amending existing ones.
- When a pre-commit hook fails, the commit did not happen — do not use --amend (which would modify \
the previous commit). Fix the issue, re-stage, and create a new commit.
</git_operations>

<investigation>
- Never speculate about code you have not read. If the user references a file, read it first.
- If the user asks about system state, configuration, or behavior, verify with tools rather than \
guessing from memory.
- When debugging, gather evidence before hypothesizing. Read error messages, check logs, inspect \
the actual state — don't assume.
- If you are uncertain about something, say what you know and what you don't, rather than \
presenting guesses as facts.
</investigation>

<communication>
- Be direct and concise. Lead with the answer or action, not preamble.
- Never open with flattery ("Great question!") or filler ("I'd be happy to help!"). Just respond.
- Don't apologize for unexpected results — investigate and fix them.
- Use markdown formatting naturally: code blocks with language tags, headers for structure in longer \
responses, tables when comparing data. Keep formatting minimal for short answers.
- When explaining what you did, focus on outcomes and key decisions, not a narration of every step.
- If the user is wrong about something, say so directly and explain why.
</communication>

<safety>
Carefully consider the reversibility and impact of actions. You can freely take local, reversible \
actions like editing files or running tests. But for actions that are hard to reverse, affect shared \
systems, or could be destructive, confirm with the user first.

Actions that always require confirmation:
- Deleting files, branches, database tables, or processes (rm -rf, git branch -D, DROP TABLE)
- Force-pushing, resetting hard, discarding uncommitted changes (git push --force, git reset --hard)
- Pushing code, creating PRs, commenting on issues, sending messages to external services
- Modifying shared infrastructure, permissions, or CI/CD configuration

Security:
- Never output, log, or commit secrets, credentials, API keys, or tokens.
- Do not introduce security vulnerabilities: no SQL injection, command injection, XSS, path \
traversal, or other OWASP top 10 issues. If you notice insecure code, fix it immediately.
- Use parameterized queries for database operations. Never concatenate user input into SQL.
- Never use eval(), exec(), or subprocess with shell=True on user-controlled input.
- Prefer reversible approaches: git reverts over file deletion, edits over full overwrites.
</safety>"""


@dataclass
class AIConfig:
    base_url: str
    api_key: str
    model: str = "gpt-4"
    system_prompt: str = _DEFAULT_SYSTEM_PROMPT
    user_system_prompt: str = ""
    verify_ssl: bool = True
    api_key_command: str = ""
    request_timeout: int = 120  # seconds; connect + per-chunk read timeout
    narration_cadence: int = 5  # progress updates every N tool calls; 0 = disabled


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
    passphrase_hash: str = ""


@dataclass
class AppSettings:
    host: str = "127.0.0.1"
    port: int = 8080
    data_dir: Path = field(default_factory=lambda: Path.home() / ".anteroom")
    tls: bool = False


@dataclass
class CliConfig:
    builtin_tools: bool = True
    max_tool_iterations: int = 50
    context_warn_tokens: int = 80_000
    context_auto_compact_tokens: int = 100_000
    tool_dedup: bool = True  # collapse consecutive similar tool calls; False = show all


@dataclass
class UserIdentity:
    user_id: str
    display_name: str
    public_key: str  # PEM
    private_key: str  # PEM


@dataclass
class EmbeddingsConfig:
    enabled: bool = True
    model: str = "text-embedding-3-small"
    dimensions: int = 1536
    base_url: str = ""
    api_key: str = ""
    api_key_command: str = ""


@dataclass
class SafetyToolConfig:
    enabled: bool = True


@dataclass
class SubagentConfig:
    max_concurrent: int = 5
    max_total: int = 10
    max_depth: int = 3
    max_iterations: int = 15
    timeout: int = 120
    max_output_chars: int = 4000
    max_prompt_chars: int = 32_000


@dataclass
class SafetyConfig:
    enabled: bool = True
    approval_mode: str = "ask_for_writes"
    approval_timeout: int = 120
    bash: SafetyToolConfig = field(default_factory=SafetyToolConfig)
    write_file: SafetyToolConfig = field(default_factory=SafetyToolConfig)
    custom_patterns: list[str] = field(default_factory=list)
    sensitive_paths: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    tool_tiers: dict[str, str] = field(default_factory=dict)
    subagent: SubagentConfig = field(default_factory=SubagentConfig)


@dataclass
class AppConfig:
    ai: AIConfig
    app: AppSettings = field(default_factory=AppSettings)
    mcp_servers: list[McpServerConfig] = field(default_factory=list)
    shared_databases: list[SharedDatabaseConfig] = field(default_factory=list)
    cli: CliConfig = field(default_factory=CliConfig)
    identity: UserIdentity | None = None
    embeddings: EmbeddingsConfig = field(default_factory=EmbeddingsConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)


def _resolve_data_dir() -> Path:
    """Resolve data directory: prefer ~/.anteroom, fall back to ~/.parlor for backward compat."""
    anteroom_dir = Path.home() / ".anteroom"
    parlor_dir = Path.home() / ".parlor"
    if anteroom_dir.exists():
        return anteroom_dir
    if parlor_dir.exists():
        return parlor_dir
    return anteroom_dir


def _get_config_path(data_dir: Path | None = None) -> Path:
    if data_dir:
        return data_dir / "config.yaml"
    return _resolve_data_dir() / "config.yaml"


def load_config(config_path: Path | None = None) -> AppConfig:
    raw: dict[str, Any] = {}
    path = config_path or _get_config_path()

    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

    ai_raw = raw.get("ai", {})
    base_url = ai_raw.get("base_url") or os.environ.get("AI_CHAT_BASE_URL", "")
    api_key = ai_raw.get("api_key") or os.environ.get("AI_CHAT_API_KEY", "")
    api_key_command = ai_raw.get("api_key_command") or os.environ.get("AI_CHAT_API_KEY_COMMAND", "")
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
    if not api_key and not api_key_command:
        raise ValueError(
            f"AI api_key or api_key_command is required. Set 'ai.api_key' or 'ai.api_key_command' "
            f"in config.yaml ({path}) or AI_CHAT_API_KEY / AI_CHAT_API_KEY_COMMAND environment variable."
        )

    verify_ssl_raw = ai_raw.get("verify_ssl", os.environ.get("AI_CHAT_VERIFY_SSL", "true"))
    verify_ssl = str(verify_ssl_raw).lower() not in ("false", "0", "no")
    try:
        _raw_timeout = ai_raw.get("request_timeout", os.environ.get("AI_CHAT_REQUEST_TIMEOUT", 120))
        request_timeout = max(10, min(600, int(_raw_timeout)))
    except (ValueError, TypeError):
        request_timeout = 120

    try:
        narration_cadence = int(ai_raw.get("narration_cadence", os.environ.get("AI_CHAT_NARRATION_CADENCE", 5)))
        narration_cadence = max(0, narration_cadence)
    except (ValueError, TypeError):
        narration_cadence = 5

    if narration_cadence > 0:
        system_prompt += (
            "\n\n<narration>\n"
            f"During multi-step tasks with tool calls, give a brief 1-2 sentence progress update every "
            f"{narration_cadence} tool calls — what you've found so far and what you're doing next. "
            f"Keep updates concise and actionable.\n"
            "</narration>"
        )

    ai = AIConfig(
        base_url=base_url,
        api_key=api_key,
        api_key_command=api_key_command,
        model=model,
        system_prompt=system_prompt,
        user_system_prompt=user_system_prompt,
        verify_ssl=verify_ssl,
        request_timeout=request_timeout,
        narration_cadence=narration_cadence,
    )

    app_raw = raw.get("app", {})
    default_data_dir = str(_resolve_data_dir())
    data_dir = Path(os.path.expanduser(app_raw.get("data_dir", default_data_dir)))
    tls_raw = app_raw.get("tls", False)
    tls_enabled = str(tls_raw).lower() not in ("false", "0", "no")

    app_settings = AppSettings(
        host=app_raw.get("host", "127.0.0.1"),
        port=int(app_raw.get("port", 8080)),
        data_dir=data_dir,
        tls=tls_enabled,
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
                passphrase_hash=sdb.get("passphrase_hash", ""),
            )
        )

    # Also support the "databases" key (newer config format)
    for db_name, db_conf in raw.get("databases", {}).items():
        if db_name == "personal":
            continue
        if isinstance(db_conf, dict):
            shared_databases.append(
                SharedDatabaseConfig(
                    name=db_name,
                    path=os.path.expanduser(db_conf.get("path", "")),
                    passphrase_hash=db_conf.get("passphrase_hash", ""),
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
    try:
        context_warn_tokens = int(cli_raw.get("context_warn_tokens", 80_000))
    except (ValueError, TypeError):
        context_warn_tokens = 80_000
    try:
        context_auto_compact_tokens = int(cli_raw.get("context_auto_compact_tokens", 100_000))
    except (ValueError, TypeError):
        context_auto_compact_tokens = 100_000
    tool_dedup_env = os.environ.get("AI_CHAT_TOOL_DEDUP")
    tool_dedup_raw = tool_dedup_env if tool_dedup_env is not None else cli_raw.get("tool_dedup", True)
    tool_dedup = str(tool_dedup_raw).lower() not in ("false", "0", "no", "off")

    cli_config = CliConfig(
        builtin_tools=cli_raw.get("builtin_tools", True),
        max_tool_iterations=int(cli_raw.get("max_tool_iterations", 50)),
        context_warn_tokens=context_warn_tokens,
        context_auto_compact_tokens=context_auto_compact_tokens,
        tool_dedup=tool_dedup,
    )

    identity_raw = raw.get("identity", {})
    identity_user_id = identity_raw.get("user_id") or os.environ.get("AI_CHAT_USER_ID", "")
    identity_display_name = identity_raw.get("display_name") or os.environ.get("AI_CHAT_DISPLAY_NAME", "")
    identity_public_key = identity_raw.get("public_key") or os.environ.get("AI_CHAT_PUBLIC_KEY", "")
    identity_private_key = identity_raw.get("private_key") or os.environ.get("AI_CHAT_PRIVATE_KEY", "")

    identity: UserIdentity | None = None
    if identity_user_id:
        identity = UserIdentity(
            user_id=identity_user_id,
            display_name=identity_display_name,
            public_key=identity_public_key,
            private_key=identity_private_key,
        )

    emb_raw = raw.get("embeddings", {})
    emb_enabled = str(emb_raw.get("enabled", os.environ.get("AI_CHAT_EMBEDDINGS_ENABLED", "true"))).lower() not in (
        "false",
        "0",
        "no",
    )
    emb_model = emb_raw.get("model") or os.environ.get("AI_CHAT_EMBEDDINGS_MODEL", "text-embedding-3-small")
    emb_dimensions = int(emb_raw.get("dimensions") or os.environ.get("AI_CHAT_EMBEDDINGS_DIMENSIONS", "1536"))
    emb_dimensions = max(1, min(emb_dimensions, 4096))
    emb_base_url = emb_raw.get("base_url") or os.environ.get("AI_CHAT_EMBEDDINGS_BASE_URL", "")
    emb_api_key = emb_raw.get("api_key") or os.environ.get("AI_CHAT_EMBEDDINGS_API_KEY", "")
    emb_api_key_command = emb_raw.get("api_key_command") or os.environ.get("AI_CHAT_EMBEDDINGS_API_KEY_COMMAND", "")

    embeddings_config = EmbeddingsConfig(
        enabled=emb_enabled,
        model=emb_model,
        dimensions=emb_dimensions,
        base_url=emb_base_url,
        api_key=emb_api_key,
        api_key_command=emb_api_key_command,
    )

    safety_raw = raw.get("safety", {})
    safety_enabled = str(safety_raw.get("enabled", os.environ.get("AI_CHAT_SAFETY_ENABLED", "true"))).lower() not in (
        "false",
        "0",
        "no",
    )
    safety_timeout = int(safety_raw.get("approval_timeout", 120))
    safety_timeout = max(10, min(safety_timeout, 600))
    bash_raw = safety_raw.get("bash", {})
    bash_safety_enabled = str(bash_raw.get("enabled", "true")).lower() not in ("false", "0", "no")
    wf_raw = safety_raw.get("write_file", {})
    wf_safety_enabled = str(wf_raw.get("enabled", "true")).lower() not in ("false", "0", "no")
    safety_approval_mode = str(
        safety_raw.get("approval_mode", os.environ.get("AI_CHAT_SAFETY_APPROVAL_MODE", "ask_for_writes"))
    ).strip()
    safety_custom_patterns = safety_raw.get("custom_patterns", [])
    if not isinstance(safety_custom_patterns, list):
        safety_custom_patterns = []
    safety_sensitive_paths = safety_raw.get("sensitive_paths", [])
    if not isinstance(safety_sensitive_paths, list):
        safety_sensitive_paths = []
    safety_allowed_tools = safety_raw.get("allowed_tools", [])
    if not isinstance(safety_allowed_tools, list):
        safety_allowed_tools = []
    safety_denied_tools = safety_raw.get("denied_tools", [])
    if not isinstance(safety_denied_tools, list):
        safety_denied_tools = []
    safety_tool_tiers = safety_raw.get("tool_tiers", {})
    if not isinstance(safety_tool_tiers, dict):
        safety_tool_tiers = {}

    sa_raw = safety_raw.get("subagent", {})
    if not isinstance(sa_raw, dict):
        sa_raw = {}

    def _sa_int(key: str, default: int, lo: int, hi: int) -> int:
        try:
            val = int(sa_raw.get(key, default))
        except (ValueError, TypeError):
            val = default
        return max(lo, min(val, hi))

    subagent_config = SubagentConfig(
        max_concurrent=_sa_int("max_concurrent", 5, 1, 20),
        max_total=_sa_int("max_total", 10, 1, 50),
        max_depth=_sa_int("max_depth", 3, 1, 10),
        max_iterations=_sa_int("max_iterations", 15, 1, 100),
        timeout=_sa_int("timeout", 120, 10, 600),
        max_output_chars=_sa_int("max_output_chars", 4000, 100, 100_000),
        max_prompt_chars=_sa_int("max_prompt_chars", 32_000, 100, 100_000),
    )

    safety_config = SafetyConfig(
        enabled=safety_enabled,
        approval_mode=safety_approval_mode,
        approval_timeout=safety_timeout,
        bash=SafetyToolConfig(enabled=bash_safety_enabled),
        write_file=SafetyToolConfig(enabled=wf_safety_enabled),
        custom_patterns=[str(p) for p in safety_custom_patterns],
        sensitive_paths=[str(p) for p in safety_sensitive_paths],
        allowed_tools=[str(t) for t in safety_allowed_tools],
        denied_tools=[str(t) for t in safety_denied_tools],
        tool_tiers={str(k): str(v) for k, v in safety_tool_tiers.items()},
        subagent=subagent_config,
    )

    return AppConfig(
        ai=ai,
        app=app_settings,
        mcp_servers=mcp_servers,
        shared_databases=shared_databases,
        cli=cli_config,
        identity=identity,
        embeddings=embeddings_config,
        safety=safety_config,
    )


def ensure_identity(config_path: Path | None = None) -> UserIdentity:
    """Ensure config has an identity section; auto-generate if missing.

    Returns the UserIdentity (existing or newly created).
    """
    import getpass

    import yaml

    from .identity import generate_identity

    path = config_path or _get_config_path()
    raw: dict[str, Any] = {}
    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

    identity_raw = raw.get("identity", {})
    if identity_raw.get("user_id") and identity_raw.get("private_key"):
        return UserIdentity(
            user_id=identity_raw["user_id"],
            display_name=identity_raw.get("display_name", ""),
            public_key=identity_raw.get("public_key", ""),
            private_key=identity_raw.get("private_key", ""),
        )

    # Partial identity (user_id but no private_key) — repair by generating keypair
    if identity_raw.get("user_id") and not identity_raw.get("private_key"):
        from .identity import generate_identity

        fresh = generate_identity(identity_raw.get("display_name", ""))
        identity_raw["private_key"] = fresh["private_key"]
        identity_raw["public_key"] = fresh["public_key"]
        raw["identity"] = identity_raw

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(raw, f, default_flow_style=False, sort_keys=False)
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

        return UserIdentity(
            user_id=identity_raw["user_id"],
            display_name=identity_raw.get("display_name", ""),
            public_key=identity_raw["public_key"],
            private_key=identity_raw["private_key"],
        )

    try:
        display_name = getpass.getuser()
    except Exception:
        display_name = "user"

    identity_data = generate_identity(display_name)
    raw["identity"] = identity_data

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(raw, f, default_flow_style=False, sort_keys=False)
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    return UserIdentity(
        user_id=identity_data["user_id"],
        display_name=identity_data["display_name"],
        public_key=identity_data["public_key"],
        private_key=identity_data["private_key"],
    )


_SAFE_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")


def write_allowed_tool(tool_name: str, config_path: Path | None = None) -> None:
    """Append a tool name to safety.allowed_tools in the config file.

    Preserves existing config structure. Creates the safety section if missing.
    Uses advisory file locking to prevent concurrent writes from corrupting the file.
    """
    try:
        import fcntl

        _has_fcntl = True
    except ImportError:
        _has_fcntl = False

    if not _SAFE_TOOL_NAME_RE.match(tool_name):
        raise ValueError(f"Invalid tool name format: {tool_name!r}")

    path = config_path or _get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    def _read_modify_write() -> None:
        raw: dict[str, Any] = {}
        if path.exists():
            with open(path) as f:
                raw = yaml.safe_load(f) or {}

        safety_section = raw.setdefault("safety", {})
        allowed = safety_section.setdefault("allowed_tools", [])
        if not isinstance(allowed, list):
            allowed = []
            safety_section["allowed_tools"] = allowed

        if tool_name not in allowed:
            allowed.append(tool_name)

            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(raw, f, default_flow_style=False, sort_keys=False)
            try:
                path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass

    if _has_fcntl:
        lock_path = path.with_suffix(".lock")
        with open(lock_path, "w") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX)
            try:
                _read_modify_write()
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)
    else:
        _read_modify_write()

"""Configuration loader: YAML file with environment variable fallbacks."""

from __future__ import annotations

import logging
import os
import re
import stat
from dataclasses import dataclass, field
from dataclasses import field as _dc_field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_UNSET = object()  # sentinel distinguishing "not set" from None/False/0

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
    "ask_user": "Ask the user a question and wait for their response. Use instead of asking in text output.",
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
- IMPORTANT: When you need to ask the user a question, you MUST use the ask_user tool. Do NOT \
ask questions in your text output — the user cannot respond to text mid-turn. The ask_user tool \
pauses execution and waits for a response before continuing.
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
    request_timeout: int = 120  # seconds; overall stream timeout
    connect_timeout: int = 5  # seconds; TCP connect timeout
    write_timeout: int = 30  # seconds; time to send request body
    pool_timeout: int = 10  # seconds; wait for free connection from pool
    first_token_timeout: int = 30  # seconds; max wait for first token after connect
    chunk_stall_timeout: int = 30  # seconds; max silence between chunks mid-stream
    retry_max_attempts: int = 3  # retries on transient errors (0 = disabled)
    retry_backoff_base: float = 1.0  # seconds; base for exponential backoff
    narration_cadence: int = 5  # progress updates every N tool calls; 0 = disabled
    max_tools: int = 128  # hard cap on tools per request; 0 = unlimited
    temperature: float | None = None  # None = provider default; 0.0-2.0
    top_p: float | None = None  # None = provider default; 0.0-1.0
    seed: int | None = None  # None = provider default; any int for deterministic output
    allowed_domains: list[str] = field(default_factory=list)  # empty = no restriction
    block_localhost_api: bool = False  # when True, reject loopback/localhost base_url


@dataclass
class McpServerConfig:
    name: str
    transport: str  # "stdio" or "sse"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0  # seconds; connection timeout per server
    tools_include: list[str] = field(default_factory=list)  # allowlist; fnmatch patterns
    tools_exclude: list[str] = field(default_factory=list)  # blocklist; fnmatch patterns
    trust_level: str = "untrusted"  # "trusted" or "untrusted"; controls defensive prompt envelopes on tool results

    def __post_init__(self) -> None:
        if self.trust_level not in ("trusted", "untrusted"):
            raise ValueError(f"trust_level must be 'trusted' or 'untrusted', got {self.trust_level!r}")


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
class PlanningConfig:
    enabled: bool = True
    auto_threshold_tools: int = 15
    auto_mode: str = "off"  # "off", "suggest", or "auto"


@dataclass
class BudgetConfig:
    """Token budget enforcement for denial-of-wallet prevention."""

    enabled: bool = False
    max_tokens_per_request: int = 0  # 0 = unlimited
    max_tokens_per_conversation: int = 0  # 0 = unlimited
    max_tokens_per_day: int = 0  # 0 = unlimited
    warn_threshold_percent: int = 80  # emit warning at this % of limit
    action_on_exceed: str = "block"  # "block" or "warn"


@dataclass
class UsageConfig:
    """Token usage tracking and cost estimation settings."""

    week_days: int = 7  # number of days in a "week" period
    month_days: int = 30  # number of days in a "month" period
    model_costs: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            "gpt-4o": {"input": 2.50, "output": 10.00},
            "gpt-4o-mini": {"input": 0.15, "output": 0.60},
            "gpt-4.1": {"input": 2.00, "output": 8.00},
            "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
            "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
            "o3": {"input": 2.00, "output": 8.00},
            "o3-mini": {"input": 1.10, "output": 4.40},
            "o4-mini": {"input": 1.10, "output": 4.40},
            "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
            "claude-opus-4-20250514": {"input": 15.00, "output": 75.00},
            "claude-haiku-4-20250514": {"input": 0.80, "output": 4.00},
        }
    )  # per 1M tokens
    budgets: BudgetConfig = field(default_factory=BudgetConfig)


@dataclass
class SkillsConfig:
    auto_invoke: bool = True  # let the AI auto-invoke skills from natural language


@dataclass
class CliConfig:
    builtin_tools: bool = True
    max_tool_iterations: int = 50
    context_warn_tokens: int = 80_000
    context_auto_compact_tokens: int = 100_000
    tool_dedup: bool = True  # collapse consecutive similar tool calls; False = show all
    retry_delay: float = 5.0  # seconds between CLI auto-retry countdown ticks
    max_retries: int = 3  # max CLI auto-retry attempts for retryable errors
    esc_hint_delay: float = 3.0  # seconds before showing "esc to cancel" hint
    stall_display_threshold: float = 5.0  # seconds of chunk silence before showing "stalled"
    stall_warning_threshold: float = 15.0  # seconds before showing full stall warning
    tool_output_max_chars: int = 2000  # max chars per tool result before truncation
    file_reference_max_chars: int = 100_000  # max chars from @file references
    model_context_window: int = 128_000  # model context window size for usage bar
    planning: PlanningConfig = field(default_factory=PlanningConfig)
    usage: UsageConfig = field(default_factory=UsageConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)


@dataclass
class UserIdentity:
    user_id: str
    display_name: str
    public_key: str  # PEM
    private_key: str  # PEM


@dataclass
class EmbeddingsConfig:
    enabled: bool | None = None  # None = auto-detect at startup, True = force-enable, False = force-disable
    provider: str = "local"  # "local" (fastembed) or "api" (OpenAI-compatible)
    model: str = "text-embedding-3-small"
    dimensions: int = 0  # 0 = auto-detect from provider/model
    local_model: str = "BAAI/bge-small-en-v1.5"
    base_url: str = ""
    api_key: str = ""
    api_key_command: str = ""


@dataclass
class SafetyToolConfig:
    enabled: bool = True


@dataclass
class OsSandboxConfig:
    """OS-level sandbox controls (Win32 Job Objects on Windows, no-op elsewhere)."""

    enabled: bool | None = None  # None = auto-detect (True on Windows)
    max_memory_mb: int = 512
    max_processes: int = 10
    cpu_time_limit: int | None = None  # CPU seconds, None = no limit

    _MIN_MEMORY_MB: int = field(default=64, init=False, repr=False)
    _MIN_PROCESSES: int = field(default=1, init=False, repr=False)
    _MAX_PROCESSES: int = field(default=1000, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_memory_mb < self._MIN_MEMORY_MB:
            logger.warning(
                "sandbox max_memory_mb=%d below minimum (%d), clamping",
                self.max_memory_mb,
                self._MIN_MEMORY_MB,
            )
            object.__setattr__(self, "max_memory_mb", self._MIN_MEMORY_MB)
        if self.max_processes < self._MIN_PROCESSES:
            object.__setattr__(self, "max_processes", self._MIN_PROCESSES)
        if self.max_processes > self._MAX_PROCESSES:
            object.__setattr__(self, "max_processes", self._MAX_PROCESSES)
        if self.cpu_time_limit is not None and self.cpu_time_limit < 1:
            object.__setattr__(self, "cpu_time_limit", 1)

    @property
    def is_enabled(self) -> bool:
        """Resolve enabled state: None means auto-detect (True on Windows)."""
        if self.enabled is None:
            import sys

            return sys.platform == "win32"
        return self.enabled


@dataclass
class BashSandboxConfig:
    """Bash tool sandboxing controls. All fields have safe defaults."""

    enabled: bool = True
    timeout: int = 120  # per-command timeout in seconds
    max_output_chars: int = 100_000
    blocked_paths: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    blocked_commands: list[str] = field(default_factory=list)
    allow_network: bool = True
    allow_package_install: bool = True
    log_all_commands: bool = False
    sandbox: OsSandboxConfig = field(default_factory=OsSandboxConfig)

    _MIN_TIMEOUT: int = field(default=1, init=False, repr=False)
    _MAX_TIMEOUT: int = field(default=600, init=False, repr=False)
    _MIN_OUTPUT: int = field(default=1000, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.timeout < self._MIN_TIMEOUT:
            logger.warning("bash timeout=%d below minimum (%d), clamping", self.timeout, self._MIN_TIMEOUT)
            object.__setattr__(self, "timeout", self._MIN_TIMEOUT)
        if self.timeout > self._MAX_TIMEOUT:
            logger.warning("bash timeout=%d above maximum (%d), clamping", self.timeout, self._MAX_TIMEOUT)
            object.__setattr__(self, "timeout", self._MAX_TIMEOUT)
        if self.max_output_chars < self._MIN_OUTPUT:
            logger.warning(
                "bash max_output_chars=%d below minimum (%d), clamping",
                self.max_output_chars,
                self._MIN_OUTPUT,
            )
            object.__setattr__(self, "max_output_chars", self._MIN_OUTPUT)


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
class ToolRateLimitConfig:
    max_calls_per_minute: int = 0
    max_calls_per_conversation: int = 0
    max_consecutive_failures: int = 5
    action: str = "block"


@dataclass
class DlpPatternConfig:
    """A single DLP detection rule."""

    name: str = ""
    pattern: str = ""
    description: str = ""


@dataclass
class DlpConfig:
    """Data Loss Prevention scanning configuration."""

    enabled: bool = False
    scan_output: bool = True
    scan_input: bool = False  # Reserved for future use
    action: str = "redact"  # "redact", "block", "warn"
    patterns: list[DlpPatternConfig] = field(default_factory=list)  # Replaces built-in patterns
    custom_patterns: list[DlpPatternConfig] = field(default_factory=list)  # Appended to patterns
    redaction_string: str = "[REDACTED]"
    log_detections: bool = True

    def __post_init__(self) -> None:
        if self.action not in ("redact", "block", "warn"):
            logger.warning("Invalid DLP action '%s', defaulting to 'redact'", self.action)
            object.__setattr__(self, "action", "redact")


@dataclass
class PromptInjectionConfig:
    """Prompt injection detection configuration."""

    enabled: bool = False
    action: str = "warn"  # "block", "warn", "log"
    canary_length: int = 16  # bytes of randomness for canary token
    detect_encoding_attacks: bool = True
    detect_instruction_override: bool = True
    heuristic_threshold: float = 0.7  # minimum confidence to trigger action
    log_detections: bool = True

    def __post_init__(self) -> None:
        if self.action not in ("block", "warn", "log"):
            logger.warning("Invalid injection detection action '%s', defaulting to 'warn'", self.action)
            object.__setattr__(self, "action", "warn")
        if self.canary_length < 8:
            object.__setattr__(self, "canary_length", 8)
        elif self.canary_length > 64:
            object.__setattr__(self, "canary_length", 64)
        if self.heuristic_threshold < 0.0:
            object.__setattr__(self, "heuristic_threshold", 0.0)
        elif self.heuristic_threshold > 1.0:
            object.__setattr__(self, "heuristic_threshold", 1.0)


@dataclass
class OutputFilterPatternConfig:
    """A custom output filter pattern rule."""

    name: str = ""
    pattern: str = ""
    description: str = ""


@dataclass
class OutputFilterConfig:
    """Output content filter configuration (system prompt leak detection + custom patterns)."""

    enabled: bool = False
    system_prompt_leak_detection: bool = True
    leak_threshold: float = 0.4
    custom_patterns: list[OutputFilterPatternConfig] = field(default_factory=list)
    action: str = "warn"  # "warn", "block", "redact"
    redaction_string: str = "[FILTERED]"
    log_detections: bool = True

    def __post_init__(self) -> None:
        if self.action not in ("warn", "block", "redact"):
            logger.warning("Invalid output_filter action '%s', defaulting to 'warn'", self.action)
            object.__setattr__(self, "action", "warn")
        if not 0.0 < self.leak_threshold <= 1.0:
            logger.warning("Invalid leak_threshold %s, defaulting to 0.4", self.leak_threshold)
            object.__setattr__(self, "leak_threshold", 0.4)


@dataclass
class SafetyConfig:
    enabled: bool = True
    approval_mode: str = "ask_for_writes"
    approval_timeout: int = 120
    bash: BashSandboxConfig = field(default_factory=BashSandboxConfig)
    write_file: SafetyToolConfig = field(default_factory=SafetyToolConfig)
    custom_patterns: list[str] = field(default_factory=list)
    sensitive_paths: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    tool_tiers: dict[str, str] = field(default_factory=dict)
    read_only: bool = False
    subagent: SubagentConfig = field(default_factory=SubagentConfig)
    tool_rate_limit: ToolRateLimitConfig = field(default_factory=ToolRateLimitConfig)
    dlp: DlpConfig = field(default_factory=DlpConfig)
    prompt_injection: PromptInjectionConfig = field(default_factory=PromptInjectionConfig)
    output_filter: OutputFilterConfig = field(default_factory=OutputFilterConfig)


@dataclass
class RagConfig:
    """Retrieval-augmented generation settings."""

    enabled: bool = True  # auto-enabled when embeddings are available
    max_chunks: int = 10  # top-K chunks to retrieve per query
    max_tokens: int = 2000  # token budget for injected context (chars/4 estimate)
    similarity_threshold: float = 0.5  # max cosine distance; lower = stricter matching
    include_sources: bool = True  # search source chunks
    include_conversations: bool = True  # search past conversation messages
    exclude_current: bool = True  # exclude current conversation from results


@dataclass
class CodebaseIndexConfig:
    """Tree-sitter codebase index settings."""

    enabled: bool = True  # auto-enabled; degrades gracefully without tree-sitter
    map_tokens: int = 1000  # token budget for the injected codebase map
    languages: list[str] = field(default_factory=list)  # auto-detect if empty
    exclude_dirs: list[str] = field(
        default_factory=lambda: [
            "node_modules",
            ".git",
            "__pycache__",
            "venv",
            ".venv",
            "dist",
            "build",
            ".tox",
            ".mypy_cache",
            ".pytest_cache",
            "egg-info",
        ]
    )


@dataclass
class ProxyConfig:
    enabled: bool = False  # opt-in; must be explicitly enabled
    allowed_origins: list[str] = field(default_factory=list)


@dataclass
class ReferencesConfig:
    """Paths to external instruction, rule, and skill files.

    All paths are resolved relative to the config file that declares them.
    Team and project configs can use this to share instructions, rules,
    and skills across the team or per project.
    """

    instructions: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)


@dataclass
class SessionConfig:
    """Session management and network access control settings."""

    store: str = "memory"  # "memory" or "sqlite"
    max_concurrent_sessions: int = 0  # 0 = unlimited
    idle_timeout: int = 1800  # seconds (30 minutes)
    absolute_timeout: int = 43200  # seconds (12 hours)
    allowed_ips: list[str] = field(default_factory=list)  # CIDR or exact; empty = allow all

    _MIN_IDLE_TIMEOUT: int = field(default=60, init=False, repr=False)
    _MIN_ABSOLUTE_TIMEOUT: int = field(default=300, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.idle_timeout < self._MIN_IDLE_TIMEOUT:
            logger.warning(
                "idle_timeout=%d is below minimum (%d), clamping",
                self.idle_timeout,
                self._MIN_IDLE_TIMEOUT,
            )
            object.__setattr__(self, "idle_timeout", self._MIN_IDLE_TIMEOUT)
        if self.absolute_timeout < self._MIN_ABSOLUTE_TIMEOUT:
            logger.warning(
                "absolute_timeout=%d is below minimum (%d), clamping",
                self.absolute_timeout,
                self._MIN_ABSOLUTE_TIMEOUT,
            )
            object.__setattr__(self, "absolute_timeout", self._MIN_ABSOLUTE_TIMEOUT)


@dataclass
class StorageConfig:
    """Data retention and encryption at rest settings."""

    retention_days: int = 0  # 0 = disabled (keep forever)
    retention_check_interval: int = 3600  # seconds between retention checks
    purge_attachments: bool = True  # also delete attachment files on disk
    purge_embeddings: bool = True  # also purge orphaned embeddings
    encrypt_at_rest: bool = False  # requires sqlcipher3 optional dependency
    encryption_kdf: str = "hkdf-sha256"  # key derivation from identity key

    _MIN_RETENTION_INTERVAL: int = field(default=60, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.retention_check_interval < self._MIN_RETENTION_INTERVAL:
            logger.warning(
                "retention_check_interval=%d is below minimum (%d), clamping",
                self.retention_check_interval,
                self._MIN_RETENTION_INTERVAL,
            )
            object.__setattr__(self, "retention_check_interval", self._MIN_RETENTION_INTERVAL)


@dataclass
class AuditConfig:
    """Structured audit log settings."""

    enabled: bool = False
    log_path: str = ""  # empty = default to data_dir/audit/
    tamper_protection: str = "hmac"  # "none" or "hmac"
    rotation: str = "daily"  # "daily" or "size"
    rotate_size_bytes: int = 10_485_760  # 10 MB; only used when rotation=size
    retention_days: int = 90  # 0 = keep forever
    redact_content: bool = True  # log metadata only, strip message/tool content
    events: dict[str, bool] = field(
        default_factory=lambda: {
            "auth": True,
            "tool_calls": True,
            "dlp": True,
            "output_filter": True,
        }
    )


@dataclass
class ComplianceRule:
    """A single declarative compliance rule evaluated against the final config."""

    field: str  # dot-path, e.g. "safety.approval_mode"
    message: str = ""  # human-readable violation message
    must_be: Any = _UNSET
    must_not_be: Any = _UNSET
    must_match: str = ""  # regex pattern
    must_not_be_empty: bool = False
    must_contain: Any = _UNSET
    _compiled_pattern: Any = _dc_field(default=None, repr=False, compare=False)


@dataclass
class PackSourceConfig:
    """A single git-based pack source repository."""

    url: str
    branch: str = "main"
    refresh_interval: int = 30  # minutes; 0 = manual only

    _MIN_REFRESH: int = field(default=5, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.refresh_interval != 0 and self.refresh_interval < self._MIN_REFRESH:
            logger.warning(
                "refresh_interval=%d is below minimum (%d), clamping",
                self.refresh_interval,
                self._MIN_REFRESH,
            )
            self.refresh_interval = self._MIN_REFRESH


@dataclass
class ComplianceConfig:
    """Declarative rules engine for configuration governance."""

    rules: list[ComplianceRule] = field(default_factory=list)


@dataclass
class AppConfig:
    ai: AIConfig
    app: AppSettings = field(default_factory=AppSettings)
    mcp_servers: list[McpServerConfig] = field(default_factory=list)
    mcp_tool_warning_threshold: int = 40  # warn when total MCP tools exceed this; 0 = disabled
    shared_databases: list[SharedDatabaseConfig] = field(default_factory=list)
    cli: CliConfig = field(default_factory=CliConfig)
    identity: UserIdentity | None = None
    references: ReferencesConfig = field(default_factory=ReferencesConfig)
    embeddings: EmbeddingsConfig = field(default_factory=EmbeddingsConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    rag: RagConfig = field(default_factory=RagConfig)
    codebase_index: CodebaseIndexConfig = field(default_factory=CodebaseIndexConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)
    compliance: ComplianceConfig = field(default_factory=ComplianceConfig)
    pack_sources: list[PackSourceConfig] = field(default_factory=list)


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


def load_config(
    config_path: Path | None = None,
    *,
    team_config_path: Path | None = None,
    project_config_path: Path | None = None,
    space_config: dict[str, Any] | None = None,
    working_dir: str | None = None,
    interactive: bool = False,
) -> tuple[AppConfig, list[str]]:
    """Load configuration with optional team, space, and project config layers.

    Returns ``(AppConfig, enforced_fields)`` where *enforced_fields* is
    the list of dot-paths from the team config's ``enforce`` section.

    Layer precedence (highest wins):
      env vars > project config > space config > personal config > team config > defaults
    Enforced team fields override everything.
    """
    raw: dict[str, Any] = {}
    path = config_path or _get_config_path()

    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

    # Validate raw config before parsing into dataclasses
    from .services.config_validator import validate_config

    validation = validate_config(raw)
    if not validation.is_valid:
        raise ValueError(f"Invalid configuration in {path}:\n{validation.format_errors()}")
    if validation.has_warnings:
        for w in validation.errors:
            if w.severity == "warning":
                logger.warning("Config %s: %s — %s", path, w.path, w.message)

    # --- Team config layer ---------------------------------------------------
    team_raw: dict[str, Any] = {}
    enforced_fields: list[str] = []

    from .services.team_config import apply_enforcement, deep_merge, discover_team_config, load_team_config

    team_path = discover_team_config(
        cli_path=team_config_path,
        env_path=os.environ.get("AI_CHAT_TEAM_CONFIG"),
        personal_path=raw.get("team_config_path"),
    )
    if team_path:
        data_dir = path.parent if path.exists() else None
        team_raw, enforced_fields = load_team_config(team_path, data_dir, interactive=interactive)
        if team_raw:
            # Team is the base, personal overlays on top
            raw = deep_merge(team_raw, raw)
            # Re-apply enforced fields so personal values can't override them
            raw = apply_enforcement(raw, team_raw, enforced_fields)

    # --- Space config layer --------------------------------------------------
    if space_config and isinstance(space_config, dict):
        raw = deep_merge(raw, space_config)
        if enforced_fields and team_raw:
            raw = apply_enforcement(raw, team_raw, enforced_fields)

    # --- Project config layer ------------------------------------------------
    from .services.project_config import discover_project_config, load_project_config

    # Only auto-discover project config when working_dir is explicitly set
    # (prevents accidentally loading configs from the test runner's cwd)
    proj_path = project_config_path
    if not proj_path and working_dir:
        proj_path = discover_project_config(working_dir)
    if proj_path:
        data_dir_for_trust = path.parent if path.exists() else None
        proj_raw, _required_keys = load_project_config(proj_path, data_dir_for_trust, interactive=interactive)
        if proj_raw:
            raw = deep_merge(raw, proj_raw)
            # Re-apply enforced fields so project config can't override them
            if enforced_fields and team_raw:
                raw = apply_enforcement(raw, team_raw, enforced_fields)

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
        _raw_connect = ai_raw.get("connect_timeout", os.environ.get("AI_CHAT_CONNECT_TIMEOUT", 5))
        connect_timeout = max(1, min(30, int(_raw_connect)))
    except (ValueError, TypeError):
        connect_timeout = 5

    try:
        _raw_write = ai_raw.get("write_timeout", os.environ.get("AI_CHAT_WRITE_TIMEOUT", 30))
        write_timeout = max(5, min(120, int(_raw_write)))
    except (ValueError, TypeError):
        write_timeout = 30

    try:
        _raw_pool = ai_raw.get("pool_timeout", os.environ.get("AI_CHAT_POOL_TIMEOUT", 10))
        pool_timeout = max(1, min(60, int(_raw_pool)))
    except (ValueError, TypeError):
        pool_timeout = 10

    try:
        _raw_first_token = ai_raw.get("first_token_timeout", os.environ.get("AI_CHAT_FIRST_TOKEN_TIMEOUT", 30))
        first_token_timeout = max(5, min(120, int(_raw_first_token)))
    except (ValueError, TypeError):
        first_token_timeout = 30

    try:
        _raw_chunk_stall = ai_raw.get("chunk_stall_timeout", os.environ.get("AI_CHAT_CHUNK_STALL_TIMEOUT", 30))
        chunk_stall_timeout = max(10, min(600, int(_raw_chunk_stall)))
    except (ValueError, TypeError):
        chunk_stall_timeout = 30

    try:
        _raw_retry_attempts = ai_raw.get("retry_max_attempts", os.environ.get("AI_CHAT_RETRY_MAX_ATTEMPTS", 3))
        retry_max_attempts = max(0, min(10, int(_raw_retry_attempts)))
    except (ValueError, TypeError):
        retry_max_attempts = 3

    try:
        _raw_retry_backoff = ai_raw.get("retry_backoff_base", os.environ.get("AI_CHAT_RETRY_BACKOFF_BASE", 1.0))
        retry_backoff_base = max(0.1, min(30.0, float(_raw_retry_backoff)))
    except (ValueError, TypeError):
        retry_backoff_base = 1.0

    try:
        narration_cadence = int(ai_raw.get("narration_cadence", os.environ.get("AI_CHAT_NARRATION_CADENCE", 5)))
        narration_cadence = max(0, narration_cadence)
    except (ValueError, TypeError):
        narration_cadence = 5

    try:
        max_tools = int(ai_raw.get("max_tools", os.environ.get("AI_CHAT_MAX_TOOLS", 128)))
        max_tools = max(0, max_tools)
    except (ValueError, TypeError):
        max_tools = 128

    _raw_temperature = ai_raw.get("temperature", os.environ.get("AI_CHAT_TEMPERATURE"))
    temperature: float | None = None
    if _raw_temperature is not None and str(_raw_temperature).strip() != "":
        try:
            temperature = max(0.0, min(2.0, float(_raw_temperature)))
        except (ValueError, TypeError):
            temperature = None

    _raw_top_p = ai_raw.get("top_p", os.environ.get("AI_CHAT_TOP_P"))
    top_p: float | None = None
    if _raw_top_p is not None and str(_raw_top_p).strip() != "":
        try:
            top_p = max(0.0, min(1.0, float(_raw_top_p)))
        except (ValueError, TypeError):
            top_p = None

    _raw_seed = ai_raw.get("seed", os.environ.get("AI_CHAT_SEED"))
    seed: int | None = None
    if _raw_seed is not None and str(_raw_seed).strip() != "":
        try:
            seed = int(_raw_seed)
        except (ValueError, TypeError):
            seed = None

    _raw_allowed_domains = ai_raw.get("allowed_domains", [])
    if not isinstance(_raw_allowed_domains, list):
        _raw_allowed_domains = []
    allowed_domains: list[str] = [str(d).strip() for d in _raw_allowed_domains if d]
    _env_allowed_domains = os.environ.get("AI_CHAT_ALLOWED_DOMAINS", "")
    if _env_allowed_domains:
        allowed_domains = [d.strip() for d in _env_allowed_domains.split(",") if d.strip()]

    _raw_block_localhost = ai_raw.get("block_localhost_api", os.environ.get("AI_CHAT_BLOCK_LOCALHOST_API", "false"))
    block_localhost_api = str(_raw_block_localhost).lower() not in ("false", "0", "no")

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
        connect_timeout=connect_timeout,
        write_timeout=write_timeout,
        pool_timeout=pool_timeout,
        first_token_timeout=first_token_timeout,
        chunk_stall_timeout=chunk_stall_timeout,
        retry_max_attempts=retry_max_attempts,
        retry_backoff_base=retry_backoff_base,
        narration_cadence=narration_cadence,
        max_tools=max_tools,
        temperature=temperature,
        top_p=top_p,
        seed=seed,
        allowed_domains=allowed_domains,
        block_localhost_api=block_localhost_api,
    )

    app_raw = raw.get("app", {})
    default_data_dir = str(_resolve_data_dir())
    data_dir = Path(os.path.expanduser(app_raw.get("data_dir", default_data_dir)))
    tls_raw = app_raw.get("tls", False)
    tls_enabled = str(tls_raw).lower() not in ("false", "0", "no")

    port_raw = app_raw.get("port") if "port" in app_raw else os.environ.get("AI_CHAT_PORT", 8080)
    try:
        port_val = int(port_raw)
    except (ValueError, TypeError):
        port_val = 8080
    port_val = max(1, min(65535, port_val))
    app_settings = AppSettings(
        host=app_raw.get("host", "127.0.0.1"),
        port=port_val,
        data_dir=data_dir,
        tls=tls_enabled,
    )

    mcp_servers: list[McpServerConfig] = []
    for srv in raw.get("mcp_servers", []):
        # Skip servers explicitly disabled by personal config (enabled: false).
        # This lets users opt out of team-defined servers without removing them.
        if not srv.get("enabled", True):
            logger.info("MCP server '%s' is disabled (enabled: false), skipping", srv.get("name", "?"))
            continue
        env_raw = srv.get("env", {})
        env: dict[str, str] = {}
        for k, v in env_raw.items():
            env[k] = os.path.expandvars(str(v))
        tools_include_raw = srv.get("tools_include", [])
        tools_include = [str(t) for t in tools_include_raw] if isinstance(tools_include_raw, list) else []
        tools_exclude_raw = srv.get("tools_exclude", [])
        tools_exclude = [str(t) for t in tools_exclude_raw] if isinstance(tools_exclude_raw, list) else []
        if tools_include and tools_exclude:
            logger.warning(
                "MCP server '%s': both tools_include and tools_exclude set; using include (ignoring exclude)",
                srv.get("name", "?"),
            )
            tools_exclude = []
        mcp_servers.append(
            McpServerConfig(
                name=srv["name"],
                transport=srv.get("transport", "stdio"),
                command=srv.get("command"),
                args=srv.get("args", []),
                url=srv.get("url"),
                env=env,
                timeout=float(srv.get("timeout", 30.0)),
                tools_include=tools_include,
                tools_exclude=tools_exclude,
                trust_level=srv.get("trust_level", "untrusted"),
            )
        )

    try:
        mcp_tool_warning_threshold = max(0, int(raw.get("mcp_tool_warning_threshold", 40)))
    except (ValueError, TypeError):
        mcp_tool_warning_threshold = 40

    shared_databases: list[SharedDatabaseConfig] = []
    for sdb in raw.get("shared_databases", []):
        if not sdb.get("enabled", True):
            logger.info("Shared database '%s' is disabled (enabled: false), skipping", sdb.get("name", "?"))
            continue
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

    try:
        retry_delay = max(1.0, min(60.0, float(cli_raw.get("retry_delay", 5.0))))
    except (ValueError, TypeError):
        retry_delay = 5.0
    try:
        max_retries = max(0, min(10, int(cli_raw.get("max_retries", 3))))
    except (ValueError, TypeError):
        max_retries = 3
    try:
        esc_hint_delay = max(0.0, float(cli_raw.get("esc_hint_delay", 3.0)))
    except (ValueError, TypeError):
        esc_hint_delay = 3.0
    try:
        stall_display_threshold = max(1.0, float(cli_raw.get("stall_display_threshold", 5.0)))
    except (ValueError, TypeError):
        stall_display_threshold = 5.0
    try:
        stall_warning_threshold = max(1.0, float(cli_raw.get("stall_warning_threshold", 15.0)))
    except (ValueError, TypeError):
        stall_warning_threshold = 15.0
    try:
        tool_output_max_chars = max(100, int(cli_raw.get("tool_output_max_chars", 2000)))
    except (ValueError, TypeError):
        tool_output_max_chars = 2000
    try:
        file_reference_max_chars = max(1000, min(10_000_000, int(cli_raw.get("file_reference_max_chars", 100_000))))
    except (ValueError, TypeError):
        file_reference_max_chars = 100_000
    try:
        model_context_window = max(1000, min(2_000_000, int(cli_raw.get("model_context_window", 128_000))))
    except (ValueError, TypeError):
        model_context_window = 128_000

    planning_raw = cli_raw.get("planning", {})
    if not isinstance(planning_raw, dict):
        planning_raw = {}
    planning_enabled = str(planning_raw.get("enabled", "true")).lower() not in ("false", "0", "no")
    try:
        planning_auto_threshold = max(0, int(planning_raw.get("auto_threshold_tools", 15)))
    except (ValueError, TypeError):
        planning_auto_threshold = 15
    planning_auto_mode = str(planning_raw.get("auto_mode", "off")).lower()
    if planning_auto_mode not in ("off", "suggest", "auto"):
        planning_auto_mode = "off"
    planning_config = PlanningConfig(
        enabled=planning_enabled,
        auto_threshold_tools=planning_auto_threshold,
        auto_mode=planning_auto_mode,
    )

    # Parse usage config
    usage_raw = cli_raw.get("usage", {})
    if not isinstance(usage_raw, dict):
        usage_raw = {}
    try:
        usage_week_days = max(1, int(usage_raw.get("week_days", 7)))
    except (ValueError, TypeError):
        usage_week_days = 7
    try:
        usage_month_days = max(1, int(usage_raw.get("month_days", 30)))
    except (ValueError, TypeError):
        usage_month_days = 30
    usage_model_costs = usage_raw.get("model_costs", {})
    if not isinstance(usage_model_costs, dict):
        usage_model_costs = {}
    usage_config = UsageConfig(
        week_days=usage_week_days,
        month_days=usage_month_days,
    )
    if usage_model_costs:
        # Merge user-provided costs with defaults (user overrides win)
        merged = dict(usage_config.model_costs)
        for model_name, costs in usage_model_costs.items():
            if isinstance(costs, dict):
                merged[str(model_name)] = {
                    "input": float(costs.get("input", 0)),
                    "output": float(costs.get("output", 0)),
                }
        usage_config.model_costs = merged

    # Parse budget config (under usage.budgets or top-level env vars)
    budgets_raw = usage_raw.get("budgets", {})
    if not isinstance(budgets_raw, dict):
        budgets_raw = {}
    budget_enabled_raw = budgets_raw.get("enabled", os.environ.get("AI_CHAT_BUDGET_ENABLED"))
    if budget_enabled_raw is not None:
        budget_enabled = str(budget_enabled_raw).lower() not in ("false", "0", "no")
    else:
        budget_enabled = False
    try:
        budget_max_per_request = max(
            0,
            int(
                budgets_raw.get(
                    "max_tokens_per_request",
                    os.environ.get("AI_CHAT_BUDGET_MAX_TOKENS_PER_REQUEST", 0),
                )
            ),
        )
    except (ValueError, TypeError):
        budget_max_per_request = 0
    try:
        budget_max_per_conversation = max(
            0,
            int(
                budgets_raw.get(
                    "max_tokens_per_conversation",
                    os.environ.get("AI_CHAT_BUDGET_MAX_TOKENS_PER_CONVERSATION", 0),
                )
            ),
        )
    except (ValueError, TypeError):
        budget_max_per_conversation = 0
    try:
        budget_max_per_day = max(
            0,
            int(
                budgets_raw.get(
                    "max_tokens_per_day",
                    os.environ.get("AI_CHAT_BUDGET_MAX_TOKENS_PER_DAY", 0),
                )
            ),
        )
    except (ValueError, TypeError):
        budget_max_per_day = 0
    try:
        budget_warn_pct = max(
            0,
            min(
                100,
                int(
                    budgets_raw.get(
                        "warn_threshold_percent",
                        os.environ.get("AI_CHAT_BUDGET_WARN_THRESHOLD_PERCENT", 80),
                    )
                ),
            ),
        )
    except (ValueError, TypeError):
        budget_warn_pct = 80
    budget_action = str(
        budgets_raw.get(
            "action_on_exceed",
            os.environ.get("AI_CHAT_BUDGET_ACTION_ON_EXCEED", "block"),
        )
    ).lower()
    if budget_action not in ("block", "warn"):
        budget_action = "block"
    usage_config.budgets = BudgetConfig(
        enabled=budget_enabled,
        max_tokens_per_request=budget_max_per_request,
        max_tokens_per_conversation=budget_max_per_conversation,
        max_tokens_per_day=budget_max_per_day,
        warn_threshold_percent=budget_warn_pct,
        action_on_exceed=budget_action,
    )

    skills_raw = cli_raw.get("skills", {})
    if not isinstance(skills_raw, dict):
        skills_raw = {}
    skills_auto_invoke = str(skills_raw.get("auto_invoke", "true")).lower() not in ("false", "0", "no")
    skills_config = SkillsConfig(auto_invoke=skills_auto_invoke)

    cli_config = CliConfig(
        builtin_tools=cli_raw.get("builtin_tools", True),
        max_tool_iterations=int(cli_raw.get("max_tool_iterations", 50)),
        context_warn_tokens=context_warn_tokens,
        context_auto_compact_tokens=context_auto_compact_tokens,
        tool_dedup=tool_dedup,
        retry_delay=retry_delay,
        max_retries=max_retries,
        esc_hint_delay=esc_hint_delay,
        stall_display_threshold=stall_display_threshold,
        stall_warning_threshold=stall_warning_threshold,
        tool_output_max_chars=tool_output_max_chars,
        file_reference_max_chars=file_reference_max_chars,
        model_context_window=model_context_window,
        planning=planning_config,
        usage=usage_config,
        skills=skills_config,
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
    _emb_enabled_raw = emb_raw.get("enabled", os.environ.get("AI_CHAT_EMBEDDINGS_ENABLED"))
    if _emb_enabled_raw is None:
        emb_enabled: bool | None = None  # auto-detect at startup
    else:
        emb_enabled = str(_emb_enabled_raw).lower() not in ("false", "0", "no")
    emb_provider = emb_raw.get("provider") or os.environ.get("AI_CHAT_EMBEDDINGS_PROVIDER", "local")
    emb_model = emb_raw.get("model") or os.environ.get("AI_CHAT_EMBEDDINGS_MODEL", "text-embedding-3-small")
    emb_local_model = emb_raw.get("local_model") or os.environ.get(
        "AI_CHAT_EMBEDDINGS_LOCAL_MODEL", "BAAI/bge-small-en-v1.5"
    )
    emb_dimensions_raw = emb_raw.get("dimensions") or os.environ.get("AI_CHAT_EMBEDDINGS_DIMENSIONS", "")
    if emb_dimensions_raw:
        emb_dimensions = int(emb_dimensions_raw)
        emb_dimensions = max(1, min(emb_dimensions, 4096))
    else:
        # Auto-detect: 0 means "use model default"
        emb_dimensions = 0
    emb_base_url = emb_raw.get("base_url") or os.environ.get("AI_CHAT_EMBEDDINGS_BASE_URL", "")
    emb_api_key = emb_raw.get("api_key") or os.environ.get("AI_CHAT_EMBEDDINGS_API_KEY", "")
    emb_api_key_command = emb_raw.get("api_key_command") or os.environ.get("AI_CHAT_EMBEDDINGS_API_KEY_COMMAND", "")

    embeddings_config = EmbeddingsConfig(
        enabled=emb_enabled,
        provider=emb_provider,
        model=emb_model,
        dimensions=emb_dimensions,
        local_model=emb_local_model,
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
    if not isinstance(bash_raw, dict):
        bash_raw = {}
    bash_safety_enabled = str(bash_raw.get("enabled", "true")).lower() not in ("false", "0", "no")

    def _bash_bool(key: str, env_key: str, default: bool) -> bool:
        return str(bash_raw.get(key, os.environ.get(env_key, str(default)))).lower() in ("true", "1", "yes")

    def _bash_int(key: str, env_key: str, default: int) -> int:
        try:
            return int(bash_raw.get(key, os.environ.get(env_key, default)))
        except (ValueError, TypeError):
            return default

    def _bash_list(key: str, env_key: str) -> list[str]:
        val = bash_raw.get(key)
        if val is None:
            env_val = os.environ.get(env_key, "")
            return [s.strip() for s in env_val.split(",") if s.strip()] if env_val else []
        if isinstance(val, list):
            return [str(v) for v in val]
        return []

    # Parse OS-level sandbox config (safety.bash.sandbox)
    sandbox_raw = bash_raw.get("sandbox", {})
    if not isinstance(sandbox_raw, dict):
        sandbox_raw = {}

    def _sandbox_int(key: str, env_key: str, default: int) -> int:
        try:
            return int(sandbox_raw.get(key, os.environ.get(env_key, default)))
        except (ValueError, TypeError):
            return default

    def _sandbox_optional_int(key: str, env_key: str) -> int | None:
        val = sandbox_raw.get(key)
        if val is None:
            env_val = os.environ.get(env_key)
            if env_val is None:
                return None
            val = env_val
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    sandbox_enabled_raw = sandbox_raw.get("enabled", os.environ.get("AI_CHAT_BASH_SANDBOX_ENABLED"))
    sandbox_enabled: bool | None = None
    if sandbox_enabled_raw is not None:
        sandbox_enabled = str(sandbox_enabled_raw).lower() in ("true", "1", "yes")

    os_sandbox = OsSandboxConfig(
        enabled=sandbox_enabled,
        max_memory_mb=_sandbox_int("max_memory_mb", "AI_CHAT_BASH_SANDBOX_MAX_MEMORY_MB", 512),
        max_processes=_sandbox_int("max_processes", "AI_CHAT_BASH_SANDBOX_MAX_PROCESSES", 10),
        cpu_time_limit=_sandbox_optional_int("cpu_time_limit", "AI_CHAT_BASH_SANDBOX_CPU_TIME_LIMIT"),
    )

    bash_sandbox = BashSandboxConfig(
        enabled=bash_safety_enabled,
        timeout=_bash_int("timeout", "AI_CHAT_BASH_TIMEOUT", 120),
        max_output_chars=_bash_int("max_output_chars", "AI_CHAT_BASH_MAX_OUTPUT", 100_000),
        blocked_paths=_bash_list("blocked_paths", "AI_CHAT_BASH_BLOCKED_PATHS"),
        allowed_paths=_bash_list("allowed_paths", "AI_CHAT_BASH_ALLOWED_PATHS"),
        blocked_commands=_bash_list("blocked_commands", "AI_CHAT_BASH_BLOCKED_COMMANDS"),
        allow_network=_bash_bool("allow_network", "AI_CHAT_BASH_ALLOW_NETWORK", True),
        allow_package_install=_bash_bool("allow_package_install", "AI_CHAT_BASH_ALLOW_PACKAGE_INSTALL", True),
        log_all_commands=_bash_bool("log_all_commands", "AI_CHAT_BASH_LOG_ALL_COMMANDS", False),
        sandbox=os_sandbox,
    )
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
    safety_read_only = str(safety_raw.get("read_only", os.environ.get("AI_CHAT_READ_ONLY", "false"))).lower() in (
        "true",
        "1",
        "yes",
    )

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

    trl_raw = safety_raw.get("tool_rate_limit", {})
    if not isinstance(trl_raw, dict):
        trl_raw = {}

    def _trl_int(key: str, default: int, lo: int, hi: int) -> int:
        try:
            val = int(trl_raw.get(key, default))
        except (ValueError, TypeError):
            val = default
        return max(lo, min(val, hi))

    trl_action = str(trl_raw.get("action", "block")).lower()
    if trl_action not in ("block", "warn"):
        trl_action = "block"

    tool_rate_limit_config = ToolRateLimitConfig(
        max_calls_per_minute=_trl_int("max_calls_per_minute", 0, 0, 100_000),
        max_calls_per_conversation=_trl_int("max_calls_per_conversation", 0, 0, 100_000),
        max_consecutive_failures=_trl_int("max_consecutive_failures", 5, 0, 1000),
        action=trl_action,
    )

    # DLP config
    dlp_raw = safety_raw.get("dlp", {})
    if not isinstance(dlp_raw, dict):
        dlp_raw = {}
    dlp_enabled = str(dlp_raw.get("enabled", os.environ.get("AI_CHAT_DLP_ENABLED", "false"))).lower() in (
        "true",
        "1",
        "yes",
    )
    dlp_scan_output = str(dlp_raw.get("scan_output", "true")).lower() not in ("false", "0", "no")
    dlp_scan_input = str(dlp_raw.get("scan_input", "false")).lower() in ("true", "1", "yes")
    dlp_action = str(dlp_raw.get("action", os.environ.get("AI_CHAT_DLP_ACTION", "redact"))).lower()
    if dlp_action not in ("redact", "block", "warn"):
        dlp_action = "redact"
    dlp_redaction_string = str(dlp_raw.get("redaction_string", "[REDACTED]"))
    dlp_log_detections = str(dlp_raw.get("log_detections", "true")).lower() not in ("false", "0", "no")

    dlp_patterns: list[DlpPatternConfig] = []
    for rule_raw in dlp_raw.get("patterns", []):
        if not isinstance(rule_raw, dict) or not rule_raw.get("name") or not rule_raw.get("pattern"):
            continue
        dlp_patterns.append(
            DlpPatternConfig(
                name=str(rule_raw["name"]),
                pattern=str(rule_raw["pattern"]),
                description=str(rule_raw.get("description", "")),
            )
        )
    dlp_custom: list[DlpPatternConfig] = []
    for rule_raw in dlp_raw.get("custom_patterns", []):
        if not isinstance(rule_raw, dict) or not rule_raw.get("name") or not rule_raw.get("pattern"):
            continue
        dlp_custom.append(
            DlpPatternConfig(
                name=str(rule_raw["name"]),
                pattern=str(rule_raw["pattern"]),
                description=str(rule_raw.get("description", "")),
            )
        )

    dlp_config = DlpConfig(
        enabled=dlp_enabled,
        scan_output=dlp_scan_output,
        scan_input=dlp_scan_input,
        action=dlp_action,
        patterns=dlp_patterns,
        custom_patterns=dlp_custom,
        redaction_string=dlp_redaction_string,
        log_detections=dlp_log_detections,
    )

    # Output filter config
    of_raw = safety_raw.get("output_filter", {})
    if not isinstance(of_raw, dict):
        of_raw = {}
    of_enabled = str(of_raw.get("enabled", os.environ.get("AI_CHAT_OUTPUT_FILTER_ENABLED", "false"))).lower() in (
        "true",
        "1",
        "yes",
    )
    of_leak_detection = str(of_raw.get("system_prompt_leak_detection", "true")).lower() not in ("false", "0", "no")
    try:
        of_leak_threshold = max(0.01, min(1.0, float(of_raw.get("leak_threshold", 0.4))))
    except (ValueError, TypeError):
        of_leak_threshold = 0.4
    of_action = str(of_raw.get("action", os.environ.get("AI_CHAT_OUTPUT_FILTER_ACTION", "warn"))).lower()
    if of_action not in ("warn", "block", "redact"):
        of_action = "warn"
    of_redaction_string = str(of_raw.get("redaction_string", "[FILTERED]"))
    of_log_detections = str(of_raw.get("log_detections", "true")).lower() not in ("false", "0", "no")

    of_custom: list[OutputFilterPatternConfig] = []
    for rule_raw in of_raw.get("custom_patterns", []):
        if not isinstance(rule_raw, dict) or not rule_raw.get("name") or not rule_raw.get("pattern"):
            continue
        of_custom.append(
            OutputFilterPatternConfig(
                name=str(rule_raw["name"]),
                pattern=str(rule_raw["pattern"]),
                description=str(rule_raw.get("description", "")),
            )
        )

    output_filter_config = OutputFilterConfig(
        enabled=of_enabled,
        system_prompt_leak_detection=of_leak_detection,
        leak_threshold=of_leak_threshold,
        custom_patterns=of_custom,
        action=of_action,
        redaction_string=of_redaction_string,
        log_detections=of_log_detections,
    )

    safety_config = SafetyConfig(
        enabled=safety_enabled,
        approval_mode=safety_approval_mode,
        approval_timeout=safety_timeout,
        bash=bash_sandbox,
        write_file=SafetyToolConfig(enabled=wf_safety_enabled),
        custom_patterns=[str(p) for p in safety_custom_patterns],
        sensitive_paths=[str(p) for p in safety_sensitive_paths],
        allowed_tools=[str(t) for t in safety_allowed_tools],
        denied_tools=[str(t) for t in safety_denied_tools],
        tool_tiers={str(k): str(v) for k, v in safety_tool_tiers.items()},
        read_only=safety_read_only,
        subagent=subagent_config,
        tool_rate_limit=tool_rate_limit_config,
        dlp=dlp_config,
        output_filter=output_filter_config,
    )

    # RAG config
    rag_raw = raw.get("rag", {})
    if not isinstance(rag_raw, dict):
        rag_raw = {}
    rag_enabled = str(rag_raw.get("enabled", os.environ.get("AI_CHAT_RAG_ENABLED", "true"))).lower() not in (
        "false",
        "0",
        "no",
    )
    try:
        rag_max_chunks = max(1, min(50, int(rag_raw.get("max_chunks", os.environ.get("AI_CHAT_RAG_MAX_CHUNKS", 10)))))
    except (ValueError, TypeError):
        rag_max_chunks = 10
    try:
        _raw_rag_tokens = rag_raw.get("max_tokens", os.environ.get("AI_CHAT_RAG_MAX_TOKENS", 2000))
        rag_max_tokens = max(100, min(20_000, int(_raw_rag_tokens)))
    except (ValueError, TypeError):
        rag_max_tokens = 2000
    try:
        _raw_rag_threshold = rag_raw.get(
            "similarity_threshold", os.environ.get("AI_CHAT_RAG_SIMILARITY_THRESHOLD", 0.5)
        )
        rag_threshold = max(0.0, min(2.0, float(_raw_rag_threshold)))
    except (ValueError, TypeError):
        rag_threshold = 0.5
    rag_include_sources = str(rag_raw.get("include_sources", "true")).lower() not in ("false", "0", "no")
    rag_include_conversations = str(rag_raw.get("include_conversations", "true")).lower() not in ("false", "0", "no")
    rag_exclude_current = str(rag_raw.get("exclude_current", "true")).lower() not in ("false", "0", "no")
    rag_config = RagConfig(
        enabled=rag_enabled,
        max_chunks=rag_max_chunks,
        max_tokens=rag_max_tokens,
        similarity_threshold=rag_threshold,
        include_sources=rag_include_sources,
        include_conversations=rag_include_conversations,
        exclude_current=rag_exclude_current,
    )

    # Proxy config
    proxy_raw = raw.get("proxy", {})
    if not isinstance(proxy_raw, dict):
        proxy_raw = {}
    proxy_enabled = str(proxy_raw.get("enabled", os.environ.get("AI_CHAT_PROXY_ENABLED", "false"))).lower() in (
        "true",
        "1",
        "yes",
    )
    proxy_origins_raw = proxy_raw.get("allowed_origins", [])
    if not isinstance(proxy_origins_raw, list):
        proxy_origins_raw = []
    proxy_origins: list[str] = []
    for o in proxy_origins_raw:
        origin = str(o).rstrip("/")
        if origin == "*" or not origin.startswith(("http://", "https://")):
            logger.warning("Ignoring invalid proxy allowed_origin: %s", origin)
            continue
        proxy_origins.append(origin)
    proxy_config = ProxyConfig(
        enabled=proxy_enabled,
        allowed_origins=proxy_origins,
    )

    # References (instructions, rules, skills from team/project configs)
    refs_raw = raw.get("references", {})
    if not isinstance(refs_raw, dict):
        refs_raw = {}
    refs_config = ReferencesConfig(
        instructions=[str(p) for p in refs_raw.get("instructions", []) if isinstance(p, str) and p],
        rules=[str(p) for p in refs_raw.get("rules", []) if isinstance(p, str) and p],
        skills=[str(p) for p in refs_raw.get("skills", []) if isinstance(p, str) and p],
    )

    # Storage config (retention + encryption)
    storage_raw = raw.get("storage", {})
    if not isinstance(storage_raw, dict):
        storage_raw = {}
    try:
        storage_retention_days = max(
            0,
            int(storage_raw.get("retention_days", os.environ.get("AI_CHAT_STORAGE_RETENTION_DAYS", 0))),
        )
    except (ValueError, TypeError):
        storage_retention_days = 0
    try:
        storage_check_interval = max(
            60,
            int(storage_raw.get("retention_check_interval", os.environ.get("AI_CHAT_STORAGE_CHECK_INTERVAL", 3600))),
        )
    except (ValueError, TypeError):
        storage_check_interval = 3600
    storage_purge_attachments = str(
        storage_raw.get("purge_attachments", os.environ.get("AI_CHAT_STORAGE_PURGE_ATTACHMENTS", "true"))
    ).lower() not in ("false", "0", "no")
    storage_purge_embeddings = str(
        storage_raw.get("purge_embeddings", os.environ.get("AI_CHAT_STORAGE_PURGE_EMBEDDINGS", "true"))
    ).lower() not in ("false", "0", "no")
    storage_encrypt = str(
        storage_raw.get("encrypt_at_rest", os.environ.get("AI_CHAT_STORAGE_ENCRYPT", "false"))
    ).lower() in ("true", "1", "yes")
    storage_kdf = str(storage_raw.get("encryption_kdf", "hkdf-sha256"))
    if storage_kdf not in ("hkdf-sha256",):
        storage_kdf = "hkdf-sha256"
    storage_config = StorageConfig(
        retention_days=storage_retention_days,
        retention_check_interval=storage_check_interval,
        purge_attachments=storage_purge_attachments,
        purge_embeddings=storage_purge_embeddings,
        encrypt_at_rest=storage_encrypt,
        encryption_kdf=storage_kdf,
    )

    # Session config
    session_raw = raw.get("session", {})
    if not isinstance(session_raw, dict):
        session_raw = {}
    session_store = str(session_raw.get("store", os.environ.get("AI_CHAT_SESSION_STORE", "memory")))
    if session_store not in ("memory", "sqlite"):
        session_store = "memory"
    try:
        session_max_concurrent = max(
            0,
            int(
                session_raw.get(
                    "max_concurrent_sessions",
                    os.environ.get("AI_CHAT_SESSION_MAX_CONCURRENT", 0),
                )
            ),
        )
    except (ValueError, TypeError):
        session_max_concurrent = 0
    try:
        session_idle_timeout = max(
            0,
            int(
                session_raw.get(
                    "idle_timeout",
                    os.environ.get("AI_CHAT_SESSION_IDLE_TIMEOUT", 1800),
                )
            ),
        )
    except (ValueError, TypeError):
        session_idle_timeout = 1800
    try:
        session_absolute_timeout = max(
            0,
            int(
                session_raw.get(
                    "absolute_timeout",
                    os.environ.get("AI_CHAT_SESSION_ABSOLUTE_TIMEOUT", 43200),
                )
            ),
        )
    except (ValueError, TypeError):
        session_absolute_timeout = 43200
    session_allowed_ips_raw = session_raw.get("allowed_ips", [])
    if not isinstance(session_allowed_ips_raw, list):
        session_allowed_ips_raw = []
    session_allowed_ips = [str(ip) for ip in session_allowed_ips_raw if ip]
    env_allowed_ips = os.environ.get("AI_CHAT_SESSION_ALLOWED_IPS", "")
    if env_allowed_ips and not session_allowed_ips:
        session_allowed_ips = [ip.strip() for ip in env_allowed_ips.split(",") if ip.strip()]
    session_config = SessionConfig(
        store=session_store,
        max_concurrent_sessions=session_max_concurrent,
        idle_timeout=session_idle_timeout,
        absolute_timeout=session_absolute_timeout,
        allowed_ips=session_allowed_ips,
    )

    # Audit config
    audit_raw = raw.get("audit", {})
    if not isinstance(audit_raw, dict):
        audit_raw = {}
    audit_enabled = str(audit_raw.get("enabled", os.environ.get("AI_CHAT_AUDIT_ENABLED", "false"))).lower() in (
        "true",
        "1",
        "yes",
    )
    audit_log_path = str(audit_raw.get("log_path", os.environ.get("AI_CHAT_AUDIT_LOG_PATH", "")))
    audit_tamper = str(audit_raw.get("tamper_protection", os.environ.get("AI_CHAT_AUDIT_TAMPER_PROTECTION", "hmac")))
    if audit_tamper not in ("none", "hmac"):
        audit_tamper = "hmac"
    audit_rotation = str(audit_raw.get("rotation", "daily"))
    if audit_rotation not in ("daily", "size"):
        audit_rotation = "daily"
    try:
        audit_rotate_size = max(1_048_576, int(audit_raw.get("rotate_size_bytes", 10_485_760)))
    except (ValueError, TypeError):
        audit_rotate_size = 10_485_760
    try:
        _raw_retention = audit_raw.get("retention_days", os.environ.get("AI_CHAT_AUDIT_RETENTION_DAYS", 90))
        audit_retention = max(0, int(_raw_retention))
    except (ValueError, TypeError):
        audit_retention = 90
    audit_redact = str(
        audit_raw.get("redact_content", os.environ.get("AI_CHAT_AUDIT_REDACT_CONTENT", "true"))
    ).lower() not in ("false", "0", "no")
    audit_events_raw = audit_raw.get("events", {})
    if not isinstance(audit_events_raw, dict):
        audit_events_raw = {}
    audit_events: dict[str, bool] = {}
    for evt_key in ("auth", "tool_calls", "dlp", "output_filter"):
        audit_events[evt_key] = str(audit_events_raw.get(evt_key, "true")).lower() not in ("false", "0", "no")
    audit_config = AuditConfig(
        enabled=audit_enabled,
        log_path=audit_log_path,
        tamper_protection=audit_tamper,
        rotation=audit_rotation,
        rotate_size_bytes=audit_rotate_size,
        retention_days=audit_retention,
        redact_content=audit_redact,
        events=audit_events,
    )

    # Codebase index config
    ci_raw = raw.get("codebase_index", {})
    if not isinstance(ci_raw, dict):
        ci_raw = {}
    ci_enabled = str(ci_raw.get("enabled", "true")).lower() not in ("false", "0", "no")
    ci_map_tokens = int(ci_raw.get("map_tokens", 1000))
    ci_languages = ci_raw.get("languages", [])
    if not isinstance(ci_languages, list):
        ci_languages = []
    ci_exclude_raw = ci_raw.get("exclude_dirs")
    ci_config = CodebaseIndexConfig(
        enabled=ci_enabled,
        map_tokens=ci_map_tokens,
        languages=[str(lang) for lang in ci_languages],
    )
    if ci_exclude_raw is not None and isinstance(ci_exclude_raw, list):
        ci_config.exclude_dirs = [str(d) for d in ci_exclude_raw]

    # Compliance rules config
    compliance_raw = raw.get("compliance", {})
    if not isinstance(compliance_raw, dict):
        compliance_raw = {}
    compliance_rules: list[ComplianceRule] = []
    for rule_raw in compliance_raw.get("rules", []):
        if not isinstance(rule_raw, dict):
            continue
        rule_field = str(rule_raw.get("field", ""))
        if not rule_field:
            continue
        must_match_str = str(rule_raw.get("must_match", ""))
        compiled = None
        if must_match_str:
            try:
                compiled = re.compile(must_match_str)
            except re.error:
                compiled = None  # invalid pattern handled at evaluation time
        compliance_rules.append(
            ComplianceRule(
                field=rule_field,
                message=str(rule_raw.get("message", "")),
                must_be=rule_raw.get("must_be", _UNSET),
                must_not_be=rule_raw.get("must_not_be", _UNSET),
                must_match=must_match_str,
                must_not_be_empty=bool(rule_raw.get("must_not_be_empty", False)),
                must_contain=rule_raw.get("must_contain", _UNSET),
                _compiled_pattern=compiled,
            )
        )
    compliance_config = ComplianceConfig(rules=compliance_rules)

    pack_sources_raw = raw.get("pack_sources", [])
    if not isinstance(pack_sources_raw, list):
        pack_sources_raw = []
    pack_sources_list: list[PackSourceConfig] = []
    for src in pack_sources_raw:
        if not isinstance(src, dict):
            continue
        url = str(src.get("url", "")).strip()
        if not url:
            continue
        try:
            refresh = int(src.get("refresh_interval", 30))
        except (ValueError, TypeError):
            refresh = 30
        pack_sources_list.append(
            PackSourceConfig(
                url=url,
                branch=str(src.get("branch", "main")),
                refresh_interval=refresh,
            )
        )

    return (
        AppConfig(
            ai=ai,
            app=app_settings,
            mcp_servers=mcp_servers,
            mcp_tool_warning_threshold=mcp_tool_warning_threshold,
            shared_databases=shared_databases,
            cli=cli_config,
            identity=identity,
            embeddings=embeddings_config,
            safety=safety_config,
            proxy=proxy_config,
            rag=rag_config,
            references=refs_config,
            codebase_index=ci_config,
            storage=storage_config,
            session=session_config,
            audit=audit_config,
            compliance=compliance_config,
            pack_sources=pack_sources_list,
        ),
        enforced_fields,
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

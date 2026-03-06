# Artifact Types

Anteroom supports 7 artifact types. Each type has a specific role in how the agent processes and uses the content.

## skill

**Purpose**: A reusable prompt template that users invoke with `/skill-name` or the AI invokes via the `invoke_skill` tool.

**Content format**: YAML with `name`, `description`, and `prompt` fields.

**How Anteroom uses it**: Skills are registered in the `SkillRegistry` and exposed as tab-completable slash commands. When invoked, the `prompt` field is expanded (with `{args}` template substitution) and sent as the user message.

**Example**:

```yaml title="skills/review.yaml"
name: review
description: Review code changes for quality and security
prompt: |
  Review the following code changes for:
  - Security vulnerabilities (OWASP Top 10)
  - Code quality and maintainability
  - Test coverage gaps

  {args}
```

**Directory**: `skills/`

**Common mistakes**:

- Forgetting the `name` field (required, must match the artifact name)
- Using `{args}` inside a fenced code block (substitution only happens outside code fences)
- Exceeding the 50KB prompt size limit

---

## rule

**Purpose**: An always-on instruction injected into every agent turn. Rules enforce standards that the AI must follow regardless of what the user asks.

**Content format**: Markdown (plain text).

**How Anteroom uses it**: All active rules are concatenated and injected into the system prompt on every turn. They're not optional — the agent sees them on every interaction.

### Enforcement Levels

Rules support two enforcement levels via the `enforce` metadata field:

| Level | Behavior |
|-------|----------|
| `soft` (default) | Injected into system prompt as guidance. The AI should follow it but is not mechanically prevented from violating it |
| `hard` | Checked at the tool execution layer before every tool call. Matching tool calls are **blocked** — the tool never runs, regardless of user approval |

### Soft Rule Example

A soft rule provides guidance in the system prompt:

```markdown title="rules/coding-standards.md"
# Coding Standards

- Use descriptive variable names
- Prefer early returns to reduce nesting
- Keep functions under 50 lines
```

### Hard Rule Example

A hard rule blocks specific tool calls via regex matching:

```markdown title="rules/no-force-push.md"
Never force push to shared branches.
```

The hard enforcement is configured in the artifact's **metadata** (set in `pack.yaml` or the artifact DB):

```yaml
enforce: hard
reason: Force pushing destroys shared history
matches:
  - tool: bash
    pattern: "git\\s+push\\s+--force"
  - tool: bash
    pattern: "git\\s+push\\s+-f"
```

Each `matches` entry specifies:

- `tool`: The tool name to check (`bash`, `write_file`, `edit_file`, `read_file`, or `*` for all tools)
- `pattern`: A regex matched against the tool's arguments

When the AI tries to run `git push --force origin main`, the RuleEnforcer detects the match and returns a `hard_denied` verdict. The tool call never executes.

**Safety guards on patterns:**

- Patterns longer than 500 characters are rejected (ReDoS prevention)
- Invalid regex patterns are skipped with a warning
- Rules with no valid match patterns are ignored

See [How Packs Work: Rule Enforcement](how-packs-work.md#rule-enforcement-hard-rules) for the full lifecycle.

**Directory**: `rules/`

**Common mistakes**:

- Writing rules that are too vague ("write good code") — be specific and actionable
- Creating rules that conflict with each other across packs
- Adding excessive rules that consume too many tokens
- Using overly broad regex patterns in hard rules (e.g., `.*` matches everything)
- Forgetting that hard rules cannot be overridden — use them only for non-negotiable policies

---

## instruction

**Purpose**: A static block of text prepended to the system prompt. Instructions provide context the agent needs but that isn't an enforceable rule.

**Content format**: Markdown (plain text).

**How Anteroom uses it**: Instructions are injected once at the start of the system prompt, before rules and conversation context.

**Example**:

```markdown title="instructions/project-context.md"
# Project Context

This is a FastAPI application with a React frontend. The API uses SQLAlchemy
with PostgreSQL. Authentication is handled via JWT tokens with refresh rotation.

Key directories:
- `src/api/` — FastAPI routers and services
- `src/models/` — SQLAlchemy models
- `frontend/` — React app (Vite + TypeScript)
```

**Directory**: `instructions/`

**Common mistakes**:

- Duplicating information already in ANTEROOM.md or CLAUDE.md
- Adding instructions that should be rules (if it's enforceable, make it a rule)

---

## context

**Purpose**: Dynamic context injected per-turn. Similar to instructions but intended for reference material that the agent consults as needed.

**Content format**: Markdown or plain text.

**How Anteroom uses it**: Context artifacts are available to the agent during each turn. They provide reference material like API documentation, schema definitions, or style guides.

**Example**:

```markdown title="context/api-reference.md"
# Internal API Reference

## POST /api/users
Creates a new user account.

Request body:
- `email` (string, required): User's email address
- `name` (string, required): Display name
- `role` (string, optional): One of "admin", "editor", "viewer". Default: "viewer"

Response: 201 with `{id, email, name, role, created_at}`
```

**Directory**: `context/`

**Common mistakes**:

- Loading large context artifacts that exceed token budgets
- Using context for rules (context is advisory, rules are mandatory)

---

## memory

**Purpose**: Persistent memory across sessions. Memories store learned preferences, patterns, or facts that the agent should retain.

**Content format**: Markdown or plain text.

**How Anteroom uses it**: Memories are loaded into the agent's context, allowing it to recall information from previous interactions or pre-configured knowledge.

**Example**:

```markdown title="memories/team-preferences.md"
# Team Preferences

- Troy prefers functional components over class components
- The team uses pnpm, never npm or yarn
- All PRs require at least one approval before merging
- Deployment window: Tuesday-Thursday, 9am-3pm EST
```

**Directory**: `memories/`

**Common mistakes**:

- Storing sensitive information (passwords, tokens) in memory artifacts
- Creating memories that contradict rules or instructions

---

## mcp_server

**Purpose**: An MCP server configuration that Anteroom connects to for additional tools.

**Content format**: YAML with MCP server connection fields.

**How Anteroom uses it**: The MCP manager reads mcp_server artifacts and establishes connections to the configured servers, making their tools available to the agent.

**Example**:

```yaml title="mcp_servers/filesystem.yaml"
command: npx
args:
  - -y
  - "@modelcontextprotocol/server-filesystem"
  - "/path/to/allowed/directory"
env:
  NODE_ENV: production
tools_include:
  - "read_file"
  - "write_file"
tools_exclude:
  - "delete_*"
trust_level: untrusted
```

**Directory**: `mcp_servers/`

**Common mistakes**:

- Hardcoding absolute paths that differ across team members' machines
- Setting `trust_level: trusted` without understanding the security implications (trusted tools skip defensive prompt envelopes)
- Forgetting `tools_include`/`tools_exclude` filters, exposing more tools than intended

---

## config_overlay

**Purpose**: A YAML fragment that merges into the running Anteroom configuration. Config overlays let packs adjust settings without requiring users to edit their config file.

**Content format**: YAML (must be valid YAML that maps to Anteroom config fields).

**How Anteroom uses it**: Config overlays are deep-merged into the active configuration at the appropriate precedence layer. They can set safety modes, model preferences, timeout values, or any other config field.

**Example**:

```yaml title="config_overlays/safety.yaml"
safety:
  approval_mode: ask_for_writes
  bash_sandbox:
    allow_network: false
    allow_package_install: false
```

**Directory**: `config_overlays/`

**Common mistakes**:

- Setting fields that conflict with team-enforced config (team enforcement always wins)
- Invalid YAML syntax (health check catches this as a `malformed` error)
- Overriding security settings to be less restrictive than intended

---

## Type-to-Directory Mapping

When no explicit `file` field is set in the manifest, Anteroom looks for artifacts in these directories:

| Artifact Type | Directory |
|--------------|-----------|
| `skill` | `skills/` |
| `rule` | `rules/` |
| `instruction` | `instructions/` |
| `context` | `context/` |
| `memory` | `memories/` |
| `mcp_server` | `mcp_servers/` |
| `config_overlay` | `config_overlays/` |

File extensions are probed in order: `.yaml`, `.md`, `.txt`, `.json`. The first match wins.

## Next Steps

- [Manifest Format](manifest-format.md) — how to declare artifacts in `pack.yaml`
- [Quickstart](quickstart.md) — install your first pack

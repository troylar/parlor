<p align="center">
  <img src="docs/logo.svg" alt="Anteroom" width="100" height="100">
</p>

<h1 align="center">Anteroom</h1>

<p align="center">
  <strong>Your private AI gateway. Self-hosted. Agentic. Secure.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/pypi/v/anteroom?style=for-the-badge&color=3b82f6&labelColor=0f1117" alt="PyPI Version">
  <img src="https://img.shields.io/badge/python-3.10%2B-10b981?style=for-the-badge&labelColor=0f1117" alt="Python 3.10+">
  <a href="https://codecov.io/gh/troylar/anteroom"><img src="https://img.shields.io/codecov/c/github/troylar/anteroom?style=for-the-badge&color=7c3aed&labelColor=0f1117&label=coverage" alt="Coverage"></a>
  <img src="https://img.shields.io/github/license/troylar/anteroom?style=for-the-badge&color=e8913a&labelColor=0f1117" alt="License">
</p>

<p align="center">
  <a href="https://anteroom.readthedocs.io">Docs</a> &bull;
  <a href="#-get-running-in-60-seconds">Quick Start</a> &bull;
  <a href="https://anteroom.readthedocs.io/en/latest/advanced/changelog/">Changelog</a> &bull;
  <a href="https://anteroom.readthedocs.io/en/latest/getting-started/quickstart/">Tutorials</a>
</p>

<br>

<p align="center">
  <img src="docs/screenshots/theme-midnight.png" alt="Anteroom Web UI" width="800">
</p>

<br>

---

<br>

## What is Anteroom?

Anteroom is a **ChatGPT-style web UI** and **agentic CLI** that runs on your machine and connects to **any OpenAI-compatible API** &mdash; OpenAI, Azure, Ollama, LM Studio, or your company's internal endpoint.

Think of it as your private room between you and the AI. Your data never leaves your machine. No cloud. No telemetry. Just `pip install` and go.

<br>

> **Built for enterprise teams behind firewalls** who need agentic AI without sending data to third parties.
>
> **Built for developers** who want a CLI-first, tool-rich AI workflow they fully control.
>
> **Built for anyone** who believes their conversations are their own.

<br>

---

<br>

## Get running in 60 seconds

```bash
pip install anteroom
aroom init          # interactive setup wizard
aroom               # web UI at http://127.0.0.1:8080
```

That's it. No Docker. No database server. No config files required.

<br>

---

<br>

## Two interfaces, one brain

Everything is shared &mdash; conversations, tools, storage. Start in the web UI, pick up in the terminal. Or live entirely in the CLI. Your choice.

<br>

### Web UI

A full-featured chat interface with projects, folders, tags, file attachments, canvas panels, inline tool approvals, and four built-in themes.

<p align="center">
  <img src="docs/screenshots/theme-midnight.png" alt="Midnight" width="390">&nbsp;&nbsp;
  <img src="docs/screenshots/theme-ember.png" alt="Ember" width="390">
</p>
<p align="center">
  <img src="docs/screenshots/theme-dawn.png" alt="Dawn" width="390">&nbsp;&nbsp;
  <img src="docs/screenshots/theme-aurora.png" alt="Aurora" width="390">
</p>

<br>

### CLI REPL

An agentic terminal with **10 built-in tools**, MCP integration, sub-agent orchestration, a skills system, and planning mode &mdash; all with Rich markdown rendering. Type while the AI works; messages queue automatically.

```
$ aroom chat

anteroom v1.24.6 — the secure AI gateway
  model: gpt-4o | tools: 10 built-in + 3 MCP | safety: ask_for_writes

> Refactor the auth module to use JWT tokens

  Thinking... (12s)

  I'll break this into steps:
  1. Read the current auth implementation
  2. Design the JWT token flow
  3. Implement and test

  read_file  src/auth.py                        ✓
  read_file  src/middleware.py                   ✓
  edit_file  src/auth.py  (+42 -18)             ✓  ⚠ requires approval
  edit_file  src/middleware.py  (+15 -8)        ✓
  bash       pytest tests/unit/test_auth.py     ✓  12 passed

  Done. Refactored auth to use JWT with RS256 signing.
  See the changes in src/auth.py and src/middleware.py.

>
```

<br>

### Exec mode

Non-interactive mode for scripts, CI/CD, and automation:

```bash
aroom exec "summarize this PR" --json          # structured output
aroom exec "run tests and fix failures" --timeout 300
echo "review this" | aroom exec - --quiet      # pipe stdin
```

<br>

---

<br>

## What makes it different

<table>
<tr>
<td width="50%" valign="top">

### Agentic, not just chat

The AI reads files, edits code, runs commands, searches your codebase, and spawns parallel sub-agents &mdash; with safety gates at every step. Not a chatbot. A collaborator.

**Built-in tools:** `read_file` `write_file` `edit_file` `bash` `glob_files` `grep` `create_canvas` `update_canvas` `patch_canvas` `run_agent`

</td>
<td width="50%" valign="top">

### Extensible via MCP

Connect any [Model Context Protocol](https://modelcontextprotocol.io/) server to add tools. Databases, APIs, file systems, custom services &mdash; the AI can use them all with the same safety controls as built-in tools.

```yaml
# config.yaml
mcp_servers:
  - name: filesystem
    command: npx @anthropic/mcp-filesystem
```

</td>
</tr>
<tr>
<td width="50%" valign="top">

### Planning mode

For complex tasks, the AI explores first, writes a plan, then executes only after you approve. No surprises.

```
> /plan build a REST API for user management
  Planning... reading codebase, designing approach

> /plan approve
  Executing plan: 8 steps across 5 files...
```

</td>
<td width="50%" valign="top">

### Security-first

Built to [OWASP ASVS Level 1](SECURITY.md) standards. Not bolted on &mdash; baked in.

- **4 tool risk tiers**: read / write / execute / destructive
- **Configurable approval modes**: auto, ask_for_writes, ask_for_dangerous
- **Session + CSRF + rate limiting + CSP**
- **Destructive command detection** before execution
- **MCP SSRF protection** built in

</td>
</tr>
<tr>
<td width="50%" valign="top">

### Knowledge sources

Upload files, save notes, bookmark URLs. Sources persist across conversations and are searchable with local vector embeddings &mdash; no API key needed.

```bash
pip install anteroom[embeddings]  # adds local vectors
```

</td>
<td width="50%" valign="top">

### Works with everything

Any endpoint that speaks the OpenAI protocol:

- **OpenAI** &mdash; GPT-4o, o1, etc.
- **Azure OpenAI** &mdash; your enterprise deployment
- **Ollama / LM Studio** &mdash; fully offline
- **vLLM / TGI** &mdash; self-hosted open models
- **Any OpenAI-compatible API**

</td>
</tr>
</table>

<br>

---

<br>

## The full picture

| | |
|---|---|
| **Web UI** | Conversations, projects, folders, tags, attachments, canvas, themes, keyboard shortcuts |
| **CLI** | REPL, one-shot, exec mode, planning, skills, @file references, Rich rendering |
| **Tools** | 10 built-in + unlimited MCP tools, parallel execution, sub-agent orchestration |
| **Safety** | 4 risk tiers, 3 approval modes, destructive command detection, SSRF protection |
| **Storage** | SQLite + FTS5 + optional vector search, fully local, no cloud |
| **Security** | OWASP ASVS L1, CSRF, CSP, HSTS, rate limiting, parameterized queries |
| **Identity** | Ed25519 keypairs, HMAC-SHA256 session tokens, stable across restarts |
| **Config** | YAML + env vars, per-project ANTEROOM.md conventions, dynamic API key refresh |
| **Deployment** | `pip install anteroom` &mdash; one command, no infrastructure |

<br>

---

<br>

## Development

```bash
git clone https://github.com/troylar/anteroom.git
cd anteroom && pip install -e ".[dev]"
pytest tests/ -v                    # 1800+ tests
ruff check src/ tests/              # lint
ruff format src/ tests/             # format
```

**Stack:** Python 3.10+ &bull; FastAPI &bull; SQLite &bull; Vanilla JS &bull; Rich &bull; prompt-toolkit &bull; OpenAI SDK &bull; MCP SDK

<br>

---

<br>

<p align="center">
  <strong>MIT License</strong><br>
  <br>
  An <em>anteroom</em> is the private chamber just outside a larger hall &mdash;<br>
  a controlled space where you decide who enters and what leaves.<br>
  <br>
  <a href="https://anteroom.readthedocs.io">anteroom.readthedocs.io</a>
</p>

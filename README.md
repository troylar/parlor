<p align="center">
  <img src="https://img.shields.io/pypi/v/anteroom?style=for-the-badge&color=3b82f6&labelColor=0f1117" alt="PyPI Version">
  <img src="https://img.shields.io/badge/python-3.10%2B-10b981?style=for-the-badge&labelColor=0f1117" alt="Python 3.10+">
  <a href="https://codecov.io/gh/troylar/anteroom"><img src="https://img.shields.io/codecov/c/github/troylar/anteroom?style=for-the-badge&color=7c3aed&labelColor=0f1117&label=coverage" alt="Coverage"></a>
  <img src="https://img.shields.io/github/license/troylar/anteroom?style=for-the-badge&color=e8913a&labelColor=0f1117" alt="License">
</p>

<p align="center">
  <img src="docs/logo.svg" alt="Anteroom Logo" width="120" height="120">
</p>

<h1 align="center">Anteroom</h1>

<h3 align="center">The room before the room &mdash; a secure, private space between you and the AI.</h3>

<p align="center">
  Self-hosted ChatGPT-style web UI <strong>and</strong> agentic CLI that connects to any OpenAI-compatible API.<br>
  <strong>Install with pip. Run locally. Own your data.</strong>
</p>

<p align="center">
  <a href="https://anteroom.readthedocs.io">Documentation</a> &bull;
  <a href="#quick-start">Quick Start</a> &bull;
  <a href="#features">Features</a> &bull;
  <a href="#security">Security</a>
</p>

<p align="center">
  <img src="docs/screenshots/theme-midnight.png" alt="Anteroom - Midnight Theme" width="800">
</p>

---

## Why Anteroom?

An **anteroom** is the private chamber just outside a larger hall --- a controlled space where you decide who enters and what leaves. That's exactly what this is: a secure layer on *your* machine between you and any AI, where your conversations never touch someone else's cloud.

Anteroom connects to **any** OpenAI-compatible endpoint --- your company's internal API, OpenAI, Azure, Ollama, LM Studio, or anything else that speaks the OpenAI protocol. Built to [OWASP ASVS L1](SECURITY.md) standards because your conversations deserve real security, not security theater.

> **One command. No cloud. No telemetry. No compromise.**

---

## Quick Start

```bash
pip install anteroom
aroom init         # Interactive setup wizard
```

Or create `~/.anteroom/config.yaml` manually:

```yaml
ai:
  base_url: "https://your-ai-endpoint/v1"
  api_key: "your-api-key"
  model: "gpt-4"
```

```bash
aroom --test       # Verify connection
aroom              # Web UI at http://127.0.0.1:8080
aroom chat         # Terminal CLI
aroom --version    # Show version
```

---

## Features

### Web UI

Full-featured ChatGPT-style interface with conversations, projects, folders, tags, file attachments, MCP tool integration, prompt queuing, canvas panels for AI-generated content (CodeMirror 6 editor), inline safety approvals, sub-agent progress cards for parallel AI execution, command palette, and four built-in themes.

<p align="center">
  <img src="docs/screenshots/theme-midnight.png" alt="Midnight Theme" width="400">
  <img src="docs/screenshots/theme-dawn.png" alt="Dawn Theme" width="400">
</p>

### CLI Chat

An agentic terminal REPL with built-in tools (read/write/edit files, bash, glob, grep, canvas, run_agent), MCP integration, sub-agent orchestration for parallel task execution, skills system, safety approval prompts for destructive operations, and Rich markdown rendering. Type while the AI works --- messages queue automatically.

```bash
aroom chat                          # Interactive REPL
aroom chat "explain main.py"        # One-shot mode
aroom chat -c                       # Continue last conversation
aroom chat --model gpt-4o "hello"   # Override model
```

### Shared Core

Both interfaces share the same agent loop, storage layer, and SQLite database. Conversations created in the CLI show up in the web UI, and vice versa.

---

## Security

| Layer | Implementation |
|---|---|
| **Auth** | Session tokens, HttpOnly cookies, HMAC-SHA256 |
| **CSRF** | Per-session double-submit tokens |
| **Headers** | CSP, X-Frame-Options, HSTS, Referrer-Policy |
| **Database** | Parameterized queries, column allowlists, path validation |
| **Input** | DOMPurify, UUID validation, filename sanitization |
| **Rate Limiting** | 120 req/min per IP |
| **Tool Safety** | Destructive action approvals (CLI + Web UI), configurable patterns, sensitive path blocking |
| **MCP Safety** | SSRF protection, shell metacharacter rejection |

Full details in [SECURITY.md](SECURITY.md).

---

## Documentation

For complete documentation including configuration, CLI commands, API reference, themes, MCP setup, skills, and development guides, visit **[anteroom.readthedocs.io](https://anteroom.readthedocs.io)**.

---

## Development

```bash
git clone https://github.com/troylar/anteroom.git
cd anteroom
pip install -e ".[dev]"
pytest tests/ -v
```

| | |
|---|---|
| **Backend** | Python 3.10+, FastAPI, Uvicorn |
| **Frontend** | Vanilla JS, marked.js, highlight.js, KaTeX, CodeMirror 6 |
| **CLI** | Rich, prompt-toolkit, tiktoken |
| **Database** | SQLite with FTS5, WAL journaling |
| **AI** | OpenAI Python SDK (async streaming) |
| **MCP** | Model Context Protocol SDK (stdio + SSE) |

---

<p align="center">
  <strong>MIT License</strong><br>
  Built for people who care about their conversations.<br>
  <a href="https://anteroom.readthedocs.io">anteroom.readthedocs.io</a>
</p>

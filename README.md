<p align="center">
  <img src="https://img.shields.io/pypi/v/parlor?style=for-the-badge&color=3b82f6&labelColor=0f1117" alt="PyPI Version">
  <img src="https://img.shields.io/badge/python-3.10%2B-10b981?style=for-the-badge&labelColor=0f1117" alt="Python 3.10+">
  <a href="https://codecov.io/gh/troylar/parlor"><img src="https://img.shields.io/codecov/c/github/troylar/parlor?style=for-the-badge&color=7c3aed&labelColor=0f1117&label=coverage" alt="Coverage"></a>
  <img src="https://img.shields.io/github/license/troylar/parlor?style=for-the-badge&color=e8913a&labelColor=0f1117" alt="License">
</p>

<p align="center">
  <img src="docs/logo.svg" alt="Parlor Logo" width="120" height="120">
</p>

<h1 align="center">Parlor</h1>

<h3 align="center">The secure digital speakeasy for AI conversation.</h3>

<p align="center">
  Self-hosted ChatGPT-style web UI <strong>and</strong> agentic CLI that connects to any OpenAI-compatible API.<br>
  <strong>Install with pip. Run locally. Own your data.</strong>
</p>

<p align="center">
  <a href="https://parlor.readthedocs.io">Documentation</a> &bull;
  <a href="#quick-start">Quick Start</a> &bull;
  <a href="#features">Features</a> &bull;
  <a href="#security">Security</a>
</p>

<p align="center">
  <img src="docs/screenshots/theme-midnight.png" alt="Parlor - Midnight Theme" width="800">
</p>

---

## Why Parlor?

Step inside. Parlor connects to **any** OpenAI-compatible endpoint --- your company's internal API, OpenAI, Azure, Ollama, LM Studio, or anything else that speaks the OpenAI protocol. Built to [OWASP ASVS L1](SECURITY.md) standards because your conversations deserve real security, not security theater.

> **One command. No cloud. No telemetry. No compromise.**

---

## Quick Start

```bash
pip install parlor
parlor init         # Interactive setup wizard
```

Or create `~/.parlor/config.yaml` manually:

```yaml
ai:
  base_url: "https://your-ai-endpoint/v1"
  api_key: "your-api-key"
  model: "gpt-4"
```

```bash
parlor --test       # Verify connection
parlor              # Web UI at http://127.0.0.1:8080
parlor chat         # Terminal CLI
parlor --version    # Show version
```

---

## Features

### Web UI

Full-featured ChatGPT-style interface with conversations, projects, folders, tags, file attachments, MCP tool integration, prompt queuing, command palette, and four built-in themes.

<p align="center">
  <img src="docs/screenshots/theme-midnight.png" alt="Midnight Theme" width="400">
  <img src="docs/screenshots/theme-dawn.png" alt="Dawn Theme" width="400">
</p>

### CLI Chat

An agentic terminal REPL with built-in tools (read/write/edit files, bash, glob, grep), MCP integration, skills system, and Rich markdown rendering. Type while the AI works --- messages queue automatically.

```bash
parlor chat                          # Interactive REPL
parlor chat "explain main.py"        # One-shot mode
parlor chat -c                       # Continue last conversation
parlor chat --model gpt-4o "hello"   # Override model
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
| **CLI Safety** | Destructive command confirmation, path blocking |
| **MCP Safety** | SSRF protection, shell metacharacter rejection |

Full details in [SECURITY.md](SECURITY.md).

---

## Documentation

For complete documentation including configuration, CLI commands, API reference, themes, MCP setup, skills, and development guides, visit **[parlor.readthedocs.io](https://parlor.readthedocs.io)**.

---

## Development

```bash
git clone https://github.com/troylar/parlor.git
cd parlor
pip install -e ".[dev]"
pytest tests/ -v
```

| | |
|---|---|
| **Backend** | Python 3.10+, FastAPI, Uvicorn |
| **Frontend** | Vanilla JS, marked.js, highlight.js, KaTeX |
| **CLI** | Rich, prompt-toolkit, tiktoken |
| **Database** | SQLite with FTS5, WAL journaling |
| **AI** | OpenAI Python SDK (async streaming) |
| **MCP** | Model Context Protocol SDK (stdio + SSE) |

---

<p align="center">
  <strong>MIT License</strong><br>
  Built for people who care about their conversations.<br>
  <a href="https://parlor.readthedocs.io">parlor.readthedocs.io</a>
</p>

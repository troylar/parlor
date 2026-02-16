# Anteroom

**A private anteroom for AI conversation.**

Self-hosted ChatGPT-style web UI **and** agentic CLI that connects to any OpenAI-compatible API. Install with pip. Run locally. Own your data.

![Anteroom - Midnight Theme](screenshots/theme-midnight.png)

---

## Two Interfaces, One Brain

Anteroom gives you two ways to interact with your AI --- a polished **web UI** and a powerful **agentic CLI** --- both backed by the same conversation database and agent loop.

<div class="grid cards" markdown>

-   **Web UI**

    ---

    Full-featured chat interface with conversations, projects, folders, tags, file attachments, themes, and a command palette. Launch with `aroom`.

    [:octicons-arrow-right-24: Web UI docs](web-ui/index.md)

-   **CLI REPL**

    ---

    Agentic terminal chat with built-in tools (file I/O, bash, grep, glob), skills, MCP integration, and prompt queuing. Launch with `aroom chat`.

    [:octicons-arrow-right-24: CLI docs](cli/index.md)

</div>

## Key Features

| Feature | Details |
|---|---|
| **Any OpenAI-compatible API** | OpenAI, Azure, Ollama, LM Studio, vLLM, or any endpoint that speaks the OpenAI protocol |
| **Agentic tool use** | Read files, write code, run commands, search codebases --- up to 50 tool iterations per turn |
| **Parallel tool execution** | Multiple tool calls in one response run concurrently via `asyncio.as_completed` |
| **Prompt queuing** | Type while the AI is working --- messages queue and process in FIFO order |
| **MCP integration** | Connect stdio or SSE-based MCP servers for external tool access |
| **Full-text search** | FTS5-powered search across all messages and conversation titles |
| **Projects & folders** | Organize conversations with project-scoped system prompts, folders, and color-coded tags |
| **4 themes** | Midnight, Dawn, Aurora, Ember --- switch instantly via command palette |
| **Security-first** | OWASP ASVS L1 compliant: CSP, CSRF, SRI, rate limiting, HSTS |
| **Local-first** | SQLite-backed, no cloud, no telemetry. Everything stays on your machine |

## Quick Install

```bash
pip install anteroom
```

Then configure your AI endpoint:

```yaml title="~/.anteroom/config.yaml"
ai:
  base_url: "https://your-ai-endpoint/v1"
  api_key: "your-api-key"
  model: "gpt-4"
```

```bash
$ aroom --test    # Verify connection
$ aroom           # Launch web UI
$ aroom chat      # Launch CLI
```

[:octicons-arrow-right-24: Full installation guide](getting-started/installation.md)

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.10+, FastAPI, Uvicorn |
| **Frontend** | Vanilla JS (no build step), marked.js, highlight.js, KaTeX, DOMPurify |
| **CLI** | Rich, prompt-toolkit, tiktoken |
| **Database** | SQLite with FTS5 full-text search, WAL journaling |
| **AI** | OpenAI Python SDK (async streaming) |
| **MCP** | Model Context Protocol SDK (stdio + SSE transports) |
| **Security** | OWASP ASVS L1 compliance, SRI, CSP, CSRF, rate limiting |

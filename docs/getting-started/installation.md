# Installation

Anteroom runs on Python 3.10+ and installs via pip.

## Prerequisites

- Python 3.10 or later
- pip (included with Python)
- An OpenAI-compatible API endpoint (OpenAI, Azure, Ollama, LM Studio, etc.), or an API key for any LLM provider (OpenRouter, Anthropic, Replicate, etc.)

## Install

```bash
$ pip install anteroom
```

!!! tip "Virtual environment recommended"
    ```bash
    $ python -m venv .venv
    $ source .venv/bin/activate  # macOS/Linux
    $ pip install anteroom
    ```

## Configure

Create the config file at `~/.anteroom/config.yaml`:

```yaml
ai:
  base_url: "https://your-ai-endpoint/v1"
  api_key: "your-api-key"
  model: "gpt-4"
```

!!! info "Environment variables work too"
    You can skip the config file and use environment variables instead:

    ```bash
    $ export AI_CHAT_BASE_URL="https://your-ai-endpoint/v1"
    $ export AI_CHAT_API_KEY="your-api-key"
    $ export AI_CHAT_MODEL="gpt-4"
    ```

    See [Environment Variables](../configuration/environment-variables.md) for the full list.

## Verify

Test your connection:

```bash
$ aroom --test
```

Expected output:

```
Config:
  Endpoint: https://your-ai-endpoint/v1
  Model:    gpt-4
  SSL:      enabled

1. Listing models...
   OK - 12 model(s) available

2. Sending test prompt to gpt-4...
   OK - Response: Hello! How can I help you today?

All checks passed.
```

## Launch

=== "Web UI"

    ```bash
    $ aroom
    ```

    Your browser opens to `http://127.0.0.1:8080`. The server runs in the foreground — closing the terminal stops it.

=== "Web UI (background)"

    ```bash
    $ aroom start
    ```

    Starts the web server in the background. You can close the terminal and the server keeps running. Use `aroom status` to check and `aroom stop` to shut down.

=== "CLI"

    ```bash
    $ aroom chat
    ```

    An interactive REPL starts in your terminal.

## Optional Extras

Anteroom has optional dependency groups for additional capabilities:

```bash
$ pip install anteroom[providers]    # 100+ LLM providers via LiteLLM (OpenRouter, Replicate, etc.)
$ pip install anteroom[office]       # MS Office tools (docx, xlsx, pptx)
$ pip install anteroom[docs]         # PDF and DOCX text extraction
$ pip install anteroom[embeddings]   # Local embeddings via fastembed
$ pip install anteroom[index]        # Codebase indexing via tree-sitter
$ pip install anteroom[encryption]   # Database encryption at rest (SQLCipher)
```

Install multiple extras at once:

```bash
$ pip install anteroom[office,embeddings]
```

All extras are optional — Anteroom works without them, and features degrade gracefully when their dependencies are missing.

## Data Directory

Anteroom creates `~/.anteroom/` on first run:

```
~/.anteroom/
  config.yaml          # Configuration          (permissions: 0600)
  chat.db              # SQLite + WAL journal   (permissions: 0600)
  cli_history          # REPL command history
  attachments/         # Files by conversation  (permissions: 0700)
```

The data directory is created with `0700` permissions (owner-only). Database files are created with `0600` permissions.

## Next Steps

- [Quickstart](quickstart.md) --- 5-minute guided walkthrough
- [Configuration](../configuration/index.md) --- full config reference
- [Concepts](concepts.md) --- how Anteroom works under the hood
